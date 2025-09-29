"""
快速文档解析服务
处理文档解析和Redis存储相关的业务逻辑
"""

import os
import redis
from docx import Document
import pdfplumber
from io import BytesIO
from fastapi import HTTPException
from utils import logger
from typing import Tuple


class QuickParseService:
    """快速文档解析服务类
    
    支持的文件格式及限制:
    - PDF: 不超过4页
    - DOCX: 不超过4000字符
    - TXT: 不超过4000字符
    
    解析结果存储到Redis，默认保存2小时
    """
    
    def __init__(self):
        # Redis 连接配置
        self.redis_host = os.getenv('REDIS_HOST', 'redis')
        self.redis_port = int(os.getenv('REDIS_PORT', 6379))
        self.redis_db = int(os.getenv('REDIS_DB', 0))
        
        # 创建 Redis 客户端
        self.redis_client = redis.Redis(
            host=self.redis_host, 
            port=self.redis_port, 
            db=self.redis_db, 
            decode_responses=True
        )
        
        # 支持的文件格式
        self.supported_formats = ['docx', 'pdf', 'txt']
        
        # 页数限制（仅用于PDF）
        self.max_pages = 4
        
        # 字符数限制（用于TXT和DOCX）
        self.max_characters = 4000
        
        # Redis 过期时间（2小时）
        self.redis_expire_seconds = 7200

    def validate_file_format(self, filename: str) -> str:
        """验证上传的文件名，确保其格式受支持。

        Args:
            filename (str): 用户上传的原始文件名。

        Returns:
            str: 小写的文件扩展名（如 'pdf', 'docx'）。

        Raises:
            HTTPException (400): 如果文件名为空或文件格式不受支持。
        """
        if not filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        
        file_extension = filename.lower().split('.')[-1]
        if file_extension not in self.supported_formats:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持 {', '.join(self.supported_formats)}"
            )
        
        return file_extension

    def check_session_exists(self, session_id: str) -> bool:
        """检查指定的 session_id 是否已在 Redis 中存在对应的文档。

        用于防止同一会话重复上传文档进行快速解析。

        Args:
            session_id (str): 需要检查的会话ID。

        Returns:
            bool: 如果 session_id 已存在于 Redis 中，则返回 True，否则返回 False。
        """
        return self.redis_client.exists(session_id)

    def parse_docx(self, file_content: bytes) -> Tuple[str, int]:
        """从内存中的 DOCX 文件内容提取纯文本。

        Args:
            file_content (bytes): DOCX 文件的原始二进制内容。

        Returns:
            Tuple[str, int]: 一个元组，包含提取出的文本内容和总字符数。

        Raises:
            HTTPException (400): 如果文件内容超过字符数限制，或文件本身已损坏无法解析。
        """
        try:
            doc = Document(BytesIO(file_content))
            text = []
            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    text.append(paragraph.text.strip())
            
            content = '\n'.join(text)
            char_count = len(content)
            
            # 检查字符数限制
            if char_count > self.max_characters:
                raise HTTPException(
                    status_code=400, 
                    detail=f"DOCX 文档字符数({char_count})超过限制({self.max_characters}字符)"
                )
            
            return content, char_count
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"DOCX 文件解析失败: {str(e)}")

    def parse_pdf(self, file_content: bytes) -> Tuple[str, int]:
        """从内存中的 PDF 文件内容提取纯文本。

        Args:
            file_content (bytes): PDF 文件的原始二进制内容。

        Returns:
            Tuple[str, int]: 一个元组，包含提取出的文本内容和PDF的总页数。

        Raises:
            HTTPException (400): 如果PDF页数超过限制，或文件本身已损坏无法解析。
        """
        try:
            pdf_file = BytesIO(file_content)
            
            # 使用 pdfplumber 解析
            with pdfplumber.open(pdf_file) as pdf:
                pages = len(pdf.pages)
                if pages > self.max_pages:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"PDF 文档页数({pages})不能超过 {self.max_pages} 页"
                    )
                
                text = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text.append(page_text)
                
                return '\n'.join(text), pages
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF 文件解析失败: {str(e)}")

    def parse_txt(self, file_content: bytes) -> Tuple[str, int]:
        """从内存中的 TXT 文件内容提取纯文本，并自动检测编码。

        会依次尝试 'utf-8', 'gbk', 'gb2312', 'ascii' 等常见编码。

        Args:
            file_content (bytes): TXT 文件的原始二进制内容。

        Returns:
            Tuple[str, int]: 一个元组，包含解码后的文本内容和总字符数。

        Raises:
            HTTPException (400): 如果文件内容超过字符数限制，或无法使用支持的编码正确解码。
        """
        try:
            # 尝试不同的编码
            encodings = ['utf-8', 'gbk', 'gb2312', 'ascii']
            content = None
            
            for encoding in encodings:
                try:
                    content = file_content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                raise HTTPException(status_code=400, detail="无法识别文本文件编码")
            
            char_count = len(content)
            
            # 检查字符数限制
            if char_count > self.max_characters:
                raise HTTPException(
                    status_code=400, 
                    detail=f"TXT 文档字符数({char_count})超过限制({self.max_characters}字符)"
                )
            
            return content, char_count
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"TXT 文件解析失败: {str(e)}")

    def parse_document(self, file_content: bytes, file_extension: str) -> Tuple[str, int]:
        """根据文件扩展名，调度到相应的解析函数进行文本提取。

        这是一个分发器方法，它本身不执行解析，而是根据文件类型调用
        专门的解析函数（如 parse_pdf, parse_docx）。

        Args:
            file_content (bytes): 文件的原始二进制内容。
            file_extension (str): 小写的文件扩展名 (e.g., 'pdf')。

        Returns:
            Tuple[str, int]: 一个元组，包含提取出的文本内容和一个统计值。
                - 对于PDF，统计值是总页数。
                - 对于TXT/DOCX，统计值是总字符数。

        Raises:
            HTTPException (400): 如果传入了当前服务不支持的文件扩展名。
        """
        if file_extension == 'docx':
            return self.parse_docx(file_content)
        elif file_extension == 'pdf':
            return self.parse_pdf(file_content)
        elif file_extension == 'txt':
            return self.parse_txt(file_content)
        else:
            raise HTTPException(status_code=400, detail="不支持的文件格式")

    def store_to_redis(self, session_id: str, content: str) -> None:
        """将解析后的文本内容存储到 Redis 中，并设置过期时间。

        Args:
            session_id (str): 作为 Redis 中的 key。
            content (str): 需要存储的文本内容，作为 Redis 中的 value。

        Raises:
            HTTPException (500): 如果连接 Redis 或执行 setex 命令失败。
        """
        try:
            # 使用 SETEX 命令将 content 存储到 Redis。
            # SETEX 是 "SET with EXpire" 的缩写，它是一个原子操作，
            # 可以在一个步骤内完成设置键值对和设置其过期时间。
            self.redis_client.setex(
                # 参数1: Key (键)
                # 使用 session_id 作为唯一的键，将文档内容与特定会话关联。
                session_id, 
                
                # 参数2: Expiration (过期时间，单位：秒)
                # self.redis_expire_seconds 的值为7200 (2小时)。
                # Redis会在2小时后自动删除这个键值对，实现了临时存储。
                self.redis_expire_seconds,
                
                # 参数3: Value (值)
                # 存储从文档中解析出的纯文本内容。
                content
            )
            logger.info(f"文档内容已存储到Redis，session_id: {session_id}")
        except Exception as e:
            raise HTTPException(
                status_code=500, 
                detail=f"存储到Redis失败: {str(e)}"
            )

    def get_from_redis(self, session_id: str) -> str:
        """根据 session_id 从 Redis 中检索之前存储的文本内容。

        Args:
            session_id (str): 要检索的 Redis key。

        Returns:
            str: 存储在 Redis 中的文本内容。

        Raises:
            HTTPException (404): 如果在 Redis 中找不到对应的 key（可能已过期或从未上传）。
        """
        content = self.redis_client.get(session_id)
        if not content:
            raise HTTPException(
                status_code=404, 
                detail="未找到该会话的文档内容，可能已过期或未上传"
            )
        return content

    def get_ttl(self, session_id: str) -> int:
        """获取 Redis 中指定 key 的剩余存活时间 (Time To Live)。

        Args:
            session_id (str): 需要查询的 Redis key。

        Returns:
            int: 剩余的秒数。如果 key 不存在或没有设置过期时间，则返回-1或-2（取决于Redis版本）。
        """
        return self.redis_client.ttl(session_id)

    def quick_parse_document(self, session_id: str, filename: str, file_content: bytes) -> dict:
        """
        执行快速文档解析的完整业务流程编排。

        这个方法是 `/quick_parse` 接口的直接服务实现，它按顺序调用
        校验、解析和存储等多个内部方法来完成整个任务。

        Args:
            session_id (str): 当前的会话ID。
            filename (str): 用户上传的原始文件名。
            file_content (bytes): 文件的原始二进制内容。

        Returns:
            dict: 一个包含处理结果的字典，用于API响应。
                  - 成功时，包含状态、消息、文件名、统计信息等。
        
        Raises:
            HTTPException: 在处理流程的任何一步失败时（如格式不支持、
                           内容超限、会话已存在文档等），都会抛出相应的
                           HTTP异常。
        """
        # 验证文件格式
        file_extension = self.validate_file_format(filename)
        
        # 检查会话是否已存在文档
        if self.check_session_exists(session_id):
            raise HTTPException(
                status_code=400, 
                detail="该会话已有文档，每个session_id只能上传一个文档"
            )
        
        # 验证文件内容
        if not file_content:
            raise HTTPException(status_code=400, detail="文件内容为空")
        
        # 解析文档
        content, count_value = self.parse_document(file_content, file_extension)
        
        # 存储到Redis
        self.store_to_redis(session_id, content)
        
        # 根据文件类型返回不同的统计信息
        if file_extension == 'pdf':
            return {
                "status": "success",
                "message": "文档解析完成",
                "session_id": session_id,
                "filename": filename,
                "file_type": file_extension,
                "pages": count_value,
                "content_length": len(content),
                "limit_info": f"PDF页数限制: {self.max_pages}页",
                "expiry_hours": self.redis_expire_seconds // 3600
            }
        else:  # txt 或 docx
            return {
                "status": "success",
                "message": "文档解析完成",
                "session_id": session_id,
                "filename": filename,
                "file_type": file_extension,
                "character_count": count_value,
                "content_length": len(content),
                "limit_info": f"字符数限制: {self.max_characters}字符",
                "expiry_hours": self.redis_expire_seconds // 3600
            }

    def get_parsed_content(self, session_id: str) -> dict:
        """
        获取并打包指定会话已解析的文档内容及其元数据。

        这个方法是 `/get_parsed_content` 接口的直接服务实现。

        Args:
            session_id (str): 需要获取内容的会话ID。

        Returns:
            dict: 一个包含处理结果的字典，用于API响应。
                  内容包括解析出的文本、文本长度、在Redis中的剩余存活时间等。
        
        Raises:
            HTTPException (404): 如果在Redis中找不到对应会话的文档内容。
        """
        content = self.get_from_redis(session_id)
        ttl = self.get_ttl(session_id)
        
        return {
            "status": "success",
            "session_id": session_id,
            "content": content,
            "content_length": len(content),
            "remaining_seconds": ttl if ttl > 0 else 0
        }


# 创建全局服务实例
# 采用单例模式，在整个应用生命周期内，只使用这一个QuickParseService实例，
# 这样可以复用其内部的Redis连接，避免重复创建连接的开销。
quick_parse_service = QuickParseService() 