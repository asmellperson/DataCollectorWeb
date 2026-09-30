from uuid import uuid4
from app.inference.base import Detector
from app.core.config import DEVICE


class WorldDetector(Detector):
    provider = "yolo_world"

    def __init__(self, path):
        self.path = path
        self.model = None
        self.classes = []

    def load(self):
        if self.model is None:
            from ultralytics import YOLOWorld

            self.model = YOLOWorld(self.path)
            self.model.to(DEVICE)

    def set_classes(self, classes):
        self.load()
        if classes != self.classes:
            self.model.set_classes([c["name"] for c in classes])
            self.classes = [dict(c) for c in classes]

    def predict(self, image, confidence=0.25):
        result = self.model.predict(
            image, conf=confidence, device=DEVICE, verbose=False
        )[0]
        objects = []
        for box in result.boxes:
            cls = self.classes[int(box.cls.item())]
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
