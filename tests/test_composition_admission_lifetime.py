"""Bounded deadlines are system policy, not model-supplied authorization."""
import pytest

from contracts.composition_profile import (
    WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256,
    WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256,
)
from total_gateway.composition_admission_lifetime import composition_admission_lifetime_ms

PYTHON=dict(execution_profile_id=WORKSPACE_PYTHON_PROFILE_ID,
            execution_profile_sha256=WORKSPACE_PYTHON_PROFILE_SHA256)
WRITE=dict(execution_profile_id=WORKSPACE_WRITE_PROFILE_ID,
           execution_profile_sha256=WORKSPACE_WRITE_PROFILE_SHA256)


def test_legacy_deadline_and_fixed_profile_budget():
    assert composition_admission_lifetime_ms(('file.read',))==60_000
    assert composition_admission_lifetime_ms(('skill.list',)*128)==60_000
    assert composition_admission_lifetime_ms(('code.write','file.read'),**WRITE)==120_000
    actual_actions=('code.write',)*3+('python.run',)*2+('file.read',)*2
    assert composition_admission_lifetime_ms(actual_actions,**PYTHON)==390_000
    assert composition_admission_lifetime_ms(('python.run',)*9,**PYTHON)==870_000


@pytest.mark.parametrize('actions,profile',[
    (('python.run',),WRITE),
    (('shell.run',),PYTHON),
    (('file.delete',),PYTHON),
    ((),PYTHON),
    (('python.run',)*10,PYTHON),
    (('file.read',)*129,PYTHON),
    (('file.read',),dict(PYTHON,execution_profile_sha256='0'*64)),
    (('file.read',),dict(execution_profile_id=None,execution_profile_sha256='0'*64)),
])
def test_profile_does_not_authorize_unbounded_or_unknown_work(actions,profile):
    with pytest.raises(ValueError):
        composition_admission_lifetime_ms(actions,**profile)


def test_default_shadow_still_rejects_sixty_seconds_plus_one():
    from tests.test_composition_activation_shadow_p7a import _validated_read_plan, _propose
    # Reuse the original authority fixture, not a fabricated low-risk permission.
    values=_validated_read_plan()
    assert _propose(*values,issued_at_ms=20,expires_at_ms=60_020).has_valid_sha256()
    with pytest.raises(ValueError,match='lifetime_invalid'):
        _propose(*values,issued_at_ms=20,expires_at_ms=60_021)
