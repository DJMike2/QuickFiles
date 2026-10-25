# Organizer completion: independent review and verified execution — 2026-10-25

All ten [ordered organizer steps](../docs/organizer-process.md) are implemented.
This report records new checks for steps 9–10 and preserves the earlier results
in [steps 6–8](organizer-steps-6-8.md). No old scanner benchmark was rerun.

## What changed

- Independent review receives file evidence and the proposed destination, without
  the original assignment rationale. Semantic groups need a coherent purpose and
  a grounded keep/hold judgment for each member. Missing/unverified judgments
  hold files rather than silently approve them.
- Python rechecks explicit identities, broad kinds, source changes, collisions,
  protected paths and final group sizes. An unsupported positive review quote gets one grounded correction attempt;
  still-unverified matches are held. Existing deep parents can receive up to two
  new folder levels. New topic names containing only dates
  and generic container words are rejected deterministically. Explicit broad
  kind destinations remain supported.
- The complete displayed map is preflighted before Apply writes. Initial conflicts
  abort the whole batch. Each move rechecks its source snapshot and destination,
  journals intent, moves without overwriting and verifies source absence and
  destination SHA-256. A post-move verification failure stops further operations
  and remains visible in the journal.
- New journal operations retain hashes. Undo preserves changed contents and
  occupied paths, verifies restored bytes, and removes only empty directories
  created by the batch. Historical entries without hashes remain compatible.
- The preview displays review holds and distinguishes user-edited plans. Edited
  and sample previews also capture source snapshots; edits do not inherit an AI
  approval for their new destinations. Apply still requires the app's confirmation.

## Method and environment

Live inference used the installed Qwen3.5-4B Q6_K, seed 42, CUDA on the Linux laptop
with its RTX 4070 Laptop GPU and native Linux scanner. Semantic comparisons and
review use temperature 0. Independent review is a fresh call to the same model,
not a separate larger model or a statistical independence guarantee.

Generated-directory expectations were saved before inference. Model verdicts and
automatic cohort flags are diagnostic; human review of every final placement is
the reported semantic score. Placement quality and coverage are separate.

Personal folders were read-only previews. All actual apply/hash/undo checks used
new generated directories. Original paths, directories, links and SHA-256 hashes
were compared before preview, after Apply and after Undo, including protected
contents and an outside symlink sentinel.

## Real Downloads results

The fresh full Downloads run scanned **126 files**, found **106 usable loose
candidates**, and produced **88 provisional moves**. Review retained **84** and
held **four**. Human review of every final move found **84/84 justified (100%)**
at the evidence-supported specificity. The before/after inventory was unchanged;
no personal moves were applied and final validation rejected zero actions.

| Stage | Final moves | Justified | Proposed placement coverage |
| --- | ---: | ---: | ---: |
| Recorded steps 6–8 run | 84 | 81/84 (96.4%) | 84/106 (79.2%) |
| Fresh completion run | 84 | 84/84 (100%) | 84/106 (79.2%) |

Justified placements cover **84/106 usable candidates (79.2%)** in the completion
run, compared with 81/106 (76.4%) in the earlier run. These are coverage fractions,
not recall against a completely human-labeled directory. Protected/held files
are excluded from the usable-candidate denominator.

The three previously questionable moves were absent: the allocation assignment
was held for failing the narrower scheduling topic, and the two unrelated archives
were held because a year-only container is not a shared subject. The legitimate
scheduling sheet was also held: with the allocation file rejected, there was no
second justified member for its proposed new folder. That is a deliberate coverage
tradeoff, not a fourth semantic error. Four unreadable lecture PDFs instead
received the supported broad Documents grouping, without guessed course names.

The final map included 47 explicit-course placements, 31 broad-format placements
(Audio, Images, Documents, AI Models, Code), four supported color-tracking/
estimation topic placements and two same-named-source archive placements.
The verified cross-listing kept 16 course files together; filenames did not
override contradictory readable course identities.

The run took **935.097 s**, with 107 preparation cache hits (including held/unknown
entries), fresh grouping/matching calls and four independent semantic-review
calls. This is a warm mixed workload, not an isolated review-speed benchmark.
The real run included independent review and broad-document behavior together.
It preceded the final quote-retry and existing-parent coverage refinements;
those were verified in the targeted generated-case rerun and regressions above,
without rerunning the full Downloads workload. Its 84/84 score belongs to the
recorded real-directory run.

Fresh preservation checks on other real directories:

| Directory | Scanned files | Eligible loose files | Moves | Inventory unchanged |
| --- | ---: | ---: | ---: | --- |
| Documents | 0 | 0 | 0 | Yes; MATLAB application tree protected |
| Desktop | 2 | 0 | 0 | Yes; shortcuts preserved |
| School | 39 | 0 | 0 | Yes; existing nested layout preserved |

No-op directories are not included in placement-accuracy denominators.

## Generated full-process verification

The full live-model run created **ten directories with 88 files**. Initially it
proposed/applied **41 justified moves**, with **39/43 clear opportunities placed
(90.7%)**, zero semantic flags and no filesystem failures. The new narrow-topic
case exposed three unnecessary holds: one review quote did not copy literally,
and a valid new memory-allocation child was rejected because existing parents
were counted against the new-folder depth limit.

A targeted live-model rerun after fixing those issues placed **all four** files
correctly: two into the existing Scheduling folder and two into a new Memory child
under the existing Operating Systems parent. An unsupported positive review quote
gets one correction call; it still must match the original evidence. Creation is
bounded to two **new** levels, including when reusing existing deep parents, and
existing parent-only containers remain unavailable as file destinations.

