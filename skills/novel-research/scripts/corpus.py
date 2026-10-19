"""Immutable source snapshots, literal retrieval, and exact evidence positions."""

from __future__ import annotations

import codecs
from bisect import bisect_left, bisect_right
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

import epub
from common import (
    SOURCE_KINDS, NovelError, atomic_write_json, atomic_write_text, digest, file_hash, load_record,
    read_json, require, safe_path, save_record, writer_lock,
)


MAX_SPAN = 2400
MAX_READ = 12000
MAX_RESULTS = 100
MAX_MANIFEST_BYTES = 128 * 1024 * 1024
_REVISION = re.compile(r"rev_[0-9a-f]{24}\Z")
_CHINESE = re.compile(
    r"第([〇零一二两三四五六七八九十百千万0-9]+)([章回节卷])"
    r"(?:[ \t　:：]+[^。！？!?，,；;]{0,100})?\Z"
)
_MARKDOWN = re.compile(r" {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*\Z")


def _root(root):
    root = Path(root).resolve()
    require(root.is_dir(), "WORKSPACE_NOT_FOUND", "Workspace must be an existing directory.")
    safe_path(root, ".novel")
    return root


def _source_codec(encoding):
    require(isinstance(encoding, str) and encoding and "\x00" not in encoding,
            "ENCODING_ERROR", "Encoding must be a nonempty codec name.")
    try:
        codec = codecs.lookup(encoding).name
    except LookupError as exc:
        raise NovelError("ENCODING_ERROR", f"Unknown encoding: {encoding}") from exc
    return "utf-8-sig" if codec == "utf-8" else codec


def _source_config(root):
    path = safe_path(root, "novel.json")
    if not path.exists():
        return None, None, []
    with path.open("rb") as handle:
        raw = handle.read(8 * 1024 * 1024 + 1)
    require(len(raw) <= 8 * 1024 * 1024, "INPUT_TOO_LARGE", "novel.json exceeds the supported size.")
    config = read_json(path)
    require(file_hash(path) == hashlib.sha256(raw).hexdigest(), "CONFIG_CHANGED", "novel.json changed while reading; retry.")
    require(isinstance(config, dict) and type(config.get("format_version")) is int and config["format_version"] == 1,
            "FORMAT_VERSION", "novel.json requires format_version 1.")
    require(isinstance(config.get("title", root.name), str) and isinstance(config.get("sources"), list),
            "INVALID_CONFIG", "novel.json requires a string title and an ordered sources list.")
    entries, seen = [], set()
    for source in config["sources"]:
        require(isinstance(source, dict) and isinstance(source.get("path"), str) and source["path"] and "\x00" not in source["path"],
                "INVALID_CONFIG", "Each source needs a relative path.")
        resolved = safe_path(root, source["path"])
        require(not resolved.is_relative_to(root / ".novel"), "UNSAFE_PATH", "Generated workspace files cannot be sources.")
        require(resolved not in seen, "DUPLICATE_SOURCE", f"Duplicate source: {source['path']}")
        seen.add(resolved)
        require(source.get("kind", "text") in SOURCE_KINDS, "SOURCE_KIND", "Unknown registered source kind.")
        _source_codec(source.get("encoding", "utf-8-sig"))
        entries.append((source, resolved))
    return config, raw, entries


def _source_identity(path, info=None):
    info = info if info is not None else path.stat()
    return (info.st_dev, info.st_ino) if info.st_ino else path


def _generated_source(root, path):
    parts = path.relative_to(root).parts
    if parts[0] == ".novel" or (parts[:2] == ("analysis", "views") and len(parts) == 3
                               and parts[2] in ("chapters.md", "characters.md", "questions.md", "library.md")):
        return True
    if len(parts) == 3 and parts[0] == "research" and parts[2] == "report.md":
        records = safe_path(root, f"research/{parts[1]}/records")
        return records.is_dir() and any(records.glob("*.json"))
    return False


