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

    // Optional: if popup closes without calling our callback (user closes manually),
    // refresh status when it closes.
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
  const disconnectBtn = document.getElementById("stripe-disconnect-btn");

  try {
    const res = await fetch("/admin/integrations/stripe/status", {
      credentials: "include",
    });

    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
      el.textContent = "Could not load Stripe status.";
      return;
    }

    if (!data.connected) {
      el.textContent = "Not connected.";
      if (connectBtn) connectBtn.classList.remove("hidden");
      if (disconnectBtn) disconnectBtn.classList.add("hidden");
      return;
    }

    // Your backend returns: connected, account_id, is_connected
    if (data.is_connected) {
      el.textContent = "Connected (" + data.account_id + ")";
    } else {
      el.textContent =
        "Connected (" +
        data.account_id +
        ") - setup incomplete (finish onboarding to accept payments).";
    }

    // Toggle buttons
    if (connectBtn) connectBtn.classList.add("hidden");
    if (disconnectBtn) disconnectBtn.classList.remove("hidden");
  } catch (e) {
    console.error(e);
    el.textContent = "Error checking Stripe status.";
  }
}

// Event delegation so it works even if tab panels mount/unmount
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
  if (e.target.closest("#stripe-disconnect-btn")) {
    e.preventDefault();
    stripeDisconnect();
    return;
  }

  // If they clicked the Settings -> Integrations tab, refresh status
  const tab = e.target.closest(".settings-tab[data-settings='integrations']");
  if (tab) {
    setTimeout(stripeConnectRefreshStatus, 50);
  }
});

document.addEventListener("DOMContentLoaded", () => {
  stripeConnectRefreshStatus();
});
