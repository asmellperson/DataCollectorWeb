"""Real upload/edit/export coverage, with storage isolated from user datasets."""

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch


class LabelImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.root = Path(
            cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="dcw-labels-"))
        )
        data_root = cls.root / "datasets"
        data_root.mkdir()
        cls.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "DATA_ROOT": str(data_root),
                    "STATE_ROOT": str(cls.root / "state"),
                },
            )
        )
        global \
            np, \
            schemas, \
            datasets, \
            annotations, \
            labels, \
            exports, \
            manager, \
            api, \
            HTTPException
        import numpy as np
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import database
        from app.schemas import contracts as schemas
        from app.services import (
            dataset_service as datasets,
            annotation_service as annotations,
        )
        from app.services import (
            label_import_service as labels,
            export_service as exports,
        )
        from app.services import task_service as tasks
        from app.inference import manager
        from app.api import annotations as api, datasets as dataset_api

        engine = create_engine(f"sqlite:///{cls.root / 'test.sqlite3'}")
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        session = sessionmaker(engine, expire_on_commit=False)
        for module in (database, datasets, annotations, exports, tasks):
            cls.stack.enter_context(patch.object(module, "Session", session))
        cls.stack.enter_context(patch.object(datasets, "DATA_ROOT", data_root))
        cls.stack.enter_context(patch.object(api, "STATE_ROOT", cls.root / "state"))
        app = FastAPI()
        app.include_router(dataset_api.router)
        app.include_router(api.router)
        cls.client = cls.stack.enter_context(TestClient(app))

    def setUp(self):
        self.dataset = datasets.create(
            schemas.DatasetCreate(
                name="标签导入",
                task_type="detection",
                classes=[{"id": 4, "name": "person"}, {"id": 9, "name": "car"}],
            )
        )
        self.did = self.dataset["id"]
        self.source_classes = [{"id": 0, "name": "person"}, {"id": 1, "name": "car"}]
        self.progress = Mock()
        self.results = []

        def submit(kind, dataset_id, work, cleanup=None):
            try:
                self.results.append(work(self.progress))
            finally:
                if cleanup:
                    cleanup()
            return {"task_id": "test-import"}

        self.enterContext(patch.object(api.tasks, "submit", side_effect=submit))
        self.image = annotations.encode(np.zeros((100, 200, 3), dtype=np.uint8))

    def preview(self, **kwargs):
        body = {
            "image_names": ["001.jpg"],
            "labels": {"001.txt": "0 .5 .5 .4 .6"},
            "classes_text": "0 person\n1 car",
        }
        body.update(kwargs)
        return self.client.post("/api/datasets/import-labels/preview", json=body)

    def upload(self, names=None, contents=None, **options):
        names = ["001.jpg"] if names is None else names
        contents = {"001.txt": "0 .5 .5 .4 .6"} if contents is None else contents
        files = [("files", (name, self.image, "image/jpeg")) for name in names]
        files += [
            ("labels", (name, value, "text/plain")) for name, value in contents.items()
        ]
        return self.client.post(
            f"/api/datasets/{self.did}/images/upload",
            files=files,
            data={
                "options": json.dumps(
                    {"source_classes": self.source_classes, **options}
                )
            },
        )

    def samples(self):
        return [
            annotations.get(self.did, s["id"])
            for s in annotations.list_samples(self.did)["items"]
        ]

    def test_manual_classes_without_yaml_and_sparse_yaml_ids_create_dataset(self):
        manual = self.preview(
            classes_text="7 人员\n2 car", labels={"001.txt": "7 .5 .5 .4 .6"}
        )
        self.assertEqual(manual.status_code, 200, manual.text)
        self.assertEqual(
            manual.json()["classes"],
            [{"id": 7, "name": "人员"}, {"id": 2, "name": "car"}],
        )
        yaml = self.preview(
            classes_text="",
            yaml_text="train: /ignored/path\nnames:\n  7: 人员\n  2: car\n",
            labels={"001.txt": "7 .5 .5 .4 .6"},
        )
        self.assertEqual(yaml.json()["classes"], manual.json()["classes"])
        created = self.client.post(
            "/api/datasets",
            json={
                "name": "来自 YAML",
                "task_type": "detection",
                "classes": yaml.json()["classes"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["classes"], manual.json()["classes"])
        sequence = self.preview(classes_text="", yaml_text="names: [person, car]")
        self.assertEqual(sequence.json()["classes"], self.source_classes)

    def test_invalid_class_definitions_are_rejected(self):
        for text in (
            "",
            "person",
            "0 person\n0 car",
            "0 person\n1 person",
            "-1 person",
            "10000 person",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.preview(classes_text=text).status_code, 400)
        for yaml in (
            "names: {0: person, 0: car}",
            "names: {true: person}",
            "names: {0: 123}",
            "names: {0: person, '00': car}",
            "names: []",
            "names: [person, person]",
            "train: images",
            "names: !!python/object/apply:os.system ['false']",
            "names: [",
        ):
            with self.subTest(yaml=yaml):
                self.assertEqual(
                    self.preview(classes_text="", yaml_text=yaml).status_code, 400
                )
        self.assertEqual(self.preview(yaml_text="names: [person]").status_code, 400)

    def test_subdirectories_missing_empty_and_unmatched_labels(self):
        response = self.preview(
            image_names=["train/001.jpg", "val/001.png", "empty.jpg", "missing.jpg"],
            labels={
                "train/001.txt": "0 .5 .5 .4 .6",
                "val/001.TXT": "1 .5 .5 .2 .2",
                "empty.txt": "\ufeff \n",
                "other.txt": "",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(
            {
                k: result[k]
                for k in ("matched", "empty", "missing", "unmatched", "error_count")
            },
            {"matched": 3, "empty": 1, "missing": 1, "unmatched": 1, "error_count": 0},
        )
        collision = self.preview(image_names=["001.jpg", "001.png"]).json()
        self.assertEqual(collision["error_count"], 1)
        self.assertEqual(self.preview(image_names=["../001.jpg"]).status_code, 400)
        self.assertEqual(self.preview(labels={"/tmp/001.txt": ""}).status_code, 400)

    def test_bad_labels_are_reported_and_upload_writes_nothing(self):
        for text in (
            "9 .5 .5 .2 .2",
            "0 nan .5 .2 .2",
            "0 inf .5 .2 .2",
            "0 .5 .5 0 .2",
            "0 .1 .5 .8 .2",
            "0 .5 .5 .2 .2 .8",
            "0.0 .5 .5 .2 .2",
            "0 a .5 .2 .2",
        ):
            with self.subTest(text=text):
                report = self.preview(labels={"001.txt": text}).json()
                self.assertEqual(report["error_count"], 1)
                self.assertIn("001.txt:1", report["errors"][0])
                upload = self.upload(contents={"001.txt": text})
                self.assertEqual(upload.status_code, 400, upload.text)
                self.assertEqual(datasets.get(self.did)["total"], 0)
        invalid_encoding = self.upload(contents={"001.txt": b"\xff"})
        self.assertEqual(invalid_encoding.status_code, 400)

    def test_upload_remaps_classes_preserves_original_and_supports_edit_and_export(
        self,
    ):
        result = self.upload()
        self.assertEqual(result.status_code, 200, result.text)
        ann = self.samples()[0]
        self.assertEqual(ann["status"], "pending")
        self.assertEqual(ann["source"]["label_status"], "labeled")
        self.assertEqual(ann["objects"][0]["class_id"], 4)
        self.assertEqual(ann["objects"][0]["bbox"], [60.0, 20.0, 140.0, 80.0])
        self.assertEqual(
            (datasets.folder(self.did) / ann["image"]).read_bytes(), self.image
        )
        obj = {**ann["objects"][0], "bbox": [20, 10, 100, 90], "class_id": 9}
        edited = self.client.put(
            f"/api/datasets/{self.did}/samples/{ann['id']}/annotation",
            json={"revision": 0, "objects": [obj]},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["objects"][0]["class_name"], "car")
        result = exports.export(
            self.did,
            schemas.ExportOptions(split_mode="none", include_pending=True),
            self.progress,
            yolo=True,
        )
        exported = (
            (Path(result["path"]) / "labels" / f"{ann['id']}.txt").read_text().split()
        )
        self.assertEqual(exported[0], "9")
        self.assertEqual([float(v) for v in exported[1:]], [0.3, 0.5, 0.4, 0.8])
        self.assertFalse(any((self.root / "state" / "uploads").iterdir()))

    def test_mapping_uses_names_not_same_numeric_id_and_can_be_explicit(self):
        self.source_classes = [{"id": 4, "name": "car"}]
        self.assertEqual(
            self.upload(contents={"001.txt": "4 .5 .5 .2 .2"}).status_code, 200
        )
        self.assertEqual(self.samples()[0]["objects"][0]["class_id"], 9)
        self.source_classes = [{"id": 0, "name": "pedestrian"}]
        self.assertEqual(self.upload().status_code, 400)
        self.assertEqual(self.upload(import_class_mapping={0: 4}).status_code, 200)
        self.assertEqual(self.upload(import_class_mapping={0: 999}).status_code, 400)
        self.assertEqual(self.upload(auto_label="yolo_world").status_code, 422)

    def test_empty_labels_and_existing_boxes_are_protected_from_auto_label(self):
        result = self.upload(
            names=["box.jpg", "empty.jpg", "missing.jpg"],
            contents={"box.txt": "0 .5 .5 .4 .6", "empty.txt": ""},
        )
        self.assertEqual(result.status_code, 200, result.text)
        samples = {ann["source"]["label_status"]: ann for ann in self.samples()}
        self.assertEqual(set(samples), {"labeled", "empty", "missing"})
        with (
            patch.object(manager, "get"),
            patch.object(manager, "predict", return_value=[]) as predict,
        ):
            annotations.auto_label(self.did, schemas.LabelOptions(), self.progress)
            self.assertEqual(predict.call_count, 1)
            predict.reset_mock()
            annotations.auto_label(
                self.did, schemas.LabelOptions(overwrite=True), self.progress
            )
            self.assertEqual(predict.call_count, 3)

    def test_image_only_upload_keeps_original_behavior(self):
        result = self.upload(contents={}, source_classes=None)
        self.assertEqual(result.status_code, 200, result.text)
        ann = self.samples()[0]
        self.assertNotIn("label_status", ann["source"])
        self.assertEqual(ann["objects"], [])
        self.assertEqual(self.upload(source_classes=None).status_code, 400)

    def test_batches_containing_only_missing_labels_are_valid(self):
        result = self.upload(contents={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.samples()[0]["source"]["label_status"], "missing")


if __name__ == "__main__":
    unittest.main()
