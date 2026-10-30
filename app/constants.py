"""File categories and defaults shared by scanning and planning."""

APP_NAME = "QuickFiles"
APP_DATA_DIR_NAME = "DesktopOrganizerAI"
DEFAULT_CONTENT_READ_BYTES = 32 * 1024

CATEGORY_EXTENSIONS = {
    "Images": ".jpg .jpeg .png .gif .bmp .webp .tiff .tif .svg .ico .heic .raw .cr2 .nef .arw",
    "Videos": ".mp4 .mkv .avi .mov .wmv .flv .webm .m4v .mpg .mpeg .3gp",
    "Audio": ".mp3 .wav .flac .aac .ogg .wma .m4a .opus",
    "Documents": ".txt .md .doc .docx .rtf .odt .pages",
    "Archives": ".zip .tar .gz .7z .rar .bz2 .xz",
    "Code": ".py .js .ts .html .htm .css .java .c .cpp .h .cs .go .rs .rb .php .sh .json .yaml .yml .xml .toml .ini .cfg .sql .r .kt .swift",
    "Spreadsheets": ".xls .xlsx .csv .ods .numbers",
    "Presentations": ".ppt .pptx .odp .key",
    "Installers": ".exe .msi .dmg .pkg .deb .rpm .appimage",
}
# Broad kinds that search can filter by; a file may belong to more than one.
SEARCH_KINDS = {
    kind: set(extensions.split()) for kind, extensions in {
        "image": CATEGORY_EXTENSIONS["Images"],
        "video": CATEGORY_EXTENSIONS["Videos"],
        "audio": CATEGORY_EXTENSIONS["Audio"],
        "document": CATEGORY_EXTENSIONS["Documents"] + " .pdf .tex .epub",
        "spreadsheet": CATEGORY_EXTENSIONS["Spreadsheets"] + " .tsv",
        "presentation": CATEGORY_EXTENSIONS["Presentations"],
        "archive": CATEGORY_EXTENSIONS["Archives"] + " .tgz",
        "code": CATEGORY_EXTENSIONS["Code"] + " .m .ipynb .ps1 .bat .cmd",
        "installer": CATEGORY_EXTENSIONS["Installers"],
        "disk image": ".iso .img .vhd .vhdx .vmdk",
        "shortcut": ".lnk .url .desktop",
        "ai model": ".gguf .safetensors .onnx .pt .pth .ckpt",
    }.items()
}
# Words that show a request really asks for a kind, rather than the model guessing one.
SEARCH_KIND_WORDS = {
    kind: set(words.split()) for kind, words in {
        "image": "image photo picture pic",
        "video": "video movie clip film footage recording",
        "audio": "audio song music track podcast recording voice",
        "document": "document doc",
        "spreadsheet": "spreadsheet sheet excel table workbook",
        "presentation": "presentation deck powerpoint keynote",
        "archive": "archive zip compressed",
        "code": "code script source program python java javascript typescript c++ rust",
        "installer": "installer setup install app application program",
        "disk image": "iso disk vm",
        "shortcut": "shortcut link",
        "ai model": "model gguf weights checkpoint llm",
    }.items()
}
SHORTCUT_EXTENSIONS = set(".lnk .url".split())
EXECUTABLE_EXTENSIONS = set(
    ".exe .msi .bat .cmd .ps1 .vbs .com .pif .psm1 .psd1 .wsf .hta .msc .cpl .scr "
    ".dmg .pkg .deb .rpm .appimage .command".split()
)
LIBRARY_EXTENSIONS = set(".dll .sys .drv .ocx".split())
LIVE_DATA_EXTENSIONS = set(".pst .ost .sqlite .sqlite3 .vhd .vhdx .vmdk".split())
CONSERVATIVE_EXTENSIONS = (
    SHORTCUT_EXTENSIONS | EXECUTABLE_EXTENSIONS | LIBRARY_EXTENSIONS | LIVE_DATA_EXTENSIONS
)
READABLE_EXTENSIONS = set(
    ".txt .md .json .yaml .yml .csv .ini .log .cfg .toml .xml .py .js .ts .html .htm .css .java .c .cpp .h .cs .go .rs .rb .php .sh .r .sql".split()
)
