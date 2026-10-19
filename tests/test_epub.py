"""Real ZIP/package fixtures exercise conversion, provenance and restart behavior."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/novel-research/scripts"
sys.path.insert(0, str(SCRIPTS))

import analysis
import corpus
import epub
import memory
from common import NovelError, atomic_write_json, file_hash, load_record


def book(path, documents=None, version="3.0", extra=None, spine=None, package_extra=""):
    documents = documents or [
        ("后排序.xhtml", '<h1>第一章 渡口</h1><p>林青只<em>怀疑</em>来客的身份🙂。</p>'),
        ("前排序.xhtml", '<h1>第二章 回信</h1><p>林青读信后才知道真相。</p>'),
    ]
    items = ''.join(f'<item id="d{i}" href="Text/{name}" media-type="application/xhtml+xml"/>'
                    for i, (name, _) in enumerate(documents))
    refs = spine if spine is not None else ''.join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    package = (f'<package xmlns="http://www.idpf.org/2007/opf" version="{version}">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<dc:title>合成 EPUB</dc:title><dc:creator>测试作者</dc:creator></metadata>'
               f'{package_extra}<manifest>{items}</manifest><spine>{refs}</spine></package>')
    members = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                                   b'<rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/>'
                                   b'</rootfiles></container>',
        "OEBPS/book.opf": package.encode("utf-8"),
    }
    for name, body in documents:
        members[f"OEBPS/Text/{name}"] = (body if isinstance(body, bytes) else
            ('<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" '
             'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>资源标题</title>'
             '<style>不要读这个样式</style></head><body>' + body + '</body></html>').encode("utf-8"))
    members.update(extra or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            if value is not None:
                archive.writestr(name, value)
    return members


class EpubTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="EPUB 中文 路径 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.relative = "原书/测试.epub"
        self.path = self.root / self.relative
        book(self.path)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(NovelError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def convert_and_prepare(self):
        converted = epub.convert(self.root, self.relative)
        corpus.register_sources(self.root, [converted["output_path"]], "text")
        return converted, corpus.prepare(self.root)

    def cli(self, *args, ok=True):
        completed = subprocess.run([sys.executable, "-S", str(SCRIPTS / "novel.py"),
                                    "--workspace", str(self.root), *args],
                                   cwd=self.root.parent, capture_output=True, encoding="utf-8")
        self.assertEqual(completed.returncode == 0, ok, completed.stdout + completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["ok"], ok)
        return payload["result" if ok else "error"]

    def test_spine_order_unicode_inline_text_and_original_snapshot(self):
        original = self.path.read_bytes()
        result = epub.convert(self.root, self.relative)
        value = (self.root / result["output_path"]).read_text(encoding="utf-8")
        self.assertLess(value.index("第一章"), value.index("第二章"))
        self.assertIn("林青只怀疑来客的身份🙂。", value)
        self.assertNotIn("不要读这个样式", value)
        record = load_record(self.root / result["record_path"])
        self.assertEqual((self.root / record["original_snapshot"]).read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(result["title"], "合成 EPUB")
        self.assertEqual([d["resource"] for d in record["documents"]],
                         ["OEBPS/Text/后排序.xhtml", "OEBPS/Text/前排序.xhtml"])
        for document in record["documents"]:
            self.assertTrue(value[document["start"]:document["end"]].startswith("# "))
        self.assertFalse((self.root / "novel.json").exists())

    def test_epub2_multiple_chapters_per_resource_entities_notes_and_ruby(self):
        book(self.path, version="2.0", documents=[("all.xhtml",
            '<h1>第一章 渡口</h1><p>林<ruby>青<rt>qīng</rt></ruby>&nbsp;&amp;雪<br/>回来了。</p>'
            '<h1>第二章 灯</h1><p>请看<a href="#n1">[1]</a>。</p>'
            '<aside id="n1" epub:type="footnote"><p>灯的旧名。</p></aside>')])
        result, manifest = self.convert_and_prepare()
        self.assertEqual(len(manifest["chapters"]), 2)
        value = (self.root / result["output_path"]).read_text(encoding="utf-8")
        self.assertIn("林青\u00a0&雪\n回来了。", value)
        self.assertNotIn("qīng", value)
        self.assertIn("[脚注]", value)
        self.assertIn("灯的旧名。", value)

    def test_no_heading_and_auxiliary_text_are_retained_with_review_warning(self):
        book(self.path, documents=[("preface.xhtml", "<p>这是没有标题的序言。</p>"),
                                    ("notes.xhtml", "<h2>附注</h2><p>附注仍能检索。</p>")],
             spine='<itemref idref="d0"/><itemref idref="d1" linear="no"/>')
        result, _ = self.convert_and_prepare()
        self.assertTrue(result["requires_review"])
        self.assertTrue(any("synthetic_heading" in item for item in result["warnings"]))
        self.assertTrue(any("auxiliary_content" in item for item in result["warnings"]))
        self.assertEqual(corpus.search(self.root, "附注仍能检索")["total"], 1)
        self.assertTrue(analysis.status(self.root)["conversion_warnings"])

    def test_font_obfuscation_allowed_but_encrypted_spine_rejected(self):
        xml = '<encryption><EncryptedData><CipherData><CipherReference URI="{}"/></CipherData></EncryptedData></encryption>'
        book(self.path, extra={"META-INF/encryption.xml": xml.format("OEBPS/fonts/test.otf").encode()})
        epub.convert(self.root, self.relative)
        previous = epub.conversion_info(self.root, self.relative)
        book(self.path, extra={"META-INF/encryption.xml": xml.format("OEBPS/Text/后排序.xhtml").encode()})
        self.error("EPUB_ENCRYPTED", epub.convert, self.root, self.relative)
        self.assertEqual(epub.conversion_info(self.root, self.relative), previous)

    def test_nontext_images_empty_and_fixed_layout_do_not_claim_full_conversion(self):
        book(self.path, documents=[("cover.xhtml", '<p><img src="cover.png"/></p>'),
                                    ("text.xhtml", "<h1>第一章 雪</h1><p>文字可读。</p>")])
        result = epub.convert(self.root, self.relative)
        self.assertTrue(any("empty_or_image_only" in item for item in result["warnings"]))
        book(self.path, documents=[("cover.xhtml", '<img src="cover.png"/>')])
        self.error("EPUB_NO_TEXT", epub.convert, self.root, self.relative)
        book(self.path, package_extra='<metadata><meta property="rendition:layout">pre-paginated</meta></metadata>')
        self.error("EPUB_LAYOUT", epub.convert, self.root, self.relative)

    def test_zip_and_resource_traversal_missing_spine_and_xml_entities_rejected(self):
        book(self.path, extra={"../escape.txt": b"escape"})
        self.error("EPUB_PATH", epub.convert, self.root, self.relative)
        book(self.path, extra={"OEBPS/Text/后排序.xhtml": None})
        self.error("EPUB_RESOURCE", epub.convert, self.root, self.relative)
        book(self.path, spine='<itemref idref="missing"/>')
        self.error("EPUB_PACKAGE", epub.convert, self.root, self.relative)
        book(self.path, extra={"META-INF/container.xml": b'<!DOCTYPE container [<!ENTITY a "a">]><container/>'})
        self.error("EPUB_XML", epub.convert, self.root, self.relative)
        self.path.write_bytes(b"not a zip")
        self.error("EPUB_ZIP", epub.convert, self.root, self.relative)
        self.assertFalse((self.root / "sources/converted").exists())

    def test_archive_and_expanded_member_limits(self):
        with mock.patch.object(epub, "MAX_ARCHIVE", 10):
            self.error("EPUB_TOO_LARGE", epub.convert, self.root, self.relative)
        with mock.patch.object(epub, "MAX_EXPANDED", 10):
            self.error("EPUB_TOO_LARGE", epub.convert, self.root, self.relative)
        with mock.patch.object(epub, "MAX_MEMBER", 10):
            self.error("EPUB_TOO_LARGE", epub.convert, self.root, self.relative)

    def test_text_resources_outside_spine_are_reported_as_missing_coverage(self):
        members = book(self.path)
        package = members["OEBPS/book.opf"].replace(b'</manifest>',
            b'<item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/></manifest>')
        book(self.path, extra={"OEBPS/book.opf": package,
                               "OEBPS/notes.xhtml": b'<html><body><p>outside note</p></body></html>'})
        result = epub.convert(self.root, self.relative)
        self.assertTrue(result["requires_review"])
        self.assertTrue(any("outside_spine: OEBPS/notes.xhtml" in warning for warning in result["warnings"]))
        self.assertNotIn("outside note", (self.root / result["output_path"]).read_text(encoding="utf-8"))

    def test_idempotent_and_user_edited_derived_output_is_not_overwritten(self):
        result = epub.convert(self.root, self.relative)
        immutable = {path: path.read_bytes() for path in (self.root / ".novel/epub").rglob("*") if path.is_file()}
        self.assertEqual(epub.convert(self.root, self.relative), result)
        self.assertEqual({path: path.read_bytes() for path in immutable}, immutable)
        output = self.root / result["output_path"]
        output.write_text("我手工改过这份文本", encoding="utf-8")
        book(self.path, documents=[("new.xhtml", "<h1>第一章 改</h1><p>新版。</p>")])
        self.error("EPUB_OUTPUT_CHANGED", epub.convert, self.root, self.relative)
        self.assertEqual(output.read_text(encoding="utf-8"), "我手工改过这份文本")

    def test_retry_after_output_written_but_pointer_failed(self):
        real_write = epub.atomic_write_json
        def fail_pointer(path, value):
            if path.name == "current.json":
                raise OSError("simulated disk error")
            return real_write(path, value)
        with mock.patch.object(epub, "atomic_write_json", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                epub.convert(self.root, self.relative)
        result = epub.convert(self.root, self.relative)
        corpus.register_sources(self.root, [result["output_path"]], "text")
        self.assertEqual(len(corpus.prepare(self.root)["chapters"]), 2)
        self.assertFalse((self.root / ".novel/write.lock").exists())

    def test_discovery_registration_repeat_and_source_update_in_separate_processes(self):
        initial_hash = file_hash(self.path)
        scan = self.cli("sources", "scan", "--new-only")
        self.assertEqual(scan["files"][0]["format"], "epub")
        converted = self.cli("epub", "convert", self.relative)
        self.assertEqual([item["path"] for item in self.cli("sources", "scan", "--new-only")["files"]], [self.relative])
        self.cli("sources", "add", converted["output_path"], "--kind", "text")
        first = self.cli("prepare")
        self.assertEqual(self.cli("sources", "scan", "--new-only")["new_count"], 0)
        self.assertEqual(self.cli("prepare")["source_revision"], first["source_revision"])
        self.assertEqual(file_hash(self.path), initial_hash)
        book(self.path, documents=[("新.xhtml", "<h1>第一章 新</h1><p>新版原文。</p>")])
        self.assertEqual(self.cli("status")["source_changes"][-1]["reason"], "epub_changed")
        self.assertEqual(self.cli("search", "来客", ok=False)["code"], "SOURCE_CHANGED")
        self.assertEqual(self.cli("prepare", ok=False)["code"], "EPUB_STALE")
        second_conversion = self.cli("epub", "convert", self.relative)
        self.assertEqual(second_conversion["output_path"], converted["output_path"])
        self.assertNotEqual(self.cli("prepare")["source_revision"], first["source_revision"])
        self.assertEqual(self.cli("search", "新版原文")["total"], 1)

    def test_historical_quotes_keep_original_epub_resource_after_update(self):
        converted, manifest = self.convert_and_prepare()
        match = corpus.search(self.root, "林青只怀疑来客的身份🙂。")["matches"][0]
        reference = {"span_id": match["span_id"], "quote": "林青只怀疑来客的身份🙂。"}
        evidence = corpus.resolve_evidence(self.root, [reference])
        self.assertEqual(evidence[0]["epub_locations"][0]["resource"], "OEBPS/Text/后排序.xhtml")
        book(self.path, documents=[("changed.xhtml", "<h1>第一章 改</h1><p>另一个版本。</p>")])
        epub.convert(self.root, self.relative)
        corpus.prepare(self.root)
        self.assertEqual(corpus.resolve_evidence(self.root, [reference], revision=manifest["revision"]), evidence)
        old = corpus.read(self.root, span_id=match["span_id"], revision=manifest["revision"])
        self.assertEqual(old["epub_origin"]["record_path"], converted["record_path"])
        self.assertIn(reference["quote"], old["text"])

    def test_check_detects_damaged_saved_original_even_if_derived_text_is_intact(self):
        converted, _ = self.convert_and_prepare()
        original = load_record(self.root / converted["record_path"])["original_snapshot"]
        (self.root / original).write_bytes(b"damaged")
        error = self.cli("check", ok=False)
        self.assertEqual(error["code"], "CHECK_FAILED")
        self.assertFalse(error["details"]["epub"]["valid"])
        self.assertEqual(error["details"]["epub"]["errors"][0]["code"], "CORRUPT_SNAPSHOT")

    def test_full_book_tasks_resume_validate_and_keep_navigation(self):
        self.convert_and_prepare()
        references = []
        for ordinal, quote in [(1, "林青只怀疑来客的身份🙂。"), (2, "林青读信后才知道真相。")]:
            task = self.cli("task", "next")["task"]
            self.assertEqual(task["chapter"]["ordinal"], ordinal)
            span = next(s for s in task["spans"] if quote in s["text"])
            reference = {"span_id": span["span_id"], "quote": quote}
            references.append(reference)
            body = {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                    "overview": quote, "mentions": [{"name": "林青", "evidence": [reference]}],
                    "claims": [{"kind": "belief", "text": quote, "evidence": [reference]}],
                    "open_questions": [{"text": "来客的身份与动机是否一致？", "evidence": [reference]}]}
            atomic_write_json(self.root / ".novel/draft.json", body)
            self.cli("task", "submit", ".novel/draft.json")
        status = self.cli("status")
        self.assertTrue(status["coverage_complete"])
        self.assertEqual(status["completed"], 2)
        self.assertTrue(self.cli("check", "--rebuild")["analysis"]["valid"])
        self.assertIn("林青", (self.root / "analysis/views/characters.md").read_text(encoding="utf-8"))
        self.assertIn("来客的身份与动机", (self.root / "analysis/views/questions.md").read_text(encoding="utf-8"))
        self.assertIn("已接收 2 / 2", (self.root / "analysis/views/library.md").read_text(encoding="utf-8"))
        questions = self.cli("search", "身份与动机", "--in", "analysis")
        self.assertEqual(questions["total"], 2)
        self.assertTrue(all(item["kind"] == "open_question" and item["navigation_only"] for item in questions["matches"]))
        self.assertEqual(self.cli("sources", "scan", "--new-only")["new_count"], 0)
        report_body = {"case_id": "belief", "question": "林青的认知如何变化？", "findings": [
            {"kind": "belief", "text": "第一章只表达怀疑。", "evidence": references[:1]},
            {"kind": "belief", "text": "第二章读信后才知道真相。", "evidence": references[1:]},
        ], "unresolved": ["真相的具体内容需要另查上下文。"]}
        atomic_write_json(self.root / ".novel/research.json", report_body)
        self.cli("research", "save", ".novel/research.json")
        self.cli("research", "render", "belief")
        report = (self.root / "research/belief/report.md").read_text(encoding="utf-8")
        self.assertIn(self.relative, report)
        self.assertIn("OEBPS/Text/后排序.xhtml", report)
        self.assertIn("converted text offsets", report)
        self.assertEqual(self.cli("sources", "scan", "--new-only")["new_count"], 0)
        self.assertTrue(self.cli("check")["memory"]["valid"])
        self.assertTrue(analysis.check_records(self.root)["valid"])
        self.assertTrue(memory.check_records(self.root)["valid"])


if __name__ == "__main__":
    unittest.main()
