"""Adapter for Gemini Nano Banana external image processing API.
All functions here are thin wrappers that:
  * encode PIL.Image to PNG bytes
  * POST to external endpoint per tool id
  * parse response (expects JSON with base64 field or raw bytes)
  * return PIL.Image

NOTE: Actual API specification for Gemini Nano Banana is unknown; these endpoints
are placeholders. Replace `endpoint_for` mapping and response parsing with real logic.
"""
from __future__ import annotations
import requests
import logging
from PIL import Image
from io import BytesIO
import base64
import time
from typing import Tuple
from config import settings
from requests.exceptions import SSLError as RequestsSSLError

# Map internal tool ids to external endpoint paths (placeholder values)
_ENDPOINT_MAP = {
    "auto_enhance": "/v1/nano/auto_enhance",
    "background_removal": "/v1/nano/background_removal",
    "face_retouch": "/v1/nano/face_retouch",
    "object_eraser": "/v1/nano/object_eraser",
    "sky_replacement": "/v1/nano/sky_replacement",
    "super_resolution": "/v1/nano/super_resolution",
    "style_transfer": "/v1/nano/style_transfer",
}

class GeminiProcessingError(Exception):
    pass

def _full_url(path: str) -> str:
    return settings.gemini_base_url.rstrip("/") + path

def _encode_image(img: Image.Image) -> bytes:
    buf = BytesIO()
    # use PNG to preserve alpha if present
    fmt = "PNG" if img.mode == "RGBA" else "JPEG"
    img.save(buf, format=fmt, quality=90)
    return buf.getvalue()

def _decode_image(payload: bytes, content_type: str) -> Image.Image:
    # If server returns JSON with base64, attempt to parse
    if content_type.startswith("application/json"):
        import json
        data = json.loads(payload.decode("utf-8"))
        if "image_base64" not in data:
            raise GeminiProcessingError("Missing image_base64 in JSON response")
        raw = base64.b64decode(data["image_base64"])
        return Image.open(BytesIO(raw))
    # Assume raw bytes image
    return Image.open(BytesIO(payload))

def process_external(tool_id: str, img: Image.Image) -> Tuple[Image.Image, int]:
    if not settings.use_gemini:
        raise GeminiProcessingError("Gemini external processing invoked but mode != new")
    path = _ENDPOINT_MAP.get(tool_id)
    if not path:
        raise GeminiProcessingError(f"No external endpoint mapped for tool '{tool_id}'")
    url = _full_url(path)
    # Domain validation: refuse known non-API product domains to prevent misleading 422 errors
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # if settings.strict_domain_guard:
    #     invalid_domains = {"aistudio.google.com", "console.cloud.google.com"}
    #     if parsed.netloc in invalid_domains:
    #         raise GeminiProcessingError(
    #             f"Invalid external base domain '{parsed.netloc}' for image processing. Configure NEURALENS_GEMINI_BASE_URL to a real inference API domain or switch processor_mode=existing."
    #         )
    #     # Additional guard: generativelanguage.googleapis.com does not host /v1/nano/* endpoints
    #     if parsed.netloc == "generativelanguage.googleapis.com" and path.startswith("/v1/nano/"):
    #         raise GeminiProcessingError(
    #             "'/v1/nano/*' endpoints are placeholders and not part of Google Generative Language API. Use a real inference endpoint (Vertex AI, custom service) or set processor_mode=existing for local tools."
    #         )
    headers = {}
    if settings.gemini_api_key:
        headers["Authorization"] = f"Bearer {settings.gemini_api_key}"
    files = {
        "file": (f"input.png", _encode_image(img), "image/png")
    }
    attempts = 0
    backoff = 0.5
    last_exc: Exception | None = None
    logger = logging.getLogger("gemini_adapter")
    while attempts < settings.gemini_max_retries:
        attempts += 1
        try:
            # Send identifier both as query and form field to satisfy unknown validation requirements.
            resp = requests.post(
                url,
                headers=headers,
                files=files,
                params={"request": tool_id},  # query param variant
                data={"request": tool_id},    # form field variant
                timeout=settings.timeout,
                verify=settings.gemini_verify_ssl,
            )
            logger.debug(
                "Gemini attempt=%d tool=%s status=%d ctype=%s body_head=%r", 
                attempts, tool_id, resp.status_code, resp.headers.get("Content-Type"), resp.text[:160]
            )
            # If we still get 422 missing 'query.request', short-circuit immediately.
            if resp.status_code == 422 and '"query","request"' in resp.text:
                last_exc = GeminiProcessingError("External API demands unknown query param 'request'; placeholder endpoint incompatible. Aborting early.")
                break
        except RequestsSSLError as e:
            last_exc = GeminiProcessingError(
                f"SSL certificate verification failed: {e}. If this is a trusted corporate proxy or dev environment, set NEURALENS_GEMINI_VERIFY_SSL=false to bypass (NOT recommended for production)."
            )
        except requests.RequestException as e:
            last_exc = e
        else:
            if resp.status_code < 400:
                try:
                    out = _decode_image(resp.content, resp.headers.get("Content-Type", ""))
                    out.load()
                    return out, attempts
                except Exception as e:
                    last_exc = e
            else:
                last_exc = GeminiProcessingError(f"Gemini API {resp.status_code}: {resp.text[:200]}")
        # exponential backoff
        if attempts < settings.gemini_max_retries:
            time.sleep(backoff)
            backoff *= 2
    raise GeminiProcessingError(f"External processing failed after {attempts} attempts: {last_exc}")
