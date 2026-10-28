"""Guards for the standalone-bundle assembler ``host/bundle-engine.sh`` (#238).

The script builds the end-user ``wlbs_engine.exe`` distributable. Bug #238: it copied the exe,
``assets/``, ``packs/default/`` and ``web_cockpit/`` but **not** ``hp_eq/`` — so a script-built
bundle shipped without the headphone-compensation / output-EQ config the engine defaults *on*,
and the only signal was a stderr ``HP_EQ_WARN … headphone EQ disabled`` (the bare bake: thinner
timbre, less stable image). The owner's Python launcher was unaffected (it passes the config via
``--output-eq``); the script-built bundle is the artifact that regressed.

The full script can't run here (it cross-builds for Windows via ``cargo xwin`` and copies
host-only ONNX/BRIR assets from ``/mnt/c``), so these tests check the two things the fix must
guarantee without a build:

* the script actually copies ``rust-engine/hp_eq`` into the bundle, and its header-comment
  bundle-layout stays in sync with what it copies (acceptance criterion: header matches copies);
* the source ``rust-engine/hp_eq/`` really is a *loadable* "default" profile — the config exists
  under a name the engine looks for, and every FIR it references *inside* the profile dir is
  present — so the copy ships a working profile, not a dangling config (acceptance criterion: the
  bundle includes a working ``hp_eq/``).

The profile-resolution rules mirrored here come from ``rust-engine/src/hp_profiles.rs``
(``PROFILE_CONFIG_NAMES``) and ``rust-engine/src/hp_eq.rs`` (``read_commands`` follows ``Include:``
relative to the including file; ``Convolution:`` paths resolve relative to their config's dir).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "host" / "bundle-engine.sh"
HP_EQ_SRC = REPO / "rust-engine" / "hp_eq"

# Canonical config filenames a profile dir may use, in priority order — must match
# ``PROFILE_CONFIG_NAMES`` in rust-engine/src/hp_profiles.rs.
PROFILE_CONFIG_NAMES = ("config.txt", "baked_7p1_rev26_final.txt")


def _script_text() -> str:
    assert SCRIPT.is_file(), f"missing bundle script: {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


def _header_layout_tokens(text: str) -> list[str]:
    """Raw first path tokens from the header-comment layout block (``#   <bundle>/`` …).

    Each layout line looks like ``#     packs/default/   the default BRIR pack``; we return the
    first path token verbatim (``packs/default/``, ``wlbs_engine.exe``, ``hp_eq/``), so callers can
    tell a root file (no slash) from a sub-directory entry (has a slash).
    """
    tokens: list[str] = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^#\s+<bundle>/\s*$", line):
            in_block = True
            continue
        if not in_block:
            continue
        m = re.match(r"^#\s+([A-Za-z0-9_./-]+)\s{2,}\S", line)
        if not m:
            # First comment line in the block that isn't an "entry   description" row ends it.
            break
        tokens.append(m.group(1))
    return tokens


def _header_dir_entries(text: str) -> set[str]:
    """Top-level sub-directory entries the header advertises (``assets`` from ``assets/``).

    A directory entry is a token containing a ``/`` (``assets/``, ``packs/default/``, ``hp_eq/``);
    root-level files (``wlbs_engine.exe``, ``DirectML.dll``) have none and are excluded — they're
    copied to the bundle root, not as named sub-entries.
    """
    return {tok.strip("/").split("/")[0] for tok in _header_layout_tokens(text) if "/" in tok}


def _copied_top_level_entries(text: str) -> set[str]:
    r"""Top-level ``$OUT/<entry>`` destinations the script writes via ``mkdir``/``cp``.

    Matches ``"$OUT/foo"`` and ``"$OUT/foo/bar"`` destination arguments; the bare ``"$OUT/"`` dest
    (the exe / DLL copied to the bundle root) is intentionally not a named sub-entry.
    """
    entries: set[str] = set()
    for m in re.finditer(r'"\$OUT/([A-Za-z0-9_][A-Za-z0-9_./-]*)"', text):
        top = m.group(1).strip("/").split("/")[0]
        if top:
            entries.add(top)
    return entries


def test_script_copies_hp_eq_into_bundle():
    """The fix: the script must copy the source ``rust-engine/hp_eq`` tree into ``$OUT/hp_eq``."""
    text = _script_text()
    assert re.search(
        r'cp\s+-r\s+"\$REPO/rust-engine/hp_eq"\s+"\$OUT/hp_eq"', text
    ), "bundle-engine.sh must `cp -r \"$REPO/rust-engine/hp_eq\" \"$OUT/hp_eq\"` (#238)"


def test_header_layout_lists_hp_eq():
    """Acceptance: the header-comment bundle layout must list ``hp_eq/``."""
    assert "hp_eq" in _header_dir_entries(_script_text()), (
        "the header-comment bundle layout in bundle-engine.sh must list hp_eq/"
    )


def test_header_layout_matches_what_is_copied():
    """Acceptance: the header layout comment matches the sub-directories the script copies.

    Every sub-directory the header advertises must be one the script creates under ``$OUT/``, and
    every sub-directory the script creates must be documented — so the two can't drift, which is
    exactly how hp_eq/ went missing from both. (Root-level files — the exe and DirectML.dll, copied
    to the bundle root — are excluded on both sides.)
    """
    text = _script_text()
    header = _header_dir_entries(text)
    copied = _copied_top_level_entries(text)
    assert header, "could not parse the header-comment bundle-layout block"
    assert copied, "could not parse any $OUT/<entry> destinations from the script"
    assert header == copied, (
        "header-comment bundle layout and actual copies disagree: "
        f"only in header={sorted(header - copied)}, only copied={sorted(copied - header)}"
    )


def test_source_hp_eq_is_a_resolvable_default_profile():
    """The copied ``hp_eq/`` must be a loadable 'default' profile, not a dangling config.

    Mirrors the engine: a profile dir is loadable iff it holds a config under a canonical name, and
    every ``Include:``/``Convolution:`` FIR the engine actually *loads* must exist. The engine only
    reconstructs the final L/R output buses, so it loads a FIR only when the command is selected on
    bus L or R; the pre-downmix ``baked_7p1_test\\*.wav`` convolutions are selected on per-speaker
    buses (``L_L``, ``R_L``, …) the loader skips, so — although they resolve *inside* ``hp_eq/`` —
    they're never opened and are deliberately not required here (see ``loads_on_output_bus`` below).
    """
    assert HP_EQ_SRC.is_dir(), f"missing source profile dir: {HP_EQ_SRC}"

    config = next((HP_EQ_SRC / n for n in PROFILE_CONFIG_NAMES if (HP_EQ_SRC / n).is_file()), None)
    assert config is not None, (
        f"{HP_EQ_SRC} has no config under a canonical name {PROFILE_CONFIG_NAMES} — the engine "
        "would not discover a 'default' profile here"
    )

    root = HP_EQ_SRC.resolve()

    def is_inside(p: Path) -> bool:
        try:
            return p.is_relative_to(root)
        except AttributeError:  # Python < 3.9 fallback (repo targets 3.10+, kept defensive)
            return str(p).startswith(str(root))

    def loads_on_output_bus(selected) -> bool:
        # The engine reconstructs only the final L/R output buses (hp_eq.rs OUTPUT_BUSES): a
        # command loads a FIR iff "all" is selected or the selection includes L or R. Per-speaker
        # buses (L_L, R_L, …) — the pre-downmix bake — are skipped, so their FIRs aren't needed.
        return selected == "all" or bool(set(selected) & {"L", "R"})

    def referenced_files(cfg: Path, selected="all", depth: int = 0) -> list[Path]:
        # Mirrors read_commands + extract_ear_chains: follows Include: relative to the including
        # file (resetting the channel to "all" when it returns, as EqualizerAPO does), and collects
        # only the Convolution:/Include FIRs that actually load on the L/R output bus and live
        # inside the profile dir — the files the bundled profile must ship to render on load.
        assert depth <= 16, "Include nesting too deep (possible cycle)"
        out: list[Path] = []
        for raw in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            head, rest = line.split(":", 1)
            head, rest = head.strip().lower(), rest.strip().replace("\\", "/")
            if head == "channel":
                selected = "all" if rest.lower() == "all" else rest.split()
                continue
            if not rest:
                continue
            target = (cfg.parent / rest).resolve()
            if head == "include":
                if is_inside(target) and target.is_file() and loads_on_output_bus(selected):
                    out.append(target)
                    out.extend(referenced_files(target, selected, depth + 1))
                selected = "all"  # EqualizerAPO resets channel selection after an Include returns
            elif head == "convolution" and is_inside(target) and loads_on_output_bus(selected):
                out.append(target)
        return out

    missing = [str(p.relative_to(root)) for p in referenced_files(config) if not p.is_file()]
    assert not missing, (
        f"default profile {config.name} references FIR/Include files absent from {HP_EQ_SRC}: "
        f"{missing} — the bundled profile would fail to load"
    )


def test_test_only_golden_not_advertised_in_layout():
    """The parity golden is a test fixture, not a runtime asset — it must not be a bundle entry.

    ``oracle_golden_48k.f32`` is generated by tools/gen_hp_eq_golden.py and read only by the Rust
    parity test; the script drops it from the bundle. Guard that it never becomes an advertised
    bundle entry (keeping the end-user artifact free of test bloat).
    """
    assert "oracle_golden_48k.f32" not in " ".join(_header_layout_tokens(_script_text()))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
