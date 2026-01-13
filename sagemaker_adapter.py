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

from io import BytesIO
from typing import Tuple

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


def process_sagemaker_gfpgan(img: Image.Image) -> Tuple[Image.Image, dict]:
    """Run GFPGAN via SageMaker.

    Returns: (output_image, meta)
    """
    try:
        import boto3  # type: ignore
    except Exception as e:
        raise SageMakerProcessingError("boto3 not installed. Add boto3 to requirements.txt") from e

    endpoint_name, region = _get_endpoint_config()
    payload = _encode_image(img)

    try:
        client = boto3.client("sagemaker-runtime", region_name=region)
        resp = client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/octet-stream",
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
        "bytes_in": len(payload),
        "bytes_out": len(raw),
    }
    return out_img, meta
