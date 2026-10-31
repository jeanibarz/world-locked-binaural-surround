//! Post-convolution headphone compensation + subjective EQ — the "timbre parity" output stage (#193).
//!
//! The baked BRIR pack this engine convolves is a **bare** 7.1→binaural bake: it carries the room
//! and the directions, but not the owner's headphone compensation or the subjective equalization
//! they tuned by ear. In their EqualizerAPO setup those live as a **global per-ear chain applied
//! once to the final stereo mix**, after the per-speaker convolution and the downmix to L/R:
//!
//! ```text
//! ... per-channel Convolution + Copy downmix to L,R ...   # the engine already does this
//! Channel: L R
//! Include: binaural\...\audeze_comp.txt   # headphone comp: per-ear FIR + ~19 biquads/ear
//! Preamp: -4.5 dB
//! Filter: ...                             # subjective EQ: ~31 biquads (both ears)
//! ```
//!
//! Because the chain is linear and time-invariant and operates on the *final* L/R buses, it is
//! exactly an output-bus stage — it never touches the individual BRIRs, so the world-lock steering
//! is untouched. Reproducing it here restores the timbre the owner listened with; without it the
//! bare bake sounds thin and (per journal 13) the spatial image wanders, because the EQ is what
//! lifts the direct sound over the diffuse reverb tail.
//!
//! This is a faithful Rust port of the project's Python oracle (`binaural_headtracker.output_eq` +
//! `.eq`), which is itself byte-parity with the sibling offline EqualizerAPO reconstruction. The
//! port is gated against that oracle numerically (see the `parity` test): the biquad coefficients
//! are the RBJ Audio EQ Cookbook exactly as EqualizerAPO implements it — including EqualizerAPO's
//! bandwidth-octaves→alpha conversion and the shelf default-slope convention — and the comp FIR runs
//! through the same uniformly-partitioned overlap-save convolver (`binaural_conv::BusConvolverCore`,
//! verified `== scipy.fftconvolve` bin for bin) that the pack convolution uses.
//!
//! Pure and cross-platform (no WASAPI), so it unit-tests natively — unlike the Windows-only glue in
//! `main.rs` that drives it per rendered block.

use std::path::{Path, PathBuf};

use binaural_conv::BusConvolverCore;

/// Shelf slope `S` used for a bare `LS`/`HS` with no `Q`/`BW`/slope given — the one case the
/// EqualizerAPO wiki leaves unpinned. Matches the oracle's `DEFAULT_SHELF_S` (and the sibling's
/// `S = 1` convention).
const DEFAULT_SHELF_S: f64 = 1.0;

/// The final stereo buses an EqualizerAPO binaural preset mixes down to, and the only buses this
/// stage reconstructs (everything before the downmix is the engine's own convolution).
const OUTPUT_BUSES: [&str; 2] = ["L", "R"];

/// A parsed EqualizerAPO `Filter:` line.
#[derive(Debug, Clone, PartialEq)]
pub struct BiquadSpec {
    /// Normalised type: PK, LS, HS, LP, HP, NO, AP (LSC/HSC/PEQ/… are aliased on parse).
    pub ftype: String,
    /// Centre / corner frequency in Hz.
    pub fc: f64,
    pub gain_db: f64,
    pub q: Option<f64>,
    pub bw_oct: Option<f64>,
    /// Shelf slope in dB (for the `LS 6 dB` / `HSC 12 dB` forms).
    pub slope_db: Option<f64>,
}

/// Parse one `Filter: ...` line into a [`BiquadSpec`], or `None` for an `OFF` filter or a line that
/// is not a filter. Mirrors the oracle's `parse_filter_line`: it accepts both the
/// `Filter: ON PK Fc 1000 Hz Gain 3 dB Q 1` form and the numbered REW form `Filter  1: ON PK ...`,
/// reads `BW Oct`, `Q`, `Fc`, `Gain`, and an optional leading shelf slope (`LS 6 dB ...`).
pub fn parse_filter_line(line: &str) -> Option<BiquadSpec> {
    let line = line.trim();
    // "Filter" [digits] ":" ... — the head between "Filter" and ":" may only be digits/spaces.
    if line.len() < 6 || !line[..6].eq_ignore_ascii_case("filter") {
        return None;
    }
    let colon = line.find(':')?;
    if !line[6..colon].chars().all(|c| c.is_ascii_digit() || c.is_whitespace()) {
        return None;
    }
    let after = line[colon + 1..].trim();
    // "ON|OFF" then the rest; only ON filters design a biquad.
    let (state, rest) = match after.split_once(char::is_whitespace) {
        Some((s, r)) => (s, r.trim()),
        None => return None,
    };
    if !state.eq_ignore_ascii_case("ON") {
        return None;
    }

    // Type = leading alphabetic run; then an optional "<number> dB" shelf slope right after it.
    let t_end = rest.find(|c: char| !c.is_ascii_alphabetic()).unwrap_or(rest.len());
    if t_end == 0 {
        return None;
    }
    let ftype_raw = rest[..t_end].to_ascii_uppercase();
    let mut tail = rest[t_end..].trim_start();
    let mut slope_db = None;
    // A slope is "<signed-number> [spaces] dB" immediately after the type (e.g. "HS 6 dB Fc ...").
    // Only consume it when the "dB" actually follows, so "PK Fc ..." is never mistaken for a slope.
    if let Some((num, after_num)) = take_leading_number(tail) {
        let rem = after_num.trim_start();
        if let Some(stripped) = strip_db_prefix(rem) {
            slope_db = Some(num);
            tail = stripped.trim_start();
        }
    }

    let tokens: Vec<&str> = tail.split_whitespace().collect();
    let fc = grab(&tokens, &["Fc"])?; // Fc is required (oracle returns None without it)
    let gain_db = grab(&tokens, &["Gain"]).unwrap_or(0.0);
    let q = grab(&tokens, &["Q"]);
    let bw_oct = grab(&tokens, &["BW", "Oct"]);

    let ftype = match ftype_raw.as_str() {
        "PEQ" | "MODAL" => "PK",
        "LSC" => "LS",
        "HSC" => "HS",
        "LPQ" => "LP",
        "HPQ" => "HP",
        other => other,
    }
    .to_string();

    Some(BiquadSpec { ftype, fc, gain_db, q, bw_oct, slope_db })
}

