from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .label_schema import SCAN_REGION_KEYS

LABEL_FIELDS = [
    "annotation_version",
    "annotation_id",
    "clip_id",
    "sample_id",
    "clip_name",
    "video_path",
    "metadata_path",
    "source_url",
    "recorded_at",
    "annotated_at",
    "segment_start_frame",
    "segment_end_frame",
    "segment_start_sec",
    "segment_end_sec",
    "fps",
    "frame_count",
    "duration_sec",
    "source_width",
    "source_height",
    "saved_width",
    "saved_height",
    "roi_x",
    "roi_y",
    "roi_w",
    "roi_h",
    "roi_normalized",
    "crop_to_roi",
    "validity",
    *SCAN_REGION_KEYS,
    "notes",
]


class LabelStore:
    def __init__(self, dataset_dir: Path) -> None:
        self.dataset_dir = dataset_dir.resolve()
        self.csv_path = self.dataset_dir / "labels.csv"
        self.json_path = self.dataset_dir / "labels.json"

    def load_all(self) -> list[dict[str, Any]]:
        if not self.json_path.exists():
            return []
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def find_for_video(self, video_path: Path) -> dict[str, Any] | None:
        resolved = video_path.resolve()
        for row in self.load_all():
            stored = Path(str(row.get("video_path", "")))
            candidate = stored if stored.is_absolute() else self.dataset_dir / stored
            try:
                if candidate.resolve() == resolved:
                    return row
            except OSError:
                continue
        return None

    def upsert(self, record: dict[str, Any]) -> None:
        rows = self.load_all()
        annotation_id = str(record["annotation_id"])
        replaced = False
        for index, row in enumerate(rows):
            if str(row.get("annotation_id", "")) == annotation_id:
                rows[index] = record
                replaced = True
                break
        if not replaced:
            rows.append(record)

        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        json_tmp = self.json_path.with_suffix(".json.tmp")
        json_tmp.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        json_tmp.replace(self.json_path)

        csv_tmp = self.csv_path.with_suffix(".csv.tmp")
        with csv_tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=LABEL_FIELDS, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                flat = dict(row)
                if isinstance(flat.get("roi_normalized"), list):
                    flat["roi_normalized"] = json.dumps(flat["roi_normalized"])
                writer.writerow({key: flat.get(key, "") for key in LABEL_FIELDS})
        csv_tmp.replace(self.csv_path)
