import socket
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.config import settings
from db.database import get_db
from storage.r2_client import r2_storage

router = APIRouter(prefix="/health", tags=["健康检查与系统诊断"])

def get_local_ip() -> str:
    """获取本机局域网 IP，优先返回物理 Wi-Fi/以太网 (如 192.168.x.x)"""
    try:
        # 优先检测物理网络接口
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if ip.startswith("192.168.") or ip.startswith("10."):
                return ip
        # 回退路由探测
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@router.get("", summary="系统综合健康检查与环境诊断")
def check_health(db: Session = Depends(get_db)):
    """
    检查 FastAPI 核心中枢、SQLite 数据库、Cloudflare R2 存储与局域网网络状态
    """
    # 1. 检查数据库连通性
    db_status = "UNKNOWN"
    db_error = None
    project_count = 0
    task_count = 0
    try:
        db.execute(text("SELECT 1"))
        db_status = "HEALTHY"
        # 统计现有数据条数
        from db.models import Project, RenderTask
        project_count = db.query(Project).count()
        task_count = db.query(RenderTask).count()
    except Exception as e:
        db_status = "ERROR"
        db_error = str(e)

    # 2. 检查 Cloudflare R2 对象存储状态
    r2_status = r2_storage.check_connection()

    # 3. 统计本地静态素材
    assets_dir = settings.BASE_DIR / "static" / "assets"
    local_assets = []
    if assets_dir.exists():
        for p in assets_dir.glob("*.*"):
            if p.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                local_assets.append({
                    "filename": p.name,
                    "size_kb": round(p.stat().st_size / 1024, 2),
                    "local_url": f"/static/assets/{p.name}"
                })

    local_ip = get_local_ip()

    return {
        "status": "ONLINE",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "current_time": datetime.now().isoformat(),
        "network": {
            "local_ip": local_ip,
            "port": settings.PORT,
            "mobile_access_url": f"http://{local_ip}:{settings.PORT}",
            "hint": "手机连接同一 Wi-Fi 后，可在手机浏览器中直接打开该网址"
        },
        "database": {
            "engine": "SQLite",
            "status": db_status,
            "path": str(settings.DB_PATH),
            "project_count": project_count,
            "task_count": task_count,
            "error": db_error
        },
        "storage": {
            "driver": "Cloudflare R2 (boto3)",
            "details": r2_status
        },
        "local_assets": {
            "count": len(local_assets),
            "items": local_assets
        }
    }
