"""Replicate adapter for hosted GFPGAN.

This module sends an input image to Replicate's hosted model and returns the
restored output image.

Configuration:
- Set `REPLICATE_API_TOKEN` (recommended) OR `PICKOO_REPLICATE_API_TOKEN`.
- Optionally override `PICKOO_REPLICATE_MODEL`.

Notes:
- This is network I/O and is invoked from a threadpool in `application.py`.
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Tuple

from PIL import Image

from config import settings


class ReplicateProcessingError(Exception):
    """Raised when Replicate processing fails."""


def _get_api_token() -> str:
    token = os.getenv("REPLICATE_API_TOKEN") or (settings.replicate_api_token or "").strip()
    if not token:
        raise ReplicateProcessingError(
            "Missing Replicate API token. Set REPLICATE_API_TOKEN (recommended) "
            "or PICKOO_REPLICATE_API_TOKEN."
        )
    return token


def _encode_image(img: Image.Image) -> Tuple[BytesIO, str]:
    """Encode PIL image to a file-like object for Replicate."""
    buf = BytesIO()
    fmt = "PNG" if img.mode == "RGBA" else "JPEG"
    img.save(buf, format=fmt, quality=95)
    buf.seek(0)

    # Replicate's client accepts file-like objects; giving it a name helps multipart handling.
    setattr(buf, "name", f"input.{fmt.lower()}")
    return buf, fmt


def process_replicate_gfpgan(img: Image.Image) -> Tuple[Image.Image, str]:
    """Run GFPGAN on Replicate.

    Returns: (output_image, output_url)
    """
    try:
        import replicate  # type: ignore
    except Exception as e:
        raise ReplicateProcessingError(
            "Replicate client not installed. Add 'replicate' to requirements.txt"
        ) from e

    token = _get_api_token()
    model = (os.getenv("PICKOO_REPLICATE_MODEL") or settings.replicate_model).strip()

    file_obj, _fmt = _encode_image(img)

    try:
        client = replicate.Client(api_token=token)
        output = client.run(model, input={"img": file_obj})
    except Exception as e:
        raise ReplicateProcessingError(f"Replicate run failed: {e}") from e

    # Replicate returns a File-like object (with .read()) for this model.
    try:
        output_url = getattr(output, "url", "")
        raw = output.read()
        out_img = Image.open(BytesIO(raw))
        out_img.load()
        return out_img, output_url
    except Exception as e:
        raise ReplicateProcessingError(f"Failed to read/parse Replicate output: {e}") from e
