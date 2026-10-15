import json
import uuid
from datetime import datetime
from typing import Optional, List, Any
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    ForeignKey
)
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field

from .database import Base

def generate_uuid() -> str:
    """生成唯一标识码"""
    return uuid.uuid4().hex

class User(Base):
    """用户成员表 (白名单席位隔离)"""
    __tablename__ = "users"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    username = Column(String(64), unique=True, nullable=False, doc="成员唯一英文代号: songyong / wufengcheng / yuanyuan")
    name = Column(String(64), nullable=False, doc="显示中文姓名: 宋勇 / 吴封成 / 源源")
    password_hash = Column(String(128), nullable=False, doc="密码哈希 (默认88888888)")
    role_front_url = Column(Text, nullable=True, doc="专属主角正面特写照")
    role_side_url = Column(Text, nullable=True, doc="专属主角45度侧身照")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "name": self.name,
            "role_front_url": self.role_front_url,
            "role_side_url": self.role_side_url
        }

class Project(Base):
    """短视频项目主表"""
    __tablename__ = "projects"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    owner = Column(String(64), default="songyong", doc="所属成员: songyong/wufengcheng/yuanyuan")
    title = Column(String(255), nullable=False, doc="选题标题")
    category = Column(String(64), nullable=False, doc="业务线: 户用重庆 / 工商业川渝")
    hook_type = Column(String(64), nullable=True, doc="开门Hook风格 (7大风格之一)")
    full_script = Column(Text, nullable=True, doc="120~135字完整台词")
    total_words = Column(Integer, default=0, doc="纯台词字数")
    moderation_pass = Column(Boolean, default=False, doc="机审是否100%通过")
    status = Column(String(32), default="DRAFT", doc="状态: DRAFT/PENDING_CONFIRM/RENDERING/COMPLETED/FAILED")
    final_video_url = Column(Text, nullable=True, doc="Cloudflare R2 成片母带公网URL")
    viral_titles = Column(Text, nullable=True, doc="10个爆款标题候选(JSON字符串)")
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联关系
    shots = relationship("Shot", back_populates="project", cascade="all, delete-orphan", order_by="Shot.shot_order")
    render_tasks = relationship("RenderTask", back_populates="project", cascade="all, delete-orphan", order_by="RenderTask.created_at.desc()")

    def to_dict(self) -> dict:
        """导出字典"""
        titles = []
        if self.viral_titles:
            try:
                titles = json.loads(self.viral_titles)
            except Exception:
                titles = [self.viral_titles]
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "hook_type": self.hook_type,
            "full_script": self.full_script,
            "total_words": self.total_words,
            "moderation_pass": self.moderation_pass,
            "status": self.status,
            "final_video_url": self.final_video_url,
            "viral_titles": titles,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "shots": [s.to_dict() for s in self.shots] if self.shots else []
        }

class Shot(Base):
    """分镜明细表"""
    __tablename__ = "shots"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    project_id = Column(String(64), ForeignKey("projects.id"), nullable=False)
    shot_order = Column(Integer, nullable=False, doc="镜号 (1~4)")
    duration = Column(Integer, nullable=False, default=8, doc="分镜时长 (秒)")
    speaker = Column(String(64), default="阿勇", doc="发言角色 (阿勇/老刘等)")
    dialogue = Column(Text, nullable=True, doc="该镜对应台词内容")
    prompt = Column(Text, nullable=False, doc="视觉调度英文提示词 (严格防念提示词)")
    audio_url = Column(Text, nullable=True, doc="配音音频URL (ref_audio_0 槽位驱动)")
    shot_video_url = Column(Text, nullable=True, doc="单镜头渲染视频URL")
    reference_image = Column(Text, nullable=True, doc="首帧参考图 (first_frame 锚点)")
    created_at = Column(DateTime, default=datetime.now)

    project = relationship("Project", back_populates="shots")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "shot_order": self.shot_order,
            "duration": self.duration,
            "speaker": self.speaker,
            "dialogue": self.dialogue,
            "prompt": self.prompt,
            "audio_url": self.audio_url,
            "shot_video_url": self.shot_video_url,
            "reference_image": self.reference_image,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

class RenderTask(Base):
    """AutoDL 算力渲染任务跟踪表"""
    __tablename__ = "render_tasks"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    project_id = Column(String(64), ForeignKey("projects.id"), nullable=False)
    task_id = Column(String(128), nullable=True, doc="AutoDL 任务实例ID")
    workflow_id = Column(String(64), default="minimax_h3_zm_u24", doc="工作流名称")
    cost_estimated = Column(Float, default=1.20, doc="预估算力费用 (¥)")
    user_confirmed = Column(Boolean, default=False, doc="★ 用户是否已在弹窗手动确认 (绝对红线安全锁)")
    confirmed_at = Column(DateTime, nullable=True, doc="用户确认时间")
    status = Column(String(32), default="INIT", doc="状态: INIT/SUBMITTED/RUNNING/SUCCESS/FAILED")
    error_message = Column(Text, nullable=True, doc="失败报错信息")
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    project = relationship("Project", back_populates="render_tasks")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "workflow_id": self.workflow_id,
            "cost_estimated": self.cost_estimated,
            "user_confirmed": self.user_confirmed,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


# ================== Pydantic 校验模型 ==================

class ShotSchema(BaseModel):
    shot_order: int
    duration: int
    speaker: str = "阿勇"
    dialogue: Optional[str] = None
    prompt: str
    audio_url: Optional[str] = None
    reference_image: Optional[str] = None

class ProjectCreateSchema(BaseModel):
    title: str
    category: str = Field(..., description="业务线: 户用重庆 / 工商业川渝")
    hook_type: Optional[str] = None
    full_script: str
    shots: Optional[List[ShotSchema]] = None

class RenderPreCheckResponse(BaseModel):
    project_id: str
    title: str
    category: str
    total_duration: int
    cost_estimated: float
    shots_count: int
    shots_summary: List[dict]
    confirmation_token: str
    message: str = "★ 必须由用户手动在弹窗中点击确认后方可发牌提交！"
