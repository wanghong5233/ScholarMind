import os
import re
import hashlib
from typing import Optional, Tuple

import httpx

from .file_utils import get_project_base_directory


def _sanitize_filename(name: str) -> str:
    """
    将任意字符串转换为安全的文件名。
    仅保留字母数字、点、下划线和连字符，并截断过长名称。
    """
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)
    return name[:128] if len(name) > 128 else name


class FileStorageUtil:
    """
    简单的文件存储工具：
    - 提供基于知识库的存储目录
    - 从 URL 下载 PDF 并计算 SHA256
    """

    @staticmethod
    def get_kb_storage_dir(kb_id: int) -> str:
        base = get_project_base_directory("storage", "file", f"kb_{kb_id}")
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def download_pdf(url: str, kb_id: int, preferred_name: Optional[str] = None, timeout: int = 30) -> Tuple[str, str]:
        """
        下载 PDF 文件到 KB 目录，返回 (local_path, sha256)。
        若服务端未返回 application/pdf，但 URL 显示为 .pdf，也尝试保存。
        """
        storage_dir = FileStorageUtil.get_kb_storage_dir(kb_id)

        filename = preferred_name or "document.pdf"
        filename = _sanitize_filename(filename)
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"

        local_path = os.path.join(storage_dir, filename)

        with httpx.stream("GET", url, timeout=timeout) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").lower()
            if "application/pdf" not in content_type and not url.lower().endswith(".pdf"):
                # 非 PDF，抛出异常，由上层处理
                raise ValueError(f"URL does not seem to be a PDF: content-type={content_type}")

            hasher = hashlib.sha256()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    hasher.update(chunk)
                    f.write(chunk)

        return local_path, hasher.hexdigest()


