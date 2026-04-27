(function () {
  const script = document.currentScript;
  const widgetKey = script?.dataset?.widgetKey;

  if (!widgetKey) return;

  const API_BASE = "https://hostaway-casaseaesta-api.onrender.com";
  const STORAGE_KEY = `hostscout_webchat_${widgetKey}`;

  let webchatSessionId = localStorage.getItem(STORAGE_KEY);

  const bubble = document.createElement("button");
  bubble.innerHTML = "Ask about this stay";
  bubble.style.cssText = `
    position: fixed;
    right: 24px;
    bottom: 24px;
    z-index: 999999;
    border: 0;
    border-radius: 999px;
    padding: 14px 18px;
    background: #111827;
    color: white;
    font: 600 14px system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    box-shadow: 0 18px 40px rgba(15, 23, 42, .22);
    cursor: pointer;
  `;

  const panel = document.createElement("div");
  panel.style.cssText = `
    position: fixed;
    right: 24px;
    bottom: 86px;
    z-index: 999999;
    width: min(380px, calc(100vw - 32px));
    height: 560px;
    max-height: calc(100vh - 120px);
    display: none;
    flex-direction: column;
    overflow: hidden;
    border-radius: 28px;
    background: white;
    box-shadow: 0 24px 80px rgba(15, 23, 42, .28);
    border: 1px solid rgba(226,232,240,.9);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  `;

  panel.innerHTML = `
    <div style="padding:18px 18px 14px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">
      <div style="font-size:16px;font-weight:750;color:#0f172a;">Property Assistant</div>
      <div style="margin-top:4px;font-size:13px;color:#64748b;">Ask anything before you book.</div>
    </div>

    <div id="hsw-messages" style="flex:1;overflow:auto;padding:16px;background:#ffffff;">
      <div style="max-width:85%;padding:12px 14px;border-radius:18px;background:#f1f5f9;color:#0f172a;font-size:14px;line-height:1.45;">
        Hi! Ask me about the property, amenities, location, or booking direct.
      </div>
    </div>

    <form id="hsw-form" style="display:flex;gap:8px;padding:12px;border-top:1px solid #e2e8f0;background:#fff;">
      <input id="hsw-input" placeholder="Ask about the property..." style="
        flex:1;
        height:44px;
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
        background:#4f46e5;
        color:white;
        font-weight:700;
        cursor:pointer;
      ">Send</button>
    </form>
  `;

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  const messages = panel.querySelector("#hsw-messages");
  const form = panel.querySelector("#hsw-form");
  const input = panel.querySelector("#hsw-input");
  const sendBtn = panel.querySelector("#hsw-send");

  bubble.addEventListener("click", () => {
    panel.style.display = panel.style.display === "flex" ? "none" : "flex";
    if (panel.style.display === "flex") input.focus();
  });

  function addMessage(text, who) {
    const div = document.createElement("div");
    div.style.cssText = `
      max-width:85%;
      margin:${who === "user" ? "10px 0 10px auto" : "10px auto 10px 0"};
      padding:12px 14px;
      border-radius:18px;
      background:${who === "user" ? "#4f46e5" : "#f1f5f9"};
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

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const text = input.value.trim();
    if (!text) return;

    input.value = "";
    input.disabled = true;
    sendBtn.disabled = true;
    sendBtn.style.opacity = "0.65";

    addMessage(text, "user");
    const loading = addMessage("Thinking...", "assistant");

    try {
      const res = await fetch(`${API_BASE}/api/public-property-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          widget_key: widgetKey,
          message: text,
          session_id: webchatSessionId ? Number(webchatSessionId) : null
        })
      });

      const data = await res.json().catch(() => ({}));

      if (data.session_id) {
        webchatSessionId = String(data.session_id);
        localStorage.setItem(STORAGE_KEY, webchatSessionId);
      }

      if (!res.ok) {
        loading.textContent = data.detail || data.reply || "Sorry, I could not answer that.";
        return;
      }

      loading.textContent = data.reply || "Sorry, I could not answer that.";
    } catch (err) {
      loading.textContent = "Sorry, something went wrong. Please try again.";
    } finally {
      input.disabled = false;
      sendBtn.disabled = false;
      sendBtn.style.opacity = "1";
      input.focus();
    }
  });
})();
