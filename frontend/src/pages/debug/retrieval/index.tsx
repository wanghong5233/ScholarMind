import * as api from '@/api'
import type { KnowledgeBase, RepositoryDocument } from '@/api/repository'
import type { RetrievalDebugResponse, RetrievalChunkPreview } from '@/api/debug'
import { useRequest } from 'ahooks'
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'
import styles from './index.module.scss'

const { Paragraph } = Typography
const { Option } = Select
const STORAGE_KEY = 'retrieval-debug-state'

interface ChunkRecord extends RetrievalChunkPreview {
  key: string
}

export default function RetrievalDebugPage() {
  const [kbList, setKbList] = useState<KnowledgeBase[]>([])
  const [selectedKbId, setSelectedKbId] = useState<number>()
  const [documents, setDocuments] = useState<RepositoryDocument[]>([])
  const [selectedDocIds, setSelectedDocIds] = useState<number[]>([])
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(6)
  const [sessionId, setSessionId] = useState('')
  const [indexMode, setIndexMode] = useState<string>('auto')
  const [result, setResult] = useState<RetrievalDebugResponse | null>(null)
  const [initialized, setInitialized] = useState(false)

  useEffect(() => {
    const cached = sessionStorage.getItem(STORAGE_KEY)
    if (!cached) {
      setInitialized(true)
      return
    }
    try {
      const parsed = JSON.parse(cached)
      if (parsed.selectedKbId) setSelectedKbId(parsed.selectedKbId)
      if (Array.isArray(parsed.selectedDocIds)) setSelectedDocIds(parsed.selectedDocIds)
      if (typeof parsed.query === 'string') setQuery(parsed.query)
      if (typeof parsed.topK === 'number') setTopK(parsed.topK)
      if (typeof parsed.sessionId === 'string') setSessionId(parsed.sessionId)
      if (typeof parsed.indexMode === 'string') setIndexMode(parsed.indexMode)
      if (parsed.result) setResult(parsed.result)
    } catch (error) {
      console.warn('Failed to restore retrieval debug state', error)
    } finally {
      setInitialized(true)
    }
  }, [])

  useEffect(() => {
    if (!initialized) return
    const payload = {
      selectedKbId,
      selectedDocIds,
      query,
      topK,
      sessionId,
      indexMode,
      result,
    }
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch (error) {
      console.warn('Failed to persist retrieval debug state', error)
    }
  }, [initialized, selectedKbId, selectedDocIds, query, topK, sessionId, indexMode, result])

  const { loading: kbLoading } = useRequest(
    async () => {
      const { data } = await api.repository.listKnowledgeBases({ errorToast: false })
      setKbList(data ?? [])
    },
    {
      refreshDeps: [],
    },
  )

  const { run: fetchDocuments, loading: docLoading } = useRequest(
    async (kbId?: number) => {
      if (!kbId) return []
      const { data } = await api.repository.listDocuments({ kbId }, { errorToast: false })
      return data ?? []
    },
    {
      manual: true,
      onSuccess(data) {
        setDocuments(data ?? [])
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `文档列表获取失败：${detail}` : '文档列表获取失败')
      },
    },
  )

  useEffect(() => {
    if (!kbList.length) return
    if (selectedKbId) {
      fetchDocuments(selectedKbId)
      return
    }
    const initKb = kbList.find((kb) => !kb.is_ephemeral) || kbList[0]
    if (initKb) {
      setSelectedKbId(initKb.id)
      fetchDocuments(initKb.id)
    }
  }, [kbList, selectedKbId, fetchDocuments])

  const {
    run: runPreview,
    loading: previewLoading,
  } = useRequest(
    async () => {
      if (!selectedKbId) {
        throw new Error('请选择知识库')
      }
      if (!query.trim()) {
        throw new Error('请输入查询内容')
      }
      const payload = {
        kb_id: selectedKbId,
        query: query.trim(),
        top_k: topK,
        session_id: sessionId || undefined,
        focus_doc_ids: selectedDocIds.length ? selectedDocIds : undefined,
        index_mode: indexMode,
      }
      const { data } = await api.debug.getRetrievalPreview(payload, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setResult(data)
        message.success('检索调试数据已刷新')
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `检索失败：${detail}` : '检索失败')
      },
    },
  )

  const handleChangeKb = useCallback(
    (kbId: number) => {
      setSelectedKbId(kbId)
      setSelectedDocIds([])
      setResult(null)
      fetchDocuments(kbId)
    },
    [fetchDocuments],
  )

  const chunkColumns: ColumnsType<ChunkRecord> = useMemo(
    () => [
      {
        title: 'Chunk ID',
        dataIndex: 'chunk_id',
        width: 160,
        render: (value: string | undefined) => value || '-',
      },
      {
        title: '文档',
        dataIndex: 'document_id',
        width: 80,
        render: (value: number | undefined) => value ?? '-',
      },
      {
        title: '页码',
        dataIndex: 'page',
        width: 60,
        render: (value: number | undefined) => (value ?? '-') as any,
      },
      {
        title: '得分',
        dataIndex: 'score',
        width: 80,
        render: (value: number | undefined) =>
          typeof value === 'number' ? value.toFixed(4) : '-',
      },
      {
        title: '类型',
        dataIndex: 'element_type',
        width: 100,
        render: (value: string | undefined) =>
          value ? <Tag color="geekblue">{value}</Tag> : <Tag>unknown</Tag>,
      },
      {
        title: '内容摘录',
        dataIndex: 'text_preview',
        ellipsis: true,
        render: (value: string | undefined) => (value ? value : '-'),
      },
    ],
    [],
  )

  const finalChunks: ChunkRecord[] = useMemo(() => {
    if (!result?.final_chunks?.length) return []
    return result.final_chunks.map((chunk, idx) => ({
      ...chunk,
      key: chunk.chunk_id || String(idx),
    }))
  }, [result])

  const rrfCandidates: ChunkRecord[] = useMemo(() => {
    if (!result?.rrf_candidates?.length) return []
    return result.rrf_candidates.map((chunk, idx) => ({
      ...chunk,
      key: chunk.chunk_id || `rrf-${idx}`,
    }))
  }, [result])

  const mmrChunks: ChunkRecord[] = useMemo(() => {
    if (!result?.mmr_chunks?.length) return []
    return result.mmr_chunks.map((chunk, idx) => ({
      ...chunk,
      key: chunk.chunk_id || `mmr-${idx}`,
    }))
  }, [result])

  const rerankCandidates: ChunkRecord[] = useMemo(() => {
    if (!result?.rerank_candidates?.length) return []
    return result.rerank_candidates.map((chunk, idx) => ({
      ...chunk,
      key: chunk.chunk_id || `rerank-candidate-${idx}`,
    }))
  }, [result])

  const rerankedFinalChunks: ChunkRecord[] = useMemo(() => {
    if (!result?.final_chunks?.length || !result?.rerank_enabled) return []
    // 如果启用了精排，final_chunks应该是精排后的结果
    return result.final_chunks.map((chunk, idx) => ({
      ...chunk,
      key: chunk.chunk_id || `reranked-final-${idx}`,
      // 如果有对应的精排分数，使用精排分数作为score
      score: result.rerank_scores && idx < result.rerank_scores.length 
        ? result.rerank_scores[idx] 
        : chunk.score,
    }))
  }, [result])

  const pathTabs = useMemo(() => {
    if (!result?.path_samples?.length) return []
    return result.path_samples.map((sample) => ({
      key: sample.path_id,
      label: `${sample.label} | ${sample.query_tag} | ${sample.source ?? 'unknown'}`,
      children: (
        <Table
          size="small"
          bordered
          pagination={false}
          columns={chunkColumns}
          dataSource={sample.hits.map((hit, idx) => ({ ...hit, key: `${sample.path_id}-${idx}` }))}
        />
      ),
    }))
  }, [result?.path_samples, chunkColumns])

  const handleCopy = useCallback(async (text: string, successMsg?: string) => {
    if (!text) {
      message.warning('没有可复制的文本')
      return
    }
    const tryClipboard = async () => {
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text)
          return true
        }
      } catch {
        return false
      }
      return false
    }

    const fallbackCopy = () => {
      try {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        textarea.style.left = '-9999px'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        const succeeded = document.execCommand('copy')
        document.body.removeChild(textarea)
        return succeeded
      } catch {
        return false
      }
    }

    const success = (await tryClipboard()) || fallbackCopy()
    if (success) {
      if (successMsg) message.success(successMsg)
    } else {
      message.error('复制失败，请手动选择文本复制')
    }
  }, [])

  const totalPathHits = useMemo(() => {
    if (!result?.path_stats) return 0
    return Object.values(result.path_stats).reduce(
      (sum, count) => sum + (typeof count === 'number' ? count : 0),
      0,
    )
  }, [result?.path_stats])

  return (
    <div className={styles['retrieval-debug-page']}>
      <Card className={styles['controls-card']} title="调试参数">
        <Space wrap size="large">
          <div className={styles['control-field']}>
            <div className={styles['control-label']}>知识库</div>
            <Select
              value={selectedKbId}
              style={{ width: 220 }}
              onChange={handleChangeKb}
              loading={kbLoading}
              placeholder="请选择知识库"
            >
              {kbList.map((kb) => (
                <Option key={kb.id} value={kb.id}>
                  {kb.name}
                </Option>
              ))}
            </Select>
          </div>
          <div className={styles['control-field']}>
            <div className={styles['control-label']}>聚焦文档（可选）</div>
            <Select
              mode="multiple"
              allowClear
              maxTagCount={2}
              style={{ width: 320 }}
              placeholder="可选择特定文档 ID"
              loading={docLoading}
              value={selectedDocIds}
              onChange={(values: number[]) => setSelectedDocIds(values)}
            >
              {documents.map((doc) => (
                <Option key={doc.id} value={doc.id}>
                  {doc.title}
                </Option>
              ))}
            </Select>
          </div>
          <div className={styles['control-field']}>
            <div className={styles['control-label']}>Top K</div>
            <InputNumber
              min={1}
              max={50}
              value={topK}
              onChange={(value) => setTopK(value || 1)}
            />
          </div>
          <div className={styles['control-field']}>
            <div className={styles['control-label']}>Session ID（可选）</div>
            <Input
              placeholder="session_xxx"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
              style={{ width: 200 }}
            />
          </div>
          <div className={styles['control-field']}>
            <div className={styles['control-label']}>索引模式</div>
            <Select
              value={indexMode}
              style={{ width: 160 }}
              onChange={(value) => setIndexMode(value)}
            >
              <Option value="auto">auto</Option>
              <Option value="global_only">global_only</Option>
              <Option value="session_only">session_only</Option>
              <Option value="hybrid">hybrid</Option>
            </Select>
          </div>
        </Space>
        <Input.TextArea
          className={styles['query-input']}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="请输入调试问题"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className={styles['action-row']}>
          <Button type="primary" onClick={runPreview} loading={previewLoading}>
            拉取检索结果
          </Button>
        </div>
      </Card>

      <Spin spinning={previewLoading}>
        {result ? (
          <>
            <Card className={styles['summary-card']} title="检索摘要">
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="索引模式">
                  {result.index_mode || 'auto'}
                </Descriptions.Item>
                <Descriptions.Item label="使用索引">
                  {result.indices_used?.length
                    ? result.indices_used.join(', ')
                    : '默认'}
                </Descriptions.Item>
                <Descriptions.Item label="候选路径数">
                  {Object.keys(result.path_stats || {}).length}
                </Descriptions.Item>
                <Descriptions.Item label="路径召回总数">
                  {totalPathHits}
                </Descriptions.Item>
                <Descriptions.Item label="RRF 候选数">
                  {result.rrf_candidates_count ?? rrfCandidates.length}
                  {result.rrf_candidates_count && result.rrf_candidates_count !== rrfCandidates.length && (
                    <span style={{ color: '#8b95a8', fontSize: '12px', marginLeft: '4px' }}>
                      (预览: {rrfCandidates.length})
                    </span>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="MMR 输出数">
                  {result.mmr_output_count ?? result.rerank_top_k ?? mmrChunks.length}
                </Descriptions.Item>
                <Descriptions.Item label="精排状态">
                  {result.rerank_enabled ? (
                    <Tag color="green">已启用</Tag>
                  ) : (
                    <Tag color="default">未启用</Tag>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="精排候选数">
                  {result.rerank_candidates?.length || 0}
                </Descriptions.Item>
                <Descriptions.Item label="最终 Chunk 数">
                  {result.final_chunks.length}
                </Descriptions.Item>
                <Descriptions.Item label="Prompt 总字符">{result.prompt_total_chars}</Descriptions.Item>
                <Descriptions.Item label="Context 字符">{result.prompt_context_chars}</Descriptions.Item>
              </Descriptions>
            </Card>

            {/* 两阶段排序流程图 */}
            <Card className={styles['flow-card']} title="两阶段排序流程">
              <div className={styles['retrieval-flow']}>
                {/* 多路径检索阶段 */}
                <div className={styles['flow-stage']}>
                  <div className={styles['stage-header']}>
                    <Tag color="cyan">检索阶段</Tag>
                    <span className={styles['stage-title']}>多路径检索</span>
                  </div>
                  <div className={styles['stage-content']}>
                    <div className={styles['chunk-count']}>
                      <span className={styles['count-value']}>{totalPathHits}</span>
                    </div>
                    <div className={styles['stage-description-inline']}>
                      <span className={styles['desc-note']}>（可能有重复的chunk）</span>
                    </div>
                  </div>
                </div>

                <div className={styles['flow-arrow']}>
                  <span className={styles['arrow-icon']}>→</span>
                </div>

                {/* RRF 融合阶段 */}
                <div className={styles['flow-stage']}>
                  <div className={styles['stage-header']}>
                    <Tag color="blue">阶段1：粗排</Tag>
                    <span className={styles['stage-title']}>RRF 融合</span>
                  </div>
                  <div className={styles['stage-content']}>
                    <div className={styles['chunk-count']}>
                      <span className={styles['count-value']}>
                        {result.rrf_candidates_count ?? rrfCandidates.length}
                      </span>
                    </div>
                    <div className={styles['stage-description-inline']}>
                      <span className={styles['desc-note']}>（去重 + 融合分数 + 重新排序）</span>
                    </div>
                  </div>
                </div>

                <div className={styles['flow-arrow']}>
                  <span className={styles['arrow-icon']}>→</span>
                </div>

                {/* MMR 多样性选择阶段 */}
                <div className={styles['flow-stage']}>
                  <div className={styles['stage-header']}>
                    <Tag color="purple">阶段1：粗排</Tag>
                    <span className={styles['stage-title']}>MMR 多样性选择</span>
                  </div>
                  <div className={styles['stage-content']}>
                    <div className={styles['chunk-count']}>
                      <span className={styles['count-value']}>
                        {result.mmr_output_count ?? result.rerank_top_k ?? mmrChunks.length}
                      </span>
                    </div>
                    <div className={styles['stage-description-inline']}>
                      <span className={styles['desc-note']}>（过滤，选择多样性好的）</span>
                    </div>
                  </div>
                </div>

                <div className={styles['flow-arrow']}>
                  <span className={styles['arrow-icon']}>→</span>
                </div>

                {/* Cross-Encoder 精排阶段（最终输出） */}
                <div className={styles['flow-stage']}>
                  <div className={styles['stage-header']}>
                    <Tag color="green">阶段2：精排</Tag>
                    <span className={styles['stage-title']}>Cross-Encoder</span>
                  </div>
                  <div className={styles['stage-content']}>
                    <div className={styles['chunk-count']}>
                      <span className={styles['count-value']}>{result.final_chunks.length}</span>
                    </div>
                    <div className={styles['stage-description-inline']}>
                      <span className={styles['desc-note']}>（过滤，选择最相关的）</span>
                      {result.rerank_scores && result.rerank_scores.length > 0 && (
                        <>
                          <span className={styles['score-separator']}>|</span>
                          <span>最高分: {Math.max(...result.rerank_scores).toFixed(4)}</span>
                          <span className={styles['score-separator']}>|</span>
                          <span>平均分: {(result.rerank_scores.reduce((a, b) => a + b, 0) / result.rerank_scores.length).toFixed(4)}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </Card>

            <Card title="查询变体" className={styles['panel-card']}>
              {result.variants?.length ? (
                <div className={styles['variants-list']}>
                  {result.variants.map((item) => (
                    <Card size="small" key={item.tag} className={styles['variant-card']}>
                      <div className={styles['variant-header']}>
                        <Tag color={item.synthetic ? 'purple' : 'blue'}>{item.tag}</Tag>
                        {item.language && <Tag>{item.language}</Tag>}
                      </div>
                      <Paragraph copyable={{ text: item.text }}>{item.text}</Paragraph>
                    </Card>
                  ))}
                </div>
              ) : (
                <Empty description="暂无查询变体" />
              )}
            </Card>

            <Card title="路径样本与召回" className={styles['panel-card']}>
              {pathTabs.length ? (
                <Tabs items={pathTabs} />
              ) : (
                <Empty description="暂无召回结果" />
              )}
            </Card>

            <Card title="融合与精排阶段" className={styles['panel-card']}>
              {rrfCandidates.length || mmrChunks.length || rerankCandidates.length ? (
                <Tabs
                  items={[
                    {
                      key: 'rrf',
                      label: `RRF 候选 (${rrfCandidates.length})`,
                      children: rrfCandidates.length ? (
                        <Table
                          columns={chunkColumns}
                          dataSource={rrfCandidates}
                          size="small"
                          bordered
                          pagination={false}
                        />
                      ) : (
                        <Empty description="暂无 RRF 数据" />
                      ),
                    },
                    {
                      key: 'mmr',
                      label: `MMR 结果 (${mmrChunks.length})`,
                      children: mmrChunks.length ? (
                        <Table
                          columns={chunkColumns}
                          dataSource={mmrChunks}
                          size="small"
                          bordered
                          pagination={false}
                        />
                      ) : (
                        <Empty description="暂无 MMR 数据" />
                      ),
                    },
                    {
                      key: 'rerank',
                      label: (
                        <span>
                          精排阶段{' '}
                          {result?.rerank_enabled ? (
                            <Tag color="green">已启用</Tag>
                          ) : (
                            <Tag color="default">未启用</Tag>
                          )}
                          {rerankCandidates.length > 0 && ` (${rerankCandidates.length})`}
                        </span>
                      ),
                      children: result?.rerank_enabled ? (
                        <div>
                          {rerankCandidates.length > 0 ? (
                            <>
                              <div style={{ marginBottom: 16 }}>
                                <Space>
                                  <span>精排前候选数: <strong>{rerankCandidates.length}</strong></span>
                                  {result.rerank_scores && result.rerank_scores.length > 0 && (
                                    <>
                                      <span>|</span>
                                      <span>最高分: <strong>{Math.max(...result.rerank_scores).toFixed(4)}</strong></span>
                                      <span>最低分: <strong>{Math.min(...result.rerank_scores).toFixed(4)}</strong></span>
                                      <span>|</span>
                                      <span>平均分: <strong>{(result.rerank_scores.reduce((a, b) => a + b, 0) / result.rerank_scores.length).toFixed(4)}</strong></span>
                                    </>
                                  )}
                                </Space>
                              </div>
                              <Table
                                columns={chunkColumns}
                                dataSource={rerankCandidates}
                                size="small"
                                bordered
                                pagination={false}
                                title={() => '精排前的候选chunks（输入到精排模型）'}
                              />
                            </>
                          ) : (
                            <Empty description="暂无精排候选数据" />
                          )}
                          {rerankedFinalChunks.length > 0 && (
                            <div style={{ marginTop: 24 }}>
                              <Table
                                columns={chunkColumns}
                                dataSource={rerankedFinalChunks}
                                size="small"
                                bordered
                                pagination={false}
                                title={() => '精排后的最终chunks（按精排分数排序）'}
                              />
                            </div>
                          )}
                        </div>
                      ) : (
                        <Empty description="精排未启用或精排失败" />
                      ),
                    },
                  ]}
                />
              ) : (
                <Empty description="暂无融合过程数据" />
              )}
            </Card>

            <Card
              title={
                <Space>
                  <span>最终 Chunk 列表</span>
                  <Button
                    size="small"
                    onClick={() =>
                      handleCopy(
                        finalChunks.map((chunk) => chunk.text_preview ?? '').join('\n\n'),
                        '已复制最终 chunk 文本',
                      )
                    }
                  >
                    复制全部
                  </Button>
                </Space>
              }
              className={styles['panel-card']}
            >
              {finalChunks.length ? (
                <Table
                  columns={chunkColumns}
                  dataSource={finalChunks}
                  rowKey="key"
                  size="small"
                  bordered
                  pagination={{ pageSize: 10 }}
                />
              ) : (
                <Empty description="暂无数据" />
              )}
            </Card>

            <Card title="LLM Prompt 片段" className={styles['panel-card']}>
              {result.prompt_sections?.length ? (
                <div className={styles['prompt-section-list']}>
                  {result.prompt_sections.map((section, idx) => (
                    <Card
                      size="small"
                      key={`${section.role}-${idx}`}
                      className={styles['prompt-card']}
                      extra={
                        <Button
                          type="link"
                          size="small"
                          onClick={() => handleCopy(section.content, '已复制该片段')}
                        >
                          复制
                        </Button>
                      }
                      title={`${section.role} · ${section.length} chars`}
                    >
                      <Paragraph className={styles['prompt-text']}>{section.content}</Paragraph>
                    </Card>
                  ))}
                </div>
              ) : (
                <Empty description="暂无 Prompt 信息" />
              )}
            </Card>
          </>
        ) : (
          <Card>
            <Empty description="请先选择知识库并拉取调试数据" />
          </Card>
        )}
      </Spin>
    </div>
  )
}

