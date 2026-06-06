"""ArUco marker detection for parking bay identification."""

import logging
from dataclasses import dataclass, field

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

OCCUPIED_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class ArucoResult:
    occupied: bool
    car_number: int | None
    aruco_id_detected: int | None
    confidence: float


@dataclass
class ArucoDebugInfo:
    votes: dict[int, int] = field(default_factory=dict)
    best_confidence: dict[int, float] = field(default_factory=dict)
    attempts: int = 0
    used_flip: bool | None = None


def _make_detector_params() -> cv2.aruco.DetectorParameters:
    """Standard params — flip handles ESP32 mirror; avoid aggressive multi-pass tuning."""
    params = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 23
    params.adaptiveThreshWinSizeStep = 10
    params.minMarkerPerimeterRate = 0.01
    params.maxMarkerPerimeterRate = 4.0
    params.errorCorrectionRate = 0.6
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return params


def _get_detector(dictionary_name: str):
    dict_id = ARUCO_DICTIONARIES.get(dictionary_name, cv2.aruco.DICT_4X4_50)
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    return cv2.aruco.ArucoDetector(dictionary, _make_detector_params())


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
        area = cv2.contourArea(pts.astype(np.float32))
        if frame_area <= 0 or sum(side_lengths) <= 0:
            continue

        size_score = min(1.0, area / (frame_area * 0.005))
        ratio = max(side_lengths) / max(min(side_lengths), 1.0)
        shape_score = max(0.0, 1.0 - (ratio - 1.0) * 0.5)
        confidences.append(min(1.0, size_score * 0.6 + shape_score * 0.4))

    return round(max(confidences) if confidences else 0.0, 3)


def _best_detection_in_image(
    image: np.ndarray,
    dictionary_name: str,
) -> tuple[int | None, float, int]:
    """Single-pass detection on one orientation."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = _get_detector(dictionary_name)
    corners, ids, _rejected = detector.detectMarkers(gray)

    best_id: int | None = None
    best_conf = 0.0

    if ids is not None and len(ids) > 0:
        for idx, marker_id in enumerate(ids.flatten()):
            conf = _marker_confidence(corners[idx : idx + 1], image.shape)
            if conf > best_conf:
                best_conf = conf
                best_id = int(marker_id)

    return best_id, best_conf, 1


def _detect_with_flip(
    image: np.ndarray,
    dictionary_name: str,
) -> tuple[int | None, float, dict[int, int], dict[int, float], int, bool | None]:
    """Try normal and horizontally flipped image; pick highest-confidence hit."""
    normal_id, normal_conf, normal_attempts = _best_detection_in_image(
        image, dictionary_name
    )
    flipped_image = cv2.flip(image, 1)
    flipped_id, flipped_conf, flipped_attempts = _best_detection_in_image(
        flipped_image, dictionary_name
    )
    attempts = normal_attempts + flipped_attempts

    votes: dict[int, int] = {}
    best_confidence: dict[int, float] = {}
    for marker_id, conf in ((normal_id, normal_conf), (flipped_id, flipped_conf)):
        if marker_id is None:
            continue
        votes[marker_id] = votes.get(marker_id, 0) + 1
        best_confidence[marker_id] = max(best_confidence.get(marker_id, 0.0), conf)

    if normal_conf >= flipped_conf:
        winner_id, confidence, used_flip = normal_id, normal_conf, False if flipped_id else None
    else:
        winner_id, confidence, used_flip = flipped_id, flipped_conf, True

    if winner_id is None:
        used_flip = None

    return winner_id, confidence, votes, best_confidence, attempts, used_flip


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
    result, _debug = analyze_image_with_debug(image, fleet, dictionary_name)
    return result


def analyze_image_with_debug(
    image: np.ndarray,
    fleet: list[dict],
    dictionary_name: str = "DICT_4X4_50",
) -> tuple[ArucoResult, ArucoDebugInfo]:
    if image is None or image.size == 0:
        return ArucoResult(False, None, None, 0.0), ArucoDebugInfo()

    winner_id, confidence, votes, best_confidence, attempts, used_flip = (
        _detect_with_flip(image, dictionary_name)
    )
    debug = ArucoDebugInfo(
        votes=votes,
        best_confidence=best_confidence,
        attempts=attempts,
        used_flip=used_flip,
    )

    if winner_id is None or confidence < OCCUPIED_CONFIDENCE_THRESHOLD:
        logger.info(
            "Bay empty or below confidence threshold (dictionary=%s id=%s confidence=%s threshold=%s flip=%s)",
            dictionary_name,
            winner_id,
            confidence,
            OCCUPIED_CONFIDENCE_THRESHOLD,
            used_flip,
        )
        return ArucoResult(False, None, None, round(float(confidence), 3)), debug

    car_number = _match_fleet(winner_id, fleet)
    if car_number is None:
        logger.info(
            "ArUco id=%s detected but not in fleet (confidence=%s)",
            winner_id,
            confidence,
        )

    return ArucoResult(
        occupied=True,
        car_number=car_number,
        aruco_id_detected=winner_id,
        confidence=round(float(confidence), 3),
    ), debug
