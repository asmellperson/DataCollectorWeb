"""窗口预标、草稿发布和按源录像分组的本地训练快照。"""

import copy
import json
import random
import time
from uuid import uuid4

from app.core.storage import read_json, require, write_json, atomic_bytes
from app.schemas.security_annotation import InspectionContent, InspectionEdit
from app.services import task_service as tasks
from . import media, store, vlm


def submit(recording_id, kind, callback):
    with store.lock(recording_id):
        meta = store.get(recording_id)
        if meta["active_task_id"]:
            active = tasks.get(meta["active_task_id"])
            require(
                active["status"] not in ("queued", "running"),
                "该录像已有任务正在执行",
                409,
            )

        def run(progress):
            try:
                return callback(progress)
            except Exception as exc:
                state = store.get(recording_id)["state"]
                store.update(
                    recording_id,
                    error=str(getattr(exc, "detail", exc))[:2000],
                    state="failed" if state == "preparing" else state,
                )
                raise
            finally:
                store.update(recording_id, active_task_id="")

        try:
            result = tasks.submit(kind, None, run)
        except Exception:
            store.update(
                recording_id,
                state="failed" if meta["state"] == "preparing" else meta["state"],
                error="任务提交失败，原文件与标注仍保留",
            )
            raise
        store.update(recording_id, active_task_id=result["task_id"], error="")
        return result


def import_video(body):
    path = media.validate_path(body.path)
    media.executable("ffmpeg")
    media.executable("ffprobe")
    meta = store.create(
        body.name or path.name,
        body.source_group,
        source_type="file",
        source_name=path.name,
    )

    def run(progress):
        media.copy_source(meta["id"], path, progress)
        current = store.get(meta["id"])
        if not body.source_group:
            store.update(meta["id"], source_group=current["sha256"])
        return media.prepare(meta["id"], progress)

    result = submit(meta["id"], "inspection_import", run)
    return {**result, "recording_id": meta["id"]}


def previous_context(content, start):
    rounds = [r for r in content["rounds"] if r["end_sec"] >= start]
    ids = {r.get(key) for r in rounds for key in ("subject_id", "inspector_id")}
    persons = [p for p in content["persons"] if p["id"] in ids]
    return {"persons": persons, "rounds": rounds}


def merge_window(previous, proposed, start, end, prefix, sampled, meta):
    # 只按模型明确复用的稳定 ID 延续，不用邻近时间或外观文本擅自合并人员。
    proposed = InspectionContent.model_validate(proposed).model_dump()
    people = {p["id"]: copy.deepcopy(p) for p in previous["persons"]}
    rounds = {r["id"]: copy.deepcopy(r) for r in previous["rounds"]}
    known_frames = set(sampled)
    for person in people.values():
        known_frames.update(e["frame_index"] for e in person["evidence"])
    known_frames.update(
        f for r in rounds.values() for e in r["events"] for f in e["evidence_frames"]
    )
    for person in proposed["persons"]:
        old = people.get(person["id"])
        require(
            old is not None or person["id"].startswith(prefix),
            "模型的新人物 ID 未使用本窗口前缀",
        )
        require(
            all(e["frame_index"] in known_frames for e in person["evidence"]),
            "模型引用了未提供的人物证据帧",
        )
        if old:
            require(
                old["role"] == person["role"]
                or "unknown" in (old["role"], person["role"]),
                "跨窗人物角色发生冲突",
            )
            evidence = {e["frame_index"]: e for e in old["evidence"]}
            evidence.update({e["frame_index"]: e for e in person["evidence"]})
            person["evidence"] = list(evidence.values())[-200:]
        people[person["id"]] = person
    for item in proposed["rounds"]:
        old = rounds.get(item["id"])
        require(item["end_sec"] <= end + 0.00001, "模型轮次结束超出输入窗口")
        if old:
            require(
                old["end_sec"] >= start and old["subject_id"] == item["subject_id"],
                "跨窗续接轮次的受检人或时间不一致",
            )
            require(
                abs(old["start_sec"] - item["start_sec"]) < 0.00001,
                "跨窗续接不能改写先前轮次起点",
            )
            require(item["end_sec"] >= old["end_sec"], "跨窗续接不能缩短先前轮次")
            # 禁止模型在更新边界时静默删掉前窗已有事件。
            require(
                {e["id"] for e in old["events"]} <= {e["id"] for e in item["events"]},
                "续接轮次遗漏了已有动作",
            )
        else:
            require(
                item["id"].startswith(prefix) and item["start_sec"] >= start - 0.00001,
                "模型的新轮次 ID 或起点超出当前窗口",
            )
        for event in item["events"]:
            require(
                all(f in known_frames for f in event["evidence_frames"]),
                "模型引用了未提供的动作证据帧",
            )
        item["review_status"] = "pending"
        if item["end_sec"] >= end - 0.00001 and end < meta["duration_sec"]:
            item["boundary_uncertain"] = True
        if any(
            g["start_sec"] < item["end_sec"] and g["end_sec"] > item["start_sec"]
            for g in meta.get("gaps", [])
        ):
            item["quality"] = "uncertain"
            item["notes"] = (item["notes"] + "；原录像时间戳存在缺口，需核验").strip(
                "；"
            )
        rounds[item["id"]] = item
    merged = InspectionContent(
        persons=list(people.values()),
        rounds=sorted(rounds.values(), key=lambda r: r["start_sec"]),
    )
    store.validate_content(meta, merged)
    return merged.model_dump()


