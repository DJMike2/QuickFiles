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
    parser.add_argument("--threads", type=int, choices=range(65), default=0,
                        metavar="0-64", help="Native worker count (0: storage-aware default)")
    parser.add_argument("--repeat", type=int, default=1, help="Number of timed runs (default: 1)")
    parser.add_argument("--max-files", type=int, default=DEFAULTS["max_files_per_scan"],
                        help="Maximum inventory size")
    args = parser.parse_args()
    if args.repeat < 1 or args.max_files < 1:
        parser.error("--repeat and --max-files must be positive")
    directory = args.directory.expanduser().resolve()
    for run in range(args.repeat):
        scanner = Scanner(directory, exclude_patterns=DEFAULTS["exclude_patterns"],
                          threads=args.threads, max_files=args.max_files)
        started = perf_counter()
        try:
            inventory = scanner.scan()
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        elapsed = perf_counter() - started
        if args.repeat > 1:
            print(f"Run: {run + 1}/{args.repeat}")
        print(f"Directory: {directory}")
        print(f"Scanner: {scanner.binary_path} | Threads: {scanner.native_threads}")
        print(f"Files scanned: {inventory.total_files:,}")
        print(f"Skipped: {inventory.skipped_count:,} | Errors: {inventory.error_count:,}")
        print(f"Scan time: {elapsed:.3f} seconds")


if __name__ == "__main__":
    main()
