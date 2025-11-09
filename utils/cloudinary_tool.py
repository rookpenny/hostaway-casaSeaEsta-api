import os
import cloudinary
import cloudinary.uploader
import requests
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_openai_file_to_cloudinary(openai_file_url, filename=None):
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise Exception("OPENAI_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {openai_api_key}"
    }

    response = requests.get(openai_file_url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    file_data = response.content
    upload_options = {}

    if filename:
        upload_options["public_id"] = filename.rsplit(".", 1)[0]

    upload_response = cloudinary.uploader.upload(file_data, **upload_options)
    return {
        "url": upload_response["secure_url"],
        "public_id": upload_response["public_id"]
    }
