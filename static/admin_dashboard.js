function getBoot() {
  const el = document.getElementById("dashboard-bootstrap");
  if (!el) return {};
  try {
    return JSON.parse((el.textContent || "{}").trim());
  } catch (err) {
    console.error("Invalid dashboard bootstrap JSON", err);
    return {};
  }
}

const BOOT = getBoot();
const IS_LOCKED = !!BOOT.is_locked;
window.CONTENT_LOCKED = IS_LOCKED;

// 1) Ensure your module exists
window.Chats = window.Chats || {};


  // -----------------------------------
  // Small helpers
  // -----------------------------------
  function $(id) {
    return document.getElementById(id);
  }

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function qsa(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function safeJson(response) {
    return response.json().catch(() => ({}));
  }

  function centsToUsd(cents) {
    const n = Number(cents || 0) / 100;
    return n.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
    });
  }

  function downloadCSV(filename, csvText) {
    const blob = new Blob([csvText], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => {
      const map = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };
      return map[char];
    });
  }

  // -----------------------------------
  // View metadata + single source of truth
  // -----------------------------------



function goToView(view) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", view);
  if (view !== "chats") url.searchParams.delete("session_id");
  window.history.pushState({}, "", url.toString());
  window.dispatchEvent(new PopStateEvent("popstate"));
}

window.applyInsightFilter = function applyInsightFilter(kind) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", "chats");
  url.searchParams.delete("session_id");

  if (kind === "needs_human") {
    url.searchParams.set("q", "needs human");
  } else if (kind === "high_risk") {
    url.searchParams.set("q", "urgent");
  } else if (kind === "automation") {
    url.searchParams.set("q", "repeat");
  } else if (kind === "top_issue") {
    const topIssue = document.getElementById("insight-top-issue")?.textContent?.trim();
    if (topIssue && topIssue !== "—") {
      url.searchParams.set("q", topIssue);
    }
  }

  window.location.href = url.toString();
};
  // -----------------------------------
  // Global functions used by inline HTML
  // -----------------------------------
  window.openInsights = function openInsights() {
    $("insightsPanel")?.classList.remove("hidden");
  };

  window.closeInsights = function closeInsights() {
    $("insightsPanel")?.classList.add("hidden");
  };

  function waitForGuideEditorFields(maxAttempts = 20, delay = 150) {
    return new Promise((resolve) => {
      let attempts = 0;

      const check = () => {
        const titleInput =
          document.querySelector('#guides-editor input[name="title"]') ||
          document.querySelector('#guides-editor input[type="text"]');

        const bodyTextarea =
          document.querySelector('#guides-editor textarea[name="body"]') ||
          document.querySelector('#guides-editor textarea');

        const quillEditor =
          document.querySelector('#guides-editor .ql-editor');

        if (titleInput || bodyTextarea || quillEditor) {
          resolve({ titleInput, bodyTextarea, quillEditor });
          return;
        }

        attempts += 1;
        if (attempts >= maxAttempts) {
          resolve({ titleInput: null, bodyTextarea: null, quillEditor: null });
          return;
        }

        window.setTimeout(check, delay);
      };

      check();
    });
  }

  window.openGuideSuggestion = function openGuideSuggestion(target) {
    window.closeInsights();

    window.pendingGuideSuggestionTarget = target || "general";
    goToView("guides");

    window.setTimeout(() => {
      const guidesView = $("view-guides");
      if (guidesView) {
        guidesView.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 100);
  };

  window.openDraftSuggestion = async function openDraftSuggestion(target) {
    window.closeInsights();

    const draftTarget = target || "general";
    goToView("guides");

    try {
      const res = await fetch(
        `/admin/suggestions/draft?target=${encodeURIComponent(draftTarget)}`,
        { credentials: "include" }
      );

      if (!res.ok) {
        const txt = await res.text();
        console.error("Draft endpoint failed:", res.status, txt);
        window.alert("Could not load draft.");
        return;
      }

      const draft = await res.json();

      if (window.Guides && typeof window.Guides.openNew === "function") {
        window.Guides.openNew();
      }

      const { titleInput, bodyTextarea, quillEditor } =
        await waitForGuideEditorFields();

      if (titleInput) {
        titleInput.value = draft.title || "";
        titleInput.dispatchEvent(new Event("input", { bubbles: true }));
        titleInput.dispatchEvent(new Event("change", { bubbles: true }));
      }

      if (bodyTextarea) {
        bodyTextarea.value = draft.body || "";
        bodyTextarea.dispatchEvent(new Event("input", { bubbles: true }));
        bodyTextarea.dispatchEvent(new Event("change", { bubbles: true }));
      }

      if (quillEditor && draft.body) {
        quillEditor.innerHTML = "";
        quillEditor.textContent = draft.body;
        quillEditor.dispatchEvent(new Event("input", { bubbles: true }));
      }

      const editorWrap = $("guides-editor");
      if (editorWrap) {
        editorWrap.classList.remove("hidden");
        editorWrap.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } catch (err) {
      console.error("Draft generation failed:", err);
      window.alert("Could not generate draft.");
    }
  };

  window.updateChatsNavBadge = function updateChatsNavBadge(count) {
    const badge = $("chats-nav-badge");
    if (!badge) return;

    const n = Number(count) || 0;
    badge.textContent = String(n);
    badge.classList.toggle("hidden", n <= 0);
  };

  
window.applyTrendFilter = function applyTrendFilter(buttonEl) {
  const form = document.getElementById("chatFilters");
  const url = new URL(form?.action || window.location.pathname, window.location.origin);

  if (form) {
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) {
      const value = String(v || "").trim();
      if (value && k !== "q") url.searchParams.set(k, value);
      else url.searchParams.delete(k);
    }
  }

  let payload = {};
  try {
    payload = JSON.parse(buttonEl?.dataset?.trendFilter || "{}");
  } catch {
    payload = {};
  }

  url.searchParams.set("view", "chats");
  url.searchParams.delete("session_id");
  url.searchParams.delete("q");

  if (Object.keys(payload).length) {
    url.searchParams.set("trend_filter", encodeURIComponent(JSON.stringify(payload)));
  } else {
    url.searchParams.delete("trend_filter");
  }

  window.location.href = url.toString();
};

window.clearTrendFilter = function clearTrendFilter() {
  const form = document.getElementById("chatFilters");
  const url = new URL(form?.action || window.location.pathname, window.location.origin);

  if (form) {
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) {
      const value = String(v || "").trim();
      if (value && k !== "q") url.searchParams.set(k, value);
      else url.searchParams.delete(k);
    }
  }

  url.searchParams.set("view", "chats");
  url.searchParams.delete("trend_filter");
  url.searchParams.delete("q");
  url.searchParams.delete("session_id");

  window.location.href = url.toString();
};

function initChatFilters() {
  const form = document.getElementById("chatFilters");
  if (!form) return;

  const searchInput = form.querySelector('input[name="q"]');

  function buildUrl() {
    const url = new URL(form.action || window.location.pathname, window.location.origin);
    const fd = new FormData(form);

    url.searchParams.set("view", "chats");
    url.searchParams.delete("session_id");

    for (const [k, v] of fd.entries()) {
      const value = String(v || "").trim();
      if (value) {
        url.searchParams.set(k, value);
      } else {
        url.searchParams.delete(k);
      }
    }

    return url;
  }

  function go() {
    window.location.href = buildUrl().toString();
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    go();
  });

  form.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => {
      go();
    });
  });

  if (searchInput) {
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        go();
      }
    });
  }
}

function initChatLoadMore() {
  const btn = document.getElementById("chat-load-more");
  if (!btn) return;

  const rows = Array.from(document.querySelectorAll('#chat-list-wrap tr[data-session-row]'));
  if (!rows.length) {
    btn.classList.add("hidden");
    return;
  }

  const pageSize = 12;
  let visibleCount = pageSize;

  function render() {
    rows.forEach((row, index) => {
      row.classList.toggle("hidden", index >= visibleCount);
    });

    document.querySelectorAll(".chat-conversation-group").forEach((group) => {
      const visibleRows = group.querySelectorAll('tr[data-session-row]:not(.hidden)');
      group.classList.toggle("hidden", visibleRows.length === 0);
    });

    btn.classList.toggle("hidden", visibleCount >= rows.length);
  }

  btn.addEventListener("click", () => {
    visibleCount += pageSize;
    render();
  });

  render();
}

function setInlineDetailOpen(open) {
  const inline = document.getElementById("chat-detail-inline");
  const list = document.getElementById("chat-list-wrap");

  const chatsView = document.getElementById("view-chats");
  const filterCard = chatsView?.querySelector(".sticky.top-28");
  const insightCards = chatsView?.querySelector(".grid.gap-4");

  if (!inline || !list || !chatsView) return;

  // Toggle main sections
  inline.classList.toggle("hidden", !open);
  list.classList.toggle("hidden", open);

  // Hide/show top UI layers
  if (filterCard) filterCard.classList.toggle("hidden", open);
  if (insightCards) insightCards.classList.toggle("hidden", open);

  // Tighten spacing when in detail mode
  chatsView.classList.toggle("space-y-0", open);
  chatsView.classList.toggle("space-y-6", !open);

  // Optional: prevent weird scroll jumps
  if (open) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function pushChatUrl(sessionId) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", "chats");
  url.searchParams.set("session_id", String(sessionId));
  window.history.pushState({}, "", url.toString());
}

function clearChatUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("session_id");
  window.history.pushState({}, "", url.toString());
}

async function openChatDetail(sessionId) {
  setInlineDetailOpen(true);
  pushChatUrl(sessionId);
  await loadChatDetail(String(sessionId));
}

let routingInitialized = false;
let currentViewKey = null;

function closeChatDetail() {
  const url = new URL(window.location.href);
  url.searchParams.delete("session_id");
  url.searchParams.set("view", "chats");
  history.pushState(null, "", url.toString());

  setInlineDetailOpen(false);

  const panel = document.getElementById("chat-detail-panel");
  if (panel) {
    panel.innerHTML = `
      <div id="chat-detail-empty" class="text-sm text-slate-500">
        Select a chat session to view details.
      </div>
    `;
  }

  const pageTitle = document.getElementById("page-title");
  const pageSubtitle = document.getElementById("page-subtitle");

  if (pageTitle) pageTitle.textContent = "Conversations";
  if (pageSubtitle) {
    pageSubtitle.textContent =
      "Monitor how your AI is performing across every stage of the stay lifecycle.";
  }
}



function formatChatTimestamp(ts) {
  const d = parseTimestamp(ts);
  if (!d) return ts || "";

  const now = new Date();
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMs < 60 * 1000) return "Just now";
  if (diffMin < 60) return `${diffMin} min ago`;

  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();

  const timeOnly = d.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });

  if (sameDay) return timeOnly;

  const datePart = d.toLocaleDateString([], {
    month: "short",
    day: "numeric",
  });

  return `${datePart} · ${timeOnly}`;
}

function initChatMessageTimes(root = document) {
  function render() {
    root.querySelectorAll(".js-chat-time").forEach((el) => {
      const ts = el.getAttribute("data-ts");
      if (!ts) return;
      el.textContent = formatChatTimestamp(ts);
    });
  }

  render();
  return render;
}

window.openChatDetail = openChatDetail;

