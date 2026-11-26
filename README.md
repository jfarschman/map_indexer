# Shadow Walk Map Indexer

A local Python + web toolkit to batch-process, AI-tag, curate, and search TTRPG battlemaps across a multi-compendium Master Index for Foundry VTT.

## The Why
I am working on a Daggerheart campaign frame where the players will (like the characters in Zelazny's *Chronicles of Amber*) have the power to walk through Shadow, manipulating their environment to travel across the multiverse. This mechanic requires the Gamemaster to rapidly produce highly specific battlemaps on the fly without breaking the narrative pacing.

Did I mention my second hobby is collecting battlemaps... but never knowing what I have?

This toolkit eliminates the cognitive load of manual map sorting. It uses the Gemini Vision API (`gemini-3.8-flash`) to analyze map images against a controlled vocabulary (`taxonomy.json`) and extract searchable visual features (e.g., `"neon"`, `"tracks"`, `"cobblestone"`) and sensory transition cues. The resulting Master Index (`amber_map_index.json`) lets a GM running a digital table (like a landscape TV) pull up the right map in seconds—whether it lives in your custom `Shadow Vault` compendium or inside a read-only third-party Foundry module (such as Milby's Maps Vol. 1–3).

---

## Safety Rules (Read-Only Third-Party Modules)
- **Never modify third-party Foundry modules:** Scripts never rotate, brighten, rename, or move files inside third-party module folders (e.g., `milbys-maps-patrons-module*`) so pre-built Foundry walls, lighting, and scenes stay intact.
- **`managed_by_vault` flag:** Every entry in `amber_map_index.json` carries `compendium` and `managed_by_vault` (`true` for your own `Shadow Vault` maps, `false` for read-only third-party modules).
- **Dry-runs, backups, and atomic writes:** Destructive or migration scripts default to `--dry-run`, save timestamped backups and undo manifests under `backups/` (gitignored), and write `amber_map_index.json` atomically via a temporary file.

---

## The Toolkit (What & Where)

### Core Indexing & Multi-Compendium Tools
- **`tag_maps.py`**: Core AI tagging engine (`gemini-3.8-flash` + structured output locked to `taxonomy.json`). Supports `--compendium "<name>"`, `--read-only`, `--dry-run`, `--rename` (blocked automatically on read-only/third-party modules), dynamic Foundry `relative_path` resolution, compendium-prefixed thumbnails (`<compendium-slug>__<stem>.jpg`), Milby variant collapsing (defaults to `140px` + `Unfurnished` when indexing Milby packs), and conditional `milby_lore_db.json` lore injection.
- **`audit_milby_dupes.py`**: Read-only deduplication auditor comparing `shadow-vault/images` against Milby's Maps Vol. 1–3 (`art/` only). Uses MD5 + 4-rotation 128-bit perceptual dHash + aspect-ratio verification + fuzzy name matching to catch copies that were rotated 90° or brightened by 15%. Defaults to `--dry-run` (writing `backups/milby_audit_report.json`); `--apply` backs up `amber_map_index.json`, re-points matched records in place (preserving `curated` metadata), and moves duplicate Shadow Vault images into `backups/deduped_shadow_vault_images/<timestamp>/` with an `undo_manifest.json`.
- **`migrate_index.py`**: Migrates `amber_map_index.json` to the multi-compendium schema (`compendium`, `managed_by_vault`, legacy `era` → `eras` cleanup, and compendium-prefixed thumbnail filenames). Defaults to `--dry-run`; `--apply` backs up the index and writes atomically.
- **`taxonomy.json`**: Controlled vocabulary authority file defining allowed `eras`, `environments`, and `structures`.
- **`milby_lore_db.json`** *(local/gitignored)*: Optional creator lore and URL lookup table used by `tag_maps.py` when indexing Milby compendiums. *Not tracked in Git for copyright reasons.*

### Curation & Search Web UI
- **`curator_server.py`**: Local Python HTTP server (`http://127.0.0.1:8080`) serving `curator.html`, `index.html`, JSON data, and `thumbnails/`, with an atomic `/api/save` endpoint that matches records uniquely by `relative_path` and locks third-party `managed_by_vault: false` attribution fields.
- **`curator.html`**: Keyboard-friendly curation UI (`⌘+Enter` to Save & Next) featuring a Compendium filter dropdown, curation-status filter, prominent Compendium & read-only module badges, an editable Compendium selector for splitting your own `Shadow Vault` maps into sub-compendiums, controlled-vocabulary pill toggles, and Creator Lore links.
- **`index.html`**: Fast GM-facing Master Index browser with free-text search, Compendium/Era/Environment/Structure dropdown filters, Curated-Only toggle, compendium badges on every card, and lazy-loaded thumbnails.

### Image Prep & Vault Maintenance (Shadow Vault Only)
- **`vault_builder.py`**: Copies and MD5-deduplicates raw battlemap images from one or more source folders (max depth 1) into a destination directory, skipping `unfurnished` and `140px` variants.
- **`rotate_landscape.py`**: Rotates portrait-oriented maps in a staging folder 90° to fit a landscape TV screen (respecting EXIF orientation).
- **`preprocess_maps.py`**: Applies a subtle `+15%` brightness and `+15%` contrast bump via Pillow (`ImageEnhance`) to brighten dark maps in a staging folder.
- **`scan_vault.py`**: Scans `shadow-vault/images` to register any untagged files, generate missing `shadow-vault__` thumbnails, rotate portrait Shadow Vault maps, and report orphaned managed entries. Refuses to run outside `shadow-vault` and leaves read-only third-party records untouched.
- **`find_dupes.py`**: Scans a single image folder for exact MD5 and visually similar perceptual dHash duplicates.

### Foundry VTT Integration
- **`module.json`**: Foundry VTT module manifest for `shadow-vault` defining the `Shadow Maps` (`shadow-vault.maps`) Scene Compendium.
- **`import-shadow-compendium.js`**: Foundry VTT macro that clears and rebuilds the `shadow-vault.maps` Scene Compendium in batches of 20 from `modules/shadow-vault/amber_map_index.json`. Automatically filters to `managed_by_vault !== false` so third-party compendiums (like Milby's Maps) are never duplicated into Shadow Vault.

### Legacy / One-Off Utilities
- **`build_thumbs.py`**: Standalone script to regenerate 500px JPEG thumbnails from a single folder. *(Normally handled automatically by `tag_maps.py`, `scan_vault.py`, and `migrate_index.py`.)*
- **`downscale_maps.py`**: Creates 2048px JPEG proxy copies for images over 5 MB. *(No longer required since `tag_maps.py` downscales in memory for API uploads.)*
- **`sizer.py`**: Early helper that backfilled `width`/`height` from a local `images/` directory.
- **`seed_from_legacy.py`**: One-time helper that pre-filled uncurated v2.0 `summary`/`features` fields from `legacy_amber_index.json`.

---

## Index Schema (`amber_map_index.json`)

Each entry in `amber_map_index.json` follows this multi-compendium schema:

```json
{
  "filename": "A-Dark-Place-VTT-140px-45x62.jpg",
  "thumb_filename": "milbys-vol-1__A-Dark-Place-VTT-140px-45x62.jpg",
  "relative_path": "modules/milbys-maps-patrons-module/art/A-Dark-Place-VTT-140px-45x62.jpg",
  "compendium": "Milby's Maps Vol. 1",
  "managed_by_vault": false,
  "title": "A Dark Place Shadowfell Manor",
  "width": 6300,
  "height": 8680,
  "grid_size": 140,
  "eras": ["Medieval", "Victorian / Industrial"],
  "environments": ["Forest / Jungle"],
  "structures": ["Settlement / Estate"],
  "summary": "A brooding gothic manor surrounded by dark woods...",
  "features": ["manor house", "courtyard", "iron gate"],
  "shadow_shift_cues": ["Cold mist curling across damp flagstones"],
  "curated": true,
  "source_url": "https://www.milbysmaps.com/...",
  "lore": "..."
}
```

---

## How to Use

### 1. Prerequisites
Install Python 3 and the required packages:
```bash
pip install google-genai pydantic pillow
```

Set your Gemini API key (required only when running `tag_maps.py` without `--dry-run`):
```bash
export GEMINI_API_KEY="your-api-key"
```

### 2. Adding New Maps to Your Own `Shadow Vault`
1. Place raw map images in `./staging/` (optional pre-processing):
   ```bash
   python3 rotate_landscape.py ./staging
   python3 preprocess_maps.py ./staging
   ```
2. Copy deduplicated maps into Foundry's `shadow-vault/images` folder:
   ```bash
   python3 vault_builder.py ./staging ~/Library/Application\ Support/FoundryVTT/Data/modules/shadow-vault/images
   ```
3. Preview and AI-tag new maps (optionally pass `--rename` to slugify filenames from AI titles):
   ```bash
   python3 tag_maps.py ~/Library/Application\ Support/FoundryVTT/Data/modules/shadow-vault/images --dry-run
   python3 tag_maps.py ~/Library/Application\ Support/FoundryVTT/Data/modules/shadow-vault/images --rename
   ```

### 3. Auditing & Indexing Read-Only Third-Party Modules (Milby's Maps)
If you previously copied any Milby maps into `shadow-vault/images`, run the read-only duplicate audit first:
```bash
python3 audit_milby_dupes.py --dry-run
# Review backups/milby_audit_report.json, then apply:
python3 audit_milby_dupes.py --apply --apply-mode repoint
```

To index remaining maps from Milby's Foundry modules (strictly read-only, collapsing `70px`/`140px` and Furnished/Unfurnished variants to canonical `140px` + `Unfurnished` files, and injecting `milby_lore_db.json`):
```bash
python3 tag_maps.py ~/Library/Application\ Support/FoundryVTT/Data/modules/milbys-maps-patrons-module/art --compendium "Milby's Maps Vol. 1" --read-only --dry-run
python3 tag_maps.py ~/Library/Application\ Support/FoundryVTT/Data/modules/milbys-maps-patrons-module-2/art --compendium "Milby's Maps Vol. 2" --read-only --dry-run
python3 tag_maps.py ~/Library/Application\ Support/FoundryVTT/Data/modules/milbys-maps-patrons-module-3/art --compendium "Milby's Maps Vol. 3" --read-only --dry-run
```
Remove `--dry-run` once the list looks right.

### 4. Curate & Search Locally
Start the local web server from the repository root:
```bash
python3 curator_server.py
```
Then open:
- **Curator UI:** `http://127.0.0.1:8080/curator.html` (tag, edit summaries, filter by compendium, or split your managed `Shadow Vault` maps into custom sub-compendiums)
- **Master Index:** `http://127.0.0.1:8080/index.html` (search and filter across all compendiums during a session)

### 5. Sync `Shadow Vault` Compendium in Foundry VTT
1. Ensure `module.json` is installed in `~/Library/Application Support/FoundryVTT/Data/modules/shadow-vault/` and enable **The Shadow Vault** module in Foundry.
2. Copy the latest `amber_map_index.json` into `~/Library/Application Support/FoundryVTT/Data/modules/shadow-vault/amber_map_index.json`.
3. Unlock the **Shadow Maps** (`shadow-vault.maps`) compendium in Foundry VTT.
4. Run the contents of `import-shadow-compendium.js` as a Script Macro inside Foundry VTT. It rebuilds `shadow-vault.maps` using only `managed_by_vault !== false` entries while leaving Milby's pre-walled compendiums untouched.
