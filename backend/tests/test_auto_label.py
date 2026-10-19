import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


def setUpModule():
    # 应用首次导入就会打开数据库，先隔离目录，避免测试触碰真实样本。
    global workspace, environment, annotations, datasets, tasks, manager
    global Session, Task, engine, DatasetCreate, LabelOptions, np
    workspace = tempfile.TemporaryDirectory(prefix="dcw-auto-label-test-")
    root = Path(workspace.name)
    environment = patch.dict(
        os.environ,
        {"DATA_ROOT": str(root / "datasets"), "STATE_ROOT": str(root / "config")},
    )
    environment.start()
    import numpy as np
    from app.db.database import Session, Task, engine
    from app.inference import manager
    from app.schemas.contracts import DatasetCreate, LabelOptions
    from app.services import annotation_service as annotations
    from app.services import dataset_service as datasets
    from app.services import task_service as tasks


def tearDownModule():
    engine.dispose()
    environment.stop()
    workspace.cleanup()


class AutoLabelTests(unittest.TestCase):
    def setUp(self):
        self.dataset = datasets.create(
            DatasetCreate(
                name="置信度重标测试",
                task_type="detection",
                classes=[{"id": 0, "name": "person"}],
            )
        )["id"]
        self.image = np.zeros((32, 32, 3), dtype=np.uint8)
        self.high = self.box("high", 0.8)
        self.low = self.box("low", 0.15)
        self.loader = self.enterContext(patch.object(manager, "get"))

    def box(self, name, confidence, source="yolo_world"):
        return {
            "id": name,
            "class_id": 0,
            "class_name": "person",
            "bbox": [1.0, 1.0, 20.0, 20.0],
            "confidence": confidence,
            "source": source,
        }

    def sample(self, objects=None):
        return annotations.save_sample(
            self.dataset, self.image, {"type": "image"}, objects=objects
        )

    def run_label(self, **options):
        task_id = uuid4().hex
        with Session() as db:
            db.add(Task(id=task_id, kind="auto_label", dataset_id=self.dataset))
            db.commit()
        tasks.events[task_id] = threading.Event()
        self.addCleanup(tasks.events.pop, task_id, None)
        progress = tasks.Progress(task_id)
        result = annotations.auto_label(self.dataset, LabelOptions(**options), progress)
        progress.update(status="completed", result=json.dumps(result))
        return tasks.get(task_id)

    def assert_counts(self, task, *, success=0, failed=0, skipped=0):
        self.assertEqual(task["success"], success)
        self.assertEqual(task["failed"], failed)
        self.assertEqual(task["skipped"], skipped)
        self.assertEqual(task["processed"], success + failed + skipped)
        self.assertEqual(task["processed"], task["total"])

    def test_lower_threshold_requires_opt_in_and_replaces_predictions(self):
        sample = self.sample()

        def predict(provider, path, classes, image, confidence):
            return [
                box.copy()
                for box in (self.high, self.low)
                if box["confidence"] >= confidence
            ]

        with patch.object(manager, "predict", side_effect=predict) as inference:
            first = self.run_label(confidence=0.25, sample_ids=[sample["id"]])
            self.assert_counts(first, success=1)
            before = annotations.get(self.dataset, sample["id"])
            self.assertEqual(before["objects"], [self.high])
            inference.reset_mock()

            skipped = self.run_label(confidence=0.1, sample_ids=[sample["id"]])
            self.assert_counts(skipped, skipped=1)
            self.assertEqual(skipped["error"], "")
            inference.assert_not_called()
            self.assertEqual(annotations.get(self.dataset, sample["id"]), before)

            updated = self.run_label(
                confidence=0.1, overwrite=True, sample_ids=[sample["id"]]
            )
            self.assert_counts(updated, success=1)
            self.assertEqual(inference.call_args.args[-1], 0.1)
            after = annotations.get(self.dataset, sample["id"])
            self.assertEqual(after["objects"], [self.high, self.low])
            self.assertEqual(after["revision"], before["revision"] + 1)

    def test_default_preserves_manual_boxes(self):
        sample = self.sample([self.box("manual", None, "manual")])
        with patch.object(manager, "predict") as inference:
            result = self.run_label(confidence=0.1)
        self.assert_counts(result, skipped=1)
        self.assertEqual(result["error"], "")
        inference.assert_not_called()
        self.assertEqual(annotations.get(self.dataset, sample["id"]), sample)

    def test_batch_distinguishes_success_skip_and_real_failure(self):
        self.sample([self.high])
        self.sample()
        self.sample()
        with patch.object(
            manager, "predict", side_effect=[[self.low], RuntimeError("推理异常")]
        ):
            with self.assertLogs(annotations.log, level="ERROR"):
                result = self.run_label(confidence=0.1)
        self.assert_counts(result, success=1, skipped=1, failed=1)
        self.assertIn("推理异常", result["error"])

    def test_overwrite_does_not_touch_approved_samples(self):
        approved = self.sample([self.high])
        annotations.set_status(self.dataset, approved["id"], "approved")
        before = annotations.get(self.dataset, approved["id"])
        pending = self.sample([self.high])
        with patch.object(manager, "predict", return_value=[]) as inference:
            result = self.run_label(confidence=0.1, overwrite=True)
        self.assert_counts(result, success=1)
        inference.assert_called_once()
        self.assertEqual(annotations.get(self.dataset, approved["id"]), before)
        self.assertEqual(annotations.get(self.dataset, pending["id"])["objects"], [])

    def test_overwrite_preserves_edits_made_during_inference(self):
        sample = self.sample([self.high])
        manual = self.box("manual", None, "manual")

        def edit_during_inference(*args):
            current = annotations.get(self.dataset, sample["id"])
            current["objects"] = [manual]
            annotations.persist(self.dataset, current)
            return [self.low]

        with patch.object(manager, "predict", side_effect=edit_during_inference):
            with self.assertLogs(annotations.log, level="ERROR"):
                result = self.run_label(confidence=0.1, overwrite=True)
        self.assert_counts(result, failed=1)
        self.assertIn("已保留人工修改", result["error"])
        self.assertEqual(
            annotations.get(self.dataset, sample["id"])["objects"], [manual]
        )


if __name__ == "__main__":
    unittest.main()
