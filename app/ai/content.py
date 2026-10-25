"""Read small local text excerpts for the LLM; never execute document contents."""
import html
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from app.constants import READABLE_EXTENSIONS


def _pdf_text(path, pages=1):
    if shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(pages), "-nopgbrk", str(path), "-"],
            capture_output=True, timeout=4 * pages,
        )
        return result.stdout[:12000 * pages].decode("utf-8", "replace")
    # Portable fallback for computers without Poppler. Large PDFs stay filename-only.
    if path.stat().st_size <= 50 * 1024 * 1024:
        try:
            from pypdf import PdfReader
            reader = PdfReader(path, strict=False)
            return " ".join(page.extract_text() or "" for page in reader.pages[:pages])[:12000 * pages]
        except Exception:
            return ""  # Encrypted, malformed and image-only PDFs need no guessed content.
    return ""


def _slide_number(name):
    match = re.search(r"(\d+)\.xml$", name)
    return int(match[1]) if match else 0


def read_content(entry, read_limit=1200, *, max_chars=180, detailed=False):
    """detailed=True (search) reads a few PDF pages, every slide and more archive entries."""
    if read_limit <= 0:
        return ""
    path = Path(entry.abs_path)
    extension = entry.extension
    text = ""
    try:
        if extension == ".pdf":
            text = _pdf_text(path, 3 if detailed else 1)
        elif extension == ".pptx" and detailed:
            with zipfile.ZipFile(path) as archive:
                slides = sorted((name for name in archive.namelist()
                                 if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=_slide_number)
                parts = []
                for name in slides[:60]:
                    with archive.open(name) as stream:
                        parts.append(stream.read(100_000).decode("utf-8", "replace"))
            text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))
        elif extension in {".docx", ".pptx", ".xlsx"}:
            member = {".docx": "word/document.xml", ".pptx": "ppt/slides/slide1.xml",
                      ".xlsx": "xl/sharedStrings.xml"}[extension]
            with zipfile.ZipFile(path) as archive, archive.open(member) as stream:
                xml = stream.read(200_000).decode("utf-8", "replace")
            text = html.unescape(re.sub(r"<[^>]+>", " ", xml))
        elif extension == ".zip" and entry.size_bytes < 500_000_000:
            with zipfile.ZipFile(path) as archive:
                text = "Archive entries: " + ", ".join(item.filename for item in archive.infolist()[:25 if detailed else 6])
        elif extension in READABLE_EXTENSIONS:
            with path.open("rb") as stream:
                text = stream.read(min(read_limit, max(1200, max_chars * 3))).decode("utf-8", "replace")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError):
        pass
    return re.sub(r"\s+", " ", text).strip()[:max_chars]
