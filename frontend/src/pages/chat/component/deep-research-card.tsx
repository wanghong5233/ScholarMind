import type { ProgressEvent } from '@/api/deepResearch'
import { Button, Card, List, Progress, Space, Spin, Steps, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useEffect, useState } from 'react'
import { localizeProgressMessage } from './deep-research-observability'
import styles from './deep-research-card.module.scss'

const { Text } = Typography

const STAGE_ORDER = ['planning', 'researching', 'reporting'] as const

const STATUS_LABEL: Record<string, string> = {
  plan: '计划确认',
  queued: '排队中',
  running: '进行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const STATUS_COLOR: Record<string, string> = {
  plan: 'blue',
  queued: 'orange',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  cancelled: 'default',
}

const TOOL_LABEL_MAP: Record<string, string> = {
  'rag.ask': 'RAG Ask',
  'rag.compare': 'RAG Compare',
  'web.search': 'Web Search',
  'web.open_page': 'Web Open Page',
  'web.find_in_page': 'Web Find In Page',
  'paper.search': 'Paper Search',
  'code.exec': 'Code Exec',
}

function parseServerTimestampMs(value?: string) {
  const text = String(value || '').trim()
  if (!text) return null
  const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(text)
  const normalized = hasTimezone ? text : `${text}Z`
  const parsed = Date.parse(normalized)
  if (Number.isFinite(parsed)) return parsed
  const fallback = Date.parse(text)
  if (Number.isFinite(fallback)) return fallback
  return null
}

function extractToolLabel(event: ProgressEvent) {
  const payload = (event.payload || {}) as Record<string, unknown>
  const toolName = String(payload.tool || '').trim()
  const message = String(event.message || '').trim()
  if (message.toLowerCase().startsWith('tool started:')) {
    return message.split(':').slice(1).join(':').trim() || '工具'
  }
  if (toolName) return TOOL_LABEL_MAP[toolName] || toolName
  return '工具'
}

function extractToolKey(event: ProgressEvent) {
  const payload = (event.payload || {}) as Record<string, unknown>
  const toolName = String(payload.tool || '').trim()
  if (toolName) return toolName
  return extractToolLabel(event)
}

function resolveActiveTool(progress?: ProgressEvent[]) {
  const events = Array.isArray(progress) ? progress : []
  let active: { key: string; label: string; startedAt?: string } | null = null
  events.forEach((event) => {
    const eventType = String(event.event_type || '')
      .trim()
      .toLowerCase()
    if (eventType === 'tool.started') {
      active = {
        key: extractToolKey(event),
        label: extractToolLabel(event),
        startedAt: event.timestamp,
      }
      return
    }
    if (eventType !== 'tool.completed' && eventType !== 'tool.failed') return
    const finishedKey = extractToolKey(event)
    if (!active) return
    if (!finishedKey || finishedKey === active.key) {
      active = null
    }
  })
  return active
}

function resolveStageStartedAt(progress: ProgressEvent[] | undefined, stage: string | undefined) {
  const target = String(stage || '').trim().toLowerCase()
  if (!target) return undefined
  const events = Array.isArray(progress) ? progress : []
  let lastStage = ''
  let startedAt: string | undefined
  events.forEach((event) => {
    const current = String(event.stage || '').trim().toLowerCase()
    if (!current) return
    if (current !== lastStage) {
      lastStage = current
      if (current === target) {
        startedAt = event.timestamp
      }
      return
    }
    if (!startedAt && current === target) {
      startedAt = event.timestamp
    }
  })
  return startedAt
}

function resolveStageIndex(stage?: string) {
  if (!stage) return 0
  const index = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number])
  return index === -1 ? 0 : index
}

function formatTimestamp(value?: string) {
  const ms = parseServerTimestampMs(value)
  if (ms === null) return '-'
  return dayjs(ms).format('HH:mm:ss')
}

function formatDurationMs(startMs: number | null, endMs: number | null) {
  if (startMs === null || endMs === null) return '-'
  const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remain = seconds % 60
  if (hours > 0) return `${hours}小时 ${minutes}分 ${remain}秒`
  return `${minutes}分 ${remain}秒`
}

function computeEstimateMinutes(
  planCount: number,
  request: API.DeepResearchCardState['request'],
) {
  const iterations = request.max_iterations ?? 4
  const parallel = request.max_parallel ?? 1
  const toolWeight =
    (request.use_web_search ? 0.6 : 0) +
    (request.use_paper_search ? 0.8 : 0) +
    (request.use_code_exec ? 0.4 : 0)
  const base = planCount * (1.2 + toolWeight) + iterations * 1.5
  const adjusted = base / Math.max(1, parallel)
  return Math.max(3, Math.round(adjusted))
}

