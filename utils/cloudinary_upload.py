import cloudinary
import cloudinary.uploader
import os
import requests
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_image_from_url(openai_url, filename=None):
    # Fetch image from OpenAI
    openai_api_key = os.getenv("OPENAI_API_KEY")
    headers = {"Authorization": f"Bearer {openai_api_key}"}
    response = requests.get(openai_url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"OpenAI download failed: {response.status_code}")

    # Upload image to Cloudinary
    upload_options = {}
    if filename:
        upload_options["public_id"] = filename.split('.')[0]

    file_stream = BytesIO(response.content)
    result = cloudinary.uploader.upload(file_stream, **upload_options)
    return result["secure_url"]
