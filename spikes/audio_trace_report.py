#!/usr/bin/env python3
"""Summarize bounded audio timing recordings without assigning a cause to glitches.

Run against a finished or live JSONL trace. Missing observations and incomplete
windows remain visible so a quiet-looking trace cannot imply clean playback.
"""

import argparse
import json
import sys
from pathlib import Path


class TraceError(ValueError):
    """The recording cannot be interpreted reliably."""


EVIDENCE = {
    "capture_gap", "capture_discontinuity", "capture_error", "render_delay",
    "reconfigure_pause", "output_gap", "output_demand_burst",
}


def _fields(row, numbers=(), booleans=()):
    for key in numbers:
        value = row.get(key)
        if type(value) is not int or value < 0:
            raise TraceError(f"{key} must be a nonnegative integer")
    for key in booleans:
        if type(row.get(key)) is not bool:
            raise TraceError(f"{key} must be a boolean")


def _evidence(row, key):
    value = row.get(key)
    if not isinstance(value, dict) or set(value) != EVIDENCE:
        raise TraceError(f"invalid {key} flags")
    _fields(value, booleans=EVIDENCE)
    return sorted(k for k, enabled in value.items() if enabled)


def summarize(source):
    """Read a text stream; only an unfinished last JSON line may be ignored."""
    lines = source.readlines()
    rows, warnings = [], []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            # A writer may be halfway through the final record when a live file is read.
            # Completed but corrupt lines must never quietly disappear from the report.
            if (number == len(lines) and not line.endswith("\n")
                    and line.lstrip().startswith("{")
                    and (error.pos >= len(line.rstrip())
                         or error.msg.startswith("Unterminated string")
                         or (error.msg == "Expecting value" and line[error.pos:].strip()
                             in {"t", "tr", "tru", "f", "fa", "fal", "fals", "n", "nu", "nul"}))):
                warnings.append(f"Incomplete final JSON line {number} ignored; recording may be live.")
                break
            raise TraceError(f"invalid JSON on line {number}: {error.msg}") from error
        if not isinstance(row, dict):
            raise TraceError(f"line {number} must be a JSON object")
        rows.append(row)
    if not rows or rows[0].get("type") != "header":
        raise TraceError("recording must start with a header")
    header = rows[0]
    if type(header.get("version")) is not int or header["version"] != 1:
        raise TraceError("unsupported trace version")
    if not isinstance(header.get("metadata"), dict):
        raise TraceError("header metadata must be an object")
    _fields(header, ("source_rate", "pre_window_us", "post_window_us", "queue_capacity",
                     "history_capacity", "incident_capacity", "max_incidents", "max_file_records"))
    if header["source_rate"] == 0:
        raise TraceError("source_rate must be positive")
    incidents, footer = {}, None
    for row in rows[1:]:
        if footer is not None:
            raise TraceError("records found after footer")
        kind = row.get("type")
        if kind == "incident":
            _fields(row, ("incident", "trigger_us", "triggers", "cushion_budget_us"),
                    ("shutdown_flush", "window_truncated", "prehistory_truncated"))
            ident = row["incident"]
            if ident in incidents or ident == 0:
                raise TraceError("duplicate or zero incident ID")
            incidents[ident] = {
                "incident": ident, "trigger_seconds": row["trigger_us"] / 1e6,
                "trigger_us": row["trigger_us"], "triggers": row["triggers"],
                "cushion_budget_ms": row["cushion_budget_us"] / 1000,
                "pretrigger_evidence": _evidence(row, "evidence"),
                "overlapping_duration_evidence": _evidence(row, "overlapping_duration_evidence"),
                "events": [],
            }
            for key, message in (
                ("shutdown_flush", "shutdown flushed the incident; post-trigger window may be incomplete"),
                ("window_truncated", "incident event capacity exceeded; window truncated"),
                ("prehistory_truncated", "pre-trigger history may be truncated"),
            ):
                if row[key]:
                    warnings.append(f"Incident {ident}: {message}.")
        elif kind == "event":
            _fields(row, ("incident", "at_us"))
            ident = row["incident"]
            if ident not in incidents:
                raise TraceError("event references an unknown incident")
            if type(row.get("relative_us")) is not int or row["relative_us"] != row["at_us"] - incidents[ident]["trigger_us"]:
                raise TraceError("event relative timestamp disagrees with trigger")
            observation = row.get("observation")
            if not isinstance(observation, dict):
                raise TraceError("event observation must be an object")
            stage = observation.get("stage")
            schemas = {
                "capture": (("gap_us", "read_us", "frames"), ("silent", "discontinuity")),
                "render": (("elapsed_us", "frames", "block", "cushion"), ()),
                "output": (("gap_us", "frames", "fill", "block", "cushion", "sample_rate"), ()),
                "fifo": (("fill", "block", "cushion"), ()),
                "reconfigure": (("elapsed_us", "block", "cushion"), ()),
                "capture_error": ((), ()),
            }
            if stage not in schemas:
                raise TraceError(f"unknown observation stage: {stage}")
            _fields(observation, *schemas[stage])
            if stage == "fifo" and observation.get("pull") not in {"block", "underrun", "repriming"}:
                raise TraceError("invalid FIFO pull")
            if stage == "output" and observation["sample_rate"] == 0:
                raise TraceError("output sample_rate must be positive")
            incidents[ident]["events"].append(row)
        elif kind == "footer":
            _fields(row, ("incidents", "suppressed_triggers", "queue_drops", "history_evictions",
                          "late_events_after_window_closed", "file_records"), ("truncated",))
            footer = row
        else:
            raise TraceError(f"unknown or misplaced record type: {kind}")
    if footer is None:
        warnings.append("Missing footer: recording incomplete or live; loss counters are unknown.")
    else:
        for key in ("queue_drops", "suppressed_triggers", "late_events_after_window_closed"):
            if footer[key]:
                warnings.append(f"{key}={footer[key]}: observations or incident context are missing.")
        if footer["truncated"]:
            warnings.append("Recording truncated by event or file limits.")
        if footer["incidents"] != len(incidents):
            warnings.append("Footer incident count differs from retained incidents; report is incomplete.")
        if footer["file_records"] != len(rows) - 2:
            raise TraceError("footer file_records does not match recording")
    for incident in incidents.values():
        _summarize_incident(incident, warnings)
    return {"interpretation": "Timing evidence only; neither evidence group establishes causality. "
            "FIFO recovery means a block resumed, not proof of audible recovery.",
            "metadata": header["metadata"], "source_rate": header["source_rate"],
            "incidents": list(incidents.values()), "footer": footer, "warnings": warnings}


