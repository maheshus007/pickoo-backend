from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Response
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageFile
from io import BytesIO
import traceback

from schemas import (
    ImageResponse,
    HealthResponse,
    ToolsResponse,
    ToolInfo,
    SubscriptionStatus,
    SubscriptionPurchaseRequest,
    RecordUsageRequest,
)
from utils import pil_to_base64
import image_processing as proc
from subscription import (
    list_tools_metadata,
    get_subscription_status,
    purchase_plan,
    record_usage,
    quota_alert_pending,
    clear_quota_alert,
)
from auth import (
    get_db,
    find_user_by_email,
    find_user_by_mobile,
    find_user_by_oauth,
    create_user,
    verify_password,
    create_access_token,
    verify_google_id_token,
    verify_facebook_token,
    get_current_user,
)
from fastapi import Depends
from pydantic import BaseModel, EmailStr
from config import settings

APP_VERSION = "0.1.0"

app = FastAPI(title="NeuraLens AI Backend", version=APP_VERSION)

# CORS: allow local Flutter web or emulator origins; adjust for production.
app.add_middleware(
    CORSMiddleware,
    # Development: permit any localhost origin (random ephemeral Flutter web ports) and 127.0.0.1.
    # WARNING: Do not use allow_origin_regex=".*" in production without auth.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],  # include OPTIONS for preflight
    allow_headers=["*"]
)

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version=APP_VERSION)

@app.get("/debug/settings")
async def debug_settings():
    """Lightweight introspection to verify live runtime config and diagnose 422 issues caused by stale code.
    Returns processor_mode and whether external adapter will be used.
    """
    return {
        "processor_mode": settings.processor_mode,
        "use_gemini": settings.use_gemini,
        "allow_fallback": settings.allow_fallback,
        "strict_domain_guard": settings.strict_domain_guard,
        "gemini_verify_ssl": settings.gemini_verify_ssl,
    }

# ---------------- AUTH MODELS -----------------
class SignupRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None
    password: str

class LoginRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None
    password: str

class OAuthRequest(BaseModel):
    token: str

class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str

class UserInfo(BaseModel):
    id: str
    email: EmailStr | None = None
    mobile: str | None = None
    oauth_provider: str | None = None
    oauth_subject: str | None = None
    plan_code: str | None = None
    plan_expires_at: str | None = None
    plan_active: bool | None = None
    quota_alerted: bool | None = None

class PlanUpgradeRequest(BaseModel):
    code: str

# ---------------- AUTH ENDPOINTS -----------------
@app.post("/auth/signup", response_model=AuthResponse)
async def auth_signup(req: SignupRequest, db=Depends(get_db)):
    if not req.email and not req.mobile:
        raise HTTPException(status_code=400, detail="Provide email or mobile")
    if req.email and await find_user_by_email(db, req.email):
        raise HTTPException(status_code=409, detail="Email already registered")
    if req.mobile and await find_user_by_mobile(db, req.mobile):
        raise HTTPException(status_code=409, detail="Mobile already registered")
    user = await create_user(db, email=req.email, mobile=req.mobile, password=req.password, oauth_provider=None, oauth_subject=None)
    user_id = str(user["_id"])
    token = create_access_token(user_id)
    return AuthResponse(access_token=token, user_id=user_id)

@app.post("/auth/login", response_model=AuthResponse)
async def auth_login(req: LoginRequest, db=Depends(get_db)):
    user = None
    if req.email:
        user = await find_user_by_email(db, req.email)
    elif req.mobile:
        user = await find_user_by_mobile(db, req.mobile)
    if not user or "password_hash" not in user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(str(user["_id"]))
    return AuthResponse(access_token=token, user_id=str(user["_id"]))

@app.post("/auth/google", response_model=AuthResponse)
async def auth_google(req: OAuthRequest, db=Depends(get_db)):
    payload = await verify_google_id_token(req.token)
    subj = payload["sub"]
    user = await find_user_by_oauth(db, "google", subj)
    if not user:
        user = await create_user(db, email=payload.get("email"), mobile=None, password=None, oauth_provider="google", oauth_subject=subj)
    token = create_access_token(str(user["_id"]))
    return AuthResponse(access_token=token, user_id=str(user["_id"]))

