# Organizer steps 6–8 — 2026-10-25

Steps 6–8 now discover existing destinations, form compatible shared groups, and
assign files with a reason and evidence. The ordered implementation and the
remaining steps 9–10 are in [organizer-process.md](../docs/organizer-process.md).

## Method and scope

Tests used the installed Qwen3.5-4B Q6_K model, seed 42, CUDA on the Linux laptop
with its RTX 4070 Laptop GPU and the native Linux scanner. Catalog/group/matching
calls use temperature 0. These were live model calls, not scripted responses.
Generated fixtures had expectations saved before inference. Every proposed
placement was reviewed against its available evidence and the expected subjects;
automatic flags and the model's own confidence were not accuracy measurements.
Personal directories were previewed only; actual apply/undo used disposable files.

The target is a justified destination at the evidence's supported specificity,
not one mandatory folder spelling. Report placement quality separately from
coverage. Protected/no-op directories do not increase placement accuracy.
These observations do not guarantee accuracy for arbitrary directories.

## Historical comparison

The [earlier independent run](organizer-independent-verification.md) used six
messy directories, 68 generated files and the previous assignment stage. It
proposed 31 moves: 29 were justified (93.5%). Its two errors were generic
probability notes assigned to an unsupported named course and an MP3 assigned to
recipes. That old implementation was **not rerun**. The new pipeline rejects
both errors in the matching fixtures. The extended corpus below also adds
existing-topic reuse and similar-but-distinct client names, so its aggregate is
not a paired benchmark on an identical corpus.

## Generated messy directories

| Scenario | Generated files | Moves | Justified | Clear opportunities placed |
| --- | ---: | ---: | ---: | ---: |
| Campus/course boundaries | 12 | 6 | 6/6 | 6/6 |
| Freelance clients/collision | 11 | 6 | 6/6 | 6/6 |
| Household topics/media | 12 | 10 | 10/10 | 8/8 |
| Adversarial text/misleading names | 12 | 4 | 4/4 | 4/4 |
| PDF/Office/CSV/archive exports | 10 | 7 | 7/7 | 7/8 |
| Protected boundaries | 11 | 0 | Not applicable | Not applicable |
| Existing topic reuse | 6 | 3 | 3/3 | 3/3 |
| Similar client names | 7 | 4 | 4/4 | 4/4 |
| **Total** | **81** | **40** | **40/40 (100%)** | **38/39 (97.4%)** |

Two additional image moves used justified broad Images grouping; their subject
was unavailable, so they are not counted among the 39 clear topic opportunities.
One readable CSV was held when its model-generated summary failed exact evidence
checks. Existing Recipes and Taxes folders were reused, including a single tax
file. Acme Dental and Acme Manufacturing stayed separate; ambiguous Acme stayed
loose. Unknown singletons, unsupported course inferences, protected projects and
instruction-only text stayed put. An occupied invoice was preserved using a
validated suffix.

All 40 moves actually succeeded on generated files. Exact paths and SHA-256 hashes
matched the plan. All **53 journal operations**, including folder creation, were
undone and the complete original trees/hashes restored. The outside symlink
sentinel was unchanged. Zero execution failures or automatic semantic flags were
recorded; manual review covered all 40 placements rather than equating zero flags
with accuracy.

The unchanged preparation probe reused seven summaries with zero new model calls.
Changing a client heading recomputed the correct literal summary without model
inference and removed stale client data. Changed-since-scan input was held with
zero calls. Selected-folder links resolved to the real root. Cancellation at a
file boundary raised the expected interruption.

The eight-scenario run took 544.930 s, including startup, execution/undo and cache
probes (31 summary calls, 21 downstream calls). This is an observation, not an
organizer speedup or a cold-cache benchmark. Subsequent actual-data corrections
for explicit course cross-listing, instructor-name roles and broad unreadable
document fallback have dedicated regression checks; the full expensive fixture
run was not repeated after those narrowly scoped corrections.

## Real laptop directories

The full read-only Downloads preview scanned **126 files**, produced **106 usable
loose-file candidates**, and proposed **84 moves**. Manual review of every move
found **81/84 justified (96.4%)**. The proportion of usable candidates proposed
for placement was **84/106 (79.2%)**; justified placements covered **81/106
(76.4%)**. These coverage denominators exclude protected and held files, so they
are not recall against a complete human-labeled directory. The validator rejected
zero moves and the before/after inventory was unchanged.