/// Read the value following a key sequence in the token stream (case-insensitive), e.g. `["Fc"]`
/// → the number after `Fc`, or `["BW", "Oct"]` → the number after `BW Oct`. Mirrors the oracle's
/// `grab`, which takes the first whitespace-delimited number after the key.
fn grab(tokens: &[&str], key: &[&str]) -> Option<f64> {
    let k = key.len();
    tokens.windows(k).position(|w| w.iter().zip(key).all(|(a, b)| a.eq_ignore_ascii_case(b)))
        .and_then(|i| tokens.get(i + k))
        .and_then(|s| s.parse::<f64>().ok())
}

/// Split a leading signed decimal number off the front of `s`, returning `(value, remainder)`.
fn take_leading_number(s: &str) -> Option<(f64, &str)> {
    let bytes = s.as_bytes();
    let mut i = 0;
    if i < bytes.len() && (bytes[i] == b'-' || bytes[i] == b'+') {
        i += 1;
    }
    let start_digits = i;
    while i < bytes.len() && (bytes[i].is_ascii_digit() || bytes[i] == b'.') {
        i += 1;
    }
    if i == start_digits {
        return None; // no digits
    }
    s[..i].parse::<f64>().ok().map(|v| (v, &s[i..]))
}

/// Strip a leading `dB` token (case-insensitive) if present, returning the remainder.
fn strip_db_prefix(s: &str) -> Option<&str> {
    if s.len() >= 2 && s[..2].eq_ignore_ascii_case("db") {
        Some(&s[2..])
    } else {
        None
    }
}

/// First signed decimal number anywhere in `s` (for `Preamp: -4.5 dB` → `-4.5`).
fn first_number(s: &str) -> Option<f64> {
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let c = bytes[i];
        let starts = c.is_ascii_digit()
            || ((c == b'-' || c == b'+' || c == b'.')
                && i + 1 < bytes.len()
                && (bytes[i + 1].is_ascii_digit() || bytes[i + 1] == b'.'));
        if starts {
            if let Some((v, _)) = take_leading_number(&s[i..]) {
                return Some(v);
            }
        }
        i += 1;
    }
    None
}

/// Cookbook `alpha` from `Q`, bandwidth-octaves, or (for shelves) slope/`S` — EqualizerAPO's exact
/// conversions. The `BW Oct` branch is EqualizerAPO's own bandwidth→alpha formula (not generic RBJ).
fn alpha(w0: f64, spec: &BiquadSpec, a_amp: f64) -> f64 {
    let sin_w0 = w0.sin();
    if let Some(q) = spec.q {
        return sin_w0 / (2.0 * q);
    }
    if let Some(bw) = spec.bw_oct {
        return sin_w0 * (std::f64::consts::LN_2 / 2.0 * bw * w0 / sin_w0).sinh();
    }
    if spec.ftype == "LS" || spec.ftype == "HS" {
        let s = match spec.slope_db {
            Some(sd) if sd > 0.0 => (sd / 12.0).min(1.0),
            _ => DEFAULT_SHELF_S,
        };
        return sin_w0 / 2.0 * ((a_amp + 1.0 / a_amp) * (1.0 / s - 1.0) + 2.0).sqrt();
    }
    // Non-shelf with no Q/BW given → Butterworth Q = 1/sqrt(2).
    sin_w0 / (2.0 * (1.0 / std::f64::consts::SQRT_2))
}

