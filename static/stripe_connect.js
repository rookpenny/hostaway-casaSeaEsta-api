let stripePopup = null;

async function stripeConnectStart() {
  const btn = document.getElementById("stripe-connect-btn");
  btn && (btn.disabled = true);

  try {
    const res = await fetch("/admin/integrations/stripe/connect", {
      method: "POST",
      credentials: "include",
    });

    const data = await res.json();

    if (!res.ok || !data.url) {
      alert(data.detail || "Stripe connect failed");
      return;
    }

    stripePopup = window.open(
      data.url,
      "stripeConnect",
      "width=900,height=700,resizable=yes,scrollbars=yes"
    );

    if (!stripePopup) {
      alert("Popup blocked. Please allow popups and try again.");
    }
  } finally {
    btn && (btn.disabled = false);
  }
}


async function stripeDisconnect() {
  if (!confirm("Disconnect Stripe? This will disable upgrade payments.")) {
    return;
  }

  const res = await fetch("/admin/integrations/stripe/disconnect", {
    method: "POST",
    credentials: "include",
  });

  if (!res.ok) {
    alert("Failed to disconnect Stripe.");
    return;
  }

  stripeConnectRefreshStatus();
  location.reload(); // ensures upgrades lock immediately
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
  
  if (data.ready) {
    el.textContent = `Connected (${data.account_id}) • charges: on • payouts: ${data.payouts_enabled ? "on" : "off"}`;
  } else {
    el.textContent = `Connected (${data.account_id}) • setup incomplete (finish onboarding to accept payments)`;
  }


  const bits = [`Connected (${data.account_id})`];
  if (data.charges_enabled !== undefined) bits.push(`charges: ${data.charges_enabled ? "on" : "off"}`);
  if (data.payouts_enabled !== undefined) bits.push(`payouts: ${data.payouts_enabled ? "on" : "off"}`);
  el.textContent = bits.join(" • ");
}

// ✅ Event delegation: works even if panel is hidden/shown dynamically
document.addEventListener("click", (e) => {
  if (e.target.closest("#stripe-connect-btn")) {
    e.preventDefault();
    stripeConnectStart();
    return;
  }
  if (e.target.closest("#stripe-refresh-btn")) {
    e.preventDefault();
    stripeConnectRefreshStatus();
    return;
  }

  // If they clicked the Settings→Integrations tab, refresh status
  const tab = e.target.closest(".settings-tab[data-settings='integrations']");
  if (tab) {
    setTimeout(stripeConnectRefreshStatus, 50);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  stripeConnectRefreshStatus();
});
