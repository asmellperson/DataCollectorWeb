from fastapi import APIRouter, Response
from app.schemas.contracts import CollectionStart
from app.services import collection_service as service

router = APIRouter(prefix="/api/collection", tags=["实时采集"])
router.get("/status")(service.status)
router.post("/stop")(service.stop)


@router.post("/start")
def start(body: CollectionStart):
    return service.start(body)


@router.get("/frame")
def frame():
    return Response(
        service.preview_image(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )
