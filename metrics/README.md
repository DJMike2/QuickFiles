# Recorded metrics

Results recorded during the native scanner integration on **2026-10-25**.
This report uses measurements and test output already collected in that session;
the previous scanner and older benchmarks were **not rerun** for this report.
All times below are historical observations, not measurements of subsequent edits.

## Scanner before and after

The fixture contained **20,000 empty `.txt` files**, arranged as **100 folders
with 200 files each**, in a temporary directory on the same Linux machine.
Each scan returned all 20,000 files. The final native runs reported zero skipped
files and zero errors.

| Implementation | Recorded elapsed time | Runs | Scope |
| --- | ---: | ---: | --- |
| Previous Python scanner | 32.114 s | 1 | Complete `Scanner.scan()` |
| Initial C++ scanner with Python adapter | 2.555 s, 2.726 s | 2 | Complete scan benchmark |
| C++ scanner after adapter optimization | **0.510 s, 0.678 s** | 2 | Complete scan benchmark |
| Intermediate native executable alone | 0.0452 s | 1 | Traversal/output only, discarded by the caller |

The final complete scans used **four native workers**. Their elapsed time
includes process startup, policy handling, native traversal, JSONL parsing,
inventory creation and deterministic sorting. The previous Python implementation
walked the tree on one thread. Its timed call used no custom exclusion patterns;
the final benchmark used the app's default patterns, which matched none of the
fixture's `.txt` files.

Relative to the one recorded Python run, the final complete scans were
approximately **47–63× faster**, reducing elapsed time by **97.9–98.4%**.
These are ratios of the observed runs, not statistical estimates. The 0.0452 s
native-only measurement excludes Python inventory conversion and must not be
used as the end-to-end speedup.

The native version moves traversal and metadata collection into C++, prunes
excluded/project folders there, and streams metadata to Python. A later adapter
optimization removed repeated `Path` construction when calculating extensions,
absolute paths and sorting keys. The report records the observed stages; it does
not isolate the contribution of each change.

The fixture had already been accessed, so results may benefit from filesystem
caches. CPU, storage model, cache state and peak memory were not captured.
This synthetic fixture does not establish performance for cold disks, network
shares, large/deep real trees, Windows, AI planning or search indexing.

## Small source-tree scan

| Fixture | Native workers | Files | Skipped / errors | Recorded elapsed times |
| --- | ---: | ---: | --- | --- |
| QuickFiles working tree at the time | 1 (automatic) | 79 | 1 / 0 | 0.014 s, 0.013 s |

No matching before measurement was collected for this exact tree snapshot.
Its contents change during development, so it is a smoke check rather than a
stable benchmark fixture.

## Validation already completed

| Check | Recorded result | What it establishes |
| --- | --- | --- |
| Existing app suite after native integration | 19 passed | Existing planning, setup and search tests still passed |
| Final expanded suite from the integration session | 32 run: 31 passed, 1 skipped; 1.845 s | Integration and new scanner contracts passed locally |
| Final Windows executable checks under Wine | 6 passed; 2.850 s | Selected Windows binary behavior was checked on Linux |
| Linux binary inspection | x86-64 ELF, statically linked | Linux uses a native executable directly |
| Windows dependency inspection | KERNEL32.dll, msvcrt.dll, SHELL32.dll | No separate MinGW runtime DLLs were required |

Scanner coverage included metadata and modification times, Unicode names, empty
and nested directories, long paths, extensionless files, stable parallel ordering,
glob exclusions, project protection, blocked subtrees, file caps, progress,
cancellation, child cleanup, protocol failures and benchmark execution from a
different working directory. Linux checks also covered links and nonregular files.

The six Windows checks covered metadata/Unicode paths, exclusions/project
protection, blocked subtrees, deterministic file caps, cancellation/child cleanup,
and long paths/extensionless files. Wine was only a development test environment
for the Windows `.exe`; **the Linux app and ELF scanner do not use Wine**.
Unix symlink/FIFO fixtures were excluded from the final Windows checks because
Wine does not expose them as Windows reparse points.

The skipped local test required Windows system/cloud file attributes. A
Windows/Linux CI workflow was added, including native Windows tests; no completed
hosted CI run or test on a real Windows installation was recorded in this session.
These counts describe the earlier integration session, before the later removal
of unsupported-platform code; they are not current test-suite totals.

## Windows/Linux scope follow-up

Later on the same date, supported platforms were restricted to Windows and
Linux. The application now rejects other operating systems before starting the
UI or setup; the scanner build also accepts only Windows and Linux targets.
Both binaries were rebuilt. The Linux executable was checked by launching it
directly, and its ELF header was verified without Wine.

Verification for that follow-up passed **4 platform-boundary tests**,
**2 affected engine-setup tests** and **7 affected planning tests**. These were
regression checks for the platform removal, not new performance measurements.
The historical scanner benchmark and Windows/Wine checks above were not rerun.

## Other existing observations

The main README already recorded approximate GPU usage of **4.1 GiB** for
Qwen3.5-4B Q6_K and **7.3 GiB** for Qwen3.5-9B Q6_K_L. No paired before/after
measurements, hardware details or raw results were available here for those
observations. They are retained as context, not new measurements or evidence of
a scanner-related change. No new AI, model-loading, organization or search
performance results were collected for this report.

## Future measurements

The current benchmark remains available for separately dated future results:

```sh
python -B tests/benchmark_scan.py "/path/to/folder" --threads 4 --repeat 3
```

Record the machine, filesystem/storage, fixture, cache conditions, worker count,
binary revision and full output with future measurements. Keep those results
separate from this historical table.
