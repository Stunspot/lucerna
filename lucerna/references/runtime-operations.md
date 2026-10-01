# Runtime operations

Use the bundled `scripts/teacher.py` as the direct interface. Resolve it relative to this installed skill, not to a remembered machine path or another skill. Python 3.10 or newer is required. The runtime uses its bundled dependencies and does not require an external model key. The active host agent does the teaching and scene authorship.

Every invocation includes the same explicit `--home` path. The examples below use `LEARNING_HOME` as a placeholder for the selected absolute user-data directory. Put temporary authoring JSON in that workspace or an authorized working folder. Keep installed code and learner data separate.

```text
python scripts/teacher.py --home "LEARNING_HOME" init
python scripts/teacher.py --home "LEARNING_HOME" serve
```

`serve` starts the local browser workspace and prints its loopback URL. Keep that process running while the learner uses the live workspace. Reuse a healthy existing server for this home when possible. On Windows, launch a background helper with a hidden window unless the user wants an interactive terminal. Stop the server when the user asks; starting it again with the same home resumes persisted work.

Explicit `--home` keeps the selected workspace authoritative. The runtime also supports `LUCERNA_HOME` for automatic home selection, with `TEACHING_HOME` retained for compatibility. Without either setting, an existing legacy default workspace is reused. A product-name change does not move learner data; recover and reuse the existing home.

## Direct command surface

| Work | Command after `--home "LEARNING_HOME"` |
|---|---|
| Read the source/lesson library | `library` |
| Import arXiv or a chosen local file | `import SOURCE` |
| Read an imported document | `document DOCUMENT_ID` |
| Create an authored lesson | `lesson --input lesson.json` |
| Assemble source, context and renderer contract | `packet DOCUMENT_ID --question "..." --section SECTION_ID` |
| Read the current authored explanation | `storyboard DOCUMENT_ID` |
| Validate and commit authored scenes | `publish DOCUMENT_ID storyboard.json --expected-revision N` |
| Call a learning operation | `learning OPERATION --input arguments.json` |
| Recover the learner's continuation | `context --learner default` |
| Find browser questions | `requests --status pending --learner LEARNER_ID` |
| Claim one request for resumable work | `claim --worker HOST_WORKER_ID --learner LEARNER_ID --request REQUEST_ID` |
| Read one question with its context packet | `request REQUEST_ID` |
| Complete a claimed request with its result | `resolve REQUEST_ID --status complete --worker HOST_WORKER_ID --claim-token TOKEN --input result.json` |
| Save a standalone exploration | `export DOCUMENT_ID output.html` |

Question and section flags are optional. Omit `--expected-revision` for the first publication of the shared original explanation; use the actual current revision for an edit. A newly created named edition already has revision 1, so its first publication uses `--expected-revision 1 --edition EDITION_ID --learner LEARNER_ID`. JSON input options accept a file path or `-` for standard input. Use files or structured input for multiline content rather than constructing a fragile shell-quoted JSON string. Read the launcher's `--help` for the exact current arguments before assuming an option.

The CLI returns JSON for machine use. Inspect its success or error instead of inferring completion from an artifact filename. If a packet is larger than the tool's display limit, preserve the returned JSON in the authorized work area and read relevant sections from it. The original document remains available; truncation in a tool display is not evidence that the paper itself is short.

## Import, author, publish, continue

Import the source and retain the returned document ID. Inspect its source identity, sections, extraction scope and warnings. An arXiv import seeks the full versioned paper, not merely the abstract. A local file is read only because it was explicitly selected by the operator or user.

Read the packet for that document and question. It contains the current storyboard when one exists. Author the desired storyboard as finite JSON in the renderer contract described in [Paper and scene authoring](paper-and-scene-authoring.md). Publish it through the CLI, inspect decisive visual behavior when browser tools are available, and give the learner the resulting workspace link. The live browser's Refresh explanation action loads a published revision.

For authored teaching material, `lesson --input lesson.json` takes the arguments of the lesson creation operation. A minimal payload is:

```json
{
  "title": "Rates and accumulated quantities",
  "text": "An initially empty container receives a constant inflow r liters per minute and has no outflow. After s minutes, its volume is r times s liters."
}
```

The result is a durable source document with `section-1` and `block-1`. More developed lessons can supply explicit `sections`, `source` attribution and `kind` (`lesson` or `visual-aid`). Use the same packet and publish route to attach interactive scenes. Published scene anchors refer to the authored lesson's real blocks, just as paper scenes refer to imported source blocks.

