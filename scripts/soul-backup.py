#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from total_gateway.soul_backup import SoulBackupManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Create, verify or restore a Tiangong Soul Backup")
    parser.add_argument("command", choices=("create", "verify", "restore"))
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--file")
    parser.add_argument("--passphrase", default=os.environ.get("TIANGONG_SOUL_BACKUP_PASSPHRASE", ""))
    args = parser.parse_args()
    manager = SoulBackupManager(Path(args.state_root), SoulBackupManager.default_sources(Path(args.state_root)))
    if args.command == "create":
        result = manager.create(Path(args.file) if args.file else None, passphrase=args.passphrase)
    elif args.command == "verify":
        result = manager.verify(Path(args.file), passphrase=args.passphrase)
    else:
        result = manager.restore(Path(args.file), passphrase=args.passphrase)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