function parseTimestamp(ts) {
  if (!ts) return null;
  const normalized = ts.includes("T") ? ts : ts.replace(" ", "T");
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatRelative(fromDate, now = new Date()) {
  const diffMs = now - fromDate;
  if (diffMs < 0) return "just now";
  const sec = Math.floor(diffMs / 1000);
  if (sec < 10) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 14) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  if (wk < 8) return `${wk}w ago`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo}mo ago`;
  const yr = Math.floor(day / 365);
  return `${yr}y ago`;
}

function initRelativeTimes() {
  function updateRelativeTimes() {
    const now = new Date();
    document.querySelectorAll(".js-rel-time").forEach((el) => {
      const ts = el.getAttribute("data-ts");
      const d = parseTimestamp(ts);
      if (!d) return;
      el.textContent = formatRelative(d, now);
    });
  }

  updateRelativeTimes();
  window.setInterval(updateRelativeTimes, 60 * 1000);
}

  // -----------------------------------
  // Portfolio chart
  // -----------------------------------
  function initPortfolioChart() {
    const ctx = $("overviewPortfolioChart");
    if (!ctx || typeof Chart === "undefined") return;

    const live = Number(BOOT.live_props || 0);
    const offline = Number(BOOT.offline_props || 0);

    if (window.__overviewPortfolioChart) {
      window.__overviewPortfolioChart.destroy();
    }

    window.__overviewPortfolioChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Live", "Offline"],
        datasets: [{
          data: [live, offline],
          backgroundColor: ["#356cf6", "#47c5c9"],
          borderWidth: 0,
          hoverOffset: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        animation: {
          animateRotate: true,
          duration: 800,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f172a",
            titleColor: "#fff",
            bodyColor: "#cbd5f5",
            padding: 10,
          },
        },
      },
    });
  }

  // -----------------------------------
  // Chat batch actions
  // -----------------------------------
  function initChatBatchActions() {
    const selectAll = $("chat-select-all");
    const batchBar = $("chatBatchBar");
    const selectedCount = $("chatSelectedCount");
    const deleteBtn = $("btnChatDelete");

    function getChatCheckboxes() {
      return qsa(".chat-select");
    }

    function getSelectedIds() {
      return getChatCheckboxes()
        .filter((cb) => cb.checked)
        .map((cb) => cb.value);
    }

    function updateBatchBar() {
      const ids = getSelectedIds();

      if (selectedCount) {
        selectedCount.textContent = `${ids.length} selected`;
      }

      if (batchBar) {
        batchBar.classList.toggle("hidden", ids.length === 0);
      }

      if (selectAll) {
        const all = getChatCheckboxes();
        const checked = all.filter((cb) => cb.checked);
        selectAll.checked = all.length > 0 && checked.length === all.length;
        selectAll.indeterminate = checked.length > 0 && checked.length < all.length;
      }
    }

    selectAll?.addEventListener("change", () => {
      const checked = selectAll.checked;
      getChatCheckboxes().forEach((cb) => {
        cb.checked = checked;
      });
      updateBatchBar();
    });

    document.addEventListener("change", (e) => {
      if (e.target instanceof Element && e.target.matches(".chat-select")) {
        updateBatchBar();
      }
    });

    deleteBtn?.addEventListener("click", async () => {
      const ids = getSelectedIds();
      if (!ids.length) return;

      const ok = window.confirm(
        ids.length === 1 ? "Delete this chat?" : `Delete ${ids.length} chats?`
      );
      if (!ok) return;

      deleteBtn.disabled = true;

      try {
        const res = await fetch("/admin/chats/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ session_ids: ids }),
        });

        const data = await safeJson(res);

        if (!res.ok) {
          window.alert(data.detail || "Failed to delete chats.");
          return;
        }

        window.location.reload();
      } catch (err) {
        console.error(err);
        window.alert("Something went wrong while deleting chats.");
      } finally {
        deleteBtn.disabled = false;
      }
    });

    updateBatchBar();
  }

  // -----------------------------------
  // Single chat delete
  // -----------------------------------
  function initChatDetailDelete() {
    document.addEventListener("click", async (e) => {
      const btn = e.target instanceof Element
        ? e.target.closest('[data-role="delete-chat-btn"]')
        : null;
      if (!btn) return;

      const panel = qs("[data-chat-panel]");
      const sessionId =
        panel?.getAttribute("data-session-id") ||
        panel?.getAttribute("data-chat-panel");

      if (!sessionId) {
        window.alert("Could not find chat ID.");
        return;
      }

      const ok = window.confirm("Delete this chat?");
      if (!ok) return;

      btn.disabled = true;

      try {
        const res = await fetch("/admin/chats/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ session_ids: [sessionId] }),
        });

        const data = await safeJson(res);

        if (!res.ok) {
          window.alert(data.detail || "Failed to delete chat.");
          return;
        }

        window.location.href = "/admin/dashboard?view=chats";
      } catch (err) {
        console.error(err);
        window.alert("Something went wrong while deleting the chat.");
      } finally {
        btn.disabled = false;
      }
    });
  }

  // -----------------------------------
  // Revenue reports
  // -----------------------------------
  function initRevenueReports() {
    const hsRunBtn = $("hs-rev-run");
    const hsCsvBtn = $("hs-rev-csv");
    const pmcRunBtn = $("pmc-pay-run");
    const pmcCsvBtn = $("pmc-pay-csv");

    async function runHostScoutRevenue() {
      const start = $("hs-rev-start")?.value || "";
      const end = $("hs-rev-end")?.value || "";

      const qsObj = new URLSearchParams();
      if (start) qsObj.set("start", start);
      if (end) qsObj.set("end", end);

      const res = await fetch(`/admin/reports/hostscout-revenue?${qsObj.toString()}`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });

      const data = await safeJson(res);
      if (!res.ok) {
        window.alert(data?.detail || "Revenue report failed");
        return;
      }

      $("hs-gross").textContent = centsToUsd(data.summary.gross_cents);
      $("hs-fee").textContent = centsToUsd(data.summary.hostscout_fee_cents);
      $("hs-net").textContent = centsToUsd(data.summary.net_to_pmc_cents);
      $("hs-count").textContent = `${data.summary.paid_count} / ${data.summary.refunded_count}`;

      const tbody = $("hs-rows");
      if (!tbody) return;

      tbody.innerHTML = "";

      for (const r of data.by_pmc || []) {
        const name =
          r.pmc && (r.pmc.name || r.pmc.email)
            ? r.pmc.name || r.pmc.email
            : `PMC ${r.pmc_id}`;

        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="py-2 pr-3 font-semibold">${escapeHtml(name)}</td>
          <td class="py-2 pr-3">${centsToUsd(r.gross_cents)}</td>
          <td class="py-2 pr-3">${centsToUsd(r.hostscout_fee_cents)}</td>
          <td class="py-2 pr-3">${centsToUsd(r.net_to_pmc_cents)}</td>
          <td class="py-2 pr-3">${Number(r.paid_count || 0)}</td>
          <td class="py-2 pr-3">${Number(r.refunded_count || 0)}</td>
        `;
        tbody.appendChild(tr);
      }

      window.__hs_rev_latest = data;
    }

    async function runPmcPayouts() {
      const start = $("pmc-pay-start")?.value || "";
      const end = $("pmc-pay-end")?.value || "";

      const qsObj = new URLSearchParams();
      if (start) qsObj.set("start", start);
      if (end) qsObj.set("end", end);

      const res = await fetch(`/admin/reports/pmc-payouts?${qsObj.toString()}`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });

      const data = await safeJson(res);
      if (!res.ok) {
        window.alert(data?.detail || "PMC payouts report failed");
        return;
      }

      $("pmc-gross").textContent = centsToUsd(data.summary.gross_cents);
      $("pmc-fee").textContent = centsToUsd(data.summary.hostscout_fee_cents);
      $("pmc-net").textContent = centsToUsd(data.summary.net_to_pmc_cents);
      $("pmc-count").textContent = `${data.summary.paid_count} / ${data.summary.refunded_count}`;

      const tbody = $("pmc-pay-rows");
      if (!tbody) return;

      tbody.innerHTML = "";

      for (const r of data.rows || []) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="py-2 pr-3">${escapeHtml(r.paid_at || "—")}</td>
          <td class="py-2 pr-3">${escapeHtml(r.property_name || `Property ${r.property_id ?? ""}`)}</td>
          <td class="py-2 pr-3">${escapeHtml(r.upgrade_title || `Upgrade ${r.upgrade_id ?? ""}`)}</td>
          <td class="py-2 pr-3">${centsToUsd(r.amount_cents)}</td>
          <td class="py-2 pr-3">${centsToUsd(r.platform_fee_cents)}</td>
          <td class="py-2 pr-3">${centsToUsd((r.amount_cents || 0) - (r.platform_fee_cents || 0))}</td>
          <td class="py-2 pr-3">${escapeHtml(r.status || "—")}</td>
        `;
        tbody.appendChild(tr);
      }

      window.__pmc_pay_latest = data;
    }

    hsRunBtn?.addEventListener("click", runHostScoutRevenue);

    hsCsvBtn?.addEventListener("click", () => {
      const data = window.__hs_rev_latest;
      if (!data?.by_pmc) {
        window.alert("Run the report first.");
        return;
      }

      const header = [
        "pmc_id",
        "pmc_name",
        "gross_cents",
        "hostscout_fee_cents",
        "net_to_pmc_cents",
        "paid_count",
        "refunded_count",
      ];
      const lines = [header.join(",")];

      for (const r of data.by_pmc) {
        const pmcName =
          r.pmc && (r.pmc.name || r.pmc.email)
            ? String(r.pmc.name || r.pmc.email).replaceAll('"', '""')
            : "";

        lines.push([
          r.pmc_id ?? "",
          `"${pmcName}"`,
          r.gross_cents ?? 0,
          r.hostscout_fee_cents ?? 0,
          r.net_to_pmc_cents ?? 0,
          r.paid_count ?? 0,
          r.refunded_count ?? 0,
        ].join(","));
      }

      downloadCSV(
        `hostscout_revenue_${data.range.start}_to_${data.range.end}.csv`,
        lines.join("\n")
      );
    });

    pmcRunBtn?.addEventListener("click", runPmcPayouts);

    pmcCsvBtn?.addEventListener("click", () => {
      const data = window.__pmc_pay_latest;
      if (!data?.rows) {
        window.alert("Run the report first.");
        return;
      }

      const header = [
        "paid_at",
        "property_id",
        "property_name",
        "upgrade_id",
        "upgrade_title",
        "amount_cents",
        "platform_fee_cents",
        "net_cents",
        "status",
      ];
      const lines = [header.join(",")];

      for (const r of data.rows) {
        const propName = String(r.property_name || "").replaceAll('"', '""');
        const upName = String(r.upgrade_title || "").replaceAll('"', '""');

        lines.push([
          r.paid_at || "",
          r.property_id ?? "",
          `"${propName}"`,
          r.upgrade_id ?? "",
          `"${upName}"`,
          r.amount_cents ?? 0,
          r.platform_fee_cents ?? 0,
          (r.amount_cents || 0) - (r.platform_fee_cents || 0),
          r.status || "",
        ].join(","));
      }

      downloadCSV(
        `pmc_payouts_${data.range.start}_to_${data.range.end}.csv`,
        lines.join("\n")
      );
    });
  }

  // -----------------------------------
  // Notifications
  // -----------------------------------
  window.Notifications = window.Notifications || (function () {
    let all = [];
    let filter = "all";
    let wired = false;

    function fmtTime(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      return d.toLocaleString();
    }

    function setUnreadBadge() {
      const badge = $("tasksNotifUnreadBadge");
      if (!badge) return;

      const unread = all.filter((n) => !n.is_read).length;
      if (unread > 0) {
        badge.classList.remove("hidden");
        badge.textContent = String(unread);
      } else {
        badge.classList.add("hidden");
        badge.textContent = "0";
      }
    }

    function applyFilter(items) {
      if (filter === "all") return items;
      if (filter === "unread") return items.filter((n) => !n.is_read);
      return items.filter((n) => String(n.type || "") === filter);
    }

    function openNotification(n) {
      const t = String(n.type || "");
      const meta = n.meta || {};

      if (t === "chat_assigned" && meta.chat_session_id) {
        window.location.href = `/admin/dashboard?view=chats&session_id=${encodeURIComponent(meta.chat_session_id)}`;
        return;
      }

      if (t === "upgrade_purchased") {
        window.location.href = "/admin/dashboard?view=upgrades";
        return;
      }

      window.location.href = "/admin/dashboard?view=tasks";
    }

    async function markRead(id) {
      const res = await fetch(`/admin/notifications/${encodeURIComponent(id)}/read`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      return res.ok;
    }

    function render() {
      const list = $("tasksNotifList");
      if (!list) return;

      const items = applyFilter(all);

      if (!items.length) {
        list.innerHTML = `<div class="text-slate-500 text-sm">No notifications.</div>`;
        return;
      }

      list.innerHTML = items
        .map((n) => {
          const unreadDot = n.is_read
            ? ""
            : `<span class="inline-block w-2 h-2 rounded-full bg-emerald-500 mr-2"></span>`;

          return `
            <div class="border border-slate-200 bg-white rounded-2xl p-4 mb-3 cursor-pointer hover:bg-slate-50"
                 data-notif-id="${escapeHtml(n.id)}">
              <div class="flex items-start justify-between gap-4">
                <div class="min-w-0">
                  <div class="font-semibold text-slate-900 flex items-center">
                    ${unreadDot}
                    <span class="truncate">${escapeHtml(n.title || "Notification")}</span>
                  </div>
                  ${n.body ? `<div class="text-sm text-slate-600 mt-1">${escapeHtml(n.body)}</div>` : ""}
                  <div class="text-xs text-slate-400 mt-2">${escapeHtml(n.type || "")}</div>
                </div>
                <div class="text-xs text-slate-400 whitespace-nowrap">${escapeHtml(fmtTime(n.created_at))}</div>
              </div>
            </div>
          `;
        })
        .join("");

      qsa("[data-notif-id]", list).forEach((row) => {
        row.addEventListener("click", async () => {
          const id = row.getAttribute("data-notif-id");
          const n = all.find((x) => String(x.id) === String(id));
          if (!n) return;

          if (!n.is_read) {
            await markRead(id);
            n.is_read = true;
            setUnreadBadge();
          }

          openNotification(n);
        });
      });
    }

    async function load() {
      const list = $("tasksNotifList");
      if (list) {
        list.innerHTML = `<div class="text-slate-500 text-sm">Loading…</div>`;
      }

      const res = await fetch("/admin/notifications?limit=200", {
        credentials: "include",
      });

      const data = await safeJson(res);

      if (!res.ok) {
        if (list) {
          list.innerHTML = `
            <div class="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-2xl p-4">
              Failed to load notifications.
            </div>
          `;
        }
        return;
      }

      all = Array.isArray(data.items) ? data.items : [];
      setUnreadBadge();
      render();
    }

    async function markAllRead() {
      const unread = all.filter((n) => !n.is_read);
      if (!unread.length) return;

      await Promise.allSettled(unread.map((n) => markRead(n.id)));
      all.forEach((n) => {
        n.is_read = true;
      });

      setUnreadBadge();
      render();
    }

    function wireUIOnce() {
      if (wired) return;
      wired = true;

      $("tasksNotifRefreshBtn")?.addEventListener("click", load);
      $("tasksNotifMarkAllReadBtn")?.addEventListener("click", markAllRead);

      qsa(".tasksNotifFilterBtn").forEach((btn) => {
        btn.addEventListener("click", () => {
          filter = btn.getAttribute("data-filter") || "all";
          render();
        });
      });
    }

    function showInTasks(show) {
      const panel = $("tasksNotificationsPanel");
      if (!panel) return;

      if (show) {
        panel.classList.remove("hidden");
        wireUIOnce();
        load();
      } else {
        panel.classList.add("hidden");
      }
    }

    return { showInTasks };
  })();


// ----------------------------
// START OF CONFIG PARTIAL (CLEAN)
// ----------------------------

window.initConfigUI = function initConfigUI(hostEl) {
  if (!hostEl) return;
  if (hostEl.__configUIInited) return;

  // ✅ Scoped DOM helpers
  const $ = (id) => {
    const el = hostEl.querySelector(`#${CSS.escape(id)}`);
    if (!el) throw new Error(`Config UI missing required element #${id}`);
    return el;
  };
  const $$ = (sel) => Array.from(hostEl.querySelectorAll(sel));

  // ✅ Bootstrap
  const bootTag = hostEl.querySelector("#config-ui-bootstrap");
  if (!bootTag) {
    console.warn("Config UI bootstrap tag not found (#config-ui-bootstrap). Aborting init.");
    return;
  }

  let boot = {};
  try {
    boot = JSON.parse((bootTag.textContent || "{}").trim());
  } catch (e) {
    console.error("Config UI bootstrap JSON parse failed:", e);
    boot = {};
  }

  hostEl.__configUIInited = true;
  hostEl.__configUIAlive = true;

  function getFilePath() {
    if (!hostEl) return "";

    const hiddenEl = hostEl.querySelector("#configFilePath");
    if (hiddenEl && hiddenEl.value) return String(hiddenEl.value).trim();

    if (boot && typeof boot === "object" && boot.file_path) return String(boot.file_path).trim();

    if (hostEl.dataset.filePath) return String(hostEl.dataset.filePath).trim();

    return "";
  }

  const IS_DEFAULTS = !!boot.is_defaults;
  const DEFAULT_WELCOME_NO_NAME =
    "Hi there! I’m {{assistant_name}}, your stay assistant for {{property_name}}.";

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function deepClone(x) {
    return JSON.parse(JSON.stringify(x || {}));
  }

  function splitLines(text) {
    return (text || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  function joinLines(arr) {
    return (arr || []).join("\n");
  }

  function escapeHtml(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function sanitizePreviewText(s) {
    s = String(s || "");
    s = s.replace(/[\u200B-\u200D\uFEFF]/g, "");
    s = s.replace(/\s+/g, " ").trim();
    if (s.length > 260) s = s.slice(0, 260) + "…";
    return s;
  }

  // -----------------------
  // State
  // -----------------------
  let cfg = deepClone(boot.config_json || {});
  let dirty = false;
  let saving = false;
  let editingRaw = false;
  // State
let selected = new Set();
let activeTab = "all";
let TEAM = [];
const TEAM_BY_ID = new Map();
// Categories (synced from /admin/api/tasks)
let CATEGORIES = [];


  

  function ensureShape() {
    cfg.assistant = cfg.assistant || {};
    const a = cfg.assistant;
    a.voice = a.voice || {};
    a.do = Array.isArray(a.do) ? a.do : a.do ? [String(a.do)] : [];
    a.dont = Array.isArray(a.dont) ? a.dont : a.dont ? [String(a.dont)] : [];
    a.quick_replies = Array.isArray(a.quick_replies)
      ? a.quick_replies
      : a.quick_replies
      ? [String(a.quick_replies)]
      : [];
  }

  function setStatus(kind, text) {
    const dot = hostEl.querySelector("#dot");
    const statusText = hostEl.querySelector("#statusText");
    if (dot) dot.className = "status-dot " + (kind || "");
    if (statusText) statusText.textContent = text || "";
  }

  // -----------------------
  // Preview (cheap updates)
  // -----------------------
  function updatePreviewOnly() {
    ensureShape();
    const a = cfg.assistant;

    const pvName = hostEl.querySelector("#pvName");
    const pvName2 = hostEl.querySelector("#pvName2");
    const pvWelcome = hostEl.querySelector("#pvWelcome");
    const pvReply = hostEl.querySelector("#pvReply");

    const name = a.name || "Sandy";
    if (pvName) pvName.textContent = name;
    if (pvName2) pvName2.textContent = name;

    if (pvWelcome) {
      const tpl = String(a.voice?.welcome_template_no_name || "").trim();

      const propertyName =
        (boot && String(boot.property_name || "").trim()) || "this property";
      
      let welcomeText = tpl
        ? tpl
            .replaceAll("{{assistant_name}}", name)
            .replaceAll("{{property_name}}", propertyName)
        : DEFAULT_WELCOME_NO_NAME
            .replaceAll("{{assistant_name}}", name)
            .replaceAll("{{property_name}}", propertyName);

      pvWelcome.textContent = sanitizePreviewText(welcomeText);
    }

    if (pvReply) {
      const v = a.verbosity || "balanced";
      const t = a.tone || "luxury";

      let reply =
        "<b>WiFi</b><br/>Network: …<br/>Password: …<br/><br/><b>Parking</b><br/>Here are the best options…";
      if (v === "short")
        reply = "<b>WiFi</b><br/>Network: … / Password: …<br/><b>Parking</b><br/>Best option: …";
      if (v === "detailed")
        reply =
          "<b>WiFi</b><br/>Network: …<br/>Password: …<br/>Tip: …<br/><br/><b>Parking</b><br/>Options: …<br/>Notes: …<br/>Map: …";
      if (t === "luxury")
        reply = reply.replace(
          "Here are the best options…",
          "Here are the best options, tailored for a smooth arrival…"
        );

      pvReply.innerHTML = reply;
    }
  }

  // -----------------------
  // Render (full)
  // -----------------------
  function renderQuickReplies() {
    const list = hostEl.querySelector("#quickRepliesList");
    if (!list) return;

    list.innerHTML = "";
    const a = cfg.assistant || {};

    (a.quick_replies || []).forEach((txt, idx) => {
      const row = document.createElement("div");
      row.className = "list-item";
      row.draggable = true;
      row.dataset.idx = String(idx);

      row.innerHTML = `
        <div class="row" style="gap:10px;">
          <span class="drag">⋮⋮</span>
          <span>${escapeHtml(txt)}</span>
        </div>
        <div class="row" style="gap:8px;">
          <button class="btn" data-act="up" type="button">↑</button>
          <button class="btn" data-act="down" type="button">↓</button>
          <button class="btn danger" data-act="del" type="button">Remove</button>
        </div>
      `;

      row.addEventListener("click", (e) => {
        const act = e.target?.dataset?.act;
        if (!act) return;
        e.preventDefault();

        if (act === "del") {
          a.quick_replies.splice(idx, 1);
          markDirtyAndRender();
        } else if (act === "up" && idx > 0) {
          [a.quick_replies[idx - 1], a.quick_replies[idx]] = [a.quick_replies[idx], a.quick_replies[idx - 1]];
          markDirtyAndRender();
        } else if (act === "down" && idx < a.quick_replies.length - 1) {
          [a.quick_replies[idx + 1], a.quick_replies[idx]] = [a.quick_replies[idx], a.quick_replies[idx + 1]];
          markDirtyAndRender();
        }
      });

      row.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", row.dataset.idx);
      });
      row.addEventListener("dragover", (e) => e.preventDefault());
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = parseInt(e.dataTransfer.getData("text/plain"), 10);
        const to = parseInt(row.dataset.idx, 10);
        if (Number.isNaN(from) || Number.isNaN(to) || from === to) return;

        const item = a.quick_replies.splice(from, 1)[0];
        a.quick_replies.splice(to, 0, item);
        markDirtyAndRender();
      });

      list.appendChild(row);
    });

    if ((a.quick_replies || []).length === 0) {
      const empty = document.createElement("div");
      empty.className = "muted small";
      empty.textContent = "No quick replies yet.";
      list.appendChild(empty);
    }
  }

  function render() {
    ensureShape();
    const a = cfg.assistant;

    $("assistant_name").value = a.name || "Sandy";
    $("assistant_avatar_url").value = a.avatar_url || "/static/img/sandy.png";
    $("assistant_tone").value = a.tone || "luxury";
    $("assistant_verbosity").value = a.verbosity || "balanced";
    $("assistant_emoji_level").value = a.emoji_level || "light";
    $("assistant_formality").value = a.formality || "polished";
    $("assistant_style").value = a.style || "";
    $("assistant_extra_instructions").value = a.extra_instructions || "";

    $("assistant_do").value = joinLines(a.do);
    $("assistant_dont").value = joinLines(a.dont);

    $("voice_welcome_template").value = a.voice.welcome_template || "";
    $("voice_welcome_template_no_name").value =
      a.voice.welcome_template_no_name || DEFAULT_WELCOME_NO_NAME;
    $("voice_offline_message").value = a.voice.offline_message || "";
    $("voice_fallback_message").value = a.voice.fallback_message || "";
    $("voice_error_message").value = a.voice.error_message || "";

    renderQuickReplies();

    if (!editingRaw) $("rawJson").value = JSON.stringify(cfg, null, 2);

    updatePreviewOnly();
  }

  // -----------------------
  // Form -> cfg
  // -----------------------
  function readFormIntoCfg() {
    ensureShape();
    const a = cfg.assistant;

    a.name = $("assistant_name").value.trim() || "Sandy";
    a.avatar_url = $("assistant_avatar_url").value.trim() || "/static/img/sandy.png";
    a.tone = $("assistant_tone").value;
    a.verbosity = $("assistant_verbosity").value;
    a.emoji_level = $("assistant_emoji_level").value;
    a.formality = $("assistant_formality").value;
    a.style = $("assistant_style").value.trim();
    a.extra_instructions = $("assistant_extra_instructions").value.trim();

    a.do = splitLines($("assistant_do").value);
    a.dont = splitLines($("assistant_dont").value);

    a.voice.welcome_template = $("voice_welcome_template").value;
    a.voice.welcome_template_no_name = $("voice_welcome_template_no_name").value;
    a.voice.offline_message = $("voice_offline_message").value;
    a.voice.fallback_message = $("voice_fallback_message").value;
    a.voice.error_message = $("voice_error_message").value;
  }

  function markDirtyAndRender() {
    dirty = true;
    setStatus("warn", "Unsaved changes…");
    render();
    scheduleAutosave();
  }

  // -----------------------
  // Save
  // -----------------------
  async function saveNow() {
    if (!hostEl.__configUIAlive) return;
    if (saving) return;

    saving = true;
    const btnSave = hostEl.querySelector("#btnSave");
    if (btnSave) btnSave.disabled = true;

    setStatus("warn", "Saving…");

    try {
      readFormIntoCfg();

      const file_path = getFilePath();
      if (!file_path) throw new Error("Missing file_path");

      const resp = await fetch("/admin/config-ui/save", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path, config: cfg }),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "Save failed");

      dirty = false;
      setStatus("ok", "Saved ✓");
    } catch (e) {
      console.error(e);
      setStatus("err", "Save failed: " + (e.message || e));
    } finally {
      saving = false;
      if (btnSave) btnSave.disabled = false;
    }
  }

  const scheduleAutosave = debounce(() => {
    if (dirty) saveNow();
  }, 900);

  // -----------------------
  // Reset
  // -----------------------
  async function resetToDefaults() {
    if (IS_DEFAULTS) {
      setStatus("ok", "You are editing defaults already.");
      return;
    }

    if (!confirm("Reset this config to defaults? This will overwrite the current file.")) return;

    try {
      setStatus("warn", "Resetting…");

      const file_path = getFilePath();
      if (!file_path) throw new Error("Missing file_path");

      const resp = await fetch("/admin/config-ui/reset", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path }),
      });

      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) throw new Error(data.error || "Reset failed");

      cfg = deepClone(data.config || {});
      dirty = false;
      render();
      setStatus("ok", "Reset to defaults ✓");
    } catch (e) {
      console.error(e);
      setStatus("err", "Reset failed: " + (e.message || e));
    }
  }

  // -----------------------
  // Raw JSON helpers
  // -----------------------
  function applyRawToForm() {
    try {
      const parsed = JSON.parse($("rawJson").value);
      cfg = deepClone(parsed);
      dirty = true;
      render();
      setStatus("warn", "Applied raw JSON (unsaved)...");
      scheduleAutosave();
    } catch (e) {
      alert("Invalid JSON: " + (e.message || e));
    }
  }

  function syncFormToRaw() {
    readFormIntoCfg();
    $("rawJson").value = JSON.stringify(cfg, null, 2);
    setStatus("warn", "Synced form → raw JSON (unsaved)...");
    dirty = true;
    scheduleAutosave();
  }

    // -----------------------
  // Wire events (scoped)
  // -----------------------
  function wire() {
    $("rawJson").addEventListener("focus", () => (editingRaw = true));
    $("rawJson").addEventListener("blur", () => (editingRaw = false));

    const onAnyChange = () => {
      readFormIntoCfg();
      dirty = true;
      setStatus("warn", "Unsaved changes…");
      if (!editingRaw) $("rawJson").value = JSON.stringify(cfg, null, 2);
      updatePreviewOnly();
      scheduleAutosave();
    };

    $$("input, textarea, select").forEach((el) => {
      if (el.id === "rawJson") return;
      el.addEventListener("input", onAnyChange);
      el.addEventListener("change", onAnyChange);
    });

    $("btnSave").addEventListener("click", (e) => {
      e.preventDefault();
      saveNow();
    });

    $("btnReload").addEventListener("click", async (e) => {
      e.preventDefault();

      const fp = getFilePath();
      if (!fp) return setStatus("err", "Missing file_path");

      setStatus("warn", "Reloading…");

      window.__configInlineOpenToken = (window.__configInlineOpenToken || 0) + 1;
      const myToken = window.__configInlineOpenToken;

      try {
        const res = await fetch(
          `/admin/config-ui?file=${encodeURIComponent(fp)}&embed=1`,
          { credentials: "include" }
        );

        const html = await res.text();

        if (myToken !== window.__configInlineOpenToken || !hostEl.__configUIAlive) return;

        hostEl.innerHTML = res.ok
          ? html
          : `<div class="p-4 text-rose-700">Failed to load config</div>`;

        delete hostEl.__configUIInited;

        hostEl.dataset.filePath = fp;
        const fpInput = hostEl.querySelector("#configFilePath");
        if (fpInput) fpInput.value = fp;

        window.initConfigUI(hostEl);
      } catch (err) {
        console.error(err);
        setStatus("err", "Reload failed");
      }
    });

    const resetBtn = hostEl.querySelector("#btnResetAll");
    if (resetBtn) resetBtn.addEventListener("click", (e) => {
      e.preventDefault();
      resetToDefaults();
    });

    $("btnApplyRaw").addEventListener("click", (e) => {
      e.preventDefault();
      applyRawToForm();
    });

    $("btnSyncRaw").addEventListener("click", (e) => {
      e.preventDefault();
      syncFormToRaw();
    });

    const addBtn = hostEl.querySelector("#btnAddQuickReply");
    const input = hostEl.querySelector("#quickReplyInput");
    if (addBtn && input) {
      addBtn.addEventListener("click", (e) => {
        e.preventDefault();
        const v = input.value.trim();
        if (!v) return;
        ensureShape();
        cfg.assistant.quick_replies.push(v);
        input.value = "";
        markDirtyAndRender();
      });
    }
  }

  // init
  try {
    ensureShape();
    render();
    wire();
    setStatus("", "Loaded.");
  } catch (err) {
    console.error("Config UI init failed:", err);
    setStatus("err", err?.message || "Config UI init failed (check console).");
  }
};





// ----------------------------
// Inline open/close (CLEAN)
// ----------------------------
window.openInlineConfig = async function (e, filePath) {
  if (e && typeof e.preventDefault === "function") e.preventDefault();

  const hostEl = document.getElementById("configInlineContainer");
  const wrap = document.getElementById("configPanelWrap");
  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");
  if (!hostEl || !wrap) return false;

  window.__configInlineOpenToken = (window.__configInlineOpenToken || 0) + 1;
  const myToken = window.__configInlineOpenToken;

  hostEl.__configUIAlive = true;
  delete hostEl.__configUIInited;
  hostEl.dataset.filePath = filePath;

  hostEl.innerHTML = `<div class="p-4 muted">Loading config…</div>`;

  try {
    const res = await fetch(
      `/admin/config-ui?file=${encodeURIComponent(filePath)}&embed=1`,
      { credentials: "include" }
    );

    const html = await res.text();

    if (myToken !== window.__configInlineOpenToken) return false;

    if (!res.ok) {
      hostEl.innerHTML = `<div class="p-4 text-rose-700">Failed to load config (${res.status})</div>`;
      return false;
    }

    hostEl.innerHTML = html;

    const fpInput = hostEl.querySelector("#configFilePath");
    if (fpInput) fpInput.value = filePath;

    window.initConfigUI?.(hostEl);

    wrap.classList.remove("hidden");
    grid?.classList.add("hidden");
    header?.classList.add("hidden");
  } catch (err) {
    console.error("openInlineConfig failed:", err);
    hostEl.innerHTML = `<div class="p-4 text-rose-700">Failed to load config</div>`;
  }

  return false;
};


