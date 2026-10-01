"""A same-origin loopback adapter for the teaching workspace. No model server."""
from __future__ import annotations
import inspect
import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from workspace import Workspace, ROOT, PRODUCT, VERSION, AppError, encode

LEARNING_OPERATIONS = frozenset("""
save_learner get_learner list_learners delete_learner
save_memory get_memory list_memories delete_memory recall
save_curriculum get_curriculum list_curricula delete_curriculum
save_pathway get_pathway list_pathways delete_pathway plan_pathway resume_pathway
record_encounter get_encounter list_encounters delete_encounter
context export_learner import_learner history
""".split())

def learning_call(store, operation, payload):
    if operation not in LEARNING_OPERATIONS:
        raise AppError("not_found", "Unknown learning operation.")
    method = getattr(store, operation)
    try:
        inspect.signature(method).bind(**payload)
    except TypeError as exc:
        raise AppError("invalid", str(exc)) from exc
    return method(**payload)

RESEARCH_OPERATIONS = frozenset("""
catalogue search_sources document_metadata document_section list_collections get_collection save_collection delete_collection
list_annotations save_annotation delete_annotation save_connection concept_graph delete_connection
list_editions get_edition create_edition rename_edition delete_edition claim_request renew_request_claim resolve_claimed_request
retry_request cleanup_deleted_sources
""".split())

def research_call(workspace, operation, payload):
    if operation not in RESEARCH_OPERATIONS:
        raise AppError("not_found", "Unknown research operation.")
    method = getattr(workspace, operation)
    try:
        inspect.signature(method).bind(**payload)
    except TypeError as exc:
        raise AppError("invalid", str(exc)) from exc
    return method(**payload)

def reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)

class TeachingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, workspace, port=0):
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), TeachingHandler)
        self.url = "http://127.0.0.1:" + str(self.server_address[1])

