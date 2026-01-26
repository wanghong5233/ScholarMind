import * as api from '@/api'
import type {
  DeepResearchCitation,
  IdeaGenerationRequest,
  IdeaGenerationResponse,
  IdeaGenerationRunMeta,
} from '@/api/deepResearch'
import Markdown from '@/components/markdown'
import { useRequest } from 'ahooks'
import {
  Button,
  Card,
  Divider,
  Empty,
  Input,
  InputNumber,
  List,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useState } from 'react'
import styles from './index.module.scss'

const { Text } = Typography

const STATUS_COLORS: Record<string, string> = {
  running: 'processing',
  completed: 'green',
  failed: 'red',
}

function getStatusColor(status?: string) {
  if (!status) return 'default'
  return STATUS_COLORS[status.toLowerCase()] || 'default'
}

export default function IdeaGenerationPage() {
  const [topic, setTopic] = useState('')
  const [ideaCount, setIdeaCount] = useState<number | null>(5)
  const [language, setLanguage] = useState('')
  const [constraints, setConstraints] = useState<string[]>([])
  const [constraintInput, setConstraintInput] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [topK, setTopK] = useState<number | null>(null)
  const [indexMode, setIndexMode] = useState('')

  const [result, setResult] = useState<IdeaGenerationResponse | null>(null)
  const [selectedMeta, setSelectedMeta] = useState<IdeaGenerationRunMeta | null>(null)
  const [runList, setRunList] = useState<IdeaGenerationRunMeta[]>([])

  const { run: refreshRuns, loading: listLoading } = useRequest(
    async () => {
      const { data } = await api.deepResearch.listIdeaGenerationRuns({ errorToast: false })
      return data?.items ?? []
    },
    {
      manual: true,
      onSuccess(items) {
        setRunList(items ?? [])
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `获取历史记录失败：${detail}` : '获取历史记录失败')
      },
    },
  )

  const { runAsync: runIdeaGeneration, loading: runLoading } = useRequest(
    async (payload: IdeaGenerationRequest) => {
      const { data } = await api.deepResearch.runIdeaGeneration(payload, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setResult(data)
        setSelectedMeta(null)
        message.success('想法生成完成')
        refreshRuns()
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `想法生成失败：${detail}` : '想法生成失败')
      },
    },
  )

  const { runAsync: loadRunDetail, loading: detailLoading } = useRequest(
    async (ideaId: string) => {
      const { data } = await api.deepResearch.getIdeaGenerationRun(ideaId, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setSelectedMeta(data.meta)
        setResult(data.payload)
        message.success('已载入历史记录')
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `载入失败：${detail}` : '载入失败')
      },
    },
  )

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  const sortedRuns = useMemo(() => {
    return [...runList].sort((a, b) => {
      const left = a.started_at || a.finished_at || ''
      const right = b.started_at || b.finished_at || ''
      return right.localeCompare(left)
    })
  }, [runList])

  const currentMeta = useMemo(() => {
    if (selectedMeta) return selectedMeta
    if (!result?.idea_id) return null
    return runList.find((item) => item.idea_id === result.idea_id) ?? null
  }, [result?.idea_id, runList, selectedMeta])

  const handleAddConstraint = useCallback(() => {
    const value = constraintInput.trim()
    if (!value) return
    if (constraints.includes(value)) {
      message.warning('该约束已存在')
      return
    }
    setConstraints((prev) => [...prev, value])
    setConstraintInput('')
  }, [constraintInput, constraints])

  const handleRemoveConstraint = useCallback((value: string) => {
    setConstraints((prev) => prev.filter((item) => item !== value))
  }, [])

  const handleSubmit = useCallback(async () => {
    const trimmedTopic = topic.trim()
    if (!trimmedTopic) {
      message.warning('请输入研究主题')
      return
    }

    const payload: IdeaGenerationRequest = {
      topic: trimmedTopic,
      idea_count: ideaCount ?? undefined,
      language: language.trim() || undefined,
      constraints,
      session_id: sessionId.trim() || undefined,
      top_k: topK ?? undefined,
      index_mode: indexMode.trim() || undefined,
    }

    await runIdeaGeneration(payload)
  }, [constraints, ideaCount, indexMode, language, runIdeaGeneration, sessionId, topK, topic])

  const renderCitations = useCallback((items: DeepResearchCitation[] = []) => {
    if (!items.length) {
      return <Text type="secondary">暂无引用</Text>
    }
    return (
      <List
        size="small"
        dataSource={items}
        renderItem={(item, index) => (
          <List.Item>
            <Space direction="vertical" size={4}>
              <Space size={8}>
                <Tag color="blue">#{item.ref_number ?? index + 1}</Tag>
                <Text strong>{item.title || item.url || '未命名引用'}</Text>
                {item.source_type ? <Tag>{item.source_type}</Tag> : null}
              </Space>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.url}
                </a>
              ) : null}
              {item.snippet ? <Text type="secondary">{item.snippet}</Text> : null}
            </Space>
          </List.Item>
        )}
      />
    )
  }, [])

  return (
    <div className={styles.container}>
      <div className={styles.side}>
        <Card title="研究想法生成" className={styles.section}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Input.TextArea
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="输入研究主题"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
            <Space wrap>
              <InputNumber
                min={1}
                max={20}
                value={ideaCount ?? undefined}
                onChange={(value) => setIdeaCount(value ?? null)}
                placeholder="想法数量"
              />
              <Input
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                placeholder="输出语言（可选）"
              />
            </Space>
            <Space wrap>
              <Input
                value={sessionId}
                onChange={(event) => setSessionId(event.target.value)}
                placeholder="会话 ID（可选）"
              />
              <InputNumber
                min={1}
                max={50}
                value={topK ?? undefined}
                onChange={(value) => setTopK(value ?? null)}
                placeholder="top_k"
              />
              <Input
                value={indexMode}
                onChange={(event) => setIndexMode(event.target.value)}
                placeholder="索引模式（可选）"
              />
            </Space>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={constraintInput}
                onChange={(event) => setConstraintInput(event.target.value)}
                placeholder="添加约束条件"
                onPressEnter={handleAddConstraint}
              />
              <Button onClick={handleAddConstraint}>添加</Button>
            </Space.Compact>
            {constraints.length ? (
              <div className={styles.tagWrap}>
                {constraints.map((item) => (
                  <Tag key={item} closable onClose={() => handleRemoveConstraint(item)}>
                    {item}
                  </Tag>
                ))}
              </div>
            ) : (
              <Text type="secondary">暂无约束条件</Text>
            )}
            <Button type="primary" onClick={handleSubmit} loading={runLoading}>
              生成想法
            </Button>
          </Space>
        </Card>

        <Card
          title="历史记录"
          className={styles.section}
          extra={
            <Button size="small" onClick={() => refreshRuns()} loading={listLoading}>
              刷新
            </Button>
          }
        >
          {sortedRuns.length ? (
            <List<IdeaGenerationRunMeta>
              size="small"
              dataSource={sortedRuns}
              loading={listLoading}
              renderItem={(item) => (
                <List.Item
                  key={item.idea_id}
                  className={styles.listItem}
                  onClick={() => loadRunDetail(item.idea_id)}
                >
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Space size={8} wrap>
                      <Tag color={getStatusColor(item.status)}>{item.status}</Tag>
                      <Text strong>{item.topic}</Text>
                    </Space>
                    <Space size={8} wrap>
                      <Text type="secondary">ID: {item.idea_id}</Text>
                      {item.started_at ? (
                        <Text type="secondary">
                          开始：{dayjs(item.started_at).format('YYYY-MM-DD HH:mm')}
                        </Text>
                      ) : null}
                      {item.finished_at ? (
                        <Text type="secondary">
                          完成：{dayjs(item.finished_at).format('YYYY-MM-DD HH:mm')}
                        </Text>
                      ) : null}
                    </Space>
                  </Space>
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无历史记录" />
          )}
          {detailLoading ? <Text type="secondary">载入中...</Text> : null}
        </Card>
      </div>

      <div className={styles.content}>
        <Card title="生成结果" className={styles.section}>
          {!result ? (
            <Empty description="暂无输出结果" />
          ) : (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <div className={styles.metaRow}>
                <Text type="secondary">ID: {result.idea_id}</Text>
                {currentMeta?.status ? (
                  <Tag color={getStatusColor(currentMeta.status)}>{currentMeta.status}</Tag>
                ) : null}
                {currentMeta?.duration_seconds ? (
                  <Text type="secondary">
                    耗时：{currentMeta.duration_seconds.toFixed(1)}s
                  </Text>
                ) : null}
              </div>
              <Markdown value={result.ideas_markdown} />
              <Divider />
              <div>
                <Text strong>引用</Text>
                {renderCitations(result.citations)}
              </div>
              <Divider />
              <div>
                <Text strong>Trace</Text>
                <pre className={styles.traceBox}>
                  {JSON.stringify(result.trace ?? {}, null, 2)}
                </pre>
              </div>
            </Space>
          )}
        </Card>
      </div>
    </div>
  )
}