@app.post("/auth/facebook", response_model=AuthResponse)
async def auth_facebook(req: OAuthRequest, db=Depends(get_db)):
    payload = await verify_facebook_token(req.token)
    subj = payload["sub"]
    user = await find_user_by_oauth(db, "facebook", subj)
    if not user:
        user = await create_user(db, email=payload.get("email"), mobile=None, password=None, oauth_provider="facebook", oauth_subject=subj)
    token = create_access_token(str(user["_id"]))
    return AuthResponse(access_token=token, user_id=str(user["_id"]))

@app.get("/auth/me", response_model=UserInfo)
async def auth_me(current=Depends(get_current_user)):
    plan_code = current.get("plan_code")
    expires_dt = current.get("plan_expires_at")
    plan_expires_at = expires_dt.isoformat() if expires_dt else None
    plan_active = True
    if plan_code and expires_dt:
        from datetime import datetime, timezone
        plan_active = datetime.now(timezone.utc) <= expires_dt
    return UserInfo(
        id=str(current["_id"]),
        email=current.get("email"),
        mobile=current.get("mobile"),
        oauth_provider=current.get("oauth_provider"),
        oauth_subject=current.get("oauth_subject"),
        plan_code=plan_code,
        plan_expires_at=plan_expires_at,
        plan_active=plan_active,
        quota_alerted=current.get("quota_alerted"),
    )

@app.post("/plan/upgrade", response_model=UserInfo)
async def plan_upgrade(req: PlanUpgradeRequest, current=Depends(get_current_user), db=Depends(get_db)):
    from auth import upgrade_user_plan
    updated = await upgrade_user_plan(db, str(current["_id"]), req.code)
    plan_code = updated.get("plan_code")
    expires_dt = updated.get("plan_expires_at")
    plan_expires_at = expires_dt.isoformat() if expires_dt else None
    plan_active = True
    if plan_code and expires_dt:
        from datetime import datetime, timezone
        plan_active = datetime.now(timezone.utc) <= expires_dt
    return UserInfo(
        id=str(updated["_id"]),
        email=updated.get("email"),
        mobile=updated.get("mobile"),
        oauth_provider=updated.get("oauth_provider"),
        oauth_subject=updated.get("oauth_subject"),
        plan_code=plan_code,
        plan_expires_at=plan_expires_at,
        plan_active=plan_active,
        quota_alerted=updated.get("quota_alerted"),
    )

@app.get("/tools", response_model=ToolsResponse)
async def tools():
    return ToolsResponse(tools=[ToolInfo(**t) for t in list_tools_metadata()])

@app.post("/process")
async def process(
    response: Response,
    tool_id: str = Query(..., description="Tool id matching registry (e.g. auto_enhance)"),
    file: UploadFile = File(...),
    raw: bool = Query(False, description="Return raw compressed image bytes instead of base64 JSON"),
    current=Depends(get_current_user),
):
    """Generic processing endpoint allowing dynamic tool selection via query param.
    Optional 'raw=1' query returns binary image (JPEG/PNG) for lower overhead (skip ~33% base64 expansion).
    """
    return await _process(tool_id, file, raw=raw)

@app.get("/subscription/status/{user_id}", response_model=SubscriptionStatus)
async def subscription_status(user_id: str, db=Depends(get_db)):
    return await get_subscription_status(user_id, db)

@app.post("/subscription/purchase", response_model=SubscriptionStatus)
async def subscription_purchase(req: SubscriptionPurchaseRequest, db=Depends(get_db)):
    await purchase_plan(req.user_id, req.plan_id, db)
    return await get_subscription_status(req.user_id, db)

@app.post("/subscription/record_usage", response_model=SubscriptionStatus)
async def subscription_record_usage(req: RecordUsageRequest, db=Depends(get_db)):
    await record_usage(req.user_id, db=db)
    return await get_subscription_status(req.user_id, db)

