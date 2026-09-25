import os
import sys
from pathlib import Path
from PIL import Image

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 downscale_maps.py <path_to_maps_folder>")
        sys.exit(1)

    source_folder = Path(sys.argv[1])
    
    # Create a new folder next to the original one for the downscaled versions
    downscaled_folder = source_folder.parent / (source_folder.name + "_downscaled_proxies")
    downscaled_folder.mkdir(exist_ok=True)
    
    # Trigger downscaling if the file is over 5 MB
    SIZE_THRESHOLD = 5 * 1024 * 1024 
    # Max width or height for the AI (2048px is plenty for vision analysis)
    MAX_DIMENSION = 2048

    valid_exts = {".webp", ".jpg", ".jpeg", ".png"}
    large_files = []

    print(f"Scanning '{source_folder}' for maps larger than 5MB...")

    for img_path in source_folder.iterdir():
        if img_path.is_file() and img_path.suffix.lower() in valid_exts:
            file_size = img_path.stat().st_size
            
            if file_size > SIZE_THRESHOLD:
                print(f"[*] Downscaling {img_path.name} ({file_size / (1024*1024):.1f} MB)...")
                
                try:
                    with Image.open(img_path) as img:
                        # Convert to RGB (strips transparency, which JPEG doesn't support)
                        if img.mode in ("RGBA", "P"):
                            img = img.convert("RGB")
                        
                        # thumbnail maintains the aspect ratio while fitting within the max bounds
                        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
                        
                        # Save as a lightweight JPEG
                        new_name = img_path.stem + "_proxy.jpg"
                        dest_path = downscaled_folder / new_name
                        
                        img.save(dest_path, "JPEG", quality=80)
                        
                        large_files.append(img_path.name)
                except Exception as e:
                    print(f"[!] Failed to process {img_path.name}: {e}")

    # Write the segregation list to a text file
    list_path = Path("large_maps_list.txt")
    with open(list_path, "w") as f:
        for name in large_files:
            f.write(f"{name}\n")

    print("\n--- Summary ---")
    print(f"Found and downscaled {len(large_files)} large maps.")
    print(f"Proxy copies saved to: {downscaled_folder}")
    print(f"Text list of original files saved to: {list_path}")

if __name__ == "__main__":
    main()