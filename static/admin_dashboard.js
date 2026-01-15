function readBootstrap() {
  const el = document.getElementById("dashboard-bootstrap");
  if (!el) return {};
  try {
    return JSON.parse(el.textContent || "{}");
  } catch (e) {
    console.error("Invalid dashboard bootstrap JSON", e);
    return {};
  }
}

const BOOT = readBootstrap();
const IS_LOCKED = !!BOOT.is_locked;
window.CONTENT_LOCKED = IS_LOCKED; // if you want global


// 1) Ensure your module exists
window.Chats = window.Chats || {};

// 2) Click handler for the "Generate / Refresh" button
document.addEventListener("click", (e) => {
  const btn = e.target.closest('[data-action="summary"]');
  if (!btn) return;

  const panel = btn.closest("[data-chat-panel]");
  if (!panel) return;

  const sessionId =
    Number(panel.getAttribute("data-session-id")) ||
    Number(panel.getAttribute("data-chat-panel")) ||
    Number(panel.dataset.sessionId);

  if (!sessionId) return console.error("Missing sessionId on panel", panel);

  window.Chats.refreshSummary(sessionId);
});


// ----------------------------
// START OF CONFIG PARTIAL (CLEAN)
// ----------------------------

window.initConfigUI = function initConfigUI(hostEl) {
  if (!hostEl) return;

  // If this host was already inited AND still contains a config bootstrap, skip.
  // (We delete this flag when reopening/reloading.)
  if (hostEl.__configUIInited) return;

  // ✅ Scoped DOM helpers (critical)
  const $ = (id) => {
    const el = hostEl.querySelector(`#${CSS.escape(id)}`);
    if (!el) throw new Error(`Config UI missing required element #${id}`);
    return el;
  };
  const $$ = (sel) => Array.from(hostEl.querySelectorAll(sel));

  // ✅ Read server bootstrap JSON from inside the injected partial
  const bootTag = hostEl.querySelector("#config-ui-bootstrap");
  if (!bootTag) {
    console.warn("Config UI bootstrap tag not found (#config-ui-bootstrap). Aborting init.");
    return;
  }

  let boot = {};
  try {
    boot = JSON.parse(bootTag.textContent || "{}");
  } catch (e) {
    console.error("Config UI bootstrap JSON parse failed:", e);
    boot = {};
  }

  // ✅ Only mark as inited after we confirm the partial exists
  hostEl.__configUIInited = true;

  function getFilePath() {
    // 1) hidden input (optional)
    const hidden = hostEl.querySelector("#configFilePath");
    if (hidden && hidden.value) return String(hidden.value).trim();

    // 2) bootstrap JSON (full-page and partial modes)
    if (boot && boot.file_path) return String(boot.file_path).trim();

    // 3) ✅ inline mode fallback (dataset set by openInlineConfig)
    if (hostEl.dataset && hostEl.dataset.filePath) return String(hostEl.dataset.filePath).trim();

    return "";
  }

  const IS_DEFAULTS = !!boot.is_defaults;
  const DEFAULT_WELCOME_NO_NAME =
    "Hi there! I’m {{assistant_name}}, your stay assistant for {{property_name}}.";

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function deepClone(x) {
    return JSON.parse(JSON.stringify(x || {}));
  }

  function splitLines(text) {
    return (text || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function joinLines(arr) {
    return (arr || []).join("\n");
  }

  function escapeHtml(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function sanitizePreviewText(s) {
    s = String(s || "");
    s = s.replace(/[\u200B-\u200D\uFEFF]/g, "");
    s = s.replace(/\s+/g, " ").trim();
    if (s.length > 260) s = s.slice(0, 260) + "…";
    return s;
  }

  // -----------------------
  // State
  // -----------------------
  let cfg = deepClone(boot.config_json || {});
  let dirty = false;
  let saving = false;
  let editingRaw = false;

  function ensureShape() {
    cfg.assistant = cfg.assistant || {};
    const a = cfg.assistant;
    a.voice = a.voice || {};
    a.do = Array.isArray(a.do) ? a.do : a.do ? [String(a.do)] : [];
    a.dont = Array.isArray(a.dont) ? a.dont : a.dont ? [String(a.dont)] : [];
    a.quick_replies = Array.isArray(a.quick_replies)
      ? a.quick_replies
      : a.quick_replies
      ? [String(a.quick_replies)]
      : [];
  }

  function setStatus(kind, text) {
    const dot = hostEl.querySelector("#dot");
    const statusText = hostEl.querySelector("#statusText");
    if (dot) dot.className = "status-dot " + (kind || "");
    if (statusText) statusText.textContent = text || "";
  }

  // -----------------------
  // Render
  // -----------------------
  function renderQuickReplies() {
    const list = hostEl.querySelector("#quickRepliesList");
    if (!list) return;

    list.innerHTML = "";
    const a = cfg.assistant || {};

    (a.quick_replies || []).forEach((txt, idx) => {
      const row = document.createElement("div");
      row.className = "list-item";
      row.draggable = true;
      row.dataset.idx = String(idx);

      row.innerHTML = `
        <div class="row" style="gap:10px;">
          <span class="drag">⋮⋮</span>
          <span>${escapeHtml(txt)}</span>
        </div>
        <div class="row" style="gap:8px;">
          <button class="btn" data-act="up" type="button">↑</button>
          <button class="btn" data-act="down" type="button">↓</button>
          <button class="btn danger" data-act="del" type="button">Remove</button>
        </div>
      `;

      row.addEventListener("click", (e) => {
        const act = e.target?.dataset?.act;
        if (!act) return;
        e.preventDefault();

        if (act === "del") {
          a.quick_replies.splice(idx, 1);
          markDirtyAndRender();
        } else if (act === "up" && idx > 0) {
          [a.quick_replies[idx - 1], a.quick_replies[idx]] = [
            a.quick_replies[idx],
            a.quick_replies[idx - 1],
          ];
          markDirtyAndRender();
        } else if (act === "down" && idx < a.quick_replies.length - 1) {
          [a.quick_replies[idx + 1], a.quick_replies[idx]] = [
            a.quick_replies[idx],
            a.quick_replies[idx + 1],
          ];
          markDirtyAndRender();
        }
      });

      row.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", row.dataset.idx);
      });
      row.addEventListener("dragover", (e) => e.preventDefault());
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
        const to = parseInt(row.dataset.idx, 10);
        if (Number.isNaN(from) || Number.isNaN(to) || from === to) return;

        const item = a.quick_replies.splice(from, 1)[0];
        a.quick_replies.splice(to, 0, item);
        markDirtyAndRender();
      });

      list.appendChild(row);
    });

    if ((a.quick_replies || []).length === 0) {
      const empty = document.createElement("div");
      empty.className = "muted small";
      empty.textContent = "No quick replies yet.";
      list.appendChild(empty);
    }
  }

  function render() {
    ensureShape();
    const a = cfg.assistant;

    // These MUST exist in your partial; $() will throw if missing.
    $("assistant_name").value = a.name || "Sandy";
    $("assistant_avatar_url").value = a.avatar_url || "/static/img/sandy.png";
    $("assistant_tone").value = a.tone || "luxury";
    $("assistant_verbosity").value = a.verbosity || "balanced";
    $("assistant_emoji_level").value = a.emoji_level || "light";
    $("assistant_formality").value = a.formality || "polished";
    $("assistant_style").value = a.style || "";
    $("assistant_extra_instructions").value = a.extra_instructions || "";

    $("assistant_do").value = joinLines(a.do);
    $("assistant_dont").value = joinLines(a.dont);

    $("voice_welcome_template").value = a.voice.welcome_template || "";
    $("voice_welcome_template_no_name").value =
      a.voice.welcome_template_no_name || DEFAULT_WELCOME_NO_NAME;
    $("voice_offline_message").value = a.voice.offline_message || "";
    $("voice_fallback_message").value = a.voice.fallback_message || "";
    $("voice_error_message").value = a.voice.error_message || "";

    renderQuickReplies();

    if (!editingRaw) {
      $("rawJson").value = JSON.stringify(cfg, null, 2);
    }

    // Preview bits (optional if your partial has them)
    const pvName = hostEl.querySelector("#pvName");
    const pvName2 = hostEl.querySelector("#pvName2");
    const pvWelcome = hostEl.querySelector("#pvWelcome");
    const pvReply = hostEl.querySelector("#pvReply");

    const name = a.name || "Sandy";
    if (pvName) pvName.textContent = name;
    if (pvName2) pvName2.textContent = name;

    if (pvWelcome) {
      const tplRaw = String(a.voice?.welcome_template_no_name || "");
      const tpl = tplRaw.trim();

      let welcomeText = tpl
        ? tpl
            .replaceAll("{{assistant_name}}", name)
            .replaceAll("{{property_name}}", "Casa Sea Esta")
        : DEFAULT_WELCOME_NO_NAME
            .replaceAll("{{assistant_name}}", name)
            .replaceAll("{{property_name}}", "Casa Sea Esta");

      pvWelcome.textContent = sanitizePreviewText(welcomeText);
    }

    if (pvReply) {
      const v = a.verbosity || "balanced";
      const t = a.tone || "luxury";

      let reply =
        "<b>WiFi</b><br/>Network: …<br/>Password: …<br/><br/><b>Parking</b><br/>Here are the best options…";
      if (v === "short")
        reply =
          "<b>WiFi</b><br/>Network: … / Password: …<br/><b>Parking</b><br/>Best option: …";
      if (v === "detailed")
        reply =
          "<b>WiFi</b><br/>Network: …<br/>Password: …<br/>Tip: …<br/><br/><b>Parking</b><br/>Options: …<br/>Notes: …<br/>Map: …";
      if (t === "luxury")
        reply = reply.replace(
          "Here are the best options…",
          "Here are the best options, tailored for a smooth arrival…"
        );

      pvReply.innerHTML = reply;
    }
  }

  // -----------------------
  // Form -> cfg
  // -----------------------
  function readFormIntoCfg() {
    ensureShape();
    const a = cfg.assistant;

    a.name = $("assistant_name").value.trim() || "Sandy";
    a.avatar_url = $("assistant_avatar_url").value.trim() || "/static/img/sandy.png";
    a.tone = $("assistant_tone").value;
    a.verbosity = $("assistant_verbosity").value;
    a.emoji_level = $("assistant_emoji_level").value;
    a.formality = $("assistant_formality").value;
    a.style = $("assistant_style").value.trim();
    a.extra_instructions = $("assistant_extra_instructions").value.trim();

    a.do = splitLines($("assistant_do").value);
    a.dont = splitLines($("assistant_dont").value);

    a.voice.welcome_template = $("voice_welcome_template").value;
    a.voice.welcome_template_no_name = $("voice_welcome_template_no_name").value;
    a.voice.offline_message = $("voice_offline_message").value;
    a.voice.fallback_message = $("voice_fallback_message").value;
    a.voice.error_message = $("voice_error_message").value;
  }

  function markDirtyAndRender() {
    dirty = true;
    setStatus("warn", "Unsaved changes…");
    render();
    scheduleAutosave();
  }

  // -----------------------
  // Save
  // -----------------------
  async function saveNow() {
    if (saving) return;
    saving = true;

    const btnSave = hostEl.querySelector("#btnSave");
    if (btnSave) btnSave.disabled = true;

    setStatus("warn", "Saving…");

    try {
      readFormIntoCfg();

      const file_path = getFilePath();
      if (!file_path) throw new Error("Missing file_path");

      const resp = await fetch("/admin/config-ui/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path, config: cfg }),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "Save failed");

      dirty = false;
      setStatus("ok", "Saved ✓");
    } catch (e) {
      console.error(e);
      setStatus("err", "Save failed: " + (e.message || e));
    } finally {
      saving = false;
      if (btnSave) btnSave.disabled = false;
    }
  }

  const scheduleAutosave = debounce(() => {
    if (dirty) saveNow();
  }, 900);

  // -----------------------
  // Reset
  // -----------------------
  async function resetToDefaults() {
    if (IS_DEFAULTS) {
      setStatus("ok", "You are editing defaults already.");
      return;
    }

    if (!confirm("Reset this config to defaults? This will overwrite the current file.")) return;

    try {
      setStatus("warn", "Resetting…");

      const file_path = getFilePath();
      if (!file_path) throw new Error("Missing file_path");

      const resp = await fetch("/admin/config-ui/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path }),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "Reset failed");

      cfg = deepClone(data.config || {});
      dirty = false;
      render();
      setStatus("ok", "Reset to defaults ✓");
    } catch (e) {
      console.error(e);
      setStatus("err", "Reset failed: " + (e.message || e));
    }
  }

  // -----------------------
  // Raw JSON helpers
  // -----------------------
  function applyRawToForm() {
    try {
      const parsed = JSON.parse($("rawJson").value);
      cfg = deepClone(parsed);
      dirty = true;
      render();
      setStatus("warn", "Applied raw JSON (unsaved)...");
      scheduleAutosave();
    } catch (e) {
      alert("Invalid JSON: " + (e.message || e));
    }
  }

  function syncFormToRaw() {
    readFormIntoCfg();
    $("rawJson").value = JSON.stringify(cfg, null, 2);
    setStatus("warn", "Synced form → raw JSON (unsaved)...");
    dirty = true;
    scheduleAutosave();
  }

  // -----------------------
  // Wire events (scoped)
  // -----------------------
  function wire() {
    // raw editor focus guard
    $("rawJson").addEventListener("focus", () => (editingRaw = true));
    $("rawJson").addEventListener("blur", () => (editingRaw = false));

    // Inputs -> autosave
    $$("input, textarea, select").forEach((el) => {
      el.addEventListener("input", () => {
        readFormIntoCfg();
        dirty = true;
        setStatus("warn", "Unsaved changes…");
        if (!editingRaw) $("rawJson").value = JSON.stringify(cfg, null, 2);
        scheduleAutosave();
        render();
      });
      el.addEventListener("change", () => {
        readFormIntoCfg();
        dirty = true;
        setStatus("warn", "Unsaved changes…");
        if (!editingRaw) $("rawJson").value = JSON.stringify(cfg, null, 2);
        scheduleAutosave();
        render();
      });
    });

    $("btnSave").addEventListener("click", (e) => {
      e.preventDefault();
      saveNow();
    });

    $("btnReload").addEventListener("click", async (e) => {
      e.preventDefault();
      const fp = getFilePath();
      if (!fp) return setStatus("err", "Missing file_path");

      const res = await fetch(`/admin/config-ui?file=${encodeURIComponent(fp)}&partial=1`);
      hostEl.innerHTML = res.ok
        ? await res.text()
        : `<div class="p-4 text-rose-700">Failed to load config</div>`;

      delete hostEl.__configUIInited;
      window.initConfigUI(hostEl);
    });

    const resetBtn = hostEl.querySelector("#btnResetAll");
    if (resetBtn) resetBtn.addEventListener("click", (e) => {
      e.preventDefault();
      resetToDefaults();
    });

    $("btnApplyRaw").addEventListener("click", (e) => {
      e.preventDefault();
      applyRawToForm();
    });

    $("btnSyncRaw").addEventListener("click", (e) => {
      e.preventDefault();
      syncFormToRaw();
    });

    const addBtn = hostEl.querySelector("#btnAddQuickReply");
    const input = hostEl.querySelector("#quickReplyInput");
    if (addBtn && input) {
      addBtn.addEventListener("click", (e) => {
        e.preventDefault();
        const v = input.value.trim();
        if (!v) return;
        ensureShape();
        cfg.assistant.quick_replies.push(v);
        input.value = "";
        markDirtyAndRender();
      });
    }
  }

  // init
  try {
    ensureShape();
    render();
    wire();
    setStatus("", "Loaded.");
  } catch (err) {
    console.error("Config UI init failed:", err);
    setStatus("err", err?.message || "Config UI init failed (check console).");
  }
};

// ----------------------------
// Inline open/close (CLEAN)
// ----------------------------
window.openInlineConfig = async function (e, filePath, propertyName) {
  e.preventDefault();

  const wrap = document.getElementById("configPanelWrap");
  const host = document.getElementById("configInlineContainer");
  const label = document.getElementById("configScopeLabel");

  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");

  if (!wrap || !host) return false;

  // ✅ Persist file path for inline mode
  host.dataset.filePath = filePath;

  // ✅ Reset init guard so reopen always wires cleanly
  delete host.__configUIInited;

  if (label) label.textContent = `Editing: ${propertyName || ""}`.trim();

  const res = await fetch(`/admin/config-ui?file=${encodeURIComponent(filePath)}&partial=1`);
  host.innerHTML = res.ok
    ? await res.text()
    : `<div class="p-4 text-rose-700">Failed to load config</div>`;

  // ✅ Init AFTER injection
  window.initConfigUI?.(host);

  wrap.classList.remove("hidden");
  grid?.classList.add("hidden");
  header?.classList.add("hidden");

  wrap.scrollIntoView({ behavior: "smooth", block: "start" });
  return false;
};

window.closeInlineConfig = function () {
  const wrap = document.getElementById("configPanelWrap");
  const host = document.getElementById("configInlineContainer");
  const label = document.getElementById("configScopeLabel");

  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");

  if (host) {
    host.innerHTML = "";
    delete host.__configUIInited;
    delete host.dataset.filePath;
  }

  if (label) label.textContent = "Editing…";

  wrap?.classList.add("hidden");
  grid?.classList.remove("hidden");
  header?.classList.remove("hidden");

  header?.scrollIntoView({ behavior: "smooth", block: "start" });
};

// ----------------------------
// END OF CONFIG PARTIAL (CLEAN)
// ----------------------------

  

// ----------------------------
// API route helper (from bootstrap)
// ----------------------------
const API = (BOOT && BOOT.api) ? BOOT.api : {};

function expandRoute(template, params = {}) {
  if (!template) return "";
  return String(template).replace(/\{(\w+)\}/g, (_, k) =>
    encodeURIComponent(params[k] ?? "")
  );
}

function apiRoute(key, params = {}) {
  const t = API[key];
  if (!t) return ""; // fallback handled by callers
  // support both "/path/{id}" and "/path?x={id}"
  return expandRoute(t, params);
}


// ✅ Expose helpers (Guest Mood / emotional_signals)
window.getMoodForEl = function getMoodForEl(el) {
  if (!el) return [];
  const raw = el.getAttribute("data-emotional-signals") || "[]";
  return window.normalizeEmotionalSignals?.(raw) || [];
};



function applyMoodConfidenceHints(root = document) {
  const nodes = root.querySelectorAll("[data-mood-badge]");
  for (const el of nodes) {
    const conf = parseInt(el.getAttribute("data-guest-mood-confidence") || "0", 10);
    if (!conf) continue;

    // After renderMoodBadges runs, badges are spans
    const badge = el.querySelector("span");
    if (!badge) continue;

    if (conf < 60) {
      badge.classList.add("opacity-70");
      badge.title = `Low confidence mood (${conf}%)`;
    } else {
      badge.classList.remove("opacity-70");
      if (badge.title && badge.title.includes("confidence")) badge.title = "";
    }
  }
}
window.applyMoodConfidenceHints = applyMoodConfidenceHints;




// =====================================================
// SIGNALS (backend-only)
// - Source of truth: data-signals (JSON array or comma string)
// - No sentiment, no derivation
// =====================================================

// =====================================================
// GUEST MOOD (emotional_signals) — backend-derived only
// Source of truth: data-emotional-signals (JSON array or comma string)
// =====================================================
(function GuestMoodOnly() {
  function normalizeEmotionalSignals(signalsRaw) {
    if (Array.isArray(signalsRaw)) {
      return signalsRaw.map(s => String(s || "").toLowerCase().trim()).filter(Boolean);
    }

    let s = String(signalsRaw ?? "").trim();
    if (!s) return [];

    const lower = s.toLowerCase();
    if (lower === "none" || lower === "null" || lower === "undefined") return [];

    // JSON string?
    try {
      const parsed = JSON.parse(s);
      if (Array.isArray(parsed)) {
        return parsed.map(x => String(x || "").toLowerCase().trim()).filter(Boolean);
      }
      if (typeof parsed === "string") s = parsed.trim();
    } catch (_) {}

    // Python-ish list string?
    if (s.startsWith("[") && s.endsWith("]") && s.includes("'")) {
      try {
        const coerced = s.replace(/'/g, '"');
        const parsed2 = JSON.parse(coerced);
        if (Array.isArray(parsed2)) {
          return parsed2.map(x => String(x || "").toLowerCase().trim()).filter(Boolean);
        }
      } catch (_) {}
    }

    // comma-separated fallback
    return s.split(",").map(x => x.trim().toLowerCase()).filter(Boolean);
  }

  // Expose normalizer for other modules
  window.normalizeEmotionalSignals = normalizeEmotionalSignals;

  function hydrateMoodAttr(root = document) {
      root.querySelectorAll("[data-mood-badge]").forEach((el) => {
        const hasSignals = el.hasAttribute("data-emotional-signals");
        if (hasSignals) return;
    
        const gm = (el.getAttribute("data-guest-mood") || "").trim().toLowerCase();
        if (!gm || gm === "null" || gm === "none" || gm === "undefined") return;
    
        // Map guest mood -> emotional_signals list (your renderer expects a list)
        el.setAttribute("data-emotional-signals", JSON.stringify([gm]));
      });
    }
    window.hydrateMoodAttr = hydrateMoodAttr;


  function pill(text, cls) {
    return `<span class="inline-block px-2 py-1 rounded-full font-semibold mr-1 ${cls}">${text}</span>`;
  }

  window.renderMoodBadges = function renderMoodBadges(el, emotionalSignals) {
    if (!el) return;

    const sig = normalizeEmotionalSignals(emotionalSignals);
    let html = "";

    if (sig.includes("panicked")) html += pill("😰 Panicked", "bg-rose-100 text-rose-700");
    if (sig.includes("angry"))    html += pill("😡 Angry",    "bg-rose-200 text-rose-900");
    if (sig.includes("upset"))    html += pill("😟 Upset",    "bg-amber-100 text-amber-800");
    if (sig.includes("confused")) html += pill("😕 Confused", "bg-blue-100 text-blue-700");
    if (sig.includes("worried"))  html += pill("🥺 Worried",  "bg-indigo-100 text-indigo-700");
    if (sig.includes("happy"))    html += pill("😊 Happy",    "bg-yellow-100 text-yellow-800");

    // Calm only if explicitly present
    if (sig.includes("calm")) {
      html += pill("🙂 Calm", "bg-emerald-100 text-emerald-700");
    }

    el.innerHTML = html || `<span class="text-slate-400">—</span>`;
  };

  window.setMoodBadge = function setMoodBadge(container, emotionalSignals) {
    if (!container) return;

    try {
      container.setAttribute("data-emotional-signals", JSON.stringify(emotionalSignals || []));
    } catch {
      container.setAttribute("data-emotional-signals", String(emotionalSignals || ""));
    }

    window.renderMoodBadges(container, emotionalSignals || []);
  };

function rerenderAllMoodBadges(root = document) {
  hydrateMoodAttr(root);

  root.querySelectorAll("[data-mood-badge]").forEach((el) => {
    const raw = el.getAttribute("data-emotional-signals") || "[]";
    let parsed = [];
    try { parsed = JSON.parse(raw); } catch { parsed = raw; }
    window.renderMoodBadges(el, parsed);
  });
}

window.rerenderAllMoodBadges = rerenderAllMoodBadges;

})();





function setInlineDetailOpen(open) {
  const inline = document.getElementById("chat-detail-inline");
  const list = document.getElementById("chat-list-wrap");
  const analytics = document.getElementById("chat-analytics-strip"); // ✅ add
  if (!inline || !list) return;

  inline.classList.toggle("hidden", !open);
  list.classList.toggle("hidden", open);

  // ✅ hide analytics while detail is open
  if (analytics) analytics.classList.toggle("hidden", open);

  if (open) inline.scrollIntoView({ behavior: "smooth", block: "start" });
}



function pushChatUrl(sessionId) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", "chats");
  url.searchParams.set("session_id", String(sessionId));
  url.hash = "#chats";
  history.pushState({ session_id: String(sessionId) }, "", url.toString());
}

function clearChatUrl() {
  const url = new URL(window.location.href);

  // Only clear the selected session; keep user's current view/hash.
  url.searchParams.delete("session_id");

  history.pushState({}, "", url.toString());
}

/*
// Open chat detail from list (delegated)
// Add data-open-chat="123" to clickable elements (or row)
document.addEventListener("click", (e) => {
  const trigger = e.target.closest("[data-open-chat]");
  if (!trigger) return;

  const sid = (trigger.getAttribute("data-open-chat") || "").trim();
  if (!sid) return;

  openChatDetail(sid);
});*/


// Back button
document.addEventListener("click", (e) => {
  if (e.target && e.target.closest("#chat-detail-back")) {
    closeChatDetail();
  }
});

/*// Back/forward support
window.addEventListener("popstate", () => {
  const params = new URLSearchParams(window.location.search);
  const sid = params.get("session_id");
  if (sid) {
    setInlineDetailOpen(true);
    loadChatDetail(sid);
  } else {
    closeChatDetail();
  }
});*/










// =====================================================
// Chat Detail: Notes + Summary collapse (GLOBAL)
// Works even when chat_detail_panel.html is injected.
// - Remembers state per chat in localStorage
// - Default = collapsed (hidden)
// - Chevron rotates
// - Keyboard: N = notes, S = summary (when not typing)
// =====================================================

(function initAdminChatPanelGlobal() {
  if (window.__ADMIN_CHAT_PANEL_GLOBAL_INIT__) return;
  window.__ADMIN_CHAT_PANEL_GLOBAL_INIT__ = true;

  function storageKey(chatId, which) {
    return `admin_chat_${chatId}_${which}_open`;
  }

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || el.isContentEditable;
  }

  function setOpen(chatId, which, open, root = document) {
    const body = root.querySelector(
      `.js-toggle-body[data-toggle-body="${which}"][data-chat-id="${chatId}"]`
    );
    const btn = root.querySelector(
      `.js-toggle[data-toggle-target="${which}"][data-chat-id="${chatId}"]`
    );
    if (!body || !btn) return;

    body.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");

    const chev = btn.querySelector(".js-chevron");
    if (chev) chev.classList.toggle("rotate-180", open);

    try {
      localStorage.setItem(storageKey(chatId, which), open ? "1" : "0");
    } catch {}
  }

  function restore(panelRoot) {
    const panel = panelRoot?.closest?.("[data-chat-panel]") || panelRoot;
    if (!panel) return;
    const chatId = panel.getAttribute("data-chat-panel");
    if (!chatId) return;

    ["note", "summary"].forEach((which) => {
      let open = false; // default CLOSED
      try {
        open = localStorage.getItem(storageKey(chatId, which)) === "1";
      } catch {}
      setOpen(chatId, which, open, panel);
    });
  }

  function formatNowStamp() {
    // lightweight “just now” UX; you can replace with locale string if preferred
    return "Updated: just now";
  }

  // Delegated click handling for toggles + actions
  document.addEventListener("click", async (e) => {
    // toggle open/close
    const toggleBtn = e.target.closest(".js-toggle");
    if (toggleBtn) {
      e.preventDefault();
      const which = toggleBtn.getAttribute("data-toggle-target");
      const chatId = toggleBtn.getAttribute("data-chat-id");
      if (!which || !chatId) return;

      const isOpen = toggleBtn.getAttribute("aria-expanded") === "true";
      const panel = toggleBtn.closest("[data-chat-panel]") || document;
      setOpen(chatId, which, !isOpen, panel);
      return;
    }

   /* const actionBtn = e.target.closest("[data-action]");
    if (!actionBtn) return;

    const panel = actionBtn.closest("[data-chat-panel]");
    const chatId = panel?.getAttribute("data-chat-panel");
    if (!chatId) return;

    const action = actionBtn.getAttribute("data-action");
    if (!action) return;*/

    
  });

  // Keyboard shortcuts: N / S (toggle current open detail panel if present)
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(document.activeElement)) return;

    const k = (e.key || "").toLowerCase();
    if (k !== "n" && k !== "s") return;

    const detailPanelContainer = document.getElementById("chat-detail-panel");
    if (!detailPanelContainer) return;

    // Find the injected panel root
    const firstPanel = detailPanelContainer.querySelector("[data-chat-panel]");
    const chatId = firstPanel?.getAttribute("data-chat-panel");
    if (!chatId) return;

    e.preventDefault();
    const which = k === "n" ? "note" : "summary";

    const toggle = firstPanel.querySelector(
      `.js-toggle[data-toggle-target="${which}"][data-chat-id="${chatId}"]`
    );
    const isOpen = toggle?.getAttribute("aria-expanded") === "true";
    setOpen(chatId, which, !isOpen, firstPanel);
  });

  // expose restore hook so loadChatDetail can call it after injection
  window.restoreAdminChatPanelState = function (container) {
    const root = container || document;
    root.querySelectorAll?.("[data-chat-panel]")?.forEach((p) => restore(p));
  };
})();



    



   async function chatPostJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 || res.status === 403) return loginRedirect();

  const parsed = await safeReadJson(res);
  if (!parsed.ok) {
    console.error("chatPostJSON failed:", parsed.status, parsed.text.slice(0, 500));
    throw new Error(`HTTP ${parsed.status}`);
  }

  if (parsed.json?.ok === false) throw new Error(parsed.json.error || "Request failed");
  return parsed.json;
}

