from uuid import uuid4
from app.inference.base import Detector
from app.core.config import DEVICE
from app.core.storage import require
from app.inference.targets import source_class_ids


class CustomDetector(Detector):
    provider = "custom_yolo"

    def __init__(self, path):
        self.path = path
        self.model = None
        self.mapping = {}

    def load(self):
        if self.model is None:
            from ultralytics import YOLO

            self.model = YOLO(self.path)
            require(self.model.task == "detect", "自定义模型必须是目标检测模型")
            self.model.to(DEVICE)

    def set_classes(self, classes):
        self.load()
        # Source IDs belong to the model; saved IDs/names belong to the dataset.
        names = {int(i): name for i, name in self.model.names.items()}
        by_name = {name: i for i, name in names.items()}
        mapping = {}
        for cls in classes:
            sources = source_class_ids(cls)
            if sources is None:
                sources = [by_name.get(cls["name"])]
            for source_id in sources:
                require(
                    source_id in names,
                    f"模型不支持所选类别 {cls['name']} 的检测映射"
                    + (f"（模型 ID {source_id}）" if source_id is not None else "")
                    + "；请填写正确的模型类别 ID、按模型名称匹配或更换模型",
                )
                require(
                    source_id not in mapping or mapping[source_id]["id"] == cls["id"],
                    f"模型类别 {source_id} 不能同时映射到多个标注类别",
                )
                mapping[source_id] = dict(cls)
        require(mapping, "请至少选择一个自动标注类别")
        self.mapping = mapping

    def predict(self, image, confidence=0.25):
        result = self.model.predict(
            image,
            conf=confidence,
            device=DEVICE,
            classes=list(self.mapping),
            verbose=False,
        )[0]
        objects = []
        for box in result.boxes:
            cls = self.mapping[int(box.cls.item())]
            objects.append(
                {
                    "id": "obj_" + uuid4().hex,
                    "class_id": cls["id"],
                    "class_name": cls["name"],
                    "bbox": [float(v) for v in box.xyxy[0].tolist()],
                    "confidence": float(box.conf.item()),
                    "source": self.provider,
                }
            )
        return objects
