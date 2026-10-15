import os
import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from core.config import settings
from storage.r2_client import r2_storage

router = APIRouter(prefix="/storage", tags=["存储管理与素材同步"])

@router.get("/status", summary="查询 Cloudflare R2 存储连通性与配置状态")
def get_storage_status():
    return r2_storage.check_connection()

@router.post("/upload-local", summary="上传素材图片到本地素材库 (免R2也可直接使用)")
async def upload_local_asset(
    file: UploadFile = File(..., description="要上传的素材图片"),
    category: str = Form("character", description="素材类别: character (主角) 或 scene (场景图)")
):
    """
    接收用户上传的本地照片，自动存入 static/assets/ 目录并返回直链
    """
    assets_dir = settings.BASE_DIR / "static" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 规范文件名
    clean_filename = f"{category}_{file.filename}"
    save_path = assets_dir / clean_filename

    try:
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        return {
            "success": True,
            "filename": clean_filename,
            "category": category,
            "local_url": f"/static/assets/{clean_filename}",
            "size_kb": round(len(content) / 1024, 2),
            "message": "素材上传成功，已加入随身素材库！"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存素材文件失败: {str(e)}")

@router.post("/upload", summary="上传文件至 Cloudflare R2 并生成公网直链")
async def upload_file_to_r2(
    file: UploadFile = File(..., description="要上传的文件"),
    folder: str = Form("uploads", description="远程存储子目录")
):
    if not settings.is_r2_configured:
        raise HTTPException(
            status_code=400,
            detail="Cloudflare R2 尚未配置凭证，无法上传！请先配置 .env 文件中的 CF_ACCOUNT_ID 等密钥。"
        )

    remote_key = f"{folder.strip('/')}/{file.filename}"
    try:
        content = await file.read()
        public_url = r2_storage.upload_bytes(
            data=content,
            remote_key=remote_key,
            content_type=file.content_type or "application/octet-stream"
        )
        return {
            "success": True,
            "filename": file.filename,
            "remote_key": remote_key,
            "public_url": public_url,
            "size_bytes": len(content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传至 R2 失败: {str(e)}")

@router.get("/assets", summary="获取本地与云端素材库列表")
def list_local_assets():
    assets_dir = settings.BASE_DIR / "static" / "assets"
    characters = []
    scenes = []

    if assets_dir.exists():
        for p in assets_dir.glob("*.*"):
            if p.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
                is_scene = "场景" in p.name or "电站" in p.name or "屋顶" in p.name or "门头" in p.name or p.name.startswith("scene_")
                item = {
                    "name": p.name,
                    "stem": p.stem,
                    "size_kb": round(p.stat().st_size / 1024, 2),
                    "local_url": f"/static/assets/{p.name}",
                    "r2_url": r2_storage.get_public_url(f"assets/{p.name}") if settings.is_r2_configured else None
                }
                if is_scene:
                    scenes.append(item)
                else:
                    characters.append(item)

    return {
        "count": len(characters) + len(scenes),
        "characters": characters,
        "scenes": scenes,
        "all": characters + scenes
    }