def scan_sources(root, limit=100, offset=0, new_only=False):
    """Inventory files without inferring source meaning or narrative order."""
    _page(limit, offset, 1000)
    require(type(new_only) is bool, "INVALID_INPUT", "new_only must be a boolean.")
    root = _root(root)
    config, _, entries = _source_config(root)
    files, missing, errors, skipped = {}, [], [], []

    def error(path, exc):
        path = Path(path)
        label = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        errors.append({"path": label, "message": str(exc)})

    for source, path in entries:
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                missing.append(source["path"])
                continue
            identity = _source_identity(path, info)
            require(identity not in files, "DUPLICATE_SOURCE", f"Duplicate source: {source['path']}")
            files[identity] = {"path": source["path"], "size": info.st_size, "registered": True,
                               "kind": source.get("kind", "text")}
        except FileNotFoundError:
            missing.append(source["path"])
        except OSError as exc:
            error(root / source["path"], exc)

    for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False,
                                                     onerror=lambda exc: error(exc.filename or root, exc)):
        directory = Path(directory)
        retained = []
        for name in sorted(directories + filenames):
            path = directory / name
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in (0xA0000003, 0xA000000C):
                    skipped.append(path.relative_to(root).as_posix())
                    continue
                if name.startswith(".") or getattr(info, "st_file_attributes", 0) & 2:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    retained.append(name)
                elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in (".txt", ".md", ".markdown", ".epub"):
                    path = safe_path(root, path.relative_to(root))
                    if _generated_source(root, path):
                        continue
                    identity = _source_identity(path)
                    if path.suffix.lower() == ".epub":
                        conversion = epub.conversion_info(root, path.relative_to(root))
                        if conversion is not None:
                            derived = safe_path(root, conversion["output_path"])
                            if (derived.is_file() and _source_identity(derived) in files
                                    and files[_source_identity(derived)]["registered"]
                                    and file_hash(path) == conversion["original_hash"]
                                    and file_hash(derived) == conversion["output_hash"]):
                                continue
                        files.setdefault(identity, {"path": path.relative_to(root).as_posix(),
                                                    "size": info.st_size, "registered": False,
                                                    "format": "epub", "requires_conversion": True})
                        continue
                    # Only our recorded derivatives are skipped; user files in converted/ remain visible.
                    if identity not in files and epub.origin_for_output(root, path.relative_to(root), verify=False):
                        continue
                    files.setdefault(identity, {"path": path.relative_to(root).as_posix(),
                                                "size": info.st_size, "registered": False})
            except (OSError, NovelError) as exc:
                error(path, exc)
        directories[:] = sorted(retained)
    inventory = sorted(files.values(), key=lambda item: item["path"])
    new_count = sum(not item["registered"] for item in inventory)
    if new_only:
        inventory = [item for item in inventory if not item["registered"]]
    total = len(inventory)
    return {"config_exists": config is not None, "registered_count": len(entries),
            "new_count": new_count,
            "files": inventory[offset:offset + limit], "missing": sorted(missing),
            "errors": errors, "skipped_symlinks": sorted(skipped), "scan_complete": not errors,
            "total": total, "offset": offset, "next_offset": offset + limit if offset + limit < total else None,
            "truncated": offset + limit < total}


