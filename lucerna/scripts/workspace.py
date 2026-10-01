"""Persistent application workspace and direct host-agent authoring operations."""
from __future__ import annotations
from contextlib import contextmanager
import base64
import hashlib
import json
import math
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))
from learning import LearningStore, LearningError
from engine.papers import import_paper
from engine.storyboards import validate_storyboard, authoring_packet
from research import ResearchMixin

VERSION = "0.3.0"
PRODUCT = "Lucerna"

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

class AppError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value):
        raise AppError("invalid", "Invalid object identifier.")
    return value

class Workspace(ResearchMixin):
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "workspace.sqlite3"
        self.learning = LearningStore(self.home)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storyboards(
                document_id TEXT PRIMARY KEY, body TEXT NOT NULL,
                revision INTEGER NOT NULL, document_revision INTEGER NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS storyboard_history(
                document_id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(document_id,revision));
            CREATE TABLE IF NOT EXISTS progress(
                learner_id TEXT NOT NULL, document_id TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(learner_id,document_id));
            CREATE TABLE IF NOT EXISTS requests(
                id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)
        try:
            self.learning.get_learner("default")
        except LearningError as exc:
            if exc.code != "not_found":
                raise
            self.learning.save_learner({"id": "default", "name": "Learner", "context": "", "preferences": {}, "goals": []})
        self._research_init()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_documents(self, **options):
        return self.catalogue(**options)["items"]

    def get_document(self, document_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identifier(document_id),)).fetchone()
        if not row:
            raise AppError("not_found", "Document not found.")
        result = json.loads(row["body"])
        result.update(revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"], kind=row["kind"])
        return result

    def save_document(self, document, kind="paper", expected_revision=None):
        if not isinstance(document, dict) or not isinstance(document.get("sections"), list):
            raise AppError("invalid", "A document needs a sections array.")
        if not document.get("title") or not isinstance(document["title"], str):
            raise AppError("invalid", "A document needs a title.")
        if kind not in {"paper", "lesson", "visual-aid"}:
            raise AppError("invalid", "Document kind must be paper, lesson, or visual-aid.")
        doc = json.loads(encode(document))
        doc_id = identifier(doc.get("id") or "lesson-" + uuid.uuid4().hex[:16])
        doc["id"] = doc_id
        sections = set()
        blocks = set()
        for section in doc["sections"]:
            sid = identifier(section["id"])
            if sid in sections:
                raise AppError("invalid", "Duplicate section identifier.")
            sections.add(sid)
            for block in section.get("blocks", []):
                bid = identifier(block["id"])
                if bid in blocks:
                    raise AppError("invalid", "Duplicate block identifier.")
                blocks.add(bid)
                if not isinstance(block.get("text", ""), str):
                    raise AppError("invalid", "Block text must be a string.")
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if existing:
                old = json.loads(existing["body"])
                old_sha = old.get("source", {}).get("sha256")
                new_sha = doc.get("source", {}).get("sha256")
                if expected_revision is None and old_sha and old_sha == new_sha:
                    return self.get_document(doc_id)
                if expected_revision != existing["revision"]:
                    raise AppError("conflict", "Document changed. Read it and supply expected_revision before replacing it.")
                revision = existing["revision"] + 1
                db.execute("UPDATE documents SET title=?,kind=?,body=?,revision=?,updated_at=? WHERE id=?",
                           (doc["title"], kind, encode(doc), revision, stamp, doc_id))
            else:
                if expected_revision not in (None, 0):
                    raise AppError("conflict", "Document does not yet exist at that revision.")
                revision = 1
                db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                           (doc_id, doc["title"], kind, encode(doc), revision, stamp, stamp))
            self._index_document(db, doc, kind, revision, existing["created_at"] if existing else stamp, stamp)
        return self.get_document(doc_id)

    def import_document(self, source, filename=None, content=None, content_encoding=None):
        if content_encoding == "base64":
            try:
                content = base64.b64decode(content, validate=True)
            except Exception:
                raise AppError("invalid", "Invalid base64 document content.")
        elif content_encoding not in (None, "text"):
            raise AppError("invalid", "Unsupported content encoding.")
        document = import_paper(source, self.home / "papers", filename=filename, content=content)
        return self.save_document(document)

    def create_lesson(self, title, text="", sections=None, source=None, kind="lesson"):
        if sections is None:
            if not isinstance(text, str) or not text.strip():
                raise AppError("invalid", "Provide lesson text or authored sections.")
            sections = [{"id": "section-1", "title": title, "level": 1, "parent_id": None,
                         "blocks": [{"id": "block-1", "kind": "paragraph", "text": text, "anchor": {}}]}]
        return self.save_document({"schema_version": 1, "title": title, "source": source or {
            "kind": "authored", "original": "Authored teaching material", "retrieved_at": now()
        }, "sections": sections, "warnings": [], "extraction": {"scope": "authored"}}, kind=kind)

    def get_storyboard(self, document_id, edition_id=None, learner_id="default"):
        if edition_id and edition_id != "default":
            return self.edition_storyboard(document_id, edition_id, learner_id)
        doc = self.get_document(document_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM storyboards WHERE document_id=?", (document_id,)).fetchone()
        if not row:
            return None
        board = json.loads(row["body"])
        board.update(revision=row["revision"], document_revision=row["document_revision"],
                     stale=row["document_revision"] != doc["revision"], updated_at=row["updated_at"])
        return board

    def save_storyboard(self, document_id, storyboard, expected_revision=None, edition_id=None, learner_id="default"):
        if edition_id and edition_id != "default":
            return self.save_edition_storyboard(document_id, edition_id, storyboard, learner_id, expected_revision)
        doc = self.get_document(document_id)
        proposed = json.loads(encode(storyboard))
        if proposed.get("document_id") not in (None, document_id):
            raise AppError("invalid", "Storyboard belongs to a different document.")
        proposed["document_id"] = document_id
        proposed = validate_storyboard(proposed, doc)
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM storyboards WHERE document_id=?", (document_id,)).fetchone()
            if row and expected_revision != row["revision"]:
                raise AppError("conflict", "Storyboard changed. Read it and supply expected_revision before revising it.")
            if not row and expected_revision not in (None, 0):
                raise AppError("conflict", "Storyboard does not yet exist at that revision.")
            revision = row["revision"] + 1 if row else 1
            db.execute("INSERT INTO storyboard_history VALUES (?,?,?,?)", (document_id, revision, encode(proposed), stamp))
            db.execute("""INSERT INTO storyboards VALUES (?,?,?,?,?)
                ON CONFLICT(document_id) DO UPDATE SET body=excluded.body,revision=excluded.revision,
                document_revision=excluded.document_revision,updated_at=excluded.updated_at""",
                (document_id, encode(proposed), revision, doc["revision"], stamp))
        return self.get_storyboard(document_id)

    def packet(self, document_id, learner_id="default", question=None, section_ids=None, edition_id=None):
        doc = self.get_document(document_id)
        context = self.learning.context(learner_id, query=question or "")
        packet = authoring_packet(doc, learner_context=context, question=question, section_ids=section_ids)
        packet["current_storyboard"] = self.get_storyboard(document_id, edition_id, learner_id)
        packet["edition_id"] = edition_id or "default"
        packet["source_annotations"] = self.list_annotations(document_id, learner_id)[:50]
        packet["concept_connections"] = self.concept_graph(document_id, learner_id)
        return packet

    def get_progress(self, document_id, learner_id="default", edition_id=None):
        self.document_metadata(document_id)
        self._learner(learner_id)
        if edition_id and edition_id != "default":
            edition = self.get_edition(edition_id, learner_id)
            if edition["document_id"] != document_id:
                raise AppError("invalid", "Exploration belongs to a different source.")
            with self.connect() as db:
                row = db.execute("SELECT body FROM edition_progress WHERE edition_id=? AND learner_id=?", (edition_id, learner_id)).fetchone()
            return json.loads(row["body"]) if row else {"learner_id": learner_id, "document_id": document_id, "edition_id": edition_id}
        with self.connect() as db:
            row = db.execute("SELECT body FROM progress WHERE learner_id=? AND document_id=?",
                             (learner_id, document_id)).fetchone()
        return json.loads(row["body"]) if row else {"learner_id": learner_id, "document_id": document_id}

    def save_progress(self, document_id, payload, learner_id="default", edition_id=None):
        doc = self.get_document(document_id)
        self.learning.get_learner(learner_id)
        allowed = {"section_id", "scene_id", "step", "parameters", "scroll_anchor", "time", "scene_states"}
        value = {k: v for k, v in payload.items() if k in allowed}
        if value.get("section_id") and value["section_id"] not in {s["id"] for s in doc["sections"]}:
            raise AppError("invalid", "Selected section does not exist.")
        board = self.get_storyboard(document_id, edition_id, learner_id)
        scenes = {s["id"]: s for s in (board or {}).get("scenes", [])}
        if value.get("scene_id") and value["scene_id"] not in scenes:
            raise AppError("invalid", "Selected scene does not exist.")
        if not isinstance(value.get("parameters", {}), dict):
            raise AppError("invalid", "Scene parameters must be an object.")
        scene_states = value.get("scene_states", {})
        if not isinstance(scene_states, dict) or len(scene_states) > len(scenes):
            raise AppError("invalid", "Invalid scene continuation map.")
        for scene_id, scene_state in scene_states.items():
            if scene_id not in scenes or not isinstance(scene_state, dict):
                raise AppError("invalid", "Saved scene does not exist.")
            stamp = scene_state.get("time", 0)
            if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or not 0 <= stamp <= 1:
                raise AppError("invalid", "Scene time must be between zero and one.")
            if not isinstance(scene_state.get("parameters", {}), dict):
                raise AppError("invalid", "Scene parameters must be an object.")
        # Numeric parameter values are interpreted and bounded by the scene engine on resumption.
        value.update(learner_id=learner_id, document_id=document_id, document_revision=doc["revision"], updated_at=now())
        if edition_id and edition_id != "default":
            value["edition_id"] = edition_id
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO edition_progress VALUES (?,?,?,?,?)",
                           (edition_id, learner_id, document_id, encode(value), now()))
            return value
        with self.connect() as db:
            db.execute("""INSERT INTO progress VALUES (?,?,?,?) ON CONFLICT(learner_id,document_id)
                DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at""",
                (learner_id, document_id, encode(value), now()))
        return value

    def new_request(self, payload):
        if not isinstance(payload.get("question"), str) or not payload["question"].strip():
            raise AppError("invalid", "Describe what you want to understand.")
        if len(payload["question"]) > 30000:
            raise AppError("invalid", "Question is too long; attach its source as a document.")
        if payload.get("document_id"):
            self.get_document(payload["document_id"])
        data = {k: v for k, v in payload.items() if k in {
            "kind", "question", "document_id", "section_id", "scene_id", "parameters", "context", "learner_id", "edition_id"}}
        data.setdefault("learner_id", "default")
        self.learning.get_learner(data["learner_id"])
        if data.get("edition_id") and data["edition_id"] != "default":
            edition = self.get_edition(data["edition_id"], data["learner_id"])
            if edition["document_id"] != data.get("document_id"):
                raise AppError("invalid", "Question edition belongs to a different source.")
        data.update(id="request-" + uuid.uuid4().hex[:16], status="pending", created_at=now(), updated_at=now())
        with self.connect() as db:
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)",
                       (data["id"], encode(data), "pending", None, data["created_at"], data["updated_at"]))
        return data

    def list_requests(self, status=None, learner_id=None, document_id=None, limit=200):
        if status not in (None, "pending", "in_progress", "complete", "failed", "cancelled"):
            raise AppError("invalid", "Invalid request status.")
        where, args = [], []
        if status:
            where.append("r.status=?"); args.append(status)
        if learner_id:
            self._learner(learner_id)
            where.append("COALESCE(json_extract(r.body,'$.learner_id'),'default')=?"); args.append(learner_id)
        if document_id:
            where.append("json_extract(r.body,'$.document_id')=?"); args.append(document_id)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as db:
            rows = db.execute("SELECT r.*,c.worker_id,c.expires_at,c.attempts FROM requests r LEFT JOIN request_claims c ON c.request_id=r.id" +
                              clause + " ORDER BY r.created_at DESC LIMIT ?", args + [min(max(int(limit),1),1000)]).fetchall()
        items = []
        for row in rows:
            item = self.request_value(row)
            if row["worker_id"]:
                item["claim"] = {"worker_id":row["worker_id"],"expires_at":row["expires_at"],"attempt":row["attempts"]}
            items.append(item)
        return items

    @staticmethod
    def request_value(row):
        return dict(json.loads(row["body"]), status=row["status"], updated_at=row["updated_at"],
                    result=json.loads(row["result"]) if row["result"] else None)

    def update_request(self, request_id, status, result=None):
        if status not in ("in_progress", "complete", "failed", "cancelled"):
            raise AppError("invalid", "Invalid request transition.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (identifier(request_id),)).fetchone()
            if not row:
                raise AppError("not_found", "Request not found.")
            if db.execute("SELECT 1 FROM request_claims WHERE request_id=?", (request_id,)).fetchone():
                raise AppError("conflict", "This request has a worker lease; resolve it with its claim token.")
            transitions = {"pending": {"in_progress", "complete", "failed", "cancelled"},
                           "in_progress": {"complete", "failed", "cancelled"},
                           "complete": set(), "failed": set(), "cancelled": set()}
            if status not in transitions[row["status"]]:
                raise AppError("conflict", "This request has already changed state.")
            if status == "complete" and not result:
                raise AppError("invalid", "A completed request needs the actual answer or artifact result.")
            if result and result.get("document_id"):
                self.get_document(result["document_id"])
            db.execute("UPDATE requests SET status=?,result=?,updated_at=? WHERE id=?",
                       (status, encode(result) if result else None, now(), request_id))
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        return self.request_value(row)

    def request_packet(self, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (identifier(request_id),)).fetchone()
        item = self.request_value(row) if row else None
        if item is None:
            raise AppError("not_found", "Request not found.")
        if item.get("document_id"):
            packet = self.packet(item["document_id"], item.get("learner_id", "default"),
                                 item["question"], [item["section_id"]] if item.get("section_id") else None, item.get("edition_id"))
        else:
            packet = {"learner_context": self.learning.context(item.get("learner_id", "default"),
                      query=item["question"])}
        packet["request"] = item
        return packet

    def forget_learner(self, learner_id):
        self.learning.get_learner(learner_id)
        self.forget_research_learner(learner_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM progress WHERE learner_id=?", (learner_id,))
            rows = db.execute("SELECT id,body FROM requests").fetchall()
            for row in rows:
                if json.loads(row["body"]).get("learner_id", "default") == learner_id:
                    db.execute("DELETE FROM request_claims WHERE request_id=?", (row["id"],))
                    db.execute("DELETE FROM requests WHERE id=?", (row["id"],))
        return self.learning.delete_learner(learner_id)

    def delete_document(self, document_id):
        return self.delete_research_document(document_id)

    def export_html(self, document_id, edition_id=None, learner_id="default"):
        document = self.get_document(document_id)
        board = self.get_storyboard(document_id, edition_id, learner_id)
        if board and board.get("stale"):
            raise AppError("conflict", "The explanation predates this source revision. Update it before exporting.")
        web = ROOT / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        snapshot = encode({"document": document, "storyboard": board, "progress": self.get_progress(document_id, learner_id, edition_id)})
        snapshot = snapshot.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        html = html.replace("</head>", "<script>window.TEACHING_SNAPSHOT=" + snapshot + ";</script></head>")
        # Inline only packaged local assets actually referenced by the authored page.
        def inline_script(match):
            name = match.group(1)
            path = (web / name.lstrip("/")).resolve()
            if not path.is_relative_to(web.resolve()) or not path.is_file():
                raise AppError("invalid", "Missing packaged script: " + name)
            code = path.read_text(encoding="utf-8")
            if re.search(r"^import\s", code, re.M):
                engine = (web / "scene-engine.js").read_text(encoding="utf-8")
                engine = re.sub(r"\bexport\s+(?=class|function|const|let)", "", engine)
                code = re.sub(r"^import\s+[^;]+;\s*", "", code, flags=re.M)
                code = "const ScenePlayer = (() => {\n" + engine + "\nreturn ScenePlayer;\n})();\n" + code
            return "<script>" + code.replace("</script", "<\\/script") + "</script>"
        def inline_style(match):
            name = match.group(1)
            path = (web / name.lstrip("/")).resolve()
            if not path.is_relative_to(web.resolve()) or not path.is_file():
                raise AppError("invalid", "Missing packaged stylesheet: " + name)
            css = path.read_text(encoding="utf-8")
            def inline_asset(asset_match):
                name = asset_match.group(1).strip(" \"'")
                if name.startswith("data:"):
                    return asset_match.group(0)
                asset = (path.parent / name).resolve()
                formats = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".png": "image/png"}
                if not asset.is_relative_to(web.resolve()) or not asset.is_file() or asset.suffix not in formats:
                    raise AppError("invalid", "Missing or unsupported packaged CSS asset: " + name)
                return "url(data:" + formats[asset.suffix] + ";base64," + base64.b64encode(asset.read_bytes()).decode("ascii") + ")"
            css = re.sub(r"url\(([^)]+)\)", inline_asset, css)
            return "<style>" + css.replace("</style", "<\\/style") + "</style>"
        html = re.sub(r'<script[^>]*src=["\']([^"\']+)["\'][^>]*>\s*</script>', inline_script, html)
        html = re.sub(r'<link[^>]*href=["\']([^"\']+\.css)["\'][^>]*>', inline_style, html)
        return html
