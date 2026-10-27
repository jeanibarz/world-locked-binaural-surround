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
    // C is the live config; seed(c) replaces it and refreshes every control, so the same function
    // runs on first load and again after "reset to defaults". Handlers read C / the state below at
    // click time, so they are wired exactly once.
    let C = cfg || {};
    let blocks = BLOCKS.slice();
    let blockIdx = 0, cushionMs = 60, cushBlk = 2;
    const setToggle = (id, on) => { const t = $(id); if (t) t.setAttribute("aria-checked", on ? "true" : "false"); };
    const sr = () => C.samplerate || 48000;
    const blockMs = () => blocks[blockIdx] / sr() * 1000;
    const cams = () => (C.cameras || [["Camera 0", 0]]).map(([label, idx]) => ({ label, value: idx }));

    // --- camera / resolution / fps ---
    // No custom render: the picked camera's own label (its name) is what we show. (The old render
    // returned `firstChild.nodeValue`, but the field's first child is the <span> element — whose
    // nodeValue is null — so choosing a camera blanked the field.)
    wireDropdown("set-camera", cams, "set_camera");
    const RES = () => C.resolutions || ["default", "320×240", "640×480", "848×480", "1280×720"];
    const FPS = () => C.framerates || ["default", "30 fps", "60 fps"];
    $("set-resolution").addEventListener("click", () => openMenu($("set-resolution"), RES(), (v) => { fieldText($("set-resolution"), v); call("set_cam_format", ...parseFmt(v, curFps())); }));
    $("set-fps").addEventListener("click", () => openMenu($("set-fps"), FPS(), (v) => { fieldText($("set-fps"), v); call("set_cam_format", ...parseFmt(curRes(), v)); }));
    wireToggle("fliph-toggle", "set_flip_h");  // camera un-mirror (#191)
    wireToggle("flipv-toggle", "set_flip_v");
    wireToggle("landmarks-toggle", "set_show_landmarks");  // FaceMesh overlay (#185)

    // --- engine ---
    wireDropdown("set-method", () => C.methods || ["nearest", "timealign", "minphase", "notchwarp"], "set_method");
    wireSegment("set-backend", "set_backend");

    // --- audio output --- (cushion is shown in blocks; ms is a derived hint)
    // The engine stores `cushion_ms` and derives blocks = max(2, round(ms / block_ms)). A block-size
    // change keeps cushion_ms and re-derives the blocks, so we mirror that (cushionMs is the source
    // of truth; cushBlk is what the engine will derive from it at the current block size).
    const cushFromMs = () => Math.max(2, Math.round(cushionMs / blockMs()));
    const showCushion = () => {
      $("cushion-val").textContent = cushBlk + " blk";
      $("cushion-ms").textContent = Math.round(cushBlk * blockMs()) + " ms";
    };
    const showBlock = () => {
      $("block-val").textContent = String(blocks[blockIdx]);
      $("block-ms").textContent = blockMs().toFixed(1) + " ms per block";
      showCushion();  // block size changes the cushion's duration, so refresh its ms hint
    };
    const applyBlock = () => {
      cushBlk = cushFromMs();  // engine keeps cushion_ms and recomputes blocks for the new block size
      showBlock(); call("set_block", blocks[blockIdx]);
    };
    $("block-minus").addEventListener("click", () => { if (blockIdx > 0) { blockIdx--; applyBlock(); } });
    $("block-plus").addEventListener("click", () => { if (blockIdx < blocks.length - 1) { blockIdx++; applyBlock(); } });
    const applyCushion = () => { cushionMs = cushBlk * blockMs(); showCushion(); call("set_cushion", cushionMs); };
    $("cushion-minus").addEventListener("click", () => { cushBlk = Math.max(2, cushBlk - 1); applyCushion(); });
    $("cushion-plus").addEventListener("click", () => { cushBlk = Math.min(64, cushBlk + 1); applyCushion(); });

    wireToggle("exclusive-toggle", "set_exclusive");

    // --- spatial filter ---
    wireDropdown("set-hrir", () => C.hrir || ["mine"], "set_hrir");
    wireSegment("set-comp", "set_comp");

    // --- headphone compensation --- EQ on/off toggle, plus the model picker (#240): wired to a
    // real dropdown when the engine reports discovered profiles (`hp_models`), so choosing one
    // switches the engine's loaded compensation profile live.
    wireToggle("hpeq-toggle", "set_hpeq");
    if (C.hp_models) wireDropdown("set-hpmodel", () => C.hp_models, "set_hp_model");

    // --- seed current selections from the live config (idempotent; also run after reset) ---
    const seed = (c) => {
      C = c || {};
      if (C.backend) selectSeg("set-backend", C.backend);
      if (C.comp) selectSeg("set-comp", C.comp);
      if (C.method) fieldText($("set-method"), C.method);
      if (C.hrir_name) fieldText($("set-hrir"), C.hrir_name);
      if (C.camera != null) {
        const cur = cams().find((x) => String(x.value) === String(C.camera));
        if (cur) fieldText($("set-camera"), cur.label);  // show the live camera, not the HTML default
      }
      // Resolution / fps must be seeded too: a lone fps change sends set_cam_format(res, fps), and
      // an unseeded resolution would silently reset the camera to the HTML default 640x480.
      fieldText($("set-resolution"), C.cam_w > 0 && C.cam_h > 0 ? C.cam_w + "×" + C.cam_h : "default");
      fieldText($("set-fps"), C.cam_fps > 0 ? C.cam_fps + " fps" : "default");
      if (C.exclusive != null) setToggle("exclusive-toggle", C.exclusive);
      if (C.hp_eq != null) setToggle("hpeq-toggle", C.hp_eq);
      // Headphone-comp model picker (#240): show the live selection (the engine's resolved profile
      // name), not the static HTML placeholder, so the field reflects what is actually loaded.
      if (C.hp_model) fieldText($("set-hpmodel"), C.hp_model);
      if (C.flip_h != null) setToggle("fliph-toggle", C.flip_h);
      if (C.flip_v != null) setToggle("flipv-toggle", C.flip_v);
      if (C.show_landmarks != null) setToggle("landmarks-toggle", C.show_landmarks);
      // block list: the standard sizes, plus the engine's real block when it is a non-standard one
      // (e.g. --block 384), so the stepper shows what the engine actually runs.
      blocks = BLOCKS.slice();
      const blk = C.block > 0 ? Math.round(C.block) : 1024;
      if (blocks.indexOf(blk) < 0) { blocks.push(blk); blocks.sort((x, y) => x - y); }
      blockIdx = blocks.indexOf(blk);
      cushionMs = C.cushion_ms != null ? C.cushion_ms : 60;
      cushBlk = cushFromMs();
      showBlock();
    };
    seed(C);

    // --- edit session --- changes apply live but are saved only on Confirm; Cancel / ✕ / closing
    // the window restores the engine to how it was when the dialog opened (the snapshot + revert
    // live in the engine). This is a separate window, so closing it must NOT quit the app.
    call("settings_begin");
    let done = false;
    // IMPORTANT: await the POST before window.close() — closing the window aborts an in-flight
    // fetch, so a non-awaited settings_commit would silently fail to persist.
    const finish = async (how) => { if (done) return; done = true; await call(how); window.close(); };
    $("settings-apply").addEventListener("click", () => finish("settings_commit"));   // "Confirm"
    $("settings-cancel").addEventListener("click", () => finish("settings_cancel"));
    $("settings-close").addEventListener("click", () => finish("settings_cancel"));   // ✕ = discard
    // reset applies live in the engine, so re-read the config afterwards and re-seed the dialog;
    // otherwise Confirm would commit values the user never saw. (No-ops standalone.)
    $("reset-defaults").addEventListener("click", async () => {
      await call("reset_defaults");
      if (location.protocol.indexOf("http") !== 0) return;
      try { seed(await (await fetch("/api/get_config")).json()); } catch (e) { /* keep current view */ }
    });
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
