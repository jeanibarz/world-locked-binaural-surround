/* Cockpit web UI logic.
   - Renders the mockup's sample data on load so the page looks identical when opened standalone.
   - applyState(state) / setCameraFrame(dataURL) are the entry points the Python (pywebview) bridge
     calls each engine tick to drive the live UI.
   - Controls call window.pywebview.api.* when hosted; no-op gracefully standalone. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const call = (name, ...args) => fetch("/api/" + name, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
  }).catch(() => {});

  // Show the engine build stamp (git SHA + date) in the SYSTEM panel, so a stale deployed engine is
  // visible at a glance. `v` is the config's `version` object; absent on a pre-stamp engine.
  function showBuild(v) {
    const el = document.getElementById("build-stamp");
    if (!el) return;
    if (!v || !v.short) { el.textContent = "unknown (pre-stamp engine)"; return; }
    const day = typeof v.date === "string" ? v.date.slice(0, 10) : "";
    el.textContent = v.short + (day ? " · " + day : "") + (v.dirty ? " ·⚠dirty" : "");
  }

  function toast(message, kind) {
    const t = document.createElement("div");
    t.textContent = message;
    const bg = kind === "error" ? "#f2545b" : kind === "info" ? "#35d0d0" : "#54d98c";
    Object.assign(t.style, {
      position: "fixed", right: "16px", bottom: "16px", zIndex: 2000, background: bg, color: "#06222a",
      font: "600 13px Inter,sans-serif", padding: "10px 14px", borderRadius: "8px",
      boxShadow: "0 8px 24px rgba(0,0,0,.4)", maxWidth: "360px",
    });
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 5000);
  }

  // ---- first-run success cue + keyboard recenter (#234) ---------------------
  // A first-time user needs a way to tell success from silent failure. The cue tells them to turn
  // their head (the sound should stay put) and to press C / Recenter to re-zero. It shows once on
  // the first hosted launch; pressing C, clicking "Got it", or recentering dismisses it for good.
  // Persistence is origin-scoped localStorage — reliable at the default fixed port (8732); a
  // non-default port would re-show it, which is acceptable for a one-line hint.
  const FIRST_RUN_KEY = "wlbs.firstRunCueSeen";
  function dismissFirstRunCue() {
    const cue = $("first-run-cue");
    if (cue) cue.hidden = true;
    try { localStorage.setItem(FIRST_RUN_KEY, "1"); } catch (_) { /* private mode: just hide it */ }
  }
  function maybeShowFirstRunCue() {
    const cue = $("first-run-cue");
    if (!cue) return;
    let seen = false;
    try { seen = localStorage.getItem(FIRST_RUN_KEY) === "1"; } catch (_) { /* unreadable: treat as unseen */ }
    if (!seen) cue.hidden = false;
  }
  // A bare letter key is a shortcut only when the user isn't typing into a field.
  function isTypingTarget(t) {
    if (!t) return false;
    const tag = t.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable === true;
  }

  // Gain commands are acknowledged by engine state, rather than the queued HTTP response.
  let outputGain = null, gainEditing = false, gainPending = null, gainTimer = null;
  let gainStateVersion = 0, gainRequestVersion = 0;
  // Attenuation floor is fixed; the boost ceiling is engine-configurable (defaults to +24 dB) and
  // arrives live in telemetry / config as `max_gain_db`. The slider's own `max` tracks it.
  const GAIN_FLOOR = -24;
  let gainMax = 24;
  const validGain = (v) => typeof v === "number" && Number.isFinite(v) && v >= GAIN_FLOOR && v <= gainMax;
  const gainText = (v) => (v > 0 ? "+" : "") + v.toFixed(1) + " dB";
  // Retune the slider's ceiling from the engine. Ignores junk; redraws so the thumb/label reflect
  // the new range. The engine has already clipped the live gain to any lowered ceiling, so the
  // slider value stays in range.
  function setGainMax(m) {
    if (typeof m !== "number" || !Number.isFinite(m) || m < 0 || m === gainMax) return;
    gainMax = m;
    const slider = $("output-gain");
    if (slider) slider.max = String(m);
    drawGain();
  }
  function drawGain() {
    const slider = $("output-gain"), reset = $("output-gain-reset");
    if (!slider) return;
    slider.disabled = outputGain === null || gainPending !== null;
    if (reset) reset.disabled = slider.disabled;
    if (!gainEditing && gainPending === null && outputGain !== null) {
      slider.value = outputGain;
      setText("output-gain-value", gainText(outputGain));
    }
  }
  function receiveGain(v) {
    if (!validGain(v)) return;
    gainStateVersion++;
    outputGain = v;
    if (gainPending !== null && Math.abs(v - gainPending) < 0.001) {
      gainPending = null; clearTimeout(gainTimer);
      setText("output-gain-help", "Raise gradually; reduce gain if clipping increases.");
    }
    drawGain();
  }
  async function submitGain(v) {
    gainEditing = false;
    if (!validGain(v) || outputGain === null || gainPending !== null) { drawGain(); return; }
    const requestVersion = ++gainRequestVersion;
    gainPending = v; drawGain();
    setText("output-gain-value", gainText(v));
    setText("output-gain-help", "Applying gain…");
    const failed = () => {
      if (gainPending === null || requestVersion !== gainRequestVersion) return;
      gainPending = null; clearTimeout(gainTimer); drawGain();
      setText("output-gain-help", "Gain was not confirmed. Check the connection and try again.");
    };
    // The settings dispatcher is asynchronous; ordinary old SSE snapshots are not a rejection.
    gainTimer = setTimeout(async () => {
      const controller = new AbortController();
      const deadline = setTimeout(() => { controller.abort(); failed(); }, 1000);
      try {
        const r = await fetch("/api/get_config", { signal: controller.signal });
        if (r.ok) { const c = await r.json(); if (requestVersion === gainRequestVersion) { setGainMax(c.max_gain_db); receiveGain(c.output_gain_db); } }
      } catch (_) { /* Restore the last engine-confirmed value below. */ }
      clearTimeout(deadline);
      failed();
    }, 4000);
    try {
      const r = await fetch("/api/set_output_gain", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify([v]),
      });
      if (!r.ok) failed();
    } catch (_) { failed(); }
  }
  const levelText = (v) => v === null ? "−∞" : typeof v === "number" && Number.isFinite(v) ? (v > 0 ? "+" : "") + v.toFixed(1) : "—";
  function renderOutputLevel(level) {
    if (!level) return;
    setGainMax(level.max_gain_db); // widen/narrow the slider range before validating the gain
    receiveGain(level.gain_db);
    const peak = level.peak_dbfs, rms = level.rms_dbfs;
    const text = "Peak " + levelText(peak) + " · RMS " + levelText(rms) + " dBFS";
    setText("output-level-text", text);
    const pct = (v) => typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.min(100, (v + 60) / 60 * 100)) : 0;
    const fill = $("output-level-fill"), marker = $("output-level-peak");
    if (fill) { fill.style.width = pct(rms) + "%"; fill.className = "bar-fill " + (typeof peak === "number" && peak > 0 ? "bad" : typeof peak === "number" && peak > -3 ? "warn" : "good"); }
    if (marker) marker.style.left = pct(peak) + "%";
    const bar = $("output-level-bar"); if (bar) bar.setAttribute("aria-label", text);
    const clipping = $("output-clipping");
    if (clipping) {
      const total = level.clipped_total, bad = level.non_finite_total;
      const known = Number.isFinite(total) && Number.isFinite(bad);
      clipping.textContent = known ? "Clipped samples: " + total.toLocaleString() + " since start" + (bad > 0 ? " · invalid samples: " + bad.toLocaleString() : "") + (total > 0 ? " · reduce gain if rising" : "") : "Clipping: waiting…";
      clipping.className = "output-hint" + (bad > 0 ? " bad" : total > 0 ? " warn" : "");
    }
  }

  // ---- output-buffer cell meter ---------------------------------------------
  // One cell per FIFO block (capacity = 2x cushion). Margin cells (1..cushion) are green when full
  // and red when empty (the underfill alarm); headroom cells are cyan spare, amber in the top ~25%
  // of capacity nearest the overflow edge. A cyan line marks the target (= cushion). Rebuilds the
  // row only when the cell count changes (cushion/block change); otherwise just re-classes cells.
  function renderBufferCells(b) {
    const host = $("buffer-cells");
    if (!host) return;
    const cush = Math.max(1, b.cushion_blocks || 0);
    const cap = Math.max(2, b.capacity || 2 * cush);
    const fill = Math.max(0, Math.min(cap, b.fill_blocks || 0));
    const amberFrom = cap - Math.max(1, Math.round(cap * 0.25));
    let row = host.querySelector(".buf-cells");
    if (!row || row.childElementCount !== cap) {
      host.innerHTML = "";
      row = document.createElement("div");
      row.className = "buf-cells";
      for (let i = 0; i < cap; i++) row.appendChild(document.createElement("div"));
      host.appendChild(row);
      const tick = document.createElement("div");
      tick.className = "buf-tick";
      tick.style.left = (cush / cap * 100) + "%";   // target boundary (margin | headroom)
      host.appendChild(tick);
    }
    const cells = row.children;
    for (let i = 0; i < cap; i++) {
      const on = i < fill, margin = i < cush;
      let cls = "buf-cell";
      if (margin) cls += on ? " f-margin" : " f-alarm";
      else if (on) cls += (i >= amberFrom) ? " f-amber" : " f-cyan";
      if (i === cush) cls += " f-divide";
      cells[i].className = cls;
    }
  }

  // ---- histogram -------------------------------------------------------------
  function buildHistogram(heights, p50pct) {
    const host = $("histogram");
    if (!host) return;
    host.innerHTML = "";
    heights.forEach((h, i) => {
      const d = document.createElement("div");
      d.className = "hist-bar";
      d.style.height = Math.max(h, 1) + "%";
      // the bins around the median are highlighted cyan, the rest a dim teal (mockup rule)
      const hot = p50pct != null
        ? (i + 0.5) / heights.length >= (p50pct - 7.5) / 100 && (i + 0.5) / heights.length <= (p50pct + 7.5) / 100
        : (i >= 8 && i <= 10);
      d.style.background = hot ? "#35d0d0" : "#2b6f78";
      host.appendChild(d);
    });
  }

  // sample state = the mockup's renderVals, so the standalone page matches the design pixel-for-pixel
  const SAMPLE = {
    bins: [0, 1, 1, 2, 4, 9, 18, 34, 56, 70, 48, 24, 10, 4, 2, 1, 0, 0, 0, 0],
  };

  // ---- camera preview + FaceMesh overlay (#185) -----------------------------
  // The picture AND the landmark cloud are drawn onto one canvas from the SAME /preview frame, so the
  // dots can never run ahead of the picture (they used to ride separate paths — dots over the fast
  // SSE/canvas, picture over the slower MJPEG <img> — which made the landmarks look "more reactive"
  // than the video). previewBmp/previewLm/previewBox are set together when each frame's JPEG decodes.
  // Coordinates are per-mille (0..1000) of the (un-mirrored) frame; the engine sends an empty overlay
  // when there's no face. object-fit:cover is replicated here so the dots stay glued to the face.
  let previewBmp = null, previewLm = null, previewBox = null, showLandmarks = true;
  function drawPreview() {
    const cv = $("cam-overlay");
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const bw = cv.clientWidth, bh = cv.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.round(bw * dpr), H = Math.round(bh * dpr);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, bw, bh);  // transparent when no frame → the SVG placeholder shows through

    if (!previewBmp) return;
    // object-fit:cover: scale the frame to fill the box, center-crop the overflow.
    const fw = previewBmp.width, fh = previewBmp.height;
    const scale = Math.max(bw / fw, bh / fh);
    const dw = fw * scale, dh = fh * scale, ox = (bw - dw) / 2, oy = (bh - dh) / 2;
    ctx.drawImage(previewBmp, ox, oy, dw, dh);

    const pts = previewLm;
    if (!showLandmarks || !Array.isArray(pts) || pts.length < 2) return;
    const X = (p) => ox + (p / 1000) * dw;
    const Y = (p) => oy + (p / 1000) * dh;
    const box = previewBox;
    if (Array.isArray(box) && box.length === 4) {
      const bx = X(box[0]), by = Y(box[1]), bbw = (box[2] / 1000) * dw, bbh = (box[3] / 1000) * dh;
      ctx.strokeStyle = "rgba(53,208,208,.9)"; ctx.lineWidth = 1.5;
      if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(bx, by, bbw, bbh, 6); ctx.stroke(); }
      else ctx.strokeRect(bx, by, bbw, bbh);
    }
    // landmark cloud — tiny squares (cheaper than arcs at ~478 points), size scaled to preview width.
    ctx.fillStyle = "rgba(53,208,208,.72)";
    const r = Math.max(0.6, 1.1 * (bw / 352));
    for (let i = 0; i + 1 < pts.length; i += 2) ctx.fillRect(X(pts[i]) - r, Y(pts[i + 1]) - r, r * 2, r * 2);
  }

  // ---- live state -> DOM -----------------------------------------------------
  function setText(id, v) { const e = $(id); if (e != null && v != null) e.textContent = v; }

  // Show a pose tile only when the backend supplies its value; hide it otherwise so the UI never
  // displays a pose axis the connected engine doesn't actually estimate (#212).
  function gatePoseTile(tileId, valId, v) {
    const tile = $(tileId); if (tile) tile.style.display = v != null ? "" : "none";
    if (v != null) setText(valId, v);
  }

  // Legend built from the same segments as the bar, so colors/labels always match. Only rebuilt
  // when the (label, color) signature changes, not on every ~20 Hz tick.
  let legendSig = null;
  function renderLatencyLegend(segments) {
    const host = $("lat-legend");
    if (!host || !Array.isArray(segments)) return;
    const sig = segments.map((g) => (g.label || "") + "|" + (g.capture ? "capture" : g.bg || "")).join(";");
    if (sig === legendSig) return;
    legendSig = sig;
    host.textContent = "";
    segments.forEach((g) => {
      const leg = document.createElement("div"); leg.className = "leg";
      const sw = document.createElement("span"); sw.className = "leg-sw" + (g.capture ? " sw-capture" : "");
      if (!g.capture && g.bg) sw.style.background = g.bg;
      leg.appendChild(sw);
      leg.appendChild(document.createTextNode(g.label || ""));
      host.appendChild(leg);
    });
  }

  function applyState(s) {
    if (!s) return;
    if (s.title) setText("tb-title", s.title);

    // camera
    if (s.cam_res) setText("cam-res", s.cam_res);
    if (s.face_badge) {
      const b = $("cam-badge"); if (b) b.classList.toggle("searching", !s.face_locked);
      setText("cam-badge-txt", s.face_badge);
    }
    // The "show landmarks" toggle gates whether the dots are drawn; the picture + dots themselves
    // arrive on /preview (#185). Redraw so toggling takes effect between preview frames.
    if (s.show_landmarks !== undefined) { showLandmarks = s.show_landmarks !== false; drawPreview(); }

    // system health rows
    if (Array.isArray(s.health)) {
      const host = $("health-list"); if (host) {
        host.innerHTML = "";
        s.health.forEach((h) => {
          const row = document.createElement("div"); row.className = "health-row";
          row.innerHTML =
            '<div class="health-l"><span class="dot"></span><span class="health-label"></span></div>' +
            '<div class="health-val"></div>';
          row.querySelector(".dot").style.background = h.dot || "#5b6678";
          row.querySelector(".health-label").textContent = h.label || "";
          row.querySelector(".health-val").textContent = h.value || "";
          host.appendChild(row);
        });
      }
    }

    // meters
    renderOutputLevel(s.output_level);
    if (s.render_load) {
      setText("render-load-txt", s.render_load.text);
      const f = $("render-load-fill"); if (f) { f.style.width = (s.render_load.pct || 0) + "%"; f.className = "bar-fill " + (s.render_load.level || "good"); }
      const p = $("render-load-peak"); if (p && s.render_load.peak_pct != null) p.style.left = s.render_load.peak_pct + "%";
    }
    if (s.buffer) { setText("buffer-txt", s.buffer.text); renderBufferCells(s.buffer); }
    if (s.dropouts) { const t = $("dropouts-txt"); if (t) { t.textContent = s.dropouts.text; t.style.color = s.dropouts.color || "#4cd98a"; } const f = $("dropouts-fill"); if (f) f.style.width = (s.dropouts.pct || 0) + "%"; }

    // predict toggle
    if (s.predict != null) { const t = $("predict-toggle"); if (t) t.setAttribute("aria-checked", s.predict ? "true" : "false"); }

    // invert-head-tracking toggle (#189)
    if (s.invert_tracking != null) { const t = $("invert-toggle"); if (t) t.setAttribute("aria-checked", s.invert_tracking ? "true" : "false"); }

    // ring / pose
    if (s.pose) {
      setText("pose-yaw", s.pose.yaw);
      // Show PITCH/ROLL only when the connected backend estimates them (#212): the Rust engine omits
      // them so the tiles hide; the Python backend sends real values so they stay. Guards against a
      // backend ever showing a pose axis it doesn't actually track.
      gatePoseTile("tile-pitch", "pose-pitch", s.pose.pitch);
      gatePoseTile("tile-roll", "pose-roll", s.pose.roll);
      if (s.pose.yaw_deg != null) {
        const r = `rotate(${-s.pose.yaw_deg} 160 160)`;
        const cone = $("radar-cone"); if (cone) cone.setAttribute("transform", r);
        const head = $("radar-head"); if (head) head.setAttribute("transform", `${r} translate(160 160) scale(1.5) translate(-160 -160)`);
      }
    }
    if (s.facing) setText("facing-pill", s.facing);

    // latency
    if (s.latency) {
      setText("lat-ms", s.latency.ms);
      setText("lat-detail", s.latency.detail);
      if (s.latency.verdict) { const v = $("lat-verdict"); if (v) { v.textContent = s.latency.verdict; v.className = "pill-verdict " + (s.latency.verdict_level || "good"); } }
      if (Array.isArray(s.latency.segments)) {
        const bar = $("lat-bar"); if (bar) {
          bar.innerHTML = "";
          s.latency.segments.forEach((seg) => {
            const d = document.createElement("div"); d.className = "lat-seg" + (seg.capture ? " seg-capture" : "");
            d.style.flex = (seg.ms || 0) + " 1 0";
            if (!seg.capture) { d.style.background = seg.bg; d.style.color = seg.fg; }
            d.textContent = seg.label; bar.appendChild(d);
          });
        }
        renderLatencyLegend(s.latency.segments);
      }
      if (Array.isArray(s.latency.bins)) buildHistogram(s.latency.bins, s.latency.p50_pct);
      if (s.latency.p50_pct != null) {
        const ln = $("hist-p50-line"); if (ln) ln.style.left = s.latency.p50_pct + "%";
        const lb = $("hist-p50-label"); if (lb) lb.style.left = s.latency.p50_pct + "%";
      }
      if (s.latency.axis_lo != null && s.latency.axis_hi != null) {
        const ax = document.querySelectorAll(".lat-axis span");  // keep labels on the real range
        if (ax.length >= 3) {
          ax[0].textContent = s.latency.axis_lo;
          ax[1].textContent = Math.round((s.latency.axis_lo + s.latency.axis_hi) / 2);
          ax[2].textContent = s.latency.axis_hi;
        }
      }
      if (s.latency.note) setText("lat-note", s.latency.note);
    }
  }

  function setCameraFrame(dataURL) {
    const img = $("cam-img"), ph = $("cam-placeholder");
    if (!img) return;
    img.src = dataURL; img.style.display = "block";
    if (ph) ph.style.display = "none";
  }

  // ---- control wiring --------------------------------------------------------
  function wire() {
    const gain = $("output-gain"), gainReset = $("output-gain-reset");
    if (gain) {
      gain.addEventListener("input", () => { gainEditing = true; setText("output-gain-value", gainText(Number(gain.value))); });
      gain.addEventListener("change", () => submitGain(Number(gain.value)));
      gain.addEventListener("blur", () => { gainEditing = false; drawGain(); });
    }
    if (gainReset) gainReset.addEventListener("click", () => submitGain(0));
    const pt = $("predict-toggle");
    if (pt) pt.addEventListener("click", () => {
      const on = pt.getAttribute("aria-checked") !== "true";
      pt.setAttribute("aria-checked", on ? "true" : "false"); call("set_predict", on);
    });
    const it = $("invert-toggle");
    if (it) it.addEventListener("click", () => {
      const on = it.getAttribute("aria-checked") !== "true";
      it.setAttribute("aria-checked", on ? "true" : "false"); call("set_invert_tracking", on);
    });
    const sb = $("settings-btn"); if (sb) sb.addEventListener("click", () => window.open("/settings.html", "cockpit-settings", "width=576,height=1012"));
    const rc = $("recenter-btn"); if (rc) rc.addEventListener("click", () => call("recenter"));
    const frc = $("first-run-cue-dismiss"); if (frc) frc.addEventListener("click", dismissFirstRunCue);
    // Press C to recenter (re-zero the world-lock), matching the first-run cue (#234). Ignored while
    // typing in a field, and when a modifier is held so Ctrl/⌘+C (copy) is untouched.
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      if (e.key === "c" || e.key === "C") {
        e.preventDefault();
        call("recenter");
        toast("Recentered — the ring is re-zeroed to where you're facing now.", "info");
        dismissFirstRunCue();
      }
    });
    // The experiments drawer is hidden (#212), so its tab/A-B/shuffle controls are not wired here.
    // Keep the preview + overlay aligned when the window (and the preview box) resizes.
    window.addEventListener("resize", () => drawPreview());
  }

  // expose (for tests / manual injection)
  window.cockpit = { applyState, setCameraFrame, toast };

  function connect() {
    // Close the window when the backend is gone: if the SSE stays down past the grace window the
    // engine was killed (e.g. single-instance replace), so this stale UI should not linger. A
    // transient drop reconnects within the grace and is ignored. Chrome may block window.close()
    // for some windows, so fall back to a clean "engine stopped" message.
    let gone = null, dropped = false;
    const alive = () => { if (gone) { clearTimeout(gone); gone = null; } };
    const lost = () => {
      dropped = true;
      gainStateVersion++; gainRequestVersion++;
      outputGain = null; gainEditing = false; gainPending = null; clearTimeout(gainTimer); drawGain();
      setText("output-gain-value", "Disconnected");
      setText("output-level-text", "Waiting for audio levels…");
      // Clear the preview so the placeholder shows through if the feed drops (#185).
      if (previewBmp && previewBmp.close) previewBmp.close();
      previewBmp = null; previewLm = null; previewBox = null; drawPreview();
      if (gone) return;
      // grace long enough that a reset-to-defaults relaunch (exit 97 -> re-open on the same port,
      // ~8 s to re-load) reconnects before we give up and close the window.
      gone = setTimeout(() => {
        try { window.close(); } catch (_) { /* blocked */ }
        try {
          document.body.innerHTML =
            '<div style="color:#b3bfd2;font:600 15px Inter,system-ui,sans-serif;background:#0d1117;' +
            'height:100vh;display:flex;align-items:center;justify-content:center">' +
            'Engine stopped — you can close this window.</div>';
        } catch (_) { /* ignore */ }
      }, 11000);
    };
    try {
      const es = new EventSource("/events");
      es.onopen = () => {
        alive();
        dropped = false;  // engine came back; the /preview EventSource reconnects on its own
      };
      es.onmessage = (e) => {
        alive();
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.type === "state") applyState(m.state);
        else if (m.type === "toast") toast(m.message, m.kind);
        // The experiments drawer is hidden (#212), but the Python backend still pushes localization-
        // test progress via drawer_status; surface it as a toast so that status isn't lost.
        else if (m.type === "drawer_status" && m.text) toast(m.text, "info");
      };
      es.onerror = lost;   // dropped; close if it doesn't recover within the grace
    } catch (_) { /* no live feed */ }
  }
  // Stream the camera preview over /preview: each SSE event carries the JPEG (base64) AND the
  // landmark overlay from the SAME frame, so the picture and the dots are drawn together — the dots
  // can't get ahead of the picture. EventSource reconnects on its own if the engine restarts.
  function startPreview() {
    const decode = (b64) => {
      const bin = atob(b64), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Blob([bytes], { type: "image/jpeg" });
    };
    try {
      const es = new EventSource("/preview");
      es.onmessage = (e) => {
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        const ov = m.ov || {};
        // Capture this frame's overlay in the closure so it is applied together with THIS frame's
        // decoded bitmap (below), never paired with a newer frame while the JPEG is still decoding.
        const lm = Array.isArray(ov.lm) ? ov.lm : null;
        const box = Array.isArray(ov.box) ? ov.box : null;
        if (!m.img) { previewLm = lm; previewBox = box; drawPreview(); return; }
        createImageBitmap(decode(m.img)).then((bmp) => {
          if (previewBmp && previewBmp.close) previewBmp.close();
          previewBmp = bmp; previewLm = lm; previewBox = box;
          drawPreview();
        }).catch(() => { /* a bad frame: keep the last one */ });
      };
      // es.onerror: EventSource auto-reconnects; the last frame stays until a new one arrives.
    } catch (_) { /* no live feed */ }
  }

  document.addEventListener("DOMContentLoaded", () => {
    wire();
    buildHistogram(SAMPLE.bins, null);  // sample histogram so the page matches the design before data
    renderBufferCells({ fill_blocks: 7, cushion_blocks: 6, capacity: 12 });  // sample buffer meter
    if (location.protocol.indexOf("http") === 0) {
      connect(); startPreview();
      maybeShowFirstRunCue();  // first-run success cue (#234), hosted only — keeps the standalone preview pristine
      // Size the window reliably from the page: Chrome ignores --window-size for an --app window when
      // Chrome is already running (and our port changes each launch, so it can't remember either), so
      // resize ourselves to the saved size. window.resizeTo works on a standalone --app window.
      const startupGainVersion = gainStateVersion;
      fetch("/api/get_config").then((r) => { if (!r.ok) throw new Error("config unavailable"); return r.json(); }).then((c) => {
        if (gainStateVersion === startupGainVersion) { setGainMax(c.max_gain_db); receiveGain(c.output_gain_db); }
        if (c.win_w && c.win_h) { try { window.resizeTo(c.win_w, c.win_h); } catch (_) { /* blocked */ } }
        showBuild(c.version);
      }).catch(() => {});
      // remember the window size: report it (debounced) on resize so the engine reopens at this
      // size next launch. outerWidth/Height is what Chrome's --window-size controls. Arm only after
      // startup settles, so Chrome's initial/DPI layout resize can't ratchet the saved size down.
      let wsT, armed = false;
      setTimeout(() => { armed = true; }, 1500);
      window.addEventListener("resize", () => {
        if (!armed) return;
        clearTimeout(wsT);
        wsT = setTimeout(() => call("window_size", window.outerWidth, window.outerHeight), 700);
      });
    }
  });
})();
