from __future__ import annotations

import importlib.util
import ast
import hashlib
import html as html_lib
import json
import os
from pathlib import Path
import re
import tempfile
import types
import typing
import unittest
import uuid
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill" / "tool_contracts.py"
RUNTIME_SOURCE = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill" / "tools" / "omni_body_tool.py"
READABLE_RUNTIME_SOURCE = ROOT / "readable-python-source" / "omni_body_skill" / "tools" / "omni_body_tool.py"
DELIVERY_SOURCE = ROOT / "app" / "backend" / "tiangong-backend" / "_internal" / "omni_body_skill" / "tools" / "delivery_kernel.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("test_omni_body_tool_contracts", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_body_run():
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"), filename=str(RUNTIME_SOURCE))
    body_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BodyRuntime"
    )
    run_method = next(
        node for node in body_class.body if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    namespace = {
        "ACTIONS": {"pptx.create": {"risk": "A2"}},
        "Any": typing.Any,
        "Dict": typing.Dict,
        "Optional": typing.Optional,
        "fact_execution_active": lambda: False,
        "uuid": uuid,
        "validate_tool_request": lambda action, target, payload, **_kwargs: {
            "ok": True,
            "action": action,
            "target": target,
            "args": payload,
        },
    }
    exec(compile(ast.Module(body=[run_method], type_ignores=[]), str(RUNTIME_SOURCE), "exec"), namespace)
    return namespace["run"]


def _load_body_methods(*names: str):
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"), filename=str(RUNTIME_SOURCE))
    body_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BodyRuntime"
    )
    selected = [
        node for node in body_class.body if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "Any": typing.Any,
        "Dict": typing.Dict,
        "List": typing.List,
        "Optional": typing.Optional,
        "Path": Path,
        "hashlib": hashlib,
        "html_lib": html_lib,
        "os": os,
        "re": re,
        "zipfile": zipfile,
        "OmniBodyError": RuntimeError,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(RUNTIME_SOURCE), "exec"), namespace)
    return {name: namespace[name] for name in names}


def _load_delivery_methods(*names: str):
    tree = ast.parse(DELIVERY_SOURCE.read_text(encoding="utf-8"), filename=str(DELIVERY_SOURCE))
    dependencies = {"_resolve", "_rel", "_issue", "_score_from_issues", "_grade", *names}
    selected = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in dependencies
    ]
    namespace = {
        "Any": typing.Any,
        "Dict": typing.Dict,
        "List": typing.List,
        "Path": Path,
        "json": json,
        "re": re,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(DELIVERY_SOURCE), "exec"), namespace)
    return {name: namespace[name] for name in names}


