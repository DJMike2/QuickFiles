<div align="center">

# QuickFiles

**Organize your files. Find what you need. Keep it local.**

Windows & Linux · Local AI · Native C++ scanner

[Get started](#get-started) · [How it works](#how-it-works) · [Development](#development)

</div>

---

## What you can do

| | Feature |
| --- | --- |
| **Organize** | Group loose files by related subjects, using readable contents before filenames. |
| **Preview** | Compare Before/After trees, rename items, drag files, or move them into a new folder. |
| **Search** | Describe what you need; browse matching files in an interactive graph. |
| **Visual search** | Search images, video frames, and scanned PDFs when the optional vision model is installed. |
| **Undo** | Restore the most recent batch, preserving files that have since changed or would conflict. |

## Get started

**Requirements:** Python 3.10+ and a C++17 compiler available in your terminal.

| Platform | Compiler |
| --- | --- |
| Windows | MinGW-w64 with POSIX threads, such as MSYS2's mingw64 toolchain |
| Linux | `g++` with static runtime libraries |

Run these commands from the repository folder:

<details open>
<summary><strong>Linux</strong></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/build_scanner.py
python -m app.main
```

</details>

<details>
<summary><strong>Windows — Command Prompt</strong></summary>

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python tools\build_scanner.py
python -m app.main
```

</details>

On first launch, choose **Setup AI** to download the local engine and **Qwen3.5-4B Q6_K** model (about 3.81 GB). CUDA acceleration is optional; a CPU engine is available if GPU startup fails. AI setup is required for organization.

Models load when you scan or search, stay ready for follow-up work, and unload after five idle minutes. Opening the app alone does not load them.

> **Your files stay on your computer.** Internet is needed for engine and model downloads; planning, search, and cached file content stay local.

## How it works

1. **Choose a folder and scan.** QuickFiles reads supported file contents and proposes related groups.
2. **Review the preview.** Edit names, drag items, or right-click a file → **Move to new folder…**.
3. **Apply when ready.** Files move only after you apply the preview. Use **Undo** to reverse the latest batch.

### Built-in safeguards

- Organizes loose files; existing folder contents and protected project/application folders stay in place.
- Uncertain matches stay put. Unreadable contents allow broad grouping without invented subjects.
- New groups need at least two assigned files. Paths, collisions, and file changes are checked before moving.
- Moves are verified with SHA-256 and recorded for undo. The organizer never deletes files.

### Find files with AI Search

Open **AI Search**, select a folder, and describe the files you want. Search supports file-type, size, and date filters and never moves files.

The first search downloads a small embedding model (about 46 MB). With the optional vision model, it can also describe images, sample video frames, and read the first page of scanned PDFs. Descriptions are cached; audio contents are not inspected.

## Development

<details>
<summary><strong>Native scanner builds</strong></summary>

Rebuild after changing the C++ source:

```sh
python tools/build_scanner.py
```

| Platform | Output |
| --- | --- |
| Windows | `app/core/native/bin/fastscan.exe` |
| Linux | `app/core/native/bin/fastscan` — native ELF; no Wine required |

Include the matching binary when distributing the app. Preserve Linux executable permissions.

Cross-compile Windows x64 from Linux with MinGW-w64:

```sh
python tools/build_scanner.py --target windows --cxx x86_64-w64-mingw32-g++-posix
```

</details>

<details>
<summary><strong>Settings and local data</strong></summary>

Model defaults: [`models/settings.json`](models/settings.json). Prompts: [`app/ai/prompts.py`](app/ai/prompts.py).

Preferences, logs, downloaded models, search caches, and the undo journal are stored outside the repository:

| Platform | Location |
| --- | --- |
| Windows | `%APPDATA%\DesktopOrganizerAI` |
| Linux | `~/.local/share/DesktopOrganizerAI` |

</details>

Tests, benchmark tools, and performance reports are maintained locally. Public CI
builds and smoke-checks the Windows and Linux scanners. Models, caches, and
generated binaries are excluded from version control.

## License

QuickFiles is **source-available** under the [Sustainable Use License 1.0](LICENSE).
Personal use, internal business use, and modifications for those purposes are allowed.
Sharing must be free of charge and for noncommercial purposes; selling copies or
charging others for hosted access requires separate permission.

Third-party dependencies and model weights retain their respective licenses.

---

[Organizer process](docs/organizer-process.md) · [License](LICENSE)
