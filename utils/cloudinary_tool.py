import requests
import cloudinary
import cloudinary.uploader
import os

# Load Cloudinary credentials (you can also hardcode them here if needed)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

def upload_openai_file_to_cloudinary(url: str, filename: str) -> dict:
    """
    Downloads a file from OpenAI's temporary URL and uploads it to Cloudinary.
    
    Args:
        url (str): The OpenAI file URL (files.oaiusercontent.com)
        filename (str): Original filename (used for Cloudinary public_id)

    Returns:
        dict: {
            "url": "https://res.cloudinary.com/...",
            "filename": "original.jpg"
        }
    Raises:
        Exception: If the download or upload fails
    """
    # Step 1: Download from OpenAI file URL using Bearer token
    openai_api_key = os.getenv("OPENAI_API_KEY")
    headers = {"Authorization": f"Bearer {openai_api_key}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"OpenAI download failed with status {response.status_code}")

    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise Exception(f"Downloaded file is not an image: {content_type}")

    # Step 2: Upload to Cloudinary using file content
    upload_response = cloudinary.uploader.upload(
        response.content,
        public_id=filename.rsplit(".", 1)[0],  # use filename without extension
        resource_type="image"
    )

    return {
        "url": upload_response["secure_url"],
        "filename": filename
    }
