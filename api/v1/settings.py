from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from core.llm_client import get_llm_config, save_llm_config, test_llm_connection

router = APIRouter(prefix="/settings", tags=["系统与自定义API配置"])

class LLMConfigRequest(BaseModel):
    base_url: str = Field(..., description="OpenAI 兼容接口 Base URL，如 https://api.deepseek.com/v1")
    api_key: str = Field(..., description="API Key")
    model: str = Field("deepseek-chat", description="模型名称")
    temperature: Optional[float] = 0.7

class LLMTestRequest(BaseModel):
    base_url: str
    api_key: str
    model: str

@router.get("/llm", summary="获取当前大模型 API 配置")
def get_config():
    """读取已配置的模型信息 (脱敏返回 API Key)"""
    cfg = get_llm_config()
    raw_key = cfg.get("api_key", "")
    masked_key = ""
    if raw_key:
        masked_key = f"{raw_key[:4]}...{raw_key[-4:]}" if len(raw_key) > 8 else "***"
    return {
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "temperature": cfg.get("temperature", 0.7),
        "is_configured": bool(raw_key),
        "masked_key": masked_key
    }

@router.post("/llm", summary="保存自定义大模型 API 配置")
def update_config(req: LLMConfigRequest):
    """保存用户输入的自定义 API 配置"""
    ok = save_llm_config({
        "base_url": req.base_url.strip(),
        "api_key": req.api_key.strip(),
        "model": req.model.strip(),
        "temperature": req.temperature
    })
    if not ok:
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"success": True, "message": "大模型 API 配置已成功持久化保存！"}

@router.post("/llm/test", summary="测试自定义大模型 API 连通性")
async def test_config(req: LLMTestRequest):
    """在线测试自定义接口是否可连通与正常响应"""
    res = await test_llm_connection(req.base_url.strip(), req.api_key.strip(), req.model.strip())
    return res
