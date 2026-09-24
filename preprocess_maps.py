import sys
from pathlib import Path
from PIL import Image, ImageEnhance

def enhance_maps(folder_path):
    target_path = Path(folder_path)
    valid_exts = {".webp", ".jpg", ".jpeg", ".png"}
    
    print(f"Applying subtle enhancement to maps in {target_path.name}...")
    
    for img_path in target_path.iterdir():
        if img_path.suffix.lower() in valid_exts:
            try:
                with Image.open(img_path) as img:
                    # 1. Bump Brightness by 15% (1.15)
                    bright_enhancer = ImageEnhance.Brightness(img)
                    img_bright = bright_enhancer.enhance(1.15)
                    
                    # 2. Bump Contrast by 15% (1.15)
                    contrast_enhancer = ImageEnhance.Contrast(img_bright)
                    final_img = contrast_enhancer.enhance(1.15)

                    # Save the result
                    final_img.save(img_path)
                    print(f"[+] Enhanced: {img_path.name}")
                    
            except Exception as e:
                print(f"[!] Error processing {img_path.name}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enhance_maps.py <path_to_maps>")
        sys.exit(1)
    enhance_maps(sys.argv[1])