"""Reports preserve uncertainty and reject misleading or corrupt recordings."""

import importlib.util
import io
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "audio_trace_report", Path(__file__).resolve().parents[1] / "spikes/audio_trace_report.py")
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def recording():
    header = {"type": "header", "version": 1, "metadata": {"block": 256, "cushion": 3},
              "source_rate": 48000, "pre_window_us": 250000, "post_window_us": 1000000,
              "queue_capacity": 4096, "history_capacity": 2048, "incident_capacity": 4096,
              "max_incidents": 32, "max_file_records": 32768}
    flags = dict.fromkeys(reporter.EVIDENCE, False)
    incident = {"type": "incident", "incident": 1, "trigger_us": 1000000, "triggers": 1,
                "cushion_budget_us": 16000, "shutdown_flush": False,
                "window_truncated": False, "prehistory_truncated": False,
                "evidence": dict(flags, render_delay=True),
                "overlapping_duration_evidence": dict(flags, capture_gap=True)}
    def event(relative, stage, **fields):
        return {"type": "event", "incident": 1, "at_us": 1000000 + relative,
                "relative_us": relative, "observation": dict(stage=stage, **fields)}
    fifo = {"fill": 0, "block": 256, "cushion": 3}
    rows = [header, incident,
            event(-1000, "render", elapsed_us=7000, frames=256, block=256, cushion=3),
            event(0, "fifo", pull="underrun", **fifo),
            event(1000, "fifo", pull="repriming", **fifo),
            event(2000, "capture", gap_us=20000, read_us=100, frames=480,
                  silent=False, discontinuity=False),
            event(5000, "output", gap_us=10000, frames=441, sample_rate=44100, **fifo),
            event(20000, "fifo", pull="block", **dict(fifo, fill=3))]
    rows.append({"type": "footer", "incidents": 1, "suppressed_triggers": 0,
                 "queue_drops": 0, "truncated": False, "history_evictions": 0,
                 "late_events_after_window_closed": 0, "file_records": len(rows) - 1})
    return rows


def read(rows, tail=""):
    return reporter.summarize(io.StringIO("".join(json.dumps(row) + "\n" for row in rows) + tail))


class AudioTraceReportTests(unittest.TestCase):
    def test_pretrigger_and_later_overlap_remain_separate(self):
        report = read(recording())
        incident = report["incidents"][0]
        self.assertEqual(incident["trigger_seconds"], 1.0)
        self.assertEqual(incident["pretrigger_evidence"], ["render_delay"])
        self.assertEqual(incident["overlapping_duration_evidence"], ["capture_gap"])
        self.assertEqual(incident["maxima_over_retained_window"], {
            "capture_gap_ms": 20.0, "render_elapsed_ms": 7.0,
            "output_gap_ms": 10.0, "output_request_ms": 10.0})
        self.assertEqual([item["state"] for item in incident["fifo_timeline"]],
                         ["underrun", "repriming", "block_resumed"])
        self.assertEqual(incident["fifo_timeline"][-1]["recovery_after_underrun_ms"], 20.0)
        self.assertFalse(incident["fifo_still_starved_at_last_observation"])
        text = reporter.format_text(report)
        self.assertIn("neither evidence group establishes causality", text)
        self.assertIn("not proof of audible recovery", text)
        self.assertEqual(report["warnings"], [])

    def test_live_partial_tail_and_missing_footer_are_visible(self):
        report = read(recording()[:-1], '{"type":"event","observation":')
        self.assertTrue(any("Incomplete final JSON" in w for w in report["warnings"]))
        self.assertTrue(any("Missing footer" in w for w in report["warnings"]))
        self.assertIsNone(report["footer"])
        self.assertEqual(len(report["incidents"]), 1)
        for tail in ('{"type":"footer","truncated":tru', '{"type":"foot'):
            self.assertTrue(any("Incomplete final JSON" in w
                                for w in read(recording()[:-1], tail)["warnings"]))

    def test_previous_underrun_in_prehistory_is_not_a_current_trigger(self):
        rows = recording()
        prior = dict(rows[3], at_us=900000, relative_us=-100000)
        recovered = dict(rows[-2], at_us=920000, relative_us=-80000)
        rows[2:2] = [prior, recovered]
        rows[-1]["file_records"] += 2
        report = read(rows)
        self.assertEqual(report["warnings"], [])
        timeline = report["incidents"][0]["fifo_timeline"]
        self.assertEqual([item["relative_ms"] for item in timeline],
                         [-100.0, -80.0, 0.0, 1.0, 20.0])
        self.assertEqual(timeline[1]["recovery_after_underrun_ms"], 20.0)
        self.assertEqual(timeline[-1]["recovery_after_underrun_ms"], 20.0)

    def test_header_only_live_recording_reports_unknown_loss_counters(self):
        report = read(recording()[:1])
        self.assertEqual(report["incidents"], [])
        self.assertIsNone(report["footer"])
        self.assertTrue(any("Missing footer" in warning for warning in report["warnings"]))
        self.assertIn("incidents retained: 0", reporter.format_text(report))

    def test_all_loss_and_shutdown_limits_warn(self):
        rows = recording()
        rows[1].update(shutdown_flush=True, window_truncated=True, prehistory_truncated=True)
        rows[-1].update(queue_drops=4, suppressed_triggers=2,
                        late_events_after_window_closed=3, truncated=True)
        warnings = " ".join(read(rows)["warnings"])
        for phrase in ("queue_drops=4", "suppressed_triggers=2", "late_events_after_window_closed=3",
                       "window truncated", "pre-trigger history", "post-trigger window", "Recording truncated"):
            self.assertIn(phrase, warnings)

    def test_corrupt_middle_and_completed_bad_tail_are_rejected(self):
        for text in ('{"type":"header"}\n{bad}\n{}\n',
                     ''.join(json.dumps(r) + '\n' for r in recording()[:-1]) + '{bad}\n',
                     ''.join(json.dumps(r) + '\n' for r in recording()[:-1]) + '{bad}'):
            with self.subTest(text=text[-20:]), self.assertRaises(reporter.TraceError):
                reporter.summarize(io.StringIO(text))

    def test_header_and_schema_are_validated(self):
        variants = []
        for key, value in (("version", 2), ("source_rate", 0), ("metadata", [])):
            rows = recording()
            rows[0][key] = value
            variants.append(rows)
        rows = recording()
        rows[3]["relative_us"] = 17
        variants.append(rows)
        rows = recording()
        rows[1]["evidence"]["capture_gap"] = "false"
        variants.append(rows)
        rows = recording()
        rows[6]["observation"]["sample_rate"] = 0
        variants.append(rows)
        rows = recording()
        rows[3]["observation"]["fill"] = True
        variants.append(rows)
        variants.append(recording()[1:])
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(reporter.TraceError):
                read(rows)

    def test_missing_recovery_and_event_loss_are_not_reported_as_clean(self):
        rows = recording()
        del rows[-2]
        rows[-1]["file_records"] -= 1
        incident = read(rows)["incidents"][0]
        self.assertTrue(incident["fifo_still_starved_at_last_observation"])
        self.assertNotIn("block_resumed", [item["state"] for item in incident["fifo_timeline"]])
        rows = recording()
        del rows[3]
        rows[-1]["file_records"] -= 1
        self.assertTrue(any("timeline is incomplete" in w for w in read(rows)["warnings"]))


if __name__ == "__main__":
    unittest.main()
