"""Time the app's metadata scanner. AI content reading and planning are separate."""
import argparse
from pathlib import Path
import sys
from time import perf_counter

# Allow running this file directly from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULTS
from app.core.scanner import Scanner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", nargs="?", type=Path, default=Path.cwd(),
        help="Folder to scan recursively (default: current directory)",
    )
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    started = perf_counter()
    try:
        inventory = Scanner(directory, exclude_patterns=DEFAULTS["exclude_patterns"]).scan()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    elapsed = perf_counter() - started
    print(f"Directory: {directory}")
    print(f"Files scanned: {inventory.total_files:,}")
    print(f"Skipped: {inventory.skipped_count:,} | Errors: {inventory.error_count:,}")
    print(f"Scan time: {elapsed:.3f} seconds")


if __name__ == "__main__":
    main()
