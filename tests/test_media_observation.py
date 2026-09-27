import base64
from io import BytesIO
import hashlib

from PIL import Image
import pytest

from v3.jineng.http_kehuduan import _inject_native_images
from omni_body_skill.tools.omni_body_tool import BodyRuntime,BodyRuntimeConfig
from omni_body_skill.tools import media_observation


def test_native_images_contain_actual_pixels_and_exact_source_identity(tmp_path):
    path=tmp_path/"image.png";Image.new("RGB",(3000,1000),(230,0,0)).save(path)
    payload={"messages":[{"role":"user","content":"Observe the image"}]}
    receipts=_inject_native_images(payload,[str(path)],"openai_chat_completions")
    assert receipts[0]["sha256"]==hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipts[0]["source_size"]==[3000,1000] and receipts[0]["submitted_size"][0]==2048
    parts=payload["messages"][0]["content"]
    raw=base64.b64decode(parts[-1]["image_url"]["url"].split(",",1)[1])
    with Image.open(BytesIO(raw)) as submitted:
        r,g,b=submitted.getpixel((0,0));assert r>200 and g<20 and b<20
    assert hashlib.sha256(raw).hexdigest()==receipts[0]["submitted_sha256"]


def test_unsupported_modality_protocol_cannot_drop_pixels_and_answer_as_text(tmp_path):
    with pytest.raises(ValueError,match="native_image_protocol_unavailable"):
        _inject_native_images({"messages":[]},["not-even-opened.png"],"unsupported")


def test_bad_image_has_no_model_observation_receipt(tmp_path):
    path=tmp_path/"bad.png";path.write_bytes(b"not an image")
    payload={"messages":[{"role":"user","content":"question"}]}
    with pytest.raises(Exception):_inject_native_images(payload,[str(path)],"openai_chat_completions")
    assert payload["messages"][0]["content"]=="question"


def test_changed_media_never_reuses_old_interpretation(tmp_path,monkeypatch):
    path=tmp_path/"image.png";Image.new("RGB",(10,10),"red").save(path)
    runtime=BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path),run_id="media"))
    def changed(paths,question,kind):
        Image.new("RGB",(10,10),"blue").save(path)
        return {"success":True,"interpretation":"red"}
    monkeypatch.setattr(media_observation,"_infer",changed)
    result=runtime.run("image.observe","image.png",{"question":"What color?"})
    assert not result["success"] and result["error"]=="media.source_changed_during_observation"
    assert not result["interpretation_valid_for_current_version"]


def test_metadata_only_is_not_a_successful_content_observation(tmp_path,monkeypatch):
    path=tmp_path/"image.png";Image.new("RGB",(10,10),"red").save(path)
    runtime=BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path),run_id="media"))
    monkeypatch.setattr(media_observation,"_infer",lambda *a:{"success":False,"error":"media.observation_unavailable","observed_modalities":[]})
    result=runtime.run("image.observe","image.png",{"question":"What color?"})
    assert not result["success"] and not result["result"]["observed_modalities"]


def test_decoded_video_frames_are_retained_and_audio_coverage_is_not_inferred(tmp_path,monkeypatch):
    from pathlib import Path
    import subprocess
    runtime=BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path),run_id="video"))
    if not runtime.ffmpeg: pytest.skip("FFmpeg is not installed on this test platform")
    subprocess.run([runtime.ffmpeg,"-nostdin","-v","error","-f","lavfi","-i","color=red:s=128x64:d=1",
        "-c:v","mpeg4",str(tmp_path/"clip.mp4")],check=True,timeout=20)
    seen=[]
    def inspect(paths,question,kind):
        for name in paths:
            p=Path(name)
            with Image.open(p) as frame:
                r,g,b=frame.getpixel((30,30));assert r>200 and g<30 and b<30
            seen.append(p)
        return {"success":True,"interpretation":"red frames","observed_modalities":["image"]}
    monkeypatch.setattr(media_observation,"_infer",inspect)
    result=runtime.run("video.observe_frames","clip.mp4",{"question":"What color?","times":[0,0.5]})
    assert result["success"],result
    assert all(p.is_file() for p in seen)
    assert len(result["retained_media_evidence"])==3
    assert not result["result"]["audio_observed"] and not result["result"]["unsampled_intervals_observed"]
