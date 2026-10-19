from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

EVENT_FIELDS = [
    "event_version",
    "event_id",
    "clip_id",
    "sample_id",
    "video_path",
    "region",
    "start_frame",
    "end_frame",
    "start_sec",
    "end_sec",
    "duration_frames",
    "duration_sec",
    "fps",
    "annotated_at",
]


class EventStore:
    """保存逐部位扫描时间段；一个片段可以包含多个事件。"""

    def __init__(self, dataset_dir: Path) -> None:
        self.dataset_dir = dataset_dir.resolve()
        self.csv_path = self.dataset_dir / "events.csv"
        self.json_path = self.dataset_dir / "events.json"

    def load_all(self) -> list[dict[str, Any]]:
        if not self.json_path.exists():
            return []
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def load_for_clip(self, clip_id: str) -> list[dict[str, Any]]:
        return [
            event
            for event in self.load_all()
            if str(event.get("clip_id", "")) == clip_id
        ]

    def replace_for_clip(self, clip_id: str, clip_events: list[dict[str, Any]]) -> None:
        events = [
            event
            for event in self.load_all()
            if str(event.get("clip_id", "")) != clip_id
        ]
        events.extend(clip_events)
        events.sort(
            key=lambda event: (
                str(event.get("clip_id", "")),
                int(event.get("start_frame", 0)),
                str(event.get("region", "")),
            )
        )

        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        json_tmp = self.json_path.with_suffix(".json.tmp")
        json_tmp.write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        json_tmp.replace(self.json_path)

        csv_tmp = self.csv_path.with_suffix(".csv.tmp")
        with csv_tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=EVENT_FIELDS, extrasaction="ignore"
            )
            writer.writeheader()
            for event in events:
                writer.writerow({key: event.get(key, "") for key in EVENT_FIELDS})
        csv_tmp.replace(self.csv_path)
