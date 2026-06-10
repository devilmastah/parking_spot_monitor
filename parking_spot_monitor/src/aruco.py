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

# Contrast multipliers for glare / reflection fallback passes.
CONTRAST_STEPS = (1.3, 1.7, 2.2)


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
    preprocess_pass: str | None = None


def _make_detector_params() -> cv2.aruco.DetectorParameters:
    """Tuned for small roof markers; multi-pass preprocessing handles glare."""
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


def _to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _gray_as_bgr(gray: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _preprocess_passes(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Detection variants: normal → enhanced B&W → stepped contrast."""
    bgr = _to_bgr(image)
    gray = _to_gray(bgr)
    passes: list[tuple[str, np.ndarray]] = [("normal", bgr)]

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    passes.append(("bw_clahe", _gray_as_bgr(clahe.apply(gray))))

    # Denoise then local contrast — helps tape/glare on shiny surfaces.
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)
    clahe_strong = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    passes.append(("bw_bilateral_clahe", _gray_as_bgr(clahe_strong.apply(denoised))))

    for step, alpha in enumerate(CONTRAST_STEPS, start=1):
        adjusted = cv2.convertScaleAbs(gray, alpha=alpha, beta=0)
        passes.append((f"contrast_{step}", _gray_as_bgr(adjusted)))

    # Softer contrast for blown highlights near windows/reflections.
    passes.append(("contrast_soft", _gray_as_bgr(cv2.convertScaleAbs(gray, alpha=0.85, beta=18))))

    return passes


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
    gray = _to_gray(image)
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


def _merge_detection_stats(
    target_votes: dict[int, int],
    target_best: dict[int, float],
    votes: dict[int, int],
    best_confidence: dict[int, float],
) -> None:
    for marker_id, count in votes.items():
        target_votes[marker_id] = target_votes.get(marker_id, 0) + count
    for marker_id, conf in best_confidence.items():
        target_best[marker_id] = max(target_best.get(marker_id, 0.0), conf)


@dataclass
class _PassDetection:
    marker_id: int
    confidence: float
    preprocess_pass: str
    used_flip: bool | None


def _pick_best_detection(
    hits: list[_PassDetection],
    fleet: list[dict],
) -> _PassDetection | None:
    if not hits:
        return None

    fleet_ids = {car["aruco_id"] for car in fleet}
    best_any = max(hits, key=lambda h: h.confidence)
    if not fleet_ids:
        return best_any

    fleet_hits = [h for h in hits if h.marker_id in fleet_ids]
    if not fleet_hits:
        return best_any

    best_fleet = max(fleet_hits, key=lambda h: h.confidence)
    # Prefer a known fleet marker when confidence is close (reduces glare false positives).
    if best_fleet.confidence >= best_any.confidence - 0.12:
        return best_fleet
    return best_any


def _detect_multi_pass(
    image: np.ndarray,
    dictionary_name: str,
    fleet: list[dict] | None = None,
) -> tuple[int | None, float, dict[int, int], dict[int, float], int, bool | None, str | None]:
    """Run normal → B&W CLAHE → contrast steps until a confident hit or all exhausted."""
    hits: list[_PassDetection] = []
    total_votes: dict[int, int] = {}
    total_best: dict[int, float] = {}
    total_attempts = 0

    for pass_name, variant in _preprocess_passes(image):
        (
            marker_id,
            confidence,
            votes,
            best_confidence,
            attempts,
            used_flip,
        ) = _detect_with_flip(variant, dictionary_name)
        total_attempts += attempts
        _merge_detection_stats(total_votes, total_best, votes, best_confidence)

        if marker_id is not None:
            hits.append(
                _PassDetection(marker_id, confidence, pass_name, used_flip)
            )

        best_so_far = _pick_best_detection(hits, fleet or [])
        if (
            best_so_far is not None
            and best_so_far.confidence >= OCCUPIED_CONFIDENCE_THRESHOLD
        ):
            logger.debug(
                "ArUco detected on pass=%s id=%s confidence=%s flip=%s",
                best_so_far.preprocess_pass,
                best_so_far.marker_id,
                best_so_far.confidence,
                best_so_far.used_flip,
            )
            break

    winner = _pick_best_detection(hits, fleet or [])
    if winner is None:
        return None, 0.0, total_votes, total_best, total_attempts, None, None

    return (
        winner.marker_id,
        winner.confidence,
        total_votes,
        total_best,
        total_attempts,
        winner.used_flip,
        winner.preprocess_pass,
    )


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

    (
        winner_id,
        confidence,
        votes,
        best_confidence,
        attempts,
        used_flip,
        preprocess_pass,
    ) = _detect_multi_pass(image, dictionary_name, fleet=fleet)
    debug = ArucoDebugInfo(
        votes=votes,
        best_confidence=best_confidence,
        attempts=attempts,
        used_flip=used_flip,
        preprocess_pass=preprocess_pass,
    )

    if winner_id is None or confidence < OCCUPIED_CONFIDENCE_THRESHOLD:
        logger.info(
            "Bay empty or below confidence threshold (dictionary=%s id=%s confidence=%s "
            "threshold=%s votes=%s flip=%s pass=%s attempts=%s)",
            dictionary_name,
            winner_id,
            confidence,
            OCCUPIED_CONFIDENCE_THRESHOLD,
            votes.get(winner_id, 0) if winner_id is not None else 0,
            used_flip,
            preprocess_pass,
            attempts,
        )
        return ArucoResult(False, None, None, round(float(confidence), 3)), debug

    car_number = _match_fleet(winner_id, fleet)
    if car_number is None:
        logger.info(
            "ArUco id=%s detected but not in fleet (confidence=%s pass=%s)",
            winner_id,
            confidence,
            preprocess_pass,
        )
    else:
        logger.info(
            "ArUco id=%s car=%s confidence=%s pass=%s flip=%s",
            winner_id,
            car_number,
            confidence,
            preprocess_pass,
            used_flip,
        )

    return ArucoResult(
        occupied=True,
        car_number=car_number,
        aruco_id_detected=winner_id,
        confidence=round(float(confidence), 3),
    ), debug
