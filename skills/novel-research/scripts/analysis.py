"""Bounded host-model tasks and rebuildable analysis navigation."""

from pathlib import Path

import corpus
from common import (CLAIM_KINDS, NovelError, atomic_write_json, atomic_write_text,
                    bounded_list, bounded_text, check_keys, digest, load_record,
                    read_json, require, safe_path, save_record, utc_now, writer_lock)


ANALYSIS_VERSION = 1
TASK_CHARS = 6000
MODES = ("skim", "analyze")
MODE_LABELS = {"skim": "粗读", "analyze": "精读"}


def _contract_hash(mode="analyze"):
    require(mode in MODES, "INVALID_INPUT", "阅读模式应为 skim 或 analyze。")
    path = Path(__file__).resolve().parent.parent / "references" / f"{mode}.md"
    require(path.is_file(), "BROKEN_SKILL", f"skill 缺少 references/{mode}.md。")
    return digest({"version": ANALYSIS_VERSION, "instructions": path.read_text(encoding="utf-8")})


def _reading_session(root):
    path = safe_path(root, ".novel/reading.json")
    session = read_json(path) if path.is_file() else {
        "format_version": 1, "mode": "analyze", "chapter_from": None, "chapter_to": None}
    require(isinstance(session, dict) and type(session.get("format_version")) is int
            and session["format_version"] == 1 and session.get("mode") in MODES,
            "INVALID_READING_STATE", "阅读断点格式或模式无效，请核对 .novel/reading.json。")
    for field in ("chapter_from", "chapter_to"):
        value = session.get(field)
        require(value is None or (type(value) is int and value >= 1),
                "INVALID_READING_STATE", "阅读断点中的章号无效。")
    start, end = session.get("chapter_from"), session.get("chapter_to")
    require(start is None or end is None or start <= end,
            "INVALID_READING_STATE", "阅读断点中的章节范围颠倒。")
    return session


def _task_specs(root, manifest, mode="analyze"):
    chapters = {item["id"]: item for item in manifest["chapters"]}
    contract = _contract_hash(mode)
    groups = []
    group = []
    characters = 0
    for span in corpus.iter_spans(root, manifest=manifest, kinds=["text"]):
        chapter = chapters.get(span.get("chapter_id"))
        if chapter is None or not chapter.get("confirmed", True):
            continue
        if group and (group[-1]["chapter_id"] != span["chapter_id"] or characters + len(span["text"]) > TASK_CHARS):
            groups.append(group)
            group = []
            characters = 0
        group.append(span)
        characters += len(span["text"])
    if group:
        groups.append(group)
    tasks = []
    for spans in groups:
        chapter = chapters[spans[0]["chapter_id"]]
        inputs = {"contract": contract,
                  "chapter": {key: chapter[key] for key in ("id", "ordinal", "title")},
                  "inputs": [{"id": span["span_id"], "unit_hash": span["unit_hash"],
                              "text": span["text"]} for span in spans]}
        # Keep existing analyze fingerprints so installed workspaces can reuse accepted work.
        if mode != "analyze":
            inputs["mode"] = mode
        fingerprint = digest(inputs)
        tasks.append({"task_id": "task-" + fingerprint[:24], "input_fingerprint": fingerprint,
                      "mode": mode,
                      "analysis_version": ANALYSIS_VERSION, "contract_hash": contract,
                      "source_revision": manifest["revision"], "workspace_id": manifest["workspace_id"],
                      "chapter": {key: chapter[key] for key in ("id", "ordinal", "title")},
                      "spans": spans, "input_characters": sum(len(span["text"]) for span in spans)})
    return tasks


def _record_path(root, task_id):
    return safe_path(root, f"analysis/records/{task_id}.json")


def _compatible_record(root, task):
    path = _record_path(root, task["task_id"])
    if not path.is_file():
        return None
    record = load_record(path)
    require(record.get("record_type") == "analysis" and record.get("workspace_id") == task["workspace_id"]
            and record.get("analysis_version") == ANALYSIS_VERSION
            and record.get("mode", "analyze") == task["mode"]
            and record.get("input_fingerprint") == task["input_fingerprint"],
            "CORRUPT_RECORD", "分析记录与任务不匹配。", str(path))
    return record


