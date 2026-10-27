# Output gain and level meters

Use **Output gain** below the cockpit's main panels to adjust loudness without changing
the BRIRs or headphone EQ. The range is −24 to +24 dB; **0 dB** restores the previous engine
level. A positive value increases level. The setting survives restarts and applies with
headphone EQ enabled or bypassed.

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
+24 are accepted; invalid values leave the setting unchanged. The queued HTTP response is
not confirmation: read `output_gain_db` from `/api/get_config` or `output_level.gain_db`
from SSE. The additive setting defaults to 0 dB for existing settings files.

SSE `output_level` also carries nullable `peak_dbfs` / `rms_dbfs`, interval
`clipped_samples`, and lifetime `clipped_total` / `non_finite_total`.

Portable DSP and wire-contract tests run with `cargo test --manifest-path rust-engine/Cargo.toml`.
Optional browser tests: `python3 -m pytest tests/test_output_gain_ui.py` (Playwright/Chromium).
The separate Windows host check uses isolated settings and a finite run; see
`spikes/output_gain_host_check.py --help`. Choose unused endpoints when running it.
