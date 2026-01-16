"""SageMaker adapter for hosted GFPGAN.

Invokes an AWS SageMaker realtime endpoint (sagemaker-runtime:InvokeEndpoint)
with raw image bytes and expects the endpoint to return an image (JPEG).

Configuration:
- PICKOO_PROCESSOR_MODE=sage_maker_gfpgan
- PICKOO_SAGEMAKER_ENDPOINT_NAME=gfpgan-endpoint
- PICKOO_SAGEMAKER_REGION=ap-south-1 (or rely on AWS_REGION/AWS_DEFAULT_REGION)

Auth / AWS credentials:
- Uses boto3 default credential chain.
- On Elastic Beanstalk, prefer attaching an instance profile role with
  `sagemaker:InvokeEndpoint` permission.

Notes:
- This is network I/O and should be called from a threadpool.
"""

from __future__ import annotations

import base64
import json
from io import BytesIO
from typing import Any, Dict, Tuple

from PIL import Image

from config import settings


class SageMakerProcessingError(Exception):
    """Raised when SageMaker processing fails."""


def _encode_image(img: Image.Image) -> bytes:
    buf = BytesIO()
    # SageMaker inference expects bytes; use JPEG for smaller payload.
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _normalize_gfpgan_params(params: Dict[str, Any] | None) -> Dict[str, Any]:
    if not params:
        return {}

    allowed = {
        "weight",
        "has_aligned",
        "only_center_face",
        "paste_back",
        "max_input_side",
    }

    out: Dict[str, Any] = {}
    for key, value in params.items():
        if key not in allowed:
            continue
        if value is None:
            continue
        out[key] = value

    weight = out.get("weight")
    if weight is not None:
        try:
            w = float(weight)
        except Exception as e:
            raise SageMakerProcessingError("Invalid 'weight' (expected float 0..1)") from e
        out["weight"] = max(0.0, min(1.0, w))

    max_side = out.get("max_input_side")
    if max_side is not None:
        try:
            m = int(max_side)
        except Exception as e:
            raise SageMakerProcessingError("Invalid 'max_input_side' (expected int > 0)") from e
        if m <= 0:
            raise SageMakerProcessingError("Invalid 'max_input_side' (expected int > 0)")
        out["max_input_side"] = m

    # Ensure JSON-serializable primitives
    for bkey in ("has_aligned", "only_center_face", "paste_back"):
        if bkey in out:
            out[bkey] = bool(out[bkey])

    return out


def _get_endpoint_config() -> Tuple[str, str]:
    endpoint = (settings.sagemaker_endpoint_name or "").strip()
    region = (settings.resolved_sagemaker_region or "").strip()

    if not endpoint:
        raise SageMakerProcessingError(
            "Missing SageMaker endpoint name. Set PICKOO_SAGEMAKER_ENDPOINT_NAME."
        )
    if not region:
        raise SageMakerProcessingError(
            "Missing AWS region. Set PICKOO_SAGEMAKER_REGION (or AWS_REGION/AWS_DEFAULT_REGION)."
        )

    return endpoint, region


def process_sagemaker_gfpgan(img: Image.Image, params: Dict[str, Any] | None = None) -> Tuple[Image.Image, dict]:
    """Run GFPGAN via SageMaker.

    Returns: (output_image, meta)
    """
    try:
        import boto3  # type: ignore
    except Exception as e:
        raise SageMakerProcessingError("boto3 not installed. Add boto3 to requirements.txt") from e

    endpoint_name, region = _get_endpoint_config()

    normalized_params = _normalize_gfpgan_params(params)
    image_bytes = _encode_image(img)

    if normalized_params:
        payload_obj = {
            "image_b64": base64.b64encode(image_bytes).decode("utf-8"),
            "params": normalized_params,
        }
        payload = json.dumps(payload_obj).encode("utf-8")
        content_type = "application/json"
        bytes_in = len(payload)
    else:
        payload = image_bytes
        content_type = "application/octet-stream"
        bytes_in = len(image_bytes)

    try:
        client = boto3.client("sagemaker-runtime", region_name=region)
        resp = client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType=content_type,
            Accept="image/jpeg",
            Body=payload,
        )
        body = resp.get("Body")
        if body is None:
            raise SageMakerProcessingError("SageMaker response missing Body")
        raw = body.read()
    except SageMakerProcessingError:
        raise
    except Exception as e:
        raise SageMakerProcessingError(f"SageMaker invoke_endpoint failed: {e}") from e

    try:
        out_img = Image.open(BytesIO(raw))
        out_img.load()
    except Exception as e:
        raise SageMakerProcessingError(f"Failed to parse SageMaker output image: {e}") from e

    meta = {
        "endpoint": endpoint_name,
        "region": region,
        "bytes_in": bytes_in,
        "bytes_out": len(raw),
        "content_type": content_type,
    }
    if normalized_params:
        meta["params"] = normalized_params
    return out_img, meta
