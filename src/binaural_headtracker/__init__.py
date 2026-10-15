"""binaural_headtracker — Python prototype of a head-tracked, world-locked binaural renderer.

The real-time audio path is prototyped here first (fast iteration) and will be
ported to a native hot path later for the latency budget (see
``docs/requirements.md`` § 5, § 7). This package will grow the render engine, the
world model, the HRIR handling and the head-pose tracker block by block.
"""

__version__ = "0.1.0"
