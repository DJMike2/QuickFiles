# Organizer preparation evaluation — 2026-10-25

This iteration implements steps 1–5 of [the organizer process](../docs/organizer-process.md).
Steps 6–10 are specified for later work. Preview measurements below use the
existing downstream folder/assignment algorithm, fed by the new preparation stage.
No personal files were moved. The old organizer and historical scanner benchmarks
were not rerun, so no before/after accuracy or speedup percentage is claimed.

## What changed

| Area | Previous implementation, inspected in source | This iteration |
| --- | --- | --- |
| Model evidence | 180-character excerpt; folder planning reduced it to 85 characters | Verified extractive summary, subject, identifiers and read limitations; no 85-character reduction |
| PDF evidence | First page | Up to three pages, bounded text; no OCR |
| Presentations | First slide for organization | Multiple slides within a bounded XML budget |
| Spreadsheets | Shared strings only | Shared strings and worksheet cells, including inline strings |
| Unsupported/unreadable content | Empty hint alongside the name | Explicit filename-only evidence and extension-derived broad kind |
| Summary grounding | No separate summary stage | Exact passage/subject checks; identifiers must appear in evidence; one correction attempt |
| Changed/empty files | No preparation-stage identity gate | Held before planning; identity checked after extraction and inference |
| Repeated organization previews | No summary cache | Local cache keyed by identity, evidence, model/settings and prompt version |

During live iteration, a free-form summary incorrectly described analysis
homework as probability homework. Free-form summaries were removed: Python now
constructs summaries only from verified passages selected by the model. PDF
typography is normalized before matching. Wide CSV evidence also exposed an
insufficient output budget; summaries now have a 1,024-token budget, and truncated
responses are discarded and retried rather than used as partial results.

## Controlled cases

The committed [fixture](../tests/fixtures/organizer_cases.json) defines **24 cases**
and independent review expectations. It includes misleading names, different
clients/courses, Spanish text, later-slide content, inline spreadsheet strings,
unreadable formats, injected instructions and preservation boundaries.

The local **Qwen3.5-4B Q6_K**, seed 42, CUDA backend on an **RTX 4070 Laptop GPU**,
produced these preparation results:

| Measure | Result |
| --- | ---: |
| Defined cases reviewed against fixture expectations | 24/24 justified outcomes |
| Project files pruned before inventory | 2 |
| Inventoried files | 22 |
| Files receiving a summary/evidence result | 19 |
| Content-based / filename-only evidence | 15 / 4 |
| Usable subjects | 15 |
| Uncertain/instruction-only files held | 4 |
| Existing nested file / empty stub / installer held | 1 / 1 / 1 |
| Preparation elapsed time, including model startup; cold summary cache | 147.748 s |
| Inventory comparison after evaluation | Unchanged |

The 24/24 result includes justified preservation/abstention decisions; it is
**not** a claim that 24 files were assigned destinations. Fourteen content-based
files and one filename-based file produced usable subjects. An instruction-only
file abstained. The invoice with an injected footer retained its invoice subject;
the footer was treated as quoted data, not an instruction from the user.

## Deterministic regression checks

The full current suite ran **66 tests: 65 passed, 1 skipped** (the skip needs
Windows file attributes). The 25 new preparation/summary tests cover misleading
names, early preservation, unavailable content, game/application markers,
before/during/after-read changes, stale cache invalidation, cancellation, links,
untrusted absolute paths, executable permissions, later slides/worksheet cells,
archive-listing provenance, invented quotes/identifiers, PDF typography,
correction retries, truncated responses, full summary delivery and literal HTML
display in previews. OneNote exports with only dates/links/homework headings are
treated as unavailable page bodies, rather than mistaken for readable subject
content. These checks ran on Linux; no new native Windows run is
claimed for this iteration.

## Real Downloads preview

The scanner inventoried **126 loose files**, preserving three detected existing
application/project folders. A deterministic **40-file sample across extensions**
was evaluated with the same local 4B model and CUDA backend.

