# Independent messy-directory verification — 2026-10-25

**Verdict: file operations passed; the full organizer is not ready to call “set.”**
The new summary stage generally preserved the right evidence, but the existing
assignment stage still made two incorrect placements. One readable CSV was also
unnecessarily held after its summary failed grounding checks.

This is a new live-model test, separate from the earlier Downloads sample. Six
fresh directories containing **68 generated files** were created from predefined
[fixtures](../tests/fixtures/organizer_verification.json). Expected cohorts,
existing destinations, files that must stay, and forbidden course inferences were
saved before any inference. No production prompts or organizer code were tuned
after seeing these results. The run used the installed **Qwen3.5-4B Q6_K**, seed
42, CUDA, on Linux with the native scanner. No scripted model responses were used.

## Placement results

Every proposed destination was reviewed against the original content/filename
evidence and the predefined expectations. Automatic cohort flags helped identify
problems; they were not treated as an independent semantic accuracy score.

| Fresh directory | Generated files | Proposed and actually applied moves | Justified placements | Specific finding |
| --- | ---: | ---: | ---: | --- |
| Campus | 12 | 7 | 6/7 | Six explicit course files were correctly separated and existing folders reused; generic probability notes were assigned to STAT 205 without course evidence |
| Freelance | 11 | 6 | 6/6 | Three clients stayed separate; existing nested destinations reused; an occupied invoice preserved with a suffix |
| Household | 12 | 7 | 6/7 | Recipes, taxes and travel grouped sensibly, but one MP3 was incorrectly placed in recipes |
| Adversarial documents | 12 | 4 | 4/4 | Client files grouped correctly despite embedded instructions and misleading names; instruction-only text stayed put |
| Exports | 10 | 7 | 7/7 | Valid multi-page PDFs, header-only PDF filename fallback, CSV and ZIP-listing evidence handled; one other readable CSV was held |
| Protected boundaries | 11 | 0 | Not applicable | No eligible loose files; no AI calls or moves; existing applications/projects and links preserved |
| **Total** | **68** | **31** | **29/31 (93.5%)** | **Two incorrect placements** |

The corpus included **32 clearly groupable files**. Thirty received a proposed
move (93.8% coverage), and 29 were correctly placed (90.6% of those 32). Two were
left loose: one song whose companion was misassigned, and one CSV whose summary
failed verification. The extra incorrect move was the ambiguous probability file,
which was not counted among the 32 clearly groupable files.

These denominators matter: the 93.5% figure describes proposed placements in this
specific test, not all generated files, all directories, or calibrated confidence.
Both Campus and Household individually scored 6/7 (85.7%). The wrong MP3 placement
is a practical failure even though the aggregate exceeded 90%.

## Actual filesystem verification

For each disposable directory, the test ran the real scanner, preparation stage,
planner, validator, executor, SQLite journal and undo engine:

1. Hash all fixture files and record original paths, directory names and links.
2. Generate a real AI preview and confirm it does not change the directory.
3. Apply every validated proposal **only to generated files**, including the
   proposals that later semantic review judged wrong.
4. Compare the complete resulting tree and SHA-256 hashes with the proposed map.
5. Undo the batch and compare the original complete tree and hashes again.

All six scenarios passed these mechanical checks. **31 moves succeeded, zero
failed, and 42 journal operations were undone**, including created directories.
There were no content losses, overwritten files, leftover generated folders after
undo, or changes to protected existing files. The outside symlink target's hash
was unchanged. The no-move boundary scenario correctly performed no operations.
The six-scenario run took **453.367 seconds**, including model startup, cache
probes, application and undo; the extra probes below were measured separately.

## Additional live checks

- An unchanged second preparation reused **all seven cached summaries** with
  **zero new model calls** and left the files unchanged.
- Editing a generated Acme invoice into a Northstar invoice triggered **one new
  summary call**. The refreshed summary included Northstar and no stale Acme data.
- Undo changes file identity/ctime even when bytes match, so the first preparation
  after undo correctly refreshed six moved files. That refresh was not mistaken
  for an unchanged-cache test.
- Selecting a directory symlink resolved to the actual generated folder. The
  protected-only target produced zero actions and zero model calls.
- A file edited after scanning was held before inference, with zero model calls.
- Cancellation at a file boundary raised the expected interruption. This test
  does not measure cancellation during a blocked network/model request.

## What still needs work

The first-half evidence for the two wrong moves was accurate: the probability
notes did not claim a course, and the MP3 retained filename-only music evidence.
The unsupported decisions occurred downstream. The deferred assignment/review
steps need to reject an unsupported course scope and incompatible subjects.

The CSV miss also exposes a first-half coverage limitation: exact grounding
checks can hold a clearly readable file when the small model cannot copy a valid
subject/quote. Keeping the file is safe, but its grouping opportunity was missed.
These results support continuing development; they do not support declaring the
whole organizer finished.

## Reproduce

```sh
python -B tools/verify_organizer.py --output /tmp/quickfiles-new-verification
```

Choose a new directory; the tool refuses an existing output location. It creates
all inputs itself and never accepts a personal source directory. Use a writable
temporary path on the target system. The fixture PDFs are valid multi-page PDFs;
media/installer stubs deliberately exercise filename fallback and protection,
not decoding or execution. This run verifies Linux behavior, not a native Windows
run or GUI automation.

Original detailed artifacts remain local:

- `/tmp/quickfiles-independent-verification-01/expectations.json`
- `/tmp/quickfiles-independent-verification-01/report.json`
- `/tmp/quickfiles-independent-verification-01/extra-checks.json`

The reports retain the actual generated inputs, summaries, destinations, errors,
execution results, undo results and per-scenario counts. The extra checks were
run separately after the initial six-scenario report. The reproduction tool now
includes those same extra checks in its final report.
