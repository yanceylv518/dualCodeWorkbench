import asyncio
import json


git_tasks: set[asyncio.Task[None]] = set()


def json_list(value: str) -> list[str]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []
