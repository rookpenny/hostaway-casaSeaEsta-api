from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter()


def _require_admin(request: Request) -> bool:
    role = (request.session.get("role") or "").strip().lower()
    return role in ("pmc", "super", "superuser")


@router.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics_page(request: Request):
    if not _require_admin(request):
        return templates.TemplateResponse("access_denied.html", {"request": request})

    role = (request.session.get("role") or "").strip().lower()
    pmc_id = request.session.get("pmc_id")

    return templates.TemplateResponse(
        "admin/admin_analytics.html",
        {
            "request": request,
            "role": role,
            "pmc_id": pmc_id,
        },
    )
