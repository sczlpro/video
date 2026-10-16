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
from core.llm_client import get_llm_config, call_llm_chat
from db.database import get_db
from db.models import Project, Shot, RenderTask, generate_uuid

router = APIRouter(prefix="/render", tags=["算力出片与自动合成"])

class ParsePreviewRequest(BaseModel):
    prompt: str = Field(..., description="豆包分镜文本或表格")
    duration: int = Field(default=30, description="成片目标时长 (秒)")
    image_map: Optional[Dict[str, str]] = Field(default=None, description="场景图片映射字典，如 {'图片1': '/static/assets/scene_xxx.jpg'}")

class RenderSubmitRequest(BaseModel):
    prompt: Optional[str] = Field(default="", description="从豆包复制的分镜提示词、台词或完整JSON计划")
    owner: str = Field(default="songyong", description="所属成员代号: songyong / wufengcheng / yuanyuan")
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

async def enrich_scene_with_llm_if_available(scene_desc: str, action_desc: str, camera_desc: str, dialogue_text: str = "") -> str:
    """
    大模型编译中枢：如果用户配置了任意厂商的大模型API，0.3秒内将中文语义动态编译为好莱坞物理级英文视觉提示词；
    ★ 核心升级：声画绝对对齐（Audio-Visual Semantic Binding），台词讲什么，画面就必须具象展示什么！
    未配置时优雅回退为规则增强器，彻底治本！
    """
    cfg = get_llm_config()
    api_key = cfg.get("api_key", "").strip()

    # 未配置 Key 时回退
    if not api_key:
        return enrich_scene_description(scene_desc)

    system_prompt = (
        "You are the official Hollywood master visual prompt director and chief cinematographer for MiniMax H3 (minimax_h3_zm_u24) in Ref2VA full-reference video mode.\n"
        "The user is an executive who speaks ordinary Chinese and does NOT understand cinema terminology. Your mission is to interpret their brief intent and translate/expand it into a dense, photorealistic, professional English physical visual prompt (65-105 words) complying strictly with H3's official visual grammar.\n"
        "\n"
        "YOU MUST RIGOROUSLY CONSTRUCT THE PROMPT ACROSS THESE 5 SCIENTIFIC CINEMATIC DIMENSIONS:\n"
        "1. KINEMATIC CAMERA MOTION (推/拉/摇/移/俯仰/环绕):\n"
        "   - 推镜头 (Push In): 'The camera pushes in with smooth motion at steady speed toward the subject...'\n"
        "   - 拉镜头 (Pull Out): 'The camera smoothly pulls out backward into an expansive wide overview...'\n"
        "   - 摇镜头 (Pan Left/Right / Tilt Up/Down): 'The camera pans horizontally across the sprawling structure...' or 'The camera tilts up from the concrete floor toward the high steel beams...'\n"
        "   - 移镜头/推轨 (Truck Left/Right / Dolly Track): 'A smooth lateral dolly track moves from left to right, revealing rich spatial parallax...'\n"
        "   - 弧形环绕 (Arc Shot / Orbit): 'The camera glides in a smooth 45-degree arc shot around the subject, rotating the background landscape...'\n"
        "   - 动态跟拍 (Tracking Shot): 'Low-angle handheld tracking shot moves backward with subtle organic physical inertia as the subject advances...'\n"
        "\n"
        "2. ADVANCED LIGHTING & ATMOSPHERE (物理光线与色彩科学):\n"
        "   - Specify the exact illumination: 'Soft diffuse overcast daylight typical of Sichuan-Chongqing valley', 'Subtle directional golden-hour side lighting casting long shadows across corrugated roof ridges', 'Crisp metallic edge highlights (rim lighting) tracing the subject's shoulders and solar panel aluminum brackets'.\n"
        "\n"
        "3. AUDIO-VISUAL SEMANTIC BINDING (声画严密对位):\n"
        "   - If dialogue mentions idle/empty roofs, show massive bare rusted corrugated roof planes without equipment.\n"
        "   - If dialogue mentions high peak electricity bills, prominently display heavy oil-immersed transformers, digital meter cabinets, and high-voltage transmission cables.\n"
        "   - If dialogue mentions solar installations/clean power, showcase vast diagonal grids of gleaming dark-blue monocrystalline panels.\n"
        "\n"
        "4. TANGIBLE PHYSICAL NOUNS & DETAILED ANCHORING:\n"
        "   - Zero generic fluff. Use weathered corrugated steel, galvanized ducts, high-voltage ceramic insulators, cable bridge trays, and misty rolling green mountains in the far distance.\n"
        "\n"
        "5. DYNAMIC SUBJECT PERFORMANCE:\n"
        "   - Walking forward with confident strides, shifting center of gravity, pointing decisively toward the exact background installation, maintaining authoritative investor eye-contact.\n"
        "\n"
        "STRICT OUTPUT REQUIREMENT:\n"
        "Output ONLY the final dense English prompt string. Strictly NO explanations, NO quotes, NO markdown, NO Chinese characters, NO subtitle or audio tags."
    )
    user_input = f"口播台词: {dialogue_text} | 画面场景需求: {scene_desc} | 运镜: {camera_desc} | 动作: {action_desc}"

    try:
        enriched_en = await call_llm_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ])
        clean_res = enriched_en.strip().replace('"', '').replace("'", "")
        if len(clean_res) > 20:
            return clean_res
    except Exception as e:
        print(f"[LLMCompiler] 调用大模型编译提示词失败，降级回退: {e}")

    return enrich_scene_description(scene_desc)

