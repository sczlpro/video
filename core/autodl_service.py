import os
import sys
import json
import time
import uuid
import hashlib
import threading
import io
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

from core.config import settings
from core.assembler import concat_videos_ffmpeg
from storage.r2_client import r2_storage
from db.database import SessionLocal
from db.models import Project, RenderTask

CONFIG_PATH = Path(r"C:\Users\Administrator\.dsh\tools\dh_api_config.json")

class AutoDLService:
    """AutoDL 算力云端联动与分镜渲染调度引擎 (全链路原生接入 Cloudflare R2 对象存储)"""

    def __init__(self):
        self.config = self._load_config()
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._upload_cache: Dict[str, str] = {}
        self.executor = ThreadPoolExecutor(max_workers=5)

    def _load_config(self) -> dict:
        """从环境变量或本地配置读取鉴权与端点"""
        base_cfg = {
            "autodl": {
                "base_url": os.getenv("AUTODL_BASE_URL", "https://www.autodl.art"),
                "token": os.getenv("AUTODL_TOKEN", settings.AUTODL_TOKEN),
                "submit_path": "/api/v1/comfyui/comfyui_workflow/{workflow_id}",
                "result_path": "/api/v1/comfyui/comfyui_workflow/result/{task_id}",
            },
            "video": {
                "workflow_id": "minimax_h3_zm_u24",
                "resolution": "768p竖",
                "poll_interval_sec": 5,
                "timeout_sec": 1800,
            }
        }

        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    if "autodl" in file_cfg:
                        base_cfg["autodl"].update(file_cfg["autodl"])
            except Exception as e:
                print(f"[AutoDLService] 读取本地配置异常: {e}")

        return base_cfg

    # ----------------- 鉴权头 -----------------
    def autodl_headers(self) -> dict:
        tok = self.config.get("autodl", {}).get("token", "")
        return {
            "Authorization": tok,
            "Content-Type": "application/json"
        }

    # ----------------- 素材直传（Cloudflare R2 极速对象存储，带智能轻量化与SHA256去重） -----------------
    def _prepare_upload_bytes(self, file_path: Path) -> tuple[bytes, str]:
        """
        准备上传字节：
        1. 针对大图等比缩放至最长边 1920，体积显著降低，加快云端拉取速度；
        2. 返回安全纯 ASCII 英文文件名。
        """
        raw_bytes = file_path.read_bytes()
        h = hashlib.md5(raw_bytes).hexdigest()
        safe_name = f"ref_{h[:16]}.jpg"

        if len(raw_bytes) > 1.2 * 1024 * 1024 or file_path.suffix.lower() not in [".jpg", ".jpeg"]:
            try:
                with Image.open(io.BytesIO(raw_bytes)) as img:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    w, h_dim = img.size
                    max_dim = max(w, h_dim)
                    if max_dim > 1920:
                        scale = 1920 / max_dim
                        new_w, new_h = int(w * scale), int(h_dim * scale)
                        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    out_io = io.BytesIO()
                    img.save(out_io, format="JPEG", quality=90, optimize=True)
                    compressed = out_io.getvalue()
                    return compressed, safe_name
            except Exception as e:
                print(f"[AutoDLService] 图片轻量化处理异常，回退原文件: {e}")

        return raw_bytes, safe_name

    def upload_file_to_storage(self, file_path: Path) -> str:
        """将本地素材直传至 Cloudflare R2 对象存储，生成全球可访问的永久 HTTPS 直链"""
        if not file_path.exists():
            raise FileNotFoundError(f"素材文件不存在: {file_path}")

        # 计算 SHA256 缓存去重
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            h.update(f.read(1024 * 512))
        file_hash = h.hexdigest()

        with self._lock:
            if file_hash in self._upload_cache:
                return self._upload_cache[file_hash]

        upload_bytes, safe_filename = self._prepare_upload_bytes(file_path)
        remote_key = f"assets/{safe_filename}"

        # 直传 Cloudflare R2
        try:
            public_url = r2_storage.upload_bytes(
                data=upload_bytes,
                remote_key=remote_key,
                content_type="image/jpeg"
            )
            with self._lock:
                self._upload_cache[file_hash] = public_url
            print(f"[AutoDLService] 素材已直传 Cloudflare R2: {safe_filename} -> {public_url}")
            return public_url
        except Exception as e:
            print(f"[AutoDLService] R2 上传失败: {e}")
            raise RuntimeError(f"Cloudflare R2 素材上传失败: {e}")

    # ----------------- 算力接口 -----------------
    def submit_autodl_workflow(self, workflow_id: str, payload: dict) -> str:
        """向 AutoDL ComfyUI 提交渲染任务 (带自动重试)"""
        base_url = self.config.get("autodl", {}).get("base_url", "https://www.autodl.art").rstrip("/")
        path = self.config.get("autodl", {}).get("submit_path", "/api/v1/comfyui/comfyui_workflow/{workflow_id}")
        url = base_url + path.format(workflow_id=workflow_id)

        last_err = None
        for attempt in range(1, 4):
            try:
                resp = requests.post(
                    url,
                    headers=self.autodl_headers(),
                    json=payload,
                    timeout=60
                )
                res_data = resp.json()
                if res_data.get("code") == "Success":
                    return res_data["data"]["task_id"]
                else:
                    raise RuntimeError(f"AutoDL 返回错误码: {res_data}")
            except Exception as e:
                last_err = e
                print(f"[AutoDLService] AutoDL 任务提交第 {attempt} 次异常: {e}")
                time.sleep(1.5 * attempt)

        raise RuntimeError(f"AutoDL 任务提交失败: {last_err}")

    def query_task_status(self, task_id: str) -> dict:
        """查询 AutoDL 任务最新状态 (内置 3 次重试，隔离瞬时网络抖动)"""
        base_url = self.config.get("autodl", {}).get("base_url", "https://www.autodl.art").rstrip("/")
        path = self.config.get("autodl", {}).get("result_path", "/api/v1/comfyui/comfyui_workflow/result/{task_id}")
        url = base_url + path.format(task_id=task_id)

        for attempt in range(1, 4):
            try:
                resp = requests.get(url, headers=self.autodl_headers(), timeout=30)
                res_data = resp.json()
                data = res_data.get("data") or {}
                st = data.get("status") or "UNKNOWN"
                results = data.get("results") or []
                video_url = results[0].get("url") if results else None
                return {
                    "status": st,
                    "video_url": video_url,
                    "raw": res_data
                }
            except Exception as e:
                if attempt < 3:
                    time.sleep(1.5)
                else:
                    # 单次查询网络失败，返回 QUERY_RETRY，绝不能误判为云端任务失败
                    return {
                        "status": "QUERY_RETRY",
                        "video_url": None,
                        "error": str(e)
                    }

    # ----------------- 异步渲染任务调度中心 -----------------
    def create_render_job(self, project_id: str, segments: List[dict], mode: str = "cloud", owner: str = "songyong") -> str:
        """初始化一个渲染任务批次，并在后台线程启动监控"""
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        total_duration = sum(s.get("duration", 10) for s in segments)
        cost_charged = round(total_duration * 0.040, 2)

        job_info = {
            "job_id": job_id,
            "project_id": project_id,
            "owner": owner,
            "status": "QUEUED",
            "progress": 5,
            "mode": mode,
            "step": "正在准备任务与解析分镜...",
            "duration": total_duration,
            "cost_charged": f"¥{cost_charged:.2f}",
            "segments": [
                {
                    "id": s.get("id", i + 1),
                    "duration": s.get("duration", 10),
                    "status": "WAITING",
                    "task_id": None,
                    "video_url": None,
                    "local_file": None,
                    "prompt_preview": s.get("prompt", "")[:60]
                }
                for i, s in enumerate(segments)
            ],
            "video_url": None,
            "download_url": None,
            "filename": None,
            "error": None,
            "started_at": now_str,
            "updated_at": now_str,
        }

        with self._lock:
            self.jobs[job_id] = job_info

        # 启动后台工作线程
        threading.Thread(target=self._run_job_worker, args=(job_id, segments, mode), daemon=True).start()
        return job_id

    def get_job_status(self, job_id: str) -> Optional[dict]:
        """获取任务最新状态字典"""
        with self._lock:
            return self.jobs.get(job_id)

    def get_active_jobs(self, owner: Optional[str] = None) -> List[dict]:
        """获取活跃渲染任务，支持按席位隔离或返回全部"""
        with self._lock:
            active = []
            for jid, info in self.jobs.items():
                if info.get("status") in ("QUEUED", "RUNNING"):
                    job_owner = info.get("owner", "songyong")
                    if owner and owner != "all" and job_owner != owner:
                        continue
                    active.append({
                        "job_id": jid,
                        "project_id": info.get("project_id"),
                        "owner": job_owner,
                        "status": info.get("status"),
                        "progress": info.get("progress"),
                        "step": info.get("step"),
                        "duration": info.get("duration"),
                        "started_at": info.get("started_at"),
                        "segments": info.get("segments", [])
                    })
            return active

    def generate_voice_via_qwen_audio(self, prompt_text: str, custom_voice_id: Optional[str] = None, user_instruction: Optional[str] = None) -> tuple[Optional[str], Optional[float]]:
        """
        调用阿里云百炼旗舰级 Qwen-Audio-3.1-TTS-Flash 语音大模型
        1. 支持用户在台词前加【语气标签】(如【质问】【兴奋】【沉稳】) 直接控制；
        2. 无标签时根据台词语义自适应智能推断，彻底实现抑扬顿挫！
        返回: (音频可公开访问直链, 真实音频时长秒数)
        """
        if not prompt_text:
            return None, None

        api_key = settings.DASHSCOPE_API_KEY
        if not api_key:
            return None, None

        clean_text = prompt_text.strip()

        # 优先读取用户显式指定的语气指令
        if user_instruction and len(user_instruction.strip()) > 1:
            instruction = user_instruction.strip()
        else:
            # 根据台词语义，自适应注入 Qwen-Audio 专属的 free-style 情绪与发音指令
            if any(k in clean_text for k in ["肉紧", "痛", "死扛", "发冲", "漏水", "贵", "烦心", "扯拐", "心痛", "吓人", "怎么"]):
                instruction = "语气带着强烈的质疑与反问，情绪急迫有力，重点词加重读音，句尾明显挑高！"
            elif any(k in clean_text for k in ["真金白银", "省下", "一大坨", "全额", "安逸", "到手", "打折", "免费", "现金"]):
                instruction = "语气高亢喜悦，充满自信与感染力，声音洪亮饱满！"
            elif any(k in clean_text for k in ["算一算", "测算", "私信", "落教", "电话", "留个面积", "明白", "亲自"]):
                instruction = "语速沉稳从容，咬字清晰，展现出权威资方的深厚底气与诚恳！"
            else:
                instruction = "语调自然生动，富有抑扬顿挫的商务说服力与交流感！"

        # 优先使用宋勇在百炼复刻成功的专属 Voice ID，未复刻前使用 xunanchuan_v3.1
        SONGYONG_OFFICIAL_VOICE_ID = "qwen-audio-3.1-tts-flash-bailian-d6f061f198d247979cd9d96c466ea1d2"
        voice_to_use = custom_voice_id if custom_voice_id else SONGYONG_OFFICIAL_VOICE_ID

        payload = {
            "model": "qwen-audio-3.1-tts-flash",
            "input": {
                "text": clean_text,
                "instruction": instruction
            },
            "parameters": {
                "voice": voice_to_use,
                "format": "wav",
                "sample_rate": 24000
            }
        }

        endpoint = settings.DASHSCOPE_TTS_URL
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            print(f"[QwenAudioTTS] 正在调用 Qwen-Audio-3.1 合成语音: '{clean_text[:20]}...' (音色: {voice_to_use})")
            req = requests.post(endpoint, json=payload, headers=headers, timeout=20)
            if req.status_code == 200:
                res_data = req.json()
                audio_raw_url = res_data.get("output", {}).get("audio", {}).get("url")
                if not audio_raw_url:
                    print(f"[QwenAudioTTS] 响应未包含音频URL: {res_data}")
                    return None, None

                # 关键：下载该音频并用 FFmpeg 精确测量物理时长
                temp_audio_path = settings.BASE_DIR / "static" / "videos" / "temp" / f"qwen_{uuid.uuid4().hex[:8]}.wav"
                temp_audio_path.parent.mkdir(parents=True, exist_ok=True)

                r_aud = requests.get(audio_raw_url, timeout=20)
                with open(temp_audio_path, "wb") as f:
                    f.write(r_aud.content)

                # 测量精确物理秒数
                exact_duration = 5.0
                try:
                    probe_cmd = ["ffmpeg", "-i", str(temp_audio_path)]
                    p_res = subprocess.run(probe_cmd, capture_output=True, text=True, errors="ignore")
                    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p_res.stderr)
                    if m:
                        h, mm, ss = m.groups()
                        exact_duration = round(int(h) * 3600 + int(mm) * 60 + float(ss), 2)
                        print(f"[QwenAudioTTS] 音频精确物理时长测量成功: {exact_duration} 秒")
                except Exception as e:
                    print(f"[QwenAudioTTS] 测量时长异常: {e}")

                # 将高质量音频上传至 Cloudflare R2，确保全球公网畅通
                r2_audio_url = self.upload_file_to_storage(temp_audio_path)
                return r2_audio_url, exact_duration
            else:
                print(f"[QwenAudioTTS] 调用百炼合成接口失败: HTTP {req.status_code} - {req.text}")
                return None, None
        except Exception as e:
            print(f"[QwenAudioTTS] 异常，降级处理: {e}")
            return None, None

    def _run_job_worker(self, job_id: str, segments: List[dict], mode: str):
        """后台异步处理线程：负责并发提交、轮询监控、下载切片与FFmpeg 0帧无损硬切"""
        time.sleep(0.5)

        # 1. 模拟模式 (免扣算力秒级全流程校验)
        if mode == "mock":
            self._run_mock_pipeline(job_id, segments)
            return

        # 2. 云端真实模式 (AutoDL minimax_h3_zm_u24)
        self._run_cloud_pipeline(job_id, segments)

    def _run_mock_pipeline(self, job_id: str, segments: List[dict]):
        """本地极速模拟链路：模拟真实阶段演进，最后调用本地切片秒级硬切合成"""
        try:
            with self._lock:
                job = self.jobs[job_id]
                job["status"] = "RUNNING"
                job["step"] = "正在模拟云端排队与算力分配..."
                job["progress"] = 25
                for seg in job["segments"]:
                    seg["status"] = "RUNNING"
                    seg["task_id"] = f"mock-task-{uuid.uuid4().hex[:8]}"

            time.sleep(1.2)

            with self._lock:
                job["step"] = "各分镜正在云端渲染中 (768p竖)..."
                job["progress"] = 65

            time.sleep(1.5)

            with self._lock:
                job["step"] = "分镜渲染完毕，正在执行 FFmpeg 0 帧硬切极速合成..."
                job["progress"] = 90
                for seg in job["segments"]:
                    seg["status"] = "SUCCESS"

            # 极速合成
            raw_shots_dir = settings.BASE_DIR / "static" / "videos" / "raw_shots"
            raw_segments = sorted(list(raw_shots_dir.glob("原片_段*.mp4")))
            if not raw_segments:
                demo_video = settings.BASE_DIR / "static" / "videos" / "示范成品_30秒户用自建房_阿勇_无字幕.mp4"
                raw_segments = [demo_video]

            output_filename = f"30秒川渝光伏_阿勇成片_模拟_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            assemble_res = concat_videos_ffmpeg(raw_segments, output_filename)

            # 更新数据库
            self._update_db_success(job["project_id"], assemble_res["local_url"])

            with self._lock:
                job["status"] = "COMPLETED"
                job["progress"] = 100
                job["step"] = "🎉 成片母带已秒级合成完成！"
                job["video_url"] = assemble_res["local_url"]
                job["download_url"] = assemble_res["download_url"]
                job["filename"] = assemble_res["filename"]
                job["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            with self._lock:
                self.jobs[job_id]["status"] = "FAILED"
                self.jobs[job_id]["error"] = str(e)
                self.jobs[job_id]["step"] = f"合成失败: {e}"

    def _run_cloud_pipeline(self, job_id: str, segments: List[dict]):
        """真实云端链路：上传底牌 ➔ 提交 AutoDL ➔ 循环轮询 ➔ 下载 ➔ FFmpeg 0 帧硬切"""
        assets_dir = settings.BASE_DIR / "static" / "assets"
        front_img = assets_dir / "主角_本人业务经理_正面特写.jpg"
        side_img = assets_dir / "主角_本人业务经理_45度侧身.jpg"

        try:
            # 步骤 1: 上传真人身份底牌至 Cloudflare R2
            with self._lock:
                job = self.jobs[job_id]
                job["status"] = "RUNNING"
                job["step"] = "正在通过 Cloudflare R2 极速通道同步真人出镜底牌..."
                job["progress"] = 15

            front_url = self.upload_file_to_storage(front_img)
            side_url = self.upload_file_to_storage(side_img)

            # 步骤 2: 逐段下发 AutoDL 云端任务
            with self._lock:
                job["step"] = "正在向 AutoDL (minimax_h3_zm_u24) 下发分镜任务..."
                job["progress"] = 25

            wf_id = self.config.get("video", {}).get("workflow_id", "minimax_h3_zm_u24")
            resolution = self.config.get("video", {}).get("resolution", "768p竖")

            # 检查当前成员是否有绑定的专属 Voice ID (宋勇专属百炼复刻ID硬编码托底)
            SONGYONG_OFFICIAL_VOICE_ID = "qwen-audio-3.1-tts-flash-bailian-d6f061f198d247979cd9d96c466ea1d2"
            user_voice_id = None

            if job.get("owner", "songyong") == "songyong":
                user_voice_id = SONGYONG_OFFICIAL_VOICE_ID
            else:
                try:
                    db_auth = SessionLocal()
                    owner_user = db_auth.query(User).filter(User.username == job.get("owner")).first()
                    if owner_user and owner_user.voice_sample_url:
                        user_voice_id = owner_user.voice_sample_url
                    db_auth.close()
                except Exception as e:
                    print(f"[AutoDLService] 读取成员专属Voice ID失败: {e}")

            for i, seg in enumerate(segments):
                sid = seg.get("id", i + 1)
                dur = min(15, max(1, int(seg.get("duration", 10))))
                prompt = seg.get("prompt", "")

                # 决定使用正面还是侧面
                img_url = side_url if ("侧身" in prompt or "45度" in prompt or seg.get("role") == "ayong_side" or i == 1) else front_url

                payload = {
                    "prompt": prompt,
                    "duration": dur,
                    "resolution": resolution,
                    "ref_image_0": img_url
                }

                # 优先调用百炼旗舰 Qwen-Audio-3.1 语音大模型，精确合成并测量物理时长
                diag_text = seg.get("dialogue", "")
                if diag_text:
                    with self._lock:
                        job["step"] = f"正在使用 Qwen-Audio-3.1 旗舰大模型合成第{sid}段高品质语音..."
                    user_tone = seg.get("tone_instruction")
                    custom_audio_url, exact_dur = self.generate_voice_via_qwen_audio(diag_text, custom_voice_id=user_voice_id, user_instruction=user_tone)
                    if custom_audio_url:
                        payload["ref_audio_0"] = custom_audio_url
                        # 核心突破：音频时长倒逼视频时长毫秒级对齐，彻底消灭吞字！
                        if exact_dur and exact_dur > 0:
                            payload["duration"] = min(15, max(1, int(round(exact_dur))))
                        print(f"[AutoDLService] ★ 分镜 {sid} 成功接入 Qwen-Audio 高保真语音 (时长: {payload['duration']}s): {custom_audio_url}")

                # 场景图插槽 (ref_image_1)
                scene_fn = seg.get("scene_image")
                if scene_fn:
                    scene_filename = Path(scene_fn).name
                    scene_path = assets_dir / scene_filename
                    if scene_path.exists():
                        scene_url = self.upload_file_to_storage(scene_path)
                        payload["ref_image_1"] = scene_url
                        print(f"[AutoDLService] ★ 分镜 {sid} 精准绑定场景底牌 (R2): {scene_filename} (URL: {scene_url})")
                    else:
                        print(f"[AutoDLService] ⚠️ 分镜 {sid} 指定的场景图未在本地找到: {scene_path}")

                print(f"[AutoDLService] 提交分镜 {sid} [时长={dur}s, 人物={'45度侧身' if '侧身' in prompt or seg.get('role') == 'ayong_side' else '正面'}, 场景={'已挂载R2' if 'ref_image_1' in payload else '纯提示词'}]")
                task_id = self.submit_autodl_workflow(wf_id, payload)
                with self._lock:
                    job["segments"][i]["task_id"] = task_id
                    job["segments"][i]["status"] = "RUNNING"

            # 步骤 3: 异步轮询等待所有段落完成 (间隔设为20秒，杜绝公网长连接频繁重试断开)
            poll_interval = max(20, self.config.get("video", {}).get("poll_interval_sec", 20))
            timeout_sec = self.config.get("video", {}).get("timeout_sec", 1800)
            t_start = time.time()

            temp_dir = settings.BASE_DIR / "static" / "videos" / "temp" / job_id
            temp_dir.mkdir(parents=True, exist_ok=True)
            completed_segments = [None] * len(segments)

            while time.time() - t_start < timeout_sec:
                all_done = True
                success_count = 0

                for i, seg in enumerate(segments):
                    if completed_segments[i] is not None:
                        success_count += 1
                        continue

                    tid = job["segments"][i]["task_id"]
                    status_info = self.query_task_status(tid)
                    st = status_info.get("status")

                    if st in ("SUCCESS", "completed", "success"):
                        v_url = status_info.get("video_url")
                        if not v_url:
                            raise RuntimeError(f"段{i+1} 状态成功但未返回成片直链")
                        # 下载该段视频 (带3次重试)
                        local_seg_path = temp_dir / f"seg_{i+1}.mp4"
                        for dl_attempt in range(1, 4):
                            try:
                                r_vid = requests.get(v_url, timeout=180)
                                local_seg_path.write_bytes(r_vid.content)
                                break
                            except Exception as dl_err:
                                if dl_attempt == 3:
                                    raise RuntimeError(f"下载段{i+1}成片失败: {dl_err}")
                                time.sleep(2.0)

                        completed_segments[i] = local_seg_path
                        with self._lock:
                            job["segments"][i]["status"] = "SUCCESS"
                            job["segments"][i]["video_url"] = v_url
                            job["segments"][i]["local_file"] = str(local_seg_path)
                        success_count += 1

                    elif st in ("FAILED", "failed"):
                        raise RuntimeError(f"段{i+1} 云端渲染失败: {status_info}")
                    elif st in ("QUERY_RETRY", "UNKNOWN"):
                        # 单次网络抖动或未就绪，记录日志后跳过，绝不中止任务
                        print(f"[AutoDLService] 段{i+1} 轮询网络瞬时波动，跳过本轮等待重试: {status_info.get('error')}")
                        all_done = False
                    else:
                        all_done = False

                # 动态计算综合进度 (25% ~ 90%)
                progress = int(25 + (success_count / len(segments)) * 65)
                elapsed = int(time.time() - t_start)
                with self._lock:
                    job["progress"] = progress
                    job["step"] = f"云端渲染中 ({success_count}/{len(segments)} 段已完成，已耗时 {elapsed}s)..."

                if all_done:
                    break

                time.sleep(poll_interval)

            if not all_done:
                raise TimeoutError("云端算力渲染等待超时！")

            # 步骤 4: FFmpeg 0 帧硬切极速合成
            with self._lock:
                job["step"] = "全部段落渲染完成，正在执行 0 帧硬切无损合成..."
                job["progress"] = 95

            output_filename = f"30秒川渝光伏_阿勇成片_云端_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            assemble_res = concat_videos_ffmpeg(completed_segments, output_filename)

            # 更新数据库
            self._update_db_success(job["project_id"], assemble_res["local_url"])

            with self._lock:
                job["status"] = "COMPLETED"
                job["progress"] = 100
                job["step"] = "🎉 云端渲染与 0 帧硬切合成完成！"
                job["video_url"] = assemble_res["local_url"]
                job["download_url"] = assemble_res["download_url"]
                job["filename"] = assemble_res["filename"]
                job["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            with self._lock:
                self.jobs[job_id]["status"] = "FAILED"
                self.jobs[job_id]["error"] = str(e)
                self.jobs[job_id]["step"] = f"出片异常: {e}"

    def _update_db_success(self, project_id: str, video_url: str):
        """持久化成功状态到 SQLite"""
        try:
            db = SessionLocal()
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj:
                proj.status = "COMPLETED"
                proj.final_video_url = video_url
            task = db.query(RenderTask).filter(RenderTask.project_id == project_id).first()
            if task:
                task.status = "SUCCESS"
            db.commit()
            db.close()
        except Exception as e:
            print(f"[AutoDLService] 更新数据库失败: {e}")

# 单例导出
autodl_service = AutoDLService()
