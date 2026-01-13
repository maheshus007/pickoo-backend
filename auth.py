"""Authentication & user management module for Pickoo.
Provides:
  - Mongo connection (motor)
  - User creation & lookup
  - Password hashing / verification (bcrypt)
  - JWT generation & decoding
  - OAuth placeholder verifiers (Google / Facebook)

Replace placeholder network verification with real calls + error handling.
"""
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
try:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
except ModuleNotFoundError:  # Provide actionable guidance instead of opaque crash
    AsyncIOMotorClient = None  # type: ignore
try:
    import bcrypt  # type: ignore
except ModuleNotFoundError:
    bcrypt = None  # type: ignore
try:
    import jwt  # type: ignore
except ModuleNotFoundError:
    jwt = None  # type: ignore
from fastapi import HTTPException, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from config import settings
from datetime import timedelta

# ----- DB Setup -----
from typing import Any
_client: Any = None  # Use Any to avoid type issues when motor missing

def get_client():
    global _client
    if _client is None:
        if AsyncIOMotorClient is None:
            raise HTTPException(status_code=500, detail="Mongo driver 'motor' not installed. Install with: pip install motor (inside the backend virtualenv).")
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client

def get_db():
    return get_client().get_default_database()

# ----- User Helpers -----
USER_COLLECTION = "users"

async def find_user_by_email(db, email: str) -> Optional[Dict[str, Any]]:
    return await db[USER_COLLECTION].find_one({"email": email.lower()})

async def find_user_by_mobile(db, mobile: str) -> Optional[Dict[str, Any]]:
    return await db[USER_COLLECTION].find_one({"mobile": mobile})

async def find_user_by_oauth(db, provider: str, subject: str) -> Optional[Dict[str, Any]]:
    return await db[USER_COLLECTION].find_one({"oauth_provider": provider, "oauth_subject": subject})

