from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiangong-total-gateway")
    parser.add_argument("--soul-backup-restore", metavar="PATH")
    parser.add_argument("--soul-backup-verify", metavar="PATH")
    parser.add_argument(
        "--passphrase-env",
        default="TIANGONG_SOUL_BACKUP_PASSPHRASE",
        help="environment variable holding the backup passphrase",
    )
    return parser


def _backup_command(args: argparse.Namespace) -> int:
    from .soul_backup import SoulBackupManager

    backup_path = args.soul_backup_restore or args.soul_backup_verify
    if not backup_path:
        return -1
    passphrase = str(os.environ.get(args.passphrase_env) or "")
    if not passphrase:
        raise SystemExit("soul_backup_passphrase_missing")
    state_root = Path(
        os.environ.get("TIANGONG_GATEWAY_STATE_ROOT")
        or Path.home() / ".tiangong" / "gateway"
    ).expanduser().resolve(strict=False)
    manager = SoulBackupManager(
        state_root=state_root,
        sources=SoulBackupManager.default_sources(state_root),
    )
    if args.soul_backup_restore:
        result = manager.restore(Path(args.soul_backup_restore), passphrase=passphrase)
    else:
        result = manager.verify(Path(args.soul_backup_verify), passphrase=passphrase)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> int:
    args = _parser().parse_args()
    result = _backup_command(args)
    if result >= 0:
        return result
    if os.environ.get("TIANGONG_MOBILE_LINK", "0") == "1":
        from .mobile_launcher import run_gateway_with_mobile

        run_gateway_with_mobile()
    else:
        from .server import run_gateway

        run_gateway()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
