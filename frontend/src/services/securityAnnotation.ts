import { api } from "./api";
import type {
  InspectionAnnotation, InspectionContent, InspectionDetail, InspectionEvent, InspectionRecording, InspectionRound,
} from "../types/securityAnnotation";

export const inspectionBase = "/security-annotations";
export const recordingBase = (id: string) => `${inspectionBase}/recordings/${id}`;

export async function deleteInspectionRecording(id: string) {
  try {
    await api(recordingBase(id), { method: "DELETE" });
  } catch (error) {
    const values = await api<InspectionRecording[]>(`${inspectionBase}/recordings`, { cache: "no-store" }).catch(() => null);
    if (!values || values.some(value => value.id === id)) throw error;
  }
}

export function inspectionId() {
  // 内网 HTTP 页面也能使用 getRandomValues，不依赖安全上下文中的 randomUUID。
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
}

export function frameAtTime(times: number[], seconds: number) {
  let low = 0, high = times.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (times[middle] <= seconds + 1e-7) low = middle + 1;
    else high = middle;
  }
  return Math.max(0, low - 1);
}

export function newRound(start: number, end: number): InspectionRound {
  return {
    id: inspectionId(), subject_id: null, inspector_id: null,
    start_sec: start, end_sec: end, inspection: "uncertain", boundary_uncertain: true,
    quality: "uncertain", review_status: "pending", events: [], coverage: {}, notes: "",
  };
}

export function newEvent(start: number, end: number, actor: string | null): InspectionEvent {
  return {
    id: inspectionId(), action: "扫描", region: "", actor_id: actor,
    start_sec: start, end_sec: end, observation: "uncertain", boundary_uncertain: true,
    evidence_frames: [], notes: "",
  };
}

export function splitRound(item: InspectionRound, at: number): [InspectionRound, InspectionRound] {
  if (at <= item.start_sec || at >= item.end_sec) throw new Error("拆轮位置须位于本轮内部");
  const left = { ...structuredClone(item), end_sec: at, review_status: "pending" as const,
    inspection: "uncertain" as const, quality: "uncertain" as const, boundary_uncertain: true, coverage: {} };
  const right = { ...structuredClone(left), id: inspectionId(), start_sec: at,
    end_sec: item.end_sec, subject_id: null, notes: "拆轮后请重新关联受检人。" + item.notes };
  left.events = [];
  right.events = [];
  for (const event of item.events) {
    if (event.end_sec <= at) left.events.push(structuredClone(event));
    else if (event.start_sec >= at) right.events.push(structuredClone(event));
    else {
      const notes = "拆轮处动作归属和边界待确认。" + event.notes;
      left.events.push({ ...event, end_sec: at, boundary_uncertain: true, observation: "uncertain", notes });
      right.events.push({ ...event, id: inspectionId(), start_sec: at,
        boundary_uncertain: true, observation: "uncertain", notes });
    }
  }
  return [left, right];
}

export function mergeRounds(first: InspectionRound, second: InspectionRound): InspectionRound {
  if (first.subject_id && second.subject_id && first.subject_id !== second.subject_id)
    throw new Error("两轮关联了不同受检人，请先核对并修正人物关联，再合并");
  return {
    ...structuredClone(first), start_sec: Math.min(first.start_sec, second.start_sec),
    end_sec: Math.max(first.end_sec, second.end_sec),
    subject_id: first.subject_id || second.subject_id,
    inspector_id: first.inspector_id === second.inspector_id ? first.inspector_id : null,
    inspection: "uncertain", quality: "uncertain", boundary_uncertain: true,
    review_status: "pending", coverage: {},
    events: [...structuredClone(first.events), ...structuredClone(second.events)].sort((a, b) => a.start_sec - b.start_sec),
    notes: [first.notes, second.notes, "合并后请复核人物、动作及重复时间段。"].filter(Boolean).join("；"),
  };
}

export async function saveInspection(id: string, base: InspectionAnnotation, content: InspectionContent, seconds: number) {
  const path = recordingBase(id);
  try {
    return await api<InspectionAnnotation>(`${path}/annotation`, {
      method: "PUT", body: JSON.stringify({ ...content, revision: base.revision, review_seconds: seconds }),
    });
  } catch (error) {
    // 沿用图片标注的丢响应回读，避免重复保存和重复累计复核耗时。
    const current = await api<InspectionDetail>(path, { cache: "no-store" }).catch(() => null);
    if (current && current.annotation.revision > base.revision &&
        JSON.stringify(current.annotation.persons) === JSON.stringify(content.persons) &&
        JSON.stringify(current.annotation.rounds) === JSON.stringify(content.rounds)) return current.annotation;
    throw error;
  }
}
