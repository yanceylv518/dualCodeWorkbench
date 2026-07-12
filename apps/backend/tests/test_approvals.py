import asyncio

import pytest

from dualcode.approvals import ApprovalGate


@pytest.mark.asyncio
async def test_approval_gate_delivers_decision_without_race():
    gate = ApprovalGate()
    gate.prepare("approval-1")
    assert gate.resolve("approval-1", True)
    assert await gate.wait("approval-1") is True


@pytest.mark.asyncio
async def test_approval_gate_rejects_unknown_decision():
    gate = ApprovalGate()
    assert not gate.resolve("missing", False)
    gate.prepare("approval-2")
    waiter = asyncio.create_task(gate.wait("approval-2"))
    await asyncio.sleep(0)
    assert gate.resolve("approval-2", False)
    assert await waiter is False
