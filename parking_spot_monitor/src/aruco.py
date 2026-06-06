"""ArUco marker detection for parking bay identification."""

import logging
from collections import Counter
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

DETECT_SCALES = (1.0, 1.5, 2.0)
MIN_MARKER_CONFIDENCE = 0.12
MIN_VOTES = 2
HIGH_CONFIDENCE = 0.35


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


def _make_detector_params() -> cv2.aruco.DetectorParameters:
    """Tuned for noisy ESP32-CAM JPEG snapshots."""
    params = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 43
    params.adaptiveThreshWinSizeStep = 4
    params.adaptiveThreshConstant = 7
    params.minMarkerPerimeterRate = 0.003
    params.maxMarkerPerimeterRate = 4.0
    params.minOtsuStdDev = 0.0
    params.errorCorrectionRate = 0.6
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return params


def _get_detector(dictionary_name: str):
    dict_id = ARUCO_DICTIONARIES.get(dictionary_name, cv2.aruco.DICT_4X4_50)
    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    return cv2.aruco.ArucoDetector(dictionary, _make_detector_params())


def _gray_variants(gray: np.ndarray) -> list[np.ndarray]:
    variants = [gray]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    variants.append(enhanced)
    variants.append(cv2.fastNlMeansDenoising(enhanced, None, 8, 7, 21))
    sharp = cv2.addWeighted(
        enhanced, 1.3, cv2.GaussianBlur(enhanced, (0, 0), 2), -0.3, 0
    )
    variants.append(sharp)
    return variants


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


def _collect_detections(
    image: np.ndarray,
    dictionary_name: str,
) -> tuple[Counter, dict[int, float], dict[int, np.ndarray], int]:
    """Run multi-scale detection and aggregate votes per marker ID."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = _get_detector(dictionary_name)
    votes: Counter = Counter()
    best_confidence: dict[int, float] = {}
    best_corners: dict[int, np.ndarray] = {}
    attempts = 0

    for scale in DETECT_SCALES:
        scaled = (
            cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if scale > 1.0
            else gray
        )
        for variant in _gray_variants(scaled):
            attempts += 1
            corners, ids, _rejected = detector.detectMarkers(variant)
            if ids is None or len(ids) == 0:
                continue

            for idx, marker_id in enumerate(ids.flatten()):
                conf = _marker_confidence(corners[idx : idx + 1], image.shape)
                if conf < MIN_MARKER_CONFIDENCE:
                    continue

                marker_id = int(marker_id)
                votes[marker_id] += 1
                if conf > best_confidence.get(marker_id, 0.0):
                    best_confidence[marker_id] = conf
                    best_corners[marker_id] = corners[idx : idx + 1]

    return votes, best_confidence, best_corners, attempts


def _pick_winner(
    votes: Counter,
    best_confidence: dict[int, float],
) -> tuple[int | None, float]:
    if not votes:
        return None, 0.0

    ranked = sorted(
        votes.keys(),
        key=lambda marker_id: (votes[marker_id], best_confidence.get(marker_id, 0.0)),
        reverse=True,
    )
    winner = ranked[0]
    conf = best_confidence.get(winner, 0.0)
    vote_count = votes[winner]

    if vote_count >= MIN_VOTES or conf >= HIGH_CONFIDENCE:
        return winner, conf

    logger.debug(
        "Rejected weak ArUco candidate id=%s votes=%s confidence=%s",
        winner,
        vote_count,
        conf,
    )
    return None, 0.0


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

    votes, best_confidence, _best_corners, attempts = _collect_detections(
        image, dictionary_name
    )
    debug = ArucoDebugInfo(
        votes=dict(votes),
        best_confidence=best_confidence,
        attempts=attempts,
    )

    winner_id, confidence = _pick_winner(votes, best_confidence)
    if winner_id is None:
        logger.info(
            "No ArUco marker accepted (dictionary=%s votes=%s attempts=%s)",
            dictionary_name,
            dict(votes),
            attempts,
        )
        return ArucoResult(False, None, None, 0.0), debug

    car_number = _match_fleet(winner_id, fleet)
    if car_number is None:
        logger.info(
            "ArUco id=%s detected but not in fleet (votes=%s confidence=%s)",
            winner_id,
            votes[winner_id],
            confidence,
        )

    return ArucoResult(
        occupied=True,
        car_number=car_number,
        aruco_id_detected=winner_id,
        confidence=confidence if car_number is not None else round(confidence * 0.5, 3),
    ), debug
