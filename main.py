import os
import sys

# 兼容 Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from core.config import settings
from db.database import init_db
from api.v1 import api_v1_router
from api.v1.health import get_local_ip
from storage.r2_client import r2_storage

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 服务启动前：自动初始化 SQLite 数据库
    init_db()
    yield
    # 服务关闭时清理

app = FastAPI(
    title=settings.APP_NAME,
    description="川渝光伏自用 30 秒短视频随身生产线 (阿勇短视频 WebApp 后端中枢)",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自定义带永久强缓存的静态文件处理中间件 (彻底解决手机公网重复下载卡顿)
class CachedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # 为所有 mp4 视频和静态素材注入 30 天强缓存头，手机浏览器只要播过一次，永久在手机本地沙箱秒开！
        resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return resp

# 挂载静态资源
assets_dir = settings.BASE_DIR / "static"
if not assets_dir.exists():
    assets_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", CachedStaticFiles(directory=str(assets_dir)), name="static")

# 注册 API 路由
app.include_router(api_v1_router)

@app.get("/", response_class=HTMLResponse, summary="即梦App像素级专业短视频随身工作台")
def index():
    """返回纯原生零外部依赖的即梦App像素级深色磨砂生产工作台"""
    template_path = settings.BASE_DIR / "templates" / "index.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Template Not Found</h1>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    print(f"🚀 正在启动 {settings.APP_NAME}...")
    print(f"🌐 本地访问地址: http://127.0.0.1:{settings.PORT}")
    print(f"📱 手机局域网访问地址: http://{get_local_ip()}:{settings.PORT}")
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=False)
