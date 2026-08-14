"""P17-M4 architecture guards: import side effects, forbidden legacy imports,
single runtime/gateway authority and layer dependency direction.

These are static AST scans over the authoritative source tree. They encode the
architecture that P17 M1-M3 converged on and fail closed whenever the tree
drifts toward a second authority, an import-time side effect, a legacy tree
import or a forbidden layer dependency. All four scans run in the permanent
Architecture Gate on both Ubuntu and Windows.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
V3 = ROOT / "app" / "backend" / "tiangong-backend" / "v3"

AUTHORITATIVE_PACKAGES = (
    "contracts",
    "life_service",
    "total_gateway",
    "communication_service",
    "runtime_security",
    "world_understanding",
    "omni_body_skill",
)

# Sanctioned cross-layer edges that remain narrow, lazy and module-specific:
#   - life_service only notifies world_understanding.post_commit (memory ->
#     world candidate outbox consumer hook)
#   - world_understanding only reads life_service.action_intents (self-will
#     integration adapter)
ALLOWED_LIFE_WORLD_EDGES = {
    ("life_service", "world_understanding.post_commit"),
    ("world_understanding", "life_service.action_intents"),
}

FORBIDDEN_LEGACY_SEGMENTS = (
    "_internal",
    "legacy_pyz",
    "frozen_modules",
    "readable_python_source",
)

SIDE_EFFECT_NAME_MARKERS = (
    "install", "observer", "register", "start", "print",
    "init", "configure", "apply", "mount", "listen", "bind",
    "connect", "attach", "watch", "hook", "patch",
)


def _module_trees(package: str):
    package_root = SRC / package
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _top_level_calls(tree: ast.AST) -> list[tuple[int, ast.Call]]:
    """All module-top-level call expressions, including assignment values.

    ``X = install_y()`` is as much an import-time side effect as a bare
    ``install_y()`` statement, so both shapes are scanned.
    """
    calls: list[tuple[int, ast.Call]] = []
    for node in tree.body:
        call = None
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.value, ast.Call)
        ):
            call = node.value
        if call is not None:
            calls.append((node.lineno, call))
    return calls


def _relative_import_module(path: Path, level: int, module: str | None) -> str | None:
    """Resolve ``from .x import`` / ``from ..x import`` to the absolute module.

    level=1 drops the file name (package remains); level=2 drops one package
    component and so on.  A level that would escape the top-level package is
    invalid at runtime and returns None.
    """
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if len(parts) <= level:
        return None
    base = parts[: len(parts) - level]
    if module:
        base = base + module.split(".")
    return ".".join(base)


def _imported_modules(tree: ast.AST, path: Path) -> list[str]:
    """Every absolute module referenced by an import statement.

    Handles multi-name ``import a, b`` (all names, not just the first), and
    resolves relative ``from . / from ..`` imports to their absolute module
    so they cannot hide a forbidden or cross-layer target.
    """
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            level = int(node.level or 0)
            if level:
                resolved = _relative_import_module(path, level, node.module)
                if resolved:
                    modules.append(resolved)
            elif node.module:
                modules.append(node.module)
    return modules


class ImportSideEffectGuardTests(unittest.TestCase):
    def test_authoritative_packages_have_no_import_time_side_effects(self) -> None:
        """No authoritative module may run install/observer/register/init
        style calls at import time — neither as bare statements nor as
        assignment values.  The V3 zongdiaodu observer seam is pinned
        separately by the M2-01 regression."""
        violations: list[str] = []
        for package in AUTHORITATIVE_PACKAGES:
            for path, tree in _module_trees(package):
                for lineno, call in _top_level_calls(tree):
                    name = _call_name(call) or ""
                    if any(marker in name.lower() for marker in SIDE_EFFECT_NAME_MARKERS):
                        violations.append(f"{path.relative_to(ROOT).as_posix()}:{lineno}: {name}")
        self.assertEqual([], violations)

    def test_synthetic_assignment_side_effect_is_detected(self) -> None:
        tree = ast.parse("GLOBAL = _install_observers()\n")
        calls = _top_level_calls(tree)
        self.assertEqual(1, len(calls))
        self.assertEqual("_install_observers", _call_name(calls[0][1]))

    def test_synthetic_bare_side_effect_is_detected(self) -> None:
        tree = ast.parse("service_registry.mount_all()\n")
        calls = _top_level_calls(tree)
        self.assertEqual(1, len(calls))
        self.assertEqual("mount_all", _call_name(calls[0][1]))


class ForbiddenLegacyImportGuardTests(unittest.TestCase):
    def test_authoritative_packages_never_import_legacy_trees(self) -> None:
        """Authoritative src/ packages must not import the frozen/legacy
        runtime trees. The gateway's sanctioned embedding of the V3 backend is
        via app/backend/tiangong-backend/v3, not the _internal mirrors."""
        violations: list[str] = []
        for package in AUTHORITATIVE_PACKAGES:
            for path, tree in _module_trees(package):
                for module in _imported_modules(tree, path):
                    if any(segment in module for segment in FORBIDDEN_LEGACY_SEGMENTS):
                        violations.append(f"{path.relative_to(ROOT).as_posix()}: imports {module}")
        self.assertEqual([], violations)

    def test_synthetic_multi_import_second_name_is_detected(self) -> None:
        tree = ast.parse("import os, _internal.frozen as f\n")
        modules = _imported_modules(tree, SRC / "life_service" / "sample.py")
        self.assertIn("_internal.frozen", modules)

    def test_synthetic_aliased_forbidden_import_is_detected(self) -> None:
        tree = ast.parse("from _internal import boot as b\n")
        modules = _imported_modules(tree, SRC / "life_service" / "sample.py")
        self.assertIn("_internal", modules)

    def test_synthetic_relative_import_resolves_to_absolute_module(self) -> None:
        tree = ast.parse("from ..sibling import helper\n")
        modules = _imported_modules(tree, SRC / "life_service" / "sub" / "sample.py")
        self.assertIn("life_service.sibling", modules)


class SingleRuntimeAuthorityTests(unittest.TestCase):
    @staticmethod
    def _calls(root: Path, *, target: str, attr: str | None = None):
        sites: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if attr is not None:
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == attr
                        and isinstance(func.value, ast.Name)
                        and func.value.id == target
                    ):
                        sites.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
                elif isinstance(func, ast.Name) and func.id == target:
                    sites.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        return sites

    def test_single_gateway_start_and_single_http_server(self) -> None:
        starts = self._calls(SRC / "total_gateway", target="GatewayRuntime", attr="start")
        servers = self._calls(SRC / "total_gateway", target="GatewayHttpServer")
        self.assertEqual(
            ["src/total_gateway/server.py"],
            [site.split(":")[0] for site in starts],
            starts,
        )
        self.assertEqual(1, len(starts))
        self.assertEqual(
            ["src/total_gateway/server.py"],
            [site.split(":")[0] for site in servers],
            servers,
        )
        self.assertEqual(1, len(servers))
        # The runtime is factory-constructed only; no direct constructor site.
        self.assertEqual([], self._calls(SRC / "total_gateway", target="GatewayRuntime"))

    def test_embedded_hosts_are_the_only_life_and_backend_entry_points(self) -> None:
        life_sites = self._calls(SRC, target="EmbeddedLifeRuntime", attr="from_environment")
        self.assertEqual(
            sorted(["src/life_service/standalone.py", "src/total_gateway/runtime.py"]),
            sorted(site.split(":")[0] for site in life_sites),
            life_sites,
        )
        backend_sites = self._calls(SRC / "total_gateway", target="EmbeddedBackendRuntime", attr="start")
        self.assertEqual(1, len(backend_sites))
        self.assertEqual("src/total_gateway/runtime.py", backend_sites[0].split(":")[0])

    def test_complete_life_system_is_constructed_only_by_the_embedded_host(self) -> None:
        sites = self._calls(SRC / "life_service", target="CompleteLifeSystem")
        self.assertTrue(sites, "embedded host must construct CompleteLifeSystem")
        self.assertEqual(
            ["src/life_service/embedded_runtime.py"],
            sorted({site.split(":")[0] for site in sites}),
        )

    def test_v3_dispatcher_has_exactly_one_daemon_entry_point(self) -> None:
        sites = self._calls(V3, target="Zongdiaodu")
        self.assertEqual(1, len(sites), sites)
        self.assertEqual(
            "app/backend/tiangong-backend/v3/desktop_daemon.py",
            sites[0].split(":")[0],
        )


class LayerDependencyTests(unittest.TestCase):
    def _sibling_imports(self, package: str) -> set[str]:
        imports: set[str] = set()
        for path, tree in _module_trees(package):
            for module in _imported_modules(tree, path):
                root = module.split(".")[0]
                # Same-package imports are structure, not layer dependencies.
                if root == package:
                    continue
                if root in AUTHORITATIVE_PACKAGES:
                    imports.add(module)
        return imports

    def test_contracts_and_skill_leaves_import_no_siblings(self) -> None:
        self.assertEqual(set(), self._sibling_imports("contracts"))
        self.assertEqual(set(), self._sibling_imports("omni_body_skill"))

    def test_runtime_security_only_depends_on_contracts(self) -> None:
        imports = self._sibling_imports("runtime_security")
        self.assertTrue(
            all(module.split(".")[0] == "contracts" for module in imports),
            imports,
        )

    def test_communication_service_only_depends_on_contracts_and_security(self) -> None:
        roots = {module.split(".")[0] for module in self._sibling_imports("communication_service")}
        self.assertTrue(roots <= {"contracts", "runtime_security"}, roots)

    def test_life_and_world_never_depend_on_gateway_communication_or_security(self) -> None:
        forbidden = {"total_gateway", "communication_service", "runtime_security"}
        for package in ("life_service", "world_understanding"):
            imports = self._sibling_imports(package)
            roots = {module.split(".")[0] for module in imports}
            self.assertFalse(roots & forbidden, f"{package}: {imports}")

    def test_life_world_edges_stay_narrow_and_sanctioned(self) -> None:
        life_world = {
            module
            for module in self._sibling_imports("life_service")
            if module.startswith("world_understanding")
        }
        world_life = {
            module
            for module in self._sibling_imports("world_understanding")
            if module.startswith("life_service")
        }
        actual = {
            ("life_service", module) for module in life_world
        } | {
            ("world_understanding", module) for module in world_life
        }
        self.assertEqual(set(), actual - ALLOWED_LIFE_WORLD_EDGES, actual)


if __name__ == "__main__":
    unittest.main()