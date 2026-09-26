"""Interleaved same-candidate A/B, retaining every attempt including failures."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os, sys, json, subprocess, threading, time

BASE=Path(__file__).resolve().parent
definition=json.loads((BASE/'experiment.json').read_text())
jobs=[]
for rep in range(1,4):
    for task in definition['tasks']:
        for arm in (['canonical','reuse'] if rep%2 else ['reuse','canonical']):
            jobs.append({'task':task['id'],'replicate':rep,'context_arm':arm})
(BASE/'comparison-plan.json').write_text(json.dumps({'source_commit':definition['source_commit'],'jobs':jobs,'parallel_processes':2,'stop_rule':'Run all 48; stop only false completion, harness failure or source drift; preserve every failure.'},indent=2)+'\n')
lock=threading.Lock(); stop=threading.Event(); results=[]; position=0
def worker():
    global position
    while True:
        with lock:
            if stop.is_set() or position>=len(jobs):return
            job=jobs[position];position+=1
        env=dict(os.environ);env.update(DICT_AB_HOST_PROFILE='1',TIANGONG_ADVERSARIAL_REVIEW='judge',
            TIANGONG_MODEL_CONTEXT_REUSE='1' if job['context_arm']=='reuse' else '0')
        batch='comparison-'+job['context_arm']
        log=BASE/(f"compare-{job['replicate']}-{job['task']}-{job['context_arm']}.log")
        with log.open('w') as stream:
            proc=subprocess.run([sys.executable,str(BASE/'run_case.py'),'--task',job['task'],'--arm','A',
                '--rep',str(job['replicate']),'--batch',batch],env=env,stdout=stream,stderr=subprocess.STDOUT)
        path=BASE/'runs'/batch/f"r{job['replicate']}-{job['task']}-A"/'result.json'
        result=json.loads(path.read_text()) if path.exists() else {}
        row={**job,'exit':proc.returncode,'result_file':str(path),'task_success':result.get('task_success',False),
             'wall_seconds':result.get('wall_seconds'),'model_metrics':result.get('model_metrics')}
        with lock:
            results.append(row)
            with (BASE/'comparison-attempts.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
            print(json.dumps({k:v for k,v in row.items() if k!='model_metrics'}),flush=True)
            if (not result.get('finished_at') or proc.returncode != 0 or 'startup_exit' in result
                    or 'request_id' not in result
                    or (result.get('request_status') == 'COMPLETED' and not result.get('artifact_check', {}).get('pass'))):stop.set()
with ThreadPoolExecutor(max_workers=2) as pool:
    futures=[pool.submit(worker) for _ in range(2)]
    for future in futures:future.result()
(BASE/'comparison-summary.json').write_text(json.dumps({'planned':len(jobs),'attempted':len(results),
    'stopped_on_failure':stop.is_set(),'results':results},indent=2)+'\n')
