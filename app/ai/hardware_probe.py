"""Best-effort hardware information; acceleration remains optional."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass

from app.platforms import MIN_CUDA_GLIBC, external_environment, glibc_version, require_supported_platform

log = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    cpu_name: str
    cpu_cores: int
    ram_gb: float
    gpu_name: str = ""
    backend: str = "cpu"
    vram_gb: float = 0


def _run(arguments):
    try:
        return subprocess.run(
            arguments, capture_output=True, text=True, timeout=8,
            env=external_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_gb():
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total_phys", "avail_phys", "total_page", "avail_page",
                    "total_virtual", "avail_virtual", "avail_extended",
                )
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total_phys / 1073741824
    elif sys.platform == "linux":
        try:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1073741824
        except (OSError, ValueError):
            pass
    return 0


def probe():
    require_supported_platform()
    hardware = HardwareProfile(platform.processor() or platform.machine(), os.cpu_count() or 1, _ram_gb())
    if sys.platform == "win32":
        raw = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if raw:
            try:
                name, memory = raw.splitlines()[0].rsplit(",", 1)
                hardware.gpu_name = name.strip()
                hardware.vram_gb = float(memory) / 1024
                hardware.backend = "cuda"
            except ValueError:
                pass
        if hardware.backend == "cpu":
            raw = _run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_VideoController | Select-Object Name | ConvertTo-Json -Compress"])
            try:
                cards = json.loads(raw)
                for card in cards if isinstance(cards, list) else [cards]:
                    name = str(card.get("Name", ""))
                    lower = name.lower()
                    if "nvidia" in lower:
                        hardware.gpu_name, hardware.backend = name, "cuda"
                        break
                    if "radeon" in lower or "amd" in lower:
                        hardware.gpu_name = name
                        if any(word in lower for word in ("rx ", "vega", "navi", "rdna", "graphics")):
                            hardware.backend = "vulkan"
                    elif "intel" in lower and hardware.backend == "cpu":
                        hardware.gpu_name = name
                        if "arc" in lower or "iris xe" in lower or re.search(r"uhd.*\b[7-9]\d\d\b", lower):
                            hardware.backend = "vulkan"
            except (ValueError, TypeError, AttributeError):
                pass
    else:
        raw = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if raw:
            try:
                name, memory = raw.splitlines()[0].rsplit(",", 1)
                hardware.gpu_name = name.strip()
                hardware.vram_gb = float(memory) / 1024
                hardware.backend = "cuda"
            except ValueError:
                pass
        if hardware.backend == "cuda" and glibc_version() < MIN_CUDA_GLIBC:
            # The pinned CUDA engine needs newer system libraries than the CPU engine.
            hardware.backend = "cpu"
            log.info("Using CPU AI: the Linux CUDA engine requires glibc 2.38 or newer.")
    return hardware
