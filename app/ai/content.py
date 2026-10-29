"""Read small local text excerpts for the LLM; never execute document contents."""
import html
import logging
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile

from app.constants import READABLE_EXTENSIONS
from app.platforms import external_environment

log = logging.getLogger(__name__)


def _check_cancelled(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('File reading stopped')


def _pdf_text(path, pages=1, *, cancelled=None):
    _check_cancelled(cancelled)
    if shutil.which("pdftotext"):
        command = ["pdftotext", "-f", "1", "-l", str(pages), "-nopgbrk", str(path), "-"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=external_environment())
        try:
            deadline = time.monotonic() + 4 * pages
            while True:
                _check_cancelled(cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 4 * pages)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
        if result.returncode:
            log.warning('PDF reader failed for %s (exit %s): %s', path, result.returncode,
                        result.stderr[:2048].decode('utf-8', 'replace').strip())
        else:
            return result.stdout[:12000 * pages].decode("utf-8", "replace")
    # Use the bundled reader if Poppler is missing or fails. Large PDFs stay filename-only.
    if path.stat().st_size <= 50 * 1024 * 1024:
        try:
            from pypdf import PdfReader
            reader = PdfReader(path, strict=False)
            chunks = []
            for page in reader.pages[:pages]:
                _check_cancelled(cancelled)
                chunks.append(page.extract_text() or "")
            _check_cancelled(cancelled)
            return " ".join(chunks)[:12000 * pages]
        except InterruptedError:
            raise
        except Exception:
            log.warning('PDF reader failed for %s', path, exc_info=True)
            return ""  # Encrypted, malformed and image-only PDFs need no guessed content.
    return ""


def _slide_number(name):
    match = re.search(r"(\d+)\.xml$", name)
    return int(match[1]) if match else 0


def read_content(entry, read_limit=1200, *, max_chars=180, detailed=False, cancelled=None):
    """detailed=True (search) reads a few PDF pages, every slide and more archive entries."""
    _check_cancelled(cancelled)
    if read_limit <= 0:
        return ""
    path = Path(entry.abs_path)
    extension = entry.extension
    text = ""
    try:
        if extension == ".pdf":
            text = _pdf_text(path, 3 if detailed else 1, cancelled=cancelled)
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
    except InterruptedError:
        raise
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError):
        log.warning('Could not read file content: %s', path, exc_info=True)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]
