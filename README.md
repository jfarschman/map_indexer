# Shadow Walk Map Indexer 

A suite of local Python tools designed to batch-process, enhance, and AI-tag TTRPG battlemaps for instant retrieval in Foundry VTT. 

## The Why
I am working on a DaggerHeart campaign frame where the players will (like the characters in the Zelazny *Chronicles of Amber*) have the power to walk through Shadow, manipulating their environment to travel across the multiverse. This mechanic requires the Gamemaster to rapidly produce highly specific battlemaps on the fly without breaking the narrative pacing.

Did I mention my second hobby is collecting battlemaps... but never knowing what I have?

This toolkit eliminates the cognitive load of manual map sorting. It uses the Gemini Vision API to analyze raw map images, score them on an Amber-to-Chaos alignment scale, and extract searchable visual features (e.g., "neon", "tracks", "cobblestone"). The resulting database allows a GM running a digital table (like a landscape TV) to pull up the perfect environment in seconds.

## The Toolkit (What & Where)
These scripts are designed to be run locally on macOS against a folder of raw image files.

* **`rotate_landscape.py`**: Detects portrait-oriented maps and mathematically rotates them 90 degrees to perfectly fit a landscape TV screen.
* **`preprocess_maps.py`**: Uses Pillow (`ImageEnhance`) to apply a subtle 15% bump to brightness and contrast, correcting dark maps without blowing out the midtones. I really don't like dark maps, but you can skip this if you want.
* **`tag_maps.py`**: The core AI engine. Analyzes the visual content of the maps via the `gemini-3.8-flash` vision model and outputs structured, queryable JSON metadata.
* **`vault_builder.py`**: Translates the indexed metadata into a format ingestible by Foundry VTT as a Scene Compendium.
* **`milby_lore_db.json` (Stub)**: A local metadata file containing author-specific lore/text. *Note: For copyright reasons, artist-provided text is not tracked in this repository. Users should generate their own lore JSON locally.*
* **`milby_lore_db.json` (Stub)**: Some locally addressable search tool (as yet unwritten)

## How to Use

### 1. Prerequisites
Ensure you have Python 3 installed, then install the required dependencies:
```bash
pip install google-genai pydantic pillow
```

You will need a free Google Gemini API key. Set it in your local environment:

```bash
export GEMINI_API_KEY="your-api-key"
```

### 2. Populate the Test Folder
Place a handful of map files (`.jpg`, `.png`, or `.webp`) into the `test-maps/` directory.

### 3. Pre-Process the Images
Optimize the maps for a TV display before generating metadata:

```bash
python3 rotate_landscape.py ./test-maps
python3 preprocess_maps.py ./test-maps
```

### 4. Generate the AI Metadata
Run the vision model to analyze the maps and generate the `amber_map_index.json` database:

```bash
python3 tag_maps.py ./test-maps
```

### 5. Build the Foundry Vault
Run the vault builder to package the maps for your VTT environment:

```bash
python3 vault_builder.py
```
