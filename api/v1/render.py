import os
import re
import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.config import settings
from core.autodl_service import autodl_service
from db.database import get_db
from db.models import Project, Shot, RenderTask, generate_uuid

router = APIRouter(prefix="/render", tags=["算力出片与自动合成"])

class ParsePreviewRequest(BaseModel):
    prompt: str = Field(..., description="豆包分镜文本或表格")
    duration: int = Field(default=30, description="成片目标时长 (秒)")
    image_map: Optional[Dict[str, str]] = Field(default=None, description="场景图片映射字典，如 {'图片1': '/static/assets/scene_xxx.jpg'}")

class RenderSubmitRequest(BaseModel):
    prompt: Optional[str] = Field(default="", description="从豆包复制的分镜提示词、台词或完整JSON计划")
    segments: Optional[List[dict]] = Field(default=None, description="已定制场景图的分镜列表")
    duration: int = Field(default=30, description="成片目标时长 (秒)")
    mode: str = Field(default="cloud", description="渲染模式: cloud (AutoDL真实算力) / mock (本地极速模拟)")
    confirmed: bool = Field(..., description="★ 用户是否在弹窗手动点击确认 (防抢跑安全锁)")
    image_map: Optional[Dict[str, str]] = Field(default=None, description="场景图片映射字典")

def clean_text_for_dialogue(text: str) -> str:
    """清洗台词文本：剔除角色标签、[图片N]占位符、分镜提示中括号等非发音字符"""
    if not text:
        return ""
    t = text
    # 剔除 @阿勇_正面 / @阿勇侧面 等
    t = re.sub(r"@阿勇[^\s]*", "", t)
    # 剔除 [图片1] / 图片1 / [Picture 1] 等
    t = re.sub(r"(?:\[)?(?:图片|图|Picture|pic)\s*\d+(?:\])?", "", t, flags=re.IGNORECASE)
    # 剔除 【黄金钩子】 / 【段1】 等
    t = re.sub(r"【[^】]*】", "", t)
    # 剔除多余空白与控制符
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t

def map_action_to_english(action_desc: str, role: str = "ayong_front", has_scene_image: bool = False) -> str:
    """智能将中文动作与神态描述映射为工业级 H3 英文肢体指令"""
    if not action_desc:
        if role == "ayong_side":
            return "Ayong turns slightly at a 45-degree angle, gesturing with an open palm toward the background environment, delivering the dialogue with sincere, persuasive empathy."
        return "Ayong speaks calmly and authoritatively, standing steady with hands naturally separated at shoulder width."

    act_l = action_desc.lower()
    if any(k in act_l for k in ["侧身", "指向", "侧面", "45度", "point"]):
        return "Ayong turns his body slightly at a 45-degree angle, gesturing with an open palm toward the surrounding environment, speaking with deep sincerity and relatable empathy."
    elif any(k in act_l for k in ["前倾", "笃定", "加重", "严肃", "重点", "电表", "痛点"]):
        return "Ayong leans slightly forward with a resolute, serious and authoritative investor gaze, using firm downward open-palm gestures to emphasize key spoken words."
    elif any(k in act_l for k in ["摊手", "摊开", "平稳", "算账", "好处", "方案"]):
        return "Ayong speaks with calm authoritative composure, holding hands naturally open and separated at shoulder width, talking directly and sincerely to the camera."
    elif any(k in act_l for k in ["指", "展示", "看", "屋顶", "车间"]):
        return "Ayong gestures smoothly with an open palm toward the background setting, maintaining steady eye contact and compelling conversational presence."
    else:
        return "Ayong speaks with authoritative investor confidence and deep sincerity, gesturing smoothly with separated open palms."

