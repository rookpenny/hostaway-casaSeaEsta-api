from fastapi import APIRouter
from fastapi.responses import JSONResponse
from utils.hostaway import sync_hostaway_properties  # Make sure this exists

admin_router = APIRouter()

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
