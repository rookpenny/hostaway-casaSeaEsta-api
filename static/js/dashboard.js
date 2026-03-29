<script>
  function openInsights() {
    document.getElementById("insightsPanel")?.classList.remove("hidden");
  }

  function closeInsights() {
    document.getElementById("insightsPanel")?.classList.add("hidden");
  }

  function openGuideSuggestion(target) {
    closeInsights();

    const guidesViewBtn = document.querySelector('[data-view="guides"]');
    if (guidesViewBtn) guidesViewBtn.click();

    setTimeout(() => {
      if (typeof Guides !== "undefined" && typeof Guides.openNew === "function") {
        Guides.openNew();
      } else {
        alert("Guide editor coming soon.");
      }
    }, 150);
  }

  function openDraftSuggestion(target) {
    const drafts = {
      checkin: {
        title: "Suggested check-in instructions",
        body:
`Check-in is from 4:00 PM.

1. Park in the designated area.
2. Go to the main entrance.
3. Enter the door code sent before arrival.
4. Inside, you'll find your welcome guide and WiFi details.

If you have trouble entering, message us and we'll help right away.`
      },
      wifi: {
        title: "Suggested WiFi section",
        body:
`WiFi Network: [ADD NETWORK NAME]
WiFi Password: [ADD PASSWORD]

If you have trouble connecting, restart WiFi on your device and try again.`
      },
      parking: {
        title: "Suggested parking instructions",
        body:
`Parking is available in the designated area only.

- Please park in [ADD LOCATION]
- Do not block neighboring driveways
- If arriving late, use the marked guest space closest to the entrance`
      },
      checkout: {
        title: "Suggested checkout policy",
        body:
`Checkout is at 10:00 AM unless otherwise approved.

Before leaving:
- Lock all doors
- Place used towels in the bathroom
- Start the dishwasher if needed
- Message us once you've checked out`
      },
      general: {
        title: "Suggested guide update",
        body:
`Review the areas guests ask about most often and make key information easier to find in your guide.`
      }
    };

    const draft = drafts[target];
    if (!draft) {
      alert("Draft generator coming soon.");
      return;
    }

    alert(draft.title + "\\n\\n" + draft.body);
  }
</script>

<script>
  window.PMC_STRIPE_CONNECTED = {{ stripe_connected | tojson }};
</script>

<script id="dashboard-bootstrap" type="application/json">
{
  "is_locked": {{ (user_role == 'pmc' and needs_payment) | tojson }},
  "live_props": {{ live_props | tojson }},
  "offline_props": {{ offline_props | tojson }},
  "user_role": {{ user_role | tojson }},
  "api": {
    "chat_detail_partial": "/admin/chats/partial/detail?session_id={session_id}",
    "chat_summarize": "/admin/chats/{session_id}/summarize",
    "chat_resolve": "/admin/chats/{session_id}/resolve",
    "chat_unresolve": "/admin/chats/{session_id}/unresolve",
    "chat_escalate": "/admin/chats/{session_id}/escalate",
    "chat_assign": "/admin/chats/{session_id}/assign",
    "chat_note": "/admin/chats/{session_id}/note",
    "messages_list": "/admin/messages?status={status}&type={type}&q={q}&limit={limit}&offset={offset}",
    "messages_unread_count": "/admin/messages/unread-count",
    "messages_mark_read": "/admin/messages/{message_id}/read",
    "messages_mark_all_read": "/admin/messages/mark-all-read",
    "notifications_list": "/admin/notifications?limit={limit}",
    "notifications_mark_read": "/admin/notifications/{notification_id}/read"

  }
}
</script>

                
<script>
  (function () {
    const selectAll = document.getElementById("chat-select-all");
    const batchBar = document.getElementById("chatBatchBar");
    const selectedCount = document.getElementById("chatSelectedCount");
    const deleteBtn = document.getElementById("btnChatDelete");

    function getChatCheckboxes() {
      return Array.from(document.querySelectorAll(".chat-select"));
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
      if (e.target.matches(".chat-select")) {
        updateBatchBar();
      }
    });


    deleteBtn?.addEventListener("click", async () => {
      const ids = getSelectedIds();
      if (!ids.length) return;

      const ok = confirm(
        ids.length === 1
          ? "Delete this chat?"
          : `Delete ${ids.length} chats?`
      );
      if (!ok) return;

      deleteBtn.disabled = true;

      try {
        const res = await fetch("/admin/chats/delete", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          credentials: "include",
          body: JSON.stringify({ session_ids: ids }),
        });

        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          alert(data.detail || "Failed to delete chats.");
          return;
        }

        window.location.reload();
      } catch (err) {
        alert("Something went wrong while deleting chats.");
      } finally {
        deleteBtn.disabled = false;
      }
    });

    updateBatchBar();
  })();