def status(root, mode=None):
    manifest = corpus.load_manifest(root, check_sources=False)
    session = _reading_session(root)
    mode = session["mode"] if mode is None else mode
    tasks = _task_specs(root, manifest, mode)
    completed = []
    pending = []
    failed = []
    invalid = []
    for task in tasks:
        try:
            if _compatible_record(root, task) is not None:
                completed.append(task["task_id"])
            else:
                pending.append(task["task_id"])
                diagnostic = _failure(root, task)
                if diagnostic:
                    failed.append(diagnostic)
        except NovelError as error:
            invalid.append({"task_id": task["task_id"], "code": error.code, "message": error.message})
    current_ids = {task["task_id"] for task in tasks}
    for other_mode in MODES:
        if other_mode != mode:
            current_ids.update(task["task_id"] for task in _task_specs(root, manifest, other_mode))
    directory = safe_path(root, "analysis/records")
    stale = [path.stem for path in directory.glob("*.json") if path.stem not in current_ids]
    changes = corpus.source_changes(root, manifest)
    review = manifest.get("needs_review", [])
    conversion_warnings = []
    for source in manifest["sources"]:
        if "epub_origin" in source:
            origin = source["epub_origin"]
            conversion = load_record(safe_path(root, origin["record_path"]))
            if conversion["warnings"]:
                conversion_warnings.append({"path": origin["original_path"], "record_path": origin["record_path"],
                                            "warning_count": len(conversion["warnings"]),
                                            "warnings": conversion["warnings"][:20],
                                            "truncated": len(conversion["warnings"]) > 20})
    return {"workspace_id": manifest["workspace_id"], "source_revision": manifest["revision"],
            "mode": mode, "reading_session": session,
            "source_changes": changes, "needs_review": review, "conversion_warnings": conversion_warnings,
            "task_count": len(tasks), "completed": len(completed), "pending": len(pending),
            "invalid": invalid, "failed_count": len(failed), "failed": failed[:20],
            "stale_record_count": len(stale),
            "coverage_complete": bool(tasks) and not pending and not invalid and not review and not changes,
            "next_task_id": pending[0] if pending else None,
            "coverage_note": "覆盖仅针对 mode 所示阅读模式，不互算粗读与精读，也不保证已抽取全部事实或伏笔。"}


def _failure(root, task):
    path = safe_path(root, f".novel/task-errors/{task['task_id']}.json")
    if not path.is_file():
        return None
    try:
        value = read_json(path)
        if (isinstance(value, dict) and value.get("task_id") == task["task_id"]
                and value.get("input_fingerprint") == task["input_fingerprint"]
                and type(value.get("attempts")) is int and value["attempts"] >= 1
                and isinstance(value.get("code"), str) and isinstance(value.get("message"), str)):
            return value
    except (NovelError, OSError):
        pass
    return None


def next_task(root, chapter_from=None, chapter_to=None, mode=None):
    manifest = corpus.load_manifest(root)
    session = _reading_session(root)
    if mode is None:
        mode = session["mode"]
        if chapter_from is None and chapter_to is None:
            chapter_from, chapter_to = session.get("chapter_from"), session.get("chapter_to")
    for value in (chapter_from, chapter_to):
        require(value is None or (type(value) is int and value >= 1), "INVALID_INPUT", "章节序号从 1 开始。")
    require(chapter_from is None or chapter_to is None or chapter_from <= chapter_to,
            "INVALID_INPUT", "章节起点不能大于终点。")
    session = {"format_version": 1, "mode": mode, "chapter_from": chapter_from, "chapter_to": chapter_to}
    scope_tasks = 0
    selected = None
    for task in _task_specs(root, manifest, mode):
        ordinal = task["chapter"]["ordinal"]
        if chapter_from is not None and ordinal < chapter_from:
            continue
        if chapter_to is not None and ordinal > chapter_to:
            continue
        scope_tasks += 1
        if _compatible_record(root, task) is not None:
            continue
        selected = task
        break
    with writer_lock(root):
        current = corpus.load_manifest(root)
        require(current["revision"] == manifest["revision"], "INPUT_CHANGED", "领取任务期间来源版本改变，请重试。")
        if selected is not None:
            atomic_write_json(safe_path(root, f".novel/tasks/{selected['task_id']}.json"), selected)
        atomic_write_json(safe_path(root, ".novel/reading.json"), session)
    scope = {"chapter_from": chapter_from, "chapter_to": chapter_to}
    if selected is not None:
        return {"state": "pending", "mode": mode, "scope": scope,
                "task": selected, "previous_error": _failure(root, selected),
                "next": f"阅读 references/{mode}.md，生成 JSON 后运行 task submit。"}
    state = "needs_review" if manifest.get("needs_review") else ("complete" if scope_tasks else "empty_scope")
    return {"state": state, "mode": mode, "scope": scope, "status": status(root, mode=mode)}


