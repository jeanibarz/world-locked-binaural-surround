/* Settings dialog logic. Seeds options/values from window.pywebview.api.get_config() when hosted,
   wires every control to window.pywebview.api.*, and no-ops gracefully standalone (so the page is
   still clickable for visual testing). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const call = (name, ...args) => fetch("/api/" + name, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
  }).catch(() => {});

  const BLOCKS = [256, 512, 1024, 2048];

  // ---- lightweight dropdown popup (the design's "field ▾" opens this) ----
  function openMenu(anchor, options, onPick) {
    document.querySelectorAll(".dc-menu").forEach((m) => m.remove());
    const r = anchor.getBoundingClientRect();
    const menu = document.createElement("div");
    menu.className = "dc-menu";
    Object.assign(menu.style, {
      position: "fixed", left: r.left + "px", top: (r.bottom + 4) + "px", minWidth: r.width + "px",
      maxHeight: "260px", overflow: "auto", background: "#151c28", border: "1px solid #2a3547",
      borderRadius: "8px", padding: "4px", zIndex: 1000, boxShadow: "0 8px 24px rgba(0,0,0,.5)",
    });
    options.forEach((opt) => {
      const label = typeof opt === "string" ? opt : opt.label;
      const value = typeof opt === "string" ? opt : opt.value;
      const it = document.createElement("div");
      it.textContent = label;
      Object.assign(it.style, { padding: "7px 10px", borderRadius: "6px", fontSize: "14px", color: "#e6edf6", cursor: "pointer" });
      it.addEventListener("mouseenter", () => it.style.background = "#1b2432");
      it.addEventListener("mouseleave", () => it.style.background = "transparent");
      it.addEventListener("click", () => { menu.remove(); onPick(value, label); });
      menu.appendChild(it);
    });
    document.body.appendChild(menu);
    setTimeout(() => document.addEventListener("click", function h(e) {
      if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", h); }
    }), 0);
  }

  function fieldText(fieldEl, text) {
    // the field's displayed value is the .val span's leading text node, else a plain value <span>
    // (method/HRIR/camera use <span>value</span><span class="caret">), else a leading text node
    const val = fieldEl.querySelector(".val");
    if (val) { val.childNodes[0].nodeValue = text + " "; return; }
    const s = fieldEl.querySelector("span:not(.caret)");
    if (s) { s.textContent = text; return; }
    if (fieldEl.firstChild) fieldEl.firstChild.nodeValue = text + " ";
  }

  function wireDropdown(id, getOptions, apiName, render) {
    const el = $(id); if (!el) return;
    el.addEventListener("click", () => openMenu(el, getOptions(), (v, label) => {
      fieldText(el, render ? render(v, label) : label);
      call(apiName, v);
    }));
  }

  function wireSegment(id, apiName) {
    const seg = $(id); if (!seg) return;
    seg.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
      seg.querySelectorAll(".seg-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active"); call(apiName, b.dataset.v);
    }));
  }

  function wireToggle(id, apiName) {
    const t = $(id); if (!t) return;
    t.addEventListener("click", () => {
      const on = t.getAttribute("aria-checked") !== "true";
      t.setAttribute("aria-checked", on ? "true" : "false"); call(apiName, on);
    });
  }

  function init(cfg) {
    cfg = cfg || {};
    // --- camera / resolution / fps ---
    const cams = (cfg.cameras || [["Camera 0", 0]]).map(([label, idx]) => ({ label, value: idx }));
    wireDropdown("set-camera", () => cams, "set_camera", () => $("set-camera").firstChild.nodeValue);
    const RES = cfg.resolutions || ["default", "320×240", "640×480", "848×480", "1280×720"];
    const FPS = cfg.framerates || ["default", "30 fps", "60 fps"];
    $("set-resolution").addEventListener("click", () => openMenu($("set-resolution"), RES, (v) => { fieldText($("set-resolution"), v); call("set_cam_format", ...parseFmt(v, curFps())); }));
    $("set-fps").addEventListener("click", () => openMenu($("set-fps"), FPS, (v) => { fieldText($("set-fps"), v); call("set_cam_format", ...parseFmt(curRes(), v)); }));

    // --- engine ---
    wireDropdown("set-method", () => cfg.methods || ["nearest", "timealign", "minphase", "notchwarp"], "set_method");
    wireSegment("set-backend", "set_backend");

    // --- audio output --- (cushion is defined in BLOCKS; ms is shown as a derived hint)
    const sr = cfg.samplerate || 48000;
    let blockIdx = Math.max(0, BLOCKS.indexOf(cfg.block || 1024));
    const blockMs = () => BLOCKS[blockIdx] / sr * 1000;
    let cushBlk = Math.max(2, Math.round((cfg.cushion_ms != null ? cfg.cushion_ms : 60) / blockMs()));

    const showCushion = () => {
      $("cushion-val").textContent = cushBlk + " blk";
      $("cushion-ms").textContent = Math.round(cushBlk * blockMs()) + " ms";
    };
    const showBlock = () => {
      $("block-val").textContent = String(BLOCKS[blockIdx]);
      $("block-ms").textContent = blockMs().toFixed(1) + " ms per block";
      showCushion();  // block size changes the cushion's duration, so refresh its ms hint
    };
    $("block-minus").addEventListener("click", () => {
      if (blockIdx > 0) { blockIdx--; showBlock(); call("set_block", BLOCKS[blockIdx]); }
    });
    $("block-plus").addEventListener("click", () => {
      if (blockIdx < BLOCKS.length - 1) { blockIdx++; showBlock(); call("set_block", BLOCKS[blockIdx]); }
    });
    const applyCushion = () => { showCushion(); call("set_cushion", cushBlk * blockMs()); };
    $("cushion-minus").addEventListener("click", () => { cushBlk = Math.max(2, cushBlk - 1); applyCushion(); });
    $("cushion-plus").addEventListener("click", () => { cushBlk = Math.min(64, cushBlk + 1); applyCushion(); });
    showBlock();

    wireToggle("exclusive-toggle", "set_exclusive");

    // --- spatial filter ---
    wireDropdown("set-hrir", () => cfg.hrir || ["mine"], "set_hrir");
    wireSegment("set-comp", "set_comp");

    // --- headphone compensation (EQ on/off is visual for now; model list if provided) ---
    wireToggle("hpeq-toggle", "set_hpeq");
    if (cfg.hp_models) wireDropdown("set-hpmodel", () => cfg.hp_models, "set_hp_model");

    // --- seed current selections from the live config ---
    if (cfg.backend) selectSeg("set-backend", cfg.backend);
    if (cfg.comp) selectSeg("set-comp", cfg.comp);
    if (cfg.method) fieldText($("set-method"), cfg.method);
    if (cfg.hrir_name) fieldText($("set-hrir"), cfg.hrir_name);
    const setToggle = (id, on) => { const t = $(id); if (t) t.setAttribute("aria-checked", on ? "true" : "false"); };
    if (cfg.exclusive != null) setToggle("exclusive-toggle", cfg.exclusive);
    if (cfg.hp_eq != null) setToggle("hpeq-toggle", cfg.hp_eq);

    // --- edit session --- changes apply live but are saved only on Confirm; Cancel / ✕ / closing
    // the window restores the engine to how it was when the dialog opened (the snapshot + revert
    // live in the engine). This is a separate window, so closing it must NOT quit the app.
    call("settings_begin");
    let done = false;
    const finish = (how) => { if (done) return; done = true; call(how); window.close(); };
    $("settings-apply").addEventListener("click", () => finish("settings_commit"));   // "Confirm"
    $("settings-cancel").addEventListener("click", () => finish("settings_cancel"));
    $("settings-close").addEventListener("click", () => finish("settings_cancel"));   // ✕ = discard
    $("reset-defaults").addEventListener("click", () => call("reset_defaults"));
    // closing the OS window without choosing = Cancel (restore); sendBeacon survives unload
    window.addEventListener("beforeunload", () => {
      if (done) return;
      done = true;
      try { navigator.sendBeacon("/api/settings_cancel", new Blob(["[]"], { type: "application/json" })); } catch (e) { /* best effort */ }
    });

    const sb = document.querySelector(".settings-body");  // reveal only once seeded with live values
    if (sb) sb.style.visibility = "visible";              // (no flash of the HTML default values)
  }

  const parseFmt = (res, fps) => { const m = (res || "").match(/(\d+)\D+(\d+)/); const f = (fps || "").match(/(\d+)/); return [m ? +m[1] : 0, m ? +m[2] : 0, f ? +f[1] : 0]; };
  const curRes = () => ($("set-resolution").querySelector(".val").childNodes[0].nodeValue || "").trim();
  const curFps = () => ($("set-fps").querySelector(".val").childNodes[0].nodeValue || "").trim();
  function selectSeg(id, v) { const s = $(id); if (!s) return; s.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.v === v)); }

  document.addEventListener("DOMContentLoaded", () => {
    if (location.protocol.indexOf("http") === 0) {
      fetch("/api/get_config").then((r) => r.json()).then(init).catch(() => init(null));
    } else {
      init(null);  // standalone (file://): wire with defaults so the page is interactive
    }
  });
})();