async function chatPostRoute(routeKey, params, body) {
  const url = apiRoute(routeKey, params);
  if (!url) throw new Error(`Missing API route: ${routeKey}`);
  return chatPostJSON(url, body);
}



  function initChatDetailHandlers(sessionId, panelEl) {
  const q = (sel) => panelEl.querySelector(sel);

  // Resolve
  q('[data-role="resolve-btn"]')?.addEventListener("click", async () => {
    await chatPostRoute("chat_resolve", { session_id: sessionId }, {});
    updateChatListRow(sessionId, { is_resolved: true });
    await loadChatDetail(sessionId);
  });

  // Unresolve
  q('[data-role="unresolve-btn"]')?.addEventListener("click", async () => {
    await chatPostRoute("chat_unresolve", { session_id: sessionId }, {});
    updateChatListRow(sessionId, { is_resolved: false });
    await loadChatDetail(sessionId);
  });

  // Escalation
  q('[data-role="escalation-select"]')?.addEventListener("change", async (e) => {
    const escalation_level = e.target.value || "";
    //await chatPostRoute("chat_escalate", { session_id: sessionId }, { escalation_level });
    await chatPostRoute("chat_escalate", { session_id: sessionId }, { level: escalation_level });
    updateChatListRow(sessionId, { escalation_level: escalation_level || null });
    await loadChatDetail(sessionId);
  });

  // Assign
  q('[data-role="assign-btn"]')?.addEventListener("click", async () => {
    const assigned_to = (q('[data-role="assigned-input"]')?.value || "").trim();
    await chatPostRoute("chat_assign", { session_id: sessionId }, { assigned_to });
    updateChatListRow(sessionId, { assigned_to });
    await loadChatDetail(sessionId);
  });

  // Save note
  q('[data-role="save-note-btn"]')?.addEventListener("click", async () => {
    const note = q('[data-role="note-input"]')?.value || "";
    await chatPostRoute("chat_note", { session_id: sessionId }, { note });

    const s = q('[data-role="note-status"]');
    if (s) {
      s.textContent = "Saved ✅";
      setTimeout(() => (s.textContent = ""), 1200);
    }
  });

  // Summary is handled globally by window.Chats.refreshSummary click binding
}



