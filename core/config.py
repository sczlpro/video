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
    CF_ACCOUNT_ID: str = os.getenv("CF_ACCOUNT_ID", "76a15b9c1f33264e46cab2d918e3621c")
    CF_R2_ACCESS_KEY_ID: str = os.getenv("CF_R2_ACCESS_KEY_ID", "3bcbaa0f5907445d25feaa6c2f783ead")
    CF_R2_SECRET_ACCESS_KEY: str = os.getenv("CF_R2_SECRET_ACCESS_KEY", "0f2647d8f893e96b6e67ff71d545652bd701113d7840b1c29e499a7d968f7151")
    CF_R2_BUCKET: str = os.getenv("CF_R2_BUCKET", "video")
    CF_R2_PUBLIC_DOMAIN: str = os.getenv("CF_R2_PUBLIC_DOMAIN", "pub-181ed1de566e43d8947046a376d951a9.r2.dev")

    # 外部算力配置 (AutoDL)
    AUTODL_TOKEN: str = os.getenv("AUTODL_TOKEN", "4jOhvQKivGDj8NeCzCC3XtQVbKW7uIlspYQcAlW0vQvNSVQK")

    # 阿里云百炼旗舰音频配置 (Qwen-Audio-3.1-TTS-Flash)
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "sk-ws-H.PYDDEER.GHs7.MEYCIQC_ReYeKULRzPWyJMH922-haEb_TvRvRRJBny5f5PSQYAIhANJSB7JRSfYsQs6vX7oSa5s8EluyM8B8fjGVPJ-xsYHQ")
    DASHSCOPE_TTS_URL: str = os.getenv("DASHSCOPE_TTS_URL", "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer")

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
