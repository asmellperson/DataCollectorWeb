import type { Annotation, AnnotationObject } from "../types";
import { api } from "./api";

export type AnnotationDraft = Pick<Annotation, "objects" | "classification">;

function objectValue(object: AnnotationObject) {
  return [
    object.id,
    object.class_id,
    object.bbox,
    object.confidence,
    object.source,
  ];
}

function matchesDraft(annotation: Annotation, draft: AnnotationDraft) {
  return (
    annotation.classification?.class_id === draft.classification?.class_id &&
    JSON.stringify(annotation.objects.map(objectValue)) ===
      JSON.stringify(draft.objects.map(objectValue))
  );
}

export async function saveAnnotation(
  base: string,
  sample: Annotation,
  draft: AnnotationDraft,
): Promise<Annotation> {
  const path = `${base}/samples/${sample.id}`;
  try {
    return await api<Annotation>(`${path}/annotation`, {
      method: "PUT",
      body: JSON.stringify({
        revision: sample.revision,
        objects: draft.objects,
        classification: draft.classification
          ? {
              id: draft.classification.class_id,
              name: draft.classification.class_name,
            }
          : null,
      }),
    });
  } catch (error) {
    // A response may be lost after the server commits. Read back before asking
    // the user to retry; never overwrite a newer, different annotation.
    const current = await api<Annotation>(path, { cache: "no-store" }).catch(
      () => null,
    );
    if (
      current &&
      current.id === sample.id &&
      current.revision > sample.revision &&
      matchesDraft(current, draft)
    )
      return current;
    throw error;
  }
}
