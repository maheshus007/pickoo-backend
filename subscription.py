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
    },
    "day25": {
        "image_quota": 25,
        "duration_days": 1,
        "ad_supported": False,
        "name": "25 Images / 1 Day",
    },
    "week100": {
        "image_quota": 100,
        "duration_days": 7,
        "ad_supported": False,
        "name": "100 Images / 1 Week",
    },
    "month1000": {
        "image_quota": 1000,
        "duration_days": 30,
        "ad_supported": False,
        "name": "1000 Images / 30 Days",
    },
}

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
    if plan_id not in _PLANS:
        raise ValueError("Unknown plan")
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    await coll.update_one(uid_filter, {"$set": {
        "subscription_plan_id": plan_id,
        "subscription_purchased_at": _now(),
        "subscription_used_images": 0,
        "quota_alerted": False,
    }})

async def get_subscription_status(user_id: str, db):
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    user = await coll.find_one(uid_filter)
    plan_id = user.get("subscription_plan_id") if user else None
    if not plan_id:
        plan_id = "free"
    plan = _PLANS[plan_id]
    purchased_at = user.get("subscription_purchased_at") if user else None
    used = user.get("subscription_used_images", 0) if user else 0
    quota = plan["image_quota"]
    duration_days = plan["duration_days"]
    expired = False
    if duration_days and purchased_at:
        purchased_at_dt = purchased_at if isinstance(purchased_at, datetime) else datetime.fromisoformat(purchased_at) if isinstance(purchased_at, str) else None
        if purchased_at_dt:
            expires = purchased_at_dt + timedelta(days=duration_days)
            if _now() > expires:
                expired = True
    remaining = None if quota is None else max(quota - used, 0)
    quota_exceeded = False if quota is None else used >= quota
    return {
        "user_id": user_id,
        "plan_id": plan_id,
        "purchased_at": purchased_at.isoformat() if isinstance(purchased_at, datetime) else (purchased_at if purchased_at else None),
        "used_images": used,
        "image_quota": quota,
        "duration_days": duration_days,
        "expired": expired,
        "remaining_images": remaining,
        "quota_exceeded": quota_exceeded,
    }

async def record_usage(user_id: str, db=None) -> None:
    if db is None:
        return
    status = await get_subscription_status(user_id, db)
    coll = db.get_collection("users")
    uid_filter = {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    # Rolling window auto-renew
    if status.get("duration_days") and status.get("purchased_at"):
        try:
            purchased_at_dt = datetime.fromisoformat(status["purchased_at"])
            if _now() > purchased_at_dt + timedelta(days=status["duration_days"]):
                await coll.update_one(uid_filter, {"$set": {
                    "subscription_used_images": 0,
                    "subscription_purchased_at": _now(),
                    "quota_alerted": False,
                }})
                status = await get_subscription_status(user_id, db)
        except Exception:
            pass
    if status["expired"] or status["quota_exceeded"]:
        return
    await coll.update_one(uid_filter, {"$inc": {"subscription_used_images": 1}})
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