## Learning operation arguments

`learning OPERATION --input arguments.json` calls a method with the JSON object's fields as named arguments. For example, `save_memory` receives `{"learner_id":"default","data":{...},"expected_revision":1}`. `save_curriculum` receives `{"data":{...}}`; `plan_pathway` receives `{"curriculum_id":"...","options":{...}}`. Use [Curriculum and memory](curriculum-and-memory.md) for the meaning of those structures.

The operation surface includes learner profile save/get/list/delete; memory save/get/list/delete/recall; curriculum save/get/list/delete; pathway save/get/list/delete/plan/resume; encounter record/get/list/delete; `context`; `history`; `export_learner`; and `import_learner`. The actual operation names are the snake_case method names, such as `list_curricula`, `record_encounter` and `resume_pathway`.

An ID names the persisted entity, and a revision names its current version. Supply the revision you read when updating shared or resumed state. `conflict` means the state changed: read it, reconcile the intended edit and preserve the newer information. `invalid` identifies a malformed input or broken invariant. `not_found` means the named entity is absent. Those failures leave the previous committed state intact.

Create a second learner with `save_learner` when a separate person will use this learning home. Pass the correct learner ID explicitly to their operations. Select that person explicitly in the browser's learner selector; it governs personal context, notebooks, collections and editions. Do not imply that a person was selected merely because their name appeared in source material.

## Browser requests are real resumable work

At the beginning of resumed teaching, read pending requests. Inspect each relevant request with `request ID`; the packet includes its source/scene context when supplied. A request copied into the host conversation is a direct cue to act on that context. Check whether the user has already answered or cancelled it before continuing an obsolete instruction.

Claim substantive work using the worker and lease route below; a successful claim marks it `in_progress`. If the result says `claimed:false`, the request is unavailable to this claim; inspect its current state instead of assuming ownership. Produce its actual answer, new lesson or published scene revision. Resolve the claim as complete with its worker ID and token, plus a JSON result containing a clear `answer` and, when relevant, `document_id` and `scene_id`. A completed request needs a real result. Answer the learner in the host conversation and link the changed explanation; the browser does not contain an unattended agent.

```json
{
  "answer": "The explanation now separates the two quantities and shows their different roles.",
  "document_id": "DOCUMENT_ID",
  "scene_id": "SCENE_ID"
}
```

The allowed request states are pending, in progress, complete, failed and cancelled; commands use `pending`, `in_progress`, `complete`, `failed` and `cancelled`. Completed requests are terminal. Failed or cancelled requests can be explicitly returned to pending through `research retry_request` with `request_id` and `learner_id`; this clears the earlier result. If a tool failure prevents the requested output, preserve the specific failure in a result and use `failed`; keep the useful source and any previous working explanation available. A fresh follow-up can create a new request with the current context.

The live browser saves reading position and scene parameters to the selected home. A standalone HTML export carries a snapshot and supports its local interactions; it does not write new state back to the live workspace. Its Copy to my conversation action carries a question to the host agent. Reopen the live workspace when durable pathway or request updates are needed.

## Data custody and practical recovery

The selected home contains `learning.sqlite3` for learner state, `workspace.sqlite3` for documents, storyboards, progress and queued requests, and `papers/` for retained imported sources. `server.json` identifies the latest local server. Stop the runtime before copying the complete home for a simple backup; include all contents rather than selecting individual database files while a writer is active.

Learner export/import moves the profile, memories, curricula, pathways, encounters and their revisions from `learning.sqlite3`. It does not include notebooks, collections, explanation editions, queued questions or shared sources from `workspace.sqlite3`. Copy the stopped, complete learning home to move all of those together. A source/scene HTML export moves one exploration. These are different artifacts. Neither silently installs a skill on the recipient's host.

Through the CLI or HTTP delivery adapter, `delete_learner` also removes that learner's notebooks, collections, concept connections, named editions and their history, browser progress, request claims and queued request context/results. Shared source and lesson documents stay in the library. Deleting the default learner permits a new blank default profile to be created on a later runtime start. External backups and exported copies remain separately owned files.

If a source host fails, import a user-selected local copy. If PDF text extraction cannot expose the needed equation or figure, inspect the retained source with available host tools or use a better source format. If a device voice is unavailable, use the complete written explanation and transcript. If browser automation is unavailable, commit only what can be checked and state that visual interaction was not inspected. These limitations describe the actual missing capability without discarding the usable work.

## Search, notebooks and parallel explanations

