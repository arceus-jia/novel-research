"""Cross-book evidence stays attached to its book and historical version."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analysis
import corpus
import memory
import topics
from common import NovelError, atomic_write_json, digest


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="跨书 研究 ")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.a = self.project / "原篇"
        self.b = self.project / "增补篇"
        self.root = self.project / "跨书专题"
        for book, text in ((self.a, "第一章 初遇\n青客只怀疑故人身份。\n第二章 回信\n青客此时才知道真相。\n"),
                           (self.b, "第一章 初遇\n青客此前见过故人。\n第二章 回信\n青客没有确认他的身份。\n")):
            book.mkdir()
            (book / "正文.txt").write_text(text, encoding="utf-8")
            (book / "评论.md").write_text("读者推测青客早已知道身份。\n", encoding="utf-8")
            corpus.prepare(book, title=book.name, sources=[
                {"path": "正文.txt", "kind": "text"}, {"path": "评论.md", "kind": "commentary"}])
        topics.init_topic(self.root, [str(self.a), str(self.b)], title="身份对照")
        books = topics.status(self.root)["books"]
        self.a_id = next(book["id"] for book in books if book["title"] == "原篇")
        self.b_id = next(book["id"] for book in books if book["title"] == "增补篇")
        self.a_ref = self.reference(self.a_id, "青客只怀疑故人身份。")
        self.b_ref = self.reference(self.b_id, "青客此前见过故人。")

    def reference(self, book_id, quote, collection="text"):
        result = topics.search(self.root, [quote], book_ids=[book_id], collection=collection)
        item = result["matches"][0]
        return {"book_id": book_id, "span_id": item["span_id"], "quote": quote}

    def body(self, **fields):
        return {"case_id": "identity", "question": "两部作品如何描写人物认知？",
                "findings": [{"kind": "inference", "text": "两部作品的认知描写有出入，需结合上下文理解。",
                              "evidence": [self.a_ref, self.b_ref]}], **fields}

    def error(self, call, code=None):
        with self.assertRaises(NovelError) as caught:
            call()
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_registration_is_idempotent_and_keeps_books_untouched(self):
        original = {p: p.read_bytes() for book in (self.a, self.b) for p in book.rglob("*") if p.is_file()}
        config = (self.root / "topic.json").read_bytes()
        topics.init_topic(self.root, [str(self.a), str(self.b)])
        self.assertEqual((self.root / "topic.json").read_bytes(), config)
        for path, raw in original.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.root / "novel.json").exists())
        self.assertFalse((self.root / "sources").exists())
        registry = json.loads(config)
        self.assertTrue(all(not Path(book["path"]).is_absolute() for book in registry["books"]))
        self.assertTrue((self.root / "library.md").is_file())

    def test_cross_book_quotes_and_reports_keep_book_identity(self):
        saved = memory.save_research(self.root, self.body())
        self.assertEqual({e["book_id"] for e in saved["findings"][0]["evidence"]}, {self.a_id, self.b_id})
        for item in saved["findings"][0]["evidence"]:
            book = self.a if item["book_id"] == self.a_id else self.b
            self.assertEqual(item["book_workspace_id"], corpus.load_manifest(book)["workspace_id"])
            self.assertEqual(item["source_revision"], corpus.load_manifest(book)["revision"])
        rendered = memory.render_research(self.root, "identity")
        self.assertFalse(rendered["stale"])
        self.assertIn("原篇", rendered["report"])
        self.assertIn("增补篇", rendered["report"])
        self.assertIn("第 1 章", rendered["report"])
        self.assertIn(self.a_ref["quote"], rendered["report"])
        self.assertTrue(memory.check_records(self.root)["valid"])

    def test_unknown_books_wrong_quotes_and_single_book_omissions_are_rejected(self):
        for reference in ({**self.a_ref, "book_id": "missing"},
                          {**self.a_ref, "book_id": self.b_id},
                          {key: value for key, value in self.a_ref.items() if key != "book_id"}):
            body = self.body(findings=[{"kind": "narration", "text": "待核对的说法。", "evidence": [reference]}])
            self.error(lambda: memory.save_research(self.root, body))
        self.assertEqual(list((self.root / "research").glob("*/records/*.json")), [])

    def test_each_book_has_its_own_chapter_and_source_scope(self):
        scope = {"books": {self.a_id: {"chapter_to": 1}, self.b_id: {"chapter_from": 2}}}
        self.error(lambda: memory.save_research(self.root, self.body(scope=scope)), "out_of_scope")
        b_later = self.reference(self.b_id, "青客没有确认他的身份。")
        saved = memory.save_research(self.root, self.body(scope=scope, findings=[{
            "kind": "inference", "text": "分别比较两书的指定范围。", "evidence": [self.a_ref, b_later]}]))
        self.assertEqual(len(saved["findings"][0]["evidence"]), 2)
        commentary = self.reference(self.b_id, "读者推测青客早已知道身份。", collection="supplements")
        self.error(lambda: memory.save_research(self.root, self.body(case_id="comment", findings=[{
            "kind": "speech", "text": "评论提出另一种读法。", "evidence": [commentary]}])), "out_of_scope")
        allowed = memory.save_research(self.root, self.body(case_id="comment", scope={"books": {
            self.b_id: {"source_kinds": ["commentary"]}}}, findings=[{
                "kind": "speech", "text": "评论提出另一种读法。", "evidence": [commentary]}]))
        self.assertEqual(allowed["findings"][0]["evidence"][0]["source_kind"], "commentary")
        self.error(lambda: memory.save_research(self.root, self.body(scope={"books": {"missing": {}}})))

    def test_source_updates_keep_old_quotes_and_allow_review_in_same_case(self):
        first = memory.save_research(self.root, self.body())
        path = self.root / "research/identity/records" / (first["record_id"] + ".json")
        original = path.read_bytes()
        text = self.a / "正文.txt"
        text.write_text(text.read_text(encoding="utf-8").replace("只怀疑", "仍怀疑"), encoding="utf-8")
        self.assertTrue(topics.status(self.root)["source_changes"])
        self.assertTrue(topics.status(self.root)["cases"][0]["needs_review"])
        old = memory.render_research(self.root, "identity")
        self.assertTrue(old["stale"])
        self.assertTrue(old["needs_review"])
        self.assertIn("青客只怀疑故人身份。", old["report"])
        corpus.prepare(self.a)
        self.a_ref = self.reference(self.a_id, "青客仍怀疑故人身份。")
        second = memory.save_research(self.root, self.body())
        self.assertNotEqual(first["source_revision"], second["source_revision"])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(memory.render_research(self.root, "identity")["stale"])
        historical = topics.read(self.root, self.a_id, span_id=self.a_ref["span_id"], revision=second["source_revision"])
        self.assertIn("仍怀疑", historical["text"])
        old_match = topics.search(self.root, ["只怀疑"], book_ids=[self.a_id], revision=first["source_revision"])["matches"][0]
        historical = topics.read(self.root, self.a_id, span_id=old_match["span_id"], revision=first["source_revision"])
        self.assertIn("只怀疑", historical["text"])
        self.assertEqual(len(list(path.parent.glob("*.json"))), 2)
        self.assertTrue(memory.check_records(self.root)["valid"])

    def test_book_corrections_flag_topic_but_preferences_do_not(self):
        raw = {key: value for key, value in self.a_ref.items() if key != "book_id"}
        book_record = memory.save_research(self.a, {"case_id": "identity", "question": "人物是否已知？", "findings": [
            {"kind": "belief", "text": "人物目前只怀疑。", "evidence": [raw]}]})
        memory.save_research(self.root, self.body())
        memory.save_note(self.a, {"type": "preference", "origin": "user", "content": "关注身份线索。"})
        self.assertFalse(memory.render_research(self.root, "identity")["stale"])
        correction = memory.save_note(self.a, {"type": "correction", "origin": "agent", "status": "applied", "content": "应保留怀疑与知道的区别。",
            "target_id": book_record["findings"][0]["id"], "evidence": [raw]})
        self.assertTrue(memory.render_research(self.root, "identity")["needs_review"])
        memory.save_research(self.root, self.body())
        self.assertFalse(memory.render_research(self.root, "identity")["stale"])
        memory.save_note(self.a, {"type": "withdrawal", "origin": "user", "content": "撤回这条更正，重新讨论。",
                                 "target_id": correction["record_id"]})
        self.assertTrue(memory.render_research(self.root, "identity")["needs_review"])
        topics.rebuild_view(self.root)
        self.assertIn("需复核", (self.root / "library.md").read_text(encoding="utf-8"))

    def test_topic_discussion_corrections_and_withdrawals_persist(self):
        saved = memory.save_research(self.root, self.body())
        note = memory.save_note(self.root, {"type": "correction", "origin": "user", "status": "applied", "content": "这两种描述不一定矛盾。",
            "case_id": "identity", "target_id": saved["findings"][0]["id"], "evidence": [self.a_ref, self.b_ref]})
        self.assertEqual(memory.recall_notes(self.root)["notes"][0]["effective_status"], "applied")
        self.assertIn("[superseded]", memory.render_research(self.root, "identity")["report"])
        memory.save_note(self.root, {"type": "withdrawal", "origin": "user", "content": "撤回这条更正，继续讨论。", "target_id": note["record_id"]})
        self.assertIn("[needs_review]", memory.render_research(self.root, "identity")["report"])
        self.assertTrue(memory.check_records(self.root)["valid"])

    def test_book_identity_is_verified_and_topic_cannot_be_inside_a_book(self):
        config_path = self.root / "topic.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        original = config_path.read_bytes()
        config["books"][0]["path"] = str(self.b)
        atomic_write_json(config_path, config)
        self.error(lambda: topics.status(self.root), "workspace_mismatch")
        config_path.write_bytes(original)
        self.error(lambda: topics.init_topic(self.a / "跨书专题", [str(self.a), str(self.b)]))
        duplicate = self.project / "复制原篇"
        shutil.copytree(self.a, duplicate)
        self.error(lambda: topics.init_topic(self.root, [str(duplicate)]))
        self.assertEqual(config_path.read_bytes(), original)

    def test_identical_span_ids_in_two_books_remain_namespaced(self):
        text = (self.a / "正文.txt").read_bytes()
        (self.b / "正文.txt").write_bytes(text)
        corpus.prepare(self.b)
        refs = [self.reference(book_id, "青客只怀疑故人身份。") for book_id in (self.a_id, self.b_id)]
        self.assertEqual(refs[0]["span_id"], refs[1]["span_id"])
        saved = memory.save_research(self.root, self.body(findings=[{
            "kind": "inference", "text": "两书此处有相同文字。", "evidence": refs}]))
        evidence = saved["findings"][0]["evidence"]
        self.assertNotEqual(evidence[0]["book_workspace_id"], evidence[1]["book_workspace_id"])

    def test_existing_analysis_modes_and_corrections_are_federated(self):
        for book, mode, reference in ((self.a, "analyze", self.a_ref), (self.b, "skim", self.b_ref)):
            task = analysis.next_task(book, mode=mode)["task"]
            evidence = [{key: value for key, value in reference.items() if key != "book_id"}]
            saved = analysis.submit_task(book, {
                "task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                "overview": reference["quote"], "mentions": [], "open_questions": [],
                "claims": [{"kind": "belief", "text": reference["quote"], "evidence": evidence}]})["record"]
            if book == self.a:
                memory.save_note(book, {"type": "correction", "origin": "agent", "status": "applied",
                    "content": "只描述当前怀疑，不能推断为已知。", "target_id": saved["claims"][0]["id"], "evidence": evidence})
        result = self.cli(self.root, "search", "青客", "--in", "analysis")
        self.assertEqual({item["mode"] for item in result["matches"]}, {"skim", "analyze"})
        claim = next(item for item in result["matches"] if item["book_id"] == self.a_id and item["kind"] == "belief")
        self.assertEqual(claim["current_status"], "superseded")
        self.assertTrue(all(item["book_id"] == self.a_id for item in claim["evidence"]))
        skim = self.cli(self.root, "search", "青客", "--in", "analysis", "--mode", "skim", "--book", self.b_id)
        self.assertTrue(skim["matches"])
        self.assertTrue(all(item["book_id"] == self.b_id and item["mode"] == "skim" for item in skim["matches"]))

    def test_adding_books_and_relocating_a_missing_path_preserve_history(self):
        body = self.body(id="original-result", scope={"books": {self.a_id: {}, self.b_id: {}}})
        saved = memory.save_research(self.root, body)
        moved = self.project / "原篇新位置"
        shutil.move(self.a, moved)
        topics.init_topic(self.root, [str(moved)])
        self.assertFalse(memory.render_research(self.root, "identity")["stale"])
        third = self.project / "另一部书"
        third.mkdir()
        (third / "正文.txt").write_text("第一章 新篇\n另一条人物线索。\n", encoding="utf-8")
        corpus.prepare(third, sources=["正文.txt"])
        topics.init_topic(self.root, [str(third)])
        self.assertTrue(memory.render_research(self.root, "identity")["stale"])
        self.assertEqual(memory.save_research(self.root, body), saved)
        self.assertEqual(len(topics.status(self.root)["books"]), 3)
        self.assertTrue(memory.check_records(self.root)["valid"])

    def test_invalid_registration_preserves_config_and_topic_notes_stay_local(self):
        path = self.root / "topic.json"
        original = path.read_bytes()
        config = json.loads(original)
        config["format_version"] = True
        atomic_write_json(path, config)
        invalid = path.read_bytes()
        self.error(lambda: topics.init_topic(self.root, [str(self.a), str(self.b)]), "FORMAT_VERSION")
        self.assertEqual(path.read_bytes(), invalid)
        path.write_bytes(original)
        memory.save_note(self.a, {"type": "preference", "origin": "user", "content": "只在原书保存的偏好。"})
        memory.save_note(self.root, {"type": "checkpoint", "origin": "agent", "content": "已比较初遇，下一步对照回信。"})
        self.assertEqual(self.cli(self.root, "notes", "recall")["total"], 1)
        self.assertEqual(self.cli(self.root, "search", "偏好", "--in", "notes")["total"], 0)
        self.assertEqual(self.cli(self.a, "notes", "recall")["total"], 1)

    def test_tampered_book_metadata_is_detected_without_overwriting_report(self):
        saved = memory.save_research(self.root, self.body())
        report = memory.render_research(self.root, "identity")
        path = self.root / "research/identity/records" / (saved["record_id"] + ".json")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["data"]["findings"][0]["evidence"][0]["book_workspace_id"] = "wrong-book"
        envelope["payload_hash"] = digest(envelope["data"])
        atomic_write_json(path, envelope)
        self.assertFalse(memory.check_records(self.root)["valid"])
        self.error(lambda: memory.render_research(self.root, "identity"))
        self.assertEqual(Path(report["report_path"]).read_text(encoding="utf-8"), report["report"])

    def cli(self, root, *args, success=True):
        process = subprocess.run([sys.executable, str(SCRIPTS / "novel.py"), "--workspace", str(root), *args],
                                 cwd=self.project, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.stderr, "")
        value = json.loads(process.stdout)
        self.assertEqual(value["ok"], success, value)
        self.assertEqual(process.returncode == 0, success, value)
        return value["result"] if success else value["error"]

    def test_cli_restarts_search_pagination_read_save_and_check(self):
        self.root = self.project / "新建的命令行专题"
        self.cli(self.root, "topic", "init", str(self.a), str(self.b))
        self.assertEqual(self.cli(self.root, "status")["workspace_type"], "topic")
        first = self.cli(self.root, "search", "青客", "--limit", "2")
        self.assertGreater(first["total"], 2)
        self.assertEqual(first["scope"]["kinds"], ["text"])
        self.assertTrue(all(item["source_revision"] == corpus.load_manifest(self.a)["revision"] for item in first["matches"]))
        second = self.cli(self.root, "search", "青客", "--limit", "2", "--offset", "2")
        self.assertEqual(first["total"], second["total"])
        locations = [(item["book_id"], item["span_id"], item["start"]) for item in first["matches"] + second["matches"]]
        self.assertEqual(len(locations), len(set(locations)))
        read = self.cli(self.root, "read", "--book", self.a_id, "--span", self.a_ref["span_id"])
        self.assertIn(self.a_ref["quote"], read["text"])
        atomic_write_json(self.root / ".novel/inbox/research.json", self.body())
        saved = self.cli(self.root, "research", "save", ".novel/inbox/research.json")
        self.assertEqual(self.cli(self.root, "research", "render", "identity")["record_id"], saved["record_id"])
        self.assertTrue(self.cli(self.root, "check", "--rebuild")["memory"]["valid"])
        self.assertEqual(self.cli(self.root, "task", "next", success=False)["code"], "TOPIC_WORKSPACE")
        self.cli(self.root, "read", "--span", self.a_ref["span_id"], success=False)

    def test_project_can_move_with_its_books(self):
        memory.save_research(self.root, self.body())
        with tempfile.TemporaryDirectory(prefix="迁移后的项目 ") as directory:
            copied = Path(directory) / "project"
            shutil.copytree(self.project, copied)
            moved_topic = copied / self.root.name
            result = self.cli(moved_topic, "research", "render", "identity")
            self.assertFalse(result["stale"])
            self.assertIn(self.b_ref["quote"], result["report"])


if __name__ == "__main__":
    unittest.main()
