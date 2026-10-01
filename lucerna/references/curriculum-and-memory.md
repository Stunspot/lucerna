# Curriculum and memory: preserve the thread, keep the route alive

This derivative combines Ed Escribo's curriculum architecture with Hyperion's situated model of understanding. The learner supplies purpose; the host interprets dialogue; `LearningStore` preserves and revises the structure needed to continue. Use this reference for sustained learning and meaningful re-entry.

## Design backward from a usable destination

Find what the learner wants to do, understand or investigate. 'Teach me organic chemistry' may mean reading mechanisms, preparing for a particular course, reasoning about a research paper or exploring a longstanding curiosity. Start useful teaching while clarifying the destination that would change the route. Recover prior experience, available time and relevant constraints from conversation rather than presenting an intake form.

Sketch the subject's conceptual backbone: the ideas that later reasoning relies on, the connections that make them usable, and the applications that reveal their purpose. Represent concepts as curriculum nodes. Use `requires` for a necessary prior connection, `related` for a useful association and `extends` for a deepening direction. A prerequisite edge points from the earlier concept to its dependent. Preserve the difference between the order you happened to explain things and a genuine dependency.

Choose a shape suited to the job. An integrative project can introduce concepts as their practical need appears. A broad survey can establish orientation before a learner selects a depth route. A portfolio of smaller creations can expose a range of techniques. Just-in-time learning can focus on one obstacle and its prerequisite closure. These are composable strategies; the supplied marketplace guide's course-sales mechanics and mandatory quiz patterns do not govern this product.

Give the curriculum enough whole-subject structure to preserve direction, then develop the next experiences at the depth the learner needs. Attach resources to the concepts they illuminate. A paper, authored lesson, interactive artifact or outside source may support several concepts. Keep a curriculum node's description about the understanding it enables, not a bureaucratic learning-objective formula.

## Store routes as intentions that can be revised

A curriculum is the concept/resource map. A pathway is a saved intention through that map: a destination, optional focus, available time and explicit skips. `save_pathway` preserves that intent; `resume_pathway` recomputes suggestions against current curriculum state. This distinction lets a later session continue intelligently after a misconception is repaired, a new prerequisite is added or the learner changes goals.

Use a concept's `status` as a provisional disposition: `new`, `exploring`, `usable`, `revisit` or `parked`. It is an editable teaching cue. Every concept remains accessible. If the learner wants to jump ahead, offer the useful connection and allow the jump. `skip_ids` changes the current route without asserting that the skipped idea is understood.

Time values are estimates for planning a session. The runtime uses a labeled ten-minute estimate where a node lacks one. Its budgeted plan exposes deferred material and the next suggested connection; the learner can still open it. Use the available time to select a coherent chunk, and revise your estimates from the work rather than treating them as a promise.

A spiral curriculum revisits concepts with newly available tools and applications. Express that through `revisit`, resources, deeper nodes or a changed pathway; keep the `requires` graph acyclic. When a saved pathway names a node being removed, retarget that pathway before removing the node. That conflict protects the learner's intended destination from disappearing silently.

## Recall situated evidence before choosing the next move

Use `context` on re-entry. It returns the learner profile, active relevant memories, recent learning encounters, a conversational continuation, curriculum summaries, saved pathways and a suggested route when a curriculum is selected. Read the remembered interpretations as provisional. A recent correction from the learner takes precedence over an old inference.

Use `recall` with the current topic or question and, when relevant, `curriculum_id`. It performs case-insensitive phrase and token matching across memory topics, text and concept IDs. Try an alternate term the learner used when the first query misses a useful bridge. This is local lexical retrieval; the host supplies semantic interpretation and decides which memory applies. Unscoped memories can help across a learner's curricula; another person's memory stays separate.

Distinguish memory kinds by what they contribute. `bridge` preserves a framing that helped and its boundary. `confusion` preserves an unresolved relation in the learner's own terms. `understanding` captures something the learner appears able to use. `preference`, `goal` and `constraint` retain stated context that changes teaching. `question` keeps an open thread available. Save only what would alter a future explanation or continuation.

Every memory has an evidence basis: `user-stated`, `observed` or `inferred`. Include a short source reference or excerpt when it anchors a consequential interpretation. 'Explained conservation using their own example' is more useful than 'strong learner.' 'Prefers seeing one worked instance before notation in this topic' is more useful than a fixed learning-style label. Keep sensitive diagnoses and unsupported personal traits out of the learner model.

Use active, resolved or superseded memory status to preserve the difference between an unresolved obstacle and one already repaired. Set an expiry only when a fact is temporary. Update an existing memory when its meaning is corrected; save a new one when the observation adds a distinct useful connection. The runtime preserves versions and rejects a stale `expected_revision` instead of overwriting a newer correction.

## Continue without making the learner keep records

Near a meaningful pause, save an encounter when it improves future continuity. Capture a short account of what was explored, what framing helped, which questions remain, and the natural thread to pick up. Summarize the useful learning state; a complete transcript and a score are unnecessary.

