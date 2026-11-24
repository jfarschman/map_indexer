import sys
from pathlib import Path
from PIL import Image, ImageEnhance

Image.MAX_IMAGE_PIXELS = None
VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
BRIGHTNESS_FACTOR = 1.15
CONTRAST_FACTOR = 1.15


def save_image(img: Image.Image, path: Path):
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(path, quality=92, subsampling=0)
    elif ext == ".webp":
        img.save(path, quality=92)
    else:
        img.save(path)


def main():
    target_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "staging").expanduser().resolve()
    if not target_dir.is_dir():
        print(f"Error: {target_dir} is not a valid directory.")
        sys.exit(1)

    files = sorted(
        p for p in target_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
    )

    processed = 0
    for img_path in files:
        try:
            with Image.open(img_path) as img:
                if img.mode == "P":
                    img = img.convert("RGBA" if "transparency" in img.info else "RGB")
                elif img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")

                img_bright = ImageEnhance.Brightness(img).enhance(BRIGHTNESS_FACTOR)
                final_img = ImageEnhance.Contrast(img_bright).enhance(CONTRAST_FACTOR)
                save_image(final_img, img_path)
                processed += 1
                print(f"[+] Enhanced: {img_path.name}")
        except Exception as e:
            print(f"[!] Failed on {img_path.name}: {e}")

    print(f"\nDone. Enhanced {processed} / {len(files)} maps in {target_dir.name}/.")


if __name__ == "__main__":
    main()