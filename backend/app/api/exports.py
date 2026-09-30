from fastapi import APIRouter
from fastapi.responses import FileResponse
from app.core.storage import require
from app.schemas.contracts import ExportOptions
from app.services import (
    dataset_service as datasets,
    export_service as service,
    task_service as tasks,
)

router = APIRouter(prefix="/api/datasets/{dataset_id}", tags=["数据集导出"])


@router.post("/export/preview")
def preview(dataset_id: str, body: ExportOptions):
    return service.preview(dataset_id, body)


@router.post("/export/yolo")
def yolo(dataset_id: str, body: ExportOptions):
    require(
        datasets.get(dataset_id)["task_type"] == "detection",
        "仅 detection 数据集支持 YOLO Detect 导出",
    )
    return tasks.submit(
        "export", dataset_id, lambda p: service.export(dataset_id, body, p, yolo=True)
    )


@router.post("/export")
def generic(dataset_id: str, body: ExportOptions):
    dataset = datasets.get(dataset_id)
    return tasks.submit(
        "export",
        dataset_id,
        lambda p: service.export(
            dataset_id, body, p, yolo=dataset["task_type"] == "detection"
        ),
    )


@router.get("/exports")
def list_exports(dataset_id: str):
    root = datasets.folder(dataset_id) / "exports"
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "download_url": f"/api/datasets/{dataset_id}/exports/{p.name}",
        }
        for p in sorted(root.glob("*.zip"), reverse=True)
        if not p.name.startswith(".")
    ]


@router.get("/exports/{name}")
def download(dataset_id: str, name: str):
    root = datasets.folder(dataset_id) / "exports"
    path = (root / name).resolve()
    require(
        path.parent == root.resolve()
        and name.endswith(".zip")
        and not name.startswith("."),
        "导出文件名无效",
    )
    require(path.is_file(), "导出文件不存在，可能已因删除样本而清理", 404)
    return FileResponse(path, filename=name, media_type="application/zip")
