import re
import requests


def extract_coordinates_from_google_maps(short_url):
    try:
        response = requests.get(
            short_url,
            allow_redirects=True,
            timeout=20
        )

        final_url = response.url

        match = re.search(
            r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)',
            final_url
        )

        if match:
            latitude = float(match.group(1))
            longitude = float(match.group(2))

            return latitude, longitude

        return None, None

    except Exception as e:
        print(e)
        return None, None