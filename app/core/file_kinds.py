"""Format metadata for broad organization; it never asserts unseen file contents."""
from pathlib import PurePosixPath
import re

from app.constants import SEARCH_KINDS

KINDS = ('code', 'presentation', 'spreadsheet', 'image', 'audio', 'video', 'archive',
         'ai model', 'disk image', 'document', 'installer', 'shortcut')
# The noun after the format in a type such as "PDF document"; code reads best as "PY file".
KIND_NOUNS = {'code': 'file', 'presentation': 'presentation', 'spreadsheet': 'spreadsheet',
              'image': 'image', 'audio': 'audio', 'video': 'video', 'archive': 'archive',
              'ai model': 'AI model', 'disk image': 'disk image', 'document': 'document',
              'installer': 'installer', 'shortcut': 'shortcut'}


def kind_of(entry):
    return next((kind for kind in KINDS if entry.extension in SEARCH_KINDS[kind]), 'unknown')


def type_label(name, *, is_dir=False, executable=False):
    """A specific type for people, such as "PDF document" or "ZIP archive", from the name alone."""
    if is_dir:
        return 'Folder'
    # "v1.2 notes" has a dot but no extension; extensions are short and have no spaces.
    suffixes = [suffix.lower() for suffix in PurePosixPath(name).suffixes
                if re.fullmatch(r'\.[A-Za-z0-9_+-]{1,12}', suffix)]
    if not suffixes or not name.lower().endswith(suffixes[-1]):
        return 'Executable' if executable else 'File without extension'
    extension = suffixes[-1]
    kind = next((kind for kind in KINDS if extension in SEARCH_KINDS[kind]), None)
    # Compressed tarballs are known by both parts: "TAR.GZ archive".
    shown = ''.join(suffixes[-2:]) if kind == 'archive' and suffixes[-2:-1] == ['.tar'] else extension
    return f'{shown[1:].upper()} {KIND_NOUNS[kind] if kind else "file"}'
