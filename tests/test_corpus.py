import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"))

import corpus
from common import NovelError, atomic_write_json, read_json


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "小说 资料"
        self.root.mkdir()

    def source(self, name, text, encoding="utf-8"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode(encoding))
        return name

    def configure(self, sources, **extra):
        config = {"format_version": 1, "title": "合成小说", "sources": sources, **extra}
        atomic_write_json(self.root / "novel.json", config)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(NovelError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_explicit_order_bom_crlf_and_original_bytes(self):
        first = self.source("正文/乙.txt", "第二章 雨\r\n林青说：‘归来。’😀\r\n\r\n尾声。", "utf-8-sig")
        second = self.source("正文/甲.txt", "第一章 雪\n先前之事。")
        raw = (self.root / first).read_bytes()
        result = corpus.prepare(self.root, sources=[first, second], title="倒叙")
        self.assertEqual([source["path"] for source in result["sources"]], [first, second])
        self.assertEqual([chapter["ordinal"] for chapter in result["chapters"]], [1, 2])
        self.assertEqual(result["title"], "倒叙")
        source = result["sources"][0]
        snapshot = self.root / ".novel" / "snapshots" / result["revision"] / source["snapshot_path"]
        self.assertEqual(snapshot.read_bytes(), raw)
        self.assertEqual(source["hash"], hashlib.sha256(raw).hexdigest())
        spans = list(corpus.iter_spans(self.root))
        self.assertEqual("".join(span["text"] for span in spans if span["source_id"] == source["id"]), raw.decode("utf-8-sig"))
        again = corpus.prepare(self.root)
        self.assertEqual(result["revision"], again["revision"])
        self.assertEqual(result["workspace_id"], again["workspace_id"])

    def test_config_is_required_without_explicit_order(self):
        self.source("正文.txt", "第一章 雪\n雪落。")
        self.assert_error("CONFIG_REQUIRED", corpus.prepare, self.root)
        self.assertFalse((self.root / "novel.json").exists())

    def test_wrong_encoding_has_no_active_snapshot_and_explicit_encoding_works(self):
        self.source("正文.txt", "第一章 雪\n林青归来。", "gb18030")
        self.assert_error("ENCODING_ERROR", corpus.prepare, self.root, sources=["正文.txt"])
        self.assertFalse((self.root / ".novel" / "active.json").exists())
        prepared = corpus.prepare(self.root, sources=["正文.txt"], encoding="gb18030")
        self.assertIn("林青", next(corpus.iter_spans(self.root))["text"])
        self.assertFalse(prepared["needs_review"])

    def test_unsafe_unknown_and_duplicate_sources_are_rejected(self):
        self.source("正文.txt", "第一章 雪\n正文。")
        self.assert_error("UNSAFE_PATH", corpus.prepare, self.root, sources=["../外部.txt"])
        self.assert_error("DUPLICATE_SOURCE", corpus.prepare, self.root, sources=["正文.txt", "./正文.txt"])
        self.assert_error("SOURCE_KIND", corpus.prepare, self.root,
                          sources=[{"path": "正文.txt", "kind": "trusted_author"}])
        self.assertFalse((self.root / ".novel" / "active.json").exists())

    def test_symlink_outside_workspace_is_rejected(self):
        external = Path(self.temporary.name) / "external.txt"
        external.write_text("not a workspace source", encoding="utf-8")
        try:
            (self.root / "link.txt").symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this system")
        self.assert_error("UNSAFE_PATH", corpus.prepare, self.root, sources=["link.txt"])

    def test_retargeted_in_workspace_source_symlink_is_detected(self):
        self.source("first.txt", "第一章 雪\n林青。")
        self.source("second.txt", "第一章 雪\n阿舟。")
        link = self.root / "link.txt"
        try:
            link.symlink_to("first.txt")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this system")
        corpus.prepare(self.root, sources=["link.txt"])
        link.unlink()
        link.symlink_to("second.txt")
        self.assert_error("SOURCE_CHANGED", corpus.load_manifest, self.root)

    def test_uncertain_text_remains_searchable_and_explicit_chapters_confirm_it(self):
        text = "林青在桥上。\n\n没有章节标题。"
        self.source("正文.txt", text)
        first = corpus.prepare(self.root, sources=["正文.txt"])
        self.assertFalse(first["chapters"][0]["confirmed"])
        self.assertTrue(first["needs_review"])
        self.assertEqual(corpus.search(self.root, ["林青"])["total"], 1)
        config = read_json(self.root / "novel.json")
        config["sources"][0]["chapters"] = [{"title": "确认的章节", "start": 0, "end": len(text)}]
        atomic_write_json(self.root / "novel.json", config)
        second = corpus.prepare(self.root)
        self.assertTrue(second["chapters"][0]["confirmed"])
        self.assertFalse(second["needs_review"])

    def test_chapter_overrides_reject_gaps(self):
        self.source("正文.txt", "第一段\n第二段")
        self.configure([{"path": "正文.txt", "kind": "text",
                         "chapters": [{"title": "坏范围", "start": 1, "end": 7}]}])
        self.assert_error("INVALID_CHAPTERS", corpus.prepare, self.root)

    def test_explicit_chapters_can_confirm_repeated_titles(self):
        text = "## 相逢\n林青。\n\n## 相逢\n阿舟。"
        boundary = text.index("## 相逢", 1)
        self.source("正文.md", text)
        self.configure([{"path": "正文.md", "kind": "text", "chapters": [
            {"title": "相逢", "start": 0, "end": boundary},
            {"title": "相逢", "start": boundary, "end": len(text)},
        ]}])
        manifest = corpus.prepare(self.root)
        self.assertFalse(manifest["needs_review"])
        self.assertTrue(all(chapter["confirmed"] for chapter in manifest["chapters"]))

    def test_markdown_duplicate_titles_and_preamble_need_review(self):
        self.source("正文.md", "# 书名\n简介。\n\n## 相逢\n林青来到。\n\n## 相逢\n林青离开。")
        result = corpus.prepare(self.root, sources=["正文.md"])
        self.assertEqual(len(result["chapters"]), 3)
        self.assertTrue(all(not chapter["confirmed"] for chapter in result["chapters"]))
        self.assertEqual(corpus.search(self.root, "简介")["total"], 1)
        self.assertEqual(corpus.search(self.root, "林青")["total"], 2)

    def test_chinese_duplicate_numbers_require_review(self):
        self.source("正文.txt", "第一章 雪\n林青。\n\n第一章 雨\n阿舟。")
        result = corpus.prepare(self.root, sources=["正文.txt"])
        self.assertEqual(len(result["needs_review"]), 2)
        self.assertTrue(all(not chapter["confirmed"] for chapter in result["chapters"]))

    def test_blank_prefix_is_preserved_without_extra_chapter(self):
        text = "\r\n\r\n第一章 雪\r\n林青。"
        self.source("正文.txt", text)
        result = corpus.prepare(self.root, sources=["正文.txt"])
        self.assertEqual(len(result["chapters"]), 1)
        self.assertTrue(result["chapters"][0]["confirmed"])
        self.assertEqual(corpus.read(self.root, chapter_id=result["chapters"][0]["id"])["text"], text)

    def test_long_paragraphs_split_without_losing_text_and_read_paginates(self):
        text = "第一章 长夜\n" + "甲" * 5000 + "😀尾声\r\n"
        self.source("正文.txt", text)
        result = corpus.prepare(self.root, sources=["正文.txt"])
        spans = list(corpus.iter_spans(self.root))
        self.assertTrue(all(len(span["text"]) <= corpus.MAX_SPAN for span in spans))
        self.assertEqual("".join(span["text"] for span in spans), text)
        chapter_id = result["chapters"][0]["id"]
        pages = []
        offset = 0
        while True:
            page = corpus.read(self.root, chapter_id=chapter_id, limit=1000, offset=offset)
            self.assertLessEqual(sum(len(span["text"]) for span in page["spans"]), 1000)
            pages.append(page["text"])
            if not page["truncated"]:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(pages), text)
        self.assert_error("INVALID_LIMIT", corpus.read, self.root, chapter_id=chapter_id, limit=0)
        self.assert_error("INVALID_OFFSET", corpus.read, self.root, chapter_id=chapter_id, offset=len(text) + 1)

    def test_literal_search_counts_overlaps_and_paginates(self):
        self.source("正文.txt", "第一章 雪\n哈哈哈 林青。林青。林青。\n\n第二章 雨\n林青来了。")
        corpus.prepare(self.root, sources=["正文.txt"])
        first = corpus.search(self.root, ["哈哈", "林青"], limit=2)
        second = corpus.search(self.root, ["哈哈", "林青"], limit=2, offset=2)
        third = corpus.search(self.root, ["哈哈", "林青"], limit=2, offset=4)
        self.assertEqual(first["counts"], {"哈哈": 2, "林青": 4})
        self.assertEqual(first["total"], 6)
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(second["next_offset"], 4)
        self.assertIsNone(third["next_offset"])
        all_matches = first["matches"] + second["matches"] + third["matches"]
        self.assertEqual(len({(match["query"], match["start"]) for match in all_matches}), 6)
        self.assertEqual(corpus.search(self.root, "林青", chapter_from=2, chapter_to=2)["total"], 1)
        self.assertEqual(corpus.search(self.root, "[林青]")["total"], 0)
        self.assertTrue(all(len(match["snippet"]) <= 240 for match in all_matches))

    def test_supplements_require_explicit_source_filter(self):
        self.source("正文.txt", "第一章 雪\n林青不知真相。")
        self.source("评论.md", "评论声称：林青早已知道真相。")
        self.configure([{"path": "正文.txt", "kind": "text"}, {"path": "评论.md", "kind": "commentary"}])
        result = corpus.prepare(self.root)
        self.assertEqual(corpus.search(self.root, "林青")["total"], 1)
        self.assertEqual(corpus.search(self.root, "林青", kinds=["text", "commentary"])["total"], 2)
        comment = next(corpus.iter_spans(self.root, result, kinds=["commentary"]))
        self.assertIsNone(comment["chapter_id"])
        evidence = corpus.resolve_evidence(self.root, [{"span_id": comment["span_id"], "quote": "早已知道"}])
        self.assertEqual(evidence[0]["source_kind"], "commentary")
        self.assert_error("SOURCE_KIND", corpus.search, self.root, "林青", kinds=["unclassified"])
        self.assertEqual(corpus.search(self.root, "林青", kinds=["commentary"], chapter_from=1)["total"], 0)

    def test_quote_positions_use_codepoints_and_preserve_crlf(self):
        text = "第一章 雪\r\n😀林青说：‘归来。’\r\n阿舟点头。"
        self.source("正文.txt", text)
        result = corpus.prepare(self.root, sources=["正文.txt"])
        span = next(corpus.iter_spans(self.root))
        quote = "😀林青说：‘归来。’\r\n阿舟"
        evidence = corpus.resolve_evidence(self.root, [{"span_id": span["span_id"], "quote": quote}])[0]
        self.assertEqual(evidence["start"], text.index(quote))
        self.assertEqual(text[evidence["start"]:evidence["end"]], quote)
        self.assertEqual(evidence["source_revision"], result["revision"])
        self.assertEqual(evidence["unit_hash"], result["chapters"][0]["content_hash"])
        self.assert_error("QUOTE_NOT_FOUND", corpus.resolve_evidence, self.root,
                          [{"span_id": span["span_id"], "quote": quote.replace("\r\n", "\n")}])

    def test_ambiguous_quote_requires_more_context(self):
        self.source("正文.txt", "第一章 雪\n林青说：‘归来。’阿舟也说：‘归来。’")
        corpus.prepare(self.root, sources=["正文.txt"])
        span = next(corpus.iter_spans(self.root))
        error = self.assert_error("AMBIGUOUS_QUOTE", corpus.resolve_evidence, self.root,
                                  [{"span_id": span["span_id"], "quote": "归来。"}])
        self.assertEqual(error.details["total"], 2)
        self.assertEqual(len(error.details["positions"]), 2)
        evidence = corpus.resolve_evidence(self.root, [{"span_id": span["span_id"], "quote": "阿舟也说：‘归来。’"}])
        self.assertEqual(len(evidence), 1)

    def test_cross_span_quotes_are_searchable_but_not_one_piece_of_evidence(self):
        prefix = "第一章 夜\n"
        text = prefix + "甲" * (corpus.MAX_SPAN - len(prefix) - 1) + "桥上" + "乙" * 20
        self.source("正文.txt", text)
        corpus.prepare(self.root, sources=["正文.txt"])
        match = corpus.search(self.root, "桥上")["matches"][0]
        self.assertIsNone(match["span_id"])
        self.assertEqual(len(match["span_ids"]), 2)
        self.assert_error("QUOTE_NOT_FOUND", corpus.resolve_evidence, self.root,
                          [{"span_id": match["span_ids"][0], "quote": "桥上"}])

    def test_evidence_rejects_unknown_out_of_scope_and_client_generated_offsets(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        corpus.prepare(self.root, sources=["正文.txt"])
        span = next(corpus.iter_spans(self.root))
        self.assert_error("UNKNOWN_SPAN", corpus.resolve_evidence, self.root,
                          [{"span_id": "sp_wrong", "quote": "林青"}])
        self.assert_error("EVIDENCE_OUT_OF_RANGE", corpus.resolve_evidence, self.root,
                          [{"span_id": span["span_id"], "quote": "林青"}], allowed_span_ids=[])
        self.assert_error("INVALID_EVIDENCE", corpus.resolve_evidence, self.root,
                          [{"span_id": span["span_id"], "quote": "林青", "start": 7}])

    def test_source_edit_preserves_history_and_unaffected_chapter_ids(self):
        self.source("正文.txt", "第一章 雪\n林青归来。\n\n第二章 雨\n阿舟等候。")
        first = corpus.prepare(self.root, sources=["正文.txt"])
        first_spans = list(corpus.iter_spans(self.root))
        old_span = first_spans[0]
        unchanged_span = next(span for span in first_spans if span["chapter_ordinal"] == 2)
        self.source("正文.txt", "第一章 雪\n林青不再归来。新消息。\n\n第二章 雨\n阿舟等候。")
        self.assert_error("SOURCE_CHANGED", corpus.load_manifest, self.root)
        self.assertEqual(corpus.source_changes(self.root, first)[0]["reason"], "content_changed")
        self.assertIn("林青归来", corpus.get_span(self.root, old_span["span_id"], first["revision"])["text"])
        second = corpus.prepare(self.root)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(first["workspace_id"], second["workspace_id"])
        self.assertEqual(first["chapters"][1]["id"], second["chapters"][1]["id"])
        current_span = corpus.get_span(self.root, unchanged_span["span_id"])
        self.assertNotEqual(unchanged_span["start"], current_span["start"])
        self.assertEqual(unchanged_span["unit_hash"], current_span["unit_hash"])
        self.assert_error("UNKNOWN_SPAN", corpus.resolve_evidence, self.root,
                          [{"span_id": old_span["span_id"], "quote": "林青归来"}])
        resolved = corpus.resolve_evidence(self.root, [{"span_id": old_span["span_id"], "quote": "林青归来"}],
                                           revision=first["revision"])
        self.assertEqual(resolved[0]["source_revision"], first["revision"])

    def test_config_only_change_creates_a_new_revision(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        first = corpus.prepare(self.root, sources=["正文.txt"])
        config = read_json(self.root / "novel.json")
        config["sources"][0]["encoding"] = "UTF8"
        atomic_write_json(self.root / "novel.json", config)
        self.assert_error("SOURCE_CHANGED", corpus.load_manifest, self.root)
        second = corpus.prepare(self.root)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(first["spans"], second["spans"])

    def test_mid_prepare_edit_preserves_active_pointer(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        first = corpus.prepare(self.root, sources=["正文.txt"])
        original_hash = corpus.file_hash
        changed = False

        def mutate_during_staging(path):
            nonlocal changed
            if not changed and "staging" in Path(path).parts:
                changed = True
                self.source("正文.txt", "第一章 雪\n阿舟。")
            return original_hash(path)

        with mock.patch.object(corpus, "file_hash", side_effect=mutate_during_staging):
            self.assert_error("SOURCE_CHANGED", corpus.prepare, self.root)
        self.assertEqual(read_json(self.root / ".novel" / "active.json")["revision"], first["revision"])
        self.assertEqual(list((self.root / ".novel" / "staging").iterdir()), [])

    def test_pointer_write_failure_keeps_previous_snapshot_active(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        first = corpus.prepare(self.root, sources=["正文.txt"])
        self.source("正文.txt", "第一章 雪\n阿舟。")
        original_write = corpus.atomic_write_json

        def fail_pointer(path, data):
            if Path(path).name == "active.json":
                raise OSError("simulated replace failure")
            return original_write(path, data)

        with mock.patch.object(corpus, "atomic_write_json", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                corpus.prepare(self.root)
        self.assertEqual(read_json(self.root / ".novel" / "active.json")["revision"], first["revision"])
        self.assertEqual(corpus.load_manifest(self.root, first["revision"])["revision"], first["revision"])

    def test_snapshot_tampering_is_detected(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        first = corpus.prepare(self.root, sources=["正文.txt"])
        source = first["sources"][0]
        snapshot = self.root / ".novel" / "snapshots" / first["revision"]
        (snapshot / source["text_path"]).write_text("forged decoded text", encoding="utf-8")
        self.assert_error("CORRUPT_SNAPSHOT", corpus.search, self.root, "林青")
        self.assert_error("CORRUPT_SNAPSHOT", corpus.prepare, self.root)

    def test_oversized_manifest_does_not_switch_active_version(self):
        self.source("正文.txt", "第一章 雪\n林青。")
        with mock.patch.object(corpus, "MAX_MANIFEST_BYTES", 128):
            self.assert_error("INPUT_TOO_LARGE", corpus.prepare, self.root, sources=["正文.txt"])
        self.assertFalse((self.root / ".novel" / "active.json").exists())

    def test_invalid_revisions_and_boolean_config_versions_are_rejected(self):
        self.assert_error("INVALID_REVISION", corpus.load_manifest, self.root, "../../outside")
        self.source("正文.txt", "第一章 雪\n林青。")
        self.configure([{"path": "正文.txt", "kind": "text"}], format_version=True)
        self.assert_error("FORMAT_VERSION", corpus.prepare, self.root)


if __name__ == "__main__":
    unittest.main()
