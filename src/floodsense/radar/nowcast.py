"""
FloodSense - Radar nowcast: estimate storm motion from recent frames and extrapolate rain ahead.

Motion: dense optical flow (Farnebäck, OpenCV) between consecutive frames, computed on the radar's
intensity levels (already logarithmic, 0.68 dB per level), averaged over the last two frame pairs.
The flow is spread from rainy pixels to their surroundings (normalised convolution), so rain near a
storm's edge moves with the storm instead of standing still.

Extrapolation: semi-Lagrangian, steady motion. The rain field at lead ``k`` steps is the current field
sampled ``k`` motion vectors upstream. Rain is advected, not grown or decayed, so the nowcast says
where today's rain is heading, not whether it intensifies.

Needs the optional ``radar`` extra (``opencv-python-headless``).
"""

import numpy as np

STEP_MINUTES = 5


def _cv2():  # type: ignore[no-untyped-def]
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "The radar nowcast needs the 'radar' extra: uv sync --extra radar"
        ) from exc
    return cv2


def _to_image(levels: np.ndarray) -> np.ndarray:
    """Intensity levels (0..33) as a smoothed 8-bit image for optical flow."""
    cv2 = _cv2()
    img = np.clip(levels.astype(np.float32) * 7.0, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (5, 5), 1.0)


def motion_field(levels: list[np.ndarray], spread_sigma: float = 12.0) -> np.ndarray:
    """Motion per 5-minute step, ``(H, W, 2)`` as (dx, dy) pixels, from 2+ consecutive frames."""
    if len(levels) < 2:
        raise ValueError("motion needs at least two consecutive frames")
    cv2 = _cv2()
    flows, weights = [], []
    for prev, curr in zip(levels[:-1], levels[1:], strict=True):
        flow = cv2.calcOpticalFlowFarneback(
            _to_image(prev), _to_image(curr), None,
            pyr_scale=0.5, levels=4, winsize=31, iterations=3, poly_n=7, poly_sigma=1.5, flags=0,
        )  # fmt: skip
        mask = ((prev > 0) | (curr > 0)).astype(np.float32)
        flows.append(flow * mask[..., None])
        weights.append(mask)
    flow_sum = np.sum(flows, axis=0)
    weight = np.sum(weights, axis=0)
    # Normalised convolution: spread motion from rainy pixels to their neighbourhood.
    k = int(spread_sigma * 3) | 1
    num = np.dstack([cv2.GaussianBlur(flow_sum[..., i], (k, k), spread_sigma) for i in range(2)])
    den = cv2.GaussianBlur(weight, (k, k), spread_sigma)
    with np.errstate(invalid="ignore", divide="ignore"):
        motion = np.where(den[..., None] > 1e-3, num / den[..., None], 0.0)
    return motion.astype(np.float32)


def extrapolate(rates: np.ndarray, motion: np.ndarray, steps: int) -> list[np.ndarray]:
    """Rain-rate fields at leads 1..``steps`` (5-minute steps), advected by ``motion``."""
    cv2 = _cv2()
    h, w = rates.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    src = rates.astype(np.float32)
    out = []
    for k in range(1, steps + 1):
        map_x = xs - k * motion[..., 0]
        map_y = ys - k * motion[..., 1]
        out.append(
            cv2.remap(src, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        )  # fmt: skip
    return out


def accumulation_mm(fields: list[np.ndarray]) -> np.ndarray:
    """Rain total (mm) over consecutive 5-minute rate fields (mm/h)."""
    return np.sum(fields, axis=0) * (STEP_MINUTES / 60.0)
