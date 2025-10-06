from typing import List, AsyncGenerator, Dict
from schemas.rag import Chunk
from service.core.abstractions.llm import BaseLLM, ToolCallResult
from core.config import settings
from utils.get_logger import log

class LocalLlm(BaseLLM):
    """
    使用部署在本地的大语言模型 (LLM) 的实现类。
    【注意】这是一个结构性占位符，具体实现将依赖于所选的推理框架 (如 vLLM)。
    """
    def __init__(self):
        # 这里的模型加载逻辑将非常复杂，可能涉及GPU配置、量化等
        # 例如: from vllm import LLM; self.model = LLM(model=settings.LOCAL_LLM_PATH)
        log.info(f"LocalLlm initialized with model path: {settings.LOCAL_LLM_PATH}. (This is a placeholder).")
        self.model = None # 占位

    def _build_prompt(self, query: str, context: List[Chunk]) -> str:
        context_str = "\n".join([f"[{i+1}] {chunk.content}" for i, chunk in enumerate(context)])
        # 不同的本地模型可能需要遵循特定的 prompt template (e.g., Llama3-instruct)
        prompt = f"""
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>
        You are a helpful assistant. Answer the user's question based on the provided context.
        <|eot_id|><|start_header_id|>user<|end_header_id|>
        Context:
        {context_str}
        ---
        Question: {query}
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """
        return prompt

    async def generate(self, query: str, context: List[Chunk]) -> str:
        log.warning("LocalLlm.generate is not implemented.")
        raise NotImplementedError("Local LLM non-stream generation is not yet implemented.")

    async def stream_generate(self, query: str, context: List[Chunk]) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Local LLM streaming is not implemented yet.")
        yield

    async def generate_from_prompt(self, prompt: str) -> str:
        raise NotImplementedError("Local LLM generation is not implemented yet.")

    async def stream_generate_from_prompt(self, prompt: str) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Local LLM streaming is not implemented yet.")
        yield

    async def generate_with_tools(self, query: str, context: List[Chunk], tools: List[Dict]) -> ToolCallResult:
        raise NotImplementedError("Tool calling is not implemented for the local LLM.")