def register_sources(root, paths, kind, encoding="utf-8", title=None, before=None, after=None):
    """Register reviewed files; preparing a snapshot remains a separate action."""
    root = _root(root)
    require(isinstance(paths, list) and paths and all(isinstance(path, str) and path and "\x00" not in path for path in paths),
            "INVALID_INPUT", "Provide a nonempty ordered list of relative source paths.")
    require(kind in SOURCE_KINDS, "SOURCE_KIND", "Choose an explicit known source kind.")
    require(before is None or after is None, "INVALID_INPUT", "before and after are mutually exclusive.")
    require(title is None or isinstance(title, str) and bool(title.strip()), "INVALID_INPUT", "Title must be a nonempty string.")
    codec = _source_codec(encoding)
    config, original, entries = _source_config(root)
    require(config is None or title is None or config.get("title", root.name) == title,
            "SOURCE_CONFLICT", "Existing title is preserved; title only initializes a new book.")
    existing = {}
    for source, path in entries:
        try:
            identity = _source_identity(path)
        except FileNotFoundError:
            identity = path
        require(identity not in existing, "DUPLICATE_SOURCE", "Registered sources refer to the same file.")
        existing[identity] = source
    insertion = len(entries)
    anchor = before if before is not None else after
    if anchor is not None:
        require(isinstance(anchor, str) and anchor and "\x00" not in anchor,
                "INVALID_INPUT", "Insertion anchor must be a registered relative path.")
        anchor_path = safe_path(root, anchor)
        try:
            anchor_id = _source_identity(anchor_path)
        except FileNotFoundError:
            anchor_id = anchor_path
        require(anchor_id in existing, "UNKNOWN_SOURCE", "Insertion anchor is not registered.")
        insertion = next(index for index, (source, _) in enumerate(entries) if source is existing[anchor_id])
        if after is not None:
            insertion += 1
    additions, already, checked, seen = [], [], [], set()
    for relative in paths:
        path = safe_path(root, relative)
        require(path.is_file(), "SOURCE_NOT_FOUND", f"Source does not exist: {relative}")
        require(path.suffix.lower() in (".txt", ".md", ".markdown"), "SOURCE_FORMAT", "Register TXT/Markdown; use epub convert before registering an EPUB.")
        require(not _generated_source(root, path), "UNSAFE_PATH", "Generated workspace files cannot be sources.")
        identity = _source_identity(path)
        if identity in seen:
            continue
        seen.add(identity)
        source = existing.get(identity)
        if source is not None:
            require(source.get("kind", "text") == kind and _source_codec(source.get("encoding", "utf-8-sig")) == codec,
                    "SOURCE_CONFLICT", "Source kind or encoding conflicts with its existing registration.", {"path": source["path"]})
            already.append(source["path"])
        raw = path.read_bytes()
        try:
            raw.decode(codec, errors="strict")
        except (UnicodeError, LookupError) as exc:
            raise NovelError("ENCODING_ERROR", f"Cannot decode {relative} as {encoding}; select the actual encoding.") from exc
        checked.append((relative, hashlib.sha256(raw).hexdigest()))
        if source is None:
            additions.append({"path": Path(relative).as_posix(), "kind": kind, "encoding": codec})
    updated = dict(config) if config is not None else {"format_version": 1, "title": title or root.name}
    old_sources = config["sources"] if config is not None else []
    updated["sources"] = old_sources[:insertion] + additions + old_sources[insertion:]
    config_path = safe_path(root, "novel.json")
    backup_path = None
    with writer_lock(root):
        _, current_raw, _ = _source_config(root)
        require(current_raw == original, "CONFIG_CHANGED", "novel.json changed during registration; retry.")
        for relative, expected in checked:
            try:
                current_hash = file_hash(safe_path(root, relative))
            except OSError as exc:
                raise NovelError("SOURCE_CHANGED", "Source changed during registration; retry.", {"path": relative}) from exc
            require(current_hash == expected, "SOURCE_CHANGED", "Source changed during registration; retry.", {"path": relative})
        if additions:
            if original is not None:
                backup_path = safe_path(root, f".novel/config-backups/{hashlib.sha256(original).hexdigest()}.json")
                if backup_path.exists():
                    require(backup_path.read_bytes() == original, "CORRUPT_RECORD", "Existing configuration backup differs from its hash.")
                else:
                    atomic_write_text(backup_path, original.decode("utf-8"))
            atomic_write_json(config_path, updated)
    prepare_required = True
    try:
        prepare_required = bool(source_changes(root, load_manifest(root, check_sources=False)))
    except (NovelError, OSError):
        pass
    result = {"config_path": str(config_path), "added": [source["path"] for source in additions],
              "already_registered": already, "prepare_required": prepare_required}
    if backup_path is not None:
        result["backup_path"] = str(backup_path)
    return result


def _config(root, sources=None, title=None, encoding="utf-8"):
    path = safe_path(root, "novel.json")
    if path.exists():
        config = read_json(path)
        require(sources is None, "CONFIG_EXISTS", "Edit novel.json to change the ordered sources.")
    else:
        require(isinstance(sources, list) and sources, "CONFIG_REQUIRED",
                "Create novel.json or provide an explicit ordered source list.")
        config = {"format_version": 1, "title": title or root.name, "sources": []}
        for source in sources:
            config["sources"].append(
                {"path": source, "kind": "text", "encoding": encoding}
                if isinstance(source, str) else source
            )
    require(isinstance(config, dict) and type(config.get("format_version")) is int and config["format_version"] == 1,
            "FORMAT_VERSION", "novel.json requires format_version 1.")
    require(isinstance(config.get("title", root.name), str), "INVALID_CONFIG", "Title must be a string.")
    require(isinstance(config.get("sources"), list) and config["sources"],
            "INVALID_CONFIG", "sources must be a nonempty ordered list.")
    seen = set()
    normalized = []
    for source in config["sources"]:
        require(isinstance(source, dict), "INVALID_CONFIG", "Each source must be an object.")
        relative = source.get("path")
        require(isinstance(relative, str) and relative, "INVALID_CONFIG", "Source path must be a string.")
        path = safe_path(root, relative)
        require(path.is_file(), "SOURCE_NOT_FOUND", f"Source does not exist: {relative}")
        require(path.suffix.lower() in (".txt", ".md", ".markdown"),
                "SOURCE_FORMAT", "Register TXT/Markdown; use epub convert before registering an EPUB.")
        require(not path.is_relative_to(root / ".novel"), "UNSAFE_PATH",
                "Generated workspace files cannot be sources.")
        require(path not in seen, "DUPLICATE_SOURCE", f"Duplicate source: {relative}")
        seen.add(path)
        kind = source.get("kind", "text")
        require(kind in SOURCE_KINDS, "SOURCE_KIND", f"Unknown source kind: {kind}")
        selected_encoding = source.get("encoding", "utf-8-sig")
        require(isinstance(selected_encoding, str), "INVALID_CONFIG", "Encoding must be a string.")
        try:
            codec = codecs.lookup(selected_encoding).name
        except LookupError as exc:
            raise NovelError("ENCODING_ERROR", f"Unknown encoding: {selected_encoding}") from exc
        # UTF-8 BOM is transport metadata, including when UTF-8 is explicitly selected.
        if codec == "utf-8":
            codec = "utf-8-sig"
        item = {"path": Path(relative).as_posix(), "kind": kind, "encoding": codec}
        origin = epub.origin_for_output(root, relative)
        if origin is not None:
            item["epub_origin"] = origin
        if "chapters" in source:
            item["chapters"] = source["chapters"]
        normalized.append(item)
    return config, normalized, not (root / "novel.json").exists()


