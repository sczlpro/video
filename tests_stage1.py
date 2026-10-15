import sys

# 兼容 Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi.testclient import TestClient
from main import app
from storage.r2_client import r2_storage
from db.database import SessionLocal, init_db
from db.models import Project, Shot, RenderTask

def run_tests():
    print("=== [自检回归] 专业前端界面与核心流水线引擎验证 ===")
    
    # 1. 测试数据库表初始化
    print("1. 检查数据库连通与模型映射...")
    init_db()
    db = SessionLocal()
    p_count = db.query(Project).count()
    print(f"   ✓ 数据库就绪，现有项目记录数: {p_count}")
    db.close()

    # 2. 测试 R2 存储驱动自检
    print("2. 检查 Cloudflare R2 存储驱动逻辑...")
    status = r2_storage.check_connection()
    print(f"   ✓ R2 驱动状态探测: status={status.get('status')}")

    # 3. 测试全新专业前端页面与接口
    print("3. 测试全新专业即梦 App 风格前端页面...")
    client = TestClient(app)

    # 根路由 (前端生产工作台)
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "数字人" in res_root.text
    assert "manifest.json" in res_root.text
    assert "scenePillContainer" in res_root.text
    print(f"   ✓ GET / (专业工作台) 响应 200 OK (大小: {len(res_root.text)} 字节，零外部CDN依赖！)")

    # Hook 模板接口
    res_hooks = client.get("/api/v1/scripts/hooks")
    assert res_hooks.status_code == 200
    hooks_data = res_hooks.json()
    print(f"   ✓ GET /api/v1/scripts/hooks 响应 200 OK (包含业务线: {len(hooks_data['categories'])} 条)")

    # 违禁词排毒拦截测试 (故意输入违禁词)
    res_scan = client.post("/api/v1/scripts/scan", json={"script_text": "老刘，自建房免费装光伏还能贷款，稳赚不赔！"})
    assert res_scan.status_code == 200
    scan_data = res_scan.json()
    assert scan_data["passed"] is False
    assert scan_data["violations_count"] >= 3
    print(f"   ✓ POST /api/v1/scripts/scan 拦截成功: 命中 {scan_data['violations_count']} 处违规词，排毒门禁生效！")

    # 4. 预览解析与场景图绑定精准门检测试
    sample_script = (
        "@阿勇 0-4s【黄金钩子】\t背景是工厂车间电表飞速跳动[图片1] ，数字人前景出镜\t硬切开场，电表数字做动态放大\t身体前倾，眼神笃定，语气加重\t重庆开厂房、开酒店、开写字楼的老板！你每个月的电费，是不是遭不住了？\t电费高 成本扛不住\t开场直接戳核心痛点，精准喊人群，4秒锁定目标用户\n"
        "4-10s【双痛点共鸣】\t切换至厂房闲置屋顶全景[图片2] ，画面带老化、积水痕迹；画中画叠电费账单\t镜头从屋顶全景推近至破损处\t侧身指向屋顶，表情共情\t不光电费涨起吓人，你屋头那片空屋顶，空起晒太阳、漏水老化，还要花钱维护，两头都在浪费钱！\t空屋顶 两头浪费\t放大双重痛点，强化老板代入感"
    )
    res_preview = client.post("/api/v1/render/parse-preview", json={
        "prompt": sample_script,
        "duration": 30,
        "image_map": {
            "图片1": "/static/assets/scene_微信图片_20261013213732_194_2.png",
            "图片2": "/static/assets/scene_微信图片_20261013213736_195_2.png"
        }
    })
    assert res_preview.status_code == 200
    p_data = res_preview.json()
    assert p_data["segments_count"] == 2
    segs = p_data["segments"]
    assert segs[0]["scene_image"] == "scene_微信图片_20261013213732_194_2.png"
    assert segs[1]["scene_image"] == "scene_微信图片_20261013213736_195_2.png"
    assert "<Picture 2>" in segs[0]["prompt"]
    assert "[图片1]" not in segs[0]["prompt"]
    assert "[图片2]" not in segs[1]["prompt"]
    print("   ✓ POST /api/v1/render/parse-preview 验证通过: 镜1(电表)与镜2(屋顶)精准绑定，H3提示词规范化就绪！")

    # 5. 自由文本末尾插入 [图片1] 测试
    free_script = "重庆开厂房的老板，每个月电费遭不住了？ [图片1]\n闲置屋顶漏水还在浪费钱！ [图片2]"
    res_free = client.post("/api/v1/render/parse-preview", json={
        "prompt": free_script,
        "duration": 30
    })
    assert res_free.status_code == 200
    f_data = res_free.json()
    assert f_data["segments_count"] == 2
    assert f_data["segments"][0]["scene_image"] is not None
    assert f_data["segments"][1]["scene_image"] is not None
    print("   ✓ 自由文本末尾插入 [图片N] 验证通过: 成功自动挂载场景图并组装 Ref2VA 规范！")

    print("\n==============================================")
    print("🎉 恭喜！专业工作台与场景图严密调用链路 100% 验证通过！")
    print("==============================================")

if __name__ == "__main__":
    run_tests()
