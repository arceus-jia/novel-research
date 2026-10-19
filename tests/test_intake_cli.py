"""Users can add nested files without writing or maintaining source JSON."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/novel-research/scripts/novel.py"


class IntakeCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="资料 接入 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "某本书"
        self.root.mkdir()

    def source(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return path

    def cli(self, *args, success=True):
        process = subprocess.run([sys.executable, str(SCRIPT), "--workspace", str(self.root), *args],
                                 cwd=self.temporary.name, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.stderr, "")
        value = json.loads(process.stdout)
        self.assertEqual((value["ok"], process.returncode == 0), (success, success), value)
        return value["result"] if success else value["error"]

    def test_first_setup_then_new_nested_material_without_manual_config(self):
        first = self.source("我的正文/上卷/第一章.txt", "第一章 重逢\n沈砚尚不认识来客。\n")
        comment = self.source("随手收集/看法.md", "# 读者分析\n我认为沈砚早已认识来客。\n")
        scan = self.cli("sources", "scan")
        self.assertFalse(scan["config_exists"])
        self.assertEqual(scan["new_count"], 2)
        self.assertFalse((self.root / "novel.json").exists())
        added = self.cli("sources", "add", first, "--kind", "text", "--title", "重逢记")
        self.assertTrue(added["prepare_required"])
        self.cli("sources", "add", comment, "--kind", "commentary")
        self.assertEqual(self.cli("prepare")["chapter_count"], 1)
        self.assertEqual(self.cli("search", "早已认识")["total"], 0)
        self.assertEqual(self.cli("search", "早已认识", "--in", "supplements")["total"], 1)
        task = self.cli("task", "next")["task"]
        body = {"task_id": task["task_id"], "input_fingerprint": task["input_fingerprint"],
                "overview": "相逢时尚不相识。", "mentions": [], "open_questions": [],
                "claims": [{"kind": "belief", "text": "沈砚尚不认识来客。", "evidence": [
                    {"span_id": task["spans"][0]["span_id"], "quote": "沈砚尚不认识来客。"}]}]}
        inbox = self.root / ".novel/inbox/analysis.json"
        inbox.parent.mkdir(parents=True)
        inbox.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        self.cli("task", "submit", str(inbox))
        second = self.source("后来加的文件/第二章.md", "# 第二章 来信\n沈砚收到来信。\n")
        scan = self.cli("sources", "scan")
        self.assertEqual(scan["new_count"], 1)
        self.assertNotIn("analysis/views/chapters.md", [item["path"] for item in scan["files"]])
        incremental = self.cli("sources", "scan", "--new-only", "--limit", "1")
        self.assertEqual([item["path"] for item in incremental["files"]], [second])
        self.assertEqual((incremental["total"], incremental["registered_count"]), (1, 2))
        self.assertEqual(self.cli("status")["sources"]["new_count"], 1)
        self.cli("sources", "add", second, "--kind", "text", "--before", comment)
        self.cli("prepare")
        status = self.cli("status", "--chapters")
        self.assertEqual((status["completed"], status["pending"]), (1, 1))
        self.assertEqual(status["sources"]["new_count"], 0)
        self.assertEqual([chapter["ordinal"] for chapter in status["chapters"]], [1, 2])
        self.assertEqual(self.cli("task", "next")["task"]["chapter"]["ordinal"], 2)
        self.assertTrue(self.cli("check")["memory"]["valid"])

    def test_registration_cannot_silently_reclassify_source(self):
        path = self.source("资料/访谈.md", "# 访谈\n主持人问，作者答。\n")
        self.cli("sources", "add", path, "--kind", "interview")
        config = (self.root / "novel.json").read_bytes()
        source = (self.root / path).read_bytes()
        self.cli("sources", "add", path, "--kind", "interview")
        self.assertEqual((self.root / "novel.json").read_bytes(), config)
        self.cli("sources", "add", path, "--kind", "text", success=False)
        self.assertEqual((self.root / "novel.json").read_bytes(), config)
        self.assertEqual((self.root / path).read_bytes(), source)
        self.assertEqual(self.cli("sources", "scan", "--limit", "0", success=False)["code"], "INVALID_LIMIT")


if __name__ == "__main__":
    unittest.main()
