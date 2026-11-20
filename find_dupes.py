import sys
import hashlib
from pathlib import Path
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# Hamming distance threshold (0 = identical, 1-6 = near-duplicate / resized / format diff)
MAX_DISTANCE = 6


def compute_dhash(img: Image.Image, hash_size: int = 8) -> int:
    """Compute a 64-bit difference hash, normalizing portrait to landscape in memory."""
    if img.height > img.width:
        img = img.transpose(Image.Transpose.ROTATE_90)
    resized = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())
    
    diff_hash = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            left = pixels[row_start + col]
            right = pixels[row_start + col + 1]
            diff_hash = (diff_hash << 1) | (1 if left > right else 0)
    return diff_hash


def hamming_distance(h1: int, h2: int) -> int:
    return (h1 ^ h2).bit_count()


def format_size(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 find_dupes.py <path_to_images_folder>")
        sys.exit(1)

    target_dir = Path(sys.argv[1]).expanduser().resolve()
    if not target_dir.is_dir():
        print(f"Error: {target_dir} is not a valid directory.")
        sys.exit(1)

    files = sorted(
        p for p in target_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTS and not p.name.startswith(".")
    )

    print(f"Hashing {len(files)} images in {target_dir}...")

    records = []
    for p in files:
        try:
            md5 = hashlib.md5(p.read_bytes()).hexdigest()
            with Image.open(p) as img:
                w, h = img.size
                dhash = compute_dhash(img)
            records.append({
                "path": p,
                "name": p.name,
                "size": p.stat().st_size,
                "dims": f"{w}x{h}",
                "md5": md5,
                "dhash": dhash,
            })
        except Exception as e:
            print(f"[!] Could not read {p.name}: {e}")

    # Compare all pairs
    exact_matches = []
    near_matches = []

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            a, b = records[i], records[j]
            if a["md5"] == b["md5"]:
                exact_matches.append((a, b))
            else:
                dist = hamming_distance(a["dhash"], b["dhash"])
                if dist <= MAX_DISTANCE:
                    near_matches.append((dist, a, b))

    print("\n=== EXACT DUPLICATES (Identical Files) ===")
    if not exact_matches:
        print("None found.")
    for a, b in exact_matches:
        print(f"  * {a['name']} ({a['dims']}, {format_size(a['size'])})")
        print(f"    {b['name']} ({b['dims']}, {format_size(b['size'])})\n")

    print("=== NEAR DUPLICATES (Visually Similar) ===")
    if not near_matches:
        print("None found.")
    for dist, a, b in sorted(near_matches, key=lambda x: x[0]):
        print(f"  [Distance: {dist}]")
        print(f"  * {a['name']} ({a['dims']}, {format_size(a['size'])})")
        print(f"    {b['name']} ({b['dims']}, {format_size(b['size'])})\n")


if __name__ == "__main__":
    main()
