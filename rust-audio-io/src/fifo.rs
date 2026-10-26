//! The cushioned output drift-FIFO with the journal-81 maintained-setpoint re-prime.
//!
//! Pure, cross-platform, deterministic — the real-time buffering policy that sits between the render
//! worker (which `push`es rendered blocks) and the output device callback (which `pull`s blocks to
//! play). Ported from the Python `voicemeeter_passthrough` output FIFO:
//!
//! * **Cushion prefill.** The FIFO starts prefilled with `cushion` silent blocks — the standing
//!   depth that absorbs the render's jitter (and is the dominant head-move-to-ear latency).
//! * **Backlog cap.** `max_backlog = 2·cushion`; when the worker out-produces the drain (clock
//!   drift), the oldest blocks are dropped so latency can't grow unbounded (counted).
//! * **Re-prime setpoint (journal 81).** The cushion is a *prefill*, not a setpoint: once a jitter
//!   lets the output lead the worker, that margin is gone for good and the FIFO rides the empty
//!   floor until the next underrun = the rare crack. The fix: on an underrun, hold the output
//!   **silent** (the clock keeps ticking, so the worker keeps filling) until the FIFO is rebuilt to
//!   the full `cushion` depth, then resume draining with the whole margin restored. The rebuild
//!   silence is bounded by the cushion's own latency and fires only on an underrun (already a
//!   glitch), so it never adds silence to a clean stream.
//!
//! Block-granular (one entry = one render block), matching the engine: the worker renders one block
//! per captured block and the output callback pops whole blocks. `T` is the block payload (PCM in
//! the engine; a marker in tests) — the policy is payload-agnostic.

use std::collections::VecDeque;

/// What a `pull` handed the device this callback.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pull {
    /// A real rendered block was drained.
    Block,
    /// Silence emitted because the FIFO underran (the worker fell behind) — starts a re-prime.
    Underrun,
    /// Silence emitted while re-priming (rebuilding the cushion after an underrun).
    Repriming,
}

/// Running counters for the measured gate / diagnostics.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct FifoStats {
    pub pulls: u64,
    pub underruns: u64,
    pub reprimes: u64,
    pub reprime_silent_blocks: u64,
    pub overflow_drops: u64,
}

/// The cushioned drift-FIFO. `push` from the render worker, `pull` from the output callback.
pub struct CushionFifo<T: Clone> {
    cushion: usize,
    max_backlog: usize,
    /// How deep to rebuild the queue after an underrun before resuming the drain. Defaults to the
    /// full `cushion` (the journal-81 policy: restore the whole margin in one bounded silent fill).
    /// A caller MAY lower it via [`set_reprime_target`](Self::set_reprime_target) to resume sooner,
    /// but the engine uses the full-cushion default: a shallow re-prime leaves the FIFO near-empty,
    /// and the output resampler's PI loop is far too slow to refill it before the next jitter spike
    /// underruns again (a sustained dropout storm — reverting #192). Clamped to `[1, cushion]`.
    reprime_target: usize,
    silence: T,
    q: VecDeque<T>,
    repriming: bool,
    stats: FifoStats,
}

impl<T: Clone> CushionFifo<T> {
    /// `cushion` = standing depth in blocks (floored at 2 by the engine); `max_backlog` caps drift
    /// growth (the engine uses `2·cushion`). `silence` is the zero block emitted on underrun /
    /// re-prime / prefill.
    pub fn new(cushion: usize, max_backlog: usize, silence: T) -> Self {
        let cushion = cushion.max(1);
        let max_backlog = max_backlog.max(cushion);
        let mut q = VecDeque::with_capacity(max_backlog + 1);
        for _ in 0..cushion {
            q.push_back(silence.clone()); // prefill the cushion with silence
        }
        Self {
            cushion,
            max_backlog,
            reprime_target: cushion, // journal-81 default: rebuild the whole cushion
            silence,
            q,
            repriming: false,
            stats: FifoStats::default(),
        }
    }

