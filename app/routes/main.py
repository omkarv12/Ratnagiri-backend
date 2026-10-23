import os
import requests as http_requests
from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import text
from app import db
import json
from datetime import date
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


def _resolve_coordinates(data, maps_link):
    """Prefer the pin dropped on the form's map; fall back to parsing a Google Maps link."""
    lat, lng = data.get("latitude"), data.get("longitude")
    if lat not in (None, "") and lng not in (None, ""):
        return lat, lng
    try:
        return extract_coordinates_from_google_maps(maps_link)
    except Exception:
        return None, None


# Fields collected from the public that must never be returned by public endpoints.
PRIVATE_FIELDS = {
    "locations": {"phone_number", "email_address", "user_type", "suggestions_improvements", "job_type"},
    "homestays": {"email", "suggestions_improvements"},
    "drivers": {"email"},
}


def _public_rows(rows, kind):
    hidden = PRIVATE_FIELDS[kind]
    return [{k: v for k, v in dict(r).items() if k not in hidden} for r in rows]

EVENT_CATEGORIES = {"beaches", "forts", "temples", "waterfalls", "mangoes"}


# ==================================================================
# Events calendar (with photo support)
# ==================================================================

@bp.route('/api/events', methods=['GET'])
def list_events():
    """Approved events for one month: /api/events?year=2026&month=11"""
    try:
        year = int(request.args["year"])
        month = int(request.args["month"])
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
    except (KeyError, ValueError):
        return jsonify({"error": "year and month are required"}), 400

    try:
        result = db.session.execute(text("""
            SELECT id, title, event_date::text AS event_date, category,
                   organizer_name, description, photo_url
            FROM tourism_events
            WHERE status = 'approved'
              AND event_date >= :start AND event_date < :end
            ORDER BY event_date, id
        """), {"start": start, "end": end}).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/events/upcoming', methods=['GET'])
