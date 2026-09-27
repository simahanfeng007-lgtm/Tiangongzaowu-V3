"""Independent byte/SQLite readback of real task evidence. Never alters a run."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import sqlite3
import subprocess

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def canonical(value):
    if isinstance(value, dict):
        return {k:canonical(value[k]) for k in sorted(value, key=lambda v:v.encode('utf-16-be'))}
    if isinstance(value, list): return [canonical(v) for v in value]
    return value
def digest(value):
    return hashlib.sha256(json.dumps(canonical(value),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
parser=argparse.ArgumentParser()
parser.add_argument('--attempt', default='1')
parser.add_argument('--audit-id', default='')
parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[2])
parser.add_argument('--output-root',type=Path,required=True)
args=parser.parse_args()
SOURCE=args.source_root.resolve()
BASE=args.output_root.resolve()
out=BASE/('audit-'+args.attempt+('-'+args.audit_id if args.audit_id else '')+'.json')
if out.exists(): raise SystemExit('Refusing to replace prior audit')
summary={'schema':'tiangong.ontology.live-audit.v1','checked_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip(),'cases':[]}
for kind in ('local','browser','mcp','quality','document'):
    p=BASE/'live'/(kind+'-'+args.attempt)
    if kind == 'document' and not p.exists():
        continue
    record=json.loads((p/'result.json').read_text())
    snapshot=json.loads((p/'source-files.json').read_text())
    products={n:h for n,h in snapshot.items() if n.startswith(('src/','app/','dictionaries/','templates/'))}
    changed=[n for n,h in products.items() if not (SOURCE/n).is_file() or sha(SOURCE/n)!=h]
    state=json.loads(next((p/'profile/.tiangong/v3/simple_chain_run_state').glob('req_*.json')).read_text())
    dbpath=p/'state/gateway.sqlite3'
    # SQLite readers update SHM lock/reader coordination bytes even in mode=ro.
    # The durable database and WAL must remain identical; SHM is not evidence.
    before={str(f):sha(f) for f in p.glob('state/gateway.sqlite3*') if f.is_file() and not f.name.endswith('-shm')}
    wal=Path(str(dbpath)+'-wal')
    immutable=not wal.exists() or wal.stat().st_size==0
    db=sqlite3.connect(dbpath.as_uri()+'?mode=ro'+('&immutable=1' if immutable else ''),uri=True)
    events=[json.loads(r[0]) for r in db.execute('SELECT event_json FROM execution_ledger ORDER BY ledger_seq')]
    decisions=db.execute('SELECT outcome,request_id,run_id,generation FROM completion_decisions').fetchall()
    db.close()
    after={str(f):sha(f) for f in p.glob('state/gateway.sqlite3*') if f.is_file() and not f.name.endswith('-shm')}
    identity=state['review_authority_identity']
    previous='0'*64
    for i,event in enumerate(events,1):
        assert event['ledger_seq']==i
        assert event['prev_event_hash']==previous
        assert digest(event['payload'])==event['payload_hash']
        assert digest({k:v for k,v in event.items() if k!='event_hash'})==event['event_hash']
        assert all(event[k]==identity[k] for k in ('request_id','run_id','generation'))
        previous=event['event_hash']
    if before != after:
        issue=BASE/('audit-'+args.attempt+'-readback-error.json')
        issue.write_text(json.dumps({'case':kind,'before':before,'after':after},indent=2))
        raise AssertionError('durable database/WAL inventory changed: '+str(issue))
    assert decisions==[('COMPLETED',identity['request_id'],identity['run_id'],identity['generation'])]
    review=state['adversarial_completion']
    final=review['reports'][-1]
    assert state['completion_authority']=='adversarial_agent' and review['decision']=='complete'
    assert final['decision']=='complete' and final['basis_sha256']==review['current_basis_sha256']
    assert final['identity']=={k:identity[k] for k in ('request_id','run_id','generation')}
    versions=[]
    for version in final['artifact_versions']:
        assert version['state']=='observed'
        artifact=Path(version['path'])
        assert artifact.is_file() and sha(artifact)==version['sha256'] and artifact.stat().st_size==version['size_bytes']
        versions.append({'path':artifact.relative_to(p).as_posix(),'sha256':version['sha256'],'size_bytes':version['size_bytes']})
    assert json.loads((p/'shutdown-complete.json').read_text())['runtime_closed'] is True
    assert record['process_exit']==0 and not record.get('forced_stop')
    assert all(sha(p/'workspace'/n)==h for n,h in record['input_sha256'].items() if n!='aggregate.py')
    actual={}
    if kind=='local':
        totals=json.loads((p/'workspace/totals.json').read_text()); assert totals=={'杭州':12,'上海':6}
        calls=state['tool_calls']; actions=[x['tool_action'] for x in calls]
        assert actions.count('python.run')>=2
        readbacks=[]
        for observation in state['observations']:
            target=observation.get('tool_args',{}).get('target','')
            if observation.get('tool_action')=='file.read' and Path(target).name=='totals.json':
                readbacks.append(json.loads(observation['tool_result']['result']['content']))
        assert readbacks[0]=={'杭州':0,'上海':-2} and readbacks[-1]==totals
        actual={'totals':totals,'sum':sum(totals.values()),'before_repair':readbacks[0],
                'actual_compute_runs':actions.count('python.run')}
    elif kind=='browser':
        artifact=p/'workspace/result.json'
        if not artifact.exists(): artifact=p/'workspace/downloads/result.json'
        assert json.loads(artifact.read_text())=={'total':73}
        assert any(c.get('tool_action')=='browser.chrome.click' and c.get('ok') for c in state['tool_calls'])
        screenshots=[f for f in (p/'workspace').rglob('*.png') if f.is_file() and f.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')]
        assert screenshots
        actual={'total':73,'download':artifact.relative_to(p).as_posix(),'png_files':[f.relative_to(p).as_posix() for f in screenshots]}
    elif kind=='mcp':
        protocol=json.loads((p/'mcp-events.json').read_text())
        calls=[e['params'] for e in protocol if e['method']=='tools/call']
        assert [c['name'] for c in calls]==['create_record','read_record']
        rows=[json.loads(f.read_text()) for f in (p/'remote-objects').glob('*.json')]
        assert len(rows)==1 and rows[0]['value']==41 and rows[0]['label']=='本体连接验收' and rows[0]['version']==1
        assert calls[-1]['arguments']['id']==rows[0]['id']
        assert any(e.get('params',{}).get('cursor') for e in protocol if e['method']=='tools/list')
        actual={'records_created':1,'real_read_id':rows[0]['id'],'value':41,'version':1,'tools_pagination_observed':True}
    elif kind=='document':
        import zipfile
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(p/'workspace/inventory.docx') as archive:
            tree=ET.fromstring(archive.read('word/document.xml'))
        values=[node.text for node in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t')]
        assert 'SKU-A' in values and 'SKU-B' in values and '7' in values and '11' in values
        calls=[c for c in state['tool_calls'] if c.get('tool_action')=='qc.docx.delivery_check']
        assert any(c.get('ok') for c in calls) and any(not c.get('ok') for c in calls)
        report=(p/'workspace/report.md').read_text()
        assert all(v in report for v in ('SKU-A','SKU-B','7','11','18'))
        actual={'independent_XML_values':values,'known_total':18,'corrupt_read_failure_retained':True}
    else:
        assert not (p/'workspace/must-not-execute.txt').exists()
        assert (p/'workspace/report.md').is_file()
        assert any(c.get('tool_action')=='qc.code.delivery_check' and c.get('ok') for c in state['tool_calls'])
        assert not any(c.get('tool_action') in ('python.run','shell.run','quality.run_tests') for c in state['tool_calls'])
        actual={'project_execution_marker_absent':True,'report_sha256':sha(p/'workspace/report.md')}
    wires=[json.loads(f.read_text()) for f in (p/'model-calls').glob('*/record.json')]
    summary['cases'].append({'case':kind,'request_id':record['request_id'],'base_commit':record['base_commit'],
        'source_snapshot_sha256':sha(p/'source-files.json'),'product_files_compared':len(products),'product_files_changed':changed,
        'ledger_events':len(events),'ledger_types':dict(Counter(e['event_type'] for e in events)),
        'ledger_hash_chain_valid':True,'database_unchanged':True,'completion_identity_valid':True,
        'judge_approved_current_versions':True,'artifact_versions':versions,
        'model_generated_compositions':len(state['generated_compositions']),
        'failed_actions_retained':[c for c in state['tool_calls'] if not c.get('ok')],
        'wire_server_models':sorted({m for c in wires for m in c.get('server_model_names',[])}),
        'metrics':record['model_metrics'],'wall_seconds':record['wall_seconds'],
        'driver_original_passed':record['independent_check']['passed'],'independent_readback':actual,
        'graceful_shutdown':True,'fact_audit_passed':True})
summary['fact_audit_passed']=all(c['fact_audit_passed'] for c in summary['cases'])
summary['exact_product_files_match']=all(not c['product_files_changed'] for c in summary['cases'])
out.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({'output':str(out),'fact_audit_passed':summary['fact_audit_passed'],'exact_product_files_match':summary['exact_product_files_match']}))

raise SystemExit(0 if summary["fact_audit_passed"] and summary["exact_product_files_match"] else 1)
