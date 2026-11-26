import argparse
import difflib
import io
import json
import random
import re
import sys
import time
from enum import Enum
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field, create_model

# Disable decompression bomb warning for large battlemaps
Image.MAX_IMAGE_PIXELS = None

VALID_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
INDEX_PATH = Path("amber_map_index.json")
TAXONOMY_PATH = Path("taxonomy.json")
THUMB_DIR = Path("thumbnails")
FOUNDRY_REL_PREFIX = "modules/shadow-vault/images"
MODEL_NAME = "gemini-3.8-flash"

SYSTEM_PROMPT = """
You are a Virtual Tabletop map archivist for a tabletop RPG campaign.
Analyze the provided battlemap image. Identify physical objects, environment, architectural cues, and lighting.
Select ONLY from the allowed controlled-vocabulary values for eras, environments, and structures.
Extract specific, concrete visual anchors into 'features' (short noun phrases).
Write a concise 1-2 sentence GM overview of the map layout in 'summary'.
If creator 'lore' and a 'url' are provided in the prompt, incorporate relevant details accurately.
"""


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        print(f"Error: {TAXONOMY_PATH} not found.")
        sys.exit(1)
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_response_schema(taxonomy: dict):
    """Dynamically build a Pydantic schema locked to taxonomy.json values."""
    EraEnum = Enum("EraEnum", {f"E{i}": v for i, v in enumerate(taxonomy["eras"])}, type=str)
    EnvEnum = Enum("EnvEnum", {f"V{i}": v for i, v in enumerate(taxonomy["environments"])}, type=str)
    StructEnum = Enum("StructEnum", {f"S{i}": v for i, v in enumerate(taxonomy["structures"])}, type=str)

    return create_model(
        "MapMetadata",
        title=(str, Field(description="Concise, descriptive title of the map")),
        eras=(List[EraEnum], Field(description="1-2 matching eras from the allowed list")),
        environments=(List[EnvEnum], Field(description="1-2 matching biomes/settings from the allowed list")),
        structures=(List[StructEnum], Field(description="0-2 matching structure types from the allowed list")),
        summary=(str, Field(description="A 1-2 sentence GM overview of the map layout")),
        features=(List[str], Field(description="Key visual anchors and physical objects (5-8 short items)")),
        shadow_shift_cues=(List[str], Field(description="2-3 short sensory cues a GM can use when transitioning into this map")),
        source_url=(Optional[str], Field(default=None, description="Creator URL, if provided")),
        lore=(Optional[str], Field(default=None, description="Creator narrative notes, if provided")),
    )


def extract_grid_size(filename: str, width: int) -> int:
    match = re.search(r"(\d+)x(\d+)", filename, re.IGNORECASE)
    if match:
        cols = int(match.group(1))
        if cols > 0:
            return round(width / cols)
    return 100


def clean_filename_for_matching(filename: str) -> str:
    name = Path(filename).stem.lower()
    name = re.sub(r"(_grid|_nogrid|_dark|_night|_day|\d+x\d+)", "", name)
    return name.replace("_", " ").replace("-", " ").strip()


def find_lore_match(filename: str, lore_db: dict) -> Optional[dict]:
    if not lore_db:
        return None
    cleaned_name = clean_filename_for_matching(filename)
    keys = list(lore_db.keys())

    matches = difflib.get_close_matches(cleaned_name, keys, n=1, cutoff=0.6)
    if matches:
        return lore_db[matches[0]]

    best_match = None
    highest_overlap = 0
    file_words = set(cleaned_name.split())
    for key in keys:
        overlap = len(file_words.intersection(set(key.lower().split())))
        if overlap > highest_overlap and overlap >= 2:
            highest_overlap = overlap
            best_match = key

    return lore_db[best_match] if best_match else None


def slugify_filename(title: str, original_path: Path) -> Path:
    clean_name = re.sub(r"[^a-z0-9\s-]", "", title.lower())
    clean_name = re.sub(r"[\s-]+", "_", clean_name).strip("_")
    if not clean_name:
        clean_name = f"map_{int(time.time())}"

    new_path = original_path.parent / f"{clean_name}{original_path.suffix.lower()}"
    counter = 1
    while new_path.exists() and new_path != original_path:
        new_path = original_path.parent / f"{clean_name}_{counter}{original_path.suffix.lower()}"
        counter += 1
    return new_path


def prepare_image_assets(img_path: Path) -> tuple[bytes, int, int]:
    """Open once: return (api_jpeg_bytes, width, height) without modifying the source file."""
    with Image.open(img_path) as img:
        width, height = img.size

        # Downscale in memory for fast, reliable API upload
        api_img = img.copy()
        api_img.thumbnail((1280, 1280))
        if api_img.mode in ("RGBA", "P"):
            api_img = api_img.convert("RGB")
        buf = io.BytesIO()
        api_img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), width, height


def build_thumbnail(img_path: Path, thumb_path: Path):
    if thumb_path.exists():
        return
    with Image.open(img_path) as img:
        thumb = img.copy()
        thumb.thumbnail((500, 500))
        if thumb.mode in ("RGBA", "P"):
            thumb = thumb.convert("RGB")
        thumb.save(thumb_path, format="JPEG", quality=80)


