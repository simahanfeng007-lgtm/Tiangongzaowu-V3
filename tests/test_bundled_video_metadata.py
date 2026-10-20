"""Video delivery observations work with the bundled FFmpeg alone."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Dict
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/omni_body_skill/tools/delivery_kernel.py"


def helpers():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in {"_ffprobe", "_qc_video"}]
    namespace = dict(Any=Any, Dict=Dict, Path=Path, json=json, shutil=shutil,
                     subprocess=subprocess, _resolve=lambda runtime, target, **kw: Path(target),
                     _rel=lambda runtime, path: path.name)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def test_bundled_metadata_closes_generator_and_preserves_observation_scope(tmp_path):
    frames = mock.MagicMock()
    frames.__next__.return_value = {"size": (64, 48), "duration": 1.25}
    adapter = SimpleNamespace(read_frames=mock.Mock(return_value=frames))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"observed-by-controlled-adapter")
    with mock.patch.object(shutil, "which", return_value=None), mock.patch.dict(
        sys.modules, {"imageio_ffmpeg": adapter}
    ):
        result = helpers()["_qc_video"](SimpleNamespace(ffprobe=None), str(video), {"max_duration": 2})
    adapter.read_frames.assert_called_once_with(str(video))
    frames.close.assert_called_once_with()
    assert result["success"]
    assert result["result"]["video_info"] == {"duration": 1.25, "width": 64, "height": 48}
    assert result["result"]["assessment_mode"] == "metadata_observation_only"
    assert result["result"]["playback"] == "not_verified"
    assert result["result"]["completion_authority"] == "adversarial_judge"


@pytest.mark.parametrize("metadata", [
    {"size": (0, 48), "duration": 1},
    {"size": (64, True), "duration": 1},
    {"size": (64, 48), "duration": float("nan")},
    {"size": (64, 48), "duration": -1},
    {"size": (64, 48)},
])
def test_invalid_metadata_remains_unavailable_and_closes_generator(metadata):
    frames = mock.MagicMock()
    frames.__next__.return_value = metadata
    adapter = SimpleNamespace(read_frames=mock.Mock(return_value=frames))
    with mock.patch.object(shutil, "which", return_value=None), mock.patch.dict(
        sys.modules, {"imageio_ffmpeg": adapter}
    ):
        assert helpers()["_ffprobe"](SimpleNamespace(ffprobe=None), Path("bad.mp4")) == {}
    frames.close.assert_called_once_with()


def test_probe_failure_remains_unavailable_and_closes_generator():
    frames = mock.MagicMock()
    frames.__next__.side_effect = RuntimeError("decoder failure")
    adapter = SimpleNamespace(read_frames=mock.Mock(return_value=frames))
    with mock.patch.object(shutil, "which", return_value=None), mock.patch.dict(
        sys.modules, {"imageio_ffmpeg": adapter}
    ):
        assert helpers()["_ffprobe"](SimpleNamespace(ffprobe=None), Path("bad.mp4")) == {}
    frames.close.assert_called_once_with()


def test_actual_bundled_ffmpeg_generates_and_observes_video_without_ffprobe(tmp_path):
    import imageio_ffmpeg

    video = tmp_path / "offline.mp4"
    generated = subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=red:s=64x48:r=10:d=0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    with mock.patch.object(shutil, "which", return_value=None):
        metadata = helpers()["_ffprobe"](SimpleNamespace(ffprobe=None), video)
    assert metadata["width"] == 64 and metadata["height"] == 48
    assert 0.4 <= metadata["duration"] <= 0.6