let chatDetailAbort = null;

async function loadChatDetail(sessionId) {
  const panel = document.getElementById("chat-detail-panel");
  if (!panel) return;

  if (chatDetailAbort) chatDetailAbort.abort();
  chatDetailAbort = new AbortController();

  panel.innerHTML = `<div class="text-sm text-slate-500">Loading…</div>`;

  try {
    const url =
    apiRoute("chat_detail_partial", { session_id: sessionId }) ||
    `/admin/chats/partial/detail?session_id=${encodeURIComponent(sessionId)}`;
  
  const res = await fetch(url, {
    credentials: "include",
    headers: { "X-Requested-With": "fetch" },
    signal: chatDetailAbort.signal,
  });


    if (res.status === 401 || res.status === 403) {
      loginRedirect();
      return;
    }

    if (!res.ok) {
      panel.innerHTML = `<div class="text-sm text-rose-700">Could not load chat.</div>`;
      return;
    }

    // Inject HTML
    panel.innerHTML = await res.text();
    panel.setAttribute("data-session-id", String(sessionId));

    // Find injected chatRoot (panel wrapper)
    const chatRoot =
      panel.querySelector(`[data-chat-panel="${sessionId}"]`) ||
      panel.querySelector("[data-chat-panel]");

    // ✅ Restore per-chat note/summary open/closed state (localStorage)
    window.restoreAdminChatPanelState?.(panel);


    // --- Guest Mood: hydrate detail mood from list row (source of truth) ---
    const listMoodEl = document.querySelector(
      `[data-session-row="${sessionId}"] [data-mood-badge]`
    );
    const listRawMood =
      (listMoodEl?.getAttribute("data-emotional-signals") || "").trim() ||
      JSON.stringify([ (listMoodEl?.getAttribute("data-guest-mood") || "").trim().toLowerCase() ].filter(Boolean));

    
    // Detail badge element (inside injected partial)
    const detailMoodEl = panel.querySelector("[data-mood-badge]");
    
    const isEmptyAttr = (raw) => {
      const s = String(raw || "").trim();
      if (!s) return true;
      const lower = s.toLowerCase();
      if (lower === "null" || lower === "none" || lower === "undefined") return true;
      if (s === "[]") return true;
      return false;
    };
    
    // If detail badge exists but has empty/missing mood, copy from list
    if (detailMoodEl && listRawMood) {
      const current = detailMoodEl.getAttribute("data-emotional-signals");
      if (isEmptyAttr(current)) {
        detailMoodEl.setAttribute("data-emotional-signals", listRawMood);
      }
    }
    
    // Also ensure chatRoot carries mood if empty (helps other logic)
    if (chatRoot && listRawMood) {
      const rootMood = chatRoot.getAttribute("data-emotional-signals");
      if (isEmptyAttr(rootMood)) {
        chatRoot.setAttribute("data-emotional-signals", listRawMood);
      }
    }
    
    // Render mood badges after hydration
    window.rerenderAllMoodBadges?.(panel);
    window.applyMoodConfidenceHints?.(panel);



    // Bind buttons etc
    initChatDetailHandlers(sessionId, panel);

    // --- Sync row state back into the table (DO NOT sync signals from detail) ---
    if (chatRoot && typeof updateChatListRow === "function") {
      const esc = (chatRoot.getAttribute("data-escalation-level") || "").trim();
      const assigned = (chatRoot.getAttribute("data-assigned-to") || "").trim();
      const isResolved = (chatRoot.getAttribute("data-is-resolved") || "0") === "1";

      updateChatListRow(sessionId, {
        escalation_level: esc || null,
        is_resolved: isResolved,
        assigned_to: assigned || null,
        // 🚫 intentionally NOT syncing signals from detail
      });
    }
  } catch (err) {
    if (err?.name === "AbortError") return;
    console.error("loadChatDetail error:", err);
    panel.innerHTML = `<div class="text-sm text-rose-700">Could not load chat.</div>`;
  }
}



function setEscalationBadge(el, levelRaw) {
  if (!el) return;

  const level = String(levelRaw || "").toLowerCase().trim();

  // If el is a container (td/div), render a pill span inside it.
  // If el is already a span (the pill itself), update it directly.
  const isSpan = el.tagName && el.tagName.toLowerCase() === "span";
  const target = isSpan ? el : (el.querySelector("span") || null);

  const pillClass = "px-2 py-1 rounded-full font-semibold";

  const cfg =
    (level === "critical" || level === "high")
      ? { text: "🔴 High", cls: `${pillClass} bg-rose-100 text-rose-800` }
    : (level === "attention" || level === "medium")
      ? { text: "🟡 Medium", cls: `${pillClass} bg-amber-100 text-amber-800` }
    : (level === "low")
      ? { text: "🟢 Low", cls: `${pillClass} bg-blue-100 text-blue-800` }
    : { text: "—", cls: `${pillClass} text-slate-400` };

  if (target) {
    // Update existing span
    target.className = cfg.cls;
    target.textContent = cfg.text;
  } else {
    // Create span in container
    el.innerHTML = `<span class="${cfg.cls}">${cfg.text}</span>`;
  }

  el.setAttribute("data-escalation-level", level);
}


