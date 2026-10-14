import os
import subprocess
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.config import settings
from storage.r2_client import r2_storage

def concat_videos_ffmpeg(video_paths: List[str | Path], output_filename: Optional[str] = None) -> Dict[str, Any]:
    """
    使用 FFmpeg 对多个分镜视频进行 0 帧硬切无缝拼接
    :param video_paths: 各分镜视频文件的本地绝对路径列表
    :param output_filename: 成品文件名 (可选)
    :return: 包含成片绝对路径、本地访问直链、R2直链等信息的字典
    """
    if not video_paths:
        raise ValueError("拼接视频列表不能为空")

    videos_dir = settings.BASE_DIR / "static" / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    if not output_filename:
        output_filename = f"ayong_master_{uuid.uuid4().hex[:8]}.mp4"
    if not output_filename.endswith(".mp4"):
        output_filename += ".mp4"

    final_output_path = videos_dir / output_filename
    concat_list_file = videos_dir / f"concat_{uuid.uuid4().hex[:6]}.txt"

    # 生成 concat 列表文件
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for vp in video_paths:
            clean_path = str(Path(vp).resolve()).replace("\\", "/")
            f.write(f"file '{clean_path}'\n")

    # 执行 0 帧硬切极速 copy 拼接 (耗时约 0.2 秒)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        str(final_output_path)
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        # 如果流编码有细微差异，回退为安全重编码拼接
        fallback_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            str(final_output_path)
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    finally:
        # 清理临时列表文件
        if concat_list_file.exists():
            concat_list_file.unlink(missing_ok=True)

    # 尝试同步到 Cloudflare R2
    r2_url = None
    if settings.is_r2_configured:
        try:
            r2_url = r2_storage.upload_file(final_output_path, f"videos/{output_filename}")
        except Exception:
            pass

    return {
        "success": True,
        "filename": output_filename,
        "local_path": str(final_output_path),
        "local_url": f"/static/videos/{output_filename}",
        "r2_url": r2_url,
        "download_url": f"/static/videos/{output_filename}"
    }
