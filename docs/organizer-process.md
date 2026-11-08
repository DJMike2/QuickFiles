# How QuickFiles organizes your files

**Choose a folder → review the preview → apply when ready.**

[Setup](../README.md#get-started) · [Measured performance](benchmarks.md)

> [!TIP]
> Analysis runs locally. Scanning never moves your files; internet access is needed
> to download the engine and model.

```mermaid
flowchart LR
    A[Check folders] --> B[Read evidence]
    B --> S[Understand file purposes]
    S --> C[Find shared subjects]
    C --> L[Plan a consistent layout]
    L --> M[Match original evidence]
    M --> D[Independent review]
    D --> E[Before / After]
    E -->|You approve| F[Apply and verify]
    F --> G[Undo]
```

## From folder to preview

| Step | What happens |
| --- | --- |
| **Check the location** | Resolve the selected folder. Preserve system paths, exclusions, links and protected file types. |
| **Understand folders** | The model checks bounded file/layout evidence to distinguish filing collections from applications, projects and managed data. Uncertain folders stay untouched. |
| **Read loose files** | Collect literal document excerpts, archive member names or honest filename/type fallbacks. Existing folder contents stay in place. |
| **Understand file purposes** | Describe readable files briefly with literal support. Unavailable contents retain filename/type facts. Matching and review keep the original evidence and filename context. |
| **Understand destinations** | Sample direct files from approved filing folders. The model judges their purpose without fixed subject or folder-name aliases. |
| **Find shared subjects** | Propose shared subjects in context-sized batches. Check unavailable files for broad kind collections, and omitted readable files once more. |
| **Plan the layout** | Compare overlapping collection proposals by meaning, then plan the whole directory before naming destinations. Reconcile aliases, reuse appropriate existing folders, and choose shared parents where justified. |
| **Match files** | Compare original evidence against existing destinations and the model's suggested new groups. If uncertain, compare the full catalog. New folders need at least two supported files. |
| **Review independently** | A separate model decision challenges each proposed placement. Unsupported matches stay where they are. |
| **Validate the preview** | Check paths, collisions, source identities and folder snapshots before offering moves. |

A folder's own text cannot authorize entry by itself: when a filing judgment uses
text previews, a second layout-only check can hold it back. This reduces misleading
README effects; model decisions still need your review.

## When evidence is limited

| Situation | Behavior |
| --- | --- |
| Readable document | Contents take priority over a misleading filename. |
| Unreadable or unsupported file | Use filename/type facts without inventing a narrower subject. |
| ZIP archive | Sample member names, without unpacking enclosed files. Oversized ZIPs or archive indexes fall back to filename/type. |
| Application, project or uncertain folder | Do not traverse it or offer it as a destination. |
| No justified match | Leave the file in place. |

## Applying and undoing

- **Folder changes require another scan.** Approved folders and source files are checked again before each move; the app accounts for its own changes within the batch.
- **No overwrites.** Occupied destinations are rejected. Completed moves are checked with SHA-256 and recorded in a local journal.
- **Undo touches journalled paths only.** It checks folder identities, file hashes and occupied original locations. Changed files and nonempty created folders are preserved.

Filesystem changes and journal commits are separate operations; crash-atomic
recovery is not promised. Older journal records may lack hashes or folder snapshots.

## Follow-up scans

Unchanged folder judgments and readable evidence can be reused from a local cache.
Changed evidence, prompts, models or settings invalidate the relevant decisions;
uncertain judgments and unreadable content are retried. Exact validated model
responses can also be reused within the current session.

Models load when work begins and unload after **five idle minutes**. Optional
**Watch for new files** uses the same folder, matching, review and apply checks,
placing arrivals into existing folders only.

<details>
<summary><strong>Evidence limits and implementation</strong></summary>

| Area | Bound |
| --- | --- |
| Folder role | Up to 32 direct entries, one child-layout level and three small text previews; 3,500 characters per view. Metadata checks still scale with direct entry counts. |
| File contents | Up to three PDF pages; bounded Office/text excerpts. No organizer OCR or media transcription. |
| Existing destinations | Up to three direct samples, read within 4,096 bytes and included within 600 characters each. Parent containers do not inherit child subjects. |
| Model support | Numbered passages resolve to literal evidence from the same file. A quote alone is not semantic approval. |

[Folder checks](../app/core/folder_policy.py) · [Evidence](../app/core/preparation.py) ·
[Planning and review](../app/core/planner.py) · [Apply](../app/core/executor.py) ·
[Undo](../app/core/undo.py)

</details>
