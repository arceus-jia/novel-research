from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"))

import corpus
from common import NovelError, atomic_write_json, read_json


class SourceIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "小说 任意资料"
        self.root.mkdir()

    def source(self, relative, text="第一章 雪\n林青走过桥边。", encoding="utf-8"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(encoding))
        return relative

    def configure(self, sources, **extra):
        config = {"format_version": 1, "title": "合成小说", "sources": sources, **extra}
        atomic_write_json(self.root / "novel.json", config)
        return config

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(NovelError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_scan_without_config_recurses_without_creating_files(self):
        expected = [self.source("书稿/卷一/正文.TXT"), self.source("访谈/原文.markdown"),
                    self.source("notes/普通用户手记.md"), self.source("analysis/其他资料.md")]
        self.source(".hidden/不扫描.md")
        self.source("书稿/.hidden/不扫描.txt")
        self.source(".临时稿.md")
        self.source("不支持.json", "{}")
        result = corpus.scan_sources(self.root, limit=2)
        following = corpus.scan_sources(self.root, offset=result["next_offset"])
        self.assertEqual([item["path"] for item in result["files"] + following["files"]], sorted(expected))
        self.assertEqual(result["new_count"], 4)
        self.assertEqual(result["registered_count"], 0)
        self.assertFalse(result["config_exists"])
        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["truncated"])
        self.assertIsNone(following["next_offset"])
        self.assertTrue(all(not item["registered"] and "kind" not in item for item in result["files"]))
        self.assertFalse((self.root / "novel.json").exists())
        self.assertFalse((self.root / ".novel").exists())

    def test_scan_excludes_only_actual_generated_outputs(self):
        for relative in (".novel/inbox/result.md", "analysis/views/chapters.md", "analysis/views/characters.md",
                         "research/saved/report.md", "research/saved/records/record.json"):
            self.source(relative)
        expected = [self.source("analysis/views/用户资料.md"), self.source("research/user/report.md"),
                    self.source("research/saved/论证原文.txt"), self.source("notes/深层/访谈.md")]
        result = corpus.scan_sources(self.root)
        self.assertEqual([item["path"] for item in result["files"]], sorted(expected))
        self.assertEqual(result["new_count"], len(expected))

    def test_scan_includes_registered_hidden_or_excluded_files_and_reports_missing(self):
        self.source(".private/正文.txt")
        self.source("analysis/views/chapters.md")
        self.source("新访谈.md")
        config = self.configure([{"path": ".private/正文.txt", "kind": "text"},
                                 {"path": "analysis/views/chapters.md", "kind": "user_note"},
                                 {"path": "缺失.txt", "kind": "supplement"}])
        result = corpus.scan_sources(self.root)
        self.assertEqual(result["registered_count"], 3)
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(result["missing"], ["缺失.txt"])
        registered = [item for item in result["files"] if item["registered"]]
        self.assertEqual({item["path"] for item in registered}, {".private/正文.txt", "analysis/views/chapters.md"})
        self.assertEqual(read_json(self.root / "novel.json"), config)
        self.assertFalse((self.root / ".novel").exists())

    def test_new_only_pagination_keeps_candidates_visible_and_global_diagnostics(self):
        sources = []
        for index in range(12):
            path = self.source(f"已登记/{index:02d}.txt")
            sources.append({"path": path, "kind": "text"})
        sources.append({"path": "已登记/缺失.txt", "kind": "text"})
        self.configure(sources)
        new_paths = [self.source("新增/甲.md"), self.source("新增/乙.md")]
        result = corpus.scan_sources(self.root, limit=1, new_only=True)
        following = corpus.scan_sources(self.root, limit=1, offset=result["next_offset"], new_only=True)
        self.assertEqual([item["path"] for item in result["files"] + following["files"]], sorted(new_paths))
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["new_count"], 2)
        self.assertEqual(result["registered_count"], 13)
        self.assertEqual(result["missing"], ["已登记/缺失.txt"])
        self.assertTrue(result["scan_complete"])
        self.assertIsNone(following["next_offset"])

    def test_scan_skips_symlink_cycles_and_external_files(self):
        self.source("正文.txt")
        outside = Path(self.temporary.name) / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        try:
            (self.root / "loop").symlink_to(self.root, target_is_directory=True)
            (self.root / "external.md").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this system")
        result = corpus.scan_sources(self.root)
        self.assertEqual([item["path"] for item in result["files"]], ["正文.txt"])
        self.assertEqual(result["skipped_symlinks"], ["external.md", "loop"])
        self.assertTrue(result["scan_complete"])

    def test_scan_exposes_walk_failures_instead_of_claiming_complete(self):
        def failed_walk(root, **options):
            options["onerror"](PermissionError(13, "access denied", str(root / "blocked")))
            yield str(root), [], []

        with mock.patch.object(corpus.os, "walk", side_effect=failed_walk):
            result = corpus.scan_sources(self.root)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["errors"][0]["path"], "blocked")
        self.assertEqual(result["total"], 0)

    def test_file_aliases_cannot_create_duplicate_registrations_or_candidates(self):
        self.source("正文.txt")
        try:
            os.link(self.root / "正文.txt", self.root / "别名.txt")
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unavailable on this system")
        corpus.register_sources(self.root, ["正文.txt", "别名.txt"], "text")
        result = corpus.scan_sources(self.root)
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["registered_count"], 1)
        self.assertEqual([item["path"] for item in result["files"]], ["正文.txt"])
        self.assertEqual(corpus.register_sources(self.root, ["别名.txt"], "text")["already_registered"], ["正文.txt"])

    def test_initial_registration_preserves_paths_bytes_and_parameter_order(self):
        paths = [self.source("随意/乙.txt", encoding="utf-8-sig"), self.source("更深/原文/甲.md")]
        originals = {path: (self.root / path).read_bytes() for path in paths}
        result = corpus.register_sources(self.root, paths + ["./" + paths[0]], "text", title="我的书")
        config = read_json(self.root / "novel.json")
        self.assertEqual(config["title"], "我的书")
        self.assertEqual([source["path"] for source in config["sources"]], paths)
        self.assertEqual(result["added"], paths)
        self.assertEqual(result["already_registered"], [])
        self.assertTrue(result["prepare_required"])
        self.assertNotIn("backup_path", result)
        self.assertFalse((self.root / ".novel" / "active.json").exists())
        for path in paths:
            self.assertEqual((self.root / path).read_bytes(), originals[path])
        manifest = corpus.prepare(self.root)
        self.assertEqual([source["path"] for source in manifest["sources"]], paths)

    def test_insert_preserves_all_existing_metadata_and_exact_backup_bytes(self):
        self.source("一.txt")
        self.source("三.txt")
        self.source("二甲.txt")
        self.source("二乙.txt")
        config = {"format_version": 1, "title": "原书名", "custom": {"tag": "保留"}, "sources": [
            {"path": "一.txt", "kind": "text", "encoding": "UTF8", "chapters": [{"title": "已确认", "start": 0, "end": 17}], "extra": True},
            {"path": "三.txt", "kind": "text"},
        ]}
        original = b"\xef\xbb\xbf" + json.dumps(config, ensure_ascii=False, indent="\t").replace("\n", "\r\n").encode("utf-8")
        (self.root / "novel.json").write_bytes(original)
        result = corpus.register_sources(self.root, ["二甲.txt", "二乙.txt"], "text", before="./三.txt")
        updated = read_json(self.root / "novel.json")
        self.assertEqual([source["path"] for source in updated["sources"]], ["一.txt", "二甲.txt", "二乙.txt", "三.txt"])
        self.assertEqual(updated["sources"][0], config["sources"][0])
        self.assertEqual(updated["sources"][-1], config["sources"][1])
        self.assertEqual(updated["custom"], config["custom"])
        self.assertEqual(updated["title"], "原书名")
        backup = Path(result["backup_path"])
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(backup.stem, hashlib.sha256(original).hexdigest())

    def test_after_anchor_inserts_only_new_files_and_never_reorders_registered_ones(self):
        for path in ("一.txt", "二.txt", "评论.md"):
            self.source(path)
        self.configure([{"path": "一.txt", "kind": "text"}, {"path": "二.txt", "kind": "text"}])
        result = corpus.register_sources(self.root, ["二.txt", "评论.md", "一.txt"], "text", after="一.txt")
        self.assertEqual(result["already_registered"], ["二.txt", "一.txt"])
        self.assertEqual([item["path"] for item in read_json(self.root / "novel.json")["sources"]],
                         ["一.txt", "评论.md", "二.txt"])

    def test_idempotent_registration_preserves_config_bytes_and_reports_stale_sources(self):
        self.source("正文.txt")
        corpus.register_sources(self.root, ["正文.txt"], "text")
        original = (self.root / "novel.json").read_bytes()
        self.assertTrue(corpus.register_sources(self.root, ["./正文.txt"], "text", encoding="UTF8")["prepare_required"])
        corpus.prepare(self.root)
        result = corpus.register_sources(self.root, ["正文.txt"], "text", encoding="utf-8-sig")
        self.assertFalse(result["prepare_required"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["already_registered"], ["正文.txt"])
        self.assertEqual((self.root / "novel.json").read_bytes(), original)
        self.assertFalse((self.root / ".novel" / "config-backups").exists())
        self.source("正文.txt", "第一章 雪\n正文已改。")
        self.assertTrue(corpus.register_sources(self.root, ["正文.txt"], "text")["prepare_required"])

    def test_metadata_conflicts_do_not_overwrite_or_backup_config(self):
        self.source("正文.txt")
        corpus.register_sources(self.root, ["正文.txt"], "text", title="原名")
        before = (self.root / "novel.json").read_bytes()
        for options in ({"kind": "commentary"}, {"kind": "text", "encoding": "gb18030"}, {"kind": "text", "title": "另一本"}):
            with self.subTest(options=options):
                self.assert_error("SOURCE_CONFLICT", corpus.register_sources, self.root, ["正文.txt"], **options)
                self.assertEqual((self.root / "novel.json").read_bytes(), before)
        self.assertFalse((self.root / ".novel" / "config-backups").exists())

    def test_strict_encoding_and_input_errors_leave_no_config(self):
        self.source("乱码.txt", "第一章 雪\n正文。", "gb18030")
        self.assert_error("ENCODING_ERROR", corpus.register_sources, self.root, ["乱码.txt"], "text")
        self.assert_error("ENCODING_ERROR", corpus.register_sources, self.root, ["乱码.txt"], "text", encoding="not-a-codec")
        self.assert_error("SOURCE_KIND", corpus.register_sources, self.root, ["乱码.txt"], "author")
        self.assert_error("INVALID_INPUT", corpus.register_sources, self.root, [], "text")
        self.assert_error("INVALID_INPUT", corpus.register_sources, self.root, ["bad\x00.txt"], "text")
        self.assert_error("ENCODING_ERROR", corpus.register_sources, self.root, ["乱码.txt"], "text", encoding="utf\x008")
        self.assert_error("INVALID_INPUT", corpus.register_sources, self.root, ["乱码.txt"], "text", before="x", after="y")
        self.assertFalse((self.root / "novel.json").exists())
        corpus.register_sources(self.root, ["乱码.txt"], "text", encoding="gb18030")
        self.assertIn("正文", next(corpus.iter_spans(self.root, corpus.prepare(self.root)))["text"])

    def test_unsafe_missing_generated_and_unknown_anchor_are_rejected(self):
        self.source("正文.txt")
        self.source(".novel/inbox/generated.md")
        self.source("analysis/views/chapters.md")
        self.assert_error("UNSAFE_PATH", corpus.register_sources, self.root, ["../outside.txt"], "text")
        self.assert_error("SOURCE_NOT_FOUND", corpus.register_sources, self.root, ["不存在.txt"], "text")
        for generated in (".novel/inbox/generated.md", "analysis/views/chapters.md"):
            self.assert_error("UNSAFE_PATH", corpus.register_sources, self.root, [generated], "text")
        self.assert_error("UNKNOWN_SOURCE", corpus.register_sources, self.root, ["正文.txt"], "text", before="不存在.txt")
        self.assertFalse((self.root / "novel.json").exists())

    def test_invalid_config_is_never_replaced(self):
        self.source("正文.txt")
        for raw in (b'{"sources":[],"sources":[]}', b'{"format_version":true,"sources":[]}',
                    b'{"format_version":2,"sources":[]}'):
            (self.root / "novel.json").write_bytes(raw)
            with self.assertRaises(NovelError):
                corpus.register_sources(self.root, ["正文.txt"], "text")
            self.assertEqual((self.root / "novel.json").read_bytes(), raw)

    def test_missing_registered_source_does_not_prevent_registering_new_material(self):
        self.configure([{"path": "缺失.txt", "kind": "text", "custom": "保留"}])
        self.source("新访谈.md")
        result = corpus.register_sources(self.root, ["新访谈.md"], "interview")
        self.assertEqual(result["added"], ["新访谈.md"])
        self.assertEqual(read_json(self.root / "novel.json")["sources"][0]["custom"], "保留")
        self.assertEqual(corpus.scan_sources(self.root)["missing"], ["缺失.txt"])

    def test_concurrent_config_change_is_preserved_instead_of_overwritten(self):
        self.source("一.txt")
        self.source("二.txt")
        config = self.configure([{"path": "一.txt", "kind": "text"}])
        changed = dict(config, title="并发修改的书名")
        original_lock = corpus.writer_lock

        @contextmanager
        def concurrent_lock(root):
            atomic_write_json(root / "novel.json", changed)
            with original_lock(root):
                yield

        with mock.patch.object(corpus, "writer_lock", side_effect=concurrent_lock):
            self.assert_error("CONFIG_CHANGED", corpus.register_sources, self.root, ["二.txt"], "text")
        self.assertEqual(read_json(self.root / "novel.json"), changed)
        self.assertFalse((self.root / ".novel" / "config-backups").exists())
        self.assertFalse((self.root / ".novel" / "write.lock").exists())

    def test_source_changed_during_registration_leaves_config_unchanged(self):
        self.source("一.txt")
        self.source("二.txt")
        self.configure([{"path": "一.txt", "kind": "text"}])
        original = (self.root / "novel.json").read_bytes()
        original_lock = corpus.writer_lock

        @contextmanager
        def concurrent_lock(root):
            self.source("二.txt", "第二章 雨\n材料已经改变。")
            with original_lock(root):
                yield

        with mock.patch.object(corpus, "writer_lock", side_effect=concurrent_lock):
            self.assert_error("SOURCE_CHANGED", corpus.register_sources, self.root, ["二.txt"], "text")
        self.assertEqual((self.root / "novel.json").read_bytes(), original)
        self.assertFalse((self.root / ".novel" / "config-backups").exists())

    def test_backup_failure_or_config_replace_failure_preserves_original(self):
        self.source("一.txt")
        self.source("二.txt")
        self.configure([{"path": "一.txt", "kind": "text"}])
        original = (self.root / "novel.json").read_bytes()
        with mock.patch.object(corpus, "atomic_write_text", side_effect=OSError("backup failed")):
            with self.assertRaisesRegex(OSError, "backup failed"):
                corpus.register_sources(self.root, ["二.txt"], "text")
        self.assertEqual((self.root / "novel.json").read_bytes(), original)
        with mock.patch.object(corpus, "atomic_write_json", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                corpus.register_sources(self.root, ["二.txt"], "text")
        self.assertEqual((self.root / "novel.json").read_bytes(), original)
        backups = list((self.root / ".novel" / "config-backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse((self.root / ".novel" / "write.lock").exists())
        result = corpus.register_sources(self.root, ["二.txt"], "text")
        self.assertEqual(Path(result["backup_path"]), backups[0].resolve())


if __name__ == "__main__":
    unittest.main()