window.closeInlineConfig = function () {
  const host = document.getElementById("configInlineContainer");
  if (host) host.__configUIAlive = false;

  window.__configInlineOpenToken = (window.__configInlineOpenToken || 0) + 1;

  const wrap = document.getElementById("configPanelWrap");
  const label = document.getElementById("configScopeLabel");
  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");

  if (host) {
    host.innerHTML = "";
    delete host.__configUIInited;
    delete host.dataset.filePath;
  }

  if (label) label.textContent = "Editing…";

  wrap?.classList.add("hidden");
  grid?.classList.remove("hidden");
  header?.classList.remove("hidden");
  header?.scrollIntoView?.({ behavior: "smooth", block: "start" });
};




// ----------------------------
// API route helper (from bootstrap)
// ----------------------------
const API = (BOOT && BOOT.api) ? BOOT.api : {};

function expandRoute(template, params = {}) {
  if (!template) return "";
  return String(template).replace(/\{(\w+)\}/g, (_, k) =>
    encodeURIComponent(params[k] ?? "")
  );
}

function apiRoute(key, params = {}) {
  const t = API[key];
  if (!t) return ""; // fallback handled by callers
  // support both "/path/{id}" and "/path?x={id}"
  return expandRoute(t, params);
}


// ✅ Expose helpers (Guest Mood / emotional_signals)
window.getMoodForEl = function getMoodForEl(el) {
  if (!el) return [];
  const raw = el.getAttribute("data-emotional-signals") || "[]";
  return window.normalizeEmotionalSignals?.(raw) || [];
};



function applyMoodConfidenceHints(root = document) {
  const nodes = root.querySelectorAll("[data-mood-badge]");
  for (const el of nodes) {
    const conf = parseInt(el.getAttribute("data-guest-mood-confidence") || "0", 10);
    if (!conf) continue;

    // After renderMoodBadges runs, badges are spans
    const badge = el.querySelector("span");
    if (!badge) continue;

    if (conf < 60) {
      badge.classList.add("opacity-70");
      badge.title = `Low confidence mood (${conf}%)`;
    } else {
      badge.classList.remove("opacity-70");
      if (badge.title && badge.title.includes("confidence")) badge.title = "";
    }
  }
}
window.applyMoodConfidenceHints = applyMoodConfidenceHints;




// =====================================================
// SIGNALS (backend-only)
// - Source of truth: data-signals (JSON array or comma string)
// - No sentiment, no derivation
// =====================================================

// =====================================================
// GUEST MOOD (emotional_signals) — backend-derived only
// Source of truth: data-emotional-signals (JSON array or comma string)
// =====================================================
(function GuestMoodOnly() {
  function normalizeEmotionalSignals(signalsRaw) {
    if (Array.isArray(signalsRaw)) {
      return signalsRaw.map(s => String(s || "").toLowerCase().trim()).filter(Boolean);
    }

    let s = String(signalsRaw ?? "").trim();
    if (!s) return [];

    const lower = s.toLowerCase();
    if (lower === "none" || lower === "null" || lower === "undefined") return [];

    // JSON string?
    try {
      const parsed = JSON.parse(s);
      if (Array.isArray(parsed)) {
        return parsed.map(x => String(x || "").toLowerCase().trim()).filter(Boolean);
      }
      if (typeof parsed === "string") s = parsed.trim();
    } catch (_) {}

    // Python-ish list string?
    if (s.startsWith("[") && s.endsWith("]") && s.includes("'")) {
      try {
        const coerced = s.replace(/'/g, '"');
        const parsed2 = JSON.parse(coerced);
        if (Array.isArray(parsed2)) {
          return parsed2.map(x => String(x || "").toLowerCase().trim()).filter(Boolean);
        }
      } catch (_) {}
    }

    // comma-separated fallback
    return s.split(",").map(x => x.trim().toLowerCase()).filter(Boolean);
  }

  // Expose normalizer for other modules
  window.normalizeEmotionalSignals = normalizeEmotionalSignals;

  function hydrateMoodAttr(root = document) {
      root.querySelectorAll("[data-mood-badge]").forEach((el) => {
        const hasSignals = el.hasAttribute("data-emotional-signals");
        if (hasSignals) return;
    
        const gm = (el.getAttribute("data-guest-mood") || "").trim().toLowerCase();
        if (!gm || gm === "null" || gm === "none" || gm === "undefined") return;
    
        // Map guest mood -> emotional_signals list (your renderer expects a list)
        el.setAttribute("data-emotional-signals", JSON.stringify([gm]));
      });
    }
    window.hydrateMoodAttr = hydrateMoodAttr;


  function pill(text, cls) {
    return `<span class="inline-block px-2 py-1 rounded-full font-semibold mr-1 ${cls}">${text}</span>`;
  }

  window.renderMoodBadges = function renderMoodBadges(el, emotionalSignals) {
    if (!el) return;

    const sig = normalizeEmotionalSignals(emotionalSignals);
    let html = "";

    if (sig.includes("panicked")) html += pill("😰 Panicked", "bg-rose-100 text-rose-700");
    if (sig.includes("angry"))    html += pill("😡 Angry",    "bg-rose-200 text-rose-900");
    if (sig.includes("upset"))    html += pill("😟 Upset",    "bg-amber-100 text-amber-800");
    if (sig.includes("confused")) html += pill("😕 Confused", "bg-blue-100 text-blue-700");
    if (sig.includes("worried"))  html += pill("🥺 Worried",  "bg-indigo-100 text-indigo-700");
    if (sig.includes("happy"))    html += pill("😊 Happy",    "bg-yellow-100 text-yellow-800");

    // Calm only if explicitly present
    if (sig.includes("calm")) {
      html += pill("🙂 Calm", "bg-emerald-100 text-emerald-700");
    }

    el.innerHTML = html || `<span class="text-slate-400">—</span>`;
  };

  window.setMoodBadge = function setMoodBadge(container, emotionalSignals) {
    if (!container) return;

    try {
      container.setAttribute("data-emotional-signals", JSON.stringify(emotionalSignals || []));
    } catch {
      container.setAttribute("data-emotional-signals", String(emotionalSignals || ""));
    }

    window.renderMoodBadges(container, emotionalSignals || []);
  };

function rerenderAllMoodBadges(root = document) {
  hydrateMoodAttr(root);

  root.querySelectorAll("[data-mood-badge]").forEach((el) => {
    const raw = el.getAttribute("data-emotional-signals") || "[]";
    let parsed = [];
    try { parsed = JSON.parse(raw); } catch { parsed = raw; }
    window.renderMoodBadges(el, parsed);
  });
}

window.rerenderAllMoodBadges = rerenderAllMoodBadges;

})();




// Back button
document.addEventListener("click", (e) => {
  const btn = e.target.closest("#chat-detail-back");
  if (!btn) return;

  e.preventDefault();
  closeChatDetail();
});

(function initAdminChatPanelGlobal() {
  if (window.__ADMIN_CHAT_PANEL_GLOBAL_INIT__) return;
  window.__ADMIN_CHAT_PANEL_GLOBAL_INIT__ = true;

  function storageKey(chatId, which) {
    return `admin_chat_${chatId}_${which}_open`;
  }

  function isTypingTarget(el) {
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || el.isContentEditable;
  }

  function setOpen(chatId, which, open, root = document) {
    const body = root.querySelector(
      `.js-toggle-body[data-toggle-body="${which}"][data-chat-id="${chatId}"]`
    );
    const btn = root.querySelector(
      `.js-toggle[data-toggle-target="${which}"][data-chat-id="${chatId}"]`
    );
    if (!body || !btn) return;

    body.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");

    const chev = btn.querySelector(".js-chevron");
    if (chev) chev.classList.toggle("rotate-180", open);

    try {
      localStorage.setItem(storageKey(chatId, which), open ? "1" : "0");
    } catch {}
  }

  function restore(panelRoot) {
    const panel = panelRoot?.closest?.("[data-chat-panel]") || panelRoot;
    if (!panel) return;
    const chatId = panel.getAttribute("data-chat-panel");
    if (!chatId) return;

    ["note", "summary"].forEach((which) => {
      let open = false; // default CLOSED
      try {
        open = localStorage.getItem(storageKey(chatId, which)) === "1";
      } catch {}
      setOpen(chatId, which, open, panel);
    });
  }

  function formatNowStamp() {
    // lightweight “just now” UX; you can replace with locale string if preferred
    return "Updated: just now";
  }

  // Delegated click handling for toggles + actions
document.addEventListener("click", async (e) => {
  // toggle open/close
  const toggleBtn = e.target.closest(".js-toggle");
  if (toggleBtn) {
    e.preventDefault();
    const which = toggleBtn.getAttribute("data-toggle-target");
    const chatId = toggleBtn.getAttribute("data-chat-id");
    if (!which || !chatId) return;

    const isOpen = toggleBtn.getAttribute("aria-expanded") === "true";
    const panel = toggleBtn.closest("[data-chat-panel]") || document;
    setOpen(chatId, which, !isOpen, panel);
    return;
  }

  // action buttons inside chat detail
  const actionBtn = e.target.closest("[data-action]");
  if (!actionBtn) return;

  const action = actionBtn.getAttribute("data-action");
  const panel = actionBtn.closest("[data-chat-panel]");
  const chatId =
    panel?.getAttribute("data-session-id") ||
    panel?.getAttribute("data-chat-panel");

  if (!chatId) return;

  // Generate summary
  if (action === "summary") {
    e.preventDefault();
    actionBtn.disabled = true;

    try {
      await window.Chats.refreshSummary(chatId);
      setOpen(chatId, "summary", true, panel);
    } finally {
      actionBtn.disabled = false;
    }
    return;
  }

  // Delete chat
  if (action === "delete-chat") {
    e.preventDefault();

    const ok = window.confirm("Delete this chat?");
    if (!ok) return;

    actionBtn.disabled = true;

    try {
      const res = await fetch("/admin/chats/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ session_ids: [chatId] }),
      });

      const data = await safeJson(res);

      if (!res.ok) {
        window.alert(data.detail || "Failed to delete chat.");
        return;
      }

      window.location.href = "/admin/dashboard?view=chats";
    } catch (err) {
      console.error(err);
      window.alert("Something went wrong while deleting the chat.");
    } finally {
      actionBtn.disabled = false;
    }
    return;
  }
});

  // Keyboard shortcuts: N / S (toggle current open detail panel if present)
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(document.activeElement)) return;

    const k = (e.key || "").toLowerCase();
    if (k !== "n" && k !== "s") return;

    const detailPanelContainer = document.getElementById("chat-detail-panel");
    if (!detailPanelContainer) return;

    // Find the injected panel root
    const firstPanel = detailPanelContainer.querySelector("[data-chat-panel]");
    const chatId = firstPanel?.getAttribute("data-chat-panel");
    if (!chatId) return;

    e.preventDefault();
    const which = k === "n" ? "note" : "summary";

    const toggle = firstPanel.querySelector(
      `.js-toggle[data-toggle-target="${which}"][data-chat-id="${chatId}"]`
    );
    const isOpen = toggle?.getAttribute("aria-expanded") === "true";
    setOpen(chatId, which, !isOpen, firstPanel);
  });

  // expose restore hook so loadChatDetail can call it after injection
  window.restoreAdminChatPanelState = function (container) {
    const root = container || document;
    root.querySelectorAll?.("[data-chat-panel]")?.forEach((p) => restore(p));
  };
})();



    



   async function chatPostJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 || res.status === 403) return loginRedirect();

  const parsed = await safeReadJson(res);
  if (!parsed.ok) {
    console.error("chatPostJSON failed:", parsed.status, parsed.text.slice(0, 500));
    throw new Error(`HTTP ${parsed.status}`);
  }

  if (parsed.json?.ok === false) throw new Error(parsed.json.error || "Request failed");
  return parsed.json;
}

async function chatPostRoute(routeKey, params, body) {
  const url = apiRoute(routeKey, params);
  if (!url) throw new Error(`Missing API route: ${routeKey}`);
  return chatPostJSON(url, body);
}



  function initChatDetailHandlers(sessionId, panelEl) {
  const q = (sel) => panelEl.querySelector(sel);

  // ✅ Assignee dropdown -> hidden input sync (keeps existing assign flow)
  const assignedSelect = q('[data-role="assigned-select"]');
  const assignedInput  = q('[data-role="assigned-input"]');

  if (assignedSelect && assignedInput) {
    // Ensure hidden input matches initial dropdown value
    assignedInput.value = assignedSelect.value || "";

    assignedSelect.addEventListener("change", () => {
      assignedInput.value = assignedSelect.value || "";
    });
  }

  // Resolve
  q('[data-role="resolve-btn"]')?.addEventListener("click", async () => {
    await chatPostRoute("chat_resolve", { session_id: sessionId }, {});
    updateChatListRow(sessionId, { is_resolved: true });
    await loadChatDetail(sessionId);
  });

  // Unresolve
  q('[data-role="unresolve-btn"]')?.addEventListener("click", async () => {
    await chatPostRoute("chat_unresolve", { session_id: sessionId }, {});
    updateChatListRow(sessionId, { is_resolved: false });
    await loadChatDetail(sessionId);
  });

  // Escalation
  q('[data-role="escalation-select"]')?.addEventListener("change", async (e) => {
    const escalation_level = e.target.value || "";
    await chatPostRoute("chat_escalate", { session_id: sessionId }, { level: escalation_level });
    updateChatListRow(sessionId, { escalation_level: escalation_level || null });
    await loadChatDetail(sessionId);
  });

  // Assign
  q('[data-role="assign-btn"]')?.addEventListener("click", async () => {
    const assigned_to = (q('[data-role="assigned-input"]')?.value || "").trim();
    await chatPostRoute("chat_assign", { session_id: sessionId }, { assigned_to });
    updateChatListRow(sessionId, { assigned_to });
    await loadChatDetail(sessionId);
  });

  // Save note
  q('[data-role="save-note-btn"]')?.addEventListener("click", async () => {
    const note = q('[data-role="note-input"]')?.value || "";
    await chatPostRoute("chat_note", { session_id: sessionId }, { note });

    const s = q('[data-role="note-status"]');
    if (s) {
      s.textContent = "Saved ✅";
      setTimeout(() => (s.textContent = ""), 1200);
    }
  });

  // Summary is handled globally by window.Chats.refreshSummary click binding
}




let chatDetailAbort = null;
let chatDetailRequestToken = 0;

async function loadChatDetail(sessionId) {
  const panel = document.getElementById("chat-detail-panel");
  if (!panel) return;

  const token = ++chatDetailRequestToken;

  if (chatDetailAbort) chatDetailAbort.abort();
  chatDetailAbort = new AbortController();

  panel.classList.add("opacity-60", "pointer-events-none");

  try {
    const url =
      apiRoute("chat_detail_partial", { session_id: sessionId }) ||
      `/admin/chats/partial/detail?session_id=${encodeURIComponent(sessionId)}`;

    const res = await fetch(url, {
      credentials: "include",
      headers: { "X-Requested-With": "fetch" },
      signal: chatDetailAbort.signal,
    });

    if (res.status === 401 || res.status === 403) {
      loginRedirect();
      return;
    }

    if (!res.ok) {
      if (token !== chatDetailRequestToken) return;
      panel.innerHTML = `<div class="text-sm text-rose-700">Could not load chat.</div>`;
      return;
    }

    const html = await res.text();

    if (token !== chatDetailRequestToken) return;

    panel.innerHTML = html;
    panel.setAttribute("data-session-id", String(sessionId));

    const chatRoot =
      panel.querySelector(`[data-chat-panel="${sessionId}"]`) ||
      panel.querySelector("[data-chat-panel]");

    window.restoreAdminChatPanelState?.(panel);

    const listMoodEl = document.querySelector(
      `[data-session-row="${sessionId}"] [data-mood-badge]`
    );

    const listRawMood =
      (listMoodEl?.getAttribute("data-emotional-signals") || "").trim() ||
      JSON.stringify(
        [(listMoodEl?.getAttribute("data-guest-mood") || "").trim().toLowerCase()].filter(Boolean)
      );

    const detailMoodEl = panel.querySelector("[data-mood-badge]");

    const isEmptyAttr = (raw) => {
      const s = String(raw || "").trim();
      if (!s) return true;
      const lower = s.toLowerCase();
      if (lower === "null" || lower === "none" || lower === "undefined") return true;
      if (s === "[]") return true;
      return false;
    };

    if (detailMoodEl && listRawMood) {
      const current = detailMoodEl.getAttribute("data-emotional-signals");
      if (isEmptyAttr(current)) {
        detailMoodEl.setAttribute("data-emotional-signals", listRawMood);
      }
    }

    if (chatRoot && listRawMood) {
      const rootMood = chatRoot.getAttribute("data-emotional-signals");
      if (isEmptyAttr(rootMood)) {
        chatRoot.setAttribute("data-emotional-signals", listRawMood);
      }
    }

    window.rerenderAllMoodBadges?.(panel);
    window.applyMoodConfidenceHints?.(panel);
    initChatMessageTimes(panel);

    initChatDetailHandlers(sessionId, panel);
    await hydrateAssigneeDropdown(sessionId, panel);

    if (chatRoot && typeof updateChatListRow === "function") {
      const esc = (chatRoot.getAttribute("data-escalation-level") || "").trim();
      const assigned = (chatRoot.getAttribute("data-assigned-to") || "").trim();
      const isResolved = (chatRoot.getAttribute("data-is-resolved") || "0") === "1";

      updateChatListRow(sessionId, {
        escalation_level: esc || null,
        is_resolved: isResolved,
        assigned_to: assigned || null,
      });
    }
  } catch (err) {
    if (err?.name === "AbortError") return;
    console.error("loadChatDetail error:", err);
    if (token === chatDetailRequestToken) {
      panel.innerHTML = `<div class="text-sm text-rose-700">Could not load chat.</div>`;
    }
  } finally {
    if (token === chatDetailRequestToken) {
      panel.classList.remove("opacity-60", "pointer-events-none");
    }
  }
}



function setEscalationBadge(el, levelRaw) {
  if (!el) return;

  const level = String(levelRaw || "").toLowerCase().trim();

  // If el is a container (td/div), render a pill span inside it.
  // If el is already a span (the pill itself), update it directly.
  const isSpan = el.tagName && el.tagName.toLowerCase() === "span";
  const target = isSpan ? el : (el.querySelector("span") || null);

  const pillClass = "px-2 py-1 rounded-full font-semibold";

  const cfg =
    (level === "critical" || level === "high")
      ? { text: "🔴 High", cls: `${pillClass} bg-rose-100 text-rose-800` }
    : (level === "attention" || level === "medium")
      ? { text: "🟡 Medium", cls: `${pillClass} bg-amber-100 text-amber-800` }
    : (level === "low")
      ? { text: "🟢 Low", cls: `${pillClass} bg-blue-100 text-blue-800` }
    : { text: "—", cls: `${pillClass} text-slate-400` };

  if (target) {
    // Update existing span
    target.className = cfg.cls;
    target.textContent = cfg.text;
  } else {
    // Create span in container
    el.innerHTML = `<span class="${cfg.cls}">${cfg.text}</span>`;
  }

  el.setAttribute("data-escalation-level", level);
}


function setStatusBadge(container, isResolved) {
  if (!container) return;
  if (isResolved) {
    container.innerHTML = `<span class="px-2 py-1 rounded-full bg-slate-200 text-slate-700 font-semibold">✅ Closed</span>`;
  } else {
    container.innerHTML = `<span class="px-2 py-1 rounded-full bg-emerald-100 text-emerald-700 font-semibold">🟢 Open</span>`;
  }
}

function setAssignedBadge(container, assignedTo) {
  if (!container) return;
  const v = String(assignedTo || "").trim();
  if (!v) {
    container.innerHTML = `<span class="text-slate-400">—</span>`;
    return;
  }
  container.innerHTML = `<span class="inline-block px-2 py-1 rounded-xl bg-slate-100 text-slate-800 font-semibold">${escapeHtml(v)}</span>`;
}



    
/**
 * Update the row in the main chat list WITHOUT hard refresh.
 *
 * payload supports:
 *  - escalation_level: "low" | "medium" | "high" | "" | null
 *  - is_resolved: boolean
 *  - assigned_to: string | null
 */
function updateChatListRow(sessionId, payload = {}) {
  const row = document.querySelector(`[data-session-row="${sessionId}"]`);
  if (!row) return;

  if (Object.prototype.hasOwnProperty.call(payload, "escalation_level")) {
    const escEl = row.querySelector("[data-escalation-badge]");
    setEscalationBadge(escEl, payload.escalation_level);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "is_resolved")) {
    const stEl = row.querySelector("[data-status-badge]");
    setStatusBadge(stEl, payload.is_resolved);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "assigned_to")) {
    const asgEl = row.querySelector("[data-assigned-badge]");
    setAssignedBadge(asgEl, payload.assigned_to);
  }

  if (Object.prototype.hasOwnProperty.call(payload, "emotional_signals")) {
    const moodEl = row.querySelector("[data-mood-badge]");
    window.setMoodBadge?.(moodEl, payload.emotional_signals || []);
  }
}


function updateChatListEscalation(sessionId, level) {
  const row = document.querySelector(`[data-session-row="${sessionId}"]`);
  if (!row) return;
  setEscalationBadge(row.querySelector("[data-escalation-badge]"), level);
}



    
function updateRelativeTimes() {
  const now = new Date();
  document.querySelectorAll(".js-rel-time").forEach(el => {
    const ts = el.getAttribute("data-ts");
    const d = parseTimestamp(ts);
    if (!d) return;
    el.textContent = formatRelative(d, now);
  });
}


///----- END OF SECTION

