from fastapi import APIRouter
from app.core.config import MODEL_ROOT, WORLD_MODEL
from app.core.storage import require
from app.inference import manager
from app.schemas.contracts import Contract, LabelOptions
from app.services import (
    annotation_service as annotations,
    dataset_service as datasets,
    task_service as tasks,
)

router = APIRouter(tags=["模型与自动预标"])


@router.get("/api/models")
def models():
    return {
        "world_model": WORLD_MODEL,
        "custom_models": [str(p) for p in MODEL_ROOT.glob("*.pt")],
    }


class InspectModel(Contract):
    path: str


@router.post("/api/models/inspect")
def inspect(body: InspectModel):
    path = manager.resolve("custom_yolo", body.path)

    def work(progress):
        progress.update(total=1)
        with manager.lock:
            names = manager.get("custom_yolo", path).model.names
        progress.advance()
        return {"classes": [{"id": i, "name": n} for i, n in names.items()]}

    return tasks.submit("model_inspect", None, work)


@router.post("/api/datasets/{dataset_id}/auto-label")
def auto_label(dataset_id: str, body: LabelOptions):
    require(
        datasets.get(dataset_id)["task_type"] == "detection",
        "自动预标仅适用于 detection 数据集",
    )
    manager.resolve(body.provider, body.model_path)
    return tasks.submit(
        "auto_label", dataset_id, lambda p: annotations.auto_label(dataset_id, body, p)
    )
