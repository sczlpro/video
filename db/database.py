import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from core.config import settings

# 数据库文件路径与引擎
DATABASE_URL = f"sqlite:///{settings.DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}, # SQLite 多线程支持
    echo=False
)

SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()

def get_db():
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """初始化数据库表结构并自动预置三大成员白名单"""
    import hashlib
    from . import models  # noqa: F401
    from .models import User
    Base.metadata.create_all(bind=engine)

    # 预置三位成员白名单 (默认密码 88888888，宋勇已绑定刚上传至R2的原声录音)
    db = SessionLocal()
    default_pwd_hash = hashlib.sha256("88888888".encode()).hexdigest()
    songyong_r2_voice = "https://pub-181ed1de566e43d8947046a376d951a9.r2.dev/assets/voice_%E6%A0%87%E5%87%86%E5%BD%95%E9%9F%B3%202.mp3"
    members = [
        {"username": "songyong", "name": "宋勇", "voice": songyong_r2_voice},
        {"username": "wufengcheng", "name": "吴封成", "voice": None},
        {"username": "yuanyuan", "name": "源源", "voice": None}
    ]
    for m in members:
        u = db.query(User).filter(User.username == m["username"]).first()
        if not u:
            u = User(
                username=m["username"],
                name=m["name"],
                password_hash=default_pwd_hash,
                role_front_url=None,
                role_side_url=None,
                voice_sample_url=m.get("voice")
            )
            db.add(u)
        elif m.get("voice") and not u.voice_sample_url:
            u.voice_sample_url = m.get("voice")
    db.commit()
    db.close()
    return True