def _evidence(root, value, task, required=False):
    bounded_list(value, "evidence", 20)
    require(not required or bool(value), "MISSING_EVIDENCE", "此条目需要原文证据；未验证想法请保存为假设。")
    return corpus.resolve_evidence(root, value, revision=task["source_revision"],
                                   allowed_span_ids=[span["span_id"] for span in task["spans"]])


def _normalize_submission(root, body, task):
    check_keys(body, ("task_id", "input_fingerprint", "overview", "mentions", "claims", "open_questions"), ("model", "mode"))
    require(body["input_fingerprint"] == task["input_fingerprint"], "STALE_INPUT", "任务输入指纹不匹配。")
    require(body.get("mode", task["mode"]) == task["mode"], "MODE_MISMATCH", "提交模式与已领取任务不一致。")
    overview = bounded_text(body["overview"], "overview", 1200, allow_empty=task["mode"] == "analyze")
    mentions = []
    for mention in bounded_list(body["mentions"], "mentions", 80):
        check_keys(mention, ("name", "evidence"))
        name = bounded_text(mention["name"], "name", 120)
        evidence = _evidence(root, mention["evidence"], task, required=True)
        require(any(name in item["quote"] for item in evidence), "MENTION_NOT_FOUND", "称谓需在其引文中实际出现。", name)
        mentions.append({"name": name, "evidence": evidence})
    claims = []
    for number, claim in enumerate(bounded_list(body["claims"], "claims", 80)):
        check_keys(claim, ("kind", "text", "evidence"))
        require(claim["kind"] in CLAIM_KINDS, "INVALID_INPUT", "不支持的主张类型。", list(CLAIM_KINDS))
        claims.append({"id": task["task_id"] + ":claim:" + str(number), "kind": claim["kind"],
                       "text": bounded_text(claim["text"], "claim.text", 1500),
                       "evidence": _evidence(root, claim["evidence"], task, required=claim["kind"] != "unknown")})
    questions = []
    for question in bounded_list(body["open_questions"], "open_questions", 40):
        check_keys(question, ("text",), ("evidence",))
        questions.append({"text": bounded_text(question["text"], "question.text", 1000),
                          "evidence": _evidence(root, question.get("evidence", []), task)})
    model = bounded_text(body.get("model", "unknown"), "model", 200)
    return {"format_version": 1, "record_type": "analysis", "record_id": task["task_id"], "task_id": task["task_id"],
            "mode": task["mode"],
            "workspace_id": task["workspace_id"], "source_revision": task["source_revision"],
            "input_fingerprint": task["input_fingerprint"], "analysis_version": ANALYSIS_VERSION,
            "contract_hash": task["contract_hash"], "submission_hash": digest(body),
            "chapter": task["chapter"], "overview": overview, "mentions": mentions, "claims": claims,
            "open_questions": questions, "model": model, "citation_checks": "passed",
            "semantic_review": "unreviewed", "created_at": utc_now()}


def submit_task(root, body):
    require(isinstance(body, dict) and isinstance(body.get("task_id"), str), "INVALID_INPUT", "缺少 task_id。")
    with writer_lock(root):
        manifest = corpus.load_manifest(root)
        task = next((item for mode in MODES for item in _task_specs(root, manifest, mode)
                     if item["task_id"] == body["task_id"]), None)
        require(task is not None, "STALE_TASK", "此任务不属于当前输入；请重新领取任务。")
        try:
            packet = safe_path(root, f".novel/tasks/{task['task_id']}.json")
            require(packet.is_file(), "TASK_NOT_ISSUED", "请先运行 task next 获取完整任务输入。")
            existing = _compatible_record(root, task)
            if existing is not None:
                require(existing.get("submission_hash") == digest(body), "RECORD_CONFLICT", "任务已有不同结果，原记录保持不变。")
                return {"state": "already_saved", "record": existing}
            record = _normalize_submission(root, body, task)
            save_record(_record_path(root, task["task_id"]), record)
        except NovelError as error:
            previous = _failure(root, task) or {}
            diagnostic = {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                          "failed_at": utc_now(), "attempts": previous.get("attempts", 0) + 1,
                          "code": error.code, "message": error.message, "details": error.details}
            try:
                atomic_write_json(safe_path(root, f".novel/task-errors/{task['task_id']}.json"), diagnostic)
            except OSError:
                error.details = {"cause": error.details, "diagnostic_warning": "本次失败原因未能保存。"}
            raise
    result = {"state": "saved", "record": record}
    try:
        result["views"] = rebuild_views(root)
    except (NovelError, OSError) as error:
        result["warning"] = "正式记录已保存，导航生成失败；可用 check --rebuild 重建：" + str(error)
    return result


