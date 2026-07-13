import asyncio
import os
import platform
import sys
import tempfile
import time
from collections.abc import Awaitable
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .cli_adapters import ClaudeCliAdapter
from .codex_app_server import CodexAppServerAdapter
from .config import settings
from .models import AuditLog, ExecutionJob
from .runtime_settings import AgentSettings, agent_settings_store
from .scheduler import scheduler
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig


PROCESS_STARTED_AT = time.monotonic()


# Compatibility name retained for integrations that patch the health adapter.
CodexCliAdapter = CodexAppServerAdapter


async def _safe_health_check(check: Awaitable[bool]) -> bool:
    """Collapse probe failures to a boolean so diagnostics never expose secrets."""
    try:
        return bool(await asyncio.wait_for(check, timeout=8))
    except Exception:
        return False


async def _agent_snapshot(runtime: AgentSettings) -> dict[str, Any]:
    codex = CodexAppServerAdapter(
        runtime.codex_executable,
        model=runtime.codex_model,
        reasoning_effort=runtime.codex_reasoning_effort,
    )
    claude = ClaudeCliAdapter(
        runtime.claude_executable,
        model=runtime.claude_model,
        reasoning_effort=runtime.claude_reasoning_effort,
    )
    codex_ok, claude_ok = await asyncio.gather(
        _safe_health_check(codex.health_check()),
        _safe_health_check(claude.health_check()),
    )
    ssh_configured = bool(
        runtime.claude_ssh_enabled
        and runtime.claude_ssh_host
        and runtime.claude_ssh_username
        and runtime.claude_ssh_known_hosts
    )
    ssh_healthy = False
    if ssh_configured:
        try:
            remote = ClaudeSshAdapter(
                ClaudeSshConfig(
                    host=runtime.claude_ssh_host,
                    username=runtime.claude_ssh_username,
                    port=runtime.claude_ssh_port,
                    known_hosts=Path(runtime.claude_ssh_known_hosts),
                    client_keys=(Path(runtime.claude_ssh_client_key),)
                    if runtime.claude_ssh_client_key
                    else (),
                    remote_root=PurePosixPath(runtime.claude_remote_root),
                    claude_executable=PurePosixPath(runtime.claude_ssh_executable),
                    model=runtime.claude_model,
                    reasoning_effort=runtime.claude_reasoning_effort,
                )
            )
            ssh_healthy = await _safe_health_check(remote.health_check())
        except (TypeError, ValueError, OSError):
            ssh_healthy = False
    return {
        "enabled": runtime.enable_real_agents,
        "codex": {"configured": bool(runtime.codex_executable), "healthy": codex_ok},
        "claude_local": {
            "configured": bool(runtime.claude_executable),
            "healthy": claude_ok,
        },
        "claude_ssh": {"configured": ssh_configured, "healthy": ssh_healthy},
    }


def _data_directory_writable() -> bool:
    """Probe the application directory without reading any existing user file."""
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".diagnostic-", dir=settings.data_dir)
        os.close(descriptor)
        Path(name).unlink()
        return True
    except OSError:
        return False


async def build_diagnostic_snapshot(
    db: AsyncSession, *, version: str, recovered_jobs: int = 0
) -> dict[str, Any]:
    database_ok = True
    job_counts: dict[str, int] = {}
    audit_count = 0
    try:
        await db.execute(text("SELECT 1"))
        rows = await db.execute(
            select(ExecutionJob.status, func.count(ExecutionJob.id)).group_by(ExecutionJob.status)
        )
        job_counts = {str(status): int(count) for status, count in rows.all()}
        audit_count = int(await db.scalar(select(func.count(AuditLog.id))) or 0)
    except Exception:
        # Database diagnostics deliberately expose no driver message or filesystem location.
        database_ok = False

    try:
        runtime = agent_settings_store.load()
        agents = await _agent_snapshot(runtime)
        configuration = {
            "loaded": True,
            "real_agents_enabled": runtime.enable_real_agents,
            "ssh_enabled": runtime.claude_ssh_enabled,
            "test_command_configured": bool(runtime.test_executable),
        }
    except (OSError, ValueError, TypeError):
        configuration = {
            "loaded": False,
            "real_agents_enabled": False,
            "ssh_enabled": False,
            "test_command_configured": False,
        }
        agents = {
            "enabled": False,
            "codex": {"configured": False, "healthy": False},
            "claude_local": {"configured": False, "healthy": False},
            "claude_ssh": {"configured": False, "healthy": False},
        }

    writable = await asyncio.to_thread(_data_directory_writable)
    return {
        "status": "ok" if database_ok and writable else "degraded",
        "version": version,
        "storage": {"database_reachable": database_ok, "data_directory_writable": writable},
        "configuration": configuration,
        "agents": agents,
        # 存活 app-server 适配器收到但未映射的协议方法名（仅名称与计数，
        # 不含参数），用于排查「事件没有出现在界面上」类问题。
        "codex_protocol": {
            "unhandled_methods": scheduler.codex_protocol_diagnostics()
        },
        "jobs": {"counts": job_counts, "recovered_on_startup": recovered_jobs},
        "audit": {"record_count": audit_count if database_ok else None},
        "process": {
            "pid": os.getpid(),
            "uptime_seconds": max(0, int(time.monotonic() - PROCESS_STARTED_AT)),
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "architecture": platform.machine(),
            "packaged": bool(getattr(sys, "frozen", False)),
        },
    }
