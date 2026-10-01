"""Direct command interface used by the host intelligence."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from workspace import Workspace, AppError, PRODUCT, VERSION, encode
from server import serve, learning_call, research_call, reject_constant

def read_json(path):
    return json.loads(sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_constant)

def default_home():
    override = os.environ.get("LUCERNA_HOME") or os.environ.get("TEACHING_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        legacy, current = root / "TeachingExplanation", root / "Lucerna"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        legacy, current = root / "teaching-explanation", root / "lucerna"
    # Keep existing user state attached to a name-only update.
    has_state = lambda p: any((p / name).is_file() for name in ("learning.sqlite3", "workspace.sqlite3"))
    return current if has_state(current) or not has_state(legacy) else legacy

def main(argv=None):
    # JSON is always UTF-8, including redirected Windows pipes and mathematical text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Lucerna: durable learning and explorable sources.")
    p.add_argument("--home", default=str(default_home()), help="Learner/library data directory; outside the installed skill.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    showcase = sub.add_parser("showcase"); showcase.add_argument("slug", nargs="?"); showcase.add_argument("--learner", default="default")
    server = sub.add_parser("serve"); server.add_argument("--port", type=int, default=0)
    library = sub.add_parser("library")
    library.add_argument("--query",default=""); library.add_argument("--kind")
    library.add_argument("--collection"); library.add_argument("--family"); library.add_argument("--learner",default="default")
    library.add_argument("--limit",type=int,default=500); library.add_argument("--offset",type=int,default=0)
    search = sub.add_parser("search"); search.add_argument("query"); search.add_argument("--document")
    search.add_argument("--collection"); search.add_argument("--kind"); search.add_argument("--learner",default="default")
    search.add_argument("--limit",type=int,default=30); search.add_argument("--offset",type=int,default=0)
    research = sub.add_parser("research"); research.add_argument("operation"); research.add_argument("--input",required=True)
    imp = sub.add_parser("import"); imp.add_argument("source")
    doc = sub.add_parser("document"); doc.add_argument("id")
    board = sub.add_parser("storyboard"); board.add_argument("id")
    board.add_argument("--edition"); board.add_argument("--learner",default="default")
    lesson = sub.add_parser("lesson"); lesson.add_argument("--input", required=True)
    packet = sub.add_parser("packet"); packet.add_argument("id")
    packet.add_argument("--learner", default="default"); packet.add_argument("--question")
    packet.add_argument("--section", action="append", dest="sections"); packet.add_argument("--edition")
    publish = sub.add_parser("publish"); publish.add_argument("id"); publish.add_argument("file")
    publish.add_argument("--expected-revision", type=int); publish.add_argument("--edition"); publish.add_argument("--learner",default="default")
    learn = sub.add_parser("learning"); learn.add_argument("operation"); learn.add_argument("--input", required=True)
    ctx = sub.add_parser("context"); ctx.add_argument("--learner", default="default")
    ctx.add_argument("--curriculum"); ctx.add_argument("--query", default=""); ctx.add_argument("--situation",help="JSON file with current applicable context selectors")
    requests = sub.add_parser("requests"); requests.add_argument("--status"); requests.add_argument("--learner"); requests.add_argument("--document")
    claim = sub.add_parser("claim"); claim.add_argument("--worker",required=True); claim.add_argument("--learner",default="default"); claim.add_argument("--request"); claim.add_argument("--lease",type=int,default=600)
    req = sub.add_parser("request"); req.add_argument("id")
    resolve = sub.add_parser("resolve"); resolve.add_argument("id")
    resolve.add_argument("--status", choices=["in_progress","complete","failed","cancelled"], required=True)
    resolve.add_argument("--input"); resolve.add_argument("--worker"); resolve.add_argument("--claim-token")
    exp = sub.add_parser("export"); exp.add_argument("id"); exp.add_argument("output"); exp.add_argument("--edition"); exp.add_argument("--learner",default="default")
    delete = sub.add_parser("delete-document"); delete.add_argument("id")
    args = p.parse_args(argv)
    try:
        ws = Workspace(args.home)
        command = args.command
        if command == "showcase":
            from showcases import available_showcases, install_showcase
            result = install_showcase(ws, args.slug, args.learner) if args.slug else {"items": available_showcases()}
        elif command == "init":
            result = {"product": PRODUCT, "version": VERSION, "home": str(ws.home),
                      "learner_id": "default", "migration":ws.migration, "capabilities": ["learning", "curricula", "pathways", "papers", "full-text-search", "source-notes", "concept-connections", "named-explorations", "request-leases", "interactive-scenes", "device-narration"]}
        elif command == "serve":
            return serve(ws, args.port)
        elif command == "library":
            result = ws.catalogue(args.query,args.kind,args.collection,args.family,args.learner,args.limit,args.offset)
        elif command == "search":
            result = ws.search_sources(args.query,args.document,args.collection,args.kind,args.learner,args.limit,args.offset)
        elif command == "research":
            result = research_call(ws,args.operation,read_json(args.input))
        elif command == "import":
            result = ws.import_document(args.source)
        elif command == "document":
            result = ws.get_document(args.id)
        elif command == "storyboard":
            result = ws.get_storyboard(args.id,args.edition,args.learner)
        elif command == "lesson":
            result = ws.create_lesson(**read_json(args.input))
        elif command == "packet":
            result = ws.packet(args.id, args.learner, args.question, args.sections,args.edition)
        elif command == "publish":
            result = ws.save_storyboard(args.id, read_json(args.file), args.expected_revision,args.edition,args.learner)
        elif command == "learning":
            payload = read_json(args.input)
            result = ws.forget_learner(**payload) if args.operation == "delete_learner" else learning_call(ws.learning, args.operation, payload)
        elif command == "context":
            result = ws.learning.context(args.learner, args.curriculum, args.query,situation=read_json(args.situation) if args.situation else None)
        elif command == "requests":
            result = {"items": ws.list_requests(args.status,args.learner,args.document)}
        elif command == "claim":
            result = ws.claim_request(args.worker,args.learner,args.request,args.lease)
        elif command == "request":
            result = ws.request_packet(args.id)
        elif command == "resolve":
            result = (ws.resolve_claimed_request(args.id,args.worker,args.claim_token,read_json(args.input) if args.input else None,args.status)
                      if args.worker and args.claim_token else ws.update_request(args.id, args.status, read_json(args.input) if args.input else None))
        elif command == "export":
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(ws.export_html(args.id,args.edition,args.learner), encoding="utf-8")
            result = {"path": str(output), "document_id": args.id, "format": "self-contained-html"}
        elif command == "delete-document":
            result = ws.delete_document(args.id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "invalid" if isinstance(exc, (ValueError, TypeError, FileNotFoundError)) else "internal")
        print(encode({"error": {"code": code, "message": str(exc)}}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
