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
    status_code: str = Field(..., description="Subscription status: F=Free, FD=FullDay, FW=FullWeek, FM=FullMonth, FY=FullYear, G=GodMode")
    purchased_at: Optional[str] = None
    expires_at: Optional[str] = None
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

# Payment Schemas
class CreateCheckoutRequest(BaseModel):
    user_id: str = Field(..., description="User ID making the purchase")
    plan_id: str = Field(..., description="Subscription plan ID")
    country_code: str = Field(default="US", description="ISO country code for currency detection")
    success_url: Optional[str] = Field(None, description="Custom success redirect URL")
    cancel_url: Optional[str] = Field(None, description="Custom cancel redirect URL")

class CheckoutResponse(BaseModel):
    session_id: str
    checkout_url: str
    amount: int
    currency: str
    
class PaymentRecord(BaseModel):
    user_id: str
    session_id: str
    plan_id: str
    plan_name: str
    amount: int
    currency: str
    base_price_usd: float
    status: str
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    
class PaymentHistoryResponse(BaseModel):
    payments: List[PaymentRecord]
    total_count: int

class CurrencyResponse(BaseModel):
    country_code: str
    currency: str
    symbol: str
    
class WebhookResponse(BaseModel):
    status: str
    message: str

# Transaction Tracking Schemas
class TransactionRecord(BaseModel):
    transaction_id: str = Field(..., description="Unique transaction identifier")
    user_id: str = Field(..., description="User who made the purchase")
    user_email: Optional[str] = Field(None, description="User email at time of purchase")
    plan_id: str = Field(..., description="Purchased plan ID")
    plan_name: str = Field(..., description="Human-readable plan name")
    product_id: Optional[str] = Field(None, description="Google Play/App Store product ID")
    
    # Payment details
    amount: float = Field(..., description="Amount paid in the currency")
    currency: str = Field(..., description="Currency code (INR, USD, etc)")
    amount_usd: float = Field(..., description="Amount converted to USD for reporting")
    payment_method: str = Field(..., description="google_play, app_store, stripe, razorpay, etc")
    
    # Platform-specific IDs
    purchase_token: Optional[str] = Field(None, description="Google Play purchase token")
    order_id: Optional[str] = Field(None, description="Platform order ID")
    receipt_data: Optional[str] = Field(None, description="App Store receipt data")
    session_id: Optional[str] = Field(None, description="Stripe/Razorpay session ID")
    
    # Subscription details
    subscription_start_date: str = Field(..., description="When subscription becomes active")
    subscription_end_date: Optional[str] = Field(None, description="When subscription expires (if applicable)")
    duration_days: Optional[int] = Field(None, description="Subscription duration in days")
    image_quota: Optional[int] = Field(None, description="Number of images included")
    
    # Status tracking
    status: str = Field(..., description="pending, completed, failed, refunded, cancelled")
    verified: bool = Field(default=False, description="Whether purchase was verified with platform")
    
    # Timestamps
    created_at: str = Field(..., description="Transaction creation timestamp")
    completed_at: Optional[str] = Field(None, description="When transaction completed")
    updated_at: str = Field(..., description="Last update timestamp")
    
    # Metadata
    device_platform: Optional[str] = Field(None, description="android, ios, web")
    app_version: Optional[str] = Field(None, description="App version at time of purchase")
    country_code: Optional[str] = Field(None, description="User country code")
    ip_address: Optional[str] = Field(None, description="User IP address")
    
    # Additional notes
    notes: Optional[str] = Field(None, description="Any additional notes or error messages")

class TransactionListResponse(BaseModel):
    transactions: List[TransactionRecord]
    total_count: int
    page: int
    page_size: int

class UserDeleteResponse(BaseModel):
    status: str
    message: str
    user_id: str
    deleted_at: str