| Downloads destinations | Moves | Justified |
| --- | ---: | ---: |
| Explicit course groups, including a verified cross-listing | 47 | 47 |
| Audio, Images, AI Models and Code | 27 | 27 |
| Color tracking and statistical estimation topics | 4 | 4 |
| Operating-system scheduling | 2 | 1 |
| Year-only archive group | 2 | 0 |
| Same-source named archive group | 2 | 2 |
| **Total** | **84** | **81** |

Three proposals are counted as failures: a memory-allocation assignment was put
in the narrower scheduling destination, and a project archive and sports-data
archive were grouped only by year. A literal supporting quote did not prevent
these semantic errors. The 90% target was exceeded, but this is not an all-correct
or finished-organizer claim. Existing application/project folders remained
protected. Other held files included failed summary grounding, unavailable
subjects, ambiguous matches, new singletons and explicitly protected file types.
The corrected course aliases recovered 16 cross-listed files without equating
unrelated courses that merely share a number.

A final targeted read-only preview on the four unreadable lecture PDFs verified
the broad Documents behavior: **4/4 justified moves**, zero validator rejections,
and unchanged inventory. This used cached evidence without loading the model.
It is a separate subset check, not four extra independent examples to add to the
full Downloads score. The full 84-move preview preceded the final broad fallback;
its recorded results have not been relabeled as a rerun of that last change.

The full Downloads run took **837.204 s**, with 107 preparation cache hits
(including held/unknown entries). It still ran fresh grouping/matching calls.
This warm mixed workload is not comparable to the generated cold-start timings
as a performance benchmark.

Documents contained a protected MATLAB application tree: zero eligible loose
files and zero moves. Desktop contained two protected shortcuts: zero moves.
School contained 39 already nested files: zero eligible loose files and zero
moves. Their inventories stayed unchanged. Empty media folders offered no
placement cases and are not counted toward accuracy. These checks establish
preservation behavior, not successful organization of additional files.

## Limits and remaining work

Readable content takes precedence over misleading filenames. A real lab file
whose name identifies one class but whose body explicitly identifies another
was assigned from the body. Cross-listed MATH/STAT identities require literal
cross-listing evidence; course numbers alone cannot establish equivalence.
Instructor and university names are not accepted as course identities.

Evidence readers remain bounded; there is no OCR, audio transcription or cloud
hydration. Filename-only files permit broad format groups. A readable CSV can
still be held by the summary grounding gate. Unknown singletons and ambiguous
matches can remain loose. Therefore placement precision is not the proportion
of every scanned file organized. Quoted evidence does not itself prove the
model's semantic inference. Step 9's independent review remains unimplemented,
as does the redesigned step 10 completion pass. Existing executor/journal/undo
behavior was exercised on fixtures; no GUI automation or new native Windows run
is claimed. The suite passed **88 tests run: 87 passed, one Windows-only skip**
on Linux in 6.582 s. `git diff --check` passed.

## Reproduce and local artifacts

```sh
python -B tools/verify_organizer.py --output /tmp/organizer-new-verification \
  --additional-fixtures tests/fixtures/organizer_step678_holdouts.json
python -B tools/audit_organizer.py /path/to/Downloads --preview \
  --cache /tmp/organizer-audit.db > /tmp/organizer-preview.json
python -B -m unittest discover -s tests -p 'test_*.py'
```

The fixture output must be a new directory. The audit never invokes the executor.
Use a writable cache/report path outside the directory being organized.

Detailed reports and private document traces stay local in `/tmp`:

- `quickfiles-step678-verification-final/report.json` and `expectations.json`
- `quickfiles-step678-downloads-reviewed.json` and its trace
- `quickfiles-step678-downloads-manual-review.json`, `quickfiles-step678-downloads-source-evidence.json`,
  `quickfiles-step678-downloads-broad-documents.json`
- `quickfiles-step678-documents.json`, `quickfiles-step678-desktop.json`,
  `quickfiles-step678-school.json`
- `quickfiles-step678-tests-final.log`

The aggregate report commits no private document contents. Temporary artifacts
may disappear on reboot. Earlier scanner timings and older organizer reports
remain unchanged; no old benchmark was rerun to create this comparison.
