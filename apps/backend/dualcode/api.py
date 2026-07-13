from fastapi import APIRouter

from .api_agents import router as agents_router
from .api_attachments import router as attachments_router
from .api_collaboration import router as collaboration_router
from .api_jobs import router as jobs_router
from .api_workspaces import router as workspaces_router

router = APIRouter()
router.include_router(jobs_router)
router.include_router(agents_router)
router.include_router(collaboration_router)
router.include_router(workspaces_router)
router.include_router(attachments_router)
