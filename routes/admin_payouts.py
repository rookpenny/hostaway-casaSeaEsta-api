# routes/admin_payouts.py
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from database import get_db
from models import UpgradePurchase, PMC

router = APIRouter()


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    # expects YYYY-MM-DD
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


@router.get("/admin/pmc-payouts", response_class=HTMLResponse)
def pmc_payouts_screen():
    # If you use Jinja templates, render here instead.
    # For now: return a minimal HTML page that hits the JSON endpoint.
    return HTMLResponse(
        """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <script src="https://cdn.tailwindcss.com"></script>
  <title>PMC Payout Report</title>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="max-w-6xl mx-auto p-6">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-semibold">PMC Payout Report</h1>
        <p class="text-slate-600 mt-1">Paid upgrades grouped by PMC, with Stripe transfer IDs for reconciliation.</p>
      </div>
      <a id="csvLink" class="text-sm font-semibold underline" href="#">Download CSV</a>
    </div>

    <div class="mt-6 grid grid-cols-1 md:grid-cols-4 gap-3 bg-white rounded-2xl border p-4">
      <div>
        <label class="text-xs font-semibold text-slate-600">Start date</label>
        <input id="start" type="date" class="mt-1 w-full rounded-xl border px-3 py-2"/>
      </div>
      <div>
        <label class="text-xs font-semibold text-slate-600">End date</label>
        <input id="end" type="date" class="mt-1 w-full rounded-xl border px-3 py-2"/>
      </div>
      <div>
        <label class="text-xs font-semibold text-slate-600">PMC ID (optional)</label>
        <input id="pmcId" type="number" placeholder="e.g. 12" class="mt-1 w-full rounded-xl border px-3 py-2"/>
      </div>
      <div class="flex items-end">
        <button id="run" class="w-full rounded-xl bg-slate-900 text-white font-semibold py-2.5">
          Run report
        </button>
      </div>
    </div>

    <div class="mt-6 bg-white rounded-2xl border overflow-hidden">
      <div class="p-4 border-b flex items-center justify-between">
        <div class="text-sm font-semibold">Summary</div>
        <div id="meta" class="text-xs text-slate-500"></div>
      </div>

      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-slate-50 text-slate-600">
            <tr>
              <th class="text-left p-3">PMC</th>
              <th class="text-left p-3">Email</th>
              <th class="text-right p-3">Gross</th>
              <th class="text-right p-3">Platform fees</th>
              <th class="text-right p-3">Net to PMC</th>
              <th class="text-right p-3">Paid count</th>
            </tr>
          </thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
    </div>

    <div class="mt-6 bg-white rounded-2xl border overflow-hidden">
      <div class="p-4 border-b">
        <div class="text-sm font-semibold">Transfers (detail)</div>
        <p class="text-xs text-slate-500 mt-1">Each paid purchase with Stripe transfer id (when available).</p>
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-slate-50 text-slate-600">
            <tr>
              <th class="text-left p-3">Paid at</th>
              <th class="text-left p-3">PMC</th>
              <th class="text-left p-3">Purchase ID</th>
              <th class="text-left p-3">Transfer ID</th>
              <th class="text-left p-3">Destination Acct</th>
              <th class="text-right p-3">Net</th>
              <th class="text-right p-3">Fee</th>
              <th class="text-right p-3">Gross</th>
            </tr>
          </thead>
          <tbody id="detail"></tbody>
        </table>
      </div>
    </div>
  </div>

<script>
function money(cents) {
  const n = (Number(cents || 0) / 100);
  return n.toLocaleString(undefined, { style:"currency", currency:"USD" });
}

function qs() {
  const start = document.getElementById("start").value;
  const end = document.getElementById("end").value;
  const pmcId = document.getElementById("pmcId").value;
  const p = new URLSearchParams();
  if (start) p.set("start", start);
  if (end) p.set("end", end);
  if (pmcId) p.set("pmc_id", pmcId);
  return p.toString();
}

async function run() {
  const q = qs();
  document.getElementById("csvLink").href = "/admin/pmc-payouts.csv" + (q ? ("?" + q) : "");
  const res = await fetch("/admin/pmc-payouts.data" + (q ? ("?" + q) : ""));
  const data = await res.json();

  document.getElementById("meta").textContent =
    `Total paid purchases: ${data.meta.total_paid} • Date window: ${data.meta.start || "—"} to ${data.meta.end || "—"}`;

  const rows = document.getElementById("rows");
  rows.innerHTML = "";
  (data.summary || []).forEach(r => {
    const tr = document.createElement("tr");
    tr.className = "border-t";
    tr.innerHTML = `
      <td class="p-3 font-semibold">${r.pmc_id}</td>
      <td class="p-3">${r.pmc_email || ""}</td>
      <td class="p-3 text-right">${money(r.gross_cents)}</td>
      <td class="p-3 text-right">${money(r.fee_cents)}</td>
      <td class="p-3 text-right font-semibold">${money(r.net_cents)}</td>
      <td class="p-3 text-right">${r.count}</td>
    `;
    rows.appendChild(tr);
  });

  const detail = document.getElementById("detail");
  detail.innerHTML = "";
  (data.transfers || []).forEach(x => {
    const tr = document.createElement("tr");
    tr.className = "border-t";
    tr.innerHTML = `
      <td class="p-3">${x.paid_at || ""}</td>
      <td class="p-3">${x.pmc_id}</td>
      <td class="p-3">${x.purchase_id}</td>
      <td class="p-3 font-mono text-xs">${x.stripe_transfer_id || ""}</td>
      <td class="p-3 font-mono text-xs">${x.stripe_destination_account_id || ""}</td>
      <td class="p-3 text-right">${money(x.net_amount_cents)}</td>
      <td class="p-3 text-right">${money(x.platform_fee_cents)}</td>
      <td class="p-3 text-right">${money(x.amount_cents)}</td>
    `;
    detail.appendChild(tr);
  });
}

document.getElementById("run").addEventListener("click", run);
run();
</script>
</body>
</html>
        """
    )


