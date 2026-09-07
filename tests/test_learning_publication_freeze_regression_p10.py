"""Same API rejection cases replayed against R0 and R1; no new-helper dependency."""
import pytest
from life_service.artifact_executor import (
    ArtifactExecutorError, compile_artifact, publish_artifact, persist_artifact_bundle,
)
from life_service.learning_workflow import build_draft, publish_draft


@pytest.mark.parametrize('kind', ['skill', 'tool'])
def test_user_direct_learning_cannot_publish_complete_capability(kind):
    draft = build_draft(life_id='life_regression', scope={}, source='user_direct',
                        decision={'target': kind, 'request': 'proof', 'draft_artifact': {'content': 'source'}})
    record, artifact = publish_draft(draft, capabilities={})
    assert record['status'] == 'migration_required'
    assert artifact is None and record['registered'] is False


@pytest.mark.parametrize('kind', ['skill', 'tool'])
@pytest.mark.parametrize('sink', ['publish', 'persist'])
def test_compiled_legacy_capability_cannot_enter_a_publication_sink(tmp_path, kind, sink):
    source = {'life_id': 'life_regression', 'learning_id': 'learn_regression', 'target': kind,
              'title': 'regression', 'summary': 'retained source evidence', 'draft_artifact': {'content': 'source', 'required_actions': ['file.read'],
              'steps': [{'step_id': 'read', 'action_id': 'file.read', 'arguments_template': {'path': 'proof'}}]}}
    built = compile_artifact(source, action_catalog=[{'action_id': 'file.read', 'risk': 'A0', 'available': True}])
    with pytest.raises(ArtifactExecutorError, match='life.learning.legacy_publication_frozen'):
        if sink == 'publish':
            publish_artifact(built)
        else:
            persist_artifact_bundle(tmp_path/'bundle', built)
    assert not list(tmp_path.iterdir())
