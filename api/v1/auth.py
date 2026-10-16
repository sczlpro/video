import hashlib
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.database import get_db
from db.models import User

router = APIRouter(prefix="/auth", tags=["成员身份与席位隔离"])

class LoginRequest(BaseModel):
    username: str = Field(..., description="成员代号: songyong / wufengcheng / yuanyuan")
    password: str = Field(..., description="登录密码 (默认88888888)")

class ChangePasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str

class UpdateRolePhotoRequest(BaseModel):
    username: str
    role_type: str = Field(..., description="front 或 side")
    photo_url: str

@router.get("/members", summary="获取系统专属白名单成员列表")
def list_members(db: Session = Depends(get_db)):
    """返回固化的三位成员基础信息（不含密码哈希）"""
    users = db.query(User).all()
    return {
        "members": [u.to_dict() for u in users]
    }

@router.post("/login", summary="成员登录验证")
def member_login(req: LoginRequest, db: Session = Depends(get_db)):
    """验证成员代号与密码"""
    pwd_hash = hashlib.sha256(req.password.strip().encode()).hexdigest()
    user = db.query(User).filter(User.username == req.username.strip()).first()
    if not user:
        raise HTTPException(status_code=403, detail="非法访问：仅限白名单成员（宋勇、吴封成、源源）使用！")

    if user.password_hash != pwd_hash:
        raise HTTPException(status_code=400, detail="密码错误！默认密码为 88888888")

    return {
        "success": True,
        "message": f"欢迎回来，{user.name}！",
        "user": user.to_dict()
    }

@router.post("/change-password", summary="修改个人密码")
def change_password(req: ChangePasswordRequest, db: Session = Depends(get_db)):
    """成员自主修改密码"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="成员不存在")

    old_hash = hashlib.sha256(req.old_password.strip().encode()).hexdigest()
    if user.password_hash != old_hash:
        raise HTTPException(status_code=400, detail="原密码错误！")

    new_hash = hashlib.sha256(req.new_password.strip().encode()).hexdigest()
    user.password_hash = new_hash
    db.commit()
    return {"success": True, "message": "密码修改成功，请牢记您的新密码！"}

@router.post("/update-role-photo", summary="更新成员专属出镜形象照片")
def update_role_photo(req: UpdateRolePhotoRequest, db: Session = Depends(get_db)):
    """持久化保存成员自己的正面/侧面照至数据库，手机端随身同步"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="成员不存在")

    if req.role_type == "front":
        user.role_front_url = req.photo_url
    else:
        user.role_side_url = req.photo_url

    db.commit()
    return {"success": True, "message": "专属出镜形象已更新！", "user": user.to_dict()}

class UpdateVoiceSampleRequest(BaseModel):
    username: str
    voice_url: str

@router.post("/update-voice-sample", summary="更新成员专属克隆原声音频")
def update_voice_sample(req: UpdateVoiceSampleRequest, db: Session = Depends(get_db)):
    """持久化保存成员自己的 5~15 秒原声录音直链，供 indextts2-v1 克隆使用"""
    user = db.query(User).filter(User.username == req.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="成员不存在")

    user.voice_sample_url = req.voice_url
    db.commit()
    return {"success": True, "message": "专属原声音频已绑定！", "user": user.to_dict()}
