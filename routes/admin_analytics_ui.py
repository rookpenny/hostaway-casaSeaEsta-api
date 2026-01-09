from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")

router = APIRouter()

def _require_admin(request: Request):
    role = (request.session.get("role") or "").strip().lower()
    if role not in ("pmc", "super", "superuser"):
        return False
    return True

@router.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics_page(request: Request):
    if not _require_admin(request):
        return templates.TemplateResponse("access_denied.html", {"request": request})

    # If PMC, we can pass pmc_id to the page (useful for debugging/UI display)
    return templates.TemplateResponse(
        "admin/admin_analytics.html",
        {
            "request": request,
            "role": (request.session.get("role") or "").strip().lower(),
            "pmc_id": request.session.get("pmc_id"),
        },
    )
