export type InspectionPerson = {
  id: string;
  role: "inspector" | "subject" | "unknown";
  description: string;
  evidence: { frame_index: number; bbox: [number, number, number, number] | null }[];
};
export type InspectionEvent = {
  id: string;
  action: string;
  region: string;
  actor_id: string | null;
  start_sec: number;
  end_sec: number;
  observation: "observed" | "uncertain";
  boundary_uncertain: boolean;
  evidence_frames: number[];
  notes: string;
};
export type InspectionRound = {
  id: string;
  subject_id: string | null;
  inspector_id: string | null;
  start_sec: number;
  end_sec: number;
  inspection: "present" | "absent" | "uncertain";
  boundary_uncertain: boolean;
  quality: "usable" | "uncertain" | "unusable";
  review_status: "pending" | "approved" | "rejected";
  coverage: Record<string, "scanned" | "not_scanned" | "uncertain">;
  events: InspectionEvent[];
  notes: string;
};
export type InspectionContent = { persons: InspectionPerson[]; rounds: InspectionRound[] };
export type InspectionAnnotation = InspectionContent & {
  revision: number;
  review_seconds: number;
  updated_at: string;
  origin?: string;
};
export type InspectionRecording = {
  id: string;
  name: string;
  source_group: string;
  source_type: "file" | "rtsp";
  created_at: string;
  state: "preparing" | "recording" | "ready" | "failed";
  error: string;
  active_task_id: string;
  duration_sec: number;
  frame_count: number;
  fps_hint: number;
  width: number;
  height: number;
  gaps?: { start_sec: number; end_sec: number }[];
  round_count?: number;
  approved_count?: number;
};
export type InspectionDetail = {
  recording: InspectionRecording;
  annotation: InspectionAnnotation;
  latest_draft: { run_id: string; round_count: number; elapsed_sec: number } | null;
};
export type PrelabelOptions = {
  window_sec: number;
  overlap_sec: number;
  sample_fps: number;
  image_max_side: number;
  action_hint: string;
};
export type InspectionConfig = {
  model: string;
  base_url: string;
  data_root: string;
  region_presets: { key: string; name: string }[];
  prelabel_defaults: PrelabelOptions;
};
