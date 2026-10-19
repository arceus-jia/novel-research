#!/usr/bin/env python3
"""Portable command line tools for the novel-research skill."""

import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys
import tempfile

from common import (NovelError, SOURCE_KINDS, VERSION, atomic_write_text, check_python, read_json,
                    require, safe_path)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise NovelError("USAGE", message)


def _pagination(parser, default=20):
    parser.add_argument("--limit", type=int, default=default)
    parser.add_argument("--offset", type=int, default=0)


def _chapters(parser):
    parser.add_argument("--chapter-from", type=int)
    parser.add_argument("--chapter-to", type=int)


def parser():
    result = Parser(description="小说研究工具；模型分析由当前宿主 Agent 执行。")
    result.add_argument("--version", action="version", version=VERSION)
    result.add_argument("--workspace", help="小说工作区；请放在子命令之前。")
    commands = result.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("doctor", help="检查 Python、skill 资源和工作区写入能力。")
    epub_parser = commands.add_parser("epub", help="保留原包，将文字 EPUB 转成可登记的 UTF-8 正文。")
    epub_commands = epub_parser.add_subparsers(dest="action", required=True, parser_class=Parser)
    epub_commands.add_parser("convert").add_argument("path", help="工作区内的相对 EPUB 路径。")
    sources = commands.add_parser("sources", help="发现资料并维护来源配置，无需用户编辑 JSON。")
    source_commands = sources.add_subparsers(dest="action", required=True, parser_class=Parser)
    scan = source_commands.add_parser("scan", help="递归发现已登记与新增的本地资料。")
    _pagination(scan, 100)
    scan.add_argument("--new-only", action="store_true", help="仅列未登记候选，避免重复加载已有路径。")
    add = source_commands.add_parser("add", help="按给定顺序登记资料，保留原文与已有配置。")
    add.add_argument("paths", nargs="+", help="相对小说工作区的 TXT / Markdown 路径。")
    add.add_argument("--kind", choices=SOURCE_KINDS, required=True)
    add.add_argument("--encoding", default="utf-8")
    add.add_argument("--title", help="首次建立配置时的书名。")
    placement = add.add_mutually_exclusive_group()
    placement.add_argument("--before", help="插在此已登记来源之前。")
    placement.add_argument("--after", help="插在此已登记来源之后。")
    prepare = commands.add_parser("prepare", help="导入 novel.json 中的有序来源并保留快照。")
    prepare.add_argument("--source", action="append", help="首次创建配置时显式指定的有序相对路径，可重复。")
    prepare.add_argument("--title")
    prepare.add_argument("--encoding", default="utf-8")
    status = commands.add_parser("status", help="查看来源变更、分析覆盖与章节目录。")
    status.add_argument("--mode", choices=("skim", "analyze"), help="查看指定阅读模式；省略时查看最近阅读模式。")
    status.add_argument("--chapters", action="store_true")
    _pagination(status)
    search = commands.add_parser("search", help="搜索正文、补充资料、分析导航或讨论记忆。")
    search.add_argument("queries", nargs="+")
    search.add_argument("--in", dest="collection", choices=("text", "supplements", "analysis", "notes"), default="text")
    search.add_argument("--kind", action="append")
    search.add_argument("--revision")
    search.add_argument("--mode", choices=("skim", "analyze"), help="仅用于 --in analysis；默认查询两种模式。")
    _chapters(search)
    _pagination(search)
    read = commands.add_parser("read", help="读取指定片段或章节的有界上下文。")
    selector = read.add_mutually_exclusive_group(required=True)
    selector.add_argument("--span")
    selector.add_argument("--chapter")
    read.add_argument("--revision")
    _pagination(read, 6000)
    task = commands.add_parser("task", help="领取或提交逐章分析任务。")
    task_commands = task.add_subparsers(dest="action", required=True, parser_class=Parser)
    next_parser = task_commands.add_parser("next")
    next_parser.add_argument("--mode", choices=("skim", "analyze"), help="显式模式默认全书；省略模式与范围时恢复最近阅读任务。")
    _chapters(next_parser)
    task_commands.add_parser("submit").add_argument("file")
    notes = commands.add_parser("notes", help="保存或读取讨论、纠错和研究偏好。")
    note_commands = notes.add_subparsers(dest="action", required=True, parser_class=Parser)
    note_commands.add_parser("save").add_argument("file")
    recall = note_commands.add_parser("recall")
    recall.add_argument("--query", default="")
    recall.add_argument("--history", action="store_true")
    recall.add_argument("--limit", type=int, default=20)
    research = commands.add_parser("research", help="保存研究结果或生成引用报告。")
    research_commands = research.add_subparsers(dest="action", required=True, parser_class=Parser)
    research_commands.add_parser("save").add_argument("file")
    research_commands.add_parser("render").add_argument("case_id")
    check = commands.add_parser("check", help="校验正式记录和引用，可重建分析视图。")
    check.add_argument("--rebuild", action="store_true")
    return result


