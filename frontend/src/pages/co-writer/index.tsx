import * as api from '@/api'
import type {
  CoWriterRequest,
  CoWriterResponse,
  CoWriterRunMeta,
  CoWriterTask,
  DeepResearchCitation,
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
  Select,
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

const TASK_LABELS: Record<CoWriterTask, string> = {
  rewrite: '改写',
  expand: '扩写',
  shorten: '精简',
  annotate: '注释',
}

function getStatusColor(status?: string) {
  if (!status) return 'default'
  return STATUS_COLORS[status.toLowerCase()] || 'default'
}

export default function CoWriterPage() {
  const [task, setTask] = useState<CoWriterTask>('rewrite')
  const [text, setText] = useState('')
  const [language, setLanguage] = useState('')
  const [instructions, setInstructions] = useState('')
  const [tone, setTone] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [topK, setTopK] = useState<number | null>(null)
  const [indexMode, setIndexMode] = useState('')

  const [result, setResult] = useState<CoWriterResponse | null>(null)
  const [selectedMeta, setSelectedMeta] = useState<CoWriterRunMeta | null>(null)
  const [runList, setRunList] = useState<CoWriterRunMeta[]>([])

  const { run: refreshRuns, loading: listLoading } = useRequest(
    async () => {
      const { data } = await api.deepResearch.listCoWriterRuns({ errorToast: false })
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

  const { runAsync: runCoWriter, loading: runLoading } = useRequest(
    async (payload: CoWriterRequest) => {
      const { data } = await api.deepResearch.runCoWriter(payload, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setResult(data)
        setSelectedMeta(null)
        message.success('交互式写作完成')
        refreshRuns()
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `交互式写作失败：${detail}` : '交互式写作失败')
      },
    },
  )

  const { runAsync: loadRunDetail, loading: detailLoading } = useRequest(
    async (operationId: string) => {
      const { data } = await api.deepResearch.getCoWriterRun(operationId, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setSelectedMeta(data.meta)
        setResult(data.payload)
        setTask(data.meta.task)
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
    if (!result?.operation_id) return null
    return runList.find((item) => item.operation_id === result.operation_id) ?? null
  }, [result?.operation_id, runList, selectedMeta])

  const handleSubmit = useCallback(async () => {
    const trimmedText = text.trim()
    if (!trimmedText) {
      message.warning('请输入待处理文本')
      return
    }

    const payload: CoWriterRequest = {
      task,
      text: trimmedText,
      language: language.trim() || undefined,
      instructions: instructions.trim() || undefined,
      tone: tone.trim() || undefined,
      session_id: sessionId.trim() || undefined,
      top_k: topK ?? undefined,
      index_mode: indexMode.trim() || undefined,
    }

    await runCoWriter(payload)
  }, [
    indexMode,
    instructions,
    language,
    runCoWriter,
    sessionId,
    task,
    text,
    tone,
    topK,
  ])

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
        <Card title="交互式想法生成" className={styles.section}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Select
              value={task}
              onChange={(value) => setTask(value)}
              options={Object.entries(TASK_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
            <Input.TextArea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="输入需要处理的文本"
              autoSize={{ minRows: 6, maxRows: 12 }}
            />
            <Input
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="附加指令（可选）"
            />
            <Space wrap>
              <Input
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                placeholder="输出语言（可选）"
              />
              <Input
                value={tone}
                onChange={(event) => setTone(event.target.value)}
                placeholder="语气风格（可选）"
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
            <Button type="primary" onClick={handleSubmit} loading={runLoading}>
              执行任务
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
            <List<CoWriterRunMeta>
              size="small"
              dataSource={sortedRuns}
              loading={listLoading}
              renderItem={(item) => (
                <List.Item
                  key={item.operation_id}
                  className={styles.listItem}
                  onClick={() => loadRunDetail(item.operation_id)}
                >
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Space size={8} wrap>
                      <Tag color={getStatusColor(item.status)}>{item.status}</Tag>
                      <Tag>{TASK_LABELS[item.task] ?? item.task}</Tag>
                      <Text strong>交互式写作</Text>
                    </Space>
                    <Space size={8} wrap>
                      <Text type="secondary">ID: {item.operation_id}</Text>
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
                <Text type="secondary">ID: {result.operation_id}</Text>
                {currentMeta?.status ? (
                  <Tag color={getStatusColor(currentMeta.status)}>{currentMeta.status}</Tag>
                ) : null}
                {currentMeta?.task ? <Tag>{TASK_LABELS[currentMeta.task]}</Tag> : null}
                {currentMeta?.duration_seconds ? (
                  <Text type="secondary">
                    耗时：{currentMeta.duration_seconds.toFixed(1)}s
                  </Text>
                ) : null}
              </div>
              <Markdown value={result.result_markdown} />
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