/// Design one biquad as `[b0, b1, b2, a1, a2]`, normalised so `a0 = 1` — RBJ Audio EQ Cookbook as
/// EqualizerAPO implements it, identical to the oracle's `eq.design`.
///
/// Returns `None` (the filter is skipped) for out-of-range parameters (`fc` not in `0 < fc < fs/2`,
/// non-positive `Q`/`BW`) or coefficients that are non-finite or unstable: the cascade keeps its
/// state across blocks, so a single NaN/inf sample would poison every later output block forever.
pub fn design(spec: &BiquadSpec, fs: f64) -> Option<[f64; 5]> {
    let params_ok = spec.fc.is_finite()
        && spec.fc > 0.0
        && spec.fc < fs / 2.0
        && spec.gain_db.is_finite()
        && spec.q.map_or(true, |q| q.is_finite() && q > 0.0)
        && spec.bw_oct.map_or(true, |b| b.is_finite() && b > 0.0);
    if !params_ok {
        return None;
    }
    let w0 = 2.0 * std::f64::consts::PI * spec.fc / fs;
    let cos_w0 = w0.cos();
    let a_amp = 10f64.powf(spec.gain_db / 40.0);
    let al = alpha(w0, spec, a_amp);

    let (b0, b1, b2, a0, a1, a2) = match spec.ftype.as_str() {
        "PK" => (
            1.0 + al * a_amp, -2.0 * cos_w0, 1.0 - al * a_amp,
            1.0 + al / a_amp, -2.0 * cos_w0, 1.0 - al / a_amp,
        ),
        "LS" => {
            let tsa = 2.0 * a_amp.sqrt() * al;
            (
                a_amp * ((a_amp + 1.0) - (a_amp - 1.0) * cos_w0 + tsa),
                2.0 * a_amp * ((a_amp - 1.0) - (a_amp + 1.0) * cos_w0),
                a_amp * ((a_amp + 1.0) - (a_amp - 1.0) * cos_w0 - tsa),
                (a_amp + 1.0) + (a_amp - 1.0) * cos_w0 + tsa,
                -2.0 * ((a_amp - 1.0) + (a_amp + 1.0) * cos_w0),
                (a_amp + 1.0) + (a_amp - 1.0) * cos_w0 - tsa,
            )
        }
        "HS" => {
            let tsa = 2.0 * a_amp.sqrt() * al;
            (
                a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w0 + tsa),
                -2.0 * a_amp * ((a_amp - 1.0) + (a_amp + 1.0) * cos_w0),
                a_amp * ((a_amp + 1.0) + (a_amp - 1.0) * cos_w0 - tsa),
                (a_amp + 1.0) - (a_amp - 1.0) * cos_w0 + tsa,
                2.0 * ((a_amp - 1.0) - (a_amp + 1.0) * cos_w0),
                (a_amp + 1.0) - (a_amp - 1.0) * cos_w0 - tsa,
            )
        }
        "LP" => (
            (1.0 - cos_w0) / 2.0, 1.0 - cos_w0, (1.0 - cos_w0) / 2.0,
            1.0 + al, -2.0 * cos_w0, 1.0 - al,
        ),
        "HP" => (
            (1.0 + cos_w0) / 2.0, -(1.0 + cos_w0), (1.0 + cos_w0) / 2.0,
            1.0 + al, -2.0 * cos_w0, 1.0 - al,
        ),
        "NO" => (1.0, -2.0 * cos_w0, 1.0, 1.0 + al, -2.0 * cos_w0, 1.0 - al),
        "AP" => (
            1.0 - al, -2.0 * cos_w0, 1.0 + al,
            1.0 + al, -2.0 * cos_w0, 1.0 - al,
        ),
        other => {
            // Unknown type: pass through (a bare gain-1 biquad). The oracle raises; here we prefer
            // not to abort the whole EQ over one unrecognised line (none occur in the owner's config).
            debug_assert!(false, "unsupported filter type: {other}");
            (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        }
    };
    let c = [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0];
    // Finite + strictly inside the stability triangle (|a2| < 1, |a1| < 1 + a2).
    let stable = c.iter().all(|v| v.is_finite()) && c[4].abs() < 1.0 && c[3].abs() < 1.0 + c[4];
    stable.then_some(c)
}

/// A stateful streaming biquad cascade over one signal. Feeding it block by block yields exactly the
/// same output as filtering the whole signal at once (the per-section state carries across blocks),
/// so there are no clicks at block boundaries — the transposed Direct-Form-II recurrence
/// `scipy.signal.sosfilt` runs, matching the oracle sample for sample. An empty cascade is a
/// pass-through.
#[derive(Debug, Clone)]
pub struct BiquadCascade {
    /// Per section `[b0, b1, b2, a1, a2]` (a0 normalised to 1).
    sections: Vec<[f64; 5]>,
    /// Per section `[d0, d1]` transposed-DF2 state (scipy `zi`).
    z: Vec<[f64; 2]>,
}

impl BiquadCascade {
    pub fn new(sections: Vec<[f64; 5]>) -> Self {
        let z = vec![[0.0; 2]; sections.len()];
        Self { sections, z }
    }

    pub fn from_specs(specs: &[BiquadSpec], fs: f64) -> Self {
        Self::new(specs.iter().filter_map(|s| design(s, fs)).collect())
    }

    pub fn n_sections(&self) -> usize {
        self.sections.len()
    }

    /// Clear the filter memory (next block starts from silence).
    pub fn reset(&mut self) {
        for s in &mut self.z {
            *s = [0.0; 2];
        }
    }

    /// Filter one block in place, advancing the retained state.
    pub fn process_in_place(&mut self, x: &mut [f64]) {
        for sample in x.iter_mut() {
            let mut v = *sample;
            for (s, z) in self.sections.iter().zip(self.z.iter_mut()) {
                let [b0, b1, b2, a1, a2] = *s;
                let y = b0 * v + z[0];
                z[0] = b1 * v + z[1] - a1 * y;
                z[1] = b2 * v - a2 * y;
                v = y;
            }
            *sample = v;
        }
    }
}

/// The linear output chain for one ear: `y = cascade(fir(gain * x))`. All three are optional
/// degenerate cases: `gain` defaults to 1, `fir` to `None` (no convolution), `specs` to empty
/// (pass-through cascade).
#[derive(Debug, Clone, Default)]
pub struct EarChain {
    pub gain: f64,
    pub fir: Option<Vec<f64>>,
    pub specs: Vec<BiquadSpec>,
}