function setStatusBadge(container, isResolved) {
  if (!container) return;
  if (isResolved) {
    container.innerHTML = `<span class="px-2 py-1 rounded-full bg-slate-200 text-slate-700 font-semibold">✅ Closed</span>`;
  } else {
    container.innerHTML = `<span class="px-2 py-1 rounded-full bg-emerald-100 text-emerald-700 font-semibold">🟢 Open</span>`;
  }
}

function setAssignedBadge(container, assignedTo) {
  if (!container) return;
  const v = String(assignedTo || "").trim();
  if (!v) {
    container.innerHTML = `<span class="text-slate-400">—</span>`;
    return;
  }
  container.innerHTML = `<span class="inline-block px-2 py-1 rounded-xl bg-slate-100 text-slate-800 font-semibold">${escapeHtml(v)}</span>`;
}


// small safety helper
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[m]));
}









    
/**
 * Update the row in the main chat list WITHOUT hard refresh.
 *
 * payload supports:
 *  - escalation_level: "low" | "medium" | "high" | "" | null
 *  - is_resolved: boolean
 *  - assigned_to: string | null
 */
function updateChatListRow(sessionId, payload = {}) {
  const row = document.querySelector(`[data-session-row="${sessionId}"]`);
  if (!row) return;

  if (Object.prototype.hasOwnProperty.call(payload, "escalation_level")) {
    const escEl = row.querySelector("[data-escalation-badge]");
    setEscalationBadge(escEl, payload.escalation_level);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "is_resolved")) {
    const stEl = row.querySelector("[data-status-badge]");
    setStatusBadge(stEl, payload.is_resolved);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "assigned_to")) {
    const asgEl = row.querySelector("[data-assigned-badge]");
    setAssignedBadge(asgEl, payload.assigned_to);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "emotional_signals")) {
    const moodEl = row.querySelector("[data-mood-badge]");
    window.setMoodBadge?.(moodEl, payload.emotional_signals || []);
  }
}


/*
function applyEscalationBadge(badgeEl, levelRaw) {
  if (!badgeEl) return;

  const level = String(levelRaw || "").toLowerCase().trim();

  // Reset classes (keep your pill shape)
  badgeEl.className = "px-2 py-1 rounded-full font-semibold";

  if (level === "critical" || level === "high") {
    badgeEl.textContent = "🔴 High";
    badgeEl.classList.add("bg-rose-100", "text-rose-800");
  } else if (level === "attention" || level === "medium") {
    badgeEl.textContent = "🟡 Medium";
    badgeEl.classList.add("bg-amber-100", "text-amber-800");
  } else if (level) {
    badgeEl.textContent = "🟢 Low";
    badgeEl.classList.add("bg-blue-100", "text-blue-800");
  } else {
    badgeEl.textContent = "—";
    badgeEl.classList.add("text-slate-400");
  }

  badgeEl.setAttribute("data-escalation-level", level);
}*/

function updateChatListEscalation(sessionId, level) {
  const row = document.querySelector(`[data-session-row="${sessionId}"]`);
  if (!row) return;
  setEscalationBadge(row.querySelector("[data-escalation-badge]"), level);
}



    

   async function openChatDetail(sessionId) {
  setInlineDetailOpen(true);
  pushChatUrl(sessionId);
  await loadChatDetail(String(sessionId));
}

function closeChatDetail() {
  setInlineDetailOpen(false);
  clearChatUrl();
  const panel = document.getElementById("chat-detail-panel");
  if (panel) panel.innerHTML = "";
}

window.openChatDetail = openChatDetail;

    // ----------------------------
    // Relative time updater
    // ----------------------------
    function parseTimestamp(ts) {
      if (!ts) return null;
      const normalized = ts.includes("T") ? ts : ts.replace(" ", "T");
      const d = new Date(normalized);
      return isNaN(d.getTime()) ? null : d;
    }

    function formatRelative(fromDate, now = new Date()) {
      const diffMs = now - fromDate;
      if (diffMs < 0) return "just now";
      const sec = Math.floor(diffMs / 1000);
      if (sec < 10) return "just now";
      if (sec < 60) return `${sec}s ago`;
      const min = Math.floor(sec / 60);
      if (min < 60) return `${min}m ago`;
      const hr = Math.floor(min / 60);
      if (hr < 24) return `${hr}h ago`;
      const day = Math.floor(hr / 24);
      if (day < 14) return `${day}d ago`;
      const wk = Math.floor(day / 7);
      if (wk < 8) return `${wk}w ago`;
      const mo = Math.floor(day / 30);
      if (mo < 12) return `${mo}mo ago`;
      const yr = Math.floor(day / 365);
      return `${yr}y ago`;
    }

    function updateRelativeTimes() {
      const now = new Date();
      document.querySelectorAll(".js-rel-time").forEach(el => {
        const ts = el.getAttribute("data-ts");
        const d = parseTimestamp(ts);
        if (!d) return;
        el.textContent = formatRelative(d, now);
      });
    }

    document.addEventListener("DOMContentLoaded", () => {
  updateRelativeTimes();
  setInterval(updateRelativeTimes, 60 * 1000);
});


///----- END OF SECTION


  // -------- Paywall flag (server-rendered) --------
//const IS_LOCKED = {{ (user_role == 'pmc' and needs_payment) | tojson }};
//const CONTENT_LOCKED = IS_LOCKED;


 // let chatAnalyticsChart = null;

// ----------------------------
// Analytics (single definition)
// ----------------------------
window.chatAnalyticsChart = window.chatAnalyticsChart || null;

function rangeToUnixMs(days) {
  const to = Date.now(); // ms
  const from = to - (Number(days) * 24 * 60 * 60 * 1000);
  return { from: Math.floor(from), to: Math.floor(to) };
  //return { from: Math.floor(from / 1000), to: Math.floor(to / 1000) };

}




