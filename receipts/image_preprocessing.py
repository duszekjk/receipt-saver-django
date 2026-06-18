from pathlib import Path
import cv2
import numpy as np


def _order_points(points: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype='float32')
    s = points.sum(axis=1)
    rect[0] = points[np.argmin(s)]
    rect[2] = points[np.argmax(s)]
    diff = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diff)]
    rect[3] = points[np.argmax(diff)]
    return rect


def _four_point_transform(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    rect = _order_points(points)
    tl, tr, br, bl = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = int(max(width_a, width_b))
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = int(max(height_a, height_b))
    dst = np.array([
        [0, 0],
        [max_width - 1, 0],
        [max_width - 1, max_height - 1],
        [0, max_height - 1],
    ], dtype='float32')
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def crop_receipt_best_effort(image_path: str) -> str:
    """Finds a receipt-like quadrilateral and writes a cropped copy.

    Returns the cropped file path when successful, otherwise returns the original path.
    This is intentionally conservative: a bad crop is worse than no crop for OCR.
    """
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return image_path

    original = image.copy()
    ratio = image.shape[0] / 700.0
    resized = cv2.resize(image, (int(image.shape[1] / ratio), 700))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 40, 140)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

    page_contour = None
    image_area = resized.shape[0] * resized.shape[1]
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        area = cv2.contourArea(approx)
        if len(approx) == 4 and area > image_area * 0.18:
            page_contour = approx.reshape(4, 2) * ratio
            break

    if page_contour is None:
        return image_path

    warped = _four_point_transform(original, page_contour.astype('float32'))
    if warped.shape[0] < 600 or warped.shape[1] < 250:
        return image_path

    gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    gray_warped = cv2.resize(gray_warped, None, fx=1.0, fy=1.0, interpolation=cv2.INTER_AREA)
    output_path = path.with_name(f'{path.stem}_cropped.jpg')
    cv2.imwrite(str(output_path), gray_warped, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return str(output_path)
