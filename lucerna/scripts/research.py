"""Local research index and learner-owned working knowledge.

The document table remains canonical. The small catalogue and FTS table are
rebuildable views; learner annotations and editions are durable authored state.
"""
from __future__ import annotations
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _error(code, message):
    from workspace import AppError
    return AppError(code, message)

def _text(value, label, maximum=30000, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise _error("invalid", f"{label} must be text up to {maximum} characters.")
    return value.strip()

def _id(value):
    from workspace import identifier
    return identifier(value)

class ResearchMixin:
    def _research_init(self):
        started = time.monotonic()
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS library_metadata(
              document_id TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,summary TEXT NOT NULL,
              authors TEXT NOT NULL,arxiv_id TEXT,version INTEGER,family_id TEXT NOT NULL,
              source_format TEXT,source_sha TEXT,original TEXT,canonical_url TEXT,
              section_count INTEGER NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS library_family ON library_metadata(family_id);
            CREATE TABLE IF NOT EXISTS research_collections(
              id TEXT PRIMARY KEY,learner_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collection_documents(
              collection_id TEXT NOT NULL,document_id TEXT NOT NULL,PRIMARY KEY(collection_id,document_id));
            CREATE TABLE IF NOT EXISTS annotations(
              id TEXT PRIMARY KEY,document_id TEXT NOT NULL,learner_id TEXT NOT NULL,
              body TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS annotation_owner ON annotations(learner_id,document_id);
            CREATE TABLE IF NOT EXISTS concept_connections(
              id TEXT PRIMARY KEY,document_id TEXT NOT NULL,learner_id TEXT NOT NULL,
              body TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exploration_editions(
              id TEXT PRIMARY KEY,document_id TEXT NOT NULL,learner_id TEXT NOT NULL,title TEXT NOT NULL,
              body TEXT,revision INTEGER NOT NULL,document_revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS edition_owner ON exploration_editions(learner_id,document_id);
            CREATE TABLE IF NOT EXISTS edition_history(
              edition_id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,
              PRIMARY KEY(edition_id,revision));
            CREATE TABLE IF NOT EXISTS edition_progress(
              edition_id TEXT NOT NULL,learner_id TEXT NOT NULL,document_id TEXT NOT NULL,body TEXT NOT NULL,
              updated_at TEXT NOT NULL,PRIMARY KEY(edition_id,learner_id));
            CREATE TABLE IF NOT EXISTS request_claims(
              request_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL,token TEXT NOT NULL,expires_at REAL NOT NULL,attempts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS research_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            mode = db.execute("SELECT value FROM research_settings WHERE key='search_mode'").fetchone()
            if not mode:
                try:
                    db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS source_search USING fts5(
                      document_id UNINDEXED,section_id UNINDEXED,block_id UNINDEXED,
                      section_title,text,anchor UNINDEXED,tokenize='unicode61')""")
                    self.search_mode = "fts5"
                except sqlite3.OperationalError:
                    db.execute("""CREATE TABLE IF NOT EXISTS source_search(
                      document_id TEXT,section_id TEXT,block_id TEXT,section_title TEXT,text TEXT,anchor TEXT)""")
                    db.execute("CREATE INDEX IF NOT EXISTS source_search_doc ON source_search(document_id)")
                    self.search_mode = "text"
                db.execute("INSERT INTO research_settings VALUES('search_mode',?)", (self.search_mode,))
            else:
                self.search_mode = mode["value"]
            # Read one body at a time. This runs only for missing/stale view rows.
            pending = [r["id"] for r in db.execute("""SELECT d.id FROM documents d
                LEFT JOIN library_metadata m ON m.document_id=d.id WHERE m.document_id IS NULL OR m.revision!=d.revision""")]
            for document_id in pending:
                row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
                self._index_document(db, json.loads(row["body"]), row["kind"], row["revision"], row["created_at"], row["updated_at"])
        self.migration = {"indexed_documents": len(pending), "search_mode": self.search_mode,
                          "elapsed_seconds": round(time.monotonic()-started, 3)}

    def _index_document(self, db, document, kind, revision, created_at, updated_at):
        source = document.get("source", {})
        document_id = document["id"]
        arxiv_id = source.get("arxiv_id")
        # Family grouping uses explicit source identifiers, never title similarity.
        family = "arxiv:" + arxiv_id if isinstance(arxiv_id, str) and arxiv_id else (
            "sha256:" + source["sha256"] if source.get("sha256") else "document:" + document_id)
        authors = source.get("authors") or document.get("authors") or []
        if not isinstance(authors, list):
            authors = [str(authors)]
        authors = [str(a) for a in authors]
        abstract = source.get("abstract") or document.get("summary") or ""
        db.execute("INSERT OR REPLACE INTO library_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (document_id, document["title"], kind, str(abstract)[:4000], _json(authors), arxiv_id,
             source.get("version"), family, source.get("format") or source.get("kind"), source.get("sha256"),
             source.get("original", ""), source.get("canonical_url", ""), len(document["sections"]),
             revision, created_at, updated_at))
        db.execute("DELETE FROM source_search WHERE document_id=?", (document_id,))
        for section in document["sections"]:
            rows = [(document_id, section["id"], block["id"], section["title"], block.get("text", ""),
                     _json(block.get("anchor", section.get("anchor", {})))) for block in section.get("blocks", []) if block.get("text")]
            db.executemany("INSERT INTO source_search(document_id,section_id,block_id,section_title,text,anchor) VALUES(?,?,?,?,?,?)", rows)

    def _learner(self, learner_id):
        self.learning.get_learner(_id(learner_id))
        return learner_id

    def catalogue(self, query="", kind=None, collection_id=None, family_id=None, learner_id="default", limit=500, offset=0):
        self._learner(learner_id)
        limit, offset = min(max(int(limit), 1), 1000), max(int(offset), 0)
        where, args = [], []
        if query:
            term = "%" + str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(m.title LIKE ? ESCAPE '\\' OR m.summary LIKE ? ESCAPE '\\' OR m.authors LIKE ? ESCAPE '\\')")
            args.extend([term]*3)
        if kind:
            where.append("m.kind=?"); args.append(kind)
        if family_id:
            where.append("m.family_id=?"); args.append(family_id)
        if collection_id:
            self.get_collection(collection_id, learner_id)
            where.append("m.document_id IN (SELECT document_id FROM collection_documents WHERE collection_id=?)")
            args.append(collection_id)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM library_metadata m"+clause, args).fetchone()[0]
            rows = db.execute("""SELECT m.*,s.document_revision AS storyboard_source,
              (SELECT count(*) FROM exploration_editions e WHERE e.document_id=m.document_id AND e.learner_id=?) AS edition_count,
              (SELECT count(*) FROM library_metadata f WHERE f.family_id=m.family_id) AS family_count
              FROM library_metadata m LEFT JOIN storyboards s ON s.document_id=m.document_id""" + clause +
              " ORDER BY m.updated_at DESC,m.document_id LIMIT ? OFFSET ?", [learner_id]+args+[limit,offset]).fetchall()
        items = []
        for row in rows:
            item = dict(row); item["id"] = item.pop("document_id")
            item["authors"] = json.loads(item["authors"])
            item["has_storyboard"] = item.pop("storyboard_source") == item["revision"]
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "collections": self.list_collections(learner_id), "search": {"available": True, "mode": self.search_mode}}

    def search_sources(self, query, document_id=None, collection_id=None, kind=None, learner_id="default", limit=30, offset=0):
        self._learner(learner_id)
        query = _text(query, "Search query", 1000)
        limit, offset = min(max(int(limit), 1), 100), max(int(offset), 0)
        filters, args = [], []
        if document_id:
            self.document_metadata(document_id)
            filters.append("f.document_id=?"); args.append(document_id)
        if collection_id:
            self.get_collection(collection_id, learner_id)
            filters.append("f.document_id IN (SELECT document_id FROM collection_documents WHERE collection_id=?)"); args.append(collection_id)
        if kind:
            filters.append("m.kind=?"); args.append(kind)
        extra = " AND " + " AND ".join(filters) if filters else ""
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            raise _error("invalid", "Use at least one word or number in the search.")
        with self.connect() as db:
            if self.search_mode == "fts5":
                match = " AND ".join('"' + token.replace('"','""') + '"' for token in terms[:24])
                base = " FROM source_search f JOIN library_metadata m ON m.document_id=f.document_id WHERE source_search MATCH ?" + extra
                total = db.execute("SELECT count(*)"+base, [match]+args).fetchone()[0]
                rows = db.execute("""SELECT f.document_id,m.title,f.section_id,f.section_title,f.block_id,f.anchor,
                    snippet(source_search,4,'','',' … ',40) AS snippet,bm25(source_search) AS score""" + base +
                    " ORDER BY score LIMIT ? OFFSET ?", [match]+args+[limit,offset]).fetchall()
            else:
                conditions = " AND ".join("(f.text LIKE ? OR f.section_title LIKE ?)" for _ in terms[:24])
                match_args = [value for token in terms[:24] for value in ["%"+token+"%"]*2]
                base = " FROM source_search f JOIN library_metadata m ON m.document_id=f.document_id WHERE " + conditions + extra
                total = db.execute("SELECT count(*)"+base, match_args+args).fetchone()[0]
                rows = db.execute("""SELECT f.document_id,m.title,f.section_id,f.section_title,f.block_id,f.anchor,
                    substr(f.text,1,700) AS snippet,0 AS score""" + base + " LIMIT ? OFFSET ?", match_args+args+[limit,offset]).fetchall()
            items = []
            for row in rows:
                item = dict(row); item["anchor"] = json.loads(item["anchor"]); item["match"] = "body"
                items.append(item)
        if not items and offset == 0:
            metadata = self.catalogue(query, kind, collection_id, learner_id=learner_id, limit=limit)["items"]
            if document_id:
                metadata = [item for item in metadata if item["id"] == document_id]
            items = [{"document_id": item["id"], "title": item["title"], "section_id": None, "block_id": None,
                      "section_title": "", "anchor": {}, "snippet": item["summary"], "score": 0, "match": "metadata"} for item in metadata]
            total = len(items)
        return {"query": query, "mode": self.search_mode, "total": total, "items": items, "limit": limit, "offset": offset}

    def document_metadata(self, document_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM library_metadata WHERE document_id=?", (_id(document_id),)).fetchone()
        if not row:
            raise _error("not_found", "Document not found.")
        value = dict(row); value["id"] = value.pop("document_id"); value["authors"] = json.loads(value["authors"])
        return value

    def document_section(self, document_id, section_id, radius=0):
        # The canonical representation is one document JSON. Only the bounded
        # result is transferred to a browser/model; library/search never load it.
        document = self.get_document(document_id)
        sections = document["sections"]
        current = next((i for i, section in enumerate(sections) if section["id"] == section_id), None)
        if current is None:
            raise _error("not_found", "Section not found.")
        radius = min(max(int(radius), 0), 3)
        return {"document": self.document_metadata(document_id), "section_id": section_id,
                "sections": sections[max(0,current-radius):current+radius+1]}

    def source_file(self, document_id):
        doc = self.get_document(document_id)
        relative = doc.get("source", {}).get("cache_path")
        if not relative:
            raise _error("not_found", "This authored lesson has no original source file.")
        base = (self.home/"papers").resolve()
        file = (base/relative).resolve()
        expected = base/_id(document_id)
        if not file.is_relative_to(expected) or not file.is_file() or file.is_symlink():
            raise _error("not_found", "The retained source file is unavailable.")
        return file

    def list_collections(self, learner_id="default"):
        self._learner(learner_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM research_collections WHERE learner_id=? ORDER BY title", (learner_id,)).fetchall()
            result = []
            for row in rows:
                value = dict(row)
                value["document_ids"] = [r[0] for r in db.execute("SELECT document_id FROM collection_documents WHERE collection_id=? ORDER BY document_id", (row["id"],))]
                value["count"] = len(value["document_ids"]); result.append(value)
        return result

    def get_collection(self, collection_id, learner_id="default"):
        self._learner(learner_id)
        found = next((item for item in self.list_collections(learner_id) if item["id"] == collection_id), None)
        if found is None:
            raise _error("not_found", "Collection not found for this learner.")
        return found

    def save_collection(self, payload, learner_id="default", collection_id=None):
        learner_id = self._learner(payload.get("learner_id", learner_id))
        previous = self.get_collection(collection_id, learner_id) if collection_id else {}
        title = _text(payload.get("title", previous.get("title")), "Collection title", 200)
        description = _text(payload.get("description", previous.get("description", "")), "Description", 5000, True)
        documents = payload.get("document_ids", previous.get("document_ids", []))
        if not isinstance(documents, list) or len(documents)>10000:
            raise _error("invalid", "document_ids must be a list of up to 10000 identifiers.")
        documents = list(dict.fromkeys(_id(i) for i in documents))
        for document_id in documents:
            self.document_metadata(document_id)
        collection_id = collection_id or "col-" + uuid.uuid4().hex[:16]
        stamp = _now()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO research_collections VALUES (?,?,?,?,?,?)",
                       (collection_id, learner_id, title, description, previous.get("created_at",stamp),stamp))
            db.execute("DELETE FROM collection_documents WHERE collection_id=?", (collection_id,))
            db.executemany("INSERT INTO collection_documents VALUES(?,?)", [(collection_id,i) for i in documents])
        return self.get_collection(collection_id, learner_id)

    def delete_collection(self, collection_id, learner_id="default"):
        self.get_collection(collection_id, learner_id)
        with self.connect() as db:
            db.execute("DELETE FROM collection_documents WHERE collection_id=?", (collection_id,))
            db.execute("DELETE FROM research_collections WHERE id=? AND learner_id=?", (collection_id,learner_id))
        return {"deleted": collection_id}


    def _anchors(self, document_id, anchors):
        if not isinstance(anchors, list) or len(anchors)>50:
            raise _error("invalid", "Source anchors must be a list of up to 50 blocks.")
        document = self.get_document(document_id)
        blocks = {b["id"]: (s,b) for s in document["sections"] for b in s.get("blocks", [])}
        result = []
        for value in anchors:
            if not isinstance(value, dict) or value.get("block_id") not in blocks:
                raise _error("invalid", "A source anchor refers to an absent block.")
            section, block = blocks[value["block_id"]]
            quote = value.get("quote", "")
            if quote and (not isinstance(quote,str) or quote not in block.get("text","")):
                raise _error("invalid", "An anchored quotation must occur in its source block.")
            result.append({"block_id": block["id"], "section_id": section["id"], "anchor": block.get("anchor",{}),
                           "quote": quote, "document_revision": document["revision"],
                           "source_sha": document.get("source",{}).get("sha256")})
        return result

    def _provenance(self, value):
        if not isinstance(value,dict) or value.get("basis") not in {"user-stated","agent-authored"}:
            raise _error("invalid", "Provenance must state user-stated or agent-authored basis.")
        return {"basis": value["basis"], "author": _text(value.get("author","User" if value["basis"]=="user-stated" else "Teaching agent"),"Author",200),
                "created_at": _now()}

    def list_annotations(self, document_id, learner_id="default"):
        self._learner(learner_id); meta = self.document_metadata(document_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM annotations WHERE document_id=? AND learner_id=? ORDER BY updated_at DESC",
                              (document_id,learner_id)).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["body"])
            value.update(id=row["id"], revision=row["revision"],created_at=row["created_at"],updated_at=row["updated_at"])
            value["stale"] = any(a.get("document_revision")!=meta["revision"] for a in value.get("anchors",[]))
            result.append(value)
        return result

    def save_annotation(self, document_id, payload, learner_id="default", annotation_id=None, expected_revision=None):
        learner_id = self._learner(payload.get("learner_id",learner_id))
        self.document_metadata(document_id)
        previous = next((a for a in self.list_annotations(document_id,learner_id) if a["id"]==annotation_id), None) if annotation_id else None
        if annotation_id and previous is None:
            raise _error("not_found","Annotation not found for this learner.")
        if previous and expected_revision != previous["revision"]:
            raise _error("conflict","Annotation changed. Supply its current expected_revision.")
        value = {"document_id":document_id,"learner_id":learner_id,
                 "text":_text(payload.get("text", previous["text"] if previous else None),"Annotation"),
                 "kind":payload.get("kind",previous["kind"] if previous else "note")}
        if value["kind"] not in {"note","question","insight"}:
            raise _error("invalid","Annotation kind must be note, question, or insight.")
        value["anchors"] = self._anchors(document_id,payload.get("anchors",previous.get("anchors",[]) if previous else []))
        tags = payload.get("tags",previous.get("tags",[]) if previous else [])
        if not isinstance(tags,list) or len(tags)>30:
            raise _error("invalid","Use up to 30 short tags.")
        value["tags"] = list(dict.fromkeys(_text(tag,"Tag",80) for tag in tags))
        value["provenance"] = self._provenance(payload["provenance"]) if "provenance" in payload else (
            previous["provenance"] if previous else self._provenance({"basis":"user-stated"}))
        annotation_id = annotation_id or "note-"+uuid.uuid4().hex[:16]
        revision = previous["revision"]+1 if previous else 1; stamp=_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM annotations WHERE id=?",(annotation_id,)).fetchone()
            if current and current["revision"] != expected_revision:
                raise _error("conflict","Annotation changed while saving.")
            db.execute("INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?,?,?)",
                       (annotation_id,document_id,learner_id,_json(value),revision,previous["created_at"] if previous else stamp,stamp))
        return next(a for a in self.list_annotations(document_id,learner_id) if a["id"]==annotation_id)

    def delete_annotation(self, annotation_id, learner_id="default"):
        self._learner(learner_id)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM annotations WHERE id=? AND learner_id=?",(_id(annotation_id),learner_id)).fetchone():
                raise _error("not_found","Annotation not found for this learner.")
            db.execute("DELETE FROM annotations WHERE id=? AND learner_id=?",(annotation_id,learner_id))
        return {"deleted":annotation_id}

    def save_connection(self, document_id, payload, learner_id="default", connection_id=None, expected_revision=None):
        learner_id=self._learner(payload.get("learner_id",learner_id)); self.document_metadata(document_id)
        previous=None
        if connection_id:
            with self.connect() as db:
                row=db.execute("SELECT * FROM concept_connections WHERE id=? AND learner_id=?",(_id(connection_id),learner_id)).fetchone()
            if not row:
                raise _error("not_found","Connection not found for this learner.")
            previous=json.loads(row["body"])
            if row["revision"]!=expected_revision:
                raise _error("conflict","Connection changed. Supply its current expected_revision.")
        value={"document_id":document_id,"learner_id":learner_id}
        for key in ("from","to"):
            endpoint=payload.get(key,previous.get(key) if previous else None)
            if not isinstance(endpoint,dict):
                raise _error("invalid","Connections need from and to concepts.")
            linked=endpoint.get("document_id") or document_id
            self.document_metadata(linked)
            anchor_inputs=[{"block_id":endpoint["block_id"],"quote":endpoint.get("quote","")}] if endpoint.get("block_id") else endpoint.get("anchors",[])
            anchors=self._anchors(linked,anchor_inputs)
            label=_text(endpoint.get("label"),"Concept label",200)
            # Explicit labels in the same source identify a node; different
            # source concepts are joined only by an authored edge.
            concept_id="concept-"+hashlib.sha256((linked+":"+label.casefold()).encode()).hexdigest()[:20]
            value[key]={"id":concept_id,"label":label,"document_id":linked,"anchors":anchors}
        value["relation"]=payload.get("relation",previous.get("relation") if previous else "related")
        if value["relation"] not in {"requires","explains","contrasts","extends","related"}:
            raise _error("invalid","Unsupported concept relation.")
        value["explanation"]=_text(payload.get("explanation",previous.get("explanation","") if previous else ""),"Connection explanation",10000,True)
        value["provenance"]=self._provenance(payload["provenance"]) if "provenance" in payload else (
            previous["provenance"] if previous else self._provenance({"basis":"user-stated"}))
        connection_id=connection_id or "link-"+uuid.uuid4().hex[:16]; stamp=_now()
        revision=row["revision"]+1 if previous else 1
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current=db.execute("SELECT revision FROM concept_connections WHERE id=?",(connection_id,)).fetchone()
            if current and current["revision"]!=expected_revision:
                raise _error("conflict","Connection changed while saving.")
            db.execute("INSERT OR REPLACE INTO concept_connections VALUES(?,?,?,?,?,?,?)",
                       (connection_id,document_id,learner_id,_json(value),revision,row["created_at"] if previous else stamp,stamp))
        value.update(id=connection_id,revision=revision,created_at=row["created_at"] if previous else stamp,updated_at=stamp)
        return value

    def concept_graph(self, document_id=None, learner_id="default", query=""):
        self._learner(learner_id)
        if document_id:
            self.document_metadata(document_id)
        with self.connect() as db:
            rows=db.execute("SELECT * FROM concept_connections WHERE learner_id=? ORDER BY updated_at DESC",(learner_id,)).fetchall()
        nodes,edges={},[]
        for row in rows:
            value=json.loads(row["body"])
            if document_id and document_id not in {value["document_id"],value["from"]["document_id"],value["to"]["document_id"]}:
                continue
            if query and query.casefold() not in _json(value).casefold():
                continue
            for key in ("from","to"):
                node=dict(value[key]); node["kind"]="concept"; nodes[node["id"]]=node
            edges.append({"id":row["id"],"from":value["from"]["id"],"to":value["to"]["id"],
                "relation":value["relation"],"explanation":value["explanation"],"provenance":value["provenance"],
                "anchors":[dict(anchor,document_id=value[endpoint]["document_id"]) for endpoint in ("from","to") for anchor in value[endpoint]["anchors"]],
                "document_id":value["document_id"],"revision":row["revision"]})
        return {"nodes":list(nodes.values()),"edges":edges,"learner_id":learner_id,"basis":"explicit-authored-connections"}

    def delete_connection(self, connection_id, learner_id="default"):
        self._learner(learner_id)
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM concept_connections WHERE id=? AND learner_id=?",(_id(connection_id),learner_id)).fetchone():
                raise _error("not_found","Connection not found for this learner.")
            db.execute("DELETE FROM concept_connections WHERE id=? AND learner_id=?",(connection_id,learner_id))
        return {"deleted":connection_id}

    def list_editions(self, document_id, learner_id="default"):
        self._learner(learner_id); meta=self.document_metadata(document_id)
        with self.connect() as db:
            legacy=db.execute("SELECT revision,document_revision,updated_at FROM storyboards WHERE document_id=?",(document_id,)).fetchone()
            rows=db.execute("""SELECT id,title,document_id,learner_id,revision,document_revision,
              body IS NOT NULL AS has_storyboard,created_at,updated_at FROM exploration_editions
              WHERE document_id=? AND learner_id=? ORDER BY updated_at DESC""",(document_id,learner_id)).fetchall()
        shared={"id":"default","title":"Original exploration","document_id":document_id,"learner_id":None,
                "revision":legacy["revision"] if legacy else 0,"document_revision":legacy["document_revision"] if legacy else meta["revision"],
                "has_storyboard":bool(legacy),"is_default":True,"stale":bool(legacy and legacy["document_revision"]!=meta["revision"])}
        return [shared]+[dict(row,is_default=False,has_storyboard=bool(row["has_storyboard"]),stale=row["document_revision"]!=meta["revision"]) for row in rows]

    def get_edition(self, edition_id, learner_id="default"):
        self._learner(learner_id)
        with self.connect() as db:
            row=db.execute("SELECT * FROM exploration_editions WHERE id=? AND learner_id=?",(_id(edition_id),learner_id)).fetchone()
        if not row:
            raise _error("not_found","Exploration edition not found for this learner.")
        value=dict(row); value["storyboard"]=json.loads(value.pop("body")) if row["body"] else None
        value["has_storyboard"]=value["storyboard"] is not None
        value["stale"]=value["document_revision"]!=self.document_metadata(value["document_id"])["revision"]
        value["is_default"]=False
        return value

    def create_edition(self, document_id, title, learner_id="default", clone_default=False):
        self._learner(learner_id); meta=self.document_metadata(document_id)
        title=_text(title,"Exploration title",200)
        if not isinstance(clone_default,bool):
            raise _error("invalid","clone_default must be true or false.")
        board=self.get_storyboard(document_id) if clone_default else None
        if board and board.get("stale"):
            raise _error("conflict","Rebuild the outdated original before cloning it.")
        if board:
            board={k:v for k,v in board.items() if k not in {"revision","document_revision","updated_at","stale","edition_id","learner_id"}}
        edition_id="edition-"+uuid.uuid4().hex[:16]; stamp=_now()
        with self.connect() as db:
            db.execute("INSERT INTO exploration_editions VALUES(?,?,?,?,?,?,?,?,?)",
                       (edition_id,document_id,learner_id,title,_json(board) if board else None,1,meta["revision"],stamp,stamp))
            if board:
                db.execute("INSERT INTO edition_history VALUES(?,?,?,?)",(edition_id,1,_json(board),stamp))
        return self.get_edition(edition_id,learner_id)

    def rename_edition(self, edition_id, title, learner_id="default", expected_revision=None):
        previous=self.get_edition(edition_id,learner_id)
        if expected_revision!=previous["revision"]:
            raise _error("conflict","Exploration changed. Supply its current expected_revision.")
        with self.connect() as db:
            cursor=db.execute("UPDATE exploration_editions SET title=?,revision=revision+1,updated_at=? WHERE id=? AND learner_id=? AND revision=?",
                (_text(title,"Exploration title",200),_now(),edition_id,learner_id,expected_revision))
            if cursor.rowcount!=1:
                raise _error("conflict","Exploration changed while saving.")
        return self.get_edition(edition_id,learner_id)

    def delete_edition(self, edition_id, learner_id="default"):
        self.get_edition(edition_id,learner_id)
        with self.connect() as db:
            db.execute("DELETE FROM edition_progress WHERE edition_id=?",(edition_id,))
            db.execute("DELETE FROM edition_history WHERE edition_id=?",(edition_id,))
            db.execute("DELETE FROM exploration_editions WHERE id=? AND learner_id=?",(edition_id,learner_id))
        return {"deleted":edition_id}

    def edition_storyboard(self, document_id, edition_id, learner_id="default"):
        edition=self.get_edition(edition_id,learner_id)
        if edition["document_id"]!=document_id:
            raise _error("invalid","Exploration belongs to a different source.")
        board=edition["storyboard"]
        if board is not None:
            board.update(revision=edition["revision"],document_revision=edition["document_revision"],
                         updated_at=edition["updated_at"],stale=edition["stale"],edition_id=edition_id,
                         learner_id=learner_id,edition_title=edition["title"])
        return board

    def save_edition_storyboard(self, document_id, edition_id, storyboard, learner_id="default", expected_revision=None):
        from engine.storyboards import validate_storyboard
        edition=self.get_edition(edition_id,learner_id)
        if edition["document_id"]!=document_id:
            raise _error("invalid","Exploration belongs to a different source.")
        if expected_revision!=edition["revision"]:
            raise _error("conflict","Exploration changed. Supply its current expected_revision.")
        document=self.get_document(document_id)
        proposed=json.loads(_json(storyboard))
        if proposed.get("document_id") not in (None,document_id):
            raise _error("invalid","Storyboard belongs to a different source.")
        proposed["document_id"]=document_id
        proposed=validate_storyboard(proposed,document)
        stamp=_now()
        with self.connect() as db:
            cursor=db.execute("""UPDATE exploration_editions SET body=?,revision=revision+1,document_revision=?,updated_at=?
              WHERE id=? AND learner_id=? AND revision=?""",(_json(proposed),document["revision"],stamp,edition_id,learner_id,expected_revision))
            if cursor.rowcount!=1:
                raise _error("conflict","Exploration changed while publishing.")
            db.execute("INSERT INTO edition_history VALUES(?,?,?,?)",(edition_id,expected_revision+1,_json(proposed),stamp))
        return self.edition_storyboard(document_id,edition_id,learner_id)


    def claim_request(self, worker_id, learner_id="default", request_id=None, lease_seconds=600):
        self._learner(learner_id); worker_id=_text(worker_id,"Worker identifier",200)
        lease_seconds=min(max(int(lease_seconds),30),3600)
        stamp=time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            params=[learner_id,stamp]
            selection="""SELECT r.*,c.attempts FROM requests r LEFT JOIN request_claims c ON c.request_id=r.id
              WHERE COALESCE(json_extract(r.body,'$.learner_id'),'default')=?
              AND (r.status='pending' OR (r.status='in_progress' AND c.expires_at<?))"""
            if request_id:
                selection+=" AND r.id=?";params.append(_id(request_id))
            row=db.execute(selection+" ORDER BY r.created_at LIMIT 1",params).fetchone()
            if row is None:
                return {"claimed":False,"request":None}
            token=secrets.token_urlsafe(32);expires=stamp+lease_seconds
            attempts=(row["attempts"] or 0)+1
            db.execute("INSERT OR REPLACE INTO request_claims VALUES(?,?,?,?,?)",(row["id"],worker_id,token,expires,attempts))
            db.execute("UPDATE requests SET status='in_progress',updated_at=? WHERE id=?",(_now(),row["id"]))
            updated=db.execute("SELECT * FROM requests WHERE id=?",(row["id"],)).fetchone()
        return {"claimed":True,"request":self.request_value(updated),"claim_token":token,"worker_id":worker_id,
                "expires_at":expires,"attempt":attempts}

    def renew_request_claim(self, request_id, worker_id, claim_token, lease_seconds=600):
        expiry=time.time()+min(max(int(lease_seconds),30),3600)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM request_claims WHERE request_id=?",(_id(request_id),)).fetchone()
            if not row or row["worker_id"]!=worker_id or not secrets.compare_digest(row["token"],str(claim_token)) or row["expires_at"]<time.time():
                raise _error("conflict","The request lease is no longer owned by this worker.")
            status=db.execute("SELECT status FROM requests WHERE id=?",(request_id,)).fetchone()
            if not status or status["status"]!="in_progress":
                raise _error("conflict","The request is no longer running.")
            db.execute("UPDATE request_claims SET expires_at=? WHERE request_id=?",(expiry,request_id))
        return {"request_id":request_id,"expires_at":expiry}

    def resolve_claimed_request(self, request_id, worker_id, claim_token, result=None, status="complete"):
        if status not in {"complete","failed","cancelled"}:
            raise _error("invalid","Resolve a claim as complete, failed, or cancelled.")
        if not isinstance(result,dict) or not result:
            raise _error("invalid","Supply an actual answer/artifact, or the failure reason.")
        if status=="complete" and not any(result.get(k) for k in ("answer","document_id","edition_id","artifact")):
            raise _error("invalid","A complete result needs an answer or published artifact.")
        if result.get("document_id"):
            self.document_metadata(result["document_id"])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            claim=db.execute("SELECT * FROM request_claims WHERE request_id=?",(_id(request_id),)).fetchone()
            if not claim or claim["worker_id"]!=worker_id or not secrets.compare_digest(claim["token"],str(claim_token)) or claim["expires_at"]<time.time():
                raise _error("conflict","The request lease is no longer owned by this worker.")
            request=db.execute("SELECT * FROM requests WHERE id=?",(request_id,)).fetchone()
            if request["status"]!="in_progress":
                raise _error("conflict","This request already has a terminal result.")
            owner=json.loads(request["body"]).get("learner_id","default")
            if result.get("edition_id"):
                # Ownership is checked independently of the worker's label.
                self.get_edition(result["edition_id"],owner)
            db.execute("UPDATE requests SET status=?,result=?,updated_at=? WHERE id=?",(status,_json(result),_now(),request_id))
            db.execute("DELETE FROM request_claims WHERE request_id=?",(request_id,))
            row=db.execute("SELECT * FROM requests WHERE id=?",(request_id,)).fetchone()
        return self.request_value(row)

    def retry_request(self, request_id, learner_id="default"):
        self._learner(learner_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM requests WHERE id=?",(_id(request_id),)).fetchone()
            if not row or json.loads(row["body"]).get("learner_id","default")!=learner_id:
                raise _error("not_found","Request not found for this learner.")
            if row["status"] not in {"failed","cancelled"}:
                raise _error("conflict","Only failed or cancelled requests can be retried.")
            db.execute("DELETE FROM request_claims WHERE request_id=?",(request_id,))
            db.execute("UPDATE requests SET status='pending',result=NULL,updated_at=? WHERE id=?",(_now(),request_id))
            row=db.execute("SELECT * FROM requests WHERE id=?",(request_id,)).fetchone()
        return self.request_value(row)

    def delete_research_document(self, document_id):
        self.document_metadata(document_id)
        cache=self.home/"papers"
        if cache.exists() and (cache.is_symlink() or (hasattr(cache,"is_junction") and cache.is_junction()) or not cache.resolve().is_relative_to(self.home)):
            raise _error("forbidden","The retained-paper directory must be inside the workspace and cannot be a link.")
        folder=cache/_id(document_id)
        if folder.exists() and (folder.is_symlink() or (hasattr(folder,"is_junction") and folder.is_junction()) or folder.resolve().parent!=cache.resolve()):
            raise _error("forbidden","The source cache path is not safe to remove.")
        quarantine=cache/(".deleted-"+document_id+"-"+uuid.uuid4().hex[:8])
        moved=False
        try:
            if folder.exists():
                # Rename only the product-owned cache, never source.original or
                # an imported Library path. A failure leaves the DB untouched.
                folder.rename(quarantine);moved=True
            references=self.learning.delete_document_references(document_id) if hasattr(self.learning,"delete_document_references") else {}
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS research_deletions(document_id TEXT PRIMARY KEY,cache_path TEXT,state TEXT NOT NULL)")
                for table in ("progress","storyboards","storyboard_history","annotations","collection_documents","source_search","library_metadata"):
                    db.execute("DELETE FROM "+table+" WHERE document_id=?",(document_id,))
                edition_ids=[r[0] for r in db.execute("SELECT id FROM exploration_editions WHERE document_id=?",(document_id,))]
                for edition_id in edition_ids:
                    db.execute("DELETE FROM edition_history WHERE edition_id=?",(edition_id,))
                    db.execute("DELETE FROM edition_progress WHERE edition_id=?",(edition_id,))
                db.execute("DELETE FROM exploration_editions WHERE document_id=?",(document_id,))
                # Cross-document links cannot retain quotations from a forgotten source.
                for row in db.execute("SELECT id,body FROM concept_connections").fetchall():
                    value=json.loads(row["body"])
                    if document_id in {value.get("document_id"),value.get("from",{}).get("document_id"),value.get("to",{}).get("document_id")}:
                        db.execute("DELETE FROM concept_connections WHERE id=?",(row["id"],))
                for row in db.execute("SELECT id,body,result FROM requests").fetchall():
                    value=json.loads(row["body"]);result=json.loads(row["result"]) if row["result"] else {}
                    if value.get("document_id")==document_id or result.get("document_id")==document_id or value.get("edition_id") in edition_ids:
                        db.execute("DELETE FROM request_claims WHERE request_id=?",(row["id"],))
                        db.execute("DELETE FROM requests WHERE id=?",(row["id"],))
                db.execute("DELETE FROM documents WHERE id=?",(document_id,))
                db.execute("INSERT OR REPLACE INTO research_deletions VALUES(?,?,?)",(document_id,str(quarantine) if moved else "", "pending" if moved else "complete"))
        except Exception:
            if moved and quarantine.exists() and not folder.exists():
                quarantine.rename(folder)
            raise
        cleanup={"complete":True,"retained_source_removed":moved,"original_source_untouched":True}
        if moved:
            try:
                # Guard the final resolved recursive-delete target once more.
                if quarantine.resolve().parent!=cache.resolve() or not quarantine.name.startswith(".deleted-"+document_id+"-"):
                    raise OSError("Unexpected retained-source deletion target.")
                shutil.rmtree(quarantine)
                with self.connect() as db:
                    db.execute("UPDATE research_deletions SET state='complete' WHERE document_id=?",(document_id,))
            except OSError as exc:
                cleanup={"complete":False,"retained_source_removed":False,"original_source_untouched":True,
                         "error":str(exc),"pending_cache":str(quarantine)}
        return {"deleted":document_id,"cleanup":cleanup,"learning_references":references}

    def cleanup_deleted_sources(self):
        """Retry only previously journaled, scoped cache removals."""
        with self.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='research_deletions'").fetchone()
            rows=db.execute("SELECT * FROM research_deletions WHERE state='pending'").fetchall() if exists else []
        result=[]
        cache=(self.home/"papers").resolve()
        for row in rows:
            target=Path(row["cache_path"])
            if not target.resolve().is_relative_to(self.home) or target.resolve().parent!=cache or not target.name.startswith(".deleted-"+_id(row["document_id"])+"-") or target.is_symlink() or (hasattr(target,"is_junction") and target.is_junction()):
                result.append({"document_id":row["document_id"],"complete":False,"error":"Unsafe cleanup target"});continue
            try:
                if target.exists():shutil.rmtree(target)
                with self.connect() as db:
                    db.execute("UPDATE research_deletions SET state='complete' WHERE document_id=?",(row["document_id"],))
                result.append({"document_id":row["document_id"],"complete":True})
            except OSError as exc:
                result.append({"document_id":row["document_id"],"complete":False,"error":str(exc)})
        return {"items":result}

    def forget_research_learner(self, learner_id):
        self._learner(learner_id)
        with self.connect() as db:
            collections=[r[0] for r in db.execute("SELECT id FROM research_collections WHERE learner_id=?",(learner_id,))]
            editions=[r[0] for r in db.execute("SELECT id FROM exploration_editions WHERE learner_id=?",(learner_id,))]
            for collection_id in collections:
                db.execute("DELETE FROM collection_documents WHERE collection_id=?",(collection_id,))
            for edition_id in editions:
                db.execute("DELETE FROM edition_history WHERE edition_id=?",(edition_id,))
            for table in ("research_collections","annotations","concept_connections","exploration_editions","edition_progress"):
                db.execute("DELETE FROM "+table+" WHERE learner_id=?",(learner_id,))
