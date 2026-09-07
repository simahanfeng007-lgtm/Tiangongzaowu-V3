"""Historical pre-P10 fixture data, not a production publication bypass.

Tests that exercise retained old capabilities must seed historical records rather
than call a now-frozen publisher. This helper is never imported by product code.
"""
from copy import deepcopy
import json
from pathlib import Path
from contracts import canonical_sha256


def historical_published_artifact(compiled):
    value = deepcopy(compiled)
    assert value['status'] == 'built'
    assert value['artifact_sha256'] == canonical_sha256({
        'schema': value['schema'], 'artifact': {k:v for k,v in value.items() if k != 'artifact_sha256'}})
    value['status'] = 'published'
    value['publish_sha256'] = canonical_sha256({'artifact_sha256': value['artifact_sha256'], 'state': 'published'})
    return value


def seed_historical_bundle(root, compiled, pointer=None):
    root = Path(root)
    directory = root / compiled['artifact_id']
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'artifact.json').write_text(json.dumps(compiled, ensure_ascii=False), encoding='utf-8')
    (directory/'SKILL.md').write_text(compiled['document']['content'],encoding='utf-8')
    if pointer is not None:
        key = 'ptr_'+canonical_sha256({'life_id':pointer['life_id'],'lineage_id':pointer['lineage_id']})[:24]
        p=root/key; p.mkdir(parents=True,exist_ok=True)
        (p/'current.json').write_text(json.dumps(pointer),encoding='utf-8')
    return directory


def seed_historical_capability(life, learning, action_catalog, *, previous=None, status='active'):
    from life_service.artifact_executor import compile_artifact
    from life_service.capability_health import attach_health
    life_id=life._active()['life_id']
    compiled=compile_artifact({**learning,'life_id':life_id},action_catalog=action_catalog,previous_artifact=previous)
    artifact=historical_published_artifact(compiled)
    pointer={'schema':'tiangong.life.capability-pointer.v1','life_id':life_id,'lineage_id':artifact['lineage_id'],
        'kind':artifact['kind'],'status':status,'current_artifact_id':artifact['artifact_id'],
        'current_artifact_sha256':artifact['artifact_sha256'],'history':[]}
    pointer=attach_health(pointer,artifact=artifact,now_ms=1)
    scope=life._scope_state(life_id)
    scope['capabilities'][artifact['artifact_id']]={**artifact,'origin':'life_learning'}
    scope['capability_pointers'][artifact['lineage_id']]=pointer
    seed_historical_bundle(life.paths.artifact_root,compiled,pointer)
    life._persist(life_id)
    return artifact
