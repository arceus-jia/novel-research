"""Small shared primitives for the local novel workspace. No model calls."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


VERSION = "0.5.0"
FORMAT_VERSION = 1
SOURCE_KINDS = ("text", "supplement", "author_note", "interview", "commentary", "user_note")
CLAIM_KINDS = ("narration", "speech", "belief", "inference", "interpretation", "unknown")


class NovelError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def require(condition, code, message, details=None):
    if not condition:
        raise NovelError(code, message, details)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "INVALID_JSON", f"JSON 中的字段重复：{key}")
        result[key] = value
    return result


def _non_finite(value):
    raise NovelError("INVALID_JSON", f"JSON 不接受非有限数值：{value}")


def read_json(path, max_bytes=8 * 1024 * 1024):
    path = Path(path)
    require(path.is_file(), "NOT_FOUND", f"文件不存在：{path}")
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        require(len(raw) <= max_bytes, "INPUT_TOO_LARGE", f"JSON 文件超过读取上限：{path}")
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_non_finite)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise NovelError("INVALID_JSON", f"无法解析 UTF-8 JSON：{path}", str(error)) from error


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    require(not relative.is_absolute(), "UNSAFE_PATH", "工作区内路径必须是相对路径。", str(relative))
    path = (root / relative).resolve()
    require(path == root or root in path.parents, "UNSAFE_PATH", "路径超出工作区。", str(relative))
    return path


def valid_id(value, field="id"):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value),
            "INVALID_INPUT", f"{field} 仅接受 1–100 个英文字母、数字、下划线或连字符。")
    return value


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


@contextmanager
def writer_lock(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    internal = safe_path(root, ".novel")
    internal.mkdir(exist_ok=True)
    lock = safe_path(root, ".novel/write.lock")
    try:
        lock.mkdir()
    except FileExistsError as error:
        owner = None
        try:
            owner = read_json(lock / "owner.json")
        except (NovelError, OSError):
            pass
        raise NovelError("WORKSPACE_BUSY", "工作区存在写锁；确认没有写入任务后再处理遗留锁。",
                         {"lock": str(lock), "owner": owner}) from error
    try:
        atomic_write_json(lock / "owner.json", {"pid": os.getpid(), "created_at": utc_now()})
        yield
    finally:
        (lock / "owner.json").unlink(missing_ok=True)
        lock.rmdir()


def load_record(path, max_bytes=8 * 1024 * 1024):
    record = read_json(path, max_bytes=max_bytes)
    require(isinstance(record, dict), "CORRUPT_RECORD", "记录必须是 JSON 对象。", str(path))
    require(type(record.get("format_version")) is int and record["format_version"] == FORMAT_VERSION,
            "UNSUPPORTED_VERSION", "记录格式版本不受支持。", str(path))
    require(isinstance(record.get("data"), dict), "CORRUPT_RECORD", "记录缺少 data 对象。", str(path))
    require(record.get("payload_hash") == digest(record["data"]), "CORRUPT_RECORD", "记录内容校验失败。", str(path))
    return record["data"]


def save_record(path, data):
    """Caller holds the workspace write lock. Accepted records are immutable."""
    path = Path(path)
    require(isinstance(data, dict), "INVALID_INPUT", "正式记录必须是 JSON 对象。")
    if path.exists():
        existing = load_record(path)
        require(digest(existing) == digest(data), "RECORD_CONFLICT", "同名记录已存在，保留原记录。", str(path))
        return existing
    atomic_write_json(path, {"format_version": FORMAT_VERSION, "created_at": utc_now(),
                             "payload_hash": digest(data), "data": data})
    return data


def check_keys(value, required=(), optional=()):
    require(isinstance(value, dict), "INVALID_INPUT", "输入必须是 JSON 对象。")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    require(not missing and not extra, "INVALID_INPUT", "输入字段不符合格式。",
            {"missing": sorted(missing), "unknown": sorted(extra)})


def bounded_text(value, field, maximum=4000, allow_empty=False):
    require(isinstance(value, str) and (allow_empty or bool(value.strip())) and len(value) <= maximum,
            "INVALID_INPUT", f"{field} 必须是长度不超过 {maximum} 的字符串。")
    return value


def bounded_list(value, field, maximum=100):
    require(isinstance(value, list) and len(value) <= maximum, "INVALID_INPUT",
            f"{field} 必须是长度不超过 {maximum} 的列表。")
    return value


def check_python():
    require(sys.version_info >= (3, 11), "ENVIRONMENT", "需要 Python 3.11 或以上版本。")