def enrich_scene_description(scene_desc: str) -> str:
    """
    智能场景丰满器：将简短的中文场景转化为具有丰富物理结构、材质纹理与光影层次的工业级电影实景描述。
    彻底解决背景空洞、不直观、缺乏工业说服力的问题。
    """
    s = (scene_desc or "").strip().lower()

    if any(k in s for k in ["屋顶", "彩钢", "老厂房", "车间顶", "通道"]):
        return (
            "Sprawling commercial factory rooftop in Chongqing with authentic industrial texture: "
            "weathered corrugated steel tiles with subtle rust spots, heavy galvanized ventilation conduits, steel safety railings, "
            "and massive air conditioning condenser units; in the far background, rolling lush green mountains of Sichuan-Chongqing under a natural soft overcast cloudy sky, creating deep atmospheric perspective."
        )
    elif any(k in s for k in ["光伏", "太阳能", "电池板", "组件", "阵列"]):
        return (
            "Expansive modern industrial rooftop installation with thousands of dark blue monocrystalline photovoltaic solar panels arranged in clean, geometric diagonal rows; "
            "metallic aluminum mounting brackets gleaming under natural diffuse daylight, cable bridge trays running along the concrete walkways, high-voltage warning markers, "
            "and an expansive open horizon showing industrial cranes and rolling hills in the distant haze."
        )
    elif any(k in s for k in ["变压器", "配电", "电表", "机房", "电房", "变电"]):
        return (
            "Heavy-duty outdoor industrial substation and step-up transformer area beside a manufacturing plant: "
            "massive oil-immersed transformers with ceramic insulator bushings and overhead high-voltage transmission cables, steel cage safety enclosures, industrial digital power meter cabinets, "
            "weathered concrete ground with yellow safety demarcation lines, and the textured corrugated metal wall of the plant in the background."
        )
    elif any(k in s for k in ["车间", "生产线", "厂房内部", "仓库"]):
        return (
            "Spacious high-ceiling industrial manufacturing workshop with soaring steel truss framework: "
            "heavy overhead bridge crane gantry, industrial CNC machinery and automated production conveyors on polished green epoxy flooring, natural soft daylight pouring through clerestory windows, "
            "and stacked manufacturing components creating rich spatial depth."
        )
    else:
        # 通用丰富工业背景
        return (
            f"Authentic Chongqing commercial industrial facility: {scene_desc}, "
            "featuring solid architectural concrete textures, steel beams, heavy industrial piping, and distant misty rolling mountains under authentic diffuse ambient daylight, rich in tangible physical details."
        )

def map_action_to_english(action_desc: str, role: str = "ayong_front", has_scene_image: bool = False) -> str:
    """智能解析动作描述：优先释放用户传入的真实肢体运动与空间位移，无输入时才使用高动态默认值"""
    act = action_desc.strip() if action_desc else ""

    # 如果用户已经传入了英文动作描述（包含 walk / move / point / look / gesture / stand 等），直接尊重用户原生英文！
    if act and any(k in act.lower() for k in ["walk", "step", "stride", "move", "turning", "pointing", "gesturing", "looking", "inspecting"]):
        # 净化其中的中文角色修饰
        clean_act = re.sub(r"@阿勇[^\s]*|@正面|@侧面|@主角[^\s]*", "", act).strip()
        return clean_act

    # 中文动作指令映射为丰富的高动态三维空间动作
    act_l = act.lower()
    if any(k in act_l for k in ["走", "迈步", "前行", "步伐", "巡视", "巡查", "前进", "walk", "step"]):
        return "The subject is actively walking forward with confident and purposeful strides through the setting, shifting body weight naturally, occasionally looking at the surrounding facility before making sharp, authoritative eye contact with the viewer."
    elif any(k in act_l for k in ["侧身", "指向", "侧面", "45度", "point", "indicate"]):
        return "The subject is positioned at a natural dynamic 45-degree angle, gesturing smoothly and decisively toward the machinery and structure in the background, speaking with convincing, relatable investor authority."
    elif any(k in act_l for k in ["看", "观察", "检查", "检修", "抬头", "环视", "look", "inspect"]):
        return "The subject inspects the surrounding environmental equipment with a focused, knowledgeable gaze, turning his body toward the camera to deliver the key spoken points with sincere composure."
    elif any(k in act_l for k in ["前倾", "笃定", "加重", "严肃", "重点", "电表", "痛点", "lean"]):
        return "The subject shifts his upper body slightly forward in a dynamic stance, gesturing decisively with separated open palms to emphasize core commercial benefits, projecting intense personal presence."
    elif any(k in act_l for k in ["摊手", "摊开", "平稳", "算账", "好处", "方案", "open"]):
        return "The subject gestures smoothly with open hands kept naturally separated, his posture relaxed yet commanding, speaking directly and compellingly to the camera."
    else:
        if role == "ayong_side":
            return "The subject turns smoothly in a dynamic three-quarter stance, gesturing toward the surrounding facility with authoritative, persuasive confidence."
        return "The subject delivers the dialogue with lively natural head movements and expressive, separated hand gestures, his posture engaging and authentic."

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

