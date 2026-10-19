"""连续视频、大模型初标与人工版本边界的回归测试；不调用真实模型。"""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.security_annotation import router
from app.core.storage import read_json, write_json
from app.schemas.security_annotation import (
    InspectionContent,
    InspectionEdit,
    LegacyImport,
    ManifestRequest,
    PrelabelOptions,
)
from app.services.security_annotation import (
    legacy,
    media,
    recorder,
    store,
    vlm,
    workflow,
)


class Progress:
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.cancelled = False

    def check(self):
        if self.cancelled:
            raise RuntimeError("cancelled")

    def update(self, **values):
        self.__dict__.update(values)

    def advance(self):
        self.processed += 1


def completion(value, finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {"content": json.dumps(value, ensure_ascii=False)},
            }
        ]
    }


class SecurityAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dcw-security-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(patch.object(store, "ROOT", self.root / "data"))
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def ready(self, group="", digest="", duration=2):
        meta = store.create(
            "测试连续录像", group, source_type="file", original_file="original.mp4"
        )
        rid = meta["id"]
        (store.folder(rid) / "original.mp4").write_bytes(b"video")
        write_json(
            store.folder(rid) / "timeline.json",
            {"times_sec": [i / 25 for i in range(int(duration * 25))]},
        )
        return store.update(
            rid,
            state="ready",
            frame_count=int(duration * 25),
            duration_sec=duration,
            fps_hint=25,
            width=64,
            height=48,
            sha256=digest or rid,
        )

    def content(self, status="pending", observation="present", quality="usable"):
        return {
            "persons": [
                {"id": "staff", "role": "inspector", "description": "安检员"},
                {"id": "subject", "role": "subject", "description": "受检人"},
            ],
            "rounds": [
                {
                    "id": "round",
                    "subject_id": "subject",
                    "inspector_id": "staff",
                    "start_sec": 0.2,
                    "end_sec": 1.4,
                    "inspection": observation,
                    "quality": quality,
                    "review_status": status,
                    "events": []
                    if observation != "present"
                    else [
                        {
                            "id": "scan",
                            "action": "扫描",
                            "region": "left_hand",
                            "actor_id": "staff",
                            "start_sec": 0.24,
                            "end_sec": 0.36,
                            "evidence_frames": [6, 8],
                        }
                    ],
                }
            ],
        }

    def save(self, meta, **kwargs):
        return store.save(
            meta["id"],
            InspectionEdit(
                revision=store.annotation(meta["id"])["revision"],
                **self.content(**kwargs),
            ),
        )

    def test_fast_action_is_preserved_and_revision_conflict_keeps_manual_work(self):
        meta = self.ready()
        doc = self.save(meta)
        self.assertAlmostEqual(doc["rounds"][0]["events"][0]["end_sec"] - 0.24, 0.12)
        before = copy.deepcopy(doc)
        with self.assertRaises(HTTPException) as failure:
            store.save(meta["id"], InspectionEdit(revision=0, **self.content()))
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(store.annotation(meta["id"]), before)
        self.assertEqual(
            len(list((store.folder(meta["id"]) / "revisions").glob("*.json"))), 1
        )

    def test_unknown_is_persistable_and_does_not_require_forced_negative(self):
        meta = self.ready()
        doc = self.save(
            meta, observation="uncertain", quality="uncertain", status="approved"
        )
        self.assertEqual(doc["rounds"][0]["inspection"], "uncertain")
        with self.assertRaises(HTTPException):
            workflow.export_manifest(
                ManifestRequest(recording_ids=[meta["id"]], val_ratio=0)
            )

    def test_cross_person_and_out_of_video_references_are_rejected(self):
        meta = self.ready()
        content = self.content()
        content["rounds"][0]["inspector_id"] = "subject"
        with self.assertRaises(ValidationError):
            InspectionEdit(revision=0, **content)
        content = self.content()
        content["rounds"][0]["events"][0]["evidence_frames"] = [100]
        with self.assertRaises(HTTPException):
            store.save(meta["id"], InspectionEdit(revision=0, **content))
        content = self.content()
        content["rounds"][0]["end_sec"] = 3
        with self.assertRaises(HTTPException):
            store.save(meta["id"], InspectionEdit(revision=0, **content))

    def test_action_overlap_is_rejected_but_other_regions_can_overlap(self):
        content = self.content()
        event = content["rounds"][0]["events"][0]
        second = {**event, "id": "second"}
        content["rounds"][0]["events"].append(second)
        with self.assertRaises(ValidationError):
            InspectionContent.model_validate(content)
        second["region"] = "right_hand"
        self.assertEqual(
            len(InspectionContent.model_validate(content).rounds[0].events), 2
        )

    def test_explicit_absence_conflicts_with_observed_inspection_events(self):
        content = self.content()
        content["rounds"][0]["inspection"] = "absent"
        with self.assertRaises(ValidationError):
            InspectionContent.model_validate(content)

    def test_window_continuation_requires_same_subject_and_preserves_prior_actions(
        self,
    ):
        meta = self.ready(duration=5)
        proposed = self.content()
        for person in proposed["persons"]:
            person["id"] = "w00000_" + person["id"]
        item = proposed["rounds"][0]
        item.update(
            id="w00000_round",
            subject_id="w00000_subject",
            inspector_id="w00000_staff",
            end_sec=3,
        )
        item["events"][0]["actor_id"] = "w00000_staff"
        merged = workflow.merge_window(
            {"persons": [], "rounds": []}, proposed, 0, 3, "w00000_", {6, 8}, meta
        )
        self.assertTrue(merged["rounds"][0]["boundary_uncertain"])
        continuation = copy.deepcopy(merged)
        continuation["rounds"][0]["end_sec"] = 4.5
        result = workflow.merge_window(
            merged, continuation, 2, 5, "w00001_", {60, 70}, meta
        )
        self.assertEqual(len(result["rounds"]), 1)
        continuation["rounds"][0]["subject_id"] = None
        with self.assertRaises(HTTPException):
            workflow.merge_window(merged, continuation, 2, 5, "w00001_", {60}, meta)
        continuation = copy.deepcopy(merged)
        continuation["rounds"][0].update(end_sec=4.5, events=[])
        with self.assertRaises(HTTPException):
            workflow.merge_window(merged, continuation, 2, 5, "w00001_", {60}, meta)

    def test_unsampled_model_evidence_and_unscoped_ids_are_rejected(self):
        meta = self.ready()
        with self.assertRaises(HTTPException):
            workflow.merge_window(
                {"persons": [], "rounds": []},
                self.content(),
                0,
                2,
                "w00000_",
                {6, 8},
                meta,
            )
        people = [
            {
                "id": "w00000_person",
                "role": "subject",
                "evidence": [{"frame_index": 13}],
            }
        ]
        with self.assertRaises(HTTPException):
            workflow.merge_window(
                {"persons": [], "rounds": []},
                {"persons": people, "rounds": []},
                0,
                2,
                "w00000_",
                {6, 8},
                meta,
            )

    def test_prelabel_produces_draft_first_and_never_overwrites_reviewed_content(self):
        meta = self.ready()
        frames = [{"frame_index": 0, "time_sec": 0, "jpeg": b"image"}]
        response = completion({"persons": [], "rounds": []})
        with (
            patch.object(media, "window_frames", return_value=frames),
            patch.object(vlm, "request_window", return_value=response),
        ):
            first = workflow.prelabel(meta["id"], PrelabelOptions(), Progress())
            self.assertTrue(first["applied"])
            self.assertEqual(store.annotation(meta["id"])["revision"], 1)
            saved = self.save(meta, status="approved")
            second = workflow.prelabel(meta["id"], PrelabelOptions(), Progress())
            self.assertFalse(second["applied"])
            self.assertEqual(store.annotation(meta["id"]), saved)
            self.assertTrue(
                (
                    store.folder(meta["id"])
                    / "drafts"
                    / second["run_id"]
                    / "window_00000.json"
                ).exists()
            )

    def test_edit_committed_during_vlm_call_is_not_overwritten(self):
        meta = self.ready()
        frames = [{"frame_index": 0, "time_sec": 0, "jpeg": b"image"}]

        def request(*_args):
            self.save(meta)
            return completion({"persons": [], "rounds": []})

        with (
            patch.object(media, "window_frames", return_value=frames),
            patch.object(vlm, "request_window", side_effect=request),
        ):
            result = workflow.prelabel(meta["id"], PrelabelOptions(), Progress())
        self.assertFalse(result["applied"])
        self.assertEqual(store.annotation(meta["id"])["rounds"][0]["id"], "round")

    def test_bad_model_response_preserves_raw_draft_and_existing_annotations(self):
        meta = self.ready()
        before = store.annotation(meta["id"])
        frames = [{"frame_index": 0, "time_sec": 0, "jpeg": b"image"}]
        with (
            patch.object(media, "window_frames", return_value=frames),
            patch.object(vlm, "request_window", return_value=completion({}, "length")),
        ):
            with self.assertRaises(HTTPException):
                workflow.prelabel(meta["id"], PrelabelOptions(), Progress())
        self.assertEqual(store.annotation(meta["id"]), before)
        self.assertEqual(
            len(
                list((store.folder(meta["id"]) / "drafts").glob("*/window_00000.json"))
            ),
            1,
        )
        self.assertFalse((store.folder(meta["id"]) / "latest_draft.json").exists())

    def test_manifest_filters_unknown_and_uses_source_group_and_duplicate_hash(self):
        a = self.ready(group="batch-a", digest="same")
        b = self.ready(group="batch-b", digest="same")
        c = self.ready(group="batch-c", digest="other")
        d = self.ready(group="batch-a", digest="different")
        for meta in (a, b, c, d):
            content = self.content(status="approved")
            uncertain = copy.deepcopy(content["rounds"][0])
            uncertain.update(id="unknown", inspection="uncertain", events=[])
            content["rounds"].append(uncertain)
            store.save(meta["id"], InspectionEdit(revision=0, **content))
        result = workflow.export_manifest(
            ManifestRequest(
                recording_ids=[m["id"] for m in (a, b, c, d)], val_ratio=0.2
            )
        )
        split = {}
        for part in ("train", "val"):
            for line in (
                (Path(result["path"]) / f"{part}.jsonl").read_text().splitlines()
            ):
                row = json.loads(line)
                split[row["recording_id"]] = part
                self.assertEqual(row["round"]["inspection"], "present")
                self.assertEqual(row["annotation_revision"], 1)
                self.assertTrue(Path(row["video_path"]).is_absolute())
        self.assertEqual(split[a["id"]], split[b["id"]])
        self.assertEqual(split[a["id"]], split[d["id"]])
        self.assertNotEqual(split[a["id"]], split[c["id"]])
        self.assertEqual(result["skipped_rounds"], 4)
        self.save(a, observation="absent", status="approved")
        snapshot = read_json(Path(result["path"]) / "snapshot.json")
        self.assertTrue(
            all(r["annotation"]["revision"] == 1 for r in snapshot["recordings"])
        )

    def test_manifest_single_source_requires_explicit_training_only(self):
        meta = self.ready()
        self.save(meta, status="approved", observation="absent")
        with self.assertRaises(HTTPException):
            workflow.export_manifest(
                ManifestRequest(recording_ids=[meta["id"]], val_ratio=0.2)
            )
        result = workflow.export_manifest(
            ManifestRequest(recording_ids=[meta["id"]], val_ratio=0)
        )
        self.assertEqual(result["counts"], {"train": 1, "val": 0})

    def test_api_version_validation_and_invalid_recording_id(self):
        meta = self.ready()
        url = f"/api/security-annotations/recordings/{meta['id']}/annotation"
        self.assertEqual(
            self.client.put(url, json={"revision": 0, **self.content()}).status_code,
            200,
        )
        self.assertEqual(
            self.client.put(url, json={"revision": 0, **self.content()}).status_code,
            409,
        )
        invalid = self.client.get("/api/security-annotations/recordings/invalid")
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/api/security-annotations/recordings/{meta['id']}/prelabel",
                json={"window_sec": 2, "overlap_sec": 2},
            ).status_code,
            422,
        )

    def test_legacy_import_uses_existing_stores_and_inclusive_frame_end(self):
        meta = self.ready()
        path = store.folder(meta["id"]) / "original.mp4"
        store.update(meta["id"], sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        old = self.root / "legacy"
        old.mkdir()
        labels, events, schema = legacy.modules()
        labels.LabelStore(old).upsert(
            {
                "annotation_id": "clip",
                "clip_id": "clip",
                "video_path": str(path),
                "segment_start_frame": 0,
                "segment_end_frame": 24,
                "validity": "valid",
                **{
                    key: "scanned" if key == "left_hand" else "not_scanned"
                    for key in schema.SCAN_REGION_KEYS
                },
            }
        )
        events.EventStore(old).replace_for_clip(
            "clip",
            [
                {
                    "clip_id": "clip",
                    "region": "left_hand",
                    "start_frame": 2,
                    "end_frame": 4,
                }
            ],
        )
        doc = legacy.import_labels(
            meta["id"], LegacyImport(revision=0, dataset_path=str(old), clip_id="clip")
        )
        event = doc["rounds"][0]["events"][0]
        self.assertEqual((event["start_sec"], event["end_sec"]), (0.08, 0.2))
        self.assertEqual(doc["rounds"][0]["review_status"], "pending")
        self.assertIsNone(doc["rounds"][0]["subject_id"])

    def test_real_ffmpeg_preserves_original_and_produces_pts_index_and_browser_preview(
        self,
    ):
        source = self.root / "source.mp4"
        subprocess.run(
            [
                media.executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=64x48:rate=25:duration=0.4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            check=True,
        )
        original = source.read_bytes()
        meta = store.create("连续原视频", source_type="file")
        progress = Progress()
        media.copy_source(meta["id"], source, progress)
        media.prepare(meta["id"], progress)
        current = store.get(meta["id"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(
            (store.folder(meta["id"]) / current["original_file"]).read_bytes(), original
        )
        self.assertEqual(current["frame_count"], 10)
        self.assertAlmostEqual(current["duration_sec"], 0.4)
        self.assertTrue((store.folder(meta["id"]) / "preview.mp4").is_file())
        self.assertTrue(media.frame(meta["id"], 2).startswith(b"\xff\xd8"))
        sampled = media.window_frames(
            meta["id"],
            media.timeline(meta["id"])["times_sec"],
            0,
            0.4,
            PrelabelOptions(sample_fps=25),
            Progress(),
        )
        self.assertEqual([f["frame_index"] for f in sampled], list(range(10)))

    def test_real_variable_frame_timestamps_are_not_replaced_by_nominal_fps(self):
        path = self.root / "variable.mkv"
        subprocess.run(
            [
                media.executable("ffmpeg"),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=64x48:rate=10:duration=0.5",
                "-vf",
                "setpts='if(gte(N,2),PTS+0.7/TB,PTS)'",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        index = media.index_video(path, Progress())
        self.assertEqual(index["frame_count"], 5)
        self.assertAlmostEqual(index["times_sec"][2], 0.9)
        self.assertEqual(len(index["gaps"]), 1)
        meta = store.create("带时间缺口的原视频", source_type="file")
        media.copy_source(meta["id"], path, Progress())
        write_json(store.folder(meta["id"]) / "timeline.json", index)
        store.update(
            meta["id"],
            state="ready",
            **{k: v for k, v in index.items() if k != "times_sec"},
        )
        cap = media.cv2.VideoCapture(str(path))
        expected = []
        try:
            while True:
                ok, image = cap.read()
                if not ok:
                    break
                expected.append(media.encode_frame(image))
        finally:
            cap.release()
        self.assertEqual([media.frame(meta["id"], i) for i in range(5)], expected)

    def test_cancel_before_copy_keeps_source_and_no_published_original(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        meta = store.create("cancel", source_type="file")
        progress = Progress()
        progress.cancelled = True
        with self.assertRaises(RuntimeError):
            media.copy_source(meta["id"], source, progress)
        self.assertEqual(source.read_bytes(), b"source")
        self.assertFalse((store.folder(meta["id"]) / "original.mp4").exists())
        self.assertFalse(list(store.folder(meta["id"]).glob("*.part")))

    def test_vlm_http_request_carries_ordered_pts_and_requires_complete_response(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                body = json.dumps(completion({"persons": [], "rounds": []})).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(
                os.environ,
                {
                    "INSPECTION_VLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"
                },
            ):
                response = vlm.request_window(
                    [{"frame_index": 3, "time_sec": 0.12, "jpeg": b"frame"}],
                    {},
                    "",
                    Progress(),
                )
            self.assertEqual(
                vlm.parse_completion(response), {"persons": [], "rounds": []}
            )
            payload = received[0]
            self.assertEqual(payload["model"], "fuda-qwen38-27b")
            self.assertIn("0.120000", payload["messages"][1]["content"][1]["text"])
            self.assertEqual(payload["messages"][1]["content"][2]["type"], "image_url")
            with self.assertRaises(HTTPException):
                vlm.parse_completion(completion({}, "length"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_rtsp_recording_copies_stream_and_stop_prepares_saved_original(self):
        done = threading.Event()
        process = Mock()
        process.poll.side_effect = lambda: 255 if done.is_set() else None
        process.wait.side_effect = lambda **_kwargs: (done.wait(5), 255)[1]
        process.send_signal.side_effect = lambda _signal: done.set()
        commands = []

        def start_process(command, **_kwargs):
            commands.append(command)
            Path(command[-1]).write_bytes(b"captured-video")
            return process

        from app.schemas.security_annotation import RecordingStart

        self.enterContext(patch.object(recorder, "sessions", {}))
        with (
            patch.object(
                recorder.camera_service,
                "get",
                return_value={
                    "name": "camera",
                    "transport": "tcp",
                    "url": "rtsp://secret@camera/stream",
                },
            ),
            patch.object(recorder.subprocess, "Popen", side_effect=start_process),
            patch.object(workflow, "submit") as submit,
        ):
            result = recorder.start(RecordingStart(camera_id="camera"))
            item = recorder.sessions[result["recording_id"]]
            recorder.stop(result["recording_id"])
            item["thread"].join(timeout=5)
            self.assertFalse(item["thread"].is_alive())
            submit.assert_called_once()
        command = commands[0]
        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertNotIn("-vf", command)
        self.assertNotIn("-r", command)
        self.assertNotIn("secret", json.dumps(store.get(result["recording_id"])))


if __name__ == "__main__":
    unittest.main()
