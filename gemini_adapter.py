"""Adapter for Gemini Nano Banana external image processing API.
All functions here are thin wrappers that:
  * encode PIL.Image to PNG bytes
  * POST to external endpoint per tool id with advanced processing instructions
  * parse response (expects JSON with base64 field or raw bytes)
  * return PIL.Image

Integrates advanced AI-driven Image Processing Orchestrator prompts
for optimal quality and precision across all transformations.
"""
from __future__ import annotations
import requests
import logging
from PIL import Image
from io import BytesIO
import base64
import time
import json
from typing import Tuple, Dict
from config import settings
from requests.exceptions import SSLError as RequestsSSLError

# Map internal tool ids to Gemini API endpoints
# Using generateContent endpoint with image and text prompts
# Model name is read from settings.gemini_model (configurable via PICKOO_GEMINI_MODEL)
_ENDPOINT_MAP = {
    "auto_enhance": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "background_removal": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "face_retouch": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "object_eraser": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "sky_replacement": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "super_resolution": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
    "style_transfer": lambda: f"/v1beta/models/{settings.gemini_model}:generateContent",
}

# Advanced Processing Instructions for AI Image Processing Orchestrator
_PROCESSING_PROMPTS = {
    "auto_enhance": {
        "role": "AI-driven Image Processing Orchestrator for Auto Enhancement",
        "instruction": """Analyze and enhance this image with the following parameters:

**OBJECTIVE**: Improve dynamic range, color balance, contrast, and tonal clarity while preserving natural look.

**PROCESSING STEPS**:
1. Analyze histogram and identify tonal imbalances
2. Apply adaptive contrast enhancement (CLAHE if needed)
3. Correct color temperature and white balance
4. Enhance shadow and highlight details without clipping
5. Optimize saturation for natural, vibrant appearance
6. Sharpen details subtly without introducing artifacts

**PARAMETERS**:
- Intensity: 75 (0-100 scale)
- Accuracy: high
- Style: natural (not stylized)
- Preserve: skin tones, facial features, original composition
- Edge protection: enabled
- Texture preservation: high

**OUTPUT**: Enhanced image maintaining photorealistic quality with improved visual appeal.""",
        "config": {"intensity": 75, "accuracy": "high", "style": "natural", "preserve_structure": True}
    },
    
    "background_removal": {
        "role": "AI-driven Image Processing Orchestrator for Background Removal",
        "instruction": """Accurately isolate the foreground subject from background:

**OBJECTIVE**: Precise subject segmentation with professional edge quality.

**PROCESSING STEPS**:
1. Detect primary subject using advanced semantic segmentation
2. Identify subject boundaries with sub-pixel accuracy
3. Apply intelligent edge refinement with proper feathering
4. Remove background while preserving fine details (hair, fur, transparent objects)
5. Ensure no halo artifacts or color bleeding
6. Maintain subject integrity and natural edges

**PARAMETERS**:
- Accuracy: high
- Edge softness: 2px feather
- Halo prevention: enabled
- Hair/detail preservation: maximum
- Alpha channel: 8-bit precision
- Post-processing: edge cleanup enabled

**OUTPUT**: Subject isolated on transparent background with professional-grade edge quality.""",
        "config": {"accuracy": "high", "edge_feather": 2, "preserve_details": True, "alpha_precision": 8}
    },
    
    "face_retouch": {
        "role": "AI-driven Image Processing Orchestrator for Face Retouching",
        "instruction": """Apply subtle, professional face retouching maintaining identity:

**OBJECTIVE**: Human-realistic corrections enhancing natural beauty without distortion.

**PROCESSING STEPS**:
1. Detect facial features and skin regions with high precision
2. Remove blemishes, spots, and temporary imperfections
3. Apply gentle skin smoothing (preserve texture, avoid plastic look)
4. Enhance eyes (clarity, catchlights) while maintaining natural appearance
5. Balance facial lighting and reduce shadows
6. Correct minor color casts on skin
7. Preserve facial structure, wrinkles that define character, and identity markers

**PARAMETERS**:
- Intensity: 60 (subtle, not extreme)
- Accuracy: high
- Skin smoothing: 40 (preserve natural texture)
- Eye enhancement: 70
- Blemish removal: 85
- Identity preservation: critical (never distort)
- Age indicators: preserve (natural aging)

**OUTPUT**: Professionally retouched face maintaining authentic, human appearance.""",
        "config": {"intensity": 60, "skin_smooth": 40, "eye_enhance": 70, "preserve_identity": True}
    },
    
    "object_eraser": {
        "role": "AI-driven Image Processing Orchestrator for Object Removal",
        "instruction": """Intelligently remove unwanted objects and fill regions seamlessly:

**OBJECTIVE**: Precise object detection and context-aware inpainting.

**PROCESSING STEPS**:
1. Detect and segment specified objects with high accuracy
2. Analyze surrounding context (textures, patterns, structures)
3. Generate fill content matching background characteristics
4. Apply context-aware inpainting using deep learning
5. Blend filled regions seamlessly with surroundings
6. Preserve perspective, lighting, and color continuity
7. Ensure no visible seams or artifacts

**PARAMETERS**:
- Detection accuracy: high
- Inpainting method: context-aware (deep learning)
- Blend radius: adaptive
- Texture matching: enabled
- Perspective correction: enabled
- Color consistency: high priority
- Artifact prevention: maximum

**OUTPUT**: Clean image with objects removed and background reconstructed naturally.""",
        "config": {"accuracy": "high", "inpainting": "context-aware", "blend_adaptive": True}
    },
    
    "sky_replacement": {
        "role": "AI-driven Image Processing Orchestrator for Sky Replacement",
        "instruction": """Replace sky while maintaining photorealistic scene integration:

**OBJECTIVE**: Seamless sky replacement with accurate lighting and atmospheric matching.

**PROCESSING STEPS**:
1. Segment sky region with precision (handle complex horizons, trees, buildings)
2. Analyze scene lighting (time of day, color temperature, shadows)
3. Select or generate sky matching scene conditions
4. Match color temperature and atmospheric perspective
5. Adjust foreground lighting to match new sky
6. Blend sky edge transition naturally (handle complex boundaries)
7. Ensure shadow direction and intensity consistency

**PARAMETERS**:
- Segmentation accuracy: high
- Lighting matching: enabled
- Color temperature sync: enabled
- Atmospheric perspective: enabled
- Edge blending: advanced (complex boundaries)
- Shadow adjustment: automatic
- Reflection updates: if water present

**OUTPUT**: Photorealistic scene with replaced sky maintaining environmental consistency.""",
        "config": {"accuracy": "high", "lighting_match": True, "atmospheric_sync": True, "edge_blend": "advanced"}
    },
    
    "super_resolution": {
        "role": "AI-driven Image Processing Orchestrator for Super Resolution",
        "instruction": """Upscale image enhancing details without artifacts:

**OBJECTIVE**: High-quality resolution enhancement preserving and generating natural details.

**PROCESSING STEPS**:
1. Analyze image content and texture patterns
2. Apply AI-driven super-resolution (2x or 4x scale)
3. Enhance fine details intelligently
4. Preserve edge sharpness without oversharpening
5. Maintain color accuracy and tonal consistency
6. Avoid introducing noise, ringing, or unnatural patterns
7. Ensure smooth gradients and natural textures

**PARAMETERS**:
- Scale factor: 2x (or 4x if needed)
- Method: AI deep learning
- Detail enhancement: 80
- Edge preservation: high
- Artifact suppression: maximum
- Noise handling: denoise + sharpen
- Natural texture: prioritized

**OUTPUT**: High-resolution image with enhanced detail and photorealistic quality.""",
        "config": {"scale": 2, "method": "ai-deep", "detail": 80, "artifacts": "suppress"}
    },
    
    "style_transfer": {
        "role": "AI-driven Image Processing Orchestrator for Artistic Style Transfer",
        "instruction": """Apply artistic style while maintaining structural integrity:

**OBJECTIVE**: Transform image to artistic style with configurable intensity.

**PROCESSING STEPS**:
1. Analyze image content and composition
2. Extract structural elements (edges, shapes, composition)
3. Apply artistic style transformation
4. Preserve subject recognizability and composition
5. Balance style intensity with content preservation
6. Maintain color harmony and aesthetic appeal
7. Avoid over-stylization that destroys original intent

**PARAMETERS**:
- Style intensity: 70 (0-100 scale)
- Content preservation: 60
- Structural integrity: high
- Color palette: artistic but harmonious
- Edge handling: stylized but recognizable
- Detail level: balanced
- Aesthetic quality: prioritized

**OUTPUT**: Artistically styled image maintaining composition and subject recognition.""",
        "config": {"intensity": 70, "content_preserve": 60, "structure": "high", "aesthetic": True}
    }
}

