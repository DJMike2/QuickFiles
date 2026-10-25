"""Build the C++ scanner for this platform, or cross-compile Windows with MinGW."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app/core/native/fastscan_mt.cpp"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("linux", "windows"),
                        default="windows" if sys.platform == "win32" else "linux")
    parser.add_argument("--cxx", help="C++17 compiler executable (also accepts CXX)")
    parser.add_argument("--output", type=Path, help="Override the output binary path")
    args = parser.parse_args()
    if sys.platform not in {"win32", "linux"}:
        parser.error("Scanner builds support only Windows and Linux")
    default_compiler = "g++"
    if args.target == "windows" and sys.platform != "win32":
        default_compiler = ("x86_64-w64-mingw32-g++-posix" if shutil.which("x86_64-w64-mingw32-g++-posix")
                            else "x86_64-w64-mingw32-g++")
    compiler = args.cxx or os.environ.get("CXX") or default_compiler
    if not shutil.which(compiler):
        parser.error(f"Compiler not found: {compiler}. Install g++ (MinGW-w64 POSIX threads for Windows).")
    triple = subprocess.check_output([compiler, "-dumpmachine"], text=True).strip()
    if ((args.target == "windows") != ("mingw" in triple) or
        (args.target == "linux" and "linux" not in triple)):
        parser.error(f"Compiler target {triple!r} does not match --target {args.target}")
    output = args.output or SOURCE.parent / "bin" / ("fastscan.exe" if args.target == "windows" else "fastscan")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = ["-O2", "-std=c++17", "-s", "-pthread", "-flto", "-Wall", "-Wextra"]
    flags.append("-static")
    with tempfile.TemporaryDirectory(prefix="fastscan-build-", dir=output.parent) as temporary:
        binary = Path(temporary) / output.name
        command = [compiler, *flags, str(SOURCE), "-o", str(binary)]
        if args.target == "windows":
            command.append("-lshell32")
        print(shlex.join(command), flush=True)
        subprocess.run(command, check=True)
        binary.replace(output)
    print(f"Built {output}")


if __name__ == "__main__":
    main()