def doctor(root=None):
    check_python()
    skill = Path(__file__).resolve().parent.parent
    resources = ["SKILL.md", "scripts/common.py", "scripts/corpus.py", "scripts/analysis.py", "scripts/memory.py", "scripts/epub.py",
                 "references/setup.md", "references/intake.md", "references/analyze.md", "references/skim.md", "references/research.md", "references/library.md"]
    missing = [name for name in resources if not (skill / name).is_file()]
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ("argparse", "hashlib", "json", "pathlib", "tempfile", "unittest", "zipfile", "html.parser", "xml.etree.ElementTree")}
    result = {"version": VERSION, "python": platform.python_version(), "executable": sys.executable,
              "platform": platform.system(), "skill_directory": str(skill),
              "standard_library": modules, "missing_resources": missing,
              "model_calls": False, "third_party_python_dependencies": []}
    if root is not None:
        candidate = root
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        require(candidate.is_dir(), "ENVIRONMENT", "工作区或其上级路径不是目录。")
        with tempfile.TemporaryDirectory(prefix="novel-doctor-", dir=candidate) as temporary:
            path = Path(temporary) / "中文 路径" / "check.txt"
            text = "中文🙂\r\n原文位置检查\n"
            atomic_write_text(path, text)
            with path.open("r", encoding="utf-8", newline="") as stream:
                require(stream.read() == text, "ENVIRONMENT", "UTF-8 / 换行往返检查失败。")
            atomic_write_text(path, text + "替换完成")
        result["workspace"] = {"path": str(root), "exists": root.is_dir(),
                               "tested_parent": str(candidate), "unicode_write_replace": True,
                               "write_lock_present": safe_path(root, ".novel/write.lock").exists()}
    result["ready"] = not missing and all(modules.values())
    require(result["ready"], "BROKEN_SKILL", "skill 文件缺失或 Python 标准库不完整。", result)
    return result


def _input(root, filename):
    path = Path(filename)
    if not path.is_absolute():
        path = safe_path(root, path)
    return read_json(path, max_bytes=1024 * 1024)


def _validate_ranges(args):
    start = getattr(args, "chapter_from", None)
    end = getattr(args, "chapter_to", None)
    require(start is None or start >= 1, "USAGE", "章节序号从 1 开始。")
    require(end is None or end >= 1, "USAGE", "章节序号从 1 开始。")
    require(start is None or end is None or start <= end, "USAGE", "章节起点不能大于终点。")


