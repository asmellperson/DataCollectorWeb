"""保留原画面和真实时间缺口，兼容局部 PTS 冲突并核对播放副本。"""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.security_annotation import router
from app.core.storage import read_json
from app.schemas.security_annotation import InspectionEdit, ManifestRequest, PrelabelOptions
from app.services.security_annotation import media, store, workflow


class Progress:
    def check(self):
        pass

    def update(self, **values):
        pass

    def advance(self):
        pass


class TimestampTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, "ROOT", self.root / "data"))

    def index(self, times, fps="25/1"):
        data = {
            "frames": [{"best_effort_timestamp_time": str(value), "pkt_duration_time": "0.04"} for value in times],
            "streams": [{"width": 64, "height": 48, "avg_frame_rate": fps, "r_frame_rate": "25/1"}],
        }
        with patch.object(media, "run_process", return_value=json.dumps(data).encode()):
            return media.index_video(self.root / "source.mkv", Progress())

    def source(self, *, duplicate=True, offset=0, duplicate_frame=3, gap=0):
        path = self.root / "source.mkv"
        filters = f"setpts=PTS+{offset}/TB"
        if duplicate:
            filters += f",setpts=if(eq(N\\,{duplicate_frame})\\,PTS-1\\,PTS)"
        if gap:
            filters += f",settb=1/1000,setpts=PTS+gte(N\\,2)*{round(gap * 1000)}"
        subprocess.run([
            media.executable("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "testsrc=size=64x48:rate=25:duration=0.4", "-vf", filters,
            "-c:v", "libx264", "-bf", "0", "-pix_fmt", "yuv420p",
            "-enc_time_base", "1:1000", "-vsync", "0", str(path),
        ], check=True, capture_output=True)
        return path

    def test_normal_variable_timestamps_and_real_gaps_remain_unchanged(self):
        result = self.index([100, 100.04, 100.08, 100.88, 100.92])
        self.assertEqual(result["times_sec"], [0, 0.04, 0.08, 0.88, 0.92])
        self.assertEqual(result["timestamp_repair_count"], 0)
        self.assertEqual(result["gaps"], [{"start_sec": 0.08, "end_sec": 0.88}])
        self.assertEqual(result["time_basis"], "source_pts_seconds_from_first_frame")

    def test_duplicate_pts_preserve_every_frame_and_the_original_timestamp_mapping(self):
        result = self.index([10, 10.04, 10.08, 10.08, 10.16])
        self.assertEqual(result["times_sec"], [0, 0.04, 0.08, 0.080001, 0.16])
        self.assertEqual(result["source_times_sec"], [0, 0.04, 0.08, 0.08, 0.16])
        self.assertEqual(result["frame_count"], 5)
        self.assertEqual(result["timestamp_repair_count"], 1)
        self.assertEqual(result["timestamp_repairs"], [
            {"start_frame": 2, "end_frame": 3, "start_sec": 0.08, "end_sec": 0.16}
        ])

    def test_small_backward_jitter_is_local_and_does_not_shift_the_healthy_tail(self):
        result = self.index([0, 0.04, 0.039, 0.12, 0.16])
        self.assertEqual(result["times_sec"], [0, 0.04, 0.040001, 0.12, 0.16])
        self.assertEqual(result["source_times_sec"][2], 0.039)

    def test_unknown_average_rate_uses_the_stream_rate_for_diagnostics(self):
        result = self.index([0, 0.04, 0.04, 0.12], fps="0/0")
        self.assertEqual(result["fps_hint"], 25)
        self.assertEqual(result["timestamp_repair_count"], 1)

    def test_clock_reset_and_sustained_stuck_clock_are_not_silently_retimed(self):
        for times in ([0, 2, 0.01], [0] * 30):
            with self.subTest(times=times):
                with self.assertRaises(HTTPException) as failure:
                    self.index(times)
                self.assertIn("原文件已保留", failure.exception.detail)
                self.assertIn("超出轻微抖动修复范围", failure.exception.detail)

    def test_non_finite_timestamps_remain_invalid(self):
        with self.assertRaises(HTTPException) as failure:
            self.index([0, float("nan")])
        self.assertIn("时间戳无效", failure.exception.detail)

    def test_preview_with_missing_frames_or_shifted_time_is_not_publishable(self):
        index = {"frame_count": 3, "times_sec": [0, 0.04, 0.08]}
        for preview in (
            {"frame_count": 2, "times_sec": [0, 0.08]},
            {"frame_count": 3, "times_sec": [0, 0.05, 0.08]},
        ):
            with self.assertRaises(HTTPException):
                media.verify_preview(index, preview)

    def test_real_duplicate_file_can_be_prepared_without_overwriting_or_dropping_source_frames(self):
        source = self.source()
        original_bytes = source.read_bytes()
        meta = store.create("摄像头原码流", source_type="rtsp")
        recording_id = meta["id"]
        media.copy_source(recording_id, source, Progress())
        media.prepare(recording_id, Progress())
        current = store.get(recording_id)
        folder = store.folder(recording_id)
        index = media.timeline(recording_id)
        self.assertEqual(current["state"], "ready")
        self.assertEqual(current["frame_count"], 10)
        self.assertEqual(current["timestamp_repair_count"], 1)
        self.assertEqual((folder / "original.mkv").read_bytes(), original_bytes)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(current["sha256"], hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(index["source_times_sec"][3], 0.08)
        preview = media.index_video(folder / "preview.mp4", Progress(), allow_repairs=False)
        media.verify_preview(index, preview)
        capture = media.cv2.VideoCapture(str(source))
        expected = []
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                expected.append(media.encode_frame(frame))
        finally:
            capture.release()
        self.assertEqual([media.frame(recording_id, value) for value in range(10)], expected)

    def test_real_nonzero_start_is_normalized_identically_in_timeline_and_preview(self):
        source = self.source(duplicate=False, offset=7)
        meta = store.create("非零起始时间", source_type="file")
        media.copy_source(meta["id"], source, Progress())
        media.prepare(meta["id"], Progress())
        index = media.timeline(meta["id"])
        self.assertEqual(index["times_sec"], [value / 25 for value in range(10)])
        self.assertEqual(index["timestamp_repair_count"], 0)

    def test_real_trailing_duplicate_is_preserved_in_the_playback_copy(self):
        source = self.source(duplicate_frame=9)
        meta = store.create("停止时末帧时间戳重复", source_type="rtsp")
        media.copy_source(meta["id"], source, Progress())
        media.prepare(meta["id"], Progress())
        index = media.timeline(meta["id"])
        self.assertEqual(index["frame_count"], 10)
        self.assertEqual(index["source_times_sec"][-2:], [0.32, 0.32])
        self.assertEqual(index["times_sec"][-2:], [0.32, 0.320001])

    def test_real_variable_frame_preview_keeps_its_timestamp_gap_and_all_frames(self):
        source = self.source(duplicate=False, gap=0.7)
        meta = store.create("可变帧率与时间缺口", source_type="file")
        media.copy_source(meta["id"], source, Progress())
        media.prepare(meta["id"], Progress())
        index = media.timeline(meta["id"])
        self.assertEqual(index["times_sec"][2], 0.78)
        self.assertEqual(index["frame_count"], 10)
        self.assertEqual(len(index["gaps"]), 1)
        self.assertEqual(index["timestamp_repair_count"], 0)

    def test_retry_endpoint_reuses_the_saved_original_and_exposes_repair_ranges(self):
        source = self.source()
        meta = store.create("上次准备失败", source_type="rtsp", state="failed")
        media.copy_source(meta["id"], source, Progress())
        app = FastAPI()
        app.include_router(router)

        def submit(recording_id, kind, callback):
            callback(Progress())
            return {"task_id": "test-prepare"}

        with TestClient(app) as client, patch.object(workflow, "submit", side_effect=submit):
            result = client.post(f"/api/security-annotations/recordings/{meta['id']}/prepare")
            self.assertEqual(result.status_code, 200, result.text)
            detail = client.get(f"/api/security-annotations/recordings/{meta['id']}").json()
        self.assertEqual(detail["recording"]["state"], "ready")
        self.assertEqual(detail["recording"]["timestamp_repairs"][0]["start_sec"], 0.08)

    def test_training_manifest_excludes_repaired_intervals_even_if_manually_approved(self):
        source = self.source()
        meta = store.create("含异常与正常轮次", source_type="file")
        recording_id = meta["id"]
        media.copy_source(recording_id, source, Progress())
        media.prepare(recording_id, Progress())
        content = {
            "persons": [{"id": "subject", "role": "subject"}],
            "rounds": [{"id": name, "subject_id": "subject", "start_sec": start, "end_sec": end,
                "inspection": "absent", "quality": "usable", "boundary_uncertain": False,
                "review_status": "approved", "events": []}
                for name, start, end in (("bad", 0.04, 0.2), ("good", 0.24, 0.36))],
        }
        store.save(recording_id, InspectionEdit(revision=0, **content))
        result = workflow.export_manifest(ManifestRequest(recording_ids=[recording_id], val_ratio=0))
        rows = [json.loads(line) for line in (Path(result["path"]) / "train.jsonl").read_text().splitlines()]
        self.assertEqual([row["round"]["id"] for row in rows], ["good"])
        self.assertEqual(result["skipped_rounds"], 1)
        self.assertEqual(rows[0]["time_basis"], "source_pts_with_local_monotonic_repairs")
        self.assertEqual(rows[0]["video_path"], str(store.folder(recording_id) / "original.mkv"))
        self.assertEqual(read_json(Path(result["path"]) / "snapshot.json")["recordings"][0]["metadata"]["timestamp_repair_count"], 1)

    def test_model_rounds_over_timestamp_conflicts_are_marked_uncertain(self):
        meta = {"duration_sec": 1, "frame_count": 25,
                "timestamp_repairs": [{"start_sec": 0.08, "end_sec": 0.16}]}
        proposed = {"persons": [], "rounds": [{"id": "w_round", "start_sec": 0.04,
                    "end_sec": 0.2, "inspection": "absent", "quality": "usable", "boundary_uncertain": False}]}
        result = workflow.merge_window({"persons": [], "rounds": []}, proposed, 0, 1, "w_", set(), meta)
        self.assertEqual(result["rounds"][0]["quality"], "uncertain")
        self.assertTrue(result["rounds"][0]["boundary_uncertain"])


if __name__ == "__main__":
    unittest.main()
