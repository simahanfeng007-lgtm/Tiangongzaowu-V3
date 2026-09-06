"""Publish or independently verify one explicitly reviewed Source version.

The review trust key is a raw 32-byte operator-configured Ed25519 public key,
not a key supplied by candidate content. The detached signature is 64 bytes.
No signing/private-key acquisition, active-version pointer, Runtime import,
Registry replacement or production deployment is performed by this command.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from total_gateway.tool_source_bundle import _read_verified_bundle  # noqa: E402
from total_gateway.tool_source_publication import (  # noqa: E402
    publish_tool_source_revision, verify_published_tool_source_revision,
)


def _read(path: Path | None, limit: int) -> bytes:
    if path is None or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError("operator review/evidence input is missing or oversized")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("operator review/evidence input changed size")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--sha256", required=True, help="external bundle digest")
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--action", action="append", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--trusted-review-key", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--evidence-contract", type=Path)
    parser.add_argument("--running-manifest-lock", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--publication-sha256")
    args = parser.parse_args(argv)
    try:
        shared = dict(base_commit=args.base, candidate_commit=args.candidate,
                      requested_action_ids=tuple(sorted(args.action)),
                      publication_root=args.destination.absolute(),
                      trusted_public_key=_read(args.trusted_review_key, 32))
        if args.verify_only:
            if not args.publication_sha256:
                raise ValueError("verification requires an external publication digest")
            result = verify_published_tool_source_revision(
                args.repository.absolute(), expected_bundle_sha256=args.sha256,
                expected_publication_sha256=args.publication_sha256, **shared,
            )
        else:
            if args.bundle is None:
                raise ValueError("publication requires a reviewed bundle")
            raw, _ = _read_verified_bundle(args.bundle.absolute(), expected_sha256=args.sha256)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                evidence = {"build": archive.read("build-report.json")}
            evidence.update(
                evidence_contract=_read(args.evidence_contract, 16 * 1024 * 1024),
                running_manifest_lock=_read(args.running_manifest_lock, 16 * 1024 * 1024),
            )
            result = publish_tool_source_revision(
                args.repository.absolute(), bundle_path=args.bundle.absolute(), expected_sha256=args.sha256,
                review_bytes=_read(args.review, 1024 * 1024), signature=_read(args.signature, 64),
                evidence=evidence, **shared,
            )
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(json.dumps({"status": "SOURCE_PUBLICATION_REJECTED", "error": str(exc),
                          "may_authorize": False, "may_execute": False}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
