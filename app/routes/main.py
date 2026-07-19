from flask import Blueprint, jsonify, request, send_file
from sqlalchemy import text
from app import db

from google.oauth2 import id_token
from google.auth.transport import requests as grequests

from app.utils.map_utils import extract_coordinates_from_google_maps

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
                photo_location, latitude, longitude
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

        return jsonify({

            "locations": [dict(row) for row in locations],

            "homestays": [dict(row) for row in homestays],

            "eco": [dict(row) for row in eco],

            "drivers": [dict(row) for row in drivers]

        }), 200

    except Exception as e:

        return jsonify({

            "error": str(e)

        }), 500

@bp.route('/api/locations/register', methods=['POST'])
def register_location():
    try:
        data = request.get_json()
        google_maps_link = data.get("google_maps_link")

        try:
            latitude, longitude = extract_coordinates_from_google_maps(
            google_maps_link
    )
        except Exception:
            latitude = None
            longitude = None

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
            "photo_homestay": data.get("photo_location"),
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
            "amenities_photos": data.get("homestay_photos"),
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