impl EarChain {
    fn empty() -> Self {
        Self { gain: 1.0, fir: None, specs: Vec::new() }
    }
}

/// Apply a per-ear output-bus chain (gain → comp FIR → biquad cascade) to interleaved stereo blocks.
/// Streaming and stateful — one instance per stream; feeding successive blocks equals processing the
/// whole signal at once (the FIR delay line and the biquad state both carry across blocks), so there
/// are no block-boundary clicks. The left and right chains are independent.
pub struct OutputEq {
    block: usize,
    gain: [f64; 2],
    fir: [Option<BusConvolverCore>; 2],
    cascade: [BiquadCascade; 2],
    fir_len: [usize; 2],
    // Per-block scratch (no per-block allocation on the audio path).
    ear_work: Vec<f64>,
    fir_out_l: Vec<f64>,
    fir_out_r: Vec<f64>,
}

impl OutputEq {
    /// Build from two explicit [`EarChain`]s (used by tests and by [`load_output_eq`]). A per-ear
    /// comp FIR is run through a one-bus [`BusConvolverCore`] with the IR in the left slot (the right
    /// slot zeroed), so `out_l` is exactly that ear's convolution.
    pub fn build(left: &EarChain, right: &EarChain, samplerate: u32, block: usize) -> Result<Self, String> {
        if block == 0 {
            return Err("block must be positive".into());
        }
        let mk_fir = |ear: &EarChain| -> Result<Option<BusConvolverCore>, String> {
            match &ear.fir {
                Some(ir) if !ir.is_empty() => {
                    let zeros = vec![0.0; ir.len()];
                    Ok(Some(BusConvolverCore::new(ir, &zeros, 1, ir.len(), block)?))
                }
                _ => Ok(None),
            }
        };
        Ok(Self {
            block,
            gain: [left.gain, right.gain],
            fir: [mk_fir(left)?, mk_fir(right)?],
            cascade: [
                BiquadCascade::from_specs(&left.specs, samplerate as f64),
                BiquadCascade::from_specs(&right.specs, samplerate as f64),
            ],
            fir_len: [left.fir.as_ref().map_or(0, |v| v.len()), right.fir.as_ref().map_or(0, |v| v.len())],
            ear_work: vec![0.0; block],
            fir_out_l: vec![0.0; block],
            fir_out_r: vec![0.0; block],
        })
    }

    /// A one-line summary for the startup log (per-ear gain in dB, comp FIR length, biquad count).
    pub fn summary(&self) -> String {
        let g = |e: usize| 20.0 * self.gain[e].abs().max(1e-12).log10();
        format!(
            "L[gain {:+.1}dB fir {} biquads {}]  R[gain {:+.1}dB fir {} biquads {}]",
            g(0), self.fir_len[0], self.cascade[0].n_sections(),
            g(1), self.fir_len[1], self.cascade[1].n_sections(),
        )
    }

    /// Clear all streaming state (FIR delay lines + biquad memory). Used when the EQ is re-enabled
    /// after a bypass so a stale tail can't leak in as a click.
    pub fn reset(&mut self) {
        for (f, c) in self.fir.iter_mut().zip(self.cascade.iter_mut()) {
            if let Some(conv) = f.as_mut() {
                conv.reset();
            }
            c.reset();
        }
    }

    /// Filter one interleaved stereo block in place. `buf` is `L,R,L,R,…` of length `2 * block`.
    /// Order per ear matches the oracle: `x = gain * x; if fir { x = fir(x); } x = cascade(x)`.
    pub fn process_interleaved(&mut self, buf: &mut [f64]) {
        debug_assert_eq!(buf.len(), self.block * 2);
        let block = self.block;
        for ear in 0..2 {
            let gain = self.gain[ear];
            for n in 0..block {
                self.ear_work[n] = buf[2 * n + ear] * gain;
            }
            if let Some(conv) = self.fir[ear].as_mut() {
                // Disjoint fields: self.fir / self.ear_work / self.fir_out_* borrow separately.
                conv.process_into(&self.ear_work, &mut self.fir_out_l, &mut self.fir_out_r);
                self.ear_work.copy_from_slice(&self.fir_out_l);
            }
            self.cascade[ear].process_in_place(&mut self.ear_work);
            for n in 0..block {
                buf[2 * n + ear] = self.ear_work[n];
            }
        }
    }
}

// ---------------------------------------------------------------------------------------------
// EqualizerAPO config parsing — isolate the post-downmix L/R chain (the port of `output_eq.py`).
// ---------------------------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum Cmd {
    Channel(String),
    Preamp(f64),
    Filter(BiquadSpec),
    /// A convolution whose IR path resolves against the file that declared it.
    Convolution { basedir: PathBuf, rel: String },
    /// A `Copy` reassigns its target buses (only the targets matter — the engine produces the mix).
    Copy(Vec<String>),
}