def _summarize_incident(incident, warnings):
    events = sorted(incident.pop("events"), key=lambda row: row["at_us"])
    maxima = {"capture_gap_ms": None, "render_elapsed_ms": None,
              "output_gap_ms": None, "output_request_ms": None}
    timeline, starved, starvation_start = [], False, None
    underruns = 0
    for row in events:
        obs, relative = row["observation"], row["relative_us"]
        stage = obs["stage"]
        values = {}
        if stage == "capture":
            values["capture_gap_ms"] = obs["gap_us"] / 1000
        elif stage == "render":
            values["render_elapsed_ms"] = obs["elapsed_us"] / 1000
        elif stage == "output":
            values = {"output_gap_ms": obs["gap_us"] / 1000,
                      "output_request_ms": obs["frames"] * 1000 / obs["sample_rate"]}
        for key, value in values.items():
            maxima[key] = value if maxima[key] is None else max(maxima[key], value)
        if stage == "fifo":
            pull = obs["pull"]
            if pull == "underrun":
                if relative >= 0:
                    underruns += 1
                if starvation_start is None:
                    starvation_start = relative
            if pull != "block" or starved:
                timeline.append({"relative_ms": relative / 1000,
                                 "state": "block_resumed" if pull == "block" else pull,
                                 "fill_blocks": obs["fill"], "block": obs["block"],
                                 "cushion": obs["cushion"]})
                if pull == "block" and starvation_start is not None:
                    timeline[-1]["recovery_after_underrun_ms"] = (relative - starvation_start) / 1000
                    starvation_start = None
            starved = pull != "block"
    incident["maxima_over_retained_window"] = maxima
    incident["fifo_timeline"] = timeline
    incident["fifo_still_starved_at_last_observation"] = starved if timeline else None
    if underruns != incident["triggers"]:
        warnings.append(f"Incident {incident['incident']}: retained underruns ({underruns}) differ "
                        f"from trigger count ({incident['triggers']}); timeline is incomplete.")
    del incident["trigger_us"]


def format_text(report):
    lines = [report["interpretation"], "Metadata: " + json.dumps(report["metadata"], sort_keys=True),
             f"Source rate: {report['source_rate']} Hz; incidents retained: {len(report['incidents'])}"]
    for incident in report["incidents"]:
        lines.append(f"Incident {incident['incident']} at {incident['trigger_seconds']:.6f} s: "
                     f"{incident['triggers']} trigger(s), cushion budget {incident['cushion_budget_ms']:.3f} ms")
        for key, label in (("pretrigger_evidence", "Evidence observed at/before trigger"),
                           ("overlapping_duration_evidence", "Later durations spanning trigger")):
            lines.append(f"  {label}: {', '.join(incident[key]) or 'none flagged'}")
        maxima = incident["maxima_over_retained_window"]
        lines.append("  Maxima over retained window: " + ", ".join(
            f"{key}={value:.3f}" if value is not None else f"{key}=unobserved"
            for key, value in maxima.items()))
        for item in incident["fifo_timeline"]:
            lines.append(f"  FIFO {item['relative_ms']:+.3f} ms: {item['state']}, "
                         f"fill={item['fill_blocks']} block={item['block']} cushion={item['cushion']}")
    lines.extend("WARNING: " + warning for warning in report["warnings"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true", help="emit the structured report")
    args = parser.parse_args(argv)
    try:
        with args.path.open(encoding="utf-8") as source:
            report = summarize(source)
    except (OSError, ValueError) as error:
        parser.exit(2, f"audio trace report: {error}\n")
    print(json.dumps(report, indent=2) if args.json else format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
