import shutil
from uuid import uuid4
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import FileResponse
from app.core.config import STATE_ROOT
from app.core.storage import require
from app.schemas.contracts import VideoOptions
from app.services import (
    video_service as service,
    task_service as tasks,
    dataset_service as datasets,
)
from app.inference import manager

router = APIRouter(tags=["视频导入"])


def check_options(dataset_id, options):
    dataset = datasets.get(dataset_id)
    require(
        options.auto_label == "off" or dataset["task_type"] == "detection",
        "视频自动预标仅支持 detection 数据集",
    )
    if options.auto_label != "off":
        manager.resolve(options.auto_label, options.model_path)


@router.post("/api/datasets/{dataset_id}/videos/import")
def import_video(dataset_id: str, body: VideoOptions):
    path = service.validate(body.path)
    check_options(dataset_id, body)
    return tasks.submit(
        "video_import", dataset_id, lambda p: service.extract(dataset_id, path, body, p)
    )


@router.post("/api/datasets/{dataset_id}/videos/upload")
def upload_video(
    dataset_id: str, file: UploadFile = File(...), options: str = Form("{}")
):
    try:
        body = VideoOptions.model_validate_json(options)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(422, "视频采样参数错误") from exc
    check_options(dataset_id, body)
    datasets.ensure_idle(dataset_id)
    suffix = Path(file.filename or "").suffix.lower()
    require(suffix in service.VIDEO_EXTS, "视频仅支持 MP4、AVI、MKV")
    temp = STATE_ROOT / "uploads" / uuid4().hex
    temp.mkdir(parents=True)
    path = temp / ("video" + suffix)
    try:
        with path.open("wb") as output:
            shutil.copyfileobj(file.file, output, 1024 * 1024)
        return tasks.submit(
            "video_import",
            dataset_id,
            lambda p: service.extract(dataset_id, path, body, p, file.filename),
            lambda: shutil.rmtree(temp),
        )
    except Exception:
        shutil.rmtree(temp)
        raise
    finally:
        file.file.close()


@router.get("/api/videos/preview")
def preview(path: str):
    return FileResponse(service.validate(path))
