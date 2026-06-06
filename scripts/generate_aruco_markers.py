#!/usr/bin/env python3
"""Generate printable ArUco markers for the parking fleet."""

import argparse
import os

import cv2
from cv2 import aruco


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ArUco marker PNG files.")
    parser.add_argument(
        "--dictionary",
        default="DICT_4X4_50",
        help="OpenCV dictionary name (default: DICT_4X4_50)",
    )
    parser.add_argument("--start", type=int, default=1, help="First marker ID")
    parser.add_argument("--count", type=int, default=10, help="How many markers")
    parser.add_argument("--size", type=int, default=200, help="Marker pixel size")
    parser.add_argument(
        "--border",
        type=int,
        default=30,
        help="White quiet zone around marker (required for reliable detection)",
    )
    parser.add_argument("--output", default="aruco_markers", help="Output directory")
    args = parser.parse_args()

    dict_id = getattr(aruco, args.dictionary)
    dictionary = aruco.getPredefinedDictionary(dict_id)
    os.makedirs(args.output, exist_ok=True)

    for marker_id in range(args.start, args.start + args.count):
        marker = aruco.generateImageMarker(dictionary, marker_id, args.size)
        marker = cv2.copyMakeBorder(
            marker,
            args.border,
            args.border,
            args.border,
            args.border,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        path = os.path.join(args.output, f"aruco_{args.dictionary}_{marker_id}.png")
        cv2.imwrite(path, marker)
        print(path)

    print(
        f"\nGenerated {args.count} markers using {args.dictionary}. "
        "Print at 100% scale on matte paper — do not photograph a monitor."
    )


if __name__ == "__main__":
    main()
