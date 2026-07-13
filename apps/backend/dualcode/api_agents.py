import asyncio
import json
from pathlib import Path, PurePosixPath
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy.ext.asyncio import AsyncSession
from .cli_adapters import ClaudeCliAdapter
from .codex_app_server import CodexAppServerAdapter
from .database import get_session
from .models import (
    AuditLog,
)
from .scheduler import scheduler
from .ssh_adapter import ClaudeSshAdapter, ClaudeSshConfig
from .runtime_settings import AgentSettings, agent_settings_store

# Compatibility name retained for integrations that patch the health adapter.
CodexCliAdapter = CodexAppServerAdapter
router = APIRouter(prefix="/api")

@router.get("/agents/models")
async def agent_models():
    """Return safe local model metadata without reading authentication material."""
    runtime = agent_settings_store.load()
    codex_models: list[dict[str, str]] = []
    cache = Path.home() / ".codex" / "models_cache.json"
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        for item in payload.get("models", []):
            slug = item.get("slug")
            if isinstance(slug, str) and slug:
                codex_models.append({
                    "id": slug,
                    "label": item.get("display_name") or slug,
                    "description": item.get("description") or "",
                    "default_reasoning": item.get("default_reasoning_level") or "medium",
                    "reasoning_levels": [level.get("effort") for level in item.get("supported_reasoning_levels", []) if level.get("effort")],
                })
    except (OSError, ValueError, TypeError):
        pass
    if runtime.codex_model and not any(item["id"] == runtime.codex_model for item in codex_models):
        codex_models.insert(0, {"id": runtime.codex_model, "label": runtime.codex_model, "description": "当前配置"})
    claude_models = [
        {"id": "fable", "label": "Fable 5"},
        {"id": "sonnet", "label": "Sonnet 5"},
        {"id": "haiku", "label": "Haiku 4.5"},
        {"id": "opus", "label": "Opus 4.8"},
        {"id": "claude-opus-4-7", "label": "Opus 4.7"},
        {"id": "claude-opus-4-6", "label": "Opus 4.6"},
        {"id": "claude-3-opus-20240229", "label": "Opus 3"},
        {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6"},
    ]
    current_claude = ""
    if runtime.claude_ssh_enabled and runtime.claude_ssh_host and runtime.claude_ssh_username and runtime.claude_ssh_known_hosts:
        try:
            remote_adapter = ClaudeSshAdapter(ClaudeSshConfig(
                host=runtime.claude_ssh_host,
                username=runtime.claude_ssh_username,
                port=runtime.claude_ssh_port,
                known_hosts=Path(runtime.claude_ssh_known_hosts),
                client_keys=(Path(runtime.claude_ssh_client_key),) if runtime.claude_ssh_client_key else (),
                remote_root=PurePosixPath(runtime.claude_remote_root),
                claude_executable=PurePosixPath(runtime.claude_ssh_executable),
                model=runtime.claude_model,
                reasoning_effort=runtime.claude_reasoning_effort,
            ))
            current_claude, _ = await remote_adapter.model_catalog()
        except Exception:  # Model discovery is optional; settings must remain usable offline.
            pass
    if runtime.claude_model and not any(item["id"] == runtime.claude_model for item in claude_models):
        claude_models.insert(0, {"id": runtime.claude_model, "label": runtime.claude_model})
    return {
        "codex": codex_models,
        "claude": [{**item, "description": f"VPS 当前默认：{current_claude}" if current_claude else "Claude 模型", "default_reasoning": "medium", "reasoning_levels": ["low", "medium", "high"]} for item in claude_models],
    }


@router.get("/agents/health")
async def agent_health():
    runtime = agent_settings_store.load()
    codex = CodexAppServerAdapter(runtime.codex_executable, model=runtime.codex_model, reasoning_effort=runtime.codex_reasoning_effort, permission_mode=runtime.codex_permission_mode)
    claude = ClaudeCliAdapter(runtime.claude_executable, model=runtime.claude_model, reasoning_effort=runtime.claude_reasoning_effort)
    codex_ok, claude_ok = await asyncio.gather(codex.health_check(), claude.health_check())
    remote: dict[str, object] = {"configured": False, "healthy": False, "vision": True}
    if (
        runtime.claude_ssh_enabled
        and runtime.claude_ssh_host
        and runtime.claude_ssh_username
        and runtime.claude_ssh_known_hosts
    ):
        remote_adapter = ClaudeSshAdapter(
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
        remote = {
            "configured": True,
            "healthy": await remote_adapter.health_check(),
            "vision": remote_adapter.capabilities.vision,
        }
    return {
        "real_agents_enabled": runtime.enable_real_agents,
        "codex": {"healthy": codex_ok, "vision": codex.capabilities.vision},
        "claude": {"healthy": claude_ok, "vision": claude.capabilities.vision},
        "claude_ssh": remote,
    }


@router.get("/settings/agents", response_model=AgentSettings)
async def get_agent_settings():
    return agent_settings_store.load()


@router.put("/settings/agents", response_model=AgentSettings)
async def update_agent_settings(value: AgentSettings, db: AsyncSession = Depends(get_session)):
    if scheduler.has_active_runs():
        raise HTTPException(409, "Agent 正在运行时不能修改设置")
    try:
        agent_settings_store.save(value)
        scheduler.configure(value)
    except ValueError as exc:
        raise HTTPException(400, f"Agent 设置无效：{exc}") from exc
    db.add(
        AuditLog(
            workspace_id="system",
            thread_id=None,
            event="agent.settings.updated",
            detail=(
                f"real={value.enable_real_agents};ssh={value.claude_ssh_enabled};"
                f"codex={value.codex_executable};codex_model={value.codex_model or 'default'};"
                f"codex_effort={value.codex_reasoning_effort};claude={value.claude_executable};"
                f"claude_model={value.claude_model or 'default'};claude_effort={value.claude_reasoning_effort}"
            ),
        )
    )
    await db.commit()
    return value


