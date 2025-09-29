
from service.core.rag.nlp.search_v2 import Dealer
from service.core.rag.utils.es_conn import ESConnection

import json
# 创建 ElasticsearchConnection 实例
es_connection = ESConnection()

# 创建 Dealer 实例
dealer = Dealer(dataStore=es_connection)


def retrieve_content(indexNames: str, question: str):
    """
    一个高级别的服务函数，作为RAG检索流程的主要入口点。

    该函数封装了底层的检索逻辑，为上层API（如 chat_rt.py）
    提供了一个清晰、简单的接口。它负责调用核心检索类 `Dealer`
    来执行实际的搜索，并对返回结果进行格式化处理。

    处理流程:
    1.  初始化 `Dealer` 类，该类包含了所有核心的检索算法。
    2.  调用 `dealer.retrieval` 方法，传入用户问题和指定的知识库索引
        (indexNames)，执行一个完整的两阶段检索（召回+精排）。
    3.  遍历检索返回的最佳匹配文本块 (chunks)。
    4.  从每个 chunk 中提取出前端需要展示的关键信息，如文本内容、
        来源文档ID和文档名称。
    5.  将提取的信息组装成一个标准化的字典列表，并返回。

    Args:
        indexNames (str): 目标Elasticsearch索引的名称。在本项目中，
                          这通常对应于用户的ID，用于限定检索范围在
                          该用户的私有知识库中。
        question (str): 用户提出的原始查询问题。

    Returns:
        list[dict]: 一个列表，其中每个字典代表一个检索到的相关知识片段。
                    每个字典包含ID、来源文档信息和文本内容。
    """

    # 执行搜索
    results = dealer.retrieval(question = question,
                               embd_mdl = None,
                               tenant_ids = indexNames,
                               kb_ids = None,
                               vector_similarity_weight=0.6,
                               page = 1,
                               page_size = 5
    )

    # 提取 chunks 中的信息
    extracted_data = []


    for i, chunk in enumerate(results['chunks'], start=1):
        content_with_weight = chunk.get('content_with_weight', 'N/A')
        # similarity = chunk.get('similarity', 'N/A')
        # vector_similarity = chunk.get('vector_similarity', 'N/A')
        # term_similarity = chunk.get('term_similarity', 'N/A')
        doc_id = chunk.get('doc_id', 'N/A')
        docnm = chunk.get('docnm_kwd', 'N/A')
        docnm = docnm.split("/")[-1]

        message = {
            "id": i,
            "document_id": doc_id,
            "document_name": docnm,
            'content_with_weight': content_with_weight,
        }
        
        extracted_data.append(message)

    return extracted_data


if __name__ == '__main__':
    res = retrieve_content(question="世运电路成长性如何", indexNames="test01")
    print(res)
    
    # 将提取的数据写入到文件
    # with open("output.txt", "w", encoding="utf-8") as file:
    #     for data in extracted_data:
    #         file.write(f"content_with_weight: {data['content_with_weight']}\n")
    #         file.write(f"similarity: {data['similarity']}\n")
    #         file.write(f"vector_similarity: {data['vector_similarity']}\n")
    #         file.write(f"term_similarity: {data['term_similarity']}\n")
    #         file.write("\n")
    
    # print("结果已写入到 output.txt 文件中")