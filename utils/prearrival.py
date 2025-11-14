from fastapi import APIRouter
from utils.config import load_property_config

prearrival_router = APIRouter()

@prearrival_router.get("/api/prearrival-options")
def prearrival_options(phone: str):
    # your logic here
    return {"message": "Prearrival options coming soon."}
