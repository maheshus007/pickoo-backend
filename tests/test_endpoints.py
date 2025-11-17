import base64
from io import BytesIO
from fastapi.testclient import TestClient
from PIL import Image

from main import app

client = TestClient(app)

def _fake_image_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), color=(128, 200, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_tools_list():
    r = client.get("/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    assert any(t["id"] == "auto_enhance" for t in tools)

def test_generic_process():
    files = {"file": ("test.png", _fake_image_bytes(), "image/png")}
    r = client.post("/process", params={}, json={"tool_id": "auto_enhance"}, files=files)
    # FastAPI TestClient can't send both json Body(embed) and multipart simultaneously directly; use data override
    if r.status_code == 422:
        # Fallback: send form field
        r = client.post("/process?tool_id=auto_enhance", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tool"] == "auto_enhance"
    assert data["image_base64"]
    # Basic base64 validation
    raw = base64.b64decode(data["image_base64"].encode("utf-8"))
    assert len(raw) > 100

def test_subscription_flow():
    user_id = "tester123"
    # Purchase day plan
    r = client.post("/subscription/purchase", json={"user_id": user_id, "plan_id": "day25"})
    assert r.status_code == 200
    status = r.json()
    assert status["plan_id"] == "day25"
    # Record usage
    r2 = client.post("/subscription/record_usage", json={"user_id": user_id})
    assert r2.status_code == 200
    status2 = r2.json()
    assert status2["used_images"] == status["used_images"] + 1
