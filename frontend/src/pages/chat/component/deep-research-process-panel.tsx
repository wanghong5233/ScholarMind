import type { DeepResearchBlockEvidence, ProgressEvent, TopicBlock } from '@/api/deepResearch'
import Markdown from '@/components/markdown'
import {
  Button,
  Collapse,
  Descriptions,
  Empty,
  List,
  Select,
  Space,
  Steps,
  Tag,
  Timeline,
  Typography,
} from 'antd'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ACTION_TYPE_LABEL,
  localizeProgressMessage,
  resolveActionType,
  resolveActionTypeColor,
  resolveAgentLabel,
  resolveEventTypeLabel,
  resolveNextActionHint,
  summarizeProgressPayload,
} from './deep-research-observability'
import styles from './deep-research-process-panel.module.scss'

const { Text, Paragraph } = Typography

const STAGE_ORDER = ['planning', 'researching', 'reporting'] as const

const STAGE_LABEL: Record<string, string> = {
  planning: '规划',
  researching: '调研',
  reporting: '成文',
}

const REPORT_PREVIEW_MAX_CHARS = 14000
const REPORT_CITATIONS_RENDER_LIMIT = 160
const REPORT_MARKDOWN_HEAVY_RENDER_THRESHOLD = 80000

function resolveStageIndex(stage?: string) {
  if (!stage) return 0
  const index = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number])
  return index === -1 ? 0 : index
}

function resolveStageFromStatus(status?: string) {
  const value = String(status || '').trim().toLowerCase()
  if (value === 'plan' || value === 'queued') return 'planning'
  if (value === 'running') return 'researching'
  if (value === 'completed' || value === 'failed' || value === 'cancelled') return 'reporting'
  return undefined
}

function resolveStatusMessageFallback(status?: string) {
  const value = String(status || '').trim().toLowerCase()
  if (value === 'completed') return '报告已完成'
  if (value === 'failed') return '任务执行失败'
  if (value === 'cancelled') return '任务已取消'
  if (value === 'queued') return '任务排队中'
  if (value === 'running') return '研究进行中'
  return '-'
}

function formatTimestamp(value?: string) {
  const ms = parseServerTimestampMs(value)
  if (ms === null) return '-'
  return dayjs(ms).format('HH:mm:ss')
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

function formatDurationMs(startMs: number | null, endMs: number | null) {
  if (startMs === null || endMs === null) return '-'
  const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const remain = seconds % 60
  return `${minutes}m ${remain}s`
}

function formatDurationSeconds(value?: number) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return '-'
  return formatDurationMs(0, Math.floor(seconds * 1000))
}

function blockStatusColor(status?: string) {
  const value = String(status || '').toLowerCase()
  if (value === 'completed') return 'green'
  if (value === 'failed') return 'red'
  if (value === 'researching') return 'processing'
  if (value === 'pending') return 'default'
  return 'default'
}

function resolveCredibility(citationCount: number) {
  const safe = Math.max(0, Math.floor(Number(citationCount) || 0))
  if (safe >= 6) return { label: '高', color: 'green', lowConfidence: false }
  if (safe >= 2) return { label: '中', color: 'gold', lowConfidence: false }
  if (safe > 0) return { label: '低', color: 'red', lowConfidence: true }
  return { label: '未知', color: 'default', lowConfidence: false }
}

function renderDecisionSummary(decision: Record<string, unknown>) {
  const parts: string[] = []
  if (typeof decision.sufficient === 'boolean') {
    parts.push(`结论充分性: ${decision.sufficient ? '是' : '否'}`)
  }
  if (typeof decision.should_compare === 'boolean') {
    parts.push(`是否对比: ${decision.should_compare ? '是' : '否'}`)
  }
  const tools = Array.isArray(decision.tool_calls)
    ? decision.tool_calls
        .map((item) => {
          if (typeof item === 'string') return item
          if (item && typeof item === 'object' && 'name' in item) {
            return String((item as { name?: string }).name || '')
          }
          return ''
        })
        .filter(Boolean)
    : []
  if (tools.length) parts.push(`工具选择: ${tools.join(', ')}`)
  if (typeof decision.rationale === 'string' && decision.rationale.trim()) {
    parts.push(`理由: ${decision.rationale.trim()}`)
  }
  return parts.join(' · ') || '无额外信息'
}