def build_cinematic_h3_prompt(scene_desc: str, action_desc: str, dialogue: str, role: str = "ayong_front", has_scene_image: bool = False, camera_desc: str = "") -> str:
    """
    根据 MiniMax H3 官方规范与 Ref2VA 工业出片标准拼装提示词：
    彻底破除固定站桩与僵硬静态机位枷锁，全面释放三维空间运镜与真实人物肢体动态！
    """
    # 1. 镜头运镜提取与动态释放 (优先尊重用户传入的真实运镜指令，绝不用 static camera 锁死)
    cam_raw = (camera_desc.strip() if camera_desc else "").strip()
    if not cam_raw and scene_desc and any(k in scene_desc.lower() for k in ["camera", "tracking", "drone", "pull", "push", "pan", "handheld"]):
        cam_raw = scene_desc.strip()

    if cam_raw and any(k in cam_raw.lower() for k in ["tracking", "pull", "push", "move", "pan", "orbit", "handheld", "steady", "crane", "drone"]):
        camera_style = f"Cinematic camera motion: {cam_raw}. Realistic physical documentary texture, natural soft overcast daylight, believable Chongqing ambient lighting."
    else:
        # 默认使用具有丰富呼吸感和纵深感的后退手持微震跟拍
        camera_style = "Cinematic dynamic handheld camera with subtle organic movement, camera smoothly tracking backward with realistic focal depth, natural soft daylight, typical Chongqing overcast sky."

    # 2. 景别与构图规则 (鼓励视差与环境流动，彻底删除 strictly NO camera movement / NO push-in 等死锁词)
    if has_scene_image:
        framing_rule = (
            "Spatial framing: The scene takes place within the authentic physical environment established in <Picture 2>. "
            "The camera maintains a natural documentary distance, framing the subject in relation to the expansive machinery, roof surfaces, or structural setting of <Picture 2>. "
            "The surrounding equipment, background depth, and architectural lines move naturally with the camera motion, creating rich spatial parallax."
        )
        env_rule = (
            "Environment and physical setting: <Picture 2> defines the exact physical location. "
            "The environmental architecture, concrete textures, steel structures, and overhead sky strictly reflect <Picture 2>."
        )
    else:
        # 当未传入真实场景图时，调用智能场景丰满器，自动生成极度具象、有结构、有纵深的工业场景细节
        env_rich = enrich_scene_description(scene_desc)
        framing_rule = (
            "Spatial framing: The camera frames the subject in a lively environmental composition, giving ample prominence to the sprawling industrial backdrop and architectural depth. "
            "The camera motion reveals rich spatial perspective and dynamic parallax between the subject and the layered background."
        )
        env_rule = f"Environment: {env_rich} Absolutely no storefront signage, no commercial street, no shopfront."

    # 3. 主角与肢体动作 (释放走动、转身、观察、指点等自然动态)
    character_desc = (
        "The subject (<Picture 1>) is an experienced, authoritative businessman dressed in clean professional attire, "
        "weathered yet resolute facial expression, delivering his message with deep, trustworthy credibility."
    )

    action_en = map_action_to_english(action_desc, role, has_scene_image)
    gesture_rule = (
        f"Subject physical action: {action_en} "
        "Hand gesture rule: when gesturing with hands, keep hands naturally separated with comfortable distance wider than shoulders, "
        "gesturing expressively outward or emphasizing points naturally; both hands strictly NEVER clasp together, NEVER hold each other, NEVER form traditional martial arts fist-and-palm salute."
    )

    clean_screen_rule = (
        "CRITICAL, strictly forbidden: NO subtitles, NO captions, NO burnt-in text, "
        "NO on-screen text of any kind, NO lower-third text, NO floating characters, NO written words rendered anywhere in the video image."
    )
    speech_rule = (
        "Voice and speech specification: The subject (S1) speaks in natural, confident, authoritative Mandarin Chinese "
        "with rich prosody, expressive emotional cadence, and measured investor poise. "
        "From 0.0s the mouth opens naturally in sync with speech, lips move accurately matching the dialogue."
    )

    # 4. 环境声场 (严格激活 H3 官方 overall_soundscape 物理声音引擎，赋予真实大风与工业运转低频)
    soundscape_rule = (
        "overall_soundscape: Authentic outdoor factory rooftop ambience with continuous natural wind murmuring through metallic conduits, "
        "distant subtle low-frequency mechanical humming from the industrial plant, and natural footsteps echoing on the surface."
    )

    clean_dialogue = clean_text_for_dialogue(dialogue)
    if clean_dialogue:
        dialogue_tag = f"<d>[Chinese] {clean_dialogue}</d>"
    else:
        dialogue_tag = "<Picture 1> subject speaking."

    return f"{camera_style} {framing_rule} {character_desc} {gesture_rule} {env_rule} {clean_screen_rule} {speech_rule} {dialogue_tag} {soundscape_rule}"

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
            parsed_dur = max(1, min(15, et - st))

            scene = cols[1] if len(cols) > 1 else ""
            camera = cols[2] if len(cols) > 2 else ""
            action = cols[3] if len(cols) > 3 else ""
            dialogue = cols[4] if len(cols) > 4 else ""

            # 若列数较少，台词可能在第2或第3列，做智能匹配
            if not dialogue and len(cols) >= 2:
                candidates = [c for c in cols[1:] if not any(k in c for k in ["特写", "全景", "镜头", "推近", "切场", "横移"])]
                if candidates:
                    dialogue = max(candidates, key=len)

            clean_diag = clean_text_for_dialogue(dialogue)

            # ★ 语速安全自适应兜底：汉字朗读按 3.8字/秒 + 标点停顿估算
            # 如果表格写的秒数明显不够把这段话念完，智能自动扩充到足够秒数（上限15秒），彻底杜绝台词被掐断！
            c_count = len(re.sub(r"[^\u4e00-\u9fa5]", "", clean_diag))
            p_count = len(re.findall(r"[，。！？、；]", clean_diag))
            min_needed_dur = int(round(c_count / 3.8 + p_count * 0.25)) if c_count > 0 else parsed_dur
            dur = max(parsed_dur, min(15, min_needed_dur))

            # 1. 角色智能判断: 严格按 @侧面 / @正面 / @阿勇侧面 / @阿勇正面 精准锁定机位
            if any(k in line for k in ["@侧面", "@阿勇侧面", "@阿勇_侧面", "@阿勇_45度"]):
                ref_role = "ayong_side"
            elif any(k in line for k in ["@正面", "@阿勇正面", "@阿勇_正面"]):
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

            # 拼装工业级电影感提示词 (传入解析出的 camera 运镜指令，全面赋能三维动态)
            clean_diag = clean_text_for_dialogue(dialogue)
            full_prompt = build_cinematic_h3_prompt(scene, action, clean_diag, ref_role, bool(scene_img), camera_desc=camera)

            table_segments.append({
                "id": len(table_segments) + 1,
                "duration": dur,
                "time_range": f"{st}-{et}s",
                "role": ref_role,
                "scene_image": scene_img,
                "scene_label": scene_label,
                "dialogue": clean_diag,
                "prompt": full_prompt,
                "raw_scene": scene,
                "raw_action": action,
                "raw_camera": camera
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
            if any(k in seg_content for k in ["@侧面", "@阿勇侧面", "@阿勇_侧面", "侧身", "45度"]):
                ref_role = "ayong_side"
            elif any(k in seg_content for k in ["@正面", "@阿勇正面", "@阿勇_正面"]):
                ref_role = "ayong_front"
            else:
                ref_role = "ayong_front"

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
        if any(k in p1 for k in ["@侧面", "@阿勇侧面", "侧身", "45度"]):
            ref_role = "ayong_side"
        elif any(k in p1 for k in ["@正面", "@阿勇正面"]):
            ref_role = "ayong_front"
        else:
            ref_role = "ayong_front"

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

async def parse_prompt_to_segments_async(prompt_text: str, total_duration: int = 30, image_map: Optional[Dict[str, str]] = None) -> List[dict]:
    """异步高能解析器：先进行结构化分镜拆解，若配置了任意厂商大模型，0.3秒并发完成语义与物理空间扩写"""
    segs = parse_prompt_to_segments(prompt_text, total_duration, image_map)
    cfg = get_llm_config()
    api_key = cfg.get("api_key", "").strip()

    # 如果配置了大模型，对每个分镜异步进行物理细节丰富
    if api_key and segs:
        for s in segs:
            raw_sc = s.get("raw_scene", "")
            raw_act = s.get("raw_action", "")
            raw_cam = s.get("raw_camera", "")
            diag = s.get("dialogue", "")
            # 若原始场景存在，由大模型深度赋能，实现声画严密对齐
            if raw_sc or raw_act or raw_cam or diag:
                try:
                    llm_env = await enrich_scene_with_llm_if_available(raw_sc, raw_act, raw_cam, dialogue_text=diag)
                    if llm_env and len(llm_env) > 15:
                        s["prompt"] = build_cinematic_h3_prompt(llm_env, raw_act, diag, s.get("role", "ayong_front"), bool(s.get("scene_image")), camera_desc=raw_cam)
                except Exception as e:
                    print(f"[AsyncParse] LLM enrich pass error: {e}")
    return segs

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
def get_render_history(owner: Optional[str] = None, db: Session = Depends(get_db)):
    """
    纯净历史成片库：
    1. 只展示真正已生成、可播放、可下载的【成品母带】；
    2. 支持按成员 owner (songyong/wufengcheng/yuanyuan) 隔离筛选；
    3. 自动过滤掉无成片的中间过程记录与废弃占位，保证列表干脆清爽。
    """
    videos_dir = settings.BASE_DIR / "static" / "videos"
    items = []
    seen_urls = set()

    # 1. 扫描 SQLite 数据库中已有成片母带的项目
    try:
        q = db.query(Project).filter(Project.final_video_url.isnot(None))
        if owner and owner != "all":
            q = q.filter(Project.owner == owner)
        projects = q.order_by(Project.created_at.desc()).all()
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

            # 对应的高清首帧海报 (如果存在，优先使用海报，秒级呈现)
            poster_fn = f"{local_file.stem}_poster.jpg"
            poster_file = videos_dir / poster_fn
            poster_url = f"/static/videos/{poster_fn}" if poster_file.exists() else None

            # 提取台词第一句作为导读摘要
            snippet = p.full_script.strip().split("\n")[0] if p.full_script else ""
            if len(snippet) > 42:
                snippet = snippet[:42] + "..."

            items.append({
                "project_id": p.id,
                "owner": getattr(p, "owner", "songyong") or "songyong",
                "title": timestamp_title,
                "script_snippet": snippet,
                "status": "COMPLETED",
                "filename": filename,
                "video_url": video_url,
                "download_url": video_url,
                "poster_url": poster_url,
                "size_mb": size_mb,
                "has_video": True,
                "mtime": p.created_at.timestamp() if p.created_at else local_file.stat().st_mtime,
                "created_at": time_str
            })
            seen_urls.add(video_url)
    except Exception as e:
        print(f"[RenderHistory] 读取数据库成片异常: {e}")

    # 2. 补漏：扫描 videos 目录下未入库或老版本的成片母带 (当未指定 owner 或看全部时展示)
    if (not owner or owner == "all") and videos_dir.exists():
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
async def parse_preview(req: ParsePreviewRequest):
    segs = await parse_prompt_to_segments_async(req.prompt, req.duration, req.image_map)
    total_dur = sum(s.get("duration", 10) for s in segs)
    return {
        "segments_count": len(segs),
        "total_duration": total_dur,
        "estimated_cost": f"¥{round(total_dur * 0.040, 2):.2f}",
        "segments": segs
    }

@router.post("/submit", summary="★ 提交生成任务并启动后台异步渲染与轮询流水线")
async def submit_render_job(req: RenderSubmitRequest, db: Session = Depends(get_db)):
    """
    接收用户从豆包生成的提示词与分镜配置，触发算力并启动非阻塞轮询，完成后自动 0 帧硬切秒出
    """
    if not req.confirmed:
        raise HTTPException(status_code=400, detail="用户未手动点击确认，算力发牌已被物理拦截！")

    if req.segments and len(req.segments) > 0:
        segments = req.segments
    else:
        segments = await parse_prompt_to_segments_async(req.prompt or "", req.duration, req.image_map)

    total_dur = sum(s.get("duration", 10) for s in segments)
    estimated_cost = round(total_dur * 0.040, 2)

    # 1. 保存到 SQLite 数据库 (使用精确到秒的提交时间戳命名)
    project_id = generate_uuid()
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now_time = datetime.now()
    project_title = f"川渝光伏成片_{now_time.strftime('%Y%m%d_%H%M%S')}"

    project = Project(
        id=project_id,
        owner=req.owner or "songyong",
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
    job_id = autodl_service.create_render_job(project_id, segments, mode=req.mode, owner=req.owner or "songyong")

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

@router.get("/active-jobs", summary="获取当前后台正在运行的渲染任务")
def get_active_render_jobs(owner: Optional[str] = None):
    """
    页面刷新或重新打开时，前端自动调用此接口，恢复当前成员运行中任务的卡片显示与轮询
    """
    return {"jobs": autodl_service.get_active_jobs(owner=owner)}

@router.get("/status/{job_id}", summary="★ 轮询任务实时进度与成片状态")
def query_render_status(job_id: str):
    """
    前端每隔 3~5 秒轮询此接口获取云端分镜进度、AutoDL 实例状态与最终母带直链
    """
    status_info = autodl_service.get_job_status(job_id)
    if not status_info:
        raise HTTPException(status_code=404, detail="未找到该渲染任务")
    return status_info
