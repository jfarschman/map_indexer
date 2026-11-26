#!/usr/bin/env python3
"""
Phase 1: Audit and deduplicate Milby's Maps copies sitting in shadow-vault/images.

Safety guarantees:
- NEVER writes, renames, rotates, or modifies any file inside Milby's Foundry module folders.
- Defaults to --dry-run (no files moved, amber_map_index.json untouched).
- In --apply mode, backs up amber_map_index.json, moves duplicate shadow-vault images to a
  local reversible backup directory with an undo manifest, and writes amber_map_index.json atomically.
"""

import argparse
import difflib
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
FOUNDRY_DATA_DIR = Path("~/Library/Application Support/FoundryVTT/Data").expanduser()
FOUNDRY_MODULES_DIR = FOUNDRY_DATA_DIR / "modules"
SHADOW_VAULT_IMAGES = FOUNDRY_MODULES_DIR / "shadow-vault" / "images"

INDEX_PATH = Path("amber_map_index.json")
LORE_PATH = Path("milby_lore_db.json")
THUMB_DIR = Path("thumbnails")
BACKUPS_DIR = Path("backups")
DEFAULT_REPORT_PATH = BACKUPS_DIR / "milby_audit_report.json"

MILBY_MODULES = [
    {
        "id": "milbys-maps-patrons-module",
        "compendium": "Milby's Maps Vol. 1",
        "slug": "milbys-vol-1",
    },
    {
        "id": "milbys-maps-patrons-module-2",
        "compendium": "Milby's Maps Vol. 2",
        "slug": "milbys-vol-2",
    },
    {
        "id": "milbys-maps-patrons-module-3",
        "compendium": "Milby's Maps Vol. 3",
        "slug": "milbys-vol-3",
    },
]

# 128-bit dHash thresholds (64-bit horizontal + 64-bit vertical)
HIGH_CONF_DIST = 11
CORROBORATED_DIST = 20
CORROBORATED_NAME_SIM = 0.52
REVIEW_VISUAL_DIST = 16
NAME_ONLY_SIM = 0.72
MAX_ASPECT_RATIO_DIFF = 0.08  # 8% tolerance for rounding/border crop


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash128_from_image(img: Image.Image) -> int:
    """Compute a 128-bit combined horizontal (64-bit) + vertical (64-bit) dHash."""
    gray = img.convert("L")

    # Horizontal 9x8 -> 64 bits
    h_small = gray.resize((9, 8), Image.Resampling.BILINEAR)
    h_px = list(h_small.getdata())
    h_hash = 0
    for r in range(8):
        row_start = r * 9
        for c in range(8):
            h_hash = (h_hash << 1) | (1 if h_px[row_start + c] > h_px[row_start + c + 1] else 0)

    # Vertical 8x9 -> 64 bits
    v_small = gray.resize((8, 9), Image.Resampling.BILINEAR)
    v_px = list(v_small.getdata())
    v_hash = 0
    for r in range(8):
        row_start = r * 8
        next_row = (r + 1) * 8
        for c in range(8):
            v_hash = (v_hash << 1) | (1 if v_px[row_start + c] > v_px[next_row + c] else 0)

    return (h_hash << 64) | v_hash


