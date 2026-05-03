(function () {
  const script = document.currentScript;
  const widgetKey = script?.dataset?.widgetKey;

  if (!widgetKey) return;

  const API_BASE = "https://hostaway-casaseaesta-api.onrender.com";
  const STORAGE_KEY = `hostscout_webchat_${widgetKey}`;

  let webchatSessionId = localStorage.getItem(STORAGE_KEY);

  async function getWidgetStatus() {
    try {
      const res = await fetch(
        `${API_BASE}/api/public-property-chat/status?widget_key=${encodeURIComponent(widgetKey)}`,
        {
          method: "GET",
          credentials: "omit",
        }
      );

      const data = await res.json().catch(() => ({}));

      return {
        enabled: !!data.enabled,
        propertyName: data.property_name || "",
        reason: data.reason || "",
      };
    } catch (err) {
      console.warn("[HostScout widget] Could not check widget status", err);

      return {
        enabled: false,
        propertyName: "",
        reason: "status_check_failed",
      };
    }
  }

  function bootWidget(status) {
    // -----------------------------
    // Floating chat launcher
    // -----------------------------
    const bubbleWrap = document.createElement("div");
    bubbleWrap.style.cssText = `
      position: fixed;
      right: 24px;
      bottom: 24px;
      z-index: 999999;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 10px;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    `;

    const prompt = document.createElement("div");
    prompt.innerHTML = `
      <span>Have a question? Text us here</span>
      <button type="button" aria-label="Dismiss chat prompt">×</button>
      <span class="hsw-caret"></span>
    `;

    prompt.style.cssText = `
      position: relative;
      display: flex;
      align-items: center;
      gap: 12px;
      max-width: 280px;
      padding: 14px 16px;
      border-radius: 16px;
      background: #ffffff;
      color: #3f3f46;
      font-size: 14px;
      font-weight: 450;
      line-height: 1.2;
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.16);
      border: 1px solid rgba(15, 23, 42, 0.08);
    `;

    const promptClose = prompt.querySelector("button");
    promptClose.style.cssText = `
      border: 0;
      background: transparent;
      color: #9ca3af;
      font-size: 20px;
      line-height: 1;
      cursor: pointer;
      padding: 0;
      margin: 0;
    `;

    const caret = prompt.querySelector(".hsw-caret");
    caret.style.cssText = `
      position: absolute;
      right: 24px;
      bottom: -7px;
      width: 14px;
      height: 14px;
      background: #ffffff;
      transform: rotate(45deg);
      border-right: 1px solid rgba(15, 23, 42, 0.08);
      border-bottom: 1px solid rgba(15, 23, 42, 0.08);
    `;

    const bubble = document.createElement("button");
    bubble.type = "button";
    bubble.innerHTML = `
      <span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
          <path d="M4 5.75A2.75 2.75 0 0 1 6.75 3h10.5A2.75 2.75 0 0 1 20 5.75v7.5A2.75 2.75 0 0 1 17.25 16H9.1l-4.02 3.18A.65.65 0 0 1 4 18.67V5.75Z"/>
        </svg>
      </span>
      <span>Chat</span>
    `;

    bubble.style.cssText = `
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
      border: 0;
      border-radius: 999px;
      padding: 14px 20px;
      background: #333333;
      color: #ffffff;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18);
    `;

    // -----------------------------
    // Chat panel
    // -----------------------------
    const panel = document.createElement("div");
    panel.style.cssText = `
      position: fixed;
      right: 24px;
      bottom: 96px;
      z-index: 999999;
      width: min(380px, calc(100vw - 32px));
      height: 560px;
      max-height: calc(100vh - 132px);
      display: none;
      flex-direction: column;
      overflow: hidden;
      border-radius: 28px;
      background: white;
      box-shadow: 0 24px 80px rgba(15, 23, 42, .28);
      border: 1px solid rgba(226,232,240,.9);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    `;

    const propertyName = status.propertyName || "the property";

    panel.innerHTML = `
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:18px 18px 14px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">
        <div>
          <div style="font-size:16px;font-weight:750;color:#0f172a;">Property Assistant</div>
          <div style="margin-top:4px;font-size:13px;color:#64748b;">Ask anything before you book.</div>
        </div>

        <button id="hsw-panel-close" type="button" aria-label="Close chat" style="
          border:0;
          background:transparent;
          color:#94a3b8;
          font-size:22px;
          line-height:1;
          cursor:pointer;
          padding:0;
        ">×</button>
      </div>

      <div id="hsw-messages" style="flex:1;overflow:auto;padding:16px;background:#ffffff;">
        <div style="max-width:85%;padding:12px 14px;border-radius:18px;background:#f1f5f9;color:#0f172a;font-size:14px;line-height:1.45;">
          Hi! Ask me about ${escapeHtml(propertyName)}, amenities, location, or booking direct.
        </div>
      </div>

      <form id="hsw-form" style="display:flex;gap:8px;padding:12px;border-top:1px solid #e2e8f0;background:#fff;">
        <input id="hsw-input" placeholder="Ask about the property..." style="
          flex:1;
          height:44px;
          min-width:0;
          border:1px solid #cbd5e1;
          border-radius:999px;
          padding:0 14px;
          font-size:14px;
          outline:none;
        " />
        <button id="hsw-send" type="submit" style="
          height:44px;
          border:0;
          border-radius:999px;
          padding:0 16px;
          background:#333333;
          color:white;
          font-weight:700;
          cursor:pointer;
        ">Send</button>
      </form>
    `;

    bubbleWrap.appendChild(prompt);
    bubbleWrap.appendChild(bubble);
    document.body.appendChild(bubbleWrap);
    document.body.appendChild(panel);

    const messages = panel.querySelector("#hsw-messages");
    const form = panel.querySelector("#hsw-form");
    const input = panel.querySelector("#hsw-input");
    const sendBtn = panel.querySelector("#hsw-send");
    const panelClose = panel.querySelector("#hsw-panel-close");

    // -----------------------------
    // UI behavior
    // -----------------------------
    function openPanel() {
      panel.style.display = "flex";
      prompt.style.display = "none";
      window.setTimeout(() => input?.focus(), 50);
    }

    function closePanel() {
      panel.style.display = "none";
    }

    function togglePanel() {
      if (panel.style.display === "flex") {
        closePanel();
      } else {
        openPanel();
      }
    }

    promptClose.addEventListener("click", (e) => {
      e.stopPropagation();
      prompt.style.display = "none";
    });

    prompt.addEventListener("click", openPanel);
    bubble.addEventListener("click", togglePanel);
    panelClose.addEventListener("click", closePanel);

    // -----------------------------
    // Messages
    // -----------------------------
    function addMessage(text, who) {
      const div = document.createElement("div");

      div.style.cssText = `
        max-width:85%;
        margin:${who === "user" ? "10px 0 10px auto" : "10px auto 10px 0"};
        padding:12px 14px;
        border-radius:18px;
        background:${who === "user" ? "#333333" : "#f1f5f9"};
        color:${who === "user" ? "white" : "#0f172a"};
        font-size:14px;
        line-height:1.45;
        white-space:pre-wrap;
      `;

      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;

      return div;
    }

    async function sendMessage(text) {
      input.disabled = true;
      sendBtn.disabled = true;
      sendBtn.style.opacity = "0.65";

      addMessage(text, "user");
      const loading = addMessage("Thinking...", "assistant");

      try {
        const res = await fetch(`${API_BASE}/api/public-property-chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "omit",
          body: JSON.stringify({
            widget_key: widgetKey,
            message: text,
            session_id: webchatSessionId ? Number(webchatSessionId) : null,
          }),
        });

        const data = await res.json().catch(() => ({}));

        if (data.session_id) {
          webchatSessionId = String(data.session_id);
          localStorage.setItem(STORAGE_KEY, webchatSessionId);
        }

        if (!res.ok) {
          loading.textContent =
            data.detail || data.reply || "Sorry, I could not answer that.";
          return;
        }

        loading.textContent = data.reply || "Sorry, I could not answer that.";
      } catch (err) {
        console.warn("[HostScout widget] Message failed", err);
        loading.textContent = "Sorry, something went wrong. Please try again.";
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        sendBtn.style.opacity = "1";
        input.focus();
      }
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();

      const text = input.value.trim();
      if (!text) return;

      input.value = "";
      await sendMessage(text);
    });
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function init() {
    const status = await getWidgetStatus();

    if (!status.enabled) {
      console.info("[HostScout widget] Widget disabled or unavailable:", status.reason);
      return;
    }

    bootWidget(status);
  }

  init();
})();
