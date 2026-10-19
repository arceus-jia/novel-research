import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "novel-research" / "scripts"))

import common


class CommonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "小说 工作区"
        self.root.mkdir()

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(common.NovelError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_invalid_json_does_not_silently_change_values(self):
        path = self.root / "input.json"
        inputs = {
            "nested_duplicate": b'{"result":{"quote":"first","quote":"second"}}',
            "nan": b'{"confidence":NaN}',
            "infinity": b'{"confidence":Infinity}',
            "negative_infinity": b'{"confidence":-Infinity}',
            "invalid_utf8": b'{"quote":"\xff"}',
        }
        for name, raw in inputs.items():
            with self.subTest(name=name):
                path.write_bytes(raw)
                self.assert_error("INVALID_JSON", common.read_json, path)
                self.assertEqual(path.read_bytes(), raw)

    def test_json_limit_counts_encoded_bytes(self):
        path = self.root / "input.json"
        text = json.dumps({"quote": "中文原文"}, ensure_ascii=False)
        raw = text.encode("utf-8")
        path.write_bytes(raw)
        self.assertGreater(len(raw), len(text))
        self.assert_error("INPUT_TOO_LARGE", common.read_json, path, max_bytes=len(text))
        self.assertEqual(common.read_json(path, max_bytes=len(raw)), {"quote": "中文原文"})

    def test_five_thousand_digit_integer_returns_domain_error(self):
        previous = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        self.addCleanup(sys.set_int_max_str_digits, previous)
        path = self.root / "input.json"
        path.write_bytes(b'{"number":' + b"1" * 5000 + b"}")
        self.assert_error("INVALID_JSON", common.read_json, path)

    def test_sealed_record_rejects_payload_tampering(self):
        path = self.root / "record.json"
        common.save_record(path, {"quote": "林青尚未知道。"})
        envelope = common.read_json(path)
        envelope["data"]["quote"] = "林青已经知道。"
        common.atomic_write_json(path, envelope)
        self.assert_error("CORRUPT_RECORD", common.load_record, path)
        self.assertEqual(common.read_json(path), envelope)

    def test_sealed_record_rejects_boolean_and_unknown_versions(self):
        path = self.root / "record.json"
        common.save_record(path, {"quote": "原文"})
        envelope = common.read_json(path)
        for version in (True, 2):
            with self.subTest(version=version):
                envelope["format_version"] = version
                common.atomic_write_json(path, envelope)
                self.assert_error("UNSUPPORTED_VERSION", common.load_record, path)

    def test_record_retry_is_idempotent_and_conflict_preserves_original(self):
        path = self.root / "record.json"
        original = {"quote": "原文", "chapter": 1}
        common.save_record(path, original)
        before = path.read_bytes()
        self.assertEqual(common.save_record(path, {"chapter": 1, "quote": "原文"}), original)
        self.assertEqual(path.read_bytes(), before)
        self.assert_error("RECORD_CONFLICT", common.save_record, path, {"quote": "另一份结果", "chapter": 1})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(common.load_record(path), original)

    def test_replace_failure_preserves_old_file_and_removes_temporary(self):
        path = self.root / "notes.json"
        old = b'{"quote":"old"}\r\n'
        path.write_bytes(old)
        with mock.patch.object(common.os, "replace", side_effect=PermissionError("simulated replacement failure")):
            with self.assertRaises(PermissionError):
                common.atomic_write_json(path, {"quote": "新内容😀"})
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(set(self.root.iterdir()), {path})
        common.atomic_write_json(path, {"quote": "重试成功"})
        self.assertEqual(common.read_json(path), {"quote": "重试成功"})

    def test_lock_body_failure_releases_lock_for_retry(self):
        lock = self.root / ".novel" / "write.lock"
        with self.assertRaisesRegex(OSError, "simulated write failure"):
            with common.writer_lock(self.root):
                raise OSError("simulated write failure")
        self.assertFalse(lock.exists())
        with common.writer_lock(self.root):
            self.assertEqual(common.read_json(lock / "owner.json")["pid"], os.getpid())
        self.assertFalse(lock.exists())

    def test_lock_metadata_failure_also_releases_lock(self):
        lock = self.root / ".novel" / "write.lock"
        with mock.patch.object(common, "atomic_write_json", side_effect=OSError("cannot save owner")):
            with self.assertRaisesRegex(OSError, "cannot save owner"):
                with common.writer_lock(self.root):
                    self.fail("Failed lock acquisition must not enter the body")
        self.assertFalse(lock.exists())
        with common.writer_lock(self.root):
            pass
        self.assertFalse(lock.exists())

    def test_lock_conflict_leaves_current_owner_and_lock_intact(self):
        lock = self.root / ".novel" / "write.lock"
        with common.writer_lock(self.root):
            before = (lock / "owner.json").read_bytes()
            with self.assertRaises(common.NovelError) as raised:
                with common.writer_lock(self.root):
                    self.fail("A competing writer must not acquire the lock")
            self.assertEqual(raised.exception.code, "WORKSPACE_BUSY")
            self.assertEqual(raised.exception.details["owner"]["pid"], os.getpid())
            self.assertTrue(lock.is_dir())
            self.assertEqual((lock / "owner.json").read_bytes(), before)
        self.assertFalse(lock.exists())

    def test_unreadable_owner_does_not_allow_stealing_existing_lock(self):
        lock = self.root / ".novel" / "write.lock"
        lock.mkdir(parents=True)
        (lock / "owner.json").write_bytes(b"not json")
        with self.assertRaises(common.NovelError) as raised:
            with common.writer_lock(self.root):
                self.fail("An unknown owner still holds the existing lock")
        self.assertEqual(raised.exception.code, "WORKSPACE_BUSY")
        self.assertIsNone(raised.exception.details["owner"])
        self.assertEqual((lock / "owner.json").read_bytes(), b"not json")

    def test_safe_path_rejects_parent_escape_and_absolute_path(self):
        outside = Path(self.temporary.name) / "outside.json"
        self.assert_error("UNSAFE_PATH", common.safe_path, self.root, "../outside.json")
        self.assert_error("UNSAFE_PATH", common.safe_path, self.root, str(outside))
        self.assertFalse(outside.exists())

    def test_safe_path_rejects_symlink_escape_to_nonexistent_output(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable on this system")
        self.assert_error("UNSAFE_PATH", common.safe_path, self.root, "link/report.json")
        self.assertFalse((outside / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
