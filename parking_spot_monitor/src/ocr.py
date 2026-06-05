"""License plate OCR and fleet matching."""

import logging
import re
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

PLATE_CHARS = re.compile(r"[^A-Z0-9]")


def normalize_plate(text: str) -> str:
    return PLATE_CHARS.sub("", text.upper())


@dataclass
class OCRResult:
    plate_read: str
    confidence: float
    occupied: bool
    car_number: int | None
    plate_matched: str | None


def _preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 11, 17, 17)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    return cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)


def _estimate_vehicle_present(image: np.ndarray) -> tuple[bool, float]:
    """Heuristic: non-uniform texture in zone suggests a vehicle is present."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    std_dev = float(np.std(gray))
    edge_density = float(np.mean(cv2.Canny(gray, 50, 150) > 0))

    score = min(1.0, (laplacian_var / 400.0 + std_dev / 50.0 + edge_density * 3.0) / 3.0)
    occupied = score > 0.25
    return occupied, round(score, 3)


def _ocr_plate(image: np.ndarray) -> tuple[str, float]:
    processed = _preprocess_for_ocr(image)
    config = "--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    data = pytesseract.image_to_data(
        processed, config=config, output_type=pytesseract.Output.DICT
    )

    texts = []
    confidences = []
    for text, conf in zip(data["text"], data["conf"]):
        cleaned = normalize_plate(text)
        if cleaned and conf != "-1":
            texts.append(cleaned)
            confidences.append(float(conf))

    if not texts:
        raw = pytesseract.image_to_string(processed, config=config)
        cleaned = normalize_plate(raw)
        if cleaned:
            return cleaned, 35.0
        return "", 0.0

    combined = max(texts, key=len)
    avg_conf = sum(confidences) / len(confidences)
    return combined, avg_conf


def _match_fleet(plate_read: str, fleet: list[dict]) -> tuple[int | None, str | None, float]:
    if not plate_read or not fleet:
        return None, None, 0.0

    choices = {normalize_plate(car["license_plate"]): car for car in fleet}
    match = process.extractOne(
        plate_read,
        list(choices.keys()),
        scorer=fuzz.ratio,
    )
    if not match:
        return None, None, 0.0

    matched_plate, score, _ = match
    car = choices[matched_plate]
    if score < 55:
        return None, None, score / 100.0

    return car["car_number"], car["license_plate"], score / 100.0


def analyze_zone(image: np.ndarray, fleet: list[dict]) -> OCRResult:
    occupied_heuristic, presence_score = _estimate_vehicle_present(image)
    plate_read, ocr_conf = _ocr_plate(image)
    car_number, plate_matched, match_conf = _match_fleet(plate_read, fleet)

    if plate_read and match_conf > 0:
        confidence = round((ocr_conf / 100.0 * 0.4) + (match_conf * 0.6), 3)
        occupied = True
    elif plate_read:
        confidence = round(ocr_conf / 100.0 * 0.7, 3)
        occupied = occupied_heuristic or len(plate_read) >= 4
    else:
        confidence = round(presence_score * 0.5, 3)
        occupied = occupied_heuristic and presence_score > 0.35
        plate_read = ""
        car_number = None
        plate_matched = None

    return OCRResult(
        plate_read=plate_read,
        confidence=confidence,
        occupied=occupied,
        car_number=car_number,
        plate_matched=plate_matched,
    )


def crop_zone(image: np.ndarray, points: list[dict]) -> np.ndarray:
    """Crop a polygon zone from image. Points are normalized 0-1 (x, y)."""
    h, w = image.shape[:2]
    absolute = np.array([[int(p["x"] * w), int(p["y"] * h)] for p in points], dtype=np.int32)

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [absolute], 255)
    masked = cv2.bitwise_and(image, image, mask=mask)

    x, y, bw, bh = cv2.boundingRect(absolute)
    return masked[y : y + bh, x : x + bw]
