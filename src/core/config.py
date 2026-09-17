from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True
    )

    PROJECT_NAME: str = "Fuelflux"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # MongoDB Atlas
    MONGODB_URL: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "fuelflux"

    # Security
    SECRET_KEY: str = "supersecretkeychangethisinproduction2026"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080

    # Azure (keep optional)
    AZURE_IOT_HUB_CONNECTION_STRING: Optional[str] = None

    # Razorpay
    RAZORPAY_KEY_ID: Optional[str] = None
    RAZORPAY_KEY_SECRET: Optional[str] = None
    RAZORPAY_WEBHOOK_SECRET: Optional[str] = None
    RAZORPAYX_ACCOUNT_NUMBER: Optional[str] = None


    # Google Authentication
    GOOGLE_CLIENT_ID: Optional[str] = None


settings = Settings()