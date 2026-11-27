#!/usr/bin/env python3
"""
AI-tag battlemaps against taxonomy.json with multi-compendium and read-only module support.
"""

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
from typing import Dict, List, Optional, Tuple

from google import genai
from google.genai import types
from PIL import Image
from pydantic import Field, create_model

# Disable decompression bomb warning for large battlemaps
Image.MAX_IMAGE_PIXELS = None

VALID_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
INDEX_PATH = Path("amber_map_index.json")
TAXONOMY_PATH = Path("taxonomy.json")
THUMB_DIR = Path("thumbnails")
FOUNDRY_DATA_DIR = Path("~/Library/Application Support/FoundryVTT/Data").expanduser().resolve()
DEFAULT_FOUNDRY_REL_PREFIX = "modules/shadow-vault/images"
MODEL_NAME = "gemini-3.8-flash"

COMPENDIUM_SLUGS = {
    "Shadow Vault": "shadow-vault",
    "Milby's Maps Vol. 1": "milbys-vol-1",
    "Milby's Maps Vol. 2": "milbys-vol-2",
    "Milby's Maps Vol. 3": "milbys-vol-3",
}

LORE_NOISE_TOKENS = {
    "vtt", "gridless", "grid", "nogrid", "70px", "140px", "50px", "49px", "100ppi",
    "unfurnished", "furnished", "day", "night", "dark", "hireslarge", "prelim",
    "annotated", "roll20", "lightingversion", "nocrest", "medium", "large", "clean",
    "1f", "2f", "3f", "1f2f", "123f", "4567f", "b1", "b2", "interior", "exterior",
    "land", "surface", "statuesonly", "nointerior", "nonests", "nobeds", "unarmed",
    "noairpockets", "hightide", "lowtide", "ruined", "dry", "roof", "roofs",
}

SYSTEM_PROMPT = """
You are a Virtual Tabletop map archivist for a tabletop RPG campaign.
Analyze the provided battlemap image. Identify physical objects, environment, architectural cues, and lighting.
Select ONLY from the allowed controlled-vocabulary values for eras, environments, and structures.
Extract specific, concrete visual anchors into 'features' (short noun phrases).
Write a concise 1-2 sentence GM overview of the map layout in 'summary'.
If creator 'lore' and a 'url' are provided in the prompt, incorporate relevant details accurately.
"""


def compendium_slug(name: str) -> str:
    if name in COMPENDIUM_SLUGS:
        return COMPENDIUM_SLUGS[name]
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "compendium"


def derive_foundry_rel_prefix(target_dir: Path, explicit_prefix: Optional[str]) -> str:
    if explicit_prefix:
        return explicit_prefix.strip("/")
    try:
        rel = target_dir.resolve().relative_to(FOUNDRY_DATA_DIR)
        return rel.as_posix()
    except ValueError:
        return DEFAULT_FOUNDRY_REL_PREFIX


def is_third_party_module_path(target_path: Path) -> bool:
    """Return True if target_path resides inside a Foundry module other than shadow-vault."""
    try:
        rel = target_path.resolve().relative_to(FOUNDRY_DATA_DIR / "modules")
        parts = rel.parts
        return bool(parts) and parts[0] != "shadow-vault"
    except ValueError:
        return False


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
    px_match = re.search(r"(\d+)px", filename, re.IGNORECASE)
    if px_match:
        px = int(px_match.group(1))
        if 20 <= px <= 300:
            return px
    match = re.search(r"(\d+)x(\d+)", filename, re.IGNORECASE)
    if match:
        cols = int(match.group(1))
        if cols > 0:
            return round(width / cols)
    return 100


