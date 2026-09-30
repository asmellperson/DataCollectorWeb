export type ClassItem = { id: number; name: string };
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
export type Page = "datasets" | "collection" | "review" | "cameras";
export type BBox = [number, number, number, number];
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
