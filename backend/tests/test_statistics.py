"""Statistics use isolated files/SQLite, including corrupt annotation fixtures."""

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


class StatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        root = Path(
            cls.stack.enter_context(
                tempfile.TemporaryDirectory(prefix="dcw-statistics-")
            )
        )
        data_root = root / "datasets"
        data_root.mkdir()
        cls.stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "DATA_ROOT": str(data_root),
                    "STATE_ROOT": str(root / "state"),
                },
            )
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db import database
        from app.services import dataset_service, statistics_service
        from app.schemas.contracts import DatasetCreate
        from app.api.datasets import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        engine = create_engine(
            f"sqlite:///{root / 'test.sqlite3'}",
            connect_args={"check_same_thread": False},
        )
        cls.stack.callback(engine.dispose)
        database.Base.metadata.create_all(engine)
        cls.session = sessionmaker(engine, expire_on_commit=False)
        for module in (database, dataset_service, statistics_service):
            cls.stack.enter_context(patch.object(module, "Session", cls.session))
        cls.stack.enter_context(patch.object(dataset_service, "DATA_ROOT", data_root))
        cls.datasets, cls.stats = dataset_service, statistics_service
        cls.Sample, cls.DatasetCreate = database.Sample, DatasetCreate
        app = FastAPI()
        app.include_router(router)
        cls.client = cls.stack.enter_context(TestClient(app))

    def setUp(self):
        self.dataset = self.create()
        self.root = self.datasets.folder(self.dataset["id"])

    def create(self, task_type="detection"):
        return self.datasets.create(
            self.DatasetCreate(
                name="统计测试",
                task_type=task_type,
                classes=[]
                if task_type == "raw"
                else [
                    {"id": 0, "name": "人员"},
                    {"id": 9, "name": "车辆"},
                    {"id": 12, "name": "零标注类别"},
                ],
            )
        )

    def box(self, cid=0, bbox=None, **overrides):
        return {
            "id": uuid4().hex,
            "class_id": cid,
            "class_name": "旧名称",
            "bbox": bbox or [0, 0, 10, 10],
            "source": "manual",
            "confidence": None,
            **overrides,
        }

    def sample(self, objects=None, status="pending", dataset=None, **extra):
        dataset = dataset or self.dataset
        root = self.datasets.folder(dataset["id"])
        sid = uuid4().hex
        image = f"images/{sid}.jpg"
        ann = {
            "id": sid,
            "image": image,
            "width": 100,
            "height": 100,
            "status": status,
            "objects": objects or [],
            "classification": None,
            "source": {"type": "image", "filename": "测试.jpg"},
            "revision": 0,
            **extra,
        }
        (root / image).write_bytes(b"image existence fixture")
        (root / "annotations" / f"{sid}.json").write_text(
            json.dumps(ann), encoding="utf-8"
        )
        with self.session() as db:
            db.add(
                self.Sample(
                    id=sid,
                    dataset_id=dataset["id"],
                    image=image,
                    width=100,
                    height=100,
                    status=status,
                )
            )
            db.commit()
        return ann

    def result(self, status=None):
        return self.stats.statistics(self.dataset["id"], status)

    def test_counts_boxes_and_distinct_images_with_stable_noncontiguous_ids(self):
        first = self.sample(
            [self.box(), self.box(bbox=[20, 20, 30, 30]), self.box(9)], "approved"
        )
        second = self.sample([self.box(9)])
        self.sample([], "approved")
        self.sample([])
        result = self.result()
        self.assertEqual([c["id"] for c in result["classes"]], [0, 9, 12])
        person, vehicle, unused = result["classes"]
        self.assertEqual(
            (person["name"], person["box_count"], person["image_count"]), ("人员", 2, 1)
        )
        self.assertEqual(
            (person["box_share"], person["image_coverage"], person["average_boxes"]),
            (0.5, 0.25, 2),
        )
        self.assertEqual(
            (
                vehicle["box_count"],
                vehicle["image_count"],
                vehicle["approved"],
                vehicle["pending"],
            ),
            (2, 2, 1, 1),
        )
        self.assertEqual((unused["box_count"], unused["image_count"]), (0, 0))
        overview = result["overview"]
        self.assertEqual(
            (overview["images"], overview["boxes"], overview["images_with_labels"]),
            (4, 4, 2),
        )
        self.assertEqual(
            (overview["confirmed_negative"], overview["pending_empty"]), (1, 1)
        )
        self.assertEqual(
            (
                overview["average_boxes"],
                overview["median_boxes"],
                overview["max_boxes"],
            ),
            (1, 0.5, 3),
        )
        self.assertEqual(
            {s["id"] for s in result["samples"] if 9 in s["class_ids"]},
            {first["id"], second["id"]},
        )

    def test_status_filter_changes_every_distribution_and_denominator(self):
        self.sample([self.box()], "approved")
        self.sample([self.box(9), self.box(9)], "rejected")
        self.sample([])
        result = self.result("approved")
        self.assertEqual(result["scope"], {"status": "approved", "dataset_total": 3})
        self.assertEqual(result["overview"]["images"], 1)
        self.assertEqual(result["classes"][0]["image_coverage"], 1)
        self.assertEqual(result["classes"][1]["image_count"], 0)
        self.assertEqual(sum(r["count"] for r in result["distributions"]["sources"]), 1)
        self.assertEqual(len(result["samples"]), 1)
        self.assertEqual(self.result()["overview"]["boxes"], 3)

    def test_unknown_ids_and_invalid_geometry_are_excluded_without_losing_valid_boxes(
        self,
    ):
        self.sample(
            [
                self.box(),
                self.box(99),
                self.box(bbox=[-1, 0, 5, 5]),
                self.box(bbox=[0, 0, 101, 5]),
                self.box(bbox=[1, 1, 1, 4]),
                self.box(bbox=[0, 0, float("nan"), 4]),
                self.box(bbox=[False, 0, 5, 5]),
                self.box(cid=[0]),
                self.box(bbox=[0, 0, "5", 4]),
            self.box(bbox=[0, 0, 10**400, 4]),
                "bad",
            ]
        )
        result = self.result()
        self.assertEqual(result["overview"]["boxes"], 1)
        self.assertEqual(result["overview"]["invalid_records"], 10)
        self.assertEqual(result["overview"]["issue_images"], 1)
        self.assertEqual(result["samples"][0]["class_ids"], [0])
        self.assertFalse(result["samples"][0]["reviewable"])
        json.dumps(result, allow_nan=False)

    def test_missing_and_malformed_files_do_not_become_negative_samples(self):
        missing = self.sample([], "approved")
        (self.root / "annotations" / f"{missing['id']}.json").unlink()
        malformed = self.sample([], "approved")
        (self.root / "annotations" / f"{malformed['id']}.json").write_text("{")
        structure = self.sample([], "approved")
        (self.root / "annotations" / f"{structure['id']}.json").write_text("[]")
        image = self.sample([self.box()], "approved")
        (self.root / image["image"]).unlink()
        result = self.result()
        self.assertEqual(result["overview"]["confirmed_negative"], 0)
        self.assertEqual(result["overview"]["annotations_readable"], 1)
        self.assertEqual(result["overview"]["missing_images"], 1)
        self.assertEqual(result["overview"]["issue_images"], 4)
        self.assertEqual(
            sum(row["count"] for row in result["distributions"]["boxes_per_image"]), 1
        )

    def test_suspicious_boxes_are_reported_but_preserved_and_sizes_have_exact_boundaries(
        self,
    ):
        self.sample(
            [
                self.box(bbox=[0, 0, 1, 50]),
                self.box(bbox=[0, 0, 1, 50]),
                self.box(bbox=[0, 0, 10, 10]),
                self.box(bbox=[0, 0, 10, 100]),
            ]
        )
        result = self.result()
        cls = result["classes"][0]
        self.assertEqual((cls["small"], cls["medium"], cls["large"]), (2, 1, 1))
        self.assertEqual(result["overview"]["boxes"], 4)
        self.assertEqual(result["overview"]["invalid_records"], 0)
        issues = {q["code"]: q for q in result["quality"]}
        self.assertEqual(
            (issues["duplicate_box"]["count"], issues["tiny_box"]["count"]), (1, 2)
        )
        self.assertEqual(issues["elongated_box"]["image_count"], 1)

    def test_empty_dataset_returns_finite_zeroes_and_all_configured_classes(self):
        result = self.result()
        self.assertEqual(result["overview"]["images"], 0)
        self.assertEqual(len(result["classes"]), 3)
        self.assertEqual(result["classes"][0]["average_boxes"], 0)
        json.dumps(result, allow_nan=False)

    def test_refresh_reads_edits_and_does_not_write_annotations(self):
        ann = self.sample([self.box()])
        path = self.root / "annotations" / f"{ann['id']}.json"
        original = path.read_bytes()
        self.assertEqual(self.result()["overview"]["boxes"], 1)
        self.assertEqual(path.read_bytes(), original)
        ann["objects"].append(self.box(9))
        path.write_text(json.dumps(ann))
        self.assertEqual(self.result()["overview"]["boxes"], 2)

    def test_classification_and_raw_do_not_report_detection_negatives(self):
        for task_type in ("classification", "raw"):
            dataset = self.create(task_type)
            self.sample([], "approved", dataset=dataset)
            if task_type == "classification":
                self.sample(
                    [],
                    "approved",
                    dataset=dataset,
                    classification={"class_id": 0, "class_name": "人员"},
                )
                self.sample([], dataset=dataset, classification={"class_id": [99]})
            result = self.stats.statistics(dataset["id"])
            self.assertEqual(result["overview"]["boxes"], 0)
            self.assertEqual(result["overview"]["confirmed_negative"], 0)
            if task_type == "classification":
                self.assertEqual(result["classes"][0]["image_count"], 1)
                self.assertEqual(result["overview"]["issue_images"], 2)
            else:
                self.assertEqual(result["classes"], [])
                self.assertEqual(result["overview"]["empty_images"], 0)

    def test_metadata_mismatch_and_unexpected_labels_are_visible(self):
        self.sample([], "approved", width=200, classification={"class_id": 0})
        result = self.result()
        self.assertEqual(result["overview"]["confirmed_negative"], 0)
        self.assertEqual(
            result["samples"][0]["issues"],
            {"metadata_mismatch": 1, "unexpected_labels": 1},
        )
        self.sample([], "approved", width=200)
        self.assertEqual(self.result()["overview"]["confirmed_negative"], 0)

    def test_api_route_validation_no_cache_and_missing_dataset(self):
        self.sample([self.box()], "approved")
        url = f"/api/datasets/{self.dataset['id']}/statistics"
        response = self.client.get(url, params={"status": "approved"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["classes"][0]["box_count"], 1)
        self.assertEqual(
            self.client.get(url, params={"status": "invalid"}).status_code, 422
        )
        self.assertEqual(
            self.client.get("/api/datasets/missing/statistics").status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
