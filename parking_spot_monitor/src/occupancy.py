"""Color-based bay occupancy from a center vertical slice of the camera frame."""

from dataclasses import dataclass

import cv2
import numpy as np

# Pixels in the vertical band at the horizontal center of the frame.
CENTER_SLICE_WIDTH = 50
# Fraction of slice pixels that must be red to treat the bay as occupied.
RED_RATIO_OCCUPIED_THRESHOLD = 0.22


@dataclass
class ColorOccupancyResult:
    occupied: bool
    red_ratio: float
    gray_ratio: float


def _center_slice(image: np.ndarray, width: int = CENTER_SLICE_WIDTH) -> np.ndarray:
    height, frame_width = image.shape[:2]
    if frame_width <= 0 or height <= 0:
        return image
    center_x = frame_width // 2
    half = max(1, width // 2)
    x0 = max(0, center_x - half)
    x1 = min(frame_width, center_x + half)
    return image[:, x0:x1]


def detect_occupied_by_color(image: np.ndarray) -> ColorOccupancyResult:
    """
    Occupied when the center slice is mostly red (car roof), empty when mostly gray (floor).
    """
    if image is None or image.size == 0:
        return ColorOccupancyResult(False, 0.0, 0.0)

    slice_bgr = _center_slice(image)
    hsv = cv2.cvtColor(slice_bgr, cv2.COLOR_BGR2HSV)

    # Red wraps around hue 0/180 on OpenCV HSV scale.
    red_low = cv2.inRange(hsv, np.array([0, 50, 40]), np.array([12, 255, 255]))
    red_high = cv2.inRange(hsv, np.array([168, 50, 40]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(red_low, red_high)

    # Low-saturation, mid-brightness pixels ≈ concrete floor.
    gray_mask = cv2.inRange(hsv, np.array([0, 0, 35]), np.array([180, 70, 210]))

    total = float(red_mask.size)
    red_ratio = float(np.count_nonzero(red_mask)) / total
    gray_ratio = float(np.count_nonzero(gray_mask)) / total

    occupied = red_ratio >= RED_RATIO_OCCUPIED_THRESHOLD
    return ColorOccupancyResult(
        occupied=occupied,
        red_ratio=round(red_ratio, 4),
        gray_ratio=round(gray_ratio, 4),
    )
