import os
import re
import cloudinary
import cloudinary.uploader
from app.drive_service import get_image_bytes, get_images_from_folder
from app import db, create_app
from sqlalchemy import text

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

app = create_app()

def is_folder_link(link):
    return '/folders/' in link

def get_folder_id(link):
    match = re.search(r'/folders/([-\w]+)', link)
    return match.group(1) if match else None

def get_file_id(link):
    match = re.search(r'[-\w]{25,}', link)
    return match.group(0) if match else None

def upload_bytes_to_cloudinary(image_bytes):
    try:
        result = cloudinary.uploader.upload(image_bytes)
        return result['secure_url']
    except Exception as e:
        print(f"  Cloudinary upload failed: {e}")
        return None

def migrate_single_link(link):
    link = link.strip()
    if not link or not link.startswith('http'):
        return None

    if is_folder_link(link):
        folder_id = get_folder_id(link)
        if not folder_id:
            return None
        try:
            images = get_images_from_folder(folder_id)
            urls = []
            for img in images:
                file_id = img["url"].split("/")[-1]
                image_bytes = get_image_bytes(file_id)
                new_url = upload_bytes_to_cloudinary(image_bytes)
                if new_url:
                    urls.append(new_url)
            return ", ".join(urls) if urls else None
        except Exception as e:
            print(f"  Folder migration failed for {link}: {e}")
            return None
    else:
        file_id = get_file_id(link)
        if not file_id:
            return None
        try:
            image_bytes = get_image_bytes(file_id)
            return upload_bytes_to_cloudinary(image_bytes)
        except Exception as e:
            print(f"  File migration failed for {link}: {e}")
            return None

def migrate_field(table, id_col, field_col, name_col):
    print(f"\n=== Migrating {table}.{field_col} ===")
    rows = db.session.execute(
        text(f"SELECT {id_col}, {name_col}, {field_col} FROM {table} WHERE {field_col} IS NOT NULL")
    ).mappings().all()

    for row in rows:
        raw_value = row[field_col]
        name = row[name_col]
        row_id = row[id_col]

        links = [l.strip() for l in raw_value.split(',') if l.strip().startswith('http')]
        if not links:
            print(f"Skipping {name} (id {row_id}) — no valid links found")
            continue

        print(f"Migrating {name} (id {row_id})...")
        new_urls = []
        for link in links:
            result = migrate_single_link(link)
            if result:
                new_urls.extend(result.split(', '))

        if new_urls:
            final_value = ", ".join(new_urls)
            db.session.execute(
                text(f"UPDATE {table} SET {field_col} = :val WHERE {id_col} = :id"),
                {"val": final_value, "id": row_id}
            )
            db.session.commit()
            print(f"  Done — {len(new_urls)} photo(s) migrated")
        else:
            print(f"  No photos migrated for {name}")

with app.app_context():
    migrate_field("homestays", "id", "photo_homestay", "homestay_name")
    migrate_field("homestays", "id", "amenities_photos", "homestay_name")
    migrate_field("locations", "id", "photo_location", "location_name")
    migrate_field("locations", "id", "site_photos", "location_name")

print("\n=== Migration complete! ===")