def resolve_scene_image(img_tag_str: Optional[str], image_map: Dict[str, str], fallback_index: int = 0) -> tuple[Optional[str], Optional[str]]:
    """
    根据识别到的图片标签数字或序号，从 image_map 或当前静态资产中精准查找真实场景图
    杜绝匹配到海报或归档文件，返回 (scene_image_filename, scene_label)
    """
    tag_num = None
    if img_tag_str:
        m = re.search(r"\d+", str(img_tag_str))
        if m:
            tag_num = m.group(0)

    # 1. 优先查 image_map
    if tag_num and image_map:
        keys_to_try = [f"图片{tag_num}", tag_num, f"图片 {tag_num}", f"Picture {tag_num}", f"Picture{tag_num}"]
        for k in keys_to_try:
            if k in image_map and image_map[k]:
                fn = Path(image_map[k]).name
                if not fn.startswith("archive_"):
                    return fn, f"图片{tag_num}"

    # 2. 从本地 assets_dir 中只匹配真实的 scene_* 文件 (严格排除 archive_ 前缀海报)
    assets_dir = settings.BASE_DIR / "static" / "assets"
    if assets_dir.exists():
        valid_scenes = sorted(
            [f for f in assets_dir.glob("scene_*.*") if not f.name.startswith("archive_") and f.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]],
            key=lambda p: p.stat().st_mtime
        )
        if valid_scenes:
            target_idx = int(tag_num) - 1 if (tag_num and tag_num.isdigit()) else fallback_index
            if 0 <= target_idx < len(valid_scenes):
                return valid_scenes[target_idx].name, f"图片{target_idx + 1}"
            elif len(valid_scenes) > 0:
                idx = fallback_index % len(valid_scenes)
                return valid_scenes[idx].name, f"图片{idx + 1}"

    return None, None

def build_cinematic_h3_prompt(scene_desc: str, action_desc: str, dialogue: str, role: str = "ayong_front", has_scene_image: bool = False) -> str:
    """
    根据 MiniMax H3 官方规范与 Ref2VA 工业出片标准拼装提示词：
    1. 纯英文原则：除 <d>[Chinese] 对白外，全篇必须纯英文，严禁任何中文修饰语或 [图片N] 标签；
    2. 景别解放（解决人物遮挡背景根因）：使用广角环境大景（Wide environmental framing），让镜头主动后拉，展现大半身/站立全貌；
    3. 场景图优先：明确声明 <Picture 2> 为核心物理环境，建筑结构、宽阔地面、特定设备完全展开，人物自然置于中景；
    4. 纯净画面铁律：严禁生成假字幕、文字排版、水印、悬浮字；
    5. 音画零时差：第0秒起同步自然开口，唇形精准吻合。
    """
    if has_scene_image:
        # 带有场景图时：使用广角中远景镜头，镜头明显拉远，给大场景腾出广阔空间，人物绝不贴脸
        camera_style = "Wide environmental shot, static camera at eye level, shot on phone camera with slight handheld micro-shake, expansive depth of field, realistic documentary texture, natural overcast soft daylight, typical Chongqing cloudy weather."
        framing_rule = (
            "Framing rule, strictly follow: The camera pulls back into an expansive wide-angle view to prioritize the full environment of <Picture 2>. "
            "The subject (<Picture 1>) stands in the mid-ground, showing his upper body down to his waist and hips, occupying only a moderate portion of the frame without dominating it. "
            "The surrounding concrete surfaces, roof edges, machinery, pipes, meters, and distant buildings from <Picture 2> remain widely visible, unobstructed, and prominent. "
            "Keep a constant wide conversational distance, strictly NO push-in, NO zoom, NO close-up, NO camera movement toward the face."
        )
        env_rule = (
            "Environment and physical setting: <Picture 2> defines the exact physical environment. "
            "The background architectural structure, roof surfaces, ground textures, machinery, equipment, lighting, and spatial perspective strictly replicate and follow <Picture 2> with high fidelity."
        )
    else:
        # 当没有上传场景图时：全面释放模型的运镜与空间深度，优先采用传入的英文场景和运镜指令
        env_clean = scene_desc.strip() if scene_desc else "Chongqing high-ceiling industrial factory rooftop and manufacturing workshop surroundings, rolling green hills in far distance"
        # 提取运镜指令（如果scene_desc中包含camera/cinematic/drone等词汇，直接提炼为camera_style）
        if any(k in env_clean.lower() for k in ["drone", "crane", "pull-back", "tracking", "wide-angle", "handheld"]):
            camera_style = f"Cinematic camera work, {env_clean}. Realistic documentary texture, natural overcast soft daylight, typical Chongqing cloudy weather."
            env_rule = f"Environment: {env_clean}. Absolutely no shopfront, no storefront signage, no commercial street."
        else:
            camera_style = "Medium-wide shot, static camera at eye level, shot on phone camera with slight handheld micro-shake, realistic documentary texture, natural overcast soft daylight, typical Chongqing cloudy weather."
            env_rule = f"Environment: {env_clean}. Typical Chongqing county town industrial surroundings, rolling green hills in far distance. Absolutely no shopfront, no storefront signage, no commercial street."

        framing_rule = (
            "Framing rule, strictly follow: the subject is placed naturally in the center area, framed from the waist up in a medium-wide shot; "
            "his whole head, face and shoulders remain completely inside the frame with comfortable margins on all sides; "
            "the camera maintains a stable and expansive perspective, strictly NO push-in, NO zoom, NO close-up, NO moving toward the face."
        )

    character_desc = (
        "The middle-aged Chinese businessman Ayong in a clean dark polo shirt (<Picture 1>), weathered and experienced face, "
        "calm and resolute expression, jaw relaxed and lips naturally closed when not speaking, strictly NO exaggerated smiling or laughing, "
        "demonstrating deep, sincere, and authoritative investor presence."
    )

    action_en = map_action_to_english(action_desc, role, has_scene_image)
    gesture_rule = (
        f"Subject action: {action_en} "
        "Hand gesture rule: when gesturing with hands, keep hands naturally separated with comfortable distance wider than shoulders, "
        "open palms pressing downward firmly or gesturing outward; both hands strictly NEVER clasp together, NEVER hold each other, NEVER form traditional martial arts fist-and-palm salute."
    )

    clean_screen_rule = (
        "CRITICAL, strictly forbidden: NO subtitles, NO captions, NO burnt-in text, "
        "NO on-screen text of any kind, NO lower-third text, NO floating characters, NO written words rendered anywhere in the video image."
    )
    speech_rule = (
        "Voice and dialect specification: The subject (S1) speaks throughout in authentic, colloquial Chongqing dialect (Sichuanese / Southwestern Mandarin), "
        "with strong local Chongqing accent, natural tone sandhi, and heavy local cadence, strictly NO standard Mandarin pronunciation. "
        "From 0.0s the mouth opens naturally in sync with speech, lips move accurately matching the dialogue."
    )

    # 清洗台词中可能遗留的标签
    clean_dialogue = clean_text_for_dialogue(dialogue)
    if clean_dialogue:
        dialogue_tag = f"<d>[Chinese] {clean_dialogue}</d>"
    else:
        dialogue_tag = "<Picture 1> subject speaking."

    return f"{camera_style} {framing_rule} {character_desc} {gesture_rule} {env_rule} {clean_screen_rule} {speech_rule} {dialogue_tag}"