def _heading_units(text, source):
    """Return exact source ranges; unsure ranges are retained for retrieval."""
    headings = []
    position = 0
    markdown = []
    for line in text.splitlines(keepends=True):
        value = line.rstrip("\r\n")
        match = _MARKDOWN.fullmatch(value)
        title = match.group(2).strip() if match else value.strip()
        chinese = _CHINESE.fullmatch(title)
        if chinese:
            headings.append((position, title, chinese.group(1) + chinese.group(2)))
        if match:
            markdown.append((position, title, len(match.group(1))))
        position += len(line)
    if not headings and markdown:
        levels = {item[2] for item in markdown}
        level = min(levels - {1}) if levels - {1} else 1
        headings = [(start, title, None) for start, title, depth in markdown if depth == level]
    if "chapters" in source:
        overrides = source["chapters"]
        require(isinstance(overrides, list) and overrides, "INVALID_CHAPTERS",
                "Explicit chapters must be a nonempty list of title/start/end ranges.")
        units = []
        cursor = 0
        for chapter in overrides:
            require(isinstance(chapter, dict) and isinstance(chapter.get("title"), str),
                    "INVALID_CHAPTERS", "Each explicit chapter needs a title.")
            start, end = chapter.get("start"), chapter.get("end")
            require(type(start) is int and type(end) is int and start == cursor and start < end <= len(text),
                    "INVALID_CHAPTERS", "Explicit chapter ranges must cover the source in order without gaps.")
            units.append((start, end, chapter["title"], True, None))
            cursor = end
        require(cursor == len(text), "INVALID_CHAPTERS", "Explicit chapters must cover the entire source.")
        return units
    if not headings:
        return [(0, len(text), f"未确认章节（{source['path']}）", False, None)]
    units = []
    if headings[0][0] > 0:
        end = headings[0][0]
        if text[:end].strip():
            units.append((0, end, f"章前文字（{source['path']}）", False, None))
        else:
            headings[0] = (0, headings[0][1], headings[0][2])
    for index, (start, title, number) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        units.append((start, end, title, True, number))
    return units


def _span_ranges(text, start, end):
    cursor = start
    for boundary in re.finditer(r"\n[ \t\r]*\n", text[start:end]):
        paragraph_end = start + boundary.end()
        while cursor < paragraph_end:
            stop = min(cursor + MAX_SPAN, paragraph_end)
            yield cursor, stop
            cursor = stop
    while cursor < end:
        stop = min(cursor + MAX_SPAN, end)
        yield cursor, stop
        cursor = stop