/// Flatten an EqualizerAPO config into commands, resolving `Include` (relative to each file). Only
/// the commands the owner's presets use are recognised; anything else is ignored, as EqualizerAPO
/// does for commands it does not know.
fn read_commands(path: &Path, depth: usize) -> Result<Vec<Cmd>, String> {
    if depth > 16 {
        return Err("Include nesting too deep (possible cycle)".into());
    }
    let basedir = path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut cmds = Vec::new();
    for raw in text.lines() {
        let line = raw.split('#').next().unwrap_or("").trim(); // strip comments
        if line.is_empty() || !line.contains(':') {
            continue;
        }
        let (head, rest) = line.split_once(':').unwrap();
        let head = head.trim().to_ascii_lowercase();
        let rest = rest.trim();
        if head == "include" {
            let inc = basedir.join(rest.replace('\\', "/"));
            if inc.exists() {
                cmds.extend(read_commands(&inc, depth + 1)?);
                // EqualizerAPO resets the channel selection to "all" when an included file finishes,
                // so a trailing `Channel: R` inside a per-ear comp block does not leak into the parent.
                cmds.push(Cmd::Channel("all".into()));
            }
        } else if head == "channel" {
            cmds.push(Cmd::Channel(rest.to_string()));
        } else if head == "preamp" {
            if let Some(v) = first_number(rest) {
                cmds.push(Cmd::Preamp(v));
            }
        } else if head.starts_with("filter") {
            if let Some(spec) = parse_filter_line(line) {
                cmds.push(Cmd::Filter(spec));
            }
        } else if head == "convolution" {
            cmds.push(Cmd::Convolution { basedir: basedir.clone(), rel: rest.replace('\\', "/") });
        } else if head == "copy" {
            let targets = rest
                .split_whitespace()
                .filter_map(|a| a.split_once('=').map(|(t, _)| t.trim().to_string()))
                .collect();
            cmds.push(Cmd::Copy(targets));
        }
    }
    Ok(cmds)
}

/// One accumulated operation on a bus, in config order.
enum Op {
    Gain(f64),
    Biquad(BiquadSpec),
    Fir(Vec<f64>),
}

/// Isolate the post-downmix per-ear chain for buses L and R, exactly as the oracle's
/// `_extract_ear_chains`: a `Copy` onto a bus clears its accumulation (the engine produces that
/// downmix), so only operations *after* the last downmix — the comp + subjective EQ — survive.
fn extract_ear_chains(cmds: &[Cmd], samplerate: u32) -> Result<(EarChain, EarChain), String> {
    let mut ops_l: Vec<Op> = Vec::new();
    let mut ops_r: Vec<Op> = Vec::new();
    // EqualizerAPO's default selection is all channels (as if `Channel: all`), so commands before
    // the first `Channel:` line still apply.
    let mut selected: Vec<String> = OUTPUT_BUSES.iter().map(|s| s.to_string()).collect();

    for cmd in cmds {
        match cmd {
            Cmd::Channel(sel) => {
                selected = if sel.eq_ignore_ascii_case("all") {
                    OUTPUT_BUSES.iter().map(|s| s.to_string()).collect()
                } else {
                    sel.split_whitespace().map(|s| s.to_string()).collect()
                };
            }
            Cmd::Copy(targets) => {
                for t in targets {
                    if t == "L" {
                        ops_l.clear();
                    } else if t == "R" {
                        ops_r.clear();
                    }
                }
            }
            Cmd::Preamp(_) | Cmd::Filter(_) | Cmd::Convolution { .. } => {
                for bus in &selected {
                    let ops = match bus.as_str() {
                        "L" => &mut ops_l,
                        "R" => &mut ops_r,
                        _ => continue, // non-output bus (L_L, C, …) — the engine's convolution, skip
                    };
                    match cmd {
                        Cmd::Preamp(db) => ops.push(Op::Gain(10f64.powf(db / 20.0))),
                        Cmd::Filter(spec) => ops.push(Op::Biquad(spec.clone())),
                        Cmd::Convolution { basedir, rel } => {
                            let ir = load_ir(&basedir.join(rel), samplerate)?;
                            ops.push(Op::Fir(ir));
                        }
                        _ => unreachable!(),
                    }
                }
            }
        }
    }

    let fold = |ops: Vec<Op>| -> EarChain {
        let mut chain = EarChain::empty();
        for op in ops {
            match op {
                Op::Gain(g) => chain.gain *= g,
                Op::Biquad(s) => chain.specs.push(s),
                Op::Fir(ir) => {
                    chain.fir = Some(match chain.fir.take() {
                        None => ir,
                        Some(prev) => convolve(&prev, &ir), // several convolutions combine into one
                    });
                }
            }
        }
        chain
    };
    Ok((fold(ops_l), fold(ops_r)))
}

/// Direct linear convolution (load-time only, for combining series comp FIRs — the owner's config
/// has at most one per ear, so this is effectively a no-op there).
fn convolve(a: &[f64], b: &[f64]) -> Vec<f64> {
    if a.is_empty() || b.is_empty() {
        return Vec::new();
    }
    let mut out = vec![0.0; a.len() + b.len() - 1];
    for (i, &av) in a.iter().enumerate() {
        for (j, &bv) in b.iter().enumerate() {
            out[i + j] += av * bv;
        }
    }
    out
}

