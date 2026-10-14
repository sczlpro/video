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
    """初始化数据库表结构"""
    # 导入模型确保元数据注册
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    return True
