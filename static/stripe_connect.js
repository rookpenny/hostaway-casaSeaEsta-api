// static/stripe_connect.js

async function stripeGetStatus() {
  const res = await fetch("/admin/integrations/stripe/status", { credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Failed to load status");
  return data;
}

function stripeSetStatusText(text) {
  const el = document.getElementById("stripe-connect-status");
  if (el) el.textContent = text;
}

async function stripeRefreshUI() {
  try {
    stripeSetStatusText("Checking status…");
    const s = await stripeGetStatus();

    if (!s.connected) {
      stripeSetStatusText("Not connected. Connect Stripe to accept upgrade payments.");
      return;
    }

    const bits = [`Connected (${s.account_id})`];
    if (s.charges_enabled !== undefined) bits.push(`charges: ${s.charges_enabled ? "on" : "off"}`);
    if (s.payouts_enabled !== undefined) bits.push(`payouts: ${s.payouts_enabled ? "on" : "off"}`);
    stripeSetStatusText(bits.join(" • "));
  } catch (e) {
    console.error(e);
    stripeSetStatusText("Could not load Stripe status.");
  }
}

async function stripeStartConnect() {
  const btn = document.getElementById("stripe-connect-btn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/admin/integrations/stripe/connect", {
      method: "POST",
      credentials: "include",
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok || !data.url) {
      throw new Error(data.detail || "Connect failed");
    }

    window.location.href = data.url;
  } catch (e) {
    console.error(e);
    alert("Stripe connect failed. Check Render logs.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("stripe-connect-btn")?.addEventListener("click", stripeStartConnect);
  document.getElementById("stripe-refresh-btn")?.addEventListener("click", stripeRefreshUI);

  // Refresh once on load; if your settings panel loads via tabs, call again when opened.
  stripeRefreshUI();
});