function fmtPct(x) {
  const n = Number(x);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n * 1000) / 10}%`;
}

function getAnalyticsFilters() {
  const days = document.getElementById("analyticsRange")?.value || 30;
  const propertyId = document.getElementById("analyticsPropertyFilter")?.value || "";
  const pmcId = document.getElementById("analyticsPmcFilter")?.value || ""; // super only
  return { days, propertyId, pmcId };
}

function buildAnalyticsQS({ from, to, propertyId, pmcId }) {
  const qs = new URLSearchParams({ from: String(from), to: String(to) });
  if (propertyId) qs.set("property_id", String(propertyId));
  if (pmcId) qs.set("pmc_id", String(pmcId));
  return qs.toString();
}

async function loadTopProperties({ from, to, pmcId, propertyId }) {
  const tbody = document.getElementById("analyticsTopPropsBody");
  if (!tbody) return;

  const qs = new URLSearchParams({ from: String(from), to: String(to), limit: "10" });
  if (pmcId) qs.set("pmc_id", String(pmcId));
  if (propertyId) qs.set("property_id", String(propertyId));

  tbody.innerHTML = `<tr><td class="px-4 py-4 text-slate-500" colspan="6">Loading…</td></tr>`;

  let res;
  try {
    res = await fetch(`/admin/analytics/chat/top-properties?${qs.toString()}`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch (e) {
    console.error("top-properties fetch failed:", e);
    tbody.innerHTML = `<tr><td class="px-4 py-4 text-rose-600" colspan="6">Network error loading Top Properties.</td></tr>`;
    return;
  }

  if (res.status === 401 || res.status === 403) return loginRedirect();

  // ✅ handle 500/other non-OK cleanly
  if (!res.ok) {
    const preview = await res.text().catch(() => "");
    console.error("top-properties HTTP error:", res.status, preview.slice(0, 500));
    tbody.innerHTML = `<tr><td class="px-4 py-4 text-rose-600" colspan="6">Top Properties failed to load (HTTP ${res.status}).</td></tr>`;
    return;
  }

    const parsed = await safeReadJson(res);
  if (!parsed.ok) {
    console.error("top-properties failed:", parsed.status, parsed.text.slice(0, 500));
    tbody.innerHTML = `<tr><td class="px-4 py-4 text-rose-600" colspan="6">Top Properties failed to load (HTTP ${parsed.status}).</td></tr>`;
    return;
  }

  const rows = parsed.json?.rows || [];


  if (!rows.length) {
    tbody.innerHTML = `<tr><td class="px-4 py-4 text-slate-500" colspan="6">No data yet.</td></tr>`;
    return;
  }

  const selectedPid = (propertyId || "").trim();

  tbody.innerHTML = rows.map((r) => {
    const pid = String(r.property_id ?? "");
    const isSelected = selectedPid && pid === selectedPid;

    return `
      <tr class="border-t ${isSelected ? "bg-slate-50" : ""}">
        <td class="px-4 py-3 font-medium">${r.property_name ? r.property_name : `#${pid}`}</td>
        <td class="px-4 py-3">${r.sessions ?? 0}</td>
        <td class="px-4 py-3">${r.messages ?? 0}</td>
        <td class="px-4 py-3">${fmtPct(r.followup_conversion_rate)}</td>
        <td class="px-4 py-3">${r.chat_errors ?? 0}</td>
        <td class="px-4 py-3">${r.contact_host_clicks ?? 0}</td>
      </tr>
    `;
  }).join("");
}

   

async function loadChatAnalytics(daysOverride = null) {
  try {
    const { days, propertyId, pmcId } = getAnalyticsFilters();
    const daysToUse = daysOverride != null ? daysOverride : days;

    const { from, to } = rangeToUnixMs(daysToUse);
    const qs = buildAnalyticsQS({ from, to, propertyId, pmcId });

    // Summary KPIs
    // Summary KPIs (robust)
    const summaryRes = await fetch(`/admin/analytics/chat/summary?${qs}`, {
  credentials: "include",
  headers: { Accept: "application/json" },
    });
    if (summaryRes.status === 401 || summaryRes.status === 403) return loginRedirect();
    
    const summaryParsed = await safeReadJson(summaryRes);
    if (!summaryParsed.ok) {
      console.error("summary failed:", summaryParsed.status, summaryParsed.text.slice(0, 500));
    }
    const summary = summaryParsed.json || {};


    document.querySelectorAll("[data-kpi]").forEach((el) => {
      const k = el.getAttribute("data-kpi");
      let val = summary?.[k];

      if (k === "response_rate") val = fmtPct(val);

      el.textContent = (val != null && val !== "") ? String(val) : "—";

    });

    // Timeseries
// Timeseries
const tsRes = await fetch(`/admin/analytics/chat/timeseries?bucket=day&${qs}`, {
  credentials: "include",
  headers: { Accept: "application/json" },
});
if (tsRes.status === 401 || tsRes.status === 403) return loginRedirect();

const tsParsed = await safeReadJson(tsRes);
if (!tsParsed.ok) {
  console.error("timeseries failed:", tsParsed.status, tsParsed.text.slice(0, 500));
}
const ts = tsParsed.json || { labels: [], series: {} };


const canvas = document.getElementById("chatAnalyticsChart");
if (!canvas || typeof canvas.getContext !== "function" || !window.Chart) {
  console.warn("Chart canvas or Chart.js missing; skipping render.");
} else {
  const ctx = canvas.getContext("2d");

  const labels = Array.isArray(ts.labels) ? ts.labels : [];
  const series = ts.series && typeof ts.series === "object" ? ts.series : {};

  const sessions = Array.isArray(series.sessions) ? series.sessions : [];
  const messages = Array.isArray(series.messages) ? series.messages : [];
  const followupClicks = Array.isArray(series.followup_clicks) ? series.followup_clicks : [];
  const chatErrors = Array.isArray(series.chat_errors) ? series.chat_errors : [];

  const datasets = [
    { label: "Sessions", data: sessions },
    { label: "Messages", data: messages },
    { label: "Followup clicks", data: followupClicks },
    { label: "Errors", data: chatErrors },
  ];

  const existing = window.chatAnalyticsChart;

  // Valid Chart.js instance check
  const isValidChart =
    existing &&
    typeof existing.update === "function" &&
    existing.data &&
    typeof existing.data === "object" &&
    Array.isArray(existing.data.datasets);

  // If something non-chart got assigned, clean it up
  if (existing && !isValidChart) {
    try { existing.destroy?.(); } catch {}
    window.chatAnalyticsChart = null;
  }

  if (!window.chatAnalyticsChart) {
    window.chatAnalyticsChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  } else {
    // Ensure dataset slots exist (in case something mutated the chart)
    window.chatAnalyticsChart.data.labels = labels;
    window.chatAnalyticsChart.data.datasets = datasets;
    window.chatAnalyticsChart.update();
  }
}



    await loadTopProperties({ from, to, pmcId, propertyId });
  } catch (err) {
    console.error("loadChatAnalytics failed:", err);
    toast("Analytics failed to load (check console).");
  }
}

function resizeChatAnalyticsChartSoon() {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (window.chatAnalyticsChart && typeof window.chatAnalyticsChart.resize === "function") {
        window.chatAnalyticsChart.resize();
      }
    });
  });
}

   
// Debounced filter reload ONLY when Analytics view is visible
let analyticsDebounce = null;
function isAnalyticsVisible() {
  const el = document.getElementById("view-analytics");
  return el && !el.classList.contains("hidden");
}

document.addEventListener("change", (e) => {
  const t = e.target;
  if (!t || !(t instanceof HTMLElement)) return;

  const isAnalyticsControl =
    t.id === "analyticsRange" ||
    t.id === "analyticsPropertyFilter" ||
    t.id === "analyticsPmcFilter";

  if (!isAnalyticsControl) return;
  if (!isAnalyticsVisible()) return;

  const days = document.getElementById("analyticsRange")?.value || 30;

  clearTimeout(analyticsDebounce);
  analyticsDebounce = setTimeout(() => {
    loadChatAnalytics(days);
    resizeChatAnalyticsChartSoon();
  }, 150);
});


// Prevent row click from firing when interacting with controls
document.addEventListener("click", (e) => {
  const row = e.target.closest("[data-session-row]");
  if (!row) return;

  // If the click originated from an interactive element, do nothing.
  if (e.target.closest("a, button, input, textarea, select, label")) return;

  const sid = row.getAttribute("data-session-row");
  if (!sid) return;

  // Avoid double-open if you still have inline onclick on the <tr>
  if (row.__opening) return;
  row.__opening = true;

  Promise.resolve(openChatDetail(sid)).finally(() => {
    row.__opening = false;
  });
});


  // ----------------------------
  // Small UI helpers
  // ----------------------------
  function toast(message) {
    const el = document.createElement("div");
    el.className =
      "fixed bottom-5 right-5 z-[9999] bg-slate-900 text-white text-sm px-4 py-3 rounded-xl shadow-lg";
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2400);
  }

  function loginRedirect() {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search + window.location.hash
    );
    window.location.href = `/auth/login/google?next=${next}`;
  }

async function safeReadJson(res) {
  const status = res?.status;

  try {
    const text = await res.text();

    if (!text) return { ok: true, json: null, text: "", status };

    try {
      return { ok: true, json: JSON.parse(text), text, status };
    } catch {
      return { ok: false, json: null, text, status, error: "Response was not valid JSON" };
    }
  } catch (e) {
    return { ok: false, json: null, text: "", status, error: e?.message || String(e) };
  }
}





  async function apiJson(url, opts = {}) {
  const res = await fetch(url, {
    credentials: "include",
    headers: { Accept: "application/json", ...(opts.headers || {}) },
    ...opts,
  });

  if (res.status === 401 || res.status === 403) {
    toast("Session expired. Please sign in again.");
    loginRedirect();
    throw new Error("auth");
  }

  const text = await res.text().catch(() => "");
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

  return { res, data };
}

  async function postJson(url) {
    return fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  }

  // ----------------------------
  // Overview counters + chart refresh (no hard refresh)
  // NOTE: make sure your HTML has:
  //   <div id="stat-total">...</div>
  //   <div id="stat-live">...</div>
  //   <div id="stat-offline">...</div>
  // ----------------------------
  let statusChartInstance = null;

  function recomputeLiveOffline() {
    const cards = document.querySelectorAll("[data-property-card]");
    const total = cards.length;
    let live = 0;
    cards.forEach((c) => {
      if (c.dataset.live === "true") live += 1;
    });
    return { total, live, offline: total - live };
  }

  function updateOverviewUI() {
    const { total, live, offline } = recomputeLiveOffline();

    const totalEl = document.getElementById("stat-total");
    const liveEl = document.getElementById("stat-live");
    const offEl = document.getElementById("stat-offline");

    if (totalEl) totalEl.textContent = String(total);
    if (liveEl) liveEl.textContent = String(live);
    if (offEl) offEl.textContent = String(offline);

    if (statusChartInstance) {
      statusChartInstance.data.datasets[0].data = [live, offline];
      statusChartInstance.update();
    }
  }


// ----------------------------
  // Properties Signal
  // ----------------------------

function getSelectedMoodFilter() {
  const sel = document.getElementById("moodFilter");
  return sel ? String(sel.value || "").toLowerCase().trim() : "";
}

function getRowMood(row) {
  const rowRaw = row.getAttribute("data-emotional-signals");
  const badgeRaw =
    row.querySelector("[data-mood-badge]")?.getAttribute("data-emotional-signals");

  const raw = rowRaw ?? badgeRaw ?? "[]";

  // Single source of truth
  return window.normalizeEmotionalSignals?.(raw) || [];
}


/*
function applyChatsFilters() {
  const selected = getSelectedMoodFilter();
  const rows = document.querySelectorAll("[data-session-row]");

  rows.forEach(row => {
    if (!selected) {
      row.style.display = "";
      return;
    }
    const mood = getRowMood(row);
    row.style.display = mood.includes(selected) ? "" : "none";
  });
}

// Optional: instant filtering on change
document.addEventListener("change", (e) => {
  if (e.target?.id === "moodFilter") applyChatsFilters();
});*/


  // ----------------------------
  // Sidebar collapse/expand
  // ----------------------------
  function initSidebar() {
    const sidebar = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("sidebar-toggle");
    const labelEls = () =>
      Array.from(document.querySelectorAll(".sidebar-label"));

    if (!sidebar || !toggleBtn) return;

    function setCollapsed(isCollapsed) {
      sidebar.classList.toggle("w-72", !isCollapsed);
      sidebar.classList.toggle("w-20", isCollapsed);
      labelEls().forEach((el) => el.classList.toggle("hidden", isCollapsed));

      const svg = toggleBtn.querySelector("svg");
      if (svg) {
        svg.style.transform = isCollapsed ? "rotate(180deg)" : "rotate(0deg)";
        svg.style.transition = "transform 200ms ease";
      }
      localStorage.setItem(
        "dashboard_sidebar_collapsed",
        isCollapsed ? "1" : "0"
      );
    }

    setCollapsed(localStorage.getItem("dashboard_sidebar_collapsed") === "1");

    toggleBtn.addEventListener("click", () => {
      const isCollapsed = sidebar.classList.contains("w-20");
      setCollapsed(!isCollapsed);
    });

    // ⌘K focus
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("globalSearch")?.focus();
      }
    });
  }

  // ----------------------------
  // Properties filter
  // ----------------------------
  function filterProperties() {
    const search = (
      document.getElementById("searchInput")?.value || ""
    ).toLowerCase();
    const status = document.getElementById("statusFilter")?.value || "all";
    const cards = document.querySelectorAll("[data-property-card]");

    cards.forEach((card) => {
      const name = (card.dataset.name || "").toLowerCase();
      const live = card.dataset.live === "true";
      const matchesSearch = name.includes(search);
      const matchesStatus =
        status === "all" ||
        (status === "live" && live) ||
        (status === "offline" && !live);

      card.style.display = matchesSearch && matchesStatus ? "" : "none";
    });
  }
  window.filterProperties = filterProperties;

 

  // ----------------------------
  // Property actions
  // ----------------------------
  window.syncProperty = async function (id, btn) {
    if (IS_LOCKED) return toast("Complete payment to unlock property syncing.");

    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = "Syncing…";

    try {
      const res = await postJson(`/auth/sync-property/${id}`);

      if (res.status === 401 || res.status === 403) return loginRedirect();
      if (res.status === 402) return (window.location.href = "/pmc/signup");

      let data = {};
      try {
        data = await res.json();
      } catch {}
      btn.innerHTML =
        res.ok && data.status === "success"
          ? "Synced ✓"
          : data.message || data.detail || "Failed";
    } catch (err) {
      console.error("Sync error:", err);
      btn.innerHTML = "Error";
    } finally {
      setTimeout(() => {
        btn.innerHTML = original;
        btn.disabled = false;
      }, 1200);
    }
  };

  window.toggleProperty = async function (id, btn) {
    if (IS_LOCKED) return toast("Complete payment to unlock Sandy activation.");

    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = "Toggling…";

    try {
      const res = await postJson(`/auth/toggle-property/${id}`);
      if (res.status === 401 || res.status === 403) return loginRedirect();

      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }

      if (data && data.status === "needs_billing" && data.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      if (!res.ok) {
        toast((data && (data.detail || data.message)) || "Request failed");
        btn.innerHTML = original;
        return;
      }

      if (data && data.status === "success") {
        const isLive = data.new_status === "LIVE";
        btn.innerHTML = isLive ? "Take Offline" : "Go Live";

        const card = btn.closest("[data-property-card]");
        if (card) {
          card.dataset.live = isLive ? "true" : "false";

          const pill = card.querySelector("span.rounded-full");
          if (pill) {
            if (isLive) {
              pill.textContent = "LIVE";
              pill.className =
                "shrink-0 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 font-semibold";
            } else {
              pill.textContent = "OFFLINE";
              pill.className =
                "shrink-0 text-xs px-2.5 py-1 rounded-full bg-rose-50 text-rose-700 border border-rose-100 font-semibold";
            }
          }
        }

        filterProperties();
        updateOverviewUI(); // ✅ update overview immediately
        return;
      }

      btn.innerHTML = original;
      toast("Toggle failed.");
    } catch (err) {
      console.error("Toggle error:", err);
      btn.innerHTML = original;
      toast("Network error. Please try again.");
    } finally {
      btn.disabled = false;
    }
  };


   document.addEventListener("click", (e) => {
  const editBtn = e.target.closest("[data-guide-edit]");
  if (editBtn) {
    const id = (editBtn.getAttribute("data-guide-edit") || "").trim();
    if (id) Guides.openEdit(id);
    return;
  }

  const delBtn = e.target.closest("[data-guide-delete]");
  if (delBtn) {
    const id = (delBtn.getAttribute("data-guide-delete") || "").trim();
    if (id) Guides.remove(id);
    return;
  }
});

  

   document.addEventListener("change", async (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement)) return;
  if (!el.matches("[data-guide-active]")) return;

  if (window.CONTENT_LOCKED) {
    toast("Complete payment to unlock Guides.");
    el.checked = !el.checked;
    return;
  }

  const id = el.dataset.guideId;
  const checked = el.checked;

  const row = el.closest("[data-guide-row]");
  const label = row?.querySelector("[data-guide-status-label]");

  if (label) {
    label.textContent = checked ? "Active" : "Inactive";
    label.className =
      "text-xs font-semibold " + (checked ? "text-emerald-700" : "text-slate-400");
  }

  try {
    const { res, data } = await apiJson("/admin/guides/ajax/toggle-active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, is_active: checked }),
    });

    if (!res.ok || !data.ok) {
      toast(data.error || "Failed to update.");
      el.checked = !checked;

      if (label) {
        const reverted = el.checked;
        label.textContent = reverted ? "Active" : "Inactive";
        label.className =
          "text-xs font-semibold " + (reverted ? "text-emerald-700" : "text-slate-400");
      }
    }
  } catch (err) {
    toast("Failed to update.");
    el.checked = !checked;

    if (label) {
      const reverted = el.checked;
      label.textContent = reverted ? "Active" : "Inactive";
      label.className =
        "text-xs font-semibold " + (reverted ? "text-emerald-700" : "text-slate-400");
    }
  }
});

window.Chats = window.Chats || {};

window.Chats.refreshSummary = async function refreshSummary(sessionId) {
  const panel =
    document.querySelector(`[data-chat-panel="${sessionId}"]`) ||
    document.querySelector("[data-chat-panel]");
  if (!panel) return;

  const box = panel.querySelector("[data-summary-box]");
  if (!box) return;

  const updatedLabel = panel.querySelector("[data-summary-updated]");

  try {
    box.textContent = "Generating…";

    const url =
      (typeof apiRoute === "function" && apiRoute("chat_summarize", { session_id: sessionId })) ||
      `/admin/chats/${sessionId}/summarize`;

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: "{}",
    });

    if (res.status === 401 || res.status === 403) {
      if (typeof loginRedirect === "function") return loginRedirect();
      throw new Error("Not authenticated");
    }

    const parsed = await safeReadJson(res);
    if (!res.ok) {
      const msg =
        parsed?.json?.error ||
        parsed?.json?.detail ||
        parsed?.error ||
        `Request failed (HTTP ${res.status})`;
      throw new Error(msg);
    }

    const data = parsed?.json || {};
    if (data.ok === false) throw new Error(data.error || "Summarize failed");

    box.textContent = data.summary || "";
    if (updatedLabel) {
      updatedLabel.textContent = data.updated_at
        ? `Updated: ${data.updated_at}`
        : "Updated: just now";
    }
  } catch (e) {
    box.textContent = `Summary error: ${e?.message || e}`;
    console.error(e);
  }
};

// ✅ global alias so any old code calling refreshSummary(...) still works
window.refreshSummary = (sessionId) => window.Chats.refreshSummary(sessionId);

/*
// ✅ click binding for your button
document.addEventListener("click", (e) => {
  const btn = e.target.closest('[data-action="summary"]');
  if (!btn) return;

  const panel = btn.closest("[data-chat-panel]");
  const sessionId = Number(panel?.dataset?.sessionId);
  if (!sessionId) return console.error("Missing sessionId on panel", panel);

  window.Chats.refreshSummary(sessionId);
});
*/









   function initQuillEditor({ editorId, inputId, placeholder = "" }) {
  const editorEl = document.getElementById(editorId);
  const inputEl = document.getElementById(inputId);

  if (!editorEl || !inputEl) {
    console.warn("Quill init skipped: missing elements", { editorId, inputId });
    return null;
  }
  if (!window.Quill) {
    console.warn("Quill init skipped: Quill library not loaded");
    return null;
  }

  // Prevent double-init if you reopen editor
  if (editorEl.__quill) return editorEl.__quill;

  const quill = new Quill(editorEl, {
    theme: "snow",
    placeholder,
    modules: {
      toolbar: [
        ["bold", "italic", "underline", "strike"],
        [{ header: [1, 2, 3, false] }],
        [{ list: "ordered" }, { list: "bullet" }],
        ["link", "blockquote"],
        ["clean"]
      ],
    },
  });

  // seed initial content from hidden input
  quill.root.innerHTML = inputEl.value || "";

  // keep hidden input updated for form submit
  quill.on("text-change", () => {
    inputEl.value = quill.root.innerHTML;
  });

  editorEl.__quill = quill;
  return quill;
}


// ----------------------------
// Upgrades (single definition + resilient AJAX handling)
// ----------------------------
window.Upgrades = {
  loaded: false,

async refresh() {
  const listEl = document.getElementById("upgrades-list");
  if (!listEl) return;

  const pid = document.getElementById("upgradesPropertyFilter")?.value || "";
  const qs = pid ? `?property_id=${encodeURIComponent(pid)}` : "";

  listEl.innerHTML = "Loading…";

  try {
    const res = await fetch(`/admin/upgrades/partial/list${qs}`, {
      credentials: "include",
      headers: { "X-Requested-With": "fetch" },
    });

    if (res.status === 401 || res.status === 403) return loginRedirect();
    if (!res.ok) {
      listEl.innerHTML = "Could not load upgrades.";
      return;
    }

    const html = await res.text();
    listEl.innerHTML = html;

    // ✅ Always rebind after injection (don’t rely on local query only)
    initAllReorderTables();

    console.log("Upgrades list refreshed; reorder bound:", !!listEl.querySelector("table[data-list-kind]"));
  } catch (err) {
    console.error("Upgrades.refresh error:", err);
    listEl.innerHTML = "Could not load upgrades.";
  }
},

  openNew() {
    return window.Upgrades.openEditor(null);
  },

async openEditor(id) {
  if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Upgrades.");

  const editorWrap = document.getElementById("upgrades-editor");
  const editorBody = document.getElementById("upgrades-editor-body");
  const editorTitle = document.getElementById("upgrades-editor-title");
  if (!editorWrap || !editorBody) return;

  const url = id
    ? `/admin/upgrades/partial/form?id=${encodeURIComponent(id)}`
    : `/admin/upgrades/partial/form`;

  // Show modal immediately (better UX, and ensures DOM exists)
  if (editorTitle) editorTitle.textContent = id ? "Edit Upgrade" : "New Upgrade";
  editorWrap.classList.remove("hidden");
  editorBody.innerHTML = `<div class="text-sm text-slate-500">Loading…</div>`;

  try {
    const res = await fetch(url, { credentials: "include" });

    if (res.status === 401 || res.status === 403) return loginRedirect();
    if (!res.ok) {
      editorBody.innerHTML = `<div class="text-sm text-rose-700">Could not load upgrade editor.</div>`;
      return;
    }

    editorBody.innerHTML = await res.text();

    // ✅ Don’t let editor init break the modal
    try {
      if (typeof initQuillEditor === "function") {
        initQuillEditor({
          editorId: "upgrade-longdesc-editor",
          inputId: "upgrade-longdesc-input",
          placeholder: "Shown in the upgrade details / guest UI...",
        });
      } else {
        console.warn("initQuillEditor is not defined (Quill init skipped).");
      }
    } catch (e) {
      console.error("Quill init failed (skipped):", e);
    }
  } catch (err) {
    console.error("Fetch failed:", err);
    editorBody.innerHTML = `<div class="text-sm text-rose-700">Network error loading editor.</div>`;
  }
},


  closeEditor() {
    document.getElementById("upgrades-editor")?.classList.add("hidden");
    const body = document.getElementById("upgrades-editor-body");
    if (body) body.innerHTML = "";
  },

  async submit(form) {
    if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Upgrades.");
    if (!form) return;

    const flash = document.getElementById("upgrades-flash");

    const showFlash = (msg, ok = true) => {
      if (!flash) return toast(msg);

      flash.innerHTML = `
        <div class="rounded-xl border ${
          ok
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-rose-200 bg-rose-50 text-rose-900"
        } p-3 text-sm">
          ${msg}
        </div>`;

      setTimeout(() => {
        if (flash) flash.innerHTML = "";
      }, 2400);
    };

    try {
      const res = await fetch("/admin/upgrades/ajax/save", {
        method: "POST",
        credentials: "include",
        body: new FormData(form),
      });

      if (res.status === 401 || res.status === 403) return loginRedirect();

      const contentType = (res.headers.get("content-type") || "").toLowerCase();
      if (!contentType.includes("application/json")) {
        const text = await res.text().catch(() => "");
        console.error("Save returned non-JSON:", {
          status: res.status,
          contentType,
          preview: text.slice(0, 500),
        });
        showFlash("Save failed (server returned non-JSON).", false);
        return;
      }

      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data.ok) {
        showFlash(data.error || data.detail || data.message || "Save failed.", false);
        return;
      }

      showFlash("Saved ✓", true);
      await window.Upgrades.refresh();
      window.Upgrades.closeEditor();
    } catch (err) {
      console.error(err);
      showFlash("Network error saving upgrade.", false);
    }
  },
};




   document.addEventListener("input", (e) => {
  const titleInput = e.target;
  if (!(titleInput instanceof HTMLInputElement)) return;
  if (!titleInput.matches('input[name="title"]')) return;

  const form = titleInput.closest("form");
  if (!form) return;

  const slugInput = form.querySelector('[data-upgrade-slug]');
  if (!slugInput) return;

  // Only auto-generate if slug is empty (don’t overwrite existing edits)
  if (slugInput.value) return;

  slugInput.value = titleInput.value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
});


// Upload image (delegated) — works after partial loads
document.addEventListener("click", async (e) => {
  const uploadBtn = e.target.closest("[data-upgrade-image-upload]");
  if (!uploadBtn) return;

  const form = uploadBtn.closest("form");
  if (!form) return;

  const fileInput = form.querySelector("[data-upgrade-image-file]");
  const preview = form.querySelector("[data-upgrade-image-preview]");
  const tmpKeyInput = form.querySelector('input[name="image_tmp_key"]');
  const imageUrlInput = form.querySelector('input[name="image_url"]'); // keep existing DB value!

  const file = fileInput?.files?.[0];
  if (!file) return toast("Choose an image first.");

  const fd = new FormData();
  fd.append("file", file);

  // If editing, pass upgrade id (optional, but useful on backend)
  const upgradeId = (form.querySelector('input[name="id"]')?.value || "").trim();
  if (upgradeId) fd.append("upgrade_id", upgradeId);

  // IMPORTANT: pass previous tmp key so backend can delete/replace it
  const prevTmpKey = (tmpKeyInput?.value || "").trim();
  if (prevTmpKey) fd.append("prev_tmp_key", prevTmpKey);

  uploadBtn.disabled = true;
  const original = uploadBtn.textContent;
  uploadBtn.textContent = "Uploading…";

  try {
    const res = await fetch("/admin/upgrades/ajax/upload-image", {
      method: "POST",
      credentials: "include",
      body: fd,
    });

    if (res.status === 401 || res.status === 403) return loginRedirect();

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      toast(data.error || "Upload failed.");
      return;
    }

    // Set ONLY the temp key (for finalize on Save)
    if (tmpKeyInput) tmpKeyInput.value = data.tmp_key || "";

    // DO NOT overwrite the saved image_url here if using tmp-key finalize workflow.
    // Keep existing DB image_url until Save runs.
    // imageUrlInput.value should only be changed in /admin/upgrades/ajax/save after finalize.
    // (But we can still show preview below.)

    // Preview from returned preview_url (could be temp-served or final)
    const previewUrl = data.preview_url || "";
    if (preview) {
      preview.src = previewUrl;
      preview.classList.toggle("hidden", !previewUrl);
    }

    // Optional: visually indicate pending unsaved change
    // e.g., store a data flag for later if you want
    if (imageUrlInput) imageUrlInput.dataset.pendingUpload = "1";

    // Clear file input so user can reselect same file again if needed
    if (fileInput) fileInput.value = "";

    toast("Image uploaded. Click Save Upgrade to apply.");
  } catch (err) {
    console.error(err);
    toast("Upload failed.");
  } finally {
    uploadBtn.disabled = false;
    uploadBtn.textContent = original;
  }
});


document.addEventListener("click", async (e) => {
  const clearBtn = e.target.closest("[data-upgrade-image-clear]");
  if (!clearBtn) return;

  const form = clearBtn.closest("form");
  if (!form) return;

  const preview = form.querySelector("[data-upgrade-image-preview]");
  const imageUrlInput = form.querySelector('input[name="image_url"]');
  const tmpKeyInput = form.querySelector('input[name="image_tmp_key"]');

  const tmpKey = (tmpKeyInput?.value || "").trim();

  // Clear UI immediately
  if (preview) {
    preview.src = "";
    preview.classList.add("hidden");
  }
  if (imageUrlInput) imageUrlInput.value = "";   // clear persisted url
  if (tmpKeyInput) tmpKeyInput.value = "";       // clear temp key

  // If there was a temp upload, delete it server-side
  if (tmpKey) {
    try {
      const res = await fetch("/admin/upgrades/ajax/delete-temp-image", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tmp_key: tmpKey }),
      });
      if (res.status === 401 || res.status === 403) return loginRedirect();
    } catch (err) {
      console.error(err);
    }
  }

  toast("Image removed.");
});


// Upgrades: delegated events (ONLY ONCE)
document.addEventListener("click", async (e) => {
  // Edit
  const editBtn = e.target.closest("[data-upgrade-edit]");
  if (editBtn) {
    const id = (editBtn.getAttribute("data-upgrade-edit") || "").trim();
    if (!id || id === "None" || id === "null" || id === "undefined") return;
    Upgrades.openEditor(id);
    return;
  }

  // Delete
  const delBtn = e.target.closest("[data-upgrade-delete]");
  if (!delBtn) return;

  if (window.CONTENT_LOCKED) {
    toast("Complete payment to unlock Upgrades.");
    return;
  }

  const id = (delBtn.getAttribute("data-upgrade-delete") || "").trim();
  if (!id || id === "None" || id === "null" || id === "undefined") return;

  if (!confirm("Delete this upgrade?")) return;

  try {
    const { res, data } = await apiJson(`/admin/upgrades/ajax/delete?id=${encodeURIComponent(id)}`, {
      method: "POST",
    });

    if (!res.ok || !data.ok) {
      toast(data.error || data.detail || "Delete failed.");
      return;
    }

    toast("Upgrade deleted");
    await Upgrades.refresh();
    Upgrades.closeEditor();
  } catch (err) {
    console.error(err);
    toast("Delete failed.");
  }
});



  document.addEventListener("change", async (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement)) return;
    if (!el.matches("[data-upgrade-active]")) return;

    const id = el.dataset.upgradeId;
    const checked = el.checked;

    // Optional: label support if you add it in the table partial
    const row = el.closest("[data-upgrade-row]");
    const label = row?.querySelector("[data-upgrade-status-label]");
    if (label) {
      label.textContent = checked ? "Active" : "Inactive";
      label.className =
        "text-xs font-semibold " +
        (checked ? "text-emerald-700" : "text-slate-400");
    }

    try {
      const { res, data } = await apiJson("/admin/upgrades/ajax/toggle-active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, is_active: checked }),
      });

      if (!res.ok || !data.ok) {
        toast(data.error || "Failed to update.");
        el.checked = !checked;

        if (label) {
          const reverted = el.checked;
          label.textContent = reverted ? "Active" : "Inactive";
          label.className =
            "text-xs font-semibold " +
            (reverted ? "text-emerald-700" : "text-slate-400");
        }
      }
    } catch (err) {
      toast("Failed to update.");
      el.checked = !checked;

      if (label) {
        const reverted = el.checked;
        label.textContent = reverted ? "Active" : "Inactive";
        label.className =
          "text-xs font-semibold " +
          (reverted ? "text-emerald-700" : "text-slate-400");
      }
    }
  });

  // ----------------------------
  // Guides (single definition)
  // ----------------------------
  function flashIn(elId, msg, ok = true) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = `<div class="text-sm rounded-xl px-3 py-2 ${
      ok
        ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
        : "bg-rose-50 border border-rose-200 text-rose-800"
    }">${msg}</div>`;
    setTimeout(() => (el.innerHTML = ""), 2400);
  }

 async function loadHtmlInto(url, targetId) {
  const r = await fetch(url, {
    credentials: "include",
    headers: { "X-Requested-With": "fetch" },
  });

  if (r.status === 401 || r.status === 403) return loginRedirect();

  const target = document.getElementById(targetId);
  if (!target) return;

  const html = await r.text();
  target.innerHTML = html;

  // ✅ Rebind ALL reorder tables after any partial inject
  initAllReorderTables();

  // Helpful debug (you can remove later)
  console.log("Injected partial into:", targetId, "tables:", document.querySelectorAll("table[data-list-kind]").length);
}



  window.Guides = {
    loaded: false,

    refresh() {
      const pid = document.getElementById("guidesPropertyFilter")?.value || "";
      const qs = pid ? `?property_id=${encodeURIComponent(pid)}` : "";
      return loadHtmlInto(`/admin/guides/partial/list${qs}`, "guides-list");
    },

    openNew() {
      return Guides.openForm("/admin/guides/partial/form", "New Guide");
    },

    openEdit(id) {
      return Guides.openForm(
        `/admin/guides/partial/form?id=${encodeURIComponent(id)}`,
        "Edit Guide"
      );
    },

    async openForm(url, title) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      document.getElementById("guides-editor-title").textContent = title || "Editor";
      await loadHtmlInto(url, "guides-editor-body");
      document.getElementById("guides-editor")?.classList.remove("hidden");
    },

    closeEditor() {
      document.getElementById("guides-editor")?.classList.add("hidden");
      const body = document.getElementById("guides-editor-body");
      if (body) body.innerHTML = "";
    },

    async submit(formEl) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      const r = await fetch("/admin/guides/ajax/save", {
        method: "POST",
        credentials: "include",
        body: new FormData(formEl),
      });
      if (r.status === 401 || r.status === 403) return loginRedirect();
      const j = await r.json().catch(() => ({}));
      if (!j.ok) return flashIn("guides-flash", j.error || "Save failed", false);
      flashIn("guides-flash", "Guide saved");
      Guides.closeEditor();
      Guides.refresh();
    },

    async remove(id) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      if (!confirm("Delete this guide?")) return;
      const r = await fetch(`/admin/guides/ajax/delete?id=${encodeURIComponent(id)}`, {
        method: "POST",
        credentials: "include",
      });
      const j = await r.json().catch(() => ({}));
      if (!j.ok) return flashIn("guides-flash", j.error || "Delete failed", false);
      flashIn("guides-flash", "Guide deleted");
      Guides.refresh();
    },
  };

  // ----------------------------
  // Settings tabs + Team settings
  // ----------------------------
  function getSettingsTabFromHash() {
    const h = location.hash || "";
    if (!h.startsWith("#settings")) return "profile";
    const q = h.split("?")[1] || "";
    const params = new URLSearchParams(q);
    return params.get("tab") || "profile";
  }

  function showSettingsPanel(key) {
    document.querySelectorAll(".settings-panel").forEach((p) => p.classList.add("hidden"));
    document.getElementById(`settings-${key}`)?.classList.remove("hidden");

    document.querySelectorAll(".settings-tab").forEach((b) => b.classList.remove("bg-slate-50"));
    document
      .querySelector(`.settings-tab[data-settings="${key}"]`)
      ?.classList.add("bg-slate-50");

    const base = (location.hash || "#overview").split("?")[0];
    if (base === "#settings") history.replaceState(null, "", `#settings?tab=${key}`);
  }

  function updateMemberStatusUI(memberId, isActive) {
    const row = document.querySelector(`[data-member-row="${memberId}"]`);
    if (!row) return;

    const statusEl = row.querySelector("[data-member-status]");
    if (statusEl) {
      statusEl.textContent = isActive ? "Active" : "Disabled";
      statusEl.className = isActive
        ? "text-xs font-semibold text-emerald-700"
        : "text-xs font-semibold text-rose-700";
    }

    const toggleBtn = row.querySelector("[data-member-toggle]");
    if (toggleBtn) {
      toggleBtn.textContent = isActive ? "Disable" : "Enable";
      toggleBtn.dataset.nextActive = isActive ? "false" : "true";
    }
  }

  function setSaveEnabled(memberId, enabled) {
    const btn = document.querySelector(`[data-member-save-role][data-member-id="${memberId}"]`);
    if (!btn) return;
    btn.disabled = !enabled;
    btn.classList.toggle("opacity-50", !enabled);
    btn.classList.toggle("cursor-not-allowed", !enabled);
  }

  async function refreshTeamRows() {
    const res = await fetch("/admin/settings/team/table", { credentials: "include" });

    if (res.status === 401 || res.status === 403) {
      toast("Session expired. Please sign in again.");
      loginRedirect();
      return false;
    }
    if (!res.ok) {
      toast("Could not refresh team list.");
      return false;
    }

    const html = await res.text();
    const tbody = document.querySelector("#settings-team tbody");
    if (tbody) tbody.innerHTML = html;

    // re-disable Save buttons by default
    document.querySelectorAll("[data-member-save-role]").forEach((b) => {
      b.disabled = true;
      b.classList.add("opacity-50", "cursor-not-allowed");
    });

    return true;
  }

  function initTeamSettings() {
    const teamPanel = document.getElementById("settings-team");
    if (!teamPanel) return;

    // Disable Save buttons initially
    document.querySelectorAll("[data-member-save-role]").forEach((b) => {
      b.disabled = true;
      b.classList.add("opacity-50", "cursor-not-allowed");
    });

    // Modal open/close
    const inviteModal = document.getElementById("inviteModal");
    document.getElementById("openInvite")?.addEventListener("click", () => inviteModal?.classList.remove("hidden"));
    ["closeInvite", "cancelInvite"].forEach((id) => {
      document.getElementById(id)?.addEventListener("click", () => inviteModal?.classList.add("hidden"));
    });

    // Invite submit
    document.getElementById("inviteForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();

      const formEl = e.currentTarget;
      const body = Object.fromEntries(new FormData(formEl).entries());

      try {
        const res = await fetch("/admin/settings/team/invite", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
        });

        const data = await res.json().catch(() => ({}));

        if (res.ok && data.ok) {
          toast(data.message || "Invite saved.");
          formEl.reset();
          inviteModal?.classList.add("hidden");
          await refreshTeamRows();
        } else {
          toast(data.detail || data.message || "Invite failed.");
        }
      } catch (err) {
        console.error(err);
        toast("Invite failed.");
      }
    });

    // Role dropdown -> enable Save if changed
    teamPanel.addEventListener("change", (e) => {
      const el = e.target;
      if (!(el instanceof HTMLSelectElement)) return;
      if (!el.matches("[data-member-role]")) return;

      const memberId = el.dataset.memberId;
      const original = (el.dataset.originalRole || "").trim();
      const current = (el.value || "").trim();
      if (!memberId) return;

      setSaveEnabled(memberId, current !== original);
    });

    // Delegated team actions
    teamPanel.addEventListener("click", async (e) => {
      const btn = e.target instanceof HTMLElement ? e.target.closest("button") : null;
      if (!btn) return;

      // Save role
      if (btn.matches("[data-member-save-role]")) {
        const memberId = btn.dataset.memberId;
        const select = document.querySelector(`[data-member-role][data-member-id="${memberId}"]`);
        const role = select?.value;
        if (!memberId || !role) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role }),
          });

          if (res.ok && data.ok) {
            toast(data.message || "Role saved.");
            await refreshTeamRows();
          } else {
            toast(data.detail || data.message || "Failed to save role.");
            btn.disabled = false;
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Failed to save role.");
          btn.disabled = false;
        }
        return;
      }

      // Enable / Disable
      if (btn.matches("[data-member-toggle]")) {
        const memberId = btn.dataset.memberId;
        const nextActive = btn.dataset.nextActive;
        if (!memberId || (nextActive !== "true" && nextActive !== "false")) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: nextActive === "true" }),
          });

          if (res.ok && data.ok) {
            const nowActive = nextActive === "true";
            updateMemberStatusUI(memberId, nowActive);
            toast(nowActive ? "User enabled." : "User disabled.");
          } else {
            toast(data.detail || data.message || "Failed to update status.");
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Failed to update status.");
        } finally {
          btn.disabled = false;
        }
        return;
      }

      // Delete invite / Remove
      if (btn.matches("[data-member-delete]")) {
        const memberId = btn.dataset.memberId;
        const label = btn.dataset.deleteLabel || "Remove";
        if (!memberId) return;

        if (!confirm(`${label}? This cannot be undone.`)) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, { method: "DELETE" });
          if (res.ok && data.ok) {
            document.querySelector(`[data-member-row="${memberId}"]`)?.remove();
            toast(label === "Delete invite" ? "Invite deleted." : "User removed.");
          } else {
            toast(data.detail || data.message || "Delete failed.");
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Delete failed.");
        } finally {
          btn.disabled = false;
        }
      }
    });
  }

  // ----------------------------
  // Settings init (only once)
  // ----------------------------
  let settingsInitialized = false;

  function initSettingsUI() {
    if (settingsInitialized) return;
    settingsInitialized = true;

    document.querySelectorAll(".settings-tab").forEach((btn) => {
      btn.addEventListener("click", () => showSettingsPanel(btn.dataset.settings));
    });

    document.getElementById("profileForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const body = Object.fromEntries(new FormData(form).entries());

      try {
        const { res, data } = await apiJson("/admin/settings/profile", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (res.ok && (data.ok ?? true)) toast("Profile saved.");
        else toast(data.detail || data.message || "Failed to save profile.");
      } catch (err) {
        if (String(err) !== "Error: auth") toast("Failed to save profile.");
      }
    });

    document.getElementById("notifForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);

      const keys = ["guest_messages", "maintenance_assigned", "turnover_due"];
      const prefs = {};
      keys.forEach((k) => (prefs[k] = fd.get(k) !== null));

      try {
        const { res, data } = await apiJson("/admin/settings/notifications", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prefs }),
        });
        if (res.ok && (data.ok ?? true)) toast("Notifications saved.");
        else toast(data.detail || data.message || "Failed to save notifications.");
      } catch (err) {
        if (String(err) !== "Error: auth") toast("Failed to save notifications.");
      }
    });

    initTeamSettings();
    showSettingsPanel(getSettingsTabFromHash());
  }