def parse_prompt_to_segments(prompt_text: str, total_duration: int = 30, image_map: Optional[Dict[str, str]] = None) -> List[dict]:
    """
    智能解析输入文本：
    1. 支持纯 JSON 计划；
    2. 支持豆包 TSV/Markdown 分镜表格（自动解析 0-4s, 4-10s 等单段秒数与场景台词）；
    3. 支持在文案或提示词末尾插入 [图片1] / 图片1 / Picture 1 并精准关联场景图片；
    4. 支持识别 @阿勇正面 / @阿勇侧面 角色出镜指定；
    5. 支持【段1】/【段2】等段落标记文本；
    6. 兜底按字数与节拍自适应拆分，并继承挂载场景图。
    """
    prompt_clean = prompt_text.strip()
    image_map = image_map or {}

    # 正则：识别各种形式的图片标签 [图片1], 图片1, Picture 1, pic2 等
    img_tag_regex = re.compile(r"(?:\[)?(?:图片|图|Picture|pic)\s*(\d+)(?:\])?", re.IGNORECASE)

    # 1. 尝试直接作为 JSON 解析
    try:
        data = json.loads(prompt_clean)
        if isinstance(data, dict) and "segments" in data:
            segs = data["segments"]
            for i, s in enumerate(segs):
                if not s.get("scene_image"):
                    # 检查 prompt 内是否有 [图片N]
                    p_text = s.get("prompt", "")
                    m_tag = img_tag_regex.search(p_text)
                    if m_tag:
                        s_img, s_lbl = resolve_scene_image(m_tag.group(1), image_map, i)
                        s["scene_image"] = s_img
                        s["scene_label"] = s_lbl
            return segs
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 2. 尝试作为豆包分镜表格解析 (制表符分隔或 Markdown | 表格)
    lines = [l.strip() for l in prompt_clean.split("\n") if l.strip()]
    table_segments = []
    
    # 遍历每行寻找类似 "0-4s" 或 "0-4秒" 的时间特征
    time_row_pattern = r"(\d+)\s*[-~至到]\s*(\d+)\s*(?:s|秒)?"
    for idx, line in enumerate(lines):
        # 排除纯表头行
        if idx == 0 and ("时间" in line or "画面场景" in line or "口播台词" in line):
            continue

        # 尝试按制表符或 | 分隔列
        if "\t" in line:
            cols = [c.strip() for c in line.split("\t") if c.strip()]
        elif "|" in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
        else:
            cols = [c.strip() for c in re.split(r"\s{2,}", line) if c.strip()]

        if not cols:
            continue

        # 第一列通常为时间，如 "0-4s【黄金钩子】" 或 "@阿勇 0-4s"
        first_col = cols[0]
        time_match = re.search(time_row_pattern, first_col)
        if time_match:
            st = int(time_match.group(1))
            et = int(time_match.group(2))
            dur = max(1, min(15, et - st))

            scene = cols[1] if len(cols) > 1 else ""
            camera = cols[2] if len(cols) > 2 else ""
            action = cols[3] if len(cols) > 3 else ""
            dialogue = cols[4] if len(cols) > 4 else ""

            # 若列数较少，台词可能在第2或第3列，做智能匹配
            if not dialogue and len(cols) >= 2:
                candidates = [c for c in cols[1:] if not any(k in c for k in ["特写", "全景", "镜头", "推近", "切场", "横移"])]
                if candidates:
                    dialogue = max(candidates, key=len)

            # 1. 角色智能判断: 优先遵循 @阿勇正面 或 @阿勇侧面，其次根据动作判断
            if "@阿勇侧面" in line or "@阿勇_侧面" in line or "@阿勇_45度" in line:
                ref_role = "ayong_side"
            elif "@阿勇正面" in line or "@阿勇_正面" in line:
                ref_role = "ayong_front"
            else:
                is_side = ("侧身" in action or "侧面" in action or "指向" in action or "45度" in action)
                ref_role = "ayong_side" if is_side else "ayong_front"

            # 2. 场景图片智能关联 (从整行中精准匹配 [图片1] / 图片2 等)
            scene_img = None
            scene_label = None
            img_tag_match = img_tag_regex.search(line)
            if img_tag_match:
                tag_num = img_tag_match.group(1)
                scene_img, scene_label = resolve_scene_image(tag_num, image_map, len(table_segments))
            elif "@场景_" in line:
                m_scene = re.search(r"@场景_([^\s]+)", line)
                if m_scene:
                    scene_img = f"场景_{m_scene.group(1)}.jpg"
                    scene_label = f"场景_{m_scene.group(1)}"

            # 拼装工业级电影感提示词
            clean_diag = clean_text_for_dialogue(dialogue)
            full_prompt = build_cinematic_h3_prompt(scene, action, clean_diag, ref_role, bool(scene_img))

            table_segments.append({
                "id": len(table_segments) + 1,
                "duration": dur,
                "time_range": f"{st}-{et}s",
                "role": ref_role,
                "scene_image": scene_img,
                "scene_label": scene_label,
                "dialogue": clean_diag,
                "prompt": full_prompt
            })

    if table_segments:
        return table_segments

    # 3. 尝试按【段1】/【段2】等标记拆解
    pattern = r"(?:【|\b)(?:段|Segment|第)\s*([0-9一二三四1-4])\s*(?:段|】|:|\.)"
    splits = re.split(pattern, prompt_clean)
    if len(splits) > 2:
        segments = []
        for i in range(1, len(splits), 2):
            seg_content = splits[i + 1].strip()
            dur_match = re.search(r"(\d+)\s*(?:秒|s|sec)", seg_content, re.IGNORECASE)
            dur = int(dur_match.group(1)) if dur_match else (10 if total_duration == 30 else 15)
            dur = min(15, max(1, dur))

            # 角色
            ref_role = "ayong_side" if any(k in seg_content for k in ["侧身", "45度", "侧面"]) else "ayong_front"

            # 场景图
            scene_img, scene_label = None, None
            img_tag_match = img_tag_regex.search(seg_content)
            if img_tag_match:
                scene_img, scene_label = resolve_scene_image(img_tag_match.group(1), image_map, len(segments))
            else:
                # 尝试按段落序号顺序绑定场景图
                scene_img, scene_label = resolve_scene_image(str(len(segments) + 1), image_map, len(segments))

            clean_diag = clean_text_for_dialogue(seg_content)
            full_prompt = build_cinematic_h3_prompt("", "", clean_diag, ref_role, bool(scene_img))

            segments.append({
                "id": len(segments) + 1,
                "duration": dur,
                "role": ref_role,
                "scene_image": scene_img,
                "scene_label": scene_label,
                "dialogue": clean_diag,
                "prompt": full_prompt
            })
        if segments:
            return segments

    # 4. 兜底回退：若为普通台词或提示词，按标准时长自适应切分
    raw_lines = [l.strip() for l in prompt_clean.split("\n") if l.strip()]
    segments = []

    if total_duration <= 15:
        p1 = prompt_clean
        dur = total_duration
        ref_role = "ayong_side" if any(k in p1 for k in ["侧身", "45度", "侧面"]) else "ayong_front"
        img_match = img_tag_regex.search(p1)
        scene_img, scene_label = resolve_scene_image(img_match.group(1) if img_match else "1", image_map, 0)
        clean_diag = clean_text_for_dialogue(p1)
        full_p = build_cinematic_h3_prompt("", "", clean_diag, ref_role, bool(scene_img))
        return [{
            "id": 1,
            "duration": dur,
            "role": ref_role,
            "scene_image": scene_img,
            "scene_label": scene_label,
            "dialogue": clean_diag,
            "prompt": full_p
        }]

    # 默认 30 秒 3 段黄金节拍 (5s + 15s + 10s)
    if len(raw_lines) == 1:
        chunk_lines = raw_lines
        durations = [total_duration]
    elif len(raw_lines) == 2:
        chunk_lines = raw_lines
        durations = [min(15, total_duration // 2), min(15, total_duration - total_duration // 2)]
    elif len(raw_lines) >= 3:
        chunk_lines = [raw_lines[0], raw_lines[1], "\n".join(raw_lines[2:])]
        durations = [5, 15, 10] if total_duration == 30 else [15, 15, 15]
    else:
        chunk_lines = [prompt_clean]
        durations = [total_duration]

    for idx, (txt, d) in enumerate(zip(chunk_lines, durations)):
        ref_role = "ayong_side" if (idx == 1 or any(k in txt for k in ["侧身", "45度", "侧面"])) else "ayong_front"
        img_match = img_tag_regex.search(txt)
        tag_str = img_match.group(1) if img_match else str(idx + 1)
        scene_img, scene_label = resolve_scene_image(tag_str, image_map, idx)
        clean_diag = clean_text_for_dialogue(txt)
        full_p = build_cinematic_h3_prompt("", "", clean_diag, ref_role, bool(scene_img))
        segments.append({
            "id": idx + 1,
            "duration": d,
            "role": ref_role,
            "scene_image": scene_img,
            "scene_label": scene_label,
            "dialogue": clean_diag,
            "prompt": full_p
        })

    return segments

@router.get("/latest", summary="获取最新一条已合成的成品母带")
def get_latest_master_video():
    """获取最新成片，供前端播放器初始加载和随时下载"""
    videos_dir = settings.BASE_DIR / "static" / "videos"
    if not videos_dir.exists():
        return {"has_video": False}

    mp4_files = sorted(
        [f for f in videos_dir.glob("*.mp4") if not f.name.startswith("concat_")],
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )

    if mp4_files:
        latest = mp4_files[0]
        return {
            "has_video": True,
            "filename": latest.name,
            "video_url": f"/static/videos/{latest.name}",
            "download_url": f"/static/videos/{latest.name}",
            "size_mb": round(latest.stat().st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        }

    return {"has_video": False}

@router.get("/history", summary="获取所有历史成片母带列表")
def get_render_history(db: Session = Depends(get_db)):
    """
    纯净历史成片库：
    1. 只展示真正已生成、可播放、可下载的【成品母带】；
    2. 自动过滤掉无成片的中间过程记录与废弃占位，保证列表干脆清爽；
    3. 名称强制统一为【提交成功时间戳】，杜绝重复雷同名字。
    """
    videos_dir = settings.BASE_DIR / "static" / "videos"
    items = []
    seen_urls = set()

    # 1. 扫描 SQLite 数据库中已有成片母带的项目
    try:
        projects = db.query(Project).filter(Project.final_video_url.isnot(None)).order_by(Project.created_at.desc()).all()
        for p in projects:
            video_url = p.final_video_url
            if not video_url or video_url in seen_urls:
                continue

            filename = Path(video_url).name
            local_file = videos_dir / filename
            if not local_file.exists():
                continue

            size_mb = round(local_file.stat().st_size / (1024 * 1024), 2)
            time_str = p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else ""
            timestamp_title = f"川渝光伏成片_{p.created_at.strftime('%Y%m%d_%H%M%S')}" if p.created_at else filename.replace(".mp4", "")

            # 提取台词第一句作为导读摘要
            snippet = p.full_script.strip().split("\n")[0] if p.full_script else ""
            if len(snippet) > 42:
                snippet = snippet[:42] + "..."

            items.append({
                "project_id": p.id,
                "title": timestamp_title,
                "script_snippet": snippet,
                "status": "COMPLETED",
                "filename": filename,
                "video_url": video_url,
                "download_url": video_url,
                "size_mb": size_mb,
                "has_video": True,
                "mtime": p.created_at.timestamp() if p.created_at else local_file.stat().st_mtime,
                "created_at": time_str
            })
            seen_urls.add(video_url)
    except Exception as e:
        print(f"[RenderHistory] 读取数据库成片异常: {e}")

    # 2. 补漏：扫描 videos 目录下未入库或老版本的成片母带
    if videos_dir.exists():
        for f in videos_dir.glob("*.mp4"):
            if f.name.startswith("concat_"):
                continue
            v_url = f"/static/videos/{f.name}"
            if v_url in seen_urls:
                continue

            st = f.stat()
            file_time = datetime.fromtimestamp(st.st_mtime)
            timestamp_title = f"成片_{file_time.strftime('%Y%m%d_%H%M%S')}"

            items.append({
                "project_id": None,
                "title": timestamp_title,
                "script_snippet": f.name.replace(".mp4", ""),
                "status": "COMPLETED",
                "filename": f.name,
                "video_url": v_url,
                "download_url": v_url,
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "has_video": True,
                "mtime": st.st_mtime,
                "created_at": file_time.strftime("%Y-%m-%d %H:%M:%S")
            })
            seen_urls.add(v_url)

    # 3. 按提交生成时间绝对倒序（最新成片永远排在第1位）
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"total": len(items), "items": items}

@router.delete("/history/{record_id}", summary="删除一条历史任务与视频记录")
def delete_history_record(record_id: str, db: Session = Depends(get_db)):
    """
    物理删除指定的历史记录：
    1. 如果是 project_id，删除数据库关联的项目与渲染任务；
    2. 如果关联有物理 .mp4 视频文件，从 static/videos 目录中一并安全移除。
    """
    deleted_db = False
    deleted_file = False

    # 1. 尝试从数据库 projects 中删除
    proj = db.query(Project).filter(Project.id == record_id).first()
    if proj:
        # 如果有物理视频文件，尝试删除物理文件
        if proj.final_video_url:
            fn = Path(proj.final_video_url).name
            fp = settings.BASE_DIR / "static" / "videos" / fn
            if fp.exists():
                try:
                    fp.unlink()
                    deleted_file = True
                except Exception as e:
                    print(f"[DeleteHistory] 删除视频文件失败: {e}")

        db.delete(proj)
        db.commit()
        deleted_db = True

    # 2. 如果不是 project_id，可能是文件名或文件名 stem
    if not deleted_db:
        videos_dir = settings.BASE_DIR / "static" / "videos"
        target_fn = record_id if record_id.endswith(".mp4") else f"{record_id}.mp4"
        fp = videos_dir / target_fn
        if fp.exists():
            try:
                fp.unlink()
                deleted_file = True
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"删除磁盘文件失败: {e}")

    if not deleted_db and not deleted_file:
        raise HTTPException(status_code=404, detail="未找到要删除的记录或文件")

    return {
        "success": True,
        "message": "🗑️ 历史任务记录及成片文件已成功删除！",
        "deleted_db": deleted_db,
        "deleted_file": deleted_file
    }


@router.get("/template-plan", summary="获取已实测固化的30秒户用自建房接龙计划模板")
def get_template_plan():
    """直接返回本地验证过的30秒自建房三段接龙计划JSON"""
    plan_path = Path(r"C:\Users\Administrator\Desktop\短视频工作流\出片计划\30秒户用自建房_阿勇_接龙计划.json")
    if plan_path.exists():
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            pass
    return {
        "name": "30秒户用自建房_阿勇",
        "segments": [
            {
                "id": 1,
                "duration": 5,
                "prompt": "Medium shot, static camera at eye level, shot on a phone camera in vertical 9:16. Chongqing county town rooftop. Stocky rural man stands on flat concrete roof. <d>[Chinese] 我家楼顶空起好几年了，听说装光伏能倒给钱？</d>"
            },
            {
                "id": 2,
                "duration": 15,
                "prompt": "Medium close shot, static camera at eye level, one continuous take, vertical 9:16. Chongqing rural house empty concrete roof. Same man gestures to the bare roof. <d>[Chinese] 倒给钱，不假。大厂租你楼顶建光伏发电站，建站钱一分不要你出。莫不是骗人的？不得喊你贷款，也不担风险。验收合格，初装费当场点给你，每年还有租金。</d>"
            },
            {
                "id": 3,
                "duration": 10,
                "prompt": "Wide shot then medium shot in one continuous take, vertical 9:16. Empty concrete roof surface. Same middle-aged man speaking calmly. <d>[Chinese] 当真白得这么多？楼顶白得个光伏发电站，隔热防雨，一年也能给你算出一两千。评论区留个面积，我给你算。</d>"
            }
        ]
    }

@router.post("/parse-preview", summary="预览并解析豆包分镜表格为独立镜头卡片")
def parse_preview(req: ParsePreviewRequest):
    segs = parse_prompt_to_segments(req.prompt, req.duration, req.image_map)
    total_dur = sum(s.get("duration", 10) for s in segs)
    return {
        "segments_count": len(segs),
        "total_duration": total_dur,
        "estimated_cost": f"¥{round(total_dur * 0.040, 2):.2f}",
        "segments": segs
    }

@router.post("/submit", summary="★ 提交生成任务并启动后台异步渲染与轮询流水线")
def submit_render_job(req: RenderSubmitRequest, db: Session = Depends(get_db)):
    """
    接收用户从豆包生成的提示词与分镜配置，触发算力并启动非阻塞轮询，完成后自动 0 帧硬切秒出
    """
    if not req.confirmed:
        raise HTTPException(status_code=400, detail="用户未手动点击确认，算力发牌已被物理拦截！")

    if req.segments and len(req.segments) > 0:
        segments = req.segments
    else:
        segments = parse_prompt_to_segments(req.prompt or "", req.duration, req.image_map)

    total_dur = sum(s.get("duration", 10) for s in segments)
    estimated_cost = round(total_dur * 0.040, 2)

    # 1. 保存到 SQLite 数据库 (使用精确到秒的提交时间戳命名)
    project_id = generate_uuid()
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now_time = datetime.now()
    project_title = f"川渝光伏成片_{now_time.strftime('%Y%m%d_%H%M%S')}"

    project = Project(
        id=project_id,
        title=project_title,
        category="COMMERCIAL_SC_CQ",
        full_script=req.prompt[:500],
        total_words=len(req.prompt),
        moderation_pass=True,
        status="RENDERING",
        created_at=now_time
    )
    db.add(project)

    render_task = RenderTask(
        id=generate_uuid(),
        project_id=project.id,
        task_id=task_id,
        workflow_id="minimax_h3_zm_u24",
        cost_estimated=estimated_cost,
        user_confirmed=True,
        confirmed_at=now_time,
        status="RUNNING",
        created_at=now_time
    )
    db.add(render_task)
    db.commit()

    # 2. 调度到 AutoDL 后台 Worker 并返回 job_id
    job_id = autodl_service.create_render_job(project_id, segments, mode=req.mode)

    return {
        "success": True,
        "message": "🚀 算力任务已成功提交！后台已进入并发渲染与自动轮询流水线！",
        "job_id": job_id,
        "project_id": project_id,
        "segments_count": len(segments),
        "total_duration": total_dur,
        "estimated_cost": f"¥{estimated_cost:.2f}",
        "mode": req.mode
    }

@router.get("/active-jobs", summary="获取当前后台所有正在运行的渲染任务")
def get_active_render_jobs():
    """
    页面刷新或重新打开时，前端自动调用此接口，恢复所有运行中任务的卡片显示与轮询
    """
    return {"jobs": autodl_service.get_active_jobs()}

@router.get("/status/{job_id}", summary="★ 轮询任务实时进度与成片状态")
def query_render_status(job_id: str):
    """
    前端每隔 3~5 秒轮询此接口获取云端分镜进度、AutoDL 实例状态与最终母带直链
    """
    status_info = autodl_service.get_job_status(job_id)
    if not status_info:
        raise HTTPException(status_code=404, detail="未找到该渲染任务")
    return status_info
