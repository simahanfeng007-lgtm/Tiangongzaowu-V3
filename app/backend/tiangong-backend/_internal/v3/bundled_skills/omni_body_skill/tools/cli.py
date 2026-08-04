from __future__ import annotations

import argparse
import json
from .omni_body_v3 import run_omni_body


def main() -> None:
    parser = argparse.ArgumentParser(description="Tiangong Omni Body Tool CLI")
    parser.add_argument("action", help="Action name, e.g. file.read, docx.create")
    parser.add_argument("--target", default=None, help="Primary target path")
    parser.add_argument("--args", default="{}", help="JSON args object")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--grant", default="{}", help="Gateway-signed Omni capability grant JSON")
    parser.add_argument("--runtime", default="{}", help="Gateway runtime-binding JSON")
    ns = parser.parse_args()
    args = json.loads(ns.args)
    grant = json.loads(ns.grant)
    runtime = json.loads(ns.runtime)
    print(json.dumps(run_omni_body({
        "action": ns.action,
        "target": ns.target or "",
        "args": args,
        "workspace": ns.workspace,
        "__capability_grant": grant,
        "__runtime": runtime,
    }), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
