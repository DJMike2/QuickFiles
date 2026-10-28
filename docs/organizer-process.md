# How QuickFiles organizes your files

**Choose a folder → review the preview → apply when ready.**

[Setup](../README.md#get-started) · [Measured performance](benchmarks.md)

> [!TIP]
> File analysis runs on your computer. No cloud API key is needed, and scanning
> never moves your files. Internet access is needed for engine/model downloads.

```mermaid
flowchart LR
    A[Choose a folder] --> B[Read evidence]
    B --> C[Plan and review]
    C --> D[Before / After]
    D -->|You approve| E[Apply and verify]
    E --> F[Undo]
```

## From folder to preview

| Step | What happens |
| --- | --- |
| **1 · Find the real folder** | Resolve the selected path and validate the location. Linked files and subfolders are not followed. |
| **2 · Preserve structure** | Keep existing folder contents, recognized projects, applications, and system paths in place. |
| **3 · Select loose files** | Skip empty stubs and protected types. Executable and shortcut eligibility follow their separate settings. |
| **4 · Read contents** | Collect bounded document excerpts. Record unreadable, unsupported, and offline content honestly. |
| **5 · Keep grounded evidence** | Use literal excerpts and explicit identifiers rather than inventing summaries. |
| **6 · Understand destinations** | Read a few direct samples from existing folders to identify their purpose. |
| **7 · Find shared subjects** | Group related files while keeping distinct courses, clients, and projects separate. |
| **8 · Match with support** | Prefer suitable existing folders. Require a supported reason and passage for semantic matches. |
| **9 · Review independently** | Challenge proposed placements using the evidence, then check paths, file changes, and group sizes again. |
| **10 · Apply and verify** | After approval, move without overwriting, verify file hashes, and record operations for undo. |

## What happens when evidence is limited?

| File situation | Organizer behavior |
| --- | --- |
| Readable document | Contents take priority over a misleading filename. |
| Unreadable or online-only file | Use filename/type facts without inventing a narrower subject. |
| ZIP archive | Member names are a listing; enclosed document contents are not read. |
| Uncertain match | Leave the file in place. |
| Existing project or application folder | Preserve it; do not regroup its contents. |

## Safeguards you can review

- **New groups need two files.** An existing suitable folder can receive one.
- **Source changes invalidate a move.** Files are checked again before applying.
- **Occupied destinations are preserved.** The app rejects collisions rather than replacing files.
- **Undo checks the destination.** Changed files and occupied original paths are preserved.

Folder protection uses conservative markers; it cannot recognize every application.
Review the preview before applying, especially on unfamiliar folders.

## Faster follow-up scans

Models start loading when work begins. Exact, validated decisions can be reused
within the same model session; evidence and filesystem checks still run each scan.
Models unload after **five idle minutes**. Changed inputs need fresh decisions.

Optional **Watch for new files** can sort new arrivals automatically when enabled.

[See fresh measurements and their limits →](benchmarks.md)

<details>
<summary><strong>Technical boundaries</strong></summary>

| Area | Limit |
| --- | --- |
| Evidence | Up to three PDF pages; bounded Office/text excerpts; no organizer OCR or media transcription. |
| Existing folders | At most four direct file samples, up to 1,600 characters each. Parent containers do not inherit child subjects. |
| New destinations | At most two new folder levels. Existing deeper hierarchies can be reused. |
| Model support | Numbered passages resolve back to literal evidence from the same file. A quote alone is not semantic approval. |
| Cache | Complete inputs, prompts, model, and settings identify reusable decisions. Holds and uncertain results are not retained. |
| Apply | Preflight the plan, check each source, journal the operation, move, then verify SHA-256 and source absence. |
| Recovery | Filesystem moves and journal commits are separate operations; crash-atomic recovery is not promised. Older journal records may lack hashes. |

Implementation: [Preparation](../app/core/preparation.py) ·
[Planning](../app/core/planner.py) · [Review](../app/core/reviewer.py) ·
[Apply](../app/core/executor.py) · [Undo](../app/core/undo.py)

</details>