def load_image_fingerprint(path: Path, compute_rotations: bool = False) -> dict:
    """Read dimensions, MD5, and 128-bit dHash (optionally for all 4 rotations) without modifying the file."""
    file_md5 = md5_file(path)
    with Image.open(path) as img:
        orig_w, orig_h = img.size
        # Use libjpeg draft mode for massive decode speedup on huge battlemaps
        if img.format == "JPEG":
            img.draft("L", (256, 256))
        base = img.convert("L")
        base.thumbnail((256, 256), Image.Resampling.BILINEAR)

        if compute_rotations:
            hashes = {
                0: dhash128_from_image(base),
                90: dhash128_from_image(base.transpose(Image.Transpose.ROTATE_90)),
                180: dhash128_from_image(base.transpose(Image.Transpose.ROTATE_180)),
                270: dhash128_from_image(base.transpose(Image.Transpose.ROTATE_270)),
            }
        else:
            hashes = {0: dhash128_from_image(base)}

    return {
        "width": orig_w,
        "height": orig_h,
        "size": path.stat().st_size,
        "md5": file_md5,
        "hashes": hashes,
    }


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def aspect_ratio_diff(w1: int, h1: int, w2: int, h2: int, rot_deg: int) -> float:
    """Relative difference in aspect ratio after applying rot_deg to (w2, h2)."""
    if w1 <= 0 or h1 <= 0 or w2 <= 0 or h2 <= 0:
        return 1.0
    mw, mh = (h2, w2) if rot_deg in (90, 270) else (w2, h2)
    r1 = w1 / h1
    r2 = mw / mh
    return abs(r1 - r2) / max(r1, r2)


NOISE_TOKENS = {
    "vtt", "gridless", "grid", "nogrid", "70px", "140px", "50px", "49px", "100ppi",
    "unfurnished", "furnished", "day", "night", "dark", "hireslarge", "prelim",
    "annotated", "roll20", "lightingversion", "nocrest", "medium", "large",
}


def normalize_name(text: str) -> str:
    """Normalize a filename or title into clean space-separated tokens for fuzzy matching."""
    stem = Path(text).stem if "." in text else text
    s = stem.lower()
    s = re.sub(r"\d+x\d+(px)?", " ", s)
    s = re.sub(r"\b\d+px\b", " ", s)
    s = re.sub(r"\b\d+ppi\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in NOISE_TOKENS]
    return " ".join(tokens).strip()


def name_similarity(shadow_filename: str, shadow_title: str, milby_filename: str) -> float:
    m_norm = normalize_name(milby_filename)
    if not m_norm:
        return 0.0
    best = 0.0
    for candidate in (normalize_name(shadow_filename), normalize_name(shadow_title)):
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, candidate, m_norm).ratio()
        # Token containment bonus when Milby's map title is a strong subset of a descriptive Shadow Vault title
        c_tokens = set(candidate.split())
        m_tokens = set(m_norm.split())
        if len(m_tokens) >= 2 and m_tokens.issubset(c_tokens):
            ratio = max(ratio, 0.85)
        elif len(c_tokens) >= 2 and c_tokens.issubset(m_tokens):
            ratio = max(ratio, 0.80)
        if ratio > best:
            best = ratio
    return round(best, 3)


def variant_base_key(filename: str) -> str:
    """Group resolution/furnished variants of the same Milby map together."""
    stem = Path(filename).stem.lower()
    stem = re.sub(r"-(70px|140px|50px|49px)\b", "", stem)
    stem = re.sub(r"-unfurnished\b", "", stem)
    return stem


def is_unfurnished(filename: str) -> bool:
    return "unfurnished" in filename.lower()


def resolution_tag(filename: str) -> str:
    low = filename.lower()
    if "140px" in low:
        return "140px"
    if "70px" in low:
        return "70px"
    m = re.search(r"(\d+px)", low)
    return m.group(1) if m else "other"


def extract_grid_size(filename: str, width: int) -> int:
    low = filename.lower()
    if "140px" in low:
        return 140
    if "70px" in low:
        return 70
    match = re.search(r"(\d+)x(\d+)", filename, re.IGNORECASE)
    if match:
        cols = int(match.group(1))
        if cols > 0:
            return round(width / cols)
    return 100


def find_lore_match(filename: str, lore_db: dict) -> Optional[dict]:
    if not lore_db:
        return None
    cleaned = normalize_name(filename)
    keys = list(lore_db.keys())
    norm_to_orig = {normalize_name(k): k for k in keys}
    matches = difflib.get_close_matches(cleaned, list(norm_to_orig.keys()), n=1, cutoff=0.65)
    if matches:
        return lore_db[norm_to_orig[matches[0]]]
    file_words = set(cleaned.split())
    best_key = None
    best_overlap = 0
    for nk, orig_k in norm_to_orig.items():
        overlap = len(file_words.intersection(set(nk.split())))
        if overlap > best_overlap and overlap >= 2:
            best_overlap = overlap
            best_key = orig_k
    return lore_db[best_key] if best_key else None


