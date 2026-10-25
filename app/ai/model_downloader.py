"""Streaming download helper shared by model and engine installation."""
import hashlib
import urllib.request
from pathlib import Path


def download_file(url: str, destination: Path, progress_cb=None, cancelled=None, *,
                  expected_size=None, expected_sha256=None):
    progress_cb = progress_cb or (lambda done, total, message: None)
    cancelled = cancelled or (lambda: False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "QuickFiles/1.0"})
    checksum = hashlib.sha256() if expected_sha256 else None
    try:
        if cancelled():
            raise InterruptedError("Download cancelled")
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as stream:
            total = int(response.headers.get("Content-Length", 0))
            done = 0
            while True:
                if cancelled():
                    raise InterruptedError("Download cancelled")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                if checksum:
                    checksum.update(chunk)
                done += len(chunk)
                progress_cb(done, total, f"{done / 1048576:.1f} MB downloaded")
        if cancelled():
            raise InterruptedError("Download cancelled")
        if not done or (total and done != total):
            raise ConnectionError("Download was incomplete")
        if expected_size is not None and done != expected_size:
            raise ConnectionError("The model download has the wrong size")
        if checksum and checksum.hexdigest() != expected_sha256:
            raise ValueError("The model download failed its SHA256 check")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
