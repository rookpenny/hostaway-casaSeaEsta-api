// static/stripe_connect.js

let stripePopup = null;

async function stripeConnectStart() {
  const btn = document.getElementById("stripe-connect-btn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/admin/integrations/stripe/connect", {
      method: "POST",
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok || !data.url) {
      alert(data.detail || "Stripe connect failed");
      return;
    }

    // Open Stripe onboarding in a popup
    stripePopup = window.open(
      data.url,
      "stripeConnect",
      "width=900,height=700,resizable=yes,scrollbars=yes"
    );

    if (!stripePopup) {
      alert("Popup blocked. Please allow popups and try again.");
      return;
    }

    // If popup closes (user closes it or callback closes it), refresh status
    const t = setInterval(() => {
      if (stripePopup && stripePopup.closed) {
        clearInterval(t);
        stripeConnectRefreshStatus();
      }
    }, 600);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function stripeDisconnect() {
  if (!confirm("Disconnect Stripe? This will disable upgrade payments.")) return;

  const res = await fetch("/admin/integrations/stripe/disconnect", {
    method: "POST",
    credentials: "include",
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || "Failed to disconnect Stripe.");
    return;
  }

  // Refresh UI and hard reload so upgrades lock immediately
  await stripeConnectRefreshStatus();
  location.reload();
}

async function stripeConnectRefreshStatus() {
  const el = document.getElementById("stripe-connect-status");
  if (!el) return;

  el.textContent = "Checking...";

  const connectBtn = document.getElementById("stripe-connect-btn");

  try {
    const res = await fetch("/admin/integrations/stripe/status", {
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      el.textContent = data.detail || "Could not load Stripe status.";
      return;
    }

    if (!data.connected) {
      el.textContent = "Not connected.";
      // Keep connect button visible
      if (connectBtn) connectBtn.classList.remove("hidden");
      return;
    }

    // We have an account on file. is_connected means callback completed.
    if (data.is_connected) {
      el.textContent = "Connected (" + data.account_id + ")";
      // Optionally hide connect once fully connected
      if (connectBtn) connectBtn.classList.add("hidden");
    } else {
      el.textContent =
        "Connected (" +
        data.account_id +
        ") - setup incomplete (finish onboarding to accept payments).";
      // Keep connect visible so they can re-open onboarding
      if (connectBtn) connectBtn.classList.remove("hidden");
    }
  } catch (e) {
    console.error(e);
    el.textContent = "Error checking Stripe status.";
  }
}

// Event delegation so it works even if Settings panels are shown/hidden dynamically
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

  // If they clicked Settings -> Integrations tab, refresh status shortly after
  const tab = e.target.closest(".settings-tab[data-settings='integrations']");
  if (tab) {
    setTimeout(stripeConnectRefreshStatus, 50);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  stripeConnectRefreshStatus();
});
