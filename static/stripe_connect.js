// static/stripe_connect.js
async function fetchStripeStatus() {
  const res = await fetch("/admin/integrations/stripe/status");
  if (!res.ok) throw new Error("Failed to load Stripe status");
  return await res.json();
}

function setStatusText(text) {
  const el = document.getElementById("stripe-connect-status");
  if (el) el.textContent = text;
}

async function refreshStripeUI() {
  try {
    setStatusText("Checking status…");
    const data = await fetchStripeStatus();
    if (!data.connected) {
      setStatusText("Not connected. Connect Stripe to accept upgrade payments.");
      return;
    }
    const parts = [];
    parts.push(`Connected (${data.account_id})`);
    if (data.charges_enabled !== undefined) parts.push(`charges: ${data.charges_enabled ? "on" : "off"}`);
    if (data.payouts_enabled !== undefined) parts.push(`payouts: ${data.payouts_enabled ? "on" : "off"}`);
    setStatusText(parts.join(" • "));
  } catch (e) {
    setStatusText("Could not load Stripe status.");
    console.error(e);
  }
}

async function startStripeConnect() {
  const btn = document.getElementById("stripe-connect-btn");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/admin/integrations/stripe/connect", { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.url) throw new Error(data.detail || "Connect failed");
    window.location.href = data.url;
  } catch (e) {
    console.error(e);
    alert("Stripe connect failed. Check logs.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("stripe-connect-btn")?.addEventListener("click", startStripeConnect);
  document.getElementById("stripe-refresh-btn")?.addEventListener("click", refreshStripeUI);

  // refresh when settings tab opens (simple approach: refresh once on load)
  refreshStripeUI();
});
