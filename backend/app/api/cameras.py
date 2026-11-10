import asyncio
import json
from fastapi import APIRouter, Response, Request
from fastapi.responses import StreamingResponse
from fastapi import HTTPException
from app.schemas.contracts import CameraEdit, CameraView, LensMove
from app.services import camera_service as service, task_service as tasks, lens_service

router = APIRouter(prefix="/api/cameras", tags=["摄像头"])
router.get("")(service.list_all)
router.delete("/{camera_id}")(service.delete)


@router.post("")
def create(body: CameraEdit):
    return service.create(body)


@router.put("/{camera_id}")
def update(camera_id: str, body: CameraEdit):
    return service.update(camera_id, body)


@router.post("/{camera_id}/test")
def test(camera_id: str):
    service.get(camera_id)
    return tasks.submit("camera_test", None, lambda p: service.test(camera_id, p))


@router.get("/{camera_id}/view")
def view(camera_id: str):
    return service.get(camera_id)["view"]


@router.put("/{camera_id}/view")
def set_view(camera_id: str, body: CameraView):
    return service.set_view(camera_id, body)


@router.get("/{camera_id}/lens")
def lens(camera_id: str):
    return lens_service.capabilities(camera_id)


@router.post("/{camera_id}/lens")
def move_lens(camera_id: str, body: LensMove):
    return lens_service.move(camera_id, body.speed)


@router.get("/{camera_id}/status")
def status(camera_id: str):
    return service.reader(camera_id).status()


@router.get("/{camera_id}/frame")
def frame(camera_id: str, original: bool = False):
    data, view = service.reader(camera_id).image(with_view=True, original=original)
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Camera-View": json.dumps(view)},
    )


@router.get("/{camera_id}/stream")
def stream(camera_id: str, request: Request):
    capture = service.reader(camera_id)

    async def frames():
        last = -1
        while not await request.is_disconnected():
            if not capture.thread.is_alive():
                break
            try:
                status = capture.status()
                if status["frame_id"] != last and status["connected"]:
                    data = await asyncio.to_thread(capture.image)
                    last = status["frame_id"]
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                    )
            except HTTPException:
                # 断流时继续等待读流线程重连，不发送过期帧。
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.1)

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )
