"""Actual OS isolation and artifact regressions; no mocked execution success."""
import json
import os
from pathlib import Path
import socket
import sys
import zipfile

import pytest

from omni_body_skill.tools import sandbox_runtime as sandbox
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from v3.tool_result_contract import _observed_write_evidence


@pytest.fixture
def runner(tmp_path):
    if not sys.platform.startswith('linux') or not Path('/usr/bin/bwrap').exists():
        pytest.skip('requires Linux bubblewrap')
    workspace = tmp_path / '工作 区'; workspace.mkdir()
    return sandbox.SandboxRunner(workspace, tmp_path / 'private', tmp_path / 'trash')


def test_real_linux_isolation_denies_host_network_and_secrets(runner, tmp_path, monkeypatch):
    outside = tmp_path / 'host-secret'; outside.write_text('secret')
    monkeypatch.setenv('TIANGONG_EXECUTION_TEST_SECRET', 'must-not-inherit')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen()
        code = f'''import os,socket
from pathlib import Path
assert os.getenv('TIANGONG_EXECUTION_TEST_SECRET') is None
assert not Path({str(outside)!r}).exists()
assert not Path('/proc/{os.getpid()}').exists()
try:
    socket.create_connection(('127.0.0.1', {listener.getsockname()[1]}), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('host network reachable')
Path('result.txt').write_text('isolated')
'''
        result = runner.run(['/usr/bin/python3', '-c', code], require_os_containment=True)
    assert result['ok'], result
    assert result['containment'] == 'linux-bubblewrap' and result['network'] == 'denied'
    assert result['changed_files'] == ['result.txt']
    assert (runner.workspace / 'result.txt').read_text() == 'isolated'
    assert outside.read_text() == 'secret'
    assert not Path(result['sandbox_root']).exists()


def test_real_linux_shell_absolute_workspace_paths_and_nested_cwd(runner):
    sub = runner.workspace / 'nested'; sub.mkdir()
    result = runner.run(['/bin/sh', '-c', f'printf shell > "{sub}/out.txt"; pwd'],
                        cwd=sub, require_os_containment=True)
    assert result['ok'], result
    assert result['stdout'].strip() == '/workspace/nested'
    assert (sub / 'out.txt').read_text() == 'shell'


def test_real_linux_failure_never_commits_partial_outputs(runner):
    (runner.workspace / 'original').write_text('before')
    result = runner.run(['/usr/bin/python3', '-c', 'from pathlib import Path; Path("original").write_text("bad"); raise SystemExit(7)'], require_os_containment=True)
    assert result['returncode'] == 7 and result['commit_state'] == 'discarded'
    assert (runner.workspace / 'original').read_text() == 'before'


def test_real_linux_timeout_and_cancellation_do_not_commit(runner):
    with pytest.raises(sandbox.SandboxError, match='sandbox_timeout'):
        runner.run(['/usr/bin/python3', '-c', 'import time; from pathlib import Path; Path("partial").write_text("bad"); time.sleep(20)'],
                   require_os_containment=True, timeout_seconds=1)
    assert not (runner.workspace / 'partial').exists()
    with pytest.raises(sandbox.SandboxError, match='sandbox_cancelled'):
        runner.run(['/bin/true'], require_os_containment=True, cancel_check=lambda: True)


def test_real_linux_process_limit_and_output_limit(runner):
    runner.limits = sandbox.SandboxLimits(max_processes=12, max_output_bytes=10000)
    code = '''import subprocess,sys
children=[]
try:
    for i in range(30):
        children.append(subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']))
except OSError:
    print('LIMIT',len(children),flush=True)
finally:
    for p in children: p.terminate()
    for p in children: p.wait()
assert len(children)<12
'''
    result = runner.run(['/usr/bin/python3', '-c', code], require_os_containment=True)
    assert result['ok'] and 'LIMIT' in result['stdout'], result
    with pytest.raises(sandbox.SandboxError, match='output_limit'):
        runner.run(['/usr/bin/python3', '-c', 'print("x"*20000)'], require_os_containment=True)


def test_linux_backend_failure_cannot_launch_uncontained(runner, monkeypatch):
    from omni_body_skill.tools import linux_sandbox
    monkeypatch.setattr(linux_sandbox, 'bubblewrap_executable', lambda: '/bin/false')
    with pytest.raises(sandbox.SandboxError, match='sandbox_linux_start_failed'):
        runner.run(['/usr/bin/python3', '-c', 'from pathlib import Path;Path("escaped").touch()'], require_os_containment=True)
    assert not (runner.workspace / 'escaped').exists()


