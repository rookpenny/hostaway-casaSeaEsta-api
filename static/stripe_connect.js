// static/stripe_connect.js

async function stripeConnectStart() {
  const btn = document.getElementById("stripe-connect-btn");
  btn && (btn.disabled = true);

  try {
    const res = await fetch("/admin/integrations/stripe/connect", {
      method: "POST",
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok || !data.url) {
      console.error("Stripe connect error", data);
      alert(data.detail || "Stripe connect failed. Check logs.");
      return;
    }

    window.location.href = data.url;
  } finally {
    btn && (btn.disabled = false);
  }
}

async function stripeConnectRefreshStatus() {
  const el = document.getElementById("stripe-connect-status");
  if (!el) return;

  el.textContent = "Checking…";

  const res = await fetch("/admin/integrations/stripe/status", { credentials: "include" });
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    el.textContent = "Could not load Stripe status.";
    return;
  }

  if (!data.connected) {
    el.textContent = "Not connected.";
    return;
  }

  const bits = [`Connected (${data.account_id})`];
  if (data.charges_enabled !== undefined) bits.push(`charges: ${data.charges_enabled ? "on" : "off"}`);
  if (data.payouts_enabled !== undefined) bits.push(`payouts: ${data.payouts_enabled ? "on" : "off"}`);
  el.textContent = bits.join(" • ");
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("stripe-connect-btn")?.addEventListener("click", stripeConnectStart);
  document.getElementById("stripe-refresh-btn")?.addEventListener("click", stripeConnectRefreshStatus);
  stripeConnectRefreshStatus();
});
