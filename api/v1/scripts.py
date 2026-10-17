import math
import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.scanner import scan_script_text
from core.llm_client import call_llm_chat, get_llm_config

router = APIRouter(prefix="/scripts", tags=["文案生成与机审引擎"])

class ScanRequest(BaseModel):
    script_text: str

class GenerateScriptRequest(BaseModel):
    category: str = Field(..., description="业务线: RESIDENTIAL_CQ 或 COMMERCIAL_SC_CQ")
    hook_id: str = Field(..., description="Hook 标识")
    custom_industry: Optional[str] = "机械与注塑制造"

class AIGenerateScriptRequest(BaseModel):
    category: str
    topic_title: str
    hook_name: str
    pain_point: Optional[str] = ""

class AIGenerateStoryboardRequest(BaseModel):
    script_text: str
    category: str
    protagonist_name: Optional[str] = "阿勇"

# 7 大爆款 Hook 预置文案库 (已剔除所有违禁词，100% PASS 工业级合规标准)
HOOK_TEMPLATES = {
    # ================= 🏡 重庆自建房户用光伏 =================
    "RESIDENTIAL_CQ": [
        {
            "id": "res_investor_full",
            "name": "🏡 资方全资建站",
            "tag": "自建房标配",
            "desc": "打破误解，讲透资方全额出资建站+装好验收领初装费+全额卖电网+年年领租金",
            "script": "老刘，听说你楼顶建正规光伏电站一分钱没掏，装好验收合格还领到初装费了？那是！资方全资建正规光伏电站，装好验收合格发初装费，发出来的电全额卖给电网，每年稳领屋顶租金。我们店就在江津珞璜封成电器，十几年门头都在这，找阿勇，踏踏实实带你建正规电站！",
            "speaker": "老刘 + 阿勇",
            "shots": [
                {
                    "order": 1,
                    "type": "A-Roll",
                    "speaker": "老刘",
                    "duration": 6,
                    "dialogue": "老刘，听说你楼顶建正规光伏电站一分钱没掏，装好验收合格还领到初装费了？",
                    "character_tag": "@老刘",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_重庆农村院坝",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "Rural Chongqing brick house courtyard, Lao Liu standing proudly in the foreground, bright natural daylight, cinematic 50mm lens, subtle breeze."
                },
                {
                    "order": 2,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 9,
                    "dialogue": "那是！资方全额出资建正规电站，装好验收合格发初装费，发电全额卖电网，每年稳领租金。",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_配电箱与逆变器",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Chongqing solar business manager A-Yong, wearing dark navy work vest, calm and confident expression, speaking clearly to camera, single right-hand pressing gesture, daylight."
                },
                {
                    "order": 3,
                    "type": "B-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "我们店就在江津珞璜封成电器，十几年门头都在这，合同白纸黑字无套路。",
                    "character_tag": "@阿勇_45度侧身",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_江津珞璜封成电器门头",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "A-Yong standing by a certified solar panel inverter installation, pointing thumb towards physical hardware, steady camera, crisp daylight."
                },
                {
                    "order": 4,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "找阿勇，踏踏实实带你建正规电站，自建房闲着不如换真金白银！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_电站远景阳光",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Medium close-up of A-Yong smiling calmly, nodding with strong credibility, sunny Chongqing countryside background, eye contact with camera."
                }
            ]
        },
        {
            "id": "res_avoid_loan",
            "name": "⚠️ 避坑认准实体店",
            "tag": "排雷专线",
            "desc": "告诫自建房业主绝不背个人连带债务，以封成电器门头为信用底牌",
            "script": "自建房顶建光伏，千万别签个人债务合同！很多游击队忽悠你，出了事连人都找不到。记住了，资方全资建站，业主零设备款投入，装好验收合格拿初装费，每年稳拿租金。认准江津珞璜封成电器实体店，阿勇随时都在，白纸黑字写进合同才靠谱！",
            "speaker": "阿勇",
            "shots": [
                {
                    "order": 1,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "自建房顶建光伏，千万别签个人债务合同！很多游击队忽悠你，出了事连人都找不到。",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_农村自建房露台",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Medium shot of A-Yong speaking earnestly, firm facial expression, shaking head slightly to warn viewers, Chongqing rural backdrop."
                },
                {
                    "order": 2,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "记住了，资方全资建站，业主零设备款投入，装好验收合格拿初装费，每年稳拿租金。",
                    "character_tag": "@阿勇_45度侧身",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_并网计量电表箱",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "A-Yong gesturing with open right palm, explaining transparent terms, clear eye contact, high dynamic range daylight."
                },
                {
                    "order": 3,
                    "type": "B-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "认准江津珞璜封成电器实体店，阿勇随时都在，白纸黑字写进合同才靠谱！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_封成电器实体店门头",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Cinematic B-Roll showing tidy rural rooftop with high quality solar panels reflecting deep blue sky, smooth crane shot."
                },
                {
                    "order": 4,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "楼顶面积多大？在后台留个信，阿勇当场给你核查电网并网资格！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_阿勇招手留信",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "A-Yong gesturing confidently towards camera, inviting consultation with trustworthy demeanor, 50mm portrait bokeh."
                }
            ]
        }
    ],
    # ================= 🏭 川渝工商业厂房光伏 =================
    "COMMERCIAL_SC_CQ": [
        {
            "id": "com_pain_point",
            "name": "🔥 盛夏白天用电刺客",
            "tag": "痛点共鸣",
            "desc": "直击注塑与机械加工白天峰段电价一块多，纯利润被电表吞食痛点",
            "script": "川渝开厂的老板，上个月电费单出来心口抽搐没有？车间几十台注塑机白天连轴转，电表跑得像陀螺，峰段电价一块多，全是给电费打工！其实大厂房顶闲着就是浪费，资方全额出资建电站，你零设备投入，白天开工用电每度少交两毛多，一年省下十几万纯利！",
            "speaker": "阿勇",
            "shots": [
                {
                    "order": 1,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "川渝开厂的老板，上个月电费单出来心口抽搐没有？车间几十台注塑机白天连轴转！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_工业车间配电房",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Factory workshop entrance, industrial power distribution cabinet in background, A-Yong speaking emphatically, sharp eye contact, 50mm lens."
                },
                {
                    "order": 2,
                    "type": "B-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "电表跑得像陀螺，峰段电价一块多，全是给电费打工！",
                    "character_tag": "@阿勇_45度侧身",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_注塑机数控机床生产",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "Industrial workshop B-Roll, automated CNC machines and injection molding lines operating, crisp factory lighting."
                },
                {
                    "order": 3,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "其实大厂房顶闲着就是浪费，资方全额出资建电站，企业零设备款投入，白天用电每度少交两毛多！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_厂房二楼眺望屋顶",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "A-Yong walking slowly along the industrial factory railing, single-handed downward gesture, composed and authoritative manager stance."
                },
                {
                    "order": 4,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "只要屋顶八百平以上，白天正常开工，把电费单发后台，阿勇帮你算笔明白账！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_阿勇合掌算账",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Close-up portrait of A-Yong, decisive expression, looking straight into lens, professional industrial lighting."
                }
            ]
        },
        {
            "id": "com_anti_cognition",
            "name": "⚡ 掀开资方真实算盘",
            "tag": "反认知辟谣",
            "desc": "拆解为什么资方全额出资不收钱，商业逻辑互利共赢彻底打消疑虑",
            "script": "很多老板第一反应：天下哪有好事，资方凭啥几百万替我建电站？今天阿勇把底牌给你掀开！资方有大笔资金要找优质资产，但他们没有大屋顶，也没有白天用电的工厂；你有厂房有稳定消纳，双方合作，你白天用便宜电降成本，余电卖给电网，这叫互利共赢，哪有套路！",
            "speaker": "阿勇",
            "shots": [
                {
                    "order": 1,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "很多老板第一反应：天下哪有好事，资方凭啥几百万替我建电站？今天阿勇把底牌给你掀开！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_车间入口摆龙门阵",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Medium shot of A-Yong, leaning forward slightly with candid expression, confident smirk, holding an industrial measuring tape, factory backdrop."
                },
                {
                    "order": 2,
                    "type": "B-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "资方有资金要找优质资产，但他们没有大屋顶，也没有白天用电的工厂！",
                    "character_tag": "@阿勇_45度侧身",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_航拍厂区连片光伏屋顶",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "Aerial drone shot of massive steel structure factory rooftop with modern solar grid array glistening under sunlight, vast industrial park."
                },
                {
                    "order": 3,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "你有厂房有稳定白天开工，双方合作，你白天用便宜电降成本，余电卖电网，这叫互利共赢！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_白板画大账互利关系",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "A-Yong standing by a clean conference whiteboard, drawing an arrow, professional business gesture, clear voice articulation."
                },
                {
                    "order": 4,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "无债务无追索，合同白纸黑字！把你的厂房面积发后台，阿勇替你把关！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_特写手势敲合同",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Medium close-up of A-Yong looking straight at camera, resolute and sincere gaze, soft industrial background bokeh."
                }
            ]
        },
        {
            "id": "com_ledger",
            "name": "💰 白板现场算大账",
            "tag": "干货算账",
            "desc": "5000平厂房实打实心算，每度电省两毛多，一年省出十几万真金白银",
            "script": "五千平米的川渝厂房，装光伏到底能省多少？今天阿勇拿笔给你心算：五千平装五百千瓦，一年发电五十多万度。供电局白天一块多，资方合作电价打八折，一度电少交两毛多！一年轻轻松松多挤出十到十二万纯利润，相当于多养两台机床！白纸黑字无债务包袱，这笔账还不划算？",
            "speaker": "阿勇",
            "shots": [
                {
                    "order": 1,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "五千平米的川渝厂房，装光伏到底能省多少？今天阿勇拿笔给你心算！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_白板前拿黑记号笔",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "A-Yong holding a black marker pen beside an industrial calculation board, energetic expression, direct eye contact with camera."
                },
                {
                    "order": 2,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "五千平装五百千瓦，一年发电五十多万度，供电局白天一块多，资方合作电价每度少交两毛多！",
                    "character_tag": "@阿勇_45度侧身",
                    "character_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "scene_tag": "@场景_电费单对账特写",
                    "scene_img": "/static/assets/主角_本人业务经理_45度侧身.jpg",
                    "prompt": "A-Yong writing clearly on board '0.80元 vs 1.05元', camera tracking in smoothly, focused and confident presentation."
                },
                {
                    "order": 3,
                    "type": "B-Roll",
                    "speaker": "阿勇",
                    "duration": 8,
                    "dialogue": "一年轻轻松松多挤出十到十二万纯利润，相当于多养两台机床！白纸黑字无债务包袱。",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_数控机床车间俯拍",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "High angle shot of spacious industrial workshop with overhead cranes and tidy production equipment, bright ambient light."
                },
                {
                    "order": 4,
                    "type": "A-Roll",
                    "speaker": "阿勇",
                    "duration": 7,
                    "dialogue": "报上你的厂房面积和区县，阿勇十分钟给你出精准测算表！",
                    "character_tag": "@阿勇_正面特写",
                    "character_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "scene_tag": "@场景_正面沉着拍板",
                    "scene_img": "/static/assets/主角_本人业务经理_正面特写.jpg",
                    "prompt": "Close-up of A-Yong tapping marker on board, solid smile, inviting message exchange with high conviction."
                }
            ]
        }
    ]
}

