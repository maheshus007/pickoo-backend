"""Configuration management for NeuraLens backend.
Uses environment variables with prefix NEURALENS_. Optional .env file support.

Variables:
    NEURALENS_PROCESSOR_MODE = existing | new
    NEURALENS_GEMINI_BASE_URL = base URL for nano banana endpoints (default placeholder)
    NEURALENS_GEMINI_API_KEY = API key / token for authentication
    NEURALENS_MONGO_URI = mongodb connection string (e.g. mongodb://localhost:27017/neuralens)
    NEURALENS_JWT_SECRET = secret key for signing JWTs
    NEURALENS_JWT_EXP_MINUTES = access token expiry minutes (default 60)
"""
try:
    from pydantic_settings import BaseSettings  # Pydantic v2 relocated BaseSettings
except ImportError:  # Fallback if migration not applied yet
    from pydantic import BaseModel as BaseSettings  # minimal fallback (no env parsing) – advise installing pydantic-settings
from functools import lru_cache

class Settings(BaseSettings):
    # 'existing' uses local Pillow stubs; set to 'new' to use external adapter.
    # Default switched to 'existing' to avoid futile external calls against placeholder domains.
    processor_mode: str = "new"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/"
    gemini_api_key: str = "AIzaSyDRWGtaK1S30gUJf_TLCBdUklUqVIbNEkc"  # NEVER hard-code real keys; supply via env or secrets manager.
    gemini_timeout_seconds: int = 15
    gemini_max_retries: int = 3
    gemini_circuit_threshold: int = 5  # consecutive failures before opening circuit
    gemini_circuit_cooldown_seconds: int = 60  # time before attempting external again
    mongo_uri: str = "mongodb://localhost:27017/neuralens"
    jwt_secret: str = "CHANGE_ME"  # replace via environment
    jwt_exp_minutes: int = 60
    allow_fallback: bool = True  # When external (Gemini) fails: True=use local fallback, False=raise error
    gemini_verify_ssl: bool = True  # Disable ONLY for debugging cert issues; insecure if False
    strict_domain_guard: bool = True  # Abort early on known invalid domains / placeholder nano paths

    class Config:
        env_prefix = "NEURALENS_"
        env_file = ".env"
        extra = "ignore"

    @property
    def use_gemini(self) -> bool:
        return self.processor_mode.lower() == "new"
    
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
