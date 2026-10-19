#!/usr/bin/env python3
"""
Build src/data/ratnagiriVillages.json from the official LGD villages CSV.

Usage:
    python build_villages.py lgd_villages.csv src/data/ratnagiriVillages.json

The CSV can be "All Villages of a District" (Maharashtra > Ratnagiri) or
"All Villages of a State" - rows for other districts are ignored.
Save the file as CSV first if you downloaded Excel (.xls/.xlsx).

Output shape:
    { "Chiplun": ["Village A", "Village B", ...], "Dapoli": [...], ... }
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DISTRICT = "ratnagiri"

# The 9 talukas of Ratnagiri district, spelled the way your app uses them.
TALUKAS = [
    "Mandangad", "Dapoli", "Khed", "Chiplun", "Guhagar",
    "Sangameshwar", "Ratnagiri", "Lanja", "Rajapur",
]

# LGD spellings that differ from the app's spelling (lowercase keys).
ALIASES = {
    "sangmeshwar": "Sangameshwar",
    "sangameshwar": "Sangameshwar",
    "sangmeshwar tehsil": "Sangameshwar",
    "mandangarh": "Mandangad",
    "guhaghar": "Guhagar",
}


def normalise_taluka(raw):
    key = re.sub(r"\b(taluka|tehsil|sub-?district)\b", "", raw.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    if key in ALIASES:
        return ALIASES[key]
    for t in TALUKAS:
        if t.lower() == key:
            return t
    return None


def clean_village(raw):
    name = re.sub(r"\s+", " ", raw).strip()
    return name


def find_col(headers, must, must_not=()):
    for i, h in enumerate(headers):
        low = h.lower()
        if all(m in low for m in must) and not any(n in low for n in must_not):
            return i
    return None


def read_rows(path):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.reader(f))
        except UnicodeDecodeError:
            continue
    raise SystemExit("Could not read the file as UTF-8 or Windows-1252 text.")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, dest = Path(sys.argv[1]), Path(sys.argv[2])

    rows = read_rows(src)

    # LGD files sometimes have title rows above the header: find the real one.
    header_idx = next(
        (i for i, r in enumerate(rows) if any("village" in c.lower() for c in r)
         and any("district" in c.lower() for c in r)),
        None,
    )
    if header_idx is None:
        raise SystemExit("Could not find a header row with village and district columns.")

    headers = rows[header_idx]
    col_village = find_col(headers, ["village", "name"], must_not=["code", "local"])
    col_sub = find_col(headers, ["sub", "district", "name"], must_not=["code"])
    col_district = find_col(headers, ["district", "name"], must_not=["sub", "code"])

    if None in (col_village, col_sub, col_district):
        raise SystemExit(f"Could not match the columns. Headers found: {headers}")

    result = defaultdict(set)
    unknown = defaultdict(int)

    for r in rows[header_idx + 1:]:
        if len(r) <= max(col_village, col_sub, col_district):
            continue
        if DISTRICT not in r[col_district].lower():
            continue
        village = clean_village(r[col_village])
        if not village:
            continue
        taluka = normalise_taluka(r[col_sub])
        if taluka is None:
            unknown[r[col_sub].strip()] += 1
            continue
        result[taluka].add(village)

    output = {t: sorted(result.get(t, []), key=str.lower) for t in TALUKAS}

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {dest}")
    for t in TALUKAS:
        print(f"  {t:<14} {len(output[t]):>4} villages")
    print(f"  {'TOTAL':<14} {sum(len(v) for v in output.values()):>4}")
    if unknown:
        print("\nSub-district names I did not recognise (add them to ALIASES):")
        for name, count in unknown.items():
            print(f"  '{name}' ({count} rows)")


if __name__ == "__main__":
    main()
