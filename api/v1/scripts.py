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

class QuickAIWriterRequest(BaseModel):
    category: str = Field(..., description="业务线: RESIDENTIAL_CQ (重庆自建房) 或 COMMERCIAL_SC_CQ (川渝工商业)")
    style: str = Field(default="pain_point", description="风格: pain_point (痛点共鸣), case_story (闪电案例), value_dry (干货拆招), anti_scam (撕套路反认知), trust_cta (10年老店强号召)")

def load_local_knowledge_base() -> str:
    """动态自适应读取知识库：本地桌面优先，云端容器回退内置目录"""
    combined_knowledge = []

    # 智能探测知识库根目录
    desktop_wenan = Path(r"C:\Users\Administrator\Desktop\文案")
    bundled_wenan = Path(__file__).resolve().parent.parent.parent / "knowledge_base"

    target_root = desktop_wenan if desktop_wenan.exists() else bundled_wenan

    # 1. 读取文案工作区最高军规 (AGENTS.md)
    agents_file = target_root / "AGENTS.md"
    if agents_file.exists():
        try:
            combined_knowledge.append(f"=== 文案工作区总指挥军规 (AGENTS.md) ===\n{agents_file.read_text(encoding='utf-8')}\n")
        except Exception:
            pass

    # 2. 读取知识库子目录下所有专业资料（电价、真实案例、套路避坑、异议金句）
    kb_dir = target_root / "知识库"
    if kb_dir.exists():
        for md_file in sorted(kb_dir.glob("*.md")):
            try:
                content = md_file.read_text(encoding="utf-8")
                combined_knowledge.append(f"=== 专业知识库板块: {md_file.name} ===\n{content}\n")
            except Exception:
                pass

    return "\n".join(combined_knowledge)

