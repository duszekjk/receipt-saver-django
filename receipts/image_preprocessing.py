from pathlib import Path
import os
import cv2
import numpy as np

_DETECTOR = None


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
    dst = np.array([[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]], dtype='float32')
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def _save_crop(path: Path, image: np.ndarray, suffix: str) -> str:
    output_path = path.with_name(f'{path.stem}_{suffix}.jpg')
    cv2.imwrite(str(output_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    return str(output_path)


def _crop_by_zero_shot_detector(image_path: str) -> str | None:
    """Optional open-vocabulary detector.

    Disabled by default because OWL-ViT/GroundingDINO-class models are too heavy
    for a small CPU-only server. Enable only for experiments with:
    RECEIPT_DETECTOR_ENABLED=1 and requirements-ml.txt installed.
    """
    if os.getenv('RECEIPT_DETECTOR_ENABLED', '0') != '1':
        return None

    global _DETECTOR
    try:
        from PIL import Image
        from transformers import pipeline
    except Exception:
        return None

    model_name = os.getenv('RECEIPT_DETECTOR_MODEL', 'google/owlvit-base-patch32')
    prompts = os.getenv('RECEIPT_DETECTOR_PROMPTS', 'paper receipt,receipt,document').split(',')
    min_score = float(os.getenv('RECEIPT_DETECTOR_MIN_SCORE', '0.12'))

    try:
        if _DETECTOR is None:
            _DETECTOR = pipeline('zero-shot-object-detection', model=model_name)
        pil_image = Image.open(image_path).convert('RGB')
        predictions = _DETECTOR(pil_image, candidate_labels=[p.strip() for p in prompts if p.strip()])
    except Exception:
        return None

    predictions = [p for p in predictions if float(p.get('score', 0)) >= min_score]
    if not predictions:
        return None

    best = max(predictions, key=lambda p: float(p.get('score', 0)))
    box = best.get('box') or {}
    x1 = int(max(0, box.get('xmin', 0)))
    y1 = int(max(0, box.get('ymin', 0)))
    x2 = int(min(pil_image.width, box.get('xmax', pil_image.width)))
    y2 = int(min(pil_image.height, box.get('ymax', pil_image.height)))

    if x2 - x1 < 250 or y2 - y1 < 500:
        return None

    margin_x = int((x2 - x1) * 0.04)
    margin_y = int((y2 - y1) * 0.04)
    x1 = max(0, x1 - margin_x)
    y1 = max(0, y1 - margin_y)
    x2 = min(pil_image.width, x2 + margin_x)
    y2 = min(pil_image.height, y2 + margin_y)

    cropped = np.array(pil_image.crop((x1, y1, x2, y2)))
    gray = cv2.cvtColor(cropped, cv2.COLOR_RGB2GRAY)
    return _save_crop(Path(image_path), gray, 'detected')


def _crop_by_contours(image_path: str) -> str | None:
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        return None

    original = image.copy()
    ratio = image.shape[0] / 700.0
    resized = cv2.resize(image, (int(image.shape[1] / ratio), 700))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 40, 140)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]

    image_area = resized.shape[0] * resized.shape[1]
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        area = cv2.contourArea(approx)
        if len(approx) == 4 and area > image_area * 0.18:
            points = approx.reshape(4, 2) * ratio
            warped = _four_point_transform(original, points.astype('float32'))
            if warped.shape[0] >= 600 and warped.shape[1] >= 250:
                gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
                return _save_crop(path, gray_warped, 'contour')
    return None


def crop_receipt_best_effort(image_path: str) -> str:
    detected = _crop_by_zero_shot_detector(image_path)
    if detected:
        return detected
    contour = _crop_by_contours(image_path)
    return contour or image_path