if (!window.__MANUAL_EDITOR_STATUS_V2__) {
  window.__MANUAL_EDITOR_STATUS_V2__ = true;

  const AUTOSAVE_MS = 900; // tweak to taste (700–1200 feels good)

  function _manualWrapFrom(el) {
    return el?.closest?.("[data-manual-editor]") || null;
  }

  function _manualBoot(wrap) {
    const tag = wrap.querySelector("[data-manual-bootstrap]");
    try { return JSON.parse((tag?.textContent || "{}").trim()); }
    catch { return {}; }
  }

  function _manualSetStatus(wrap, state) {
    // state: loaded | dirty | saving | saved | error
    const dot = wrap.querySelector("[data-manual-dot]");
    const txt = wrap.querySelector("[data-manual-status-text]");
    if (!dot || !txt) return;

    dot.classList.remove("ok", "warn", "err");

    if (state === "loaded") {
      txt.textContent = "Loaded.";
      return;
    }
    if (state === "dirty") {
      txt.textContent = "Unsaved changes…";
      dot.classList.add("warn");
      return;
    }
    if (state === "saving") {
      txt.textContent = "Saving…";
      dot.classList.add("warn");
      return;
    }
    if (state === "saved") {
      txt.textContent = "Saved ✓";
      dot.classList.add("ok");
      return;
    }

    txt.textContent = "Save failed";
    dot.classList.add("err");
  }

  function _manualSetSaveEnabled(wrap, enabled) {
    const btn = wrap.querySelector("[data-save-manual]");
    if (btn) btn.disabled = !enabled;
  }

  function _manualEnsureInitial(wrap) {
    if (wrap.__initialText != null) return;
    const b = _manualBoot(wrap);
    wrap.__initialText = String(b.initial_content || "");
    _manualSetStatus(wrap, "loaded");
    _manualSetSaveEnabled(wrap, false);
  }

  async function _manualDoSave(wrap, { source = "auto" } = {}) {
    const btn = wrap.querySelector("[data-save-manual]");
    const ta = wrap.querySelector("[data-manual-textarea]");
    if (!ta) return;

    _manualEnsureInitial(wrap);

    const b = _manualBoot(wrap);
    const file_path = String(b.file_path || "").trim();
    if (!file_path) {
      _manualSetStatus(wrap, "error");
      alert("Save failed: missing file path");
      return;
    }

    const current = ta.value || "";
    const dirty = current !== (wrap.__initialText || "");
    if (!dirty) {
      _manualSetSaveEnabled(wrap, false);
      _manualSetStatus(wrap, "loaded");
      return;
    }

    // prevent double-save
    clearTimeout(wrap.__autosaveTimer);

    if (btn) btn.disabled = true;
    _manualSetStatus(wrap, "saving");

    try {
      const resp = await fetch("/admin/save-github-file", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ file_path, content: current }),
      });

      const text = await resp.text().catch(() => "");
      if (!resp.ok) throw new Error(text || `Save failed (${resp.status})`);

      // ✅ mark clean baseline so it stops being "dirty"
      wrap.__initialText = current;

      _manualSetSaveEnabled(wrap, false);
      _manualSetStatus(wrap, "saved");

      clearTimeout(wrap.__statusTimer);
      wrap.__statusTimer = setTimeout(() => _manualSetStatus(wrap, "loaded"), 1200);
    } catch (err) {
      console.error(err);
      _manualSetStatus(wrap, "error");
      alert("Save failed: " + (err.message || err));

      // If still dirty, re-enable save + restore dirty status
      const stillDirty = (ta.value || "") !== (wrap.__initialText || "");
      _manualSetSaveEnabled(wrap, stillDirty);
      if (stillDirty) _manualSetStatus(wrap, "dirty");
    }
  }

  function _manualScheduleAutosave(wrap) {
    clearTimeout(wrap.__autosaveTimer);
    wrap.__autosaveTimer = setTimeout(() => {
      // only autosave if still dirty
      const ta = wrap.querySelector("[data-manual-textarea]");
      if (!ta) return;
      const dirty = (ta.value || "") !== (wrap.__initialText || "");
      if (dirty) _manualDoSave(wrap, { source: "auto" });
    }, AUTOSAVE_MS);
  }

  // Dirty tracking + autosave
  document.addEventListener("input", (e) => {
    const ta = e.target.closest?.("[data-manual-textarea]");
    if (!ta) return;

    const wrap = _manualWrapFrom(ta);
    if (!wrap) return;

    _manualEnsureInitial(wrap);

    const dirty = (ta.value || "") !== (wrap.__initialText || "");
    _manualSetSaveEnabled(wrap, dirty);
    _manualSetStatus(wrap, dirty ? "dirty" : "loaded");

    if (dirty) _manualScheduleAutosave(wrap);
  }, true);

  // Manual save click
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest?.("[data-save-manual]");
    if (!btn) return;

    e.preventDefault();

    const wrap = _manualWrapFrom(btn);
    if (!wrap) return;

    clearTimeout(wrap.__autosaveTimer); // avoid double fire
    await _manualDoSave(wrap, { source: "manual" });
  }, true);
}





window.openInlineManual = async function (e, filePath) {
  e.preventDefault();

  const hostEl = document.getElementById("configInlineContainer");
  hostEl.dataset.filePath = filePath;

  const wrap = document.getElementById("configPanelWrap");
  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");

  const res = await fetch(`/admin/edit-config?file=${encodeURIComponent(filePath)}&embed=1`, {
    credentials: "include",
  });

  hostEl.innerHTML = res.ok
  ? await res.text()
  : `<div class="p-4 text-rose-700">Failed to load manual</div>`;

  //window.initManualEditor?.(hostEl); // ✅ ADD THIS

  wrap.classList.remove("hidden");
  grid?.classList.add("hidden");
  header?.classList.add("hidden");
};


window.closeInlineManual = function () {
  const wrap = document.getElementById("configPanelWrap");
  const host = document.getElementById("configInlineContainer");
  const grid = document.getElementById("propertiesGridWrap");
  const header = document.getElementById("propertiesHeaderCard");

  if (host) {
    host.innerHTML = "";
    delete host.dataset.filePath;
  }

  wrap?.classList.add("hidden");
  grid?.classList.remove("hidden");
  header?.classList.remove("hidden");
  header?.scrollIntoView({ behavior: "smooth", block: "start" });
};




// ----------------------------
// Analytics
// ----------------------------
window.chatAnalyticsChart = window.chatAnalyticsChart || null;
window.chatAnalyticsState = {
  mode: "chats",
  compare: true,
  hoveredIndex: null,
  selectedIndex: null,
  payload: null,
};

function fmtPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n * 10) / 10}%`;
}

function fmtInt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString();
}

function getAnalyticsFilters() {
  return {
    days: Number(document.getElementById("analyticsRange")?.value || 30),
    propertyId: document.getElementById("analyticsPropertyFilter")?.value || "",
    pmcId: document.getElementById("analyticsPmcFilter")?.value || "",
  };
}

function buildAnalyticsQS({ days, propertyId, pmcId }) {
  const qs = new URLSearchParams();
  qs.set("days", String(days));
  if (propertyId) qs.set("property_id", String(propertyId));
  if (pmcId) qs.set("pmc_id", String(pmcId));
  return qs.toString();
}

function getEventMeta(eventName) {
  const key = String(eventName || "").toLowerCase();
  if (key === "peak") return { icon: "🔥", label: "Peak day" };
  if (key === "friction") return { icon: "⚠️", label: "Issue spike" };
  if (key === "convert") return { icon: "📈", label: "High conversion" };
  if (key === "inquiry") return { icon: "💬", label: "Inquiry surge" };
  if (key === "quiet") return { icon: "🌙", label: "Quiet day" };
  return { icon: "•", label: "Stable" };
}

function currentAnalyticsMode() {
  return window.chatAnalyticsState?.mode || "chats";
}

function currentCompareMode() {
  return !!window.chatAnalyticsState?.compare;
}

function analyticsModeValue(day, mode) {
  if (!day) return 0;
  if (mode === "conversion") return Number(day.conversion || 0);
  if (mode === "lost") return Number(day.lost_opportunity || 0);
  return Number(day.chats || 0);
}

function setAnalyticsKpi(name, value) {
  const el = document.querySelector(`[data-kpi="${name}"]`);
  if (!el) return;
  el.textContent = value;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value == null || value === "" ? "—" : String(value);
}

function setWidth(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  const n = Math.max(0, Math.min(100, Number(pct || 0)));
  el.style.width = `${n}%`;
}

function renderAnalyticsSummary(summary) {
  setAnalyticsKpi("sessions_total", fmtInt(summary.sessions_total));
  setAnalyticsKpi("response_rate", fmtPct(summary.response_rate));
  setAnalyticsKpi("followup_clicks", fmtInt(summary.followup_clicks));
  setAnalyticsKpi("chat_errors", fmtInt(summary.chat_errors));
}

function renderAnalyticsLifecycle(lifecycle) {
  const total = Number(lifecycle.total || 0) || 1;

  const inquiry = Number(lifecycle.inquiry || 0);
  const upcoming = Number(lifecycle.upcoming || 0);
  const current = Number(lifecycle.current || 0);
  const post = Number(lifecycle.checked_out || 0);

  setText("analytics-stage-inquiry", `${inquiry}%`);
  setText("analytics-stage-upcoming", `${upcoming}%`);
  setText("analytics-stage-current", `${current}%`);
  setText("analytics-stage-post", `${post}%`);

  setWidth("analytics-stage-inquiry-bar", inquiry);
  setWidth("analytics-stage-upcoming-bar", upcoming);
  setWidth("analytics-stage-current-bar", current);
  setWidth("analytics-stage-post-bar", post);
}

function renderAnalyticsPeak(hours) {
  setText("analytics-peak-window", hours?.peak_window || "—");

  const barsWrap = document.getElementById("analytics-hour-bars");
  const detailWrap = document.getElementById("analytics-hourly-bars");

  const items = Array.isArray(hours?.items) ? hours.items : [];
  const max = Math.max(...items.map((x) => Number(x.value || 0)), 1);

  if (barsWrap) {
    barsWrap.innerHTML = items.map((item) => {
      const h = Math.max(10, Math.round((Number(item.value || 0) / max) * 96));
      return `
        <div class="flex flex-col items-center gap-2">
          <div class="flex h-[100px] items-end">
            <div class="w-8 rounded-t-2xl bg-indigo-500/90" style="height:${h}px"></div>
          </div>
          <div class="text-xs font-medium text-slate-500">${escapeHtml(item.label || "—")}</div>
        </div>
      `;
    }).join("");
  }

  if (detailWrap) {
    detailWrap.innerHTML = items.map((item) => {
      const pct = Math.round((Number(item.value || 0) / max) * 100);
      return `
        <div class="grid grid-cols-[36px_1fr_42px] items-center gap-3 text-xs text-slate-500">
          <div>${escapeHtml(item.label || "—")}</div>
          <div class="h-2.5 rounded-full bg-white">
            <div class="h-2.5 rounded-full bg-indigo-500" style="width:${pct}%"></div>
          </div>
          <div class="text-right">${fmtInt(item.value || 0)}</div>
        </div>
      `;
    }).join("");
  }
}

function renderAnalyticsEmotions(emotions, spike) {
  const wrap = document.getElementById("analytics-emotion-bars");
  if (wrap) {
    const items = Array.isArray(emotions?.items) ? emotions.items : [];

    if (!items.length) {
      wrap.innerHTML = `<div class="text-sm text-slate-500">No emotion data yet.</div>`;
    } else {
      const toneClass = (tone) => {
        if (tone === "emerald") return "bg-emerald-500";
        if (tone === "blue") return "bg-blue-500";
        if (tone === "indigo") return "bg-indigo-500";
        if (tone === "amber") return "bg-amber-500";
        if (tone === "orange") return "bg-orange-500";
        if (tone === "rose") return "bg-rose-500";
        return "bg-slate-400";
      };

      wrap.innerHTML = items.map((item) => `
        <div>
          <div class="mb-2 flex items-center justify-between text-sm">
            <span class="font-semibold text-slate-800">${escapeHtml(item.label || "Unknown")}</span>
            <span class="text-slate-500">${fmtInt(item.value || 0)}%</span>
          </div>
          <div class="h-3 rounded-full bg-slate-100">
            <div class="h-3 rounded-full ${toneClass(item.tone)}" style="width:${Math.max(0, Math.min(100, Number(item.value || 0)))}%"></div>
          </div>
        </div>
      `).join("");
    }
  }

  setText(
    "analytics-emotion-spike-title",
    spike?.title || "No major emotional spike detected"
  );
  setText(
    "analytics-emotion-spike-body",
    spike?.body || "Current conversations are relatively balanced."
  );
}

function renderAnalyticsTopProperties(rows) {
  const tbody = document.getElementById("analyticsTopPropsBody");
  if (!tbody) return;

  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    tbody.innerHTML = `<tr><td class="py-4 text-slate-500" colspan="6">No data yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map((r) => `
    <tr class="border-t border-slate-100">
      <td class="py-3 pr-4 font-medium text-slate-900">${escapeHtml(r.property_name || "Unknown")}</td>
      <td class="py-3 pr-4 text-slate-600">${fmtInt(r.sessions || 0)}</td>
      <td class="py-3 pr-4 text-slate-600">${fmtInt(r.messages || 0)}</td>
      <td class="py-3 pr-4 text-slate-600">${fmtPct(r.followup_conversion_rate || 0)}</td>
      <td class="py-3 pr-4 text-slate-600">${fmtInt(r.chat_errors || 0)}</td>
      <td class="py-3 pr-4 text-slate-600">${fmtInt(r.escalations || 0)}</td>
    </tr>
  `).join("");
}

function renderAnalyticsSummaryCards(days, selectedIndex) {
  const selected = days[selectedIndex] || days[days.length - 1];
  if (!selected) return;

  const peak = [...days].sort((a, b) => Number(b.chats || 0) - Number(a.chats || 0))[0];
  const quiet = [...days].sort((a, b) => Number(a.chats || 0) - Number(b.chats || 0))[0];
  const avg = days.length
    ? Math.round(days.reduce((sum, d) => sum + Number(d.chats || 0), 0) / days.length)
    : 0;

  setText("analytics-summary-peak-day", peak?.label || "—");
  setText("analytics-summary-peak-meta", peak ? `${fmtInt(peak.chats)} chats` : "—");

  setText("analytics-summary-quiet-day", quiet?.label || "—");
  setText("analytics-summary-quiet-meta", quiet ? `${fmtInt(quiet.chats)} chats` : "—");

  setText("analytics-summary-average", fmtInt(avg));

  setText("analytics-summary-selected", selected?.label || "—");
  setText(
    "analytics-summary-selected-meta",
    selected ? `${selected.delta >= 0 ? "+" : ""}${selected.delta || 0}% delta` : "—"
  );
}

function positionAnalyticsHoverLine(chart, index) {
  const line = document.getElementById("analyticsHoverLine");
  if (!line || !chart || index == null) return;

  const xScale = chart.scales?.x;
  if (!xScale) return;

  const x = xScale.getPixelForValue(index);
  line.classList.remove("hidden");
  line.style.left = `${x}px`;
}

function hideAnalyticsHover() {
  const card = document.getElementById("analyticsChartHoverCard");
  const line = document.getElementById("analyticsHoverLine");
  if (card) card.classList.add("hidden");
  if (line) line.classList.add("hidden");
}

function showAnalyticsHover(day, chart, index) {
  const card = document.getElementById("analyticsChartHoverCard");
  if (!card || !day || !chart) return;

  const meta = getEventMeta(day.event);
  const dateEl = card.querySelector("[data-hover-date]");
  const chatsEl = card.querySelector("[data-hover-chats]");
  const convEl = card.querySelector("[data-hover-conversion]");
  const lostEl = card.querySelector("[data-hover-lost]");
  const signalEl = card.querySelector("[data-hover-signal]");

  if (dateEl) dateEl.textContent = `${day.day || "—"} · ${day.label || "—"}`;
  if (chatsEl) chatsEl.textContent = fmtInt(day.chats);
  if (convEl) convEl.textContent = fmtPct(day.conversion);
  if (lostEl) lostEl.textContent = fmtInt(day.lost_opportunity);
  if (signalEl) signalEl.textContent = meta.label;

  card.classList.remove("hidden");

  const xScale = chart.scales?.x;
  const chartArea = chart.chartArea;
  if (!xScale || !chartArea) {
    positionAnalyticsHoverLine(chart, index);
    return;
  }

  const x = xScale.getPixelForValue(index);

  const cardWidth = card.offsetWidth || 220;
  const cardHeight = card.offsetHeight || 120;
  const padding = 12;

  let left = x + 16;
  let top = chartArea.top + 12;

  if (left + cardWidth > chartArea.right - padding) {
    left = x - cardWidth - 16;
  }

  if (left < chartArea.left + padding) {
    left = chartArea.left + padding;
  }

  if (top + cardHeight > chartArea.bottom - padding) {
    top = chartArea.bottom - cardHeight - padding;
  }

  card.style.left = `${left}px`;
  card.style.top = `${top}px`;

  positionAnalyticsHoverLine(chart, index);
}
function renderChatAnalyticsChart(payload) {
  const canvas = document.getElementById("chatAnalyticsChart");
  if (!canvas || !window.Chart) return;

  window.analyticsPayload = payload;

  const days = Array.isArray(payload?.days) ? payload.days : [];
  const labels = days.map((d) => d.day || d.label || "—");
  const mode = currentAnalyticsMode();
  const compare = currentCompareMode();

  const values = days.map((d) => analyticsModeValue(d, mode));
  const previousValues = days.map((d) => analyticsModeValue(d?.previous || {}, mode));
  const trendValues = values.slice();

  const chartLabel =
    mode === "conversion"
      ? "Conversion"
      : mode === "lost"
      ? "Lost opportunity"
      : "Chats";

  const currentBarColor =
    mode === "conversion"
      ? "rgba(16,185,129,0.85)"
      : mode === "lost"
      ? "rgba(244,63,94,0.85)"
      : "rgba(79,70,229,0.88)";

  const currentHoverColor =
    mode === "conversion"
      ? "rgba(16,185,129,1)"
      : mode === "lost"
      ? "rgba(244,63,94,1)"
      : "rgba(79,70,229,1)";

  const datasets = [
    {
      type: "bar",
      label: chartLabel,
      data: values,
      backgroundColor: currentBarColor,
      hoverBackgroundColor: currentHoverColor,
      borderRadius: 14,
      borderSkipped: false,
      order: 2,
      categoryPercentage: 0.72,
      barPercentage: 0.9,
    },
    {
      type: "bar",
      label: "Prior period",
      data: compare ? previousValues : previousValues.map(() => null),
      backgroundColor: "rgba(148,163,184,0.25)",
      borderColor: "rgba(148,163,184,0.55)",
      borderWidth: 1,
      borderRadius: 14,
      borderSkipped: false,
      order: 1,
      categoryPercentage: 0.72,
      barPercentage: 0.9,
    },
    {
      type: "line",
      label: "Trend",
      data: trendValues,
      borderColor: "rgba(15,23,42,0.18)",
      pointRadius: 0,
      pointHoverRadius: 0,
      tension: 0.35,
      borderWidth: 1.5,
      order: 0,
      yAxisID: "y",
    },
  ];

  if (window.chatAnalyticsChart) {
    try {
      window.chatAnalyticsChart.destroy();
    } catch (_) {}
    window.chatAnalyticsChart = null;
  }

  const hoverPlugin = {
    id: "analyticsHoverPlugin",
    afterEvent(chart, args) {
      const event = args.event;
      if (!event) return;

      if (event.type === "mouseout") {
        hideAnalyticsHover();
        return;
      }

      const points = chart.getElementsAtEventForMode(
        event,
        "index",
        { intersect: false },
        false
      );

      if (!points.length) {
        hideAnalyticsHover();
        return;
      }

      const idx = points[0].index;
      const meta = chart.getDatasetMeta(points[0].datasetIndex);
      const element = meta?.data?.[idx];

      if (!element || !days[idx]) {
        hideAnalyticsHover();
        return;
      }

      window.chatAnalyticsState.hoveredIndex = idx;
      showAnalyticsHover(days[idx], chart, idx);
    },
  };

  window.chatAnalyticsChart = new Chart(canvas.getContext("2d"), {
    data: {
      labels,
      datasets,
    },
    plugins: [hoverPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 400,
        easing: "easeOutCubic",
      },
      interaction: {
        mode: "index",
        intersect: false,
      },
      onClick(_, elements) {
        if (!elements?.length) return;
        const idx = elements[0].index;
        window.chatAnalyticsState.selectedIndex = idx;
        renderAnalyticsSummaryCards(days, idx);
      },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: {
          grid: {
            display: false,
            drawBorder: false,
          },
          ticks: {
            color: "#64748b",
          },
          border: {
            display: false,
          },
        },
        y: {
          beginAtZero: true,
          grid: {
            color: "rgba(148,163,184,0.18)",
            drawBorder: false,
          },
          ticks: {
            color: "#94a3b8",
            precision: 0,
          },
          border: {
            display: false,
          },
        },
      },
    },
  });

  const selectedIndex =
    Number.isInteger(window.chatAnalyticsState.selectedIndex) &&
    window.chatAnalyticsState.selectedIndex >= 0 &&
    window.chatAnalyticsState.selectedIndex < days.length
      ? window.chatAnalyticsState.selectedIndex
      : Math.max(days.length - 1, 0);

  window.chatAnalyticsState.selectedIndex = selectedIndex;

  if (days.length) {
    renderAnalyticsSummaryCards(days, selectedIndex);
  } else {
    hideAnalyticsHover();
  }
}

