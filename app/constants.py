"""File categories and defaults shared by scanning and planning."""

APP_NAME = "QuickFiles"
APP_DATA_DIR_NAME = "DesktopOrganizerAI"
DEFAULT_MAX_FILES = 999_999_999
DEFAULT_CONTENT_READ_BYTES = 32 * 1024

CATEGORY_EXTENSIONS = {
    "Images": ".jpg .jpeg .png .gif .bmp .webp .tiff .tif .svg .ico .heic .raw .cr2 .nef .arw",
    "Videos": ".mp4 .mkv .avi .mov .wmv .flv .webm .m4v .mpg .mpeg .3gp",
    "Audio": ".mp3 .wav .flac .aac .ogg .wma .m4a .opus",
    "Documents": ".txt .md .doc .docx .rtf .odt .pages",
    "PDFs": ".pdf",
    "Archives": ".zip .tar .gz .7z .rar .bz2 .xz",
    "Code": ".py .js .ts .html .htm .css .java .c .cpp .h .cs .go .rs .rb .php .sh .json .yaml .yml .xml .toml .ini .cfg .sql .r .kt .swift",
    "Spreadsheets": ".xls .xlsx .csv .ods .numbers",
    "Presentations": ".ppt .pptx .odp .key",
    "Apps & Shortcuts": ".lnk .url .bat .cmd .ps1 .vbs",
    "Installers": ".exe .msi .dmg .pkg .deb .rpm .appimage",
}
EXT_CATEGORY_MAP = {
    extension: category
    for category, extensions in CATEGORY_EXTENSIONS.items()
    for extension in extensions.split()
}
CONSERVATIVE_EXTENSIONS = set(
    ".lnk .url .exe .msi .bat .cmd .ps1 .vbs .dll .sys .drv .ocx .scr".split()
)
READABLE_EXTENSIONS = set(
    ".txt .md .json .yaml .yml .csv .ini .log .cfg .toml .xml .py .js .ts .html .htm .css .java .c .cpp .h .cs .go .rs .rb .php .sh .r".split()
)
