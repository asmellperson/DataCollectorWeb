from fastapi import APIRouter, Response
from app.schemas.contracts import CollectionStart
from app.services import collection_service as service

router = APIRouter(prefix="/api/collection", tags=["实时采集"])


@router.get("/status")
def status(camera_id: str | None = None):
    return service.status(camera_id)


@router.post("/stop")
def stop(camera_id: str | None = None, dataset_id: str | None = None):
    return service.stop(camera_id, dataset_id)


@router.post("/start")
def start(body: CollectionStart):
    return service.start(body)


@router.get("/frame")
def frame(camera_id: str | None = None):
    return Response(
        service.preview_image(camera_id),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
