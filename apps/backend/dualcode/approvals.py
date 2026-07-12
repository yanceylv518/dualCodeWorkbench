import asyncio


class ApprovalGate:
    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[bool]] = {}

    def prepare(self, approval_id: str) -> None:
        self._waiters[approval_id] = asyncio.get_running_loop().create_future()

    async def wait(self, approval_id: str) -> bool:
        future = self._waiters.get(approval_id)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._waiters[approval_id] = future
        try:
            return await future
        finally:
            self._waiters.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        future = self._waiters.get(approval_id)
        if not future or future.done():
            return False
        future.set_result(approved)
        return True


approval_gate = ApprovalGate()