/// Load a mono IR WAV → f64 (channel 0, int→/max, float as-is). The bundled comp IRs are 48 kHz
/// float; a sample-rate mismatch is a hard error (the oracle resamples, but the engine ships a
/// matching-rate comp — a mismatch means the wrong pack/comp pairing, worth surfacing loudly).
fn load_ir(path: &Path, samplerate: u32) -> Result<Vec<f64>, String> {
    let mut r = hound::WavReader::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let spec = r.spec();
    let ch = spec.channels.max(1) as usize;
    // A truncated/corrupt data chunk is a hard error, not a silently zero-filled or short IR.
    let bad = |e: hound::Error| format!("{}: {e}", path.display());
    let samples: Vec<f64> = match spec.sample_format {
        hound::SampleFormat::Float => r
            .samples::<f32>()
            .collect::<Result<Vec<_>, _>>()
            .map_err(bad)?
            .into_iter()
            .step_by(ch)
            .map(|s| s as f64)
            .collect(),
        hound::SampleFormat::Int => {
            let max = (1i64 << (spec.bits_per_sample - 1)) as f64;
            r.samples::<i32>()
                .collect::<Result<Vec<_>, _>>()
                .map_err(bad)?
                .into_iter()
                .step_by(ch)
                .map(|s| s as f64 / max)
                .collect()
        }
    };
    if let Some(i) = samples.iter().position(|v| !v.is_finite()) {
        return Err(format!("{}: non-finite IR tap at sample {i}", path.display()));
    }
    if spec.sample_rate != samplerate {
        return Err(format!(
            "{}: comp IR is {} Hz but the engine runs at {} Hz — the bundled comp is 48 kHz, use a 48 kHz pack",
            path.display(), spec.sample_rate, samplerate
        ));
    }
    Ok(samples)
}

