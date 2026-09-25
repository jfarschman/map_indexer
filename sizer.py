import json
from pathlib import Path
from PIL import Image

# Disable the Decompression Bomb warning for giant maps
Image.MAX_IMAGE_PIXELS = None

# 1. Load the existing JSON
index_path = Path("amber_map_index.json")
with open(index_path, "r") as f:
    maps = json.load(f)

print(f"Adding dimensions to {len(maps)} maps...")

# 2. Add Width and Height instantly
for map_data in maps:
    img_path = Path("images") / map_data["filename"]
    
    if img_path.exists():
        with Image.open(img_path) as img:
            map_data["width"], map_data["height"] = img.size
    else:
        print(f"[!] Could not find {img_path} - using fallback dimensions.")
        map_data["width"], map_data["height"] = 4000, 3000

# 3. Save it back
with open(index_path, "w") as f:
    json.dump(maps, f, indent=2)

print("Done! Dimensions added successfully.")