// Helper: "guides" -> "guide", "upgrades" -> "upgrade"
function singularize(kind) {
  const k = String(kind || "").trim().toLowerCase();
  if (!k) return "";
  if (k.endsWith("ies")) return k.slice(0, -3) + "y";
  if (k.endsWith("s")) return k.slice(0, -1);
  return k;
}

// Helper: accept "guide" or "guides" and normalize to plural route kind
function normalizeKind(kind) {
  const k = String(kind || "").trim().toLowerCase();
  if (k === "guide") return "guides";
  if (k === "upgrade") return "upgrades";
  return k;
}

function initReorderForTable(rootEl) {
  if (!rootEl) return;

  if (!window.Sortable) {
    console.warn("Sortable missing — drag/drop disabled.");
    return;
  }

  // ✅ normalize (critical)
  const rawKind = (rootEl.getAttribute("data-list-kind") || "").trim();
  const kind = normalizeKind(rawKind); // "guides" | "upgrades"
  if (!kind) {
    console.warn("Missing data-list-kind on table.");
    return;
  }

  const tbody = rootEl.querySelector("[data-reorder-body]") || rootEl.querySelector("tbody");
  if (!tbody) {
    console.warn("No tbody found for reorder table.");
    return;
  }

  // Prevent double init
  if (tbody._sortable) return;

  const singular = singularize(kind); // "guide" | "upgrade"
  const rowAttrSingular = `data-${singular}-row`; // data-guide-row
  const rowAttrPlural = `data-${kind}-row`;       // data-guides-row

  const hasHandles = rootEl.querySelectorAll("[data-reorder-handle]").length > 0;

  tbody._sortable = new Sortable(tbody, {
    animation: 150,
    draggable: "tr",
    ...(hasHandles ? { handle: "[data-reorder-handle]" } : {}),

    onEnd: async () => {
      // Prefer singular row attr, fallback to plural
      let rows = Array.from(tbody.querySelectorAll(`tr[${rowAttrSingular}]`));
      if (!rows.length) rows = Array.from(tbody.querySelectorAll(`tr[${rowAttrPlural}]`));

      const idsRaw = rows
        .map((tr) => (tr.getAttribute(rowAttrSingular) || tr.getAttribute(rowAttrPlural) || "").trim())
        .filter(Boolean);

      const ids = idsRaw.map(Number).filter(Number.isFinite);

      if (!ids.length) {
        console.error("Reorder: no valid numeric IDs found.", {
          rawKind, kind, idsRaw, rowAttrSingular, rowAttrPlural
        });
        toast("Could not reorder: row IDs missing.");
        return;
      }

      const url =
        kind === "guides"
          ? "/admin/guides/ajax/reorder"
          : "/admin/upgrades/ajax/reorder";

      const payload = { ids };

      try {
        const { res, data } = await apiJson(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (!res.ok || !data.ok) {
          console.error("Reorder failed:", { url, status: res.status, data, payload });
          toast(data?.error || data?.detail || "Failed to save order.");
          // revert UI to DB order
          if (kind === "guides") await Guides.refresh();
          if (kind === "upgrades") await Upgrades.refresh();
          return;
        }

        toast("Order saved ✓");
      } catch (err) {
        console.error("Reorder error:", err);
        toast("Failed to save order.");
        if (kind === "guides") await Guides.refresh();
        if (kind === "upgrades") await Upgrades.refresh();
      }
    },
  });

  console.log("Sortable bound:", { rawKind, kind, rowAttrSingular, rowAttrPlural, hasHandles });
}
function initAllReorderTables() {
  document.querySelectorAll("table[data-list-kind]").forEach(initReorderForTable);
}


let routingInitialized = false;
let currentViewKey = null;

function initRouting() {
  if (routingInitialized) return;
  routingInitialized = true;

  const pageTitle = document.getElementById("page-title");
  const pageSubtitle = document.getElementById("page-subtitle");
  const navItems = Array.from(document.querySelectorAll(".nav-item"));
  const views = Array.from(document.querySelectorAll(".view"));

  const subtitles = {
  overview: BOOT.user_role === "super" ? "System health & activity" : "Your portfolio at a glance",
  properties: "Search and manage your portfolio",
  chats: "Lifecycle, priority, escalations",
  pmcs: "Partners, integrations, access",
  guides: "Guest-facing guides per property",
  upgrades: "Paid add-ons per property",
  files: "Configs & manuals",
  analytics: BOOT.user_role === "super" ? "Trends & performance" : "Your chat trends & performance",
  settings: "Account and system settings",
};

  async function showView(key) {
    key = (key || "overview").toLowerCase();

    // fallback safety FIRST
    if (!document.getElementById(`view-${key}`)) key = "overview";

    if (key === currentViewKey) return;
    currentViewKey = key;

    // show/hide views
    views.forEach(v => v.classList.add("hidden"));
    document.getElementById(`view-${key}`)?.classList.remove("hidden");

    // active nav
    navItems.forEach(btn => btn.setAttribute("aria-current", "false"));
    const activeBtn = navItems.find(b => (b.dataset.view || "").toLowerCase() === key);
    if (activeBtn) activeBtn.setAttribute("aria-current", "page");

    // titles
    const label = activeBtn?.querySelector(".sidebar-label")?.textContent?.trim() || "Overview";
    if (pageTitle) pageTitle.textContent = label;
    if (pageSubtitle) pageSubtitle.textContent = subtitles[key] || "";

    // view hooks
    if (key === "properties") filterProperties();

    if (key === "settings") {
      initSettingsUI();
      showSettingsPanel(getSettingsTabFromHash());
    }

    if (key === "guides" && !Guides.loaded) {
      Guides.loaded = true;
      Guides.refresh();
    }

    if (key === "upgrades" && !Upgrades.loaded) {
      Upgrades.loaded = true;
      Upgrades.refresh();
    }

    if (key === "analytics") {
      const days = document.getElementById("analyticsRange")?.value || 30;
      requestAnimationFrame(() => {
        loadChatAnalytics(days);
        resizeChatAnalyticsChartSoon();
      });
    }
  }

  function route() {
    const params = new URLSearchParams(window.location.search);

    // If a session is selected, ALWAYS show chats
    if (params.has("session_id")) {
      showView("chats");
      return;
    }

    const keyFromHash = (location.hash || "").slice(1).split("?")[0].toLowerCase();
    const keyFromView = (params.get("view") || "").toLowerCase();

    showView(keyFromHash || keyFromView || "overview");
  }

  // nav clicks
  navItems.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const key = (btn.dataset.view || "overview").toLowerCase();

      const url = new URL(window.location.href);
      url.searchParams.delete("session_id");
      url.searchParams.set("view", key);
      url.hash = `#${key}`;

      history.pushState(null, "", url.toString());
      showView(key);
    });
  });

  window.addEventListener("popstate", route);
  window.addEventListener("hashchange", route);

  route();
}