function runStateLabel(eventType?: string) {
  const value = String(eventType || '')
  if (value.endsWith('.started')) return 'started'
  if (value.endsWith('.completed')) return 'completed'
  if (value.endsWith('.failed')) return 'failed'
  return ''
}

function normalizeStage(stage?: string) {
  const value = String(stage || '').toLowerCase()
  if (value === 'planning' || value === 'researching' || value === 'reporting') {
    return value as (typeof STAGE_ORDER)[number]
  }
  return 'researching'
}

function buildEventId(event: ProgressEvent, index: number) {
  return `${event.timestamp || 't'}-${event.event_type || ''}-${event.message || ''}-${index}`
}

function extractBlockId(payload?: Record<string, unknown>) {
  if (!payload || typeof payload !== 'object') return ''
  const value = payload.block_id
  if (typeof value === 'string' && value.trim()) return value.trim()
  return ''
}

function downloadText(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function csvEscape(value: string) {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

function buildActionStreamMarkdown(events: ProgressEvent[]) {
  const lines = ['# DeepResearch 实时动作流', '']
  events.forEach((event) => {
    const actionType = resolveActionType(event)
    const message = localizeProgressMessage(event.message || '')
    const eventLabel = resolveEventTypeLabel(event)
    const agent = resolveAgentLabel(event)
    const time = formatTimestamp(event.timestamp)
    const payloadText = summarizeProgressPayload(event.payload || {})
    lines.push(`- [${time}] ${eventLabel} / ${ACTION_TYPE_LABEL[actionType]} / ${agent}`)
    lines.push(`  - ${message}`)
    if (payloadText) {
      lines.push(`  - ${payloadText}`)
    }
  })
  return lines.join('\n')
}

function buildActionStreamCsv(events: ProgressEvent[]) {
  const header = [
    'time',
    'stage',
    'event_type',
    'action_type',
    'agent',
    'message',
    'payload',
    'block_id',
  ]
  const rows = events.map((event) => {
    const payload = (event.payload || {}) as Record<string, unknown>
    return [
      formatTimestamp(event.timestamp),
      String(event.stage || ''),
      String(event.event_type || ''),
      ACTION_TYPE_LABEL[resolveActionType(event)],
      resolveAgentLabel(event),
      localizeProgressMessage(event.message || ''),
      summarizeProgressPayload(payload),
      extractBlockId(payload),
    ]
      .map((value) => csvEscape(value))
      .join(',')
  })
  return [header.join(','), ...rows].join('\n')
}

export default function DeepResearchProcessPanel(props: {
  item: API.ChatItem | null
  blocks: TopicBlock[]
  selectedBlockId: string | null
  evidence: DeepResearchBlockEvidence | null
  evidenceLoading?: boolean
  onSelectBlock?: (blockId: string) => void
  onRefreshSnapshot?: () => void
  onRefreshEvidence?: (blockId?: string) => void
  onOpenWorkspace?: (item: API.ChatItem) => void
  onExportReport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
}) {
  const {
    item,
    blocks,
    selectedBlockId,
    evidence,
    evidenceLoading,
    onSelectBlock,
    onRefreshSnapshot,
    onRefreshEvidence,
    onOpenWorkspace,
    onExportReport,
  } = props

  const [newEventIds, setNewEventIds] = useState<string[]>([])
  const timelineStreamRef = useRef<HTMLDivElement | null>(null)
  const lastProgressCountRef = useRef(0)
  const newEventTimerMapRef = useRef<Map<string, number>>(new Map())

  const deepResearch = item?.deepResearch
  const isActiveRun =
    deepResearch?.status === 'running' || deepResearch?.status === 'queued'
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    if (!deepResearch || !isActiveRun) return
    const timer = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [deepResearch?.researchId, isActiveRun])

  useEffect(() => {
    setNewEventIds([])
    newEventTimerMapRef.current.forEach((timer) => window.clearTimeout(timer))
    newEventTimerMapRef.current.clear()
    lastProgressCountRef.current = deepResearch?.progress?.length || 0
  }, [deepResearch?.researchId])

  useEffect(
    () => () => {
      newEventTimerMapRef.current.forEach((timer) => window.clearTimeout(timer))
      newEventTimerMapRef.current.clear()
    },
    [],
  )

  if (!item || !deepResearch) {
    return <Empty description="暂无深度研究任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  const progressEvents = deepResearch.progress || []

  useEffect(() => {
    const previousCount = lastProgressCountRef.current
    const currentCount = progressEvents.length
    if (currentCount > previousCount) {
      const appendedIds = progressEvents
        .slice(previousCount)
        .map((event, offset) => buildEventId(event, previousCount + offset))
      if (appendedIds.length) {
        setNewEventIds((prev) => Array.from(new Set([...prev, ...appendedIds])).slice(-40))
      }
      appendedIds.forEach((eventId) => {
        const oldTimer = newEventTimerMapRef.current.get(eventId)
        if (oldTimer) {
          window.clearTimeout(oldTimer)
        }
        const timer = window.setTimeout(() => {
          setNewEventIds((prev) => prev.filter((id) => id !== eventId))
          newEventTimerMapRef.current.delete(eventId)
        }, 3600)
        newEventTimerMapRef.current.set(eventId, timer)
      })
    }
    lastProgressCountRef.current = currentCount
  }, [progressEvents])

  const latestEvent = progressEvents.length ? progressEvents[progressEvents.length - 1] : undefined
  const researchId = String(deepResearch.researchId || '').trim()
  const currentStage =
    deepResearch.lastStage || latestEvent?.stage || resolveStageFromStatus(deepResearch.status)
  const stageIndex = resolveStageIndex(currentStage)
  const startedAt =
    deepResearch.status === 'plan'
      ? progressEvents[0]?.timestamp || deepResearch.startedAt
      : deepResearch.executionStartedAt ||
        deepResearch.startedAt ||
        progressEvents[0]?.timestamp
  const lastAt = deepResearch.updatedAt || deepResearch.finishedAt || latestEvent?.timestamp
  const startedMs = parseServerTimestampMs(startedAt)
  const durationEndMs = isActiveRun ? nowMs : parseServerTimestampMs(deepResearch.finishedAt || lastAt)
  const durationByTimeline = formatDurationMs(startedMs, durationEndMs)
  const durationLabel =
    durationByTimeline !== '-'
      ? durationByTimeline
      : formatDurationSeconds(deepResearch.durationSeconds)
  const nextActionHint = progressEvents.length
    ? resolveNextActionHint(progressEvents)
    : deepResearch.status === 'completed'
    ? '报告已完成，可查看与导出'
    : deepResearch.status === 'failed'
    ? '任务已失败，可查看失败原因后重试'
    : deepResearch.status === 'cancelled'
    ? '任务已取消，可重新发起研究'
    : '等待下一步动作...'
  const currentActionText = localizeProgressMessage(
    latestEvent?.message ||
      deepResearch.statusMessage ||
      resolveStatusMessageFallback(deepResearch.status),
  )
  const blockStats = deepResearch.blockStats || {}
  const requestMetadata =
    deepResearch.request?.metadata && typeof deepResearch.request.metadata === 'object'
      ? (deepResearch.request.metadata as Record<string, unknown>)
      : {}
  const requestPresetKey = String(requestMetadata.deep_research_preset || '').trim().toLowerCase()
  const requestPresetLabel =
    requestPresetKey === 'quick' ? '快速' : requestPresetKey === 'deep' ? '深度' : '标准'
  const reportMarkdown = String(deepResearch.report?.report_markdown || '').trim()
  const rawDraftMarkdown = String(deepResearch.report?.draft_markdown || '').trim()
  const reportPreviewTruncated = rawDraftMarkdown.length > REPORT_PREVIEW_MAX_CHARS
  const draftMarkdown = reportPreviewTruncated
    ? rawDraftMarkdown.slice(-REPORT_PREVIEW_MAX_CHARS)
    : rawDraftMarkdown
  const reportPreviewText = draftMarkdown
  const reportCitations = Array.isArray(deepResearch.citations) ? deepResearch.citations : []
  const credibility = resolveCredibility(reportCitations.length)
  const showFinalCredibility = deepResearch.status === 'completed'
  const visibleReportCitations = reportCitations.slice(0, REPORT_CITATIONS_RENDER_LIMIT)
  const reportCitationsTruncated = reportCitations.length > REPORT_CITATIONS_RENDER_LIMIT
  const usePlainReportRender = reportMarkdown.length > REPORT_MARKDOWN_HEAVY_RENDER_THRESHOLD
  const showReportPreview =
    !!reportPreviewText &&
    !reportMarkdown &&
    (deepResearch.status === 'running' ||
      deepResearch.status === 'queued' ||
      currentStage === 'reporting')

  const blockOptions = useMemo(
    () =>
      blocks
        .filter((block) => block.depth > 0)
        .map((block) => ({
          label: `${block.title} · ${block.status} · ${block.iterations}/${block.max_iterations}`,
          value: block.block_id,
        })),
    [blocks],
  )

  const activeBlock = useMemo(
    () => blocks.find((block) => block.block_id === selectedBlockId) || null,
    [blocks, selectedBlockId],
  )

  const progressEntries = useMemo(
    () =>
      progressEvents.map((event, index) => ({
        event,
        index,
        id: buildEventId(event, index),
      })),
    [progressEvents],
  )

  useEffect(() => {
    if (!timelineStreamRef.current) return
    timelineStreamRef.current.scrollTop = timelineStreamRef.current.scrollHeight
  }, [progressEntries.length])

  const handleExportMarkdown = () => {
    if (!progressEvents.length) return
    const runId = researchId || 'run'
    const stamp = dayjs().format('YYYYMMDD-HHmmss')
    const filename = `deep-research-actions-${runId}-${stamp}.md`
    const markdown = buildActionStreamMarkdown(progressEvents)
    downloadText(filename, markdown, 'text/markdown;charset=utf-8')
  }

  const handleExportCsv = () => {
    if (!progressEvents.length) return
    const runId = researchId || 'run'
    const stamp = dayjs().format('YYYYMMDD-HHmmss')
    const filename = `deep-research-actions-${runId}-${stamp}.csv`
    const csv = buildActionStreamCsv(progressEvents)
    downloadText(filename, csv, 'text/csv;charset=utf-8')
  }

  const realtimeTimelineItems = useMemo(() => {
    const visible = progressEntries.slice(-120)
    const stageCountMap = new Map<string, number>()
    visible.forEach((entry) => {
      const stage = normalizeStage(entry.event.stage)
      stageCountMap.set(stage, (stageCountMap.get(stage) || 0) + 1)
    })

    const items: Array<{ key: string; color: string; children: ReactNode }> = []
    let lastStage = ''
    visible.forEach((entry, idx) => {
      const event = entry.event
      const stage = normalizeStage(event.stage)
      if (stage !== lastStage) {
        lastStage = stage
        items.push({
          key: `segment-${stage}-${entry.id}`,
          color: 'gray',
          children: (
            <div className={styles['timeline-stage-segment']}>
              <Tag color="default">{STAGE_LABEL[stage] || stage}</Tag>
              <Text type="secondary">阶段分段 · {stageCountMap.get(stage) || 0} 条动作</Text>
            </div>
          ),
        })
      }
      const actionType = resolveActionType(event)
      const payloadSummary = summarizeProgressPayload(event.payload)
      const blockId = extractBlockId((event.payload || {}) as Record<string, unknown>)
      const isNew = newEventIds.includes(entry.id)
      const stateLabel = runStateLabel(event.event_type)
      const color = actionType === 'error' ? 'red' : actionType === 'decision' ? 'purple' : 'blue'
      items.push({
        key: `${entry.id}-${idx}`,
        color,
        children: (
          <div
            className={
              isNew
                ? `${styles['timeline-item']} ${styles['timeline-item--new']}`
                : styles['timeline-item']
            }
          >
            <div className={styles['timeline-item__meta']}>
              <Tag>{resolveEventTypeLabel(event)}</Tag>
              <Tag color={resolveActionTypeColor(actionType)}>{ACTION_TYPE_LABEL[actionType]}</Tag>
              <Tag>{resolveAgentLabel(event)}</Tag>
              {stateLabel ? (
                <Tag color={stateLabel.includes('failed') ? 'red' : 'default'}>
                  {stateLabel}
                </Tag>
              ) : null}
              <Text type="secondary">{formatTimestamp(event.timestamp)}</Text>
            </div>
            <div className={styles['timeline-item__text']}>
              {localizeProgressMessage(event.message)}
            </div>
            {payloadSummary ? (
              <div className={styles['timeline-item__payload']}>{payloadSummary}</div>
            ) : null}
            {blockId ? (
              <div className={styles['timeline-item__footer']}>
                <Button
                  type="link"
                  size="small"
                  className={styles['timeline-item__locate']}
                  onClick={() => {
                    onSelectBlock?.(blockId)
                    onRefreshEvidence?.(blockId)
                  }}
                >
                  定位到证据链
                </Button>
              </div>
            ) : null}
          </div>
        ),
      })
    })
    return items
  }, [newEventIds, onRefreshEvidence, onSelectBlock, progressEntries])

  const evidenceEventItems = useMemo(() => {
    const events = evidence?.progress_events || []
    return events.slice(-80).reverse().map((event, idx) => {
      const actionType = resolveActionType(event)
      return {
        key: `${event.timestamp || idx}-${idx}`,
        color: actionType === 'error' ? 'red' : 'blue',
        children: (
          <Space direction="vertical" size={2}>
            <Space size={8} wrap>
              <Tag>{resolveEventTypeLabel(event as ProgressEvent)}</Tag>
              <Tag color={resolveActionTypeColor(actionType)}>
                {ACTION_TYPE_LABEL[actionType]}
              </Tag>
              <Text type="secondary">{formatTimestamp(event.timestamp)}</Text>
            </Space>
            <Text>{localizeProgressMessage(event.message || '')}</Text>
            {summarizeProgressPayload(event.payload) ? (
              <Text type="secondary">{summarizeProgressPayload(event.payload)}</Text>
            ) : null}
          </Space>
        ),
      }
    })
  }, [evidence?.progress_events])

  return (
    <div className={styles['process-panel']}>
      <div className={styles['process-panel__toolbar']}>
        <Space size={8} wrap>
          <Tag color="blue">{deepResearch.status}</Tag>
          {currentStage ? <Tag>{STAGE_LABEL[currentStage] || currentStage}</Tag> : null}
          {lastAt ? <Text type="secondary">最近更新 {formatTimestamp(lastAt)}</Text> : null}
        </Space>
        <Space size={8}>
          {researchId ? (
            <>
              <Button
                size="small"
                onClick={() => onExportReport?.(item, 'markdown')}
              >
                下载MD
              </Button>
              <Button
                size="small"
                onClick={() => onExportReport?.(item, 'pdf')}
              >
                下载PDF
              </Button>
            </>
          ) : null}
          {researchId ? (
            <Button size="small" onClick={onRefreshSnapshot}>
              刷新快照
            </Button>
          ) : null}
          <Button size="small" onClick={() => onOpenWorkspace?.(item)}>
            打开工作区
          </Button>
        </Space>
      </div>

      <div className={styles['process-panel__summary']}>
        <Steps
          size="small"
          current={stageIndex}
          items={STAGE_ORDER.map((stage) => ({ title: STAGE_LABEL[stage] || stage }))}
        />
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="当前动作" span={2}>
            {currentActionText}
          </Descriptions.Item>
          <Descriptions.Item label="下一步动作" span={2}>
            {nextActionHint}
          </Descriptions.Item>
          <Descriptions.Item label="运行时长">
            {durationLabel}
          </Descriptions.Item>
          <Descriptions.Item label="主进度">
            {typeof blockStats.completed === 'number' || typeof blockStats.total === 'number'
              ? `${blockStats.completed ?? 0}/${blockStats.total ?? '-'}`
              : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="档位">{requestPresetLabel}</Descriptions.Item>
          <Descriptions.Item label="可信度">
            {showFinalCredibility ? (
              <Space size={8}>
                <Tag color={credibility.color}>{credibility.label}</Tag>
                <Text type="secondary">引用 {reportCitations.length}</Text>
              </Space>
            ) : (
              <Text type="secondary">报告生成后展示</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="队列信息">
            {deepResearch.queuePosition !== undefined && deepResearch.queuePosition !== null
              ? `排队 ${deepResearch.queuePosition} · 运行 ${deepResearch.activeRuns ?? '-'}`
              : '-'}
          </Descriptions.Item>
        </Descriptions>
        {showFinalCredibility && credibility.lowConfidence ? (
          <Text type="danger">可信度预警：有效引用不足 2 条，建议补充检索证据后重跑。</Text>
        ) : null}
      </div>

      {showReportPreview ? (
        <div className={styles['process-panel__draft']}>
          <div className={styles['process-panel__draft-title']}>
            <Tag color="processing">流式预览</Tag>
            <Text type="secondary">
              长文正在生成，内容会持续刷新
              {reportPreviewTruncated ? `（仅展示最新 ${REPORT_PREVIEW_MAX_CHARS} 字）` : ''}
            </Text>
          </div>
          <pre className={styles['process-panel__draft-content']}>{reportPreviewText}</pre>
        </div>
      ) : null}

      <Collapse
        className={styles['process-panel__collapse']}
        defaultActiveKey={['realtime']}
        items={[
          {
            key: 'realtime',
            label: `实时动作流 (${progressEvents.length})`,
            children: (
              <div className={styles['timeline-panel']}>
                <div className={styles['timeline-toolbar']}>
                  <div className={styles['timeline-toolbar-actions']}>
                    <Button
                      size="small"
                      disabled={!progressEvents.length}
                      onClick={handleExportMarkdown}
                    >
                      导出 Markdown
                    </Button>
                    <Button
                      size="small"
                      disabled={!progressEvents.length}
                      onClick={handleExportCsv}
                    >
                      导出 CSV
                    </Button>
                    <Button
                      size="small"
                      onClick={() => {
                        if (!timelineStreamRef.current) return
                        timelineStreamRef.current.scrollTop = timelineStreamRef.current.scrollHeight
                      }}
                    >
                      定位最新
                    </Button>
                  </div>
                </div>
                {realtimeTimelineItems.length ? (
                  <div ref={timelineStreamRef} className={styles['timeline-stream']}>
                    <Timeline items={realtimeTimelineItems} />
                  </div>
                ) : (
                  <Empty description="暂无实时动作" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </div>
            ),
          },
          {
            key: 'evidence',
            label: '可回看证据链',
            children: (
              <div className={styles['evidence-panel']}>
                <Space size={8} wrap>
                  <Select
                    value={selectedBlockId || undefined}
                    options={blockOptions}
                    placeholder="选择任务块"
                    style={{ minWidth: 320 }}
                    onChange={(value) => onSelectBlock?.(String(value))}
                    allowClear
                  />
                  <Button
                    size="small"
                    loading={evidenceLoading}
                    onClick={() => onRefreshEvidence?.(selectedBlockId || undefined)}
                  >
                    刷新证据
                  </Button>
                </Space>

                {!activeBlock ? (
                  <Empty
                    description="请选择任务块查看证据"
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                ) : (
                  <div className={styles['evidence-detail']}>
                    <Descriptions size="small" column={2}>
                      <Descriptions.Item label="Block">{activeBlock.block_id}</Descriptions.Item>
                      <Descriptions.Item label="状态">
                        <Tag color={blockStatusColor(activeBlock.status)}>
                          {activeBlock.status}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="迭代">
                        {activeBlock.iterations}/{activeBlock.max_iterations}
                      </Descriptions.Item>
                      <Descriptions.Item label="子任务">
                        {activeBlock.child_ids?.length || 0}
                      </Descriptions.Item>
                      <Descriptions.Item label="工具调用">
                        {evidence?.tool_traces?.length || activeBlock.tool_traces?.length || 0}
                      </Descriptions.Item>
                      <Descriptions.Item label="引用">
                        {evidence?.citations?.length || activeBlock.citations?.length || 0}
                      </Descriptions.Item>
                    </Descriptions>

                    <Collapse
                      className={styles['evidence-collapse']}
                      defaultActiveKey={['tools']}
                      items={[
                        {
                          key: 'decisions',
                          label: `决策 (${evidence?.decisions?.length || 0})`,
                          children: evidence?.decisions?.length ? (
                            <List
                              size="small"
                              dataSource={evidence.decisions}
                              renderItem={(decision, index) => (
                                <List.Item>
                                  <Space direction="vertical" size={2}>
                                    <Text strong>Decision {index + 1}</Text>
                                    <Text>{renderDecisionSummary(decision || {})}</Text>
                                  </Space>
                                </List.Item>
                              )}
                            />
                          ) : (
                            <Text type="secondary">暂无决策记录</Text>
                          ),
                        },
                        {
                          key: 'tools',
                          label: `工具轨迹 (${evidence?.tool_traces?.length || 0})`,
                          children: evidence?.tool_traces?.length ? (
                            <List
                              size="small"
                              dataSource={evidence.tool_traces}
                              renderItem={(trace) => (
                                <List.Item>
                                  <Space direction="vertical" size={2}>
                                    <Space size={8} wrap>
                                      <Tag color="blue">{trace.tool_type || '-'}</Tag>
                                      <Text type="secondary">
                                        {formatTimestamp(trace.timestamp)}
                                      </Text>
                                    </Space>
                                    <Text>{trace.summary || '-'}</Text>
                                    {trace.query ? (
                                      <Paragraph type="secondary" className={styles['trace-query']}>
                                        Query: {trace.query}
                                      </Paragraph>
                                    ) : null}
                                  </Space>
                                </List.Item>
                              )}
                            />
                          ) : (
                            <Text type="secondary">暂无工具轨迹</Text>
                          ),
                        },
                        {
                          key: 'citations',
                          label: `引用 (${evidence?.citation_details?.length || 0})`,
                          children: evidence?.citation_details?.length ? (
                            <List
                              size="small"
                              dataSource={evidence.citation_details}
                              renderItem={(citation) => (
                                <List.Item>
                                  <Space direction="vertical" size={2}>
                                    <Text strong>
                                      {citation.title || citation.url || citation.citation_id || '-'}
                                    </Text>
                                    {citation.snippet ? (
                                      <Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                                        {citation.snippet}
                                      </Paragraph>
                                    ) : null}
                                  </Space>
                                </List.Item>
                              )}
                            />
                          ) : (
                            <Text type="secondary">暂无引用证据</Text>
                          ),
                        },
                        {
                          key: 'events',
                          label: `块内事件 (${evidence?.progress_events?.length || 0})`,
                          children: evidenceEventItems.length ? (
                            <Timeline items={evidenceEventItems} />
                          ) : (
                            <Text type="secondary">暂无块内事件</Text>
                          ),
                        },
                      ]}
                    />
                  </div>
                )}
              </div>
            ),
          },
          {
            key: 'report',
            label: `研究报告${reportMarkdown ? '' : '（生成中）'}`,
            children: (
              <div className={styles['report-panel']}>
                {reportMarkdown ? (
                  <>
                    {usePlainReportRender ? (
                      <>
                        <Text type="secondary">
                          报告内容较长，为保证页面流畅已切换为纯文本渲染。
                        </Text>
                        <pre className={styles['process-panel__draft-content']}>{reportMarkdown}</pre>
                      </>
                    ) : (
                      <Markdown value={reportMarkdown} />
                    )}
                    {reportCitations.length ? (
                      <List
                        size="small"
                        className={styles['report-citations']}
                        dataSource={visibleReportCitations}
                        renderItem={(citation: any, idx) => (
                          <List.Item>
                            <Space direction="vertical" size={2}>
                              <Text strong>
                                [{citation?.ref_number ?? idx + 1}]{' '}
                                {citation?.title || citation?.url || citation?.citation_id || '未命名引用'}
                              </Text>
                              {citation?.snippet ? (
                                <Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                                  {citation.snippet}
                                </Paragraph>
                              ) : null}
                              {citation?.url ? (
                                <a href={citation.url} target="_blank" rel="noreferrer">
                                  查看原文
                                </a>
                              ) : null}
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : null}
                    {reportCitationsTruncated ? (
                      <Text type="secondary">
                        引文较多，仅渲染前 {REPORT_CITATIONS_RENDER_LIMIT} 条以保证页面流畅。
                      </Text>
                    ) : null}
                  </>
                ) : reportPreviewText ? (
                  <>
                    <Text type="secondary">
                      报告草稿流式预览（最终版将自动替换）
                      {reportPreviewTruncated ? ` · 仅展示最新 ${REPORT_PREVIEW_MAX_CHARS} 字` : ''}
                    </Text>
                    <pre className={styles['process-panel__draft-content']}>{reportPreviewText}</pre>
                  </>
                ) : (
                  <Empty description="报告生成后会在此展示" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}

