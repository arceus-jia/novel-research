"""Append-only discussion notes and source-bound research records."""

from __future__ import annotations

from pathlib import Path
import re
import uuid

from common import (
    NovelError, SOURCE_KINDS, atomic_write_json, atomic_write_text, digest,
    load_record, require, safe_path, save_record, utc_now, writer_lock,
)
from corpus import load_manifest, resolve_evidence


NOTE_TYPES = {"correction", "hypothesis", "finding", "preference", "checkpoint", "withdrawal"}
FINDING_KINDS = {"narration", "speech", "belief", "inference", "interpretation", "unknown"}
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
MAX_RECORDS = 10000


def _text(value, name, maximum=12000, optional=False):
    require(isinstance(value, str), "invalid_input", f"{name} must be a string")
    require((optional or bool(value.strip())) and len(value) <= maximum,
            "invalid_input", f"{name} must contain 1–{maximum} characters")
    return value


def _identifier(value, name):
    require(isinstance(value, str) and ID_PATTERN.fullmatch(value),
            "invalid_id", f"{name} must use 1–96 ASCII letters, digits, underscores or hyphens")
    return value


def _strings(value, name, maximum=100):
    require(isinstance(value, list) and len(value) <= maximum,
            "invalid_input", f"{name} must be a list of at most {maximum} strings")
    return [_text(item, name, 4000) for item in value]


def _evidence_input(value):
    require(isinstance(value, list) and len(value) <= 32,
            "invalid_input", "evidence must be a list of at most 32 quotations")
    for item in value:
        require(isinstance(item, dict) and set(item) == {"span_id", "quote"},
                "invalid_input", "evidence entries must contain only span_id and quote")
        _text(item["span_id"], "span_id", 500)
        _text(item["quote"], "quote", 12000)
    return value


def _workspace(root):
    return load_manifest(root, check_sources=False)


def _paths(root, pattern):
    root = Path(root).resolve()
    paths = sorted(root.glob(pattern))
    require(len(paths) <= MAX_RECORDS, "limit_exceeded", "Too many records; narrow the workspace")
    return [safe_path(root, str(path.relative_to(root))) for path in paths]


def _load_records(root, pattern, workspace_id):
    records = []
    for path in _paths(root, pattern):
        record = load_record(path)
        require(record.get("workspace_id") == workspace_id, "workspace_mismatch",
                "Record belongs to a different book", {"path": str(path)})
        require(record.get("record_id") == path.stem, "invalid_record",
                "Record ID does not match its filename", {"path": str(path)})
        records.append(record)
    return sorted(records, key=lambda item: (item.get("created_at", ""), item["record_id"]))


def _notes(root, workspace_id):
    records = _load_records(root, "notes/records/*.json", workspace_id)
    for record in records:
        require(record.get("format_version") == 1 and record.get("record_type") == "note"
                and isinstance(record.get("type"), str) and record["type"] in NOTE_TYPES,
                "invalid_record", "Unsupported note record", {"record_id": record["record_id"]})
        if record["type"] == "correction":
            require(isinstance(record.get("status"), str) and record["status"] in {"pending", "applied"}
                    and (record["status"] != "applied" or bool(record.get("evidence"))),
                    "invalid_record", "Applied corrections require evidence")
    return records


def _targets(root, workspace_id, notes):
    targets = {record["record_id"]: record for record in notes}
    for pattern in ("analysis/records/*.json", "research/*/records/*.json"):
        for record in _load_records(root, pattern, workspace_id):
            require(record.get("format_version") == 1, "invalid_record", "Unsupported record format")
            require(record["record_id"] not in targets, "invalid_record", "Duplicate record ID")
            targets[record["record_id"]] = record
            for field in ("claims", "findings"):
                for finding in record.get(field, []):
                    require(isinstance(finding, dict) and isinstance(finding.get("id"), str),
                            "invalid_record", "Finding has no ID")
                    require(finding["id"] not in targets, "invalid_record", "Duplicate finding ID")
                    targets[finding["id"]] = finding
    return targets