def _build_units(sources, texts):
    chapters, spans, review = [], [], []
    occurrences = {}
    numbering, titles = {}, {}
    for source in sources:
        text = texts[source["id"]]
        units = _heading_units(text, source) if source["kind"] == "text" else [(0, len(text), None, True, None)]
        for start, end, title, confirmed, number in units:
            content_hash = hashlib.sha256(text[start:end].encode("utf-8")).hexdigest()
            key = (source["id"], content_hash)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            unit_id = digest([source["id"], content_hash, occurrence])[:24]
            chapter = None
            if source["kind"] == "text":
                chapter = {"id": "ch_" + unit_id, "ordinal": len(chapters) + 1,
                           "title": title, "source_id": source["id"], "content_hash": content_hash,
                           "start": start, "end": end, "confirmed": confirmed}
                chapters.append(chapter)
                if number is not None:
                    numbering.setdefault(number, []).append(chapter)
                if confirmed and "chapters" not in source:
                    titles.setdefault(title, []).append(chapter)
                if not confirmed:
                    review.append({"source_id": source["id"], "chapter_id": chapter["id"],
                                   "reason": "unconfirmed_chapter", "title": title})
            for span_start, span_end in _span_ranges(text, start, end):
                spans.append({"span_id": "sp_" + digest([unit_id, span_start - start, span_end - start])[:24],
                              "chapter_id": chapter["id"] if chapter else None,
                              "chapter_ordinal": chapter["ordinal"] if chapter else None,
                              "source_id": source["id"], "source_kind": source["kind"],
                              "unit_hash": content_hash, "start": span_start, "end": span_end})
    duplicate_ids = set()
    for duplicates in list(numbering.values()) + list(titles.values()):
        if len(duplicates) > 1:
            for chapter in duplicates:
                if chapter["id"] in duplicate_ids:
                    continue
                duplicate_ids.add(chapter["id"])
                chapter["confirmed"] = False
                review.append({"source_id": chapter["source_id"], "chapter_id": chapter["id"],
                               "reason": "duplicate_chapter_heading", "title": chapter["title"]})
    return chapters, spans, review


