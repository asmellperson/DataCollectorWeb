"""安检时序标注入口；视频始终保存在服务器，不打包下载训练数据。"""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response

from app.core.storage import read_json, require
from app.schemas.security_annotation import (
    InspectionEdit,
    LegacyImport,
    ManifestRequest,
    PrelabelOptions,
    RecordingImport,
    RecordingStart,
    RevisionRequest,
)
from app.services.security_annotation import (
    legacy,
    media,
    recorder,
    store,
    vlm,
    workflow,
)

router = APIRouter(prefix="/api/security-annotations", tags=["安检视频标注"])


@router.get("/config")
def config():
    return {
        "model": vlm.settings()["model"],
        "base_url": vlm.settings()["base_url"],
        "data_root": str(store.ROOT.resolve()),
        "region_presets": legacy.presets(),
        "prelabel_defaults": PrelabelOptions().model_dump(),
    }


@router.get("/recordings")
def recordings():
    return store.listing()


@router.post("/recordings/import")
def import_video(body: RecordingImport):
    return workflow.import_video(body)


@router.get("/capture")
def capture_status():
    return recorder.status()


@router.post("/capture")
def capture_start(body: RecordingStart):
    return recorder.start(body)


@router.post("/recordings/{recording_id}/stop")
def capture_stop(recording_id: str):
    return recorder.stop(recording_id)


@router.get("/recordings/{recording_id}")
def recording(recording_id: str):
    with store.lock(recording_id):
        meta = store.get(recording_id)
        draft_path = store.folder(recording_id) / "latest_draft.json"
        return {
            "recording": meta,
            "annotation": store.annotation(recording_id),
            "latest_draft": read_json(draft_path) if draft_path.exists() else None,
        }


@router.delete("/recordings/{recording_id}")
def delete_recording(recording_id: str):
    return recorder.delete(recording_id)


@router.get("/recordings/{recording_id}/timeline")
def timeline(recording_id: str):
    require(store.get(recording_id)["state"] == "ready", "原视频尚未准备完成", 409)
    return media.timeline(recording_id)


@router.get("/recordings/{recording_id}/video")
def video(recording_id: str):
    require(store.get(recording_id)["state"] == "ready", "播放副本尚未准备完成", 409)
    return FileResponse(
        store.folder(recording_id) / "preview.mp4", media_type="video/mp4"
    )


@router.get("/recordings/{recording_id}/frame")
def frame(recording_id: str, index: int = Query(ge=0)):
    return Response(
        media.frame(recording_id, index),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/recordings/{recording_id}/prepare")
def prepare(recording_id: str):
    with store.lock(recording_id):
        meta = store.get(recording_id)
        require(
            meta["state"] == "failed" and meta.get("original_file"),
            "此录像无需重新准备",
            409,
        )
        store.update(recording_id, state="preparing")
        return workflow.submit(
            recording_id, "inspection_prepare", lambda p: media.prepare(recording_id, p)
        )


@router.post("/recordings/{recording_id}/prelabel")
def prelabel(recording_id: str, body: PrelabelOptions):
    require(store.get(recording_id)["state"] == "ready", "请等待原视频准备完成", 409)
    return workflow.submit(
        recording_id,
        "inspection_prelabel",
        lambda p: workflow.prelabel(recording_id, body, p),
    )


@router.put("/recordings/{recording_id}/annotation")
def edit(recording_id: str, body: InspectionEdit):
    return store.save(recording_id, body)


@router.post("/recordings/{recording_id}/apply-draft")
def apply_draft(recording_id: str, body: RevisionRequest):
    return workflow.apply_draft(recording_id, body.revision)


@router.post("/recordings/{recording_id}/legacy-import")
def import_legacy(recording_id: str, body: LegacyImport):
    return legacy.import_labels(recording_id, body)


@router.post("/manifests")
def manifest(body: ManifestRequest):
    return workflow.export_manifest(body)
