export type ClassItem = { id: number; name: string };
// Text is kept while editing so commas and partially entered IDs are not lost.
export type ClassMapping = Record<number, number | number[] | string>;
export type Dataset = {
  id: string;
  name: string;
  task_type: "raw" | "classification" | "detection";
  classes: ClassItem[];
  total: number;
  pending: number;
  approved: number;
  rejected: number;
};
export type Page = "datasets" | "collection" | "review" | "statistics" | "cameras" | "security-annotation";
export type BBox = [number, number, number, number];
export type Camera = {
  id: string;
  name: string;
  url: string;
  transport: string;
};
export type CollectionSession = {
  camera_id: string;
  dataset_id: string;
  task_id: string;
  active: boolean;
  stopping: boolean;
  detecting: boolean;
  mode: string;
  saved: number;
  labeled: number;
  duplicates: number;
  error: string;
  config: {
    interval: number;
    roi: BBox | null;
    auto_label: string;
    detector: string;
    model_path: string | null;
    trigger_model_path: string | null;
    target_classes: string[];
    label_class_ids: number[] | null;
    label_class_mapping: ClassMapping;
    confidence: number;
    similarity: number;
  };
};
export type CollectionStatus = {
  active: boolean;
  active_count: number;
  max_cameras: number;
  sessions: CollectionSession[];
};
export type AnnotationObject = {
  id: string;
  class_id: number;
  class_name: string;
  bbox: BBox;
  confidence: number | null;
  source: "manual" | "yolo_world" | "custom_yolo";
};
export type Annotation = {
  id: string;
  image: string;
  image_url: string;
  preview_url: string;
  thumbnail_url: string;
  width: number;
  height: number;
  source: Record<string, unknown>;
  status: "pending" | "approved" | "rejected";
  objects: AnnotationObject[];
  classification: { class_id: number; class_name: string } | null;
  revision: number;
};