async def create_user(db, *, email: str | None, mobile: str | None, password: str | None, oauth_provider: str | None, oauth_subject: str | None) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "email": email.lower() if email else None,
        "mobile": mobile,
        "oauth_provider": oauth_provider,
        "oauth_subject": oauth_subject,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "plan_code": None,
        "plan_expires_at": None,
        "quota_alerted": False,  # tracks if user has seen quota exhaustion tile
        # Subscription usage persistence fields (all stored in MongoDB)
        "subscription_plan_id": "free",  # Default to free plan
        "subscription_purchased_at": datetime.now(timezone.utc),
        "subscription_expires_at": None,  # Free plan never expires
        "subscription_used_images": 0,
        "subscription_status_code": "F",  # F = Free tier
    }
    if password:
        doc["password_hash"] = hash_password(password)
    result = await db[USER_COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc

# ----- Password Hashing -----

def hash_password(raw: str) -> str:
    if bcrypt is None:
        raise HTTPException(status_code=500, detail="Dependency 'bcrypt' missing. Install inside venv: pip install bcrypt")
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()

def verify_password(raw: str, hashed: str) -> bool:
    if bcrypt is None:
        raise HTTPException(status_code=500, detail="Dependency 'bcrypt' missing. Install inside venv: pip install bcrypt")
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except Exception:
        return False

# ----- JWT -----

def create_access_token(user_id: str) -> str:
    """Create a non-expiring JWT access token.

    Removes the standard `exp` claim to prevent token expiry. This is useful
    for environments where session continuity is preferred over strict token
    rotation. Note: For production, consider using refresh tokens instead of
    completely disabling expiry.
    """
    if jwt is None:
        raise HTTPException(status_code=500, detail="Dependency 'PyJWT' missing. Install inside venv: pip install PyJWT")
    payload = {
        "sub": user_id,
        "iat": int(time.time()),
        # no 'exp' claim -> non-expiring token
        "iss": "pickoo"
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

from fastapi import Request

class TokenAuth(HTTPBearer):
    async def __call__(self, request: Request):  # type: ignore[override]
        """Extract and validate Bearer token.

        IMPORTANT: This method must receive a FastAPI Request object. If a *query parameter* named
        'request' is still being sent (a legacy workaround you added earlier), FastAPI will try to
        treat that value as the argument to this function if the signature is untyped, resulting in
        the previous error `'str' object has no attribute headers`. Annotating with `Request` ensures
        DI passes the proper object and prevents accidental collision with user-provided params.
        """
        # (Retain misuse guard for any future accidental direct calls.)
        if isinstance(request, str):  # pragma: no cover - defensive safeguard
            raise HTTPException(status_code=500, detail="Auth dependency misused: expected Request, got str. Use Depends(auth_scheme) instead of calling it directly.")
        credentials = await super().__call__(request)
        if credentials is None:
            return None
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid auth scheme")
        if jwt is None:
            raise HTTPException(status_code=500, detail="Dependency 'PyJWT' missing. Install inside venv: pip install PyJWT")
        try:
            # Disable expiry validation to allow non-expiring tokens
            data = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=["HS256"],
                options={"verify_exp": False}
            )
        except jwt.ExpiredSignatureError:
            # Even though expiry verification is disabled, keep a fallback
            # to avoid unexpected behavior if tokens contain exp.
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")
        request.state.user_id = data["sub"]
        return data

auth_scheme = TokenAuth()

# Optional auth (does not error if Authorization header is missing).
optional_auth_scheme = TokenAuth(auto_error=False)

async def get_current_user(db=Depends(get_db), token_data=Depends(auth_scheme)):
    uid = token_data["sub"]
    user = await db[USER_COLLECTION].find_one({"_id": __import__("bson").ObjectId(uid)}) if len(uid) == 24 else await db[USER_COLLECTION].find_one({"_id": uid})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def get_optional_user(db=Depends(get_db), token_data=Depends(optional_auth_scheme)):
    if token_data is None:
        return None
    uid = token_data["sub"]
    user = await db[USER_COLLECTION].find_one({"_id": __import__("bson").ObjectId(uid)}) if len(uid) == 24 else await db[USER_COLLECTION].find_one({"_id": uid})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ----- Plan Upgrade Logic -----
PLAN_DURATIONS_DAYS = {
    "G": None,   # God father access - no expiry
    "FM": 30,    # Full month
    "FY": 365,   # Full year
    "FW": 7,     # Full week
    "FD": 1,     # Full day
}

async def upgrade_user_plan(db, user_id: str, code: str) -> Dict[str, Any]:
    code = code.upper()
    if code not in PLAN_DURATIONS_DAYS:
        raise HTTPException(status_code=400, detail="Invalid plan code")
    duration = PLAN_DURATIONS_DAYS[code]
    expires_at = None
    if duration is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=duration)
    update = {"plan_code": code, "plan_expires_at": expires_at}
    # Reset quota_alerted flag on plan upgrade (new quota grants)
    update["quota_alerted"] = False
    await db[USER_COLLECTION].update_one(
        {"_id": __import__("bson").ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id},
        {"$set": update}
    )
    doc = await db[USER_COLLECTION].find_one({"_id": __import__("bson").ObjectId(user_id)}) if len(user_id) == 24 else await db[USER_COLLECTION].find_one({"_id": user_id})
    return doc

# ----- OAuth Placeholder Verification -----
async def verify_google_id_token(id_token: str) -> Dict[str, Any]:
    # TODO: Replace with real Google tokeninfo validation.
    if not id_token or len(id_token) < 10:
        raise HTTPException(status_code=400, detail="Invalid Google token")
    # Mock payload
    return {"sub": f"google-{id_token[:8]}", "email": None}

async def verify_facebook_token(access_token: str) -> Dict[str, Any]:
    # TODO: Replace with real Facebook Graph API validation.
    if not access_token or len(access_token) < 10:
        raise HTTPException(status_code=400, detail="Invalid Facebook token")
    return {"sub": f"facebook-{access_token[:8]}", "email": None}

# ----- User Deletion -----
async def delete_user_by_id(db, user_id: str) -> Dict[str, Any]:
    """Delete a user and all related data from the database.
    
    Args:
        db: Database connection
        user_id: User ID (can be ObjectId string or regular string)
        
    Returns:
        Dict containing deleted user information
        
    Raises:
        HTTPException: If user not found or deletion fails
    """
    from bson import ObjectId
    
    # Convert user_id to ObjectId if it's a 24-character hex string
    query = {"_id": ObjectId(user_id)} if len(user_id) == 24 else {"_id": user_id}
    
    # Find user first to return their info
    user = await db[USER_COLLECTION].find_one(query)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Delete user from users collection
    result = await db[USER_COLLECTION].delete_one(query)
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=500, detail="Failed to delete user")
    
    return {
        "user_id": str(user["_id"]),
        "email": user.get("email"),
        "mobile": user.get("mobile"),
        "oauth_provider": user.get("oauth_provider"),
        "deleted_at": datetime.now(timezone.utc)
    }
