# QuickFiles

A desktop file organizer powered by **Qwen3.5-4B Q6_K**, running locally through
llama.cpp. Scan a folder, review the Before/After trees, then apply the proposed
moves. Undo restores the most recent batch. The organizer never deletes files.

Supports Windows and Linux only. Requires Python 3.10 or newer:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/build_scanner.py
python -B -m app.main
```

**Setup AI** installs the tested llama.cpp b11869 engine and the 4B Q6_K model
(3.81 GB). Windows and Linux packages are supported. CUDA is optional on
Windows/Linux. A CPU engine remains available when GPU
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
python -B tests/benchmark_scan.py "/path/to/folder" --threads 4 --repeat 3
python -B tests/benchmark_organization.py "/path/to/folder"
python -B tests/benchmark_organization.py "/path/to/folder" --testing-model --seed 1337
```

The organization benchmark prints proposed Before/After paths and never applies
them. Its elapsed time includes scanning, context extraction and model loading.
`-B` avoids Python cache directories. Downloaded models, caches and generated
build files are excluded from version control.

Recorded before/after performance and validation results are in
[metrics/README.md](metrics/README.md). Those results reuse existing measurements;
the older benchmarks were not rerun to produce the report.

## Native file scanner

Folder traversal runs in `app/core/native/fastscan_mt.cpp`, adapted from the
supplied multithreaded scanner. Windows keeps its buffered NT directory queries
and relative directory handles. Linux uses `readdir`, `fstatat` and `openat`.
Python only converts the streamed metadata into the app's inventory. Scanning
does not read file contents or require the AI engine.

Build once before launching from source; rebuilding is required after changes to
the C++ source. GCC with C++17 and POSIX threads is required. On Windows, use a
MinGW-w64 POSIX-thread compiler in PATH (for example, MSYS2's mingw64 toolchain).
On Linux, use g++ with its static runtime libraries installed.

```sh
# Build for the current platform:
python tools/build_scanner.py

# Cross-compile Windows x64 from Linux using MinGW-w64:
python tools/build_scanner.py --target windows --cxx x86_64-w64-mingw32-g++-posix
```

Outputs are `app/core/native/bin/fastscan.exe` on Windows and
`app/core/native/bin/fastscan` on Linux. Both link their runtimes statically;
Windows needs no extra MinGW DLLs. The binaries built here are x64; build on the
target architecture for other CPUs. Preserve Linux's executable permission when
copying it. Distributions must include the corresponding binary at that path.
The adapter resolves it independently of the working directory and reports a
build instruction if it is missing. It never compiles during a scan.

The Linux binary is a native ELF executable: it runs directly, with no Wine or
Windows compatibility layer. The app selects it automatically on Linux. To
check it directly from the repository root:

```sh
./app/core/native/bin/fastscan --version
```

For a complete scan, use the app or `python -B tests/benchmark_scan.py "/path/to/folder"`.
The executable's scan arguments include an internal policy file supplied by the
Python adapter. Wine was used only during development on Linux to check the
separate Windows `.exe`; neither app installation nor Linux scanning requires it.

The scanner prunes hidden/excluded folders, protected locations, links and
project/application folders before descending. It skips protected filenames,
Windows system/cloud-only files and nonregular files. Root-level project markers
do not prevent scanning a folder explicitly selected by the user. Native output
is buffered JSONL with a version header and completion summary; failures or
truncated output abort the scan. Cancellation terminates and reaps the process.
Parallel results are sorted before planning so file IDs and file limits stay
consistent.

Windows automatically uses up to four workers for detected SSDs, and one for
HDDs or unknown storage. Linux defaults to one worker for unknown storage.
`--threads 4` in the scan benchmark overrides that choice. Benchmark timings
include process startup, native traversal and Python inventory conversion;
`--repeat` runs each complete scan again. The workflow in
`.github/workflows/scanner.yml` builds and tests Windows and Linux separately and
uploads Windows ZIP and Linux tar.gz artifacts (the tar preserves executable
permissions). Generated binaries are ignored by git.

## AI Search

Open **AI Search**, choose its search folder, and describe the files you want.
The local Qwen model turns the request into a concise query and explicit filters.
A small All-MiniLM-L6-v2 embedding model retrieves files by meaning. Filename
terms also help preserve exact identifiers. Results appear in the graph; the
conversation panel reports the actual number found and how many indexed files
were searched. Counts are generated after retrieval, without a second LLM call.
History restores the original query, answer and result graph during this session.

Search is read-only and has its own folder selection. It does not call organizer
Scan, Apply or Undo. Project source files can be searched; hidden files, links,
dependency/build folders and protected system locations are skipped. Existing safety rules
and drag-and-drop support remain in the organizer.

The first search downloads a verified 46 MB embedding GGUF when needed. It runs
on the existing native CPU engine. NumPy handles vector comparisons; no PyTorch
or separate vector database is used. Embeddings and short excerpts are cached
in `search.sqlite3` in application data. Unchanged files reuse their vectors;
changed, added and deleted files are refreshed on later searches.

This first version uses filenames, paths and bounded text excerpts (up to 600
characters), with an English embedding model. It does not perform OCR or inspect
image/audio content. It supports explicit file-type and exclusion filters;
dates remain part of the search text rather than strict filesystem-date filters.
Messages summarize actual retrieval results; multi-turn conversational reasoning
is not part of this version. All inference and indexed content stay local.
