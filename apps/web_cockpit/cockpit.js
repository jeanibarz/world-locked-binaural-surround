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

  // ---- FaceMesh overlay (#185) ----------------------------------------------
  // Draw the landmark cloud + face box over the camera preview so the operator can SEE the tracking.
  // The preview <img> uses object-fit:cover, so we replicate that crop here (from the img's natural
  // frame size) to keep the dots glued to the face. Coordinates arrive per-mille (0..1000) of the
  // (un-mirrored) frame; the engine omits them when there's no face or the toggle is off, and we
  // then clear. lastOverlay lets a resize redraw between engine ticks.
  let lastOverlay = null;
  function drawOverlay(s) {
    const cv = $("cam-overlay"), img = $("cam-img");
    if (!cv || !img) return;
    if (s) lastOverlay = { show: s.show_landmarks !== false, pts: s.landmarks, box: s.face_box, yaw: s.pose && s.pose.yaw_deg };
    const o = lastOverlay || {};

    const ctx = cv.getContext("2d");
    const bw = cv.clientWidth, bh = cv.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    const W = Math.round(bw * dpr), H = Math.round(bh * dpr);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, bw, bh);

    const pts = o.pts;
    if (!o.show || !Array.isArray(pts) || pts.length < 2) return;
    const fw = img.naturalWidth, fh = img.naturalHeight;
    if (!fw || !fh || img.style.display === "none") return;  // no live frame yet to crop against

    // object-fit:cover: scale to fill the box, center-crop the overflow.
    const scale = Math.max(bw / fw, bh / fh);
    const dw = fw * scale, dh = fh * scale, ox = (bw - dw) / 2, oy = (bh - dh) / 2;
    const X = (p) => ox + (p / 1000) * dw;
    const Y = (p) => oy + (p / 1000) * dh;

    const box = o.box;
    if (Array.isArray(box) && box.length === 4) {
      const bx = X(box[0]), by = Y(box[1]), bbw = (box[2] / 1000) * dw, bbh = (box[3] / 1000) * dh;
      ctx.strokeStyle = "rgba(53,208,208,.9)"; ctx.lineWidth = 1.5;
      if (ctx.roundRect) { ctx.beginPath(); ctx.roundRect(bx, by, bbw, bbh, 6); ctx.stroke(); }
      else ctx.strokeRect(bx, by, bbw, bbh);
      // coarse yaw needle from the box top-center — just a turn cue; the ring radar is the precise
      // readout. Leans toward the turn side (yaw+ = turned left), clamped to ±45°.
      if (typeof o.yaw === "number") {
        const cx = bx + bbw / 2, a = Math.max(-45, Math.min(45, o.yaw)) * Math.PI / 180, len = 16;
        ctx.strokeStyle = "rgba(76,217,138,.95)"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(cx, by); ctx.lineTo(cx - Math.sin(a) * len, by - Math.cos(a) * len); ctx.stroke();
      }
    }

    // landmark cloud — tiny squares (cheaper than arcs at ~478 points × 20 Hz), size scaled to the
    // preview width.
    ctx.fillStyle = "rgba(53,208,208,.72)";
    const r = Math.max(0.6, 1.1 * (bw / 352));
    for (let i = 0; i + 1 < pts.length; i += 2) ctx.fillRect(X(pts[i]) - r, Y(pts[i + 1]) - r, r * 2, r * 2);
  }

  // ---- live state -> DOM -----------------------------------------------------
  function setText(id, v) { const e = $(id); if (e != null && v != null) e.textContent = v; }

  function applyState(s) {
    if (!s) return;
    if (s.title) setText("tb-title", s.title);

    // camera
    if (s.cam_res) setText("cam-res", s.cam_res);
    if (s.face_badge) {
      const b = $("cam-badge"); if (b) b.classList.toggle("searching", !s.face_locked);
      setText("cam-badge-txt", s.face_badge);
    }
    drawOverlay(s);  // FaceMesh landmark overlay (#185)

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
      setText("pose-yaw", s.pose.yaw); setText("pose-pitch", s.pose.pitch); setText("pose-roll", s.pose.roll);
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
    if (s.drawer_status) setText("drawer-status", s.drawer_status);
  }

  function setCameraFrame(dataURL) {
    const img = $("cam-img"), ph = $("cam-placeholder");
    if (!img) return;
    img.src = dataURL; img.style.display = "block";
    if (ph) ph.style.display = "none";
  }

  // ---- control wiring --------------------------------------------------------
  function wire() {
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
    const ss = $("shuffle-start"); if (ss) ss.addEventListener("click", () => call("blind_shuffle_start"));

    document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("tab-active"));
      t.classList.add("tab-active"); call("set_drawer_tab", t.dataset.tab);
    }));
    // Keep the FaceMesh overlay aligned when the preview resizes (the box is % of the window).
    window.addEventListener("resize", () => drawOverlay(null));
    const abA = $("ab-a"), abB = $("ab-b");
    if (abA && abB) {
      abA.addEventListener("click", () => { abA.classList.add("ab-active"); abB.classList.remove("ab-active"); call("blind_play", "A"); });
      abB.addEventListener("click", () => { abB.classList.add("ab-active"); abA.classList.remove("ab-active"); call("blind_play", "B"); });
    }
  }

  // expose (for tests / manual injection)
  window.cockpit = { applyState, setCameraFrame, toast };

  function connect() {
    // Close the window when the backend is gone: if the SSE stays down past the grace window the
    // engine was killed (e.g. single-instance replace), so this stale UI should not linger. A
    // transient drop reconnects within the grace and is ignored. Chrome may block window.close()
    // for some windows, so fall back to a clean "engine stopped" message.
    let gone = null;
    const alive = () => { if (gone) { clearTimeout(gone); gone = null; } };
    const lost = () => {
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
      es.onopen = alive;
      es.onmessage = (e) => {
        alive();
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.type === "state") applyState(m.state);
        else if (m.type === "toast") toast(m.message, m.kind);
        else if (m.type === "drawer_status") { const d = $("drawer-status"); if (d) d.textContent = "Status: " + m.text; }
      };
      es.onerror = lost;   // dropped; close if it doesn't recover within the grace
    } catch (_) { /* no live feed */ }
  }
  function startCamera() {
    const img = $("cam-img"), ph = $("cam-placeholder");
    if (!img) return;
    img.onload = () => { img.style.display = "block"; if (ph) ph.style.display = "none"; };
    img.onerror = () => { img.style.display = "none"; if (ph) ph.style.display = "block"; };
    img.src = "/camera";
  }

  document.addEventListener("DOMContentLoaded", () => {
    wire();
    buildHistogram(SAMPLE.bins, null);  // sample histogram so the page matches the design before data
    renderBufferCells({ fill_blocks: 7, cushion_blocks: 6, capacity: 12 });  // sample buffer meter
    if (location.protocol.indexOf("http") === 0) {
      connect(); startCamera();
      // Size the window reliably from the page: Chrome ignores --window-size for an --app window when
      // Chrome is already running (and our port changes each launch, so it can't remember either), so
      // resize ourselves to the saved size. window.resizeTo works on a standalone --app window.
      fetch("/api/get_config").then((r) => r.json()).then((c) => {
        if (c.win_w && c.win_h) { try { window.resizeTo(c.win_w, c.win_h); } catch (_) { /* blocked */ } }
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