def prepare(root, sources=None, title=None, encoding="utf-8"):
    root = _root(root)
    config, selected_sources, new_config = _config(root, sources, title, encoding)
    source_records, texts, originals = [], {}, {}
    for source in selected_sources:
        path = safe_path(root, source["path"])
        raw = path.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode(source["encoding"], errors="strict")
        except (UnicodeDecodeError, LookupError) as exc:
            raise NovelError("ENCODING_ERROR", f"Cannot decode {source['path']} as {source['encoding']}; select the actual encoding.") from exc
        source_id = "src_" + digest([source["path"], source["kind"]])[:20]
        record = dict(source, id=source_id, hash=source_hash,
                      snapshot_path=f"sources/{source_id}.bin", text_path=f"texts/{source_id}.txt")
        source_records.append(record)
        texts[source_id], originals[source_id] = text, raw
    chapters, spans, review = _build_units(source_records, texts)
    revision = "rev_" + digest({"parser_version": 1, "config_hash": digest(config), "title": config.get("title", root.name),
                                "sources": source_records, "chapters": chapters})[:24]
    stage = safe_path(root, f".novel/staging/{uuid.uuid4().hex}")
    stage.mkdir(parents=True)
    try:
        for source in source_records:
            raw_path = stage / source["snapshot_path"]
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(originals[source["id"]])
            text_path = stage / source["text_path"]
            text_path.parent.mkdir(parents=True, exist_ok=True)
            with text_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(texts[source["id"]])
            require(file_hash(raw_path) == source["hash"], "SNAPSHOT_ERROR", "Staged source hash mismatch.")
        with writer_lock(root):
            for source in source_records:
                require(file_hash(safe_path(root, source["path"])) == source["hash"],
                        "SOURCE_CHANGED", "Sources changed while preparing; run prepare again.", {"path": source["path"]})
                if "epub_origin" in source:
                    require(epub.origin_for_output(root, source["path"]) == source["epub_origin"],
                            "SOURCE_CHANGED", "EPUB conversion changed while preparing; retry.")
            config_path = safe_path(root, "novel.json")
            if new_config:
                require(not config_path.exists(), "SOURCE_CHANGED", "novel.json was created during prepare; retry.")
            else:
                require(read_json(config_path) == config, "SOURCE_CHANGED", "novel.json changed during prepare; retry.")
            identity_path = safe_path(root, ".novel/workspace.json")
            if identity_path.exists():
                identity = read_json(identity_path)
                require(isinstance(identity, dict) and identity.get("format_version") == 1
                        and isinstance(identity.get("workspace_id"), str),
                        "FORMAT_VERSION", "Unknown workspace identity format; migration is required.")
            else:
                identity = {"format_version": 1, "workspace_id": "ws_" + uuid.uuid4().hex}
                atomic_write_json(identity_path, identity)
            manifest = {"format_version": 1, "workspace_id": identity["workspace_id"],
                        "revision": revision, "title": config.get("title", root.name),
                        "sources": source_records, "chapters": chapters, "spans": spans,
                        "needs_review": review, "config_hash": digest(config)}
            destination = safe_path(root, f".novel/snapshots/{revision}")
            if destination.exists():
                require(load_record(destination / "manifest.json", max_bytes=MAX_MANIFEST_BYTES) == manifest,
                        "SNAPSHOT_CONFLICT", "An existing snapshot has incompatible data.")
                for source in source_records:
                    _text(root, manifest, source)
            else:
                save_record(stage / "manifest.json", manifest)
                require((stage / "manifest.json").stat().st_size <= MAX_MANIFEST_BYTES,
                        "INPUT_TOO_LARGE", "Source manifest exceeds the supported size; active version was preserved.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(stage, destination)
            if new_config:
                atomic_write_json(config_path, config)
            atomic_write_json(safe_path(root, ".novel/active.json"),
                              {"format_version": 1, "revision": revision})
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return dict(manifest, chapter_count=len(chapters),
                preview=[{key: chapter[key] for key in ("id", "ordinal", "title", "confirmed")}
                         for chapter in chapters[:20]])


def source_changes(root, manifest):
    root = _root(root)
    changes = []
    config_path = safe_path(root, "novel.json")
    if not config_path.exists() or digest(read_json(config_path)) != manifest.get("config_hash"):
        changes.append({"path": "novel.json", "reason": "config_changed"})
    for source in manifest["sources"]:
        path = safe_path(root, source["path"])
        if not path.is_file():
            changes.append({"path": source["path"], "reason": "missing"})
        elif file_hash(path) != source["hash"]:
            changes.append({"path": source["path"], "reason": "content_changed"})
        if "epub_origin" in source:
            origin = source["epub_origin"]
            original = safe_path(root, origin["original_path"])
            if not original.is_file():
                changes.append({"path": origin["original_path"], "reason": "epub_missing"})
            elif file_hash(original) != origin["original_hash"]:
                changes.append({"path": origin["original_path"], "reason": "epub_changed"})
    return changes


def load_manifest(root, revision=None, check_sources=True):
    root = _root(root)
    historical = revision is not None
    if revision is None:
        active_path = safe_path(root, ".novel/active.json")
        require(active_path.is_file(), "NOT_PREPARED", "Run prepare before using the corpus.")
        active = read_json(active_path)
        require(isinstance(active, dict) and active.get("format_version") == 1,
                "FORMAT_VERSION", "Unknown active manifest format; migration is required.")
        revision = active.get("revision")
    require(isinstance(revision, str) and _REVISION.fullmatch(revision),
            "INVALID_REVISION", "Invalid source revision.")
    path = safe_path(root, f".novel/snapshots/{revision}/manifest.json")
    require(path.is_file(), "UNKNOWN_REVISION", f"Unknown source revision: {revision}")
    manifest = load_record(path, max_bytes=MAX_MANIFEST_BYTES)
    require(isinstance(manifest, dict) and manifest.get("format_version") == 1,
            "FORMAT_VERSION", "Unknown source manifest format; migration is required.")
    require(manifest.get("revision") == revision, "CORRUPT_RECORD", "Manifest revision mismatch.")
    if check_sources and not historical:
        changes = source_changes(root, manifest)
        require(not changes, "SOURCE_CHANGED", "Sources changed; prepare a new version.", changes)
    return manifest


def _text(root, manifest, source):
    path = safe_path(_root(root), f".novel/snapshots/{manifest['revision']}/{source['text_path']}")
    raw_path = safe_path(_root(root), f".novel/snapshots/{manifest['revision']}/{source['snapshot_path']}")
    require(raw_path.is_file() and file_hash(raw_path) == source["hash"],
            "CORRUPT_SNAPSHOT", "Source snapshot hash mismatch.")
    require(path.is_file(), "CORRUPT_SNAPSHOT", "Decoded snapshot is missing.")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            value = handle.read()
        require(value == raw_path.read_bytes().decode(source["encoding"], errors="strict"),
                "CORRUPT_SNAPSHOT", "Decoded snapshot does not match the original source.")
        return value
    except UnicodeError as exc:
        raise NovelError("CORRUPT_SNAPSHOT", "Snapshot cannot be decoded.") from exc


def _filters(kinds, chapter_from, chapter_to):
    if kinds is None:
        kinds = ["text"]
    require(isinstance(kinds, (list, tuple, set)) and kinds and all(kind in SOURCE_KINDS for kind in kinds),
            "SOURCE_KIND", "Choose one or more known source kinds.")
    for value in (chapter_from, chapter_to):
        require(value is None or type(value) is int and value >= 1,
                "INVALID_RANGE", "Chapter bounds must be positive integers.")
    require(chapter_from is None or chapter_to is None or chapter_from <= chapter_to,
            "INVALID_RANGE", "Chapter range is reversed.")
    return set(kinds)


def _in_scope(span, kinds, chapter_from, chapter_to):
    ordinal = span["chapter_ordinal"]
    return (span["source_kind"] in kinds
            and (chapter_from is None or ordinal is not None and ordinal >= chapter_from)
            and (chapter_to is None or ordinal is not None and ordinal <= chapter_to))


def iter_spans(root, manifest=None, kinds=None, chapter_from=None, chapter_to=None):
    root = _root(root)
    manifest = manifest if manifest is not None else load_manifest(root)
    selected = _filters(kinds, chapter_from, chapter_to)
    texts = {}
    sources = {source["id"]: source for source in manifest["sources"]}
    for span in manifest["spans"]:
        if not _in_scope(span, selected, chapter_from, chapter_to):
            continue
        source_id = span["source_id"]
        if source_id not in texts:
            texts[source_id] = _text(root, manifest, sources[source_id])
        text = texts[source_id]
        require(0 <= span["start"] < span["end"] <= len(text),
                "CORRUPT_SNAPSHOT", "Span range is outside its source.")
        yield dict(span, text=text[span["start"]:span["end"]], source_revision=manifest["revision"])


def get_span(root, span_id, revision=None):
    manifest = load_manifest(root, revision)
    require(isinstance(span_id, str), "UNKNOWN_SPAN", "Span ID must be a string.")
    span = next((span for span in manifest["spans"] if span["span_id"] == span_id), None)
    require(span is not None, "UNKNOWN_SPAN", "Span is unknown or belongs to a different source version.")
    source = next(source for source in manifest["sources"] if source["id"] == span["source_id"])
    text = _text(root, manifest, source)
    require(0 <= span["start"] < span["end"] <= len(text), "CORRUPT_SNAPSHOT", "Invalid span range.")
    return dict(span, text=text[span["start"]:span["end"]], source_revision=manifest["revision"],
                **_provenance(root, source, span["start"], span["end"]))


def _provenance(root, source, start, end):
    origin = source.get("epub_origin")
    if origin is None:
        return {}
    return {"epub_origin": origin, "epub_locations": epub.locations(root, origin, start, end)}


def _page(limit, offset, maximum):
    require(type(limit) is int and 1 <= limit <= maximum, "INVALID_LIMIT", f"limit must be between 1 and {maximum}.")
    require(type(offset) is int and offset >= 0, "INVALID_OFFSET", "offset must be a nonnegative integer.")


def read(root, span_id=None, chapter_id=None, limit=6000, offset=0, revision=None):
    _page(limit, offset, MAX_READ)
    require((span_id is None) != (chapter_id is None), "READ_SELECTOR", "Choose exactly one span_id or chapter_id.")
    manifest = load_manifest(root, revision)
    if span_id is not None:
        item = get_span(root, span_id, manifest["revision"])
        start, end, source_id = item["start"], item["end"], item["source_id"]
        spans = [item]
    else:
        chapter = next((chapter for chapter in manifest["chapters"] if chapter["id"] == chapter_id), None)
        require(chapter is not None, "UNKNOWN_CHAPTER", "Chapter is unknown or belongs to another source version.")
        start, end, source_id = chapter["start"], chapter["end"], chapter["source_id"]
        spans = [span for span in manifest["spans"] if span["chapter_id"] == chapter_id]
    source = next(source for source in manifest["sources"] if source["id"] == source_id)
    source_text = _text(root, manifest, source)
    require(offset <= end - start, "INVALID_OFFSET", "Read offset is outside the selected text.")
    actual_start, actual_end = start + offset, min(start + offset + limit, end)
    visible_spans = []
    for span in spans:
        left, right = max(actual_start, span["start"]), min(actual_end, span["end"])
        if left < right:
            visible_spans.append(dict(span, text=source_text[left:right], text_start=left, text_end=right,
                                      source_revision=manifest["revision"]))
    return {"source_revision": manifest["revision"], "span_id": span_id, "chapter_id": chapter_id,
            "source_id": source_id, "source_kind": source["kind"], "text": source_text[actual_start:actual_end],
            "spans": visible_spans, "offset": offset, "total": end - start,
            "truncated": actual_end < end, "next_offset": actual_end - start if actual_end < end else None,
            **_provenance(root, source, actual_start, actual_end)}


def _positions(text, query):
    position = text.find(query)
    while position >= 0:
        yield position
        position = text.find(query, position + 1)


def search(root, queries, kinds=None, chapter_from=None, chapter_to=None, limit=20, offset=0, revision=None):
    _page(limit, offset, MAX_RESULTS)
    if isinstance(queries, str):
        queries = [queries]
    require(isinstance(queries, list) and 1 <= len(queries) <= 20
            and all(isinstance(query, str) and 0 < len(query) <= MAX_SPAN for query in queries),
            "INVALID_QUERY", f"Use 1–20 nonempty literal queries, each at most {MAX_SPAN} characters.")
    require(len(set(queries)) == len(queries), "INVALID_QUERY", "Queries must be unique.")
    selected = _filters(kinds, chapter_from, chapter_to)
    root, manifest = _root(root), load_manifest(root, revision)
    counts, matches, total = {query: 0 for query in queries}, [], 0
    for source in manifest["sources"]:
        if source["kind"] not in selected:
            continue
        text = _text(root, manifest, source)
        source_spans = [span for span in manifest["spans"] if span["source_id"] == source["id"]]
        starts = [span["start"] for span in source_spans]
        ends = [span["end"] for span in source_spans]
        for query in queries:
            for start in _positions(text, query):
                end = start + len(query)
                overlapping = source_spans[bisect_right(ends, start):bisect_left(starts, end)]
                if not overlapping or not all(_in_scope(span, selected, chapter_from, chapter_to) for span in overlapping):
                    continue
                counts[query] += 1
                if offset <= total < offset + limit:
                    first = overlapping[0]
                    snippet_start = max(0, start - 60)
                    snippet_end = min(len(text), end + 60, snippet_start + 240)
                    matches.append({"query": query, "source_id": source["id"], "source_kind": source["kind"],
                                    "chapter_id": first["chapter_id"], "chapter_ordinal": first["chapter_ordinal"],
                                    "span_id": first["span_id"] if len(overlapping) == 1 else None,
                                    "span_ids": [span["span_id"] for span in overlapping],
                                    "start": start, "end": end, "snippet": text[snippet_start:snippet_end],
                                    "snippet_start": snippet_start, "snippet_end": snippet_end,
                                    **_provenance(root, source, start, end)})
                total += 1
    return {"source_revision": manifest["revision"], "queries": queries,
            "scope": {"kinds": sorted(selected), "chapter_from": chapter_from, "chapter_to": chapter_to},
            "counts": counts, "total": total, "matches": matches, "offset": offset,
            "next_offset": offset + len(matches) if offset + len(matches) < total else None,
            "truncated": offset + len(matches) < total}


def resolve_evidence(root, evidence, revision=None, allowed_span_ids=None):
    require(isinstance(evidence, list), "INVALID_EVIDENCE", "Evidence must be a list.")
    manifest = load_manifest(root, revision)
    allowed = set(allowed_span_ids) if allowed_span_ids is not None else None
    spans = {span["span_id"]: span for span in manifest["spans"]}
    sources = {source["id"]: source for source in manifest["sources"]}
    texts, resolved = {}, []
    for item in evidence:
        require(isinstance(item, dict) and set(item) == {"span_id", "quote"},
                "INVALID_EVIDENCE", "Each evidence entry must contain exactly span_id and quote.")
        span_id, quote = item["span_id"], item["quote"]
        require(isinstance(span_id, str) and isinstance(quote, str) and 0 < len(quote) <= MAX_SPAN,
                "INVALID_EVIDENCE", "Evidence needs a span ID and a nonempty bounded quote.")
        require(span_id in spans, "UNKNOWN_SPAN", "Evidence span is unknown or belongs to a different version.")
        require(allowed is None or span_id in allowed, "EVIDENCE_OUT_OF_RANGE", "Evidence span is outside the allowed input.")
        span = spans[span_id]
        source_id = span["source_id"]
        if source_id not in texts:
            texts[source_id] = _text(root, manifest, sources[source_id])
        text = texts[source_id]
        require(0 <= span["start"] < span["end"] <= len(text), "CORRUPT_SNAPSHOT", "Invalid evidence span range.")
        candidates = list(_positions(text[span["start"]:span["end"]], quote))
        require(candidates, "QUOTE_NOT_FOUND", "Quote does not exactly match text within this span.")
        require(len(candidates) == 1, "AMBIGUOUS_QUOTE", "Quote occurs more than once; include more surrounding text.",
                {"positions": [{"start": span["start"] + position, "end": span["start"] + position + len(quote)}
                               for position in candidates[:20]], "total": len(candidates)})
        start = span["start"] + candidates[0]
        resolved.append({"source_revision": manifest["revision"], "span_id": span_id,
                         "source_id": source_id, "source_kind": span["source_kind"],
                         "chapter_id": span["chapter_id"], "chapter_ordinal": span["chapter_ordinal"],
                         "unit_hash": span["unit_hash"], "start": start, "end": start + len(quote), "quote": quote,
                         **_provenance(root, sources[source_id], start, start + len(quote))})
    return resolved
