"""Streaming download helper shared by model and engine installation."""
import urllib.request
from pathlib import Path


def download_file(url: str, destination: Path, progress_cb=None, cancelled=None):
    progress_cb = progress_cb or (lambda done, total, message: None)
    cancelled = cancelled or (lambda: False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "QuickFiles/1.0"})
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
                done += len(chunk)
                progress_cb(done, total, f"{done / 1048576:.1f} MB downloaded")
        if cancelled():
            raise InterruptedError("Download cancelled")
        if not done or (total and done != total):
            raise ConnectionError("Download was incomplete")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
