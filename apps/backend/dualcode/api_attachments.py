import io
import json
import uuid
from pathlib import Path
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image
from .config import settings
from .connections import manager
from .database import get_session
from .events import AgentEvent, EventType
from .models import (
    Attachment,
    AuditLog,
    Thread,
)

router = APIRouter(prefix="/api")

@router.post("/workspaces/{workspace_id}/threads/{thread_id}/attachments", status_code=201)
async def upload_attachment(
    workspace_id: str,
    thread_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.workspace_id == workspace_id)
    )
    if not thread:
        raise HTTPException(404, "项目与任务不匹配")
    if file.content_type not in settings.allowed_attachment_types:
        raise HTTPException(415, "不支持此附件类型")
    content = await file.read(settings.max_attachment_bytes + 1)
    if len(content) > settings.max_attachment_bytes:
        raise HTTPException(413, "附件大小超过限制")
    if file.content_type in {"image/png", "image/jpeg", "image/webp"}:
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                output = io.BytesIO()
                format_name = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[file.content_type]
                if format_name == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output, format=format_name)
                content = output.getvalue()
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "图片附件无效或已损坏") from exc
    attachment_suffix = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "text/plain": ".txt",
    }.get(file.content_type, "")
    key = f"{workspace_id}/{thread_id}/{uuid.uuid4()}{attachment_suffix}"
    target = settings.data_dir / "attachments" / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    item = Attachment(
        workspace_id=workspace_id,
        thread_id=thread_id,
        name=Path(file.filename or "attachment").name,
        media_type=file.content_type,
        size=len(content),
        storage_key=key,
    )
    db.add(item)
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            thread_id=thread_id,
            event="attachment.created",
            detail=f"{item.name}:{len(content)}",
        )
    )
    await db.commit()
    return {"id": item.id, "name": item.name, "media_type": item.media_type, "size": item.size}


@router.get("/workspaces/{workspace_id}/threads/{thread_id}/attachments/{attachment_id}/content")
async def attachment_content(workspace_id: str, thread_id: str, attachment_id: str, db: AsyncSession = Depends(get_session)):
    item = await db.scalar(select(Attachment).where(
        Attachment.id == attachment_id, Attachment.workspace_id == workspace_id, Attachment.thread_id == thread_id
    ))
    if not item:
        raise HTTPException(404, "未找到附件")
    root = (settings.data_dir / "attachments").resolve()
    target = (root / item.storage_key).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "未找到附件内容")
    return FileResponse(target, media_type=item.media_type, filename=item.name)


@router.websocket("/ws/threads/{thread_id}")
async def thread_events(ws: WebSocket, thread_id: str):
    await manager.connect(thread_id, ws)
    await ws.send_json(
        AgentEvent(type=EventType.CONNECTED, thread_id=thread_id).model_dump(mode="json")
    )
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await manager.disconnect(thread_id, ws)
