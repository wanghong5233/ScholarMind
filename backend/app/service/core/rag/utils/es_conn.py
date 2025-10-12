#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import logging
import re
import time
import os
import json

import copy
from elasticsearch import Elasticsearch
from elasticsearch_dsl import UpdateByQuery, Q, Search, Index
from service.core.rag.utils import singleton
from service.core.api.utils.file_utils import get_project_base_directory
from service.core.rag.utils.doc_store_conn import MatchExpr, OrderByExpr, MatchTextExpr, MatchDenseExpr, FusionExpr
from service.core.rag.nlp import is_english
from core.config import settings

# 统一使用 settings.ES_URL（可包含认证信息）
ES_URL = settings.ES_URL
ATTEMPT_TIME = 2
PAGERANK_FLD = "pagerank_fea"
TAG_FLD = "tag_feas"

logger = logging.getLogger('ragflow.es_conn')


@singleton
class ESConnection():
    """
    一个单例类，用于管理与Elasticsearch数据库的连接和交互。

    该类封装了所有底层的Elasticsearch操作，为上层服务提供了一个简洁、统一的接口，
    用于执行文档的增、删、查等操作。它负责建立连接、加载索引配置、构建复杂的查询语句，
    并解析返回结果。
    """
    def __init__(self):
        """
        初始化ESConnection单例实例。

        该方法执行以下操作：
        1. 从环境变量 `ES_HOST` 读取Elasticsearch服务的地址，并建立连接。
        2. 设置认证信息和超时时间。
        3. 从配置文件 `conf/mapping.json` 加载Elasticsearch索引的映射（mapping）定义，
           该定义规定了索引中每个字段的数据类型和属性。
        """
        self.info = {}
        logger.info(f"Connecting to Elasticsearch at {ES_URL}")
        self.es = Elasticsearch(
            [ES_URL],  # 完整URL，包含认证信息
            verify_certs=False,
            timeout=600,
        )
        logger.info("Elasticsearch connection established")

        fp_mapping = os.path.join(get_project_base_directory(), "conf", "mapping.json")
        self.mapping = json.load(open(fp_mapping, "r"))

    def create_index_if_not_exists(self, index_name: str):
        """
        如果索引不存在，则创建它。
        """
        try:
            if not self.es.indices.exists(index=index_name):
                self.es.indices.create(index=index_name, body=self.mapping)
                logger.info(f"Created index '{index_name}' with mapping.")
        except Exception as e:
            logger.error(f"Failed to create index '{index_name}': {e}")
            # Even if it fails (e.g., race condition), we can proceed,
            # as the insert operation might still succeed if another process created it.

    """
    Helper functions for search result
    """

    def getTotal(self, res):
        """
        从Elasticsearch搜索结果中提取匹配的文档总数。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。

        Returns:
            int: 匹配的文档总数。
        """
        if isinstance(res["hits"]["total"], type({})):
            return res["hits"]["total"]["value"]
        return res["hits"]["total"]

    def getChunkIds(self, res):
        """
        从Elasticsearch搜索结果中提取所有命中（hit）文档的ID列表。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。

        Returns:
            list[str]: 一个包含所有文档ID的列表。
        """
        return [d["_id"] for d in res["hits"]["hits"]]
    

    def getHighlight(self, res, keywords: list[str], fieldnm: str):
        """
        从Elasticsearch搜索结果中提取并格式化高亮片段。

        高亮片段是在指定字段中与关键词匹配的部分，通常用`<em>`标签包裹。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。
            keywords (list[str]): 用于高亮处理的关键词列表。
            fieldnm (str): 需要提取高亮片段的字段名。

        Returns:
            dict[str, str]: 一个字典，键是文档ID，值是格式化后的高亮文本。
        """
        ans = {}
        for d in res["hits"]["hits"]:
            hlts = d.get("highlight")
            if not hlts:
                continue
            txt = "...".join([a for a in list(hlts.items())[0][1]])
            if not is_english(txt.split()):
                ans[d["_id"]] = txt
                continue

            txt = d["_source"][fieldnm]
            txt = re.sub(r"[\r\n]", " ", txt, flags=re.IGNORECASE | re.MULTILINE)
            txts = []
            for t in re.split(r"[.?!;\n]", txt):
                for w in keywords:
                    t = re.sub(r"(^|[ .?/'\"\(\)!,:;-])(%s)([ .?/'\"\(\)!,:;-])" % re.escape(w), r"\1<em>\2</em>\3", t,
                               flags=re.IGNORECASE | re.MULTILINE)
                if not re.search(r"<em>[^<>]+</em>", t, flags=re.IGNORECASE | re.MULTILINE):
                    continue
                txts.append(t)
            ans[d["_id"]] = "...".join(txts) if txts else "...".join([a for a in list(hlts.items())[0][1]])

        return ans
    

    def getAggregation(self, res, fieldnm: str):
        """
        从Elasticsearch搜索结果中提取聚合（aggregation）数据。

        聚合数据类似于SQL中的 `GROUP BY` 结果，用于统计分析。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。
            fieldnm (str): 执行聚合的字段名。

        Returns:
            list[tuple[str, int]]: 一个元组列表，每个元组包含 (聚合键, 文档数量)。
        """
        agg_field = "aggs_" + fieldnm
        if "aggregations" not in res or agg_field not in res["aggregations"]:
            return list()
        bkts = res["aggregations"][agg_field]["buckets"]
        return [(b["key"], b["doc_count"]) for b in bkts]

    def getFields(self, res, fields: list[str]) -> dict[str, dict]:
        """
        从Elasticsearch搜索结果中提取每个命中（hit）文档的指定字段。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。
            fields (list[str]): 需要提取的字段名列表。

        Returns:
            dict[str, dict]: 一个字典，键是文档ID，值是包含所请求字段的子字典。
        """
        res_fields = {}
        if not fields:
            return {}
        for d in self.__getSource(res):
            m = {n: d.get(n) for n in fields if d.get(n) is not None}
            for n, v in m.items():
                if isinstance(v, list):
                    m[n] = v
                    continue
                if not isinstance(v, str):
                    m[n] = str(m[n])
                # if n.find("tks") > 0:
                #     m[n] = rmSpace(m[n])

            if m:
                res_fields[d["id"]] = m
        return res_fields


    def __getSource(self, res):
        """
        一个私有辅助函数，用于从搜索结果中提取 `_source` 字段，
        并附加 `_id` 和 `_score` 到 `_source` 中。

        Args:
            res (dict): Elasticsearch返回的原始搜索结果JSON对象。

        Returns:
            list[dict]: 一个列表，其中每个元素都是一个文档的 `_source` 字典。
        """
        rr = []
        for d in res["hits"]["hits"]:
            d["_source"]["id"] = d["_id"]
            d["_source"]["_score"] = d["_score"]
            rr.append(d["_source"])
        return rr

    """
    Database operations
    """
    def insert(self, documents: list[dict], indexName: str, knowledgebaseId: str = None) -> list[str]:
        """
        向指定的Elasticsearch索引中批量插入文档。

        该方法使用Elasticsearch的Bulk API以提高插入效率。

        Args:
            documents (list[dict]): 一个字典列表，每个字典代表一个待插入的文档。
                                   每个文档字典必须包含一个 'id' 键，用作其在ES中的 `_id`。
            indexName (str): 目标索引的名称。
            knowledgebaseId (str, optional): 知识库ID，此参数在此方法中当前未被使用，
                                             但保留以备将来扩展。

        Returns:
            list[str]: 一个错误信息列表。如果所有文档都成功插入，则返回空列表。
                       如果存在错误，列表中的每个元素都是一个格式为 "文档ID:错误详情" 的字符串。
        """
        # 插入前确保索引存在
        self.create_index_if_not_exists(indexName)
        
        # Refers to https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-bulk.html
        operations = []
        for d in documents:
            assert "_id" not in d
            assert "id" in d
            d_copy = copy.deepcopy(d)
            meta_id = d_copy.pop("id", "")
            operations.append(
                {"index": {"_index": indexName, "_id": meta_id}})
            operations.append(d_copy)

        res = []
        for _ in range(ATTEMPT_TIME):
            try:
                res = []
                r = self.es.bulk(index=(indexName), operations=operations,
                                 refresh=False, timeout="60s")
                if re.search(r"False", str(r["errors"]), re.IGNORECASE):
                    return res

                for item in r["items"]:
                    for action in ["create", "delete", "index", "update"]:
                        if action in item and "error" in item[action]:
                            res.append(str(item[action]["_id"]) + ":" + str(item[action]["error"]))
                return res
            except Exception as e:
                res.append(str(e))
                logger.warning("ESConnection.insert got exception: " + str(e))
                res = []
                if re.search(r"(Timeout|time out)", str(e), re.IGNORECASE):
                    res.append(str(e))
                    time.sleep(3)
                    continue
        return res
    

    def search(
            self, selectFields: list[str],
            highlightFields: list[str],
            condition: dict,
            matchExprs: list[MatchExpr],
            orderBy: OrderByExpr,
            offset: int,
            limit: int,
            indexNames: str | list[str],
            knowledgebaseIds: list[str],
            aggFields: list[str] = [],
            rank_feature: dict | None = None
    ):
        """
        在Elasticsearch中执行一个复杂的、可组合的搜索查询。

        该方法是RAG检索功能的核心“翻译官”，它将上层服务传入的、由各种抽象
        表达式对象（Expr）构成的“标准化订单”，动态地翻译并组装成一个完整、
        原生、可执行的Elasticsearch查询DSL (Domain Specific Language)。
        
        支持混合查询（关键词+向量）、过滤、排序、分页、高亮和聚合等多种功能。

        Args:
            selectFields (list[str]): 指定需要从 `_source` 中返回的字段列表 (类似于SQL的 `SELECT`)。
            highlightFields (list[str]): 需要进行关键词高亮的字段列表。
            condition (dict): 一个包含精确匹配过滤条件的字典 (类似于SQL的 `WHERE` 和 `AND`)。
                              键是字段名，值可以是单个值或一个值列表 (用于 `term` 或 `terms` 查询)。
            matchExprs (list[MatchExpr]): 一个“查询组件清单”，包含了所有用于计算相关度的查询表达式。
                                          - `MatchTextExpr`: 翻译为全文检索 (query_string)。
                                          - `MatchDenseExpr`: 翻译为向量检索 (knn)。
                                          - `FusionExpr`: 用于调整不同查询组件的权重。
            orderBy (OrderByExpr): 一个封装了排序规则的对象 (类似于SQL的 `ORDER BY`)。
            offset (int): 分页查询的起始位置 (类似于SQL的 `OFFSET`)。
            limit (int): 本次查询返回的最大文档数 (类似于SQL的 `LIMIT`)。
            indexNames (str | list[str]): 目标索引的名称或名称列表 (类似于SQL的 `FROM`)。
            knowledgebaseIds (list[str]): 知识库ID列表，会被自动加入到 `condition` 的 `kb_id` 过滤中。
            aggFields (list[str], optional): 需要进行聚合统计的字段列表 (类似于SQL的 `GROUP BY`)。
            rank_feature (dict | None, optional): 用于提升特定文档排名的特征及其权重，
                                                 例如使用 `pagerank` 来调整得分。

        Returns:
            dict: Elasticsearch返回的原始搜索结果JSON对象。
        """
        # Refers to https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl.html
        if isinstance(indexNames, str):
            indexNames = indexNames.split(",")
        assert isinstance(indexNames, list) and len(indexNames) > 0
        assert "_id" not in condition

        # 第一步：构建过滤查询 (bool.filter)
        # 这部分用于精确匹配，不计算得分，能有效利用ES缓存，性能高。
        # 类似于SQL中的 WHERE a=1 AND b IN (2,3)
        bqry = Q("bool", must=[])
        condition["kb_id"] = knowledgebaseIds
        for k, v in condition.items():
            if k == "available_int":
                if v == 0:
                    bqry.filter.append(Q("range", available_int={"lt": 1}))
                else:
                    bqry.filter.append(
                        Q("bool", must_not=Q("range", available_int={"lt": 1})))
                continue
            if not v:
                continue
            if isinstance(v, list):
                bqry.filter.append(Q("terms", **{k: v}))
            elif isinstance(v, str) or isinstance(v, int):
                bqry.filter.append(Q("term", **{k: v}))
            else:
                raise Exception(
                    f"Condition `{str(k)}={str(v)}` value type is {str(type(v))}, expected to be int, str or list.")

        s = Search()
        
        # 第二步：解析查询组件清单 (matchExprs)，构建相关度查询 (must/should) 和向量查询 (knn)
        # 这是整个方法最核心的“翻译”部分。
        vector_similarity_weight = 0.5
        
        # 2.1 (预处理): 如果是混合查询，先提取融合权重。
        for m in matchExprs:
            if isinstance(m, FusionExpr) and m.method == "weighted_sum" and "weights" in m.fusion_params:
                # 断言确保是标准的“文本+向量+融合”三组件模式
                assert len(matchExprs) == 3 and isinstance(matchExprs[0], MatchTextExpr) and isinstance(matchExprs[1],
                                                                                                       MatchDenseExpr) and isinstance(
                    matchExprs[2], FusionExpr)
                weights = m.fusion_params["weights"]
                vector_similarity_weight = float(weights.split(",")[1])
        
        # 2.2 (翻译): 遍历清单，将每个Expr对象翻译成对应的ES查询子句。
        for m in matchExprs:
            # 翻译 MatchTextExpr -> bool.must + query_string
            if isinstance(m, MatchTextExpr):
                minimum_should_match = m.extra_options.get("minimum_should_match", 0.0)
                if isinstance(minimum_should_match, float):
                    minimum_should_match = str(int(minimum_should_match * 100)) + "%"
                bqry.must.append(Q("query_string", fields=m.fields,
                                   type="best_fields", query=m.matching_text,
                                   minimum_should_match=minimum_should_match,
                                   boost=1))
                # 使用从FusionExpr中提取的权重，来调整文本查询的整体重要性
                bqry.boost = 1.0 - vector_similarity_weight

            # 翻译 MatchDenseExpr -> knn
            elif isinstance(m, MatchDenseExpr):
                assert (bqry is not None)
                similarity = 0.0
                if "similarity" in m.extra_options:
                    similarity = m.extra_options["similarity"]
                # k-NN查询是一个特殊的顶层查询，它内部可以包含一个filter子句。
                # 这里我们将前面构建的所有过滤条件都传给了它。
                s = s.knn(m.vector_column_name,
                          m.topn,
                          m.topn * 2,
                          query_vector=list(m.embedding_data),
                          filter=bqry.to_dict(),
                          similarity=similarity,
                          )

        # 2.3 (增强): 如果有rank_feature，构建should子句以提升特定文档的得分。
        if bqry and rank_feature:
            for fld, sc in rank_feature.items():
                if fld != PAGERANK_FLD:
                    fld = f"{TAG_FLD}.{fld}"
                bqry.should.append(Q("rank_feature", field=fld, linear={}, boost=sc))

        # 将构建好的布尔查询（包含filter, must, should）应用到主查询上
        if bqry:
            s = s.query(bqry)
            
        # 第三步：构建辅助功能 (高亮、排序、聚合、分页)
        # 翻译 Highlight -> highlight
        for field in highlightFields:
            s = s.highlight(field)

        # 翻译 OrderByExpr -> sort
        if orderBy:
            orders = list()
            for field, order in orderBy.fields:
                order = "asc" if order == 0 else "desc"
                # _score 是 ES 的内置排序字段，不支持 unmapped_type 等扩展参数
                if field == "_score":
                    orders.append({field: {"order": order}})
                    continue
                if field in ["page_num_int", "top_int"]:
                    order_info = {"order": order, "unmapped_type": "float",
                                  "mode": "avg", "numeric_type": "double"}
                elif field.endswith("_int") or field.endswith("_flt"):
                    order_info = {"order": order, "unmapped_type": "float"}
                else:
                    order_info = {"order": order, "unmapped_type": "text"}
                orders.append({field: order_info})
            s = s.sort(*orders)

        # 翻译 aggFields -> aggs
        for fld in aggFields:
            s.aggs.bucket(f'aggs_{fld}', 'terms', field=fld, size=1000000)

        # 翻译 offset/limit -> from/size
        if limit > 0:
            s = s[offset:offset + limit]
            
        # 第四步：最终组装与执行
        # 将所有构建的子句和选项，最终序列化为一个完整的ES查询JSON
        q = s.to_dict()
        logger.debug(f"ESConnection.search {str(indexNames)} query: " + json.dumps(q))

        # 执行查询，并包含超时重试逻辑
        for i in range(ATTEMPT_TIME):
            try:
                #print(json.dumps(q, ensure_ascii=False))
                res = self.es.search(index=indexNames,
                                     body=q,
                                     timeout="600s",
                                     # search_type="dfs_query_then_fetch",
                                     track_total_hits=True,
                                     _source=True)
                if str(res.get("timed_out", "")).lower() == "true":
                    raise Exception("Es Timeout.")
                logger.debug(f"ESConnection.search {str(indexNames)} res: " + str(res))
                return res
            except Exception as e:
                logger.exception(f"ESConnection.search {str(indexNames)} query: " + str(q))
                if str(e).find("Timeout") > 0:
                    continue
                raise e
        logger.error("ESConnection.search timeout for 3 times!")
        raise Exception("ESConnection.search timeout.")

    def delete(self, condition: dict, indexName: str, knowledgebaseId: str) -> int:
        """
        根据指定的条件，从一个索引中删除文档。

        该方法使用Elasticsearch的 `delete_by_query` API，可以批量删除满足条件的文档。

        Args:
            condition (dict): 一个包含过滤条件的字典，用于指定哪些文档需要被删除。
                              键是字段名，值是匹配条件。支持精确匹配、列表匹配 (`terms`)
                              以及通配符 (`wildcard`) 匹配。
            indexName (str): 目标索引的名称。
            knowledgebaseId (str): 知识库ID，这是一个强制性的过滤条件，以确保删除操作
                                   只在指定的知识库范围内进行，防止误删。

        Returns:
            int: 成功删除的文档数量。如果发生错误，则返回0。
        """
        try:
            # 构建删除查询
            query = {
                "query": {
                    "bool": {
                        "must": []
                    }
                }
            }
            
            # 添加知识库ID条件
            if knowledgebaseId:
                query["query"]["bool"]["must"].append({"term": {"kb_id": knowledgebaseId}})
            
            # 添加其他条件
            for field, value in condition.items():
                if isinstance(value, list):
                    query["query"]["bool"]["must"].append({"terms": {field: value}})
                elif isinstance(value, str) and value.startswith("*") and value.endswith("*"):
                    # 通配符查询（两端都有*）
                    query["query"]["bool"]["must"].append({"wildcard": {field: value}})
                elif isinstance(value, str) and (value.startswith("*") or value.endswith("*")):
                    # 通配符查询（一端有*）
                    query["query"]["bool"]["must"].append({"wildcard": {field: value}})
                else:
                    # 精确匹配
                    query["query"]["bool"]["must"].append({"term": {field: value}})
            
            # 打印调试信息
            logger.info(f"ES delete query: {json.dumps(query, ensure_ascii=False, indent=2)}")
            logger.info(f"ES delete index name: {indexName}")
            
            # 执行删除
            response = self.es.delete_by_query(
                index=indexName,
                body=query,
                refresh=True
            )
            
            logger.info(f"ES delete response: {response}")
            
            return response["deleted"]
            
        except Exception as e:
            logger.error(f"Failed to delete documents from ES: {str(e)}")
            return 0
