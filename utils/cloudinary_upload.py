# utils/cloudinary_upload.py

import cloudinary
import cloudinary.uploader
import os
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_image_from_url(url, filename=None):
    upload_options = {}
    if filename:
        upload_options["public_id"] = filename.split('.')[0]

    result = cloudinary.uploader.upload(url, **upload_options)
    return result["secure_url"]
