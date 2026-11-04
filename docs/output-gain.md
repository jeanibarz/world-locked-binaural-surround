# Output gain and level meters

Use **Output gain** below the cockpit's main panels to adjust loudness without changing
the BRIRs or headphone EQ. The range is −24 dB up to the configurable boost ceiling
(**Max output gain**, default +24 dB); **0 dB** restores the previous engine level. A positive
value increases level. The setting survives restarts and applies with headphone EQ enabled or
bypassed.

## Raising the maximum

For a very quiet source, +24 dB of boost may not be enough. **Settings → Audio output →
Max output gain** raises the ceiling of the Output gain slider in 6 dB steps, from 0 dB up to a
+60 dB hard limit (a hearing-safety and overflow guard). The slider's upper bound follows the
ceiling live. Lowering the ceiling below the current gain pulls the gain down to the new limit;
a gain already within the new range is left unchanged. The attenuation floor stays fixed at
−24 dB — only the positive headroom is configurable, since attenuation is always safe. Like the
gain itself, the ceiling survives restarts and defaults to +24 dB for existing settings files.

Start at 0 dB and increase gradually while normal material plays. Watch **Peak**, **RMS**
(the root-mean-square signal level), and **Clipped samples**. Reduce gain if the clipping count grows.
The counter remains visible until the engine restarts, so an old count alone does not mean
clipping is still occurring. The control waits for engine confirmation; it reports a failure
if a requested value cannot be confirmed.

## What the meters measure

Peak and RMS cover the rendered stereo samples since the previous engine heartbeat
(about 50 ms), after EQ and adjustable gain but before the safety clamp and output FIFO.
Both use dBFS: 0 dBFS is digital full scale. Positive readings expose requested overshoot,
which the clamp cuts off. Silence or an empty interval displays −∞. The peak marker and
RMS bar span −60 to 0 dBFS; numeric peak readings can exceed that range.

Clipped samples counts individual left/right samples whose magnitude exceeded 1 before
the clamp. Invalid samples counts non-finite samples scrubbed at the gain stage. Samples
removed earlier by the EQ input guard are not included. These are engine-stage meters;
they do not measure the later output resampler, Windows mixer, DAC, or acoustic loudness.
They are sample peaks, not oversampled true peaks.

The existing −20 dB preamp remains in place when headphone EQ is enabled. Adjustable gain follows the whole
EQ chain and uses the same 10 ms ramp for both ears. No limiter or automatic gain control
is added. Turning up gain can clip; the hard clamp prevents values outside full scale but
does not preserve an overloaded waveform.

BRIR impulse sample peaks are not playback headroom. Normalizing each BRIR independently
would change interaural and speaker balance. Use the complete signal chain's measurements.

## API and verification

`POST /api/set_output_gain` with JSON `[6.0]` requests +6 dB. Finite values between −24 and
the current **Max output gain** ceiling are accepted; invalid or out-of-range values leave the
setting unchanged. The queued HTTP response is not confirmation: read `output_gain_db` from
`/api/get_config` or `output_level.gain_db` from SSE. The additive setting defaults to 0 dB for
existing settings files.

`POST /api/set_max_gain` with JSON `[36.0]` raises the ceiling to +36 dB. The request is
clamped to `[max_gain_min_db, max_gain_limit_db]` (0 … +60 dB, both reported by
`/api/get_config`); a non-finite request falls back to the +24 dB default. When the new ceiling
is below the current gain, the gain is clipped down to it in the same move. Read back
`max_gain_db` from `/api/get_config`, or `output_level.max_gain_db` from SSE. The ceiling
defaults to +24 dB for existing settings files.

SSE `output_level` also carries `gain_db`, the live `max_gain_db` ceiling, nullable
`peak_dbfs` / `rms_dbfs`, interval `clipped_samples`, and lifetime `clipped_total` /
`non_finite_total`.

Portable DSP and wire-contract tests run with `cargo test --manifest-path rust-engine/Cargo.toml`.
Optional browser tests: `python3 -m pytest tests/test_output_gain_ui.py` (Playwright/Chromium).
The separate Windows host check uses isolated settings and a finite run; see
`spikes/output_gain_host_check.py --help`. Choose unused endpoints when running it.