def current_records(root, mode=None):
    manifest = corpus.load_manifest(root, check_sources=False)
    result = []
    for selected_mode in MODES if mode is None else (mode,):
        for task in _task_specs(root, manifest, selected_mode):
            record = _compatible_record(root, task)
            if record is not None:
                result.append(record)
    return sorted(result, key=lambda record: (record["chapter"]["ordinal"], MODES.index(record.get("mode", "analyze"))))


def _item_overlay(changes, record_id, item_id):
    parent = changes.get(record_id, {})
    child = changes.get(item_id, {})
    if not parent:
        return child
    if not child:
        return parent
    priority = ("withdrawn", "superseded", "disputed", "needs_review", "stale", "pending", "applied", "active")
    states = {parent.get("status"), child.get("status")}
    return {"status": next((state for state in priority if state in states), "needs_review"),
            "record_status": parent.get("status"), "item_status": child.get("status"),
            "correction_ids": list(dict.fromkeys(parent.get("correction_ids", []) + child.get("correction_ids", []))),
            "needs_review": bool(parent.get("needs_review") or child.get("needs_review"))}


def search_analysis(root, queries, limit=20, offset=0, mode=None):
    import memory

    require(1 <= limit <= 100 and offset >= 0, "INVALID_INPUT", "limit 需为 1–100，offset 不得为负。")
    changes = memory.overlays(root)
    results = []
    for record in current_records(root, mode=mode):
        items = [{"id": record["record_id"], "kind": "overview", "text": record["overview"], "evidence": []}]
        items += record["claims"]
        items += [{"id": record["record_id"] + ":mention:" + str(number), "kind": "mention",
                   "text": item["name"], "evidence": item["evidence"]}
                  for number, item in enumerate(record["mentions"])]
        items += [{"id": record["record_id"], "kind": "open_question", "question_index": number,
                   "text": item["text"], "evidence": item["evidence"]}
                  for number, item in enumerate(record["open_questions"])]
        for item in items:
            if not queries or any(query.casefold() in item["text"].casefold() for query in queries):
                effect = _item_overlay(changes, record["record_id"], item["id"])
                results.append({**item, "chapter": record["chapter"], "record_id": record["record_id"],
                                "mode": record.get("mode", "analyze"),
                                "source_revision": record["source_revision"], "navigation_only": item["kind"] in ("overview", "open_question"),
                                "current_status": effect.get("status", "active"), "corrections": effect})
    return {"source_type": "generated_analysis", "matches": results[offset:offset + limit], "total": len(results),
            "offset": offset, "next_offset": offset + limit if offset + limit < len(results) else None,
            "truncated": offset + limit < len(results), "note": "导航和候选记录需回查原文；注意 current_status。"}


def _quote_lines(evidence):
    lines = []
    for item in evidence:
        lines.append(f"- `{item['span_id']}`，版本 `{item['source_revision']}`，范围 [{item['start']}, {item['end']})：")
        if "epub_origin" in item:
            resources = ", ".join(location["resource"] for location in item["epub_locations"])
            lines.append(f"  EPUB：`{item['epub_origin']['original_path']}` / `{resources}`（转换文本位置）")
        lines.extend("> " + line for line in item["quote"].splitlines())
    return lines