    /// Set how deep the queue is rebuilt after an underrun before the drain resumes. `1` resumes as
    /// soon as a single block is available — the shortest possible silent hole — but leaves the FIFO
    /// near-empty afterwards; only choose it if something refills the cushion quickly (the engine's
    /// output resampler PI loop does NOT — it is slow by design, so the engine keeps the full-cushion
    /// default; see rust-engine `new_fifo`). Clamped to `[1, cushion]`.
    pub fn set_reprime_target(&mut self, blocks: usize) {
        self.reprime_target = blocks.clamp(1, self.cushion);
    }

    /// The current re-prime rebuild depth in blocks (see [`set_reprime_target`](Self::set_reprime_target)).
    pub fn reprime_target(&self) -> usize {
        self.reprime_target
    }

    pub fn stats(&self) -> FifoStats {
        self.stats
    }
    pub fn len(&self) -> usize {
        self.q.len()
    }
    pub fn is_empty(&self) -> bool {
        self.q.is_empty()
    }
    pub fn is_repriming(&self) -> bool {
        self.repriming
    }
    pub fn cushion(&self) -> usize {
        self.cushion
    }

    /// The render worker produced one block. Extend, then drop oldest while over the backlog cap
    /// (bounding drift-driven latency growth, as `voicemeeter_passthrough` does). Returns how many
    /// blocks were dropped this push.
    pub fn push(&mut self, block: T) -> usize {
        self.q.push_back(block);
        let mut dropped = 0;
        while self.q.len() > self.max_backlog {
            self.q.pop_front();
            dropped += 1;
        }
        self.stats.overflow_drops += dropped as u64;
        dropped
    }