def pick_preferred_sibling(
    matched_milby: dict,
    all_milby_by_group: Dict[Tuple[str, str], List[dict]],
    prefer_res: str,
    prefer_furnishing: str,
) -> dict:
    """Given a visually matched Milby file, pick the canonical sibling (e.g., 140px + unfurnished/furnished)."""
    group_key = (matched_milby["module_id"], variant_base_key(matched_milby["filename"]))
    siblings = all_milby_by_group.get(group_key, [matched_milby])
    if len(siblings) == 1:
        return matched_milby

    def score(item: dict) -> Tuple[int, int, int]:
        fn = item["filename"]
        res = resolution_tag(fn)
        unfurn = is_unfurnished(fn)

        if prefer_furnishing == "unfurnished":
            furn_score = 1 if unfurn else 0
        elif prefer_furnishing == "furnished":
            furn_score = 0 if unfurn else 1
        else:
            # match_visual: keep same furnishing as visual match
            furn_score = 1 if unfurn == is_unfurnished(matched_milby["filename"]) else 0

        m_px = re.search(r"(\d+)px", fn, re.IGNORECASE)
        px_val = int(m_px.group(1)) if m_px else 0
        if prefer_res == "140px":
            res_score = px_val
        else:
            res_score = 200 if res == "70px" else -px_val

        same_as_visual = 1 if fn == matched_milby["filename"] else 0
        return (furn_score, res_score, same_as_visual)

    return max(siblings, key=score)


def collect_milby_catalog(modules: List[dict]) -> List[dict]:
    catalog = []
    for mod in modules:
        art_dir = FOUNDRY_MODULES_DIR / mod["id"] / "art"
        if not art_dir.is_dir():
            print(f"[!] Warning: {art_dir} not found, skipping.")
            continue
        files = sorted(
            p for p in art_dir.iterdir()
            if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
        )
        print(f"[*] Fingerprinting {len(files)} images in {mod['id']}/art/ (read-only)...")
        for p in files:
            try:
                fp = load_image_fingerprint(p, compute_rotations=True)
                catalog.append({
                    "module_id": mod["id"],
                    "compendium": mod["compendium"],
                    "slug": mod["slug"],
                    "filename": p.name,
                    "abs_path": str(p),
                    "relative_path": f"modules/{mod['id']}/art/{p.name}",
                    "width": fp["width"],
                    "height": fp["height"],
                    "size": fp["size"],
                    "md5": fp["md5"],
                    "hashes": fp["hashes"],
                    "res_tag": resolution_tag(p.name),
                    "unfurnished": is_unfurnished(p.name),
                })
            except Exception as e:
                print(f"    [!] Failed reading {p.name}: {e}")
    return catalog


