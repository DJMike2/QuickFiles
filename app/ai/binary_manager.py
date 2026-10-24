"""Install a matching llama.cpp engine and optional GPU build."""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from app.ai.model_downloader import download_file

log = logging.getLogger(__name__)


class BinaryManager:
    def __init__(self, app_data_dir, hardware):
        self.bin_dir = app_data_dir / "bin"
        self.hardware = hardware
        self.server_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"

    def _binary(self, kind):
        directory = self.bin_dir / kind
        direct = directory / self.server_name
        if direct.is_file():
            return direct
        return next(directory.rglob(self.server_name), direct) if directory.is_dir() else direct

    @property
    def cpu_binary_path(self):
        return self._binary("cpu")

    @property
    def gpu_binary_path(self):
        path = self._binary("gpu")
        return path if path.is_file() and os.access(path, os.X_OK) else None

    @property
    def is_available(self):
        return self.cpu_binary_path.is_file() and os.access(self.cpu_binary_path, os.X_OK)

    @property
    def has_gpu_build(self):
        return self.gpu_binary_path is not None

    def _platform_patterns(self):
        arm = platform.machine().lower() in {"arm64", "aarch64"}
        if sys.platform == "win32":
            return [r"^llama-.*-bin-win-arm64\.zip$"] if arm else [
                r"^llama-.*-bin-win-avx2-x64\.zip$", r"^llama-.*-bin-win-avx-x64\.zip$",
                r"^llama-.*-bin-win-x64\.zip$",
            ]
        if sys.platform == "darwin":
            return [rf"^llama-.*-bin-macos-{'arm64' if arm else 'x64'}\.zip$"]
        raise RuntimeError("Automatic AI installation requires Windows or macOS")

    def download(self, progress_cb=None, cancelled=None, status_cb=None):
        cancelled = cancelled or (lambda: False)
        status_cb = status_cb or (lambda message: None)
        patterns = self._platform_patterns()
        if self.hardware.os_warning and sys.platform in {"win32", "darwin"}:
            raise RuntimeError(self.hardware.os_warning)
        status_cb("Checking the latest llama.cpp release…")
        request = urllib.request.Request(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
            headers={"User-Agent": "QuickFiles/1.0", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            assets = json.load(response).get("assets", [])

        def find_asset(patterns):
            for pattern in patterns:
                for asset in assets:
                    if re.fullmatch(pattern, asset["name"], re.IGNORECASE):
                        return asset
            return None

        cpu_asset = find_asset(patterns)
        if cpu_asset is None:
            raise RuntimeError("This llama.cpp release has no matching CPU build for this computer")
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        def install(asset, kind, dlls_only=False):
            if cancelled():
                raise InterruptedError("Installation cancelled")
            status_cb(f"Downloading {asset['name']}…")
            # Staging keeps partial installations out of the active engine directory.
            with tempfile.TemporaryDirectory(prefix="install-", dir=self.bin_dir) as staging:
                stage = Path(staging)
                package = download_file(asset["browser_download_url"], stage / "engine.zip", progress_cb, cancelled)
                extracted = stage / "files"
                extracted.mkdir()
                with zipfile.ZipFile(package) as archive:
                    for member in archive.infolist():
                        if cancelled():
                            raise InterruptedError("Installation cancelled")
                        if member.is_dir():
                            continue
                        name = Path(member.filename).name
                        if name in {"", ".", ".."} or (dlls_only and not name.lower().endswith(".dll")):
                            continue
                        target = extracted / name
                        with archive.open(member) as original, target.open("wb") as output:
                            shutil.copyfileobj(original, output)
                        if sys.platform != "win32":
                            target.chmod(0o755)
                if not dlls_only and not (extracted / self.server_name).is_file():
                    raise RuntimeError("The downloaded engine package contains no llama-server")
                destination = self.bin_dir / kind
                destination.mkdir(exist_ok=True)
                for file in extracted.iterdir():
                    file.replace(destination / file.name)

        if not self.is_available:
            install(cpu_asset, "cpu")
        machine = platform.machine().lower()
        if sys.platform == "win32" and machine not in {"arm64", "aarch64"}:
            variant = self.hardware.backend
            if variant in {"cuda", "vulkan"} and not self.has_gpu_build:
                gpu = find_asset([rf"^llama-.*-bin-win-{variant}.*x64\.zip$"])
                if gpu:
                    try:
                        install(gpu, "gpu")
                        if variant == "cuda":
                            version = re.search(r"cuda-([\d.]+)", gpu["name"])
                            runtime = find_asset([
                                rf"^cudart-llama-bin-win-cuda-{re.escape(version.group(1))}.*x64\.zip$"
                            ]) if version else None
                            if runtime:
                                install(runtime, "gpu", dlls_only=True)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        log.warning("Optional GPU installation failed: %s", exc)
                        status_cb("GPU build unavailable; the CPU engine remains available")
        return self.cpu_binary_path