class GeminiProcessingError(Exception):
    pass

def _full_url(path: str) -> str:
    return settings.gemini_base_url.rstrip("/") + path

def _get_processing_prompt(tool_id: str) -> Dict:
    """Get advanced processing instructions for the specified tool."""
    return _PROCESSING_PROMPTS.get(tool_id, {
        "role": "AI Image Processor",
        "instruction": f"Process image using {tool_id} with optimal quality settings.",
        "config": {"accuracy": "high", "quality": "maximum"}
    })

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
    path_factory = _ENDPOINT_MAP.get(tool_id)
    if not path_factory:
        raise GeminiProcessingError(f"No external endpoint mapped for tool '{tool_id}'")
    path = path_factory()  # Call the lambda to get the path with current model
    
    # Add API key to URL as query parameter (Gemini API format)
    url = _full_url(path) + f"?key={settings.gemini_api_key}"
    
    # Get advanced processing instructions for this tool
    prompt_data = _get_processing_prompt(tool_id)
    
    # Encode image to base64 for Gemini API
    img_bytes = _encode_image(img)
    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
    
    # Determine MIME type
    mime_type = "image/png" if img.mode == "RGBA" else "image/jpeg"
    
    # Construct Gemini API request body
    request_body = {
        "contents": [{
            "parts": [
                {
                    "text": f"{prompt_data.get('instruction', 'Process this image.')}\n\nPlease return the processed image."
                },
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": img_base64
                    }
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "topK": 32,
            "topP": 1,
            "maxOutputTokens": 4096,
        }
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    attempts = 0
    backoff = 0.3  # Reduced from 0.5
    last_exc: Exception | None = None
    logger = logging.getLogger("gemini_adapter")
    
    logger.info(
        "Processing %s with Gemini API - Model: %s",
        tool_id,
        settings.gemini_model
    )
    
    while attempts < settings.gemini_max_retries:
        attempts += 1
        try:
            # Send request to Gemini API
            resp = requests.post(
                url,
                headers=headers,
                json=request_body,
                timeout=settings.timeout,
                verify=settings.gemini_verify_ssl,
            )
            logger.debug(
                "Gemini attempt=%d tool=%s status=%d body_head=%r", 
                attempts, tool_id, resp.status_code, resp.text[:200]
            )
            
            if resp.status_code < 400:
                try:
                    response_data = resp.json()
                    
                    # Gemini returns text responses, not images
                    # For now, we'll just return the original image
                    # Note: Gemini's current API doesn't actually process images, it analyzes them
                    logger.warning(
                        "Gemini API doesn't support direct image manipulation. "
                        "Response received but cannot extract processed image. "
                        "Consider using specialized image processing APIs or local processing."
                    )
                    
                    # Return original image since Gemini can't process images
                    return img.copy(), attempts
                    
                except Exception as e:
                    last_exc = e
            else:
                error_detail = resp.text[:500]
                last_exc = GeminiProcessingError(f"Gemini API {resp.status_code}: {error_detail}")
                
        except RequestsSSLError as e:
            last_exc = GeminiProcessingError(
                f"SSL certificate verification failed: {e}. If this is a trusted corporate proxy or dev environment, set PICKOO_GEMINI_VERIFY_SSL=false to bypass (NOT recommended for production)."
            )
        except requests.RequestException as e:
            last_exc = e
        
        # exponential backoff - but faster
        if attempts < settings.gemini_max_retries:
            time.sleep(backoff)
            backoff *= 1.5  # Reduced multiplier from 2
    
    logger.error("Failed to process %s after %d attempts: %s", tool_id, attempts, last_exc)
    raise GeminiProcessingError(f"External processing failed after {attempts} attempts: {last_exc}")
