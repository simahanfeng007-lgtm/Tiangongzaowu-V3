"""One isolated real-model case. It does not alter product gates or task output."""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from suite import check

BASE=Path(__file__).resolve().parent
SOURCE=(BASE.parent/'source').resolve()
sys.path.insert(0,str(SOURCE/'src'))
from total_gateway.release_manifest import generate_release_manifest,release_manifest_bytes

def run(task,arm,rep,batch):
    definition=json.loads((BASE/'experiment.json').read_text())
    prompt=next((t['prompt'] for t in definition['tasks'] if t['id']==task),None)
    if task=='smoke':prompt='读取工作区 input.json，计算 left 与 right 的和。生成 result.json，字段 sum 为计算结果，label 保留输入原始值。请实际生成文件。禁止访问桌面。'
    if prompt is None:raise ValueError('unknown task')
    out=BASE/'runs'/batch/f'r{rep}-{task}-{arm}'
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.umask(0o077)
    for name in ('profile/.tiangong','profile/.tiangong-v3','state','life','complete-life','world'):
        (out/name).mkdir(parents=True,exist_ok=True)
    shutil.copytree(BASE/'fixtures'/task,out/'workspace')
    (out/'profile/.tiangong/api_keys.json').touch()
    release=generate_release_manifest(SOURCE)
    (out/'release-manifest.json').write_bytes(release_manifest_bytes(release))
    env={k:v for k,v in os.environ.items() if not k.startswith('TIANGONG_') and k not in ('PYTHONPATH','PYTHONSTARTUP')}
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env.update({'PYTHONDONTWRITEBYTECODE':'1','DICT_AB_OUT':str(out),'DICT_AB_ARM':arm,
        'TIANGONG_MODEL_CONTEXT_REUSE':os.environ.get('TIANGONG_MODEL_CONTEXT_REUSE','1'),'TIANGONG_ADVERSARIAL_REVIEW':os.environ.get('TIANGONG_ADVERSARIAL_REVIEW','off'),'TIANGONG_GATEWAY_ENVIRONMENT':'test','TIANGONG_GATEWAY_DEPLOYMENT_MODE':'embedded',
        'TIANGONG_GATEWAY_PORT':str(port),'TIANGONG_GATEWAY_URL':url,
        'TIANGONG_GATEWAY_STATE_ROOT':str(out/'state'),'TIANGONG_GATEWAY_WORKSPACE_ROOT':str(out/'workspace'),
        'TIANGONG_GATEWAY_RELEASE_SOURCE_ROOT':str(SOURCE),'TIANGONG_GATEWAY_RELEASE_MANIFEST_PATH':str(out/'release-manifest.json'),
        'TIANGONG_LIFE_DATA_ROOT':str(out/'life'),'TIANGONG_LIFE_RUNTIME_ROOT':str(out/'complete-life'),
        'TIANGONG_RUN_STATE_DIR':str(out/'state'),'TIANGONG_V3_STATE_DIR':str(out/'state'),
        'TIANGONG_WORLD_STATE_ROOT':str(out/'world'),
        'TIANGONG_OMNI_BODY_ROOT':str(SOURCE/'app/backend/tiangong-backend/_internal/omni_body_skill'),
        'TIANGONG_OMNI_BODY_STATE_ROOT':str(out/'omni-body'),'TIANGONG_OMNI_BODY_WORKSPACE':str(out/'workspace')})
    for name in ('TIANGONG_BACKEND_INTERNAL_TOKEN','TIANGONG_LIFE_INTERNAL_TOKEN','TIANGONG_GATEWAY_COMMUNICATION_TOKEN',
                 'TIANGONG_GATEWAY_LIFE_INTENT_TOKEN','TIANGONG_GATEWAY_SHADOW_TOKEN','TIANGONG_DESKTOP_TOKEN'):
        env[name]=secrets.token_hex(32)
    config=BASE/'private/model-config.json'
    settings=json.loads(config.read_text());provider=settings['_default_provider'];profile=settings['_provider_inputs'][provider]
    keys={x for x in (settings.get('_api_keys',{}).get(provider),profile.get('api_key')) if isinstance(x,str) and x.strip()}
    if provider!='deepseek_v4' or profile['base_url'].rstrip('/')!='https://api.deepseek.com' or len(keys)!=1:
        raise ValueError('experiment_provider_config_changed')
    env['TIANGONG_DEEPSEEK_V4_API_KEY']=next(iter(keys))
    candidate_identity=json.loads((BASE.parent/'candidate-source.json').read_text())
    for name,digest in candidate_identity['files'].items():
        if hashlib.sha256((SOURCE/name).read_bytes()).hexdigest()!=digest: raise RuntimeError('source_changed:'+name)
    record={'context_reuse':env['TIANGONG_MODEL_CONTEXT_REUSE'],'task':task,'arm':arm,'replicate':rep,'batch':batch,'source_baseline_commit':definition['source_commit'],
        'source_candidate_tree_sha256':candidate_identity['candidate_tree_sha256'], 'source_identity_type':'git_commit', 'source_commit':candidate_identity['head'],'review_mode':os.environ.get('TIANGONG_ADVERSARIAL_REVIEW','off'),
        'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'fixture_sha256':definition['fixtures_sha256'][task], 'experiment_sha256':hashlib.sha256((BASE/'experiment.json').read_bytes()).hexdigest(),
        'dictionary_encoding_sha256':hashlib.sha256((BASE/'full-short-dictionary.json').read_bytes()).hexdigest(),
        'configured_provider':provider,'configured_model':profile['model_name'],
        'profile_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'scope':'pilot' if task=='smoke' else 'paired_experiment','port':port}
    def save():
        tmp=out/'result.tmp';tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2));tmp.replace(out/'result.json')
    def api(path,body=None):
        req=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers={
            'Content-Type':'application/json','X-Tiangong-Token':env['TIANGONG_DESKTOP_TOKEN']})
        try:
            with urllib.request.urlopen(req,timeout=20) as response:return {'http_status':response.status,'payload':json.load(response)}
        except urllib.error.HTTPError as exc:return {'http_status':exc.code,'payload':json.load(exc)}
    args=['bwrap','--die-with-parent','--ro-bind','/','/','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
          '--bind',str(out),str(out),'--bind',str(out/'profile/.tiangong'),str(Path.home()/'.tiangong'),
          '--bind',str(out/'profile/.tiangong-v3'),str(Path.home()/'.tiangong-v3'),
          '--ro-bind',str(config),str(Path.home()/'.tiangong/api_keys.json'),sys.executable,'-B',str(BASE/'gateway_entry.py')]
    if os.environ.get('DICT_AB_HOST_PROFILE') == '1':
        # Run the real Gateway with isolated per-process profile paths. Its
        # product tool runner still creates its own full OS sandbox. The old
        # outer user namespace forbids nested namespace creation on this host.
        (out/'profile/.tiangong/api_keys.json').unlink()
        (out/'profile/.tiangong/api_keys.json').symlink_to(config.resolve())
        env['DICT_AB_PROFILE_ROOT'] = str(out/'profile')
        record['harness_isolation'] = 'private-profile-with-product-os-tool-sandbox'
        args = [sys.executable, '-B', str(BASE/'gateway_entry.py')]
    save();print(json.dumps({'case':out.name,'phase':'startup'},ensure_ascii=False),flush=True)
    with (out/'gateway.log').open('w') as log:
        process=subprocess.Popen(args,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            for _ in range(45):
                if process.poll() is not None:record['startup_exit']=process.returncode;break
                try:
                    record['ready']=api('/ready')
                    if record['ready']['http_status']==200:break
                except (OSError,ValueError):pass
                time.sleep(1)
            if record.get('ready',{}).get('http_status')==200:
                payload={'presentation_request_id':'dict-ab-'+secrets.token_hex(8),'session_id':'dict-ab-'+secrets.token_hex(8),
                    'message_id':'msg-'+secrets.token_hex(8),'submitted_at_ms':int(time.time()*1000),'text':prompt,'attachments':[]}
                started=time.monotonic();record['inbound']=api('/api/v1/gateway/desktop/inbound',payload)
                request_id=record['inbound'].get('payload',{}).get('gateway_request_id');record['request_id']=request_id;save()
                if request_id:
                    deadline=definition['pilot_deadline_seconds'] if task=='smoke' else definition['task_deadline_seconds']
                    history=[]
                    while time.monotonic()-started<deadline:
                        status=api('/api/v1/gateway/desktop/status?request_id='+request_id)
                        if not history or history[-1]!=status:
                            history.append(status);(out/'status-history.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
                        record['final_status']=status
                        state=status.get('payload',{}).get('run',{}).get('status')
                        record['request_status']=state
                        if state in ('COMPLETED','FAILED','CANCELLED','STOPPED'):break
                        time.sleep(1)
                    else:
                        record['deadline_exceeded']=True
                        record['cancel_response']=api('/api/v1/run/control',{'action':'cancel','request_id':request_id})
                    record['wall_seconds']=round(time.monotonic()-started,3)
        except Exception as exc:record['driver_error']=type(exc).__name__+':'+str(exc)[:500]
        finally:
            if process.poll() is None:
                (out/'stop.requested').touch()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                    record['forced_stop']=True
            record['process_exit']=process.returncode
            record['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
            save()
    record['artifact_check']=check(task,out/'workspace')
    record['task_success']=record.get('request_status')=='COMPLETED' and record['artifact_check']['pass'] and not record.get('deadline_exceeded')
    calls=[]
    for p in sorted((out/'model-calls').glob('*/record.json')):
        value=json.loads(p.read_text());last=p.parent/'last-usage.json'
        if not value.get('usage') and last.exists():value['usage']=json.loads(last.read_text())
        calls.append(value)
    token_fields=('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')
    record['model_metrics']={'calls':len(calls),'task_calls':sum(bool(c.get('task_call')) for c in calls),
        'auxiliary_calls':sum(c.get('task_call') is False for c in calls),'wire_requests':sum(c.get('wire_requests',len([k for k in c if k.startswith('wire-')])) for c in calls),
        'errors':sum(c.get('ok') is False for c in calls),'usage_report_count':sum(bool(c.get('usage')) for c in calls),
        'incomplete_calls':sum(not c.get('complete') for c in calls),
        'server_model_names':sorted({m for c in calls for m in c.get('server_model_names',[])}),
        'tokens':{k:sum(c.get('usage',{}).get(k,0) or 0 for c in calls if c.get('usage')) for k in token_fields}}
    main_calls=[c for c in calls if c.get('task_call') and c.get('request_id')==record.get('request_id')]
    record['model_metrics']['main_task_calls']=len(main_calls)
    record['model_metrics']['main_task_usage_reports']=sum(bool(c.get('usage')) for c in main_calls)
    record['model_metrics']['main_task_tokens']={k:sum(c.get('usage',{}).get(k,0) or 0 for c in main_calls if c.get('usage')) for k in token_fields}
    decodes=out/'model-calls/shortcode-decoding.jsonl'
    record['shortcode_substitutions']=sum(len(json.loads(line)['changes']) for line in decodes.read_text().splitlines()) if decodes.is_file() else 0
    db=out/'state/gateway.sqlite3'
    if db.is_file():
        connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
        events=[json.loads(x[0]) for x in connection.execute('select event_json from execution_ledger order by ledger_seq')]
        record['execution_event_counts']=dict(collections.Counter(e['event_type'] for e in events))
        record['observed_step_failures']=sum(e['event_type']=='step.observed' and e['payload'].get('ok') is False for e in events)
        actions=[]
        for event in events:
            if event['event_type']=='composition.registered':
                program=json.loads(event['payload']['program_json']);actions += [x['invocation']['action'] for x in program['leaves']]
        record['registered_actions']=dict(collections.Counter(actions))
        connection.close()
    record['output_file_sha256']={p.relative_to(out/'workspace').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((out/'workspace').rglob('*')) if p.is_file() and not p.is_symlink()}
    save();print(json.dumps({k:record.get(k) for k in ('task','arm','replicate','request_status','task_success','wall_seconds','artifact_check','shortcode_substitutions','model_metrics')},ensure_ascii=False),flush=True)
    return record

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--task',required=True);parser.add_argument('--arm',choices=['A','C'],required=True)
    parser.add_argument('--rep',type=int,default=1);parser.add_argument('--batch',required=True);args=parser.parse_args()
    run(args.task,args.arm,args.rep,args.batch)
