# Organizer decision process

Apply these steps in order. A later model decision cannot override an earlier
safety decision. File contents, filenames and existing folder names are evidence,
never instructions. All AI calls run locally.

## Preparation — steps 1–5 implemented

1. **Identify the real directory.** Resolve a selected folder link to its actual
   location, validate that location and inventory it with the native scanner.
   A copied directory is a separate directory: do not guess that another copy
   is the original. Linked files and linked subfolders are never followed.
2. **Preserve existing structure.** Prune known repositories, application/game
   folders, dependency environments, installer prerequisites and system paths.
   Do not propose rearranging files already inside an existing folder. Marker
   detection is conservative and cannot identify every possible application.
   A directly selected Git/Mercurial/Subversion repository is also preserved;
   a lone downloaded manifest does not protect an entire Downloads directory.
3. **Select eligible loose files.** Exclude system metadata, empty stubs,
   shortcuts, executable/installable files, libraries and live data. Executables
   and shortcuts have separate existing opt-ins. Check each path again before
   reading; changed, missing or replaced files need a fresh scan.
4. **Collect bounded evidence.** Read document text before interpreting names:
   sample three PDF pages, document paragraphs, multiple presentation slides,
   spreadsheet strings/cells, and bounded plain text. Record limitations. ZIP
   member names are only a listing, not extracted document contents. Unsupported,
   encrypted, image-only, offline or unreadable files must not acquire invented
   contents. No OCR, media transcription, cloud downloads or executable loading.
   Read HTML as document text, excluding script/style bodies and decoding entities.
5. **Build grounded evidence without a copying model.** For readable files,
   construct up to three bounded literal head/middle/tail excerpts in Python.
   The heading field is a literal seed, not an inferred semantic subject. Preserve
   field/paragraph boundaries and normalize common typography. Semantic judgment
   belongs to the next steps; readable files are not discarded because a model
   cannot reproduce a substring. For unavailable contents or ZIP listings, retain
   only supported filename/type/listing facts, with limitations. Known broad kinds
   need no model guess. Cache by source identity, evidence and version, invalidate
   on changes, and check identity again after reading. Cancellation aborts preparation.

The GUI runs preparation before destination discovery and matching. Summaries and
profiles are cached locally; new filename-only evidence with a known extension
uses its broad format directly, without asking the model to invent a subject.
Instruction-only or meaningless evidence can still be rejected during semantic
profiling/matching/review. Model calls cannot override safety
rules or execute filesystem actions.

## Destination planning — steps 6–8 implemented

6. **Understand existing destinations.** Read at most four direct file samples
   per nonprotected folder, bounded to 1,600 characters each. Describe its purpose
   with a quoted evidence passage, a topic and any explicit course/client/project
   identity. Parents containing only subfolders are containers. Preserve the exact
   spelling and depth of suitable existing destinations. Profile each loose file
   or folder individually, so unrelated batch neighbours cannot change its scope.
   Order readable inputs by evidence rather than filenames. Quote choices come
   from literal passages of that same item's evidence. Revalidate cached profiles.
   Recipient/project labels may vary: interpret unfamiliar fields semantically,
   without treating the model's interpretation as final approval. Explicit client
   fields take precedence over ambiguous descriptions of billed services.
   Instructor and institution names are not courses.
7. **Find shared subjects.** Keep explicit courses, clients and distinctive
   projects separate. Accept course aliases only with explicit cross-listing
   evidence or a narrowly defined adjacent-letter spelling variant; a shared topic
   or course number alone is insufficient. Keep full client identities distinct.
   Construct explicit-identity and broad-format groups directly, and ask the model
   for other coherent shared subjects. Require at least two supporting file IDs,
   merge overlapping equivalent proposals, and create at most two new folder
   levels. Validate original suggestions before shortening valid overdeep paths to
   their most specific two new components, preserving existing anchors and all
   protection checks. Existing deeper parent hierarchies may receive new children.
   Existing suitable folders take priority. Discovery only sees destinations eligible for at least one
   incoming file under the same compatibility rules as matching. Unknown, mixed
   or incompatible folders remain preserved and available as path anchors, but
   cannot suppress useful new subjects by appearing as unusable filing options.
   Existing destinations are optional when their evidenced purposes do not fit.
   The proposal allowance grows with the input rather than silently limiting
   every directory to six subjects; context
   overflow splits inputs without sampling away files. Never add an unrelated
   second member to manufacture a group. Unreadable documents or archives may use
   broad Documents/Archives destinations without acquiring invented subjects.