function wireAnalyticsModeControls() {
  const modeButtons = Array.from(document.querySelectorAll(".analytics-mode-btn"));
  const compareBtn = document.getElementById("analyticsCompareToggle");

  function paintModeButtons() {
    modeButtons.forEach((btn) => {
      const isActive =
        (btn.getAttribute("data-chart-mode") || "chats") === window.chatAnalyticsState.mode;

      btn.classList.toggle("is-active", isActive);
      btn.classList.toggle("bg-slate-900", isActive);
      btn.classList.toggle("text-white", isActive);
      btn.classList.toggle("text-slate-500", !isActive);
      btn.classList.toggle("hover:bg-slate-100", !isActive);
    });
  }

  function paintCompareButton() {
    if (!compareBtn) return;

    const isActive = !!window.chatAnalyticsState.compare;
    compareBtn.classList.toggle("bg-slate-900", isActive);
    compareBtn.classList.toggle("text-white", isActive);
    compareBtn.classList.toggle("border-slate-900", isActive);

    compareBtn.classList.toggle("bg-white", !isActive);
    compareBtn.classList.toggle("text-slate-600", !isActive);
    compareBtn.classList.toggle("border-slate-200", !isActive);
  }

  modeButtons.forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";

    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-chart-mode") || "chats";
      if (window.chatAnalyticsState.mode === mode) return;

      window.chatAnalyticsState.mode = mode;
      paintModeButtons();

      if (window.analyticsPayload) {
        renderChatAnalyticsChart(window.analyticsPayload);
      } else if (window.chatAnalyticsState.payload) {
        renderChatAnalyticsChart(window.chatAnalyticsState.payload);
      }
    });
  });

  if (compareBtn && compareBtn.dataset.wired !== "1") {
    compareBtn.dataset.wired = "1";

    compareBtn.addEventListener("click", () => {
      window.chatAnalyticsState.compare = !window.chatAnalyticsState.compare;
      paintCompareButton();

      if (window.analyticsPayload) {
        renderChatAnalyticsChart(window.analyticsPayload);
      } else if (window.chatAnalyticsState.payload) {
        renderChatAnalyticsChart(window.chatAnalyticsState.payload);
      }
    });
  }

  paintModeButtons();
  paintCompareButton();
}

