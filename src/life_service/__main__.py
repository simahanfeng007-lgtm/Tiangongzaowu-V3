"""Fail-closed CLI for shadow QA and explicit P11 production activation."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .legacy_adapter import LegacySnapshotReader
from .runtime import build_source_ownership_report
from .shadow_api import DEFAULT_SHADOW_PORT, build_shadow_http_server
from .cutover import PRODUCTION_PORT, load_and_verify_handoff, load_cow_manifest
from .production_api import build_production_http_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m life_service")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--status-json",
        action="store_true",
        help="print the source-ownership report",
    )
    actions.add_argument(
        "--inspect-snapshot",
        type=Path,
        default=None,
        metavar="PATH",
        help="verify one immutable legacy snapshot and print its comparison anchor",
    )
    actions.add_argument(
        "--serve-shadow",
        type=Path,
        default=None,
        metavar="PATH",
        help="serve the read-only compatibility API from one immutable snapshot",
    )
    actions.add_argument(
        "--serve-production",
        type=Path,
        default=None,
        metavar="SNAPSHOT",
        help="serve the source-owned API after verifying explicit P11 handoff artifacts",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="optional workspace root used only for baseline discovery",
    )
    parser.add_argument(
        "--shadow-port",
        type=int,
        default=DEFAULT_SHADOW_PORT,
        help="separate loopback shadow port; 7175 is always rejected",
    )
    parser.add_argument(
        "--shadow-token-env",
        default="TIANGONG_LIFE_SHADOW_TOKEN",
        help="environment variable containing the shadow bearer token",
    )
    parser.add_argument("--final-manifest", type=Path, default=None)
    parser.add_argument("--overlay", type=Path, default=None)
    parser.add_argument("--handoff", type=Path, default=None)
    parser.add_argument("--handoff-public-key", type=Path, default=None)
    parser.add_argument("--production-port", type=int, default=PRODUCTION_PORT)
    parser.add_argument(
        "--production-token-env",
        default="TIANGONG_DESKTOP_TOKEN",
        help="environment variable containing the production desktop token",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.status_json:
        report = build_source_ownership_report(arguments.workspace_root)
        print(
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if arguments.serve_production is not None:
        artifacts = (
            arguments.final_manifest,
            arguments.overlay,
            arguments.handoff,
            arguments.handoff_public_key,
        )
        if any(value is None for value in artifacts):
            raise SystemExit(
                "--serve-production requires --final-manifest, --overlay, --handoff, and --handoff-public-key"
            )
        now_ms = int(time.time() * 1000)
        manifest = load_cow_manifest(arguments.final_manifest)
        permit = load_and_verify_handoff(
            arguments.handoff,
            arguments.handoff_public_key,
            now_ms=now_ms,
        )
        public_key = arguments.handoff_public_key.resolve(strict=True).read_bytes()
        token = os.environ.get(str(arguments.production_token_env), "")
        server, config = build_production_http_server(
            LegacySnapshotReader(arguments.serve_production),
            arguments.overlay,
            manifest,
            permit,
            trusted_public_key=public_key,
            token=token,
            port=arguments.production_port,
            now_ms=now_ms,
        )
        print(
            json.dumps(
                {
                    "schema": "tiangong.life.source-listener.v1",
                    "host": config.host,
                    "port": config.port,
                    "writer_epoch": config.writer_epoch,
                    "production_writer_enabled": True,
                    "scheduler_enabled": False,
                    "side_effects_enabled": False,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
        return 0
    snapshot = arguments.inspect_snapshot or arguments.serve_shadow
    reader = LegacySnapshotReader(snapshot)
    if arguments.inspect_snapshot is not None:
        anchor = reader.anchor()
        print(
            json.dumps(
                {
                    "schema": "tiangong.life.shadow-inspection.v1",
                    "mode": "read_only_snapshot",
                    "anchor": anchor.to_dict(),
                    "anchor_sha256": anchor.sha256,
                    "production_writer_enabled": False,
                    "writer_lease_acquisition_enabled": False,
                    "scheduler_enabled": False,
                    "side_effects_enabled": False,
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    token = os.environ.get(str(arguments.shadow_token_env), "")
    server, config = build_shadow_http_server(
        reader,
        token=token,
        port=arguments.shadow_port,
    )
    print(
        json.dumps(
            {
                "schema": "tiangong.life.shadow-listener.v1",
                "host": config.host,
                "port": config.port,
                "token_sha256": config.token_sha256,
                "production_writer_enabled": config.production_writer_enabled,
                "writer_lease_acquisition_enabled": config.writer_lease_acquisition_enabled,
                "scheduler_enabled": config.scheduler_enabled,
                "side_effects_enabled": config.side_effects_enabled,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
