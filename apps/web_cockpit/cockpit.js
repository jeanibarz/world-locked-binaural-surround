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
    if (s.buffer) { setText("buffer-txt", s.buffer.text); const f = $("buffer-fill"); if (f) { f.style.width = (s.buffer.pct || 0) + "%"; f.className = "bar-fill " + (s.buffer.level || "good"); } }
    if (s.dropouts) { const t = $("dropouts-txt"); if (t) { t.textContent = s.dropouts.text; t.style.color = s.dropouts.color || "#4cd98a"; } const f = $("dropouts-fill"); if (f) f.style.width = (s.dropouts.pct || 0) + "%"; }

    // predict toggle
    if (s.predict != null) { const t = $("predict-toggle"); if (t) t.setAttribute("aria-checked", s.predict ? "true" : "false"); }

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
    const sb = $("settings-btn"); if (sb) sb.addEventListener("click", () => call("open_settings"));
    const rc = $("recenter-btn"); if (rc) rc.addEventListener("click", () => call("recenter"));
    const ss = $("shuffle-start"); if (ss) ss.addEventListener("click", () => call("blind_shuffle_start"));

    document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("tab-active"));
      t.classList.add("tab-active"); call("set_drawer_tab", t.dataset.tab);
    }));
    const abA = $("ab-a"), abB = $("ab-b");
    if (abA && abB) {
      abA.addEventListener("click", () => { abA.classList.add("ab-active"); abB.classList.remove("ab-active"); call("blind_play", "A"); });
      abB.addEventListener("click", () => { abB.classList.add("ab-active"); abA.classList.remove("ab-active"); call("blind_play", "B"); });
    }
    document.querySelectorAll(".tb-btn").forEach((b) => b.addEventListener("click", () => call("window_" + b.dataset.win)));
  }

  // expose (for tests / manual injection)
  window.cockpit = { applyState, setCameraFrame, toast };

  function connect() {
    try {
      const es = new EventSource("/events");
      es.onmessage = (e) => {
        let m; try { m = JSON.parse(e.data); } catch (_) { return; }
        if (m.type === "state") applyState(m.state);
        else if (m.type === "toast") toast(m.message, m.kind);
        else if (m.type === "drawer_status") { const d = $("drawer-status"); if (d) d.textContent = "Status: " + m.text; }
      };
      es.onerror = () => {};  // EventSource auto-reconnects
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
    if (location.protocol.indexOf("http") === 0) { connect(); startCamera(); }  // live feed when served
  });
})();