@router.get("/hooks", summary="获取7大开门Hook胶囊与行业预置模板")
def get_hook_templates():
    return {
        "categories": [
            {"id": "RESIDENTIAL_CQ", "name": "🏡 重庆自建房户用", "desc": "资方全资建站 + 验收发初装费 + 稳领租金 + 封成电器信用支点"},
            {"id": "COMMERCIAL_SC_CQ", "name": "🏭 川渝工商业厂房", "desc": "川渝实体制造 + 零投入无初装费无租金 + 白天打折省几十万 + 余电卖电网"}
        ],
        "templates": HOOK_TEMPLATES
    }

@router.post("/scan", summary="文案全量违禁词与排毒机审接口")
def scan_script(req: ScanRequest):
    res = scan_script_text(req.script_text)
    pure_words = len([c for c in req.script_text if c.strip() and c not in "，。！？、；：“”‘’（）《》\n\r\t "])
    estimated_seconds = math.ceil(pure_words / 4.2) + 1.0 if pure_words > 0 else 0
    return {
        "passed": res["passed"],
        "violations_count": res["violations_count"],
        "violations": res["violations"],
        "pure_words_count": pure_words,
        "estimated_duration_sec": estimated_seconds,
        "within_30s_standard": 25 <= estimated_seconds <= 35
    }

