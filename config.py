"""Configuration management for Pickoo backend.
Uses environment variables with prefix PICKOO_. Optional .env file support.

Variables:
    PICKOO_PROCESSOR_MODE = existing | new | replicate
    PICKOO_GEMINI_BASE_URL = base URL for Gemini API (default: https://generativelanguage.googleapis.com/)
    PICKOO_GEMINI_API_KEY = API key for Gemini authentication
    PICKOO_GEMINI_MODEL = Gemini model name (default: gemini-2.0-flash-exp)
    PICKOO_MONGO_URI = mongodb connection string (e.g. mongodb://localhost:27017/pickoo)
    PICKOO_JWT_SECRET = secret key for signing JWTs
    PICKOO_JWT_EXP_MINUTES = access token expiry minutes (default 60)

    # Replicate (hosted model inference)
    PICKOO_REPLICATE_API_TOKEN = Replicate API token (preferred: set REPLICATE_API_TOKEN)
    PICKOO_REPLICATE_MODEL = Replicate model/version (default points to tencentarc/gfpgan)
"""
try:
    from pydantic_settings import BaseSettings  # Pydantic v2 relocated BaseSettings
except ImportError:  # Fallback if migration not applied yet
    from pydantic import BaseModel as BaseSettings  # minimal fallback (no env parsing) – advise installing pydantic-settings
from functools import lru_cache

class Settings(BaseSettings):
    # Read from environment PICKOO_PROCESSOR_MODE or fallback to 'existing'
    # 'existing' = local Pillow processing, 'new' = external Gemini API, 'replicate' = Replicate-hosted GFPGAN
    processor_mode: str = "existing"
    
    # Gemini API configuration - all overridable via environment variables
    gemini_base_url: str = "https://generativelanguage.googleapis.com/"
    gemini_api_key: str = ""  # Set via PICKOO_GEMINI_API_KEY
    gemini_model: str = "gemini-2.0-flash-exp"  # Model name for all endpoints
    
    gemini_timeout_seconds: int = 10  # Reduced from 15
    gemini_max_retries: int = 2  # Reduced from 3
    gemini_circuit_threshold: int = 5  # consecutive failures before opening circuit
    gemini_circuit_cooldown_seconds: int = 60  # time before attempting external again
    mongo_uri: str = "mongodb://localhost:27017/pickoo"
    jwt_secret: str = "CHANGE_ME"  # replace via environment
    jwt_exp_minutes: int = 60
    allow_fallback: bool = False  # When external (Gemini) fails: True=use local fallback, False=raise error
    gemini_verify_ssl: bool = True  # Disable ONLY for debugging cert issues; insecure if False
    strict_domain_guard: bool = True  # Abort early on known invalid domains / placeholder nano paths

    # Auth
    # Keep API protected by default; set PICKOO_REQUIRE_AUTH=0 for local dev.
    require_auth: bool = True

    # Replicate configuration
    replicate_api_token: str = ""  # Prefer setting REPLICATE_API_TOKEN; this supports PICKOO_REPLICATE_API_TOKEN
    replicate_model: str = (
        "tencentarc/gfpgan:0fbacf7afc6c144e5be9767cff80f25aff23e52b0708f17e20f9879b2f21516c"
    )
    
    # Stripe Payment Configuration
    stripe_secret_key: str = ""  # Replace with your Stripe secret key
    stripe_publishable_key: str = ""  # Replace with your Stripe publishable key
    stripe_webhook_secret: str = ""  # Stripe webhook signing secret for security
    payment_success_url: str = "http://localhost:8000/payment/success"
    payment_cancel_url: str = "http://localhost:8000/payment/cancel"

    class Config:
        env_prefix = "PICKOO_"
        env_file = ".env"
        extra = "ignore"

    @property
    def use_gemini(self) -> bool:
        return self.processor_mode.lower() == "new"

    @property
    def use_replicate(self) -> bool:
        return self.processor_mode.lower() == "replicate"
    
    @property
    def timeout(self) -> float:
        return float(self.gemini_timeout_seconds)

    @property
    def jwt_exp_seconds(self) -> int:
        return int(self.jwt_exp_minutes * 60)

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
