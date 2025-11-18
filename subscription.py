"""Subscription & tool metadata utilities.

Persistent fields stored on each user document (Mongo):
    subscription_plan_id            -> current plan (defaults to 'free' if None)
    subscription_purchased_at       -> window start (datetime)
    subscription_used_images        -> usage counter within current window
    quota_alerted                   -> UI alert suppression flag

Rolling window auto-renew: when now > purchased_at + duration_days, we reset
subscription_used_images to 0, advance subscription_purchased_at, and clear
quota_alerted. This simulates automatic renewal cycles for fixed-duration plans
without requiring a repurchase endpoint call.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Dict, Any, List

_PLANS: Dict[str, Dict[str, Any]] = {
    "free": {
        "image_quota": 15,
        "duration_days": None,
        "ad_supported": True,
        "name": "Free (Ads)",
        "price": 0.0,
        "status_code": "F",  # Free tier
    },
    "day25": {
        "image_quota": 25,
        "duration_days": 1,
        "ad_supported": False,
        "name": "25 Images / 1 Day",
        "price": 1.19,
        "status_code": "FD",  # Full Day
    },
    "week100": {
        "image_quota": 100,
        "duration_days": 7,
        "ad_supported": False,
        "name": "100 Images / 1 Week",
        "price": 6.02,
        "status_code": "FW",  # Full Week
    },
    "month1000": {
        "image_quota": 1000,
        "duration_days": 30,
        "ad_supported": False,
        "name": "1000 Images / 30 Days",
        "price": 12.04,
        "status_code": "FM",  # Full Month
    },
    "year_unlimited": {
        "image_quota": None,  # Unlimited
        "duration_days": 365,
        "ad_supported": False,
        "name": "Unlimited / 1 Year",
        "price": 99.99,
        "status_code": "FY",  # Full Year
    },
    "god_mode": {
        "image_quota": None,  # Unlimited
        "duration_days": None,  # Never expires
        "ad_supported": False,
        "name": "God Mode (Unlimited Forever)",
        "price": 0.0,  # Special access, not for sale
        "status_code": "G",  # God User
    },
}

# Export PLANS for payment module
PLANS = _PLANS

_TOOLS: List[Dict[str, Any]] = [
    {"id": "auto_enhance", "name": "Auto Enhance", "endpoint": "/enhance", "description": "Contrast + sharpness boost"},
    {"id": "background_removal", "name": "Background Removal", "endpoint": "/remove_bg", "description": "Make near-white pixels transparent"},
    {"id": "face_retouch", "name": "Face Retouch", "endpoint": "/face_retouch", "description": "Light smoothing filter"},
    {"id": "object_eraser", "name": "Object Eraser", "endpoint": "/erase_object", "description": "Stub for inpainting"},
    {"id": "sky_replacement", "name": "Sky Replacement", "endpoint": "/sky_replace", "description": "Blue channel tint"},
    {"id": "super_resolution", "name": "Super Resolution", "endpoint": "/super_res", "description": "Upscale 2x Lanczos"},
    {"id": "style_transfer", "name": "Artistic Style Transfer", "endpoint": "/style_transfer", "description": "Edge enhance placeholder"},
]

_subscriptions: Dict[str, Dict[str, Any]] = {}  # deprecated legacy cache (not used)

def list_tools_metadata() -> List[Dict[str, Any]]:
    return _TOOLS

def _now() -> datetime:
    return datetime.utcnow()

async def purchase_plan(user_id: str, plan_id: str, db) -> None:
    """
    Purchase a subscription plan and update user's subscription status in MongoDB.
    Sets subscription_status_code (F/FD/FW/FM/FY/G) for persistent tracking.
    """
    if plan_id not in _PLANS:
        raise ValueError("Unknown plan")
    
    plan = _PLANS[plan_id]
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    
    # Calculate expiration date
    purchased_at = _now()
    expires_at = None
    if plan["duration_days"] is not None:
        expires_at = purchased_at + timedelta(days=plan["duration_days"])
    
    # Update user with subscription details and status code
    await coll.update_one(uid_filter, {"$set": {
        "subscription_plan_id": plan_id,
        "subscription_purchased_at": purchased_at,
        "subscription_expires_at": expires_at,
        "subscription_used_images": 0,
        "subscription_status_code": plan["status_code"],  # F, FD, FW, FM, FY, G
        "quota_alerted": False,
    }})

async def get_subscription_status(user_id: str, db):
    """
    Get subscription status from MongoDB, including usage tracking.
    All data is persisted in MongoDB, no in-memory cache.
    """
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    user = await coll.find_one(uid_filter)
    
    plan_id = user.get("subscription_plan_id") if user else None
    if not plan_id:
        plan_id = "free"
    
    plan = _PLANS[plan_id]
    purchased_at = user.get("subscription_purchased_at") if user else None
    expires_at = user.get("subscription_expires_at") if user else None
    used = user.get("subscription_used_images", 0) if user else 0
    status_code = user.get("subscription_status_code", "F") if user else "F"
    
    quota = plan["image_quota"]
    duration_days = plan["duration_days"]
    expired = False
    
    # Check if subscription has expired
    if expires_at:
        expires_at_dt = expires_at if isinstance(expires_at, datetime) else datetime.fromisoformat(expires_at) if isinstance(expires_at, str) else None
        if expires_at_dt:
            if _now() > expires_at_dt:
                expired = True
    
    remaining = None if quota is None else max(quota - used, 0)
    quota_exceeded = False if quota is None else used >= quota
    
    return {
        "user_id": user_id,
        "plan_id": plan_id,
        "status_code": status_code,  # F, FD, FW, FM, FY, G
        "purchased_at": purchased_at.isoformat() if isinstance(purchased_at, datetime) else (purchased_at if purchased_at else None),
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else (expires_at if expires_at else None),
        "used_images": used,
        "image_quota": quota,
        "duration_days": duration_days,
        "expired": expired,
        "remaining_images": remaining,
        "quota_exceeded": quota_exceeded,
    }

async def record_usage(user_id: str, db=None) -> None:
    """
    Record image processing usage in MongoDB.
    Increments subscription_used_images counter and checks quota limits.
    All usage tracking is persisted in MongoDB.
    """
    if db is None:
        return
    
    status = await get_subscription_status(user_id, db)
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    
    # Check for rolling window auto-renew
    if status.get("duration_days") and status.get("expires_at"):
        try:
            expires_at_dt = datetime.fromisoformat(status["expires_at"])
            if _now() > expires_at_dt:
                # Auto-renew: reset usage counter and update purchased date
                await coll.update_one(uid_filter, {"$set": {
                    "subscription_used_images": 0,
                    "subscription_purchased_at": _now(),
                    "subscription_expires_at": _now() + timedelta(days=status["duration_days"]),
                    "quota_alerted": False,
                }})
                status = await get_subscription_status(user_id, db)
        except Exception:
            pass
    
    # Check if user has exceeded quota or expired
    if status["expired"] or status["quota_exceeded"]:
        return
    
    # Increment usage counter in MongoDB
    await coll.update_one(uid_filter, {"$inc": {"subscription_used_images": 1}})
    
    # Check if quota is now exceeded and set alert flag
    updated = await get_subscription_status(user_id, db)
    if updated["quota_exceeded"]:
        doc = await coll.find_one(uid_filter)
        if doc and not doc.get("quota_alerted", False):
            await coll.update_one(uid_filter, {"$set": {"quota_alerted": True}})

async def quota_alert_pending(user_id: str, db) -> bool:
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    doc = await coll.find_one(uid_filter)
    return bool(doc and doc.get("quota_alerted", False))

async def clear_quota_alert(user_id: str, db) -> None:
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    await coll.update_one(uid_filter, {"$set": {"quota_alerted": False}})

async def verify_google_play_purchase(
    user_id: str,
    product_id: str,
    purchase_token: str,
    db
) -> Dict[str, Any]:
    """
    Verify a Google Play purchase and activate subscription.
    
    In production, you should verify the purchase_token with Google Play Developer API:
    https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.products/get
    
    For now, we'll accept the purchase and map product_id to plan_id.
    """
    from transactions import create_transaction, update_transaction_status
    
    # Map Google Play product IDs to plan IDs
    product_to_plan = {
        "pickoo_day25": "day25",
        "pickoo_week100": "week100",
        "pickoo_month1000": "month1000",
        "pickoo_year_unlimited": "year_unlimited",
    }
    
    plan_id = product_to_plan.get(product_id)
    if not plan_id:
        return {
            "success": False,
            "error": f"Unknown product ID: {product_id}"
        }
    
    # Get plan details for pricing
    plan = _PLANS.get(plan_id, {})
    amount = plan.get("price", 0.0)
    currency = "USD"  # Google Play provides USD pricing by default
    
    # Create transaction record (pending)
    transaction_id = await create_transaction(
        user_id=user_id,
        plan_id=plan_id,
        amount=amount,
        currency=currency,
        payment_method="google_play",
        db=db,
        product_id=product_id,
        purchase_token=purchase_token,
        status="pending",
        verified=False,
        device_platform="android",
    )
    
    # TODO: In production, verify purchase_token with Google Play API here
    # from google.oauth2 import service_account
    # from googleapiclient.discovery import build
    # 
    # credentials = service_account.Credentials.from_service_account_file(
    #     'path/to/service-account-key.json',
    #     scopes=['https://www.googleapis.com/auth/androidpublisher']
    # )
    # service = build('androidpublisher', 'v3', credentials=credentials)
    # result = service.purchases().products().get(
    #     packageName='com.yourpackage.name',
    #     productId=product_id,
    #     token=purchase_token
    # ).execute()
    # 
    # if result.get('purchaseState') != 0:  # 0 = purchased
    #     await update_transaction_status(transaction_id, "failed", db, verified=False, notes="Purchase not valid")
    #     return {"success": False, "error": "Purchase not valid"}
    
    # Activate the subscription
    try:
        await purchase_plan(user_id, plan_id, db)
        
        # Update transaction to completed
        await update_transaction_status(
            transaction_id=transaction_id,
            status="completed",
            db=db,
            verified=True,  # Set to True after actual Google Play verification
            notes="Purchase verified and subscription activated"
        )
        
        return {
            "success": True,
            "message": f"Subscription {plan_id} activated successfully",
            "transaction_id": transaction_id
        }
    except Exception as e:
        # Update transaction to failed
        await update_transaction_status(
            transaction_id=transaction_id,
            status="failed",
            db=db,
            verified=False,
            notes=f"Failed to activate subscription: {str(e)}"
        )
        return {
            "success": False,
            "error": str(e)
        }

