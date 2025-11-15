# In routes/admin.py
from fastapi import APIRouter
from utils.hostaway_sync import sync_hostaway_properties

admin_router = APIRouter()

@admin_router.post("/admin/sync-hostaway-properties")
def sync_properties():
    try:
        result = sync_hostaway_properties()
        return {"success": True, "synced": result}
    except Exception as e:
        return {"success": False, "error": str(e)}
