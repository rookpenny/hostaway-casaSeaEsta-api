async function loadAIInsights() {
  const res = await fetch("/api/analytics/ai-insights");
  const data = await res.json();

  document.getElementById("insight-top-issue").innerText =
    data.top_issue || "None";

  document.getElementById("insight-top-issue-detail").innerText =
    `${data.top_issue_count} chats`;

  document.getElementById("insight-risk").innerText =
    data.high_risk || "None";

  document.getElementById("insight-risk-detail").innerText =
    `${data.high_risk_count} high-risk chats`;

  document.getElementById("insight-automation").innerText =
    data.automation || "None";

  document.getElementById("insight-automation-detail").innerText =
    `${data.automation_count} repeat low-risk`;

  document.getElementById("insight-human").innerText =
    `${data.needs_human_pct}%`;

  document.getElementById("insight-human-detail").innerText =
    `${data.needs_human} chats`;
}
