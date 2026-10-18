"""Global output-bus comp + EQ — the "full-chain" stereo stage (B5.3).

The baked BRIRs this renderer convolves are a **bare** 7.1→binaural bake: they carry
the room and the directions, but *not* the owner's headphone compensation or subjective
equalization. In EqualizerAPO those live as a **global per-ear chain applied once to the
stereo mix**, after the per-speaker convolution and downmix::

    ... per-channel Convolution + Copy downmix to L,R ...   # the renderer already does this
    Channel: L R
    Include: binaural\\...\\audeze_comp.txt   # headphone comp: per-ear FIR + biquads
    Preamp: -4.5 dB
    Filter: ...                               # subjective EQ: ~30 biquads (both ears)

Because the chain is linear and time-invariant and operates on the *final* L/R buses, it
is exactly an output-bus stage — it never touches the individual BRIRs, so the world-lock
steering is untouched. :func:`load_output_eq` reads an EqualizerAPO config, isolates that
post-downmix L/R chain (a scalar gain + an optional per-ear comp FIR + a per-ear biquad
cascade), and returns an :class:`OutputEQ` the apps apply to every rendered stereo block.

Equivalence is the whole point: feeding this stage the bare neutral render (which
``test_7p1_neutral_equals_static_render`` pins equal to the EqualizerAPO downmix) reproduces
the owner's EqualizerAPO output to floating error — so the live renderer now *sounds* and
*localises* like the config they tuned by ear, not like the thin bare bake (journal 13/14).

How the global chain is isolated. The config is walked as EqualizerAPO would apply it
(``Include`` resolved, and the channel selection reset to ``all`` when an include ends — see
journal 18). Every ``Preamp``/``Filter``/``Convolution`` that lands on bus **L** or **R** is
accumulated; a ``Copy`` that reassigns L or R *clears* that bus's accumulation, because the
copy overwrites the bus with a fresh sum (the downmix) that the renderer itself produces. So
only the operations *after* the final downmix survive — exactly the comp + subjective EQ.

Pure numpy/scipy; the biquad math is :mod:`binaural_headtracker.eq` (EqualizerAPO-exact,
byte-parity with the sibling on the owner's real filters) and the FIR is
:class:`binaural_headtracker.convolve.PartitionedConvolver`, so this stays headless-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .convolve import PartitionedConvolver
from .eq import BiquadCascade, BiquadSpec, design_sos, parse_filter_line

__all__ = ["OutputEQ", "EarChain", "load_output_eq", "build_output_eq"]

# The final stereo output buses an EqualizerAPO binaural preset mixes down to.
_OUTPUT_BUSES = ("L", "R")


@dataclass
class _Command:
    kind: str  # channel | preamp | filter | convolution | copy
    payload: object
    basedir: Path


def _read_commands(path: str | Path, _depth: int = 0) -> list[_Command]:
    """Flatten an EqualizerAPO config into commands, resolving ``Include``.

    Only the commands this project's presets use are recognised (``Channel``, ``Preamp``,
    ``Filter``, ``Convolution``, ``Copy``, ``Include``); anything else is ignored, as
    EqualizerAPO does for commands it does not know. ``Convolution`` payloads keep the
    file's own ``basedir`` so a relative IR path resolves against the file it came from.
    """
    if _depth > 16:
        raise ValueError("Include nesting too deep (possible cycle)")
    path = Path(path)
    basedir = path.parent
    cmds: list[_Command] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()  # strip comments
        if not line or ":" not in line:
            continue
        head, _, rest = line.partition(":")
        head = head.strip().lower()
        rest = rest.strip()
        if head == "include":
            inc = basedir / rest.replace("\\", "/")
            if inc.exists():
                cmds.extend(_read_commands(inc, _depth + 1))
                # EqualizerAPO resets the channel selection to "all" when an included file
                # finishes; without this a trailing `Channel: R` inside a per-ear comp block
                # would leak and apply the parent's following filters to one ear only.
                cmds.append(_Command("channel", "all", basedir))
        elif head == "channel":
            cmds.append(_Command("channel", rest, basedir))
        elif head == "preamp":
            m = re.search(r"(-?\d+(?:\.\d+)?)", rest)
            if m:
                cmds.append(_Command("preamp", float(m.group(1)), basedir))
        elif head.startswith("filter"):
            spec = parse_filter_line(line)
            if spec is not None:
                cmds.append(_Command("filter", spec, basedir))
        elif head == "convolution":
            cmds.append(_Command("convolution", rest.replace("\\", "/"), basedir))
        elif head == "copy":
            # Record only the assignment targets; the renderer produces the downmix itself,
            # so a copy onto L/R marks "everything before here is the renderer's job".
            targets = [a.split("=", 1)[0].strip() for a in rest.split() if "=" in a]
            cmds.append(_Command("copy", targets, basedir))
    return cmds


def _load_ir(path: Path, samplerate: int) -> np.ndarray:
    """Load a mono IR WAV as float64, resampled to ``samplerate`` if needed."""
    import warnings

    from scipy.io import wavfile

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sr, data = wavfile.read(path)
    data = np.asarray(data)
    if data.ndim > 1:
        data = data[:, 0]  # a comp IR is mono; take the first channel defensively
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / float(np.iinfo(data.dtype).max)
    else:
        data = data.astype(np.float64)
    if sr != samplerate:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(sr), int(samplerate))
        data = resample_poly(data, samplerate // g, sr // g)
    return data


@dataclass
class EarChain:
    """The linear output chain for one ear: ``y = cascade(fir(gain * x))``.

    All three are optional degenerate cases: ``gain`` defaults to 1, ``fir`` to ``None``
    (no convolution), ``specs`` to empty (pass-through cascade). ``gain`` collects every
    scalar ``Preamp`` on the bus, ``fir`` is the comp impulse response (several convolutions
    are pre-combined into one), and ``specs`` are the biquads in EqualizerAPO order.
    """

    gain: float = 1.0
    fir: np.ndarray | None = None
    specs: list[BiquadSpec] = field(default_factory=list)


def _extract_ear_chains(commands: list[_Command], samplerate: int) -> dict[str, EarChain]:
    """Isolate the post-downmix per-ear chain for the output buses L and R."""
    # Per bus, an ordered op list; a Copy onto the bus clears it (the downmix is the
    # renderer's output, so only operations after the last downmix matter).
    ops: dict[str, list[tuple[str, object]]] = {b: [] for b in _OUTPUT_BUSES}
    selected: list[str] = []
    for cmd in commands:
        if cmd.kind == "channel":
            sel = cmd.payload
            selected = list(_OUTPUT_BUSES) if sel.lower() == "all" else sel.split()
        elif cmd.kind == "copy":
            for target in cmd.payload:
                if target in ops:
                    ops[target] = []
        elif cmd.kind in ("preamp", "filter", "convolution"):
            for bus in selected:
                if bus not in ops:
                    continue
                if cmd.kind == "preamp":
                    ops[bus].append(("gain", 10.0 ** (float(cmd.payload) / 20.0)))
                elif cmd.kind == "filter":
                    ops[bus].append(("biquad", cmd.payload))
                else:
                    ops[bus].append(("fir", _load_ir(cmd.basedir / cmd.payload, samplerate)))

    chains: dict[str, EarChain] = {}
    for bus, op_list in ops.items():
        gain = 1.0
        fir: np.ndarray | None = None
        specs: list[BiquadSpec] = []
        for kind, payload in op_list:
            if kind == "gain":
                gain *= float(payload)
            elif kind == "biquad":
                specs.append(payload)  # type: ignore[arg-type]
            else:  # fir — several convolutions in series combine into one IR
                ir = np.asarray(payload, dtype=np.float64)
                fir = ir if fir is None else np.convolve(fir, ir)
        chains[bus] = EarChain(gain=gain, fir=fir, specs=specs)
    return chains


class OutputEQ:
    """Apply a per-ear output-bus chain (gain + comp FIR + biquads) to stereo blocks.

    Streaming and stateful — one instance per stream. ``process_block`` takes a
    ``(block_size, 2)`` stereo block and returns ``(block_size, 2)``; feeding successive
    blocks equals processing the whole signal at once (the FIR delay line and the biquad
    state both carry across blocks), so there are no block-boundary clicks. The left and
    right chains are independent (the comp FIR and comp biquads differ per ear).

    Adds one block of latency per ear from the :class:`PartitionedConvolver` (no extra
    algorithmic delay); the apps' output FIFO cushion absorbs it. Build one with
    :func:`load_output_eq`.
    """

    def __init__(self, left: EarChain, right: EarChain, *, block_size: int, samplerate: int):
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        self._block = int(block_size)
        self._samplerate = int(samplerate)
        self._gain = (left.gain, right.gain)
        self._fir = [
            PartitionedConvolver(ear.fir, block_size) if ear.fir is not None and ear.fir.size
            else None
            for ear in (left, right)
        ]
        self._cascade = [BiquadCascade(design_sos(ear.specs, samplerate)) for ear in (left, right)]

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._samplerate

    def process_block(self, block) -> np.ndarray:
        """Filter one ``(block_size, 2)`` stereo block → ``(block_size, 2)``."""
        block = np.asarray(block, dtype=np.float64)
        if block.ndim != 2 or block.shape != (self._block, 2):
            raise ValueError(f"block must be ({self._block}, 2); got {block.shape}")
        out = np.empty_like(block)
        for ear in (0, 1):
            x = block[:, ear] * self._gain[ear]
            if self._fir[ear] is not None:
                x = self._fir[ear].process_block(x)
            out[:, ear] = self._cascade[ear].process_block(x)
        return out


def build_output_eq(
    left: EarChain, right: EarChain, *, samplerate: int, block_size: int
) -> OutputEQ:
    """Construct an :class:`OutputEQ` from two explicit :class:`EarChain`\\ s (for tests)."""
    return OutputEQ(left, right, block_size=block_size, samplerate=samplerate)


def load_output_eq(
    config_path: str | Path, *, samplerate: int, block_size: int
) -> OutputEQ:
    """Build the output-bus :class:`OutputEQ` from an EqualizerAPO config file.

    Parses ``config_path`` (resolving ``Include``), isolates the global per-ear chain that
    applies to the final L/R stereo buses *after* the downmix, and returns a streaming
    :class:`OutputEQ`. Comp FIRs are resolved relative to the file that references them and
    resampled to ``samplerate`` if necessary. Raises if the config yields no chain at all
    (nothing to apply — likely the wrong file).
    """
    commands = _read_commands(config_path)
    chains = _extract_ear_chains(commands, samplerate)
    left, right = chains["L"], chains["R"]
    empty = (
        left.gain == 1.0 and right.gain == 1.0
        and left.fir is None and right.fir is None
        and not left.specs and not right.specs
    )
    if empty:
        raise ValueError(
            f"{config_path}: no output-bus (L/R) comp/EQ chain found — is this an "
            "EqualizerAPO binaural config with a downmix to L/R?"
        )
    return OutputEQ(left, right, block_size=block_size, samplerate=samplerate)
