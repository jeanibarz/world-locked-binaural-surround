# Audio timing trace

The optional timing recorder saves the context around output-FIFO underruns. It helps
compare capture delivery, render service, output demand, and FIFO recovery on one timeline.
It records timing and frame counts, not audio samples or camera images.

## Collect a recording

Add `--audio-trace <file.jsonl>` to the normal Windows engine command. Create the parent
directory first and choose a new filename: an existing file is overwritten at startup.
Tracing is off when the option is absent and is not persisted as an audio setting.

From the installation directory, this example uses block 256 and a four-block cushion:

```bat
mkdir traces
run-engine.bat --block 256 --latency-ms 21.333333 --audio-trace traces\session-01.jsonl
```

Use your current block/cushion values when reproducing a real event. The recorder itself
does not change devices, routing, rendering method, drift gains, or buffer settings. An
invalid output path fails startup before audio devices are opened.

Leave playback running through an event. Each admitted incident retains up to 250 ms
before its first underrun and 1,000 ms afterward. Multiple underruns inside that window
share one incident. Completed incidents are flushed by a worker thread. Close the engine
normally to flush a pending window and the final loss counters; forcibly killing it may
leave an incomplete recording.

## Read the evidence

The portable report tool accepts completed recordings and live files:

```sh
python3 spikes/audio_trace_report.py /path/to/session-01.jsonl
python3 spikes/audio_trace_report.py /path/to/session-01.jsonl --json
```

It separates observations at/before the trigger from later completed intervals that span
the trigger. For example, a capture call can finish after an underrun and reveal a gap
that was already in progress. A later unrelated delay is excluded from that evidence group.

The flags are measurements crossing simple thresholds, not a causal verdict:

| Flag | Observation |
| --- | --- |
| `capture_gap` | Time since the previous successful capture delivery exceeds the trigger's nominal cushion budget. This includes intentional source pauses and delays in servicing capture. |
| `capture_discontinuity` | WASAPI reported a discontinuity before/at the trigger. |
| `capture_error` | A capture read returned an error before/at the trigger. |
| `render_delay` | Render-batch wall time exceeded the duration of its produced frames, with at least one configured block of budget for partial input. It includes scheduling delay and capture-thread batch overhead. |
| `reconfigure_pause` | A graph rebuild took longer than the trigger's nominal cushion budget. |
| `output_gap` | The interval between serviced output wakes exceeded the current nominal cushion budget. |
| `output_demand_burst` | A serviced output request represented more source-rate audio than the configured cushion target. Actual FIFO fill can differ from that target. |

Inspect raw events and concurrent flags together. An unflagged window does not prove that
playback was clean. A FIFO `block_resumed` observation means a block was drained again;
it does not establish audible recovery, because that block may contain silence.

The report warns about missing footers, shortened windows, queue drops, late arrivals,
and suppressed incidents. Window maxima include the whole retained interval, so a maximum
after the trigger must not be treated as pretrigger evidence. A live recording has no
final footer yet; its final loss counters are unknown.

## Limits and overhead

Producers submit fixed-size events with a bounded `try_send`; they do no JSON conversion or
file I/O during the audio loop. The standard channel uses internal synchronization; this is
not a lock-free or hard real-time guarantee. Startup file creation/header writing happens
before the audio graph is opened. Collection, analysis, and incident writes run on a worker.
The enabled output path also takes a short FIFO snapshot for output-wake context.

The queue holds 4,096 events, prehistory 2,048, and each incident at most 4,096. A session
admits at most 32 incidents and writes at most 32,768 incident/event rows plus its header
and footer. Reaching a cap suppresses or truncates observations; it does not imply that
subsequent playback was clean. The final footer records losses and suppression. Background
write failures disable the recorder and print `AUDIO_TRACE_ERROR` while audio continues.

A target-OS producer probe opens no audio devices:

```sh
cargo xwin build --release --target x86_64-pc-windows-msvc \
  --manifest-path rust-engine/Cargo.toml --bin audio_trace_probe
# Run the resulting Windows executable with a Windows-local output filename:
audio_trace_probe.exe C:\path\to\producer-overhead.jsonl
```

Its synthetic incident is marked in metadata. It measures submission with a concurrent
collector and JSON writer, not full application overhead or actual audio deadlines. See
journal 84 for the observed distributions and the live-engine validation limits.
