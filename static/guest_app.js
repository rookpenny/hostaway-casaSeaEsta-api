



document.addEventListener("DOMContentLoaded", async function () {
    // Normalize Sandy live flag (boolean-safe)
    const sandyLive = window.SANDY_LIVE === true || window.SANDY_LIVE === "true";

   
    // --- Screens + core elements ---
    const screens = {
      home: document.getElementById("screen-home"),
      chat: document.getElementById("screen-chat"),
      experiences: document.getElementById("screen-experiences"),
      upgrades: document.getElementById("screen-upgrades"),
    };

    const sandyEnabled = window.SANDY_ENABLED === true || window.SANDY_ENABLED === "true";
    if (!sandyEnabled) {
      document.body.innerHTML = `
        <div class="min-h-screen bg-[#f5f5f5] flex items-center justify-center p-6">
          <div class="max-w-md w-full bg-white rounded-3xl p-6 shadow-sm text-center">
            <h1 class="text-2xl font-semibold text-slate-900">Guest experience unavailable</h1>
            <p class="mt-3 text-slate-600">This property hasn’t enabled Sandy yet. Please contact your host.</p>
          </div>
        </div>`;
      return;
    }

    const LOGO_WHITE = "/static/img/neat-sleeps-white.png";
    const LOGO_BLACK = "/static/img/neat-sleeps-black.png";

    // helper: are we currently on the chat screen?
    function isChatVisible() {
      const chatScreen = screens.chat;
      return chatScreen && !chatScreen.classList.contains("hidden");
    }

    // --- Core UI refs ---
    const homeLogin = document.getElementById("home-login");
    const homeStay = document.getElementById("home-stay");

    // Chat logic refs (MUST be defined before quick replies wiring)
    const chatBox = document.getElementById("chat-box");
    const chatInput = document.getElementById("chat-input");
    const chatSend = document.getElementById("chat-send");
    const chatPlus = document.querySelector(".chat-plus");


     // --- Global state ---
    let isUnlocked = !!window.INITIAL_VERIFIED;
    let lastUnlockCode = null;
    let currentSessionId =
      window.INITIAL_SESSION_ID ||
      localStorage.getItem(`server_session_${window.PROPERTY_ID}`) ||
      null;

    let guestName = null;
    let arrivalDate = null;
    let departureDate = null;
    let checkinTime = null;
    let checkoutTime = null;
    let upgradesBound = false;

    let nextUserParentId = null; // set when user clicks a chip; consumed by sendChat()


    let lastUserMessage = null;
    let chatInFlight = false;
    let chatAbort = null;
    let lastUserIntent = null;
    let lastBotIntent = null;
    let transcript = [];
    let chatRestored = false;

    let chatHasWelcome = false;
    let guidesAbortController = null;
    let willRedirect = false;
    
    // ===============================
// NEW: Chat UX Enhancements
// ===============================

// --- Haptics (mobile) ---
function haptic(ms = 10) {
  try { if (navigator.vibrate) navigator.vibrate(ms); } catch {}
}

// --- Chat persistence ---
function getChatStorageKey() {
  return `chat_history_${window.PROPERTY_ID}`;
}


// ===============================
// IDs / Threading / Reactions / Analytics
// ===============================

const THREAD_KEY = `thread_${window.PROPERTY_ID}`;

// canonical thread id (bind to server session_id when available)
let currentThreadId = null;
try {
  currentThreadId = localStorage.getItem(THREAD_KEY) || null;
} catch {}

function newMid(prefix = "m") {
  // good-enough client id for analytics + reactions
  return `${prefix}_${Date.now().toString(16)}_${Math.random().toString(16).slice(2)}`;
}

// Reactions storage
const REACTIONS_KEY = `sandy_reactions_${window.PROPERTY_ID}`;
let reactionsById = (() => {
  try { return JSON.parse(localStorage.getItem(REACTIONS_KEY) || "{}") || {}; } catch { return {}; }
})();
function saveReactions(map) {
  try { localStorage.setItem(REACTIONS_KEY, JSON.stringify(map || {})); } catch {}
}

// Analytics hook (NO UI IMPACT)
// Replace internals later with your analytics SDK if you want.
function emitAnalytics(eventName, payload = {}) {
  try {
    // window.analytics?.track?.(eventName, payload);
    // console.log("[analytics]", eventName, payload);
  } catch {}
}


function getOrCreateThreadId() {
  try {
    let id = localStorage.getItem(THREAD_KEY);
    if (!id) {
      id = `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(THREAD_KEY, id);
    }
    return id;
  } catch {
    return `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  }
}

currentThreadId = getOrCreateThreadId();


function saveTranscript() {
  try {
    localStorage.setItem(getChatStorageKey(), JSON.stringify(transcript || []));
  } catch {}
}

function restoreChatHistory() {
  try {
    const raw = localStorage.getItem(getChatStorageKey());
    if (!raw) return;

    const items = JSON.parse(raw);
    if (!Array.isArray(items)) return;

    transcript = items;

    // recover thread id from history if present (older sessions)
    const lastWithThread = [...transcript].reverse().find((m) => m?.thread_id);
    if (lastWithThread?.thread_id && !currentThreadId) {
      currentThreadId = lastWithThread.thread_id;
      try { localStorage.setItem(THREAD_KEY, currentThreadId); } catch {}
    }

    chatBox.innerHTML = "";

    transcript.forEach((m) => {
      renderMessage(m.text, m.sender, {
        skipSave: true,
        id: m.id || null,
        thread_id: m.thread_id || currentThreadId || null,
        parent_id: m.parent_id ?? null,
        variant: m.variant || "normal",
        ts: m.ts || null,
      });
    });

    scrollChatToBottom();
  } catch {}
}


//const PAID_UPGRADES_KEY = `paid_upgrades_${window.PROPERTY_ID}`;

function paidUpgradesKey() {
  if (!currentSessionId) return null; // 🚫 don’t cache without a stay
  return `paid_upgrades_${window.PROPERTY_ID}_${String(currentSessionId)}`;
}


function readPaidUpgrades() {
  try {
    return JSON.parse(localStorage.getItem(paidUpgradesKey()) || "[]");
  } catch { return []; }
}

function writePaidUpgrades(ids) {
  try { localStorage.setItem(paidUpgradesKey(), JSON.stringify(ids || [])); } catch {}
}

async function loadUpgradeRecommendation(propertyId, upgradeId) {
  const el = document.getElementById("upgrade-active-recommendation");
  if (!el) return;

  el.textContent = ""; // clear

  try {
    const res = await fetch(
      `/guest/properties/${encodeURIComponent(propertyId)}/upgrades/${encodeURIComponent(upgradeId)}/recommendation`,
      { credentials: "include" }
    );
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      // don't scare the guest — fail quietly
      return;
    }

    if (data?.suggested_message) {
      el.textContent = data.suggested_message;
    }
  } catch (e) {
    // fail silently
  }
}

    


// --- Lightweight “memory” (frontend-only) ---
const MEMORY_KEY = `sandy_memory_${window.PROPERTY_ID}`;

function loadMemory() {
  try {
    const raw = localStorage.getItem(MEMORY_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveMemory(mem) {
  try { localStorage.setItem(MEMORY_KEY, JSON.stringify(mem || {})); } catch {}
}

let sandyMemory = loadMemory();

function maybeUpdateMemoryFromUserText(text) {
  const t = String(text || "").toLowerCase();

  // very simple “preferences”
  const cuisines = ["mexican","italian","sushi","seafood","thai","indian","bbq","steak","vegan","vegetarian","gluten-free","coffee","breakfast","brunch"];
  const found = cuisines.find(c => t.includes(c));
  if (found) {
    sandyMemory.last_food_pref = found;
    saveMemory(sandyMemory);
    renderSystemNote(`Noted: you mentioned **${found}**.`); // feels ChatGPT-like
  }
}

function renderSystemNote(mdText, parent_id = null) {
  if (!mdText) return;

  const entry = pushTranscript("bot", mdText, "system", {
    id: newMid("s"),
    thread_id: currentThreadId || null,
    parent_id,
    ts: Date.now(),
  });

  renderMessage(mdText, "bot", {
    skipSave: true,
    id: entry.id,
    thread_id: entry.thread_id,
    parent_id: entry.parent_id,
    variant: "system",
    ts: entry.ts,
  });

  emitAnalytics("message_sent", {
    sender: "bot",
    message_id: entry.id,
    parent_id: entry.parent_id,
    thread_id: entry.thread_id,
    variant: entry.variant,
    length: entry.text.length,
  });

  return entry;
}



// --- Typing indicator w/ “thinking” label ---
function getThinkingLabel(userText) {
  const t = String(userText || "").toLowerCase();
  if (t.includes("restaurant") || t.includes("food") || t.includes("coffee")) return "Looking up nearby spots…";
  if (t.includes("wifi")) return "Checking WiFi details…";
  if (t.includes("check") || t.includes("door") || t.includes("code")) return "Pulling up check-in info…";
  if (t.includes("late") || t.includes("early")) return "Checking availability…";
  if (t.includes("guide") || t.includes("things to do")) return "Finding local tips…";
  return "Thinking…";
}

// Replace your addTyping() with this version (same name)
function addTyping(labelText = "Thinking…") {
  if (!chatBox || document.getElementById("typing-indicator")) return;

  const row = document.createElement("div");
  row.id = "typing-indicator";
  row.className = "msg bot";

  const avatar = document.createElement("div");
  avatar.className = "avatar avatar-sandy";

  const column = document.createElement("div");

  const label = document.createElement("div");
  label.className = "timestamp";
  label.textContent = labelText;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = `
    <span class="typing-dots">
      <span> </span><span> </span><span> </span>
    </span>
  `;

  column.appendChild(label);
  column.appendChild(bubble);

  row.appendChild(avatar);
  row.appendChild(column);

  chatBox.appendChild(row);
  scrollChatToBottom();
}

// --- Typewriter “streaming” effect ---
function createBotBubble({ id = null, thread_id = null, parent_id = null, variant = "normal", ts = null } = {}) {
  if (!chatBox) return null;

  const row = document.createElement("div");
  row.className = "msg bot";

  if (id) row.dataset.mid = id;
  if (thread_id) row.dataset.threadId = thread_id;
  if (parent_id) row.dataset.parentId = parent_id;

  const avatar = document.createElement("div");
  avatar.className = "avatar avatar-sandy";

  const column = document.createElement("div");

  const time = document.createElement("div");
  time.className = "timestamp";
  time.textContent = formatTime(ts ? new Date(ts) : new Date());

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (variant === "system") {
    bubble.style.background = "#e2e8f0";
    bubble.style.color = "#0f172a";
  }

  // handy for click handlers
  if (id) bubble.dataset.mid = id;
  if (thread_id) bubble.dataset.threadId = thread_id;
  if (parent_id) bubble.dataset.parentId = parent_id;

  const body = document.createElement("div");
  body.className = "message-text";
  body.innerHTML = "";

  bubble.appendChild(body);
  column.appendChild(time);
  column.appendChild(bubble);

  row.appendChild(avatar);
  row.appendChild(column);

  chatBox.appendChild(row);
  scrollChatToBottom();

  return { row, bubble, body };
}




async function typewriterTo(el, fullText, { speed = 12 } = {}) {
  const text = String(fullText || "");
  let i = 0;

  // render markdown progressively (simple + stable approach)
  // We incrementally set textContent first, then finalize with formatted markdown at the end.
  el.textContent = "";

  return new Promise((resolve) => {
    const tick = () => {
      i += Math.max(1, Math.round(text.length / 220)); // adaptive chunking
      el.textContent = text.slice(0, i);

      scrollChatToBottom();

      if (i >= text.length) {
        // finalize formatting once at the end (prevents jumpy HTML)
        el.innerHTML = formatMessage(text);
        resolve();
        return;
      }
      setTimeout(tick, speed);
    };
    tick();
  });
}

    

function isCheckoutDay() {
  if (!departureDate) return false;
  const today = new Date().toISOString().slice(0, 10);
  return departureDate === today;
}

function hourNow() {
  return new Date().getHours();
}

function maybeSendTimeNudge() {
  if (!isChatVisible() || !chatBox) return;

  const h = hourNow();

  // checkout day nudge
  if (isCheckoutDay() && h >= 7 && h <= 11) {
    renderSystemNote(`Heads up — checkout is **${checkoutTime || "10:00 AM"}**. Want late checkout?`);
    return;
  }

  // evening nudge
  if (h >= 21) {
    renderSystemNote("If you’re looking for something open late, tell me what you’re craving.");
  }
}
function shouldSendNudge(key, ttlMinutes = 180) {
  try {
    const k = `nudge_${window.PROPERTY_ID}_${key}`;
    const last = Number(localStorage.getItem(k) || 0);
    const now = Date.now();
    if (now - last < ttlMinutes * 60 * 1000) return false;
    localStorage.setItem(k, String(now));
    return true;
  } catch { return true; }
}


    function withAcknowledgement(finalBotText, intent) {
  const openers = {
    wifi: "Sure thing — ",
    door: "Absolutely — ",
    checkin: "Happy to help — ",
    checkout: "Good question — ",
    parking: "Yep — ",
    trash: "Got it — ",
    food: "Love that — ",
    things_to_do: "Great idea — ",
    weather: "You got it — ",
    general: "",
  };

  const prefix = openers[intent] || "";
  // Don’t double-prefix if your backend already starts like this
  const t = String(finalBotText || "");
  if (!prefix) return t;
  if (/^(sure thing|absolutely|happy to help|good question|got it|you got it)/i.test(t.trim())) return t;

  return prefix + t;
}

    function applyMemoryToFinalBotText(finalBotText, intent) {
  const t = String(finalBotText || "");

  // Only lightly weave memory in when it helps.
  if (intent === "food" && sandyMemory?.last_food_pref) {
    return (
      t +
      `\n\nSince you mentioned **${sandyMemory.last_food_pref}** earlier, I can lean that direction.`
    );
  }

  if (intent === "things_to_do" && sandyMemory?.last_food_pref) {
    // optional: tiny personalization
    return t + `\n\nWant me to pair this with a **${sandyMemory.last_food_pref}** spot nearby?`;
  }

  return t;
}

    function userExplicitlyWantsOpen(text, intent) {
  const t = String(text || "").toLowerCase();

  // verbs that mean “open/show”
  const openVerb = /\b(open|show|pull up|bring up|take me to|go to|launch)\b/;

  // intent-specific targets
  const targets = {
    wifi: /\b(wifi|wi-fi|internet|ssid|password|network)\b/,
    door: /\b(door|lock|keypad|entry|code|check-?in)\b/,
    checkin: /\b(check\s*in|check-?in|arrival|early check)\b/,
    checkout: /\b(check\s*out|check-?out|checkout|late checkout)\b/,
    experiences: /\b(guides|things to do|activities|recommendations|recommend)\b/,
    upgrades: /\b(upgrades|late checkout|early check-?in)\b/,
  };

  // map your intent to a target bucket
  let bucket = null;
  if (intent === "wifi") bucket = "wifi";
  else if (intent === "door") bucket = "door";
  else if (intent === "checkin") bucket = "checkin";
  else if (intent === "checkout") bucket = "checkout";
  else if (intent === "things_to_do") bucket = "experiences";
  else if (intent === "food") bucket = "experiences";

  if (!bucket) return false;

  return openVerb.test(t) && (targets[bucket]?.test(t) ?? false);
}

    
function detectIntent(text = "") {
  const t = String(text || "").toLowerCase();

  // Highest priority: emergencies and lockouts
  if (/\b(911|emergency|urgent|ambulance|fire|police|hospital)\b/.test(t)) return "emergency";
  if (/\b(lock(ed)? out|can'?t (get )?in|won'?t unlock)\b/.test(t)) return "door";

  // Stay ops
  if (/\b(wifi|wi-fi|internet|ssid|password|network)\b/.test(t)) return "wifi";
  if (/\b(door|lock|keypad|entry|code|check-?in code|door code)\b/.test(t)) return "door";
  if (/\b(park|parking|driveway|garage)\b/.test(t)) return "parking";

  // Times (split into checkin vs checkout)
  if (/\b(check\s*in|check-?in|arrival|arriving|early check)\b/.test(t)) return "checkin";
  if (/\b(check\s*out|check-?out|checkout|leaving|late check|late checkout)\b/.test(t)) return "checkout";

  // House / property ops
  if (/\b(house rules|rules|quiet hours|no smoking|pets|occupancy|part(y|ies))\b/.test(t)) return "house_rules";
  if (/\b(trash|garbage|recycl(e|ing)|bins?)\b/.test(t)) return "trash";

  // Support / escalation
  if (/\b(contact|host|phone|call|text|message|support)\b/.test(t)) return "contact_host";

  // Concierge
  if (/\b(restaurant|food|dinner|lunch|breakfast|brunch|coffee|cafe|espresso)\b/.test(t)) return "food";
  if (/\b(things to do|activities|fun|recommend|spots|bars|museum|hike|shopping|nightlife|beach)\b/.test(t)) return "things_to_do";
  if (/\b(weather|forecast|rain|temperature|wind)\b/.test(t)) return "weather";

  return "general";
}

  

// --- Follow-up chips under each bot answer ---
function buildFollowups(userText, finalBotText) {
  const intent = lastBotIntent || detectIntent(`${userText}\n${finalBotText}`);

  switch (intent) {
    case "wifi":
      return [
        { label: "Open WiFi", action: "OPEN_WIFI" },
        { label: "Copy password", action: "COPY_WIFI_PASSWORD" },
        { label: "Any speed tips?", action: "ASK", text: "Any tips to improve WiFi speed?" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "door":
      return [
        { label: "Open Door Code", action: "OPEN_DOOR" },
        { label: "Lock troubleshooting", action: "ASK", text: "The lock isn’t working—what should I try?" },
        { label: "What if it won’t unlock?", action: "ASK", text: "What do I do if the door won’t unlock?" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "checkin":
      return [
        { label: "Open Door Code", action: "OPEN_DOOR" },
        { label: "Check-in time", action: "ASK", text: "What’s check-in time?" },
        { label: "Early check-in", action: "OPEN_UPGRADES" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "checkout":
      return [
        { label: "Check-out time", action: "ASK", text: "What’s check-out time?" },
        { label: "Late checkout", action: "OPEN_UPGRADES" },
        { label: "Checkout steps", action: "ASK", text: "What are the check-out instructions?" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "parking":
      return [
        { label: "Parking instructions", action: "ASK", text: "Where should we park?" },
        { label: "Any restrictions?", action: "ASK", text: "Are there any parking restrictions?" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "trash":
      return [
        { label: "Trash instructions", action: "ASK", text: "Where do I take the trash?" },
        { label: "Trash day", action: "ASK", text: "What day is trash pickup?" },
        { label: "Recycling", action: "ASK", text: "How does recycling work here?" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];

    case "emergency":
      return [
        { label: "Emergency steps", action: "ASK", text: "What should I do in an emergency?" },
        { label: "Nearest hospital", action: "ASK", text: "Where is the nearest hospital?" },
        { label: "Contact the host", action: "CONTACT_HOST" },
        { label: "Call 911", action: "ASK", text: "Call 911" },
      ];

    case "contact_host":
      return [
        { label: "How do I contact the host?", action: "CONTACT_HOST" },
        { label: "House rules", action: "ASK", text: "What are the house rules?" },
        { label: "Emergency info", action: "ASK", text: "What’s the emergency info / nearest hospital?" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
      ];

    case "food":
      return [
        { label: "3 closest options", action: "ASK", text: "Give me 3 closest restaurant options with quick notes." },
        { label: "Kid-friendly places?", action: "ASK", text: "Any kid-friendly restaurants nearby?" },
        { label: "Date-night spots?", action: "ASK", text: "Any great date-night spots nearby?" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
      ];

    case "things_to_do":
      return [
        { label: "Top 3 nearby spots", action: "ASK", text: "What are the top 3 nearby things to do?" },
        { label: "Something indoors?", action: "ASK", text: "Any good indoor activities nearby?" },
        { label: "Family-friendly ideas", action: "ASK", text: "Any family-friendly activities nearby?" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
      ];

    case "weather":
      return [
        { label: "What about tomorrow?", action: "ASK", text: "What’s the weather like tomorrow?" },
        { label: "Best time to go out?", action: "ASK", text: "What’s the best time to go out today?" },
        { label: "Indoor options", action: "ASK", text: "If it rains, what indoor activities do you recommend?" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
      ];

    default:
      return [
        { label: "WiFi", action: "OPEN_WIFI" },
        { label: "Door code", action: "OPEN_DOOR" },
        { label: "Open Guides", action: "OPEN_GUIDES" },
        { label: "Contact the host", action: "CONTACT_HOST" },
      ];
  }
}


// ===============================
// Analytics (no UI impact)
// ===============================
const ANALYTICS_ENDPOINT = "/analytics/event"; // change if you want
const ANALYTICS_QUEUE_KEY = `analytics_q_${window.PROPERTY_ID}`;

function getAnonId() {
  try {
    const k = `anon_${window.PROPERTY_ID}`;
    let v = localStorage.getItem(k);
    if (!v) {
      v = `a_${Math.random().toString(36).slice(2)}_${Date.now()}`;
      localStorage.setItem(k, v);
    }
    return v;
  } catch {
    return `a_${Date.now()}`;
  }
}


function baseAnalyticsContext() {
  return {
    // chat/threading
    thread_id: currentThreadId || null,
    session_id: currentSessionId || null,

    // guest identity (anon)
    anon_id: getAnonId(),
    user_role: "guest",

    // request metadata
    path: location.pathname,
    ua: navigator.userAgent,
    ts: Date.now(),
  };
}



function readAnalyticsQueue() {
  try {
    const raw = localStorage.getItem(ANALYTICS_QUEUE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function writeAnalyticsQueue(arr) {
  try {
    localStorage.setItem(ANALYTICS_QUEUE_KEY, JSON.stringify(arr || []));
  } catch {}
}

function enqueueAnalytics(evt) {
  const q = readAnalyticsQueue();
  q.push(evt);

  // keep it bounded
  while (q.length > 200) q.shift();

  writeAnalyticsQueue(q);
}

function flushAnalyticsQueue() {
  const q = readAnalyticsQueue();
  if (!q.length) return;

  const blob = new Blob(
    [JSON.stringify({ events: q })],
    { type: "application/json" }
  );

  try {
    if (navigator.sendBeacon) {
      const ok = navigator.sendBeacon(ANALYTICS_ENDPOINT, blob);
      if (ok) {
        writeAnalyticsQueue([]);
        return;
      }
    }
  } catch {}

  // fallback fetch
  fetch(ANALYTICS_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    keepalive: true,
    body: JSON.stringify({ events: q }),
  })
    .then((res) => {
      if (res.ok) writeAnalyticsQueue([]);
    })
    .catch(() => {});
}



// Bridge emitAnalytics -> queue (NO UI IMPACT)
// Guard against double-wrapping
    
if (!window.__emitAnalyticsQueued) {
  window.__emitAnalyticsQueued = true;

  const _emitAnalytics = emitAnalytics;

emitAnalytics = function queuedEmitAnalytics(eventName, payload = {}) {
  try { _emitAnalytics(eventName, payload); } catch {}

  enqueueAnalytics({
    event_name: eventName,

    // ✅ stored in columns (not only JSON)
    property_id: window.PROPERTY_ID || null,
    session_id: currentSessionId || null,

    // ✅ stored in JSONB context/data
    context: baseAnalyticsContext(),
    data: payload,
  });

  if (document.visibilityState === "hidden") flushAnalyticsQueue();
  else if (Math.random() < 0.15) flushAnalyticsQueue();
};



  // flush bindings once
  if (!window.__analyticsFlushBound) {
    window.__analyticsFlushBound = true;

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushAnalyticsQueue();
    });
    window.addEventListener("pagehide", flushAnalyticsQueue);
  }
}




function renderFollowupChips(items, { parent_id = null } = {}) {
  if (!chatBox) return;

  const wrap = document.createElement("div");
  wrap.className = "flex gap-2 flex-wrap ml-[52px] mt-2 mb-4";

  if (parent_id) wrap.dataset.parentId = parent_id;
  if (currentThreadId) wrap.dataset.threadId = currentThreadId;

  const list = (items || []).slice(0, 4).map((item) =>
    typeof item === "string" ? { label: item, action: "ASK", text: item } : item
  );

  emitAnalytics("followups_shown", {
    thread_id: currentThreadId || null,
    parent_id: parent_id || null,
    count: list.length,
    labels: list.map((x) => x?.label || "").filter(Boolean),
  });

  list.forEach((obj, idx) => {
    const label = obj.label || "Option";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "px-3 py-2 rounded-full bg-white border border-slate-200 text-slate-900 text-sm font-semibold whitespace-nowrap active:scale-95";
    btn.textContent = label;

    btn.dataset.parentId = parent_id || "";
    btn.dataset.threadId = currentThreadId || "";
    btn.dataset.action = obj.action || "ASK";
    btn.dataset.index = String(idx);

    btn.addEventListener("click", () => {
      emitAnalytics("followup_click", {
        thread_id: currentThreadId || null,
        parent_id: parent_id || null,
        index: idx,
        label,
        action: obj.action || "ASK",
      });

      runFollowupAction(obj, { parent_id: parent_id || null });
    });

    wrap.appendChild(btn);
  });

  chatBox.appendChild(wrap);
  scrollChatToBottom();
}

function runFollowupAction(item, { parent_id = null } = {}) {
  const action = item?.action;

  switch (action) {
    case "OPEN_WIFI":
      emitAnalytics("ui_open", { target: "wifi_modal", parent_id });
      return openWifiModal();

    case "COPY_WIFI_PASSWORD": {
      const pw = document.getElementById("wifi-password")?.textContent?.trim();
      if (pw) navigator.clipboard?.writeText(pw);
      renderSystemNote("Copied WiFi password.", parent_id);
      emitAnalytics("copy", { what: "wifi_password", parent_id });
      return;
    }

    case "OPEN_DOOR":
      emitAnalytics("ui_open", { target: "door_modal", parent_id });
      return openCheckinModal();

    case "OPEN_GUIDES":
      emitAnalytics("navigate", { screen: "experiences", parent_id });
      return showScreen("experiences");

    case "OPEN_UPGRADES":
      emitAnalytics("navigate", { screen: "upgrades", parent_id });
      return showScreen("upgrades");

    case "CONTACT_HOST":
      emitAnalytics("contact_host_click", {
        parent_id,
        thread_id: currentThreadId || null,
        session_id: currentSessionId || null,
      });
      renderSystemNote("You can reach your host via your booking app messages.", parent_id);
      return;


    case "ASK":
    default: {
      const text = item?.text || item?.label;
      if (!text || !chatInput) return;

      // ✅ next user message should be parented to the bot message that offered the chip
      nextUserParentId = parent_id || null;

      chatInput.value = text;
      return sendChat();
    }
  }
}





// --- Rich “cards” (frontend-only parser; backend optional) ---
function renderCardsFromText(finalBotText) {
  const text = String(finalBotText || "");
  const intent = lastBotIntent || detectIntent(text);

  if (!chatBox) return;

  const card = document.createElement("div");
  card.className = "ml-[52px] mb-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm";

  const title = document.createElement("div");
  title.className = "font-semibold text-slate-900";
  title.textContent = "Quick actions";
  card.appendChild(title);

  const row = document.createElement("div");
  row.className = "mt-3 flex gap-2 flex-wrap";

  function pill(label, onClick, primary=false) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = primary
      ? "px-3 py-2 rounded-full bg-slate-900 text-white text-sm font-semibold active:scale-95"
      : "px-3 py-2 rounded-full bg-white border border-slate-200 text-slate-900 text-sm font-semibold active:scale-95";
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  if (intent === "wifi") {
    row.appendChild(pill("Open WiFi", openWifiModal, true));
    row.appendChild(pill("Copy password", () => runFollowupAction({ action: "COPY_WIFI_PASSWORD" })));
  } else if (intent === "door" || intent === "checkin") {
    row.appendChild(pill("Open Door Code", openCheckinModal, true));
    row.appendChild(pill("Troubleshooting", () => {
      chatInput.value = "The lock isn’t working—what should I try?";
      sendChat();
    }));
  } else if (intent === "checkout") {
    row.appendChild(pill("Late checkout", () => showScreen("upgrades"), true));
    row.appendChild(pill("Checkout steps", () => {
      chatInput.value = "What are the check-out instructions?";
      sendChat();
    }));
  } else {
    // keep your maps heuristic as an extra
    if (/google\.com\/maps/i.test(text)) {
      row.appendChild(pill("Open Guides", () => showScreen("experiences"), true));
      row.appendChild(pill("More options", () => {
        chatInput.value = "Give me 3 more options with short notes.";
        sendChat();
      }));
    } else {
      return; // don’t render a card for generic replies
    }
  }

  card.appendChild(row);
  chatBox.appendChild(card);
  scrollChatToBottom();
}


// --- Better error UI (retry etc.) ---
function renderErrorWithActions(message, { parent_id = null } = {}) {
  const text = message || "Oops! Something went wrong.";

  // Persist + render error message
  const entry = pushTranscript("bot", text, "normal", {
    id: newMid("b"),
    thread_id: currentThreadId || null,
    parent_id,
    ts: Date.now(),
  });

  
  emitAnalytics("chat_error", {
    thread_id: entry.thread_id || currentThreadId || null,
    parent_id: parent_id || null,
    message_id: entry.id,
    error: text,
  });



  renderMessage(text, "bot", {
    id: entry.id,
    thread_id: entry.thread_id,
    parent_id: entry.parent_id,
    variant: "normal",
    ts: entry.ts,
    skipSave: true,
  });

  emitAnalytics("message_sent", {
    sender: "bot",
    message_id: entry.id,
    parent_id: entry.parent_id,
    thread_id: entry.thread_id,
    variant: entry.variant,
    length: entry.text.length,
  });

  const actions = document.createElement("div");
  actions.className = "flex gap-2 flex-wrap ml-[52px] mt-2 mb-4";

  const retry = document.createElement("button");
  retry.type = "button";
  retry.className =
    "px-3 py-2 rounded-full bg-slate-900 text-white text-sm font-semibold active:scale-95";
  retry.textContent = "Try again";
  retry.addEventListener("click", () => {
    emitAnalytics("error_action_click", { parent_id, action: "retry" });
    if (!lastUserMessage) {
      chatInput?.focus();
      return;
    }
    chatInput.value = lastUserMessage;
    sendChat();
  });

  const guides = document.createElement("button");
  guides.type = "button";
  guides.className =
    "px-3 py-2 rounded-full bg-white border border-slate-200 text-slate-900 text-sm font-semibold active:scale-95";
  guides.textContent = "Open Guides";
  guides.addEventListener("click", () => {
    emitAnalytics("error_action_click", { parent_id, action: "open_guides" });
    showScreen("experiences");
  });

  actions.appendChild(retry);
  actions.appendChild(guides);

  chatBox?.appendChild(actions);
  scrollChatToBottom();
}





    

    // ✅ Assistant config from config.json (injected by server)
    const assistantCfg = window.ASSISTANT_CONFIG || {};
    const assistantName = assistantCfg.name || "Sandy";
    const voiceCfg = assistantCfg.voice || {};
    const quickReplies = Array.isArray(assistantCfg.quick_replies) ? assistantCfg.quick_replies : [];

    // --- Guides / experiences (local tips) ---
    const guidesGrid = document.getElementById("guides-grid");
    const guidesEmptyState = document.getElementById("guides-empty-state");
    const guidesFiltersContainer = document.getElementById("guides-filters");

    const guideModal = document.getElementById("guide-modal");
    const guideModalClose = document.getElementById("guide-modal-close");
    const guideModalTitle = document.getElementById("guide-modal-title");
    const guideModalBody = document.getElementById("guide-modal-body");

    const guidesState = {
      loaded: false,
      loading: false,
      guides: [],
      activeFilter: "all",
    };

    

   

    // Keep guest name in sync across UI
    function updateGuestNameInUI(name) {
      document.querySelectorAll(".guest-name").forEach((el) => {
        el.textContent = name || "Guest";
      });
    }

    // --- Guest state persistence ---
    const GUEST_STATE_KEY = `guest_state_${window.PROPERTY_ID}`;

    function saveGuestState() {
      try {
        const state = {
          isUnlocked,
          lastUnlockCode,
          sessionId: currentSessionId,
          guestName,
          arrivalDate,
          departureDate,
          checkinTime,
          checkoutTime,
        };
        window.localStorage.setItem(GUEST_STATE_KEY, JSON.stringify(state));
      } catch (e) {
        console.warn("Unable to save guest state:", e);
      }
    }

    function formatDateLong(dateStr) {
      if (!dateStr) return "-";
      const date = new Date(dateStr + "T00:00:00");
      return date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
    }

async function refreshUpgradeEligibility() {
  try {
    if (!isUnlocked || !currentSessionId) return;

    const res = await fetch(
      `/guest/properties/${window.PROPERTY_ID}/upgrades/evaluated?session_id=${encodeURIComponent(currentSessionId)}`,
      { credentials: "include", cache: "no-store" }
    );
    if (!res.ok) return;

    const data = await readJsonSafely(res);
    const list = Array.isArray(data?.upgrades) ? data.upgrades : [];

    const byId = new Map(list.map(u => [String(u.id), u]));

    document.querySelectorAll(".upgrade-slide").forEach((slide) => {
      const id = String(slide.dataset.upgradeId || "");
      const ev = byId.get(id);
      if (!ev) return;

      const disabled = !ev.eligible;
      slide.dataset.upgradeDisabled = disabled ? "true" : "false";
      slide.dataset.upgradeDisabledReason = ev.disabled_reason || "";

      if (disabled) slide.setAttribute("disabled", "disabled");
      else slide.removeAttribute("disabled");

      const banner = slide.querySelector(".upgrade-status-banner");
        if (banner) {
          banner.textContent = "";
          banner.classList.add("hidden");
          banner.classList.remove("block");
        }

    });

    // re-apply active CTA state
    const active = document.querySelector(".upgrade-slide.is-active");
    if (active) {
      const activeId = active.dataset.upgradeId;
      const idx = upgradeSlides.findIndex(s => String(s.dataset.upgradeId) === String(activeId));
      if (idx >= 0) setActiveSlideByIndex(idx);
    }
  } catch (e) {
    console.warn("refreshUpgradeEligibility failed:", e);
  }
}



    
async function syncPaidUpgradesFromServer() {
  try {
    const res = await fetch(`/guest/properties/${window.PROPERTY_ID}/upgrades/paid`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return;

    const data = await readJsonSafely(res);
    const ids = Array.isArray(data?.paid_upgrade_ids) ? data.paid_upgrade_ids : [];
    writePaidUpgrades(ids);

    const activeSlide = document.querySelector(".upgrade-slide.is-active");
    const id = activeSlide?.dataset?.upgradeId;
    if (window.applyPaidState && id) window.applyPaidState(id);
  } catch (e) {
    console.warn("syncPaidUpgradesFromServer failed:", e);
  }
}



    function loadGuestState() {
      try {

        if (window.INITIAL_SESSION_ID) {
          currentSessionId = window.INITIAL_SESSION_ID;
        }

        const raw = window.localStorage.getItem(GUEST_STATE_KEY);
        if (!raw) return null;

        const state = JSON.parse(raw);
        if (!state || typeof state !== "object") return null;

        const today = new Date().toISOString().slice(0, 10);
        if (state.departureDate && typeof state.departureDate === "string" && state.departureDate < today) {
          window.localStorage.removeItem(GUEST_STATE_KEY);
          return null;
        }

        if (state.isUnlocked === true) isUnlocked = true;
        if (state.lastUnlockCode) lastUnlockCode = state.lastUnlockCode;
        if (state.sessionId) currentSessionId = state.sessionId;

        if (state.guestName) {
          guestName = state.guestName;
          updateGuestNameInUI(guestName);
        }

        if (state.arrivalDate) {
          arrivalDate = state.arrivalDate;
          const el = document.getElementById("arrival-date-span");
          if (el) el.textContent = formatDateLong(arrivalDate);
        }

        if (state.departureDate) {
          departureDate = state.departureDate;
          const el = document.getElementById("departure-date-span");
          if (el) el.textContent = formatDateLong(departureDate);
        }

        if (state.checkinTime) {
          checkinTime = state.checkinTime;
          const el = document.getElementById("checkin-time-span");
          if (el) el.textContent = checkinTime;
        }

        if (state.checkoutTime) {
          checkoutTime = state.checkoutTime;
          const el = document.getElementById("checkout-time-span");
          if (el) el.textContent = checkoutTime;
        }

        return state;
      } catch (err) {
        console.warn("Failed to load guest state:", err);
        return null;
      }
    }

    loadGuestState();

    if (isUnlocked) {
  syncPaidUpgradesFromServer();
}
    

    function updateHomeState() {
      if (isUnlocked) {
        homeLogin?.classList.add("hidden");
        homeStay?.classList.remove("hidden");
      } else {
        homeLogin?.classList.remove("hidden");
        homeStay?.classList.add("hidden");
      }
    }

    // --- Top bar sliding menu nav ---
    const topBar = document.getElementById("top-bar");
    const mobileMenu = document.getElementById("mobile-menu");
    const menuToggle = document.getElementById("menu-toggle");
    const logoImg = document.getElementById("logo-img");
    const menuLinks = document.querySelectorAll("[data-menu-screen]");
    let isMenuOpen = false;

    const MOBILE_MENU_SLIDE_DURATION = 700;

    function swapLogoWithFade(newSrc) {
      if (!logoImg) return;
      const currentSrc = logoImg.getAttribute("src");
      if (currentSrc === newSrc) return;

      logoImg.style.opacity = "0";
      setTimeout(() => {
        logoImg.src = newSrc;
        logoImg.style.opacity = "1";
      }, 120);
    }

    function openMenu() {
      if (!mobileMenu) return;
      isMenuOpen = true;

      mobileMenu.classList.remove("fade-out");
      mobileMenu.classList.remove("-translate-y-full", "pointer-events-none");
      mobileMenu.classList.add("translate-y-0");

      topBar?.classList.remove("bg-transparent", "text-white");
      topBar?.classList.add("bg-slate-50", "text-slate-900");

      swapLogoWithFade(LOGO_BLACK);
      menuToggle?.classList.add("menu-toggle-open");
      document.body.classList.add("menu-open");
      menuToggle?.setAttribute("aria-expanded", "true");

      setTimeout(() => {
        if (isMenuOpen && mobileMenu) mobileMenu.classList.add("show-links");
      }, MOBILE_MENU_SLIDE_DURATION);
    }

    function closeMenu() {
      if (!mobileMenu) return;
      isMenuOpen = false;

      mobileMenu.classList.add("fade-out");
      mobileMenu.classList.remove("show-links");

      topBar?.classList.add("bg-transparent", "text-white");
      topBar?.classList.remove("bg-slate-50", "text-slate-900");

      if (document.body.classList.contains("chat-screen")) swapLogoWithFade(LOGO_BLACK);
      else swapLogoWithFade(LOGO_WHITE);

      menuToggle?.classList.remove("menu-toggle-open");
      document.body.classList.remove("menu-open");
      menuToggle?.setAttribute("aria-expanded", "false");

      setTimeout(() => {
        if (!isMenuOpen && mobileMenu) {
          mobileMenu.classList.add("-translate-y-full", "pointer-events-none");
          mobileMenu.classList.remove("translate-y-0", "fade-out");
        }
      }, MOBILE_MENU_SLIDE_DURATION);
    }

    menuToggle?.addEventListener("click", () => {
      if (isMenuOpen) closeMenu();
      else openMenu();
    });



function isAnyOverlayOpen() {
  const wifiModal = document.getElementById("wifi-modal");
  const checkinModal = document.getElementById("checkin-modal");
  const guideModal = document.getElementById("guide-modal");
  const drawer = document.getElementById("suggestions-drawer");

  const open = (el) => el && !el.classList.contains("hidden");

  return (
    open(wifiModal) ||
    open(checkinModal) ||
    open(guideModal) ||
    open(drawer) ||
    document.body.classList.contains("menu-open") ||
    document.body.classList.contains("modal-open") ||
    document.body.classList.contains("guide-open")
  );
}



/**
 * pushTranscript(sender, text, variant, meta?)
 * Returns the saved entry (source of truth).
 */
function pushTranscript(sender, text, variant = "normal", meta = {}) {
  const entry = {
    id: meta.id || newMid(sender === "user" ? "u" : "b"),
    thread_id: meta.thread_id ?? currentThreadId ?? null,
    parent_id: meta.parent_id ?? null,
    sender: sender === "user" ? "user" : "bot",
    variant: variant || "normal",
    text: String(text || ""),
    ts: meta.ts ?? Date.now(),
  };

  transcript = Array.isArray(transcript) ? transcript : [];
  transcript.push(entry);
  saveTranscript();
  return entry;
}

    
function autoActOnIntent(intent, userText) {
  try {
    if (!userExplicitlyWantsOpen(userText, intent)) return;
    if (isAnyOverlayOpen()) return; // ✅ don’t “take over” while something is open

    switch (intent) {
      case "wifi":
        setTimeout(() => { if (isChatVisible()) openWifiModal(); }, 250);
        break;

      case "door":
      case "checkin":
        setTimeout(() => { if (isChatVisible()) openCheckinModal(); }, 250);
        break;

      case "checkout":
        setTimeout(() => { if (isChatVisible()) showScreen("upgrades"); }, 250);
        break;

      case "things_to_do":
      case "food":
        setTimeout(() => { if (isChatVisible()) showScreen("experiences"); }, 250);
        break;

      default:
        break;
    }
  } catch {}
}


    


    
    function scrollChatToBottom() {
      if (!isChatVisible()) return;

      requestAnimationFrame(() => {
        const docEl = document.documentElement;
        const body = document.body;

        const fullHeight = Math.max(
          docEl.scrollHeight,
          body.scrollHeight,
          docEl.offsetHeight,
          body.offsetHeight
        );

        window.scrollTo({ top: fullHeight, behavior: "auto" });
      });
    }

    function formatTime(date) {
      let hours = date.getHours();
      const minutes = date.getMinutes().toString().padStart(2, "0");
      const ampm = hours >= 12 ? "PM" : "AM";
      hours = hours % 12;
      if (hours === 0) hours = 12;
      return `${hours.toString().padStart(2, "0")}:${minutes} ${ampm}`;
    }

    function formatMessage(text) {
      const raw = String(text || "");
    
      // 1) Pre-process: replace Google Maps URLs with a friendly link label
      const withMapLinks = raw.replace(
        /(https:\/\/www\.google\.com\/maps[^\s]+)/g,
        `[📍 View on Google Maps]($1)`
      );
    
      // 2) Markdown -> HTML (ChatGPT-like formatting)
      const html = marked.parse(withMapLinks, {
        breaks: true,   // convert single newlines to <br>
        gfm: true
      });
    
      // 3) Sanitize the HTML for safety
      const clean = DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        FORBID_TAGS: ["script", "iframe", "object", "embed"],
        FORBID_ATTR: ["onerror", "onclick", "onload", "style"],
        ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|\/|#)/i,
      });
    
      // 4) Ensure links open in new tab safely (marked doesn't always add these)
      const tmp = document.createElement("div");
      tmp.innerHTML = clean;
      tmp.querySelectorAll("a").forEach((a) => {
        const href = (a.getAttribute("href") || "").trim();
        if (href && !href.startsWith("#")) {
          a.target = "_blank";
          a.rel = "noopener noreferrer";
        }
      });

      return tmp.innerHTML;
    
      //return withSafeTargets;
    }


function renderMessage(text, sender, opts = {}) {
  if (!chatBox) return null;

  const {
    skipSave = false,
    variant = "normal",
    id = null,
    thread_id = currentThreadId || null,
    parent_id = null,
    ts = null,
  } = opts;

  const isUser = sender === "user";
  const mid = id || newMid(isUser ? "u" : "b");
  const timeObj = ts ? new Date(ts) : new Date();
  const timeLabel = formatTime(timeObj);

  const row = document.createElement("div");
  row.className = "msg " + (isUser ? "user" : "bot");
  row.dataset.mid = mid;
  row.dataset.sender = isUser ? "user" : "bot";
  row.dataset.variant = variant || "normal";
  if (thread_id) row.dataset.threadId = thread_id;
  if (parent_id) row.dataset.parentId = parent_id;
  row.dataset.ts = String(ts || Date.now());

  let avatar = null;
  if (!isUser) {
    avatar = document.createElement("div");
    avatar.className = "avatar avatar-sandy";
  }

  const column = document.createElement("div");

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.dataset.mid = mid;
  if (thread_id) bubble.dataset.threadId = thread_id;
  if (parent_id) bubble.dataset.parentId = parent_id;

  if (variant === "system") {
    bubble.style.background = "#e2e8f0";
    bubble.style.color = "#0f172a";
  }

  bubble.innerHTML = `<div class="message-text">${formatMessage(text)}</div>`;

  const time = document.createElement("div");
  time.className = "timestamp";
  time.textContent = timeLabel;

  column.appendChild(time);
  column.appendChild(bubble);

  if (isUser) {
    row.appendChild(column);
  } else {
    row.appendChild(avatar);
    row.appendChild(column);
  }

  // 🔹 Reactions (bot only, not system)
  if (!isUser && variant !== "system") {
    bubble.style.cursor = "pointer";

    bubble.addEventListener("click", () => {
      const mid = row.dataset.mid;
      if (!mid) return;

      if (column.querySelector(".reaction-row")) return;

      if (reactionsById[mid]) {
        renderSystemNote("Already saved your feedback — thank you!", mid);
        return;
      }

      const reactions = document.createElement("div");
      reactions.className = "reaction-row flex gap-2 mt-2";

      function reactionButton(label, reactionValue, responseText) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "text-xs px-3 py-1 rounded-full bg-white border border-slate-200 text-slate-700 hover:bg-slate-100 active:scale-95 transition";
        btn.textContent = label;

        btn.addEventListener("click", () => {
          reactionsById[mid] = {
            reaction: reactionValue,
            ts: Date.now(),
          };
          saveReactions(reactionsById);

          emitAnalytics("reaction_set", {
            thread_id: row.dataset.threadId || currentThreadId || null,
            message_id: mid,
            value: reactionValue,     // ✅ admin SQL expects data->>'value'
            reaction: reactionValue,  // optional (keep for debugging)
          });


          reactions.remove();
          renderSystemNote(responseText, mid);
        });

        return btn;
      }

      reactions.appendChild(reactionButton("👍 Helpful", "up", "Glad that helped! 😊"));
      reactions.appendChild(
        reactionButton("👎 Not quite", "down", "Got it — tell me what you were hoping for instead.")
      );

      column.appendChild(reactions);
      scrollChatToBottom();
    });
  }

  chatBox.appendChild(row);
  scrollChatToBottom();

  // Optional persistence path (try to avoid using this; prefer commit helpers)
  if (!skipSave) {
    const entry = pushTranscript(sender, text, variant, {
      id: mid,
      thread_id,
      parent_id,
      ts: ts || Date.now(),
    });

    emitAnalytics("message_sent", {
      sender: entry.sender,
      message_id: entry.id,
      parent_id: entry.parent_id,
      thread_id: entry.thread_id,
      variant: entry.variant,
      length: entry.text.length,
    });
  }

  return { row, bubble, id: mid };
}


   

    function removeTyping() {
      document.getElementById("typing-indicator")?.remove();
    }

    function getSelectedLanguage() {
      return "auto";
    }

    async function readJsonSafely(res) {
      const contentType = (res.headers.get("content-type") || "").toLowerCase();
      const text = await res.text(); // read response once
    
      if (contentType.includes("application/json")) {
        try {
          return JSON.parse(text);
        } catch (e) {
          return { error: "Invalid JSON response from server." };
        }
      }
    
      // Fallback for HTML / text / streaming responses
      return {
        error: `Server returned ${res.status} (${res.statusText})`,
        raw: text.slice(0, 300),
      };
    }


    

 async function sendChat() {
  if (chatInFlight) return;

  const text = (chatInput?.value || "").trim();
  if (!text) return;

  chatInFlight = true;
  lastUserMessage = text;
  lastUserIntent = detectIntent(text);

  function endChatFlight() {
    chatInFlight = false;
    if (chatInput) chatInput.disabled = false;
    if (chatSend) chatSend.disabled = false;
  }

  // --- commit helpers (single source of truth) ---
  function commitUserMessage(userText, variant = "normal") {
    const parent_id = nextUserParentId || null;
    nextUserParentId = null; // consume once

    const entry = pushTranscript("user", userText, variant, {
      id: newMid("u"),
      thread_id: currentThreadId || null,
      parent_id,
      ts: Date.now(),
    });

    renderMessage(userText, "user", {
      skipSave: true,
      id: entry.id,
      thread_id: entry.thread_id,
      parent_id: entry.parent_id,
      variant: entry.variant,
      ts: entry.ts,
    });

    emitAnalytics("message_sent", {
      sender: "user",
      message_id: entry.id,
      parent_id: entry.parent_id,
      thread_id: entry.thread_id,
      variant: entry.variant,
      length: entry.text.length,
    });

    return entry;
  }

  async function commitBotMessage(botText, variant = "normal", parent_id = null, { typewriter = true } = {}) {
    const entry = pushTranscript("bot", botText, variant, {
      id: newMid("b"),
      thread_id: currentThreadId || null,
      parent_id,
      ts: Date.now(),
    });

    emitAnalytics("message_sent", {
      sender: "bot",
      message_id: entry.id,
      parent_id: entry.parent_id,
      thread_id: entry.thread_id,
      variant: entry.variant,
      length: entry.text.length,
    });

    if (typewriter) {
      const bubble = createBotBubble({
        id: entry.id,
        thread_id: entry.thread_id,
        parent_id: entry.parent_id,
        variant: entry.variant,
        ts: entry.ts,
      });

      if (bubble?.body) {
        await typewriterTo(bubble.body, entry.text, { speed: 12 });
      } else {
        renderMessage(entry.text, "bot", {
          skipSave: true,
          id: entry.id,
          thread_id: entry.thread_id,
          parent_id: entry.parent_id,
          variant: entry.variant,
          ts: entry.ts,
        });
      }
    } else {
      renderMessage(entry.text, "bot", {
        skipSave: true,
        id: entry.id,
        thread_id: entry.thread_id,
        parent_id: entry.parent_id,
        variant: entry.variant,
        ts: entry.ts,
      });
    }

    return entry;
  }

  // --- Slash commands ---
  const t = text.toLowerCase();

  if (t === "/wifi") {
    const u = commitUserMessage(text);
    if (chatInput) chatInput.value = "";
    openWifiModal();
    renderSystemNote("Opening WiFi details.", u.id);
    endChatFlight();
    return;
  }

  if (t === "/door" || t === "/code") {
    const u = commitUserMessage(text);
    if (chatInput) chatInput.value = "";
    openCheckinModal();
    renderSystemNote("Opening your door code.", u.id);
    endChatFlight();
    return;
  }

  if (t === "/guides") {
    const u = commitUserMessage(text);
    if (chatInput) chatInput.value = "";
    showScreen("experiences");
    renderSystemNote("Opening Guides.", u.id);
    endChatFlight();
    return;
  }

  if (t === "/upgrades") {
    const u = commitUserMessage(text);
    if (chatInput) chatInput.value = "";
    showScreen("upgrades");
    renderSystemNote("Opening Upgrades.", u.id);
    endChatFlight();
    return;
  }

  if (!sandyLive) {
    const u = commitUserMessage(text);
    if (chatInput) chatInput.value = "";
    await commitBotMessage(
      "Sandy is currently offline for this property. Please contact your host directly. 🙏",
      "normal",
      u.id,
      { typewriter: true }
    );
    endChatFlight();
    return;
  }

  // ✅ Commit user message once
  const userEntry = commitUserMessage(text);

  if (chatInput) chatInput.disabled = true;
  if (chatSend) chatSend.disabled = true;

  haptic(12);
  maybeUpdateMemoryFromUserText(text);

  if (chatInput) chatInput.value = "";
  addTyping(getThinkingLabel(text));

  try {
    const body = {
      message: text,
      session_id: currentSessionId || null,
      language: getSelectedLanguage(),

      thread_id: currentThreadId || null,
      client_message_id: userEntry.id,      // user message id you generated
      parent_id: userEntry.parent_id || null // optional: if user clicked a chip
    };

    try {
      chatSend?.classList.add("scale-90");
      setTimeout(() => chatSend?.classList.remove("scale-90"), 120);
    } catch {}

    const res = await fetch(`/properties/${window.PROPERTY_ID}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });

    const data = await readJsonSafely(res);
    removeTyping();
    
    // 🔎 Debug guard: ensure server replied to the expected user message
    if (data.reply_to && data.reply_to !== userEntry.id) {
      console.warn("Server replied to unexpected message id", {
        expected: userEntry.id,
        got: data.reply_to,
        thread_id: currentThreadId || null,
        session_id: currentSessionId || null,
      });
    }


    if (!res.ok) {
      await commitBotMessage(
        data?.error || data?.detail || voiceCfg.error_message || "Something went wrong. Please try again.",
        "normal",
        userEntry.id
      );
      return;
    }

   // ✅ keep client thread stable; store server session separately
    if (data.session_id != null) {                 // handles 0 too
      currentSessionId = data.session_id;
      saveGuestState();
    
      try {
        localStorage.setItem(
          `server_session_${window.PROPERTY_ID}`,
          String(data.session_id)
        );
      } catch {}
    }
    
    // ✅ if/when backend starts returning thread_id, prefer it
    if (data.thread_id) {
      currentThreadId = data.thread_id;
      try { localStorage.setItem(THREAD_KEY, currentThreadId); } catch {}
    }



    if (data.error) {
      renderErrorWithActions(String(data.error), { parent_id: userEntry.id });
      return;
    }

    const rawBotText = data.response || voiceCfg.fallback_message || "Could you try that again?";
    lastBotIntent = detectIntent(`${text}\n${rawBotText}`);

    let finalBotText = rawBotText;
    finalBotText = applyMemoryToFinalBotText(finalBotText, lastBotIntent);
    finalBotText = withAcknowledgement(finalBotText, lastBotIntent);

    // ✅ bot response parented to user msg
    const botEntry = await commitBotMessage(finalBotText, "normal", userEntry.id, { typewriter: true });

    autoActOnIntent(lastBotIntent, text);

    const followups =
      Array.isArray(data.suggestions) && data.suggestions.length
        ? data.suggestions
        : buildFollowups(text, finalBotText);

    // ✅ chips parented to bot msg
    renderFollowupChips(followups, { parent_id: botEntry.id });
    renderCardsFromText(finalBotText);
  } catch (err) {
    console.error(err);
    removeTyping();
    renderErrorWithActions(voiceCfg.error_message || "Oops! Something went wrong. Please try again.", {
      parent_id: userEntry.id,
    });
  } finally {
    endChatFlight();
  }
}


if (chatSend && chatInput) {
  chatSend.addEventListener("click", sendChat);
  chatInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
}




function loadReactions() {
  try {
    return JSON.parse(localStorage.getItem(REACTIONS_KEY) || "{}");
  } catch {
    return {};
  }
}

  
    function renderSoftSuggestion(intent) {
  if (!chatBox) return;

  // prevent stacking multiple soft suggestions in a row
  if (chatBox.querySelector("[data-soft-suggest='1']")) return;

  const wrap = document.createElement("div");
  wrap.dataset.softSuggest = "1";
  wrap.className = "flex gap-2 flex-wrap ml-[52px] mt-2 mb-4";

  function btn(label, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className =
      "px-3 py-2 rounded-full bg-white border border-slate-200 text-slate-900 text-sm font-semibold whitespace-nowrap active:scale-95";
    b.textContent = label;
    b.addEventListener("click", () => {
      wrap.remove();
      onClick();
    });
    return b;
  }

  // choose suggestion based on intent
  if (intent === "wifi") {
    wrap.appendChild(btn("Open WiFi", openWifiModal));
    wrap.appendChild(btn("Copy password", () => runFollowupAction({ action: "COPY_WIFI_PASSWORD" })));
  } else if (intent === "door" || intent === "checkin") {
    wrap.appendChild(btn("Open Door Code", openCheckinModal));
  } else if (intent === "things_to_do" || intent === "food") {
    wrap.appendChild(btn("Open Guides", () => showScreen("experiences")));
  } else if (intent === "checkout") {
    wrap.appendChild(btn("See Upgrades", () => showScreen("upgrades")));
  } else {
    return; // no suggestion for general
  }

  chatBox.appendChild(wrap);
  scrollChatToBottom();

  // auto-remove after a bit so it doesn't linger forever
  setTimeout(() => {
    try { wrap.remove(); } catch {}
  }, 12000);
}



    

    // --- Quick replies + suggestions drawer ---
    const quickBar = document.getElementById("quick-replies-bar");
    const quickTrack = document.getElementById("quick-replies-track");
    const drawer = document.getElementById("suggestions-drawer");
    const drawerList = document.getElementById("suggestions-list");
    const drawerClose = document.getElementById("suggestions-close");

    function renderQuickReplies() {
      if (!quickTrack) return;
      quickTrack.innerHTML = "";

      quickReplies.slice(0, 10).forEach((label) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "px-3 py-2 rounded-full bg-white border border-slate-200 text-slate-900 text-sm font-semibold whitespace-nowrap";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          if (!chatInput) return;
          chatInput.value = label;
          sendChat();
        });
        quickTrack.appendChild(btn);
      });
    }

    function showQuickReplies(show) {
      if (!quickBar) return;

      if (!show || !quickReplies.length) {
        quickBar.classList.add("hidden");
        return;
      }

      renderQuickReplies();
      quickBar.classList.remove("hidden");
    }

    function openSuggestions() {
      if (!drawer || !drawerList) return;

      drawerList.innerHTML = "";
      quickReplies.forEach((label) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className =
          "w-full text-left px-4 py-3 rounded-2xl bg-slate-50 border border-slate-200 font-semibold text-slate-900";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          closeSuggestions();
          if (!chatInput) return;
          chatInput.value = label;
          sendChat();
        });
        drawerList.appendChild(btn);
      });

      drawer.classList.remove("hidden");
    }

    function closeSuggestions() {
      drawer?.classList.add("hidden");
    }

    drawer?.addEventListener("click", (e) => {
      if (e.target === drawer) closeSuggestions();
    });
    drawerClose?.addEventListener("click", closeSuggestions);
    chatPlus?.addEventListener("click", openSuggestions);

    // --- Guides (same logic as you had) ---
    function createGuideCard(guide, index) {
      let minHeight;
      if (index % 4 === 0) minHeight = "320px";
      else if (index % 2 === 0) minHeight = "240px";
      else minHeight = "180px";

      const card = document.createElement("button");
      card.type = "button";
      card.className =
        "masonry-item group relative overflow-hidden rounded-[24px] bg-slate-200 shadow-sm " +
        "focus:outline-none focus:ring-2 focus:ring-slate-900 transition-transform active:scale-95 text-left w-full";
      card.style.minHeight = minHeight;

      const imageUrl =
        guide.image_url ||
        window.EXPERIENCES_HERO_URL ||
        window.HERO_IMAGE_URL ||
        window.DEFAULT_IMAGE_URL ||
        "/static/img/default-hero.jpg";


      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = guide.title || "Guide";
      img.className =
        "absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]";
      card.appendChild(img);

      const overlay = document.createElement("div");
      overlay.className =
        "absolute inset-0 bg-gradient-to-t from-black/60 via-black/10 to-transparent";
      card.appendChild(overlay);

      const textWrap = document.createElement("div");
      textWrap.className = "absolute inset-x-3 bottom-3 text-left text-white space-y-1";
      card.appendChild(textWrap);

      const titleEl = document.createElement("h2");
      titleEl.className = "text-[13px] font-semibold leading-tight line-clamp-2";
      titleEl.textContent = guide.title || "Guide";
      textWrap.appendChild(titleEl);

      if (guide.category) {
        const catEl = document.createElement("p");
        catEl.className = "text-[10px] opacity-80";
        catEl.textContent = guide.category;
        textWrap.appendChild(catEl);
      }

      card.addEventListener("click", () => openGuideModalFromGuide(guide));
      return card;
    }

    function openGuideModalFromGuide(guide) {
      if (!guideModal) return;
      closeMenu();

      if (guideModalTitle) guideModalTitle.textContent = guide.title || "Guide";

      if (guideModalBody) {
        if (guide.body_html) {
          guideModalBody.innerHTML = DOMPurify.sanitize(guide.body_html, {
            USE_PROFILES: { html: true },
            FORBID_TAGS: ["script", "iframe", "object", "embed"],
            FORBID_ATTR: ["onerror", "onclick", "onload"],
          });
          guideModalBody.classList.remove("whitespace-pre-line");
        } else if (guide.long_description) {
          guideModalBody.textContent = guide.long_description;
          guideModalBody.classList.add("whitespace-pre-line");
        } else if (guide.short_description) {
          guideModalBody.textContent = guide.short_description;
          guideModalBody.classList.add("whitespace-pre-line");
        } else {
          guideModalBody.textContent = "";
          guideModalBody.classList.add("whitespace-pre-line");
        }
      }

      guideModal.classList.remove("hidden");
      guideModal.classList.add("flex");
      document.body.classList.add("overflow-hidden", "guide-open");
      document.documentElement.classList.add("overflow-hidden");

      const scroller = guideModal.querySelector(".overflow-y-auto");
      if (scroller) scroller.scrollTop = 0;
    }

    function closeGuideModal() {
      if (!guideModal) return;
      guideModal.classList.add("hidden");
      guideModal.classList.remove("flex");
      document.body.classList.remove("overflow-hidden", "guide-open");
      document.documentElement.classList.remove("overflow-hidden");
    }

    guideModalClose?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeGuideModal();
    });

    guideModal?.addEventListener("click", (e) => {
      if (e.target === guideModal) closeGuideModal();
    });

    function buildGuideFiltersFromGuides(guides) {
      if (!guidesFiltersContainer) return;

      const categories = [
        ...new Set(
          guides
            .map((g) => g.category)
            .filter((c) => typeof c === "string" && c.trim() !== "")
        ),
      ];

      guidesFiltersContainer.innerHTML = "";

      categories.forEach((category) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.filter = category;
        btn.textContent = category;
        btn.className =
          "guide-filter whitespace-nowrap px-3 py-1.5 rounded-full border border-slate-300 bg-white text-slate-900 text-[11px]";
        btn.addEventListener("click", () => {
          guidesState.activeFilter = category;
          updateGuideFilterStates();
          renderGuidesGrid();
        });
        guidesFiltersContainer.appendChild(btn);
      });

      const allBtn = document.querySelector('button.guide-filter[data-filter="all"]');
      allBtn?.addEventListener("click", () => {
        guidesState.activeFilter = "all";
        updateGuideFilterStates();
        renderGuidesGrid();
      });

      updateGuideFilterStates();
    }

    function updateGuideFilterStates() {
      const buttons = document.querySelectorAll(".guide-filter");
      const active = guidesState.activeFilter;

      buttons.forEach((btn) => {
        const filter = btn.dataset.filter;
        const isActive = active === "all" ? filter === "all" : filter === active;

        if (isActive) {
          btn.classList.add("bg-slate-900", "text-white", "border-slate-900");
          btn.classList.remove("bg-white", "text-slate-900", "border-slate-300");
        } else if (filter !== "all") {
          btn.classList.add("bg-white", "text-slate-900", "border-slate-300");
          btn.classList.remove("bg-slate-900", "text-white", "border-slate-900");
        }
      });
    }

    function getMasonryColumnCount() {
      return window.matchMedia("(min-width: 768px)").matches ? 3 : 2;
    }

    function renderGuidesGrid() {
      if (!guidesGrid) return;

      guidesGrid.innerHTML = "";

      const filter = guidesState.activeFilter;
      const visible =
        filter === "all"
          ? guidesState.guides
          : guidesState.guides.filter((g) => g.category === filter);

      if (!visible.length) {
        guidesEmptyState?.classList.remove("hidden");
        return;
      }
      guidesEmptyState?.classList.add("hidden");

      const colCount = getMasonryColumnCount();
      const cols = [];

      for (let i = 0; i < colCount; i++) {
        const col = document.createElement("div");
        col.className = "masonry-col";
        guidesGrid.appendChild(col);
        cols.push(col);
      }

      visible.forEach((guide, index) => {
        const card = createGuideCard(guide, index);
        cols[index % cols.length].appendChild(card);
      });
    }

    window.addEventListener("resize", () => {
      if (screens?.experiences && !screens.experiences.classList.contains("hidden")) {
        renderGuidesGrid();
      }
    });

    async function loadGuidesIfNeeded() {
      if (guidesState.loaded || guidesState.loading) return;

      guidesState.loading = true;

      if (guidesAbortController) guidesAbortController.abort();
      guidesAbortController = new AbortController();

      try {
        guidesEmptyState?.classList.add("hidden");
        if (guidesEmptyState) {
          guidesEmptyState.textContent =
            "We don’t have any guides yet for this stay. Check back soon or ask Sandy for local tips.";
        }

        const res = await fetch(`/properties/${window.PROPERTY_ID}/guides`, {
          signal: guidesAbortController.signal,
          headers: { Accept: "application/json" },
          cache: "no-store",
          credentials: "include",
        });

        if (!res.ok) throw new Error(`Failed to load guides (${res.status})`);

        const data = await res.json();
        const guides = Array.isArray(data?.guides) ? data.guides : [];

        guidesState.guides = guides;
        guidesState.loaded = true;

        if (!guides.length) {
          if (guidesGrid) guidesGrid.innerHTML = "";
          guidesEmptyState?.classList.remove("hidden");
          return;
        }

        buildGuideFiltersFromGuides(guides);
        renderGuidesGrid();
      } catch (err) {
        if (err?.name === "AbortError") return;
        console.error("Error loading guides:", err);

        if (guidesGrid) guidesGrid.innerHTML = "";
        if (guidesEmptyState) {
          guidesEmptyState.textContent = "We couldn’t load guides right now. Please try again in a moment.";
          guidesEmptyState.classList.remove("hidden");
        }

        guidesState.loaded = false;
      } finally {
        guidesState.loading = false;
      }
    }


    

 function safeReplaceAll(str, needle, replacement) {
  str = String(str ?? "");
  needle = String(needle ?? "");
  replacement = String(replacement ?? "");
  if (!needle) return str; // ✅ critical: prevents replaceAll("")
  return str.split(needle).join(replacement);
}

 function renderWelcomeTemplate(template, vars) {
  const open = "{" + "{";
  const close = "}" + "}";
  const keys = ["guest_name", "assistant_name", "property_name"];

  let out = String(template || "");

  keys.forEach((k) => {
    const v = String(vars?.[k] ?? "");

    // Replace common spacing variants:
    const variants = [
      open + k + close,               // {{guest_name}}
      open + " " + k + close,         // {{ guest_name}}
      open + k + " " + close,         // {{guest_name }}
      open + " " + k + " " + close,   // {{ guest_name }}
    ];

    variants.forEach((needle) => {
      out = safeReplaceAll(out, needle, v);
    });
  });

  return out;
}








    // --- Screen router ---
   function showScreen(name) {
  const sandyHeader = document.getElementById("chat-header-sandy");
  if (sandyHeader) sandyHeader.classList.toggle("hidden", name !== "chat");

  Object.entries(screens).forEach(([key, el]) => {
    if (!el) return;
    el.classList.toggle("hidden", key !== name);
  });

  document.body.classList.toggle("chat-screen", name === "chat");

  if (name === "experiences") loadGuidesIfNeeded();

if (name === "upgrades") {
  initUpgradesCarousel();
  requestAnimationFrame(updateActiveFromScroll);
  syncPaidUpgradesFromServer();
  refreshUpgradeEligibility(); // ✅ add
}



  if (name === "chat") swapLogoWithFade(LOGO_BLACK);
  else if (!isMenuOpen) swapLogoWithFade(LOGO_WHITE);

  showQuickReplies(name === "chat");

  if (name === "chat") {
    // ✅ hard guard: once per page load (and per property)
    // ✅ count a chat session once per page load (per property)
      const chatSessionKey = `chat_session_created_${window.PROPERTY_ID}`;
      if (isUnlocked && !sessionStorage.getItem(chatSessionKey)) {
        sessionStorage.setItem(chatSessionKey, "1");
        emitAnalytics("chat_session_created", {
          thread_id: currentThreadId || null,
          session_id: currentSessionId || null,
        });
      }

    if (!chatRestored) {
      restoreChatHistory();
      chatRestored = true;
    }
    const welcomeKey = `welcome_shown_${window.PROPERTY_ID}`;
maybeSendTimeNudge();
    
    if (!chatHasWelcome && !sessionStorage.getItem(welcomeKey)) {
      if (sandyLive && (!transcript || transcript.length === 0)) {
        const template = guestName
          ? (voiceCfg.welcome_template || "")
          : (voiceCfg.welcome_template_no_name || "");

        const intro = renderWelcomeTemplate(template, {
          guest_name: guestName || "",
          assistant_name: assistantName || "Sandy",
          property_name: window.PROPERTY_NAME || "",
        });


        const fallback = `Hi! I’m ${assistantName || "Sandy"}, your stay assistant for ${window.PROPERTY_NAME || "your stay"}. How can I help?`;

        // Persist welcome + render once
const welcomeEntry = pushTranscript("bot", (intro.trim() || fallback), "normal", {
  id: newMid("b"),
  thread_id: currentThreadId || null,
  parent_id: null,
  ts: Date.now(),
});
renderMessage(welcomeEntry.text, "bot", {
  skipSave: true,
  id: welcomeEntry.id,
  thread_id: welcomeEntry.thread_id,
  parent_id: welcomeEntry.parent_id,
  variant: welcomeEntry.variant,
  ts: welcomeEntry.ts,
});
emitAnalytics("message_sent", {
  sender: "bot",
  message_id: welcomeEntry.id,
  parent_id: null,
  thread_id: welcomeEntry.thread_id,
  variant: welcomeEntry.variant,
  length: welcomeEntry.text.length,
});


        chatHasWelcome = true;
        sessionStorage.setItem(welcomeKey, "1");
      }
    }

    scrollChatToBottom();
  }
}

    // --- Menu screen links ---
    menuLinks.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.getAttribute("data-menu-screen");
        if (!target) return;

        if (!isUnlocked && target !== "home") {
          const err = document.getElementById("unlock-error");
          if (err)
            err.textContent =
              "Please unlock your stay first with the last 4 digits of your booking phone number.";
          showScreen("home");
        } else {
          showScreen(target);
        }

        closeMenu();
      });
    });

    // --- Initial render ---
    updateHomeState();
    showScreen("home");

    // --- Unlock flow ---
    const unlockBtn = document.getElementById("unlock-button");
    const unlockCodeInput = document.getElementById("unlock-code");
    const unlockError = document.getElementById("unlock-error");
    const unlockSpinner = document.getElementById("unlock-spinner");

    // --- Unlock / Verify (clean + hardened) ---

let unlockInFlight = false;

async function attemptUnlock() {
  if (unlockInFlight) return; // prevent double-submit
  unlockInFlight = true;

  const code = String(unlockCodeInput?.value || "").trim();

  if (unlockError) unlockError.textContent = "";

  // Validate property id early (prevents /guest/undefined/verify-json)
  const propertyId = window.PROPERTY_ID;
  if (!propertyId && propertyId !== 0) {
    if (unlockError) unlockError.textContent = "Missing property id. Please refresh the page.";
    unlockInFlight = false;
    return;
  }

  // Validate 4 digits
  if (!/^\d{4}$/.test(code)) {
    if (unlockError) unlockError.textContent = "Please enter exactly 4 digits.";
    unlockInFlight = false;
    return;
  }

  if (unlockBtn) unlockBtn.disabled = true;
  unlockSpinner?.classList.remove("hidden");

  try {
    const res = await fetch(`/guest/${propertyId}/verify-json`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ code }),
    });

    // Be robust if backend returns non-JSON on error
    let data = {};
    try {
      data = await res.json();
    } catch {
      data = {};
    }

    // Handle failure (HTTP or logical)
    if (!res.ok || !data.success) {
      const fallback =
        res.status === 401
          ? "Please refresh and try again."
          : res.status === 403
          ? "That code didn’t match the reservation phone number."
          : "We couldn’t verify that code. Please double-check or contact your host.";

      if (unlockError) unlockError.textContent = data.error || fallback;
      return;
    }

    // Success
    isUnlocked = true;
    lastUnlockCode = code;

    // ✅ bind server session immediately after verify
    if (data.session_id != null) {
      currentSessionId = data.session_id;
    
      try {
        localStorage.setItem(`server_session_${window.PROPERTY_ID}`, String(data.session_id));
      } catch {}
    }


    if (unlockError) unlockError.textContent = "";
    if (unlockCodeInput) unlockCodeInput.value = "";

    // Apply PMS / guest info safely
    try {
      if (data.guest_name) {
        guestName = data.guest_name;
        updateGuestNameInUI(guestName);
      }

      if (data.arrival_date) {
        arrivalDate = data.arrival_date;
        const el = document.getElementById("arrival-date-span");
        if (el) el.textContent = formatDateLong(arrivalDate);
      }

      if (data.departure_date) {
        departureDate = data.departure_date;
        const el = document.getElementById("departure-date-span");
        if (el) el.textContent = formatDateLong(departureDate);
      }

      if (data.checkin_time) {
        checkinTime = data.checkin_time;
        const el = document.getElementById("checkin-time-span");
        if (el) el.textContent = checkinTime;
      }

      if (data.checkout_time) {
        checkoutTime = data.checkout_time;
        const el = document.getElementById("checkout-time-span");
        if (el) el.textContent = checkoutTime;
      }
        } catch (e) {
          console.warn("Error applying guest info to UI:", e);
        }

        await syncPaidUpgradesFromServer();
    refreshUpgradeEligibility(); // ✅ add this

        saveGuestState();
        updateHomeState();
        showScreen("home");
      } catch (err) {
        console.error("Verify error:", err);
        if (unlockError) unlockError.textContent = "Something went wrong while verifying. Please try again.";
      } finally {
        if (unlockBtn) unlockBtn.disabled = false;
        unlockSpinner?.classList.add("hidden");
        unlockInFlight = false;
      }
    }
    
    // Click to unlock
    unlockBtn?.addEventListener("click", (e) => {
      e.preventDefault();
      attemptUnlock();
    });
    
    // Enter key to unlock
    unlockCodeInput?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        attemptUnlock();
      }
    });


    // --- Modals: Door + WiFi ---
    const checkinButton = document.getElementById("checkin-button");
    const checkinModal = document.getElementById("checkin-modal");
    const checkinClose = document.getElementById("checkin-close");
    const checkinCodeEl = document.getElementById("checkin-code");

    const wifiButton = document.getElementById("wifi-button");
    const wifiModal = document.getElementById("wifi-modal");
    const wifiClose = document.getElementById("wifi-close");

    function openWifiModal() {
      if (!wifiModal) return;
      closeMenu();

      wifiModal.classList.remove("hidden");
      wifiModal.classList.add("flex");

      document.body.classList.add("overflow-hidden", "modal-open");
      document.documentElement.classList.add("overflow-hidden");
    }

    function closeWifiModal() {
      if (!wifiModal) return;

      wifiModal.classList.add("hidden");
      wifiModal.classList.remove("flex");

      document.body.classList.remove("overflow-hidden", "modal-open");
      document.documentElement.classList.remove("overflow-hidden");
    }

    wifiButton?.addEventListener("click", (e) => {
      e.preventDefault();
      openWifiModal();
    });

    wifiClose?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeWifiModal();
    });

    wifiModal?.addEventListener("click", (e) => {
      if (e.target === wifiModal) closeWifiModal();
    });

    function openCheckinModal() {
      if (!checkinModal) return;
      closeMenu();

      const code = lastUnlockCode || "----";
      if (checkinCodeEl) checkinCodeEl.textContent = code;

      checkinModal.classList.remove("hidden");
      checkinModal.classList.add("flex");

      document.body.classList.add("overflow-hidden", "modal-open");
      document.documentElement.classList.add("overflow-hidden");
    }

    function closeCheckinModal() {
      if (!checkinModal) return;

      checkinModal.classList.add("hidden");
      checkinModal.classList.remove("flex");

      document.body.classList.remove("overflow-hidden", "modal-open");
      document.documentElement.classList.remove("overflow-hidden");
    }

    checkinButton?.addEventListener("click", (e) => {
      e.preventDefault();
      openCheckinModal();
    });

    checkinClose?.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeCheckinModal();
    });

    checkinModal?.addEventListener("click", (e) => {
      if (e.target === checkinModal) closeCheckinModal();
    });

    // --- Escape closes modals ---
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;

      if (wifiModal && !wifiModal.classList.contains("hidden")) closeWifiModal();
      if (checkinModal && !checkinModal.classList.contains("hidden")) closeCheckinModal();
      if (guideModal && !guideModal.classList.contains("hidden")) closeGuideModal();
      if (drawer && !drawer.classList.contains("hidden")) closeSuggestions();
      if (isMenuOpen) closeMenu();
    });

    // --- If offline, show message + disable chat UI ---
    if (!sandyLive) {
      if (chatBox) {
        renderMessage(
          voiceCfg.offline_message ||
            "I’m currently offline for this property 🌙\n\nFor urgent questions, please contact your host directly.",
          "bot"
        );
      }
      if (chatInput) {
        chatInput.disabled = true;
        chatInput.placeholder = `${assistantName} is offline for this property.`;
      }
      if (chatSend) chatSend.disabled = true;
      if (chatPlus) chatPlus.disabled = true;
    }

    
const upgradesCarousel = document.getElementById("upgrades-carousel");
const upgradeSlides = Array.from(document.querySelectorAll(".upgrade-slide"));
const upgradeActiveButton = document.getElementById("upgrade-active-button");



// ===============================
// Upgrade Status Router (ONE output)
// ===============================
const STATUS_SOURCES = {
  STRIPE: "stripe",
  PAID: "paid",
  ELIGIBILITY: "eligibility",
  INFO: "info",
};

const STATUS_PRIORITY = {
  [STATUS_SOURCES.STRIPE]: 300,
  [STATUS_SOURCES.PAID]: 250,
  [STATUS_SOURCES.ELIGIBILITY]: 100,
  [STATUS_SOURCES.INFO]: 10,
};

let statusState = {
  entries: new Map(), // source -> { text, cls, ts, sticky, upgradeId }
};

function getStatusEl() {
  return document.getElementById("upgrade-active-status"); // orange line
}

function getActiveUpgradeIdStr() {
  return activeUpgradeId != null ? String(activeUpgradeId) : null;
}

function setUpgradeStatus({ source, text = "", cls = "", sticky = false, upgradeId = null } = {}) {
  if (!source) return;

  statusState.entries.set(source, {
    text: String(text || ""),
    cls: String(cls || ""),
    ts: Date.now(),
    sticky: !!sticky,
    upgradeId: upgradeId != null ? String(upgradeId) : null,
  });

  renderUpgradeStatus();
}

function clearUpgradeStatus(source) {
  if (!source) return;
  statusState.entries.delete(source);
  renderUpgradeStatus();
}

function clearAllUpgradeStatus() {
  statusState.entries.clear();
  renderUpgradeStatus();
}

function renderUpgradeStatus() {
  const el = getStatusEl();
  if (!el) return;

  const activeId = getActiveUpgradeIdStr();

  const candidates = [...statusState.entries.entries()]
    .map(([source, entry]) => ({ source, ...entry }))
    .filter((e) => e.text && (!e.upgradeId || (activeId && e.upgradeId === activeId)));

  if (!candidates.length) {
    el.textContent = "";
    el.classList.add("hidden");
    return;
  }

  candidates.sort((a, b) => {
    const pa = STATUS_PRIORITY[a.source] || 0;
    const pb = STATUS_PRIORITY[b.source] || 0;
    if (pb !== pa) return pb - pa;
    return (b.ts || 0) - (a.ts || 0);
  });

  const top = candidates[0];

  el.textContent = top.text || "";
  el.classList.remove("hidden");

  // ✅ apply class (optional)
  // clear previous status class you manage
  el.classList.remove("status-ok", "status-warn", "status-error");
  if (top.cls) el.classList.add(top.cls);

  el.dataset.statusSource = top.source;
  el.dataset.statusSticky = top.sticky ? "1" : "0";

}

function isStatusStickyAbove(source) {
  const p = STATUS_PRIORITY[source] || 0;
  for (const [src, entry] of statusState.entries.entries()) {
    if (!entry?.sticky) continue;
    const ps = STATUS_PRIORITY[src] || 0;
    if (ps >= p) return true;
  }
  return false;
}



// track which upgrade is currently selected
let activeUpgradeId = null;

// helper: call backend -> get checkout_url -> redirect
let checkoutInFlight = false;

async function startUpgradeCheckout(upgradeId) {
  const idNum = Number.parseInt(String(upgradeId), 10);
  if (!Number.isFinite(idNum) || idNum <= 0) {
    alert("Invalid upgrade selected. Please refresh and try again.");
    return;
  }
  if (checkoutInFlight) return;
  checkoutInFlight = true;

  if (!isUnlocked) {
    alert("Please unlock your stay first.");
    showScreen("home");
    checkoutInFlight = false;
    return;
  }

  const propertyId = window.PROPERTY_ID;
  if (propertyId == null) {
    alert("Missing property id. Please refresh the page.");
    checkoutInFlight = false;
    return;
  }

  // ✅ Must have a server session id to map to a stay
  if (!currentSessionId) {
    alert("Please re-unlock your stay to continue.");
    showScreen("home");
    checkoutInFlight = false;
    return;
  }

  willRedirect = false;

  try {
    if (upgradeActiveButton) upgradeActiveButton.disabled = true;

    const sid = encodeURIComponent(String(currentSessionId));
    const url =
      `/guest/properties/${encodeURIComponent(propertyId)}/upgrades/${encodeURIComponent(idNum)}/checkout` +
      `?session_id=${sid}`;

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      cache: "no-store",
      body: JSON.stringify({ session_id: Number(currentSessionId) }),
    });

    const text = await res.text();
    let data = {};
    try { data = JSON.parse(text); } catch { data = { raw: text }; }

    if (!res.ok) {
      console.error("[UPGRADE CHECKOUT ERROR]", res.status, data);
      alert(data?.detail || data?.error || `Checkout failed (${res.status}).`);
      return;
    }

    if (!data?.checkout_url) {
      console.error("[UPGRADE CHECKOUT] Missing checkout_url:", data);
      alert("Checkout URL missing. Please try again.");
      return;
    }

    try {
      localStorage.setItem(`pending_upgrade_${propertyId}`, String(idNum));
    } catch {}

    willRedirect = true;
    window.location.assign(data.checkout_url);
  } catch (e) {
    console.error("[UPGRADE CHECKOUT EXCEPTION]", e);
    alert("Checkout failed. Please try again.");
  } finally {
    if (!willRedirect) {
      if (upgradeActiveButton) upgradeActiveButton.disabled = false;
    }
    checkoutInFlight = false;
  }
}


    function getUpgradeAvailabilityMessage(slideEl, ev) {
  // 1) Server wins if it gives us a reason
  if (ev && !ev.eligible) {
    return ev.disabled_reason || "";
  }

  // 2) Opens-at logic (exact, date-based)
  if (ev?.opens_at) {
    const opens = new Date(ev.opens_at);
    const today = new Date();
    const diffDays = Math.ceil(
      (opens.setHours(0,0,0,0) - today.setHours(0,0,0,0)) / 86400000
    );

    if (diffDays === 1) return "Early check-in opens tomorrow.";
    if (diffDays > 1) return `Early check-in opens in ${diffDays} days.`;
  }

  // 3) Client-only same-day rules
  const rule = getSameDayRuleForSlide(slideEl);
  if (rule?.disabled && rule.reason) {
    return rule.reason;
  }

  return ""; // available → no message
}


/* ===============================
   Paid + Disabled + Carousel UI
   (Stripe checkout + Stripe return are NOT touched)
   ✅ Keeps applyScaleEasing
=============================== */

window.applyPaidState = function applyPaidState(upgradeId) {
  const paidIds = new Set(readPaidUpgrades());
  const idNum = Number.parseInt(String(upgradeId), 10);
  const isPaid = Number.isFinite(idNum) && paidIds.has(idNum);

  const btn = document.getElementById("upgrade-active-button");
  const label = document.getElementById("upgrade-active-button-label");
  const desc = document.getElementById("upgrade-active-description");

  if (!isPaid) return;

  if (label) label.textContent = "Purchase confirmed";
  if (desc) desc.textContent = "✅ Upgrade confirmed — Your host has been notified.";

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-not-allowed");
  }
};

function getCarouselCenterX() {
  if (!upgradesCarousel) return 0;
  const r = upgradesCarousel.getBoundingClientRect();
  return r.left + r.width / 2;
}
function isTodayDateStr(dateStr) {
  if (!dateStr) return false;
  const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  return String(dateStr) === today;
}

function isArrivalDay() {
  return isTodayDateStr(arrivalDate);
}

function isDepartureDay() {
  return isTodayDateStr(departureDate);
}

// Heuristic rules by title (no backend changes required)
function getSameDayRuleForSlide(slideEl) {
  const title = (slideEl?.dataset?.upgradeTitle || "").toLowerCase().trim();

  // You can expand these phrases if needed
  const isLateCheckout = title.includes("late checkout") || title.includes("late check-out");
  const isEarlyCheckin = title.includes("early check-in") || title.includes("early checkin") || title.includes("early check in");

  if (isLateCheckout) {
    return {
      disabled: !isDepartureDay(),
      reason: "Late checkout is only available on your departure day.",
    };
  }

  if (isEarlyCheckin) {
    return {
      disabled: !isArrivalDay(),
      reason: "Early check-in is only available on your arrival day.",
    };
  }

  return { disabled: false, reason: "" };
}

// Original dataset-based disabled (server-driven)
function isUpgradeDisabled(slideEl) {
  if (!slideEl) return false;

  // 1) server-driven disabled always wins
  const v = (slideEl.dataset?.upgradeDisabled || slideEl.getAttribute("data-upgrade-disabled") || "").toString();
  const serverDisabled = v === "true" || v === "1";

  if (serverDisabled) return true;

  // 2) same-day rule (client-side)
  const rule = getSameDayRuleForSlide(slideEl);
  return !!rule.disabled;
}

function getUpgradeDisabledReason(slideEl) {
  if (!slideEl) return "";

  // If server disabled, show server reason
  const v = (slideEl.dataset?.upgradeDisabled || slideEl.getAttribute("data-upgrade-disabled") || "").toString();
  const serverDisabled = v === "true" || v === "1";

  if (serverDisabled) {
    return (
      slideEl.dataset?.upgradeDisabledReason ||
      slideEl.getAttribute("data-upgrade-disabled-reason") ||
      ""
    ).toString().trim();
  }

  // Otherwise show same-day reason if it applies
  const rule = getSameDayRuleForSlide(slideEl);
  return (rule.reason || "").toString().trim();
}


/* ✅ KEEP: smooth scaling effect */
function applyScaleEasing() {
  if (!upgradesCarousel || !upgradeSlides.length) return;

  const viewportCenter = upgradesCarousel.scrollLeft + upgradesCarousel.clientWidth / 2;

  upgradeSlides.forEach((slide) => {
    const slideCenter = slide.offsetLeft + slide.offsetWidth / 2;
    const dist = Math.abs(viewportCenter - slideCenter);

    const maxDist = upgradesCarousel.clientWidth * 0.7;
    const t = Math.min(dist / maxDist, 1);
    const eased = 1 - Math.pow(t, 1.8);

    const minScale = 0.72;
    const scale = minScale + (1 - minScale) * eased;

    const lift = (1 - scale) * 18;
    const opacity = 0.65 + 0.35 * eased;

    slide.style.transform = `translateY(${lift}px) scale(${scale})`;
    slide.style.opacity = opacity.toFixed(3);
  });
}

function setActiveSlideByIndex(idx) {
  const slide = upgradeSlides[idx];
  if (!slide) return;

  document.getElementById("upgrade-active-info")?.classList.remove("hidden");
  document.getElementById("upgrade-active-cta")?.classList.remove("hidden");

  const rawId = slide.getAttribute("data-upgrade-id") || slide.dataset.upgradeId;
  const idNum = Number.parseInt(String(rawId || ""), 10);
  activeUpgradeId = Number.isFinite(idNum) ? idNum : null;

  upgradeSlides.forEach((s, i) => {
    s.classList.toggle("is-active", i === idx);
    s.classList.toggle("is-inactive", i !== idx);
  });

  const titleEl  = document.getElementById("upgrade-active-title");
  const descEl   = document.getElementById("upgrade-active-description");
  const statusEl = document.getElementById("upgrade-active-status"); // ✅ your orange line
  const ctaLabel = document.getElementById("upgrade-active-button-label");
  const ctaBtn   = document.getElementById("upgrade-active-button");

  const title = slide.dataset.upgradeTitle || "Upgrade";
  const price = slide.dataset.upgradePrice || "";
  const long  = slide.querySelector(".upgrade-long")?.textContent?.trim() || "";

  const disabled = isUpgradeDisabled(slide);
  const reason = getUpgradeDisabledReason(slide) || "";

  // --- Title ---
  if (titleEl) titleEl.textContent = title;

    // --- ✅ Status: route ONLY eligibility messages here ---
  // If Stripe is currently showing something sticky, don't overwrite it.
  if (!isStatusStickyAbove(STATUS_SOURCES.ELIGIBILITY)) {
    const msg = disabled ? (reason || "") : "";
    if (msg) {
      setUpgradeStatus({
        source: STATUS_SOURCES.ELIGIBILITY,
        text: msg,
        sticky: false,
        upgradeId: activeUpgradeId, // tie to current upgrade
      });
    } else {
      clearUpgradeStatus(STATUS_SOURCES.ELIGIBILITY);
    }
  }

  // --- Description stays value prop only ---
  if (descEl) descEl.textContent = long;


  // --- CTA label ---
  if (ctaLabel) ctaLabel.textContent = price ? `${price} – Purchase` : "Purchase";

  // --- CTA enabled/disabled ---
  if (ctaBtn) {
    if (disabled) {
      ctaBtn.disabled = true;
      ctaBtn.classList.add("opacity-60", "cursor-not-allowed");
    } else {
      ctaBtn.disabled = false;
      ctaBtn.classList.remove("opacity-60", "cursor-not-allowed");
    }
  }

  // paid overrides
  window.applyPaidState?.(activeUpgradeId);

  // keep scaling synced with active selection
  applyScaleEasing();
}


function findClosestSlideIndex() {
  if (!upgradesCarousel || !upgradeSlides.length) return 0;

  const centerX = getCarouselCenterX();
  let bestIdx = 0;
  let bestDist = Infinity;

  upgradeSlides.forEach((slide, idx) => {
    const r = slide.getBoundingClientRect();
    const slideCenter = r.left + r.width / 2;
    const dist = Math.abs(slideCenter - centerX);
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = idx;
    }
  });

  return bestIdx;
}

function centerSlide(idx, behavior = "smooth") {
  if (!upgradesCarousel || !upgradeSlides[idx]) return;

  const slide = upgradeSlides[idx];
  const cRect = upgradesCarousel.getBoundingClientRect();
  const sRect = slide.getBoundingClientRect();

  const current = upgradesCarousel.scrollLeft;
  const delta = (sRect.left + sRect.width / 2) - (cRect.left + cRect.width / 2);

  upgradesCarousel.scrollTo({ left: current + delta, behavior });
}

function updateActiveFromScroll() {
  const idx = findClosestSlideIndex();
  setActiveSlideByIndex(idx);
}

function initUpgradesCarousel() {
  if (upgradesBound) return;
  upgradesBound = true;

  if (!upgradesCarousel || !upgradeSlides.length) return;

  setActiveSlideByIndex(0);

  requestAnimationFrame(() => {
    centerSlide(0, "auto");
    applyScaleEasing();
  });

  let raf = null;
  upgradesCarousel.addEventListener("scroll", () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      raf = null;
      // ✅ keep BOTH effects
      applyScaleEasing();
      updateActiveFromScroll();
    });
  }, { passive: true });

  upgradeSlides.forEach((slide, idx) => {
    slide.addEventListener("click", () => {
      setActiveSlideByIndex(idx);
      centerSlide(idx, "smooth");
      applyScaleEasing();
    });
  });

  window.addEventListener("resize", () => {
    updateActiveFromScroll();
    applyScaleEasing();
  });
}

/* CTA click — uses YOUR existing Stripe function above */
upgradeActiveButton?.addEventListener("click", () => {
  const idNum = Number.parseInt(String(activeUpgradeId), 10);
  if (!Number.isFinite(idNum) || idNum <= 0) {
    alert("Invalid upgrade selected. Please refresh and try again.");
    return;
  }

  const slide = document.querySelector(".upgrade-slide.is-active");
  if (slide && isUpgradeDisabled(slide)) {
    alert(getUpgradeDisabledReason(slide) || "Not available for this stay.");
    return;
  }

  // ✅ keep Stripe checkout intact
  startUpgradeCheckout(idNum);
});




    
// ===============================
// Stripe Return → Upgrades UI (CLEAN)
// ===============================

function getQueryParam(name) {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

function cleanUrlKeepPath() {
  try {
    window.history.replaceState({}, "", window.location.pathname);
  } catch {}
}

function findUpgradeSlideIndexById(upgradeId) {
  if (!upgradeId) return 0;
  const idStr = String(upgradeId);

  // uses the outer-scoped upgradeSlides array you already have
  const idx = (upgradeSlides || []).findIndex(
    (s) => String(s?.dataset?.upgradeId || "") === idStr
  );

  return idx >= 0 ? idx : 0;
}





function setPurchasedUI({ confirmedText } = {}) {
  const descEl = document.getElementById("upgrade-active-description");
  if (descEl) {
    descEl.textContent =
      confirmedText || "✅ Upgrade confirmed — Your host has been notified.";
  }

  const btn = document.getElementById("upgrade-active-button");
  const btnLabel = document.getElementById("upgrade-active-button-label");
  if (btnLabel) btnLabel.textContent = "Purchase confirmed";

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-not-allowed");
  }
}




async function pollUpgradePurchaseStatus(purchaseId, sessionId, upgradeId) {
  const pid = purchaseId ? String(purchaseId) : "";
  const sid = sessionId ? String(sessionId) : "";
  const uid = upgradeId ? String(upgradeId) : null;

  if (!pid) return false;

  // ✅ Show Stripe status in the orange line (sticky so carousel updates won't overwrite)
  setUpgradeStatus({
    source: STATUS_SOURCES.STRIPE,
    text: "✅ Payment received — Confirming with the host…",
    sticky: true,
    upgradeId: uid,
  });

  const maxAttempts = 10;
  const delayMs = 1000;

  for (let i = 0; i < maxAttempts; i++) {
    try {
      if (document.visibilityState === "hidden") break;

      const url =
        `/guest/upgrades/purchase-status?purchase_id=${encodeURIComponent(pid)}` +
        (sid ? `&session_id=${encodeURIComponent(sid)}` : "") +
        `&t=${Date.now()}`;

      const res = await fetch(url, { credentials: "include", cache: "no-store" });
      const data = await readJsonSafely(res);

      if (res.ok && data?.status === "paid") {
        if (uid) markUpgradePaid(uid);
        await syncPaidUpgradesFromServer();

        // ✅ Confirmed message in orange line
        setUpgradeStatus({
          source: STATUS_SOURCES.STRIPE,
          text: "✅ Upgrade confirmed — Your host has been notified.",
          sticky: true,
          upgradeId: uid,
        });

        // Keep your existing purchased UI behavior
        setPurchasedUI({
          confirmedText: "✅ Upgrade confirmed — Your host has been notified.",
        });

        // Optional: release the orange line after a few seconds so eligibility can show again
        setTimeout(() => clearUpgradeStatus(STATUS_SOURCES.STRIPE), 8000);

        return true;
      }
    } catch {
      // ignore + keep polling
    }

    await new Promise((r) => setTimeout(r, delayMs));
  }

  // ✅ Timed out (still not confirmed)
  setUpgradeStatus({
    source: STATUS_SOURCES.STRIPE,
    text: "✅ Payment received — Your host will confirm shortly.",
    sticky: true,
    upgradeId: uid,
  });

  // Optional: clear later
  setTimeout(() => clearUpgradeStatus(STATUS_SOURCES.STRIPE), 12000);

  return false;
}


async function handleUpgradeReturnFromStripe() {
  const upgradeResult = (getQueryParam("upgrade") || "").toLowerCase().trim(); // success|cancel
  const purchaseId = getQueryParam("purchase_id");
  const sessionId = getQueryParam("session_id");

  let upgradeId = getQueryParam("upgrade_id");
  if (!upgradeId) {
    try { upgradeId = localStorage.getItem(`pending_upgrade_${window.PROPERTY_ID}`); } catch {}
  }
  upgradeId = upgradeId ? String(upgradeId) : null;

  if (!upgradeResult) return;

  const clearPendingUpgrade = () => {
    try { localStorage.removeItem(`pending_upgrade_${window.PROPERTY_ID}`); } catch {}
  };

  // Go to upgrades UI + ensure carousel is ready
  showScreen("upgrades");
  try { initUpgradesCarousel(); } catch {}

  // wait for layout
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

  // Focus the relevant upgrade card if we have it
  if (upgradeId) {
    const idx = findUpgradeSlideIndexById(upgradeId);
    setActiveSlideByIndex(idx);
    centerSlide(idx, "auto");
    try { applyScaleEasing(); } catch {}
  } else {
    try { updateActiveFromScroll(); } catch {}
  }

  // --- CANCEL ---
  if (upgradeResult === "cancel") {
    setUpgradeStatus({
      source: STATUS_SOURCES.STRIPE,
      text: "Payment canceled — You were not charged.",
      sticky: false,
      upgradeId,
    });
    setTimeout(() => clearUpgradeStatus(STATUS_SOURCES.STRIPE), 6000);

    clearPendingUpgrade();
    cleanUrlKeepPath();
    return;
  }

  // --- SUCCESS + purchase_id (poll + host confirm) ---
  if (upgradeResult === "success" && purchaseId) {
    // pollUpgradePurchaseStatus already sets orange-line statuses + clears later
    await pollUpgradePurchaseStatus(purchaseId, sessionId, upgradeId);

    clearPendingUpgrade();
    cleanUrlKeepPath();
    return;
  }

  // --- SUCCESS but no purchase_id (fallback) ---
  if (upgradeResult === "success" && !purchaseId) {
    setUpgradeStatus({
      source: STATUS_SOURCES.STRIPE,
      text: "✅ Payment received — Your host will confirm shortly.",
      sticky: true,
      upgradeId,
    });
    setTimeout(() => clearUpgradeStatus(STATUS_SOURCES.STRIPE), 12000);

    clearPendingUpgrade();
    cleanUrlKeepPath();
    return;
  }
}


await handleUpgradeReturnFromStripe();
await refreshUpgradeEligibility();

});
