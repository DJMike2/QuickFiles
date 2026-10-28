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
invalid catalog/group responses abort planning; unverifiable individual matches
leave the affected file in place.

Planning uses the tested prompts and settings in `app/ai/prompts.py` and
`models/settings.json`:

- Thinking off; temperature 0.2, top_p 0.8, top_k 20, min_p 0, seed 42.
- An 8,192-token context and four CPU threads. Evidence comparisons use temperature 0.
- Only loose top-level files are candidates. Existing organized files stay put;
  project/application folders are excluded from traversal and destinations.
- Preparation reads bounded text/PDF/Office evidence and builds a grounded summary.
  Literal course/client headings and known filename-only formats use direct evidence;
  other summaries select passages through the local model. Python verifies the
  passages. Content takes precedence over misleading filenames. Summaries retain
  read limitations and reuse an identity/evidence/model-aware local cache.
- Steps 6–8 describe existing destinations, find compatible shared subjects, and
  match individual files with a reason and supporting quote. Explicit courses and
  full client identities stay separate; suitable existing destinations take priority.
- Independent review challenges semantic matches without seeing the first judge's
  reasoning. Python rechecks identities, kinds, file changes and the complete map.
  Rejected/ungrounded matches stay in place, and new singleton groups are pruned.
- Apply preflights the displayed map and verifies every moved file with SHA-256.
  New journal entries retain checksums; undo preserves changed or occupied files.
- New folders need at least two actual assigned files. Unreadable files permit
  broad grouping; uncertain subjects and changed files stay in place.

The model does not supply confidence percentages. Relative paths, protected
locations and destination collisions are validated before preview and again
before applying. Double-click names in the After tree to edit a proposal, or drag them to move.
Right-click a file and choose **Move to new folder…** to type a folder name; the file moves into
that new folder beside it in the preview, and Apply creates the folder.

The [ordered organizer process](docs/organizer-process.md) documents all ten implemented
steps. [Completion results](metrics/organizer-completion.md) and the historical
[steps 6–8 results](metrics/organizer-steps-6-8.md) separate placement
quality from coverage and include real-directory previews and generated apply/undo
checks. Use `python -B tools/audit_organizer.py /path/to/folder` for a read-only
evidence report, or add `--preview` to inspect the independently reviewed proposals.

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
terms also help preserve exact identifiers: when a request names a numbered item
(STAT 415, homework 2) and some files carry it, only those are shown. Clear outliers
far below the best match, and media files whose name and folder share no word with
the request, are left out. Files beside a match in a folder named for the request
(Travel, Health) are considered too, so "japan trip" also reaches the flight booking
that never says Japan. Qwen then reviews the top 10 matches against the request
as typed, answering only with the IDs of the files that fit (usually under a second);
requests that only name a type, size, date or order skip this review. If the review
fails, the unreviewed matches are shown. The text model drafts several tokens at a time
with its built-in multi-token-prediction layers (`"speculative": "draft-mtp"` in
`models/settings.json`), which makes its answers about 1.6x faster; remove the
setting for a model without those layers. Results appear in the graph; the
conversation panel reports the actual number shown and how many indexed files
were searched. Counts come from the files kept, never from model text.
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

Search uses filenames, paths and bounded text excerpts, with an English embedding
model. When the vision model (Qwen3-VL) is installed, photos and screenshots,
videos (four frames spread across the clip) and PDFs without a text layer (the
first page, typed or handwritten) are described once while indexing, so they can be
found by what they show and by the text in them. This takes a few seconds per new
file; descriptions are cached until the file changes. The search model is unloaded
while descriptions are written, because both models do not fit together on an 8 GB
GPU. Audio is not inspected, and HEIC or camera RAW photos remain name-only.
Pillow, pypdfium2 and PyAV (bundled FFmpeg) read the images, scans and video frames.
It supports explicit file-type and exclusion filters, size limits and date filters.
Messages summarize actual retrieval results; multi-turn conversational reasoning
is not part of this version. All inference and indexed content stay local.