def run_audit(prefer_res: str, prefer_furnishing: str, report_path: Path) -> dict:
    t0 = time.time()
    if not INDEX_PATH.exists():
        print(f"Error: {INDEX_PATH} not found.")
        sys.exit(1)
    if not SHADOW_VAULT_IMAGES.is_dir():
        print(f"Error: {SHADOW_VAULT_IMAGES} not found.")
        sys.exit(1)

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        index_records = json.load(f)
    index_by_filename = {r["filename"]: r for r in index_records if "filename" in r}

    milby_catalog = collect_milby_catalog(MILBY_MODULES)
    milby_by_md5: Dict[str, List[dict]] = {}
    milby_by_group: Dict[Tuple[str, str], List[dict]] = {}
    for item in milby_catalog:
        milby_by_md5.setdefault(item["md5"], []).append(item)
        gkey = (item["module_id"], variant_base_key(item["filename"]))
        milby_by_group.setdefault(gkey, []).append(item)

    sv_files = sorted(
        p for p in SHADOW_VAULT_IMAGES.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
    )
    print(f"[*] Fingerprinting {len(sv_files)} images in shadow-vault/images/ (read-only)...")

    matches = []
    for p in sv_files:
        rec = index_by_filename.get(p.name, {})
        sv_title = rec.get("title", "")
        sv_curated = bool(rec.get("curated", False))
        try:
            fp = load_image_fingerprint(p, compute_rotations=False)
        except Exception as e:
            print(f"    [!] Failed reading {p.name}: {e}")
            continue

        sv_hash = fp["hashes"][0]
        exact_hits = milby_by_md5.get(fp["md5"], [])

        best_visual = None
        best_dist = 999
        best_rot = 0

        if exact_hits:
            best_visual = exact_hits[0]
            best_dist = 0
            best_rot = 0
        else:
            for m in milby_catalog:
                for rot, mh in m["hashes"].items():
                    d = hamming(sv_hash, mh)
                    if d < best_dist:
                        best_dist = d
                        best_rot = rot
                        best_visual = m

        # Also check best name match in case of heavy crop / different floor / variant
        best_name_item = None
        best_name_sim = 0.0
        for m in milby_catalog:
            sim = name_similarity(p.name, sv_title, m["filename"])
            if sim > best_name_sim:
                best_name_sim = sim
                best_name_item = m

        visual_name_sim = (
            name_similarity(p.name, sv_title, best_visual["filename"])
            if best_visual else 0.0
        )
        ar_diff = (
            aspect_ratio_diff(fp["width"], fp["height"], best_visual["width"], best_visual["height"], best_rot)
            if best_visual else 1.0
        )

        tier = None
        chosen_visual = best_visual
        chosen_dist = best_dist
        chosen_rot = best_rot
        chosen_name_sim = visual_name_sim
        match_reason = ""

        if exact_hits:
            tier = "tier1_exact"
            match_reason = "Exact MD5 match"
        elif best_dist <= HIGH_CONF_DIST and ar_diff <= MAX_ASPECT_RATIO_DIFF:
            tier = "tier2_high_visual"
            match_reason = f"dHash128 dist={best_dist}/128 (rot={best_rot}°, ar_diff={ar_diff:.1%})"
        elif best_dist <= CORROBORATED_DIST and visual_name_sim >= CORROBORATED_NAME_SIM and ar_diff <= MAX_ASPECT_RATIO_DIFF:
            tier = "tier2_high_visual"
            match_reason = f"dHash128 dist={best_dist}/128 (rot={best_rot}°, ar_diff={ar_diff:.1%}) + name_sim={visual_name_sim}"
        elif best_dist <= REVIEW_VISUAL_DIST and ar_diff <= MAX_ASPECT_RATIO_DIFF:
            tier = "tier3_review"
            match_reason = f"Borderline visual dHash128 dist={best_dist}/128 (rot={best_rot}°, ar_diff={ar_diff:.1%})"
        elif best_name_sim >= NAME_ONLY_SIM and best_name_item is not None:
            # Compute visual distance specifically against the name-matched item for context
            name_item_dist = min(hamming(sv_hash, mh) for mh in best_name_item["hashes"].values())
            tier = "tier3_review"
            chosen_visual = best_name_item
            chosen_dist = name_item_dist
            chosen_rot = min(best_name_item["hashes"].keys(), key=lambda r: hamming(sv_hash, best_name_item["hashes"][r]))
            chosen_name_sim = best_name_sim
            match_reason = f"Name/title match only (sim={best_name_sim}, dHash128 dist={name_item_dist}/128)"

        if tier and chosen_visual is not None:
            canonical = pick_preferred_sibling(
                chosen_visual, milby_by_group, prefer_res=prefer_res, prefer_furnishing=prefer_furnishing
            )
            gkey = (chosen_visual["module_id"], variant_base_key(chosen_visual["filename"]))
            siblings = [s["filename"] for s in milby_by_group.get(gkey, [])]

            matches.append({
                "tier": tier,
                "reason": match_reason,
                "shadow_filename": p.name,
                "shadow_title": sv_title,
                "shadow_curated": sv_curated,
                "shadow_dims": f"{fp['width']}x{fp['height']}",
                "shadow_in_index": p.name in index_by_filename,
                "milby_module_id": chosen_visual["module_id"],
                "milby_compendium": chosen_visual["compendium"],
                "milby_slug": chosen_visual["slug"],
                "visual_match_filename": chosen_visual["filename"],
                "canonical_filename": canonical["filename"],
                "canonical_relative_path": canonical["relative_path"],
                "canonical_dims": f"{canonical['width']}x{canonical['height']}",
                "canonical_width": canonical["width"],
                "canonical_height": canonical["height"],
                "available_siblings": sorted(siblings),
                "dhash128_dist": chosen_dist,
                "rotation_deg": chosen_rot,
                "name_sim": chosen_name_sim,
                "approved": tier in ("tier1_exact", "tier2_high_visual"),
                "role": "primary",  # updated below if N-to-1 collision
            })

    # Detect N-to-1 collisions (multiple shadow-vault files pointing to the same canonical Milby file)
    by_canonical: Dict[str, List[dict]] = {}
    for m in matches:
        if m["tier"] in ("tier1_exact", "tier2_high_visual"):
            by_canonical.setdefault(m["canonical_relative_path"], []).append(m)

    collisions = []
    for canon_rel, group in by_canonical.items():
        if len(group) > 1:
            # Prefer curated=True, then lowest dHash distance, then filename
            group.sort(key=lambda x: (not x["shadow_curated"], x["dhash128_dist"], x["shadow_filename"]))
            group[0]["role"] = "primary"
            for dup in group[1:]:
                dup["role"] = "secondary_duplicate"
            collisions.append({
                "canonical_relative_path": canon_rel,
                "primary": group[0]["shadow_filename"],
                "secondaries": [d["shadow_filename"] for d in group[1:]],
            })

    elapsed = round(time.time() - t0, 1)
    tier1 = [m for m in matches if m["tier"] == "tier1_exact"]
    tier2 = [m for m in matches if m["tier"] == "tier2_high_visual"]
    tier3 = [m for m in matches if m["tier"] == "tier3_review"]

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "preferences": {
            "prefer_res": prefer_res,
            "prefer_furnishing": prefer_furnishing,
            "modules_scanned": [m["id"] for m in MILBY_MODULES],
        },
        "summary": {
            "shadow_vault_images_scanned": len(sv_files),
            "milby_images_scanned": len(milby_catalog),
            "tier1_exact": len(tier1),
            "tier2_high_visual": len(tier2),
            "tier3_review": len(tier3),
            "curated_in_tier1_2": sum(1 for m in tier1 + tier2 if m["shadow_curated"]),
            "n_to_1_collisions": len(collisions),
        },
        "collisions": collisions,
        "matches": matches,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print_report(report, report_path)
    return report