If the conversation also changes concept dispositions, send `status_updates` with that encounter and the curriculum's revision. The runtime commits the encounter and graph changes together. An interruption cannot leave a claimed state change detached from its observation. The host remains responsible for whether the conversation actually supports the interpretation.

When the learner returns, begin with the useful thread: 'Last time the shared-pair picture clicked; the open question was why this carbon is attacked.' Continue the teaching. Save durable changes as ordinary background work within the authorized learning scope, not as questions about whether every sentence deserves a form.

## Runtime shapes

The command `learning OP --input FILE.json` binds the JSON object's keys to the named method. These examples illustrate API shape; author the actual content from the current learner and source.

A useful memory (this example assumes curriculum `chemistry` and concept `electron-pairs` already exist; omit those two scope fields for an unscoped memory):

```json
{
  "learner_id": "default",
  "data": {
    "id": "charge-bridge",
    "kind": "bridge",
    "topic": "Charge and electron pairs",
    "text": "Moving a shared pair between atoms clarified the relation; connect the next mechanism to that picture.",
    "evidence": {"basis": "observed", "source_ref": "current learning conversation"},
    "curriculum_id": "chemistry",
    "concept_ids": ["electron-pairs"],
    "status": "active"
  }
}
```

A curriculum uses `save_curriculum` with `{"data": {...}}`. Its data includes `id`, `learner_id`, `title`, `goal`, `nodes` and `edges`. Each node has `id`, `title`, optional `description`, `minutes`, `status` and `resources`. Structured resources accept `{"type":"paper|lesson|artifact|url","id":"...","url":"...","anchor":"...","label":"..."}` with an ID or URL; plain descriptive reference strings also work. Edges use `{"from":"prerequisite-id","to":"dependent-id","relation":"requires"}`.

A saved route uses `save_pathway` with `{"curriculum_id":"...","data":{"id":"...","title":"...","target_ids":["..."],"focus_ids":[],"skip_ids":[],"available_minutes":30}}`. Pass `expected_revision` as a sibling of `data` when editing an entity already read. Empty target IDs mean the whole curriculum.

A continuation uses `record_encounter` with `learner_id` and `data`. Its data accepts `curriculum_id`, `concept_ids`, `summary`, `what_worked`, `open_questions`, `next_thread`, `evidence`, `status_updates` and `expected_curriculum_revision`. Keep `what_worked` and `open_questions` as arrays of strings; keep `status_updates` as a mapping from concept IDs to their new dispositions.

## Ownership, correction and portability

Use one explicit home for the selected learning workspace. Keep installed skill files separate from learner data so an upgrade replaces code while preserving learning. Retrieve the existing learner before saving an edit. New people receive separate profiles; IDs identify custody, not a claim about identity outside this workspace.

Show remembered context when the learner asks. Correct it directly when they correct you. `delete_memory` and `delete_encounter` remove the selected entity and its revision history. `delete_learner` through the packaged CLI or HTTP adapter removes the personal learning-store entities and their histories, plus that learner's notebooks, collections, concept connections, named editions and their history, browser progress, request claims and queued question context/results. Shared source and lesson documents remain in the library. External exports have separate custody; describe the requested deletion scope precisely.

`export_learner` creates a portable JSON bundle with the profile, memories, curricula, pathways, encounters and their revisions. It does not include personal research notebooks, collections, named explanation editions, queued questions or shared sources; moving the complete stopped learning home preserves those too. `import_learner` validates and imports that bundle atomically; an existing learner requires explicit `replace:true`. Keep replacement a deliberate user action. A failed import leaves the prior learner intact. Copies the user exported elsewhere remain their separate files.

## Preserve the meaning around a remembered fact

Memory is interpreted in a situation and at a time. Optional `valid_from`, `valid_to` and `expires_at` carry effective time; the runtime's creation/update timestamps carry recording time. Use ISO timestamps. A future-effective correction does not suppress today's applicable memory. An expired replacement does not resurrect an old fact that it already superseded.

Use `applies_when` for exact contextual selectors, for example `{"activity":"derivation","topic":"linear-algebra"}`. Pass the current selectors through `context --situation situation.json` or the `situation` argument to recall. Conditional memories require matching selectors to become operative. Without that context, they remain disclosed candidates, not assumed universal preferences. An empty selector object is general scope.

Optional `supersedes`, `conflicts_with` and `qualifies` arrays reference memory IDs belonging to this learner. Supersession requires the same curriculum and conditional scope and cannot form a cycle. A topic-specific exception should qualify the general record instead of deleting it. Conflicting interpretations travel together; the compiler does not pick a winner merely because one is newer. Related historical statements can support interpretation without becoming current instructions.

The context packet is a derived view (`canonical:false`) with its compilation time, situation, bounded related-memory context, unresolved conflicts, omitted-context counts and evidence gaps. Read those qualifications before acting. A source reference establishes traceability; check the source episode when tone, sequence or a correction could change the answer. Do not invent missing evidence to fill the gap. The bounded packet may omit material; retrieve the implicated record when it could change the next teaching move.

Forgetting a memory also removes incoming relationship references in retained memory history. Deleting a source through the workspace removes explicit document references and evidence excerpts from learning state while retaining independent learned observations. External copies and backups remain separately owned.
