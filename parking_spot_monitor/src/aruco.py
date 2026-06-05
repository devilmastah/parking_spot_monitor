"""ArUco marker detection for parking bay identification."""

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ARUCO_DICTIONARIES = {
    "DICT_4X4_50": cv2.aruco.DICT_4X4_50,
    "DICT_4X4_100": cv2.aruco.DICT_4X4_100,
    "DICT_4X4_250": cv2.aruco.DICT_4X4_250,
    "DICT_5X5_50": cv2.aruco.DICT_5X5_50,
    "DICT_5X5_100": cv2.aruco.DICT_5X5_100,
    "DICT_5X5_250": cv2.aruco.DICT_5X5_250,
    "DICT_6X6_50": cv2.aruco.DICT_6X6_50,
    "DICT_6X6_100": cv2.aruco.DICT_6X6_100,
    "DICT_6X6_250": cv2.aruco.DICT_6X6_250,
    "DICT_7X7_100": cv2.aruco.DICT_7X7_100,
    "DICT_7X7_250": cv2.aruco.DICT_7X7_250,
}


@dataclass
class ArucoResult:
    occupied: bool
    car_number: int | None
    aruco_id_detected: int | None
    confidence: float


def _get_detector(dictionary_name: str):
    dict_id = ARUCO_DICTIONARIES.get(dictionary_name, cv2.aruco.DICT_4X4_50)
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    params = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 23
    params.adaptiveThreshWinSizeStep = 10
    params.minMarkerPerimeterRate = 0.03
    params.maxMarkerPerimeterRate = 4.0
    return cv2.aruco.ArucoDetector(dictionary, params)


def _marker_confidence(corners: np.ndarray, image_shape: tuple[int, ...]) -> float:
    """Estimate detection quality from marker size and shape in the frame."""
    if corners is None or len(corners) == 0:
        return 0.0

    height, width = image_shape[:2]
    frame_area = float(width * height)
    confidences = []

    for corner in corners:
        pts = corner.reshape(4, 2)
        side_lengths = [
            np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)
        ]
        perimeter = sum(side_lengths)
        area = cv2.contourArea(pts.astype(np.float32))
        if frame_area <= 0 or perimeter <= 0:
            continue

        size_score = min(1.0, area / (frame_area * 0.02))
        ratio = max(side_lengths) / max(min(side_lengths), 1.0)
        shape_score = max(0.0, 1.0 - (ratio - 1.0) * 0.5)
        confidences.append(min(1.0, size_score * 0.6 + shape_score * 0.4))

    return round(max(confidences) if confidences else 0.0, 3)


def _match_fleet(aruco_id: int, fleet: list[dict]) -> int | None:
    for car in fleet:
        if car["aruco_id"] == aruco_id:
            return car["car_number"]
    return None


def analyze_image(
    image: np.ndarray,
    fleet: list[dict],
    dictionary_name: str = "DICT_4X4_50",
) -> ArucoResult:
    if image is None or image.size == 0:
        return ArucoResult(False, None, None, 0.0)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = _get_detector(dictionary_name)
    corners, ids, _rejected = detector.detectMarkers(gray)

    if ids is None or len(ids) == 0:
        return ArucoResult(False, None, None, 0.0)

    best_id = None
    best_conf = 0.0
    best_corners = None

    for idx, marker_id in enumerate(ids.flatten()):
        conf = _marker_confidence(corners[idx : idx + 1], image.shape)
        if conf > best_conf:
            best_conf = conf
            best_id = int(marker_id)
            best_corners = corners[idx : idx + 1]

    if best_id is None:
        return ArucoResult(False, None, None, 0.0)

    if best_corners is not None:
        best_conf = max(best_conf, _marker_confidence(best_corners, image.shape))

    car_number = _match_fleet(best_id, fleet)
    confidence = best_conf if car_number is not None else round(best_conf * 0.5, 3)

    return ArucoResult(
        occupied=True,
        car_number=car_number,
        aruco_id_detected=best_id,
        confidence=confidence,
    )