@router.get("/admin/pmc-payouts.data")
def pmc_payouts_data(
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    pmc_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    start_dt = _parse_date(start)
    end_dt = _parse_date(end)

    filters = [UpgradePurchase.status == "paid"]
    if start_dt:
        filters.append(UpgradePurchase.paid_at >= start_dt)
    if end_dt:
        # include the whole end day
        filters.append(UpgradePurchase.paid_at < end_dt.replace(hour=23, minute=59, second=59))
    if pmc_id:
        filters.append(UpgradePurchase.pmc_id == pmc_id)

    # Summary grouped by PMC
    summary_q = (
        db.query(
            UpgradePurchase.pmc_id.label("pmc_id"),
            func.sum(UpgradePurchase.amount_cents).label("gross_cents"),
            func.sum(UpgradePurchase.platform_fee_cents).label("fee_cents"),
            func.sum(UpgradePurchase.net_amount_cents).label("net_cents"),
            func.count(UpgradePurchase.id).label("count"),
            func.max(PMC.email).label("pmc_email"),
        )
        .join(PMC, PMC.id == UpgradePurchase.pmc_id)
        .filter(and_(*filters))
        .group_by(UpgradePurchase.pmc_id)
        .order_by(func.sum(UpgradePurchase.net_amount_cents).desc())
    )

    summary = [
        {
            "pmc_id": int(r.pmc_id),
            "pmc_email": r.pmc_email,
            "gross_cents": int(r.gross_cents or 0),
            "fee_cents": int(r.fee_cents or 0),
            "net_cents": int(r.net_cents or 0),
            "count": int(r.count or 0),
        }
        for r in summary_q.all()
    ]

    # Detail rows (for reconciliation)
    transfers_q = (
        db.query(UpgradePurchase)
        .filter(and_(*filters))
        .order_by(UpgradePurchase.paid_at.desc())
        .limit(500)
        .all()
    )

    transfers = []
    for p in transfers_q:
        transfers.append(
            {
                "purchase_id": p.id,
                "pmc_id": getattr(p, "pmc_id", None),
                "paid_at": (p.paid_at.isoformat() if getattr(p, "paid_at", None) else None),
                "amount_cents": int(getattr(p, "amount_cents", 0) or 0),
                "platform_fee_cents": int(getattr(p, "platform_fee_cents", 0) or 0),
                "net_amount_cents": int(getattr(p, "net_amount_cents", 0) or 0),
                "stripe_transfer_id": getattr(p, "stripe_transfer_id", None),
                "stripe_destination_account_id": getattr(p, "stripe_destination_account_id", None),
            }
        )

    total_paid = int(sum(x["count"] for x in summary))

    return JSONResponse(
        {
            "meta": {
                "start": start,
                "end": end,
                "pmc_id": pmc_id,
                "total_paid": total_paid,
            },
            "summary": summary,
            "transfers": transfers,
        }
    )


@router.get("/admin/pmc-payouts.csv")
def pmc_payouts_csv(
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    pmc_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    start_dt = _parse_date(start)
    end_dt = _parse_date(end)

    filters = [UpgradePurchase.status == "paid"]
    if start_dt:
        filters.append(UpgradePurchase.paid_at >= start_dt)
    if end_dt:
        filters.append(UpgradePurchase.paid_at < end_dt.replace(hour=23, minute=59, second=59))
    if pmc_id:
        filters.append(UpgradePurchase.pmc_id == pmc_id)

    q = (
        db.query(UpgradePurchase)
        .filter(and_(*filters))
        .order_by(UpgradePurchase.paid_at.desc())
        .all()
    )

    def gen():
        yield "purchase_id,pmc_id,paid_at,gross_cents,fee_cents,net_cents,transfer_id,destination_account_id\n"
        for p in q:
            yield (
                f"{p.id},"
                f"{getattr(p,'pmc_id', '')},"
                f"{(p.paid_at.isoformat() if getattr(p,'paid_at', None) else '')},"
                f"{int(getattr(p,'amount_cents',0) or 0)},"
                f"{int(getattr(p,'platform_fee_cents',0) or 0)},"
                f"{int(getattr(p,'net_amount_cents',0) or 0)},"
                f"{getattr(p,'stripe_transfer_id','') or ''},"
                f"{getattr(p,'stripe_destination_account_id','') or ''}\n"
            )

    return StreamingResponse(gen(), media_type="text/csv")