def _check_links(notes, targets):
    links = {}
    for note in notes:
        target = note.get("target_id")
        if target:
            require(target in targets, "unknown_target", "Target is not a persisted record or finding",
                    {"record_id": note["record_id"], "target_id": target})
            links[note["record_id"]] = target
            if note["type"] == "withdrawal":
                require(targets[target].get("record_type") == "note", "invalid_target",
                        "A withdrawal must reference a discussion note")
    for first in links:
        seen = set()
        current = first
        while current in links:
            require(current not in seen, "cyclic_link", "Discussion links must not form a cycle")
            seen.add(current)
            current = links[current]


def _overlay_view(notes, targets, revision):
    _check_links(notes, targets)
    by_target = {}
    for note in notes:
        if note.get("target_id"):
            by_target.setdefault(note["target_id"], []).append(note)
    notes_by_id = {record["record_id"]: record for record in notes}
    view = {}

    def state(record_id):
        if record_id in view:
            return view[record_id]
        note = notes_by_id.get(record_id, {})
        related = by_target.get(record_id, [])
        withdrawals = [item for item in related if item["type"] == "withdrawal"]
        corrections = [item for item in related if item["type"] == "correction"]
        active_withdrawals = [item for item in withdrawals
                              if state(item["record_id"])["status"] == "active"]
        current_corrections = [item for item in corrections
                               if state(item["record_id"])["status"] in {"pending", "applied"}]
        status = note.get("status", "active") if note.get("type") == "correction" else "active"
        if note.get("type") == "correction" and note.get("evidence") and note.get("source_revision") != revision:
            status = "stale"
        if active_withdrawals:
            status = "withdrawn"
        elif any(item["status"] == "applied" for item in current_corrections):
            status = "superseded"
        elif current_corrections:
            status = "disputed"
        elif corrections:
            # Retracting a correction does not prove its original target again.
            status = "needs_review"
        result = {"status": status,
                  "correction_ids": [item["record_id"] for item in corrections],
                  "withdrawal_ids": [item["record_id"] for item in active_withdrawals],
                  "needs_review": status in {"disputed", "needs_review", "stale", "superseded", "withdrawn"}}
        view[record_id] = result
        return result

    for record_id in set(notes_by_id) | set(by_target):
        state(record_id)
    return view


def overlays(root):
    """Derive current note and finding statuses without changing history."""
    manifest = _workspace(root)
    notes = _notes(root, manifest["workspace_id"])
    return _overlay_view(notes, _targets(root, manifest["workspace_id"], notes), manifest["revision"])


