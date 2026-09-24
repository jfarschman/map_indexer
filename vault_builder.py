import os
import sys
import shutil
import hashlib
from pathlib import Path

def get_file_hash(filepath: Path) -> str:
    """Generates an MD5 hash of the file to detect exact byte-for-byte duplicates."""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        # Read in chunks to handle large files efficiently
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def should_skip_variant(filename: str) -> bool:
    """Filters out unwanted map variants like Unfurnished or 140px."""
    name_lower = filename.lower()
    
    # We want to skip anything that is explicitly unfurnished
    if "unfurnished" in name_lower:
        return True
        
    # We want the 70px versions, so we skip the 140px high-res files
    if "140px" in name_lower:
        return True
        
    return False

def main():
    if len(sys.argv) < 3:
        print("Usage: python vault_builder.py <source_dir_1> [source_dir_2 ...] <destination_dir>")
        sys.exit(1)

    # The last argument is the destination
    dest_dir = Path(sys.argv[-1])
    # Everything before the last argument are source directories
    source_dirs = [Path(p) for p in sys.argv[1:-1]]

    # Ensure destination exists
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] Destination set to: {dest_dir}")

    seen_hashes = set()
    valid_extensions = {'.webp', '.jpg', '.jpeg', '.png'}
    
    total_found = 0
    total_copied = 0
    total_duplicates = 0
    total_skipped_variants = 0

    for src_dir in source_dirs:
        if not src_dir.exists() or not src_dir.is_dir():
            print(f"[!] Skipping invalid source directory: {src_dir}")
            continue
            
        print(f"\n[*] Scanning: {src_dir} (Max depth: 1)")
        
        # Collect files from the root dir AND exactly 1 folder deep
        files_to_check = []
        for item in src_dir.iterdir():
            if item.is_file():
                files_to_check.append(item)
            elif item.is_dir():
                for subitem in item.iterdir():
                    if subitem.is_file():
                        files_to_check.append(subitem)

        for filepath in files_to_check:
            if filepath.suffix.lower() in valid_extensions:
                total_found += 1
                
                # Check if it's a variant we want to ignore (unfurnished, 140px)
                if should_skip_variant(filepath.name):
                    print(f"    [-] Skipping unwanted variant: {filepath.name}")
                    total_skipped_variants += 1
                    continue
                
                # Check for exact file duplicates
                file_hash = get_file_hash(filepath)
                if file_hash in seen_hashes:
                    print(f"    [-] Skipping exact duplicate: {filepath.name}")
                    total_duplicates += 1
                    continue
                
                # We have a unique, desired file!
                seen_hashes.add(file_hash)
                
                # Handle potential filename collisions (different files, same name)
                dest_path = dest_dir / filepath.name
                counter = 1
                while dest_path.exists():
                    dest_path = dest_dir / f"{filepath.stem}_{counter}{filepath.suffix}"
                    counter += 1

                try:
                    # shutil.copy2 preserves metadata. On macOS, this takes advantage of APFS cloning.
                    shutil.copy2(filepath, dest_path)
                    print(f"    [+] Copied: {dest_path.name}")
                    total_copied += 1
                except Exception as e:
                    print(f"    [!] Error copying {filepath.name}: {e}")

    print("\n" + "="*40)
    print("         VAULT BUILD COMPLETE")
    print("="*40)
    print(f"Total images found:        {total_found}")
    print(f"Unwanted variants skipped: {total_skipped_variants}")
    print(f"Exact duplicates skipped:  {total_duplicates}")
    print(f"Unique maps copied:        {total_copied}")
    print(f"Destination:               {dest_dir}")

if __name__ == "__main__":
    main()