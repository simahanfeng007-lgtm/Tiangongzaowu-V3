"""Operator CLI for the fail-closed P11 life-service cutover."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from life_service.cutover import (
    LifeCutoverAuthority,
    activate_handoff,
    capture_final_delta,
    capture_stopped_legacy_snapshot,
    create_drain_evidence,
    install_cutover_state_bundle,
    load_handoff_permit,
    load_cow_manifest,
    prepare_cow_import,
    recover_cutover_state_bundle,
    renew_handoff_permit,
    rollback_cutover_state_bundle,
    verify_cutover_state_bundle,
    write_handoff_artifacts,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: object) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _private(path: Path) -> LifeCutoverAuthority:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError("cutover private key path is unsafe")
    return LifeCutoverAuthority.from_private_bytes(resolved.read_bytes())


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="life-cutover-p11")
    actions = root.add_subparsers(dest="action", required=True)
    capture = actions.add_parser("capture-snapshot")
    capture.add_argument("--source", type=Path, required=True)
    capture.add_argument("--snapshot", type=Path, required=True)
    capture.add_argument("--writer-stopped", action="store_true")
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--snapshot", type=Path, required=True)
    prepare.add_argument("--stage", type=Path, required=True)
    final = actions.add_parser("final-delta")
    final.add_argument("--snapshot", type=Path, required=True)
    final.add_argument("--stage", type=Path, required=True)
    keygen = actions.add_parser("keygen")
    keygen.add_argument("--private-key", type=Path, required=True)
    handoff = actions.add_parser("handoff")
    handoff.add_argument("--stage", type=Path, required=True)
    handoff.add_argument("--private-key", type=Path, required=True)
    handoff.add_argument("--scheduler-pending", type=int, required=True)
    handoff.add_argument("--inflight", type=int, required=True)
    handoff.add_argument("--old-writer-stopped", action="store_true")
    handoff.add_argument("--ttl-ms", type=int, default=31_536_000_000)
    renew = actions.add_parser("renew-handoff")
    renew.add_argument("--stage", type=Path, required=True)
    renew.add_argument("--private-key", type=Path, required=True)
    renew.add_argument("--ttl-ms", type=int, default=31_536_000_000)
    install = actions.add_parser("install")
    install.add_argument("--stage", type=Path, required=True)
    install.add_argument("--root", type=Path, required=True)
    install.add_argument("--release-id", required=True)
    install.add_argument(
        "--mode", choices=("fresh", "overwrite", "upgrade", "recovery"), required=True
    )
    install.add_argument("--writer-stopped", action="store_true")
    verify = actions.add_parser("verify-install")
    verify.add_argument("--root", type=Path, required=True)
    rollback = actions.add_parser("rollback-install")
    rollback.add_argument("--root", type=Path, required=True)
    rollback.add_argument("--rollback-permit", type=Path, required=True)
    rollback.add_argument("--writer-stopped", action="store_true")
    recover = actions.add_parser("recover-install")
    recover.add_argument("--root", type=Path, required=True)
    recover.add_argument("--release-id", required=True)
    recover.add_argument("--previous-release-id")
    recover.add_argument("--expected-overlay-sha256", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    now = _now_ms()
    if args.action == "capture-snapshot":
        _json(
            capture_stopped_legacy_snapshot(
                args.source,
                args.snapshot,
                writer_stopped=args.writer_stopped,
                now_ms=now,
            )
        )
    elif args.action == "prepare":
        _json(prepare_cow_import(args.snapshot, args.stage, now_ms=now).to_dict())
    elif args.action == "final-delta":
        _json(capture_final_delta(args.snapshot, args.stage, now_ms=now).to_dict())
    elif args.action == "keygen":
        authority = LifeCutoverAuthority.generate()
        path = args.private_key.expanduser()
        path.parent.resolve(strict=True)
        with path.open("xb") as handle:
            handle.write(authority.private_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _json({"ok": True, "private_key": str(path), "public_key_sha256": __import__("hashlib").sha256(authority.public_bytes()).hexdigest()})
    elif args.action == "handoff":
        final = load_cow_manifest(args.stage / "cow_final.json")
        drain = create_drain_evidence(
            scheduler_pending=args.scheduler_pending,
            inflight_requests=args.inflight,
            old_writer_stopped=args.old_writer_stopped,
            final_manifest_sha256=final.manifest_sha256,
            observed_at_ms=now,
        )
        authority = _private(args.private_key)
        permit = activate_handoff(
            final,
            drain,
            authority,
            issued_at_ms=now,
            expires_at_ms=now + args.ttl_ms,
        )
        write_handoff_artifacts(args.stage, permit, authority)
        _json(permit.to_dict())
    elif args.action == "renew-handoff":
        authority = _private(args.private_key)
        active = load_handoff_permit(args.stage / "writer_handoff.json")
        permit = renew_handoff_permit(
            active,
            authority,
            issued_at_ms=now,
            expires_at_ms=now + args.ttl_ms,
        )
        write_handoff_artifacts(args.stage, permit, authority)
        _json(permit.to_dict())
    elif args.action == "install":
        _json(
            install_cutover_state_bundle(
                args.stage,
                args.root,
                release_id=args.release_id,
                mode=args.mode,
                writer_stopped=args.writer_stopped,
            )
        )
    elif args.action == "verify-install":
        _json(verify_cutover_state_bundle(args.root))
    elif args.action == "rollback-install":
        _json(
            rollback_cutover_state_bundle(
                args.root,
                writer_stopped=args.writer_stopped,
                rollback_permit_path=args.rollback_permit,
            )
        )
    elif args.action == "recover-install":
        _json(
            recover_cutover_state_bundle(
                args.root,
                release_id=args.release_id,
                previous_release_id=args.previous_release_id,
                expected_overlay_sha256=args.expected_overlay_sha256,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
