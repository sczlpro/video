import os
import subprocess
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.config import settings
from storage.r2_client import r2_storage

def optimize_video_faststart_and_poster(video_path: Path, output_filename_base: str) -> tuple[str, Optional[str]]:
    """
    流媒体终极优化：
    1. 注入 -movflags +faststart，将 MP4 MOOV 元数据索引表前置到第0字节，手机公网实现 0.1 秒秒级起播；
    2. 同步压制一张 30KB 的 WebP/JPG 首帧高清海报，供画廊和历史库瞬间渲染，杜绝整段视频下载导致的雪崩。
    返回: (优化后的视频文件名, 对应的高清首帧海报URL)
    """
    videos_dir = settings.BASE_DIR / "static" / "videos"
    faststart_filename = f"fs_{output_filename_base}" if not output_filename_base.startswith("fs_") else output_filename_base
    faststart_path = videos_dir / faststart_filename
    poster_filename = f"{Path(output_filename_base).stem}_poster.jpg"
    poster_path = videos_dir / poster_filename

    # 1. 执行 faststart 重打包 (不重新编码，耗时仅需 0.1 秒)
    cmd_fs = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(faststart_path)
    ]
    try:
        subprocess.run(cmd_fs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        # 用优化后的文件替换原文件
        if faststart_path.exists() and faststart_path.stat().st_size > 0:
            target_path = video_path
            faststart_path.replace(target_path)
    except Exception as e:
        print(f"[Assembler] 注入 +faststart 异常: {e}")

    # 2. 截取第 0.5 秒高清首帧海报 (体积仅 20~40KB)
    poster_url = None
    cmd_poster = [
        "ffmpeg", "-y",
        "-ss", "00:00:00.500",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(poster_path)
    ]
    try:
        subprocess.run(cmd_poster, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        if poster_path.exists():
            poster_url = f"/static/videos/{poster_filename}"
    except Exception as e:
        print(f"[Assembler] 生成首帧海报异常: {e}")

    return output_filename_base, poster_url

def concat_videos_ffmpeg(video_paths: List[str | Path], output_filename: Optional[str] = None) -> Dict[str, Any]:
    """
    使用 FFmpeg 对多个分镜视频进行 0 帧硬切无缝拼接，并强制开启 +faststart 流媒体秒开优化
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

    # 执行 0 帧硬切拼接并直接注入 +faststart
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(final_output_path)
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        # 如果流编码有细微差异，回退为安全重编码拼接
        fallback_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c:v", "libx264", "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(final_output_path)
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    finally:
        # 清理临时列表文件
        if concat_list_file.exists():
            concat_list_file.unlink(missing_ok=True)

    # 提取首帧高清海报图
    _, poster_url = optimize_video_faststart_and_poster(final_output_path, output_filename)

    # 尝试同步到 Cloudflare R2 并优先以 R2 全球 CDN 作为播放和下载直链
    r2_url = None
    r2_poster_url = None
    if settings.is_r2_configured:
        try:
            r2_url = r2_storage.upload_file(final_output_path, f"videos/{output_filename}")
            print(f"[Assembler] 成片已同步至 Cloudflare R2 全球加速网络: {r2_url}")
            if poster_url:
                local_poster = videos_dir / Path(poster_url).name
                if local_poster.exists():
                    r2_poster_url = r2_storage.upload_file(local_poster, f"posters/{local_poster.name}")
        except Exception as e:
            print(f"[Assembler] 同步 R2 异常: {e}")

    # 核心：如果 R2 可用，优先返回 R2 全球 CDN 直链，彻底免除容器小水管带宽拥堵！
    fast_video_url = r2_url if r2_url else f"/static/videos/{output_filename}"
    fast_poster_url = r2_poster_url if r2_poster_url else poster_url

    return {
        "success": True,
        "filename": output_filename,
        "local_path": str(final_output_path),
        "local_url": fast_video_url,
        "r2_url": r2_url,
        "download_url": fast_video_url,
        "poster_url": fast_poster_url
    }
