import type { AnnotationObject, BBox, FacePoint } from "../types";

// Left/right refer to the image, following SCRFD's output order.
export const facePointNames = ["左侧眼", "右侧眼", "鼻尖", "左侧嘴角", "右侧嘴角"];
export const facePointColors = ["#ff6666", "#66ddff", "#ffdd55", "#88ff88", "#dd88ff"];

export function facePointRadius(bbox: BBox, scale: number, highlighted = false): number {
  const zoom = Math.max(scale, 0.0001);
  const faceSize = Math.min(bbox[2] - bbox[0], bbox[3] - bbox[1]) * zoom;
  // A small face gets smaller visible dots; the separate hit area stays usable.
  const pixels = Math.max(0.65, Math.min(highlighted ? 2.5 : 1.8, faceSize * 0.025));
  return pixels / zoom;
}

export function moveFaceBox(obj: AnnotationObject, bbox: BBox, width: number, height: number): AnnotationObject {
  const [a, b, c, d] = obj.bbox;
  const [x, y, right, bottom] = bbox;
  return {
    ...obj, bbox, source: "manual", confidence: null,
    ...(obj.keypoints ? {
      keypoints: obj.keypoints.map((point): FacePoint => point === null ? null : [
        Math.max(0, Math.min(width, x + (point[0] - a) * (right - x) / (c - a))),
        Math.max(0, Math.min(height, y + (point[1] - b) * (bottom - y) / (d - b))),
      ]),
    } : {}),
  };
}

export function setFacePoint(obj: AnnotationObject, index: number, point: FacePoint): AnnotationObject {
  const points = [...(obj.keypoints ?? Array<FacePoint>(5).fill(null))];
  points[index] = point;
  return { ...obj, keypoints: points, source: "manual", confidence: null };
}
