"""Discussion state survives reloads; reports keep their original citations."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from common import NovelError, atomic_write_json, load_record, save_record, writer_lock
from corpus import load_manifest, prepare, search
import memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="小说 memory ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "sources").mkdir()
        self.text = self.root / "sources" / "正文.txt"
        self.text.write_text(
            "第一章 相逢\n青客低声说：“我怀疑他是旧友。”\n旁白只记下青客的猜测。\n"
            "第二章 回信\n信里写道：“他昨夜才知道。”\n", encoding="utf-8")
        (self.root / "sources" / "评论.md").write_text(
            "# 评论\n评论声称青客早已知道身份。\n", encoding="utf-8")
        prepare(self.root, sources=[
            {"path": "sources/正文.txt", "kind": "text", "encoding": "utf-8"},
            {"path": "sources/评论.md", "kind": "commentary", "encoding": "utf-8"},
        ])
        self.manifest = load_manifest(self.root)
        self.quote = "我怀疑他是旧友。"
        self.evidence = self.reference(self.quote)

    def reference(self, quote, kinds=None):
        result = search(self.root, [quote], kinds=kinds)
        return {"span_id": result["matches"][0]["span_id"], "quote": quote}

    def note(self, content="青客知道身份。", **fields):
        return memory.save_note(self.root, {"type": "finding", "content": content, "origin": "agent", **fields})

    def correction(self, target, **fields):
        return memory.save_note(self.root, {
            "type": "correction", "origin": "user", "content": "这里只能支持怀疑，不能写成知道。",
            "target_id": target, "status": "applied", "evidence": [self.evidence], **fields,
        })

    def research(self, **fields):
        return memory.save_research(self.root, {
            "case_id": "identity", "question": "青客何时知道身份？",
            "findings": [{"kind": "speech", "text": "青客表达了怀疑。", "evidence": [self.evidence]}],
            "unresolved": ["怀疑不能直接证明已知。"], **fields,
        })

    def assertDomainError(self, call):
        with self.assertRaises(NovelError) as caught:
            call()
        return caught.exception

    def test_applied_correction_survives_fresh_reload(self):
        original = self.note()
        correction = self.correction(original["record_id"])
        importlib.reload(memory)
        current = memory.recall_notes(self.root)
        self.assertEqual([item["record_id"] for item in current["notes"]], [correction["record_id"]])
        self.assertEqual(current["notes"][0]["effective_status"], "applied")
        self.assertTrue(current["notes"][0]["source_valid"])
        self.assertEqual(current["notes"][0]["semantic_status"], "reviewed")
        historical = memory.recall_notes(self.root, include_history=True)
        old = next(item for item in historical["notes"] if item["record_id"] == original["record_id"])
        self.assertEqual(old["effective_status"], "superseded")
        self.assertEqual(load_record(self.root / "notes" / "records" / (original["record_id"] + ".json")), original)

    def test_evidence_free_user_opinion_is_pending_and_labels_dispute(self):
        original = self.note()
        correction = self.correction(original["record_id"], evidence=[])
        self.assertEqual(correction["status"], "pending")
        self.assertEqual(correction["semantic_status"], "unreviewed")
        self.assertFalse(correction["source_valid"])
        current = {item["record_id"]: item for item in memory.recall_notes(self.root)["notes"]}
        self.assertEqual(current[original["record_id"]]["effective_status"], "disputed")
        self.assertEqual(current[correction["record_id"]]["effective_status"], "pending")

    def test_withdrawing_correction_does_not_restore_old_claim_as_truth(self):
        original = self.note()
        correction = self.correction(original["record_id"])
        withdrawal = self.note("撤回这条更正，原判断仍待核查。", type="withdrawal", origin="user", target_id=correction["record_id"])
        importlib.reload(memory)
        states = memory.overlays(self.root)
        self.assertEqual(states[correction["record_id"]]["status"], "withdrawn")
        self.assertEqual(states[original["record_id"]]["status"], "needs_review")
        self.assertEqual(states[withdrawal["record_id"]]["status"], "active")
        self.assertEqual(len(list((self.root / "notes" / "records").glob("*.json"))), 3)

    def test_target_validation_and_cross_book_input_do_not_write(self):
        self.assertDomainError(lambda: self.correction("missing"))
        self.assertDomainError(lambda: self.correction("self-note", id="self-note"))
        self.assertDomainError(lambda: self.note(workspace_id="another-book"))
        self.assertDomainError(lambda: self.note(type=[]))
        self.assertDomainError(lambda: self.note(origin=[]))
        self.assertEqual(list((self.root / "notes").glob("records/*.json")), [])

    def test_note_retry_is_idempotent_and_preserves_handwritten_notes(self):
        user_note = self.root / "notes" / "my-note.md"
        user_note.parent.mkdir()
        user_note.write_text("用户手写内容\n", encoding="utf-8")
        original = self.note(id="one-note")
        self.assertEqual(self.note(id="one-note"), original)
        self.assertDomainError(lambda: self.note("另一个判断", id="one-note"))
        memory.recall_notes(self.root)
        self.assertEqual(user_note.read_text(encoding="utf-8"), "用户手写内容\n")

    def test_source_revision_change_makes_applied_correction_need_review(self):
        original = self.note()
        correction = self.correction(original["record_id"])
        self.text.write_text(self.text.read_text(encoding="utf-8") + "后来他才确认。\n", encoding="utf-8")
        self.note("偏好保持可保存", type="preference")
        self.assertEqual(memory.overlays(self.root)[correction["record_id"]]["status"], "applied")
        prepare(self.root)
        view = memory.overlays(self.root)
        self.assertEqual(view[correction["record_id"]]["status"], "stale")
        self.assertEqual(view[original["record_id"]]["status"], "needs_review")
        self.assertTrue(memory.check_records(self.root)["stale"])

    def test_research_render_uses_snapshot_and_labels_information_types(self):
        counter = self.reference("他昨夜才知道。")
        saved = self.research(findings=[
            {"kind": "speech", "text": "青客表达了怀疑。", "evidence": [self.evidence], "counter_evidence": [counter]},
            {"kind": "unknown", "text": "尚不能确定他当时已知。", "evidence": []},
        ])
        self.text.write_text("第一章 改写\n这不是旧版内容。\n", encoding="utf-8")
        prepare(self.root)
        rendered = memory.render_research(self.root, saved["case_id"])
        self.assertTrue(rendered["stale"])
        self.assertTrue(rendered["needs_review"])
        self.assertIn(self.quote, rendered["report"])
        self.assertIn("他昨夜才知道。", rendered["report"])
        self.assertIn("Character speech", rendered["report"])
        self.assertIn("Unknown / unresolved [draft]", rendered["report"])
        self.assertIn(saved["source_revision"], rendered["report"])

    def test_research_scope_rejects_commentary_by_default_and_wrong_chapters(self):
        commentary = self.reference("评论声称青客早已知道身份。", kinds=["commentary"])
        finding = {"kind": "speech", "text": "评论声称角色已知。", "evidence": [commentary]}
        self.assertDomainError(lambda: self.research(findings=[finding]))
        self.assertDomainError(lambda: self.research(scope={"chapter_from": 2}, findings=[{
            "kind": "speech", "text": "青客表达了怀疑。", "evidence": [self.evidence]}]))
        saved = self.research(scope={"source_kinds": ["commentary"]}, findings=[finding])
        rendered = memory.render_research(self.root, saved["case_id"])
        self.assertIn("commentary /", rendered["report"])
        self.assertEqual(saved["findings"][0]["evidence"][0]["source_kind"], "commentary")

    def test_mixed_research_scope_filters_chapters_only_for_text(self):
        self.text.write_text(self.text.read_text(encoding="utf-8") + "第三章 确认\n青客这时终于确认了身份。\n", encoding="utf-8")
        prepare(self.root)
        commentary = self.reference("评论声称青客早已知道身份。", kinds=["commentary"])
        later_text = self.reference("青客这时终于确认了身份。")
        scope = {"chapter_from": 1, "chapter_to": 2, "source_kinds": ["text", "commentary"]}
        saved = self.research(scope=scope, findings=[{
            "kind": "speech", "text": "正文只表明怀疑，评论另有说法。",
            "evidence": [self.evidence], "counter_evidence": [commentary],
        }])
        self.assertEqual(saved["findings"][0]["counter_evidence"][0]["source_kind"], "commentary")
        self.assertIsNone(saved["findings"][0]["counter_evidence"][0]["chapter_ordinal"])
        rendered = memory.render_research(self.root, saved["case_id"])
        self.assertIn(commentary["quote"], rendered["report"])
        self.assertIn("补充资料不按章号过滤", rendered["report"])
        self.assertEqual(self.assertDomainError(lambda: self.research(
            case_id="outside", scope=scope,
            findings=[{"kind": "narration", "text": "后文确认身份。", "evidence": [later_text]}],
        )).code, "out_of_scope")
        self.assertEqual(self.assertDomainError(lambda: self.research(
            case_id="text-only", scope={"chapter_from": 1, "chapter_to": 2},
            findings=[{"kind": "speech", "text": "评论的说法。", "evidence": [commentary]}],
        )).code, "out_of_scope")

    def test_formal_fact_requires_evidence_but_hypothesis_can_remain_draft(self):
        self.assertDomainError(lambda: self.research(findings=[{"kind": "belief", "text": "他知道。", "evidence": []}]))
        saved = self.research(findings=[{"kind": "inference", "text": "也许他在隐瞒。", "evidence": []}])
        self.assertEqual(saved["findings"][0]["status"], "draft")

    def test_research_revisions_keep_history_and_rebuild_missing_pointer(self):
        first = self.research()
        self.assertEqual(self.research(), first)
        second = self.research(unresolved=["还需查回信的上下文。"])
        self.assertNotEqual(first["record_id"], second["record_id"])
        pointer = self.root / "research" / "identity" / "case.json"
        pointer.unlink()
        rendered = memory.render_research(self.root, "identity")
        self.assertEqual(rendered["record_id"], second["record_id"])
        self.assertEqual(rendered["record_count"], 2)
        self.assertEqual(json.loads(pointer.read_text(encoding="utf-8"))["latest_record_id"], second["record_id"])
        self.assertEqual(len(list(pointer.parent.glob("records/*.json"))), 2)

    def test_explicit_retry_ids_keep_their_original_revision_and_scope(self):
        note = self.note(id="pinned-note")
        research = self.research(id="pinned-research")
        self.research(scope={"chapter_from": 1, "chapter_to": 1})
        self.assertEqual(self.research(id="pinned-research"), research)
        self.assertDomainError(lambda: self.research(id="pinned-note"))
        self.text.write_text(self.text.read_text(encoding="utf-8") + "新版补记。\n", encoding="utf-8")
        prepare(self.root)
        self.assertEqual(self.note(id="pinned-note"), note)

    def test_research_finding_correction_flags_report_on_fresh_reload(self):
        saved = self.research()
        correction = self.correction(saved["findings"][0]["id"])
        importlib.reload(memory)
        rendered = memory.render_research(self.root, "identity")
        self.assertTrue(rendered["needs_review"])
        self.assertIn("[superseded]", rendered["report"])
        self.assertIn(correction["content"], rendered["report"])
        memory.save_note(self.root, {"type": "withdrawal", "content": "撤回更正。", "origin": "user",
                                    "target_id": correction["record_id"]})
        self.assertIn("[needs_review]", memory.render_research(self.root, "identity")["report"])

    def test_corrections_can_reference_persisted_analysis_claims(self):
        task_id = "task-abc123"
        with writer_lock(self.root):
            save_record(self.root / "analysis" / "records" / (task_id + ".json"), {
                "format_version": 1, "record_type": "analysis", "record_id": task_id, "task_id": task_id,
                "workspace_id": self.manifest["workspace_id"], "claims": [{
                    "id": task_id + ":claim:0", "kind": "belief", "text": "青客知道。", "evidence": []}],
            })
        self.correction(task_id + ":claim:0")
        self.assertEqual(memory.overlays(self.root)[task_id + ":claim:0"]["status"], "superseded")

    def test_check_and_render_detect_tampered_quotation_metadata(self):
        saved = self.research()
        path = self.root / "research" / "identity" / "records" / (saved["record_id"] + ".json")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["data"]["findings"][0]["evidence"][0]["start"] += 1
        from common import digest
        envelope["payload_hash"] = digest(envelope["data"])
        atomic_write_json(path, envelope)
        result = memory.check_records(self.root)
        self.assertGreaterEqual(result["error_count"], 1)
        self.assertDomainError(lambda: memory.render_research(self.root, "identity"))


if __name__ == "__main__":
    unittest.main()
