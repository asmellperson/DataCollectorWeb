export type ReviewStatus = "pending" | "approved" | "rejected";
export type StatisticsClass = {
  id: number;
  name: string;
  box_count: number;
  image_count: number;
  box_share: number;
  image_coverage: number;
  average_boxes: number;
  max_boxes: number;
  pending: number;
  approved: number;
  rejected: number;
  small: number;
  medium: number;
  large: number;
};
export type StatisticsSample = {
  id: string;
  image: string;
  filename: string;
  width: number;
  height: number;
  status: ReviewStatus;
  box_count: number;
  class_ids: number[];
  annotation_ok: boolean;
  empty: boolean;
  image_exists: boolean;
  reviewable: boolean;
  issues: Record<string, number>;
};
export type Distribution = { name: string; count: number }[];
export type DatasetStatistics = {
  dataset_id: string;
  dataset_name: string;
  task_type: "raw" | "classification" | "detection";
  generated_at: string;
  scope: { status: ReviewStatus | null; dataset_total: number };
  overview: {
    images: number;
    annotations_readable: number;
    images_with_labels: number;
    empty_images: number;
    confirmed_negative: number;
    pending_empty: number;
    boxes: number;
    invalid_records: number;
    missing_images: number;
    issue_images: number;
    approved: number;
    pending: number;
    rejected: number;
    average_boxes: number;
    median_boxes: number;
    max_boxes: number;
    configured_classes: number;
    present_classes: number;
  };
  classes: StatisticsClass[];
  distributions: Record<
    | "boxes_per_image"
    | "resolutions"
    | "orientations"
    | "formats"
    | "sources"
    | "box_sources",
    Distribution
  >;
  quality: {
    code: string;
    name: string;
    severity: "warning" | "error";
    count: number;
    image_count: number;
  }[];
  samples: StatisticsSample[];
};

export type ImageSelection =
  | { kind: "all" }
  | { kind: "class"; id: number }
  | { kind: "issue"; code: string }
  | { kind: "negative" }
  | { kind: "pending_empty" }
  | { kind: "issues" }
  | { kind: "bucket"; name: string };
