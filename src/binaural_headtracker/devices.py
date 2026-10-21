"""Camera & audio-device selection for the host GUIs (B6.2).

The host apps (``apps/calibration_gui.py``, ``apps/voicemeeter_passthrough.py``) need to
let the owner pick *which* webcam and *which* audio output to use — on Windows there
are usually several (the laptop cam, a USB cam; the speakers, the headphones, a
VoiceMeeter endpoint). This module splits that the project's way:

* **Pure selection logic** (unit-tested, no cv2/sounddevice/tk): turn a raw device list
  into labelled :class:`DeviceChoice`\\ s, pick a sensible default, and resolve a
  command-line ``--audio-device`` spec (index or name substring) to a device index.
* **Thin host enumeration + a Tk settings dialog** (lazy imports, host-only): probe the
  cameras, query the output devices, and show a small pre-launch menu with two
  dropdowns. These can't run on headless CI/WSL, so they are kept out of the tested core
  exactly like the cv2 drawing in the apps.

So the part that can be *wrong* (labelling, default choice, CLI spec resolution) is
covered headless; the part that needs real hardware is a minimal shell.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceChoice:
    """One selectable device: its backend ``index`` and a human ``label``."""

    index: int
    label: str


def output_device_choices(
    raw_devices, hostapi_names=None, default_index: int | None = None
) -> list[DeviceChoice]:
    """Labelled choices for the **output-capable** devices in a ``query_devices()`` list.

    ``raw_devices`` is the list ``sounddevice.query_devices()`` returns — each item a
    mapping with at least ``name`` and ``max_output_channels`` (and optionally
    ``hostapi``, an index into ``hostapi_names``). Only devices with
    ``max_output_channels > 0`` are kept; the label is ``"<name> (<hostapi>, Nch)"`` and
    the default device (``default_index``, if it is output-capable) is marked ``*``. The
    returned ``index`` is each device's position in ``raw_devices`` — the value
    ``sounddevice`` expects as its ``device=`` argument.
    """
    choices: list[DeviceChoice] = []
    for i, dev in enumerate(raw_devices):
        if int(dev.get("max_output_channels", 0)) <= 0:
            continue
        name = str(dev.get("name", f"device {i}")).strip()
        parts = [name]
        api = dev.get("hostapi")
        if hostapi_names is not None and api is not None and 0 <= int(api) < len(hostapi_names):
            parts.append(str(hostapi_names[int(api)]))
        parts.append(f"{int(dev['max_output_channels'])}ch")
        label = f"{parts[0]} ({', '.join(parts[1:])})"
        if default_index is not None and i == default_index:
            label = f"* {label}"
        choices.append(DeviceChoice(index=i, label=label))
    return choices


def camera_choices(indices) -> list[DeviceChoice]:
    """Labelled choices for a list of working camera indices (``[0, 1, …]``)."""
    return [DeviceChoice(index=int(i), label=f"Camera {int(i)}") for i in indices]


# When one device (e.g. "FiiO K11") is exposed by several Windows host APIs, prefer the
# lowest-latency one. WASAPI (shared, and the only one that supports exclusive mode) beats
# WDM-KS, DirectSound and especially MME, whose shared buffer is ~90–100 ms (journal 43).
_HOSTAPI_PRIORITY = ("wasapi", "wdm-ks", "directsound", "mme")


def resolve_output_device(spec, choices: list[DeviceChoice]) -> int | None:
    """Resolve an ``--audio-device`` spec to a device index.

    ``spec`` may be ``None`` (use the system default → ``None``, which tells
    ``sounddevice`` to use its default), an ``int`` / all-digit string (a device index,
    returned as-is), or a case-insensitive substring matched against the choice labels.
    When the substring matches **several** devices — the same hardware under MME, DirectSound,
    WASAPI, … — the **lowest-latency host API wins** (WASAPI first), so a bare name like
    ``"FiiO"`` lands on WASAPI rather than MME's big buffer. Raises :class:`ValueError` if a
    name spec matches nothing, so a typo fails loudly instead of playing the wrong device.
    """
    if spec is None:
        return None
    s = str(spec).strip()
    if s == "":
        return None
    if s.lstrip("-").isdigit():
        return int(s)
    low = s.lower()
    matches = [c for c in choices if low in c.label.lower()]
    if not matches:
        raise ValueError(
            f"no output device matching {spec!r}; available: "
            + ", ".join(c.label for c in choices)
        )
    if len(matches) == 1:
        return matches[0].index

    def rank(c: DeviceChoice) -> int:
        lab = c.label.lower()
        for i, api in enumerate(_HOSTAPI_PRIORITY):
            if api in lab:
                return i
        return len(_HOSTAPI_PRIORITY)  # unknown host API → after the known ones

    return min(matches, key=rank).index


def default_choice_label(choices: list[DeviceChoice], index: int | None) -> str | None:
    """The label of the choice whose ``index`` equals ``index`` (for pre-selecting a menu)."""
    if index is None:
        return None
    for c in choices:
        if c.index == index:
            return c.label
    return None


# --- host-only (lazy imports; not run on CI) --------------------------------------


def probe_cameras(max_index: int = 5):
    """Return the indices ``0..max_index-1`` that open as a camera (host-only, cv2)."""
    import cv2

    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        ok = cap.isOpened()
        cap.release()
        if ok:
            found.append(i)
    return found


def query_output_devices():
    """Return ``(choices, default_index)`` for the output devices (host-only, sounddevice)."""
    import sounddevice as sd

    raw = sd.query_devices()
    hostapis = [a["name"] for a in sd.query_hostapis()]
    try:
        default_index = sd.default.device[1]
        if isinstance(default_index, (list, tuple)):
            default_index = default_index[0]
    except Exception:
        default_index = None
    return output_device_choices(raw, hostapis, default_index), default_index


def settings_dialog(cameras, audio, *, title="Device settings",
                    camera_default=None, audio_default=None, demo=False):
    """Show a small Tk pre-launch menu; return ``{'camera', 'audio_device'}`` or ``None``.

    ``cameras`` / ``audio`` are :class:`DeviceChoice` lists. Returns the chosen backend
    indices on **Start**, or ``None`` if the window is closed/cancelled. ``audio`` may be
    empty (camera-only apps, e.g. the calibration GUI) — the audio row is then hidden.
    Host-only (Tk); imported lazily so the module stays importable on CI.
    """
    import tkinter as tk
    from tkinter import ttk

    chosen: dict[str, int | None] = {}
    root = tk.Tk()
    root.title(title + (" — preview" if demo else ""))
    root.minsize(420, 0)
    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")
    row = 0
    if demo:
        ttk.Label(frm, text="Preview with example devices (not live hardware).",
                  foreground="#b06").grid(column=0, columnspan=2, row=row, sticky="w",
                                          pady=(0, 10))
        row += 1

    cam_var = tk.StringVar()
    cam_labels = [c.label for c in cameras] or ["(no camera found)"]
    cam_default_label = next((c.label for c in cameras if c.index == camera_default),
                             cam_labels[0])
    ttk.Label(frm, text="Camera").grid(column=0, row=row, sticky="w", padx=(0, 10), pady=4)
    cam_box = ttk.Combobox(frm, textvariable=cam_var, values=cam_labels, state="readonly",
                           width=40)
    cam_box.set(cam_default_label)
    cam_box.grid(column=1, row=row, sticky="ew", pady=4)
    row += 1

    aud_var = tk.StringVar()
    if audio:
        aud_labels = [c.label for c in audio]
        aud_default_label = next((c.label for c in audio if c.index == audio_default),
                                 aud_labels[0])
        ttk.Label(frm, text="Audio output").grid(column=0, row=row, sticky="w",
                                                  padx=(0, 10), pady=4)
        aud_box = ttk.Combobox(frm, textvariable=aud_var, values=aud_labels,
                               state="readonly", width=40)
        aud_box.set(aud_default_label)
        aud_box.grid(column=1, row=row, sticky="ew", pady=4)
        row += 1

    frm.columnconfigure(1, weight=1)

    def on_start() -> None:
        chosen["camera"] = next((c.index for c in cameras if c.label == cam_var.get()), None)
        chosen["audio_device"] = next(
            (c.index for c in audio if c.label == aud_var.get()), None) if audio else None
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(column=0, columnspan=2, row=row, sticky="e", pady=(12, 0))
    ttk.Button(btns, text="Cancel", command=root.destroy).grid(column=0, row=0, padx=(0, 8))
    ttk.Button(btns, text="Start", command=on_start).grid(column=1, row=0)
    root.bind("<Return>", lambda _e: on_start())

    root.update_idletasks()
    root.mainloop()
    return chosen or None
