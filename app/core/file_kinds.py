"""Format metadata for broad organization; it never asserts unseen file contents."""
from app.constants import SEARCH_KINDS

KINDS = ('code', 'presentation', 'spreadsheet', 'image', 'audio', 'video', 'archive',
         'ai model', 'disk image', 'document', 'installer', 'shortcut')
KIND_LABELS = {'code': 'Code', 'presentation': 'Presentations', 'spreadsheet': 'Spreadsheets',
              'image': 'Images', 'audio': 'Audio', 'video': 'Videos', 'archive': 'Archives',
              'ai model': 'AI Models', 'disk image': 'Disk Images', 'document': 'Documents',
              'installer': 'Installers', 'shortcut': 'Shortcuts'}


def kind_of(entry):
    return next((kind for kind in KINDS if entry.extension in SEARCH_KINDS[kind]), 'unknown')


def folder_kind(name):
    """Only unambiguous broad labels; e.g. Models alone does not establish AI weights."""
    import re
    label = re.sub(r'[^\w]+', '', name.casefold())
    aliases = {'audio': {'audio', 'music', 'songs'}, 'image': {'images', 'pictures', 'photos'},
               'video': {'video', 'videos', 'movies'}, 'code': {'code', 'sourcecode'},
               'archive': {'archives'}, 'ai model': {'aimodels', 'llmmodels'},
               'disk image': {'diskimages', 'isos'}, 'document': {'documents', 'docs', 'pdfs'}}
    return next((kind for kind, names in aliases.items() if label in names), '')