def clean_filename_for_matching(filename: str) -> str:
    stem = Path(filename).stem if "." in filename else filename
    s = stem.lower()
    s = re.sub(r"\d+x\d+(px)?", " ", s)
    s = re.sub(r"\b\d+px\b", " ", s)
    s = re.sub(r"\b\d+ppi\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in LORE_NOISE_TOKENS]
    return " ".join(tokens).strip()


def find_lore_match(filename: str, lore_db: dict) -> Optional[dict]:
    if not lore_db:
        return None
    cleaned_name = clean_filename_for_matching(filename)
    if not cleaned_name:
        return None

    norm_to_orig = {clean_filename_for_matching(k): k for k in lore_db.keys()}
    norm_keys = [k for k in norm_to_orig.keys() if k]

    matches = difflib.get_close_matches(cleaned_name, norm_keys, n=1, cutoff=0.65)
    if matches:
        return lore_db[norm_to_orig[matches[0]]]

    best_match = None
    highest_overlap = 0
    file_words = set(cleaned_name.split())
    for nk in norm_keys:
        k_words = set(nk.split())
        overlap = len(file_words.intersection(k_words))
        if overlap > highest_overlap and overlap >= 2 and overlap >= len(k_words) * 0.6:
            highest_overlap = overlap
            best_match = norm_to_orig[nk]

    return lore_db[best_match] if best_match else None


def variant_base_key(filename: str) -> str:
    """Group resolution (70px/140px) and furnishing (Unfurnished/Furnished) variants together."""
    stem = Path(filename).stem.lower()
    stem = re.sub(r"-(70px|140px|50px|49px)\b", "", stem)
    stem = re.sub(r"-unfurnished\b", "", stem)
    return stem


def select_canonical_variants(
    files: List[Path],
    prefer_res: str,
    prefer_furnishing: str,
) -> Tuple[List[Path], int]:
    """
    Collapse resolution/furnishing sibling files down to 1 canonical file per variant group.
    Returns (selected_files, collapsed_count).
    """
    if prefer_res == "all" and prefer_furnishing == "all":
        return files, 0

    groups: Dict[str, List[Path]] = {}
    for p in files:
        key = variant_base_key(p.name)
        groups.setdefault(key, []).append(p)

    def score(p: Path) -> Tuple[int, int, str]:
        fn = p.name.lower()
        unfurn = "unfurnished" in fn
        if prefer_furnishing == "unfurnished":
            furn_score = 1 if unfurn else 0
        elif prefer_furnishing == "furnished":
            furn_score = 0 if unfurn else 1
        else:
            furn_score = 0

        m_px = re.search(r"(\d+)px", fn)
        px_val = int(m_px.group(1)) if m_px else 0
        if prefer_res == "140px":
            res_score = px_val
        elif prefer_res == "70px":
            res_score = 200 if px_val == 70 else -px_val
        else:
            res_score = 0

        return (furn_score, res_score, p.name)

    selected = []
    for key in sorted(groups.keys()):
        siblings = groups[key]
        if prefer_furnishing == "all":
            # Only collapse resolution within each furnishing state
            subgroups: Dict[bool, List[Path]] = {}
            for s in siblings:
                subgroups.setdefault("unfurnished" in s.name.lower(), []).append(s)
            for sub in subgroups.values():
                selected.append(max(sub, key=score))
        elif prefer_res == "all":
            # Only collapse furnishing within each resolution
            subgroups_r: Dict[str, List[Path]] = {}
            for s in siblings:
                m_px = re.search(r"(\d+)px", s.name.lower())
                subgroups_r.setdefault(m_px.group(1) if m_px else "other", []).append(s)
            for sub in subgroups_r.values():
                selected.append(max(sub, key=score))
        else:
            selected.append(max(siblings, key=score))

    selected.sort(key=lambda p: p.name)
    return selected, len(files) - len(selected)


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


def make_thumb_filename(compendium: str, img_path: Path, existing_thumbs: set) -> str:
    slug = compendium_slug(compendium)
    base = f"{slug}__{img_path.stem}.jpg"
    if base not in existing_thumbs:
        return base
    ext = img_path.suffix.lstrip(".").lower()
    return f"{slug}__{img_path.stem}_{ext}.jpg"


def prepare_image_assets(img_path: Path) -> Tuple[bytes, int, int]:
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
    sorted_records = sorted(records, key=lambda r: (r.get("compendium", ""), r.get("filename", "")))
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted_records, f, indent=2)
    tmp_path.replace(INDEX_PATH)


