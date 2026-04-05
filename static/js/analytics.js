async function loadAIInsights() {
  const topIssueEl = document.getElementById("insight-top-issue");
  const topIssueDetailEl = document.getElementById("insight-top-issue-detail");
  const riskEl = document.getElementById("insight-risk");
  const riskDetailEl = document.getElementById("insight-risk-detail");
  const automationEl = document.getElementById("insight-automation");
  const automationDetailEl = document.getElementById("insight-automation-detail");
  const humanEl = document.getElementById("insight-human");
  const humanDetailEl = document.getElementById("insight-human-detail");

  if (
    !topIssueEl ||
    !topIssueDetailEl ||
    !riskEl ||
    !riskDetailEl ||
    !automationEl ||
    !automationDetailEl ||
    !humanEl ||
    !humanDetailEl
  ) {
    return;
  }

  try {
    const res = await fetch("/api/analytics/ai-insights", {
      credentials: "include",
      headers: { Accept: "application/json" },
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();

    topIssueEl.innerText = data.top_issue || "None";
    topIssueDetailEl.innerText = `${data.top_issue_count || 0} chats`;

    riskEl.innerText = data.high_risk || "None";
    riskDetailEl.innerText = `${data.high_risk_count || 0} high-risk chats`;

    automationEl.innerText = data.automation || "None";
    automationDetailEl.innerText = `${data.automation_count || 0} repeat low-risk`;

    humanEl.innerText =
      data.needs_human_pct != null ? `${data.needs_human_pct}%` : "0%";
    humanDetailEl.innerText = `${data.needs_human || 0} chats`;
  } catch (err) {
    console.error("loadAIInsights failed:", err);

    topIssueEl.innerText = "Unavailable";
    topIssueDetailEl.innerText = "Could not load";

    riskEl.innerText = "Unavailable";
    riskDetailEl.innerText = "Could not load";

    automationEl.innerText = "Unavailable";
    automationDetailEl.innerText = "Could not load";

    humanEl.innerText = "Unavailable";
    humanDetailEl.innerText = "Could not load";
  }
}

window.applyInsightFilter = function (type) {
  if (type === "top_issue") {
    window.location.href = "/admin/dashboard?view=chats&filter=ops_category";
    return;
  }

  if (type === "high_risk") {
    window.location.href = "/admin/dashboard?view=chats&severity=high";
    return;
  }

  if (type === "automation") {
    window.location.href = "/admin/dashboard?view=chats&severity=low";
    return;
  }

  if (type === "needs_human") {
    window.location.href = "/admin/dashboard?view=chats&needs_human=true";
    return;
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const hasInsightsUI =
    document.getElementById("insight-top-issue") ||
    document.getElementById("insight-risk") ||
    document.getElementById("insight-automation") ||
    document.getElementById("insight-human");

  if (!hasInsightsUI) return;

  loadAIInsights();
});