async function loadAnalyticsInsights(days, propertyId, pmcId) {
  const qs = new URLSearchParams();
  if (propertyId) qs.set("property_id", propertyId);
  if (pmcId) qs.set("pmc_id", pmcId);

  const res = await fetch(`/analytics/ai-insights?${qs.toString()}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (res.status === 401 || res.status === 403) return loginRedirect();

  const parsed = await safeReadJson(res);
  if (!parsed.ok) return;

  const raw = parsed.json || {};

  renderAnalyticsInsights({
    top_issue: raw.top_issue ? raw.top_issue.replaceAll("_", " ") : null,
    top_issue_detail: raw.top_issue_count ? `${fmtInt(raw.top_issue_count)} conversations point to this issue.` : null,
    high_risk: raw.high_risk ? raw.high_risk.replaceAll("_", " ") : null,
    high_risk_detail: raw.high_risk_count ? `${fmtInt(raw.high_risk_count)} high-risk conversations in this slice.` : null,
    automation: raw.automation ? raw.automation.replaceAll("_", " ") : null,
    automation_detail: raw.automation_count ? `${fmtInt(raw.automation_count)} low-severity conversations could likely be automated.` : null,
    needs_human: `${fmtInt(raw.needs_human || 0)} needs human`,
    needs_human_detail: raw.needs_human_pct != null ? `${fmtPct(raw.needs_human_pct)} of sessions needed a human.` : null,
  });
}

async function loadTopProperties(days, propertyId, pmcId) {
  const qs = new URLSearchParams();
  qs.set("days", String(days));
  qs.set("limit", "10");
  if (propertyId) qs.set("property_id", propertyId);
  if (pmcId) qs.set("pmc_id", pmcId);

  const res = await fetch(`/admin/analytics/chat/top-properties?${qs.toString()}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (res.status === 401 || res.status === 403) return loginRedirect();

  const parsed = await safeReadJson(res);
  if (!parsed.ok) {
    console.error("top properties failed", parsed.status, parsed.text);
    renderAnalyticsTopProperties([]);
    return;
  }

  renderAnalyticsTopProperties(parsed.json?.items || []);
}

async function loadChatAnalytics() {
  try {
    const { days, propertyId, pmcId } = getAnalyticsFilters();
    const qs = buildAnalyticsQS({ days, propertyId, pmcId });

    const summaryRes = await fetch(`/admin/analytics/chat/summary?${qs}`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (summaryRes.status === 401 || summaryRes.status === 403) return loginRedirect();
    const summaryParsed = await safeReadJson(summaryRes);
    if (!summaryParsed.ok) throw new Error("Summary failed");
    renderAnalyticsSummary(summaryParsed.json || {});

    const tsRes = await fetch(`/admin/analytics/chat/timeseries?${qs}`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (tsRes.status === 401 || tsRes.status === 403) return loginRedirect();
    const tsParsed = await safeReadJson(tsRes);
    if (!tsParsed.ok) throw new Error("Timeseries failed");

    const payload = tsParsed.json || {};
    window.chatAnalyticsState.payload = payload;

    renderChatAnalyticsChart(payload);
    renderAnalyticsLifecycle(payload.lifecycle || {});
    renderAnalyticsPeak(payload.hours || {});
    renderAnalyticsEmotions(payload.emotions || {}, payload.emotion_spike || {});

    await Promise.all([
      loadTopProperties(days, propertyId, pmcId),
      loadAnalyticsInsights(days, propertyId, pmcId),
    ]);
  } catch (err) {
    console.error("loadChatAnalytics failed:", err);
    toast("Analytics failed to load.");
  }
}

let analyticsDebounce = null;

function isAnalyticsVisible() {
  const el = document.getElementById("view-analytics");
  return !!el && !el.classList.contains("hidden");
}

document.addEventListener("change", (e) => {
  const t = e.target;
  if (!(t instanceof HTMLElement)) return;

  if (!["analyticsRange", "analyticsPropertyFilter", "analyticsPmcFilter"].includes(t.id)) return;
  if (!isAnalyticsVisible()) return;

  clearTimeout(analyticsDebounce);
  analyticsDebounce = setTimeout(() => {
    window.chatAnalyticsState.selectedIndex = null;
    loadChatAnalytics();
  }, 120);
});

function initAnalyticsSection() {
  if (!document.getElementById("view-analytics")) return;
  wireAnalyticsModeControls();
  if (isAnalyticsVisible()) {
    loadChatAnalytics();
  }
}




// ----------------------------
// END OF Analytics
// ----------------------------

// Prevent row click from firing when interacting with controls
document.addEventListener("click", (e) => {
  const row = e.target.closest("[data-session-row]");
  if (!row) return;

  // If the click originated from an interactive element, do nothing.
  if (e.target.closest("a, button, input, textarea, select, label")) return;

  const sid = row.getAttribute("data-session-row");
  if (!sid) return;

  // Avoid double-open if you still have inline onclick on the <tr>
  if (row.__opening) return;
  row.__opening = true;

  Promise.resolve(openChatDetail(sid)).finally(() => {
    row.__opening = false;
  });
});


  // ----------------------------
  // Small UI helpers
  // ----------------------------
  function toast(message) {
    const el = document.createElement("div");
    el.className =
      "fixed bottom-5 right-5 z-[9999] bg-slate-900 text-white text-sm px-4 py-3 rounded-xl shadow-lg";
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2400);
  }

  function loginRedirect() {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search + window.location.hash
    );
    window.location.href = `/auth/login/google?next=${next}`;
  }

async function safeReadJson(res) {
  const status = res?.status;

  try {
    const text = await res.text();

    if (!text) return { ok: true, json: null, text: "", status };

    try {
      return { ok: true, json: JSON.parse(text), text, status };
    } catch {
      return { ok: false, json: null, text, status, error: "Response was not valid JSON" };
    }
  } catch (e) {
    return { ok: false, json: null, text: "", status, error: e?.message || String(e) };
  }
}


// static/admin_dashboard.js

async function fetchTeamUsersForChat(sessionId) {
  const res = await fetch(`/admin/api/team?session_id=${encodeURIComponent(sessionId)}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 401 || res.status === 403) return loginRedirect();
  if (!res.ok) throw new Error(`Failed to load team users (${res.status})`);
  return res.json();
}

function buildAssigneeLabel(u) {
  const name = (u.full_name || "").trim();
  const email = (u.email || "").trim();
  if (name && email) return `${name} — ${email}`;
  return email || name || "Unknown";
}

async function hydrateAssigneeDropdown(sessionId, panelEl) {
  const input = panelEl.querySelector('[data-role="assigned-input"]');
  const assignBtn = panelEl.querySelector('[data-role="assign-btn"]');
  if (!input || !assignBtn) return;

  // Avoid duplicating if user reopens the same chat
  if (panelEl.querySelector('[data-role="assigned-select"]')) return;

  // Create select
  const sel = document.createElement("select");
  sel.setAttribute("data-role", "assigned-select");
  sel.className =
    input.className ||
    "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm";

  // First option: unassigned
  sel.appendChild(new Option("Unassigned", ""));

  let data;
  try {
    data = await fetchTeamUsersForChat(sessionId);
  } catch (e) {
    console.error("Could not load team users:", e);
    return; // Keep fallback input
  }

  const items = Array.isArray(data?.items) ? data.items : [];
  for (const u of items) {
    const email = (u.email || "").trim();
    if (!email) continue;
    sel.appendChild(new Option(buildAssigneeLabel(u), email));
  }

  // Sync current value
  const current = (input.value || "").trim();
  sel.value = current;

  // Keep input as the actual submitted value (so existing assign click logic works)
  input.style.display = "none";
  input.insertAdjacentElement("beforebegin", sel);

  sel.addEventListener("change", () => {
    input.value = sel.value || "";
    assignBtn.click();
  });
}



  async function apiJson(url, opts = {}) {
  const res = await fetch(url, {
    credentials: "include",
    headers: { Accept: "application/json", ...(opts.headers || {}) },
    ...opts,
  });

  if (res.status === 401 || res.status === 403) {
    toast("Session expired. Please sign in again.");
    loginRedirect();
    throw new Error("auth");
  }

  const text = await res.text().catch(() => "");
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

  return { res, data };
}

  async function postJson(url) {
    return fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  }


// ----------------------------
// Messages (Admin notifications)
// ----------------------------
window.Messages = {
  loaded: false,
  state: {
    limit: 20,
    offset: 0,
    status: "all", // all | unread
    type: "all",   // all | upgrade_purchase | upgrade_request
    q: "",
    selectedId: null,
    total: null, // optional if backend returns it
  },

  // --- helpers ---
  _el(id) { return document.getElementById(id); },

  _routeList() {
    // supports query placeholders in bootstrap:
    // "/admin/messages?status={status}&type={type}&q={q}&limit={limit}&offset={offset}"
    const url = apiRoute("messages_list", {
      status: this.state.status,
      type: this.state.type,
      q: this.state.q,
      limit: this.state.limit,
      offset: this.state.offset,
    });

    // fallback if bootstrap missing
    if (url) return url;

    const qs = new URLSearchParams({
      status: this.state.status,
      type: this.state.type,
      q: this.state.q,
      limit: String(this.state.limit),
      offset: String(this.state.offset),
    });
    return `/admin/messages?${qs.toString()}`;
  },

  _routeUnreadCount() {
    return apiRoute("messages_unread_count") || "/admin/messages/unread-count";
  },

  _routeMarkRead(id) {
    return apiRoute("messages_mark_read", { message_id: id }) || `/admin/messages/${encodeURIComponent(id)}/read`;
  },

  _routeMarkAllRead() {
    return apiRoute("messages_mark_all_read") || "/admin/messages/mark-all-read";
  },

  _escape(s) {
    return String(s ?? "").replace(/[&<>"']/g, (m) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[m]));
  },

  _fmtTime(ts) {
    // reuse your relative time style if provided
    // Accept both ISO and "YYYY-MM-DD HH:MM:SS"
    if (!ts) return "—";
    const normalized = String(ts).includes("T") ? String(ts) : String(ts).replace(" ", "T");
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return String(ts);
    // show short absolute; relative updater is for .js-rel-time elements elsewhere
    return d.toLocaleString();
  },

  _badgeKind(kind) {
    const k = String(kind || "").toLowerCase();
    if (k === "upgrade_purchase") return "Upgrade purchase";
    if (k === "upgrade_request") return "Upgrade request";
    return k ? k : "Message";
  },

  // --- unread badge in sidebar ---
  async refreshUnreadBadge() {
    const badge = this._el("messages-unread-badge");
    if (!badge) return;

    try {
      const { res, data } = await apiJson(this._routeUnreadCount());
      if (!res.ok) return;

      const n = Number(data?.unread_count ?? data?.count ?? 0) || 0;
      badge.textContent = String(n);
      badge.classList.toggle("hidden", n <= 0);
    } catch (e) {
      // silent; badge is non-critical
      console.warn("messages unread badge failed:", e);
    }
  },

  // --- list rendering ---
  _renderList(rows) {
    const listEl = this._el("messages-list");
    if (!listEl) return;

    if (!rows || !rows.length) {
      listEl.innerHTML = `<div class="p-4 text-sm text-slate-500">No messages.</div>`;
      return;
    }

    const selectedId = this.state.selectedId;

    listEl.innerHTML = rows.map((m) => {
      const id = m.id ?? m.message_id ?? "";
      const unread = !!(m.is_unread ?? m.unread ?? (m.read_at == null && m.is_read === false));
      const subject = this._escape(m.subject || m.title || this._badgeKind(m.kind || m.type));
      const preview = this._escape(m.preview || m.body_preview || m.body || "").slice(0, 140);
      const when = this._escape(m.created_at || m.ts || m.timestamp || "");
      const kind = this._escape(this._badgeKind(m.kind || m.type));

      const active = String(id) === String(selectedId);

      return `
        <button type="button"
          class="w-full text-left p-4 border-t border-slate-200 hover:bg-slate-50 ${active ? "bg-slate-50" : ""}"
          data-message-open="${this._escape(id)}">
          <div class="flex items-center gap-2">
            <div class="text-sm font-semibold text-slate-900 flex-1">
              ${unread ? `<span class="inline-block w-2 h-2 rounded-full bg-rose-500 mr-2 align-middle"></span>` : ""}
              ${subject}
            </div>
            <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
              ${kind}
            </span>
          </div>
          <div class="mt-1 text-sm text-slate-500">${preview || "—"}</div>
          <div class="mt-2 text-xs text-slate-400">${when}</div>
        </button>
      `;
    }).join("");
  },

  _renderDetail(m) {
    const detailEl = this._el("messages-detail");
    if (!detailEl) return;

    if (!m) {
      detailEl.innerHTML = `<div class="text-sm text-slate-500">Select a message on the left.</div>`;
      return;
    }

    const id = m.id ?? m.message_id ?? "";
    const unread = !!(m.is_unread ?? m.unread ?? (m.read_at == null && m.is_read === false));
    const subject = this._escape(m.subject || m.title || this._badgeKind(m.kind || m.type));
    const kind = this._escape(this._badgeKind(m.kind || m.type));
    const created = this._escape(this._fmtTime(m.created_at || m.ts || m.timestamp));
    const body = this._escape(m.body || m.content || m.message || "");

    detailEl.innerHTML = `
      <div class="flex items-start justify-between gap-3">
        <div>
          <div class="text-sm font-semibold text-slate-900">${subject}</div>
          <div class="mt-1 text-xs text-slate-500">${created}</div>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">${kind}</span>
          ${unread ? `
            <button type="button" class="h-9 px-3 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-xs font-semibold"
              data-message-mark-read="${this._escape(id)}">
              Mark read
            </button>` : `
            <span class="text-xs text-slate-400">Read</span>
          `}
        </div>
      </div>

      <div class="mt-4 whitespace-pre-wrap text-sm text-slate-700 bg-white border border-slate-200 rounded-xl p-4">
        ${body || "—"}
      </div>
    `;
  },

  _updatePagingLabel(countOnPage) {
    const label = this._el("messages-paging-label");
    if (!label) return;

    const { limit, offset } = this.state;
    if (!countOnPage) {
      label.textContent = "—";
      return;
    }
    const start = offset + 1;
    const end = offset + countOnPage;
    label.textContent = `${start}–${end}`;
  },

  // --- fetch list ---
  async refreshList({ keepSelection = true } = {}) {
    const listEl = this._el("messages-list");
    if (listEl) listEl.innerHTML = `<div class="p-4 text-sm text-slate-500">Loading…</div>`;

    try {
      const { res, data } = await apiJson(this._routeList());
      if (!res.ok) {
        if (listEl) listEl.innerHTML = `<div class="p-4 text-sm text-rose-700">Failed to load messages.</div>`;
        return;
      }

      const rows = data?.rows || data?.messages || [];
      this.state.total = data?.total ?? data?.count ?? null;

      this._renderList(rows);
      this._updatePagingLabel(rows.length);

      // auto-select first (or keep current)
      if (!keepSelection || !this.state.selectedId) {
        const first = rows[0];
        const firstId = first?.id ?? first?.message_id ?? null;
        if (firstId != null) {
          this.state.selectedId = firstId;
          this._renderDetail(first);
          // mark read on open
          await this._maybeMarkRead(first);
        } else {
          this._renderDetail(null);
        }
      } else {
        // if selection exists, re-render detail from the newly loaded rows if found
        const found = rows.find(r => String(r.id ?? r.message_id) === String(this.state.selectedId));
        if (found) this._renderDetail(found);
      }

      await this.refreshUnreadBadge();
    } catch (e) {
      console.error("Messages.refreshList failed:", e);
      if (listEl) listEl.innerHTML = `<div class="p-4 text-sm text-rose-700">Network error loading messages.</div>`;
    }
  },

  async _maybeMarkRead(m) {
    const id = m?.id ?? m?.message_id;
    if (!id) return;

    const unread = !!(m.is_unread ?? m.unread ?? (m.read_at == null && m.is_read === false));
    if (!unread) return;

    try {
      const { res, data } = await apiJson(this._routeMarkRead(id), { method: "POST" });
      if (!res.ok || data?.ok === false) return;
      // optimistic: refresh list to remove unread dot + update badge
      await this.refreshList({ keepSelection: true });
    } catch (e) {
      console.warn("mark read failed:", e);
    }
  },

  // --- init / wire ---
  initOnce() {
    if (this.loaded) return;
    this.loaded = true;

    const filterEl = this._el("messages-filter");
    const searchEl = this._el("messages-search");
    const refreshBtn = this._el("messages-refresh");
    const markAllBtn = this._el("messages-mark-all-read");
    const prevBtn = this._el("messages-prev");
    const nextBtn = this._el("messages-next");

    // Filter dropdown (maps to status/type)
    filterEl?.addEventListener("change", () => {
      const v = String(filterEl.value || "all");
      this.state.offset = 0;

      if (v === "unread") {
        this.state.status = "unread";
        this.state.type = "all";
      } else if (v === "upgrade_purchase" || v === "upgrade_request") {
        this.state.status = "all";
        this.state.type = v;
      } else {
        this.state.status = "all";
        this.state.type = "all";
      }

      this.refreshList({ keepSelection: false });
    });

    // Search (debounced)
    let t = null;
    searchEl?.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => {
        this.state.q = String(searchEl.value || "").trim();
        this.state.offset = 0;
        this.refreshList({ keepSelection: false });
      }, 250);
    });

    refreshBtn?.addEventListener("click", () => {
      this.refreshList({ keepSelection: true });
    });

    markAllBtn?.addEventListener("click", async () => {
      try {
        const { res, data } = await apiJson(this._routeMarkAllRead(), { method: "POST" });
        if (!res.ok || data?.ok === false) return toast(data?.error || "Failed to mark all read.");
        toast("Marked all read ✓");
        this.state.offset = 0;
        this.state.selectedId = null;
        await this.refreshList({ keepSelection: false });
      } catch (e) {
        console.error(e);
        toast("Failed to mark all read.");
      }
    });

    prevBtn?.addEventListener("click", () => {
      this.state.offset = Math.max(0, this.state.offset - this.state.limit);
      this.refreshList({ keepSelection: false });
    });

    nextBtn?.addEventListener("click", () => {
      // if backend returns total, enforce it; otherwise allow next and the page may be empty
      if (this.state.total != null && this.state.offset + this.state.limit >= this.state.total) return;
      this.state.offset += this.state.limit;
      this.refreshList({ keepSelection: false });
    });

    // Delegated clicks inside list/detail
    document.addEventListener("click", async (e) => {
      const openBtn = e.target.closest("[data-message-open]");
      if (openBtn) {
        const id = openBtn.getAttribute("data-message-open");
        if (!id) return;
        this.state.selectedId = id;

        // find the object by re-parsing from the list by refetching quickly:
        // simplest: refreshList(keepSelection:true) then mark read.
        await this.refreshList({ keepSelection: true });

        // after refreshList, detail is already rendered if selection found;
        // mark read based on selected message in current DOM by clicking "Mark read" if present
        const markBtn = document.querySelector(`[data-message-mark-read="${CSS.escape(id)}"]`);
        if (markBtn) markBtn.click();
        return;
      }

      const markBtn = e.target.closest("[data-message-mark-read]");
      if (markBtn) {
        const id = markBtn.getAttribute("data-message-mark-read");
        if (!id) return;
        try {
          const { res, data } = await apiJson(this._routeMarkRead(id), { method: "POST" });
          if (!res.ok || data?.ok === false) return;
          toast("Marked read ✓");
          await this.refreshList({ keepSelection: true });
        } catch (err) {
          console.error(err);
          toast("Failed to mark read.");
        }
      }
    });
  },

  async openView() {
    // called by router when view-messages becomes visible
    this.initOnce();
    await this.refreshUnreadBadge();
    await this.refreshList({ keepSelection: false });
  },
};




  // ----------------------------
  // Overview counters + chart refresh (no hard refresh)
  // NOTE: make sure your HTML has:
  //   <div id="stat-total">...</div>
  //   <div id="stat-live">...</div>
  //   <div id="stat-offline">...</div>
  // ----------------------------
  let statusChartInstance = null;

  function recomputeLiveOffline() {
    const cards = document.querySelectorAll("[data-property-card]");
    const total = cards.length;
    let live = 0;
    cards.forEach((c) => {
      if (c.dataset.live === "true") live += 1;
    });
    return { total, live, offline: total - live };
  }

function updateOverviewUI() {
  const { total, live, offline } = recomputeLiveOffline();

  const totalEl = document.getElementById("stat-total");
  const liveEl = document.getElementById("stat-live");
  const offEl = document.getElementById("stat-offline");

  if (totalEl) totalEl.textContent = String(total);
  if (liveEl) liveEl.textContent = String(live);
  if (offEl) offEl.textContent = String(offline);

   if (window.__overviewPortfolioChart) {
    window.__overviewPortfolioChart.data.datasets[0].data = [live, offline];
    window.__overviewPortfolioChart.update();
  }
}


// ----------------------------
  // Properties Signal
  // ----------------------------

function getSelectedMoodFilter() {
  const sel = document.getElementById("moodFilter");
  return sel ? String(sel.value || "").toLowerCase().trim() : "";
}

function getRowMood(row) {
  const rowRaw = row.getAttribute("data-emotional-signals");
  const badgeRaw =
    row.querySelector("[data-mood-badge]")?.getAttribute("data-emotional-signals");

  const raw = rowRaw ?? badgeRaw ?? "[]";

  // Single source of truth
  return window.normalizeEmotionalSignals?.(raw) || [];
}


  // ----------------------------
  // Sidebar collapse/expand
  // ----------------------------
  function initSidebar() {
    const sidebar = document.getElementById("sidebar");
    const toggleBtn = document.getElementById("sidebar-toggle");
    const labelEls = () =>
      Array.from(document.querySelectorAll(".sidebar-label"));

    if (!sidebar || !toggleBtn) return;

    function setCollapsed(isCollapsed) {
      sidebar.classList.toggle("w-72", !isCollapsed);
      sidebar.classList.toggle("w-20", isCollapsed);
      labelEls().forEach((el) => el.classList.toggle("hidden", isCollapsed));

      const svg = toggleBtn.querySelector("svg");
      if (svg) {
        svg.style.transform = isCollapsed ? "rotate(180deg)" : "rotate(0deg)";
        svg.style.transition = "transform 200ms ease";
      }
      localStorage.setItem(
        "dashboard_sidebar_collapsed",
        isCollapsed ? "1" : "0"
      );
    }

    setCollapsed(localStorage.getItem("dashboard_sidebar_collapsed") === "1");

    toggleBtn.addEventListener("click", () => {
      const isCollapsed = sidebar.classList.contains("w-20");
      setCollapsed(!isCollapsed);
    });

    // ⌘K focus
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        document.getElementById("globalSearch")?.focus();
      }
    });
  }

  // ----------------------------
  // Properties filter
  // ----------------------------
  function filterProperties() {
    const search = (
      document.getElementById("searchInput")?.value || ""
    ).toLowerCase();
    const status = document.getElementById("statusFilter")?.value || "all";
    const cards = document.querySelectorAll("[data-property-card]");

    cards.forEach((card) => {
      const name = (card.dataset.name || "").toLowerCase();
      const live = card.dataset.live === "true";
      const matchesSearch = name.includes(search);
      const matchesStatus =
        status === "all" ||
        (status === "live" && live) ||
        (status === "offline" && !live);

      card.style.display = matchesSearch && matchesStatus ? "" : "none";
    });
  }
  window.filterProperties = filterProperties;

 

  // ----------------------------
  // Property actions
  // ----------------------------
  window.syncProperty = async function (id, btn) {
    if (IS_LOCKED) return toast("Complete payment to unlock property syncing.");

    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = "Syncing…";

    try {
      const res = await postJson(`/auth/sync-property/${id}`);

      if (res.status === 401 || res.status === 403) return loginRedirect();
      if (res.status === 402) return (window.location.href = "/pmc/signup");

      let data = {};
      try {
        data = await res.json();
      } catch {}
      btn.innerHTML =
        res.ok && data.status === "success"
          ? "Synced ✓"
          : data.message || data.detail || "Failed";
    } catch (err) {
      console.error("Sync error:", err);
      btn.innerHTML = "Error";
    } finally {
      setTimeout(() => {
        btn.innerHTML = original;
        btn.disabled = false;
      }, 1200);
    }
  };

  window.toggleProperty = async function (id, btn) {
    if (IS_LOCKED) return toast("Complete payment to unlock Sandy activation.");

    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = "Toggling…";

    try {
      const res = await postJson(`/auth/toggle-property/${id}`);
      if (res.status === 401 || res.status === 403) return loginRedirect();

      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }

      if (data && data.status === "needs_billing" && data.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }

      if (!res.ok) {
        toast((data && (data.detail || data.message)) || "Request failed");
        btn.innerHTML = original;
        return;
      }

      if (data && data.status === "success") {
        const isLive = data.new_status === "LIVE";
        btn.innerHTML = isLive ? "Take Offline" : "Go Live";

        const card = btn.closest("[data-property-card]");
        if (card) {
          card.dataset.live = isLive ? "true" : "false";

          const pill = card.querySelector("span.rounded-full");
          if (pill) {
            if (isLive) {
              pill.textContent = "LIVE";
              pill.className =
                "shrink-0 text-xs px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 font-semibold";
            } else {
              pill.textContent = "OFFLINE";
              pill.className =
                "shrink-0 text-xs px-2.5 py-1 rounded-full bg-rose-50 text-rose-700 border border-rose-100 font-semibold";
            }
          }
        }

        filterProperties();
        updateOverviewUI(); // ✅ update overview immediately
        return;
      }

      btn.innerHTML = original;
      toast("Toggle failed.");
    } catch (err) {
      console.error("Toggle error:", err);
      btn.innerHTML = original;
      toast("Network error. Please try again.");
    } finally {
      btn.disabled = false;
    }
  };


   document.addEventListener("click", (e) => {
  const editBtn = e.target.closest("[data-guide-edit]");
  if (editBtn) {
    const id = (editBtn.getAttribute("data-guide-edit") || "").trim();
    if (id) Guides.openEdit(id);
    return;
  }

  const delBtn = e.target.closest("[data-guide-delete]");
  if (delBtn) {
    const id = (delBtn.getAttribute("data-guide-delete") || "").trim();
    if (id) Guides.remove(id);
    return;
  }
});

  

   document.addEventListener("change", async (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement)) return;
  if (!el.matches("[data-guide-active]")) return;

  if (window.CONTENT_LOCKED) {
    toast("Complete payment to unlock Guides.");
    el.checked = !el.checked;
    return;
  }

  const id = el.dataset.guideId;
  const checked = el.checked;

  const row = el.closest("[data-guide-row]");
  const label = row?.querySelector("[data-guide-status-label]");

  if (label) {
    label.textContent = checked ? "Active" : "Inactive";
    label.className =
      "text-xs font-semibold " + (checked ? "text-emerald-700" : "text-slate-400");
  }

  try {
    const { res, data } = await apiJson("/admin/guides/ajax/toggle-active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, is_active: checked }),
    });

    if (!res.ok || !data.ok) {
      toast(data.error || "Failed to update.");
      el.checked = !checked;

      if (label) {
        const reverted = el.checked;
        label.textContent = reverted ? "Active" : "Inactive";
        label.className =
          "text-xs font-semibold " + (reverted ? "text-emerald-700" : "text-slate-400");
      }
    }
  } catch (err) {
    toast("Failed to update.");
    el.checked = !checked;

    if (label) {
      const reverted = el.checked;
      label.textContent = reverted ? "Active" : "Inactive";
      label.className =
        "text-xs font-semibold " + (reverted ? "text-emerald-700" : "text-slate-400");
    }
  }
});

window.Chats = window.Chats || {};

window.Chats.refreshSummary = async function refreshSummary(sessionId) {
  const panel =
    document.querySelector(`[data-chat-panel="${sessionId}"]`) ||
    document.querySelector("[data-chat-panel]");
  if (!panel) return;

  const box = panel.querySelector("[data-summary-box]");
  if (!box) return;

  const updatedLabel = panel.querySelector("[data-summary-updated]");

  try {
    box.textContent = "Generating…";

    const url =
      (typeof apiRoute === "function" && apiRoute("chat_summarize", { session_id: sessionId })) ||
      `/admin/chats/${sessionId}/summarize`;

    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: "{}",
    });

    if (res.status === 401 || res.status === 403) {
      if (typeof loginRedirect === "function") return loginRedirect();
      throw new Error("Not authenticated");
    }

    const parsed = await safeReadJson(res);
    if (!res.ok) {
      const msg =
        parsed?.json?.error ||
        parsed?.json?.detail ||
        parsed?.error ||
        `Request failed (HTTP ${res.status})`;
      throw new Error(msg);
    }

    const data = parsed?.json || {};
    if (data.ok === false) throw new Error(data.error || "Summarize failed");

    box.textContent = data.summary || "";
    if (updatedLabel) {
      updatedLabel.textContent = data.updated_at
        ? `Updated: ${data.updated_at}`
        : "Updated: just now";
    }
  } catch (e) {
    box.textContent = `Summary error: ${e?.message || e}`;
    console.error(e);
  }
};

// ✅ global alias so any old code calling refreshSummary(...) still works
window.refreshSummary = (sessionId) => window.Chats.refreshSummary(sessionId);

// ----------------------------
// Upgrades (single definition + resilient AJAX handling)
// ----------------------------
window.Upgrades = {
  loaded: false,

async refresh() {
  const listEl = document.getElementById("upgrades-list");
  if (!listEl) return;

  const pid = document.getElementById("upgradesPropertyFilter")?.value || "";
  const qs = pid ? `?property_id=${encodeURIComponent(pid)}` : "";

  listEl.innerHTML = "Loading…";

  try {
    const res = await fetch(`/admin/upgrades/partial/list${qs}`, {
      credentials: "include",
      headers: { "X-Requested-With": "fetch" },
    });

    if (res.status === 401 || res.status === 403) return loginRedirect();
    if (!res.ok) {
      listEl.innerHTML = "Could not load upgrades.";
      return;
    }

    const html = await res.text();
    listEl.innerHTML = html;

    // ✅ Always rebind after injection (don’t rely on local query only)
    initAllReorderTables();

    console.log("Upgrades list refreshed; reorder bound:", !!listEl.querySelector("table[data-list-kind]"));
  } catch (err) {
    console.error("Upgrades.refresh error:", err);
    listEl.innerHTML = "Could not load upgrades.";
  }
},

  openNew() {
    return window.Upgrades.openEditor(null);
  },

async openEditor(id) {
  if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Upgrades.");

  const editorWrap = document.getElementById("upgrades-editor");
  const editorBody = document.getElementById("upgrades-editor-body");
  const editorTitle = document.getElementById("upgrades-editor-title");
  if (!editorWrap || !editorBody) return;

  const url = id
    ? `/admin/upgrades/partial/form?id=${encodeURIComponent(id)}`
    : `/admin/upgrades/partial/form`;

  // Show modal immediately (better UX, and ensures DOM exists)
  if (editorTitle) editorTitle.textContent = id ? "Edit Upgrade" : "New Upgrade";
  editorWrap.classList.remove("hidden");
  editorBody.innerHTML = `<div class="text-sm text-slate-500">Loading…</div>`;

  try {
    const res = await fetch(url, { credentials: "include" });

    if (res.status === 401 || res.status === 403) return loginRedirect();
    if (!res.ok) {
      editorBody.innerHTML = `<div class="text-sm text-rose-700">Could not load upgrade editor.</div>`;
      return;
    }

    editorBody.innerHTML = await res.text();

  } catch (err) {
    console.error("Fetch failed:", err);
    editorBody.innerHTML = `<div class="text-sm text-rose-700">Network error loading editor.</div>`;
  }
},


  closeEditor() {
    document.getElementById("upgrades-editor")?.classList.add("hidden");
    const body = document.getElementById("upgrades-editor-body");
    if (body) body.innerHTML = "";
  },

  async submit(form) {
    if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Upgrades.");
    if (!form) return;

    const flash = document.getElementById("upgrades-flash");

    const showFlash = (msg, ok = true) => {
      if (!flash) return toast(msg);

      flash.innerHTML = `
        <div class="rounded-xl border ${
          ok
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-rose-200 bg-rose-50 text-rose-900"
        } p-3 text-sm">
          ${msg}
        </div>`;

      setTimeout(() => {
        if (flash) flash.innerHTML = "";
      }, 2400);
    };

    try {
      const res = await fetch("/admin/upgrades/ajax/save", {
        method: "POST",
        credentials: "include",
        body: new FormData(form),
      });

      if (res.status === 401 || res.status === 403) return loginRedirect();

      const contentType = (res.headers.get("content-type") || "").toLowerCase();
      if (!contentType.includes("application/json")) {
        const text = await res.text().catch(() => "");
        console.error("Save returned non-JSON:", {
          status: res.status,
          contentType,
          preview: text.slice(0, 500),
        });
        showFlash("Save failed (server returned non-JSON).", false);
        return;
      }

      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data.ok) {
        showFlash(data.error || data.detail || data.message || "Save failed.", false);
        return;
      }

      showFlash("Saved ✓", true);
      await window.Upgrades.refresh();
      window.Upgrades.closeEditor();
    } catch (err) {
      console.error(err);
      showFlash("Network error saving upgrade.", false);
    }
  },
};




   document.addEventListener("input", (e) => {
  const titleInput = e.target;
  if (!(titleInput instanceof HTMLInputElement)) return;
  if (!titleInput.matches('input[name="title"]')) return;

  const form = titleInput.closest("form");
  if (!form) return;

  const slugInput = form.querySelector('[data-upgrade-slug]');
  if (!slugInput) return;

  // Only auto-generate if slug is empty (don’t overwrite existing edits)
  if (slugInput.value) return;

  slugInput.value = titleInput.value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
});


// Upload image (delegated) — works after partial loads
document.addEventListener("click", async (e) => {
  const uploadBtn = e.target.closest("[data-upgrade-image-upload]");
  if (!uploadBtn) return;

  const form = uploadBtn.closest("form");
  if (!form) return;

  const fileInput = form.querySelector("[data-upgrade-image-file]");
  const preview = form.querySelector("[data-upgrade-image-preview]");
  const tmpKeyInput = form.querySelector('input[name="image_tmp_key"]');
  const imageUrlInput = form.querySelector('input[name="image_url"]'); // keep existing DB value!

  const file = fileInput?.files?.[0];
  if (!file) return toast("Choose an image first.");

  const fd = new FormData();
  fd.append("file", file);

  // If editing, pass upgrade id (optional, but useful on backend)
  const upgradeId = (form.querySelector('input[name="id"]')?.value || "").trim();
  if (upgradeId) fd.append("upgrade_id", upgradeId);

  // IMPORTANT: pass previous tmp key so backend can delete/replace it
  const prevTmpKey = (tmpKeyInput?.value || "").trim();
  if (prevTmpKey) fd.append("prev_tmp_key", prevTmpKey);

  uploadBtn.disabled = true;
  const original = uploadBtn.textContent;
  uploadBtn.textContent = "Uploading…";

  try {
    const res = await fetch("/admin/upgrades/ajax/upload-image", {
      method: "POST",
      credentials: "include",
      body: fd,
    });

    if (res.status === 401 || res.status === 403) return loginRedirect();

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      toast(data.error || "Upload failed.");
      return;
    }

    // Set ONLY the temp key (for finalize on Save)
    if (tmpKeyInput) tmpKeyInput.value = data.tmp_key || "";

    // DO NOT overwrite the saved image_url here if using tmp-key finalize workflow.
    // Keep existing DB image_url until Save runs.
    // imageUrlInput.value should only be changed in /admin/upgrades/ajax/save after finalize.
    // (But we can still show preview below.)

    // Preview from returned preview_url (could be temp-served or final)
    const previewUrl = data.preview_url || "";
    if (preview) {
      preview.src = previewUrl;
      preview.classList.toggle("hidden", !previewUrl);
    }

    // Optional: visually indicate pending unsaved change
    // e.g., store a data flag for later if you want
    if (imageUrlInput) imageUrlInput.dataset.pendingUpload = "1";

    // Clear file input so user can reselect same file again if needed
    if (fileInput) fileInput.value = "";

    toast("Image uploaded. Click Save Upgrade to apply.");
  } catch (err) {
    console.error(err);
    toast("Upload failed.");
  } finally {
    uploadBtn.disabled = false;
    uploadBtn.textContent = original;
  }
});


document.addEventListener("click", async (e) => {
  const clearBtn = e.target.closest("[data-upgrade-image-clear]");
  if (!clearBtn) return;

  const form = clearBtn.closest("form");
  if (!form) return;

  const preview = form.querySelector("[data-upgrade-image-preview]");
  const imageUrlInput = form.querySelector('input[name="image_url"]');
  const tmpKeyInput = form.querySelector('input[name="image_tmp_key"]');

  const tmpKey = (tmpKeyInput?.value || "").trim();

  // Clear UI immediately
  if (preview) {
    preview.src = "";
    preview.classList.add("hidden");
  }
  if (imageUrlInput) imageUrlInput.value = "";   // clear persisted url
  if (tmpKeyInput) tmpKeyInput.value = "";       // clear temp key

  // If there was a temp upload, delete it server-side
  if (tmpKey) {
    try {
      const res = await fetch("/admin/upgrades/ajax/delete-temp-image", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tmp_key: tmpKey }),
      });
      if (res.status === 401 || res.status === 403) return loginRedirect();
    } catch (err) {
      console.error(err);
    }
  }

  toast("Image removed.");
});


// Upgrades: delegated events (ONLY ONCE)
document.addEventListener("click", async (e) => {
  // Edit
  const editBtn = e.target.closest("[data-upgrade-edit]");
  if (editBtn) {
    const id = (editBtn.getAttribute("data-upgrade-edit") || "").trim();
    if (!id || id === "None" || id === "null" || id === "undefined") return;
    Upgrades.openEditor(id);
    return;
  }

  // Delete
  const delBtn = e.target.closest("[data-upgrade-delete]");
  if (!delBtn) return;

  if (window.CONTENT_LOCKED) {
    toast("Complete payment to unlock Upgrades.");
    return;
  }

  const id = (delBtn.getAttribute("data-upgrade-delete") || "").trim();
  if (!id || id === "None" || id === "null" || id === "undefined") return;

  if (!confirm("Delete this upgrade?")) return;

  try {
    const { res, data } = await apiJson(`/admin/upgrades/ajax/delete?id=${encodeURIComponent(id)}`, {
      method: "POST",
    });

    if (!res.ok || !data.ok) {
      toast(data.error || data.detail || "Delete failed.");
      return;
    }

    toast("Upgrade deleted");
    await Upgrades.refresh();
    Upgrades.closeEditor();
  } catch (err) {
    console.error(err);
    toast("Delete failed.");
  }
});



  document.addEventListener("change", async (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement)) return;
    if (!el.matches("[data-upgrade-active]")) return;

    const id = el.dataset.upgradeId;
    const checked = el.checked;

    // Optional: label support if you add it in the table partial
    const row = el.closest("[data-upgrade-row]");
    const label = row?.querySelector("[data-upgrade-status-label]");
    if (label) {
      label.textContent = checked ? "Active" : "Inactive";
      label.className =
        "text-xs font-semibold " +
        (checked ? "text-emerald-700" : "text-slate-400");
    }

    try {
      const { res, data } = await apiJson("/admin/upgrades/ajax/toggle-active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, is_active: checked }),
      });

      if (!res.ok || !data.ok) {
        toast(data.error || "Failed to update.");
        el.checked = !checked;

        if (label) {
          const reverted = el.checked;
          label.textContent = reverted ? "Active" : "Inactive";
          label.className =
            "text-xs font-semibold " +
            (reverted ? "text-emerald-700" : "text-slate-400");
        }
      }
    } catch (err) {
      toast("Failed to update.");
      el.checked = !checked;

      if (label) {
        const reverted = el.checked;
        label.textContent = reverted ? "Active" : "Inactive";
        label.className =
          "text-xs font-semibold " +
          (reverted ? "text-emerald-700" : "text-slate-400");
      }
    }
  });

  // ----------------------------
  // Guides (single definition)
  // ----------------------------
  function flashIn(elId, msg, ok = true) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = `<div class="text-sm rounded-xl px-3 py-2 ${
      ok
        ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
        : "bg-rose-50 border border-rose-200 text-rose-800"
    }">${msg}</div>`;
    setTimeout(() => (el.innerHTML = ""), 2400);
  }

 async function loadHtmlInto(url, targetId) {
  const r = await fetch(url, {
    credentials: "include",
    headers: { "X-Requested-With": "fetch" },
  });

  if (r.status === 401 || r.status === 403) return loginRedirect();

  const target = document.getElementById(targetId);
  if (!target) return;

  const html = await r.text();
  target.innerHTML = html;

  // ✅ Rebind ALL reorder tables after any partial inject
  initAllReorderTables();

  // Helpful debug (you can remove later)
  console.log("Injected partial into:", targetId, "tables:", document.querySelectorAll("table[data-list-kind]").length);
}



  window.Guides = {
    loaded: false,

    refresh() {
      const pid = document.getElementById("guidesPropertyFilter")?.value || "";
      const qs = pid ? `?property_id=${encodeURIComponent(pid)}` : "";
      return loadHtmlInto(`/admin/guides/partial/list${qs}`, "guides-list");
    },

    openNew() {
      return Guides.openForm("/admin/guides/partial/form", "New Guide");
    },

    openEdit(id) {
      return Guides.openForm(
        `/admin/guides/partial/form?id=${encodeURIComponent(id)}`,
        "Edit Guide"
      );
    },

    async openForm(url, title) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      document.getElementById("guides-editor-title").textContent = title || "Editor";
      await loadHtmlInto(url, "guides-editor-body");
      document.getElementById("guides-editor")?.classList.remove("hidden");
    },

    closeEditor() {
      document.getElementById("guides-editor")?.classList.add("hidden");
      const body = document.getElementById("guides-editor-body");
      if (body) body.innerHTML = "";
    },

    async submit(formEl) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      const r = await fetch("/admin/guides/ajax/save", {
        method: "POST",
        credentials: "include",
        body: new FormData(formEl),
      });
      if (r.status === 401 || r.status === 403) return loginRedirect();
      const j = await r.json().catch(() => ({}));
      if (!j.ok) return flashIn("guides-flash", j.error || "Save failed", false);
      flashIn("guides-flash", "Guide saved");
      Guides.closeEditor();
      Guides.refresh();
    },

    async remove(id) {
      if (window.CONTENT_LOCKED) return toast("Complete payment to unlock Guides.");
      if (!confirm("Delete this guide?")) return;
      const r = await fetch(`/admin/guides/ajax/delete?id=${encodeURIComponent(id)}`, {
        method: "POST",
        credentials: "include",
      });
      const j = await r.json().catch(() => ({}));
      if (!j.ok) return flashIn("guides-flash", j.error || "Delete failed", false);
      flashIn("guides-flash", "Guide deleted");
      Guides.refresh();
    },
  };

  // ----------------------------
  // Settings tabs + Team settings
  // ----------------------------
  function getSettingsTabFromHash() {
    const h = location.hash || "";
    if (!h.startsWith("#settings")) return "profile";
    const q = h.split("?")[1] || "";
    const params = new URLSearchParams(q);
    return params.get("tab") || "profile";
  }

  function showSettingsPanel(key) {
    document.querySelectorAll(".settings-panel").forEach((p) => p.classList.add("hidden"));
    document.getElementById(`settings-${key}`)?.classList.remove("hidden");

    document.querySelectorAll(".settings-tab").forEach((b) => b.classList.remove("bg-slate-50"));
    document
      .querySelector(`.settings-tab[data-settings="${key}"]`)
      ?.classList.add("bg-slate-50");

    const base = (location.hash || "#overview").split("?")[0];
    if (base === "#settings") history.replaceState(null, "", `#settings?tab=${key}`);
  }

  function updateMemberStatusUI(memberId, isActive) {
    const row = document.querySelector(`[data-member-row="${memberId}"]`);
    if (!row) return;

    const statusEl = row.querySelector("[data-member-status]");
    if (statusEl) {
      statusEl.textContent = isActive ? "Active" : "Disabled";
      statusEl.className = isActive
        ? "text-xs font-semibold text-emerald-700"
        : "text-xs font-semibold text-rose-700";
    }

    const toggleBtn = row.querySelector("[data-member-toggle]");
    if (toggleBtn) {
      toggleBtn.textContent = isActive ? "Disable" : "Enable";
      toggleBtn.dataset.nextActive = isActive ? "false" : "true";
    }
  }

  function setSaveEnabled(memberId, enabled) {
    const btn = document.querySelector(`[data-member-save-role][data-member-id="${memberId}"]`);
    if (!btn) return;
    btn.disabled = !enabled;
    btn.classList.toggle("opacity-50", !enabled);
    btn.classList.toggle("cursor-not-allowed", !enabled);
  }

  async function refreshTeamRows() {
    const res = await fetch("/admin/settings/team/table", { credentials: "include" });

    if (res.status === 401 || res.status === 403) {
      toast("Session expired. Please sign in again.");
      loginRedirect();
      return false;
    }
    if (!res.ok) {
      toast("Could not refresh team list.");
      return false;
    }

    const html = await res.text();
    const tbody = document.querySelector("#settings-team tbody");
    if (tbody) tbody.innerHTML = html;

    // re-disable Save buttons by default
    document.querySelectorAll("[data-member-save-role]").forEach((b) => {
      b.disabled = true;
      b.classList.add("opacity-50", "cursor-not-allowed");
    });

    return true;
  }

  function initTeamSettings() {
    const teamPanel = document.getElementById("settings-team");
    if (!teamPanel) return;

    // Disable Save buttons initially
    document.querySelectorAll("[data-member-save-role]").forEach((b) => {
      b.disabled = true;
      b.classList.add("opacity-50", "cursor-not-allowed");
    });

    // Modal open/close
    const inviteModal = document.getElementById("inviteModal");
    document.getElementById("openInvite")?.addEventListener("click", () => inviteModal?.classList.remove("hidden"));
    ["closeInvite", "cancelInvite"].forEach((id) => {
      document.getElementById(id)?.addEventListener("click", () => inviteModal?.classList.add("hidden"));
    });

    // Invite submit
    document.getElementById("inviteForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();

      const formEl = e.currentTarget;
      const body = Object.fromEntries(new FormData(formEl).entries());

      try {
        const res = await fetch("/admin/settings/team/invite", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(body),
        });

        const data = await res.json().catch(() => ({}));

        if (res.ok && data.ok) {
          toast(data.message || "Invite saved.");
          formEl.reset();
          inviteModal?.classList.add("hidden");
          await refreshTeamRows();
        } else {
          toast(data.detail || data.message || "Invite failed.");
        }
      } catch (err) {
        console.error(err);
        toast("Invite failed.");
      }
    });

    // Role dropdown -> enable Save if changed
    teamPanel.addEventListener("change", (e) => {
      const el = e.target;
      if (!(el instanceof HTMLSelectElement)) return;
      if (!el.matches("[data-member-role]")) return;

      const memberId = el.dataset.memberId;
      const original = (el.dataset.originalRole || "").trim();
      const current = (el.value || "").trim();
      if (!memberId) return;

      setSaveEnabled(memberId, current !== original);
    });

    // Delegated team actions
    teamPanel.addEventListener("click", async (e) => {
      const btn = e.target instanceof HTMLElement ? e.target.closest("button") : null;
      if (!btn) return;

      // Save role
      if (btn.matches("[data-member-save-role]")) {
        const memberId = btn.dataset.memberId;
        const select = document.querySelector(`[data-member-role][data-member-id="${memberId}"]`);
        const role = select?.value;
        if (!memberId || !role) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role }),
          });

          if (res.ok && data.ok) {
            toast(data.message || "Role saved.");
            await refreshTeamRows();
          } else {
            toast(data.detail || data.message || "Failed to save role.");
            btn.disabled = false;
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Failed to save role.");
          btn.disabled = false;
        }
        return;
      }

      // Enable / Disable
      if (btn.matches("[data-member-toggle]")) {
        const memberId = btn.dataset.memberId;
        const nextActive = btn.dataset.nextActive;
        if (!memberId || (nextActive !== "true" && nextActive !== "false")) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_active: nextActive === "true" }),
          });

          if (res.ok && data.ok) {
            const nowActive = nextActive === "true";
            updateMemberStatusUI(memberId, nowActive);
            toast(nowActive ? "User enabled." : "User disabled.");
          } else {
            toast(data.detail || data.message || "Failed to update status.");
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Failed to update status.");
        } finally {
          btn.disabled = false;
        }
        return;
      }

      // Delete invite / Remove
      if (btn.matches("[data-member-delete]")) {
        const memberId = btn.dataset.memberId;
        const label = btn.dataset.deleteLabel || "Remove";
        if (!memberId) return;

        if (!confirm(`${label}? This cannot be undone.`)) return;

        btn.disabled = true;
        try {
          const { res, data } = await apiJson(`/admin/settings/team/${memberId}`, { method: "DELETE" });
          if (res.ok && data.ok) {
            document.querySelector(`[data-member-row="${memberId}"]`)?.remove();
            toast(label === "Delete invite" ? "Invite deleted." : "User removed.");
          } else {
            toast(data.detail || data.message || "Delete failed.");
          }
        } catch (err) {
          if (String(err) !== "Error: auth") toast("Delete failed.");
        } finally {
          btn.disabled = false;
        }
      }
    });
  }

  // ----------------------------
  // Settings init (only once)
  // ----------------------------
  let settingsInitialized = false;

  function initSettingsUI() {
    if (settingsInitialized) return;
    settingsInitialized = true;

    document.querySelectorAll(".settings-tab").forEach((btn) => {
      btn.addEventListener("click", () => showSettingsPanel(btn.dataset.settings));
    });

    document.getElementById("profileForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget;
      const body = Object.fromEntries(new FormData(form).entries());

      try {
        const { res, data } = await apiJson("/admin/settings/profile", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (res.ok && (data.ok ?? true)) toast("Profile saved.");
        else toast(data.detail || data.message || "Failed to save profile.");
      } catch (err) {
        if (String(err) !== "Error: auth") toast("Failed to save profile.");
      }
    });

    document.getElementById("notifForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.currentTarget);

      const keys = ["guest_messages", "maintenance_assigned", "turnover_due"];
      const prefs = {};
      keys.forEach((k) => (prefs[k] = fd.get(k) !== null));

      try {
        const { res, data } = await apiJson("/admin/settings/notifications", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prefs }),
        });
        if (res.ok && (data.ok ?? true)) toast("Notifications saved.");
        else toast(data.detail || data.message || "Failed to save notifications.");
      } catch (err) {
        if (String(err) !== "Error: auth") toast("Failed to save notifications.");
      }
    });

    initTeamSettings();
    showSettingsPanel(getSettingsTabFromHash());
  }




