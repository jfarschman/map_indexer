import sys
import json
from pathlib import Path
from PIL import Image

# Prevent decompression bomb warnings for giant battlemaps
Image.MAX_IMAGE_PIXELS = None

def main():
    if len(sys.argv) < 2:
        print("Usage: python build_thumbs.py <path_to_foundry_images_folder>")
        sys.exit(1)

    source_dir = Path(sys.argv[1])
    if not source_dir.is_dir():
        print(f"Error: {source_dir} is not a valid directory.")
        sys.exit(1)

    # Create local thumbnails directory
    thumb_dir = Path("thumbnails")
    thumb_dir.mkdir(exist_ok=True)

    index_path = Path("amber_map_index.json")
    if not index_path.exists():
        print("Error: amber_map_index.json not found in current directory.")
        sys.exit(1)

    with open(index_path, "r") as f:
        maps = json.load(f)

    print(f"Generating thumbnails for {len(maps)} maps...")
    success_count = 0

    for map_data in maps:
        original_filename = map_data.get("filename")
        if not original_filename:
            continue

        source_img_path = source_dir / original_filename
        if not source_img_path.exists():
            print(f"[-] Missing source image: {original_filename}")
            continue

        try:
            with Image.open(source_img_path) as img:
                thumb_img = img.copy()
                thumb_img.thumbnail((500, 500))
                
                # Ensure compatibility for transparency layers
                if thumb_img.mode in ("RGBA", "P"):
                    thumb_img = thumb_img.convert("RGB")
                
                # Standardize to JPG for the web app
                thumb_filename = f"{Path(original_filename).stem}.jpg"
                thumb_path = thumb_dir / thumb_filename
                
                thumb_img.save(thumb_path, format="JPEG", quality=80)
                
                # Inject the reference back into the JSON
                map_data["thumb_filename"] = thumb_filename
                success_count += 1
                
        except Exception as e:
            print(f"[!] Error processing {original_filename}: {e}")

    # Save the updated JSON
    with open(index_path, "w") as f:
        json.dump(maps, f, indent=2)

    print(f"\nDone! Successfully generated {success_count} thumbnails and updated JSON.")

if __name__ == "__main__":
    main()