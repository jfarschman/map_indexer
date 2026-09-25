import os
import sys
import time
import json
import re
import difflib
import random
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from PIL import Image

# Disable the Decompression Bomb warning for giant maps
Image.MAX_IMAGE_PIXELS = None

# 1. Expanded Metadata Schema
class MapMetadata(BaseModel):
    title: str = Field(description="Descriptive title of the map")
    era: str = Field(description="Technological era (e.g., Medieval/Sword, Victorian/Steampunk, Modern, Far-Future)")
    environment: str = Field(description="Primary biome or setting (e.g., Subterranean, Temperate Forest, Urban)")
    atmosphere: str = Field(description="Sensory and emotional tone")
    order_chaos_scale: int = Field(description="Amber-to-Chaos 1 to 5 scale (1=Amber, 5=Chaos)")
    features: List[str] = Field(description="Key visual anchors and physical objects")
    shadow_shift_cues: List[str] = Field(description="Short phrase cues a GM can listen for when walking Shadow")
    summary: str = Field(description="A 1-2 sentence GM overview of the map layout")
    source_url: Optional[str] = Field(default=None, description="The URL to the creator's post, if provided.")
    lore: Optional[str] = Field(default=None, description="The creator's narrative or historical notes, if provided.")

# 2. Base System Prompt
SYSTEM_PROMPT = """
You are a Virtual Tabletop map archivist specializing in the Chronicles of Amber RPG.
Analyze the provided battlemap image. Identify physical objects, environment, architectural cues, and lighting.
Evaluate where this location sits on the 1-5 Order vs Chaos scale:
- 1: Pure Order / Amber (solid, traditional, stone, majestic)
- 2: Near Amber (Earth-like, grounded physical laws, medieval to modern)
- 3: Mid-Shadow (diverse tech worlds, sci-fi, strange but rule-bound)
- 4: Near Chaos (warped physics, floating rocks, bleeding colors)
- 5: Courts of Chaos (raw primordial chaos, shifting fractal matter)

Extract specific, concrete visual anchors into 'features'.
If text 'lore' and a 'url' are provided in the prompt, include them accurately in the output.
"""

def clean_filename_for_matching(filename: str) -> str:
    """Strips extensions and common VT map suffixes to help matching."""
    name = Path(filename).stem.lower()
    # Remove common suffixes like _grid, _nogrid, _50x50, _night, etc.
    name = re.sub(r'(_grid|_nogrid|_dark|_night|_day|\d+x\d+|-.*$\vert{}_.*$)', '', name)
    return name.replace('_', ' ').replace('-', ' ').strip()

def find_lore_match(filename: str, lore_db: dict) -> dict:
    """Uses difflib to find a highly accurate match, preventing single-word collisions."""
    cleaned_name = clean_filename_for_matching(filename)
    keys = list(lore_db.keys())
    
    # Require at least 60% structural similarity to consider it a match
    matches = difflib.get_close_matches(cleaned_name, keys, n=1, cutoff=0.6)
    
    if matches:
        return lore_db[matches[0]]
    
    # Fallback: if difflib fails, require at least TWO shared words
    best_match = None
    highest_overlap = 0
    file_words = set(cleaned_name.split())
    
    for key in keys:
        key_words = set(key.lower().split())
        overlap = len(file_words.intersection(key_words))
        if overlap > highest_overlap and overlap >= 2:
            highest_overlap = overlap
            best_match = key
            
    if best_match:
        return lore_db[best_match]
        
    return None

