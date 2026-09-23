"""Explicit local source-installation trial; never a formal planner cutover.

Only the installer/operator calls the writer. Model JSON and HTTP invocation
fields do not reach this API. Per-request registry, policy, signatures and
native workspace enforcement remain mandatory after this capability ceiling.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from contracts import canonical_sha256
from contracts.composition_profile import (
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    composition_profile_valid,
)

PROFILE_FILE = "source-execution-profile.json"


class SourceTrialProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema: Literal["tiangong.source-trial-execution-profile.v1"] = "tiangong.source-trial-execution-profile.v1"
    activation_kind: Literal["source-installation-trial"] = "source-installation-trial"
    route: Literal["explicit_dictionary"] = "explicit_dictionary"
    workspace_root: str
    execution_profile_id: str
    execution_profile_sha256: str
    profile_config_sha256: str

    @model_validator(mode="after")
    def validate_profile(self):
        if not composition_profile_valid(self.execution_profile_id, self.execution_profile_sha256):
            raise ValueError("unknown source trial execution profile")
        if self.profile_config_sha256 != canonical_sha256(self.model_dump(mode="json", exclude={"profile_config_sha256"})):
            raise ValueError("source trial config digest mismatch")
        return self


def is_dictionary_request(text: str) -> bool:
    return str(text).lstrip().startswith(("字典任务：", "字典任务:", "/dictionary "))


def install_source_trial_profile(*, state_root: Path, workspace_root: Path,
                                 profile_id: str = WORKSPACE_PYTHON_PROFILE_ID) -> Path:
    profiles = {WORKSPACE_WRITE_PROFILE_ID: WORKSPACE_WRITE_PROFILE_SHA256,
                WORKSPACE_PYTHON_PROFILE_ID: WORKSPACE_PYTHON_PROFILE_SHA256}
    if profile_id not in profiles:
        raise ValueError("installer must select a supported fixed profile")
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source trial workspace must be a directory")
    payload = {"schema": "tiangong.source-trial-execution-profile.v1",
               "activation_kind": "source-installation-trial", "route": "explicit_dictionary",
               "workspace_root": str(root), "execution_profile_id": profile_id,
               "execution_profile_sha256": profiles[profile_id]}
    payload["profile_config_sha256"] = canonical_sha256(payload)
    profile = SourceTrialProfile.model_validate(payload)
    destination = Path(state_root) / PROFILE_FILE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def load_source_trial_profile(*, state_root: Path, workspace_root: Path) -> SourceTrialProfile | None:
    path = Path(state_root) / PROFILE_FILE
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError("source trial config file is invalid")
    profile = SourceTrialProfile.model_validate_json(path.read_text(encoding="utf-8"))
    if profile.workspace_root != str(Path(workspace_root).resolve(strict=True)):
        raise ValueError("source trial workspace binding mismatch")
    return profile
