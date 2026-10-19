"""
Flask blueprint for the Medical Facilities page.

Register it in your app:
    from medical_facilities_api import bp as medical_facilities_bp
    app.register_blueprint(medical_facilities_bp)

Adjust get_conn() to however your app already connects to Postgres.

Query params (all optional):
    category   hospital | clinic | diagnostic | pharmacy | blood_bank | ambulance
    specialty  exact specialty text
    lat, lng   visitor position; adds distance_km and sorts nearest first
"""
from flask import Blueprint, jsonify, request
import psycopg2
import psycopg2.extras
import os

bp = Blueprint("medical_facilities", __name__)


def get_conn():
    # Replace with your existing connection helper if you have one.
    return psycopg2.connect(os.environ["DATABASE_URL"])


SELECT_COLUMNS = """
    f.id, f.slug, f.name, f.name_mr, f.category, f.specialty, f.ownership,
    f.description, f.features,
    -- 'address' and 'phone' keep the names the React page already uses
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
    f.is_verified, f.last_verified,

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
    ), '[]'::json) AS images
"""


@bp.route("/api/medical-facilities", methods=["GET"])
def list_medical_facilities():
    category = request.args.get("category")
    specialty = request.args.get("specialty")
    lat = request.args.get("lat", type=float)
    lng = request.args.get("lng", type=float)

    columns = SELECT_COLUMNS
    params = []

    if lat is not None and lng is not None:
        columns += ", haversine_km(%s, %s, f.lat::float8, f.lng::float8) AS distance_km"
        params += [lat, lng]
        order_by = "distance_km, f.name"
    else:
        order_by = "f.name"

    sql = f"SELECT {columns} FROM medical_facilities f WHERE f.is_active"
    if category:
        sql += " AND f.category = %s"
        params.append(category)
    if specialty:
        sql += " AND f.specialty = %s"
        params.append(specialty)
    sql += f" ORDER BY {order_by}"

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    for r in rows:
        if r["last_verified"]:
            r["last_verified"] = r["last_verified"].isoformat()
    return jsonify(rows)