def analyze_map(client: genai.Client, image_path: Path, matched_lore: dict) -> dict:
    print(f"[*] Processing: {image_path.name}...")
    
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    suffix = image_path.suffix.lower()
    mime_type = "image/webp" if suffix == ".webp" else ("image/png" if suffix == ".png" else "image/jpeg")

    user_prompt = "Analyze this battlemap and generate the structured metadata."
    if matched_lore:
        print(f"    [+] Found matching lore. Injecting into prompt.")
        user_prompt += f"\n\nCreator URL: {matched_lore.get('link', '')}\nCreator Lore: {matched_lore.get('lore', '')}"

    max_retries = 7  # Increased for backoff
    base_wait = 4    # Base wait time in seconds

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    user_prompt
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=MapMetadata,
                    temperature=0.2,
                )
            )
            
            data = json.loads(response.text)
            data["filename"] = image_path.name
            data["relative_path"] = str(image_path)
            return data
            
        except Exception as e:
            error_msg = str(e)
            if ("503" in error_msg or "429" in error_msg) and attempt < max_retries - 1:
                wait_time = (base_wait * (2 ** attempt)) + random.uniform(0, 2)
                print(f"    [-] Server busy. Backing off for {wait_time:.1f}s (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise e

def get_foundry_relative_path(file_path: Path) -> str:
    """Extracts the Foundry-friendly relative path starting from 'modules'."""
    try:
        parts = file_path.parts
        modules_index = parts.index('modules')
        return str(Path(*parts[modules_index:]))
    except ValueError:
        # Fallback if the script is run in a folder not containing 'modules'
        return file_path.name

def slugify_filename(title: str, original_path: Path) -> Path:
    """Converts a title like 'Stygian City - Abyss' into 'stygian_city_abyss.jpg'"""
    clean_name = re.sub(r'[^a-z0-9\s-]', '', title.lower())
    clean_name = re.sub(r'[\s-]+', '_', clean_name).strip('_')
    
    if not clean_name:
        clean_name = f"map_{int(time.time())}"
        
    new_filename = f"{clean_name}{original_path.suffix.lower()}"
    new_path = original_path.parent / new_filename
    
    counter = 1
    while new_path.exists() and new_path != original_path:
        new_path = original_path.parent / f"{clean_name}_{counter}{original_path.suffix.lower()}"
        counter += 1
        
    return new_path

def get_foundry_relative_path(file_path: Path) -> str:
    """Extracts the Foundry-friendly relative path starting from 'modules'."""
    try:
        parts = file_path.parts
        modules_index = parts.index('modules')
        return str(Path(*parts[modules_index:]))
    except ValueError:
        return file_path.name

def main():
    if len(sys.argv) < 2:
        print("Usage: python tag_maps.py <path_to_maps_folder_or_image>")
        sys.exit(1)

    target_path = Path(sys.argv[1])
    client = genai.Client()
    results = []

    # Safe Load Lore DB
    lore_db = {}
    lore_file = Path("milby_lore_db.json")
    if lore_file.exists():
        print("[*] Found milby_lore_db.json, attempting to load...")
        try:
            with open(lore_file, "r") as f:
                content = f.read().strip()
                if content.startswith("```json"):
                    content = content[7:]
                elif content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                if content:
                    lore_db = json.loads(content)
                    print(f"[*] Successfully loaded {len(lore_db)} lore entries.")
        except json.JSONDecodeError as e:
            print(f"[!] Could not parse milby_lore_db.json (invalid JSON). Proceeding without lore.")

    if target_path.is_file():
        image_files = [target_path]
    else:
        valid_exts = {".webp", ".jpg", ".jpeg", ".png"}
        # Build the list, then slice the first 5 items
        # image_files = [p for p in target_path.iterdir() if p.suffix.lower() in valid_exts][:5]
        image_files = [p for p in target_path.iterdir() if p.suffix.lower() in valid_exts]

    print(f"Found {len(image_files)} map(s) to process in this test batch.")

    output_file = Path("amber_map_index.json")
    if output_file.exists():
        with open(output_file, "r") as f:
            try:
                results = json.load(f)
            except Exception:
                results = []

    processed_names = {r["filename"] for r in results}

    for img_path in image_files:
        if img_path.name in processed_names:
            print(f"[-] Skipping {img_path.name} (already indexed)")
            continue

        try:
            matched_lore = find_lore_match(img_path.name, lore_db)
            
            # 1. AI Analyzes the original, un-rotated image
            metadata = analyze_map(client, img_path, matched_lore)
            
            # 2. Rename the file based on AI metadata
            new_file_path = slugify_filename(metadata['title'], img_path)
            if new_file_path != img_path:
                img_path.rename(new_file_path)
                print(f"    [~] Renamed file to: {new_file_path.name}")
            
            # 3. Update paths in metadata
            metadata['filename'] = new_file_path.name
            metadata['relative_path'] = get_foundry_relative_path(new_file_path)
            
            # 4. Extract Dimensions & Rotate if Portrait
            try:
                with Image.open(new_file_path) as img:
                    width, height = img.size
                    
                    # If it's a portrait map, rotate it for VTT use
                    if height > width:
                        print(f"    [*] Rotating {new_file_path.name} to landscape (Was: {width}x{height})")
                        img = img.transpose(Image.Transpose.ROTATE_90)
                        img.save(new_file_path) # Overwrite the file with the rotated version
                        width, height = img.size # Update to the new dimensions
                        
                    metadata["width"], metadata["height"] = width, height
            except Exception as e:
                print(f"    [!] Could not read/rotate {new_file_path.name}: {e}")
                metadata["width"], metadata["height"] = 4000, 3000

            results.append(metadata)
            
            with open(output_file, "w") as f:
                json.dump(results, f, indent=2)

            print(f"[+] Successfully indexed: {metadata['title']}")
            time.sleep(0.5) 

        except Exception as e:
            print(f"[!] Error analyzing {img_path.name}: {e}")

    print(f"\nDone! Indexed {len(results)} total maps.")

if __name__ == "__main__":
    main()