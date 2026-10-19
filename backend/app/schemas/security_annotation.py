"""安检视频、模型草稿和人工复核共用的时序契约。"""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from app.schemas.contracts import Contract


def identifier():
    return uuid4().hex


Observation = Literal["present", "absent", "uncertain"]
Key = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")]


class PersonEvidence(Contract):
    frame_index: int = Field(ge=0, strict=True)
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def check_box(self):
        if self.bbox is not None:
            x1, y1, x2, y2 = self.bbox
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError("人物框须为归一化的 x1,y1,x2,y2")
        return self


class InspectionPerson(Contract):
    id: Key = Field(default_factory=identifier)
    role: Literal["inspector", "subject", "unknown"] = "unknown"
    description: str = Field(default="", max_length=1000)
    evidence: list[PersonEvidence] = Field(default_factory=list, max_length=200)


class InspectionEvent(Contract):
    id: Key = Field(default_factory=identifier)
    action: str = Field(min_length=1, max_length=100)
    region: str = Field(default="", max_length=100)
    actor_id: Key | None = None
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    observation: Literal["observed", "uncertain"] = "observed"
    boundary_uncertain: bool = False
    evidence_frames: list[Annotated[int, Field(ge=0, strict=True)]] = Field(
        default_factory=list, max_length=200
    )
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_interval(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("动作结束时间必须晚于开始时间，时间区间为左闭右开")
        return self


class InspectionRound(Contract):
    id: Key = Field(default_factory=identifier)
    subject_id: Key | None = None
    inspector_id: Key | None = None
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    inspection: Observation = "uncertain"
    boundary_uncertain: bool = False
    quality: Literal["usable", "uncertain", "unusable"] = "uncertain"
    review_status: Literal["pending", "approved", "rejected"] = "pending"
    coverage: dict[str, Literal["scanned", "not_scanned", "uncertain"]] = Field(
        default_factory=dict
    )
    events: list[InspectionEvent] = Field(default_factory=list, max_length=1000)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_events(self):
        if self.end_sec <= self.start_sec:
            raise ValueError("轮次结束时间必须晚于开始时间")
        if self.subject_id and self.subject_id == self.inspector_id:
            raise ValueError("安检员与受检人不能是同一个人物")
        groups: dict[tuple[str, str], list[InspectionEvent]] = {}
        for event in self.events:
            if event.start_sec < self.start_sec or event.end_sec > self.end_sec:
                raise ValueError("动作时间段必须位于所属轮次内")
            if event.observation == "observed":
                if self.inspection == "absent":
                    raise ValueError("未发生安检的轮次不能包含已观察到的安检动作")
                if self.coverage.get(event.region) == "not_scanned":
                    raise ValueError("未扫描部位与已有扫描事件冲突")
            groups.setdefault((event.action, event.region), []).append(event)
        # 沿用原工具的同类时间段不重叠校验，不限制不同部位同时发生。
        for events in groups.values():
            ordered = sorted(events, key=lambda e: e.start_sec)
            if any(a.end_sec > b.start_sec for a, b in zip(ordered, ordered[1:])):
                raise ValueError("同一轮相同动作和部位的时间段不能重叠")
        if self.review_status == "approved" and self.quality == "usable":
            if self.inspection != "uncertain" and not self.subject_id:
                raise ValueError("确认安检有无前须明确受检人")
            observed_regions = {
                event.region for event in self.events if event.observation == "observed"
            }
            if any(
                v == "scanned" and k not in observed_regions
                for k, v in self.coverage.items()
            ):
                raise ValueError("已确认的扫描部位必须有对应动作时间段")
        return self


class InspectionContent(Contract):
    persons: list[InspectionPerson] = Field(default_factory=list, max_length=2000)
    rounds: list[InspectionRound] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def check_references(self):
        people = {p.id: p for p in self.persons}
        if len(people) != len(self.persons):
            raise ValueError("人物 ID 不能重复")
        ids = [r.id for r in self.rounds]
        event_ids = [e.id for r in self.rounds for e in r.events]
        if len(set(ids)) != len(ids) or len(set(event_ids)) != len(event_ids):
            raise ValueError("轮次或动作 ID 不能重复")
        for item in self.rounds:
            for pid, role in (
                (item.subject_id, "subject"),
                (item.inspector_id, "inspector"),
            ):
                if pid is not None and (
                    pid not in people or people[pid].role not in (role, "unknown")
                ):
                    raise ValueError("轮次关联的人物不存在或角色不一致")
            for event in item.events:
                if event.actor_id is not None and event.actor_id not in people:
                    raise ValueError("动作执行人不存在")
                if event.actor_id and event.actor_id == item.subject_id:
                    raise ValueError("扫描动作执行人不能是本轮受检人")
                if event.actor_id and people[event.actor_id].role == "subject":
                    raise ValueError("受检人不能作为其他轮次的扫描动作执行人")
        return self


class InspectionEdit(InspectionContent):
    revision: int = Field(ge=0)
    review_seconds: float = Field(default=0, ge=0, le=86400)


class RecordingImport(Contract):
    path: str = Field(min_length=1, max_length=4096)
    name: str = Field(default="", max_length=200)
    source_group: str = Field(default="", max_length=200)


class RecordingStart(Contract):
    camera_id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="", max_length=200)
    source_group: str = Field(default="", max_length=200)


class PrelabelOptions(Contract):
    window_sec: float = Field(default=6, ge=1, le=30)
    overlap_sec: float = Field(default=2, ge=0.1, le=15)
    sample_fps: float = Field(default=12, ge=1, le=60)
    image_max_side: int = Field(default=960, ge=320, le=1920)
    action_hint: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_window(self):
        if self.overlap_sec >= self.window_sec:
            raise ValueError("重叠时间必须小于窗口长度")
        if self.window_sec * self.sample_fps > 192:
            raise ValueError("每个窗口最多采样 192 帧，请调整窗口或采样帧率")
        return self


class RevisionRequest(Contract):
    revision: int = Field(ge=0)


class ManifestRequest(Contract):
    recording_ids: list[Key] = Field(min_length=1, max_length=1000)
    val_ratio: float = Field(default=0.2, ge=0, lt=1)
    seed: int = 42


class LegacyImport(Contract):
    revision: int = Field(ge=0)
    dataset_path: str = Field(min_length=1, max_length=4096)
    clip_id: str = Field(min_length=1, max_length=300)