class TeachingHandler(BaseHTTPRequestHandler):
    server_version = "Lucerna/0.3"
    def log_message(self, format, *args):
        # HTTP paths can contain source questions; do not retain those by default.
        pass

    def send_bytes(self, value, content_type="application/json; charset=utf-8", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: https://arxiv.org; connect-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if extra:
            for key, val in extra.items():
                self.send_header(key, val)
        self.end_headers()
        self.wfile.write(value)

    def send_json(self, value, status=200):
        self.send_bytes(encode(value).encode("utf-8"), status=status)

    def check_request(self, mutation=False):
        port = self.server.server_address[1]
        hosts = {"127.0.0.1:" + str(port), "localhost:" + str(port)}
        if self.headers.get("Host", "") not in hosts:
            raise AppError("forbidden", "This workspace accepts only its loopback address.")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + x for x in hosts}:
            raise AppError("forbidden", "Cross-origin workspace access is disabled.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("forbidden", "Cross-site workspace access is disabled.")
        if mutation and not secrets.compare_digest(self.headers.get("X-Teaching-Token", ""), self.server.token):
            raise AppError("forbidden", "Reload this workspace before changing it.")

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise AppError("invalid", "Invalid content length.")
        if size < 0 or size > 32 * 1024 * 1024:
            raise AppError("too_large", "This upload exceeds the 32 MB request limit.")
        if self.headers.get("Transfer-Encoding"):
            raise AppError("invalid", "Chunked requests are not supported.")
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise AppError("invalid", "Send JSON to this endpoint.")
        try:
            value = json.loads(self.rfile.read(size), parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise AppError("invalid", "Request contains invalid JSON.")
        if not isinstance(value, dict):
            raise AppError("invalid", "Request must be a JSON object.")
        return value

    def dispatch(self, method):
        try:
            self.check_request(method != "GET")
            path = unquote(urlsplit(self.path).path)
            query = parse_qs(urlsplit(self.path).query)
            ws = self.server.workspace
            learner = query.get("learner_id", ["default"])[0]
            edition = query.get("edition_id", [None])[0]
            if method == "GET":
                if path == "/api/showcases":
                    from showcases import available_showcases
                    return self.send_json({"items": available_showcases()})
                if path == "/health":
                    return self.send_json({"ok": True, "product": PRODUCT, "version": VERSION})
                if path == "/api/bootstrap":
                    return self.send_json({"token": self.server.token, "learner_id": "default",
                        "capabilities": {"paper_import": True, "narration": "device", "agent_generation": "host",
                                         "full_text_search": ws.search_mode, "collections": True, "annotations": True,
                                         "concept_connections": True, "exploration_editions": True, "request_claims": True},
                        "product": {"name": PRODUCT, "version": VERSION}})
                if path == "/api/library":
                    return self.send_json(ws.catalogue(query.get("query", query.get("q", [""]))[0], kind=query.get("kind",[None])[0],
                        collection_id=query.get("collection_id",[None])[0],family_id=query.get("family_id",[None])[0],learner_id=learner,
                        limit=query.get("limit",[500])[0],offset=query.get("offset",[0])[0]))
                if path == "/api/requests":
                    return self.send_json({"items": ws.list_requests(query.get("status", [None])[0], learner, query.get("document_id",[None])[0])})
                if path == "/api/search":
                    return self.send_json(ws.search_sources(query.get("q",query.get("query",[""]))[0],
                        document_id=query.get("document_id",[None])[0],collection_id=query.get("collection_id",[None])[0],
                        kind=query.get("kind",[None])[0],learner_id=learner,limit=query.get("limit",[30])[0],offset=query.get("offset",[0])[0]))
                if path == "/api/collections":
                    return self.send_json({"items":ws.list_collections(learner)})
                if path == "/api/learners":
                    return self.send_json({"items":ws.learning.list_learners()})
                if path == "/api/graph":
                    return self.send_json(ws.concept_graph(query.get("document_id",[None])[0],learner,query.get("query",[""])[0]))
                if path == "/api/learning/context":
                    return self.send_json(ws.learning.context(learner,
                        curriculum_id=query.get("curriculum_id", [None])[0], query=query.get("query", [""])[0],
                        situation=json.loads(query["situation"][0],parse_constant=reject_constant) if query.get("situation") else None))
                if path == "/api/learning/pathways":
                    items = []
                    for p in ws.learning.list_pathways(learner_id=learner):
                        items.append({"pathway": p, "curriculum": ws.learning.get_curriculum(p["curriculum_id"]),
                                      "plan": ws.learning.resume_pathway(p["id"])["plan"]})
                    return self.send_json({"items": items})
                parts = path.strip("/").split("/")
                if len(parts) >= 3 and parts[:2] == ["api", "documents"]:
                    doc_id = parts[2]
                    if len(parts) == 3:
                        return self.send_json(ws.get_document(doc_id))
                    if len(parts) == 4 and parts[3] == "storyboard":
                        return self.send_json(ws.get_storyboard(doc_id,edition,learner))
                    if len(parts) == 4 and parts[3] == "metadata":
                        return self.send_json(ws.document_metadata(doc_id))
                    if len(parts) == 4 and parts[3] == "source":
                        source=ws.source_file(doc_id)
                        mime="application/pdf" if source.suffix==".pdf" else "application/octet-stream"
                        return self.send_bytes(source.read_bytes(),mime,extra={"Content-Disposition":'attachment; filename="'+source.name+'"'})
                    if len(parts) == 4 and parts[3] in {"annotations","notes"}:
                        return self.send_json({"items":ws.list_annotations(doc_id,learner)})
                    if len(parts) == 4 and parts[3] == "connections":
                        return self.send_json(ws.concept_graph(doc_id,learner))
                    if len(parts) == 4 and parts[3] == "editions":
                        return self.send_json({"items":ws.list_editions(doc_id,learner)})
                    if len(parts) == 5 and parts[3] == "sections":
                        return self.send_json(ws.document_section(doc_id,parts[4],query.get("radius",[0])[0]))
                    if len(parts) == 4 and parts[3] == "export":
                        return self.send_bytes(ws.export_html(doc_id,edition,learner).encode("utf-8"), "text/html; charset=utf-8",
                            extra={"Content-Disposition": 'attachment; filename="teaching-exploration.html"'})
                if len(parts) == 3 and parts[:2] == ["api", "progress"]:
                    return self.send_json(ws.get_progress(parts[2], learner, edition))
                if path.startswith("/api/"):
                    raise AppError("not_found", "Unknown endpoint.")
                # Static files are confined to the packaged web directory.
                relative = path.lstrip("/") or "index.html"
                file = (ROOT / "web" / relative).resolve()
                if not file.is_relative_to((ROOT / "web").resolve()) or not file.is_file():
                    raise AppError("not_found", "Page not found.")
                if file.suffix not in {".html", ".js", ".css", ".svg", ".png", ".ico", ".woff2", ".woff", ".ttf"}:
                    raise AppError("not_found", "Page not found.")
                mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".woff2":"font/woff2", ".woff":"font/woff", ".ttf":"font/ttf"}.get(
                    file.suffix, mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return self.send_bytes(file.read_bytes(), mime)
            data = {} if method == "DELETE" and not int(self.headers.get("Content-Length","0")) else self.body()
            if method == "POST" and path == "/api/showcases":
                from showcases import install_showcase
                return self.send_json(install_showcase(ws, data.get("slug"), data.get("learner_id", learner)), 201)
            if method == "POST" and path == "/api/import":
                source = data.get("source", "")
                if not isinstance(source, str):
                    raise AppError("invalid", "Source must be text.")
                # HTTP cannot read a local filesystem path. Local files arrive as uploads;
                # CLI imports retain the host agent's explicit filesystem authority.
                if data.get("content") is None and not (
                    re.fullmatch(r"(?:arxiv:)?(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", source.strip())
                    or urlsplit(source).scheme in {"http", "https"}):
                    raise AppError("invalid", "Paste an arXiv link or upload the source file.")
                return self.send_json(ws.import_document(source, data.get("filename"), data.get("content"), data.get("content_encoding")), 201)
            if method == "POST" and path == "/api/requests/claim":
                return self.send_json(ws.claim_request(**data))
            if method == "POST" and path == "/api/collections":
                return self.send_json(ws.save_collection(data,learner),201)
            if method == "POST" and path.startswith("/api/research/"):
                return self.send_json(research_call(ws,path.rsplit("/",1)[1],data))
            if method == "POST" and path == "/api/requests":
                return self.send_json(ws.new_request(data), 201)
            if method == "POST" and path.startswith("/api/learning/"):
                operation = path.rsplit("/", 1)[1]
                return self.send_json(ws.forget_learner(**data) if operation == "delete_learner" else learning_call(ws.learning, operation, data))
            parts = path.strip("/").split("/")
            owner=data.get("learner_id",learner)
            if len(parts)==3 and parts[:2]==["api","collections"]:
                if method=="PUT":
                    return self.send_json(ws.save_collection(data,owner,parts[2]))
                if method=="DELETE":
                    return self.send_json(ws.delete_collection(parts[2],owner))
            if len(parts)==3 and parts[:2]==["api","editions"]:
                if method=="PUT":
                    return self.send_json(ws.rename_edition(parts[2],data["title"],owner,data.get("expected_revision")))
                if method=="DELETE":
                    return self.send_json(ws.delete_edition(parts[2],owner))
            if len(parts)==3 and parts[:2] in (["api","annotations"],["api","notes"]):
                if method=="DELETE":
                    return self.send_json(ws.delete_annotation(parts[2],owner))
                if method=="PUT":
                    with ws.connect() as db:
                        row=db.execute("SELECT document_id FROM annotations WHERE id=? AND learner_id=?",(parts[2],owner)).fetchone()
                    if not row:raise AppError("not_found","Annotation not found for this learner.")
                    return self.send_json(ws.save_annotation(row["document_id"],data,owner,parts[2],data.get("expected_revision")))
            if len(parts)==3 and parts[:2]==["api","connections"]:
                if method=="DELETE":
                    return self.send_json(ws.delete_connection(parts[2],owner))
                if method=="PUT":
                    with ws.connect() as db:
                        row=db.execute("SELECT document_id FROM concept_connections WHERE id=? AND learner_id=?",(parts[2],owner)).fetchone()
                    if not row:raise AppError("not_found","Connection not found for this learner.")
                    return self.send_json(ws.save_connection(row["document_id"],data,owner,parts[2],data.get("expected_revision")))
            if len(parts)>=3 and parts[:2]==["api","documents"]:
                document_id=parts[2]
                if method=="DELETE" and len(parts)==3:
                    return self.send_json(ws.delete_document(document_id))
                if method=="POST" and len(parts)==4:
                    if parts[3] in {"notes","annotations"}:
                        return self.send_json(ws.save_annotation(document_id,data,owner),201)
                    if parts[3]=="connections":
                        return self.send_json(ws.save_connection(document_id,data,owner),201)
                    if parts[3]=="editions":
                        return self.send_json(ws.create_edition(document_id,data["title"],owner,data.get("clone_default",False)),201)
                if method=="PUT" and len(parts)==4 and parts[3]=="storyboard":
                    return self.send_json(ws.save_storyboard(document_id,data["storyboard"],data.get("expected_revision"),data.get("edition_id",edition),owner))
            if method=="POST" and len(parts)==4 and parts[:2]==["api","requests"]:
                payload={k:v for k,v in data.items() if k!="learner_id"}
                if parts[3]=="resolve":
                    return self.send_json(ws.resolve_claimed_request(parts[2],**payload))
                if parts[3]=="renew":
                    return self.send_json(ws.renew_request_claim(parts[2],**payload))
                if parts[3]=="retry":
                    return self.send_json(ws.retry_request(parts[2],owner))
            if method == "PUT" and len(parts) == 3 and parts[:2] == ["api", "progress"]:
                return self.send_json(ws.save_progress(parts[2], data, learner_id=owner,edition_id=data.get("edition_id",edition)))
            raise AppError("not_found", "Unknown endpoint.")
        except Exception as exc:
            code = getattr(exc, "code", "invalid" if isinstance(exc, (ValueError, TypeError)) else "internal")
            status = {"invalid_source": 400, "unsafe_redirect": 400, "import_failed": 422, "ocr_required": 422, "extraction_failed": 422, "source_too_large": 413, "invalid": 400, "not_found": 404, "conflict": 409,
                      "forbidden": 403, "too_large": 413}.get(code, 500)
            message = str(exc) if status != 500 else "Workspace operation failed. Check the local runtime."
            self.send_json({"error": {"code": code, "message": message}}, status)

    def do_GET(self): self.dispatch("GET")
    def do_POST(self): self.dispatch("POST")
    def do_PUT(self): self.dispatch("PUT")
    def do_DELETE(self): self.dispatch("DELETE")

def serve(workspace, port=0):
    server = TeachingServer(workspace, port)
    status = {"url": server.url, "home": str(workspace.home), "product": PRODUCT, "version": VERSION}
    (workspace.home / "server.json").write_text(encode(status), encoding="utf-8")
    print(encode(status), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