document.addEventListener("change", (e) => {
  const form = document.getElementById("chatFilters");
  if (!form) return;

  // Only auto-submit changes that happen inside the filters form
  if (!e.target.closest("#chatFilters")) return;

  // Only auto-submit dropdowns (not checkboxes / inputs)
  if (!(e.target instanceof HTMLSelectElement)) return;

  form.requestSubmit ? form.requestSubmit() : form.submit();
});



// ==================================================
// Escalation dropdown (detail panel)
// ==================================================

/*document.addEventListener("change", async (e) => {
  const sel = e.target.closest('[data-role="escalation-select"]');
  if (!sel) return;

  const panel = sel.closest("[data-session-id]");
  const sessionId = panel?.getAttribute("data-session-id");
  if (!sessionId) {
    console.warn("Escalation change: no session id");
    return;
  }

  const level = (sel.value || "").trim(); // "", low, medium, high

  // bootstrap api
  const bootEl = document.getElementById("dashboard-bootstrap");
  const boot = bootEl ? JSON.parse(bootEl.textContent) : null;
  const tmpl = boot?.api?.chat_escalate;
  if (!tmpl) {
    console.warn("Missing api.chat_escalate");
    return;
  }

  const url = tmpl.replace("{session_id}", encodeURIComponent(sessionId));

  // optimistic UI update (detail)
  const pill = panel.querySelector('[data-role="escalation-pill"]');
  if (pill) {
    pill.textContent =
      level === "high" ? "🔴 Escalation: High" :
      level === "medium" ? "🟡 Escalation: Medium" :
      level === "low" ? "⚪ Escalation: Low" :
      "No escalation";

    pill.className =
      "inline-flex items-center px-2 py-1 rounded-full text-xs font-semibold " +
      (level === "high" ? "bg-rose-100 text-rose-800" :
       level === "medium" ? "bg-amber-100 text-amber-800" :
       level === "low" ? "bg-slate-100 text-slate-700" :
       "bg-slate-100 text-slate-500");
  }

  panel.setAttribute("data-escalation-level", level);

  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ escalation_level: level }) // ⚠️ adjust if needed
    });

    // update list row badge
    const row = document.querySelector(`[data-session-row="${sessionId}"]`);
    const rowBadge = row?.querySelector("[data-escalation-badge]");
    if (rowBadge) {
      rowBadge.textContent =
        level === "high" ? "🔴 High" :
        level === "medium" ? "🟡 Medium" :
        level === "low" ? "⚪ Low" : "—";

      rowBadge.className =
        "px-2 py-1 rounded-full font-semibold " +
        (level === "high" ? "bg-rose-100 text-rose-800" :
         level === "medium" ? "bg-amber-100 text-amber-800" :
         level === "low" ? "bg-slate-100 text-slate-700" :
         "text-slate-400");
    }
  } catch (err) {
    console.error("Escalation update failed", err);
  }
});
*/


