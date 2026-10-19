"""Accepted work survives restarts, failures, source updates and corrections."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import analysis
import corpus
import memory
from common import NovelError, atomic_write_json, digest


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="小说 分析 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "正文.txt"
        self.source.write_text("第一章 来客\n青客只怀疑故人身份。\n第二章 回信\n青客这时才知道真相。\n", encoding="utf-8")
        corpus.prepare(self.root, sources=["正文.txt"])

    def body(self, task=None):
        task = task or analysis.next_task(self.root)["task"]
        quote = "青客只怀疑故人身份。" if task["chapter"]["ordinal"] == 1 else "青客这时才知道真相。"
        evidence = [{"span_id": task["spans"][0]["span_id"], "quote": quote}]
        return {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                "overview": quote, "mentions": [{"name": "青客", "evidence": evidence}],
                "claims": [{"kind": "belief", "text": quote, "evidence": evidence}], "open_questions": []}

    def error(self, code, call):
        with self.assertRaises(NovelError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_resume_idempotency_conflict_and_scoped_completion(self):
        body = self.body()
        saved = analysis.submit_task(self.root, body)
        self.assertEqual(saved["state"], "saved")
        self.assertEqual(saved["record"]["format_version"], 1)
        self.assertEqual(analysis.submit_task(self.root, body)["state"], "already_saved")
        changed = {**body, "overview": "不同结果"}
        self.error("RECORD_CONFLICT", lambda: analysis.submit_task(self.root, changed))
        self.assertEqual(analysis.status(self.root)["completed"], 1)
        self.assertEqual(analysis.next_task(self.root, 1, 1)["state"], "complete")
        self.assertEqual(analysis.next_task(self.root, mode="analyze")["task"]["chapter"]["ordinal"], 2)
        analysis.submit_task(self.root, self.body())
        self.assertTrue(analysis.status(self.root)["coverage_complete"])
        self.assertTrue(memory.check_records(self.root)["valid"])
        self.assertIn("青客", (self.root / "analysis/views/characters.md").read_text(encoding="utf-8"))

    def test_failed_submission_is_recoverable_and_diagnostic_persists(self):
        body = self.body()
        invalid = copy.deepcopy(body)
        invalid["claims"][0]["evidence"] = []
        self.error("MISSING_EVIDENCE", lambda: analysis.submit_task(self.root, invalid))
        self.error("MISSING_EVIDENCE", lambda: analysis.submit_task(self.root, invalid))
        status = analysis.status(self.root)
        self.assertEqual((status["completed"], status["failed_count"]), (0, 1))
        retry = analysis.next_task(self.root)
        self.assertEqual(retry["previous_error"]["attempts"], 2)
        self.assertEqual(retry["previous_error"]["code"], "MISSING_EVIDENCE")
        analysis.submit_task(self.root, body)
        self.assertEqual(analysis.status(self.root)["failed_count"], 0)

    def test_corrupt_diagnostic_does_not_prevent_retry(self):
        body = self.body()
        diagnostic = self.root / ".novel/task-errors" / (body["task_id"] + ".json")
        atomic_write_json(diagnostic, {**body, "attempts": "broken"})
        invalid = {**body, "claims": [{"kind": "belief", "text": "待查", "evidence": []}]}
        self.error("MISSING_EVIDENCE", lambda: analysis.submit_task(self.root, invalid))
        self.assertEqual(analysis.next_task(self.root)["previous_error"]["attempts"], 1)
        read_json = analysis.read_json

        def unreadable_diagnostic(path):
            if path.parent.name == "task-errors":
                raise OSError("diagnostic unavailable")
            return read_json(path)

        with mock.patch.object(analysis, "read_json", side_effect=unreadable_diagnostic):
            self.assertIsNone(analysis.next_task(self.root)["previous_error"])
        self.assertEqual(analysis.submit_task(self.root, body)["state"], "saved")

    def test_long_chapter_packets_are_bounded_without_missing_text(self):
        self.source.write_text("第一章 长夜\n" + "青客仍在等待。" * 2500, encoding="utf-8")
        corpus.prepare(self.root)
        seen = []
        characters = 0
        while True:
            result = analysis.next_task(self.root)
            if result["state"] != "pending":
                self.assertEqual(result["state"], "complete")
                break
            task = result["task"]
            self.assertLessEqual(task["input_characters"], analysis.TASK_CHARS)
            seen.extend(span["span_id"] for span in task["spans"])
            characters += task["input_characters"]
            analysis.submit_task(self.root, {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                "overview": "本段等待情节。", "mentions": [], "claims": [], "open_questions": []})
        self.assertGreater(len(seen), 1)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(characters, sum(len(span["text"]) for span in corpus.iter_spans(self.root)))
        self.assertTrue(analysis.status(self.root)["coverage_complete"])

    def test_empty_or_unconfirmed_scope_does_not_claim_completion(self):
        self.assertEqual(analysis.next_task(self.root, 99, 100)["state"], "empty_scope")
        self.source.write_text("没有章节标题的正文仍然可以查。", encoding="utf-8")
        corpus.prepare(self.root)
        self.assertEqual(analysis.next_task(self.root)["state"], "needs_review")
        self.assertFalse(analysis.status(self.root)["coverage_complete"])
        self.assertEqual(corpus.search(self.root, ["正文"])["total"], 1)

    def test_evidence_cannot_escape_packet_and_unissued_task_is_rejected(self):
        task = analysis._task_specs(self.root, corpus.load_manifest(self.root))[0]
        body = self.body(task)
        self.error("TASK_NOT_ISSUED", lambda: analysis.submit_task(self.root, body))
        analysis.next_task(self.root)
        other = list(corpus.iter_spans(self.root))[1]
        body["claims"][0]["evidence"] = [{"span_id": other["span_id"], "quote": "青客这时才知道真相。"}]
        self.error("EVIDENCE_OUT_OF_RANGE", lambda: analysis.submit_task(self.root, body))
        self.assertEqual(analysis.status(self.root)["completed"], 0)

    def test_source_edit_reuses_unaffected_chapter_but_not_changed_input(self):
        first = self.body()
        analysis.submit_task(self.root, first)
        second = self.body()
        analysis.submit_task(self.root, second)
        self.source.write_text(self.source.read_text(encoding="utf-8").replace("只怀疑", "仍怀疑"), encoding="utf-8")
        self.error("SOURCE_CHANGED", lambda: analysis.next_task(self.root))
        corpus.prepare(self.root)
        status = analysis.status(self.root)
        self.assertEqual((status["completed"], status["pending"], status["stale_record_count"]), (1, 1, 1))
        self.assertNotEqual(analysis.next_task(self.root)["task"]["task_id"], first["task_id"])
        self.error("STALE_TASK", lambda: analysis.submit_task(self.root, first))
        self.assertTrue(analysis.check_records(self.root)["valid"])

    def test_contract_upgrade_invalidates_previous_task(self):
        body = self.body()
        analysis.submit_task(self.root, body)
        with mock.patch.object(analysis, "_contract_hash", return_value="new-contract"):
            self.assertEqual(analysis.status(self.root)["completed"], 0)
            self.error("STALE_TASK", lambda: analysis.submit_task(self.root, body))

    def test_view_failure_does_not_lose_accepted_work(self):
        body = self.body()
        with mock.patch.object(analysis, "rebuild_views", side_effect=OSError("disk unavailable")):
            result = analysis.submit_task(self.root, body)
        self.assertEqual(result["state"], "saved")
        self.assertIn("warning", result)
        self.assertEqual(analysis.status(self.root)["completed"], 1)
        analysis.rebuild_views(self.root)
        self.assertTrue((self.root / "analysis/views/chapters.md").is_file())

    def test_corrupt_record_is_not_counted_as_complete(self):
        body = self.body()
        result = analysis.submit_task(self.root, body)
        path = self.root / "analysis/records" / (result["record"]["record_id"] + ".json")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["data"]["overview"] = "意外改写"
        atomic_write_json(path, envelope)
        status = analysis.status(self.root)
        self.assertEqual((status["completed"], len(status["invalid"])), (0, 1))
        self.assertFalse(status["coverage_complete"])
        self.assertFalse(analysis.check_records(self.root)["valid"])

    def test_record_correction_cannot_be_masked_by_item_checkpoint(self):
        body = self.body()
        record = analysis.submit_task(self.root, body)["record"]
        evidence = body["claims"][0]["evidence"]
        correction = memory.save_note(self.root, {"type": "correction", "origin": "agent", "status": "applied",
            "content": "整条分析需要按怀疑程度更正。", "target_id": record["record_id"], "evidence": evidence})
        memory.save_note(self.root, {"type": "checkpoint", "origin": "agent", "content": "已查到这条主张。",
                                    "target_id": record["claims"][0]["id"]})
        result = analysis.search_analysis(self.root, ["故人"])
        claim = next(item for item in result["matches"] if item["kind"] == "belief")
        self.assertEqual(claim["current_status"], "superseded")
        self.assertIn(correction["record_id"], claim["corrections"]["correction_ids"])
        analysis.rebuild_views(self.root)
        self.assertIn("belief · superseded", (self.root / "analysis/views/chapters.md").read_text(encoding="utf-8"))

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / "novel.py"), "--workspace", str(self.root), *args],
                                cwd=self.temporary.name, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertEqual(value["ok"], success, value)
        self.assertEqual(result.returncode == 0, success, value)
        return value.get("result") if success else value["error"]

    def test_cli_fresh_process_workflow_and_structured_errors(self):
        self.assertTrue(self.cli("doctor")["ready"])
        packet = self.cli("task", "next")["task"]
        body = self.body(packet)
        atomic_write_json(self.root / "analysis.json", body)
        self.assertEqual(self.cli("task", "submit", "analysis.json")["state"], "saved")
        atomic_write_json(self.root / "note.json", {"id": "user-pref", "type": "preference", "origin": "user",
                                                   "content": "请关注人物认知变化。"})
        self.cli("notes", "save", "note.json")
        self.assertEqual(self.cli("notes", "recall")["notes"][0]["record_id"], "user-pref")
        atomic_write_json(self.root / "research.json", {"case_id": "identity", "question": "是否已知？",
            "findings": body["claims"], "scope": {"chapter_from": 1, "chapter_to": 1}})
        saved = self.cli("research", "save", "research.json")
        report = self.cli("research", "render", "identity")
        self.assertEqual(report["record_id"], saved["record_id"])
        self.assertIn("青客只怀疑故人身份。", report["report"])
        self.assertTrue(self.cli("check", "--rebuild")["analysis"]["valid"])
        self.assertEqual(self.cli("task", "next")["task"]["chapter"]["ordinal"], 2)
        self.assertEqual(self.cli("search", "青客", "--chapter-from", "2", "--chapter-to", "1", success=False)["code"], "USAGE")
        (self.root / "bad.json").write_text('{"n":' + "9" * 5000 + "}", encoding="utf-8")
        self.assertEqual(self.cli("notes", "save", "bad.json", success=False)["code"], "INVALID_JSON")

    def test_modes_keep_completion_separate_and_share_search_and_views(self):
        deep = analysis.next_task(self.root, mode="analyze")["task"]
        skim = analysis.next_task(self.root, mode="skim")["task"]
        self.assertNotEqual(deep["task_id"], skim["task_id"])
        self.assertEqual(deep["spans"], skim["spans"])
        analysis.submit_task(self.root, self.body(deep))
        self.assertEqual(analysis.status(self.root)["mode"], "skim")
        self.assertEqual(analysis.status(self.root)["completed"], 0)
        while True:
            result = analysis.next_task(self.root)
            if result["state"] != "pending":
                break
            analysis.submit_task(self.root, self.body(result["task"]))
        self.assertEqual(result["state"], "complete")
        self.assertTrue(analysis.status(self.root, mode="skim")["coverage_complete"])
        deep_status = analysis.status(self.root, mode="analyze")
        self.assertEqual((deep_status["completed"], deep_status["pending"], deep_status["stale_record_count"]), (1, 1, 0))
        self.assertFalse(deep_status["coverage_complete"])
        matches = analysis.search_analysis(self.root, ["青客"])["matches"]
        self.assertEqual({item["mode"] for item in matches}, {"skim", "analyze"})
        self.assertTrue(all(item["mode"] == "skim" for item in analysis.search_analysis(self.root, ["青客"], mode="skim")["matches"]))
        library = (self.root / "analysis/views/library.md").read_text(encoding="utf-8")
        self.assertIn("粗读任务：已接收 2 / 2", library)
        self.assertIn("精读任务：已接收 1 / 2", library)
        chapters = (self.root / "analysis/views/chapters.md").read_text(encoding="utf-8")
        self.assertIn("· 粗读", chapters)
        self.assertIn("· 精读", chapters)
        self.assertTrue(analysis.check_records(self.root)["valid"])

    def test_cli_resume_keeps_mode_and_scope_and_explicit_mode_means_whole_book(self):
        task = self.cli("task", "next", "--mode", "skim", "--chapter-from", "2", "--chapter-to", "2")["task"]
        self.assertEqual((task["mode"], task["chapter"]["ordinal"]), ("skim", 2))
        atomic_write_json(self.root / ".novel/inbox/skim.json", self.body(task))
        self.cli("task", "submit", ".novel/inbox/skim.json")
        resumed = self.cli("task", "next")
        self.assertEqual((resumed["mode"], resumed["state"]), ("skim", "complete"))
        self.assertEqual(resumed["scope"], {"chapter_from": 2, "chapter_to": 2})
        self.assertFalse(resumed["status"]["coverage_complete"])
        full = self.cli("task", "next", "--mode", "skim")["task"]
        self.assertEqual(full["chapter"]["ordinal"], 1)
        selection = self.cli("status")["reading_session"]
        self.assertEqual((selection["mode"], selection["chapter_from"], selection["chapter_to"]), ("skim", None, None))
        self.assertEqual(self.cli("status", "--mode", "analyze")["completed"], 0)
        deep = self.cli("task", "next", "--mode", "analyze")["task"]
        self.assertEqual((deep["mode"], deep["chapter"]["ordinal"]), ("analyze", 1))
        matches = self.cli("search", "青客", "--in", "analysis", "--mode", "skim")["matches"]
        self.assertTrue(matches)
        self.assertTrue(all(item["mode"] == "skim" for item in matches))
        self.assertEqual(self.cli("search", "青客", "--mode", "skim", success=False)["code"], "USAGE")
        self.assertEqual(self.cli("task", "next", "--mode", "missing", success=False)["code"], "USAGE")

    def test_skim_preserves_evidence_checks_and_correction_links(self):
        task = analysis.next_task(self.root, mode="skim")["task"]
        body = self.body(task)
        self.error("MODE_MISMATCH", lambda: analysis.submit_task(self.root, {**body, "mode": "analyze"}))
        self.error("INVALID_INPUT", lambda: analysis.submit_task(self.root, {**body, "overview": ""}))
        invalid = copy.deepcopy(body)
        invalid["claims"][0]["evidence"] = []
        self.error("MISSING_EVIDENCE", lambda: analysis.submit_task(self.root, invalid))
        invalid["claims"][0]["evidence"] = [{"span_id": list(corpus.iter_spans(self.root))[1]["span_id"], "quote": "青客这时才知道真相。"}]
        self.error("EVIDENCE_OUT_OF_RANGE", lambda: analysis.submit_task(self.root, invalid))
        self.assertEqual(analysis.status(self.root)["completed"], 0)
        record = analysis.submit_task(self.root, body)["record"]
        correction = memory.save_note(self.root, {"type": "correction", "origin": "agent", "status": "applied",
            "content": "粗读也只能记为怀疑，不能提前写为知道。", "target_id": record["claims"][0]["id"],
            "evidence": body["claims"][0]["evidence"]})
        item = next(item for item in analysis.search_analysis(self.root, ["怀疑"], mode="skim")["matches"] if item["kind"] == "belief")
        self.assertEqual(item["current_status"], "superseded")
        self.assertIn(correction["record_id"], item["corrections"]["correction_ids"])

    def test_legacy_analysis_without_mode_is_reused(self):
        body = self.body()
        record = analysis.submit_task(self.root, body)["record"]
        path = self.root / "analysis/records" / (record["record_id"] + ".json")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["data"].pop("mode", None)
        envelope["payload_hash"] = digest(envelope["data"])
        atomic_write_json(path, envelope)
        original = path.read_bytes()
        self.assertEqual(analysis.status(self.root, mode="analyze")["completed"], 1)
        self.assertEqual(analysis.next_task(self.root, mode="analyze")["task"]["chapter"]["ordinal"], 2)
        self.assertEqual(analysis.status(self.root, mode="skim")["completed"], 0)
        self.assertEqual(path.read_bytes(), original)

    def test_source_edit_invalidates_both_modes_but_reuses_unchanged_chapter(self):
        for mode in ("skim", "analyze"):
            for _ in range(2):
                task = analysis.next_task(self.root, mode=mode)["task"]
                analysis.submit_task(self.root, self.body(task))
        self.source.write_text(self.source.read_text(encoding="utf-8").replace("只怀疑", "仍怀疑"), encoding="utf-8")
        corpus.prepare(self.root)
        for mode in ("skim", "analyze"):
            progress = analysis.status(self.root, mode=mode)
            self.assertEqual((progress["completed"], progress["pending"], progress["stale_record_count"]), (1, 1, 2))
            self.assertEqual(analysis.next_task(self.root, mode=mode)["task"]["chapter"]["ordinal"], 1)
        self.assertTrue(analysis.check_records(self.root)["valid"])

    def test_corrupt_reading_session_does_not_silently_switch_modes(self):
        analysis.next_task(self.root, mode="skim")
        atomic_write_json(self.root / ".novel/reading.json", {"format_version": 1, "mode": "missing"})
        self.error("INVALID_READING_STATE", lambda: analysis.next_task(self.root))

    def test_skim_long_chapter_reads_every_span_once(self):
        self.source.write_text("第一章 长夜\n" + "青客仍在等待。" * 2500, encoding="utf-8")
        corpus.prepare(self.root)
        seen = []
        text = []
        result = analysis.next_task(self.root, mode="skim")
        while result["state"] == "pending":
            task = result["task"]
            self.assertLessEqual(task["input_characters"], analysis.TASK_CHARS)
            seen.extend(span["span_id"] for span in task["spans"])
            text.extend(span["text"] for span in task["spans"])
            analysis.submit_task(self.root, {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                "mode": "skim", "overview": "青客继续等待。", "mentions": [], "claims": [], "open_questions": []})
            result = analysis.next_task(self.root)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual("".join(text), self.source.read_text(encoding="utf-8"))
        self.assertTrue(analysis.status(self.root, mode="skim")["coverage_complete"])
        self.assertEqual(analysis.status(self.root, mode="analyze")["completed"], 0)

    def test_skim_contract_change_keeps_existing_deep_analysis(self):
        for mode in ("skim", "analyze"):
            task = analysis.next_task(self.root, mode=mode)["task"]
            analysis.submit_task(self.root, self.body(task))
        contract_hash = analysis._contract_hash
        with mock.patch.object(analysis, "_contract_hash", side_effect=lambda mode: "new-skim-contract" if mode == "skim" else contract_hash(mode)):
            self.assertEqual(analysis.status(self.root, mode="skim")["completed"], 0)
            self.assertEqual(analysis.status(self.root, mode="analyze")["completed"], 1)


if __name__ == "__main__":
    unittest.main()
