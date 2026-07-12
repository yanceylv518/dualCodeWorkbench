import pytest
from dualcode.models import RunState
from dualcode.state_machine import transition


def test_happy_path():
    state = RunState.CREATED
    for target in (
        RunState.PLANNING,
        RunState.WAITING_APPROVAL,
        RunState.IMPLEMENTING,
        RunState.TESTING,
        RunState.REVIEWING,
        RunState.COMPLETED,
    ):
        state = transition(state, target)
    assert state is RunState.COMPLETED


def test_rejects_unsafe_skip():
    with pytest.raises(ValueError):
        transition(RunState.CREATED, RunState.IMPLEMENTING)


def test_real_planning_can_pause_for_network_approval():
    state = transition(RunState.CREATED, RunState.PLANNING)
    state = transition(state, RunState.WAITING_APPROVAL)
    assert transition(state, RunState.PLANNING) is RunState.PLANNING