`search "QUERY"` returns matching source passages with real document, section and block IDs. Optional `--document`, `--collection`, `--kind`, `--learner`, `--limit` and `--offset` narrow the results. `library` accepts `--query`, `--kind`, `--collection`, `--family`, `--learner`, `--limit` and `--offset`; its query matches title, summary and author metadata. Use `document DOCUMENT_ID` for one complete source or `search ... --document DOCUMENT_ID` for matching passages within it. Source families use verified arXiv identities or matching source hashes; title resemblance is not enough. Full-text indexing is local lexical search, not an embeddings service.

`research OPERATION --input arguments.json` exposes the same research operations as the browser. Fields bind directly to the selected method. Useful payloads are:

```json
{"document_id":"DOCUMENT_ID","title":"A geometric explanation","learner_id":"default","clone_default":true}
```

Use that with `create_edition`. It returns the edition ID and revision. Use `save_annotation` with `{"document_id":"DOCUMENT_ID","learner_id":"default","payload":{"text":"My observation","anchors":[{"block_id":"BLOCK_ID"}],"provenance":{"basis":"agent-authored","author":"Teaching agent"}}}`. User-authored observations use `user-stated`; do not impersonate their authorship.

`save_connection` receives `document_id`, `learner_id` and `payload`. Its payload has `from` and `to` concepts, each with `label`, optional `document_id`, and `block_id` or an `anchors` array. Add `relation` (`requires`, `explains`, `contrasts`, `extends`, `related`), a substantive `explanation` and provenance. Connections describe an authored claim; they do not establish learner mastery or automatic semantic discovery. Edits use `connection_id`/`annotation_id` and the current `expected_revision`.

Other operations include `catalogue`, `search_sources`, `document_metadata`, `document_section`, `list_collections`, `get_collection`, `save_collection`, `delete_collection`, `list_annotations`, `delete_annotation`, `concept_graph`, `delete_connection`, `list_editions`, `get_edition`, `rename_edition`, `delete_edition`, `renew_request_claim`, `retry_request` and `cleanup_deleted_sources`. Inspect the bundled method signature before authoring an unfamiliar payload; do not guess fields.

## Appearance in the working explorer

The header's Visual theme selector offers LAMPLIGHT, APERTURE and MARGIN. Apply a choice through that actual control, then inspect the requested source/experiment state. It changes the whole interface without rebuilding the active scene. The choice belongs to browser storage (`lucerna-visual-theme/v1`), separate from the home, learner profile and scene progress; do not edit those stores to change appearance. A standalone export includes all three complete treatments and its own usable selector. [Full design records](visual-themes.md) retain each identity and its material application. Diagram color semantics and source content are deliberately preserved.

## Claim work before processing a browser request

For shared or resumable request handling, use `claim --worker HOST_WORKER_ID --learner LEARNER_ID --request REQUEST_ID --lease 600`. The result includes the claimed request and a claim token; retain both. Read `request ID`, do the actual work, then resolve with `resolve ID --status complete --worker HOST_WORKER_ID --claim-token TOKEN --input result.json`. Results may include `answer`, `document_id`, `scene_id` and `edition_id`. Renew a long-running claim through `research renew_request_claim` with `{"request_id":"REQUEST_ID","worker_id":"HOST_WORKER_ID","claim_token":"TOKEN","lease_seconds":600}` before its lease expires. An expired lease can be reclaimed; a different worker cannot resolve an active claim without its token. The simpler unclaimed update route remains available for a single active host, but cannot override another worker's live claim.

The browser's learner selector governs its personal notebook, collections, editions and learning context. Source documents and the original exploration are shared within the home. This is a personal local workspace with scoped profiles, not a multi-tenant authentication boundary. Export carries the selected source, explanation and scene-position snapshot with local math assets; it excludes notebooks and the full learner profile. An exported file can still contain private source text and scene context, so share the specific artifact deliberately.

`showcase` lists original working labs; `showcase attention`, `showcase memory`, `showcase equilibrium` or `showcase oscillator` installs one without downloading a paper. The browser exposes the same lab launcher. Reopening a lab preserves an existing edited exploration.

`delete-document DOCUMENT_ID` removes the retained workspace source cache and its indexed passages, editions, notes, connections, progress and requests, plus explicit learning references. Original files outside the home are untouched. Interrupted cache deletion is journaled and can resume through `research cleanup_deleted_sources`. Backups and standalone exports are independent copies. Never use a source deletion as a substitute for a requested learner correction.
