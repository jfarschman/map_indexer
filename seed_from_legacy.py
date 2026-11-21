import json
from pathlib import Path

LEGACY_PATH = Path("legacy_amber_index.json")
CURRENT_PATH = Path("amber_map_index.json")


def normalize_features(val) -> list:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return []


def main():
    if not LEGACY_PATH.exists():
        print(f"Error: {LEGACY_PATH} not found.")
        return

    with open(LEGACY_PATH, "r", encoding="utf-8") as f:
        legacy_records = json.load(f)

    with open(CURRENT_PATH, "r", encoding="utf-8") as f:
        current_records = json.load(f)

    legacy_by_file = {r["filename"]: r for r in legacy_records if "filename" in r}

    matched = 0
    summaries_filled = 0
    features_filled = 0

    for rec in current_records:
        if rec.get("curated"):
            continue

        old = legacy_by_file.get(rec.get("filename"))
        if not old:
            continue

        matched += 1

        if not rec.get("summary") and old.get("summary"):
            rec["summary"] = old["summary"].strip()
            summaries_filled += 1

        if not rec.get("features") and old.get("features"):
            rec["features"] = normalize_features(old["features"])
            features_filled += 1

    with open(CURRENT_PATH, "w", encoding="utf-8") as f:
        json.dump(current_records, f, indent=2)

    print(f"Matched uncurated maps : {matched} / {len(current_records)}")
    print(f"Summaries pre-filled   : {summaries_filled}")
    print(f"Features pre-filled    : {features_filled}")


if __name__ == "__main__":
    main()
