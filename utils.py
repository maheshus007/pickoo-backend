import base64
from io import BytesIO
from PIL import Image


def pil_to_base64(img: Image.Image) -> str:
    buf = BytesIO()
    # Preserve transparency where applicable
    format = 'PNG' if img.mode in ('RGBA', 'LA') else 'JPEG'
    img.save(buf, format=format, optimize=True)
    return base64.b64encode(buf.getvalue()).decode('utf-8')