function getLatestEvent(progress?: ProgressEvent[]) {
  if (!progress || !progress.length) return undefined
  return progress[progress.length - 1]
}

export default function DeepResearchCard(props: {
  item: API.ChatItem
  onConfirm?: (item: API.ChatItem) => void
  onCancel?: (item: API.ChatItem) => void
  onEdit?: (item: API.ChatItem) => void
  onRetryPlan?: (item: API.ChatItem) => void
  onOpenProcess?: (item: API.ChatItem) => void
  onOpenWorkspace?: (item: API.ChatItem) => void
  onExportReport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onCopyReport?: (item: API.ChatItem) => void
  onSaveToNotebook?: (item: API.ChatItem) => void
  onInsertSummary?: (item: API.ChatItem, summary: string) => void
}) {
  const {
    item,
    onConfirm,
    onCancel,
    onEdit,
    onRetryPlan,
    onOpenProcess,
    onExportReport,
    onCopyReport,
    onSaveToNotebook,
  } = props
  const data = item.deepResearch
  const isActiveRun = data?.status === 'running' || data?.status === 'queued'
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (!isActiveRun) return
    const timer = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isActiveRun, data?.researchId])
  if (!data) return null

  const latestEvent = getLatestEvent(data.progress)
  const currentStage = data.lastStage || latestEvent?.stage
  const stageIndex = resolveStageIndex(currentStage)
  const planItems = data.plan?.items ?? []
  const estimateMinutes = computeEstimateMinutes(planItems.length || 1, data.request)
  const blockStats = data.blockStats

  const statusLabel = STATUS_LABEL[data.status] || data.status
  const statusColor = STATUS_COLOR[data.status] || 'default'
  const statusMessage = localizeProgressMessage(data.statusMessage || latestEvent?.message || '-')
  const progressPercent =
    data.status === 'completed'
      ? 100
      : currentStage === 'reporting'
      ? 90
      : currentStage === 'researching'
      ? 60
      : 25

  const latestProgressAt = data.updatedAt || latestEvent?.timestamp
  const startedAt = data.progress?.[0]?.timestamp
  const stageStartedAt = resolveStageStartedAt(data.progress, currentStage)
  const startedMs = parseServerTimestampMs(startedAt)
  const stageStartedMs = parseServerTimestampMs(stageStartedAt)
  const latestProgressMs = parseServerTimestampMs(latestProgressAt)
  const durationEndMs = isActiveRun ? nowMs : latestProgressMs
  const durationLabel = formatDurationMs(startedMs, durationEndMs)
  const stageDurationLabel = formatDurationMs(stageStartedMs, durationEndMs)
  const activeTool = resolveActiveTool(data.progress)
  const activeToolDuration = formatDurationMs(
    parseServerTimestampMs(activeTool?.startedAt),
    durationEndMs,
  )
  // Prefer the actual finalized citations array length as the ground truth.
  // blockStats.citations reflects intermediate per-tool-call counts (e.g. 120 raw
  // RAG results) and can be misleadingly large before the final filter runs.
  const finalCitationCount = data.citations?.length ?? 0
  const citationCountRaw =
    finalCitationCount > 0
      ? finalCitationCount
      : typeof blockStats?.citations === 'number'
      ? blockStats.citations
      : 0
  const citationCount = Math.max(0, Math.floor(Number(citationCountRaw) || 0))
  const credibility = citationCount >= 6 ? '高' : citationCount >= 2 ? '中' : citationCount > 0 ? '低' : '未知'
  const statusMessageLower = statusMessage.toLowerCase()
  const activeToolLabelLower = activeTool?.label?.toLowerCase() || ''
  const statusMessageHasToolLabel =
    !!activeToolLabelLower && statusMessageLower.includes(activeToolLabelLower)
  const runLoadingText =
    activeTool && activeToolDuration && activeToolDuration !== '-'
      ? `${statusMessage || '研究进行中...'}${
          statusMessageHasToolLabel ? '' : ` · ${activeTool.label}`
        } · ${activeToolDuration}`
      : stageDurationLabel !== '-'
      ? `${statusMessage || '研究进行中...'} · ${stageDurationLabel}`
      : durationLabel !== '-'
      ? `${statusMessage || '研究进行中...'} · ${durationLabel}`
      : statusMessage || '研究进行中...'

  return (
    <Card className={styles['deep-research-card']} bordered={false}>
      <div className={styles['header']}>
        <div>
          <div className={styles['title']}>🔬 深度研究</div>
          <Text type="secondary">{data.topic}</Text>
        </div>
        <Tag color={statusColor}>{statusLabel}</Tag>
      </div>

      <div className={styles['meta']}>
        <Text type="secondary">
          系统自动执行 · 计划项：{planItems.length || '-'} · 预计耗时 ~{estimateMinutes} 分钟
        </Text>
      </div>

      {data.status === 'plan' ? (
        <div className={styles['section']}>
          {data.planLoading ? (
            <div className={styles['plan-loading']}>
              <Spin size="small" />
              <span>{statusMessage || '正在生成研究计划...'}</span>
            </div>
          ) : data.planError ? (
            <div className={styles['plan-error']}>
              <Text type="danger">计划生成失败：{data.planError}</Text>
            </div>
          ) : null}

          {!data.planLoading && planItems.length > 0 ? (
            <List
              size="small"
              dataSource={planItems}
              renderItem={(plan) => (
                <List.Item className={styles['plan-item']}>
                  <div className={styles['plan-item__title']}>{plan.title}</div>
                  {String(plan.question || '').trim() &&
                  String(plan.question || '').trim() !== String(plan.title || '').trim() ? (
                    <div className={styles['plan-item__meta']}>{plan.question}</div>
                  ) : null}
                </List.Item>
              )}
            />
          ) : null}

          <Text type="secondary">计划执行细节与流式预览请在右侧研究过程面板查看。</Text>

          <div className={styles['actions']}>
            <Space>
              <Button
                type="primary"
                onClick={() => onConfirm?.(item)}
                disabled={data.planLoading}
              >
                确认开始
              </Button>
              <Button onClick={() => onEdit?.(item)}>修改计划</Button>
              <Button danger onClick={() => onCancel?.(item)}>
                取消
              </Button>
              {data.planError ? (
                <Button onClick={() => onRetryPlan?.(item)}>重试计划</Button>
              ) : null}
              <Button onClick={() => onOpenProcess?.(item)}>查看过程</Button>
            </Space>
          </div>
        </div>
      ) : null}

      {data.status !== 'plan' ? (
        <div className={styles['section']}>
          <Steps
            current={stageIndex}
            items={[
              { title: 'Planning' },
              { title: 'Researching' },
              { title: 'Reporting' },
            ]}
            size="small"
          />
          <div className={styles['progress']}>
            <Progress percent={progressPercent} size="small" />
            {isActiveRun ? (
              <div className={styles['run-loading']}>
                <Spin size="small" />
                <span>{runLoadingText}</span>
              </div>
            ) : null}
            <div className={styles['progress-meta']}>
              <span>当前阶段：{currentStage || '-'}</span>
              <span>{statusMessage}</span>
              <span>运行时长：{durationLabel}</span>
              <span>最近更新：{formatTimestamp(latestProgressAt)}</span>
              {blockStats?.total ? (
                <span>
                  进度：{blockStats.completed ?? 0}/{blockStats.total}
                  {blockStats.pending ? ` · 待处理 ${blockStats.pending}` : ''}
                </span>
              ) : null}
              {data.status === 'completed' ? (
                <span>
                  可信度：{credibility}
                  {citationCount > 0 ? ` · 引用 ${citationCount}` : ''}
                </span>
              ) : null}
              {data.status === 'completed' && citationCount > 0 && citationCount < 2 ? (
                <Text type="danger">可信度预警：有效引用少于 2 条，建议补充证据后重跑。</Text>
              ) : null}
            </div>
          </div>

          {data.queuePosition !== undefined && data.queuePosition !== null ? (
            <Text type="secondary">
              排队位置：{data.queuePosition} · 运行中 {data.activeRuns ?? '-'} · 等待中{' '}
              {data.pendingRuns ?? '-'}
            </Text>
          ) : null}

          <Text type="secondary">
            主对话保持简洁。实时动作流、证据链、流式预览和最终报告都在右侧过程面板查看。
          </Text>

          <div className={styles['actions']}>
            <Space>
              <Button onClick={() => onOpenProcess?.(item)}>查看过程</Button>
              {data.status === 'completed' && data.report?.report_markdown ? (
                <>
                  <Button onClick={() => onSaveToNotebook?.(item)}>导入笔记本</Button>
                  <Button onClick={() => onExportReport?.(item, 'markdown')}>导出 Markdown</Button>
                  <Button onClick={() => onCopyReport?.(item)}>复制报告</Button>
                </>
              ) : null}
              {isActiveRun ? (
                <>
                  <Button danger onClick={() => onCancel?.(item)}>
                    取消任务
                  </Button>
                </>
              ) : null}
            </Space>
          </div>
        </div>
      ) : null}
    </Card>
  )
}