@router.post("/generate", summary="生成或装配30秒标准文案与分镜 (模板通道)")
def generate_script(req: GenerateScriptRequest):
    category_templates = HOOK_TEMPLATES.get(req.category, [])
    selected = None
    for t in category_templates:
        if t["id"] == req.hook_id:
            selected = t
            break

    if not selected and category_templates:
        selected = category_templates[0]

    if not selected:
        raise HTTPException(status_code=404, detail="未找到匹配的文案模板")

    scan_res = scan_script_text(selected["script"])
    pure_words = len([c for c in selected["script"] if c.strip() and c not in "，。！？、；：“”‘’（）《》\n\r\t "])
    total_duration = sum(s["duration"] for s in selected["shots"])

    return {
        "title": f"川渝光伏30秒获客-{selected['name']}",
        "category": req.category,
        "hook_id": selected["id"],
        "hook_name": selected["name"],
        "script_text": selected["script"],
        "pure_words": pure_words,
        "total_duration": total_duration,
        "moderation_pass": scan_res["passed"],
        "moderation_report": scan_res,
        "shots": selected["shots"]
    }

@router.post("/ai-generate-script", summary="调用自定义大模型生成大白话口播台词")
async def ai_generate_script(req: AIGenerateScriptRequest):
    """
    根据用户配置的大模型 API，调用生成严格符合 30 秒 (120~135字) 的大白话文案
    """
    system_prompt = (
        "你是一位服务于重庆与四川光伏业务的资深短视频编剧大师，出镜主角叫'阿勇'。\n"
        "【文案绝对铁律】：\n"
        "1. 字字讲大白话！严禁任何行业黑话（严禁'消纳'、'EMC模式'、'固定资产沉淀'、'首先/其次/综上所述'）！\n"
        "2. 严禁任何违禁词（严禁'免费装'、'白送'、'贷款'、'分红'、'稳赚不赔'、'最强'、'唯一'）！\n"
        "3. 户用重庆口径：资方全额出资建正规光伏电站 + 装好验收合格发初装费 + 发电全额卖电网 + 每年稳领租金 + 江津珞璜封成电器实体店底牌；禁用'棚子'，禁用'进门当场领'；\n"
        "4. 工商业川渝口径：白天机床开工用电打折少交两毛多 + 资方全资投入企业零设备款投入 + 余电卖电网；\n"
        "5. 字数硬指标：必须严格控制在 120 字至 135 字之间，朗读时长恰好 30 秒！不要写分镜括号，只输出一段自然连贯的大白话口播台词！"
    )

    user_prompt = (
        f"业务线：{'🏡 重庆自建房户用' if req.category == 'RESIDENTIAL_CQ' else '🏭 川渝工商业厂房'}\n"
        f"选题主题：{req.topic_title}\n"
        f"开门Hook风格：{req.hook_name}\n"
        f"核心痛点/客群：{req.pain_point or '机械注塑制造/自建房闲置楼顶'}\n"
        f"请立即撰写出这段 120~135 字的大白话口播台词："
    )

    try:
        reply = await call_llm_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        clean_text = re.sub(r'["“”]', '', reply).strip()
        # 立即进行违禁词排毒机审
        scan_res = scan_script_text(clean_text)
        pure_words = len([c for c in clean_text if c.strip() and c not in "，。！？、；：“”‘’（）《》\n\r\t "])
        duration = math.ceil(pure_words / 4.2) + 1.0

        return {
            "success": True,
            "script_text": clean_text,
            "pure_words": pure_words,
            "estimated_duration": duration,
            "moderation_pass": scan_res["passed"],
            "moderation_report": scan_res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 生成文案失败: {str(e)}")

@router.post("/ai-generate-storyboard", summary="调用自定义大模型智能拆解4段分镜(@主角与插场景图)")
async def ai_generate_storyboard(req: AIGenerateStoryboardRequest):
    """
    根据口播台词，拆解为 4 段工业级 A/B Roll 分镜头，并自动打上 @主角 与 @场景 标签
    """
    system_prompt = (
        "你是一位工业级短视频导演。请根据输入的 30 秒口播台词，将其智能拆解为刚好 4 个 A/B Roll 分镜头。\n"
        "【拆解规则】：\n"
        "1. 镜号 1: 开门 Hook (A-Roll 或真实人物场景)，时长 5~7 秒；\n"
        "2. 镜号 2: 撕开真相/算账 (A-Roll 阿勇正面中特写 50mm)，时长 7~9 秒；\n"
        "3. 镜号 3: 解法与大账 (B-Roll 车间/电站/配电箱实景)，时长 7~9 秒；\n"
        "4. 镜号 4: 行动号召 (A-Roll 阿勇正面真诚招手)，时长 6~8 秒；\n"
        "5. 必须返回纯 JSON 格式：\n"
        "{\n"
        "  \"shots\": [\n"
        "    {\n"
        "      \"order\": 1,\n"
        "      \"type\": \"A-Roll\",\n"
        "      \"speaker\": \"阿勇\",\n"
        "      \"duration\": 7,\n"
        "      \"dialogue\": \"对应的单句台词\",\n"
        "      \"character_tag\": \"@阿勇_正面特写\",\n"
        "      \"scene_tag\": \"@场景_工业配电房\",\n"
        "      \"prompt\": \"纯英文视觉调度指令，严禁写台词，如 A-Yong speaking earnestly, 50mm portrait lens, industrial bokeh\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        reply = await call_llm_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"台词：{req.script_text}"}
        ], json_mode=True)

        parsed = json.loads(reply)
        shots = parsed.get("shots", [])
        
        # 补全图片资源默认值
        for s in shots:
            if "正面" in s.get("character_tag", ""):
                s["character_img"] = "/static/assets/主角_本人业务经理_正面特写.jpg"
            else:
                s["character_img"] = "/static/assets/主角_本人业务经理_45度侧身.jpg"
            s["scene_img"] = "/static/assets/主角_本人业务经理_正面特写.jpg"

        return {"success": True, "shots": shots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 生成分镜失败: {str(e)}")