// Helper: "guides" -> "guide", "upgrades" -> "upgrade"
function singularize(kind) {
  const k = String(kind || "").trim().toLowerCase();
  if (!k) return "";
  if (k.endsWith("ies")) return k.slice(0, -3) + "y";
  if (k.endsWith("s")) return k.slice(0, -1);
  return k;
}

// Helper: accept "guide" or "guides" and normalize to plural route kind
function normalizeKind(kind) {
  const k = String(kind || "").trim().toLowerCase();
  if (k === "guide") return "guides";
  if (k === "upgrade") return "upgrades";
  return k;
}

function initReorderForTable(rootEl) {
  if (!rootEl) return;

  if (!window.Sortable) {
    console.warn("Sortable missing — drag/drop disabled.");
    return;
  }

  // ✅ normalize (critical)
  const rawKind = (rootEl.getAttribute("data-list-kind") || "").trim();
  const kind = normalizeKind(rawKind); // "guides" | "upgrades"
  if (!kind) {
    console.warn("Missing data-list-kind on table.");
    return;
  }

  const tbody = rootEl.querySelector("[data-reorder-body]") || rootEl.querySelector("tbody");
  if (!tbody) {
    console.warn("No tbody found for reorder table.");
    return;
  }

  // Prevent double init
  if (tbody._sortable) return;

  const singular = singularize(kind); // "guide" | "upgrade"
  const rowAttrSingular = `data-${singular}-row`; // data-guide-row
  const rowAttrPlural = `data-${kind}-row`;       // data-guides-row

  const hasHandles = rootEl.querySelectorAll("[data-reorder-handle]").length > 0;

  tbody._sortable = new Sortable(tbody, {
    animation: 150,
    draggable: "tr",
    ...(hasHandles ? { handle: "[data-reorder-handle]" } : {}),

    onEnd: async () => {
      // Prefer singular row attr, fallback to plural
      let rows = Array.from(tbody.querySelectorAll(`tr[${rowAttrSingular}]`));
      if (!rows.length) rows = Array.from(tbody.querySelectorAll(`tr[${rowAttrPlural}]`));

      const idsRaw = rows
        .map((tr) => (tr.getAttribute(rowAttrSingular) || tr.getAttribute(rowAttrPlural) || "").trim())
        .filter(Boolean);

      const ids = idsRaw.map(Number).filter(Number.isFinite);

      if (!ids.length) {
        console.error("Reorder: no valid numeric IDs found.", {
          rawKind, kind, idsRaw, rowAttrSingular, rowAttrPlural
        });
        toast("Could not reorder: row IDs missing.");
        return;
      }

      const url =
        kind === "guides"
          ? "/admin/guides/ajax/reorder"
          : "/admin/upgrades/ajax/reorder";

      const payload = { ids };

      try {
        const { res, data } = await apiJson(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (!res.ok || !data.ok) {
          console.error("Reorder failed:", { url, status: res.status, data, payload });
          toast(data?.error || data?.detail || "Failed to save order.");
          // revert UI to DB order
          if (kind === "guides") await Guides.refresh();
          if (kind === "upgrades") await Upgrades.refresh();
          return;
        }

        toast("Order saved ✓");
      } catch (err) {
        console.error("Reorder error:", err);
        toast("Failed to save order.");
        if (kind === "guides") await Guides.refresh();
        if (kind === "upgrades") await Upgrades.refresh();
      }
    },
  });

  console.log("Sortable bound:", { rawKind, kind, rowAttrSingular, rowAttrPlural, hasHandles });
}
function initAllReorderTables() {
  document.querySelectorAll("table[data-list-kind]").forEach(initReorderForTable);
}


function initRouting() {
  if (routingInitialized) return;
  routingInitialized = true;

  const pageTitle = document.getElementById("page-title");
  const pageSubtitle = document.getElementById("page-subtitle");
  const navItems = Array.from(document.querySelectorAll(".nav-item"));
  const views = Array.from(document.querySelectorAll(".view"));

  const titles = {
    overview: "Overview",
    properties: "Properties",
    chats: "Conversations",
    pmcs: "PMCs",
    guides: "Guides",
    upgrades: "Upgrades",
    files: "Files",
    analytics: "Analytics",
    settings: "Settings",
    payouts: "Payouts",
    admin_payouts: "Revenue",
    messages: "Messages",
    tasks: "Tasks",
  };

  const subtitles = {
    overview: BOOT.user_role === "super" ? "System health & activity" : "Your portfolio at a glance",
    properties: "Search and manage your portfolio",
    chats: "Monitor how your AI is performing across every stage of the stay lifecycle.",
    pmcs: "Partners, integrations, access",
    guides: "Guest-facing guides per property",
    upgrades: "Paid add-ons per property",
    files: "Configs & manuals",
    analytics: BOOT.user_role === "super" ? "Trends & performance" : "Your chat trends & performance",
    settings: "Account and system settings",
    payouts: "Revenue and transfers",
    admin_payouts: "HostScout platform revenue",
    messages: "Admin notifications and inbox",
    tasks: "Track and manage operational work",
  };

  async function showView(key) {
    key = (key || "overview").toLowerCase();

    if (!document.getElementById(`view-${key}`)) key = "overview";
    currentViewKey = key;

    const url = new URL(window.location.href);
    const sessionId = url.searchParams.get("session_id");
    const isChatDetail = key === "chats" && !!sessionId;

    views.forEach((v) => v.classList.add("hidden"));
    document.getElementById(`view-${key}`)?.classList.remove("hidden");

    navItems.forEach((btn) => {
      btn.setAttribute("aria-current", "false");
      btn.classList.remove("active");
    });

    const activeBtn = navItems.find(
      (b) => (b.dataset.view || "").toLowerCase() === key
    );
    if (activeBtn) {
      activeBtn.setAttribute("aria-current", "page");
      activeBtn.classList.add("active");
    }

    if (pageTitle) {
      pageTitle.textContent = isChatDetail
        ? "Conversation detail"
        : (titles[key] || "Overview");
    }

    if (pageSubtitle) {
      pageSubtitle.textContent = isChatDetail
        ? "Monitor what happened, how the AI responded, and what should happen next."
        : (subtitles[key] || "");
    }

    if (key === "properties") filterProperties();

    if (key === "settings") {
      initSettingsUI();
      showSettingsPanel(getSettingsTabFromHash());
    }

    if (key === "guides" && window.Guides && !Guides.loaded) {
      Guides.loaded = true;
      Guides.refresh();
    }

    if (key === "upgrades" && window.Upgrades && !Upgrades.loaded) {
      Upgrades.loaded = true;
      Upgrades.refresh();
    }

    if (key === "tasks") {
      window.Tasks?.init?.();
      window.Tasks?.refresh?.();
    }

    if (key === "messages") {
      await window.Messages?.openView?.();
    }

    if (key === "analytics") {
      const days = document.getElementById("analyticsRange")?.value || 30;
      requestAnimationFrame(() => {
        loadChatAnalytics(days);
        resizeChatAnalyticsChartSoon();
      });
    }
  }

  async function route() {
    const params = new URLSearchParams(window.location.search);
    const keyFromHash = (location.hash || "").slice(1).split("?")[0].toLowerCase();
    const keyFromView = (params.get("view") || "").toLowerCase();
    const view = keyFromHash || keyFromView || "overview";
    const sessionId = params.get("session_id");

    await showView(view);

    if (view === "chats" && sessionId) {
      setInlineDetailOpen(true);
      await loadChatDetail(sessionId);
    } else {
      setInlineDetailOpen(false);
    }
  }

  navItems.forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      const key = (btn.dataset.view || "overview").toLowerCase();

      const url = new URL(window.location.href);
      url.searchParams.delete("session_id");
      url.searchParams.set("view", key);

      if (key === "chats") {
        url.hash = "";
      } else {
        url.hash = `#${key}`;
      }

      history.pushState(null, "", url.toString());
      await route();
    });
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#chat-detail-back");
    if (!btn) return;

    e.preventDefault();
    closeChatDetail();
  });

  window.addEventListener("popstate", route);
  window.addEventListener("hashchange", route);

  route();
}



