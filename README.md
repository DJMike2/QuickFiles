# QuickFiles

A desktop file organizer with a local AI planner and a built-in rule-based fallback.
Scan a folder, review the Before/After trees, then apply the plan. Undo restores the
most recent batch. Files are never deleted by the organizer.

Requires Python 3.10 or newer and the Qt Essentials package. From this project directory:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -B -m app.main
```

`-B` prevents Python from creating `__pycache__` directories. The app works without
an AI model. **Setup AI** optionally downloads llama.cpp and the original Mistral
Nemo 12B GGUF model (about 7.1 GB). Downloads require internet; planning runs locally.
Automatic engine installation targets Windows 10+ and macOS 12+, as in the source
project. Linux can use the rule-based planner; automatic AI installation is unavailable.
GPU acceleration is optional, with CPU fallback.

Choose Fast or Accurate planning, or Auto in Settings. Double-click names in the
After tree to edit the proposed destination. Settings also contains exclusions,
scan limits, light/dark themes, and the opt-in for moving executables and shortcuts.
Protected system folders, paths outside the selected root, and destination collisions
are checked before applying. Undo skips conflicts so existing files are preserved.

Preferences, logs, downloaded engines/models, and the SQLite undo journal live outside
this source tree:

- Windows: `%APPDATA%\DesktopOrganizerAI`
- macOS: `~/Library/Application Support/DesktopOrganizerAI`
- Linux: `~/.local/share/DesktopOrganizerAI`

Recreated on October 24, 2026, from the supplied archive. The original scan, preview,
apply, undo, and optional local AI workflow remains. Removed the malformed empty
`{app/...` directories, tests, empty package markers, unused progress widgets,
duplicate workers, unused SQLite settings/scan-history plumbing, stale multi-model
wrappers, unused dependencies, and nonfunctional delete proposals. Shared workers,
filename checks, database access, and theme styling replace duplicated code. The
model download streams to disk instead of buffering the entire model in memory.
No test suite, caches, downloaded models, or generated build files are included.
