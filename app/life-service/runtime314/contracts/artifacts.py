"""Deterministic JSON Schema/OpenAPI artifact generation and verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from .canonical import canonical_sha256
from .models import SCHEMA_VERSION
from .schema import CONTRACT_MODELS, contract_schema_bundle, contract_schema_bundle_sha256


ARTIFACT_SET_ID = "tiangong.gateway.contract-artifacts.v1"
OPENAPI_VERSION = "3.1.1"
_DOCUMENT_NAMES = (
    "contract-artifacts.manifest.json",
    "openapi.json",
    "schema-bundle.json",
)


def _document_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def openapi_contract_catalog() -> dict[str, object]:
    """Build a component-only catalog without claiming not-yet-implemented routes."""

    _, definitions = models_json_schema(
        [(model, "validation") for model in CONTRACT_MODELS],
        ref_template="#/components/schemas/{model}",
    )
    schemas = definitions.get("$defs")
    if not isinstance(schemas, dict):
        raise RuntimeError("Pydantic did not generate OpenAPI component schemas")
    ordered_schemas = {name: schemas[name] for name in sorted(schemas)}
    roots = sorted(model.__name__ for model in CONTRACT_MODELS)
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Tiangong Gateway Contract Catalog",
            "version": "1.0.0",
            "summary": "Shared schemas only; concrete routes are owned by service OpenAPI documents.",
        },
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "paths": {},
        "components": {"schemas": ordered_schemas},
        "x-tiangong-contract-catalog": {
            "artifact_set_id": ARTIFACT_SET_ID,
            "schema_version": SCHEMA_VERSION,
            "schema_bundle_sha256": contract_schema_bundle_sha256(),
            "root_contract_count": len(roots),
            "root_contracts": roots,
            "route_contract_phase": "P3.1",
        },
    }


def generate_contract_artifact_documents() -> dict[str, bytes]:
    bundle = contract_schema_bundle()
    payloads = {
        "openapi.json": _document_bytes(openapi_contract_catalog()),
        "schema-bundle.json": _document_bytes(bundle),
    }
    entries = tuple(
        {
            "path": name,
            "media_type": "application/json",
            "size_bytes": len(payloads[name]),
            "sha256": hashlib.sha256(payloads[name]).hexdigest(),
        }
        for name in sorted(payloads)
    )
    root_names = tuple(sorted(bundle))
    manifest_body: dict[str, object] = {
        "artifact_set_id": ARTIFACT_SET_ID,
        "schema_version": SCHEMA_VERSION,
        "generator_id": "contracts.artifacts.v1",
        "root_contract_count": len(root_names),
        "root_contracts": root_names,
        "schema_bundle_sha256": contract_schema_bundle_sha256(),
        "artifacts": entries,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": canonical_sha256(manifest_body),
    }
    return {
        "contract-artifacts.manifest.json": _document_bytes(manifest),
        **payloads,
    }


def verify_contract_artifact_directory(output_dir: str | os.PathLike[str]) -> Mapping[str, Any]:
    root = Path(output_dir)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("contract artifact root must be a real directory")
    names = tuple(sorted(path.name for path in root.iterdir() if path.is_file()))
    if names != _DOCUMENT_NAMES or any(path.is_dir() for path in root.iterdir()):
        raise ValueError("contract artifact directory contains missing or unexpected entries")
    expected = generate_contract_artifact_documents()
    for name in _DOCUMENT_NAMES:
        path = root / name
        if path.is_symlink() or path.read_bytes() != expected[name]:
            raise ValueError(f"contract artifact does not match source: {name}")
    manifest = json.loads((root / "contract-artifacts.manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("contract artifact manifest is not an object")
    claimed = manifest.pop("manifest_sha256", None)
    if claimed != canonical_sha256(manifest):
        raise ValueError("contract artifact manifest digest is invalid")
    return {**manifest, "manifest_sha256": claimed}


def write_contract_artifacts(output_dir: str | os.PathLike[str]) -> Mapping[str, Any]:
    """Atomically create the three-file artifact set; never merge into a dirty target."""

    target = Path(output_dir).absolute()
    if target.exists():
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise FileExistsError("contract artifact target must be absent or empty")
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        for name, payload in generate_contract_artifact_documents().items():
            (stage / name).write_bytes(payload)
        os.replace(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return verify_contract_artifact_directory(target)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Empty or absent artifact directory")
    arguments = parser.parse_args(argv)
    manifest = write_contract_artifacts(arguments.output)
    print(
        json.dumps(
            {
                "artifact_set_id": manifest["artifact_set_id"],
                "manifest_sha256": manifest["manifest_sha256"],
                "schema_bundle_sha256": manifest["schema_bundle_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARTIFACT_SET_ID",
    "OPENAPI_VERSION",
    "generate_contract_artifact_documents",
    "openapi_contract_catalog",
    "verify_contract_artifact_directory",
    "write_contract_artifacts",
]
