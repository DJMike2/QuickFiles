"""Bounded, read-only evidence for organizer summaries, separate from search snippets."""
from dataclasses import dataclass
import html
from pathlib import Path
import re
import subprocess
import unicodedata
import zipfile

from app.ai.content import _pdf_text, _slide_number
from app.constants import READABLE_EXTENSIONS


def compact(text):
    # Normalize PDF typography before quoting: curly apostrophes and ligatures
    # are not different factual evidence. Mathematical layout is not reconstructed.
    text = unicodedata.normalize("NFKC", text).translate(str.maketrans(
        {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}))
    text = "".join(char for char in text if char.isspace() or unicodedata.category(char) != "Cc")
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class ContentEvidence:
    status: str
    text: str = ""
    limitation: str = ""


def _xml_text(archive, member, budget):
    with archive.open(member) as stream:
        xml = stream.read(budget).decode("utf-8", "replace")
    # Keep adjacent cells/paragraphs from merging into invented words.
    return html.unescape(re.sub(r"<[^>]+>", " ", xml))


def extract_evidence(entry, read_limit=32768):
    """Never download placeholders, execute files, or interpret instructions in them.

    Callers check file eligibility and identity before and after this operation.
    PDFs sample three pages; Office XML reads are bounded even in compressed files.
    Archive member names are explicitly weaker evidence than document contents.
    """
    limit = max(0, min(int(read_limit), 256 * 1024))
    if not limit:
        return ContentEvidence("disabled", limitation="Content reading is disabled; filename only")
    path = Path(entry.abs_path)
    extension = entry.extension.lower()
    text = ""
    limitation = ""
    status = "content"
    try:
        if extension == ".pdf":
            text = _pdf_text(path, pages=3)
            limitation = "First three PDF pages only; no OCR"
        elif extension in {".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp"}:
            budget = min(512 * 1024, max(32768, limit * 8))
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if extension == ".pptx":
                    members = sorted((name for name in names if re.fullmatch(
                        r"ppt/slides/slide\d+\.xml", name)), key=_slide_number)
                elif extension == ".xlsx":
                    members = (["xl/sharedStrings.xml"] if "xl/sharedStrings.xml" in names else [])
                    members += sorted(name for name in names if re.fullmatch(
                        r"xl/worksheets/sheet\d+\.xml", name))
                else:
                    members = ["word/document.xml" if extension == ".docx" else "content.xml"]
                chunks = []
                remaining = budget
                for member in members[:60]:
                    size = min(archive.getinfo(member).file_size, remaining)
                    chunks.append(_xml_text(archive, member, size))
                    remaining -= size
                    if remaining <= 0:
                        break
                text = " ".join(chunks)
            limitation = "Bounded document XML sample; embedded images are not read"
        elif extension == ".zip":
            # A ZIP central directory must be parsed by zipfile before it can be sampled.
            if entry.size_bytes > 500_000_000:
                return ContentEvidence("unsupported", limitation="Large archive; filename only")
            with zipfile.ZipFile(path) as archive:
                text = "Archive member names: " + ", ".join(
                    item.filename for item in archive.infolist()[:40])
            status = "archive_listing"
            limitation = "Member names only; archive contents were not opened"
        elif extension in READABLE_EXTENSIONS | {".m", ".tex", ".tsv", ".ipynb"}:
            with path.open("rb") as stream:
                raw = stream.read(limit)
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = raw.decode(encoding, "replace")
            if "\x00" in text or text.count("\ufffd") > max(2, len(text) // 100):
                return ContentEvidence("unreadable", limitation="Text encoding could not be read reliably")
            limitation = f"First {limit:,} bytes only"
        else:
            return ContentEvidence("unsupported", limitation="No content reader for this format; filename only")
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile,
            NotImplementedError, subprocess.SubprocessError):
        return ContentEvidence("unreadable", limitation="Content could not be read; filename only")
    text = compact(text)
    if extension == ".pdf" and "onenote" in text.casefold():
        # Handwritten OneNote exports can expose only dates, URLs and a generic
        # homework heading. Those headers do not mean the page body was readable.
        without_links = re.sub(r"https?://\S+", "", text, flags=re.I)
        words = set(re.findall(r"[^\W\d_]+", without_links.casefold()))
        export_words = set(("onenote homework fhw assignment notes lecture am pm "
                            "monday tuesday wednesday thursday friday saturday sunday "
                            "january february march april may june july august september october november december").split())
        if words <= export_words:
            return ContentEvidence("no_body_text", limitation=(
                "Only export headers were readable; page body unavailable, filename only"))
    cap = min(limit, 12000)
    if len(text) > cap:
        limitation += f"; extracted text capped at {cap:,} characters"
    text = text[:cap]
    if not text:
        return ContentEvidence("no_text", limitation="No extractable text; filename only (no OCR)")
    return ContentEvidence(status, text, limitation)
