
import os
import json
import time
import logging
import requests
#import openai



from database import SessionLocal
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from database import engine


from fastapi import (
    FastAPI, Request, Query, Path, HTTPException, Header, Form,
    APIRouter
)
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime, timedelta

from utils.config import load_property_config
from utils.hostaway import cached_token, fetch_reservations, find_upcoming_guest_by_code
from utils.message_helpers import classify_category, smart_response, detect_log_types
from utils.prearrival import prearrival_router
from utils.prearrival_debug import prearrival_debug_router
from utils.pms_sync import sync_all_pmcs
from apscheduler.schedulers.background import BackgroundScheduler
from uuid import uuid4
import uvicorn

from starlette.middleware.sessions import SessionMiddleware
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

from routes import admin, pmc_auth


# --- Config ---
#openai.api_key = os.getenv("OPENAI_API_KEY")


# --- Init ---
app = FastAPI()  # ✅ Define app before using it


# --- Routers ---
app.include_router(admin.router)
app.include_router(pmc_auth.router)
app.include_router(prearrival_router)
app.include_router(prearrival_debug_router)

# Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or "fallbacksecret"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static + Templates
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print("❌ Validation Error:")
    print("➡️ Raw body:", await request.body())
    print("➡️ Errors:", exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()}
    )
    
# --- Startup Jobs ---
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(sync_all_pmcs, "interval", hours=24)
    scheduler.start()

# --- DB Connection Test ---
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        print("✅ Database connected successfully.")
except SQLAlchemyError as e:
    print(f"❌ Database connection failed: {e}")


start_scheduler()

# --- Sync Trigger ---
@app.post("/admin/sync-properties")
def manual_sync():
    try:
        count = sync_all_pmcs()
        return HTMLResponse(
            f"<h2>Synced {count} properties across all PMCs.</h2>"
            "<a href='/admin/dashboard'>Back to Dashboard</a>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<h2>Sync failed: {str(e)}</h2>"
            "<a href='/admin/dashboard'>Back to Dashboard</a>",
            status_code=500
        )

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

# --- Chat Endpoint ---
class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat(request: ChatRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": request.message}
            ]
        )
        return {"response": response.choices[0].message.content}
    except Exception as e:
        return {"error": str(e)}

# --- Start Server ---
if __name__ == "__main__":
    try:
        uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
    except Exception as e:
        print(f"Error: {e}")
