"""Install the same llama.cpp release used to test the Qwen models."""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from app.ai.model_downloader import download_file
from app.platforms import require_supported_platform

log = logging.getLogger(__name__)
ENGINE_RELEASE = "b11869"


class BinaryManager:
    def __init__(self, app_data_dir, hardware):
        require_supported_platform()
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
        require_supported_platform()
        arm = platform.machine().lower() in {"arm64", "aarch64"}
        arch = "arm64" if arm else "x64"
        if sys.platform == "win32":
            return [rf"^llama-{ENGINE_RELEASE}-bin-win-cpu-{arch}\.zip$"]
        if sys.platform.startswith("linux"):
            return [rf"^llama-{ENGINE_RELEASE}-bin-ubuntu-{arch}\.tar\.gz$"]
        raise RuntimeError("No automatic AI engine package is available for this operating system")

    def download(self, progress_cb=None, cancelled=None, status_cb=None):
        cancelled = cancelled or (lambda: False)
        status_cb = status_cb or (lambda message: None)
        patterns = self._platform_patterns()
        if self.hardware.os_warning:
            raise RuntimeError(self.hardware.os_warning)
        status_cb(f"Checking llama.cpp {ENGINE_RELEASE}…")
        request = urllib.request.Request(
            f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{ENGINE_RELEASE}",
            headers={"User-Agent": "QuickFiles/1.0", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            assets = json.load(response).get("assets", [])

        def find_asset(patterns):
            return next((asset for pattern in patterns for asset in assets
                         if re.fullmatch(pattern, asset["name"], re.IGNORECASE)), None)

        cpu_asset = find_asset(patterns)
        if cpu_asset is None:
            raise RuntimeError("This engine release has no matching CPU build for this computer")
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        def install(asset, kind, libraries_only=False):
            if cancelled():
                raise InterruptedError("Installation cancelled")
            status_cb(f"Downloading {asset['name']}…")
            with tempfile.TemporaryDirectory(prefix="install-", dir=self.bin_dir) as staging:
                stage = Path(staging)
                package = download_file(asset["browser_download_url"], stage / asset["name"],
                                        progress_cb, cancelled)
                extracted = stage / "files"
                extracted.mkdir()

                def unpack(name, stream=None, link=None):
                    if cancelled():
                        raise InterruptedError("Installation cancelled")
                    name = Path(name).name
                    if name in {"", ".", ".."}:
                        return
                    if libraries_only and not (name.lower().endswith(".dll") or ".so" in name):
                        return
                    target = extracted / name
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    if link is not None:
                        # Keep aliases inside the flattened engine directory.
                        alias = Path(link).name
                        if alias in {"", ".", ".."} or alias == name:
                            raise ValueError("Invalid engine library alias")
                        target.symlink_to(alias)
                    else:
                        with target.open("wb") as output:
                            shutil.copyfileobj(stream, output)
                        if sys.platform != "win32":
                            target.chmod(0o755)

                if zipfile.is_zipfile(package):
                    with zipfile.ZipFile(package) as archive:
                        for member in archive.infolist():
                            if not member.is_dir():
                                with archive.open(member) as stream:
                                    unpack(member.filename, stream)
                else:
                    with tarfile.open(package, "r:gz") as archive:
                        for member in archive:
                            if member.isfile():
                                with archive.extractfile(member) as stream:
                                    unpack(member.name, stream)
                            elif member.issym() or member.islnk():
                                unpack(member.name, link=member.linkname)
                if not libraries_only and not (extracted / self.server_name).is_file():
                    raise RuntimeError("The engine package contains no llama-server")
                destination = self.bin_dir / kind
                if libraries_only:
                    destination.mkdir(exist_ok=True)
                    for item in extracted.iterdir():
                        item.replace(destination / item.name)
                else:
                    if destination.exists():
                        shutil.rmtree(destination)
                    extracted.replace(destination)

        release_file = self.bin_dir / "release.txt"
        installed_release = release_file.read_text().strip() if release_file.is_file() else ""
        upgrade = installed_release != ENGINE_RELEASE
        if upgrade or not self.is_available:
            install(cpu_asset, "cpu")
        arm = platform.machine().lower() in {"arm64", "aarch64"}
        arch = "arm64" if arm else "x64"
        variant = self.hardware.backend
        if sys.platform in {"win32", "linux"} and variant in {"cuda", "vulkan"}:
            if upgrade or not self.has_gpu_build:
                system = "win" if sys.platform == "win32" else "ubuntu"
                suffix = r"\.zip" if system == "win" else r"\.tar\.gz"
                cuda_version = "13.4" if arm else "12.4" if system == "win" else "12.8"
                tag = f"cuda-{cuda_version}" if variant == "cuda" else "vulkan"
                gpu = find_asset([rf"^llama-{ENGINE_RELEASE}-bin-{system}-{re.escape(tag)}-{arch}{suffix}$"])
                if gpu:
                    try:
                        install(gpu, "gpu")
                        if variant == "cuda":
                            runtime = find_asset([
                                rf"^cudart-llama(?:-{ENGINE_RELEASE})?-bin-{system}-cuda-{re.escape(cuda_version)}-{arch}{suffix}$"
                            ])
                            if runtime:
                                install(runtime, "gpu", libraries_only=True)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        log.warning("Optional GPU installation failed: %s", exc)
                        status_cb("GPU build unavailable; the CPU engine remains available")
        release_file.write_text(ENGINE_RELEASE + "\n")
        return self.cpu_binary_path
