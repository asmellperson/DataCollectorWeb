from fastapi import APIRouter
from app.services import task_service as service

router = APIRouter(prefix="/api/tasks", tags=["后台任务"])
router.get("")(service.list_tasks)
router.get("/{task_id}")(service.get)
router.post("/{task_id}/cancel")(service.cancel)