def dispatch(args):
    root = Path(args.workspace).expanduser().resolve() if args.workspace else None
    if args.command == "doctor":
        return doctor(root)
    require(root is not None, "USAGE", "请在子命令之前提供 --workspace 小说目录。")
    require(root.is_dir(), "WORKSPACE_NOT_FOUND", "小说工作区目录不存在。", str(root))
    _validate_ranges(args)
    import analysis
    import corpus
    import memory

    if args.command == "epub":
        import epub
        return epub.convert(root, args.path)

    if args.command == "sources":
        if args.action == "scan":
            return corpus.scan_sources(root, limit=args.limit, offset=args.offset, new_only=args.new_only)
        return corpus.register_sources(root, args.paths, args.kind, encoding=args.encoding,
                                       title=args.title, before=args.before, after=args.after)

    if args.command == "prepare":
        manifest = corpus.prepare(root, sources=args.source, title=args.title, encoding=args.encoding)
        return {"workspace_id": manifest["workspace_id"], "source_revision": manifest["revision"],
                "source_count": len(manifest["sources"]), "chapter_count": len(manifest["chapters"]),
                "span_count": len(manifest["spans"]), "needs_review": manifest.get("needs_review", []),
                "chapter_preview": [{key: chapter[key] for key in ("id", "ordinal", "title", "confirmed")}
                                    for chapter in manifest["chapters"][:20]],
                "preview_truncated": len(manifest["chapters"]) > 20,
                "next": "status --chapters 可查看目录；task next 可开始分析；search 可直接检索原文。"}
    if args.command == "status":
        result = analysis.status(root, mode=args.mode)
        result["sources"] = corpus.scan_sources(root, limit=20)
        if args.chapters:
            require(1 <= args.limit <= 100 and args.offset >= 0, "USAGE", "limit 需为 1–100，offset 不得为负。")
            chapters = corpus.load_manifest(root, check_sources=False)["chapters"]
            result["chapters"] = [{key: chapter[key] for key in ("id", "ordinal", "title", "confirmed")}
                                  for chapter in chapters[args.offset:args.offset + args.limit]]
            result["chapter_count"] = len(chapters)
            result["next_offset"] = args.offset + args.limit if args.offset + args.limit < len(chapters) else None
        return result
    if args.command == "search":
        require(args.mode is None or args.collection == "analysis", "USAGE", "--mode 只用于 --in analysis。")
        if args.collection in ("analysis", "notes"):
            require(not args.kind and not args.revision and args.chapter_from is None and args.chapter_to is None,
                    "USAGE", "分析/记忆检索不接受原文版本或章节过滤；请检查返回记录的范围和状态。")
            if args.collection == "analysis":
                return analysis.search_analysis(root, args.queries, limit=args.limit, offset=args.offset, mode=args.mode)
            require(len(args.queries) == 1 and args.offset == 0, "USAGE", "记忆检索一次接受一个查询，请使用 notes recall。")
            return memory.recall_notes(root, query=args.queries[0], limit=args.limit)
        kinds = args.kind or (["text"] if args.collection == "text" else [kind for kind in SOURCE_KINDS if kind != "text"])
        return corpus.search(root, args.queries, kinds=kinds, chapter_from=args.chapter_from,
                             chapter_to=args.chapter_to, limit=args.limit, offset=args.offset, revision=args.revision)
    if args.command == "read":
        return corpus.read(root, span_id=args.span, chapter_id=args.chapter,
                           limit=args.limit, offset=args.offset, revision=args.revision)
    if args.command == "task":
        if args.action == "next":
            return analysis.next_task(root, chapter_from=args.chapter_from, chapter_to=args.chapter_to, mode=args.mode)
        return analysis.submit_task(root, _input(root, args.file))
    if args.command == "notes":
        if args.action == "recall":
            return memory.recall_notes(root, query=args.query, limit=args.limit, include_history=args.history)
        result = memory.save_note(root, _input(root, args.file))
        try:
            analysis.rebuild_views(root)
        except (NovelError, OSError) as error:
            result = {**result, "view_warning": "记忆已保存，视图待重建：" + str(error)}
        return result
    if args.command == "research":
        return memory.save_research(root, _input(root, args.file)) if args.action == "save" else memory.render_research(root, args.case_id)
    if args.command == "check":
        import epub
        result = {"analysis": analysis.check_records(root), "memory": memory.check_records(root),
                  "epub": epub.check_conversions(root), "status": analysis.status(root)}
        if args.rebuild:
            result["views"] = analysis.rebuild_views(root)
        valid = (result["analysis"]["valid"] and result["memory"].get("valid", not result["memory"].get("errors"))
                 and result["epub"]["valid"])
        require(valid, "CHECK_FAILED", "部分记录或引用校验失败。", result)
        return result
    raise NovelError("USAGE", "未知命令。")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        check_python()
        result = dispatch(parser().parse_args(argv))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, allow_nan=False))
        return 0
    except NovelError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": error.message,
                                                   "details": error.details}}, ensure_ascii=False))
        return 2
    except (OSError, UnicodeError) as error:
        print(json.dumps({"ok": False, "error": {"code": "IO_ERROR", "message": str(error)}}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
