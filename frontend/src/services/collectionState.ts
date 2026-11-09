import type { BBox, ClassMapping, CollectionSession, CollectionStatus, Dataset } from "../types";
import { readPreference } from "./preferences";
import { parseSourceIds } from "./classMapping";

export type CollectionDraft = {
  source: string;
  selectedCameras: string[];
  mode: string;
  interval: number;
  rois: Record<string, BBox | null>;
  autoLabel: string;
  detector: string;
  modelPath: string;
  triggerModelPath: string;
  labelClasses: number[] | null;
  classMapping: ClassMapping;
  confidence: number;
  similarity: number;
  targets: string;
  videoPath: string;
  sampling: string;
  every: number;
};

function numeric(value: unknown, fallback: number, min: number, max: number) {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? value : fallback;
}

export function readCollectionPreferences(dataset: Dataset): CollectionDraft & { hasSelection: boolean; hasSettings: boolean } {
  const value = readPreference(dataset.id, "collection");
  const face = dataset.annotation_mode === "face_5pt";
  const text = (key: string, fallback = "") => typeof value[key] === "string" ? value[key] as string : fallback;
  const known = new Set(dataset.classes.map((c) => c.id));
  const rois: Record<string, BBox | null> = {};
  if (value.rois && typeof value.rois === "object" && !Array.isArray(value.rois)) {
    for (const [id, box] of Object.entries(value.rois)) {
      if (Array.isArray(box) && box.length === 4 && box.every((n) => typeof n === "number" && Number.isFinite(n))
        && box[0] >= 0 && box[1] >= 0 && box[0] < box[2] && box[1] < box[3]) rois[id] = box as BBox;
    }
  }
  const classMapping: ClassMapping = {};
  if (value.classMapping && typeof value.classMapping === "object" && !Array.isArray(value.classMapping)) {
    for (const [id, source] of Object.entries(value.classMapping)) {
      if (!known.has(Number(id))) continue;
      try {
        const ids = parseSourceIds(source);
        if (ids) classMapping[Number(id)] = ids;
      } catch { /* Invalid saved entries must not block opening the collection page. */ }
    }
  }
  const sampling = value.sampling === "seconds" ? "seconds" : "frames";
  const every = numeric(value.every, sampling === "frames" ? 30 : 2, sampling === "frames" ? 1 : 0.001, 1000000);
  return {
    hasSelection: Array.isArray(value.selectedCameras),
    hasSettings: ["raw_interval", "roi_interval", "detect_raw", "detect_crop"].includes(String(value.mode)),
    source: value.source === "video" ? "video" : "rtsp",
    selectedCameras: Array.isArray(value.selectedCameras)
      ? [...new Set(value.selectedCameras.filter((id): id is string => typeof id === "string" && id.length > 0))].slice(0, 9) : [],
    mode: ["raw_interval", "roi_interval", "detect_raw", "detect_crop"].includes(String(value.mode)) ? String(value.mode) : "raw_interval",
    interval: numeric(value.interval, 2, 0.1, 86400), rois,
    autoLabel: dataset.task_type !== "detection" ? "off" : face
      ? value.autoLabel === "off" ? "off" : "scrfd"
      : ["off", "yolo_world", "custom_yolo"].includes(String(value.autoLabel)) ? String(value.autoLabel) : "off",
    detector: ["yolo_world", "custom_yolo", "scrfd"].includes(String(value.detector)) ? String(value.detector) : face ? "scrfd" : "yolo_world",
    modelPath: text("modelPath"), triggerModelPath: text("triggerModelPath"),
    labelClasses: face ? null : Array.isArray(value.labelClasses)
      ? [...new Set(value.labelClasses.filter((id): id is number => typeof id === "number" && known.has(id)))] : null,
    classMapping: face ? {} : classMapping,
    confidence: numeric(value.confidence, 0.25, 0.001, 1),
    similarity: numeric(value.similarity, 0.92, 0, 1),
    targets: text("targets", face ? "face" : "person"),
    videoPath: text("videoPath"), sampling,
    every: sampling === "frames" ? Math.max(1, Math.floor(every)) : every,
  };
}

export const emptyCollectionStatus = (): CollectionStatus => ({ active: false, active_count: 0, max_cameras: 9, sessions: [] });
// A returning page can show the last known session immediately, then verify it.
// Cached state never authorizes a new start; the page waits for a fresh response.
const snapshots = new Map<string, CollectionStatus>();
export function cachedCollectionStatus(datasetId: string): CollectionStatus {
  return snapshots.get(datasetId) ?? emptyCollectionStatus();
}
export function rememberCollectionStatus(datasetId: string, status: CollectionStatus) {
  snapshots.delete(datasetId);
  snapshots.set(datasetId, status);
  if (snapshots.size > 32) snapshots.delete(snapshots.keys().next().value!);
}

export function mergeStartedSessions(status: CollectionStatus, started: CollectionSession[]): CollectionStatus {
  const replacements = new Map(started.map((session) => [session.camera_id, session]));
  const sessions = [...status.sessions.filter((session) => !replacements.has(session.camera_id)), ...started];
  const active = sessions.filter((session) => session.active);
  return { ...status, sessions, active: active.length > 0, active_count: active.length };
}

export function collectionPhase(session?: CollectionSession): string {
  if (!session) return "实时预览";
  if (session.stopping) return "正在停止";
  if (!session.active) return session.error ? "采集失败" : "已停止";
  return ({ starting: "准备启动", loading_model: "正在加载检测模型", preparing: "正在准备采集",
    waiting_frame: "等待摄像头画面", collecting: "采集中" } as Record<string, string>)[session.phase ?? ""] ?? "采集中";
}
