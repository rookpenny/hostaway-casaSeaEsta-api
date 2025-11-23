import os
import json
import time
import logging
import requests

from fastapi import (
    FastAPI, Request, Query, Path, HTTPException, Header, Form,
    APIRouter
)
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel
from datetime import datetime, timedelta

from utils.airtable_client import (
    get_properties_table,
    get_pmcs_table,
    get_guests_table,
    get_messages_table
)

from utils.config import load_property_config
from utils.hostaway import cached_token, fetch_reservations, find_upcoming_guest_by_code
from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.hostaway_sync import sync_all_pmc_properties
from apscheduler.schedulers.background import BackgroundScheduler
from uuid import uuid4
import uvicorn

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from routes import admin, pmc_auth  # ✅ make sure these match your folder/filenames

app.include_router(pmc_auth.router)


# --- Config ---
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_PMC_TABLE_ID = "tblzUdyZk1tAQ5wjx"

# --- Init ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")


# Mount templates/static if needed
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register routes
app.include_router(admin.admin_router)
app.include_router(pmc_auth.router)  # ✅ this line should come after `app = FastAPI()`


# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
from routes.admin import admin_router
app.include_router(admin_router)
app.include_router(prearrival_router)
app.include_router(prearrival_debug_router)

# --- Startup Jobs ---
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_all_pmc_properties, 'interval', hours=24)
    scheduler.start()

start_scheduler()

# --- Sync Trigger ---
@app.post("/admin/sync-properties")
def manual_sync():
    try:
        sync_all_pmc_properties()
        return HTMLResponse("<h2>Sync completed successfully!</h2><a href='/admin'>Back to Dashboard</a>")
    except Exception as e:
        return HTMLResponse(f"<h2>Sync failed: {str(e)}</h2><a href='/admin'>Back to Dashboard</a>", status_code=500)

# --- Root Health Check ---
@app.get("/")
def root():
    return {"message": "Welcome to the multi-property Sandy API (FastAPI edition)!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/routes")
def list_routes():
    return [{"path": route.path, "methods": list(route.methods)} for route in app.router.routes]

# Additional routes (e.g., /properties, /guests, /guest-message, etc.)
# are handled and correct as provided in your current file

# --- Start Server ---
if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")