function initSyncAllProperties() {
  const btn = document.getElementById("sync-all-properties-btn");
  if (!btn) return;

  btn.addEventListener("click", async () => {
    if (window.CONTENT_LOCKED) return toast("Complete payment to unlock property syncing.");

    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Syncing…";

    try {
      const res = await fetch(`/auth/sync-pmc-properties`, {
        method: "POST",
        credentials: "include",
      });

      if (res.status === 401 || res.status === 403) return loginRedirect();
      if (res.status === 402) return (window.location.href = "/pmc/signup");

      if (!res.ok) throw new Error(await res.text());

      window.location.reload();
    } catch (err) {
      console.error(err);
      alert("Failed to sync properties");
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  });
}

// ----------------------------
// TASKS MODULE (UI-matching + clean)
// ----------------------------
window.Tasks =
  window.Tasks ||
  (function () {
    const STATUS_ORDER = ["in_review", "in_progress", "waiting", "todo", "completed", "canceled"];

    const STATUS_LABEL = {
      todo: "To-do",
      in_progress: "In Progress",
      waiting: "Waiting",
      in_review: "In Review",
      canceled: "Canceled",
      completed: "Completed",
    };

    const STATUS_MENU = [
      { key: "todo", label: "To-do", dotClass: "is-todo", glyph: "" },
      { key: "in_progress", label: "In Progress", dotClass: "is-in_progress", glyph: "" },
      { key: "waiting", label: "Waiting", dotClass: "is-waiting", glyph: "" },
      { key: "in_review", label: "In Review", dotClass: "is-in_review", glyph: "" },
      { key: "canceled", label: "Canceled", dotClass: "is-canceled", glyph: "×" },
      { key: "completed", label: "Completed", dotClass: "is-completed", glyph: "✓" },
    ];

    const STATUS_PILL_CLASS = {
      in_review: "bg-amber-50 text-amber-700 border-amber-200",
      in_progress: "bg-sky-50 text-sky-700 border-sky-200",
      waiting: "bg-violet-50 text-violet-700 border-violet-200",
      todo: "bg-slate-50 text-slate-700 border-slate-200",
      completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
      canceled: "bg-rose-50 text-rose-700 border-rose-200",
    };

    // State
    let selected = new Set(); // ALWAYS store string IDs
    let activeTab = "all";
    let TEAM = [];
    const TEAM_BY_ID = new Map();

    // Categories (synced from /admin/api/tasks)
    let CATEGORIES = [];

    // Utils
    const $id = (id) => document.getElementById(id);
    const qsa = (parent, sel) => Array.from(parent.querySelectorAll(sel));

    function esc(s) {
      return String(s ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function isoToPrettyDate(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }

    function toDateInputValue(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      const yyyy = d.getFullYear();
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const dd = String(d.getDate()).padStart(2, "0");
      return `${yyyy}-${mm}-${dd}`;
    }

    function getDisplayName(u) {
      if (!u) return "";
      return (u.full_name || "").trim() || (u.email || "").trim() || `User ${u.id}`;
    }

    function initials(name) {
      const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
      if (!parts.length) return "?";
      const a = parts[0]?.[0] || "";
      const b = parts.length > 1 ? parts[parts.length - 1][0] : "";
      return (a + b).toUpperCase();
    }

    // ----------------------------
    // Icons (Status + Category)
    // ----------------------------
    function statusIconHTML(statusKey) {
      const s = (statusKey || "todo").toLowerCase();

      if (s === "todo")
        return `<span class="tasks-status-dot is-todo" style="width:12px;height:12px;border-width:2px;"></span>`;
      if (s === "in_progress")
        return `<span class="tasks-status-dot is-in_progress" style="width:12px;height:12px;border-width:2px;"></span>`;
      if (s === "waiting")
        return `<span class="tasks-status-dot is-waiting" style="width:12px;height:12px;border-width:2px;"></span>`;
      if (s === "in_review")
        return `<span class="tasks-status-dot is-in_review" style="width:12px;height:12px;border-width:2px;"></span>`;

      if (s === "canceled") {
        return `
          <span class="tasks-status-dot is-canceled" style="width:12px;height:12px;border-width:2px;">
            <svg viewBox="0 0 16 16" fill="none" aria-hidden="true" style="width:10px;height:10px;display:block;">
              <path d="M5.2 5.2 L10.8 10.8 M10.8 5.2 L5.2 10.8"
                    stroke="white" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </span>
        `;
      }

      if (s === "completed") {
        return `
          <span class="tasks-status-dot is-completed" style="width:12px;height:12px;border-width:2px;">
            <svg viewBox="0 0 16 16" fill="none" aria-hidden="true" style="width:10px;height:10px;display:block;">
              <path d="M4.4 8.2 L6.9 10.7 L11.8 5.8"
                    stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
        `;
      }

      return `<span class="tasks-status-dot is-todo" style="width:12px;height:12px;border-width:2px;"></span>`;
    }

    const CATEGORY_ICON = {
      Maintenance: "🧰",
      Operations: "⚙️",
      Cleaning: "🧹",
      Repairs: "🔧",
      Supplies: "📦",
      Admin: "🗂️",
      Safety: "🛡️",
      Inspection: "🔍",
    };

    // Master defaults (always available even if no tasks yet)
    const DEFAULT_CATEGORIES = [
      "Maintenance",
      "Safety",
      "Cleaning",
      "Operations",
      "Repairs",
      "Supplies",
      "Admin",
      "Inspection",
    ];
    
    // Merge helper that NEVER shrinks categories
    function setCategories(nextCats) {
      CATEGORIES = uniq([...(CATEGORIES || []), ...(nextCats || []), ...DEFAULT_CATEGORIES]);
    }

    function categoryIconHTML(category) {
      const key = String(category || "").trim();
      const ico = CATEGORY_ICON[key] || "🏷️";
      return `<span aria-hidden="true" style="font-size:14px;line-height:1;">${esc(ico)}</span>`;
    }

    // ----------------------------
    // API
    // ----------------------------
    async function apiList({ q, status } = {}) {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (status) params.set("status", status);

      const res = await fetch(`/admin/api/tasks?${params.toString()}`, { credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || "Failed to load tasks");
      return data;
    }

    async function apiCreate(payload) {
      const res = await fetch(`/admin/api/tasks`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.error || "Create failed");
      return data.item;
    }

    async function apiUpdate(taskId, payload) {
      const res = await fetch(`/admin/api/tasks/${taskId}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.error || "Update failed");
      return data.item;
    }

    async function apiBatch(action, payload) {
      const res = await fetch(`/admin/api/tasks/batch`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...payload }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.error || "Batch failed");
      return data;
    }

    async function apiTeamMembers() {
      const res = await fetch(`/admin/api/team-members`, { credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.error || "Failed to load team members");
      return data.items || [];
    }

    async function ensureTeamLoaded() {
      if (TEAM.length) return;
      TEAM = await apiTeamMembers();
      TEAM_BY_ID.clear();
      for (const u of TEAM) TEAM_BY_ID.set(String(u.id), u);
      populateAssigneeSelect($id("taskAssignee"));
    }

    function populateAssigneeSelect(selectEl) {
      if (!selectEl) return;
      const current = selectEl.value;
      selectEl.innerHTML = `<option value="">Unassigned</option>`;
      for (const u of TEAM) {
        const opt = document.createElement("option");
        opt.value = String(u.id);
        opt.textContent = getDisplayName(u);
        selectEl.appendChild(opt);
      }
      if (current) selectEl.value = current;
    }

    // ----------------------------
    // Categories
    // ----------------------------
    function uniq(arr) {
      return Array.from(new Set((arr || []).map((x) => String(x || "").trim()).filter(Boolean)));
    }

    function extractCategoriesFromTasksData(data) {
      if (Array.isArray(data?.categories) && data.categories.length) {
        const raw = data.categories
          .map((c) => (typeof c === "string" ? c : c?.name))
          .filter(Boolean);
        return uniq(raw);
      }
      return uniq((data?.items || []).map((t) => t.category).filter(Boolean));
    }

   function populateCategorySelect(selectEl) {
  if (!selectEl) return;

  const current = selectEl.value;
  const cats = uniq([...(CATEGORIES || []), ...DEFAULT_CATEGORIES]);

  selectEl.innerHTML = "";
  for (const c of cats) {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    selectEl.appendChild(opt);
  }

  // restore selection if still valid
  if (current && cats.includes(current)) selectEl.value = current;
  else selectEl.value = cats[0] || "Maintenance";
}


  async function ensureCategoriesLoaded() {
  if (CATEGORIES.length) return;

  const data = await apiList({}); // unfiltered list
  const next = extractCategoriesFromTasksData(data);
  setCategories(next);
  populateCategorySelect($id("taskCategory"));
}


    // ----------------------------
    // Batch bar
    // ----------------------------
    function setBatchBar() {
      const bar = $id("tasksBatchBar");
      if (!bar) return;
      const cnt = selected.size;
      bar.classList.toggle("hidden", cnt === 0);
      const label = $id("tasksSelectedCount");
      if (label) label.textContent = `${cnt} selected`;
    }

    // ----------------------------
    // Status popover (batch status)
    // ----------------------------
    let openStatusMenuEl = null;
    let statusMenuCleanup = null;

    function closeStatusMenu() {
      if (openStatusMenuEl) openStatusMenuEl.remove();
      openStatusMenuEl = null;
      if (statusMenuCleanup) statusMenuCleanup();
      statusMenuCleanup = null;
    }

    function openStatusMenu(anchorEl, { current, onPick }) {
      closeStatusMenu();
      if (!anchorEl) return;

      const rect = anchorEl.getBoundingClientRect();
      const menu = document.createElement("div");
      menu.className = "tasks-status-menu";
      menu.setAttribute("role", "menu");

      for (const opt of STATUS_MENU) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "tasks-status-item";
        item.setAttribute("role", "menuitem");
        item.innerHTML = `
          <span class="tasks-status-dot ${opt.dotClass}">${esc(opt.glyph || "")}</span>
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;">
            <span style="font-weight:600;color:#0f172a;">${esc(opt.label)}</span>
            ${opt.key === current ? `<span style="font-size:12px;color:#64748b;">Selected</span>` : ``}
          </div>
        `;
        item.addEventListener("click", async () => {
          try {
            await onPick(opt.key);
          } finally {
            closeStatusMenu();
          }
        });
        menu.appendChild(item);
      }

      document.body.appendChild(menu);
      openStatusMenuEl = menu;

      const menuRect = menu.getBoundingClientRect();
      let top = rect.bottom + 8;
      let left = rect.right - menuRect.width;

      const pad = 10;
      if (left < pad) left = pad;
      if (left + menuRect.width > window.innerWidth - pad) left = window.innerWidth - pad - menuRect.width;
      if (top + menuRect.height > window.innerHeight - pad) top = rect.top - 8 - menuRect.height;

      menu.style.top = `${top}px`;
      menu.style.left = `${left}px`;

      const onDoc = (e) => {
        if (!openStatusMenuEl) return;
        if (openStatusMenuEl.contains(e.target)) return;
        if (anchorEl.contains(e.target)) return;
        closeStatusMenu();
      };
      const onEsc = (e) => {
        if (e.key === "Escape") closeStatusMenu();
      };

      document.addEventListener("mousedown", onDoc, true);
      document.addEventListener("keydown", onEsc, true);
      statusMenuCleanup = () => {
        document.removeEventListener("mousedown", onDoc, true);
        document.removeEventListener("keydown", onEsc, true);
      };
    }

    // ----------------------------
    // Render
    // ----------------------------
    function groupByStatus(items) {
      const g = {};
      for (const it of items || []) {
        const s = it.status || "todo";
        (g[s] ||= []).push(it);
      }
      return g;
    }

    function resolveAssignee(task) {
      const obj = task.assigned_user || task.assignee || null;
      if (obj && obj.id != null) return obj;

      const id = task.assigned_user_id ?? task.assignee_id ?? null;
      if (!id) return null;
      return TEAM_BY_ID.get(String(id)) || { id, full_name: `User ${id}` };
    }

    function cellInlineHTML(iconHtml, textHtml) {
      return `
        <div class="inline-flex items-center gap-2 text-xs text-slate-700">
          ${iconHtml || ""}
          <span class="whitespace-nowrap">${textHtml || ""}</span>
        </div>
      `;
    }

    function renderList(host, items, counts) {
      host.innerHTML = "";
      const grouped = groupByStatus(items);

      for (const status of STATUS_ORDER) {
        const rows = grouped[status] || [];

        if (status !== "completed" && status !== "canceled" && rows.length === 0) continue;

        const sec = document.createElement("div");
        sec.className = "tasks-group";

        const head = document.createElement("div");
        head.className = "tasks-group-head flex items-center justify-between";

        const left = document.createElement("div");
        left.className = "flex items-center gap-3";

        const pill = document.createElement("span");
        pill.className = `tasks-group-pill ${STATUS_PILL_CLASS[status] || "bg-slate-50 text-slate-700 border-slate-200"}`;
        pill.textContent = STATUS_LABEL[status] || status;

        const cnt = counts && counts[status] ? counts[status] : rows.length;
        const count = document.createElement("span");
        count.className = "text-xs text-slate-500";
        count.textContent = `${cnt} task${cnt === 1 ? "" : "s"}`;

        left.appendChild(pill);
        left.appendChild(count);

        head.appendChild(left);
        sec.appendChild(head);

        const cols = document.createElement("div");
        cols.className = "mt-3 text-xs text-slate-400 px-4";
        cols.innerHTML = `
          <div class="grid grid-cols-12 gap-3 items-center">
            <div class="col-span-1"></div>
            <div class="col-span-1">Status</div>
            <div class="col-span-4">Name</div>
            <div class="col-span-2">Due date</div>
            <div class="col-span-2">Category</div>
            <div class="col-span-1">Assignee</div>
            <div class="col-span-1 text-right">Actions</div>
          </div>
        `;
        sec.appendChild(cols);

        if (!rows.length) {
          const empty = document.createElement("div");
          empty.className = "mt-3 text-sm text-slate-500 px-4";
          empty.textContent = "No tasks";
          sec.appendChild(empty);
          host.appendChild(sec);
          continue;
        }

        for (const t of rows) {
          const tid = String(t.id); // ✅ normalize ID

          const row = document.createElement("div");
          row.className = "tasks-row hover:bg-slate-50 transition-colors";

          const grid = document.createElement("div");
          grid.className = "tasks-row-grid grid grid-cols-12 gap-3 items-center";

          // checkbox
          const cbWrap = document.createElement("div");
          cbWrap.className = "col-span-12 sm:col-span-1 flex items-center";
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.className = "h-5 w-5 rounded border-slate-300";
          cb.checked = selected.has(tid);
          cb.addEventListener("change", () => {
            if (cb.checked) selected.add(tid);
            else selected.delete(tid);
            setBatchBar();
          });
          cbWrap.appendChild(cb);

          // status icon
          const st = document.createElement("div");
          st.className = "col-span-12 sm:col-span-1 flex items-center";
          st.innerHTML = `
            <div class="inline-flex items-center" title="${esc(STATUS_LABEL[t.status] || STATUS_LABEL.todo)}">
              ${statusIconHTML(t.status || "todo")}
            </div>
          `;

          // name
          const name = document.createElement("div");
          name.className = "col-span-12 sm:col-span-4";
          name.innerHTML = `
            <div class="font-semibold text-slate-900">${esc(t.title)}</div>
            <div class="text-sm text-slate-500 mt-0.5">${esc(t.property_name || "")}</div>
          `;

          // due
          const due = document.createElement("div");
          due.className = "col-span-12 sm:col-span-2 flex items-center";
          const pretty = isoToPrettyDate(t.due_at);
          due.innerHTML = cellInlineHTML(`<span aria-hidden="true">📅</span>`, esc(pretty || "No due date"));

          // category
          const cat = document.createElement("div");
          cat.className = "col-span-12 sm:col-span-2 flex items-center";
          const catName = t.category || "Maintenance";
          cat.innerHTML = cellInlineHTML(categoryIconHTML(catName), esc(catName));

          // assignee
          const asg = document.createElement("div");
          asg.className = "col-span-12 sm:col-span-1 flex items-center";
          const assigneeObj = resolveAssignee(t);

          if (assigneeObj) {
            const nm = getDisplayName(assigneeObj);
            asg.innerHTML = `
              <div class="inline-flex items-center gap-2 text-xs text-slate-900">
                <span class="inline-flex items-center justify-center h-7 w-7 rounded-full bg-slate-100 text-slate-700 text-[11px] font-bold">
                  ${esc(initials(nm))}
                </span>
                <span class="hidden lg:inline">${esc(nm)}</span>
              </div>
            `;
          } else {
            asg.innerHTML = `
              <div class="inline-flex items-center gap-2 text-xs text-slate-500">
                <span class="inline-flex items-center justify-center h-7 w-7 rounded-full bg-slate-100 text-slate-700 text-[14px] font-bold">–</span>
                <span class="hidden lg:inline">Unassigned</span>
              </div>
            `;
          }

          // actions
          const actions = document.createElement("div");
          actions.className = "col-span-12 sm:col-span-1 flex justify-end items-center";

          const editBtn = document.createElement("button");
          editBtn.type = "button";
          editBtn.className =
            "h-9 w-9 rounded-xl hover:bg-slate-100 inline-flex items-center justify-center text-slate-700";
          editBtn.title = "Edit task";
          editBtn.textContent = "✎";
          editBtn.addEventListener("click", async () => {
            try {
              await ensureTeamLoaded();
              await openTaskModal({ mode: "edit", task: t });
            } catch (e) {
              alert(e.message || e);
            }
          });

          actions.appendChild(editBtn);

          // assemble
          grid.appendChild(cbWrap);
          grid.appendChild(st);
          grid.appendChild(name);
          grid.appendChild(due);
          grid.appendChild(cat);
          grid.appendChild(asg);
          grid.appendChild(actions);

          row.appendChild(grid);
          sec.appendChild(row);
        }

        host.appendChild(sec);
      }
    }

    // ----------------------------
    // Modal (Create/Edit)
    // ----------------------------
    async function openTaskModal({ mode, task } = {}) {
      const modal = $id("taskModal");
      if (!modal) return;

      await ensureCategoriesLoaded();
      populateCategorySelect($id("taskCategory"));

      const isEdit = mode === "edit";
      const titleEl = $id("taskModalTitle");
      const saveBtn = $id("taskModalSave");

      if (titleEl) titleEl.textContent = isEdit ? "Edit Task" : "New Task";
      if (saveBtn) saveBtn.textContent = isEdit ? "Save" : "Create";

      $id("taskId").value = isEdit ? String(task.id) : "";
      $id("taskTitle").value = isEdit ? (task.title || "") : "";
      $id("taskDueAt").value = isEdit ? toDateInputValue(task.due_at) : "";
      $id("taskStatus").value = isEdit ? (task.status || "todo") : "todo";
      $id("taskDescription").value = isEdit ? (task.description || "") : "";

      const catEl = $id("taskCategory");
      const desiredCat = isEdit ? (task?.category || "") : "";
      if (catEl) {
        if (desiredCat && Array.from(catEl.options).some((o) => o.value === desiredCat)) {
          catEl.value = desiredCat;
        } else {
          catEl.value = catEl.options[0]?.value || "Maintenance";
        }
      }

      const assigneeId = task?.assigned_user_id ?? task?.assignee_id ?? "";
      if ($id("taskAssignee")) $id("taskAssignee").value = assigneeId ? String(assigneeId) : "";

      modal.classList.remove("hidden");
    }

    function closeTaskModal() {
      const modal = $id("taskModal");
      if (!modal) return;
      modal.classList.add("hidden");
    }

    // ----------------------------
    // Wiring
    // ----------------------------
    function wireTabs() {
      const host = $id("view-tasks");
      if (!host) return;

      const tabs = qsa(host, "[data-tasks-tab]");
      tabs.forEach((b) => {
        b.addEventListener("click", () => {
          tabs.forEach((x) => x.classList.remove("is-active"));
          b.classList.add("is-active");
          activeTab = b.dataset.tasksTab || "all";
          window.Notifications?.showInTasks?.(activeTab === "notifications");
          refresh();
        });
      });
    }

    function wireSearch() {
      const s = $id("tasksSearch");
      const st = $id("tasksFilterStatus");
      if (s) s.addEventListener("input", () => refresh());
      if (st) st.addEventListener("change", () => refresh());
    }

    function wireModal() {
      const openBtn = $id("btnCreateTask");
      const modal = $id("taskModal");
      const close = $id("taskModalClose");
      const cancel = $id("taskModalCancel");
      const save = $id("taskModalSave");

      if (!modal || !close || !cancel || !save) return;

      openBtn?.addEventListener("click", async () => {
        try {
          await ensureTeamLoaded();
          await openTaskModal({ mode: "create" });
        } catch (e) {
          alert(e.message || e);
        }
      });

      close.addEventListener("click", closeTaskModal);
      cancel.addEventListener("click", closeTaskModal);

      modal.addEventListener("click", (e) => {
        if (e.target === modal || e.target.classList.contains("bg-black/40")) closeTaskModal();
      });

      save.addEventListener("click", async () => {
        try {
          const id = ($id("taskId").value || "").trim();
          const isEdit = !!id;

          const title = ($id("taskTitle").value || "").trim();
          const category = $id("taskCategory").value || "Maintenance";
          const due_at = ($id("taskDueAt").value || "").trim() || null; // keep as YYYY-MM-DD unless backend wants ISO
          const status = $id("taskStatus").value || "todo";
          const description = ($id("taskDescription").value || "").trim() || null;

          const assigneeRaw = ($id("taskAssignee").value || "").trim();
          const assigned_user_id = assigneeRaw ? Number(assigneeRaw) : null;

          if (!title) return alert("Title required");

          if (isEdit) {
            await apiUpdate(id, { title, category, due_at, status, description, assigned_user_id });
          } else {
            await apiCreate({ title, category, due_at, status, description, assigned_user_id });
          }

          closeTaskModal();
          await refresh();
        } catch (e) {
          alert(e.message || e);
        }
      });
    }

    function wireBatch() {
      const btnDone = $id("btnBatchComplete");
      const btnStatus = $id("btnBatchStatus");
      const btnDelete = $id("btnBatchDelete");

      btnDone?.addEventListener("click", async () => {
        try {
          const ids = Array.from(selected).map(String); // ✅ normalize
          if (!ids.length) return;
          await apiBatch("status", { task_ids: ids, status: "completed" });
          selected.clear();
          setBatchBar();
          await refresh();
        } catch (e) {
          alert(e.message || e);
        }
      });

      btnStatus?.addEventListener("click", () => {
        const ids = Array.from(selected).map(String);
        if (!ids.length) return;
        openStatusMenu(btnStatus, {
          current: null,
          onPick: async (next) => {
            await apiBatch("status", { task_ids: ids, status: next });
            selected.clear();
            setBatchBar();
            await refresh();
          },
        });
      });

      btnDelete?.addEventListener("click", async () => {
        const ids = Array.from(selected).map(String);
        if (!ids.length) return;
        if (!confirm("Delete selected tasks?")) return;
        try {
          await apiBatch("delete", { task_ids: ids });
          selected.clear();
          setBatchBar();
          await refresh();
        } catch (e) {
          alert(e.message || e);
        }
      });
    }

    // ----------------------------
    // Refresh
    // ----------------------------
    async function refresh() {
      const host = $id("tasksListHost");
      if (!host) return;

      // If user is on Tasks → Notifications, show notifications panel and hide task list
      if (activeTab === "notifications") {
        host.classList.add("hidden");
        window.Notifications?.showInTasks?.(true);
        return;
      } else {
        host.classList.remove("hidden");
        window.Notifications?.showInTasks?.(false);
      }


      // ✅ prevent “sticky selections” after refresh/filtering
      selected.clear();
      setBatchBar();

      // Handle tabs that are not the main list
      if (activeTab === "notifications") {
        // handled above (hide list + show notifications panel)
        return;
      }
      
      if (activeTab === "recurring") {
        host.innerHTML = `<div class="text-sm text-slate-500 py-6">Recurring tasks view coming next.</div>`;
        return;
      }
      
      if (activeTab === "auto") {
        host.innerHTML = `<div class="text-sm text-slate-500 py-6">Auto-assignment view coming next.</div>`;
        return;
      }
      
      // default
      if (activeTab !== "all") {
        host.innerHTML = `<div class="text-sm text-slate-500 py-6">Coming next: ${esc(activeTab)}.</div>`;
        return;
      }


      const q = ($id("tasksSearch")?.value || "").trim();
      const st = ($id("tasksFilterStatus")?.value || "").trim();

      host.innerHTML = `<div class="text-sm text-slate-500 py-6">Loading…</div>`;

      try {
        await ensureTeamLoaded();
        const data = await apiList({ q, status: st });

        // If backend provides a categories list, merge it.
// If not, DO NOT shrink categories from filtered results — only merge.
const nextCats = extractCategoriesFromTasksData(data);
setCategories(nextCats);
populateCategorySelect($id("taskCategory"));


        renderList(host, data.items || [], data.counts || {});
      } catch (e) {
        host.innerHTML = `<div class="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-2xl p-4">${esc(
          e.message || e
        )}</div>`;
      }
    }

    // ----------------------------
    // Init
    // ----------------------------
    function init() {
      const view = document.getElementById("view-tasks");
      if (!view) return;
      if (view.__tasksInit) return; // wires once
      view.__tasksInit = true;

      wireTabs();
      wireSearch();
      wireModal();
      wireBatch();
      refresh();
    }

    return { init, refresh };
  })();


// ------------------------------
// DOM ready (single, clean)
// ------------------------------
document.addEventListener("DOMContentLoaded", async () => {
  initRouting();
  initSidebar();
  initPortfolioChart();
  initAnalyticsSection();
  initRevenueReports();
  initSyncAllProperties();
  

  initChatBatchActions();
  initChatDetailDelete();
  initChatFilters();
  initChatLoadMore();
  initRelativeTimes();
  window.setInterval(() => {
    initChatMessageTimes(document);
  }, 60 * 1000);

  initSettingsUI();
  initAllReorderTables();

  document.getElementById("searchInput")?.addEventListener("input", filterProperties);
  document.getElementById("statusFilter")?.addEventListener("change", filterProperties);

  document.getElementById("guidesPropertyFilter")?.addEventListener("change", () => Guides.refresh());
  document.getElementById("upgradesPropertyFilter")?.addEventListener("change", () => {
    Upgrades.closeEditor();
    Upgrades.refresh();
  });

  window.Messages?.refreshUnreadBadge?.();
  window.rerenderAllMoodBadges?.();
  window.applyMoodConfidenceHints?.(document);

  filterProperties();
  updateOverviewUI();

  const params = new URLSearchParams(window.location.search);
  const sid = params.get("session_id");
  if (sid) {
    setInlineDetailOpen(true);
    await loadChatDetail(sid);
  } else {
    setInlineDetailOpen(false);
  }

  if ((params.get("view") || "overview") === "tasks") {
    window.Tasks?.init?.();
    window.Tasks?.refresh?.();
  }

  if ((params.get("view") || "overview") === "analytics") {
  initAnalyticsSection();
  loadChatAnalytics();
}
});

// ------------------------------
// View switching (matches HTML: data-view)
// Uses "hidden" class (matches template)
// Keeps Tasks init + ALWAYS refresh
// ------------------------------


window.DashboardOverview = window.DashboardOverview || {};
window.DashboardOverview.jumpTo = function (view) {
  goToView(view);
};