class OmniBodyOfficeContractTests(unittest.TestCase):
    def test_readable_runtime_mirror_is_identical(self) -> None:
        self.assertEqual(
            RUNTIME_SOURCE.read_bytes(),
            READABLE_RUNTIME_SOURCE.read_bytes(),
        )

    def test_long_doc_accepts_workspace_markdown_source(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            source = workspace / "正文.md"
            source.write_text("# 标题\n\n正文", encoding="utf-8")
            result = module.validate_tool_request(
                "docx.create",
                str(workspace / "交付.docx"),
                {"markdown_file": str(source)},
                workspace=workspace,
                available_actions=("docx.create",),
            )
            self.assertTrue(result["ok"], result["issues"])
            self.assertEqual(result["args"]["source"], str(source))
            self.assertIn("args.markdown_file->args.source", result["argument_aliases"])

    def test_office_generators_reject_empty_or_wrong_extension(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            empty = module.validate_tool_request(
                "pptx.create",
                str(workspace / "deck.pptx"),
                {},
                workspace=workspace,
                available_actions=("pptx.create",),
            )
            self.assertFalse(empty["ok"])
            self.assertTrue(any(item["code"] == "content_source_required" for item in empty["issues"]))
            wrong = module.validate_tool_request(
                "mindmap.create",
                str(workspace / "map.opml"),
                {"content": "根\n  子节点"},
                workspace=workspace,
                available_actions=("mindmap.create",),
            )
            self.assertFalse(wrong["ok"])
            self.assertTrue(any(item["code"] == "output_extension" for item in wrong["issues"]))

    def test_pptx_read_and_design_spec_contracts_are_model_callable_and_bounded(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            accepted = module.validate_tool_request(
                "pptx.read",
                str(workspace / "deck.pptx"),
                {"max_chars_per_slide": 2400},
                workspace=workspace,
                available_actions=("pptx.read",),
            )
            self.assertTrue(accepted["ok"], accepted["issues"])
            invalid_read = module.validate_tool_request(
                "pptx.read",
                str(workspace / "deck.pptx"),
                {"max_chars_per_slide": 100_000},
                workspace=workspace,
                available_actions=("pptx.read",),
            )
            self.assertFalse(invalid_read["ok"])
            self.assertTrue(any(item["code"] == "bounded_integer" for item in invalid_read["issues"]))
            outside_design = module.validate_tool_request(
                "pptx.create",
                str(workspace / "deck.pptx"),
                {"content": "# title", "design_spec": str(workspace.parent / "outside.json")},
                workspace=workspace,
                available_actions=("pptx.create",),
            )
            self.assertFalse(outside_design["ok"])
            self.assertTrue(any(item["code"] == "outside_workspace" for item in outside_design["issues"]))

    def test_execution_timeouts_are_positive_and_bounded_before_dispatch(self) -> None:
        module = _load_module()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            for invalid in (-1, 0, True, "30", 3601):
                result = module.validate_tool_request(
                    "python.run",
                    "",
                    {"code": "print('ok')", "timeout": invalid},
                    workspace=workspace,
                    available_actions=("python.run",),
                )
                self.assertFalse(result["ok"], invalid)
                self.assertTrue(
                    any(item["code"] == "bounded_positive_integer" for item in result["issues"]),
                    (invalid, result["issues"]),
                )
            accepted = module.validate_tool_request(
                "python.run",
                "",
                {"code": "print('ok')", "timeout": 30},
                workspace=workspace,
                available_actions=("python.run",),
            )
            self.assertTrue(accepted["ok"], accepted["issues"])

    def test_managed_long_document_qc_is_manifest_bound_and_rejects_partial_delivery(self) -> None:
        qc = _load_delivery_methods("_qc_managed_long_document")["_qc_managed_long_document"]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            project = workspace / "project"
            chapters = project / "chapters"
            chapters.mkdir(parents=True)
            chapter_paths = []
            for index in range(1, 4):
                chapter = chapters / f"{index:03d}.md"
                chapter.write_text(f"第{index}章\n" + chr(64 + index) * 1800, encoding="utf-8")
                chapter_paths.append(chapter.relative_to(project).as_posix())
            manifest = project / "project_manifest.json"
            manifest.write_text(
                json.dumps({"target_words": 5000, "chapter_files": chapter_paths}, ensure_ascii=False),
                encoding="utf-8",
            )
            output = project / "final.docx"
            output.write_bytes(b"openable-container-evidence")

            class Runtime:
                def _resolve(self, value, must_exist=False):
                    path = Path(value)
                    if not path.is_absolute():
                        path = workspace / path
                    path = path.resolve()
                    path.relative_to(workspace)
                    if must_exist and not path.exists():
                        raise FileNotFoundError(path)
                    return path

                def _rel(self, path):
                    return Path(path).resolve().relative_to(workspace).as_posix()

            complete_text = "\n".join(
                [f"第 {index} 章\n" + chr(96 + index) * 1800 for index in range(1, 4)]
            )
            passed = qc(
                Runtime(),
                output,
                complete_text,
                {"project_manifest": str(manifest)},
            )
            self.assertTrue(passed["result"]["acceptance"], passed["result"]["issues"])
            self.assertEqual(passed["result"]["chapter_file_count"], 3)

            broken_manifest = project / "broken_manifest.json"
            broken_manifest.write_text(
                json.dumps(
                    {"target_words": 20000, "chapter_files": ["chapters/001.md", "../escape.md", "chapters/missing.md"]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            failed = qc(
                Runtime(),
                output,
                "第 1 章\n局部内容",
                {"project_manifest": str(broken_manifest)},
            )
            self.assertFalse(failed["result"]["acceptance"])
            codes = {item["code"] for item in failed["result"]["issues"]}
            self.assertIn("unsafe_chapter_paths", codes)
            self.assertIn("missing_chapter_files", codes)
            self.assertIn("document_incomplete", codes)

    def test_wechat_miniapp_qc_checks_native_file_family_and_page_paths(self) -> None:
        check = _load_delivery_methods("_miniapp_project_issues")["_miniapp_project_issues"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            valid = {
                "app.js": "App({});",
                "app.json": json.dumps({"pages": ["pages/index/index"]}),
                "project.config.json": json.dumps({"appid": "touristappid"}),
                "pages/index/index.js": "Page({});",
                "pages/index/index.wxml": "<view>ok</view>",
                "pages/index/index.wxss": "view { padding: 1px; }",
            }
            for relative, content in valid.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            runtime = types.SimpleNamespace(_rel=lambda path: Path(path).resolve().relative_to(root).as_posix())
            candidates = [path for path in root.rglob("*") if path.is_file()]
            self.assertEqual(check(runtime, root, candidates), [])

            (root / "app.json").write_text(
                json.dumps({"pages": ["../escape", "pages/missing/index"]}),
                encoding="utf-8",
            )
            candidates = [path for path in root.rglob("*") if path.is_file()]
            issues = check(runtime, root, candidates)
            codes = {item["code"] for item in issues}
            self.assertIn("miniapp_page_path_unsafe", codes)
            self.assertIn("miniapp_page_file_missing", codes)

    def test_missing_generator_output_invalidates_successful_idempotent_replay(self) -> None:
        run = _load_body_run()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "deck.pptx"

            class FakeKernel:
                def __init__(self) -> None:
                    self.keys: list[str] = []

                def execute(self, *_args, **kwargs):
                    self.keys.append(str(kwargs.get("idempotency_key") or ""))
                    if len(self.keys) == 1:
                        return {
                            "success": True,
                            "fact_transaction": {
                                "idempotent_replay": True,
                                "operation_id": "op_stale",
                            },
                        }
                    output.write_bytes(b"rebuilt")
                    return {
                        "success": True,
                        "fact_transaction": {"idempotent_replay": False},
                    }

            runtime = types.SimpleNamespace(
                workspace=Path(temporary),
                config=types.SimpleNamespace(
                    fact_kernel_enabled=True,
                    step_id="step-1",
                    task_node_id="task-1",
                ),
                fact_kernel=FakeKernel(),
                _resolve=lambda target: Path(target),
                _run_legacy=lambda *_args, **_kwargs: {"success": True},
                _recreatable_output_valid=lambda _action, _target, _args, _result: output.is_file(),
            )
            result = run(
                runtime,
                "pptx.create",
                str(output),
                {"content": "# cover", "idempotency_key": "stable-key"},
            )

            self.assertTrue(output.is_file())
            self.assertTrue(result["stale_replay_recovered"])
            self.assertEqual(result["stale_replay_operation_id"], "op_stale")
            self.assertEqual(runtime.fact_kernel.keys[0], "stable-key")
            self.assertRegex(runtime.fact_kernel.keys[1], r"^stable-key:missing-output-recovery:[0-9a-f]{32}$")

    def test_corrupted_existing_replay_is_recovered_and_revalidated(self) -> None:
        run = _load_body_run()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "deck.pptx"
            output.write_bytes(b"corrupt")

            class FakeKernel:
                def __init__(self) -> None:
                    self.calls = 0

                def execute(self, *_args, **_kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        return {"success": True, "fact_transaction": {"idempotent_replay": True, "operation_id": "old"}}
                    output.write_bytes(b"valid")
                    return {"success": True, "fact_transaction": {"idempotent_replay": False}}

            runtime = types.SimpleNamespace(
                workspace=Path(temporary),
                config=types.SimpleNamespace(fact_kernel_enabled=True, step_id="step", task_node_id="task"),
                fact_kernel=FakeKernel(),
                _resolve=lambda target: Path(target),
                _run_legacy=lambda *_args, **_kwargs: {"success": True},
                _recreatable_output_valid=lambda _action, _target, _args, _result: output.read_bytes() == b"valid",
            )
            result = run(runtime, "pptx.create", str(output), {"content": "# cover", "idempotency_key": "stable"})
            self.assertTrue(result["success"])
            self.assertTrue(result["stale_replay_recovered"])
            self.assertEqual(runtime.fact_kernel.calls, 2)

    def test_replay_validator_checks_digest_and_office_container_structure(self) -> None:
        method = _load_body_methods("_recreatable_output_valid")["_recreatable_output_valid"]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "deck.pptx"
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("ppt/presentation.xml", "<presentation/>")
                archive.writestr("ppt/slides/slide1.xml", "<slide/>")

            class Runtime:
                _recreatable_output_valid = method

                def _resolve(self, value):
                    return Path(value)

            evidence = {
                "output": {
                    "size_bytes": output.stat().st_size,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                }
            }
            runtime = Runtime()
            self.assertTrue(runtime._recreatable_output_valid("pptx.create", str(output), {}, evidence))
            output.write_bytes(b"not-a-pptx")
            self.assertFalse(runtime._recreatable_output_valid("pptx.create", str(output), {}, evidence))

    def test_mindmap_source_generates_one_root_and_transactional_opml(self) -> None:
        methods = _load_body_methods(
            "_action_mindmap_create",
            "_mindmap_html",
            "_escape_mermaid",
            "_parse_indented_tree",
            "_mindmap_lines",
            "_opml",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "outline.md"
            source.write_text(
                "# City Resilience\n  Prevention\n    Monitoring\n  Response\n    Recovery\n",
                encoding="utf-8",
            )
            output = root / "map.md"

            class Runtime:
                _action_mindmap_create = methods["_action_mindmap_create"]
                _mindmap_html = methods["_mindmap_html"]
                _escape_mermaid = methods["_escape_mermaid"]
                _parse_indented_tree = methods["_parse_indented_tree"]
                _mindmap_lines = methods["_mindmap_lines"]
                _opml = methods["_opml"]

                def _resolve(self, value, must_exist=False):
                    path = Path(value)
                    if not path.is_absolute():
                        path = root / path
                    if must_exist and not path.exists():
                        raise FileNotFoundError(path)
                    return path

                def _snapshot(self, _op_id, paths):
                    return [str(path) for path in paths]

                def _file_evidence(self, path):
                    return {"path": str(path), "exists": path.is_file(), "bytes": path.stat().st_size}

            result = Runtime()._action_mindmap_create(
                "op-1",
                str(output),
                {"source": str(source), "opml": True},
            )

            markdown = output.read_text(encoding="utf-8")
            self.assertEqual(markdown.count("root((City Resilience))"), 1)
            self.assertEqual(markdown.count("City Resilience"), 1)
            opml = output.with_suffix(".opml")
            html = output.with_suffix(".html")
            xml_root = ET.parse(opml).getroot()
            top_labels = [node.attrib["text"] for node in xml_root.findall("./body/outline")]
            self.assertEqual(top_labels, ["Prevention", "Response"])
            self.assertEqual(result["content_mode"], "source_text")
            self.assertEqual(result["source"], str(source))
            self.assertEqual(set(result["snapshots"]), {str(output), str(opml), str(html)})
            self.assertIn('class="mermaid"', html.read_text(encoding="utf-8"))
            self.assertEqual(result["html"]["path"], str(html))

            hostile_output = root / "hostile.md"
            hostile = Runtime()._action_mindmap_create(
                "op-2",
                str(hostile_output),
                {
                    "title": "Root)):::evil%%{init}<script>\x01",
                    "tree": [{"Child[bad]:::class\x02": []}],
                    "opml": True,
                },
            )
            hostile_markdown = hostile_output.read_text(encoding="utf-8")
            self.assertEqual(hostile["content_mode"], "tree")
            self.assertNotIn(":::", hostile_markdown)
            self.assertNotIn("[bad]", hostile_markdown)
            self.assertNotIn("<script>", hostile_markdown)
            ET.parse(hostile_output.with_suffix(".opml"))
            hostile_html = hostile_output.with_suffix(".html").read_text(encoding="utf-8")
            self.assertNotIn("<script>\x01", hostile_html)


if __name__ == "__main__":
    unittest.main()