def save_note(root, body):
    require(isinstance(body, dict), "invalid_input", "Note input must be an object")
    allowed = {"id", "type", "content", "origin", "evidence", "target_id", "case_id", "status",
               "source_revision", "workspace_id", "reason", "semantic_decision"}
    require(not set(body) - allowed, "invalid_input", "Unknown note input fields",
            {"fields": sorted(set(body) - allowed)})
    require(isinstance(body.get("type"), str) and body["type"] in NOTE_TYPES, "invalid_input", "Unsupported note type")
    require(isinstance(body.get("origin"), str) and body["origin"] in {"user", "agent", "discussion"}, "invalid_input", "Unsupported note origin")
    content = _text(body.get("content"), "content")
    require(isinstance(body.get("status", "pending"), str) and body.get("status", "pending") in {"pending", "applied"},
            "invalid_input", "Invalid correction status")
    evidence_input = _evidence_input(body.get("evidence", []))
    for field in ("reason", "semantic_decision"):
        if field in body:
            _text(body[field], field, 4000)
    record_id = _identifier(body.get("id", "note-" + uuid.uuid4().hex), "id")
    target_id = body.get("target_id")
    if target_id is not None:
        _text(target_id, "target_id", 250)
    if body["type"] == "withdrawal":
        require(target_id is not None, "invalid_target", "Withdrawal requires target_id")
    case_id = body.get("case_id")
    if case_id is not None:
        _identifier(case_id, "case_id")
    with writer_lock(root):
        manifest = _workspace(root)
        workspace_id = manifest["workspace_id"]
        require(body.get("workspace_id", workspace_id) == workspace_id, "workspace_mismatch",
                "Note belongs to a different book")
        path = safe_path(root, f"notes/records/{record_id}.json")
        existing = load_record(path) if path.exists() else None
        if existing:
            require(existing.get("workspace_id") == workspace_id, "workspace_mismatch", "Note belongs to another book")
        revision = body.get("source_revision", existing["source_revision"] if existing else manifest["revision"])
        _text(revision, "source_revision", 200)
        load_manifest(root, revision=revision, check_sources=False)
        resolved = resolve_evidence(root, evidence_input, revision=revision) if evidence_input else []
        require(all(item["source_kind"] == "text" for item in resolved), "invalid_source_kind",
                "Discussion evidence must come from the novel text")
        status = "applied" if body["type"] == "correction" and resolved and body.get("status") == "applied" else "pending"
        request = {key: value for key, value in body.items() if key not in {"id", "workspace_id"}}
        request["source_revision"] = revision
        request["evidence"] = evidence_input
        fingerprint = digest(request)
        if existing:
            require(existing.get("input_fingerprint") == fingerprint, "record_conflict",
                    "This note ID already contains different content")
            return existing
        notes = _notes(root, workspace_id)
        targets = _targets(root, workspace_id, notes)
        require(record_id not in targets, "record_conflict", "Record ID already exists")
        if case_id is not None:
            require(bool(_paths(root, f"research/{case_id}/records/*.json")), "unknown_case", "Research case does not exist")
        record = {"format_version": 1, "record_type": "note", "record_id": record_id,
                  "workspace_id": workspace_id, "created_at": utc_now(), "type": body["type"],
                  "content": content, "origin": body["origin"], "source_revision": revision,
                  "evidence": resolved, "source_valid": bool(resolved), "input_fingerprint": fingerprint}
        if body["type"] == "correction":
            record.update(status=status, semantic_status="reviewed" if status == "applied" else "unreviewed")
        for field in ("target_id", "case_id", "reason", "semantic_decision"):
            if field in body:
                record[field] = body[field]
        _check_links(notes + [record], {**targets, record_id: record})
        return save_record(path, record)


def recall_notes(root, query="", limit=20, include_history=False):
    _text(query, "query", 1000, optional=True)
    require(type(limit) is int and 1 <= limit <= 100, "invalid_input", "limit must be 1–100")
    require(type(include_history) is bool, "invalid_input", "include_history must be boolean")
    manifest = _workspace(root)
    notes = _notes(root, manifest["workspace_id"])
    view = _overlay_view(notes, _targets(root, manifest["workspace_id"], notes), manifest["revision"])
    matches = []
    for note in reversed(notes):
        effective = view[note["record_id"]]
        if not include_history and effective["status"] in {"superseded", "withdrawn"}:
            continue
        if query and query.casefold() not in (note["content"] + " " + note.get("reason", "")).casefold():
            continue
        matches.append({**note, "effective_status": effective["status"], "overlay": effective,
                        "stale": bool(note.get("evidence")) and note.get("source_revision") != manifest["revision"]})
    return {"workspace_id": manifest["workspace_id"], "source_revision": manifest["revision"],
            "notes": matches[:limit], "total": len(matches), "truncated": len(matches) > limit,
            "include_history": include_history}


def _scope(value):
    require(isinstance(value, dict) and not set(value) - {"chapter_from", "chapter_to", "source_kinds"},
            "invalid_input", "Invalid research scope")
    scope = dict(value)
    for field in ("chapter_from", "chapter_to"):
        if field in scope:
            require(type(scope[field]) is int and scope[field] >= 1, "invalid_input", f"{field} must be positive")
    require(scope.get("chapter_from", 1) <= scope.get("chapter_to", 10**12), "invalid_scope", "Chapter range is reversed")
    kinds = scope.setdefault("source_kinds", ["text"])
    require(isinstance(kinds, list) and 1 <= len(kinds) <= len(SOURCE_KINDS)
            and all(isinstance(kind, str) and kind in SOURCE_KINDS for kind in kinds)
            and len(set(kinds)) == len(kinds), "invalid_input", "Invalid source_kinds")
    return scope


