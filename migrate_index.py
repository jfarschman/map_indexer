#!/usr/bin/env python3
"""
Phase 2: Migrate amber_map_index.json to the multi-compendium schema.

Changes applied (only with --apply; defaults to --dry-run):
- Adds `compendium` ("Shadow Vault" for existing vault maps) and `managed_by_vault` (bool).
- Fixes legacy singular `era` key -> `eras`.
- Standardizes thumbnail filenames to `<compendium-slug>__<stem>.jpg` (disambiguating
  same-stem collisions like `.jpeg` vs `.png` with `_<ext>`).
- Backs up amber_map_index.json to backups/ and writes atomically.
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

INDEX_PATH = Path("amber_map_index.json")
THUMB_DIR = Path("thumbnails")
BACKUPS_DIR = Path("backups")
FOUNDRY_DATA_DIR = Path("~/Library/Application Support/FoundryVTT/Data").expanduser()

COMPENDIUM_SLUGS = {
    "Shadow Vault": "shadow-vault",
    "Milby's Maps Vol. 1": "milbys-vol-1",
    "Milby's Maps Vol. 2": "milbys-vol-2",
    "Milby's Maps Vol. 3": "milbys-vol-3",
}


def compendium_slug(name: str) -> str:
    if name in COMPENDIUM_SLUGS:
        return COMPENDIUM_SLUGS[name]
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "compendium"


def build_thumbnail(src_path: Path, thumb_path: Path):
    with Image.open(src_path) as img:
        thumb = img.copy()
        thumb.thumbnail((500, 500))
        if thumb.mode in ("RGBA", "P"):
            thumb = thumb.convert("RGB")
        thumb.save(thumb_path, format="JPEG", quality=80)


def plan_migration(records: List[dict]) -> dict:
    # Detect same-(compendium, stem) collisions so thumbnails never collide
    stem_counts: Dict[tuple, int] = {}
    for rec in records:
        comp = rec.get("compendium") or "Shadow Vault"
        stem = Path(rec["filename"]).stem
        key = (comp, stem)
        stem_counts[key] = stem_counts.get(key, 0) + 1

    # Also track how many records currently point to each existing thumb_filename
    old_thumb_refs: Dict[str, int] = {}
    for rec in records:
        tf = rec.get("thumb_filename", "")
        if tf:
            old_thumb_refs[tf] = old_thumb_refs.get(tf, 0) + 1

    actions = []
    migrated = []

    for rec in records:
        new_rec = dict(rec)
        changes = []

        # 1. Compendium & managed_by_vault
        if "compendium" not in new_rec or not new_rec["compendium"]:
            new_rec["compendium"] = "Shadow Vault"
            changes.append("add compendium='Shadow Vault'")

        if "managed_by_vault" not in new_rec:
            is_sv = new_rec["compendium"] == "Shadow Vault" and new_rec.get("relative_path", "").startswith("modules/shadow-vault/")
            new_rec["managed_by_vault"] = is_sv
            changes.append(f"add managed_by_vault={is_sv}")

        # 2. Legacy singular 'era' -> 'eras'
        if "era" in new_rec:
            legacy_era = new_rec.pop("era")
            existing_eras = new_rec.get("eras") or []
            if isinstance(legacy_era, list):
                merged = list(dict.fromkeys(existing_eras + legacy_era))
            elif isinstance(legacy_era, str) and legacy_era:
                merged = list(dict.fromkeys(existing_eras + [legacy_era]))
            else:
                merged = existing_eras
            new_rec["eras"] = merged
            changes.append(f"migrate era -> eras ({merged})")

        # 3. Guarantee required fields
        for list_field in ("eras", "environments", "structures", "features", "shadow_shift_cues"):
            if list_field not in new_rec:
                new_rec[list_field] = []
                changes.append(f"add {list_field}=[]")
        new_rec.setdefault("summary", "")
        new_rec.setdefault("grid_size", 100)
        new_rec.setdefault("curated", False)

        # 4. Compendium-prefixed thumb_filename
        comp = new_rec["compendium"]
        slug = compendium_slug(comp)
        fn = new_rec["filename"]
        stem = Path(fn).stem
        ext = Path(fn).suffix.lstrip(".").lower()

        is_stem_collision = stem_counts[(comp, stem)] > 1
        if is_stem_collision:
            desired_thumb = f"{slug}__{stem}_{ext}.jpg"
        else:
            desired_thumb = f"{slug}__{stem}.jpg"

        old_thumb = new_rec.get("thumb_filename", "")
        thumb_op = None
        if old_thumb != desired_thumb:
            new_rec["thumb_filename"] = desired_thumb
            changes.append(f"thumb: {old_thumb} -> {desired_thumb}")
            if is_stem_collision:
                thumb_op = {
                    "op": "rebuild_from_source",
                    "old_thumb": old_thumb,
                    "new_thumb": desired_thumb,
                    "relative_path": new_rec["relative_path"],
                }
            else:
                thumb_op = {
                    "op": "rename_or_copy",
                    "old_thumb": old_thumb,
                    "new_thumb": desired_thumb,
                    "relative_path": new_rec["relative_path"],
                    "shared_old_thumb": old_thumb_refs.get(old_thumb, 0) > 1,
                }

        migrated.append(new_rec)
        if changes or thumb_op:
            actions.append({
                "filename": fn,
                "compendium": comp,
                "changes": changes,
                "thumb_op": thumb_op,
            })

    return {
        "migrated": migrated,
        "actions": actions,
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate amber_map_index.json to multi-compendium schema.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files (default).")
    parser.add_argument("--apply", action="store_true", help="Back up index, update thumbnails, and write atomically.")
    args = parser.parse_args()

    if not INDEX_PATH.exists():
        print(f"Error: {INDEX_PATH} not found.")
        sys.exit(1)

    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    plan = plan_migration(records)
    actions = plan["actions"]
    migrated = plan["migrated"]

    add_comp = sum(1 for a in actions if any("add compendium" in c for c in a["changes"]))
    add_managed = sum(1 for a in actions if any("add managed_by_vault" in c for c in a["changes"]))
    fix_era = sum(1 for a in actions if any("migrate era" in c for c in a["changes"]))
    thumb_renames = sum(1 for a in actions if a["thumb_op"] and a["thumb_op"]["op"] == "rename_or_copy")
    thumb_rebuilds = sum(1 for a in actions if a["thumb_op"] and a["thumb_op"]["op"] == "rebuild_from_source")

    by_comp: Dict[str, int] = {}
    for r in migrated:
        by_comp[r["compendium"]] = by_comp.get(r["compendium"], 0) + 1

    print("=== MULTI-COMPENDIUM SCHEMA MIGRATION ===")
    print(f"Total records          : {len(migrated)}")
    print(f"By compendium          : {by_comp}")
    print(f"Records to modify      : {len(actions)}")
    print(f"  + add compendium     : {add_comp}")
    print(f"  + add managed_by_vault: {add_managed}")
    print(f"  + fix legacy era     : {fix_era}")
    print(f"  + prefix thumbnails  : {thumb_renames} rename/copy, {thumb_rebuilds} rebuild (stem collision)")

    if not args.apply:
        print("\n--- DRY RUN (No files modified) ---")
        for a in actions:
            if any("migrate era" in c for c in a["changes"]) or (a["thumb_op"] and a["thumb_op"]["op"] == "rebuild_from_source"):
                print(f"  [SPECIAL] {a['filename']}: {'; '.join(a['changes'])}")
        print("Run with --apply to execute migration.")
        return

    # 1. Backup index
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUPS_DIR / f"amber_map_index.pre_migrate.{ts}.json"
    shutil.copy2(INDEX_PATH, backup_path)
    print(f"\n[+] Backed up {INDEX_PATH} -> {backup_path}")

    # 2. Update thumbnails
    THUMB_DIR.mkdir(exist_ok=True)
    renamed_count = 0
    rebuilt_count = 0
    for a in actions:
        op = a["thumb_op"]
        if not op:
            continue
        dst = THUMB_DIR / op["new_thumb"]
        if dst.exists():
            continue
        src_old = THUMB_DIR / op["old_thumb"] if op["old_thumb"] else None
        if op["op"] == "rebuild_from_source":
            foundry_src = FOUNDRY_DATA_DIR / op["relative_path"]
            if foundry_src.is_file():
                build_thumbnail(foundry_src, dst)
                rebuilt_count += 1
            elif src_old and src_old.is_file():
                shutil.copy2(src_old, dst)
                renamed_count += 1
            else:
                print(f"  [!] Missing source for thumbnail rebuild: {op['relative_path']}")
        else:
            if src_old and src_old.is_file():
                if op["shared_old_thumb"]:
                    shutil.copy2(src_old, dst)
                else:
                    shutil.move(str(src_old), str(dst))
                renamed_count += 1
            else:
                foundry_src = FOUNDRY_DATA_DIR / op["relative_path"]
                if foundry_src.is_file():
                    build_thumbnail(foundry_src, dst)
                    rebuilt_count += 1
                else:
                    print(f"  [!] Missing thumbnail and source image for {a['filename']}")

    # 3. Sort cleanly & write atomically
    migrated.sort(key=lambda r: (r.get("compendium", ""), r.get("filename", "")))
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(migrated, f, indent=2)
    tmp_path.replace(INDEX_PATH)

    print(f"[+] Thumbnails updated : {renamed_count} moved/copied, {rebuilt_count} rebuilt")
    print(f"[+] Migrated index saved atomically to {INDEX_PATH}")


if __name__ == "__main__":
    main()
