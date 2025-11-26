from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Response, Body
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
from PIL import Image, ImageFile, ImageOps
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
    CreateCheckoutRequest,
    CheckoutResponse,
    PaymentHistoryResponse,
    CurrencyResponse,
    WebhookResponse,
    TransactionRecord,
    TransactionListResponse,
    UserDeleteResponse,
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
    verify_google_play_purchase,
)
from transactions import (
    get_user_transactions,
    get_transaction_by_id,
    get_revenue_stats,
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
    delete_user_by_id,
)
from fastapi import Depends
from pydantic import BaseModel, EmailStr
from config import settings

APP_VERSION = "0.1.0"

app = FastAPI(title="Pickoo AI Backend", version=APP_VERSION)

# Elastic Beanstalk compatibility - alias for WSGI
application = app

# CORS: allow local Flutter web or emulator origins; adjust for production.
app.add_middleware(
    CORSMiddleware,
    # Allow localhost for development + any origin for production (consider restricting in prod)
    # For production, consider using specific origins like your deployed Flutter web app URL
    allow_origins=[
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "*",  # Allow all origins - ONLY for testing, restrict in production
    ],
    allow_credentials=True,
    allow_methods=["*"],  # include OPTIONS for preflight
    allow_headers=["*"],
    max_age=3600,  # Cache preflight for 1 hour
)

# Middleware to allow larger uploads
class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)

app.add_middleware(LimitUploadSizeMiddleware)

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

