"""The same original-pixel crop is used for preview, inference and saved samples."""

import cv2

DEFAULT_VIEW = {"zoom": 1.0, "center_x": 0.5, "center_y": 0.5}


def crop_bounds(width, height, view):
    zoom = view["zoom"]
    # Explicit half-up rounding matches the browser's Math.round.
    crop_w = max(1, int(width / zoom + 0.5))
    crop_h = max(1, int(height / zoom + 0.5))
    left = max(0, min(width - crop_w, int(view["center_x"] * width - crop_w / 2 + 0.5)))
    top = max(0, min(height - crop_h, int(view["center_y"] * height - crop_h / 2 + 0.5)))
    return [left, top, left + crop_w, top + crop_h]


def apply_view(frame, view):
    height, width = frame.shape[:2]
    area = crop_bounds(width, height, view)
    if view["zoom"] <= 1:
        return frame, area
    left, top, right, bottom = area
    return cv2.resize(frame[top:bottom, left:right], (width, height)), area
