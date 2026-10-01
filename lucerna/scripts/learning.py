"""Durable learner context and curriculum/pathway infrastructure.

The host intelligence interprets conversation. This module preserves those
interpretations with their evidence, supports correction and forgetting, and
recommends routes through explicitly authored prerequisite graphs. Dispositions
are editable context, never grades or access gates. No network/model dependency.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import copy
import json
import math
import re
import sqlite3
import uuid


class LearningError(ValueError):
    """A stable delivery-adapter error: invalid (400), not_found (404), conflict (409)."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _fail(message, code="invalid"):
    raise LearningError(code, message)


def _text(value, field, required=False, maximum=50000):
    if not isinstance(value, str) or (required and not value.strip()):
        _fail(f"{field} must be {'nonempty ' if required else ''}text")
    if len(value) > maximum:
        _fail(f"{field} exceeds {maximum} characters")
    return value


def _strings(value, field):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        _fail(f"{field} must be a list of text values")
    return list(dict.fromkeys(value))


def _identifier(value, field="id"):
    _text(value, field, required=True, maximum=200)
    if any(ord(c) < 32 for c in value):
        _fail(f"{field} contains control characters")
    return value


def _allowed(value, choices):
    return isinstance(value, str) and value in choices


def _plain(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
    except (ValueError, TypeError, OverflowError) as exc:
        _fail(f"Payload must contain finite JSON values: {exc}")


def _datetime(value, field):
    if value is None:
        return None
    _text(value, field, required=True)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            _fail(f"{field} must include a timezone")
        return stamp
    except ValueError:
        _fail(f"{field} must be an ISO-8601 timestamp with timezone")


class LearningStore:
    """State owner for one explicitly selected home; safe for threaded adapters.

    Methods return detached JSON-compatible dictionaries/lists. For edits,
    expected_revision enables optimistic concurrency; a stale writer gets a
    conflict and changes nothing. IDs may be caller-supplied for idempotent saves.
    Histories contain entity versions, never hidden reasoning or full transcripts.
    """
    KINDS = {"learner", "memory", "curriculum", "pathway", "encounter"}
    DISPOSITIONS = {"new", "exploring", "usable", "revisit", "parked"}
    MEMORY_KINDS = {"understanding", "confusion", "bridge", "preference", "goal", "constraint", "question"}
    META = {"revision", "created_at", "updated_at", "learner_id"}

    def __init__(self, home: str | Path):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "learning.sqlite3"
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS learning_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO learning_meta VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS learning_entities (
                    kind TEXT NOT NULL, id TEXT NOT NULL, learner_id TEXT NOT NULL,
                    parent_id TEXT, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, id)
                );
                CREATE INDEX IF NOT EXISTS learning_owner ON learning_entities(learner_id, kind);
                CREATE INDEX IF NOT EXISTS learning_parent ON learning_entities(kind, parent_id);
                CREATE TABLE IF NOT EXISTS learning_revisions (
                    kind TEXT NOT NULL, id TEXT NOT NULL, learner_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    changed_at TEXT NOT NULL, PRIMARY KEY(kind, id, revision)
                );
            """)
            version = conn.execute("SELECT value FROM learning_meta WHERE key='schema_version'").fetchone()[0]
            if version != "1":
                _fail(f"Unsupported learning database schema {version}")

    @contextmanager
    def _connection(self, write=False):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _read(self, conn, kind, entity_id, required=True):
        _identifier(entity_id)
        row = conn.execute("SELECT payload FROM learning_entities WHERE kind=? AND id=?", (kind, entity_id)).fetchone()
        if row is None:
            if required:
                _fail(f"{kind} '{entity_id}' does not exist", "not_found")
            return None
        return json.loads(row[0])

    def _list(self, conn, kind, learner_id=None, parent_id=None):
        query, args = "SELECT payload FROM learning_entities WHERE kind=?", [kind]
        if learner_id is not None:
            query += " AND learner_id=?"
            args.append(learner_id)
        if parent_id is not None:
            query += " AND parent_id=?"
            args.append(parent_id)
        query += " ORDER BY updated_at DESC, id"
        return [json.loads(row[0]) for row in conn.execute(query, args)]

    def _merge(self, conn, kind, data):
        if not isinstance(data, dict):
            _fail(f"{kind} payload must be an object")
        clean = _plain(data)
        entity_id = _identifier(clean.get("id", uuid.uuid4().hex))
        previous = self._read(conn, kind, entity_id, False)
        merged = dict(previous or {})
        merged.update(clean)
        merged["id"] = entity_id
        return merged, previous

    def _save(self, conn, kind, data, learner_id, parent_id=None, expected_revision=None):
        if expected_revision is not None and (not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0):
            _fail("expected_revision must be a nonnegative integer")
        previous = self._read(conn, kind, data["id"], False)
        old_revision = previous["revision"] if previous else 0
        if expected_revision is not None and expected_revision != old_revision:
            _fail(f"{kind} revision changed: expected {expected_revision}, found {old_revision}", "conflict")
        if previous and previous["learner_id"] != learner_id:
            _fail("An entity cannot move between learners; export and import explicitly", "conflict")
        stamp = _now()
        saved = _plain(data)
        saved.update(learner_id=learner_id, revision=old_revision + 1,
                     created_at=previous["created_at"] if previous else stamp, updated_at=stamp)
        body = json.dumps(saved, ensure_ascii=False, allow_nan=False)
        conn.execute("INSERT OR REPLACE INTO learning_entities VALUES (?,?,?,?,?,?,?,?)",
                     (kind, saved["id"], learner_id, parent_id, saved["revision"], body, saved["created_at"], stamp))
        conn.execute("INSERT INTO learning_revisions VALUES (?,?,?,?,?,?)",
                     (kind, saved["id"], learner_id, saved["revision"], body, stamp))
        return saved

    def _delete(self, conn, kind, entity_id):
        self._read(conn, kind, entity_id)
        conn.execute("DELETE FROM learning_revisions WHERE kind=? AND id=?", (kind, entity_id))
        conn.execute("DELETE FROM learning_entities WHERE kind=? AND id=?", (kind, entity_id))

    def get_learner(self, learner_id):
        with self._connection() as conn:
            return self._read(conn, "learner", learner_id)

    def list_learners(self):
        with self._connection() as conn:
            return self._list(conn, "learner")

    def save_learner(self, data, expected_revision=None):
        with self._connection(True) as conn:
            item, _ = self._merge(conn, "learner", data)
            _text(item.get("name", ""), "name", required=True, maximum=500)
            item.setdefault("preferences", {})
            if not isinstance(item["preferences"], dict):
                _fail("preferences must be an object")
            item["goals"] = _strings(item.get("goals", []), "goals")
            item["context"] = _text(item.get("context", ""), "context")
            return self._save(conn, "learner", item, item["id"], expected_revision=expected_revision)

    def delete_learner(self, learner_id):
        """Forget this learner and all owned entities and their revision histories.

        External exports/backups are separate files under the user's custody.
        """
        with self._connection(True) as conn:
            self._read(conn, "learner", learner_id)
            count = conn.execute("SELECT COUNT(*) FROM learning_entities WHERE learner_id=?", (learner_id,)).fetchone()[0]
            conn.execute("DELETE FROM learning_revisions WHERE learner_id=?", (learner_id,))
            conn.execute("DELETE FROM learning_entities WHERE learner_id=?", (learner_id,))
        return {"deleted": learner_id, "entities_deleted": count}

    def history(self, kind, entity_id):
        if kind not in self.KINDS:
            _fail("Unknown learning entity kind")
        with self._connection() as conn:
            self._read(conn, kind, entity_id)
            return [json.loads(row[0]) for row in conn.execute(
                "SELECT payload FROM learning_revisions WHERE kind=? AND id=? ORDER BY revision", (kind, entity_id))]
    def _validate_memory(self, conn, learner_id, data, allow_missing_curriculum=False):
        item, _ = self._merge(conn, "memory", data)
        self._read(conn, "learner", learner_id)
        if not isinstance(item.get("kind"), str) or item.get("kind") not in self.MEMORY_KINDS:
            _fail("memory kind must be understanding, confusion, bridge, preference, goal, constraint, or question")
        item["topic"] = _text(item.get("topic", ""), "topic", maximum=1000)
        item["text"] = _text(item.get("text", ""), "text", required=True)
        evidence = item.get("evidence")
        if not isinstance(evidence, dict) or not _allowed(evidence.get("basis"), {"user-stated", "observed", "inferred"}):
            _fail("memory evidence requires basis user-stated, observed, or inferred")
        for field in ("source_ref", "excerpt", "rationale"):
            if field in evidence:
                _text(evidence[field], f"evidence.{field}")
        item.setdefault("status", "active")
        if not isinstance(item["status"], str) or item["status"] not in {"active", "resolved", "superseded"}:
            _fail("memory status must be active, resolved, or superseded")
        item["concept_ids"] = _strings(item.get("concept_ids", []), "concept_ids")
        _datetime(item.get("expires_at"), "expires_at")
        start = _datetime(item.get("valid_from"), "valid_from")
        end = _datetime(item.get("valid_to"), "valid_to")
        if start and end and end <= start:
            _fail("valid_to must follow valid_from")
        conditions = item.setdefault("applies_when", {})
        if not isinstance(conditions, dict) or len(conditions) > 20:
            _fail("applies_when must be an object of at most 20 exact context selectors")
        for key, value in conditions.items():
            _text(key, "applies_when key", required=True, maximum=100)
            _text(value, "applies_when value", required=True, maximum=1000)
        for relation in ("supersedes", "conflicts_with", "qualifies"):
            item[relation] = _strings(item.get(relation, []), relation)
            if len(item[relation]) > 100 or item["id"] in item[relation]:
                _fail("Memory relationships must name at most 100 other memories")
            for target_id in item[relation]:
                target = self._read(conn, "memory", target_id)
                if target["learner_id"] != learner_id:
                    _fail("Memory relationships cannot cross learners")
                if relation == "supersedes":
                    if target.get("curriculum_id") != item.get("curriculum_id") or target.get("applies_when", {}) != conditions:
                        _fail("Supersession requires the same curriculum and context; use qualifies for a contextual exception")
                    # A new edge may not make its own ancestor a descendant.
                    frontier, visited = [target], set()
                    while frontier:
                        ancestor = frontier.pop()
                        if ancestor["id"] in visited:
                            continue
                        visited.add(ancestor["id"])
                        if item["id"] in ancestor.get("supersedes", []):
                            _fail("Supersession relationships contain a cycle")
                        frontier.extend(self._read(conn, "memory", ident) for ident in ancestor.get("supersedes", []))
        # A referenced target cannot be moved into a different scope while a
        # surviving supersession still claims to replace it in the old scope.
        for referring in self._list(conn, "memory", learner_id):
            if referring["id"] != item["id"] and item["id"] in referring.get("supersedes", []):
                if (referring.get("curriculum_id") != item.get("curriculum_id")
                        or referring.get("applies_when", {}) != conditions):
                    _fail("This memory is superseded in its existing scope; update that relationship before changing curriculum or context")
        if item.get("curriculum_id"):
            curriculum = self._read(conn, "curriculum", item["curriculum_id"], required=not allow_missing_curriculum)
            if curriculum is not None:
                if curriculum["learner_id"] != learner_id:
                    _fail("Memory curriculum belongs to another learner")
                known = {node["id"] for node in curriculum["nodes"]}
                if set(item["concept_ids"]) - known:
                    _fail("Memory refers to an unknown curriculum concept")
        return item

    def save_memory(self, learner_id, data, expected_revision=None):
        with self._connection(True) as conn:
            item = self._validate_memory(conn, learner_id, data)
            return self._save(conn, "memory", item, learner_id,
                              parent_id=item.get("curriculum_id"), expected_revision=expected_revision)

    def get_memory(self, memory_id):
        with self._connection() as conn:
            return self._read(conn, "memory", memory_id)

    def list_memories(self, learner_id, include_inactive=True):
        with self._connection() as conn:
            self._read(conn, "learner", learner_id)
            items = self._list(conn, "memory", learner_id)
        return items if include_inactive else [item for item in items if self._active(item)]

    @staticmethod
    def _active(item, at=None):
        at = at or datetime.now(timezone.utc)
        expiry = _datetime(item.get("expires_at"), "expires_at")
        start = _datetime(item.get("valid_from"), "valid_from")
        end = _datetime(item.get("valid_to"), "valid_to")
        return (item.get("status") == "active" and (expiry is None or expiry > at)
                and (start is None or start <= at) and (end is None or at < end))

    def _memory_view(self, learner_id, curriculum_id=None, situation=None):
        """Interpret explicit temporal/context relations; never infer contradictions."""
        situation = situation or {}
        if not isinstance(situation, dict):
            _fail("situation must be an object of context selector values")
        items = self.list_memories(learner_id)
        if curriculum_id is not None:
            curriculum = self.get_curriculum(curriculum_id)
            if curriculum["learner_id"] != learner_id:
                _fail("Curriculum belongs to another learner")
            items = [m for m in items if m.get("curriculum_id") in {None, curriculum_id}]
        now = datetime.now(timezone.utc)
        applicable = [m for m in items if all(situation.get(k) == v for k, v in m.get("applies_when", {}).items())]
        # An expired replacement does not silently resurrect the superseded claim.
        replaced = {ident for m in applicable
                    if (_datetime(m.get("valid_from"), "valid_from") or _datetime(m["created_at"], "created_at")) <= now
                    for ident in m.get("supersedes", [])}
        operative = [m for m in applicable if self._active(m, now) and m["id"] not in replaced]
        conditional = [m for m in items if self._active(m, now) and m not in applicable and m.get("applies_when")]
        return operative, conditional, {m["id"]: m for m in items}


    def delete_memory(self, memory_id):
        """Forget text/history and scrub inbound relationship IDs atomically.

        Surviving observations keep their wording and provenance. Their saved
        revisions lose the forgotten identifiers too, so export/reimport cannot
        restore a dangling relationship or reintroduce the forgotten record.
        """
        relationships_removed = 0
        with self._connection(True) as conn:
            target = self._read(conn, "memory", memory_id)
            for table in ("learning_entities", "learning_revisions"):
                rows = conn.execute("SELECT rowid,payload FROM " + table +
                    " WHERE kind='memory' AND learner_id=? AND id!=?",
                    (target["learner_id"], memory_id)).fetchall()
                for row in rows:
                    value = json.loads(row["payload"])
                    changed = False
                    for relation in ("supersedes", "conflicts_with", "qualifies"):
                        prior = value.get(relation, [])
                        if memory_id in prior:
                            value[relation] = [ident for ident in prior if ident != memory_id]
                            relationships_removed += 1
                            changed = True
                    if changed:
                        conn.execute("UPDATE " + table + " SET payload=? WHERE rowid=?",
                                     (json.dumps(value, ensure_ascii=False, allow_nan=False), row["rowid"]))
            self._delete(conn, "memory", memory_id)
        return {"deleted": memory_id, "relationship_records_cleaned": relationships_removed}

    def recall(self, learner_id, query="", curriculum_id=None, limit=12, situation=None):
        """Case-insensitive phrase/token recall, no remote embeddings or inference.

        Empty query returns recent active memories. Curriculum scope includes
        unscoped learner memories plus memories of that curriculum, never another
        learner's context. Matching rank describes retrieval, not learner ability.
        """
        _text(query, "query", maximum=3000)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            _fail("limit must be an integer from 1 to 100")
        items, _, _ = self._memory_view(learner_id, curriculum_id, situation)
        phrase = " ".join(query.casefold().split())
        if not phrase:
            return items[:limit]
        tokens = set(re.findall(r"[^\W_]+", phrase, re.UNICODE))
        matches = []
        for item in items:
            haystack = " ".join((item["topic"], item["text"], " ".join(item["concept_ids"]))).casefold()
            haystack = " ".join(haystack.split())
            words = set(re.findall(r"[^\W_]+", haystack, re.UNICODE))
            rank = 5 * int(phrase in haystack) + len(tokens & words)
            if rank:
                matches.append((rank, item["updated_at"], item))
        matches.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [entry[2] for entry in matches[:limit]]

    @staticmethod
    def _resources(value):
        if not isinstance(value, list):
            _fail("resources must be a list")
        for resource in value:
            if isinstance(resource, str):
                _text(resource, "resource", required=True)
            elif isinstance(resource, dict):
                if not _allowed(resource.get("type"), {"paper", "lesson", "artifact", "url"}):
                    _fail("resource type must be paper, lesson, artifact, or url")
                if not resource.get("id") and not resource.get("url"):
                    _fail("resource requires id or url")
                for key in ("id", "url", "anchor", "label"):
                    if key in resource:
                        _text(resource[key], f"resource.{key}")
            else:
                _fail("resource must be text or a structured reference")
        return value

    def _validate_curriculum(self, conn, data):
        item, previous = self._merge(conn, "curriculum", data)
        learner_id = _identifier(item.get("learner_id", ""), "learner_id")
        self._read(conn, "learner", learner_id)
        _text(item.get("title", ""), "title", required=True, maximum=1000)
        item["goal"] = _text(item.get("goal", ""), "goal")
        nodes, edges = item.get("nodes", []), item.get("edges", [])
        if not isinstance(nodes, list) or len(nodes) > 5000:
            _fail("nodes must be a list of at most 5000 concepts")
        if not isinstance(edges, list) or len(edges) > 50000:
            _fail("edges must be a list of at most 50000 relations")
        known = set()
        for node in nodes:
            if not isinstance(node, dict):
                _fail("Each curriculum node must be an object")
            node_id = _identifier(node.get("id", ""), "node.id")
            if node_id in known:
                _fail(f"Duplicate concept ID {node_id}")
            known.add(node_id)
            _text(node.get("title", ""), "node.title", required=True, maximum=1000)
            node["description"] = _text(node.get("description", ""), "node.description")
            node.setdefault("status", "new")
            if not isinstance(node["status"], str) or node["status"] not in self.DISPOSITIONS:
                _fail(f"Unknown concept disposition {node['status']}")
            if "minutes" in node and (isinstance(node["minutes"], bool) or not isinstance(node["minutes"], (int, float)) or not math.isfinite(node["minutes"]) or node["minutes"] <= 0):
                _fail("node.minutes must be a positive finite estimate")
            node["resources"] = self._resources(node.get("resources", []))
        dependencies = {node_id: [] for node_id in known}
        edge_keys = set()
        for edge in edges:
            if not isinstance(edge, dict):
                _fail("Each curriculum edge must be an object")
            source, target = _identifier(edge.get("from", ""), "edge.from"), _identifier(edge.get("to", ""), "edge.to")
            relation = edge.setdefault("relation", "requires")
            if source not in known or target not in known:
                _fail("Every curriculum edge must reference existing concepts")
            if source == target:
                _fail("A concept cannot depend on or relate to itself")
            if not isinstance(relation, str) or relation not in {"requires", "related", "extends"}:
                _fail("Relation must be requires, related, or extends")
            key = (source, target, relation)
            if key in edge_keys:
                _fail("Duplicate curriculum edge")
            edge_keys.add(key)
            if relation == "requires":
                dependencies[target].append(source)
        # Kahn's algorithm rejects cycles without recursion depth limits.
        indegree = {node_id: len(required) for node_id, required in dependencies.items()}
        outgoing = {node_id: [] for node_id in known}
        for node_id, required in dependencies.items():
            for source in required:
                outgoing[source].append(node_id)
        ready = [node_id for node_id in known if indegree[node_id] == 0]
        visited = 0
        while ready:
            source = ready.pop()
            visited += 1
            for target in outgoing[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
        if visited != len(known):
            _fail("Prerequisite relations contain a cycle")
        item["nodes"], item["edges"] = nodes, edges
        if previous:
            removed = {node["id"] for node in previous["nodes"]} - known
            for route in self._list(conn, "pathway", parent_id=item["id"]):
                used = set(route.get("target_ids", [])) | set(route.get("focus_ids", [])) | set(route.get("skip_ids", []))
                if used & removed:
                    _fail("Retarget saved pathways before deleting their referenced concepts", "conflict")
        return item

    def save_curriculum(self, data, expected_revision=None):
        with self._connection(True) as conn:
            item = self._validate_curriculum(conn, data)
            return self._save(conn, "curriculum", item, item["learner_id"], expected_revision=expected_revision)

    def get_curriculum(self, curriculum_id):
        with self._connection() as conn:
            return self._read(conn, "curriculum", curriculum_id)

    def list_curricula(self, learner_id):
        with self._connection() as conn:
            self._read(conn, "learner", learner_id)
            return self._list(conn, "curriculum", learner_id)

    def delete_curriculum(self, curriculum_id):
        with self._connection(True) as conn:
            self._read(conn, "curriculum", curriculum_id)
            for route in self._list(conn, "pathway", parent_id=curriculum_id):
                self._delete(conn, "pathway", route["id"])
            self._delete(conn, "curriculum", curriculum_id)
        # Conversational memories/encounters remain owned by the learner. Their
        # curriculum references describe historical context, not foreign authority.
        return {"deleted": curriculum_id}
    def _route_options(self, curriculum, options):
        if not isinstance(options, dict):
            _fail("Pathway options must be an object")
        item = _plain(options)
        known = {node["id"] for node in curriculum["nodes"]}
        for key in ("target_ids", "focus_ids", "skip_ids"):
            item[key] = _strings(item.get(key, []), key)
            if set(item[key]) - known:
                _fail(f"{key} contains unknown curriculum concepts")
        if item.get("available_minutes") is not None:
            minutes = item["available_minutes"]
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
                _fail("available_minutes must be a positive finite number")
        return item

    def save_pathway(self, curriculum_id, data, expected_revision=None):
        with self._connection(True) as conn:
            curriculum = self._read(conn, "curriculum", curriculum_id)
            item, previous = self._merge(conn, "pathway", data)
            if previous and previous["curriculum_id"] != curriculum_id:
                _fail("A pathway cannot change its owning curriculum", "conflict")
            item = self._route_options(curriculum, item)
            item.setdefault("title", curriculum["title"])
            _text(item["title"], "title", required=True, maximum=1000)
            item["curriculum_id"] = curriculum_id
            item["graph_revision_at_save"] = curriculum["revision"]
            return self._save(conn, "pathway", item, curriculum["learner_id"], curriculum_id, expected_revision)

    def get_pathway(self, pathway_id):
        with self._connection() as conn:
            return self._read(conn, "pathway", pathway_id)

    def list_pathways(self, learner_id=None, curriculum_id=None):
        with self._connection() as conn:
            if learner_id is not None:
                self._read(conn, "learner", learner_id)
            if curriculum_id is not None:
                curriculum = self._read(conn, "curriculum", curriculum_id)
                if learner_id and curriculum["learner_id"] != learner_id:
                    _fail("Curriculum belongs to another learner")
            return self._list(conn, "pathway", learner_id, curriculum_id)

    def delete_pathway(self, pathway_id):
        with self._connection(True) as conn:
            self._delete(conn, "pathway", pathway_id)
        return {"deleted": pathway_id}

    def plan_pathway(self, curriculum_id, options=None):
        """Recommend a prerequisite-aware route; users may open any concept.

        targets constrain the route to their prerequisite closure; focus breaks
        ties among currently eligible concepts; skip_ids bypasses concepts for
        this recommendation only. Time budget selects a coherent prefix and
        exposes deferred work. Missing estimates use 10 minutes, labeled below.
        No disposition is assigned automatically by this function.
        """
        curriculum = self.get_curriculum(curriculum_id)
        opts = self._route_options(curriculum, options or {})
        nodes = {node["id"]: node for node in curriculum["nodes"]}
        index = {node_id: i for i, node_id in enumerate(nodes)}
        prerequisites = {node_id: [] for node_id in nodes}
        for edge in curriculum["edges"]:
            if edge["relation"] == "requires":
                prerequisites[edge["to"]].append(edge["from"])
        targets = opts["target_ids"] or list(nodes)
        selected, pending = set(), list(targets)
        while pending:
            node_id = pending.pop()
            if node_id not in selected:
                selected.add(node_id)
                pending.extend(prerequisites[node_id])
        skipped = set(opts["skip_ids"])
        usable = {node_id for node_id in selected if nodes[node_id]["status"] == "usable"}
        remaining = selected - skipped - usable
        focus = set(opts["focus_ids"])
        ordered, considered = [], set(usable) | skipped
        while remaining:
            eligible = [node_id for node_id in remaining if set(prerequisites[node_id]) <= considered]
            if not eligible:
                _fail("Stored prerequisite graph cannot be planned")
            eligible.sort(key=lambda node_id: (node_id not in focus,
                                                {"revisit": 0, "exploring": 1, "new": 2, "parked": 3}[nodes[node_id]["status"]],
                                                index[node_id]))
            node_id = eligible[0]
            remaining.remove(node_id)
            considered.add(node_id)
            ordered.append(node_id)
        active_memories = self.recall(curriculum["learner_id"], curriculum_id=curriculum_id, limit=100)
        budget = opts.get("available_minutes")
        scheduled, deferred, used, overflowed = [], [], 0, False
        for node_id in ordered:
            node = copy.deepcopy(nodes[node_id])
            estimate = node.get("minutes", 10)
            missing = [p for p in prerequisites[node_id] if nodes[p]["status"] != "usable" and p not in skipped]
            if node_id in focus:
                reason = "Your selected focus, with its prerequisites arranged before it."
            elif node["status"] == "revisit":
                reason = "Revisit this connection before building further on it."
            elif node["status"] == "exploring":
                reason = "Continue the idea already under exploration."
            elif node_id not in targets:
                reason = "A prerequisite connection on the way to your chosen destination."
            elif node["status"] == "parked":
                reason = "Previously parked; included as a suggestion you may skip or reopen."
            else:
                reason = "A next connection toward the curriculum goal."
            node.update(prerequisites=prerequisites[node_id], suggested_preparation=missing,
                        ready=not missing, reason=reason, estimated_minutes=estimate,
                        estimate_assumed="minutes" not in node, access="open",
                        memory_hints=[m for m in active_memories if node_id in m.get("concept_ids", [])][:4])
            if overflowed or (budget is not None and used + estimate > budget):
                overflowed = True
                node["deferred_reason"] = "Beyond the current time budget; still available to explore."
                deferred.append(node)
            else:
                used += estimate
                scheduled.append(node)
        return {"curriculum_id": curriculum_id, "learner_id": curriculum["learner_id"],
                "title": curriculum["title"], "goal": curriculum["goal"], "graph_revision": curriculum["revision"],
                "target_ids": targets, "nodes": scheduled, "deferred": deferred,
                "next_node_id": scheduled[0]["id"] if scheduled else (deferred[0]["id"] if deferred else None),
                "estimated_minutes": used, "available_minutes": budget,
                "skipped_ids": opts["skip_ids"], "already_usable_ids": sorted(usable, key=index.get),
                "complete_for_current_dispositions": not ordered,
                "guidance": "Recommendations are editable; every concept remains open. Estimates are planning aids."}

    def resume_pathway(self, pathway_id):
        pathway = self.get_pathway(pathway_id)
        return {"pathway": pathway, "plan": self.plan_pathway(pathway["curriculum_id"], pathway)}

    def record_encounter(self, learner_id, data, expected_revision=None):
        """Save the useful learning continuation; apply optional graph edits atomically.

        summary is a conversational observation, not a transcript requirement.
        status_updates are host/user interpretations accompanied by this evidence;
        they remain editable and never turn into a score or locked prerequisite.
        """
        with self._connection(True) as conn:
            self._read(conn, "learner", learner_id)
            item, previous = self._merge(conn, "encounter", data)
            item["summary"] = _text(item.get("summary", ""), "summary", required=True)
            for key in ("concept_ids", "what_worked", "open_questions"):
                item[key] = _strings(item.get(key, []), key)
            item["next_thread"] = _text(item.get("next_thread", ""), "next_thread")
            evidence = item.setdefault("evidence", {"basis": "inferred"})
            if not isinstance(evidence, dict) or not _allowed(evidence.get("basis"), {"user-stated", "observed", "inferred"}):
                _fail("Encounter evidence requires basis user-stated, observed, or inferred")
            updates = item.get("status_updates", {})
            if not isinstance(updates, dict) or any(not isinstance(status, str) or status not in self.DISPOSITIONS for status in updates.values()):
                _fail("status_updates must map concept IDs to valid editable dispositions")
            curriculum_id = item.get("curriculum_id")
            curriculum = None
            if curriculum_id:
                curriculum = self._read(conn, "curriculum", curriculum_id)
                if curriculum["learner_id"] != learner_id:
                    _fail("Encounter curriculum belongs to another learner")
                known = {node["id"] for node in curriculum["nodes"]}
                if (set(item["concept_ids"]) | set(updates)) - known:
                    _fail("Encounter refers to an unknown curriculum concept")
            elif updates:
                _fail("status_updates requires curriculum_id")
            # Check encounter concurrency before touching its curriculum.
            old_revision = previous["revision"] if previous else 0
            if expected_revision is not None and expected_revision != old_revision:
                _fail("Encounter changed since it was read", "conflict")
            if curriculum and updates:
                if data.get("expected_curriculum_revision") is not None and data["expected_curriculum_revision"] != curriculum["revision"]:
                    _fail("Curriculum changed since this encounter began", "conflict")
                for node in curriculum["nodes"]:
                    if node["id"] in updates:
                        node["status"] = updates[node["id"]]
                        node["disposition_evidence"] = {"encounter_id": item["id"], "basis": evidence["basis"]}
                curriculum = self._save(conn, "curriculum", curriculum, learner_id)
                item["resulting_curriculum_revision"] = curriculum["revision"]
            item.pop("expected_curriculum_revision", None)
            return self._save(conn, "encounter", item, learner_id, curriculum_id, expected_revision)

    def get_encounter(self, encounter_id):
        with self._connection() as conn:
            return self._read(conn, "encounter", encounter_id)

    def list_encounters(self, learner_id, curriculum_id=None, limit=20):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            _fail("limit must be an integer from 1 to 1000")
        with self._connection() as conn:
            self._read(conn, "learner", learner_id)
            return self._list(conn, "encounter", learner_id, curriculum_id)[:limit]

    def delete_encounter(self, encounter_id):
        with self._connection(True) as conn:
            self._delete(conn, "encounter", encounter_id)
        return {"deleted": encounter_id}

    def context(self, learner_id, curriculum_id=None, query="", situation=None):
        """A bounded, rebuildable re-entry packet, not another canonical store."""
        learner = self.get_learner(learner_id)
        curriculum = self.get_curriculum(curriculum_id) if curriculum_id else None
        if curriculum and curriculum["learner_id"] != learner_id:
            _fail("Curriculum belongs to another learner")
        encounters = self.list_encounters(learner_id, curriculum_id, 3)
        routes = self.list_pathways(learner_id, curriculum_id)
        selected = self.recall(learner_id, query, curriculum_id, situation=situation)
        operative, conditional, by_id = self._memory_view(learner_id, curriculum_id, situation)
        eligible_ids = {m["id"] for m in operative}
        # Relationships are directed authored claims, but discovery must work
        # from either endpoint. Only currently applicable records enter operative
        # context; older and differently scoped records remain visibly separate.
        edges = [(memory["id"], relation, target_id)
                 for memory in by_id.values()
                 for relation in ("supersedes", "conflicts_with", "qualifies")
                 for target_id in memory.get(relation, [])]
        selected_ids = {m["id"] for m in selected}
        closure_limit = 32
        changed = True
        while changed and len(selected) < closure_limit:
            changed = False
            for source_id, relation, target_id in edges:
                if source_id not in selected_ids and target_id not in selected_ids:
                    continue
                for endpoint in (source_id, target_id):
                    if endpoint in eligible_ids and endpoint not in selected_ids and len(selected) < closure_limit:
                        selected.append(by_id[endpoint])
                        selected_ids.add(endpoint)
                        changed = True
        related = []
        conflicts = []
        conflict_pairs = set()
        relevant_edges = [edge for edge in edges if edge[0] in selected_ids or edge[2] in selected_ids]
        for source_id, relation, target_id in relevant_edges[:200]:
            source, target = by_id.get(source_id), by_id.get(target_id)
            related.append({"from_id": source_id, "relation": relation, "target_id": target_id,
                            "source": source, "target": target,
                            "state": ("unavailable" if source is None or target is None else
                                      "applicable" if source_id in eligible_ids and target_id in eligible_ids else
                                      "historical-or-contextual")})
            if relation == "conflicts_with" and source_id in eligible_ids and target_id in eligible_ids:
                pair = tuple(sorted((source_id, target_id)))
                if pair not in conflict_pairs:
                    conflicts.append({"memory_ids": [source_id, target_id], "resolution": "unresolved"})
                    conflict_pairs.add(pair)
        omitted_related_ids = {endpoint for source_id, _, target_id in relevant_edges
                               for endpoint in (source_id,target_id)
                               if endpoint in eligible_ids and endpoint not in selected_ids}
        relation_limits = {"memory_limit": closure_limit, "relations_limit": 200,
                           "omitted_applicable_memories": len(omitted_related_ids),
                           "omitted_relations": max(0,len(relevant_edges)-200)}
        return {"canonical": False, "compiled_at": _now(), "situation": situation or {},
                "learner": learner, "memories": selected, "memory_relations": related,
                "unresolved_conflicts": conflicts, "contextual_candidates": conditional[:12],
                "relation_limits": relation_limits,
                "evidence_gaps": [{"memory_id": m["id"], "issue": "No source locator retained"}
                                  for m in selected if not m.get("evidence", {}).get("source_ref")],
                "recent_encounters": encounters,
                "next_thread": next((e["next_thread"] for e in encounters if e.get("next_thread")), ""),
                "curricula": [{k: c[k] for k in ("id", "title", "goal", "revision")} for c in self.list_curricula(learner_id)][:30],
                "pathways": routes[:10],
                "suggested_pathway": self.plan_pathway(curriculum_id, routes[0] if routes else {}) if curriculum else None,
                "interpretation": ("Recover what governs this lesson from the current request, effective time, context and evidence. "
                                   "Related historical records explain change; contextual candidates apply only when their selectors fit. "
                                   "Keep unresolved conflicts visible. A newer timestamp alone does not settle them. "
                                   "Treat missing source locators as an evidence gap. Recall ranks relevance, never truth or ability.")}
    def export_learner(self, learner_id):
        """Portable JSON bundle containing current entities and their revisions."""
        with self._connection() as conn:
            conn.execute("BEGIN")  # one consistent snapshot while other sessions teach
            learner = self._read(conn, "learner", learner_id)
            entities = {kind: self._list(conn, kind, learner_id) for kind in sorted(self.KINDS) if kind != "learner"}
            history = [{"kind": row["kind"], "id": row["id"], "revision": row["revision"],
                        "payload": json.loads(row["payload"]), "changed_at": row["changed_at"]}
                       for row in conn.execute("SELECT * FROM learning_revisions WHERE learner_id=? ORDER BY kind,id,revision", (learner_id,))]
        return {"format": "teaching-learning-bundle", "schema_version": 1,
                "exported_at": _now(), "learner": learner, "entities": entities, "history": history}

    def import_learner(self, bundle, replace=False):
        """Validate and import one complete learner atomically.

        Existing learner IDs conflict unless replace=True. A malformed bundle
        rolls back even when replacing. Entity IDs cannot collide with another
        learner's state. Histories travel with their current entities; forgotten
        entities cannot be reintroduced via orphan history records.
        """
        bundle = _plain(bundle)
        if not isinstance(bundle, dict) or bundle.get("format") != "teaching-learning-bundle" or bundle.get("schema_version") != 1:
            _fail("Unsupported learner bundle format or schema")
        learner = bundle.get("learner")
        if not isinstance(learner, dict):
            _fail("Bundle learner must be an object")
        learner_id = _identifier(learner.get("id", ""), "learner.id")
        groups = bundle.get("entities", {})
        if not isinstance(groups, dict) or set(groups) - (self.KINDS - {"learner"}):
            _fail("Bundle contains unknown entity groups")
        for kind, items in groups.items():
            if not isinstance(items, list) or len(items) > 100000:
                _fail(f"Bundle {kind} must be a list of at most 100000 entities")
        rows = [("learner", learner)]
        for kind in ("curriculum", "memory", "pathway", "encounter"):
            rows.extend((kind, item) for item in groups.get(kind, []))
        keys = set()
        for kind, item in rows:
            if not isinstance(item, dict):
                _fail("Every imported entity must be an object")
            required_fields = {
                "learner": {"name", "preferences", "goals", "context"},
                "memory": {"kind", "topic", "text", "evidence", "status", "concept_ids"},
                "curriculum": {"title", "goal", "nodes", "edges"},
                "pathway": {"title", "curriculum_id", "target_ids", "focus_ids", "skip_ids"},
                "encounter": {"summary", "concept_ids", "what_worked", "open_questions", "next_thread", "evidence"},
            }
            missing = required_fields[kind] - set(item)
            if missing:
                _fail(f"Imported {kind} lacks required fields: {', '.join(sorted(missing))}")
            entity_id = _identifier(item.get("id", ""))
            if (kind, entity_id) in keys:
                _fail("Duplicate entity in learner bundle")
            keys.add((kind, entity_id))
            if item.get("learner_id") != learner_id:
                _fail("Every imported entity must belong to the bundle learner")
            if not isinstance(item.get("revision"), int) or isinstance(item["revision"], bool) or item["revision"] < 1:
                _fail("Imported revision must be a positive integer")
            _datetime(item.get("created_at"), "created_at")
            _datetime(item.get("updated_at"), "updated_at")
            if not item.get("created_at") or not item.get("updated_at"):
                _fail("Imported entities require creation and update timestamps")
        with self._connection(True) as conn:
            existing = self._read(conn, "learner", learner_id, False)
            if existing and not replace:
                _fail("Learner already exists; explicit replace is required", "conflict")
            for kind, item in rows:
                collision = self._read(conn, kind, item["id"], False)
                if collision and collision["learner_id"] != learner_id:
                    _fail("Imported entity ID belongs to another learner", "conflict")
            if existing:
                conn.execute("DELETE FROM learning_revisions WHERE learner_id=?", (learner_id,))
                conn.execute("DELETE FROM learning_entities WHERE learner_id=?", (learner_id,))
            for kind, item in rows:
                parent_id = item.get("curriculum_id")
                if kind == "learner":
                    _text(item.get("name", ""), "name", required=True, maximum=500)
                    if not isinstance(item.get("preferences", {}), dict):
                        _fail("preferences must be an object")
                    _strings(item.get("goals", []), "goals")
                    _text(item.get("context", ""), "context")
                elif kind == "curriculum":
                    self._validate_curriculum(conn, item)
                elif kind == "memory":
                    # A deleted curriculum may remain as a historical scope ref.
                    validation = copy.deepcopy(item)
                    for relation in ("supersedes", "conflicts_with", "qualifies"):
                        validation[relation] = []
                    self._validate_memory(conn, learner_id, validation, allow_missing_curriculum=True)
                elif kind == "pathway":
                    curriculum = self._read(conn, "curriculum", parent_id)
                    if curriculum["learner_id"] != learner_id:
                        _fail("Pathway curriculum belongs to another learner")
                    self._route_options(curriculum, item)
                    _text(item.get("title", ""), "pathway.title", required=True)
                elif kind == "encounter":
                    _text(item.get("summary", ""), "summary", required=True)
                    for field in ("concept_ids", "what_worked", "open_questions"):
                        _strings(item.get(field, []), field)
                    _text(item.get("next_thread", ""), "next_thread")
                    evidence = item.get("evidence", {})
                    if not isinstance(evidence, dict) or not _allowed(evidence.get("basis"), {"user-stated", "observed", "inferred"}):
                        _fail("Imported encounter has invalid evidence basis")
                    if parent_id:
                        curriculum = self._read(conn, "curriculum", parent_id, False)
                        if curriculum and curriculum["learner_id"] != learner_id:
                            _fail("Encounter curriculum belongs to another learner")
                conn.execute("INSERT INTO learning_entities VALUES (?,?,?,?,?,?,?,?)",
                             (kind, item["id"], learner_id, parent_id, item["revision"],
                              json.dumps(item, ensure_ascii=False), item["created_at"], item["updated_at"]))
            for kind, item in rows:
                if kind == "memory":
                    validation = copy.deepcopy(item)
                    self._validate_memory(conn, learner_id, validation, allow_missing_curriculum=True)
            history = bundle.get("history")
            if history is None:
                history = [{"kind": kind, "id": item["id"], "revision": item["revision"],
                            "payload": item, "changed_at": item["updated_at"]} for kind, item in rows]
            if not isinstance(history, list):
                _fail("Bundle history must be a list")
            historical_keys = set()
            current = {(kind, item["id"]): item for kind, item in rows}
            for record in history:
                if not isinstance(record, dict):
                    _fail("Each history record must be an object")
                kind, entity_id, version = record.get("kind"), record.get("id"), record.get("revision")
                key = (kind, entity_id)
                payload = record.get("payload")
                if key not in current or not isinstance(payload, dict):
                    _fail("History cannot reference an absent entity")
                if not isinstance(version, int) or isinstance(version, bool) or version < 1 or version > current[key]["revision"]:
                    _fail("Invalid historical revision")
                if payload.get("id") != entity_id or payload.get("learner_id") != learner_id or payload.get("revision") != version:
                    _fail("Historical payload identity does not match its entity")
                history_key = (kind, entity_id, version)
                if history_key in historical_keys:
                    _fail("Duplicate historical revision")
                historical_keys.add(history_key)
                if version == current[key]["revision"] and payload != current[key]:
                    _fail("Latest history must equal the current entity")
                changed = record.get("changed_at")
                if changed is None:
                    _fail("History record requires changed_at")
                _datetime(changed, "changed_at")
                conn.execute("INSERT INTO learning_revisions VALUES (?,?,?,?,?,?)",
                             (kind, entity_id, learner_id, version, json.dumps(payload, ensure_ascii=False), changed))
            for kind, item in rows:
                if (kind, item["id"], item["revision"]) not in historical_keys:
                    _fail("Every entity requires its current revision in history")
        return {"imported": learner_id, "entities_imported": len(rows), "history_versions_imported": len(history),
                "replaced": bool(existing)}
    def delete_document_references(self, document_id):
        """Remove explicit paper references/excerpts, including from saved revisions.

        General learning observations are retained. Original source files,
        unrelated free text, and external exports are outside this store.
        """
        _identifier(document_id, "document_id")
        changed = 0
        def clean(value):
            if isinstance(value, list):
                return [clean(v) for v in value
                        if not (isinstance(v, dict) and v.get("type") in {"paper", "lesson"}
                                and v.get("id") == document_id)]
            if not isinstance(value, dict):
                return value
            result = {k: clean(v) for k, v in value.items()}
            ref = result.get("source_ref", "")
            if isinstance(ref, str) and (ref == document_id or ref.startswith(document_id + "#")
                    or ref.startswith("document:" + document_id)):
                result.pop("source_ref", None)
                result.pop("excerpt", None)
                result["source_status"] = "removed"
            if result.get("document_id") == document_id:
                result.pop("document_id")
                result.pop("source_excerpt", None)
                result["source_status"] = "removed"
            return result
        with self._connection(True) as conn:
            for table in ("learning_entities", "learning_revisions"):
                for row in conn.execute("SELECT rowid,payload FROM " + table).fetchall():
                    before = json.loads(row["payload"])
                    after = clean(before)
                    if after != before:
                        conn.execute("UPDATE " + table + " SET payload=? WHERE rowid=?",
                                     (json.dumps(after, ensure_ascii=False), row["rowid"]))
                        changed += 1
        return {"document_id": document_id, "records_cleaned": changed}