def prelabel(recording_id, options, progress):
    with store.lock(recording_id):
        meta = store.get(recording_id)
        require(meta["state"] == "ready", "请等待原视频准备完成", 409)
        revision = store.annotation(recording_id)["revision"]
    run_id = uuid4().hex
    root = store.folder(recording_id) / "drafts" / run_id
    root.mkdir(parents=True)
    write_json(
        root / "run.json",
        {
            "id": run_id,
            "options": options.model_dump(),
            "prompt_version": vlm.PROMPT_VERSION,
            "model": vlm.settings()["model"],
            "base_revision": revision,
            "created_at": store.now(),
        },
    )
    times = media.timeline(recording_id)["times_sec"]
    step = options.window_sec - options.overlap_sec
    windows = []
    start = 0.0
    while True:
        end = min(round(start + options.window_sec, 6), meta["duration_sec"])
        windows.append((round(start, 6), end))
        if end >= meta["duration_sec"]:
            break
        start += step
    progress.update(total=len(windows))
    content = {"persons": [], "rounds": []}
    started = time.monotonic()
    for index, (start, end) in enumerate(windows):
        progress.check()
        frames = media.window_frames(recording_id, times, start, end, options, progress)
        context = {
            "window_start_sec": start,
            "window_end_sec": end,
            "id_prefix": f"w{index:05d}_",
            "previous": previous_context(content, start),
        }
        response = vlm.request_window(frames, context, options.action_hint, progress)
        write_json(
            root / f"window_{index:05d}.json",
            {
                "start_sec": start,
                "end_sec": end,
                "sampled_frames": [
                    {k: v for k, v in f.items() if k != "jpeg"} for f in frames
                ],
                "response": response,
            },
        )
        proposed = vlm.parse_completion(response)
        content = merge_window(
            content,
            proposed,
            start,
            end,
            context["id_prefix"],
            {f["frame_index"] for f in frames},
            meta,
        )
        write_json(root / "annotation.json", content)
        progress.advance()
    progress.check()
    with store.lock(recording_id):
        current = store.annotation(recording_id)
        write_json(
            store.folder(recording_id) / "latest_draft.json",
            {
                "run_id": run_id,
                "created_at": store.now(),
                "elapsed_sec": round(time.monotonic() - started, 3),
                "base_revision": revision,
                "round_count": len(content["rounds"]),
            },
        )
        applied = (
            current["revision"] == revision
            and not current["rounds"]
            and not current["persons"]
        )
        if applied:
            store.save(
                recording_id, InspectionEdit(revision=revision, **content), origin="vlm"
            )
    return {
        "recording_id": recording_id,
        "run_id": run_id,
        "rounds": len(content["rounds"]),
        "applied": applied,
        "message": "模型草稿已进入待复核"
        if applied
        else "模型草稿已保存，人工版本保持不变",
    }


