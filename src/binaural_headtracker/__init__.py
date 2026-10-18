"""binaural_headtracker — Python prototype of a head-tracked, world-locked binaural renderer.

The real-time audio path was prototyped in Python first (fast iteration); its hot loop
— the batched partitioned convolution — now also has a native implementation (the Rust
``binaural_kernel`` / :mod:`binaural_headtracker.rust_render`, B8.2) that renders in real
time on the CPU. This package holds the render engine, the world model, the HRIR handling
and the head-pose tracker (see ``docs/requirements.md`` § 5, § 7).
"""

__version__ = "0.1.0"