def rebuild_views(root):
    import memory

    with writer_lock(root):
        records = current_records(root)
        changes = memory.overlays(root)
        lines = ["# 小说分析导航", "", "生成视图；原文与正式 JSON 记录为依据，引用存在不代表解释已证实。", ""]
        questions = ["# 疑问与待核查线索", "", "这是阅读时产生的问题，可能已由后续研究回答；回查研究记录与有效纠错，不当作事实。", ""]
        characters = {}
        for record in records:
            chapter = record["chapter"]
            label = MODE_LABELS[record.get("mode", "analyze")]
            lines += [f"## 第 {chapter['ordinal']} 章 {chapter['title']} · {label}", "", record["overview"], ""]
            for claim in record["claims"]:
                effect = _item_overlay(changes, record["record_id"], claim["id"])
                state = effect.get("status", "active")
                lines += [f"### {claim['kind']} · {state}", "", claim["text"], f"记录：`{claim['id']}`", ""]
                if effect:
                    lines += ["存在纠错或争议，请通过 notes recall 读取当前判断。", ""]
                lines += _quote_lines(claim["evidence"]) + [""]
            for mention in record["mentions"]:
                characters.setdefault(mention["name"], set()).add(chapter["ordinal"])
            for question in record["open_questions"]:
                state = changes.get(record["record_id"], {}).get("status", "active")
                questions += [f"## 第 {chapter['ordinal']} 章 · {label} · {state}", "", question["text"],
                              f"来源分析：`{record['record_id']}`", ""]
                questions += _quote_lines(question["evidence"]) + [""]
        character_lines = ["# 人物称谓导航", "", "同名提及的汇总不证明身份相同；本表不执行别名合并。", ""]
        for name, ordinals in sorted(characters.items()):
            character_lines.append(f"- {name}：第 {', '.join(map(str, sorted(ordinals)))} 章")
        chapter_path = safe_path(root, "analysis/views/chapters.md")
        character_path = safe_path(root, "analysis/views/characters.md")
        question_path = safe_path(root, "analysis/views/questions.md")
        library_path = safe_path(root, "analysis/views/library.md")
        progress = status(root, mode="analyze")
        skim_progress = status(root, mode="skim")
        library = ["# 小说资料库", "", f"来源版本：`{progress['source_revision']}`", ""]
        for mode, value in (("skim", skim_progress), ("analyze", progress)):
            library += [f"{MODE_LABELS[mode]}任务：已接收 {value['completed']} / {value['task_count']}；待处理 {value['pending']}。", ""]
        session = progress["reading_session"]
        start, end = session.get("chapter_from"), session.get("chapter_to")
        scope = "全书" if start is None and end is None else f"第 {start or 1} 章至 {end or '末章'}"
        library += [f"继续阅读：{MODE_LABELS[session['mode']]}，{scope}。", "",
                   "粗读与精读分别计数；覆盖只表示对应模式的产物已接收，不保证已抽取全部事实或伏笔。", "",
                   "- [章节、事件与判断（标注阅读模式）](chapters.md)", "- [人物称谓出现位置](characters.md)",
                   "- [疑问与待核查线索](questions.md)", "",
                   "跨章研究与报告在书籍目录 research/，有效纠错与进度通过 notes recall 查询。", ""]
        if progress["needs_review"] or progress["source_changes"]:
            library += ["存在待确认目录或来源变化；继续研究前运行 status，不能把此页当作当前完整覆盖。", ""]
        if progress["conversion_warnings"]:
            library += ["EPUB 存在转换范围提示；运行 status 查看 conversion_warnings 并核对原书。", ""]
        atomic_write_text(chapter_path, "\n".join(lines) + "\n")
        atomic_write_text(character_path, "\n".join(character_lines) + "\n")
        atomic_write_text(question_path, "\n".join(questions) + "\n")
        atomic_write_text(library_path, "\n".join(library) + "\n")
        return {"chapters": str(chapter_path), "characters": str(character_path),
                "questions": str(question_path), "library": str(library_path)}


def check_records(root):
    manifest = corpus.load_manifest(root, check_sources=False)
    errors = []
    checked = 0
    for path in safe_path(root, "analysis/records").glob("*.json"):
        try:
            record = load_record(path)
            require(record.get("record_type") == "analysis" and record.get("workspace_id") == manifest["workspace_id"],
                    "CORRUPT_RECORD", "分析记录不属于当前工作区。")
            require(record.get("mode", "analyze") in MODES, "CORRUPT_RECORD", "分析记录的阅读模式无效。")
            for item in record.get("mentions", []) + record.get("claims", []) + record.get("open_questions", []):
                evidence = item.get("evidence", [])
                resolved = corpus.resolve_evidence(root, [{"span_id": e["span_id"], "quote": e["quote"]} for e in evidence],
                                                   revision=record["source_revision"])
                require(resolved == evidence, "CORRUPT_RECORD", "已保存的证据位置不一致。")
            checked += 1
        except (NovelError, KeyError, TypeError, OSError) as error:
            errors.append({"path": str(path), "message": str(error)})
    return {"checked": checked, "errors": errors, "valid": not errors}