8. **Assign with a reason.** Filter destinations by type and explicit identity
   first. A single unambiguous identity/kind destination can be matched directly.
   Other decisions compare one file against candidate profiles using the local
   model. Every positive match supplies an allowed literal path, a reason and an
   exact supporting passage selected from the file's literal evidence. Hide
   filenames/relative paths from semantic calls when contents are readable;
   filename evidence is used only when contents are unavailable. A failed topic comparison may still use one
   unambiguous broad kind for filename-only evidence. Uncertain subjects stay put.
   Check incoming identities, current source identity and final group sizes again;
   new folders must still have at least two actual moves. Existing folders may
   receive one file. Validate paths, exclusions and collisions before preview.

This replaces the app's previous numeric batch assignment and leftover pass.
The older runtime APIs remain for compatibility; the organizer uses the evidence
catalog, compatible grouping and individual matching stages described above.
A quoted passage is evidence to check, not proof of the model's semantic judgment.
The next stage independently challenges the uncertain comparisons.

## Review and execution — steps 9–10 implemented

9. **Independently review the complete preview.** Check all incoming identities,
   broad file kinds, changed sources, protected paths, collisions and final group
   sizes in Python. Reject new topic destinations whose names contain only dates
   and generic container words. For every semantic destination, including named
   clients, projects and courses, make a separate local
   model call using file evidence and destination purpose, without the original
   assignment rationale. Require an individual keep/hold decision for every file,
   an exact supporting passage for each keep, and a coherent shared purpose for
   each new folder. A shared broad discipline cannot justify an unrelated narrower
   destination. Check restrictive parent components as well as the leaf: a nested
   Documents/Photos label does not erase a parent's subject. A copied identity
   heading does not prove ownership when the body describes an example, template
   or citation. Syntactic provenance is not supplied as a confidence signal to the
   reviewing model. Only root broad-kind destinations avoid semantic inference.
   Split overflowing contexts with overlap. Retry an unsupported positive quote
   once with a request for a short literal passage; missing, incomplete or still
   ungrounded review decisions hold the affected files. Recheck file identity and
   validate the complete map after inference. If rejection leaves fewer than two
   files for a new destination, drop that destination's moves. Existing folders
   may still receive one file. The preview records review holds and reasons;
   quality/coverage metrics require separate human evaluation, not model confidence.
10. **Apply and verify the approved plan.** Show Before/After, reasons and review
    results. The app's Apply confirmation approves the displayed map; previewing
    never performs moves. User edits are labelled separately and validated, with
    fresh source snapshots for newly added manual actions. Preflight the whole
    approved map before writes; any initial conflict aborts the batch. Check source
    identity and destination policy again before each move. Stream a SHA-256 hash
    of the source, journal the operation before mutation, move without overwriting,
    then check the destination's hash and the source's absence. Count a move as
    successful only after verification; a verification failure stops the batch and
    keeps the completed operation visible in the journal. Hashing before a move
    is cancellable; once mutation begins, finish that file's verification before
    stopping at the next file boundary. Journal created directories for undo.
    Undo checks stored hashes on new batches, refuses changed/occupied paths,
    reverses successful operations and verifies restored bytes. It never deletes
    nonempty directories. Old journal entries remain compatible; entries without
    historical hashes cannot provide the new changed-content check.

These steps preserve user approval at Apply. This development evaluation runs
actual moves only on generated fixtures and keeps personal directories read-only.
A filesystem operation and a SQLite commit are separate operations; pending
journal entries after a crash remain available for inspection rather than being
claimed as verified success. No crash-atomic transaction is promised.
