# Measured performance

[Organizer guide](organizer-process.md) · [Setup](../README.md#get-started)

**Measured on 28 October 2026 · local Qwen3.5-4B Q6_K · Linux**

## First preview vs unchanged repeat

| Same 21 loose files | Median | Observed range |
| --- | ---: | ---: |
| **First preview** · model unloaded, fresh summary/profile cache | **155.9 s** | 155.4–157.6 s |
| **Unchanged repeat** · model loaded, validated decisions reusable | **3.5 s** | 2.3–3.5 s |

Three trials; each first preview was followed by one repeat.
Timing includes native scanning, evidence, planning, and independent review.
Downloads, GUI painting, Apply, and Undo are excluded. Files were never moved.

> [!NOTE]
> The repeat saves work through caching. It is not a fresh-inference speedup.
> Changed inputs need fresh decisions; idle model unloading clears session decisions.

### Placement checks

The fixture was labelled before inference: four subjects, two archives,
three unrelated singletons, an existing destination, and a protected project.
Filenames were shuffled with seed `2026102821`.

| Check | Result in every first/repeat pair |
| --- | --- |
| Supported placements | **18 / 18** matched the labelled subject and destination purpose |
| Useful coverage | **18 / 18** groupable loose files received a move |
| Unrelated loose files | **3 / 3** stayed in place |
| Files inside existing folders/projects | **4 / 4** preserved |
| Repeat consistency | Same ordered source/destination pairs as that trial's first preview |
| Source integrity | All source contents and inventory snapshots unchanged |

These are checks on one small synthetic fixture, not an accuracy estimate for all folders.

## Metadata inventory: 1,000,000 files

| Measured scope | Native workers | Median | Observed range |
| --- | ---: | ---: | ---: |
| Native executable + buffered JSONL output | 1 | **11.171 s** | 10.110–14.838 s |
| Native executable + buffered JSONL output | 4 | **5.221 s** | 5.187–10.266 s |
| Complete Python app inventory, 1M file cap | 1 | **26.233 s** | 22.159–29.974 s |

Three timed runs per row on **1,000 folders × 1,000 empty text files**.
Every native run returned one million file records with zero errors or skips;
every app run returned **1,000,000 unique inventory entries**.

Native-only timing includes process startup, traversal, metadata, and buffered JSONL
writing. The app timing also includes policy setup, parsing, inventory objects, and
sorting. Neither includes file contents or AI. Fixture creation, output validation,
and cleanup are outside the timers. Native output was checked after each run.

One native warm-up per worker configuration preceded timing; OS caches were not
flushed. The app run explicitly used `max_files=1_000_000`. Same machine as below;
source [`8c2b451`](https://github.com/DJMike2/QuickFiles/commit/8c2b451).
The generated million-file tree was removed after verification.

## Native inventory: 20,000 files

| Native workers | Median | Observed range |
| --- | ---: | ---: |
| **1** · Linux automatic default | **1.470 s** | 0.671–1.565 s |
| **4** · explicit override | **1.450 s** | 0.756–1.462 s |

Seven timed runs per configuration, randomized order, after one warm-up each; `max_files=20000`.
The generated tree contained 100 folders × 200 text files. All 20,000 files
were returned in every run, with **zero skipped files and zero errors**.
Timing includes process startup, policy checks, metadata parsing, and sorting.
It excludes content reading and AI. Four workers brought little benefit on this fixture.

<details>
<summary><strong>Machine, method, and individual runs</strong></summary>

| Setting | Value |
| --- | --- |
| CPU / RAM | Intel Core i7-14700HX / approximately 31 GiB usable RAM |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU, 8 GiB VRAM; CUDA |
| System | Linux x86-64, ext4; NVIDIA driver 595.99.02 |
| Local engine | llama.cpp `b11869` |
| Model | Qwen3.5-4B Q6_K; no vision or embedding model used |
| Generation | Seed 42; 8,192-token context; 4 CPU threads; existing app defaults |
| Source | [`087de77`](https://github.com/DJMike2/QuickFiles/commit/087de77) |
| Corpus | 21 loose files: 19 text files and 2 ZIPs; 4 additional nested files |
| Cache conditions | New summary/profile cache and unloaded model for each trial; same session for its repeat; OS file caches were not flushed |

| Trial | First preview | Repeat preview | Local model requests: first → repeat |
| --- | ---: | ---: | ---: |
| 1 | 157.644 s | 3.547 s | 28 → 1 |
| 2 | 155.948 s | 3.505 s | 28 → 1 |
| 3 | 155.355 s | 2.306 s | 28 → 1 |

Subjects covered calculus, client invoices, vegetable gardening, and hiking navigation.
Predetermined labels and destination-purpose checks were used for the audit,
not the model's confidence. This run did not benchmark images, large PDFs,
Windows performance, cold storage, new-file watcher behavior, or other products.

Raw inputs, full model traces, and reproduction runners stay in local benchmark storage.
The tables report aggregate results only. Hardware, files, power state, and cache
conditions affect timings; the measured first-preview cost remains visible above.

</details>
