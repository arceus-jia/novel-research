"""Cross-book workspaces reference prepared books without copying their texts."""

import os
from pathlib import Path
import re
import uuid

import corpus
from common import (NovelError, atomic_write_json, atomic_write_text, bounded_list,
                    bounded_text, check_keys, digest, load_record, read_json,
                    require, safe_path, save_record, valid_id, writer_lock)


_REVISION = re.compile(r"topic_rev_[0-9a-f]{24}\Z")


def is_topic(root):
    return safe_path(root, "topic.json").is_file()


def _registry(root):
    config = read_json(safe_path(root, "topic.json"))
    require(isinstance(config, dict) and type(config.get("format_version")) is int
            and config["format_version"] == 1 and config.get("workspace_type") == "topic",
            "FORMAT_VERSION", "跨书专题配置格式不受支持。")
    check_keys(config, ("format_version", "workspace_type", "workspace_id", "title", "books"))
    valid_id(config.get("workspace_id"), "workspace_id")
    bounded_text(config.get("title"), "title", 300)
    books = bounded_list(config.get("books"), "books", 100)
    require(len(books) >= 2, "INVALID_INPUT", "跨书专题至少需要两个独立的书籍工作区。")
    ids, identities = set(), set()
    for book in books:
        check_keys(book, ("id", "workspace_id", "path"))
        valid_id(book["id"], "book_id")
        valid_id(book["workspace_id"], "workspace_id")
        bounded_text(book["path"], "book.path", 4000)
        require(book["id"] not in ids and book["workspace_id"] not in identities,
                "INVALID_INPUT", "专题中的书籍身份重复。")
        ids.add(book["id"])
        identities.add(book["workspace_id"])
    return config


def _book_path(root, path):
    root = Path(root).resolve()
    location = Path(path).expanduser()
    location = (root / location).resolve() if not location.is_absolute() else location.resolve()
    require(location != root and location not in root.parents,
            "DIRECTORY_CONFLICT", "专题目录应位于各书籍工作区之外。")
    if root in location.parents:
        require(location.relative_to(root).parts[0] not in {".novel", "research", "notes", "analysis"},
                "DIRECTORY_CONFLICT", "书籍目录不能占用专题的记录目录。")
    require(not is_topic(location), "INVALID_INPUT", "参与跨书研究的来源应是书籍工作区。")
    return location


def _member_root(root, member):
    config = _registry(root)
    registered = next((book for book in config["books"] if book["id"] == member["id"]), None)
    require(registered is not None and registered["workspace_id"] == member["workspace_id"],
            "UNKNOWN_BOOK", "引文书籍不属于此专题。")
    location = _book_path(root, registered["path"])
    current = corpus.load_manifest(location, check_sources=False)
    require(current["workspace_id"] == member["workspace_id"],
            "workspace_mismatch", "登记路径中的书籍身份发生变化，请核对原工作区。")
    return location


def _revision(manifest):
    return "topic_rev_" + digest({key: manifest[key] for key in
                                 ("workspace_id", "title", "books", "source_changes")})[:24]


def _current(root, config):
    import memory

    books, changes = [], []
    for book in config["books"]:
        location = _member_root(root, book)
        manifest = corpus.load_manifest(location, check_sources=False)
        corrections = [note for note in memory._notes(location, book["workspace_id"])
                       if note["type"] in {"correction", "withdrawal"}]
        books.append({"id": book["id"], "workspace_id": book["workspace_id"],
                      "title": manifest["title"], "source_revision": manifest["revision"],
                      "corrections_hash": digest(corrections)})
        pending = corpus.source_changes(location, manifest)
        if pending:
            changes.append({"book_id": book["id"], "title": manifest["title"], "changes": pending})
    result = {"format_version": 1, "workspace_type": "topic", "workspace_id": config["workspace_id"],
              "title": config["title"], "books": books, "source_changes": changes}
    result["revision"] = _revision(result)
    return result


def load_manifest(root, revision=None, check_sources=True):
    config = _registry(root)
    if revision is not None:
        require(isinstance(revision, str) and _REVISION.fullmatch(revision),
                "INVALID_REVISION", "跨书引用需要专题来源版本。")
        path = safe_path(root, f".novel/topic-snapshots/{revision}.json")
        if path.is_file():
            manifest = load_record(path)
            check_keys(manifest, ("format_version", "workspace_type", "workspace_id", "title", "books", "source_changes", "revision"))
            require(type(manifest["format_version"]) is int and manifest["format_version"] == 1
                    and manifest["workspace_type"] == "topic", "FORMAT_VERSION", "专题来源快照格式不受支持。")
            require(manifest["workspace_id"] == config["workspace_id"] and manifest["revision"] == revision
                    and _revision(manifest) == revision, "CORRUPT_RECORD", "专题来源快照与工作区不匹配。")
            return manifest
    manifest = _current(root, config)
    require(revision is None or manifest["revision"] == revision, "UNKNOWN_REVISION", "专题历史来源快照不存在。")
    if revision is None and check_sources:
        require(not manifest["source_changes"], "SOURCE_CHANGED", "参与书籍有未准备的来源变化。", manifest["source_changes"])
    return manifest


