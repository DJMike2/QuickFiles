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

## License

QuickFiles is **source-available** under the [Sustainable Use License 1.0](LICENSE).
Personal use, internal business use, and modifications for those purposes are allowed.
Sharing must be free of charge and for noncommercial purposes; selling copies or
charging others for hosted access requires separate permission.

Third-party dependencies and model weights retain their respective licenses.
