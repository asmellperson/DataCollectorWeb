from uuid import uuid4
from app.inference.base import Detector
from app.core.config import DEVICE
from app.core.storage import require


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
        # 模型原始类别不覆盖；以名称显式映射到数据集的稳定类别 ID。
        by_name = {c["name"]: c for c in classes}
        self.mapping = {
            int(i): by_name[name]
            for i, name in self.model.names.items()
            if name in by_name
        }
        require(
            self.mapping,
            "模型类别与数据集类别没有同名项，请在数据集中添加需要的模型类别",
        )

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