@router.post("/generate-quick-ai-script", summary="★ 网页端一键调用DeepSeek与本地知识库，秒出15秒80字实战分镜表格")
async def generate_quick_ai_script(req: QuickAIWriterRequest):
    """
    网页端一键生成：
    1. 动态注入桌面【文案/知识库/】下的真实数据（重庆分时电价、初装费、租金、真实案例、防骗秘籍）；
    2. 严格遵循 15 秒总长、75~88 字物理死线；
    3. 直接输出 100% 纯中文 TSV 表格，一键填入输入框，直接点击即可生成！
    """
    kb_text = load_local_knowledge_base()

    system_prompt = (
        "你现在是重庆江津珞璜【封成电器】（实体店开业整整10年）的店主老陈/宋勇本人兼首席金牌编剧！\n"
        "你的任务是为你的短视频账号写一条极具杀伤力、完播率极高的15秒获客口播分镜脚本。\n\n"
        "【必须调用的本地真实事实与知识库数据（严禁虚构乱编）】：\n"
        f"{kb_text}\n\n"
        "【四大不可逾越的死线铁律】：\n"
        "1. 总时长与字数绝对控制：\n"
        "   - 所有分镜时长相加必须【严格等于 15 秒】（如 0-4s + 4-11s + 11-15s，或 0-5s + 5-11s + 11-15s）；\n"
        "   - 所有镜头的口播台词总汉字数必须【严格卡死在 75 ~ 86 字之间】（按4字/秒计算），绝对不能超过88字，多一个字视频模型就念不完！\n"
        "2. 业务边界严格区分：\n"
        "   - 若是重庆自建房户用：发出来的电全额上网业主不能自己用！核心利益必须是【初装补贴费打卡 + 每年固定屋顶租金 + 免费白得一个高大隔热防雨阳光棚露台】！\n"
        "   - 若是川渝工商业大厂房：屋顶绝对没有租金！核心利益必须是【资方全资建站零垫资零负债，白天高负荷生产直接用折上折便宜电，一度电省两三毛，一年省出大几万乃至十来万真金白银】！\n"
        "3. 实体店信誉背书与高转化号召（铁律）：\n"
        "   - 绝不在视频里邀约用户来店里坐（门槛太高没人跑）；\n"
        "   - 必须在结尾顺嘴提一嘴【我在珞璜开封成电器十年了，跑得了和尚跑不了庙】立住千斤信任，随后立刻引导【评论区留个屋顶面积我帮你算算】或【直接发私信】，行动门槛最低，转化率最高！\n"
        "4. 输出格式铁律（严禁任何英文代码）：\n"
        "   - 必须且只能输出包含表头的标准 TSV 文本表格，严禁任何客套话、前言或总结！\n"
        "   - 表格所有列（包括镜头运动）必须全部使用生动的纯中文通俗大白话；\n"
        "   - 台词栏必须是纯粹干净的中文汉字，绝对严禁出现 <font>, <b>, <d>[Chinese] 等任何 HTML 或机器代码标签！\n"
        "   - 动作栏必须标明机位（@正面 或 @侧面），且声明主角为唯一发言人。\n\n"
        "【表格输出结构示例】：\n"
        "时间\\t画面场景\\t镜头运动\\t数字人动作表情\\t口播台词\n"
        "0-4s【痛点钩子】\\t[真实场景描述]\\t[手持平稳后退跟拍等中文运镜]\\t@正面 [动作描述，主角唯一发言]\\t[严格对应秒数的纯中文台词]\n"
        "4-11s【方案展开】\\t[真实场景描述]\\t[仰拍缓缓推升等中文运镜]\\t@侧面 [动作描述，主角唯一发言]\\t[严格对应秒数的纯中文台词]\n"
        "11-15s【立信转化】\\t[真实场景描述]\\t[平视平稳推进等中文运镜]\\t@正面 [动作描述，主角唯一发言]\\t[严格对应秒数的纯中文台词]\n"
    )

    style_prompts = {
        "pain_point": "风格要求：【痛点掀桌共鸣型】。开篇第一秒用最扎心的现实痛点直击灵魂（电费比工资贵 / 夏天三楼烫成烤箱），反问质问戳中心头刺，随后抛出反常识零成本解法，结尾提10年老店并引导评论区留面积。",
        "case_story": "风格要求：【同城真实案例闪电击穿型】。直接调取知识库里江津珞璜郭坝村老周家自建房（发了3000初装费每年稳拿2240租金）或珞璜工业园精密机械厂的真实数字切入，摆真实数据震撼用户，结尾提10年老店并引导评论区留面积。",
        "value_dry": "风格要求：【干货算账与细节拆解型】。用最通俗的人话拆解资方为什么愿意全款建站（全额上网卖电/峰平谷电价打折），把账算得明明白白，结尾提10年老店并引导评论区留面积。",
        "anti_scam": "风格要求：【专撕套路与反认知型】。直接掀桌子撕开市场上不良中介假免费真贷款、游击队装完漏水跑路的黑幕，教老百姓如何一眼看穿，立稳专撕套路、帮老百姓避坑的硬核人设，结尾提10年老店并引导直接发私信免费帮忙看合同。",
        "trust_cta": "风格要求：【10年老店千斤信誉强号召型】。开篇直奔老百姓怕被骗、怕惹麻烦的顾虑，顺水推舟亮出珞璜封成电器十年实体老门市的定海神针底牌，强力号召老乡和老板在评论区留面积或发私信。"
    }

    user_task = f"请针对业务分类：{'【重庆自建房户用光伏】' if req.category == 'RESIDENTIAL_CQ' else '【川渝工商业大厂房光伏】'}，按照风格：{style_prompts.get(req.style, style_prompts['pain_point'])}，直接生成标准 TSV 表格！"

    try:
        reply = await call_llm_chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_task}
        ], json_mode=False)

        # 清洗可能存在的 markdown 代码块反引号
        raw_text = reply.replace("```tsv", "").replace("```text", "").replace("```markdown", "").replace("```", "").strip()

        # 智能提取纯净 TSV 表格行，防止大模型前后的啰嗦废话干扰
        lines = [l.rstrip() for l in raw_text.split("\n") if l.strip()]
        table_lines = [l for l in lines if ("\t" in l or "时间" in l or re.search(r"\d+\s*[-~至到]\s*\d+\s*(?:s|秒)?", l))]
        final_tsv = "\n".join(table_lines) if table_lines else raw_text

        # 如果提取出的表格为空，直接以原始内容兜底，绝对不为空字符串
        if not final_tsv.strip():
            final_tsv = raw_text

        return {
            "success": True,
            "script_table": final_tsv,
            "category": req.category,
            "style": req.style,
            "message": "🎉 封成电器金牌文案已秒级生成完毕！"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调用大模型撰写文案失败: {str(e)}")

class ChatMessage(BaseModel):
    role: str = Field(..., description="user 或 assistant 或 system")
    content: str = Field(..., description="消息内容")

class AIChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., description="历史对话上下文")
    category: Optional[str] = Field("RESIDENTIAL_CQ", description="当前业务偏好: RESIDENTIAL_CQ 或 COMMERCIAL_SC_CQ")