def _resolve_scoped(root, evidence, revision, scope):
    resolved = resolve_evidence(root, _evidence_input(evidence), revision=revision) if evidence else []
    for item in resolved:
        require(item["source_kind"] in scope["source_kinds"], "out_of_scope", "Evidence source kind is outside research scope")
        if item["source_kind"] == "text" and ("chapter_from" in scope or "chapter_to" in scope):
            ordinal = item.get("chapter_ordinal")
            require(type(ordinal) is int and scope.get("chapter_from", 1) <= ordinal <= scope.get("chapter_to", 10**12),
                    "out_of_scope", "Evidence chapter is outside research scope")
    return resolved


def _case_records(root, case_id, workspace_id):
    records = _load_records(root, f"research/{case_id}/records/*.json", workspace_id)
    for record in records:
        require(record.get("format_version") == 1 and record.get("record_type") == "research" and record.get("case_id") == case_id,
                "invalid_record", "Research record has an invalid case")
    return records


def save_research(root, body):
    require(isinstance(body, dict), "invalid_input", "Research input must be an object")
    allowed = {"id", "case_id", "question", "source_revision", "workspace_id", "scope", "findings", "queries", "hypotheses", "unresolved"}
    require(not set(body) - allowed, "invalid_input", "Unknown research input fields",
            {"fields": sorted(set(body) - allowed)})
    question = _text(body.get("question"), "question", 4000)
    case_id = _identifier(body.get("case_id", "case-" + uuid.uuid4().hex), "case_id")
    record_id = _identifier(body.get("id", "research-" + uuid.uuid4().hex), "id")
    findings = body.get("findings")
    require(isinstance(findings, list) and len(findings) <= 100,
            "invalid_input", "findings must be a list of at most 100 entries")
    extras = {field: _strings(body.get(field, []), field) for field in ("queries", "hypotheses", "unresolved")}
    with writer_lock(root):
        manifest = _workspace(root)
        workspace_id = manifest["workspace_id"]
        require(body.get("workspace_id", workspace_id) == workspace_id, "workspace_mismatch", "Research belongs to another book")
        previous = _case_records(root, case_id, workspace_id)
        prior_id = next((item for item in previous if item["record_id"] == record_id), None)
        base = prior_id or (previous[-1] if previous else None)
        revision = body.get("source_revision", base["source_revision"] if base else manifest["revision"])
        _text(revision, "source_revision", 200)
        load_manifest(root, revision=revision, check_sources=False)
        require(not previous or previous[0]["source_revision"] == revision, "revision_mismatch",
                "A research case uses one fixed source revision; create a new case for a new revision")
        scope = _scope(body.get("scope", base["scope"] if base else {}))
        accepted = []
        normalized = []
        for index, finding in enumerate(findings):
            require(isinstance(finding, dict) and not set(finding) - {"kind", "text", "evidence", "counter_evidence"},
                    "invalid_input", "Invalid research finding")
            require(isinstance(finding.get("kind"), str) and finding["kind"] in FINDING_KINDS,
                    "invalid_input", "Invalid finding kind")
            text = _text(finding.get("text"), "finding text", 8000)
            evidence_input = _evidence_input(finding.get("evidence", []))
            counter_input = _evidence_input(finding.get("counter_evidence", []))
            require(finding["kind"] not in {"narration", "speech", "belief"} or bool(evidence_input),
                    "missing_evidence", "Narration, speech and belief findings need supporting evidence")
            evidence = _resolve_scoped(root, evidence_input, revision, scope)
            counter = _resolve_scoped(root, counter_input, revision, scope)
            normalized.append({"kind": finding["kind"], "text": text, "evidence": evidence_input, "counter_evidence": counter_input})
            accepted.append({"id": f"{record_id}:finding:{index}", "kind": finding["kind"], "text": text,
                             "evidence": evidence, "counter_evidence": counter, "source_valid": bool(evidence),
                             "semantic_status": "unreviewed", "status": "cited" if evidence else "draft"})
        fingerprint = digest({"question": question, "source_revision": revision, "scope": scope,
                              "findings": normalized, **extras})
        for existing in previous:
            if existing["record_id"] == record_id:
                require(existing.get("input_fingerprint") == fingerprint, "record_conflict", "Research ID already contains different content")
                atomic_write_json(safe_path(root, f"research/{case_id}/case.json"),
                                  {"format_version": 1, "case_id": case_id, "latest_record_id": previous[-1]["record_id"]})
                return existing
        if "id" not in body:
            for existing in reversed(previous):
                if existing.get("input_fingerprint") == fingerprint:
                    atomic_write_json(safe_path(root, f"research/{case_id}/case.json"),
                                      {"format_version": 1, "case_id": case_id, "latest_record_id": previous[-1]["record_id"]})
                    return existing
        targets = _targets(root, workspace_id, _notes(root, workspace_id))
        require(record_id not in targets, "record_conflict", "Record ID already exists")
        record = {"format_version": 1, "record_type": "research", "record_id": record_id,
                  "workspace_id": workspace_id, "case_id": case_id, "created_at": utc_now(),
                  "question": question, "source_revision": revision, "scope": scope,
                  "findings": accepted, "input_fingerprint": fingerprint, **extras}
        saved = save_record(safe_path(root, f"research/{case_id}/records/{record_id}.json"), record)
        atomic_write_json(safe_path(root, f"research/{case_id}/case.json"),
                          {"format_version": 1, "case_id": case_id, "latest_record_id": record_id})
        return saved