def save_snapshot(root, manifest):
    """The caller holds the topic write lock; snapshots contain metadata only."""
    return save_record(safe_path(root, f".novel/topic-snapshots/{manifest['revision']}.json"), manifest)


def init_topic(root, book_paths, title=None):
    root = Path(root).resolve()
    require(not safe_path(root, "novel.json").exists() and not safe_path(root, ".novel/workspace.json").exists(),
            "DIRECTORY_CONFLICT", "请为跨书专题使用独立目录，保留原书工作区。")
    additions = []
    for value in book_paths:
        location = _book_path(root, value)
        manifest = corpus.load_manifest(location, check_sources=False)
        additions.append((location, manifest["workspace_id"]))
    with writer_lock(root):
        path = safe_path(root, "topic.json")
        old = _registry(root) if path.exists() else None
        config = {**old, "books": [dict(book) for book in old["books"]]} if old else {
            "format_version": 1, "workspace_type": "topic", "workspace_id": "topic_" + uuid.uuid4().hex,
            "title": root.name, "books": []}
        if title is not None:
            config["title"] = bounded_text(title, "title", 300)
        for location, identity in additions:
            existing = next((book for book in config["books"] if book["workspace_id"] == identity), None)
            if existing is not None:
                previous = (root / existing["path"]).resolve()
                require(previous == location or not previous.exists(), "DUPLICATE_BOOK",
                        "不同目录具有同一书籍身份；请核对是否复制了整个旧工作区。")
            try:
                relative = Path(os.path.relpath(location, root)).as_posix()
            except ValueError:
                relative = str(location)
            if existing is not None:
                existing["path"] = relative
            else:
                config["books"].append({"id": "book_" + digest(identity)[:16], "workspace_id": identity, "path": relative})
        require(2 <= len(config["books"]) <= 100, "INVALID_INPUT", "专题需要登记2至100个独立书籍工作区。")
        if config != old:
            if old is not None:
                save_record(safe_path(root, f".novel/topic-config/{digest(old)}.json"), old)
            atomic_write_json(path, config)
        save_snapshot(root, _current(root, _registry(root)))
    result = status(root)
    try:
        result["library_path"] = rebuild_view(root)
    except (NovelError, OSError) as error:
        result["view_warning"] = "专题已登记，入口待重建：" + str(error)
    return result


def status(root):
    import memory

    manifest = load_manifest(root, check_sources=False)
    registered = {book["id"]: book for book in _registry(root)["books"]}
    effects = memory.overlays(root)
    records = memory._load_records(root, "research/*/records/*.json", manifest["workspace_id"])
    latest = {record["case_id"]: record for record in records}
    cases = []
    for case_id, record in sorted(latest.items()):
        memory._identifier(case_id, "case_id")
        ids = [record["record_id"], *(item["id"] for item in record["findings"])]
        stale = record["source_revision"] != manifest["revision"]
        review = stale or bool(manifest["source_changes"]) or any(effects.get(item, {}).get("needs_review") for item in ids)
        cases.append({"case_id": case_id, "record_id": record["record_id"], "question": record["question"],
                      "source_revision": record["source_revision"], "stale": stale, "needs_review": review})
    return {**manifest, "books": [{**book, "path": registered[book["id"]]["path"]} for book in manifest["books"]],
            "case_ids": [case["case_id"] for case in cases], "cases": cases}


def resolve_evidence(root, evidence, revision=None):
    manifest = load_manifest(root, revision=revision, check_sources=False)
    members = {book["id"]: book for book in manifest["books"]}
    result = []
    for item in evidence:
        check_keys(item, ("book_id", "span_id", "quote"))
        require(item["book_id"] in members, "UNKNOWN_BOOK", "引用的书籍不属于此专题版本。")
        book = members[item["book_id"]]
        location = _member_root(root, book)
        original = corpus.load_manifest(location, revision=book["source_revision"], check_sources=False)
        require(original["workspace_id"] == book["workspace_id"], "workspace_mismatch", "原书快照身份不匹配。")
        resolved = corpus.resolve_evidence(location, [{"span_id": item["span_id"], "quote": item["quote"]}],
                                            revision=book["source_revision"])[0]
        result.append({**resolved, "book_id": book["id"], "book_title": book["title"],
                       "book_workspace_id": book["workspace_id"]})
    return result


