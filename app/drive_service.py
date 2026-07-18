import os
import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


import json

def get_drive_service():
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if service_account_json:
        credentials_info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=SCOPES
        )
    else:
        credentials_path = os.getenv(
            "GOOGLE_APPLICATION_CREDENTIALS",
            "/etc/secrets/service-account.json"
        )

        if not os.path.exists(credentials_path):
            credentials_path = "service-account.json"

        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=SCOPES
        )

    service = build(
        "drive",
        "v3",
        credentials=credentials
    )

    return service

def find_folder_by_name(folder_name):
    print("Searching:", repr(folder_name))
    service = get_drive_service()

    results = service.files().list(
        q="mimeType='application/vnd.google-apps.folder'",
        fields="files(id,name)"
    ).execute()

    target = folder_name.strip().lower()

    for folder in results.get("files", []):
        print("Drive folder:", repr(folder["name"]))
        current = folder["name"].strip().lower()

        if current == target:
            return folder["id"]

    return None

def get_images_from_folder(folder_id):
    service = get_drive_service()

    results = service.files().list(
        q=(
            f"'{folder_id}' in parents "
            "and mimeType contains 'image/'"
        ),
        fields="files(id,name)"
    ).execute()

    images = []

    for file in results.get("files", []):
        images.append({
            "name": file["name"],
            "url": f"https://lh3.googleusercontent.com/d/{file['id']}"
        })

    return images

def get_image_bytes(file_id):
    service = get_drive_service()

    request = service.files().get_media(fileId=file_id)

    file_data = io.BytesIO()

    downloader = MediaIoBaseDownload(file_data, request)

    done = False

    while not done:
        status, done = downloader.next_chunk()

    file_data.seek(0)

    return file_data