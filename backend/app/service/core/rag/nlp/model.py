from openai import OpenAI
from llama_index.core.data_structs import Node
from llama_index.core.schema import NodeWithScore
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank
import numpy as np
from typing import List

from core.config import settings


def get_chat_completion_block(session_id, question, references):
    try:
        client = OpenAI(
            api_key=settings.DASHSCOPE_API_KEY,
            base_url=settings.DASHSCOPE_BASE_URL,
        )
        formatted_references = "\n".join([f"[{ref['id']}] {ref['content']}" for ref in references])
        prompt = f"Question: {question}\nReferences:\n{formatted_references}\nAnswer concisely with citations."
        completion = client.chat.completions.create(
            model="deepseek-r1",
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        return completion.choices[0].message.content
    except Exception as e:
        return f"Error: {str(e)}"


def rerank_similarity(query, texts):
    api_key = settings.DASHSCOPE_API_KEY
    nodes = [NodeWithScore(node=Node(text=text), score=1.0) for text in texts]
    dashscope_rerank = DashScopeRerank(top_n=len(texts), api_key=api_key)
    results = dashscope_rerank.postprocess_nodes(nodes, query_str=query)
    scores = [res.score for res in results]
    return np.array(scores), None


def generate_embedding(
    text: str | List[str],
    api_key: str | None = None,
    base_url: str | None = None,
    model_name: str = "text-embedding-v3",
    dimensions: int = 1024,
    encoding_format: str = "float",
    max_batch_size: int = 10,
):
    api_key = api_key or settings.DASHSCOPE_API_KEY
    base_url = base_url or settings.DASHSCOPE_BASE_URL

    client = OpenAI(api_key=api_key, base_url=base_url)

    if isinstance(text, str):
        try:
            completion = client.embeddings.create(
                model=model_name,
                input=text,
                dimensions=dimensions,
                encoding_format=encoding_format,
            )
            return completion.data[0].embedding
        except Exception as e:
            print(f"OpenAI API 请求失败: {e}")
            return None

    if isinstance(text, list):
        all_embeddings: List[List[float] | None] = []
        for i in range(0, len(text), max_batch_size):
            batch = text[i : i + max_batch_size]
            try:
                completion = client.embeddings.create(
                    model=model_name,
                    input=batch,
                    dimensions=dimensions,
                    encoding_format=encoding_format,
                )
                batch_embeddings = [item.embedding for item in completion.data]
                all_embeddings.extend(batch_embeddings)
            except Exception as e:
                print(f"OpenAI API 批量请求失败 (batch {i//max_batch_size + 1}): {e}")
                all_embeddings.extend([None] * len(batch))
        return all_embeddings


if __name__ == "__main__":
    question = "法国的首都是哪里？"
    references = [
        {"id": 1, "content": "法国的首都是巴黎。"},
        {"id": 2, "content": "巴黎是欧洲的文化中心之一。"},
    ]
    session_id = "sd"
    response = get_chat_completion_block(session_id, question, references)
    print(response)