def analyze_map(client: genai.Client, api_bytes: bytes, schema_model, matched_lore: Optional[dict]) -> dict:
    user_prompt = "Analyze this battlemap and generate the structured metadata."
    if matched_lore:
        print("    [+] Injecting matching lore into prompt.")
        user_prompt += f"\n\nCreator URL: {matched_lore.get('link', '')}\nCreator Lore: {matched_lore.get('lore', '')}"

    max_retries = 6
    base_wait = 4

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=[
                    types.Part.from_bytes(data=api_bytes, mime_type="image/jpeg"),
                    user_prompt,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=schema_model,
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            error_msg = str(e)
            if ("503" in error_msg or "429" in error_msg) and attempt < max_retries - 1:
                wait_time = (base_wait * (2 ** attempt)) + random.uniform(0, 2)
                print(f"    [-] Server busy. Backing off {wait_time:.1f}s (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise


def save_index_atomic(records: list):
    sorted_records = sorted(records, key=lambda r: r.get("filename", ""))
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted_records, f, indent=2)
    tmp_path.replace(INDEX_PATH)


def main():
    parser = argparse.ArgumentParser(description="AI-tag new battlemaps against taxonomy.json.")
    parser.add_argument("target", help="Path to Foundry images folder (or a single image)")
    parser.add_argument("--dry-run", action="store_true", help="List new maps to tag without calling the API or writing files")
    parser.add_argument("--rename", action="store_true", help="Rename image files based on the AI-generated title")
    args = parser.parse_args()

    target_path = Path(args.target).expanduser().resolve()
    if not target_path.exists():
        print(f"Error: {target_path} does not exist.")
        sys.exit(1)

    taxonomy = load_taxonomy()
    schema_model = build_response_schema(taxonomy)

    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            results = json.load(f)
    else:
        results = []

    processed_names = {r["filename"] for r in results if "filename" in r}

    if target_path.is_file():
        image_files = [target_path]
    else:
        image_files = sorted(
            p for p in target_path.iterdir()
            if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
        )

    new_files = [p for p in image_files if p.name not in processed_names]
    skipped = len(image_files) - len(new_files)

    print(f"Existing index entries : {len(results)}")
    print(f"Already indexed (skip) : {skipped}")
    print(f"New maps to tag        : {len(new_files)}")

    if args.dry_run:
        print("\n--- DRY RUN (No API calls or writes) ---")
        for p in new_files:
            print(f"  [NEW] {p.name}")
        return

    if not new_files:
        print("Nothing new to tag.")
        return

    THUMB_DIR.mkdir(exist_ok=True)
    client = genai.Client()

    lore_db = {}
    lore_file = Path("milby_lore_db.json")
    if lore_file.exists():
        try:
            with open(lore_file, "r", encoding="utf-8") as f:
                lore_db = json.load(f)
        except Exception:
            print("[!] Could not parse milby_lore_db.json. Proceeding without lore.")

    added = 0
    for img_path in new_files:
        print(f"\n[*] Processing: {img_path.name}")
        try:
            api_bytes, width, height = prepare_image_assets(img_path)
            grid_size = extract_grid_size(img_path.name, width)
            matched_lore = find_lore_match(img_path.name, lore_db)

            ai_data = analyze_map(client, api_bytes, schema_model, matched_lore)

            final_path = img_path
            if args.rename:
                candidate = slugify_filename(ai_data["title"], img_path)
                if candidate != img_path:
                    img_path.rename(candidate)
                    final_path = candidate
                    print(f"    [~] Renamed to: {final_path.name}")

            thumb_filename = f"{final_path.stem}.jpg"
            build_thumbnail(final_path, THUMB_DIR / thumb_filename)

            record = {
                "filename": final_path.name,
                "thumb_filename": thumb_filename,
                "relative_path": f"{FOUNDRY_REL_PREFIX}/{final_path.name}",
                "title": ai_data.get("title", final_path.stem),
                "width": width,
                "height": height,
                "grid_size": grid_size,
                "eras": ai_data.get("eras", []),
                "environments": ai_data.get("environments", []),
                "structures": ai_data.get("structures", []),
                "summary": ai_data.get("summary", ""),
                "features": ai_data.get("features", []),
                "shadow_shift_cues": ai_data.get("shadow_shift_cues", []),
                "curated": False,
            }
            if ai_data.get("source_url"):
                record["source_url"] = ai_data["source_url"]
            if ai_data.get("lore"):
                record["lore"] = ai_data["lore"]

            results.append(record)
            save_index_atomic(results)
            added += 1
            print(f"    [+] Tagged: {record['title']} | Eras: {record['eras']} | Envs: {record['environments']}")
            time.sleep(0.5)

        except Exception as e:
            print(f"    [!] Error on {img_path.name}: {e}")

    print(f"\nDone! Added {added} new maps. Total in index: {len(results)}")


if __name__ == "__main__":
    main()