def apply_draft(recording_id, revision):
    with store.lock(recording_id):
        root = store.folder(recording_id)
        require((root / "latest_draft.json").exists(), "暂无完成的模型草稿", 404)
        run = read_json(root / "latest_draft.json")
        content = read_json(root / "drafts" / run["run_id"] / "annotation.json")
        return store.save(
            recording_id, InspectionEdit(revision=revision, **content), origin="vlm"
        )


def export_manifest(options):
    snapshots = []
    for rid in sorted(set(options.recording_ids)):
        with store.lock(rid):
            meta = store.get(rid)
            require(meta["state"] == "ready", "所选录像尚未准备完成", 409)
            snapshots.append((meta, store.annotation(rid)))
    eligible = []
    skipped = 0
    for meta, doc in snapshots:
        people = {p["id"]: p for p in doc["persons"]}
        for item in doc["rounds"]:
            if (
                item["review_status"] != "approved"
                or item["quality"] != "usable"
                or item["inspection"] == "uncertain"
                or item["boundary_uncertain"]
                or people.get(item["subject_id"], {}).get("role") != "subject"
            ):
                skipped += 1
                continue
            events = [
                e
                for e in item["events"]
                if e["observation"] == "observed" and not e["boundary_uncertain"]
            ]
            eligible.append(
                (
                    meta,
                    {
                        "schema_version": "security-inspection/1",
                        "recording_id": meta["id"],
                        "video_path": str(
                            (store.folder(meta["id"]) / meta["original_file"]).resolve()
                        ),
                        "timeline_path": str(
                            (store.folder(meta["id"]) / "timeline.json").resolve()
                        ),
                        "annotation_revision": doc["revision"],
                        "source_group": meta["source_group"],
                        "sha256": meta.get("sha256"),
                        "time_basis": "source_pts_seconds_from_first_frame",
                        "round": {**item, "events": events},
                        "persons": doc["persons"],
                    },
                )
            )
    require(
        eligible,
        "没有可用于训练的已确认轮次；待确认、边界不确定和不可用样本不会作为负样本导出",
    )
    # 相同采集批次或相同原文件哈希必须在同一分组，避免重复导入造成泄漏。
    parent = {meta["id"]: meta["id"] for meta, _ in eligible}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    seen = {}
    for meta, _ in eligible:
        for key in (
            ("group", meta["source_group"]),
            ("hash", meta.get("sha256") or meta["id"]),
        ):
            if key in seen:
                parent[find(meta["id"])] = find(seen[key])
            else:
                seen[key] = meta["id"]
    groups = sorted({find(meta["id"]) for meta, _ in eligible})
    require(
        not options.val_ratio or len(groups) >= 2,
        "训练/验证划分至少需要两组独立录像；仅生成训练清单时将验证比例设为 0",
    )
    random.Random(options.seed).shuffle(groups)
    count = (
        max(1, min(len(groups) - 1, round(len(groups) * options.val_ratio)))
        if options.val_ratio
        else 0
    )
    validation = set(groups[:count])
    export_id = uuid4().hex
    destination = store.ROOT / "exports" / export_id
    destination.mkdir(parents=True)
    rows = {"train": [], "val": []}
    for meta, row in eligible:
        rows["val" if find(meta["id"]) in validation else "train"].append(row)
    for part, records in rows.items():
        atomic_bytes(
            destination / f"{part}.jsonl",
            "".join(
                json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n"
                for r in records
            ).encode(),
        )
    # 清单引用确定版本，后续人工修改不会改变本次训练标签。
    write_json(
        destination / "snapshot.json",
        {"recordings": [{"metadata": m, "annotation": d} for m, d in snapshots]},
    )
    result = {
        "id": export_id,
        "path": str(destination.resolve()),
        "counts": {k: len(v) for k, v in rows.items()},
        "skipped_rounds": skipped,
        "created_at": store.now(),
        "options": options.model_dump(),
    }
    write_json(destination / "manifest.json", result)
    return result
