import os
import json
import httpx
from typing import Optional, Dict, Any, List
from pathlib import Path
from core.config import settings

CONFIG_FILE = settings.BASE_DIR / "llm_config.json"

DEFAULT_LLM_CONFIG = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.7
}

def get_llm_config() -> Dict[str, Any]:
    """读取自定义大模型 API 配置"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_LLM_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_LLM_CONFIG.copy()

def save_llm_config(config_data: Dict[str, Any]) -> bool:
    """持久化保存自定义大模型 API 配置"""
    try:
        current = get_llm_config()
        current.update(config_data)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存 LLM 配置失败: {e}")
        return False

async def test_llm_connection(base_url: str, api_key: str, model: str) -> Dict[str, Any]:
    """测试自定义大模型 API 连通性"""
    if not api_key:
        return {"success": False, "message": "API Key 不能为空"}

    clean_url = base_url.rstrip("/")
    endpoint = f"{clean_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model or "deepseek-chat",
        "messages": [
            {"role": "user", "content": "请回复四个字：连接正常"}
        ],
        "max_tokens": 16
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return {"success": True, "message": f"连接成功！模型响应: {reply}"}
            else:
                return {
                    "success": False,
                    "status_code": resp.status_code,
                    "message": f"接口返回错误: HTTP {resp.status_code} - {resp.text[:120]}"
                }
    except Exception as e:
        return {"success": False, "message": f"连接异常: {str(e)}"}

async def call_llm_chat(messages: List[Dict[str, str]], json_mode: bool = False) -> str:
    """调用配置的大模型进行对话补全"""
    cfg = get_llm_config()
    api_key = cfg.get("api_key")
    if not api_key:
        raise ValueError("未配置大模型 API Key！请点击右上角【⚙️ API 设置】填入您的 API 密钥。")

    clean_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    endpoint = f"{clean_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": cfg.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": cfg.get("temperature", 0.7),
        "max_tokens": 1200
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"大模型 API 调用失败: HTTP {resp.status_code} - {resp.text}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]
