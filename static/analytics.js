// ===============================
// ANALYTICS.JS (DROP-IN FILE)
// ===============================

// expose globals
window.chatAnalyticsChart = null;

window.analyticsUIState = {
  chartMode: "chats",
  compareMode: true,
  rows: [],
  previousRows: [],
  selectedIndex: null,
  hoveredIndex: null,
};

// -------------------------------
// HELPERS
// -------------------------------

function safeNum(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function rangeToUnixMs(days) {
  const to = Date.now();
  const from = to - days * 86400000;
  return { from, to };
}

function buildQS({ from, to, propertyId, pmcId }) {
  const qs = new URLSearchParams({ from, to });
  if (propertyId) qs.set("property_id", propertyId);
  if (pmcId) qs.set("pmc_id", pmcId);
  return qs.toString();
}

// -------------------------------
// DATA TRANSFORM
// -------------------------------

function buildRows(labels, series) {
  return labels.map((label, i) => {
    const chats = safeNum(series.sessions?.[i]);
    const clicks = safeNum(series.followup_clicks?.[i]);
    const errors = safeNum(series.chat_errors?.[i]);

    return {
      label,
      chats,
      conversion: chats > 0 ? Math.round((clicks / chats) * 100) : 0,
      lost: errors * 2,
      errors,
      clicks,
    };
  });
}

// -------------------------------
// CHART
// -------------------------------

function renderChart() {
  const canvas = document.getElementById("chatAnalyticsChart");
  if (!canvas || !window.Chart) return;

  const state = window.analyticsUIState;
  const ctx = canvas.getContext("2d");

  if (window.chatAnalyticsChart) {
    window.chatAnalyticsChart.destroy();
  }

  const values = state.rows.map(r => {
    if (state.chartMode === "conversion") return r.conversion;
    if (state.chartMode === "lost") return r.lost;
    return r.chats;
  });

  window.chatAnalyticsChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: state.rows.map(r => r.label),
      datasets: [
        {
          data: values,
          borderColor: "#356cf6",
          backgroundColor: "rgba(53,108,246,0.1)",
          fill: true,
          tension: 0.35,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
    },
  });
}

// -------------------------------
// HOVER + DRILLDOWN
// -------------------------------

function renderDrilldown(index) {
  const row = window.analyticsUIState.rows[index];
  if (!row) return;

  document.getElementById("analytics-drilldown-date").textContent = row.label;
}

// -------------------------------
// MAIN LOAD
// -------------------------------

window.loadChatAnalytics = async function (daysOverride = null) {
  const days = daysOverride || document.getElementById("analyticsRange")?.value || 30;
  const { from, to } = rangeToUnixMs(days);

  const qs = buildQS({ from, to });

  const res = await fetch(`/admin/analytics/chat/timeseries?${qs}`);
  const data = await res.json();

  const rows = buildRows(data.labels || [], data.series || {});

  window.analyticsUIState.rows = rows;

  renderChart();
  renderDrilldown(rows.length - 1);
};

// -------------------------------
// INTERACTIONS
// -------------------------------

window.initAnalyticsInteractions = function () {
  if (window.__analyticsInit) return;
  window.__analyticsInit = true;

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".analytics-mode-btn");
    if (btn) {
      window.analyticsUIState.chartMode = btn.dataset.chartMode;
      renderChart();
    }

    if (e.target.id === "analyticsCompareToggle") {
      window.analyticsUIState.compareMode = !window.analyticsUIState.compareMode;
      renderChart();
    }
  });

  document.addEventListener("change", (e) => {
    if (e.target.id === "analyticsRange") {
      loadChatAnalytics(e.target.value);
    }
  });
};
