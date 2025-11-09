import cloudinary
import cloudinary.uploader
import os
import requests
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_image_from_url(url, filename=None):
    headers = {}
    if "files.oaiusercontent.com" in url:
        headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY')}"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    upload_options = {}
    if filename:
        upload_options["public_id"] = filename.split('.')[0]

    result = cloudinary.uploader.upload(
        response.content,
        resource_type="image",
        **upload_options
    )

    return result["secure_url"]
