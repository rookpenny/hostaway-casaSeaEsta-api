from fastapi import APIRouter
from fastapi.responses import JSONResponse, HTMLResponse
from utils.hostaway import sync_hostaway_properties  # Update if path differs

admin_router = APIRouter()

# ✅ Serve the Admin Dashboard page
@admin_router.get("/admin", response_class=HTMLResponse)
def get_admin_dashboard():
    try:
        with open("templates/admin_dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="Admin dashboard not found.", status_code=404)

# ✅ Handle Sync button POST
@admin_router.post("/admin/sync-hostaway-properties")
def sync_hostaway_properties_route():
    try:
        result = sync_hostaway_properties()
        return {"success": True, "synced": result}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to sync Hostaway properties", "details": str(e)}
        )
