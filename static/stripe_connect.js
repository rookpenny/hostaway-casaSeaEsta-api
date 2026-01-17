// ----------------------------
// START OF CONFIG PARTIAL (CLEAN)
// ----------------------------

const StripeConnect = {
  async refreshStatus() {
    const pill = document.querySelector("[data-stripe-status-pill]");
    const detail = document.querySelector("[data-stripe-status-detail]");
    const errBox = document.querySelector("[data-stripe-error]");
    const connectBtn = document.querySelector("[data-stripe-connect-btn]");
    const manageBtn = document.querySelector("[data-stripe-manage-btn]");

    if (!pill || !detail || !connectBtn) return;

    errBox?.classList.add("hidden");
    pill.textContent = "Checking…";

    try {
      const res = await fetch("/admin/stripe/ajax/status");
      const data = await res.json();

      if (!data.ok) throw new Error(data.error || "Failed to check Stripe status");

      // states: not_connected | onboarding_required | active
      if (data.state === "not_connected") {
        pill.textContent = "Not connected";
        pill.className = "text-xs px-2 py-1 rounded-full border bg-white text-slate-700";
        detail.textContent = "Connect Stripe to enable paid upgrades and payouts.";
        connectBtn.textContent = "Connect Stripe";
        manageBtn?.classList.add("hidden");
      }

      if (data.state === "onboarding_required") {
        pill.textContent = "Onboarding required";
        pill.className = "text-xs px-2 py-1 rounded-full border bg-amber-50 border-amber-200 text-amber-800";
        detail.textContent = "Stripe onboarding is incomplete. Continue onboarding to enable charges and payouts.";
        connectBtn.textContent = "Continue onboarding";
        manageBtn?.classList.add("hidden");
      }

      if (data.state === "active") {
        pill.textContent = "Connected ✅";
        pill.className = "text-xs px-2 py-1 rounded-full border bg-emerald-50 border-emerald-200 text-emerald-800";
        detail.textContent = "Stripe is connected. Guests can pay for upgrades and payouts go to your Stripe account.";
        connectBtn.textContent = "Re-connect";
        manageBtn?.classList.remove("hidden");

        // optional: if you create a Stripe login link for Express dashboard
        if (manageBtn && data.manage_url) {
          manageBtn.onclick = () => window.open(data.manage_url, "_blank");
        }
      }
    } catch (e) {
      pill.textContent = "Error";
      pill.className = "text-xs px-2 py-1 rounded-full border bg-rose-50 border-rose-200 text-rose-800";
      if (errBox) {
        errBox.textContent = e.message || "Stripe status failed";
        errBox.classList.remove("hidden");
      } else {
        detail.textContent = "Stripe status failed.";
      }
    }
  },

  async startOnboarding() {
    const errBox = document.querySelector("[data-stripe-error]");
    errBox?.classList.add("hidden");

    const res = await fetch("/admin/stripe/ajax/connect", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      if (errBox) {
        errBox.textContent = data.error || "Unable to start Stripe onboarding.";
        errBox.classList.remove("hidden");
      }
      return;
    }
    window.location.href = data.url; // Stripe account link redirect
  },

  bind() {
    document.querySelector("[data-stripe-connect-btn]")?.addEventListener("click", (e) => {
      e.preventDefault();
      StripeConnect.startOnboarding();
    });
  },
};

// Call when settings panel is shown
function onSettingsTabChange(panelId) {
  if (panelId === "integrations") {
    StripeConnect.bind();
    StripeConnect.refreshStatus();
  }
}