// ------------------------------
// SYNC ALL PROPERTIES PMC SIDE
// ------------------------------

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("sync-all-properties-btn");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    if (window.CONTENT_LOCKED) return toast("Complete payment to unlock property syncing.");

    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Syncing…";

    try {
      const res = await fetch(`/auth/sync-pmc-properties`, {
        method: "POST",
        credentials: "include",
      });

      if (res.status === 401 || res.status === 403) return loginRedirect();
      if (res.status === 402) return (window.location.href = "/pmc/signup");

      if (!res.ok) throw new Error(await res.text());

      window.location.reload();
    } catch (err) {
      console.error(err);
      alert("Failed to sync properties");
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });
});


// ------------------------------
// DOM ready (single, clean)
// ------------------------------
document.addEventListener("DOMContentLoaded", async () => {
  // 1) Core shell
  initSidebar();
  initRouting();

  window.rerenderAllMoodBadges?.();
  window.applyMoodConfidenceHints?.(document);

  // 2) Property filters
  document.getElementById("searchInput")?.addEventListener("input", filterProperties);
  document.getElementById("statusFilter")?.addEventListener("change", filterProperties);

  // 3) Guides / Upgrades dropdowns
  document.getElementById("guidesPropertyFilter")?.addEventListener("change", () => Guides.refresh());
  document.getElementById("upgradesPropertyFilter")?.addEventListener("change", () => {
    Upgrades.closeEditor();
    Upgrades.refresh();
  });

  // 4) Overview chart init (after DOM exists)
  const statusCanvas = document.getElementById("statusChart");
  if (statusCanvas && window.Chart) {
    statusChartInstance = new Chart(statusCanvas, {
      type: "bar",
      data: {
        labels: ["LIVE", "OFFLINE"],
        datasets: [
          {
            label: "Properties",
            data: [Number(BOOT.live_props || 0), Number(BOOT.offline_props || 0)],
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
      },
    });
  }

  // 5) Render overview counters immediately
  updateOverviewUI();

  // 6) If URL has session_id, open inline chat detail
  const params = new URLSearchParams(window.location.search);
  const sid = params.get("session_id");
  if (sid) {
    setInlineDetailOpen(true);
    await loadChatDetail(sid);
  } else {
    setInlineDetailOpen(false);
  }

  // 7) (Optional) If analytics view is currently visible on load, render it once
  if (typeof isAnalyticsVisible === "function" && isAnalyticsVisible()) {
    const days = document.getElementById("analyticsRange")?.value || 30;
    loadChatAnalytics(days);
    resizeChatAnalyticsChartSoon();
  }
 // applyChatsFilters();
});