def upcoming_events():
    """Next approved events from today: /api/events/upcoming?limit=12"""
    try:
        try:
            limit = min(int(request.args.get("limit", 12)), 50)
        except ValueError:
            limit = 12

        result = db.session.execute(text("""
            SELECT id, title, event_date::text AS event_date, category,
                   organizer_name, description, photo_url
            FROM tourism_events
            WHERE status = 'approved' AND event_date >= CURRENT_DATE
            ORDER BY event_date, id
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/events/contribute', methods=['POST'])
def contribute_event():
    try:
        data = request.get_json(silent=True) or {}

        title = (data.get("title") or "").strip()
        event_date = (data.get("event_date") or "").strip()
        category = (data.get("category") or "").strip()
        organizer_name = (data.get("organizer_name") or "").strip()
        organizer_contact = (data.get("organizer_contact") or "").strip()

        if not (title and event_date and organizer_name and organizer_contact):
            return jsonify({"success": False, "error": "Please fill all required fields"}), 400
        if category not in EVENT_CATEGORIES:
            return jsonify({"success": False, "error": "Invalid category"}), 400
        try:
            date.fromisoformat(event_date)
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date"}), 400

        photo_url = (data.get("photo_url") or "").strip() or None
        if photo_url and not photo_url.startswith("https://"):
            return jsonify({"success": False, "error": "Invalid photo URL"}), 400

        result = db.session.execute(text("""
            INSERT INTO tourism_events
                (title, event_date, category, organizer_name,
                 organizer_contact, organizer_email, description, photo_url)
            VALUES
                (:title, :event_date, :category, :organizer_name,
                 :organizer_contact, :organizer_email, :description, :photo_url)
            RETURNING id
        """), {
            "title": title[:200],
            "event_date": event_date,
            "category": category,
            "organizer_name": organizer_name[:150],
            "organizer_contact": organizer_contact[:20],
            "organizer_email": (data.get("organizer_email") or "").strip()[:150] or None,
            "description": (data.get("description") or "").strip() or None,
            "photo_url": photo_url,
        })
        new_id = result.scalar()
        db.session.commit()

        return jsonify({"success": True, "id": new_id, "status": "pending"}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------- Admin: review contributions ----------------

@bp.route('/api/admin/events', methods=['GET'])
def admin_list_events():
    """/api/admin/events?status=pending  (pending | approved | rejected | all)"""
    try:
        status = request.args.get("status", "pending")
        result = db.session.execute(text("""
            SELECT id, title, event_date::text AS event_date, category,
                   organizer_name, organizer_contact, organizer_email,
                   description, photo_url, status, created_at
            FROM tourism_events
            WHERE (:status = 'all' OR status = :status)
            ORDER BY created_at DESC
        """), {"status": status}).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/admin/events/<int:id>/approve', methods=['POST'])
def admin_approve_event(id):
    try:
        result = db.session.execute(text("""
            UPDATE tourism_events
            SET status = 'approved', approved_at = NOW()
            WHERE id = :id
        """), {"id": id})
        db.session.commit()

        if result.rowcount == 0:
            return jsonify({"success": False, "error": "Event not found."}), 404
        return jsonify({"success": True, "message": "Event approved."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/events/<int:id>/reject', methods=['POST'])
def admin_reject_event(id):
    try:
        db.session.execute(
            text("DELETE FROM tourism_events WHERE id = :id"), {"id": id}
        )
        db.session.commit()
        return jsonify({"success": True, "message": "Event rejected and removed."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ==================================================================
# Core routes


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

@bp.route('/api/village-profiles', methods=['GET'])
def list_village_profiles():
    try:
        result = db.session.execute(text("""
            SELECT taluka_name, village_name, tagline, hero_image
            FROM village_profiles
            ORDER BY taluka_name, village_name
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/api/village-profile/<taluka>/<village>', methods=['GET'])
def get_village_profile(taluka, village):
    try:
        row = db.session.execute(text("""
            SELECT * FROM village_profiles
            WHERE taluka_name = :taluka AND village_name = :village
        """), {"taluka": taluka, "village": village}).mappings().first()

        if row is None:
            return jsonify({"success": True, "profile": None}), 200

        return jsonify({"success": True, "profile": dict(row)}), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/village-profiles', methods=['GET'])
def admin_list_village_profiles():
    try:
        result = db.session.execute(text("""
            SELECT id, taluka_name, village_name, tagline, updated_at
            FROM village_profiles ORDER BY taluka_name, village_name
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/api/admin/village-profile/<taluka>/<village>', methods=['DELETE'])
def admin_delete_village_profile(taluka, village):
    try:
        result = db.session.execute(text("""
            DELETE FROM village_profiles
            WHERE taluka_name = :taluka AND village_name = :village
        """), {"taluka": taluka, "village": village})
        db.session.commit()

        if result.rowcount == 0:
            return jsonify({"success": False, "error": "Profile not found."}), 404

        return jsonify({"success": True, "message": "Village profile deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
        

@bp.route('/api/admin/village-profile', methods=['POST'])
def admin_save_village_profile():
    try:
        data = request.get_json()
        taluka = (data.get("taluka_name") or "").strip()
        village = (data.get("village_name") or "").strip()

        if not taluka or not village:
            return jsonify({"success": False, "error": "Taluka and village name are required."}), 400

        params = {
            "taluka_name": taluka,
            "village_name": village,
            "tagline": data.get("tagline"),
            "intro_text": data.get("intro_text"),
            "about_text": data.get("about_text"),
            "hero_image": data.get("hero_image"),
            "top_attractions": json.dumps(data.get("top_attractions", [])),
            "experiences": json.dumps(data.get("experiences", [])),
            "services": json.dumps(data.get("services", [])),
            "how_to_reach_road": data.get("how_to_reach_road"),
            "how_to_reach_rail": data.get("how_to_reach_rail"),
            "how_to_reach_air": data.get("how_to_reach_air"),
            "best_time_to_visit": data.get("best_time_to_visit"),
            "sustainability_tips": json.dumps(data.get("sustainability_tips", [])),
            "stay_options_text": data.get("stay_options_text"),
            "stay_options_image": data.get("stay_options_image"),
            "hashtags": data.get("hashtags"),
        }

        db.session.execute(text("""
            INSERT INTO village_profiles (
                taluka_name, village_name, tagline, intro_text, about_text, hero_image,
                top_attractions, experiences, services, how_to_reach_road, how_to_reach_rail,
                how_to_reach_air, best_time_to_visit, sustainability_tips,
                stay_options_text, stay_options_image, hashtags
            ) VALUES (
                :taluka_name, :village_name, :tagline, :intro_text, :about_text, :hero_image,
                :top_attractions, :experiences, :services, :how_to_reach_road, :how_to_reach_rail,
                :how_to_reach_air, :best_time_to_visit, :sustainability_tips,
                :stay_options_text, :stay_options_image, :hashtags
            )
            ON CONFLICT (taluka_name, village_name) DO UPDATE SET
                tagline = EXCLUDED.tagline,
                intro_text = EXCLUDED.intro_text,
                about_text = EXCLUDED.about_text,
                hero_image = EXCLUDED.hero_image,
                top_attractions = EXCLUDED.top_attractions,
                experiences = EXCLUDED.experiences,
                services = EXCLUDED.services,
                how_to_reach_road = EXCLUDED.how_to_reach_road,
                how_to_reach_rail = EXCLUDED.how_to_reach_rail,
                how_to_reach_air = EXCLUDED.how_to_reach_air,
                best_time_to_visit = EXCLUDED.best_time_to_visit,
                sustainability_tips = EXCLUDED.sustainability_tips,
                stay_options_text = EXCLUDED.stay_options_text,
                stay_options_image = EXCLUDED.stay_options_image,
                hashtags = EXCLUDED.hashtags,
                updated_at = NOW()
        """), params)
        db.session.commit()

        return jsonify({"success": True}), 200

    except Exception as e:
        db.session.rollback()
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

        return jsonify(_public_rows(result, "homestays")), 200
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
        latitude, longitude = _resolve_coordinates(data, google_maps_link)

        query = text("""
            INSERT INTO pending_drivers (
                driver_name, phone_number, vehicle_type, vehicle_number,
                base_village, taluka_name, district_name, service_area,
                per_day_rate, google_maps_link, latitude, longitude,
                vehicle_photos, driver_photo, vehicle_photo, number_plate_photo, email
            )
            VALUES (
                :driver_name, :phone_number, :vehicle_type, :vehicle_number,
                :base_village, :taluka_name, :district_name, :service_area,
                :per_day_rate, :google_maps_link, :latitude, :longitude,
                :vehicle_photos, :driver_photo, :vehicle_photo, :number_plate_photo, :email
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
            "vehicle_photos": data.get("vehicle_photo") or data.get("vehicle_photos"),
            "driver_photo": data.get("driver_photo"),
            "vehicle_photo": data.get("vehicle_photo"),
            "number_plate_photo": data.get("number_plate_photo"),
            "email": data.get("email"),
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

        # _rows() returns [] if the atms table is missing or the query fails,
        # so a problem with ATMs can never break the whole dashboard.
        atms = _rows("SELECT * FROM atms ORDER BY bank_name, atm_name")

        return jsonify({

            "locations": _public_rows(locations, "locations"),

            "homestays": _public_rows(homestays, "homestays"),

            "eco": [dict(row) for row in eco],

            "drivers": _public_rows(drivers, "drivers"),

            "bus_stops": [dict(row) for row in bus_stops],

            "atms": [dict(row) for row in atms]

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
        site_photos,

        phone_number,
        user_type,
        is_crowded,
        site_activity_details_doc

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
        :site_photos,

        :phone_number,
        :user_type,
        :is_crowded,
        :site_activity_details_doc

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
    "photo_location": data.get("header_photo") or data.get("photo_location"),
    "site_photos": data.get("site_photos"),

    # Submitter details (private - never returned by public endpoints)
    "phone_number": data.get("phone_number"),
    "user_type": data.get("user_type"),
    "is_crowded": data.get("is_crowded"),
    "site_activity_details_doc": data.get("site_activity_details_doc")

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
        latitude, longitude = _resolve_coordinates(data, google_map_link)

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
                longitude,

                email,
                homestay_location,
                room_type,
                price_range,
                check_in_out_time,
                activity_details_doc,
                suggestions_improvements

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
                :longitude,

                :email,
                :homestay_location,
                :room_type,
                :price_range,
                :check_in_out_time,
                :activity_details_doc,
                :suggestions_improvements

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
            "photo_homestay": data.get("header_photo"),
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
            "amenities_photos": data.get("site_photos"),  # gallery (comma-separated URLs)
            "google_map_link": data.get("google_maps_link"),
            "latitude": latitude,
            "longitude": longitude,

            "email": data.get("email"),
            "homestay_location": data.get("homestay_location"),
            "room_type": data.get("room_type"),
            "price_range": data.get("price_range"),
            "check_in_out_time": data.get("check_in_out_time"),
            "activity_details_doc": data.get("activity_details_doc"),
            "suggestions_improvements": data.get("suggestions_improvements")

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


# ==================================================================
# Admin analytics (one call powers the whole analytics dashboard)
# ==================================================================

def _rows(sql, params=None):
    """Run a query and return list[dict]; a failing query never breaks the rest."""
    try:
        res = db.session.execute(text(sql), params or {}).mappings().all()
        return [dict(r) for r in res]
    except Exception as e:
        print("ROWS ERROR:", e)
        db.session.rollback()
        return []


def _scalar(sql, params=None):
    try:
        return db.session.execute(text(sql), params or {}).scalar() or 0
    except Exception:
        db.session.rollback()
        return 0


def _one(sql):
    rows = _rows(sql)
    return rows[0] if rows else {}


@bp.route('/api/admin/analytics', methods=['GET'])
def admin_analytics():
    try:
        totals = {
            "locations": _scalar("SELECT COUNT(*) FROM locations"),
            "homestays": _scalar("SELECT COUNT(*) FROM homestays"),
            "drivers": _scalar("SELECT COUNT(*) FROM drivers"),
            "bus_stops": _scalar("SELECT COUNT(*) FROM bus_stops"),
            "eco_water": _scalar("SELECT COUNT(*) FROM eco_and_water"),
            "village_profiles": _scalar("SELECT COUNT(*) FROM village_profiles"),
            "stories_published": _scalar("SELECT COUNT(*) FROM blogs WHERE status = 'published'"),
            "events_approved": _scalar("SELECT COUNT(*) FROM tourism_events WHERE status = 'approved'"),
        }

        pending = {
            "locations": _scalar("SELECT COUNT(*) FROM pending_locations"),
            "homestays": _scalar("SELECT COUNT(*) FROM pending_homestays"),
            "drivers": _scalar("SELECT COUNT(*) FROM pending_drivers"),
            "events": _scalar("SELECT COUNT(*) FROM tourism_events WHERE status = 'pending'"),
        }

        locations_by_taluka = _rows("""
            SELECT COALESCE(NULLIF(taluka_name, ''), 'Unknown') AS label, COUNT(*) AS value
            FROM locations GROUP BY 1 ORDER BY value DESC
        """)
        homestays_by_taluka = _rows("""
            SELECT COALESCE(NULLIF(taluka_name, ''), 'Unknown') AS label, COUNT(*) AS value
            FROM homestays GROUP BY 1 ORDER BY value DESC
        """)
        locations_by_type = _rows("""
            SELECT COALESCE(NULLIF(attraction_type, ''), 'Other') AS label, COUNT(*) AS value
            FROM locations GROUP BY 1 ORDER BY value DESC
        """)
        homestays_by_type = _rows("""
            SELECT COALESCE(NULLIF(homestay_type, ''), 'Other') AS label, COUNT(*) AS value
            FROM homestays GROUP BY 1 ORDER BY value DESC
        """)
        road_condition = _rows("""
            SELECT COALESCE(NULLIF(road_condition::text, ''), 'Not stated') AS label, COUNT(*) AS value
            FROM locations GROUP BY 1 ORDER BY value DESC
        """)

        location_readiness = _one("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (
                       WHERE COALESCE(site_photos::text, '') NOT IN ('', '{}', '[]')
                   ) AS with_photos,
                   COUNT(*) FILTER (
                       WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                   ) AS with_coordinates,
                   COUNT(*) FILTER (
                       WHERE signboards_available::text ILIKE 'y%'
                   ) AS with_signboards
            FROM locations
        """)

        homestay_readiness = _one("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE registered_mtdc::text ILIKE 'y%') AS mtdc_registered,
                   COUNT(*) FILTER (
                       WHERE digital_payments_upi::text ILIKE 'y%'
                          OR digital_payments_upi::text ILIKE '%upi%'
                   ) AS digital_payments,
                   COUNT(*) FILTER (WHERE listed_booking_airbnb::text ILIKE 'y%') AS listed_online
            FROM homestays
        """)

        events_by_month = _rows("""
            SELECT to_char(g.m, 'Mon') AS label, COUNT(e.id) AS value
            FROM generate_series(
                date_trunc('month', CURRENT_DATE),
                date_trunc('month', CURRENT_DATE) + interval '5 months',
                interval '1 month'
            ) AS g(m)
            LEFT JOIN tourism_events e
                   ON date_trunc('month', e.event_date) = g.m
                  AND e.status = 'approved'
            GROUP BY g.m ORDER BY g.m
        """)
        events_by_category = _rows("""
            SELECT category AS label, COUNT(*) AS value
            FROM tourism_events WHERE status = 'approved'
            GROUP BY category ORDER BY value DESC
        """)

        stories = {
            "published": totals["stories_published"],
            "drafts": _scalar("SELECT COUNT(*) FROM blogs WHERE status <> 'published'"),
            "total_views": _scalar("SELECT COALESCE(SUM(views), 0) FROM blogs WHERE status = 'published'"),
            "comments": _scalar("SELECT COUNT(*) FROM blog_comments"),
            "top": _rows("""
                SELECT title AS label, COALESCE(views, 0) AS value
                FROM blogs WHERE status = 'published'
                ORDER BY views DESC NULLS LAST LIMIT 5
            """),
        }

        return jsonify({
            "totals": totals,
            "pending": pending,
            "locations_by_taluka": locations_by_taluka,
            "homestays_by_taluka": homestays_by_taluka,
            "locations_by_type": locations_by_type,
            "homestays_by_type": homestays_by_type,
            "road_condition": road_condition,
            "location_readiness": location_readiness,
            "homestay_readiness": homestay_readiness,
            "events_by_month": events_by_month,
            "events_by_category": events_by_category,
            "stories": stories,
        }), 200

    except Exception as e:
        db.session.rollback()
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


# ==================================================================
# Medical facilities  (NEW)
# Requires medical_facilities.sql to have been run on the database first.
# ==================================================================

@bp.route('/api/medical-facilities', methods=['GET'])
def list_medical_facilities():
    """
    Optional query params:
      category   hospital | clinic | diagnostic | pharmacy | blood_bank | ambulance
      specialty  exact specialty text
      lat, lng   visitor position; adds distance_km and sorts nearest first
    """
    try:
        params = {
            "category": request.args.get("category") or None,
            "specialty": request.args.get("specialty") or None,
            "lat": request.args.get("lat", type=float),
            "lng": request.args.get("lng", type=float),
        }

        rows = db.session.execute(text("""
            SELECT
                f.id, f.slug, f.name, f.name_mr, f.category, f.specialty, f.ownership,
                f.description, f.features,
                f.address_line AS address, f.landmark, f.area, f.taluka, f.district,
                f.state, f.pincode,
                f.lat::float8 AS lat, f.lng::float8 AS lng, f.map_url,
                f.phone_primary AS phone, f.phone_secondary, f.emergency_phone,
                f.whatsapp, f.email, f.website,
                f.opening_hours_note, f.is_24x7, f.emergency_available,
                f.ambulance_available, f.icu_available, f.bed_count,
                f.appointment_required, f.walk_in_allowed, f.teleconsultation,
                f.home_service, f.wheelchair_accessible, f.hearing_loop,
                f.parking_available, f.accepts_upi, f.accepts_cards,
                f.cashless_insurance, f.govt_schemes, f.is_free,
                f.consultation_fee_min::float8 AS consultation_fee_min,
                f.consultation_fee_max::float8 AS consultation_fee_max,
                f.languages, f.registration_no, f.accreditations, f.cover_image_url,
                f.rating_avg::float8 AS rating_avg, f.rating_count,
                f.is_verified, f.last_verified::text AS last_verified,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'day', h.day_of_week,
                               'opens', to_char(h.opens, 'HH24:MI'),
                               'closes', to_char(h.closes, 'HH24:MI'),
                               'is_24h', h.is_24h,
                               'is_closed', h.is_closed)
                           ORDER BY h.day_of_week, h.opens)
                    FROM facility_hours h WHERE h.facility_id = f.id
                ), '[]'::json) AS hours,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'name', s.name, 'department', s.department, 'notes', s.notes)
                           ORDER BY s.sort_order, s.name)
                    FROM facility_services s WHERE s.facility_id = f.id
                ), '[]'::json) AS services,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'name', d.full_name, 'qualification', d.qualification,
                               'specialization', d.specialization,
                               'experience_years', d.experience_years,
                               'available_days', d.available_days,
                               'timing', d.timing_note,
                               'fee', d.consultation_fee::float8)
                           ORDER BY d.full_name)
                    FROM facility_doctors d WHERE d.facility_id = f.id AND d.is_active
                ), '[]'::json) AS doctors,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'url', i.url, 'caption', i.caption, 'is_cover', i.is_cover)
                           ORDER BY i.sort_order)
                    FROM facility_images i WHERE i.facility_id = f.id
                ), '[]'::json) AS images,

                haversine_km(CAST(:lat AS float8), CAST(:lng AS float8),
                             f.lat::float8, f.lng::float8) AS distance_km
            FROM medical_facilities f
            WHERE f.is_active
              AND (CAST(:category AS text) IS NULL OR f.category = CAST(:category AS text))
              AND (CAST(:specialty AS text) IS NULL OR f.specialty = CAST(:specialty AS text))
            ORDER BY distance_km NULLS LAST, f.name
        """), params).mappings().all()

        return jsonify([dict(row) for row in rows]), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==================================================================
# Police stations  (NEW)
# Requires police_stations.sql to have been run on the database first.
# ==================================================================

@bp.route('/api/police-stations', methods=['GET'])
def list_police_stations():
    """
    Optional query params:
      category   station | district_office | cyber | headquarters |
                 women_cell | traffic | outpost
      lat, lng   visitor position; adds distance_km and sorts nearest first
    """
    try:
        params = {
            "category": request.args.get("category") or None,
            "lat": request.args.get("lat", type=float),
            "lng": request.args.get("lng", type=float),
        }

        rows = db.session.execute(text("""
            SELECT
                p.id, p.slug, p.name, p.name_mr, p.category, p.unit_type,
                p.description, p.features, p.jurisdiction,
                p.address_line AS address, p.landmark, p.area, p.taluka, p.district,
                p.state, p.pincode,
                p.lat::float8 AS lat, p.lng::float8 AS lng, p.map_url,
                p.phone_primary AS phone, p.phone_secondary, p.emergency_phone,
                p.whatsapp, p.email, p.website, p.online_complaint_url,
                p.opening_hours_note, p.is_24x7, p.files_fir, p.online_complaints,
                p.women_help_desk, p.tourist_help, p.lost_property_reports,
                p.wheelchair_accessible, p.accessible_restrooms, p.parking_available,
                p.languages, p.cover_image_url,
                p.is_verified, p.last_verified::text AS last_verified,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'day', h.day_of_week,
                               'opens', to_char(h.opens, 'HH24:MI'),
                               'closes', to_char(h.closes, 'HH24:MI'),
                               'is_24h', h.is_24h,
                               'is_closed', h.is_closed)
                           ORDER BY h.day_of_week, h.opens)
                    FROM police_station_hours h WHERE h.station_id = p.id
                ), '[]'::json) AS hours,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'name', s.name, 'notes', s.notes)
                           ORDER BY s.sort_order, s.name)
                    FROM police_station_services s WHERE s.station_id = p.id
                ), '[]'::json) AS services,

                COALESCE((
                    SELECT json_agg(json_build_object(
                               'url', i.url, 'caption', i.caption, 'is_cover', i.is_cover)
                           ORDER BY i.sort_order)
                    FROM police_station_images i WHERE i.station_id = p.id
                ), '[]'::json) AS images,

                haversine_km(CAST(:lat AS float8), CAST(:lng AS float8),
                             p.lat::float8, p.lng::float8) AS distance_km
            FROM police_stations p
            WHERE p.is_active
              AND (CAST(:category AS text) IS NULL OR p.category = CAST(:category AS text))
            ORDER BY distance_km NULLS LAST, p.name
        """), params).mappings().all()

        return jsonify([dict(row) for row in rows]), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==================================================================
# ATMs
# Requires atms.sql to have been run on the database first.
# ==================================================================

@bp.route('/api/atms/register', methods=['POST'])
def register_atm():
    try:
        data = request.get_json() or {}

        atm_name = (data.get("atm_name") or "").strip()
        bank_name = (data.get("bank_name") or "").strip()
        if not atm_name or not bank_name:
            return jsonify({"success": False, "error": "ATM name and bank are required."}), 400

        google_maps_link = data.get("google_maps_link")
        latitude, longitude = _resolve_coordinates(data, google_maps_link)
        if latitude is None or longitude is None:
            return jsonify({"success": False, "error": "Location coordinates are required."}), 400

        photo_url = (data.get("photo_url") or "").strip() or None
        if photo_url and not photo_url.startswith("https://"):
            return jsonify({"success": False, "error": "Invalid photo URL"}), 400

        db.session.execute(text("""
            INSERT INTO atms (
                atm_name, bank_name, village_name, taluka_name, district_name,
                address, google_maps_link, latitude, longitude,
                is_24x7, has_cash_deposit, photo_url
            )
            VALUES (
                :atm_name, :bank_name, :village_name, :taluka_name, :district_name,
                :address, :google_maps_link, :latitude, :longitude,
                :is_24x7, :has_cash_deposit, :photo_url
            )
        """), {
            "atm_name": atm_name[:200],
            "bank_name": bank_name[:150],
            "village_name": data.get("village_name"),
            "taluka_name": data.get("taluka_name"),
            "district_name": data.get("district_name", "Ratnagiri"),
            "address": data.get("address"),
            "google_maps_link": google_maps_link,
            "latitude": latitude,
            "longitude": longitude,
            "is_24x7": bool(data.get("is_24x7", True)),
            "has_cash_deposit": bool(data.get("has_cash_deposit", False)),
            "photo_url": photo_url,
        })
        db.session.commit()

        return jsonify({"success": True, "message": "ATM added successfully."}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/atms', methods=['GET'])
def get_atms():
    try:
        result = db.session.execute(text("""
            SELECT * FROM atms ORDER BY bank_name, atm_name
        """)).mappings().all()
        return jsonify([dict(row) for row in result]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/api/atms/<int:id>', methods=['DELETE'])
def delete_atm(id):
    try:
        result = db.session.execute(text("DELETE FROM atms WHERE id = :id"), {"id": id})
        db.session.commit()
        if result.rowcount == 0:
            return jsonify({"success": False, "error": "ATM not found."}), 404
        return jsonify({"success": True, "message": "ATM deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ==================================================================
# Admin data manager: view / edit / delete / add live records
#
# PASTE THIS AT THE END OF YOUR ROUTES FILE (the one that defines `bp`,
# `_rows` and `_resolve_coordinates`). It needs no new imports beyond the
# ones that file already has (json, text, db, request, jsonify).
#
# Table names come from the fixed ADMIN_TABLES list and column names are
# checked against the real table columns, so nothing from the request is
# ever put into the SQL text.
# ==================================================================

ADMIN_TABLES = {
    "locations": "locations",
    "homestays": "homestays",
    "drivers": "drivers",
    "bus_stops": "bus_stops",
    "atms": "atms",
    "pending_locations": "pending_locations",
    "pending_homestays": "pending_homestays",
}

# Kinds that can be created from the admin panel, with their required fields.
ADMIN_CREATABLE = {
    "bus_stops": ("stop_name",),
    "atms": ("atm_name", "bank_name"),
}

_ADMIN_READONLY_COLUMNS = {
    "id", "created_at", "updated_at", "submitted_at", "reviewed_at",
    "reviewed_by", "approved_at",
}
_ADMIN_TIME_TYPES = {
    "timestamp without time zone", "timestamp with time zone", "date",
    "time without time zone", "time with time zone",
}
_ADMIN_NUMERIC_TYPES = {
    "integer", "bigint", "smallint", "numeric", "double precision", "real",
}


def _admin_table_columns(table):
    rows = db.session.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = :t
        ORDER BY ordinal_position
    """), {"t": table}).all()
    return {r[0]: r[1] for r in rows}


def _admin_coerce(value, data_type):
    """Turn a value from the admin form into something the column accepts."""
    if data_type == "ARRAY":
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return None
        return [p.strip() for p in str(value).split(",") if p.strip()]
    if data_type == "boolean":
        if isinstance(value, bool) or value is None:
            return value
        s = str(value).strip().lower()
        if s == "":
            return None
        return s in ("true", "t", "1", "yes", "y")
    if data_type in ("json", "jsonb"):
        if value in (None, ""):
            return None
        return value if isinstance(value, str) else json.dumps(value)
    if data_type in _ADMIN_NUMERIC_TYPES:
        return None if value in (None, "") else value
    return value


def _admin_check_coordinates(data):
    """Return an error message if latitude / longitude are present but invalid."""
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return f"{key.capitalize()} must be a number."
        if not (low <= number <= high):
            return f"{key.capitalize()} is out of range."
    return None


def _admin_editable(cols, key):
    return (
        key in cols
        and key not in _ADMIN_READONLY_COLUMNS
        and cols[key] not in _ADMIN_TIME_TYPES
    )


@bp.route('/api/admin/data/<kind>', methods=['GET'])
def admin_data_list(kind):
    table = ADMIN_TABLES.get(kind)
    if not table:
        return jsonify({"error": "Unknown data type."}), 404
    try:
        rows = db.session.execute(
            text(f"SELECT * FROM {table} ORDER BY id DESC")
        ).mappings().all()
        return jsonify([dict(r) for r in rows]), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/admin/data/<kind>/<int:id>', methods=['PUT'])
def admin_data_update(kind, id):
    table = ADMIN_TABLES.get(kind)
    if not table:
        return jsonify({"error": "Unknown data type."}), 404
    try:
        data = request.get_json(silent=True) or {}

        problem = _admin_check_coordinates(data)
        if problem:
            return jsonify({"success": False, "error": problem}), 400

        cols = _admin_table_columns(table)
        sets, params = [], {"id": id}
        for i, (key, value) in enumerate(data.items()):
            if not _admin_editable(cols, key):
                continue
            sets.append(f'"{key}" = :p{i}')
            params[f"p{i}"] = _admin_coerce(value, cols[key])

        if not sets:
            return jsonify({"success": False, "error": "Nothing to update."}), 400

        row = db.session.execute(
            text(f"UPDATE {table} SET {', '.join(sets)} WHERE id = :id RETURNING *"),
            params,
        ).mappings().first()

        if row is None:
            db.session.rollback()
            return jsonify({"success": False, "error": "Record not found."}), 404

        db.session.commit()
        return jsonify(dict(row)), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/data/<kind>/<int:id>', methods=['DELETE'])
def admin_data_delete(kind, id):
    table = ADMIN_TABLES.get(kind)
    if not table:
        return jsonify({"error": "Unknown data type."}), 404
    try:
        result = db.session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": id})
        db.session.commit()
        if result.rowcount == 0:
            return jsonify({"success": False, "error": "Record not found."}), 404
        return jsonify({"success": True, "message": "Deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/admin/data/<kind>', methods=['POST'])
def admin_data_create(kind):
    table = ADMIN_TABLES.get(kind)
    required = ADMIN_CREATABLE.get(kind)
    if not table or required is None:
        return jsonify({"success": False, "error": "This type can't be added here."}), 400
    try:
        data = request.get_json(silent=True) or {}

        for field in required:
            if not str(data.get(field) or "").strip():
                return jsonify({"success": False, "error": f"{field.replace('_', ' ').capitalize()} is required."}), 400

        problem = _admin_check_coordinates(data)
        if problem:
            return jsonify({"success": False, "error": problem}), 400

        latitude, longitude = _resolve_coordinates(data, data.get("google_maps_link"))
        if latitude in (None, "") or longitude in (None, ""):
            return jsonify({"success": False, "error": "Location coordinates are required."}), 400
        data["latitude"], data["longitude"] = latitude, longitude

        data.setdefault("district_name", "Ratnagiri")
        if kind == "atms":
            data.setdefault("is_24x7", True)
            data.setdefault("has_cash_deposit", False)
            photo = (data.get("photo_url") or "").strip()
            if photo and not photo.startswith("https://"):
                return jsonify({"success": False, "error": "Photo URL must start with https://"}), 400

        cols = _admin_table_columns(table)
        column_parts, value_parts, params = [], [], {}
        for i, (key, value) in enumerate(data.items()):
            if not _admin_editable(cols, key):
                continue
            column_parts.append(f'"{key}"')
            value_parts.append(f":p{i}")
            params[f"p{i}"] = _admin_coerce(value, cols[key])

        columns_sql = ", ".join(column_parts)
        placeholders = ", ".join(value_parts)

        row = db.session.execute(
            text(f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders}) RETURNING *"),
            params,
        ).mappings().first()
        db.session.commit()

        return jsonify(dict(row)), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# The Pending Submissions tab already calls these to save an edit, but the
# routes did not exist. They reuse the same updater and return the updated row.

@bp.route('/api/pending_locations/<int:id>', methods=['PUT'])
def update_pending_location(id):
    return admin_data_update("pending_locations", id)


@bp.route('/api/pending_homestays/<int:id>', methods=['PUT'])
def update_pending_homestay(id):
    return admin_data_update("pending_homestays", id)