from fastapi import APIRouter
from utils.hostaway_sync import sync_hostaway_to_airtable

sync_router = APIRouter()

@sync_router.post("/admin/sync-properties")
def run_sync():
    try:
        sync_hostaway_to_airtable()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
