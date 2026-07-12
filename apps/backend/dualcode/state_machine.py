from .models import RunState

TRANSITIONS = {
    RunState.CREATED: {RunState.PLANNING, RunState.CANCELLED},
    RunState.PLANNING: {
        RunState.WAITING_APPROVAL,
        RunState.FALLBACK_TO_CODEX,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.WAITING_APPROVAL: {
        RunState.PLANNING,
        RunState.IMPLEMENTING,
        RunState.TESTING,
        RunState.REVIEWING,
        RunState.CANCELLED,
    },
    RunState.IMPLEMENTING: {
        RunState.WAITING_APPROVAL,
        RunState.TESTING,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.TESTING: {
        RunState.WAITING_APPROVAL,
        RunState.REVIEWING,
        RunState.IMPLEMENTING,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.REVIEWING: {
        RunState.COMPLETED,
        RunState.IMPLEMENTING,
        RunState.FALLBACK_TO_CODEX,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.FALLBACK_TO_CODEX: {RunState.IMPLEMENTING, RunState.FAILED, RunState.CANCELLED},
}


def transition(current: RunState, target: RunState) -> RunState:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Illegal transition: {current} -> {target}")
    return target
