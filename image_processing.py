"""Placeholder image processing functions for Pickoo AI backend.
Each function accepts a Pillow Image and returns a processed Pillow Image.
For production replace these stubs with real ML inference (ONNX, Torch, etc.).
"""
from __future__ import annotations
from PIL import Image, ImageEnhance, ImageFilter
import hashlib
from functools import lru_cache
import numpy as np
from config import settings
from gemini_adapter import process_external, GeminiProcessingError
from replicate_adapter import process_replicate_gfpgan, ReplicateProcessingError

# Utility no-op fallback

def _copy(img: Image.Image) -> Image.Image:
    return img.copy()

def auto_enhance(img: Image.Image) -> Image.Image:
    """Simple contrast + sharpness boost.
    Added defensive logging/normalization to diagnose rare 500 errors.
    """
    # Normalize modes that sometimes trigger PIL enhancement edge cases
    try:
        base_mode = img.mode
        if base_mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        # Enhancement steps
        img = ImageEnhance.Contrast(img).enhance(1.2)
        img = ImageEnhance.Sharpness(img).enhance(1.3)
        return img
    except Exception as e:
        # Raise a clearer error so /process returns actionable detail rather than generic 500
        raise RuntimeError(f"auto_enhance failed (mode={base_mode}, size={img.size}): {e}") from e

def remove_bg(img: Image.Image) -> Image.Image:
    """Naive background removal: make near-white pixels transparent.

    Previous implementation used transposed channel arrays (r,g,b,a = data.T),
    which produces channel mats shaped (W,H) instead of (H,W). Indexing the
    alpha plane (shape (H,W)) with a (W,H) mask triggers shape mismatch when
    images are not square, causing: "boolean index did not match indexed array".

    Fix: access channels directly without transpose so all channel arrays have
    shape (H,W). Then mutate alpha safely.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = np.array(img)
    # Extract channels with consistent (H,W) layout
    r = data[..., 0]
    g = data[..., 1]
    b = data[..., 2]
    # Near-white threshold; tweak if needed
    mask = (r > 240) & (g > 240) & (b > 240)
    # Zero alpha where mask true
    alpha = data[..., 3]
    alpha[mask] = 0
    data[..., 3] = alpha
    return Image.fromarray(data)

def face_retouch(img: Image.Image) -> Image.Image:
    # Placeholder: slight blur to mimic smoothing.
    return img.filter(ImageFilter.GaussianBlur(radius=1))

def erase_object(img: Image.Image) -> Image.Image:
    # Placeholder: just return copy (requires inpainting input mask in real impl)
    return _copy(img)

def sky_replace(img: Image.Image) -> Image.Image:
    # Placeholder: tint image slightly blue.
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    arr[...,2] = np.clip(arr[...,2] * 1.1, 0, 255)  # boost blue channel
    return Image.fromarray(arr.astype(np.uint8))

def super_res(img: Image.Image) -> Image.Image:
    # Placeholder: upscale by 2 using Lanczos
    w,h = img.size
    return img.resize((w*2, h*2), Image.LANCZOS)

def style_transfer(img: Image.Image) -> Image.Image:
    # Placeholder: edge enhance for pseudo artistic feel.
    return img.filter(ImageFilter.EDGE_ENHANCE_MORE)

_TOOL_MAP = {
    "auto_enhance": auto_enhance,
    "background_removal": remove_bg,
    "face_retouch": face_retouch,
    "object_eraser": erase_object,
    "sky_replacement": sky_replace,
    "super_resolution": super_res,
    "style_transfer": style_transfer,
}

def _hash_image(img: Image.Image) -> str:
    # Fast hash of raw pixels + mode + size; avoid reprocessing identical inputs
    h = hashlib.sha256()
    h.update(img.mode.encode())
    h.update(str(img.size).encode())
    # Use memoryview for speed; convert to RGB to normalize small mode differences
    if img.mode not in ("RGB", "RGBA"):
        norm = img.convert("RGB")
    else:
        norm = img
    h.update(norm.tobytes())
    return h.hexdigest()

@lru_cache(maxsize=128)
def _cached(tool_id: str, key: str) -> Image.Image:
    # Placeholder object; real image returned via dispatch wrapper.
    # lru_cache requires deterministic return, we'll rebuild outside.
    return Image.new("RGB", (1,1))

def dispatch(tool_id: str, img: Image.Image):
    """Return (image, meta) where meta contains processor provenance.
    meta keys: processor ('gemini'|'local'|'local-fallback'), attempts, fallback(bool)
    """
    mode = (settings.processor_mode or "existing").lower()

    if mode == "replicate":
        # Replicate GFPGAN only makes sense for face enhancement tools.
        # For other tools, either fall back to local (if enabled) or reject.
        if tool_id in {"auto_enhance", "face_retouch"}:
            try:
                out, url = process_replicate_gfpgan(img)
                return out, {"processor": "replicate", "attempts": 1, "fallback": False, "url": url}
            except ReplicateProcessingError:
                if not settings.allow_fallback:
                    raise
                # else fall through to local
        else:
            if not settings.allow_fallback:
                raise ReplicateProcessingError(
                    f"Tool '{tool_id}' is not supported in replicate mode. "
                    "Enable PICKOO_ALLOW_FALLBACK=1 to use local processing for non-face tools."
                )

    if mode == "new":
        try:
            out, attempts = process_external(tool_id, img)
            return out, {"processor": "gemini", "attempts": attempts, "fallback": False}
        except GeminiProcessingError:
            # fallback path
            if not settings.allow_fallback:
                # Surface external failure explicitly when fallback disabled
                raise
    fn = _TOOL_MAP.get(tool_id, _copy)
    key = _hash_image(img)
    _cached(tool_id, key)  # mark seen (placeholder usage)
    out = fn(img)
    # Determine if this is fallback from external attempt
    external_attempted = mode in {"new", "replicate"}
    processor = "local-fallback" if external_attempted else "local"
    return out, {"processor": processor, "attempts": 0, "fallback": external_attempted and settings.allow_fallback}