@app.delete("/auth/user/{user_id}", response_model=UserDeleteResponse)
async def delete_user(user_id: str, db=Depends(get_db), current=Depends(get_current_user)):
    """Delete a user account and all associated data.
    
    Requires authentication. Users can only delete their own account unless they have admin privileges.
    This is a destructive operation and cannot be undone.
    
    Args:
        user_id: The ID of the user to delete
        
    Returns:
        UserDeleteResponse with deletion confirmation
        
    Raises:
        HTTPException: 403 if user tries to delete another user's account
        HTTPException: 404 if user not found
    """
    # Security check: users can only delete their own account
    # Add admin role check here if needed: if current.get("role") != "admin" and ...
    current_user_id = str(current["_id"])
    if current_user_id != user_id:
        raise HTTPException(
            status_code=403, 
            detail="Forbidden: You can only delete your own account"
        )
    
    # Delete the user
    deleted_info = await delete_user_by_id(db, user_id)
    
    return UserDeleteResponse(
        status="success",
        message=f"User account successfully deleted",
        user_id=deleted_info["user_id"],
        deleted_at=deleted_info["deleted_at"].isoformat()
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

@app.get("/subscription/status", response_model=SubscriptionStatus)
async def subscription_status(user_id: str = Query(..., description="User ID to get subscription status for"), db=Depends(get_db)):
    """
    Get subscription status for a user using query parameter.
    Example: /subscription/status?user_id=123
    """
    return await get_subscription_status(user_id, db)

@app.get("/subscription/status/{user_id}", response_model=SubscriptionStatus)
async def subscription_status_path(user_id: str, db=Depends(get_db)):
    """
    Get subscription status for a user using path parameter.
    Example: /subscription/status/123
    """
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

@app.post("/subscription/verify-google-play")
async def subscription_verify_google_play(
    user_id: str = Body(...),
    purchase_token: str = Body(...),
    product_id: str = Body(...),
    db=Depends(get_db)
):
    """
    Verify a Google Play in-app purchase and activate subscription.
    
    In production, this should verify the purchase_token with Google Play Developer API
    before activating the subscription to prevent fraud.
    """
    result = await verify_google_play_purchase(
        user_id=user_id,
        product_id=product_id,
        purchase_token=purchase_token,
        db=db
    )
    return result

# Generic processor to reduce duplication.
async def _process(tool_id: str, file: UploadFile, raw: bool = False, response: Response | None = None):
    # Accept truncated JPEGs rather than failing hard (common with camera uploads)
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Unsupported file type")
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty upload")
    
    # DISABLED: Server-side compression to preserve original dimensions for compare slider
    # Client-side compression (flutter_image_compress) already handles size optimization
    
    try:
        img = Image.open(BytesIO(raw_bytes))
        img.load()  # fully load before threadpool handoff
        # Apply EXIF orientation to fix rotated images
        img = ImageOps.exif_transpose(img)
    except Exception:
        # Fallback: use incremental parser (already imported ImageFile at module level)
        try:
            parser = ImageFile.Parser()
            parser.feed(raw_bytes)
            img = parser.close()
            img.load()
            # Apply EXIF orientation to fix rotated images
            img = ImageOps.exif_transpose(img)
        except Exception:
            # Last resort: write to temp file and open
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.img', delete=True) as tmp:
                    tmp.write(raw_bytes)
                    tmp.flush()
                    img = Image.open(tmp.name)
                    img.load()
                    # Apply EXIF orientation to fix rotated images
                    img = ImageOps.exif_transpose(img)
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

# ---------------- PAYMENT ENDPOINTS -----------------
from payment import payment_service
from fastapi import Request as FastAPIRequest
import requests

@app.post("/payment/create-checkout", response_model=CheckoutResponse)
async def create_checkout_session(req: CreateCheckoutRequest):
    """
    Create a Stripe Checkout session for subscription purchase.
    Automatically detects currency based on country code.
    """
    try:
        # Get subscription plan details
        from subscription import PLANS
        
        plan = PLANS.get(req.plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan {req.plan_id} not found")
        
        # Get currency for country
        currency = payment_service.get_currency_for_country(req.country_code)
        
        # Create checkout session
        result = await payment_service.create_checkout_session(
            user_id=req.user_id,
            plan_id=req.plan_id,
            plan_name=plan["name"],
            base_price_usd=plan["price"],
            currency=currency,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
        )
        
        return CheckoutResponse(**result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/payment/webhook", response_model=WebhookResponse)
async def payment_webhook(request: FastAPIRequest):
    """
    Handle Stripe webhook events for payment confirmations.
    """
    try:
        payload = await request.body()
        signature = request.headers.get("stripe-signature")
        
        if not signature:
            raise HTTPException(status_code=400, detail="Missing stripe-signature header")
        
        result = await payment_service.handle_webhook_event(payload, signature)
        
        return WebhookResponse(
            status="success",
            message=f"Processed event: {result.get('event_type')}"
        )
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/payment/history/{user_id}", response_model=PaymentHistoryResponse)
async def get_payment_history(user_id: str, current=Depends(get_current_user)):
    """
    Get payment history for a user.
    """
    try:
        # Verify user can only access their own payment history
        if str(current["_id"]) != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        payments = await payment_service.get_user_payments(user_id)
        
        return PaymentHistoryResponse(
            payments=payments,
            total_count=len(payments)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/payment/detect-currency", response_model=CurrencyResponse)
async def detect_currency(request: FastAPIRequest):
    """
    Detect user's currency based on IP geolocation.
    Uses ipapi.co free API for country detection.
    """
    try:
        # Try to get country from IP
        client_ip = request.client.host
        
        # Skip localhost/private IPs
        if client_ip in ["127.0.0.1", "localhost"] or client_ip.startswith("192.168."):
            country_code = "US"
        else:
            # Use ipapi.co for geolocation (free tier: 1000 requests/day)
            try:
                geo_response = requests.get(f"https://ipapi.co/{client_ip}/json/", timeout=2)
                geo_data = geo_response.json()
                country_code = geo_data.get("country_code", "US")
            except:
                # Fallback to US if geolocation fails
                country_code = "US"
        
        currency = payment_service.get_currency_for_country(country_code)
        
        # Currency symbols mapping
        currency_symbols = {
            "usd": "$", "eur": "€", "gbp": "£", "cad": "CA$", "aud": "A$",
            "inr": "₹", "jpy": "¥", "cny": "¥", "sgd": "S$", "hkd": "HK$",
            "nzd": "NZ$", "chf": "CHF", "sek": "kr", "nok": "kr", "dkk": "kr",
            "mxn": "MX$", "brl": "R$", "zar": "R", "aed": "AED", "sar": "SAR",
            "krw": "₩", "thb": "฿", "myr": "RM", "php": "₱", "idr": "Rp",
        }
        
        return CurrencyResponse(
            country_code=country_code,
            currency=currency,
            symbol=currency_symbols.get(currency, "$")
        )
        
    except Exception as e:
        # Return USD as safe fallback
        return CurrencyResponse(
            country_code="US",
            currency="usd",
            symbol="$"
        )


# ==========================================
# Transaction Tracking Endpoints
# ==========================================

@app.get("/transactions/user/{user_id}", response_model=TransactionListResponse)
async def get_transactions_by_user(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db=Depends(get_db)
):
    """
    Get all transactions for a specific user with pagination.
    
    This endpoint is useful for:
    - Showing user purchase history
    - Building transaction analytics dashboards
    - Auditing and reporting
    """
    skip = (page - 1) * page_size
    transactions = await get_user_transactions(user_id, db, limit=page_size, skip=skip)
    
    # Get total count
    trans_coll = db.get_collection("transactions")
    total_count = await trans_coll.count_documents({"user_id": user_id})
    
    return TransactionListResponse(
        transactions=transactions,
        total_count=total_count,
        page=page,
        page_size=page_size
    )


@app.get("/transactions/{transaction_id}", response_model=TransactionRecord)
async def get_transaction_details(
    transaction_id: str,
    db=Depends(get_db)
):
    """
    Get details of a specific transaction by ID.
    
    Useful for:
    - Transaction lookup
    - Debugging payment issues
    - Customer support
    """
    transaction = await get_transaction_by_id(transaction_id, db)
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    return TransactionRecord(**transaction)


@app.get("/transactions/stats/revenue")
async def get_revenue_statistics(
    start_date: Optional[str] = Query(None, description="ISO format: 2025-01-01T00:00:00Z"),
    end_date: Optional[str] = Query(None, description="ISO format: 2025-12-31T23:59:59Z"),
    db=Depends(get_db)
):
    """
    Get revenue statistics for a date range.
    
    Returns:
    - Total transactions
    - Total revenue in USD
    - Average transaction value
    - Currencies used
    - Payment methods used
    
    Useful for:
    - Revenue reporting
    - Business analytics
    - Financial dashboards
    """
    from datetime import datetime
    
    start_dt = datetime.fromisoformat(start_date.replace("Z", "")) if start_date else None
    end_dt = datetime.fromisoformat(end_date.replace("Z", "")) if end_date else None
    
    stats = await get_revenue_stats(db, start_date=start_dt, end_date=end_dt)
    
    return stats


@app.get("/transactions/list/all", response_model=TransactionListResponse)
async def get_all_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: pending, completed, failed, refunded"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method: google_play, app_store, stripe"),
    db=Depends(get_db)
):
    """
    Get all transactions with filtering and pagination.
    
    Admin endpoint for:
    - Viewing all transactions across users
    - Filtering by status or payment method
    - Building admin dashboards
    - Exporting transaction data
    """
    trans_coll = db.get_collection("transactions")
    
    # Build query filter
    query = {}
    if status:
        query["status"] = status
    if payment_method:
        query["payment_method"] = payment_method
    
    # Get paginated results
    skip = (page - 1) * page_size
    cursor = trans_coll.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    transactions = await cursor.to_list(length=page_size)
    
    # Remove MongoDB _id field
    for trans in transactions:
        trans.pop("_id", None)
    
    # Get total count
    total_count = await trans_coll.count_documents(query)
    
    return TransactionListResponse(
        transactions=transactions,
        total_count=total_count,
        page=page,
        page_size=page_size
    )

# Run with: uvicorn main:app --reload --port 8000
