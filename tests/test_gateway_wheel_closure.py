"""Gateway package discovery must work outside the developer checkout."""

from __future__ import annotations

import ast
import fnmatch
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def local_import_closure() -> set[str]:
    source = ROOT / "src"
    packages = {
        path.name: path for path in source.iterdir()
        if path.is_dir() and not path.name.endswith(".egg-info")
        and path.name != "__pycache__"
    }
    imports = {name: set() for name in packages}
    for package, directory in packages.items():
        for file in directory.rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8-sig"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                elif isinstance(node, ast.Call) and node.args:
                    called = (node.func.id if isinstance(node.func, ast.Name)
                              else node.func.attr if isinstance(node.func, ast.Attribute) else "")
                    argument = node.args[0]
                    if called in {"import_module", "__import__"} and isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        names = [argument.value]
                imports[package].update(name.split(".", 1)[0] for name in names if name.split(".", 1)[0] in packages)
    closure = {"total_gateway"}
    pending = ["total_gateway"]
    while pending:
        for package in imports[pending.pop()] - closure:
            closure.add(package)
            pending.append(package)
    return closure


class GatewayWheelClosureTests(unittest.TestCase):
    def test_package_discovery_covers_gateway_local_import_closure(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        discovery = config["tool"]["setuptools"]["packages"]["find"]
        # Omni Body is separately shipped under the selected backend root. The
        # adapter inserts that verified root before its lazy executor imports;
        # putting another copy in this wheel would create a competing origin.
        backend_package = ROOT / "app/backend/tiangong-backend/omni_body_skill"
        self.assertTrue((backend_package / "__init__.py").is_file())
        required = local_import_closure() - {"omni_body_skill"}
        excluded = discovery.get("exclude", [])
        uncovered = sorted(package for package in required if
                           not any(fnmatch.fnmatchcase(package, pattern) for pattern in discovery["include"])
                           or any(fnmatch.fnmatchcase(package, pattern) for pattern in excluded))
        self.assertEqual(uncovered, [], f"Gateway source packages missing from the installed wheel: {uncovered}")

    def test_built_wheel_contains_real_source_authority_and_imports_in_isolated_child(self) -> None:
        # Build only this package in a temporary project using the REAL release
        # metadata/discovery rules. No package manager, dependency resolution,
        # repository build directory, or product process is involved.
        with tempfile.TemporaryDirectory(prefix="tiangong-wheel-closure-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            shutil.copyfile(ROOT / "pyproject.toml", project / "pyproject.toml")
            source = ROOT / "src/source_authority"
            for file in source.rglob("*.py"):
                target = project / "src/source_authority" / file.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
            wheel_root = root / "dist"
            wheel_root.mkdir()
            built = subprocess.run(
                [sys.executable, "-I", "-B", "-c",
                 "import sys; from setuptools.build_meta import build_wheel; print(build_wheel(sys.argv[1]))",
                 str(wheel_root)],
                cwd=project, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            wheels = list(wheel_root.glob("*.whl"))
            self.assertEqual(len(wheels), 1, built.stdout + built.stderr)
            installed = root / "installed-site-packages"
            installed.mkdir()
            with zipfile.ZipFile(wheels[0]) as wheel:
                names = set(wheel.namelist())
                for file in source.rglob("*.py"):
                    member = "source_authority/" + file.relative_to(source).as_posix()
                    self.assertIn(member, names, f"Required Gateway module absent from wheel: {member}")
                    self.assertEqual(wheel.read(member), file.read_bytes())
                wheel.extractall(installed)

            checkout_trap = root / "foreign-checkout"
            checkout_trap.mkdir()
            env = {**os.environ, "PYTHONPATH": str(checkout_trap), "PYTHONDONTWRITEBYTECODE": "1"}
            script = r"""
import json, sys
from pathlib import Path
installed = Path(sys.argv[1]).resolve()
trap = Path(sys.argv[2]).resolve()
assert sys.flags.isolated == 1
assert all(Path(value).resolve() != trap for value in sys.path if value)
# A wheel installation changes the runtime's site-packages contents. Simulate
# that installation directly; PYTHONPATH cannot repair an embedded _pth file.
sys.path.insert(0, str(installed))
from source_authority import validator
origin = Path(validator.__file__).resolve()
assert origin.is_relative_to(installed), origin
assert validator.EXPECTED_SCHEMA == 'tiangong.source-ownership.v2'
errors = validator.validate_source_authority({}, repo_root=installed)
assert 'mappings: expected a non-empty list' in errors, errors
print(json.dumps({'origin': str(origin), 'isolated': sys.flags.isolated}))
"""
            imported = subprocess.run(
                [sys.executable, "-I", "-B", "-S", "-c", script, str(installed), str(checkout_trap)],
                cwd=root, env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            result = json.loads(imported.stdout)
            self.assertEqual(result["isolated"], 1)
            self.assertTrue(Path(result["origin"]).is_relative_to(installed))


if __name__ == "__main__":
    unittest.main()
