import cloudinary
import cloudinary.uploader
import os
from dotenv import load_dotenv
import requests
from io import BytesIO  # ✅ Needed to wrap binary content

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_image_from_url(url, filename=None):
    headers = {}
    openai_key = os.getenv("OPENAI_API_KEY")
    
    # ✅ Add auth if this is an OpenAI gated file
    if "files.oaiusercontent.com" in url and openai_key:
        headers["Authorization"] = f"Bearer {openai_key}"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Download failed: {response.status_code}")

    # ✅ Wrap the binary content in a file-like object
    image_file = BytesIO(response.content)

    upload_options = {}
    if filename:
        upload_options["public_id"] = filename.split('.')[0]

    result = cloudinary.uploader.upload(image_file, **upload_options)
    return result["secure_url"]