@router.post("/chat", summary="★ 川渝光伏文案编剧大模型交互对话 (支持多轮修改与分镜生成)")
async def chat_with_script_ai(req: AIChatRequest):
    """
    网页端【文案】标签专享：
    1. 动态注入桌面【文案/知识库/】真实数据与军规；
    2. 支持与大模型多轮自由对话交流与分镜精修；
    3. 只要涉及文案或分镜，自动严格卡死 15 秒、75~86 字物理红线，并输出规范 TSV 表格！
    """
    kb_text = load_local_knowledge_base()

    system_prompt = (
        "你现在是重庆江津珞璜【封成电器】（实体店扎根珞璜开业整整10年）的店主老陈/宋勇本人兼首席金牌编剧！\n"
        "你的任务是为你的川渝光伏短视频账号与用户进行沟通对话，撰写并精修极具杀伤力、完播率极高的 15 秒短视频获客口播分镜脚本，或解答政策与打法疑问。\n\n"
        "【必须调用的本地真实事实与知识库数据（严禁虚构乱编）】：\n"
        f"{kb_text}\n\n"
        "【四大不可逾越的死线铁律】：\n"
        "1. 总时长与字数绝对控制（物理死线）：\n"
        "   - 所有分镜时长相加必须【严格等于 15 秒】（如 0-4s + 4-11s + 11-15s，或 0-5s + 5-10s + 10-15s）；\n"
        "   - 所有镜头的口播台词总汉字数必须【严格卡死在 75 ~ 86 字之间】（按4字/秒计算），绝对不能超过88字，多一个字视频模型就念不完！\n"
        "2. 业务边界与核心利益严格区分：\n"
        "   - 🏡 重庆自建房户用：发出来的电全额上网业主不能自己用！核心利益必须是【一分钱不用掏 + 建成就发现金初装补贴费打卡（2000-5000元）+ 每年固定稳拿屋顶租金 + 免费白得一个高大隔热防雨轻钢阳光棚露台】（合规挑高2.2-3.2m，严禁砌墙封窗）！\n"
        "   - 🏭 川渝工商业大厂房：屋顶原则上无租金！核心利益必须是【资方100%全额全资建站零垫资零负债，白天高负荷生产直接用折上折便宜电，一度电省两三毛到四毛，一年帮企业省出大几万乃至十来万纯利润净现金】！\n"
        "3. 实体店信誉背书与高转化号召（铁律）：\n"
        "   - 绝不在视频里邀约用户来店里坐（门槛太高没人跑）；\n"
        "   - 视频结尾必须顺嘴提一嘴【我在珞璜开封成电器十年了，跑得了和尚跑不了庙】立住千斤信任，随后立刻引导【评论区留个屋顶面积我帮你算算】或【直接发私信】，转化率最高！\n"
        "4. 输出格式与交互规范：\n"
        "   - 当用户要求写文案、写分镜或修改调整分镜时，必须给出完整的标准 TSV 分镜表格（时间\\t画面场景\\t镜头运动\\t数字人动作表情\\t口播台词）；\n"
        "   - 表格所有列（包括镜头运动）必须全部使用生动的纯中文通俗大白话；\n"
        "   - 台词栏必须是纯粹干净的中文汉字，绝对严禁出现 <font>, <b>, <d>[Chinese] 等任何 HTML 或机器代码标签！\n"
        "   - 动作栏必须标明机位（@正面 或 @侧面），且声明主角为唯一发言人。\n"
        "   - 如果用户只是在咨询问题、探讨打法或日常交流，用老成持重、热情爽朗的江津实体店主口吻直接自然交谈回答。"
    )

    llm_messages = [{"role": "system", "content": system_prompt}]
    for m in req.messages:
        # 过滤并添加历史
        role = m.role if m.role in ["user", "assistant", "system"] else "user"
        llm_messages.append({"role": role, "content": m.content})

    try:
        reply = await call_llm_chat(llm_messages, json_mode=False)
        if not reply or not reply.strip():
            raise RuntimeError("大模型未能返回有效内容，请稍后重试。")
        return {
            "success": True,
            "reply": reply.strip()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调用大模型对话失败: {str(e)}")

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
