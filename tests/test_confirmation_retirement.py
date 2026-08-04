"""G3 确认退役（草案 §4.2）回归测试。

验证目标：
- /api/v1/policy/confirm 任何方法都返回 HTTP 410 + POLICY_CONFIRMATION_RETIRED；
- /api/v1/policy/confirm/archive 只读：调用前后 pending_confirmations.json
  内容哈希不变；文件缺失/损坏时返回空列表且不创建任何文件；
- 退役端点不创建任何新 grant/confirmation 记录（state 目录保持为空）。

装配方式参考 tests/test_foundation_closeout.py：
object.__new__(_ChuliQi) + 桩掉的 _write_json/_authorize_business_route，
不起真实 HTTP 服务、不占端口。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for item in (
    ROOT / "src",
    ROOT / "app/backend/tiangong-backend",
    ROOT / "app/life-service/runtime314",
):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from v3.duihua_qiaojie import _ChuliQi  # noqa: E402

RETIRED_MARK = "POLICY_CONFIRMATION_RETIRED"
CONFIRM_PATH = "/api/v1/policy/confirm"
ARCHIVE_PATH = "/api/v1/policy/confirm/archive"


def _make_handler(path: str):
    """最小装配 handler：捕获 _write_json 输出，绕过鉴权与 socket。"""
    handler = object.__new__(_ChuliQi)
    handler.path = path
    handler.headers = Message()
    handler._authorize_business_route = lambda _path: True
    observed: list[tuple[dict, int]] = []
    handler._write_json = lambda body, status=200: observed.append((body, status))
    return handler, observed


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PolicyConfirmRetiredTests(unittest.TestCase):
    """退役端点：任何方法一律 410 + POLICY_CONFIRMATION_RETIRED。"""

    def _assert_retired(self, observed: list[tuple[dict, int]]) -> None:
        self.assertTrue(observed, "handler 必须产生一条应答")
        body, status = observed[-1]
        self.assertEqual(status, 410)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("error"), RETIRED_MARK)
        self.assertEqual(body.get("error_code"), RETIRED_MARK)
        self.assertIs(body.get("retired"), True)

    def test_post_returns_410(self) -> None:
        handler, observed = _make_handler(CONFIRM_PATH)
        handler.do_POST()  # confirm 分支不读体即固定 410
        self._assert_retired(observed)

    def test_get_returns_410(self) -> None:
        handler, observed = _make_handler(CONFIRM_PATH)
        handler.do_GET()
        self._assert_retired(observed)

    def test_put_delete_patch_return_410(self) -> None:
        for method in ("do_PUT", "do_DELETE", "do_PATCH"):
            with self.subTest(method=method):
                handler, observed = _make_handler(CONFIRM_PATH)
                getattr(handler, method)()
                self._assert_retired(observed)


class PolicyConfirmArchiveTests(unittest.TestCase):
    """归档端点：只读列出 pending_confirmations.json，绝不写盘。"""

    def setUp(self) -> None:
        # 每个用例独立临时 state 目录，避免触碰真实 ~/.tiangong
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self._env = mock.patch.dict(
            os.environ, {"TIANGONG_V3_STATE_DIR": str(self.state_dir)}
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def _write_pending(self, records: list[dict]) -> Path:
        target = self.state_dir / "pending_confirmations.json"
        payload = {"version": 1, "records": records}
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def test_archive_lists_records_and_never_writes(self) -> None:
        records = [
            {"confirm_id": "c-1", "action": "file.write", "status": "expired"},
            {"confirm_id": "c-2", "action": "shell.run", "status": "pending"},
        ]
        target = self._write_pending(records)
        before = _sha256(target)
        handler, observed = _make_handler(ARCHIVE_PATH)
        handler.do_GET()
        body, status = observed[-1]
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIs(body.get("read_only"), True)
        self.assertIs(body.get("retired"), True)
        self.assertEqual(body.get("count"), 2)
        ids = {item.get("confirm_id") for item in body.get("archive") or []}
        self.assertEqual(ids, {"c-1", "c-2"})
        # 只读：调用前后文件内容哈希不变
        self.assertEqual(_sha256(target), before)

    def test_archive_missing_file_returns_empty_and_creates_nothing(self) -> None:
        handler, observed = _make_handler(ARCHIVE_PATH)
        handler.do_GET()
        body, status = observed[-1]
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIs(body.get("read_only"), True)
        self.assertIs(body.get("retired"), True)
        self.assertEqual(body.get("archive"), [])
        self.assertEqual(body.get("count"), 0)
        # 读不到文件时返回空列表而非报错，且不创建任何文件
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_archive_corrupt_file_returns_empty_and_never_writes(self) -> None:
        target = self.state_dir / "pending_confirmations.json"
        target.write_text("{ 这不是合法 JSON", encoding="utf-8")
        before = _sha256(target)
        handler, observed = _make_handler(ARCHIVE_PATH)
        handler.do_GET()
        body, status = observed[-1]
        self.assertEqual(status, 200)
        self.assertEqual(body.get("archive"), [])
        self.assertEqual(body.get("count"), 0)
        self.assertEqual(_sha256(target), before)


class RetirementCreatesNothingTests(unittest.TestCase):
    """退役端点不得创建任何新 grant/confirmation 记录或签名材料。"""

    def test_confirm_endpoints_create_no_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"TIANGONG_V3_STATE_DIR": tmp}):
                for method in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
                    handler, observed = _make_handler(CONFIRM_PATH)
                    getattr(handler, method)()
                    _, status = observed[-1]
                    self.assertEqual(status, 410)
                handler, _ = _make_handler(ARCHIVE_PATH)
                handler.do_GET()
                # state 目录必须保持为空：无 pending 文件、无签名密钥、无 grant 记录
                self.assertEqual(list(Path(tmp).rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