def print_report(report: dict, report_path: Path):
    s = report["summary"]
    print("\n" + "=" * 78)
    print("MILBY MAPS DRY-RUN AUDIT REPORT (READ-ONLY)")
    print("=" * 78)
    print(f"Elapsed                : {report['elapsed_seconds']}s")
    print(f"Shadow Vault scanned   : {s['shadow_vault_images_scanned']}")
    print(f"Milby Vol 1-3 scanned  : {s['milby_images_scanned']}")
    print(f"Tier 1 (Exact MD5)     : {s['tier1_exact']}")
    print(f"Tier 2 (High Visual)   : {s['tier2_high_visual']}")
    print(f"Tier 3 (Needs Review)  : {s['tier3_review']}")
    print(f"Curated in Tier 1+2    : {s['curated_in_tier1_2']}")
    print(f"N-to-1 Collisions      : {s['n_to_1_collisions']}")
    print(f"Report written to      : {report_path}")

    for tier_key, label in [
        ("tier1_exact", "TIER 1 — EXACT MD5 MATCHES"),
        ("tier2_high_visual", "TIER 2 — HIGH-CONFIDENCE VISUAL MATCHES"),
        ("tier3_review", "TIER 3 — NEEDS MANUAL REVIEW"),
    ]:
        items = [m for m in report["matches"] if m["tier"] == tier_key]
        print(f"\n--- {label} ({len(items)}) ---")
        for m in sorted(items, key=lambda x: (x["dhash128_dist"], x["shadow_filename"])):
            cur = "[CURATED]" if m["shadow_curated"] else "[uncur]  "
            role = " (SECONDARY DUP)" if m["role"] == "secondary_duplicate" else ""
            sib_note = ""
            if m["canonical_filename"] != m["visual_match_filename"]:
                sib_note = f"  (visual={m['visual_match_filename']})"
            print(
                f"  {cur} {m['shadow_filename']} ({m['shadow_dims']}){role}\n"
                f"           -> [{m['milby_compendium']}] {m['canonical_filename']} ({m['canonical_dims']}){sib_note}\n"
                f"              {m['reason']} | siblings={len(m['available_siblings'])}"
            )

    if report["collisions"]:
        print(f"\n--- N-TO-1 COLLISIONS ({len(report['collisions'])}) ---")
        for c in report["collisions"]:
            print(f"  Target: {c['canonical_relative_path']}")
            print(f"    Keep/Re-point : {c['primary']}")
            print(f"    Retire dup(s) : {', '.join(c['secondaries'])}")


