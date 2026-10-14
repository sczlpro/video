import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 加载 .env
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings(BaseSettings):
    # 项目根路径
    BASE_DIR: Path = BASE_DIR

    # 应用基础配置
    APP_NAME: str = "A-Yong Solar Video WebApp"
    APP_ENV: str = os.getenv("APP_ENV", "development")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

    # SQLite 数据库配置
    DB_PATH: Path = BASE_DIR / "solar_production.db"

    # Cloudflare R2 对象存储配置 (3 个核心凭证 + 桶名与公开域名)
    CF_ACCOUNT_ID: str = os.getenv("CF_ACCOUNT_ID", "")
    CF_R2_ACCESS_KEY_ID: str = os.getenv("CF_R2_ACCESS_KEY_ID", "")
    CF_R2_SECRET_ACCESS_KEY: str = os.getenv("CF_R2_SECRET_ACCESS_KEY", "")
    CF_R2_BUCKET: str = os.getenv("CF_R2_BUCKET", "ayong-solar-video")
    CF_R2_PUBLIC_DOMAIN: str = os.getenv("CF_R2_PUBLIC_DOMAIN", "")

    # 外部算力与TTS配置预留
    AUTODL_API_KEY: Optional[str] = os.getenv("AUTODL_API_KEY", "")
    DASHSCOPE_API_KEY: Optional[str] = os.getenv("DASHSCOPE_API_KEY", "")

    @property
    def is_r2_configured(self) -> bool:
        """检查 R2 所需凭证是否完整配置"""
        return bool(
            self.CF_ACCOUNT_ID
            and self.CF_R2_ACCESS_KEY_ID
            and self.CF_R2_SECRET_ACCESS_KEY
        )

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
