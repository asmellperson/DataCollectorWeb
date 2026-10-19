import type { ClassItem, ClassMapping, Dataset, Page } from "../types";
import { parseSourceIds } from "./classMapping";

export const preferenceKey = (datasetId: string, section: string) =>
  `dcw:${section}:v1:${datasetId}`;

export function readPreference(
  datasetId: string,
  section: string,
): Record<string, unknown> {
  try {
    const value = JSON.parse(
      localStorage.getItem(preferenceKey(datasetId, section)) || "null",
    );
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}

export function writePreference(
  datasetId: string,
  section: string,
  value: unknown,
): boolean {
  try {
    localStorage.setItem(
      preferenceKey(datasetId, section),
      JSON.stringify(value),
    );
    return true;
  } catch {
    // Restricted storage must not prevent labeling or saving annotations.
    return false;
  }
}

export function readReviewPreferences(dataset: Dataset) {
  const value = readPreference(dataset.id, "review");
  return {
    sampleId:
      typeof value.sampleId === "string" && value.sampleId.length <= 100
        ? value.sampleId
        : null,
    index:
      Number.isSafeInteger(value.index) && Number(value.index) >= 0
        ? Number(value.index)
        : 0,
    filter: ["pending", "approved", "rejected"].includes(String(value.filter))
      ? String(value.filter)
      : "",
    classId: dataset.classes.some((c) => c.id === value.classId)
      ? Number(value.classId)
      : (dataset.classes[0]?.id ?? 0),
  };
}

export function readAutoLabelPreferences(
  datasetId: string,
  classes: ClassItem[],
) {
  const value = readPreference(datasetId, "auto-label");
  const known = new Set(classes.map((c) => c.id));
  const mapping: ClassMapping = {};
  if (
    value.classMapping &&
    typeof value.classMapping === "object" &&
    !Array.isArray(value.classMapping)
  ) {
    for (const [id, source] of Object.entries(value.classMapping)) {
      if (!known.has(Number(id))) continue;
      try {
        const ids = parseSourceIds(source);
        if (ids) mapping[Number(id)] = ids.length === 1 ? ids[0] : ids;
      } catch {
        /* Ignore corrupt saved entries without affecting annotation. */
      }
    }
  }
  return {
    provider: value.provider === "custom_yolo" ? "custom_yolo" : "yolo_world",
    path: typeof value.path === "string" ? value.path : "",
    confidence:
      typeof value.confidence === "number" &&
      value.confidence > 0 &&
      value.confidence <= 1
        ? value.confidence
        : 0.25,
    overwrite: value.overwrite === true,
    labelClasses: Array.isArray(value.labelClasses)
      ? [
          ...new Set(
            value.labelClasses.filter(
              (id): id is number => typeof id === "number" && known.has(id),
            ),
          ),
        ]
      : null,
    classMapping: mapping,
  };
}

export function readWorkspacePreferences(): { page: Page; selected: string } {
  const value = readPreference("", "workspace");
  return {
    page: ["datasets", "collection", "review", "statistics", "cameras", "security-annotation"].includes(
      String(value.page),
    )
      ? (value.page as Page)
      : "datasets",
    selected: typeof value.selected === "string" ? value.selected : "",
  };
}
