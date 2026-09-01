import os
import requests as http_requests
from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import text
from app import db

from google.oauth2 import id_token
from google.auth.transport import requests as grequests

from app.utils.map_utils import extract_coordinates_from_google_maps
import cloudinary
import cloudinary.uploader
import re

def slugify(text_value):
    text_value = text_value.lower().strip()
    text_value = re.sub(r'[^a-z0-9]+', '-', text_value)
    return text_value.strip('-') or 'post'

def unique_slug(base, table):
    base_slug = slugify(base)
    candidate = base_slug
    i = 2
    while True:
        exists = db.session.execute(
            text(f"SELECT 1 FROM {table} WHERE slug = :slug"),
            {"slug": candidate}
        ).first()
        if not exists:
            return candidate
        candidate = f"{base_slug}-{i}"
        i += 1

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)

from app.drive_service import (
    find_folder_by_name,
    get_images_from_folder,
    get_image_bytes,
)
bp = Blueprint('main', __name__)

@bp.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "success",
        "message": "Antigravity Flask Backend Initialized Ready for PostGIS"
    }), 200

@bp.route('/api/youtube-videos', methods=['GET'])
def get_youtube_videos():
    try:
        playlist_id = os.environ.get('YOUTUBE_PLAYLIST_ID')
        api_key = os.environ.get('YOUTUBE_API_KEY')

        if not playlist_id or not api_key:
            return jsonify({"success": False, "error": "YouTube playlist not configured."}), 500

        response = http_requests.get(
            "https://www.googleapis.com/youtube/v3/playlistItems",
            params={
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 25,
                "key": api_key,
            }
        )
        data = response.json()

        if "error" in data:
            return jsonify({"success": False, "error": data["error"].get("message", "YouTube API error.")}), 502

        videos = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            video_id = snippet.get("resourceId", {}).get("videoId")
            if not video_id:
                continue
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = (
                thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
            ).get("url")

            videos.append({
                "video_id": video_id,
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "thumbnail": thumbnail,
                "published_at": snippet.get("publishedAt"),
            })

        return jsonify({"success": True, "videos": videos}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    
@bp.route('/api/locations', methods=['GET'])
def get_locations():
    try:
        taluka = request.args.get('taluka', 'All')
        attraction_type = request.args.get('type', 'All Types')
        search = request.args.get('q', '')

        query = text("""
            SELECT
                id, location_name, village_name, taluka_name, district_name,
                attraction_type, category, nearest_landmark, road_condition,
                signboards_available, seasonal_availability, avg_time_spent,
                photo_location, site_photos, latitude, longitude
            FROM locations
            WHERE (:taluka = 'All' OR taluka_name = :taluka)
              AND (:attraction_type = 'All Types' OR attraction_type = :attraction_type)
              AND (
                    :search = '' OR
                    location_name ILIKE :search_like OR
                    village_name ILIKE :search_like OR
                    nearest_landmark ILIKE :search_like
              )
            ORDER BY location_name
        """)
        

        result = db.session.execute(query, {
            "taluka": taluka,
            "attraction_type": attraction_type,
            "search": search,
            "search_like": f"%{search}%"
        }).mappings().all()

        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/api/homestays', methods=['GET'])
def get_homestays():
    try:
        taluka = request.args.get('taluka', 'All')
        homestay_type = request.args.get('type', 'All Types')
        search = request.args.get('q', '')

        query = text("""
            SELECT *
            FROM homestays
            WHERE (:taluka = 'All' OR taluka_name = :taluka)
              AND (:homestay_type = 'All Types' OR homestay_type = :homestay_type)
              AND (
                    :search = '' OR
                    homestay_name ILIKE :search_like OR
                    village_town_city ILIKE :search_like OR
                    owner_name ILIKE :search_like
              )
            ORDER BY homestay_name
        """)

        result = db.session.execute(query, {
            "taluka": taluka,
            "homestay_type": homestay_type,
            "search": search,
            "search_like": f"%{search}%"
        }).mappings().all()

        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@bp.route('/api/nearby-locations/<path:location_name>', methods=['GET'])
def get_nearby_locations(location_name):
    try:
        result = db.session.execute(
            text("SELECT nearby_name, lat, lng FROM nearby_locations WHERE main_location = :main_location"),
            {"main_location": location_name}
        ).mappings().all()

        nearby = [{"name": row["nearby_name"], "lat": row["lat"], "lng": row["lng"]} for row in result]

        return jsonify({"success": True, "nearby": nearby}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/drivers/register', methods=['POST'])
def register_driver():
    try:
        data = request.get_json()
        google_maps_link = data.get("google_maps_link")

        try:
            latitude, longitude = extract_coordinates_from_google_maps(google_maps_link)
        except Exception:
            latitude = None
            longitude = None

        query = text("""
            INSERT INTO pending_drivers (
                driver_name, phone_number, vehicle_type, vehicle_number,
                base_village, taluka_name, district_name, service_area,
                per_day_rate, google_maps_link, latitude, longitude,
                vehicle_photos
            )
            VALUES (
                :driver_name, :phone_number, :vehicle_type, :vehicle_number,
                :base_village, :taluka_name, :district_name, :service_area,
                :per_day_rate, :google_maps_link, :latitude, :longitude,
                :vehicle_photos
            )
        """)

        db.session.execute(query, {
            "driver_name": data.get("driver_name"),
            "phone_number": data.get("phone_number"),
            "vehicle_type": data.get("vehicle_type"),
            "vehicle_number": data.get("vehicle_number"),
            "base_village": data.get("base_village"),
            "taluka_name": data.get("taluka_name"),
            "district_name": data.get("district_name", "Ratnagiri"),
            "service_area": data.get("service_area"),
            "per_day_rate": data.get("per_day_rate"),
            "google_maps_link": google_maps_link,
            "latitude": latitude,
            "longitude": longitude,
            "vehicle_photos": data.get("vehicle_photos"),
        })
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Driver submitted successfully. Waiting for admin approval."
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/pending_drivers', methods=['GET'])
def get_pending_drivers():
    try:
        result = db.session.execute(text("""
            SELECT * FROM pending_drivers ORDER BY submitted_at DESC
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/pending_drivers/<int:id>/approve', methods=['POST'])
def approve_driver(id):
    try:
        pending = db.session.execute(
            text("SELECT * FROM pending_drivers WHERE id=:id"), {"id": id}
        ).mappings().first()

        if pending is None:
            return jsonify({"success": False, "error": "Pending driver not found."}), 404

        data = dict(pending)
        data.pop("id", None)
        data.pop("submitted_at", None)

        columns = ", ".join(data.keys())
        values = ", ".join([f":{k}" for k in data.keys()])

        db.session.execute(
            text(f"INSERT INTO drivers ({columns}) VALUES ({values})"), data
        )
        db.session.execute(
            text("DELETE FROM pending_drivers WHERE id=:id"), {"id": id}
        )
        db.session.commit()

        return jsonify({"success": True, "message": "Driver copied to drivers table."}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/pending_drivers/<int:id>/reject', methods=['POST'])
def reject_driver(id):
    try:
        db.session.execute(
            text("DELETE FROM pending_drivers WHERE id = :id"), {"id": id}
        )
        db.session.commit()
        return jsonify({"success": True, "message": "Driver rejected and removed successfully."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/eco', methods=['GET'])
def get_eco():
    try:
        result = db.session.execute(text("SELECT * FROM eco_and_water")).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/bus_stops/register', methods=['POST'])
def register_bus_stop():
    try:
        data = request.get_json()
        google_maps_link = data.get("google_maps_link")

        try:
            latitude, longitude = extract_coordinates_from_google_maps(google_maps_link)
        except Exception:
            latitude = None
            longitude = None

        # Allow manually entered lat/lng to override/fill in if the maps link didn't resolve
        if latitude is None and data.get("latitude") not in (None, ""):
            latitude = data.get("latitude")
        if longitude is None and data.get("longitude") not in (None, ""):
            longitude = data.get("longitude")

        query = text("""
            INSERT INTO bus_stops (
                stop_name, taluka_name, district_name,
                google_maps_link, latitude, longitude, timetable_link
            )
            VALUES (
                :stop_name, :taluka_name, :district_name,
                :google_maps_link, :latitude, :longitude, :timetable_link
            )
        """)

        db.session.execute(query, {
            "stop_name": data.get("stop_name"),
            "taluka_name": data.get("taluka_name"),
            "district_name": data.get("district_name", "Ratnagiri"),
            "google_maps_link": google_maps_link,
            "latitude": latitude,
            "longitude": longitude,
            "timetable_link": data.get("timetable_link"),
        })
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Bus stand added successfully."
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/bus_stops', methods=['GET'])
def get_bus_stops():
    try:
        result = db.session.execute(text("""
            SELECT * FROM bus_stops ORDER BY stop_name
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@bp.route('/api/blog-categories', methods=['GET'])
def list_blog_categories():
    try:
        result = db.session.execute(text("""
            SELECT c.id, c.name, c.slug,
                   COUNT(b.id) FILTER (WHERE b.status = 'published') AS post_count
            FROM blog_categories c
            LEFT JOIN blogs b ON b.category_id = c.id
            GROUP BY c.id, c.name, c.slug
            ORDER BY c.name
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/blogs', methods=['GET'])
def list_blogs():
    try:
        latest = request.args.get('latest')
        if latest:
            limit = int(latest)
            result = db.session.execute(text("""
                SELECT id, title, slug, cover_image, published_at
                FROM blogs
                WHERE status = 'published'
                ORDER BY published_at DESC
                LIMIT :limit
            """), {"limit": limit}).mappings().all()
            return jsonify([dict(row) for row in result]), 200

        category = request.args.get('category', 'All')
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 6))
        offset = (page - 1) * limit

        base_query = """
            FROM blogs b
            LEFT JOIN blog_categories c ON c.id = b.category_id
            WHERE b.status = 'published'
              AND (:category = 'All' OR c.slug = :category)
              AND (:search = '' OR b.title ILIKE :search_like OR b.excerpt ILIKE :search_like)
        """
        params = {
            "category": category, "search": search,
            "search_like": f"%{search}%", "limit": limit, "offset": offset,
        }

        total = db.session.execute(text(f"SELECT COUNT(*) {base_query}"), params).scalar()

        rows = db.session.execute(text(f"""
            SELECT b.id, b.title, b.slug, b.excerpt, b.cover_image, b.author_name,
                   b.published_at, b.views,
                   c.id AS category_id, c.name AS category_name, c.slug AS category_slug,
                   (SELECT COUNT(*) FROM blog_comments cm WHERE cm.blog_id = b.id) AS comment_count
            {base_query}
            ORDER BY b.published_at DESC
            LIMIT :limit OFFSET :offset
        """), params).mappings().all()

        blogs = []
        for row in rows:
            row = dict(row)
            category_obj = None
            if row.get("category_id"):
                category_obj = {"id": row["category_id"], "name": row["category_name"], "slug": row["category_slug"]}
            blogs.append({
                "id": row["id"], "title": row["title"], "slug": row["slug"],
                "excerpt": row["excerpt"], "cover_image": row["cover_image"],
                "author_name": row["author_name"], "published_at": row["published_at"],
                "views": row["views"], "comment_count": row["comment_count"],
                "category": category_obj,
            })

        total_pages = max(1, (total + limit - 1) // limit)
        return jsonify({"blogs": blogs, "total": total, "total_pages": total_pages}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/blogs/<slug>', methods=['GET'])
def get_blog(slug):
    try:
        row = db.session.execute(text("""
            SELECT b.*, c.id AS cat_id, c.name AS cat_name, c.slug AS cat_slug
            FROM blogs b
            LEFT JOIN blog_categories c ON c.id = b.category_id
            WHERE b.slug = :slug AND b.status = 'published'
        """), {"slug": slug}).mappings().first()

        if row is None:
            return jsonify({"error": "Story not found."}), 404

        db.session.execute(text("UPDATE blogs SET views = views + 1 WHERE id = :id"), {"id": row["id"]})
        db.session.commit()

        comments = db.session.execute(text("""
            SELECT id, name, comment, created_at
            FROM blog_comments WHERE blog_id = :blog_id
            ORDER BY created_at DESC
        """), {"blog_id": row["id"]}).mappings().all()

        data = dict(row)
        category_obj = None
        if data.get("cat_id"):
            category_obj = {"id": data["cat_id"], "name": data["cat_name"], "slug": data["cat_slug"]}
        data["category"] = category_obj
        for k in ("cat_id", "cat_name", "cat_slug"):
            data.pop(k, None)
        data["comments"] = [dict(c) for c in comments]

        return jsonify(data), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/blogs/<int:blog_id>/comments', methods=['POST'])
def add_blog_comment(blog_id):
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        comment = (data.get("comment") or "").strip()
        email = (data.get("email") or "").strip() or None

        if not name or not comment:
            return jsonify({"success": False, "error": "Name and comment are required."}), 400

        db.session.execute(text("""
            INSERT INTO blog_comments (blog_id, name, email, comment)
            VALUES (:blog_id, :name, :email, :comment)
        """), {"blog_id": blog_id, "name": name, "email": email, "comment": comment})
        db.session.commit()

        return jsonify({"success": True}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/blogs', methods=['GET'])
def admin_list_blogs():
    try:
        rows = db.session.execute(text("""
            SELECT b.id, b.title, b.slug, b.status, b.published_at, b.views,
                   c.id AS category_id, c.name AS category_name
            FROM blogs b
            LEFT JOIN blog_categories c ON c.id = b.category_id
            ORDER BY b.created_at DESC
        """)).mappings().all()

        blogs = []
        for row in rows:
            row = dict(row)
            category_obj = {"id": row["category_id"], "name": row["category_name"]} if row.get("category_id") else None
            blogs.append({
                "id": row["id"], "title": row["title"], "slug": row["slug"],
                "status": row["status"], "published_at": row["published_at"],
                "views": row["views"], "category": category_obj,
            })
        return jsonify(blogs), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/admin/blogs', methods=['POST'])
def admin_create_blog():
    try:
        data = request.get_json()
        title = (data.get("title") or "").strip()
        content = (data.get("content") or "").strip()

        if not title or not content:
            return jsonify({"success": False, "error": "Title and content are required."}), 400

        slug = unique_slug(title, "blogs")
        status = data.get("status", "draft")
        published_at_clause = "NOW()" if status == "published" else "NULL"

        result = db.session.execute(text(f"""
            INSERT INTO blogs (title, slug, excerpt, content, cover_image, author_name, category_id, status, published_at)
            VALUES (:title, :slug, :excerpt, :content, :cover_image, :author_name, :category_id, :status, {published_at_clause})
            RETURNING id
        """), {
            "title": title, "slug": slug,
            "excerpt": data.get("excerpt") or None,
            "content": content,
            "cover_image": data.get("cover_image") or None,
            "author_name": data.get("author_name") or None,
            "category_id": data.get("category_id") or None,
            "status": status,
        })
        new_id = result.scalar()
        db.session.commit()

        return jsonify({"success": True, "id": new_id, "slug": slug}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/blogs/<int:id>', methods=['PUT'])
def admin_update_blog(id):
    try:
        data = request.get_json()
        existing = db.session.execute(
            text("SELECT status, published_at FROM blogs WHERE id = :id"), {"id": id}
        ).mappings().first()

        if existing is None:
            return jsonify({"success": False, "error": "Story not found."}), 404

        status = data.get("status", existing["status"])
        set_published = ", published_at = NOW()" if (status == "published" and existing["published_at"] is None) else ""

        db.session.execute(text(f"""
            UPDATE blogs SET
                title = :title, excerpt = :excerpt, content = :content,
                cover_image = :cover_image, author_name = :author_name,
                category_id = :category_id, status = :status, updated_at = NOW()
                {set_published}
            WHERE id = :id
        """), {
            "title": (data.get("title") or "").strip(),
            "excerpt": data.get("excerpt") or None,
            "content": data.get("content") or "",
            "cover_image": data.get("cover_image") or None,
            "author_name": data.get("author_name") or None,
            "category_id": data.get("category_id") or None,
            "status": status, "id": id,
        })
        db.session.commit()

        return jsonify({"success": True}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/blogs/<int:id>', methods=['DELETE'])
def admin_delete_blog(id):
    try:
        db.session.execute(text("DELETE FROM blogs WHERE id = :id"), {"id": id})
        db.session.commit()
        return jsonify({"success": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/blog-categories', methods=['POST'])
def admin_create_category():
    try:
        data = request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "Category name is required."}), 400

        slug = unique_slug(name, "blog_categories")

        result = db.session.execute(text("""
            INSERT INTO blog_categories (name, slug)
            VALUES (:name, :slug)
            RETURNING id, name, slug
        """), {"name": name, "slug": slug})
        row = result.mappings().first()
        db.session.commit()

        return jsonify(dict(row)), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    try:

        locations = db.session.execute(text("""
    SELECT *
    FROM locations
""")).mappings().all()

        homestays = db.session.execute(text("""
            SELECT *
            FROM homestays
        """)).mappings().all()

        eco = db.session.execute(text("""
            SELECT *
            FROM eco_and_water
        """)).mappings().all()

        drivers = db.session.execute(text("""
            SELECT *
            FROM drivers
        """)).mappings().all()

        bus_stops = db.session.execute(text("""
            SELECT *
            FROM bus_stops
        """)).mappings().all()

        return jsonify({

            "locations": [dict(row) for row in locations],

            "homestays": [dict(row) for row in homestays],

            "eco": [dict(row) for row in eco],

            "drivers": [dict(row) for row in drivers],

            "bus_stops": [dict(row) for row in bus_stops]

        }), 200

    except Exception as e:

        return jsonify({

            "error": str(e)

        }), 500

@bp.route('/api/locations/register', methods=['POST'])
def register_location():
    try:
        data = request.get_json()

        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if latitude is None or longitude is None:
            return jsonify({
                "success": False,
                "error": "Location coordinates are required."
            }), 400

        query = text("""
    INSERT INTO pending_locations (

        location_name,
        located_in,
        village_name,
        taluka_name,
        district_name,

        nearest_landmark,
        attraction_type,

        road_condition,
        signboards_available,
        public_transport,
        nearest_bus_stand,
        nearest_railway_station,

        parking_space,
        food_stalls,
        amenities_available,

        owned_by,
        managed_by,

        entry_fee,
        entry_fee_amount,
        visiting_hours,
        seasonal_availability,
        peak_period,
        avg_time_spent,
        visitor_type,
        crowd_level,
        site_activities,

        formal_regulations,
        local_residents_involved,
        job_type,
        suggestions_improvements,

        email_address,
        user_description,
        google_maps_link,
        latitude,
        longitude,

        photo_location,
        site_photos

    )
    VALUES (

        :location_name,
        :located_in,
        :village_name,
        :taluka_name,
        :district_name,

        :nearest_landmark,
        :attraction_type,

        :road_condition,
        :signboards_available,
        :public_transport,
        :nearest_bus_stand,
        :nearest_railway_station,

        :parking_space,
        :food_stalls,
        :amenities_available,

        :owned_by,
        :managed_by,

        :entry_fee,
        :entry_fee_amount,
        :visiting_hours,
        :seasonal_availability,
        :peak_period,
        :avg_time_spent,
        :visitor_type,
        :crowd_level,
        :site_activities,

        :formal_regulations,
        :local_residents_involved,
        :job_type,
        :suggestions_improvements,

        :email_address,
        :user_description,
        :google_maps_link,
        :latitude,
        :longitude,
        :photo_location,
        :site_photos

    )
""")

        db.session.execute(query, {

    # Basic Information
    "location_name": data.get("location_name"),
    "located_in": data.get("located_in"),
    "village_name": data.get("village_name"),
    "taluka_name": data.get("taluka_name"),
    "district_name": data.get("district_name", "Ratnagiri"),

    # Accessibility
    "nearest_landmark": data.get("nearest_landmark"),
    "attraction_type": data.get("attraction_type"),
    "road_condition": data.get("road_condition"),
    "signboards_available": data.get("signboards_available"),
    "public_transport": data.get("public_transport"),
    "nearest_bus_stand": data.get("nearest_bus_stand"),
    "nearest_railway_station": data.get("nearest_railway_station"),

    # Tourism Facilities
    "parking_space": data.get("parking_space"),
    "food_stalls": data.get("food_stalls"),
    "amenities_available": data.get("amenities_available"),

    # Management
    "owned_by": data.get("owned_by"),
    "managed_by": data.get("managed_by"),

    # Visitor Information
    "entry_fee": data.get("entry_fee"),
    "entry_fee_amount": data.get("entry_fee_amount"),
    "visiting_hours": data.get("visiting_hours"),
    "seasonal_availability": data.get("seasonal_availability"),
    "peak_period": data.get("peak_period"),
    "avg_time_spent": data.get("avg_time_spent"),
    "visitor_type": data.get("visitor_type"),
    "crowd_level": data.get("crowd_level"),
    "site_activities": data.get("site_activities"),

    # Sustainability
    "formal_regulations": data.get("formal_regulations"),
    "local_residents_involved": data.get("local_residents_involved"),
    "job_type": data.get("job_type"),
    "suggestions_improvements": data.get("suggestions_improvements"),

    # Contact
    "email_address": data.get("email_address"),
    "user_description": data.get("user_description"),
    "google_maps_link": data.get("google_maps_link"),
    "latitude": latitude,
    "longitude": longitude,

    

    # Photos
    "photo_location": data.get("photo_location"),
    "site_photos": data.get("site_photos")

})

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Location submitted successfully. Waiting for admin approval."
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/counts', methods=['GET'])
def get_counts():
    try:
        location_count = db.session.execute(text("SELECT COUNT(*) FROM locations")).scalar()
        homestay_count = db.session.execute(text("SELECT COUNT(*) FROM homestays")).scalar()
        pending_location_count = db.session.execute(text("SELECT COUNT(*) FROM pending_locations")).scalar()
        pending_homestay_count = db.session.execute(text("SELECT COUNT(*) FROM pending_homestays")).scalar()
 
        return jsonify({
            "locations": location_count or 0,
            "homestays": homestay_count or 0,
            "pending_locations": pending_location_count or 0,
            "pending_homestays": pending_homestay_count or 0
        }), 200
 
    except Exception as e:
        return jsonify({"error": str(e)}), 500        

        
@bp.route('/api/pending_locations', methods=['GET'])
def get_pending_locations():
    try:
        result = db.session.execute(text("""
            SELECT *
            FROM pending_locations
            ORDER BY submitted_at DESC
        """)).mappings().all()

        return jsonify([dict(row) for row in result]), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/pending_homestays', methods=['GET'])
def get_pending_homestays():
    try:

        result = db.session.execute(text("""
            SELECT *
            FROM pending_homestays
            ORDER BY submitted_at DESC
        """)).mappings().all()

        return jsonify([dict(row) for row in result]), 200

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/pending_locations/<int:id>/approve', methods=['POST'])
def approve_location(id):
    try:
        pending = db.session.execute(
            text("SELECT * FROM pending_locations WHERE id=:id"),
            {"id": id}
        ).mappings().first()

        if pending is None:
            return jsonify({
                "success": False,
                "error": "Pending location not found."
            }), 404

        data = dict(pending)

        data.pop("id", None)
        data.pop("status", None)
        data.pop("submitted_at", None)
        data.pop("reviewed_at", None)
        data.pop("reviewed_by", None)
        data.pop("remarks", None)

        columns = ", ".join(data.keys())
        values = ", ".join([f":{k}" for k in data.keys()])

        db.session.execute(
            text(f"""
                INSERT INTO locations ({columns})
                VALUES ({values})
            """),
            data
        )

        db.session.execute(
            text("DELETE FROM pending_locations WHERE id=:id"),
            {"id": id}
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Location copied to locations table."
        }), 200

        

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/pending_homestays/<int:id>/approve', methods=['POST'])
def approve_homestay(id):
    try:

        pending = db.session.execute(
            text("SELECT * FROM pending_homestays WHERE id=:id"),
            {"id": id}
        ).mappings().first()

        if pending is None:
            return jsonify({
                "success": False,
                "error": "Pending homestay not found."
            }), 404

        data = dict(pending)

        data.pop("id", None)
        data.pop("status", None)
        data.pop("submitted_at", None)
        data.pop("reviewed_at", None)

        columns = ", ".join(data.keys())
        values = ", ".join([f":{k}" for k in data.keys()])

        db.session.execute(
            text(f"""
                INSERT INTO homestays ({columns})
                VALUES ({values})
            """),
            data
        )

        db.session.execute(
            text("DELETE FROM pending_homestays WHERE id=:id"),
            {"id": id}
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Homestay copied to homestays table."
        }), 200

    except Exception as e:

        db.session.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/pending_homestays/<int:id>/reject', methods=['POST'])
def reject_homestay(id):
    try:

        db.session.execute(
            text("""
                DELETE FROM pending_homestays
                WHERE id = :id
            """),
            {"id": id}
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Homestay rejected and removed successfully."
        }), 200

    except Exception as e:

        db.session.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/pending_locations/<int:id>/reject', methods=['POST'])
def reject_location(id):
    try:

        db.session.execute(
            text("""
                DELETE FROM pending_locations
                WHERE id = :id
            """),
            {"id": id}
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Location rejected and removed successfully."
        }), 200

    except Exception as e:

        db.session.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500 

@bp.route('/api/homestays/register', methods=['POST'])
def register_homestay():
    try:

        data = request.get_json()

        google_map_link = data.get("google_maps_link")

        try:
            latitude, longitude = extract_coordinates_from_google_maps(
            google_map_link
    )
        except Exception:
            latitude = None
            longitude = None

        query = text("""
            INSERT INTO pending_homestays (

                homestay_name,
                owner_name,
                phone_number,
                situated_in,
                village_town_city,
                taluka_name,
                district_name,

                live_on_premises,
                unit_type,
                homestay_type,
                discoverable_google_map,
                photo_homestay,
                registered_mtdc,

                accept_bookings,
                booking_app,
                listed_booking_airbnb,
                photo_price_list,

                facilities_services,
                digital_payments_upi,
                cancellation_policy,
                veg_meals,
                both_veg_nonveg,

                tourist_attractions,
                guidance_provided,
                guides_available,
                local_experiences,

                social_media_page,
                amenities_photos,
                google_map_link,
                latitude,
                longitude

            )

            VALUES (

                :homestay_name,
                :owner_name,
                :phone_number,
                :situated_in,
                :village_town_city,
                :taluka_name,
                :district_name,

                :live_on_premises,
                :unit_type,
                :homestay_type,
                :discoverable_google_map,
                :photo_homestay,
                :registered_mtdc,

                :accept_bookings,
                :booking_app,
                :listed_booking_airbnb,
                :photo_price_list,

                :facilities_services,
                :digital_payments_upi,
                :cancellation_policy,
                :veg_meals,
                :both_veg_nonveg,

                :tourist_attractions,
                :guidance_provided,
                :guides_available,
                :local_experiences,

                :social_media_page,
                :amenities_photos,
                :google_map_link,
                :latitude,
                :longitude

            )
        """)

        db.session.execute(query, {

            "homestay_name": data.get("homestay_name"),
            "owner_name": data.get("owner_name"),
            "phone_number": data.get("phone_number"),
            "situated_in": data.get("situated_in"),
            "village_town_city": data.get("village_name"),
            "taluka_name": data.get("taluka_name"),
            "district_name": data.get("district_name", "Ratnagiri"),

            "live_on_premises": data.get("live_on_premises"),
            "unit_type": data.get("homestay_unit_type"),
            "homestay_type": data.get("homestay_type"),
            "discoverable_google_map": data.get("google_maps_discoverable"),
            "photo_homestay": data.get("site_photos"),
            "registered_mtdc": data.get("mtdc_registered"),

            "accept_bookings": data.get("booking_method"),
            "booking_app": data.get("booking_app_name"),
            "listed_booking_airbnb": data.get("listed_on_booking_platform"),
            "photo_price_list": data.get("price_list"),

            "facilities_services": data.get("facilities_services"),
            "digital_payments_upi": data.get("digital_payment"),
            "cancellation_policy": data.get("cancellation_policy"),
            "veg_meals": data.get("vegetarian_meals"),
            "both_veg_nonveg": data.get("non_vegetarian_meals"),

            "tourist_attractions": data.get("nearby_attractions"),
            "guidance_provided": data.get("guidance_available"),
            "guides_available": data.get("guides_available"),
            "local_experiences": data.get("local_experiences"),

            "social_media_page": data.get("social_media_link"),
            "amenities_photos": data.get("site_photos"),
            "google_map_link": data.get("google_maps_link"),
            "latitude": latitude,
            "longitude": longitude

        })
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Homestay submitted successfully. Waiting for admin approval."
        }), 201

    except Exception as e:

        db.session.rollback()

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route('/api/auth/google', methods=['POST'])
def google_login():

    try:

        data = request.get_json()

        credential = data.get("credential")

        if not credential:

            return jsonify({
                "success": False,
                "error": "Missing Google credential."
            }), 400

        idinfo = id_token.verify_oauth2_token(

            credential,
            grequests.Request(),
            "115345881365-6h9fghgg0gf2pohug60sqfoclult7lqv.apps.googleusercontent.com"

        )

        email = idinfo["email"]

        user = db.session.execute(

            text("""
                select
                    full_name,
                    email,
                    role
                from users
                where email = :email
            """),

            {
                "email": email
            }

        ).mappings().first()

        if user is None:

            return jsonify({

                "success": False,
                "error": "Access denied."

            }), 403

        return jsonify({

            "success": True,
            "name": user["full_name"],
            "email": user["email"],
            "role": user["role"]

        }), 200

    except Exception as e:

        return jsonify({

            "success": False,
            "error": str(e)

        }), 500


@bp.route('/api/location-photos/<path:location_name>', methods=['GET'])
def get_location_photos(location_name):

    print("REQUESTED LOCATION =", repr(location_name))

    try:

        folder_id = find_folder_by_name(location_name)

        print("FOLDER ID =", folder_id)

        if folder_id is None:
            return jsonify({
                "success": False,
                "error": "Folder not found."
            }), 404

        images = get_images_from_folder(folder_id)

        for photo in images:
            file_id = photo["url"].split("/")[-1]
            photo["url"] = request.host_url.rstrip("/") + "/api/photo/" + file_id

        return jsonify({
            "success": True,
            "photos": images
        }), 200

    except Exception as e:

        print(e)

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp.route("/api/photo/<file_id>")
def get_photo(file_id):
    try:
        image = get_image_bytes(file_id)

        return send_file(
            image,
            mimetype="image/jpeg",
            download_name=f"{file_id}.jpg"
        )

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
    
@bp.route('/api/talukas', methods=['GET'])
def get_talukas():
    try:
        result = db.session.execute(text("""
            SELECT DISTINCT taluka_name
            FROM locations
            WHERE taluka_name IS NOT NULL AND taluka_name <> ''
            UNION
            SELECT DISTINCT taluka_name
            FROM homestays
            WHERE taluka_name IS NOT NULL AND taluka_name <> ''
            ORDER BY taluka_name
        """)).all()
        return jsonify([row[0] for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/attraction-types', methods=['GET'])
def get_attraction_types():
    try:
        result = db.session.execute(text("""
            SELECT DISTINCT attraction_type
            FROM locations
            WHERE attraction_type IS NOT NULL AND attraction_type <> ''
            ORDER BY attraction_type
        """)).all()
        return jsonify([row[0] for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/homestay-types', methods=['GET'])
def get_homestay_types():
    try:
        result = db.session.execute(text("""
            SELECT DISTINCT homestay_type
            FROM homestays
            WHERE homestay_type IS NOT NULL AND homestay_type <> ''
            ORDER BY homestay_type
        """)).all()
        return jsonify([row[0] for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/upload-photo', methods=['POST'])
def upload_photo():
    try:
        if 'photo' not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400

        file = request.files['photo']
        result = cloudinary.uploader.upload(file)

        return jsonify({
            "success": True,
            "url": result['secure_url']
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500   