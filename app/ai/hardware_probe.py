"""Best-effort hardware information; acceleration remains optional."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    cpu_name: str
    cpu_cores: int
    ram_gb: float
    gpu_name: str = ""
    backend: str = "cpu"
    vram_gb: float = 0
    os_warning: str = ""

    @property
    def recommended_mode(self):
        return "accurate" if self.vram_gb >= 4 or self.ram_gb >= 16 else "fast"


def _run(arguments):
    try:
        return subprocess.run(
            arguments, capture_output=True, text=True, timeout=8,
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
    elif sys.platform == "darwin":
        value = _run(["sysctl", "-n", "hw.memsize"])
        if value.isdigit():
            return int(value) / 1073741824
    else:
        try:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1073741824
        except (OSError, ValueError):
            pass
    return 0


def probe():
    hardware = HardwareProfile(platform.processor() or platform.machine(), os.cpu_count() or 1, _ram_gb())
    if sys.platform == "win32":
        if sys.getwindowsversion().major < 10:
            hardware.os_warning = "Automatic AI installation requires Windows 10 or newer."
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
    elif sys.platform == "darwin":
        hardware.cpu_name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or hardware.cpu_name
        if platform.machine().lower() in {"arm64", "aarch64"}:
            hardware.gpu_name, hardware.backend = "Apple Silicon", "metal"
            hardware.vram_gb = hardware.ram_gb / 2
        version = platform.mac_ver()[0]
        if version and int(version.split(".")[0]) < 12:
            hardware.os_warning = "Automatic AI installation requires macOS 12 or newer."
    else:
        hardware.os_warning = "Automatic AI installation is available on Windows and macOS. A local AI engine and model are required to organize files here."
    return hardware