def main():
    parser = argparse.ArgumentParser(description="AI-tag new battlemaps against taxonomy.json.")
    parser.add_argument("target", help="Path to Foundry images/art folder (or a single image)")
    parser.add_argument("--compendium", default="Shadow Vault", help='Compendium label (default: "Shadow Vault")')
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Treat target folder as strictly read-only (blocks --rename, sets managed_by_vault=false)",
    )
    parser.add_argument("--rel-prefix", default=None, help="Override Foundry relative_path prefix (auto-derived by default)")
    parser.add_argument(
        "--lore-db",
        default=None,
        help="Path to creator lore JSON (defaults to milby_lore_db.json ONLY for Milby compendiums; pass 'none' to disable)",
    )
    parser.add_argument(
        "--prefer-res",
        choices=["140px", "70px", "all"],
        default=None,
        help="Collapse resolution variants to one file (defaults to '140px' for Milby compendiums, 'all' otherwise)",
    )
    parser.add_argument(
        "--prefer-furnishing",
        choices=["unfurnished", "furnished", "all"],
        default=None,
        help="Collapse furnished/unfurnished variants (defaults to 'unfurnished' for Milby compendiums, 'all' otherwise)",
    )
    parser.add_argument(
        "--retag-uncurated",
        action="store_true",
        help="Include existing indexed maps where curated=false and update their records in place",
    )
    parser.add_argument("--dry-run", action="store_true", help="List maps to tag/re-tag without calling the API or writing files")
    parser.add_argument("--rename", action="store_true", help="Rename new image files based on AI title (blocked in --read-only mode; skipped for in-place re-tags)")
    args = parser.parse_args()

    target_path = Path(args.target).expanduser().resolve()
    if not target_path.exists():
        print(f"Error: {target_path} does not exist.")
        sys.exit(1)

    # Hard safety guardrail: never allow writes/renames inside third-party Foundry modules
    third_party = is_third_party_module_path(target_path)
    is_read_only = args.read_only or third_party
    if third_party and not args.read_only:
        print("[!] Target is a third-party Foundry module: enforcing --read-only automatically.")

    if is_read_only and args.rename:
        print("Error: --rename is forbidden in --read-only / third-party module mode.")
        sys.exit(1)

    is_milby = "milby" in args.compendium.lower() or "milby" in str(target_path).lower()
    prefer_res = args.prefer_res if args.prefer_res is not None else ("140px" if is_milby else "all")
    prefer_furnishing = args.prefer_furnishing if args.prefer_furnishing is not None else ("unfurnished" if is_milby else "all")

    target_dir = target_path.parent if target_path.is_file() else target_path
    rel_prefix = derive_foundry_rel_prefix(target_dir, args.rel_prefix)

    taxonomy = load_taxonomy()
    schema_model = build_response_schema(taxonomy)

    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            results = json.load(f)
    else:
        results = []

    records_by_rel_path: Dict[str, Tuple[int, dict]] = {
        r["relative_path"]: (idx, r)
        for idx, r in enumerate(results)
        if r.get("relative_path")
    }
    # Also track already-indexed variant groups in the same folder so we don't re-tag a sibling variant
    processed_variant_keys = {
        (str(Path(r["relative_path"]).parent), variant_base_key(Path(r["relative_path"]).name))
        for r in results
        if r.get("relative_path")
    }
    existing_thumbs = {r.get("thumb_filename") for r in results if r.get("thumb_filename")}

    if target_path.is_file():
        all_image_files = [target_path]
        collapsed_variants = 0
        candidate_files = all_image_files
    else:
        all_image_files = sorted(
            p for p in target_path.iterdir()
            if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
        )
        candidate_files, collapsed_variants = select_canonical_variants(
            all_image_files, prefer_res=prefer_res, prefer_furnishing=prefer_furnishing
        )

    # Each queued item is (img_path, existing_idx_or_None)
    queue: List[Tuple[Path, Optional[int]]] = []
    new_count = 0
    retag_count = 0
    skipped_indexed = 0
    skipped_curated = 0
    for p in candidate_files:
        rel_p = f"{rel_prefix}/{p.name}"
        vkey = (rel_prefix, variant_base_key(p.name))
        existing_entry = records_by_rel_path.get(rel_p)
        if existing_entry is not None:
            existing_idx, existing_rec = existing_entry
            if args.retag_uncurated and existing_rec.get("curated") is False:
                queue.append((p, existing_idx))
                retag_count += 1
            else:
                skipped_indexed += 1
                if existing_rec.get("curated") is True:
                    skipped_curated += 1
        elif (prefer_res != "all" or prefer_furnishing != "all") and vkey in processed_variant_keys:
            skipped_indexed += 1
        else:
            queue.append((p, None))
            new_count += 1

    # Determine lore DB
    lore_db = {}
    lore_file: Optional[Path] = None
    if args.lore_db:
        if args.lore_db.lower() != "none":
            lore_file = Path(args.lore_db)
    elif is_milby:
        lore_file = Path("milby_lore_db.json")

    if lore_file and lore_file.exists():
        try:
            with open(lore_file, "r", encoding="utf-8") as f:
                lore_db = json.load(f)
        except Exception:
            print(f"[!] Could not parse {lore_file}. Proceeding without lore.")

    print(f"Compendium             : {args.compendium} (managed_by_vault={not is_read_only})")
    print(f"Foundry rel prefix     : {rel_prefix}")
    print(f"Variant filter         : res={prefer_res}, furnishing={prefer_furnishing} (collapsed {collapsed_variants} siblings)")
    print(f"Lore DB                : {lore_file if lore_db else 'disabled'}")
    print(f"Retag uncurated        : {'enabled' if args.retag_uncurated else 'disabled'}")
    print(f"Existing index entries : {len(results)}")
    skip_detail = f" ({skipped_curated} curated)" if args.retag_uncurated else ""
    print(f"Already indexed (skip) : {skipped_indexed}{skip_detail}")
    print(f"New maps to tag        : {new_count}")
    if args.retag_uncurated:
        print(f"Uncurated to re-tag    : {retag_count}")
        print(f"Total maps to process  : {len(queue)}")

    if args.dry_run:
        print("\n--- DRY RUN (No API calls or writes) ---")
        lore_hits = 0
        for p, existing_idx in queue:
            matched = find_lore_match(p.name, lore_db) if lore_db else None
            if matched:
                lore_hits += 1
            lore_tag = " [lore match]" if matched else ""
            status_tag = "[RETAG]" if existing_idx is not None else "[NEW]"
            print(f"  {status_tag} {p.name}{lore_tag}")
        if lore_db:
            print(f"\nLore DB matches        : {lore_hits} / {len(queue)}")
        return

    if not queue:
        print("Nothing to tag." if args.retag_uncurated else "Nothing new to tag.")
        return

    THUMB_DIR.mkdir(exist_ok=True)
    client = genai.Client()

    added = 0
    retagged = 0
    for img_path, existing_idx in queue:
        existing_rec = results[existing_idx] if existing_idx is not None else None
        mode_tag = "Re-tagging" if existing_rec is not None else "Processing"
        print(f"\n[*] {mode_tag}: {img_path.name}")
        try:
            api_bytes, width, height = prepare_image_assets(img_path)
            grid_size = extract_grid_size(img_path.name, width)
            matched_lore = find_lore_match(img_path.name, lore_db)

            ai_data = analyze_map(client, api_bytes, schema_model, matched_lore)

            final_path = img_path
            if args.rename and not is_read_only and existing_rec is None:
                candidate = slugify_filename(ai_data["title"], img_path)
                if candidate != img_path:
                    img_path.rename(candidate)
                    final_path = candidate
                    print(f"    [~] Renamed to: {final_path.name}")

            if existing_rec is not None:
                compendium = existing_rec.get("compendium", args.compendium)
                managed_by_vault = existing_rec.get("managed_by_vault", not is_read_only)
                thumb_filename = existing_rec.get("thumb_filename") or make_thumb_filename(
                    compendium, final_path, existing_thumbs
                )
            else:
                compendium = args.compendium
                managed_by_vault = not is_read_only
                thumb_filename = make_thumb_filename(compendium, final_path, existing_thumbs)

            build_thumbnail(final_path, THUMB_DIR / thumb_filename)
            existing_thumbs.add(thumb_filename)

            record = {
                "filename": final_path.name,
                "thumb_filename": thumb_filename,
                "relative_path": f"{rel_prefix}/{final_path.name}",
                "compendium": compendium,
                "managed_by_vault": managed_by_vault,
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
            elif matched_lore and matched_lore.get("link"):
                record["source_url"] = matched_lore["link"]
            elif existing_rec is not None and existing_rec.get("source_url"):
                record["source_url"] = existing_rec["source_url"]

            if ai_data.get("lore"):
                record["lore"] = ai_data["lore"]
            elif matched_lore and matched_lore.get("lore"):
                record["lore"] = matched_lore["lore"]
            elif existing_rec is not None and existing_rec.get("lore"):
                record["lore"] = existing_rec["lore"]

            if existing_idx is not None:
                results[existing_idx] = record
                retagged += 1
                action_label = "Re-tagged"
            else:
                results.append(record)
                records_by_rel_path[record["relative_path"]] = (len(results) - 1, record)
                added += 1
                action_label = "Tagged"

            save_index_atomic(results)
            print(f"    [+] {action_label}: {record['title']} | Eras: {record['eras']} | Envs: {record['environments']}")
            time.sleep(0.5)

        except Exception as e:
            print(f"    [!] Error on {img_path.name}: {e}")

    print(f"\nDone! Added {added} new maps, re-tagged {retagged} uncurated maps. Total in index: {len(results)}")


if __name__ == "__main__":
    main()