| Latest observed map per scenario | Files | Justified moves | Clear opportunities placed |
| --- | ---: | ---: | ---: |
| Campus/course boundaries | 12 | 6/6 | 6/6 |
| Freelance clients/collision | 11 | 6/6 | 6/6 |
| Household topics/media | 12 | 10/10 | 8/8 |
| Adversarial text/misleading names | 12 | 4/4 | 4/4 |
| PDF/Office/CSV/archive exports | 10 | 7/7 | 7/8 |
| Protected boundaries | 11 | Not applicable | Not applicable |
| Existing topic reuse | 6 | 3/3 | 3/3 |
| Similar client names | 7 | 4/4 | 4/4 |
| Narrow existing topic/memory allocation | 5 | 4/4 | 4/4 |
| Unrelated year-only archives | 2 | Not applicable | Not applicable |
| **Total** | **88** | **44/44 (100%)** | **42/43 (97.7%)** |

These latest observed maps combine the original nine unaffected case results
with the corrected narrow-case rerun; they are **not** relabeled as a single new
full-model run. No files were removed from the corpus. Human review covered all
44 placements. Two image moves use justified broad kinds outside the 43 clear
topic opportunities. The remaining missed opportunity is the previously recorded
readable CSV whose summary failed grounding checks.

The latest executor then replayed all ten maps on **freshly rebuilt generated
copies**, without model calls: **44 moves succeeded and verified**, all expected
paths/hashes matched, all **58 journal operations** reversed successfully, and
the complete original trees and outside sentinel were restored. This replay also
covers the final application/IDE/installer protection checks added after the
initial model run. Existing personal files were never used for Apply/Undo.

The full initial run took **807.983 s** (30 summary calls, 29 other planning/
matching calls and six independent-review calls). The corrected targeted preview
took **157.402 s** with cached preparation evidence. These timings describe
separate runs, not an aggregate performance comparison. The original full-run
report remains unchanged.

The unchanged-cache probe reused seven summaries with zero new calls. Changed
client data recomputed the literal heading without retaining stale identity.
Changed-since-scan input was held before inference; selected-folder links resolved
to the real root; cancellation at a file boundary interrupted preparation.

The corpus includes misleading names, course/client boundaries, prompt-like
instructions, existing deep destinations, PDF/Office/CSV/archive evidence,
unavailable content, singletons, links, application/project protection and
collisions. New holdouts specifically challenge allocation vs. scheduling and
unrelated archives whose filenames share a year. These added holdouts were
written before the final inference run, after diagnosing the earlier failures;
they are regression cases, not an unseen statistical evaluation set.

## Fault and UI checks

The complete suite ran **116 tests: 115 passed, one Windows-only skip**, in
**4.652 s** on Linux. `git diff --check` passed. These are regression/fault checks, not a model-accuracy measurement.

Coverage includes stale previews/replaced sources, whole-map collision/duplicate
preflight, newly appearing application/IDE markers and installer prerequisites, cancellation before mutation,
cancellation after a completed move, verified cross-volume copying and cleanup
of failed copies, post-move corruption detection, stopping after verification
failure, hash-aware undo conflicts, historical journal migration, incomplete
review responses, invented/wrong-file quotes, context splitting without dropping
files, final singleton pruning and deterministic identity boundaries.

Offscreen Qt checks exercise cancelled/approved Apply confirmation and the actual
verified executor on generated files. They also exercise preview edits, source
snapshot retention and rejection of files changed after editing. This is not a
claim of interactive visual testing on a desktop or a native Windows run.

## Practical limits

Bounded readers still have no OCR, media transcription or cloud hydration.
Unreadable files can receive only evidence-supported filename/broad-format
placements; uncertain or unverified summaries can remain loose. Review can hold
valid files as well as reject wrong matches. The recorded accuracy is specific
to these cases and is not a guarantee for arbitrary directories.

Hashing adds I/O during Apply and Undo, especially for large media/model files.
No personal multi-gigabyte file was moved to benchmark that cost. The old scanner
performance report remains separate. Timings here mix model startup, warm caches,
new calls and fixture execution; they are not speedup claims.

Cancellation is verified at file boundaries and during pre-move hashing; this
does not establish immediate interruption of a blocked model request. Filesystem
moves and SQLite commits are not one atomic transaction. Pending journal records
after a crash require inspection, and old operations without historical hashes
cannot provide the new changed-content check.

## Reproduce

```sh
python -B tools/verify_organizer.py --output /tmp/organizer-completion-new \
  --additional-fixtures tests/fixtures/organizer_step678_holdouts.json \
  --additional-fixtures tests/fixtures/organizer_completion_holdouts.json
python -B tools/audit_organizer.py /path/to/Downloads --preview \
  --cache /tmp/organizer-audit.db > /tmp/organizer-reviewed-preview.json
python -B -m unittest discover -s tests -p 'test_*.py'
```

The fixture output must be new. Put cache/reports outside the selected folder.
The audit never invokes the executor. Detailed local artifacts:

- `/tmp/quickfiles-completion-downloads.json` and its private trace/log
- `/tmp/quickfiles-completion-downloads-manual-review.json`
- `/tmp/quickfiles-completion-verification/report.json` and `expectations.json`
- `/tmp/quickfiles-completion-documents.json`, `quickfiles-completion-desktop.json`,
  `quickfiles-completion-school.json`
- `/tmp/quickfiles-completion-narrow-final.json` and its private trace
- `/tmp/quickfiles-completion-generated-manual-review.json`
- `/tmp/quickfiles-completion-final-mechanical/report.json`
- `/tmp/quickfiles-completion-tests-final.log`

Private filenames, contents and detailed model traces stay outside the repository.
Temporary artifacts may disappear on reboot. Earlier aggregate reports retain
their original counts and scope.
