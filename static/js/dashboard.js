(function () {
  "use strict";

  // -----------------------------------
  // Bootstrap / config
  // -----------------------------------
  function getBoot() {
    const el = document.getElementById("dashboard-bootstrap");
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (err) {
      console.error("Invalid dashboard bootstrap JSON", err);
      return {};
    }
  }

  const BOOT = getBoot();

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
  const VIEW_TITLES = {
    overview: ["Overview", "Your portfolio at a glance"],
    chats: ["Chats", "Guest conversations and issues"],
    analytics: ["Analytics", "Performance across your portfolio"],
    properties: ["Properties", "Manage listing status and configuration"],
    guides: ["Guides", "Create and manage guest guides per property."],
    upgrades: ["Upgrades", "Manage paid add-ons and offerings"],
    pmcs: ["PMCs", "Property management companies"],
    files: ["Configs & Manuals", "Central file management"],
    payouts: ["Payouts", "Revenue and transfers"],
    admin_payouts: ["Revenue", "HostScout platform revenue"],
    settings: ["Settings", "Workspace and account controls"],
  };

  function setActiveView(viewName) {
    const pageTitle = $("page-title");
    const pageSubtitle = $("page-subtitle");

    qsa("[data-view]").forEach((item) => {
      const active = item.getAttribute("data-view") === viewName;
      item.classList.toggle("active", active);
      item.setAttribute("aria-current", active ? "page" : "false");
    });

    qsa(".view").forEach((panel) => {
      panel.classList.add("hidden");
      panel.classList.remove("fade-in");
    });

    const activePanel = $(`view-${viewName}`);
    if (activePanel) {
      activePanel.classList.remove("hidden");
      activePanel.classList.add("fade-in");
    }

    if (pageTitle && VIEW_TITLES[viewName]) {
      pageTitle.textContent = VIEW_TITLES[viewName][0];
    }

    if (pageSubtitle && VIEW_TITLES[viewName]) {
      pageSubtitle.textContent = VIEW_TITLES[viewName][1];
    }

    const url = new URL(window.location.href);
    url.searchParams.set("view", viewName);
    window.history.replaceState({}, "", url.toString());
  }

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
    setActiveView("guides");

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
    setActiveView("guides");

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

  // -----------------------------------
  // View navigation
  // -----------------------------------
  function initViewNavigation() {
    qsa("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.getAttribute("data-view");
        if (!view) return;
        setActiveView(view);
      });
    });
  }

  // -----------------------------------
  // Sidebar collapse
  // -----------------------------------
  function initSidebar() {
    const sidebar = $("sidebar");
    const toggleBtn = $("sidebar-toggle");
    if (!sidebar || !toggleBtn) return;

    function setCollapsed(isCollapsed) {
      sidebar.classList.toggle("w-72", !isCollapsed);
      sidebar.classList.toggle("w-20", isCollapsed);

      qsa(".sidebar-label").forEach((el) => {
        el.classList.toggle("hidden", isCollapsed);
      });

      qsa(".nav-item").forEach((item) => {
        item.classList.toggle("justify-center", isCollapsed);
        item.classList.toggle("px-4", !isCollapsed);
        item.classList.toggle("px-0", isCollapsed);
      });

      qsa(".nav-icon-wrap").forEach((icon) => {
        icon.classList.toggle("mx-auto", isCollapsed);
      });

      const svg = toggleBtn.querySelector("svg");
      if (svg) {
        svg.style.transform = isCollapsed ? "rotate(180deg)" : "rotate(0deg)";
        svg.style.transition = "transform 200ms ease";
      }

      window.localStorage.setItem(
        "dashboard_sidebar_collapsed",
        isCollapsed ? "1" : "0"
      );
    }

    const saved = window.localStorage.getItem("dashboard_sidebar_collapsed") === "1";
    setCollapsed(saved);

    toggleBtn.addEventListener("click", () => {
      const isCollapsed = sidebar.classList.contains("w-20");
      setCollapsed(!isCollapsed);
    });
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

  // -----------------------------------
  // DOM ready
  // -----------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    initViewNavigation();
    initSidebar();
    initChatBatchActions();
    initChatDetailDelete();
    initRevenueReports();
    initPortfolioChart();

    if (BOOT && BOOT.user_role) {
      window.CONTENT_LOCKED = !!BOOT.is_locked;
    }
  });
})();
