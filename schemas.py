from pydantic import BaseModel, Field
from typing import Optional, List

class ImageResponse(BaseModel):
    image_base64: str
    tool: str
    width: int
    height: int
    mode: str
    # Provenance metadata (optional)
    processor: Optional[str] = None  # e.g. 'gemini', 'local'
    attempts: Optional[int] = None   # number of external attempts before success/fallback
    fallback: Optional[bool] = None  # True if fell back to local after external failure

class HealthResponse(BaseModel):
    status: str
    version: str

class ToolInfo(BaseModel):
    id: str
    name: str
    endpoint: str
    description: Optional[str] = None

class ToolsResponse(BaseModel):
    tools: List[ToolInfo]

class SubscriptionStatus(BaseModel):
    user_id: str
    plan_id: str
    purchased_at: Optional[str] = None
    used_images: int
    image_quota: Optional[int]
    duration_days: Optional[int]
    expired: bool
    remaining_images: Optional[int]
    quota_exceeded: bool

class SubscriptionPurchaseRequest(BaseModel):
    user_id: str = Field(..., description="Unique user identifier or device id")
    plan_id: str

class RecordUsageRequest(BaseModel):
    user_id: str

