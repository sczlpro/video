import mimetypes
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError

from core.config import settings

class R2StorageClient:
    """
    Cloudflare R2 对象存储驱动客户端
    基于 AWS S3 兼容协议 (boto3)，支持大文件上传、公网直链生成与连通性自检
    """

    def __init__(self):
        self.account_id = settings.CF_ACCOUNT_ID
        self.access_key_id = settings.CF_R2_ACCESS_KEY_ID
        self.secret_access_key = settings.CF_R2_SECRET_ACCESS_KEY
        self.bucket_name = settings.CF_R2_BUCKET
        self.public_domain = settings.CF_R2_PUBLIC_DOMAIN

        self._client = None
        if settings.is_r2_configured:
            self._init_client()

    def _init_client(self):
        """初始化 boto3 S3 客户端"""
        endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
        self._client = boto3.client(
            service_name="s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=30
            )
        )

    def _ensure_client(self):
        """确保客户端已初始化"""
        if not self._client:
            if settings.is_r2_configured:
                self._init_client()
            else:
                raise RuntimeError(
                    "Cloudflare R2 尚未配置完整凭证！\n"
                    "请在 .env 文件中配置：\n"
                    "- CF_ACCOUNT_ID\n"
                    "- CF_R2_ACCESS_KEY_ID\n"
                    "- CF_R2_SECRET_ACCESS_KEY\n"
                    "- CF_R2_BUCKET (可选，默认 ayong-solar-video)\n"
                    "- CF_R2_PUBLIC_DOMAIN (可选，用于生成公网直链)"
                )

    def get_public_url(self, remote_key: str) -> str:
        """根据配置生成文件的公网直链"""
        clean_key = remote_key.lstrip("/")
        if self.public_domain:
            domain = self.public_domain.rstrip("/")
            # 如果没带协议，自动补齐 https://
            if not domain.startswith("http://") and not domain.startswith("https://"):
                domain = f"https://{domain}"
            return f"{domain}/{clean_key}"
        # 若未绑定自定义域名，默认返回 R2 dev 格式说明
        return f"https://pub-{self.bucket_name}.r2.dev/{clean_key}"

    def upload_file(
        self,
        local_path: str | Path,
        remote_key: str,
        content_type: Optional[str] = None
    ) -> str:
        """
        上传本地文件至 R2 并返回可直接在线播放或下载的公网 URL
        :param local_path: 本地文件绝对路径或相对路径
        :param remote_key: R2 存储路径（例如 'videos/20261013_master.mp4'）
        :param content_type: MIME 类型，若为空则自动通过扩展名推断
        :return: 公网访问 URL
        """
        self._ensure_client()
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"本地文件不存在: {local_path}")

        # 自动推断 Content-Type
        if not content_type:
            mime, _ = mimetypes.guess_type(str(local_path))
            content_type = mime or "application/octet-stream"

        clean_key = remote_key.lstrip("/")
        extra_args = {"ContentType": content_type}

        with open(local_path, "rb") as f:
            self._client.upload_fileobj(
                Fileobj=f,
                Bucket=self.bucket_name,
                Key=clean_key,
                ExtraArgs=extra_args
            )

        return self.get_public_url(clean_key)

    def upload_bytes(
        self,
        data: bytes,
        remote_key: str,
        content_type: str = "application/octet-stream"
    ) -> str:
        """上传内存字节流至 R2"""
        self._ensure_client()
        clean_key = remote_key.lstrip("/")
        self._client.put_object(
            Bucket=self.bucket_name,
            Key=clean_key,
            Body=data,
            ContentType=content_type
        )
        return self.get_public_url(clean_key)

    def check_connection(self) -> Dict[str, Any]:
        """
        检查 Cloudflare R2 连通性
        :return: 包含连通状态、桶名、信息或错误原因的字典
        """
        if not settings.is_r2_configured:
            return {
                "status": "UNCONFIGURED",
                "connected": False,
                "bucket": self.bucket_name,
                "message": "未检测到完整的 R2 凭证（缺少 Account ID、Access Key 或 Secret Key）",
                "missing_keys": [
                    k for k in ["CF_ACCOUNT_ID", "CF_R2_ACCESS_KEY_ID", "CF_R2_SECRET_ACCESS_KEY"]
                    if not getattr(settings, k)
                ]
            }

        try:
            self._ensure_client()
            # 探测存储桶是否存在及权限
            self._client.head_bucket(Bucket=self.bucket_name)
            return {
                "status": "CONNECTED",
                "connected": True,
                "bucket": self.bucket_name,
                "endpoint": f"https://{self.account_id}.r2.cloudflarestorage.com",
                "public_domain": self.public_domain or "未配置（将使用 r2.dev 格式）",
                "message": f"成功连通 Cloudflare R2 存储桶 [{self.bucket_name}]"
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            return {
                "status": "ERROR",
                "connected": False,
                "bucket": self.bucket_name,
                "error_code": error_code,
                "message": f"R2 响应错误: {error_code} - {error_msg}"
            }
        except Exception as e:
            return {
                "status": "FAILED",
                "connected": False,
                "bucket": self.bucket_name,
                "message": f"连接 R2 失败: {str(e)}"
            }

    def list_files(self, prefix: str = "", max_keys: int = 100) -> List[Dict[str, Any]]:
        """列出指定目录前缀下的文件列表"""
        self._ensure_client()
        try:
            response = self._client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix.lstrip("/"),
                MaxKeys=max_keys
            )
            contents = response.get("Contents", [])
            result = []
            for item in contents:
                key = item["Key"]
                result.append({
                    "key": key,
                    "size": item["Size"],
                    "last_modified": item["LastModified"].isoformat(),
                    "url": self.get_public_url(key)
                })
            return result
        except ClientError as e:
            raise RuntimeError(f"列出 R2 文件列表失败: {str(e)}")

    def file_exists(self, remote_key: str) -> bool:
        """检查远程文件是否存在"""
        self._ensure_client()
        try:
            self._client.head_object(Bucket=self.bucket_name, Key=remote_key.lstrip("/"))
            return True
        except ClientError:
            return False

    def delete_file(self, remote_key: str) -> bool:
        """删除 R2 上的指定文件"""
        self._ensure_client()
        try:
            self._client.delete_object(Bucket=self.bucket_name, Key=remote_key.lstrip("/"))
            return True
        except ClientError:
            return False

# 全局单例
r2_storage = R2StorageClient()
