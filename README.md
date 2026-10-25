# QuickFiles

A desktop file organizer powered by **Qwen3.5-4B Q6_K**, running locally through
llama.cpp. Scan a folder, review the Before/After trees, then apply the proposed
moves. Undo restores the most recent batch. The organizer never deletes files.

Requires Python 3.10 or newer:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -B -m app.main
```

**Setup AI** installs the tested llama.cpp b11869 engine and the 4B Q6_K model
(3.81 GB). Windows, macOS and Linux packages are supported. CUDA is optional on
Windows/Linux; Apple Silicon uses Metal. A CPU engine remains available when GPU
startup fails. Downloads require internet; filenames and document excerpts stay
on this computer during planning.

There is no organizer without an LLM. Missing engine/model files require setup;
invalid or unfinished model responses abort planning.

Planning uses the tested prompts and settings in `app/ai/prompts.py` and
`models/settings.json`:

- Thinking off; temperature 0.2, top_p 0.8, top_k 20, min_p 0, seed 42.
- An 8,192-token context, four CPU threads and batches of 20 files.
- One shared folder plan, constrained `file_id:folder_id` assignments and one
  additional pass for files that did not fit.
- Only loose top-level files are candidates. Existing organized files stay put;
  project/application folders are excluded from traversal and destinations.
- Bounded text excerpts from text, PDF and Office files, plus ZIP entry names.
  PDFs use pdftotext when available, with pypdf as a portable first-page fallback.
- New folders need at least two assigned files. Uncertain files can remain in place.

The 1,024-token folder-planning budget and compact assignment budgets are fixed
internally. The model does not supply confidence percentages. Relative paths,
protected locations and destination collisions are validated before preview and
again before applying. Double-click names in the After tree to edit a proposal.

Preferences, logs, engines, models and the SQLite undo journal live outside the
source tree:

- Windows: `%APPDATA%\DesktopOrganizerAI`
- macOS: `~/Library/Application Support/DesktopOrganizerAI`
- Linux: `~/.local/share/DesktopOrganizerAI`

**Qwen3.5-9B Q6_K_L** is retained in that data directory's `models/` folder for
development testing. Its pinned URL, size and hash are under `testing_model` in
`models/settings.json`. The app and Setup AI use 4B; fresh installations do not
download 9B automatically. Both selected models have been retained on this computer.
GPU measurements were approximately 4.1 GiB for 4B and 7.3 GiB for 9B with the tested
configuration. CPU-only performance on weak computers has not been measured.

Development checks:

```sh
python -B -m unittest discover -s tests -p 'test_*.py'
python -B tests/benchmark_scan.py "/path/to/folder"
python -B tests/benchmark_organization.py "/path/to/folder"
python -B tests/benchmark_organization.py "/path/to/folder" --testing-model --seed 1337
```

The organization benchmark prints proposed Before/After paths and never applies
them. Its elapsed time includes scanning, context extraction and model loading.
`-B` avoids Python cache directories. Downloaded models, caches and generated
build files are excluded from version control.