def build_thumbnail(src_path: Path, thumb_path: Path):
    with Image.open(src_path) as img:
        thumb = img.copy()
        thumb.thumbnail((500, 500))
        if thumb.mode in ("RGBA", "P"):
            thumb = thumb.convert("RGB")
        thumb.save(thumb_path, format="JPEG", quality=80)


def apply_report(report_path: Path, mode: str):
    """
    Apply approved matches from a previously generated dry-run report.
    mode:
      - 'repoint': preserve curated metadata, re-point primary entry to Milby's module, remove secondary dups.
      - 'remove':  remove all approved deduped entries from amber_map_index.json completely.
    """
    if not report_path.exists():
        print(f"Error: report {report_path} not found. Run --dry-run first.")
        sys.exit(1)

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    approved = [m for m in report["matches"] if m.get("approved")]
    if not approved:
        print("No approved matches in report. Nothing to apply.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    index_backup = BACKUPS_DIR / f"amber_map_index.{ts}.json"
    shutil.copy2(INDEX_PATH, index_backup)
    print(f"[+] Backed up {INDEX_PATH} -> {index_backup}")

    img_backup_dir = BACKUPS_DIR / "deduped_shadow_vault_images" / ts
    img_backup_dir.mkdir(parents=True, exist_ok=True)

    lore_db = {}
    if LORE_PATH.exists():
        with open(LORE_PATH, "r", encoding="utf-8") as f:
            lore_db = json.load(f)

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    approved_by_sv = {m["shadow_filename"]: m for m in approved}
    undo_actions = []
    updated_records = []
    repointed = 0
    removed = 0
    moved = 0

    for rec in records:
        sv_fn = rec.get("filename")
        match = approved_by_sv.get(sv_fn)
        if not match:
            updated_records.append(rec)
            continue

        # Verify target Milby file actually exists (read-only check)
        milby_abs = FOUNDRY_DATA_DIR / match["canonical_relative_path"]
        if not milby_abs.is_file():
            print(f"[!] Target Milby file missing, skipping {sv_fn}: {milby_abs}")
            updated_records.append(rec)
            continue

        # Move duplicate image out of shadow-vault/images
        src_sv_img = SHADOW_VAULT_IMAGES / sv_fn
        dst_backup_img = img_backup_dir / sv_fn
        if src_sv_img.is_file():
            shutil.move(str(src_sv_img), str(dst_backup_img))
            moved += 1

        undo_actions.append({
            "shadow_filename": sv_fn,
            "original_image_path": str(src_sv_img),
            "backup_image_path": str(dst_backup_img),
            "action": "remove" if (mode == "remove" or match.get("role") == "secondary_duplicate") else "repoint",
            "original_record": dict(rec),
            "target_relative_path": match["canonical_relative_path"],
        })

        if mode == "remove" or match.get("role") == "secondary_duplicate":
            removed += 1
            continue

        # Re-point record while preserving curated metadata
        new_rec = dict(rec)
        new_rec["filename"] = match["canonical_filename"]
        new_rec["relative_path"] = match["canonical_relative_path"]
        new_rec["width"] = match["canonical_width"]
        new_rec["height"] = match["canonical_height"]
        new_rec["grid_size"] = extract_grid_size(match["canonical_filename"], match["canonical_width"])
        new_rec["compendium"] = match["milby_compendium"]
        new_rec["managed_by_vault"] = False

        # Generate prefixed thumbnail from Milby's untouched file
        THUMB_DIR.mkdir(exist_ok=True)
        new_thumb = f"{match['milby_slug']}__{Path(match['canonical_filename']).stem}.jpg"
        build_thumbnail(milby_abs, THUMB_DIR / new_thumb)
        new_rec["thumb_filename"] = new_thumb

        # Non-destructively enrich lore/source_url if missing
        matched_lore = find_lore_match(match["canonical_filename"], lore_db)
        if matched_lore:
            if not new_rec.get("source_url") and matched_lore.get("link"):
                new_rec["source_url"] = matched_lore["link"]
            if not new_rec.get("lore") and matched_lore.get("lore"):
                new_rec["lore"] = matched_lore["lore"]

        updated_records.append(new_rec)
        repointed += 1

    # Write undo manifest
    undo_path = img_backup_dir / "undo_manifest.json"
    with open(undo_path, "w", encoding="utf-8") as f:
        json.dump(undo_actions, f, indent=2)

    # Atomic write of amber_map_index.json
    updated_records.sort(key=lambda r: (r.get("compendium", "Shadow Vault"), r.get("filename", "")))
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(updated_records, f, indent=2)
    tmp_path.replace(INDEX_PATH)

    print("\n--- Apply Complete ---")
    print(f"Images moved to backup : {moved} -> {img_backup_dir}")
    print(f"Records re-pointed     : {repointed}")
    print(f"Records removed        : {removed}")
    print(f"Total records in index : {len(updated_records)}")
    print(f"Undo manifest          : {undo_path}")


def main():
    parser = argparse.ArgumentParser(description="Audit and deduplicate Milby's Maps in shadow-vault/images.")
    parser.add_argument("--dry-run", action="store_true", help="Run read-only audit and generate report (default).")
    parser.add_argument("--apply", action="store_true", help="Apply approved matches from the audit report.")
    parser.add_argument(
        "--apply-mode",
        choices=["repoint", "remove"],
        default="repoint",
        help="In --apply mode: 'repoint' preserves curated metadata & points to Milby's module; 'remove' drops entries.",
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH, help="Path to audit report JSON.")
    parser.add_argument(
        "--prefer-res",
        choices=["140px", "70px"],
        default="140px",
        help="Preferred resolution when both 70px and 140px exist in Milby's art/.",
    )
    parser.add_argument(
        "--prefer-furnishing",
        choices=["unfurnished", "furnished", "match_visual"],
        default="unfurnished",
        help="Preferred variant when both Furnished and Unfurnished exist in Milby's art/.",
    )
    args = parser.parse_args()

    if args.apply:
        apply_report(args.report, args.apply_mode)
    else:
        run_audit(args.prefer_res, args.prefer_furnishing, args.report)


if __name__ == "__main__":
    main()