def search(root, queries, collection="text", book_ids=None, kinds=None, chapter_from=None, chapter_to=None,
           limit=20, offset=0, mode=None, revision=None):
    import analysis

    require(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and offset >= 0,
            "INVALID_INPUT", "limit需为1至100，offset不得为负。")
    require(collection in {"text", "supplements", "analysis"}, "INVALID_INPUT", "专题来源检索支持正文、补充资料和分析。")
    require(collection == "analysis" or mode is None, "USAGE", "--mode仅用于分析记录。")
    if collection == "analysis":
        require(not kinds and revision is None and chapter_from is None and chapter_to is None,
                "USAGE", "分析检索不接受原文章节或版本过滤。")
    manifest = load_manifest(root, revision=revision, check_sources=False)
    members = manifest["books"]
    if book_ids is not None:
        require(bool(book_ids) and set(book_ids) <= {book["id"] for book in members},
                "UNKNOWN_BOOK", "所选书籍不属于此专题版本。")
        members = [book for book in members if book["id"] in book_ids]
    selected_kinds = kinds or (["text"] if collection == "text" else [kind for kind in corpus.SOURCE_KINDS if kind != "text"])
    matches, total = [], 0
    for book in members:
        location = _member_root(root, book)
        start, size = max(0, offset - total), max(1, limit - len(matches))
        if collection == "analysis":
            result = analysis.search_analysis(location, queries, limit=size, offset=start, mode=mode)
        else:
            result = corpus.search(location, queries, kinds=selected_kinds, chapter_from=chapter_from,
                                   chapter_to=chapter_to, limit=size, offset=start, revision=book["source_revision"])
        if len(matches) < limit:
            for item in result["matches"]:
                item = {**item, "book_id": book["id"], "book_title": book["title"], "book_workspace_id": book["workspace_id"],
                        "source_revision": item.get("source_revision", book["source_revision"])}
                if "evidence" in item:
                    item["evidence"] = [{**e, "book_id": book["id"], "book_title": book["title"],
                                         "book_workspace_id": book["workspace_id"]} for e in item["evidence"]]
                matches.append(item)
        total += result["total"]
    return {"source_type": "cross_book_" + collection, "topic_revision": manifest["revision"],
            "books": [book["id"] for book in members], "source_changes": manifest["source_changes"],
            "queries": queries, "scope": {"mode": mode} if collection == "analysis" else {
                "kinds": selected_kinds, "chapter_from": chapter_from, "chapter_to": chapter_to},
            "matches": matches, "total": total, "offset": offset,
            "next_offset": offset + len(matches) if offset + len(matches) < total else None,
            "truncated": offset + len(matches) < total}


def read(root, book_id, span_id=None, chapter_id=None, limit=6000, offset=0, revision=None):
    manifest = load_manifest(root, revision=revision, check_sources=False)
    book = next((book for book in manifest["books"] if book["id"] == book_id), None)
    require(book is not None, "UNKNOWN_BOOK", "请用--book指定专题中登记的书籍ID。")
    result = corpus.read(_member_root(root, book), span_id=span_id, chapter_id=chapter_id,
                         limit=limit, offset=offset, revision=book["source_revision"])
    return {**result, "book_id": book["id"], "book_title": book["title"],
            "book_workspace_id": book["workspace_id"], "topic_revision": manifest["revision"]}


def rebuild_view(root):
    with writer_lock(root):
        manifest = status(root)
        lines = [f"# {manifest['title']}", "", "跨书专题入口；各书的正文、分析与讨论保留在原工作区。", "", "## 参与书籍", ""]
        for book in manifest["books"]:
            lines.append(f"- {book['title']}：`{book['path']}`，来源版本 `{book['source_revision']}`")
        lines += ["", "## 专题报告", ""]
        for case in manifest["cases"]:
            case_id = case["case_id"]
            path = safe_path(root, f"research/{case_id}/report.md")
            label = "需复核" if case["needs_review"] else "已保存"
            target = f"[{case['question']}](research/{case_id}/report.md)" if path.is_file() else case["question"] + "（报告待生成）"
            lines.append(f"- {target} — {label}")
        if not manifest["cases"]:
            lines.append("- 尚无专题报告，可直接提出跨书比较问题。")
        if manifest["source_changes"]:
            lines += ["", "参与书籍存在尚未准备的来源变化，请先检查各书工作区。"]
        lines += ["", "讨论与纠错保存在 notes/，继续研究时读取有效记录。",
                  "来源或原书纠错变化会按参与书籍提示复核，不会自动改写已有结论。", ""]
        path = safe_path(root, "library.md")
        atomic_write_text(path, "\n".join(lines))
    return str(path)
