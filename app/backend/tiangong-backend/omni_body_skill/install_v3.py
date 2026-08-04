from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_INSTALL_ROOT = Path.home() / ".tiangong" / "v3" / "omni_body_skill"
DEFAULT_NENGLI_FILE = Path.home() / ".tiangong" / "v3" / "nengli_zhuche.json"
DEFAULT_TOOLS_DIR = Path("/api/v1/v3/tools")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(path.suffix + ".broken.bak")
        shutil.copy2(path, backup)
        return default


def _merge_abilities(existing: Any, incoming: list[dict[str, Any]]) -> Any:
    if isinstance(existing, list):
        items = existing
        container = None
    elif isinstance(existing, dict) and isinstance(existing.get("abilities"), list):
        items = existing["abilities"]
        container = existing
    elif isinstance(existing, dict) and isinstance(existing.get("items"), list):
        items = existing["items"]
        container = existing
    elif isinstance(existing, dict) and existing.get("id"):
        items = [existing]
        container = None
    else:
        items = []
        container = None

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id") or "").strip()
        if not aid:
            continue
        if aid not in by_id:
            order.append(aid)
        by_id[aid] = item
    for item in incoming:
        aid = str(item.get("id") or "").strip()
        if not aid:
            continue
        if aid not in by_id:
            order.append(aid)
        by_id[aid] = item
    merged = [by_id[i] for i in order]
    if container is not None:
        if "abilities" in container:
            container["abilities"] = merged
        else:
            container["items"] = merged
        return container
    return merged


def install(args: argparse.Namespace) -> dict[str, Any]:
    install_root = Path(args.install_root).expanduser().resolve()
    tools_dir = Path(args.tools_dir).expanduser()
    nengli_file = Path(args.nengli_file).expanduser()

    incoming = json.loads((PACKAGE_ROOT / "v3" / "registry" / "nengli_zhuche.append.json").read_text(encoding="utf-8"))
    if not isinstance(incoming, list):
        raise RuntimeError("v3/registry/nengli_zhuche.append.json must be a list")

    actions = []
    actions.append({"step": "copy_package", "from": str(PACKAGE_ROOT), "to": str(install_root)})
    actions.append({"step": "copy_tool", "from": str(PACKAGE_ROOT / "api/v1/v3/tools/omni_body.py"), "to": str(tools_dir / "omni_body.py")})
    actions.append({"step": "merge_nengli", "file": str(nengli_file), "incoming_count": len(incoming)})

    if args.dry_run:
        return {"ok": True, "dry_run": True, "actions": actions}

    if install_root.exists():
        backup = install_root.with_name(install_root.name + ".bak")
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(install_root, backup)
        shutil.rmtree(install_root)
    shutil.copytree(PACKAGE_ROOT, install_root, ignore=shutil.ignore_patterns(".omni_audit", ".omni_backups", ".omni_trash", "__pycache__", "*.pyc"))

    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGE_ROOT / "api/v1/v3/tools/omni_body.py", tools_dir / "omni_body.py")
    shutil.copy2(PACKAGE_ROOT / "api/v1/v3/tools/omni_body.tool.json", tools_dir / "omni_body.tool.json")

    nengli_file.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_json(nengli_file, [])
    merged = _merge_abilities(existing, incoming)
    if nengli_file.exists():
        shutil.copy2(nengli_file, nengli_file.with_suffix(nengli_file.suffix + ".bak"))
    nengli_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "dry_run": False,
        "install_root": str(install_root),
        "tools_dir": str(tools_dir),
        "nengli_file": str(nengli_file),
        "abilities_merged": len(incoming),
        "note": "If v3 runs tools in a different process, set TIANGONG_OMNI_BODY_ROOT to the install_root above. The wrapper also probes ~/.tiangong/v3/omni_body_skill automatically.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Omni Body as a Tiangong v3 native tool + skill registry entries.")
    parser.add_argument("--install-root", default=str(DEFAULT_INSTALL_ROOT), help="Where to copy the package root; default ~/.tiangong/v3/omni_body_skill")
    parser.add_argument("--tools-dir", default=str(DEFAULT_TOOLS_DIR), help="Tiangong v3 tools dir; default /api/v1/v3/tools")
    parser.add_argument("--nengli-file", default=str(DEFAULT_NENGLI_FILE), help="Ability registry JSON; default ~/.tiangong/v3/nengli_zhuche.json")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned operations; do not write files")
    ns = parser.parse_args()
    print(json.dumps(install(ns), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
