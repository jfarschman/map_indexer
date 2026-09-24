import sys
from pathlib import Path
from PIL import Image

def ensure_landscape(folder_path):
    target_path = Path(folder_path)
    valid_exts = {".webp", ".jpg", ".jpeg", ".png"}
    
    if not target_path.is_dir():
        print(f"Error: {folder_path} is not a valid directory.")
        return

    print(f"Scanning {target_path.name} for portrait maps...")
    
    rotated_count = 0
    for img_path in target_path.iterdir():
        if img_path.suffix.lower() in valid_exts:
            try:
                with Image.open(img_path) as img:
                    width, height = img.size
                    
                    if height > width:
                        print(f"[*] Rotating {img_path.name} (Was: {width}x{height})")
                        # Transpose.ROTATE_90 rotates 90 degrees counter-clockwise
                        rotated_img = img.transpose(Image.Transpose.ROTATE_90)
                        
                        # Overwrite the original file
                        rotated_img.save(img_path)
                        rotated_count += 1
                        
            except Exception as e:
                print(f"[!] Error processing {img_path.name}: {e}")

    print(f"\nDone! Rotated {rotated_count} maps to landscape.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 rotate_landscape.py <path_to_maps_folder>")
        sys.exit(1)
        
    ensure_landscape(sys.argv[1])