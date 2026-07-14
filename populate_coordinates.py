from sqlalchemy import text

from app import create_app, db
from app.utils.map_utils import extract_coordinates_from_google_maps

app = create_app()

with app.app_context():

    # ---------------- Locations ----------------

    locations = db.session.execute(text("""
        SELECT id, google_maps_link
        FROM locations
        WHERE google_maps_link IS NOT NULL
          AND google_maps_link <> ''
    """)).mappings().all()

    print(f"Found {len(locations)} locations")

    for row in locations:

        try:

            lat, lon = extract_coordinates_from_google_maps(
                row["google_maps_link"]
            )

            db.session.execute(
                text("""
                    UPDATE locations
                    SET latitude=:lat,
                        longitude=:lon
                    WHERE id=:id
                """),
                {
                    "lat": lat,
                    "lon": lon,
                    "id": row["id"]
                }
            )

            print(f"Location {row['id']} -> {lat}, {lon}")

        except Exception as e:

            print(f"Location {row['id']} FAILED:", e)

    # ---------------- Homestays ----------------

    homestays = db.session.execute(text("""
        SELECT id, google_map_link
        FROM homestays
        WHERE google_map_link IS NOT NULL
          AND google_map_link <> ''
    """)).mappings().all()

    print(f"Found {len(homestays)} homestays")

    for row in homestays:

        try:

            lat, lon = extract_coordinates_from_google_maps(
                row["google_map_link"]
            )

            db.session.execute(
                text("""
                    UPDATE homestays
                    SET latitude=:lat,
                        longitude=:lon
                    WHERE id=:id
                """),
                {
                    "lat": lat,
                    "lon": lon,
                    "id": row["id"]
                }
            )

            print(f"Homestay {row['id']} -> {lat}, {lon}")

        except Exception as e:

            print(f"Homestay {row['id']} FAILED:", e)

    db.session.commit()

    print("DONE")