"""One set of portable filename rules for validation and inline editing."""
import re

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_valid_filename(name: str) -> bool:
    return bool(
        name and name == name.strip() and len(name) <= 255
        and not name.endswith(".") and not _FORBIDDEN.search(name)
        and name.split(".")[0].upper() not in _RESERVED
    )


def relative_parts(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if not all(is_valid_filename(part) for part in parts):
        raise ValueError(f"Invalid relative path: {value!r}")
    return tuple(parts)
