import * as api from '@/api'
import type {
  DocumentParseBlock,
  DocumentParsePreviewResponse,
  KnowledgeBase,
  RepositoryDocument,
} from '@/api/repository'
import { useRequest } from 'ahooks'
import {
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Tabs,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { MouseEvent, useCallback, useEffect, useMemo, useState } from 'react'
import styles from './index.module.scss'

const { Option } = Select
const { Paragraph, Text } = Typography

const ELEMENT_TYPE_COLORS: Record<string, string> = {
  paragraph: 'blue',
  figure_summary: 'purple',
  table_json: 'volcano',
  table_struct: 'volcano',
  equation_latex: 'geekblue',
  metadata: 'gold',
}

type BlockTableItem = DocumentParseBlock & { key: string; textLength: number }

export default function ParseDebugPage() {
  const [selectedKbId, setSelectedKbId] = useState<number>()
  const [selectedDocId, setSelectedDocId] = useState<number>()
  const [documents, setDocuments] = useState<RepositoryDocument[]>([])
  const [preview, setPreview] = useState<DocumentParsePreviewResponse | null>(null)
  const [filterKeyword, setFilterKeyword] = useState('')
  const [activeStageKey, setActiveStageKey] = useState<string>()
  const [copyRange, setCopyRange] = useState<{ start: number; end: number }>({ start: 1, end: 10 })

  const stages = useMemo(() => {
    if (preview?.stages?.length) return preview.stages
    if (!preview) return []
    return [
      {
        key: 'parser',
        title: '解析输出',
        description: '解析器返回的原始块（兼容旧版本）。',
        stats: preview.stats,
        blocks: preview.blocks ?? [],
      },
    ]
  }, [preview])

  const currentStage = useMemo(() => {
    if (!stages.length) return null
    if (activeStageKey) {
      return stages.find((stage) => stage.key === activeStageKey) || stages[0]
    }
    return stages[0]
  }, [stages, activeStageKey])

  useEffect(() => {
    setFilterKeyword('')
    if (currentStage?.blocks?.length) {
      setCopyRange({
        start: 1,
        end: Math.min(10, currentStage.blocks.length),
      })
    } else {
      setCopyRange({ start: 1, end: 10 })
    }
  }, [currentStage?.key])

  const { data: kbList = [], loading: kbLoading } = useRequest(async () => {
    const { data } = await api.repository.listKnowledgeBases({ errorToast: false })
    return data ?? []
  })

  const {
    loading: docLoading,
    run: fetchDocuments,
  } = useRequest(
    async (kbId?: number) => {
      if (!kbId) return []
      const { data } = await api.repository.listDocuments(
        { kbId },
        {
          errorToast: false,
        },
      )
      return data ?? []
    },
    {
      manual: true,
      onSuccess(data) {
        setDocuments(data ?? [])
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `文档列表获取失败：${detail}` : '文档列表获取失败')
      },
    },
  )

  const {
    loading: previewLoading,
    run: fetchPreview,
  } = useRequest(
    async (params: { kbId: number; docId: number }) => {
      const { data } = await api.repository.getDocumentParsePreview(params, {
        errorToast: false,
      })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setPreview(data)
        if (data?.stages?.length) {
          setActiveStageKey(data.stages[0].key)
        } else {
          setActiveStageKey('parser')
        }
        message.success('解析结果已刷新')
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `解析失败：${detail}` : '解析失败')
      },
    },
  )

  useEffect(() => {
    if (!kbList.length || selectedKbId) return
    const initialKb = kbList.find((kb) => !kb.is_ephemeral) || kbList[0]
    if (initialKb) {
      setSelectedKbId(initialKb.id)
      fetchDocuments(initialKb.id)
    }
  }, [kbList, selectedKbId, fetchDocuments])

  const handleChangeKb = useCallback(
    (kbId: number) => {
      setSelectedKbId(kbId)
      setSelectedDocId(undefined)
      setPreview(null)
      setFilterKeyword('')
      fetchDocuments(kbId)
    },
    [fetchDocuments],
  )

  const handleChangeDoc = useCallback((docId: number) => {
    setSelectedDocId(docId)
    setPreview(null)
    setFilterKeyword('')
  }, [])

  const handlePreview = useCallback(() => {
    if (!selectedKbId || !selectedDocId) {
      message.warning('请选择知识库和文档后再拉取解析结果')
      return
    }
    fetchPreview({ kbId: selectedKbId, docId: selectedDocId })
  }, [selectedKbId, selectedDocId, fetchPreview])

  const copyText = useCallback(async (text: string, successMsg: string, errorMsg: string) => {
    if (!text) {
      message.warning('没有可复制的文本')
      return false
    }
    const tryClipboard = async () => {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        return true
      }
      return false
    }
    const fallbackCopy = () => {
      const textarea = document.createElement('textarea')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.left = '-9999px'
      textarea.value = text
      document.body.appendChild(textarea)
      textarea.focus()
      textarea.select()
      textarea.setSelectionRange(0, textarea.value.length)
      let ok = false
      try {
        ok = document.execCommand('copy')
      } catch (err) {
        ok = false
      } finally {
        document.body.removeChild(textarea)
      }
      return ok
    }
    try {
      const ok = await tryClipboard()
      if (ok) {
        message.success(successMsg)
        return true
      }
    } catch (_) {
      // ignore
    }
    try {
      const ok = fallbackCopy()
      if (ok) {
        message.success(successMsg)
        return true
      }
    } catch (_) {
      // ignore
    }
    message.error(errorMsg)
    return false
  }, [])

  const handleCopyText = useCallback(
    async (text: string, event?: MouseEvent<HTMLElement>) => {
      event?.stopPropagation()
      event?.preventDefault()
      await copyText(text, '段落已复制', '复制失败，请检查浏览器权限或手动复制')
    },
    [copyText],
  )

  const handleCopyAllChunks = useCallback(async () => {
    if (!currentStage || !currentStage.blocks?.length) {
      message.warning('暂无可复制的解析内容')
      return
    }
    const total = currentStage.blocks.length
    const start = Math.max(1, Math.min(copyRange.start || 1, total))
    const end = Math.max(start, Math.min(copyRange.end || start, total))
    const targetBlocks = currentStage.blocks.slice(start - 1, end)
    const parts = targetBlocks.map((block) => {
      const meta: string[] = []
      if (block.element_type) meta.push(`type=${block.element_type}`)
      if (typeof block.page === 'number') meta.push(`page=${block.page}`)
      const header = [`#${block.index}`, meta.length ? `(${meta.join(', ')})` : '']
        .join(' ')
        .trim()
      const sanitizedMeta = { ...(block.metadata ?? {}) }
      delete sanitizedMeta.vector
      delete sanitizedMeta.embedding
      delete sanitizedMeta.pre_embedding
      const metaLine = `${header} metadata=${JSON.stringify(sanitizedMeta)}`
      const text = block.text ?? ''
      return `${metaLine}\n${text}`.trimEnd()
    })
    const content = parts.join('\n\n')
    await copyText(
      content,
      `已复制第 ${start}-${end} 个 chunk，可直接粘贴给大模型`,
      '复制失败，请检查浏览器权限或手动复制',
    )
  }, [currentStage, copyText, copyRange])

  const selectedKb: KnowledgeBase | undefined = useMemo(
    () => kbList.find((kb) => kb.id === selectedKbId),
    [kbList, selectedKbId],
  )

  const selectedDoc: RepositoryDocument | undefined = useMemo(
    () => documents.find((doc) => doc.id === selectedDocId),
    [documents, selectedDocId],
  )

  const filteredBlocks: BlockTableItem[] = useMemo(() => {
    if (!currentStage?.blocks?.length) return []
    const keyword = filterKeyword.trim().toLowerCase()
    return currentStage.blocks
      .filter((block) => {
        if (!keyword) return true
        const typeMatch = block.element_type?.toLowerCase().includes(keyword)
        const textMatch = (block.text || '').toLowerCase().includes(keyword)
        const pageMatch = block.page?.toString().includes(keyword)
        return typeMatch || textMatch || pageMatch
      })
      .map((block) => ({
        ...block,
        key: `${block.index}`,
        textLength: (block.text || '').length,
      }))
  }, [currentStage, filterKeyword])

  const blockColumns: ColumnsType<BlockTableItem> = useMemo(
    () => [
      {
        title: '序号',
        dataIndex: 'index',
        width: 80,
        sorter: (a, b) => a.index - b.index,
        defaultSortOrder: 'ascend',
      },
      {
        title: '类型 / 页码',
        dataIndex: 'element_type',
        width: 200,
        render(value: string | undefined, record) {
          const type = value || 'unknown'
          const color = ELEMENT_TYPE_COLORS[type] || 'default'
          return (
            <Space direction="vertical" size={4}>
              <Tag color={color}>{type}</Tag>
              <Text type="secondary">第 {record.page ?? '-'} 页</Text>
            </Space>
          )
        },
      },
      {
        title: '字符数',
        dataIndex: 'textLength',
        width: 100,
        sorter: (a, b) => a.textLength - b.textLength,
      },
      {
        title: '文本预览',
        dataIndex: 'text',
        render(value: string | undefined) {
          if (!value?.trim()) {
            return <Text type="secondary">（空文本）</Text>
          }
          const tooltipContent = (
            <div className={styles['tooltip-content']}>
              <div className={styles['tooltip-actions']}>
                <Button
                  type="link"
                  size="small"
                  onClick={(event) => handleCopyText(value, event)}
                >
                  复制全文
                </Button>
              </div>
              <div className={styles['tooltip-text']}>{value}</div>
            </div>
          )
          return (
            <Paragraph
              ellipsis={{
                rows: 2,
                tooltip: {
                  title: tooltipContent,
                  overlayStyle: { maxWidth: 1440 },
                  overlayInnerStyle: {
                    maxWidth: '100%',
                    fontSize: 14,
                    lineHeight: 1.66,
                    padding: 12,
                  },
                },
              }}
              copyable={{ text: value }}
            >
              {value}
            </Paragraph>
          )
        },
      },
    ],
    [],
  )

  return (
    <div className={styles['parse-debug-page']}>
      <Card className={styles['controls-card']} title="解析调试面板">
        <div className={styles['controls-row']}>
          <Select
            style={{ minWidth: 240 }}
            placeholder="选择知识库"
            value={selectedKbId}
            loading={kbLoading}
            onChange={handleChangeKb}
            allowClear={false}
          >
            {kbList.map((kb) => (
              <Option key={kb.id} value={kb.id}>
                {kb.name || `知识库 ${kb.id}`}
              </Option>
            ))}
          </Select>

          <Select
            style={{ minWidth: 280 }}
            placeholder="选择文档"
            value={selectedDocId}
            loading={docLoading}
            onChange={handleChangeDoc}
            disabled={!selectedKbId}
            showSearch
            optionFilterProp="children"
          >
            {documents.map((doc) => (
              <Option key={doc.id} value={doc.id}>
                {doc.title || `文档 ${doc.id}`}
              </Option>
            ))}
          </Select>

          <Button type="primary" onClick={handlePreview} loading={previewLoading}>
            拉取解析结果
          </Button>
        </div>

        {(selectedKb || selectedDoc || preview) && (
          <div className={styles['meta-row']}>
            {selectedKb && (
              <Text type="secondary">
                • 当前知识库：{selectedKb.name || `#${selectedKb.id}`}
              </Text>
            )}
            {selectedDoc && (
              <Text type="secondary">
                • 当前文档：{selectedDoc.title || `#${selectedDoc.id}`}
              </Text>
            )}
            {preview?.parser_order?.length ? (
              <Space size={8} wrap>
                <Text type="secondary">• 解析链：</Text>
                <div className={styles['parser-tags']}>
                  {preview.parser_order.map((item, idx) => (
                    <Tag key={`${item}-${idx}`} color="blue">
                      {item || '-'}
                    </Tag>
                  ))}
                </div>
              </Space>
            ) : null}
          </div>
        )}
      </Card>

      <Card className={styles['content-card']} title="解析结果">
        {previewLoading ? (
          <div className={styles.placeholder}>
            <Spin />
          </div>
        ) : !preview ? (
          <div className={styles.placeholder}>
            <Empty description="请选择文档并点击“拉取解析结果”查看解析内容" />
          </div>
        ) : (
          <>
            {stages.length > 1 ? (
              <Tabs
                className={styles['stage-tabs']}
                activeKey={currentStage?.key}
                onChange={(key) => setActiveStageKey(key)}
                items={stages.map((stage) => ({
                  key: stage.key,
                  label: stage.title,
                }))}
              />
            ) : null}
            {currentStage?.description ? (
              <div className={styles['stage-description']}>{currentStage.description}</div>
            ) : null}
            <div className={styles['stats-grid']}>
              <div className={styles['stat-box']}>
                <div className={styles.label}>总块数</div>
                <div className={styles.value}>{currentStage?.stats.total_blocks ?? 0}</div>
              </div>
              <div className={styles['stat-box']}>
                <div className={styles.label}>非空块</div>
                <div className={styles.value}>{currentStage?.stats.nonempty_blocks ?? 0}</div>
              </div>
              <div className={styles['stat-box']}>
                <div className={styles.label}>字符总数</div>
                <div className={styles.value}>{currentStage?.stats.total_chars ?? 0}</div>
              </div>
              <div className={styles['stat-box']}>
                <div className={styles.label}>解析器数量</div>
                <div className={styles.value}>
                  {Object.keys(currentStage?.stats.parser_engines || {}).length}
                </div>
              </div>
            </div>

            <div className={styles['element-types']}>
              {Object.entries(currentStage?.stats.element_types || {}).map(([type, count]) => (
                <Tag key={type} color={ELEMENT_TYPE_COLORS[type] || 'default'}>
                  {type}: {count}
                </Tag>
              ))}
            </div>

            <div className={styles['table-toolbar']}>
              <Input.Search
                placeholder="按类型、文本或页码过滤"
                allowClear
                value={filterKeyword}
                onChange={(event) => setFilterKeyword(event.target.value)}
                style={{ maxWidth: 360 }}
              />
              <Space size={12} wrap>
                <Space size={4}>
                  <Text type="secondary">复制范围：</Text>
                  <InputNumber
                    min={1}
                    max={currentStage?.blocks?.length || 1}
                    value={copyRange.start}
                    onChange={(value) =>
                      setCopyRange((prev) => ({
                        start: value || 1,
                        end: Math.max(value || 1, prev.end),
                      }))
                    }
                    size="small"
                  />
                  <span>-</span>
                  <InputNumber
                    min={1}
                    max={currentStage?.blocks?.length || 1}
                    value={copyRange.end}
                    onChange={(value) =>
                      setCopyRange((prev) => ({
                        start: Math.min(prev.start, value || prev.start),
                        end: value || prev.start,
                      }))
                    }
                    size="small"
                  />
                </Space>
                <Text type="secondary">
                  共 {filteredBlocks.length} / {currentStage?.blocks?.length ?? 0} 个块
                </Text>
                <Button onClick={handleCopyAllChunks} disabled={!currentStage?.blocks?.length}>
                  复制当前阶段
                </Button>
              </Space>
            </div>

            <Table<BlockTableItem>
              rowKey="key"
              columns={blockColumns}
              dataSource={filteredBlocks}
              pagination={{ pageSize: 10, showSizeChanger: true }}
              expandable={{
                expandedRowRender(record) {
                  return (
                    <div className={styles['block-detail']}>
                      <Paragraph copyable>
                        {record.text?.trim() ? record.text : '（空文本）'}
                      </Paragraph>
                      <Text type="secondary">Metadata</Text>
                      <pre>{JSON.stringify(record.metadata ?? {}, null, 2)}</pre>
                    </div>
                  )
                },
              }}
            />
          </>
        )}
      </Card>
    </div>
  )
}