@pytest.mark.parametrize('size,args,expected', [
    ((160,160), {'width':256,'height':256}, (256,256)),
    ((320,160), {'width':256,'height':256}, (256,128)),
    ((80,40), {'width':256}, (256,128)),
    ((80,40), {'height':100}, (200,100)),
    ((80,40), {'width':256,'height':256,'keep_ratio':False}, (256,256)),
])
def test_resize_enlarges_and_preserves_ratio_when_requested(tmp_path, size, args, expected):
    from PIL import Image
    Image.new('RGB', size, 'red').save(tmp_path/'source.png')
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path)))
    runtime._action_image_resize('resize', 'source.png', {**args,'output':'out.png'})
    with Image.open(tmp_path/'out.png') as im:
        assert im.size == expected
    with Image.open(tmp_path/'source.png') as im:
        assert im.size == size


def test_extract_returns_actual_write_and_unchanged_evidence(tmp_path):
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path)))
    with zipfile.ZipFile(tmp_path/'source.zip', 'w') as z:
        z.writestr('nested/data.txt', 'actual bytes')
    for expected_changed in (True, False):
        result = runtime._action_zip_extract('extract', 'source.zip', {'destination':'output'})
        contract = _observed_write_evidence('omni_body', {'action':'zip.extract','result':result}, True)
        assert contract and contract['authoritative']
        key = 'changed_files' if expected_changed else 'verified_unchanged_files'
        assert str(tmp_path/'output/nested/data.txt') in contract[key]
        assert (tmp_path/'output/nested/data.txt').read_text() == 'actual bytes'


def test_extract_rejects_escape_without_writing(tmp_path):
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path)))
    with zipfile.ZipFile(tmp_path/'source.zip', 'w') as z:
        z.writestr('../escape.txt', 'bad')
    with pytest.raises(Exception, match='Unsafe zip member'):
        runtime._action_zip_extract('bad', 'source.zip', {'destination':'output'})
    assert not (tmp_path/'escape.txt').exists()


def test_absolute_paths_inside_script_see_only_workspace_copy(runner, tmp_path):
    (runner.workspace / 'input.json').write_text('{"value":7}')
    outside = tmp_path / 'private-secret'; outside.write_text('not mounted')
    # The literal occurs inside file bytes, so rewriting argv cannot fix it.
    (runner.workspace / 'script.py').write_text(f'''from pathlib import Path
import json
root=Path({str(runner.workspace)!r})
assert not Path({str(outside)!r}).exists()
assert sorted(p.name for p in root.parent.iterdir()) == [root.name]
data=json.loads((root/'input.json').read_text())
(root/'output.json').write_text(str(data['value']+1))
''')
    result = runner.run(['/usr/bin/python3', str(runner.workspace / 'script.py')], require_os_containment=True)
    assert result['ok'], result
    assert (runner.workspace / 'output.json').read_text() == '8'
    assert outside.read_text() == 'not mounted'
    (runner.workspace / 'script.py').write_text((runner.workspace / 'script.py').read_text() + '\nraise SystemExit(7)')
    (runner.workspace / 'output.json').unlink()
    result = runner.run(['/usr/bin/python3', str(runner.workspace / 'script.py')], require_os_containment=True)
    assert result['returncode'] == 7 and not (runner.workspace / 'output.json').exists()


def test_media_encoder_fits_real_sandbox_and_produces_decodable_frames(runner):
    import shutil
    import subprocess
    from PIL import Image
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('requires installed FFmpeg and FFprobe')
    for name, color in [('first.png', 'red'), ('second.png', 'blue')]:
        Image.new('RGB', (160, 80), color).save(runner.workspace / name)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(runner.workspace),
        sandbox_enabled=True, sandbox_require_os_containment=True))
    result = runtime.run('video.slideshow', 'movie.mp4',
        {'images':['first.png','second.png'], 'frame_rate': 5, 'size':'160x80'})
    assert result['success'], json.dumps(result, ensure_ascii=False, indent=2)
    assert result['ffmpeg']['containment'] == 'linux-bubblewrap'
    decoded = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-threads', '1',
        '-i', str(runner.workspace/'movie.mp4'), '-f', 'null', '-'], capture_output=True)
    assert decoded.returncode == 0, decoded.stderr
    probe = subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,nb_frames', '-of', 'json', str(runner.workspace/'movie.mp4')],
        capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)['streams'][0]
    assert (info['width'], info['height'], int(info['nb_frames'])) == (160, 80, 2)


@pytest.mark.parametrize('explicit_source', [True, False])
def test_portable_browser_snapshot_keeps_html_input_and_output_distinct(tmp_path, explicit_source):
    from PIL import Image
    source = '<html><title>Fixture</title><body>Actual content 42</body></html>'
    (tmp_path / 'page.html').write_text(source)
    runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(tmp_path)))
    target = 'snapshot.png' if explicit_source else 'page.html'
    args = {'source': 'page.html'} if explicit_source else {'output': 'snapshot.png'}
    result = runtime.run('browser.chrome.screenshot', target, args)
    assert result['success'], result
    assert (tmp_path / 'page.html').read_text() == source
    with Image.open(tmp_path / 'snapshot.png') as image:
        assert image.size == (1280, 1600)
        assert image.convert('L').getextrema()[0] < 255