class QuotaAlertResponse(BaseModel):
    user_id: str
    quota_exhausted: bool
    remaining_images: int | None
    image_quota: int | None
    used_images: int

@app.get("/subscription/quota_alert/{user_id}", response_model=QuotaAlertResponse)
async def subscription_quota_alert(user_id: str, db=Depends(get_db)):
    status = await get_subscription_status(user_id, db)
    exhausted = await quota_alert_pending(user_id, db)
    return QuotaAlertResponse(
        user_id=user_id,
        quota_exhausted=exhausted,
        remaining_images=status.get("remaining_images"),
        image_quota=status.get("image_quota"),
        used_images=status.get("used_images"),
    )

@app.post("/subscription/quota_alert/clear/{user_id}")
async def subscription_quota_alert_clear(user_id: str, db=Depends(get_db)):
    await clear_quota_alert(user_id, db)
    return {"user_id": user_id, "cleared": True}

# Generic processor to reduce duplication.
async def _process(tool_id: str, file: UploadFile, raw: bool = False, response: Response | None = None):
    # Accept truncated JPEGs rather than failing hard (common with camera uploads)
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty upload")
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()  # fully load before threadpool handoff
    except Exception:
        # Fallback: use incremental parser (already imported ImageFile at module level)
        try:
            parser = ImageFile.Parser()
            parser.feed(raw_bytes)
            img = parser.close()
            img.load()
        except Exception:
            # Last resort: write to temp file and open
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.img', delete=True) as tmp:
                    tmp.write(raw_bytes)
                    tmp.flush()
                    img = Image.open(tmp.name)
                    img.load()
            except Exception:
                raise HTTPException(status_code=400, detail=f"Failed to parse image (content_type={file.content_type}, bytes={len(raw_bytes)})")
    try:
        # Offload CPU-bound work
        out, meta = await run_in_threadpool(proc.dispatch, tool_id, img)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing error: {e}")
    # provenance headers
    if response is not None:
        response.headers["X-Processor"] = meta.get("processor", "unknown")
        response.headers["X-Attempts"] = str(meta.get("attempts", 0))
        if meta.get("fallback"):
            response.headers["X-Fallback"] = "1"
    if raw:
        from io import BytesIO
        buf = BytesIO()
        fmt = "PNG" if out.mode == "RGBA" else "JPEG"
        out.save(buf, format=fmt, quality=85, optimize=True)
        return Response(content=buf.getvalue(), media_type=f"image/{fmt.lower()}")
    else:
        return ImageResponse(
            image_base64=pil_to_base64(out),
            tool=tool_id,
            width=out.width,
            height=out.height,
            mode=out.mode,
            processor=meta.get("processor"),
            attempts=meta.get("attempts"),
            fallback=meta.get("fallback"),
        )

@app.post("/enhance", response_model=ImageResponse)
async def enhance(response: Response, file: UploadFile = File(...)):
    return await _process("auto_enhance", file, response=response)

@app.post("/remove_bg", response_model=ImageResponse)
async def remove_bg(response: Response, file: UploadFile = File(...)):
    return await _process("background_removal", file, response=response)

@app.post("/face_retouch", response_model=ImageResponse)
async def face_retouch(response: Response, file: UploadFile = File(...)):
    return await _process("face_retouch", file, response=response)

@app.post("/erase_object", response_model=ImageResponse)
async def erase_object(response: Response, file: UploadFile = File(...)):
    return await _process("object_eraser", file, response=response)

@app.post("/sky_replace", response_model=ImageResponse)
async def sky_replace(response: Response, file: UploadFile = File(...)):
    return await _process("sky_replacement", file, response=response)

@app.post("/super_res", response_model=ImageResponse)
async def super_res(response: Response, file: UploadFile = File(...)):
    return await _process("super_resolution", file, response=response)

@app.post("/style_transfer", response_model=ImageResponse)
async def style_transfer(response: Response, file: UploadFile = File(...)):
    return await _process("style_transfer", file, response=response)

# Run with: uvicorn main:app --reload --port 8000