</script>

<script>
  function centsToUsd(cents) {
    const n = Number(cents || 0) / 100;
    return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
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

  // ---------------------------
  // SUPER: HostScout revenue
  // ---------------------------
  async function runHostScoutRevenue() {
    const start = document.getElementById("hs-rev-start")?.value || "";
    const end = document.getElementById("hs-rev-end")?.value || "";

    const qs = new URLSearchParams();
    if (start) qs.set("start", start);
    if (end) qs.set("end", end);

    const res = await fetch(`/admin/reports/hostscout-revenue?${qs.toString()}`, {
      credentials: "include",
      headers: { "Accept": "application/json" }
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) { alert(data?.detail || "Revenue report failed"); return; }

    document.getElementById("hs-gross").textContent = centsToUsd(data.summary.gross_cents);
    document.getElementById("hs-fee").textContent = centsToUsd(data.summary.hostscout_fee_cents);
    document.getElementById("hs-net").textContent = centsToUsd(data.summary.net_to_pmc_cents);
    document.getElementById("hs-count").textContent = `${data.summary.paid_count} / ${data.summary.refunded_count}`;

    const tbody = document.getElementById("hs-rows");
    tbody.innerHTML = "";

    for (const r of data.by_pmc || []) {
      const name = (r.pmc && (r.pmc.name || r.pmc.email)) ? (r.pmc.name || r.pmc.email) : `PMC ${r.pmc_id}`;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="py-2 pr-3 font-semibold">${name}</td>
        <td class="py-2 pr-3">${centsToUsd(r.gross_cents)}</td>
        <td class="py-2 pr-3">${centsToUsd(r.hostscout_fee_cents)}</td>
        <td class="py-2 pr-3">${centsToUsd(r.net_to_pmc_cents)}</td>
        <td class="py-2 pr-3">${r.paid_count}</td>
        <td class="py-2 pr-3">${r.refunded_count}</td>
      `;
      tbody.appendChild(tr);
    }

    window.__hs_rev_latest = data;
  }

  document.getElementById("hs-rev-run")?.addEventListener("click", runHostScoutRevenue);
  document.getElementById("hs-rev-csv")?.addEventListener("click", () => {
    const data = window.__hs_rev_latest;
    if (!data?.by_pmc) return alert("Run the report first.");

    const header = ["pmc_id","pmc_name","gross_cents","hostscout_fee_cents","net_to_pmc_cents","paid_count","refunded_count"];
    const lines = [header.join(",")];

    for (const r of data.by_pmc) {
      const pmcName = (r.pmc && (r.pmc.name || r.pmc.email)) ? String(r.pmc.name || r.pmc.email).replaceAll('"','""') : "";
      lines.push([
        r.pmc_id ?? "",
        `"${pmcName}"`,
        r.gross_cents ?? 0,
        r.hostscout_fee_cents ?? 0,
        r.net_to_pmc_cents ?? 0,
        r.paid_count ?? 0,
        r.refunded_count ?? 0
      ].join(","));
    }

    downloadCSV(`hostscout_revenue_${data.range.start}_to_${data.range.end}.csv`, lines.join("\n"));
  });

  // ---------------------------
  // PMC: payouts (PMC only)
  // NOTE: endpoint name below — see note at bottom
  // ---------------------------
  async function runPmcPayouts() {
    const start = document.getElementById("pmc-pay-start")?.value || "";
    const end = document.getElementById("pmc-pay-end")?.value || "";

    const qs = new URLSearchParams();
    if (start) qs.set("start", start);
    if (end) qs.set("end", end);

    const res = await fetch(`/admin/reports/pmc-payouts?${qs.toString()}`, {
      credentials: "include",
      headers: { "Accept": "application/json" }
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) { alert(data?.detail || "PMC payouts report failed"); return; }

    document.getElementById("pmc-gross").textContent = centsToUsd(data.summary.gross_cents);
    document.getElementById("pmc-fee").textContent = centsToUsd(data.summary.hostscout_fee_cents);
    document.getElementById("pmc-net").textContent = centsToUsd(data.summary.net_to_pmc_cents);
    document.getElementById("pmc-count").textContent = `${data.summary.paid_count} / ${data.summary.refunded_count}`;

    const tbody = document.getElementById("pmc-pay-rows");
    tbody.innerHTML = "";

    for (const r of data.rows || []) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="py-2 pr-3">${r.paid_at || "—"}</td>
        <td class="py-2 pr-3">${r.property_name || ("Property " + (r.property_id ?? ""))}</td>
        <td class="py-2 pr-3">${r.upgrade_title || ("Upgrade " + (r.upgrade_id ?? ""))}</td>
        <td class="py-2 pr-3">${centsToUsd(r.amount_cents)}</td>
        <td class="py-2 pr-3">${centsToUsd(r.platform_fee_cents)}</td>
        <td class="py-2 pr-3">${centsToUsd((r.amount_cents || 0) - (r.platform_fee_cents || 0))}</td>
        <td class="py-2 pr-3">${r.status || "—"}</td>
      `;
      tbody.appendChild(tr);
    }

    window.__pmc_pay_latest = data;
  }

  document.getElementById("pmc-pay-run")?.addEventListener("click", runPmcPayouts);
  document.getElementById("pmc-pay-csv")?.addEventListener("click", () => {
    const data = window.__pmc_pay_latest;
    if (!data?.rows) return alert("Run the report first.");

    const header = ["paid_at","property_id","property_name","upgrade_id","upgrade_title","amount_cents","platform_fee_cents","net_cents","status"];
    const lines = [header.join(",")];

    for (const r of data.rows) {
      const propName = String(r.property_name || "").replaceAll('"','""');
      const upName = String(r.upgrade_title || "").replaceAll('"','""');
      lines.push([
        r.paid_at || "",
        r.property_id ?? "",
        `"${propName}"`,
        r.upgrade_id ?? "",
        `"${upName}"`,
        r.amount_cents ?? 0,
        r.platform_fee_cents ?? 0,
        (r.amount_cents || 0) - (r.platform_fee_cents || 0),
        r.status || ""
      ].join(","));
    }

    downloadCSV(`pmc_payouts_${data.range.start}_to_${data.range.end}.csv`, lines.join("\n"));
  });

  // Optional: auto-run when user lands on those tabs (works with your nav JS that toggles views)
  // You can also call these in your existing "setView()" logic if you have one.


</script>


                <script>
  // ------------------------------
  // Tasks → Notifications panel (in-app)
  // ------------------------------
  window.Notifications = window.Notifications || (function () {
    let all = [];
    let filter = "all";
    let wired = false;

    const $id = (id) => document.getElementById(id);

    function fmtTime(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return "";
      return d.toLocaleString();
    }

    function setUnreadBadge() {
      const badge = $id("tasksNotifUnreadBadge");
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

      // If chat assigned, jump to chat
      if (t === "chat_assigned" && meta.chat_session_id) {
        window.location.href =
          `/admin/dashboard?view=chats&session_id=${encodeURIComponent(meta.chat_session_id)}`;
        return;
      }

      // If upgrade, jump to upgrades page (or whatever you prefer)
      if (t === "upgrade_purchased") {
        window.location.href = `/admin/dashboard?view=upgrades`;
        return;
      }

      // default fallback
      window.location.href = `/admin/dashboard?view=tasks`;
    }

    async function markRead(id) {
      const res = await fetch(`/admin/notifications/${encodeURIComponent(id)}/read`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      // even if it errors, we won't hard-fail UI
      return res.ok;
    }

    function render() {
      const list = $id("tasksNotifList");
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
                 data-notif-id="${n.id}">
              <div class="flex items-start justify-between gap-4">
                <div class="min-w-0">
                  <div class="font-semibold text-slate-900 flex items-center">
                    ${unreadDot}
                    <span class="truncate">${(n.title || "Notification")}</span>
                  </div>
                  ${n.body ? `<div class="text-sm text-slate-600 mt-1">${n.body}</div>` : ""}
                  <div class="text-xs text-slate-400 mt-2">${n.type || ""}</div>
                </div>
                <div class="text-xs text-slate-400 whitespace-nowrap">${fmtTime(n.created_at)}</div>
              </div>
            </div>
          `;
        })
        .join("");

      document.querySelectorAll(".sidebar-label").forEach((el) => {
  el.classList.toggle("hidden", isCollapsed);
});

      // click handlers
      document.querySelectorAll("[data-notif-id]").forEach((row) => {
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
      const list = $id("tasksNotifList");
      if (list) list.innerHTML = `<div class="text-slate-500 text-sm">Loading…</div>`;

      const res = await fetch(`/admin/notifications?limit=200`, { credentials: "include" });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        if (list) {
          list.innerHTML = `<div class="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-2xl p-4">
            Failed to load notifications.
          </div>`;
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
      all.forEach((n) => (n.is_read = true));
      setUnreadBadge();
      render();
    }

    function wireUIOnce() {
      if (wired) return;
      wired = true;

      $id("tasksNotifRefreshBtn")?.addEventListener("click", load);
      $id("tasksNotifMarkAllReadBtn")?.addEventListener("click", markAllRead);

      document.querySelectorAll(".tasksNotifFilterBtn").forEach((btn) => {
        btn.addEventListener("click", () => {
          filter = btn.getAttribute("data-filter") || "all";
          render();
        });
      });
    }

    // ✅ Tasks.refresh() calls this
    function showInTasks(show) {
      const panel = $id("tasksNotificationsPanel");
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
</script>

<script>
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest('[data-role="delete-chat-btn"]');
    if (!btn) return;

    const panel = document.querySelector("[data-chat-panel]");
    const sessionId = panel?.getAttribute("data-session-id");

    if (!sessionId) {
      alert("Could not find chat ID.");
      return;
    }

    const ok = confirm("Delete this chat?");
    if (!ok) return;

    btn.disabled = true;

    try {
      const res = await fetch("/admin/chats/delete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({ session_ids: [sessionId] }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        alert(data.detail || "Failed to delete chat.");
        return;
      }

      window.location.href = "/admin/dashboard?view=chats";
    } catch (err) {
      alert("Something went wrong while deleting the chat.");
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelectorAll("[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.getAttribute("data-view");

    document.querySelectorAll("[data-view]").forEach((item) => {
      item.classList.remove("active");
      item.setAttribute("aria-current", "false");
    });

    btn.classList.add("active");
    btn.setAttribute("aria-current", "page");

    document.querySelectorAll(".view").forEach((panel) => {
      panel.classList.add("hidden");
    });

    const activePanel = document.getElementById(`view-${view}`);
    if (activePanel) {
      activePanel.classList.remove("hidden");
      activePanel.classList.add("fade-in");
    }

    const pageTitle = document.getElementById("page-title");
    const pageSubtitle = document.getElementById("page-subtitle");

    const titles = {
      overview: ["Overview", "Your portfolio at a glance"],
      chats: ["Chats", "Guest conversations and issues"],
      analytics: ["Analytics", "Performance across your portfolio"],
      properties: ["Properties", "Manage listing status and configuration"],
      guides: ["Guides", "Guest-facing content and instructions"],
      upgrades: ["Upgrades", "Manage paid add-ons and offerings"],
      pmcs: ["PMCs", "Property management companies"],
      files: ["Configs & Manuals", "Central file management"],
      payouts: ["Payouts", "Revenue and transfers"],
      admin_payouts: ["Revenue", "HostScout platform revenue"],
      settings: ["Settings", "Workspace and account controls"]
    };

    if (pageTitle && titles[view]) pageTitle.textContent = titles[view][0];
    if (pageSubtitle && titles[view]) pageSubtitle.textContent = titles[view][1];
  });
});

  function setCollapsed(isCollapsed) {
  sidebar.classList.toggle("w-72", !isCollapsed);
  sidebar.classList.toggle("w-20", isCollapsed);

  labelEls().forEach((el) => el.classList.toggle("hidden", isCollapsed));

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("justify-center", isCollapsed);
    item.classList.toggle("px-4", !isCollapsed);
    item.classList.toggle("px-0", isCollapsed);
  });

  document.querySelectorAll(".nav-icon-wrap").forEach((icon) => {
    icon.classList.toggle("mx-auto", isCollapsed);
  });

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

  function updateChatsNavBadge(count) {
  const badge = document.getElementById("chats-nav-badge");
  if (!badge) return;

  const n = Number(count) || 0;
  badge.textContent = String(n);
  badge.classList.toggle("hidden", n <= 0);
}
</script>