/// Build the output-bus [`OutputEq`] from an EqualizerAPO config file. Parses `config_path`
/// (resolving `Include`), isolates the global per-ear chain that applies to the final L/R buses
/// after the downmix, folds in `global_preamp_db` (the top-level config.txt headroom preamp —
/// e.g. `-20` for the owner's setup), and returns a streaming processor. Errors if the config
/// yields no chain at all (wrong file).
pub fn load_output_eq(
    config_path: &Path,
    samplerate: u32,
    block: usize,
    global_preamp_db: f64,
) -> Result<OutputEq, String> {
    let cmds = read_commands(config_path, 0)?;
    let (mut left, mut right) = extract_ear_chains(&cmds, samplerate)?;

    let empty = |c: &EarChain| c.gain == 1.0 && c.fir.is_none() && c.specs.is_empty();
    if empty(&left) && empty(&right) {
        return Err(format!(
            "{}: no output-bus (L/R) comp/EQ chain found — is this an EqualizerAPO binaural config \
             with a downmix to L/R?",
            config_path.display()
        ));
    }

    // The global headroom preamp (top config.txt) applies to both ears equally — fold it into the
    // per-ear gain. Kept separate from the parsed chain so the parity gate can match the oracle
    // (which does not read config.txt) by passing 0 dB.
    let g = 10f64.powf(global_preamp_db / 20.0);
    left.gain *= g;
    right.gain *= g;

    OutputEq::build(&left, &right, samplerate, block)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn bundled_config() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("hp_eq").join("baked_7p1_rev26_final.txt")
    }

    #[test]
    fn parses_subjective_filters() {
        let s = parse_filter_line("Filter: ON PK Fc 8200 Hz Gain -5 dB BW Oct 1.1").unwrap();
        assert_eq!(s.ftype, "PK");
        assert_eq!(s.fc, 8200.0);
        assert_eq!(s.gain_db, -5.0);
        assert_eq!(s.bw_oct, Some(1.1));
        assert_eq!(s.q, None);

        let ls = parse_filter_line("Filter: ON LS Fc 40 Hz Gain 22 dB").unwrap();
        assert_eq!(ls.ftype, "LS");
        assert_eq!(ls.slope_db, None); // bare shelf → default S

        let shelf = parse_filter_line("Filter: ON HS 6 dB Fc 3000 Hz Gain 0 dB").unwrap();
        assert_eq!(shelf.ftype, "HS");
        assert_eq!(shelf.slope_db, Some(6.0));
        assert_eq!(shelf.gain_db, 0.0);

        let rew = parse_filter_line("Filter  3: ON  PK       Fc   452.0 Hz  Gain  18.00 dB  Q  1.702").unwrap();
        assert_eq!(rew.ftype, "PK");
        assert_eq!(rew.fc, 452.0);
        assert_eq!(rew.gain_db, 18.0);
        assert_eq!(rew.q, Some(1.702));

        assert!(parse_filter_line("Filter: OFF PK Fc 100 Hz Gain 1 dB Q 1").is_none());
        assert!(parse_filter_line("Preamp: -4.5 dB").is_none());
    }

    #[test]
    fn hsc_lsc_aliases() {
        assert_eq!(parse_filter_line("Filter: ON HSC 12 dB Fc 100 Hz Gain -4 dB").unwrap().ftype, "HS");
        assert_eq!(parse_filter_line("Filter: ON LSC 6 dB Fc 100 Hz Gain -4 dB").unwrap().ftype, "LS");
    }

    #[test]
    fn peaking_unity_at_dc_and_gain_at_fc() {
        // A PK biquad: |H| at its centre frequency ≈ its gain; at DC ≈ 0 dB.
        let spec = BiquadSpec { ftype: "PK".into(), fc: 1000.0, gain_db: 6.0, q: Some(1.0), bw_oct: None, slope_db: None };
        let fs = 48000.0;
        let c = design(&spec, fs).unwrap();
        let mag_db = |f: f64| {
            let w = 2.0 * std::f64::consts::PI * f / fs;
            let (b0, b1, b2, a1, a2) = (c[0], c[1], c[2], c[3], c[4]);
            let z1 = num_complex_exp(-w);
            let z2 = num_complex_exp(-2.0 * w);
            let num = (b0 + b1 * z1.0 + b2 * z2.0, b1 * z1.1 + b2 * z2.1);
            let den = (1.0 + a1 * z1.0 + a2 * z2.0, a1 * z1.1 + a2 * z2.1);
            let nm = (num.0 * num.0 + num.1 * num.1).sqrt();
            let dm = (den.0 * den.0 + den.1 * den.1).sqrt();
            20.0 * (nm / dm).log10()
        };
        assert!(mag_db(1000.0) > 5.9 && mag_db(1000.0) < 6.1, "peak {}", mag_db(1000.0));
        assert!(mag_db(20.0).abs() < 0.2, "dc {}", mag_db(20.0));
    }

    fn num_complex_exp(theta: f64) -> (f64, f64) {
        (theta.cos(), theta.sin())
    }

    #[test]
    fn cascade_matches_manual_single_biquad() {
        // One-section cascade equals the hand-run transposed-DF2 recurrence.
        let c = design(&BiquadSpec { ftype: "PK".into(), fc: 1000.0, gain_db: 3.0, q: Some(0.7), bw_oct: None, slope_db: None }, 48000.0).unwrap();
        let mut casc = BiquadCascade::new(vec![c]);
        let x: Vec<f64> = (0..64).map(|n| (n as f64 * 0.3).sin()).collect();
        let mut buf = x.clone();
        casc.process_in_place(&mut buf);
        let (b0, b1, b2, a1, a2) = (c[0], c[1], c[2], c[3], c[4]);
        let (mut d0, mut d1) = (0.0, 0.0);
        for n in 0..x.len() {
            let y = b0 * x[n] + d0;
            d0 = b1 * x[n] + d1 - a1 * y;
            d1 = b2 * x[n] - a2 * y;
            assert!((buf[n] - y).abs() < 1e-12);
        }
    }

    #[test]
    fn copy_onto_bus_clears_prior_accumulation() {
        // The owner's config never lands an op on L/R before the final downmix Copy, so the parity
        // gate can't exercise the "Copy clears the bus" rule (it's a no-op there). Pin it directly:
        // a Preamp + biquad on L, then a Copy onto L (the downmix), then a Preamp — only the ops
        // AFTER the Copy must survive (the pre-Copy accumulation is the engine's own convolution).
        let pk = BiquadSpec { ftype: "PK".into(), fc: 1000.0, gain_db: 3.0, q: Some(1.0), bw_oct: None, slope_db: None };
        let cleared = vec![
            Cmd::Channel("L".into()),
            Cmd::Preamp(6.0),
            Cmd::Filter(pk.clone()),
            Cmd::Copy(vec!["L".into()]),
            Cmd::Preamp(-3.0),
        ];
        let (l, _r) = extract_ear_chains(&cleared, 48000).unwrap();
        assert!((l.gain - 10f64.powf(-3.0 / 20.0)).abs() < 1e-12, "only the post-Copy gain survives, got {}", l.gain);
        assert!(l.specs.is_empty(), "the pre-Copy biquad must be cleared by the Copy");

        // Control: the identical ops WITHOUT the Copy accumulate both preamps and the biquad.
        let kept = vec![Cmd::Channel("L".into()), Cmd::Preamp(6.0), Cmd::Filter(pk), Cmd::Preamp(-3.0)];
        let (l2, _) = extract_ear_chains(&kept, 48000).unwrap();
        assert_eq!(l2.specs.len(), 1);
        assert!((l2.gain - 10f64.powf(6.0 / 20.0) * 10f64.powf(-3.0 / 20.0)).abs() < 1e-12);
    }

    #[test]
    fn commands_before_first_channel_line_apply_to_all_buses() {
        let cmds = vec![Cmd::Preamp(-6.0)];
        let (l, r) = extract_ear_chains(&cmds, 48000).unwrap();
        let g = 10f64.powf(-6.0 / 20.0);
        assert!((l.gain - g).abs() < 1e-12 && (r.gain - g).abs() < 1e-12);
    }

    #[test]
    fn design_rejects_out_of_range_or_unstable_filters() {
        let mk = |fc: f64, q: Option<f64>, bw: Option<f64>| BiquadSpec {
            ftype: "PK".into(), fc, gain_db: 3.0, q, bw_oct: bw, slope_db: None,
        };
        assert!(design(&mk(1000.0, Some(1.0), None), 48000.0).is_some());
        assert!(design(&mk(24000.0, Some(1.0), None), 48000.0).is_none(), "fc at Nyquist");
        assert!(design(&mk(30000.0, Some(1.0), None), 48000.0).is_none(), "fc above Nyquist");
        assert!(design(&mk(0.0, Some(1.0), None), 48000.0).is_none(), "fc 0");
        assert!(design(&mk(1000.0, Some(0.0), None), 48000.0).is_none(), "Q 0");
        assert!(design(&mk(1000.0, None, Some(0.0)), 48000.0).is_none(), "BW 0");
        // A skipped filter leaves the cascade finite on arbitrary input.
        let mut casc = BiquadCascade::from_specs(&[mk(24000.0, Some(1.0), None)], 48000.0);
        let mut x = vec![1.0; 16];
        casc.process_in_place(&mut x);
        assert!(x.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn loads_the_owner_config_chain() {
        // The bundled baked config must yield a non-trivial per-ear chain: a comp FIR + biquads on
        // both ears, and the right ear's extra -1.2 dB comp preamp (so its gain differs from L).
        let eq = load_output_eq(&bundled_config(), 48000, 256, 0.0).expect("load");
        // 31 subjective (both) + 19 left comp + 18 right comp biquads; FIR on both ears.
        assert!(eq.cascade[0].n_sections() >= 45, "L biquads {}", eq.cascade[0].n_sections());
        assert!(eq.cascade[1].n_sections() >= 45, "R biquads {}", eq.cascade[1].n_sections());
        assert!(eq.fir_len[0] > 10000 && eq.fir_len[1] > 10000, "comp FIRs {:?}", eq.fir_len);
        assert!((eq.gain[0] - eq.gain[1]).abs() > 1e-6, "right -1.2 dB comp preamp should split the gains");
    }

    /// The parity gate (#193): the Rust chain must reproduce the independent Python oracle
    /// (`binaural_headtracker.output_eq.load_output_eq` on the same config) to a tiny tolerance.
    /// The oracle's per-ear impulse responses are committed as a **frozen golden fixture**
    /// (`hp_eq/oracle_golden_48k.f32`, left then right, 32768 samples each). The oracle itself —
    /// and the `tools/gen_hp_eq_golden.py` that drove it — were removed with the Python prototype
    /// (chore/remove-python); the fixture is the permanent snapshot that keeps this gate honest,
    /// since it was produced by code *other* than the Rust implementation under test (recover the
    /// generator from git history if the oracle ever needs to be re-derived). Here we drive the
    /// same impulse through the Rust `OutputEq`, then compare both in the time domain (max abs
    /// sample error) and the frequency domain (max |ΔdB| across 20 Hz–20 kHz) — the "measured max
    /// error (dB) vs the oracle" the brief asks for.
    #[test]
    fn parity_vs_python_oracle() {
        use realfft::RealFftPlanner;

        const N: usize = 32768;
        let golden_path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("hp_eq").join("oracle_golden_48k.f32");
        let bytes = match std::fs::read(&golden_path) {
            Ok(b) => b,
            Err(e) => panic!("missing frozen golden fixture {} ({e}) — it is committed data (recover the Python oracle generator from git history if it must be re-derived)", golden_path.display()),
        };
        assert_eq!(bytes.len(), N * 2 * 4, "golden must be 2×{N} f32 LE");
        let golden: Vec<f64> = bytes.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]) as f64).collect();
        let (gold_l, gold_r) = golden.split_at(N);

        // Drive the same unit impulse (into both ears) through the Rust chain, block by block.
        let block = 256;
        let mut eq = load_output_eq(&bundled_config(), 48000, block, 0.0).expect("load");
        let mut rust_l = vec![0.0f64; N];
        let mut rust_r = vec![0.0f64; N];
        let mut buf = vec![0.0f64; block * 2];
        for blk in 0..(N / block) {
            for v in buf.iter_mut() {
                *v = 0.0;
            }
            if blk == 0 {
                buf[0] = 1.0; // impulse, sample 0, both ears
                buf[1] = 1.0;
            }
            eq.process_interleaved(&mut buf);
            for n in 0..block {
                rust_l[blk * block + n] = buf[2 * n];
                rust_r[blk * block + n] = buf[2 * n + 1];
            }
        }

        // Time-domain parity.
        let max_abs = |a: &[f64], b: &[f64]| a.iter().zip(b).map(|(x, y)| (x - y).abs()).fold(0.0f64, f64::max);
        let err_l = max_abs(&rust_l, gold_l);
        let err_r = max_abs(&rust_r, gold_r);

        // Frequency-domain parity: max |ΔdB| across the audio band.
        let mut planner = RealFftPlanner::<f64>::new();
        let fft = planner.plan_fft_forward(N);
        let spectrum = |ir: &[f64]| {
            let mut inp = ir.to_vec();
            let mut out = fft.make_output_vec();
            fft.process(&mut inp, &mut out).unwrap();
            out
        };
        let max_db = |a: &[f64], b: &[f64]| {
            let sa = spectrum(a);
            let sb = spectrum(b);
            let bin_hz = 48000.0 / N as f64;
            let mut worst = 0.0f64;
            for k in 1..sa.len() {
                let f = k as f64 * bin_hz;
                if f < 20.0 || f > 20000.0 {
                    continue;
                }
                let ma = sa[k].norm().max(1e-12);
                let mb = sb[k].norm().max(1e-12);
                let d = (20.0 * (ma / mb).log10()).abs();
                worst = worst.max(d);
            }
            worst
        };
        let db_l = max_db(&rust_l, gold_l);
        let db_r = max_db(&rust_r, gold_r);

        println!("HP_EQ PARITY vs oracle: time-domain max|Δ| L={err_l:.3e} R={err_r:.3e}; freq-domain max|ΔdB| L={db_l:.5} R={db_r:.5}");
        assert!(err_l < 1e-5 && err_r < 1e-5, "time-domain parity: L={err_l:.3e} R={err_r:.3e}");
        assert!(db_l < 0.05 && db_r < 0.05, "freq-domain parity: L={db_l:.5}dB R={db_r:.5}dB");
    }
}