    /// The output device wants one block. Implements the re-prime state machine: while re-priming,
    /// stay silent until the cushion is whole again; otherwise drain a block, or underrun to silence
    /// (which starts a new re-prime). Returns what was handed out (and, for a real block, it).
    pub fn pull(&mut self) -> (Pull, Option<T>) {
        self.stats.pulls += 1;
        if self.repriming {
            if self.q.len() >= self.reprime_target {
                self.repriming = false; // rebuilt to the target — resume draining this same pull
            } else {
                self.stats.reprime_silent_blocks += 1;
                return (Pull::Repriming, Some(self.silence.clone()));
            }
        }
        match self.q.pop_front() {
            Some(b) => (Pull::Block, Some(b)),
            None => {
                // Underrun: silence now, and rebuild the cushion before draining again (journal 81).
                self.stats.underruns += 1;
                self.stats.reprimes += 1;
                self.stats.reprime_silent_blocks += 1;
                self.repriming = true;
                (Pull::Underrun, Some(self.silence.clone()))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefills_the_cushion_with_silence() {
        let f = CushionFifo::new(3, 6, 0u32);
        assert_eq!(f.len(), 3);
        assert!(!f.is_repriming());
    }

    #[test]
    fn steady_push_pull_holds_near_cushion() {
        // cushion 2, backlog 4. Push one, pull one each tick: fill stays at the prefilled depth.
        let mut f = CushionFifo::new(2, 4, 0u32);
        for i in 1..=100u32 {
            f.push(i);
            let (p, _) = f.pull();
            assert_eq!(p, Pull::Block);
        }
        assert_eq!(f.stats().underruns, 0);
        assert_eq!(f.stats().overflow_drops, 0);
        assert_eq!(f.len(), 2); // rode the cushion depth
    }

    #[test]
    fn backlog_cap_drops_oldest_on_drift() {
        // Worker out-produces the drain: push 10 without pulling, backlog caps at 4.
        let mut f = CushionFifo::new(2, 4, 0u32);
        for i in 1..=10u32 {
            f.push(i);
        }
        assert_eq!(f.len(), 4);
        assert_eq!(f.stats().overflow_drops, 2 + 10 - 4); // 2 prefill + 10 pushed - 4 kept = 8
        // the 4 kept are the newest
        let (_, b) = f.pull();
        assert_eq!(b, Some(7));
    }

    #[test]
    fn underrun_triggers_reprime_and_stays_silent_until_cushion_rebuilt() {
        let mut f = CushionFifo::new(3, 6, 0u32);
        // drain the 3 prefilled blocks
        for _ in 0..3 {
            assert_eq!(f.pull().0, Pull::Block);
        }
        // next pull underruns -> silence + reprime
        assert_eq!(f.pull().0, Pull::Underrun);
        assert!(f.is_repriming());
        // worker pushes 2 (< cushion): still repriming (silent), not drained
        f.push(10);
        f.push(11);
        assert_eq!(f.pull().0, Pull::Repriming);
        assert_eq!(f.len(), 2); // not drained while repriming
        // worker pushes the 3rd -> cushion whole -> next pull resumes draining
        f.push(12);
        let (p, b) = f.pull();
        assert_eq!(p, Pull::Block);
        assert_eq!(b, Some(10)); // resumes from the oldest rebuilt block
        assert!(!f.is_repriming());
        assert_eq!(f.stats().underruns, 1);
        assert_eq!(f.stats().reprimes, 1);
        assert_eq!(f.stats().reprime_silent_blocks, 2); // the underrun pull + one repriming pull
    }

    #[test]
    fn softened_reprime_resumes_at_one_block() {
        // #192: with reprime_target = 1 the drain resumes the instant a single block is available,
        // so a residual underrun is a one-block silent hole, not a full-cushion crack.
        let mut f = CushionFifo::new(4, 8, 0u32);
        f.set_reprime_target(1);
        assert_eq!(f.reprime_target(), 1);
        for _ in 0..4 {
            assert_eq!(f.pull().0, Pull::Block); // drain the prefill
        }
        assert_eq!(f.pull().0, Pull::Underrun); // empty -> underrun + reprime
        assert!(f.is_repriming());
        f.push(10); // just one block rebuilt
        let (p, b) = f.pull();
        assert_eq!(p, Pull::Block); // resumes immediately at one block
        assert_eq!(b, Some(10));
        assert!(!f.is_repriming());
        // Only the underrun pull itself was silent — no full-cushion rebuild hole.
        assert_eq!(f.stats().reprime_silent_blocks, 1);
    }

    #[test]
    fn full_cushion_reprime_restores_the_margin_a_one_block_reprime_leaves_missing() {
        // Why the engine re-primes to the full cushion, not one block: after an underrun, a
        // one-block re-prime resumes with the FIFO near-empty, so the very next pull that races
        // ahead of the worker underruns AGAIN — the clustered-dropout storm the user hit. The
        // full-cushion re-prime resumes only once the whole margin is back, so the same jitter is
        // absorbed. (The engine relies on this because the drift PI loop refills far too slowly to
        // climb out of a shallow re-prime in time — see rust-engine `new_fifo`.)
        let jitter = |target: usize| -> u64 {
            let mut f = CushionFifo::new(4, 8, 0u32);
            f.set_reprime_target(target);
            for _ in 0..4 {
                f.pull(); // drain the prefill
            }
            f.pull(); // underrun -> reprime begins
                      // The worker rebuilds exactly `target` blocks, then the drain resumes.
            for i in 0..target {
                f.push(i as u32);
                f.pull();
            }
            // Now one pull races ahead of the worker (a single jitter spike, no push this tick).
            let p = f.pull().0;
            if p == Pull::Underrun {
                1
            } else {
                0
            }
        };
        assert_eq!(jitter(1), 1, "one-block re-prime underruns again on the first jitter spike");
        assert_eq!(jitter(4), 0, "full-cushion re-prime absorbs the same jitter spike");
    }

    #[test]
    fn reprime_target_is_clamped_to_cushion_range() {
        let mut f = CushionFifo::new(3, 6, 0u32);
        f.set_reprime_target(0);
        assert_eq!(f.reprime_target(), 1); // floored at 1
        f.set_reprime_target(99);
        assert_eq!(f.reprime_target(), 3); // capped at the cushion
    }
}
