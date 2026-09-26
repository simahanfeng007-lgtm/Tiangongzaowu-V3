"""Prepare a NEW reproduction run. No requests are sent by this script."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--profile',required=True);a=p.parse_args()
os.umask(0o077);base=Path(__file__).resolve().parent;source=Path(a.source).resolve(strict=True);profile=Path(a.profile).resolve(strict=True)
if base.is_relative_to(source):raise SystemExit('Copy this reproduction directory outside the source checkout first')
x=json.loads((base/'source-template.json').read_text());bad=[n for n,h in x['files'].items() if hashlib.sha256((source/n).read_bytes()).hexdigest()!=h]
if bad:raise SystemExit('Product source differs: '+','.join(bad))
if subprocess.check_output(['git','status','--porcelain'],cwd=source):raise SystemExit('Use a clean checkout')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()
(base/'source').symlink_to(source)
private=base/'cache-harness/private';private.mkdir(mode=0o700)
(private/'model-config.json').symlink_to(profile)
x.update(head=head,dirty=False,worktree=str(source),reproduced_from_product_commit=x['head'])
(base/'candidate-source.json').write_text(json.dumps(x,indent=2)+'\n')
f=base/'cache-harness/experiment.json';experiment=json.loads(f.read_text());experiment.update(source_commit=head,source_worktree=str(source));f.write_text(json.dumps(experiment,ensure_ascii=False,indent=2)+'\n')
print('Prepared fresh local run for '+head+'; no model calls have been made.')