| Measure | Result |
| --- | ---: |
| Files evaluated | 40 |
| Files excluded before model summarization | 7 (protected types or empty stub) |
| Files with an evidence/summary result | 33 |
| Content / filename / archive-listing basis | 17 / 14 / 2 |
| Usable subjects / uncertain files held | 23 / 10 |
| Cached summaries reused in the completed preview | 20 |
| Proposed moves / validator rejections | 19 / 0 |
| Proposed placements independently reviewed as justified | **19/19 (100%)** |
| Placement coverage among usable subjects | **19/23 (82.6%)** |
| Placement coverage across the entire sample | **19/40 (47.5%)** |
| Completed preview time, including model startup and mixed cache reuse | 425.725 s |
| Post-run paths, sizes, modification times and visible directory list | Unchanged |

The reviewed destinations were separate math/course folders, music, hockey data,
processed lab images, source code and Linux installation images. Evidence supports
those placements at that specificity. The 90% justified-placement target was met
on this preview; that does not mean 90% of Downloads was organized. Singleton
subjects, weak evidence and protected files remained in place. Several filename-
only files and application configuration files abstained; wide numeric CSV
summaries remain less descriptive than ordinary document summaries.

## Controlled preview and deferred assignment issue

The existing planner proposed **6 moves** for the controlled fixture, with zero
validator rejections and 19 summary-cache hits. Five were justified: three Acme
invoice/finance files and two files explicitly identifying STAT 415. One was
unsupported: generic probability notes were also assigned to STAT 415 despite
having no explicit course identifier. Its first-half summary correctly retained
only the probability topic; the inference occurred in the legacy assignment stage.

That controlled preview scored **5/6 justified placements (83.3%)**, placing
6/15 usable-subject files (40%). Combined with the real Downloads preview, the
two previews yielded **24/25 justified proposed placements (96%)**. Report both
individual results as well as the combined figure. These are manual judgments
on these samples, not calibrated model confidence or population accuracy.

The unsupported course assignment is recorded in the controlled fixture's
`preview_review` expectation. Enforcing course scope and independently reviewing
ambiguous assignments belong to deferred steps 8–9. They were not implemented
under the first-half scope. The controlled preview took 44.451 s with cached
summaries, left the inventory unchanged and did not apply any moves.

## Additional academic/PDF checks

To complement the format-spread preview, **13 additional real PDFs** were
evaluated, covering course notes, homework, operating systems, a textbook, a
resume, a programming project and a study guide. These are separate from the
40-file preview, giving **53 unique real files** checked in total.

Eleven produced grounded subjects; two were conservatively held after evidence
checks (the textbook's broken cover typography and the resume). The 11 accepted
results were reviewed as justified at their stated specificity. Eight files had
content-based evidence, including the two held results. Five used filename-only
evidence: three OneNote exports had no readable body, and two scanned lecture
PDFs had no extractable text. Those filename results do not establish unseen
lecture contents or a specific course unless the name itself identifies one.

The completed PDF evaluation took **265.079 s**, reused one cached summary and
left the inventory unchanged. It tested preparation only; it does not add any
proposed placements to the 24/25 placement-review denominator. Conservatism here
also shows a remaining coverage cost: some recognizable documents can be held
when the small model cannot produce an exact supported subject phrase.

## Reproduction and interpretation

Use the commands in [the process document](../docs/organizer-process.md), with the
configured local model and native scanner installed. Fixture creation refuses an
existing destination. Private model traces, per-file evidence and Before/After
reports were kept in `/tmp`, outside the source folder and version control.

The model runs use temperature 0 for extraction; the downstream legacy planner
retains its existing settings. Review means checking that the subject or proposed
destination is justified by the available evidence at its stated specificity.
Filename-only files cannot establish unseen document contents. Report placement
coverage separately from the percentage of proposed placements judged justified.
These local development samples do not establish population accuracy.
