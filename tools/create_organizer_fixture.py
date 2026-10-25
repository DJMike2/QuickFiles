"""Create the controlled organizer cases in a NEW temporary test directory."""
import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    # Refuse existing directories: this utility never overwrites personal files.
    if args.directory.exists():
        parser.error("Choose a new directory; the destination already exists")
    cases_path = Path(__file__).resolve().parents[1] / "tests/fixtures/organizer_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    args.directory.mkdir(parents=True)
    for case in cases:
        path = args.directory / case["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if any(key in case for key in ("docx", "slides", "sheet")):
            with zipfile.ZipFile(path, "w") as archive:
                if "docx" in case:
                    archive.writestr("word/document.xml", "<w:document><w:t>" +
                                     escape(case["docx"]) + "</w:t></w:document>")
                if "slides" in case:
                    for number, text in enumerate(case["slides"], 1):
                        archive.writestr(f"ppt/slides/slide{number}.xml", "<a:t>" + escape(text) + "</a:t>")
                if "sheet" in case:
                    archive.writestr("xl/worksheets/sheet1.xml", "<t>" + escape(case["sheet"]) + "</t>")
        else:
            path.write_text(case.get("text", case.get("binary", "")), encoding="utf-8")
    print(f"Created {len(cases)} controlled cases in {args.directory}")


if __name__ == "__main__":
    main()
