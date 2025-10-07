import os
import re
import hashlib
import time
from typing import Optional, Tuple, Dict
from uuid import uuid4
from fastapi import UploadFile
from core.config import settings

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
    def sanitize_filename(name: str) -> str:
        return _sanitize_filename(name)

    @staticmethod
    def save_upload_temp(file: UploadFile, kb_id: int) -> Dict[str, str]:
        """
        将上传文件保存为临时文件，同时计算 sha256。
        返回 {temp_path, sha256, original_name, extension, size}
        """
        storage_dir = FileStorageUtil.get_kb_storage_dir(kb_id)
        tmp_dir = os.path.join(storage_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)

        original_name = file.filename or "uploaded"
        _, ext = os.path.splitext(original_name)
        temp_name = f"tmp_{uuid4().hex}{ext}"
        temp_path = os.path.join(tmp_dir, temp_name)

        hasher = hashlib.sha256()
        size = 0
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        with open(temp_path, "wb") as f:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    # 删除已写入的临时文件并报错
                    try:
                        f.close()
                    except Exception:
                        pass
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                    raise ValueError(f"Uploaded file exceeds limit: {settings.MAX_UPLOAD_SIZE_MB} MB")
                hasher.update(chunk)
                f.write(chunk)

        # 重置文件指针，避免上层复用时报错
        try:
            file.file.seek(0)
        except Exception:
            pass

        return {
            "temp_path": temp_path,
            "sha256": hasher.hexdigest(),
            "original_name": original_name,
            "extension": ext.lower(),
            "size": str(size),
        }

    @staticmethod
    def move_temp_to_final(temp_path: str, kb_id: int, doc_id: int, original_name: str) -> str:
        """
        将临时文件移动到最终文档路径，命名为 {doc_id}_{sanitized_original_name}
        返回最终路径。
        """
        storage_dir = FileStorageUtil.get_kb_storage_dir(kb_id)
        safe_name = FileStorageUtil.sanitize_filename(original_name)

        # 保留原始扩展名（避免过长截断掉 .pdf/.docx/.txt 等）
        base_ext = os.path.splitext(original_name)
        ext = base_ext[1].lower() if base_ext else ""
        if ext:
            # 若截断导致丢失扩展名，则补回；并确保整体长度不超限制
            if not safe_name.lower().endswith(ext):
                max_len = 128
                if len(safe_name) > max_len - len(ext):
                    safe_name = safe_name[: max(1, max_len - len(ext))] + ext
                else:
                    safe_name = safe_name + ext

        final_path = os.path.join(storage_dir, f"{doc_id}_{safe_name}")
        os.replace(temp_path, final_path)
        return final_path

    @staticmethod
    def download_pdf(
        url: str,
        kb_id: int,
        preferred_name: Optional[str] = None,
        timeout: int = 30,
        retries: int = 2,
        backoff: float = 1.5,
    ) -> Tuple[str, str]:
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

        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                with httpx.stream("GET", url, timeout=timeout) as resp:
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    if "application/pdf" not in content_type and not url.lower().endswith(".pdf"):
                        raise ValueError(f"URL does not seem to be a PDF: content-type={content_type}")

                    hasher = hashlib.sha256()
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_bytes():
                            if not chunk:
                                continue
                            hasher.update(chunk)
                            f.write(chunk)
                return local_path, hasher.hexdigest()
            except Exception as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(backoff ** attempt)
                else:
                    raise last_exc



