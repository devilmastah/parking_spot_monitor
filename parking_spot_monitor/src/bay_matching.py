"""Match detected cars against the car assigned to each bay."""

CORRECT_CAR_YES = "yes"
CORRECT_CAR_NO = "no"
CORRECT_CAR_UNCERTAIN = "uncertain"
CORRECT_CAR_UNKNOWN = "unknown"

VALID_CORRECT_CAR = {
    CORRECT_CAR_YES,
    CORRECT_CAR_NO,
    CORRECT_CAR_UNCERTAIN,
    CORRECT_CAR_UNKNOWN,
}


def _expected_aruco_id(expected_car_number: int, fleet: list[dict]) -> int | None:
    for car in fleet:
        if car["car_number"] == expected_car_number:
            return car["aruco_id"]
    return None


def has_detected_marker(
    aruco_id_detected: int | None,
    car_number_detected: int | None,
) -> bool:
    return aruco_id_detected is not None or car_number_detected is not None


def compute_correct_car(
    occupied: bool,
    car_number_detected: int | None,
    aruco_id_detected: int | None,
    expected_car_number: int | None,
    fleet: list[dict],
) -> str:
    """Return whether the expected car is in this bay."""
    if expected_car_number is None:
        return CORRECT_CAR_UNCERTAIN

    if not occupied:
        return CORRECT_CAR_NO

    if not has_detected_marker(aruco_id_detected, car_number_detected):
        return CORRECT_CAR_UNKNOWN

    if car_number_detected == expected_car_number:
        return CORRECT_CAR_YES

    expected_aruco = _expected_aruco_id(expected_car_number, fleet)
    if expected_aruco is not None and aruco_id_detected == expected_aruco:
        return CORRECT_CAR_YES

    return CORRECT_CAR_NO
