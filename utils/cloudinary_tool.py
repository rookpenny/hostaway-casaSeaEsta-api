# utils/cloudinary_tool.py

import cloudinary
import cloudinary.uploader
import os
from dotenv import load_dotenv
import requests

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_image_from_url(url, filename=None):
    # Fetch the image content (required if url is OpenAI gated)
    headers = {}
    openai_key = os.getenv("OPENAI_API_KEY")
    if "files.oaiusercontent.com" in url and openai_key:
        headers["Authorization"] = f"Bearer {openai_key}"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    upload_options = {}
    if filename:
        upload_options["public_id"] = filename.split('.')[0]

    result = cloudinary.uploader.upload(response.content, **upload_options)
    return result["secure_url"]
