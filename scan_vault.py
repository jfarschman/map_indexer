import sys
import re
import json
from pathlib import Path
from PIL import Image

# Prevent decompression bomb warnings on large battlemaps
Image.MAX_IMAGE_PIXELS = None

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
INDEX_PATH = Path("amber_map_index.json")
THUMB_DIR = Path("thumbnails")
FOUNDRY_REL_PREFIX = "modules/shadow-vault/images"


def extract_grid_size(filename: str, width: int) -> int:
    """Calculate grid size if dimensions like 25x25 exist in the filename."""
    match = re.search(r"(\d+)x(\d+)", filename, re.IGNORECASE)
    if match:
        cols = int(match.group(1))
        if cols > 0:
            return round(width / cols)
    return 100


def make_default_title(filename: str) -> str:
    """Turn 'whiskey_point_pirate_fort.jpg' into 'Whiskey Point Pirate Fort'."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem)
    return cleaned.strip().title()


def load_index() -> list:
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def ensure_schema(record: dict) -> dict:
    """Guarantee every record has the v2.0 controlled-vocabulary fields."""
    record.setdefault("curated", False)
    record.setdefault("eras", [])
    record.setdefault("environments", [])
    record.setdefault("structures", [])
    record.setdefault("features", [])
    record.setdefault("shadow_shift_cues", [])
    record.setdefault("summary", "")
    record.setdefault("grid_size", 100)
    return record


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scan_vault.py <path_to_foundry_images_folder>")
        sys.exit(1)

    source_dir = Path(sys.argv[1]).expanduser().resolve()
    if not source_dir.is_dir():
        print(f"Error: {source_dir} is not a valid directory.")
        sys.exit(1)

    THUMB_DIR.mkdir(exist_ok=True)

    existing_records = load_index()
    records_by_filename = {r["filename"]: ensure_schema(r) for r in existing_records if "filename" in r}

    disk_files = sorted(
        p for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
    )
    disk_filenames = {p.name for p in disk_files}

    added = 0
    thumbs_built = 0
    rotated = 0

    print(f"Scanning {len(disk_files)} images in {source_dir}...")

    for img_path in disk_files:
        filename = img_path.name
        thumb_filename = f"{img_path.stem}.jpg"
        thumb_path = THUMB_DIR / thumb_filename

        is_new = filename not in records_by_filename
        needs_thumb = not thumb_path.exists()

        # Only open the full-res image if it's new or missing its thumbnail
        if is_new or needs_thumb:
            try:
                with Image.open(img_path) as img:
                    width, height = img.size

                    # Standardize portrait maps to landscape
                    if height > width:
                        img = img.transpose(Image.Transpose.ROTATE_90)
                        img.save(img_path)
                        width, height = img.size
                        rotated += 1

                    # Generate local 500px JPEG thumbnail
                    if needs_thumb:
                        thumb_img = img.copy()
                        thumb_img.thumbnail((500, 500))
                        if thumb_img.mode in ("RGBA", "P"):
                            thumb_img = thumb_img.convert("RGB")
                        thumb_img.save(thumb_path, format="JPEG", quality=80)
                        thumbs_built += 1

                if is_new:
                    records_by_filename[filename] = {
                        "filename": filename,
                        "thumb_filename": thumb_filename,
                        "relative_path": f"{FOUNDRY_REL_PREFIX}/{filename}",
                        "title": make_default_title(filename),
                        "width": width,
                        "height": height,
                        "grid_size": extract_grid_size(filename, width),
                        "eras": [],
                        "environments": [],
                        "structures": [],
                        "summary": "",
                        "features": [],
                        "shadow_shift_cues": [],
                        "curated": False,
                    }
                    added += 1
                else:
                    records_by_filename[filename]["thumb_filename"] = thumb_filename
                    records_by_filename[filename]["width"] = width
                    records_by_filename[filename]["height"] = height

            except Exception as e:
                print(f"[!] Failed to process {filename}: {e}")

    # Audit for orphaned JSON entries whose files are no longer on disk
    orphans = [name for name in records_by_filename if name not in disk_filenames]

    # Save sorted by filename for clean Git diffs
    final_records = [records_by_filename[k] for k in sorted(records_by_filename.keys())]
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(final_records, f, indent=2)

    print("\n--- Scan Complete ---")
    print(f"Total maps in index : {len(final_records)}")
    print(f"New maps added      : {added}")
    print(f"Thumbnails built    : {thumbs_built}")
    print(f"Portrait rotated    : {rotated}")
    if orphans:
        print(f"[!] Orphaned entries (in JSON, missing on disk): {len(orphans)}")
        for o in orphans[:10]:
            print(f"    - {o}")
        if len(orphans) > 10:
            print(f"    ... and {len(orphans) - 10} more")


if __name__ == "__main__":
    main()