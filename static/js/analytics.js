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
    !topIssueEl || !topIssueDetailEl ||
    !riskEl || !riskDetailEl ||
    !automationEl || !automationDetailEl ||
    !humanEl || !humanDetailEl
  ) return;

  try {
    const params = new URLSearchParams();

    const pmcFilter = document.getElementById("analyticsPmcFilter");
    const propertyFilter = document.getElementById("analyticsPropertyFilter");

    if (pmcFilter?.value) params.set("pmc_id", pmcFilter.value);
    if (propertyFilter?.value) params.set("property_id", propertyFilter.value);

    const qs = params.toString();
    const url = qs ? `/analytics/ai-insights?${qs}` : "/analytics/ai-insights";

    const res = await fetch(url, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

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
  }
}

window.applyInsightFilter = function (type) {
  const params = new URLSearchParams();
  params.set("view", "chats");

  const pmcFilter = document.getElementById("analyticsPmcFilter");
  const propertyFilter = document.getElementById("analyticsPropertyFilter");

  if (pmcFilter?.value) params.set("pmc_id", pmcFilter.value);
  if (propertyFilter?.value) params.set("property_id", propertyFilter.value);

  if (type === "top_issue") {
    params.set("conversation_group", "monitor");
  } else if (type === "high_risk") {
    params.set("conversation_group", "needs_attention");
    params.set("action_priority", "urgent");
  } else if (type === "automation") {
    params.set("conversation_group", "monitor");
    params.set("action_priority", "low");
  } else if (type === "needs_human") {
    params.set("conversation_group", "needs_attention");
  }

  window.location.href = `/admin/dashboard?${params.toString()}`;
};

document.addEventListener("DOMContentLoaded", () => {
  const hasInsightsUI = document.getElementById("insight-top-issue");
  if (!hasInsightsUI) return;

  loadAIInsights();

  document.getElementById("analyticsPmcFilter")?.addEventListener("change", loadAIInsights);
  document.getElementById("analyticsPropertyFilter")?.addEventListener("change", loadAIInsights);
});
