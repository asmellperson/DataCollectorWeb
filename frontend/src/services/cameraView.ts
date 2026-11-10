import type { CameraView } from "../types";

export const defaultCameraView: CameraView = { zoom: 1, center_x: .5, center_y: .5 };

export function viewBounds(width: number, height: number, view: CameraView) {
  const w = Math.max(1, Math.round(width / view.zoom));
  const h = Math.max(1, Math.round(height / view.zoom));
  const x = Math.max(0, Math.min(width - w, Math.round(view.center_x * width - w / 2)));
  const y = Math.max(0, Math.min(height - h, Math.round(view.center_y * height - h / 2)));
  return { x, y, width: w, height: h };
}

// Input is a point within the displayed image, excluding letterboxing.
export function selectViewCenter(x: number, y: number, width: number, height: number, view: CameraView): CameraView {
  const crop = viewBounds(width, height, view);
  return { ...view,
    center_x: Math.max(0, Math.min(1, (crop.x + x * crop.width) / width)),
    center_y: Math.max(0, Math.min(1, (crop.y + y * crop.height) / height)),
  };
}