def _quote_lines(evidence):
    lines = []
    for item in evidence:
        label = f"{item['source_kind']} / {item['source_id']} / {item.get('chapter_id') or 'source'}"
        lines.append(f"- [{label}; {item['source_revision']}; {item['span_id']}; {item['start']}:{item['end']}]")
        if "epub_origin" in item:
            resources = ", ".join(location["resource"] for location in item["epub_locations"])
            lines.append(f"  EPUB: {item['epub_origin']['original_path']} / {resources} (converted text offsets)")
        lines.extend("> " + line for line in item["quote"].splitlines())
    return lines


def _verify_evidence(root, group, revision):
    require(isinstance(group, list), "invalid_record", "Stored evidence must be a list")
    references = [{"span_id": item["span_id"], "quote": item["quote"]} for item in group]
    resolved = resolve_evidence(root, references, revision=revision) if references else []
    require(group == resolved, "invalid_evidence", "Stored quotation metadata does not match snapshot")
    return resolved


def render_research(root, case_id):
    _identifier(case_id, "case_id")
    manifest = _workspace(root)
    records = _case_records(root, case_id, manifest["workspace_id"])
    require(bool(records), "unknown_case", "Research case does not exist")
    record = records[-1]
    for finding in record["findings"]:
        _verify_evidence(root, finding["evidence"], record["source_revision"])
        _verify_evidence(root, finding["counter_evidence"], record["source_revision"])
    view = overlays(root)
    stale = record["source_revision"] != manifest["revision"]
    review_ids = [record_id for record_id in [record["record_id"], *(item["id"] for item in record["findings"])]
                  if view.get(record_id, {}).get("needs_review")]
    needs_review = stale or bool(review_ids)
    lines = [f"# {record['question']}", "", f"Case: {case_id}", f"Record: {record['record_id']}",
             f"Source revision: {record['source_revision']}",
             "Scope: " + str(record["scope"]), "",
             "Quotation validation checks source locations; it does not prove the conclusions.", ""]
    if ("chapter_from" in record["scope"] or "chapter_to" in record["scope"]) and any(
            kind != "text" for kind in record["scope"]["source_kinds"]):
        lines.extend(["章节上下限仅用于正文；补充资料不按章号过滤，且仍受 source_kinds 限制。", ""])
    if stale:
        lines.extend(["**STALE: this report uses a previous source revision.**", ""])
    if review_ids:
        lines.extend(["**NEEDS REVIEW: saved claims have discussion corrections or withdrawals.**", ""])
    labels = {"narration": "Narration", "speech": "Character speech", "belief": "Character belief",
              "inference": "Research inference", "interpretation": "Literary interpretation", "unknown": "Unknown / unresolved"}
    for index, finding in enumerate(record["findings"], 1):
        status = view.get(finding["id"], {}).get("status", finding["status"])
        lines.extend([f"## {index}. {labels[finding['kind']]} [{status}]", "", finding["text"], "",
                      f"Finding: {finding['id']}", "", "Supporting evidence:"])
        lines.extend(_quote_lines(finding["evidence"]) or ["- None; this is an unverified draft."])
        lines.extend(["", "Counter evidence:"])
        lines.extend(_quote_lines(finding["counter_evidence"]) or ["- None recorded."])
        lines.append("")
    for field, label in (("queries", "Queries used"), ("hypotheses", "Alternative hypotheses"), ("unresolved", "Unresolved")):
        lines.extend([f"## {label}", ""])
        lines.extend(["- " + item for item in record[field]] or ["- None recorded."])
        lines.append("")
    related = []
    ids = {item["record_id"] for item in records} | {item["id"] for saved in records for item in saved["findings"]}
    for note in _notes(root, manifest["workspace_id"]):
        if note.get("case_id") == case_id or note.get("target_id") in ids:
            related.append(note)
    lines.extend(["## Discussion notes", ""])
    for note in related:
        _verify_evidence(root, note["evidence"], note["source_revision"])
        lines.append(f"- [{view[note['record_id']]['status']}; {note['type']}; {note['origin']}; {note['record_id']}] {note['content']}")
        lines.extend(_quote_lines(note["evidence"]))
    if not related:
        lines.append("- None recorded.")
    report = "\n".join(lines).rstrip() + "\n"
    require(len(report) <= 250000, "limit_exceeded", "Report is too large; save a smaller research record")
    with writer_lock(root):
        path = safe_path(root, f"research/{case_id}/report.md")
        atomic_write_text(path, report)
        atomic_write_json(safe_path(root, f"research/{case_id}/case.json"),
                          {"format_version": 1, "case_id": case_id, "latest_record_id": record["record_id"]})
    return {"case_id": case_id, "record_id": record["record_id"], "source_revision": record["source_revision"],
            "stale": stale, "needs_review": needs_review, "review_ids": review_ids,
            "record_count": len(records), "report_path": str(path), "report": report}


