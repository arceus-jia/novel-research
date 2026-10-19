"""EPUB 2/3 text extraction in spine order; never rewrites the original book."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
from urllib.parse import unquote, urlsplit
import uuid
import xml.etree.ElementTree as ET
import zipfile

from common import (NovelError, atomic_write_json, atomic_write_text, digest, file_hash,
                    load_record, read_json, require, safe_path, save_record, writer_lock)


CONVERTER_VERSION = 1
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
_OUTPUT = re.compile(r"epub_([0-9a-f]{24})\.txt\Z")
_BLOCKS = {"p", "div", "section", "article", "aside", "blockquote", "li", "tr", "pre", "dt", "dd"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _reference(base, href):
    parsed = urlsplit(href)
    require(not parsed.scheme and not parsed.netloc and not parsed.query,
            "EPUB_PATH", "EPUB resources must be local archive paths.", href)
    name = unquote(parsed.path)
    require(name and not name.startswith("/") and "\\" not in name and "\x00" not in name,
            "EPUB_PATH", "Invalid EPUB resource path.", href)
    result = posixpath.normpath(posixpath.join(posixpath.dirname(base), name))
    require(result != ".." and not result.startswith("../"), "EPUB_PATH", "Resource escapes the EPUB archive.", href)
    return result


def _xml(raw):
    # Container/package documents need no DTD; rejecting declarations also blocks entity expansion.
    require(not re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw.replace(b"\x00", b""), re.I),
            "EPUB_XML", "EPUB package XML cannot contain DTD/entity declarations.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise NovelError("EPUB_XML", "Cannot parse EPUB package XML.", str(error)) from error


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title = []
        self.headings = 0
        self.images = 0

    def _break(self):
        if self.parts and self.parts[-1] != "\n\n":
            self.parts.append("\n\n")

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        hidden = (any(item[1] for item in self.stack) or tag in {"head", "script", "style", "rt", "rp"}
                  or "hidden" in attrs or attrs.get("aria-hidden") == "true")
        if not hidden:
            if tag in _BLOCKS or re.fullmatch(r"h[1-6]", tag):
                self._break()
            if re.fullmatch(r"h[1-6]", tag):
                self.parts.append("#" * int(tag[1]) + " ")
                self.headings += 1
            if tag == "br":
                self.parts.append("\n")
            if tag == "td":
                self.parts.append("\t")
            if tag in ("img", "svg"):
                self.images += 1
            if ({"footnote", "rearnote", "endnote"} & set((attrs.get("epub:type") or "").split())
                    or attrs.get("role") in ("doc-footnote", "doc-endnote")):
                self.parts.append("[脚注] ")
        if tag not in _VOID:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag.lower()), None)
        if index is None:
            return
        hidden = self.stack[index][1]
        del self.stack[index:]
        if not hidden and (tag in _BLOCKS or re.fullmatch(r"h[1-6]", tag)):
            self._break()

    def handle_data(self, data):
        if any(tag == "title" for tag, _ in self.stack):
            self.title.append(data)
        if any(hidden for _, hidden in self.stack):
            return
        if any(tag == "pre" for tag, _ in self.stack):
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"[\t\n\r\f ]+", " ", data))

    def text(self):
        value = "".join(self.parts)
        value = re.sub(r" *\n *", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def _html(raw):
    encoding = "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        declaration = re.match(br"\s*<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", raw[:500], re.I)
        if declaration:
            encoding = declaration[1].decode("ascii")
    try:
        value = raw.decode(encoding, errors="strict")
    except (UnicodeError, LookupError) as error:
        raise NovelError("EPUB_ENCODING", "Cannot decode EPUB text resource.", encoding) from error
    parser = _Text()
    parser.feed(value)
    parser.close()
    return parser


def _extract(raw):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        with archive:
            members = archive.infolist()
            require(len(members) <= 10000 and sum(item.file_size for item in members) <= MAX_EXPANDED,
                    "EPUB_TOO_LARGE", "EPUB has too many resources or exceeds the expanded size limit.")
            names = set()
            for member in members:
                name = member.filename.rstrip("/")
                require(name and not name.startswith("/") and "\\" not in name and "\x00" not in name
                        and all(part not in ("", ".", "..") for part in name.split("/")),
                        "EPUB_PATH", "Unsafe archive member path.", member.filename)
                require(member.filename not in names, "EPUB_ZIP", "Duplicate ZIP member.", member.filename)
                names.add(member.filename)

            def read(name):
                require(name in names, "EPUB_RESOURCE", "EPUB resource is missing.", name)
                member = archive.getinfo(name)
                require(not member.flag_bits & 1, "EPUB_ENCRYPTED", "Encrypted EPUB text cannot be converted.", name)
                require(member.file_size <= MAX_MEMBER, "EPUB_TOO_LARGE", "EPUB resource exceeds size limit.", name)
                with archive.open(member) as stream:
                    value = stream.read(MAX_MEMBER + 1)
                require(len(value) <= MAX_MEMBER, "EPUB_TOO_LARGE", "EPUB resource exceeds size limit.", name)
                return value

            container = _xml(read("META-INF/container.xml"))
            roots = container.findall(".//{*}rootfile")
            require(roots, "EPUB_PACKAGE", "EPUB container has no package document.")
            package_path = _reference("", roots[0].get("full-path", ""))
            package = _xml(read(package_path))
            layout = package.findall(".//{*}meta[@property='rendition:layout']")
            require(not any((item.text or "").strip() == "pre-paginated" for item in layout),
                    "EPUB_LAYOUT", "Fixed-layout EPUB needs a separate layout/OCR workflow.")
            manifest = package.find("{*}manifest")
            spine = package.find("{*}spine")
            require(manifest is not None and spine is not None and len(spine),
                    "EPUB_PACKAGE", "EPUB requires a manifest and a nonempty spine.")
            resources = {}
            for item in manifest:
                identity = item.get("id")
                require(identity and identity not in resources, "EPUB_PACKAGE", "Missing or duplicate manifest ID.")
                resources[identity] = item
            encrypted = set()
            if "META-INF/encryption.xml" in names:
                encryption = _xml(read("META-INF/encryption.xml"))
                for item in encryption.findall(".//{*}CipherReference"):
                    encrypted.add(_reference("", item.get("URI", "")))
            warnings = []
            if len(roots) > 1:
                warnings.append("multiple_renditions: converted the first package document only")
            parts, documents, cursor, seen = [], [], 0, set()
            for reference in spine:
                identity = reference.get("idref")
                require(identity in resources, "EPUB_PACKAGE", "Spine references an unknown manifest ID.", identity)
                item = resources[identity]
                resource = _reference(package_path, item.get("href", ""))
                require(resource not in seen, "EPUB_PACKAGE", "Duplicate spine resource.", resource)
                seen.add(resource)
                require(resource not in encrypted, "EPUB_ENCRYPTED", "Encrypted EPUB text cannot be converted.", resource)
                require(resource in names, "EPUB_RESOURCE", "EPUB spine resource is missing.", resource)
                linear = reference.get("linear", "yes") != "no"
                if item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    warnings.append(f"non_text_resource: {resource} (not extracted)")
                    continue
                parser = _html(read(resource))
                value = parser.text()
                title = " ".join("".join(parser.title).split()) or posixpath.basename(resource)
                if not value:
                    warnings.append(f"empty_or_image_only: {resource} (no extractable text)")
                    continue
                if not parser.headings:
                    value = "## " + title + "\n\n" + value
                    warnings.append(f"synthetic_heading: {resource} (resource boundary, confirm chapters)")
                if not linear:
                    warnings.append(f"auxiliary_content: {resource} (linear=no, retained)")
                if "nav" in item.get("properties", "").split():
                    warnings.append(f"navigation_in_spine: {resource} (retained)")
                if parser.images:
                    warnings.append(f"images_not_extracted: {resource} ({parser.images} images)")
                value += "\n\n"
                documents.append({"resource": resource, "title": title, "linear": linear,
                                  "start": cursor, "end": cursor + len(value), "heading_count": parser.headings})
                cursor += len(value)
                parts.append(value)
            for item in resources.values():
                if (item.get("media-type") in ("application/xhtml+xml", "text/html")
                        and "nav" not in item.get("properties", "").split()):
                    href = item.get("href", "")
                    parsed = urlsplit(href)
                    if parsed.scheme or parsed.netloc:
                        warnings.append(f"external_text_resource: {href} (not fetched)")
                    else:
                        resource = _reference(package_path, href)
                        if resource not in seen:
                            warnings.append(f"outside_spine: {resource} (not extracted; check notes/appendices)")
            require(parts, "EPUB_NO_TEXT", "EPUB has no extractable text in its reading order.")
            title = package.find(".//{*}metadata/{*}title")
            creator = package.find(".//{*}metadata/{*}creator")
            return {"title": (title.text or "").strip() if title is not None else "",
                    "author": (creator.text or "").strip() if creator is not None else "",
                    "package_path": package_path, "documents": documents,
                    "warnings": warnings}, "".join(parts)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, ValueError) as error:
        raise NovelError("EPUB_ZIP", "Cannot read EPUB archive.", str(error)) from error


def _paths(root, relative):
    path = safe_path(root, relative)
    relative = path.relative_to(root).as_posix()
    identity = digest(relative)[:24]
    return path, relative, f".novel/epub/{identity}", f"sources/converted/epub_{identity}.txt"


def conversion_info(root, relative):
    """Read the current conversion pointer without requiring the original to be unchanged."""
    root = Path(root).resolve()
    _, relative, base, output = _paths(root, relative)
    pointer = safe_path(root, base + "/current.json")
    if not pointer.is_file():
        return None
    current = read_json(pointer)
    require(isinstance(current, dict) and current.get("format_version") == 1
            and isinstance(current.get("record_path"), str), "CORRUPT_RECORD", "Invalid EPUB conversion pointer.")
    record = load_record(safe_path(root, current["record_path"]))
    require(record.get("original_path") == relative and record.get("output_path") == output,
            "CORRUPT_RECORD", "EPUB conversion pointer does not match its source.")
    return dict(record, record_path=current["record_path"])


def origin_for_output(root, relative, verify=True):
    root = Path(root).resolve()
    path = safe_path(root, relative)
    if path.parent != root / "sources" / "converted":
        return None
    match = _OUTPUT.fullmatch(path.name)
    if not match:
        return None
    pointer = safe_path(root, f".novel/epub/{match[1]}/current.json")
    if not pointer.is_file():
        return None
    current = read_json(pointer)
    require(isinstance(current, dict) and isinstance(current.get("record_path"), str),
            "CORRUPT_RECORD", "Invalid EPUB conversion pointer.")
    record = load_record(safe_path(root, current["record_path"]))
    require(record.get("output_path") == path.relative_to(root).as_posix(),
            "CORRUPT_RECORD", "EPUB output path mismatch.")
    if verify:
        original = safe_path(root, record["original_path"])
        require(original.is_file() and file_hash(original) == record["original_hash"],
                "EPUB_STALE", "Original EPUB changed or is missing; run epub convert again.", record["original_path"])
        require(path.is_file() and file_hash(path) == record["output_hash"],
                "EPUB_OUTPUT_CHANGED", "Converted text was edited; preserve the edit separately before converting again.")
    return {key: record[key] for key in ("original_path", "original_hash", "output_hash")} | {"record_path": current["record_path"]}


def locations(root, origin, start, end):
    record = load_record(safe_path(root, origin["record_path"]))
    require(record.get("original_hash") == origin["original_hash"] and record.get("output_hash") == origin["output_hash"],
            "CORRUPT_RECORD", "EPUB provenance mismatch.")
    return [{"resource": item["resource"], "title": item["title"], "linear": item["linear"],
             "text_start": max(start, item["start"]), "text_end": min(end, item["end"])}
            for item in record["documents"] if item["start"] < end and start < item["end"]]


def check_conversions(root):
    root = Path(root).resolve()
    checked, errors = 0, []
    for path in safe_path(root, ".novel/epub").glob("*/*/record.json"):
        try:
            path = safe_path(root, path.relative_to(root))
            record = load_record(path)
            for name, key in (("original.epub", "original_hash"), ("text.txt", "output_hash")):
                snapshot = safe_path(root, (path.parent / name).relative_to(root))
                require(snapshot.is_file() and file_hash(snapshot) == record.get(key),
                        "CORRUPT_SNAPSHOT", "EPUB conversion snapshot hash mismatch.", str(snapshot))
            checked += 1
        except (NovelError, OSError) as error:
            errors.append({"path": str(path), "code": getattr(error, "code", "IO_ERROR"), "message": str(error)})
    return {"valid": not errors, "checked": checked, "errors": errors}


def convert(root, relative):
    root = Path(root).resolve()
    original, relative, base, output = _paths(root, relative)
    require(original.is_file() and original.suffix.lower() == ".epub", "EPUB_SOURCE", "Provide a local .epub file inside the workspace.")
    require(not original.is_relative_to(root / ".novel"), "UNSAFE_PATH", "Internal snapshots cannot be new sources.")
    with original.open("rb") as stream:
        raw = stream.read(MAX_ARCHIVE + 1)
    require(len(raw) <= MAX_ARCHIVE, "EPUB_TOO_LARGE", "EPUB exceeds the 128 MiB archive limit.")
    metadata, text = _extract(raw)
    original_hash = hashlib.sha256(raw).hexdigest()
    output_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    version = digest([CONVERTER_VERSION, original_hash])[:24]
    snapshot = base + "/" + version
    record_path = snapshot + "/record.json"
    record = dict(metadata, converter_version=CONVERTER_VERSION, original_path=relative,
                  original_hash=original_hash, output_path=output, output_hash=output_hash,
                  original_snapshot=snapshot + "/original.epub", text_snapshot=snapshot + "/text.txt")
    require(len(json.dumps(record, ensure_ascii=False).encode("utf-8")) < 7 * 1024 * 1024,
            "EPUB_TOO_LARGE", "EPUB conversion map exceeds the supported size.")
    stage = safe_path(root, ".novel/staging/epub-" + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    try:
        (stage / "original.epub").write_bytes(raw)
        atomic_write_text(stage / "text.txt", text)
        with writer_lock(root):
            require(file_hash(original) == original_hash, "SOURCE_CHANGED", "EPUB changed during conversion; retry.")
            old = conversion_info(root, relative)
            output_path = safe_path(root, output)
            if output_path.exists():
                actual = file_hash(output_path)
                allowed = {output_hash}
                if old:
                    allowed.add(old["output_hash"])
                require(actual in allowed, "EPUB_OUTPUT_CHANGED", "Converted output has user edits; preserve them separately before retrying.", output)
            destination = safe_path(root, snapshot)
            if destination.exists():
                require(load_record(destination / "record.json") == record
                        and file_hash(destination / "original.epub") == original_hash
                        and file_hash(destination / "text.txt") == output_hash,
                        "CORRUPT_RECORD", "Saved EPUB conversion snapshot is damaged.")
            else:
                save_record(stage / "record.json", record)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage, destination)
            if not output_path.exists() or file_hash(output_path) != output_hash:
                atomic_write_text(output_path, text)
            atomic_write_json(safe_path(root, base + "/current.json"), {"format_version": 1, "record_path": record_path})
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"original_path": relative, "original_hash": original_hash, "output_path": output,
            "record_path": record_path, "title": metadata["title"], "author": metadata["author"],
            "document_count": len(metadata["documents"]), "text_characters": len(text),
            "warnings": metadata["warnings"][:20], "warning_count": len(metadata["warnings"]),
            "warnings_truncated": len(metadata["warnings"]) > 20, "requires_review": bool(metadata["warnings"]),
            "documents_preview": metadata["documents"][:20], "preview_truncated": len(metadata["documents"]) > 20,
            "next": "Review conversion warnings, register output_path with sources add --kind, then prepare and verify chapter order."}
