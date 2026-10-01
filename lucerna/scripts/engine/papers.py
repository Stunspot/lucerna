"""Source-preserving imports for the local teaching workspace.

Imported HTML is parsed as data, never executed. Exact source bytes stay in the
cache. Semantic interpretation belongs to the host; extraction losses are explicit.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_SOURCE_BYTES = 50 * 1024 * 1024
ARXIV_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]+/\d{7})(?:v[1-9]\d*)?$")
ALLOWED_HOSTS = {"arxiv.org", "export.arxiv.org", "ar5iv.labs.arxiv.org", "ar5iv.org"}


class PaperError(ValueError):
    def __init__(self, message, code="import_failed"):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize_arxiv_id(source):
    value = str(source).strip()
    if value.lower().startswith("arxiv:"):
        value = value[6:].strip()
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
            raise PaperError("Use an arXiv URL/ID, or import an explicitly selected local file.", "invalid_source")
        value = re.sub(r"^/(?:abs|pdf|html)/", "", parsed.path).removesuffix(".pdf")
    if not ARXIV_PATTERN.fullmatch(value):
        raise PaperError("Expected an arXiv ID such as 1706.03762v7 or a full arxiv.org paper URL.", "invalid_source")
    return value


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != "https" or target.hostname not in ALLOWED_HOSTS:
            raise PaperError("The paper host redirected outside the supported source hosts.", "unsafe_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise PaperError("Unsupported paper source host.", "invalid_source")
    request = Request(url, headers={"User-Agent": "Lucerna/0.1 (local paper reader)", "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
    with build_opener(_SafeRedirect()).open(request, timeout=25) as response:
        data = response.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise PaperError("Source exceeds the 50 MB import limit.", "source_too_large")
        return data, response.geturl(), response.headers.get_content_type()


class _Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs or []), parent, []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def text(self):
        if self.tag == "math":
            latex = self.attrs.get("alttext")
            if not latex:
                annotation = next((n for n in self.walk() if n.tag == "annotation" and n.attrs.get("encoding") in {"application/x-tex", "application/x-latex"}), None)
                latex = annotation.text() if annotation else None
            if latex:
                return " " + latex.strip() + " "
        if self.tag in {"script", "style", "nav", "noscript", "template"}:
            return ""
        return "".join(c.text() if isinstance(c, _Node) else c for c in self.children)


class _Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.current = self.root
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node
        elif tag in {"br", "hr"}:
            self.current.children.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, text):
        self.current.children.append(text)


def _clean(text):
    return re.sub(r"\s+", " ", text).strip()


def _anchor(node):
    while node is not None:
        if node.attrs.get("id"):
            return {"fragment": node.attrs["id"]}
        node = node.parent
    return {}


def _parse_html(data, base_url=""):
    html = data.decode("utf-8-sig", errors="replace")
    tree = _Tree(html)
    root = next((n for n in tree.root.walk() if n.tag == "article"), None)
    root = root or next((n for n in tree.root.walk() if n.tag == "main"), None)
    root = root or next((n for n in tree.root.walk() if n.tag == "body"), tree.root)
    title_node = next((n for n in root.walk() if n.tag == "h1"), None)
    title_node = title_node or next((n for n in tree.root.walk() if n.tag == "title"), None)
    title = _clean(title_node.text()) if title_node else "Imported document"
    sections, section_stack = [], []
    block_number = 0

    def section(title, level=1, node=None):
        while section_stack and section_stack[-1]["level"] >= level:
            section_stack.pop()
        item = {"id": "s-" + str(len(sections) + 1), "title": title, "level": level,
                "parent_id": section_stack[-1]["id"] if section_stack and section_stack[-1]["title"] != "Opening" else None,
                "blocks": [], "anchor": _anchor(node) if node else {}}
        sections.append(item)
        section_stack.append(item)
        return item

    current = section("Opening", 0)

    def block(kind, text, node, **extras):
        nonlocal block_number
        text = _clean(text) if kind != "code" else text.strip()
        if not text and not extras:
            return
        block_number += 1
        current["blocks"].append({"id": "b-" + str(block_number), "kind": kind,
                                  "text": text, "anchor": _anchor(node), **extras})

    def visit(node):
        nonlocal current
        if isinstance(node, str):
            return
        if node.tag in {"script", "style", "nav", "noscript", "template"}:
            return
        if re.fullmatch(r"h[1-6]", node.tag):
            heading = _clean(node.text())
            if heading:
                current = section(heading, int(node.tag[1]), node)
            return
        classes = node.attrs.get("class", "").split()
        if node.tag == "figure" or "ltx_figure" in classes:
            captions = [n for n in node.walk() if n.tag == "figcaption" or "ltx_caption" in n.attrs.get("class", "").split()]
            caption = " ".join(_clean(n.text()) for n in captions) or _clean(node.text())
            images = [n for n in node.walk() if n.tag == "img"]
            assets = []
            for img in images:
                candidate = urljoin(base_url, img.attrs.get("src", ""))
                if urlparse(candidate).scheme in {"https", "http"}:
                    assets.append({"url": candidate, "alt": img.attrs.get("alt", "")})
            block("figure", caption, node, assets=assets, **({"asset_url": assets[0]["url"]} if assets else {}))
            for table in (n for n in node.walk() if n.tag == "table"):
                table_block(table)
            return
        if node.tag == "table":
            table_block(node)
            return
        if node.tag == "math":
            latex = node.attrs.get("alttext", "")
            if not latex:
                annotation = next((n for n in node.walk() if n.tag == "annotation" and "tex" in n.attrs.get("encoding", "")), None)
                latex = annotation.text() if annotation else node.text()
            block("equation", latex, node, latex=latex.strip(), display=node.attrs.get("display") == "block")
            return
        if node.tag in {"p", "pre", "blockquote", "li", "dt", "dd", "figcaption"}:
            kind = "code" if node.tag == "pre" else "list" if node.tag in {"li", "dt", "dd"} else "paragraph"
            block(kind, node.text(), node)
            for math_node in (n for n in node.walk() if n.tag == "math"):
                visit(math_node)
            return
        for child in node.children:
            if isinstance(child, str) and _clean(child):
                block("paragraph", child, node)
            else:
                visit(child)

    def table_block(node):
        rows = []
        for row in (n for n in node.walk() if n.tag == "tr"):
            cells = [n for n in row.children if isinstance(n, _Node) and n.tag in {"td", "th"}]
            if cells:
                rows.append([_clean(n.text()) for n in cells])
        maths = [{"latex": n.attrs.get("alttext") or n.text(), "anchor": _anchor(n)} for n in node.walk() if n.tag == "math"]
        if "ltx_eqn_table" in node.attrs.get("class", "").split() and maths:
            latex = "\n".join(m["latex"] for m in maths)
            block("equation", latex, node, latex=latex, display=True, rows=rows)
        else:
            block("table", "\n".join(" | ".join(row) for row in rows), node, rows=rows, equations=maths)

    visit(root)
    sections = [s for s in sections if s["blocks"] or s["title"] != "Opening"]
    if not any(s["blocks"] for s in sections):
        plain = _clean(root.text())
        if plain:
            sections = [{"id": "s-1", "title": title, "level": 1, "parent_id": None,
                         "blocks": [{"id": "b-1", "kind": "paragraph", "text": plain, "anchor": {}}]}]
    return title, sections, [], "structured-html"


def _parse_text(data, markdown=False):
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    sections = [{"id": "s-1", "title": "Document", "level": 1, "parent_id": None, "blocks": []}]
    current, block_no, buffer, start_line, in_code = sections[0], 0, [], 1, False
    def flush(end_line):
        nonlocal block_no, buffer
        if buffer and "\n".join(buffer).strip():
            block_no += 1
            body = "\n".join(buffer).strip()
            kind = "code" if in_code else "equation" if body.startswith("$$") and body.endswith("$$") else "paragraph"
            entry = {"id": f"b-{block_no}", "kind": kind, "text": body, "anchor": {"line": start_line, "end_line": end_line}}
            if kind == "equation":
                entry["latex"] = body[2:-2].strip()
            current["blocks"].append(entry)
        buffer = []
    title = None
    for line_no, line in enumerate(text.splitlines(), 1):
        if markdown and line.startswith("```"):
            flush(line_no - 1)
            in_code = not in_code
            start_line = line_no + 1
            continue
        header = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if markdown and not in_code else None
        if header:
            flush(line_no - 1)
            level, name = len(header.group(1)), header.group(2)
            title = title or name
            parent = next((s["id"] for s in reversed(sections) if s["level"] < level and s["title"] != "Document"), None)
            current = {"id": f"s-{len(sections) + 1}", "title": name, "level": level, "parent_id": parent, "blocks": []}
            sections.append(current)
            start_line = line_no + 1
        elif not line.strip() and not in_code:
            flush(line_no - 1)
            start_line = line_no + 1
        else:
            if not buffer:
                start_line = line_no
            buffer.append(line)
    flush(len(text.splitlines()))
    sections = [s for s in sections if s["blocks"] or s["title"] != "Document"]
    title = title or next((line.strip()[:160] for line in text.splitlines() if line.strip()), "Imported document")
    return title, sections, [], "markdown" if markdown else "plain-text"


def _parse_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise PaperError("PDF extraction needs the bundled pypdf library. Use the packaged launcher or import HTML/text.", "pdf_unavailable") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise PaperError("This PDF is encrypted. Export an unlocked copy before importing.", "pdf_encrypted")
        sections, warnings, block_no = [], [], 0
        for page_no, page in enumerate(reader.pages, 1):
            text = (page.extract_text(extraction_mode="layout") or "") if page.get("/Contents") is not None else ""
            blocks = []
            for paragraph in re.split(r"\n\s*\n", text):
                if paragraph.strip():
                    block_no += 1
                    blocks.append({"id": f"b-{block_no}", "kind": "paragraph", "text": paragraph.strip(), "anchor": {"page": page_no}})
            if not blocks:
                warnings.append(f"Page {page_no} has no extractable text; it may require OCR. The original PDF is retained.")
            sections.append({"id": f"s-{page_no}", "title": f"Page {page_no}", "level": 1, "parent_id": None, "blocks": blocks, "anchor": {"page": page_no}})
        title = str((reader.metadata or {}).get("/Title", "Imported PDF")).strip() or "Imported PDF"
        warnings.insert(0, "PDF page text preserves all pages. Equations, columns and figure geometry need checking against the retained PDF; use HTML when available for structured math and figure extraction.")
        if not any(s["blocks"] for s in sections):
            raise PaperError("This PDF has no extractable text. OCR it with the host's document tools and import the resulting text; the file was not treated as a successfully read paper.", "ocr_required")
        return title, sections, warnings, "pdf-page-text"
    except PaperError:
        raise
    except Exception as exc:
        raise PaperError(f"PDF extraction failed: {type(exc).__name__}: {exc}", "pdf_invalid") from exc


def _arxiv_source(source):
    requested = normalize_arxiv_id(source)
    requested_version = re.search(r"v(\d+)$", requested)
    abs_url = "https://arxiv.org/abs/" + requested
    meta_data, _, _ = _fetch(abs_url)
    tree = _Tree(meta_data.decode("utf-8", errors="replace"))
    meta = {}
    authors = []
    for node in tree.root.walk():
        if node.tag == "meta":
            key = node.attrs.get("name") or node.attrs.get("property")
            meta[key] = node.attrs.get("content", "")
            if key == "citation_author":
                authors.append(node.attrs.get("content", ""))
    exact = requested
    if not requested_version:
        canonical = meta.get("og:url", "")
        exact = normalize_arxiv_id(canonical) if canonical else requested
        if not re.search(r"v\d+$", exact):
            raise PaperError("arXiv did not identify the current paper version. Retry with an explicit vN identifier.", "version_unresolved")
    errors = []
    for url in ("https://arxiv.org/html/" + exact, "https://ar5iv.labs.arxiv.org/html/" + exact):
        try:
            data, actual_url, mime = _fetch(url)
            body = data.decode("utf-8", errors="replace")
            if "/html/" not in urlparse(actual_url).path or not ("ltx_document" in body or "ltx_page_main" in body):
                raise PaperError("The host returned a landing page rather than full paper HTML.", "abstract_only")
            if not urlparse(actual_url).path.rstrip("/").endswith("/" + exact):
                raise PaperError("HTML host redirected to a different paper version.", "version_mismatch")
            return data, "html", {"canonical_url": "https://arxiv.org/abs/" + exact, "content_url": actual_url,
                                   "arxiv_id": re.sub(r"v\d+$", "", exact), "version": int(re.search(r"v(\d+)$", exact).group(1)),
                                   "authors": authors, "abstract": meta.get("citation_abstract", ""), "title": meta.get("citation_title", "")}, errors
        except Exception as exc:
            errors.append(f"Structured HTML unavailable from {urlparse(url).hostname}: {type(exc).__name__}.")
    data, actual_url, _ = _fetch("https://arxiv.org/pdf/" + exact)
    if not data.startswith(b"%PDF-"):
        raise PaperError("arXiv returned neither full paper HTML nor a PDF.", "source_unavailable")
    return data, "pdf", {"canonical_url": "https://arxiv.org/abs/" + exact, "content_url": actual_url,
                         "arxiv_id": re.sub(r"v\d+$", "", exact), "version": int(re.search(r"v(\d+)$", exact).group(1)),
                         "authors": authors, "abstract": meta.get("citation_abstract", ""), "title": meta.get("citation_title", "")}, errors


def import_paper(source, cache_dir, filename=None, content=None):
    """Import an arXiv URL/ID, local file path, or supplied bytes/text.

    ``content`` bypasses file/network reads; ``filename`` selects its format.
    Cache paths in the result are relative to ``cache_dir``. The raw source is
    immutable by content hash; importing changed bytes produces a new document.
    """
    cache = Path(cache_dir).expanduser().resolve()
    original = str(source)
    metadata, import_warnings = {}, []
    if content is not None:
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        display_name = Path(filename or "document.txt").name
        suffix = Path(display_name).suffix.lower()
        kind = {".html": "html", ".htm": "html", ".pdf": "pdf", ".md": "markdown", ".markdown": "markdown"}.get(suffix, "text")
        original = display_name
    elif "://" in original or original.lower().startswith("arxiv:") or ARXIV_PATTERN.fullmatch(original.strip()):
        data, kind, metadata, import_warnings = _arxiv_source(original)
        display_name = metadata["arxiv_id"].replace("/", "-") + "v" + str(metadata["version"]) + "." + kind
        original = metadata["canonical_url"]
    else:
        path = Path(original).expanduser()
        if not path.is_file():
            raise PaperError("The selected local document does not exist.", "file_not_found")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise PaperError("Source exceeds the 50 MB import limit.", "source_too_large")
        data = path.read_bytes()
        display_name, original = path.name, path.name
        kind = {".html": "html", ".htm": "html", ".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}.get(path.suffix.lower())
        if not kind:
            raise PaperError("Supported local formats are PDF, HTML, Markdown and UTF-8 text.", "unsupported_format")
    if len(data) > MAX_SOURCE_BYTES:
        raise PaperError("Source exceeds the 50 MB import limit.", "source_too_large")
    if not data.strip():
        raise PaperError("The source document is empty.", "empty_source")
    digest = hashlib.sha256(data).hexdigest()
    identity = hashlib.sha256((metadata.get("canonical_url", "") + ":" + digest).encode()).hexdigest()[:24]
    document_id = "doc-" + identity
    destination = cache / document_id
    destination.mkdir(parents=True, exist_ok=True)
    source_name = "source." + {"markdown": "md", "text": "txt"}.get(kind, kind)
    raw_path = destination / source_name
    if not raw_path.exists():
        raw_path.write_bytes(data)
    if kind == "html":
        title, sections, warnings, parser = _parse_html(data, metadata.get("content_url", ""))
    elif kind == "pdf":
        title, sections, warnings, parser = _parse_pdf(data)
    else:
        title, sections, warnings, parser = _parse_text(data, kind == "markdown")
    if not any(s["blocks"] for s in sections):
        raise PaperError("No readable document content was extracted.", "empty_extraction")
    source_meta = {"kind": "arxiv" if metadata.get("arxiv_id") else kind, "format": kind,
                   "original": original, "filename": display_name, "retrieved_at": _now(),
                   "sha256": digest, "cache_path": f"{document_id}/{source_name}", **{k: v for k, v in metadata.items() if k != "title"}}
    document = {"schema_version": 1, "id": document_id, "title": metadata.get("title") or title,
                "source": source_meta, "sections": sections, "warnings": import_warnings + warnings,
                "extraction": {"parser": parser, "section_count": len(sections), "block_count": sum(len(s["blocks"]) for s in sections),
                               "characters": sum(len(b["text"]) for s in sections for b in s["blocks"]),
                               "completeness": "page-text" if kind == "pdf" else "structured" if kind in {"html", "markdown"} else "plain-text"}}
    (destination / "document.json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document