def check_records(root):
    """Verify persisted notes/research, retaining damaged files for inspection."""
    manifest = _workspace(root)
    errors = []
    stale = []
    checked = 0
    valid_notes = []
    for pattern in ("notes/records/*.json", "research/*/records/*.json"):
        for path in _paths(root, pattern):
            checked += 1
            try:
                record = load_record(path)
                require(record.get("workspace_id") == manifest["workspace_id"], "workspace_mismatch", "Record belongs to another book")
                require(record.get("record_id") == path.stem, "invalid_record", "Record ID differs from filename")
                require(record.get("format_version") == 1 and isinstance(record.get("record_type"), str)
                        and record["record_type"] in {"note", "research"}, "invalid_record", "Invalid record type or version")
                revision = record.get("source_revision")
                load_manifest(root, revision=revision, check_sources=False)
                evidence_groups = [record.get("evidence", [])]
                if record["record_type"] == "note":
                    require(isinstance(record.get("type"), str) and record["type"] in NOTE_TYPES, "invalid_record", "Invalid note type")
                    if record["type"] == "correction":
                        require(isinstance(record.get("status"), str) and record["status"] in {"pending", "applied"}
                                and (record["status"] != "applied" or bool(record.get("evidence"))),
                                "invalid_record", "Applied corrections require evidence")
                    valid_notes.append(record)
                else:
                    require(record.get("case_id") == path.parent.parent.name, "invalid_record", "Research case differs from directory")
                    for finding in record.get("findings", []):
                        evidence_groups.extend([finding.get("evidence", []), finding.get("counter_evidence", [])])
                for group in evidence_groups:
                    if not group:
                        continue
                    _verify_evidence(root, group, revision)
                if revision != manifest["revision"] and (record["record_type"] == "research" or record.get("evidence")):
                    stale.append(record["record_id"])
            except (NovelError, KeyError, TypeError, AttributeError) as exc:
                errors.append({"path": str(path), "code": getattr(exc, "code", "invalid_record"), "message": str(exc)})
    try:
        _check_links(valid_notes, _targets(root, manifest["workspace_id"], valid_notes))
    except NovelError as exc:
        errors.append({"code": exc.code, "message": str(exc)})
    return {"checked": checked, "valid": not errors,
            "valid_count": checked - len({item.get('path') for item in errors if item.get('path')}),
            "errors": errors[:100], "error_count": len(errors), "stale": stale[:100], "stale_count": len(stale),
            "truncated": len(errors) > 100 or len(stale) > 100}
