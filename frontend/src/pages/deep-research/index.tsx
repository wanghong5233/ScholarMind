import * as api from '@/api'
import type {
  DeepResearchCitation,
  DeepResearchRequest,
  DeepResearchResponse,
  DeepResearchSubmitResponse,
  DeepResearchQueueStatus,
  DeepResearchRunMeta,
  DeepResearchSnapshot,
  DeepResearchReportPayload,
  DeepResearchReportDetails,
  PlanItem,
  ProgressEvent,
  TopicBlock,
} from '@/api/deepResearch'
import Markdown from '@/components/markdown'
import { exportToPdf } from '@/utils/pdfExport'
import { createWorkspace, updateFileContent } from '@/api/docStudio'
import { useRequest } from 'ahooks'
import dayjs from 'dayjs'
import {
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Input,
  InputNumber,
  List,
  Progress,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tree,
  Steps,
  Timeline,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  BulbOutlined,
  ClockCircleOutlined,
  CopyOutlined,
  DownloadOutlined,
  FileTextOutlined,
  ToolOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import styles from './index.module.scss'

const { Text, Paragraph } = Typography
const STORAGE_KEY = 'deep-research-state'
const PARAMS_ANCHOR_ID = 'deep-research-params'
const PROGRESS_TAIL = 300
const PROGRESS_PAGE_LIMIT = 200
const DEFAULTS = {
  depth: 2,
  breadth: 5,
  maxParallel: 1,
  maxIterations: 4,
  topK: 6,
  indexMode: 'auto',
}

const PRESETS = {
  quick: { depth: 1, breadth: 2, maxParallel: 1, maxIterations: 2 },
  medium: { depth: 2, breadth: 5, maxParallel: 1, maxIterations: 4 },
  deep: { depth: 2, breadth: 8, maxParallel: 1, maxIterations: 7 },
}

type PresetKey = keyof typeof PRESETS | 'custom'

const PRESET_OPTIONS: { label: string; value: PresetKey; description: string }[] = [
  { label: '快速', value: 'quick', description: '轻量探索，快速收敛' },
  { label: '标准', value: 'medium', description: '平衡深度与效率' },
  { label: '深度', value: 'deep', description: '高覆盖，适合深入研究' },
  { label: '自定义', value: 'custom', description: '手动调整参数' },
]

function resolvePresetKey(params: {
  depth: number
  breadth: number
  maxParallel: number
  maxIterations: number
}): PresetKey {
  for (const [key, preset] of Object.entries(PRESETS)) {
    if (
      preset.depth === params.depth &&
      preset.breadth === params.breadth &&
      preset.maxParallel === params.maxParallel &&
      preset.maxIterations === params.maxIterations
    ) {
      return key as PresetKey
    }
  }
  return 'custom'
}

type SessionOption = {
  session_id: string
  session_name: string
  created_at: string
}

type RunListItem = DeepResearchRunMeta
type ProcessTab = 'planning' | 'researching' | 'reporting'

function formatDuration(seconds?: number) {
  if (!seconds && seconds !== 0) return '-'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
}

function parseSnippets(rawText: string): string[] {
  return rawText
    .split(/\n-{3,}\n/)
    .map((chunk) => chunk.trim())
    .filter(Boolean)
}

function safeParseJson(text: string): Record<string, any> | null {
  if (!text.trim()) return {}
  try {
    return JSON.parse(text)
  } catch (error) {
    return null
  }
}

const TOOL_TYPE_COLOR: Record<string, string> = {
  rag: 'blue',
  search: 'purple',
  compare: 'geekblue',
  code: 'orange',
  note: 'gold',
  report: 'green',
}

const TOOL_STAGE_MAP: Record<string, string> = {
  rag: 'researching',
  search: 'researching',
  compare: 'researching',
  code: 'researching',
  note: 'reporting',
  report: 'reporting',
}

const ERROR_KEYWORDS = ['error', 'failed', 'exception', '错误', '失败', '异常']

function getTraceStage(toolType: string) {
  return TOOL_STAGE_MAP[toolType] || 'researching'
}

function isTraceError(trace: any) {
  const raw = `${trace?.summary || ''} ${trace?.raw_answer || ''}`.toLowerCase()
  return ERROR_KEYWORDS.some((keyword) => raw.includes(keyword))
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

function downloadJson(filename: string, payload: unknown) {
  downloadText(filename, JSON.stringify(payload, null, 2), 'application/json;charset=utf-8')
}

function buildThoughtExportBaseName(researchId?: string, blockId?: string) {
  const safeResearchId = researchId?.trim() || 'run'
  const safeBlockId = blockId?.trim() || 'block'
  const stamp = dayjs().format('YYYYMMDD-HHmmss')
  return `deep-research-thoughts-${safeResearchId}-${safeBlockId}-${stamp}`
}

function escapeHtml(text: string) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function buildHtmlReport(markdown: string) {
  const safeContent = escapeHtml(markdown)
  return `<!doctype html>
<html lang="zh">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DeepResearch Report</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px; }
    pre { white-space: pre-wrap; line-height: 1.6; }
  </style>
</head>
<body>
  <pre>${safeContent}</pre>
</body>
</html>`
}

function buildTimelineItems(events: ProgressEvent[]) {
  const visible = events.slice(-120)
  const lastIndex = visible.length - 1
  return visible
    .map((item, idx) => ({
      color: idx === lastIndex ? 'blue' : 'gray',
      children: (
        <Space>
          <Tag>{item.stage}</Tag>
          <Text>{item.message}</Text>
          {item.timestamp ? (
            <Text type="secondary">{dayjs(item.timestamp).format('HH:mm:ss')}</Text>
          ) : null}
          {item.payload ? <Text type="secondary">{JSON.stringify(item.payload)}</Text> : null}
        </Space>
      ),
    }))
    .reverse()
}

type ThoughtType = 'decision' | 'tool_call' | 'note' | 'error' | 'progress'

type ThoughtItem = {
  id: string
  type: ThoughtType
  title: string
  content?: string
  timestamp?: string
}

const THOUGHT_LABEL: Record<ThoughtType, string> = {
  decision: '决策',
  tool_call: '工具',
  note: '笔记',
  error: '错误',
  progress: '进度',
}

const THOUGHT_TAG_COLOR: Record<ThoughtType, string> = {
  decision: 'purple',
  tool_call: 'blue',
  note: 'gold',
  error: 'red',
  progress: 'default',
}

const THOUGHT_ICON_COLOR: Record<ThoughtType, string> = {
  decision: '#722ed1',
  tool_call: '#1677ff',
  note: '#faad14',
  error: '#cf1322',
  progress: '#595959',
}

function getThoughtIcon(type: ThoughtType) {
  switch (type) {
    case 'decision':
      return <BulbOutlined />
    case 'tool_call':
      return <ToolOutlined />
    case 'note':
      return <FileTextOutlined />
    case 'error':
      return <WarningOutlined />
    case 'progress':
    default:
      return <ClockCircleOutlined />
  }
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function countKeywordMatches(text: string | undefined, keyword: string) {
  const trimmed = keyword.trim()
  if (!trimmed || !text) return 0
  const regex = new RegExp(escapeRegExp(trimmed), 'ig')
  const matches = text.match(regex)
  return matches ? matches.length : 0
}

function renderHighlightedText(text: string, keyword: string) {
  const trimmed = keyword.trim()
  if (!trimmed) return text
  if (!text) return ''
  const regex = new RegExp(escapeRegExp(trimmed), 'ig')
  const matches = text.match(regex)
  if (!matches) return text
  const parts = text.split(regex)
  const nodes: Array<string | JSX.Element> = []
  parts.forEach((part, index) => {
    if (part) nodes.push(part)
    const match = matches[index]
    if (match) {
      nodes.push(
        <mark key={`hl-${index}-${match}`} className={styles.thoughtHighlight}>
          {match}
        </mark>,
      )
    }
  })
  return <>{nodes}</>
}

function buildThoughtMarkdown(
  items: ThoughtItem[],
  options: {
    researchId?: string
    block?: TopicBlock
    filters?: { types: string[]; keyword: string }
  },
) {
  const { researchId, block, filters } = options
  const lines: string[] = []
  lines.push('# DeepResearch 思维流导出')
  lines.push('')
  lines.push(`- 导出时间：${dayjs().format('YYYY-MM-DD HH:mm:ss')}`)
  lines.push(`- 研究ID：${researchId || '-'}`)
  lines.push(`- 任务：${block?.title || '-'}`)
  lines.push(`- Block ID：${block?.block_id || '-'}`)
  lines.push(`- 过滤类型：${filters?.types?.length ? filters.types.join(', ') : '全部'}`)
  lines.push(`- 关键词：${filters?.keyword?.trim() || '-'}`)
  lines.push('')
  lines.push('---')
  lines.push('')
  if (!items.length) {
    lines.push('> 暂无思维流记录')
    return lines.join('\n')
  }
  items.forEach((item) => {
    const time = item.timestamp ? dayjs(item.timestamp).format('HH:mm:ss') : '-'
    lines.push(`## [${time}] ${THOUGHT_LABEL[item.type]} · ${item.title}`)
    if (item.content) {
      lines.push('')
      lines.push(item.content)
    }
    lines.push('')
  })
  return lines.join('\n')
}

function buildThoughtCsv(items: ThoughtItem[]) {
  const header = ['timestamp', 'type', 'type_label', 'title', 'content']
  const rows = items.map((item) => {
    return [
      csvEscape(item.timestamp),
      csvEscape(item.type),
      csvEscape(THOUGHT_LABEL[item.type]),
      csvEscape(item.title),
      csvEscape(item.content),
    ].join(',')
  })
  return [header.join(','), ...rows].join('\n')
}

function buildThoughtHtml(
  items: ThoughtItem[],
  options: {
    researchId?: string
    block?: TopicBlock
    filters?: { types: string[]; keyword: string }
  },
) {
  const { researchId, block, filters } = options
  const keyword = filters?.keyword?.trim() || ''
  const matchCount = keyword
    ? items.reduce((sum, item) => {
        return sum + countKeywordMatches(item.title, keyword) + countKeywordMatches(item.content, keyword)
      }, 0)
    : 0
  const typeCounts = Object.keys(THOUGHT_LABEL).reduce((acc, type) => {
    acc[type as ThoughtType] = 0
    return acc
  }, {} as Record<ThoughtType, number>)
  items.forEach((item) => {
    typeCounts[item.type] = (typeCounts[item.type] || 0) + 1
  })
  const metadata: Array<[string, string]> = [
    ['导出时间', dayjs().format('YYYY-MM-DD HH:mm:ss')],
    ['研究ID', researchId || '-'],
    ['任务', block?.title || '-'],
    ['Block ID', block?.block_id || '-'],
    ['过滤类型', filters?.types?.length ? filters.types.join(', ') : '全部'],
    ['关键词', keyword || '-'],
    ['记录总数', `${items.length}`],
    ['关键词命中', keyword ? `${matchCount} 次` : '-'],
  ]
  const metaHtml = metadata
    .map(
      ([label, value]) =>
        `<div class="meta-row"><span class="meta-label">${escapeHtml(
          label,
        )}</span><span class="meta-value">${escapeHtml(value)}</span></div>`,
    )
    .join('')
  const summaryStats: Array<[string, string]> = [
    ['状态', block?.status || '-'],
    [
      '迭代',
      block ? `${block.iterations}/${block.max_iterations ?? block.iterations}` : '-',
    ],
    ['工具调用', `${block?.tool_traces?.length ?? 0}`],
    ['引用', `${block?.citations?.length ?? 0}`],
    ['决策', `${block?.decisions?.length ?? 0}`],
    ['子任务', `${block?.child_ids?.length ?? 0}`],
  ]
  const summaryHtml = summaryStats
    .map(
      ([label, value]) =>
        `<div class="summary-card"><div class="summary-label">${escapeHtml(
          label,
        )}</div><div class="summary-value">${escapeHtml(value)}</div></div>`,
    )
    .join('')
  const legendHtml = Object.entries(THOUGHT_LABEL)
    .map(([type, label]) => {
      const count = typeCounts[type as ThoughtType]
      if (!count) return ''
      const color = THOUGHT_ICON_COLOR[type as ThoughtType] || '#595959'
      return `<div class="legend-item"><span class="legend-dot" style="background:${color};"></span><span>${escapeHtml(
        label,
      )}</span><span class="legend-count">${count}</span></div>`
    })
    .filter(Boolean)
    .join('')
  const itemsHtml = items.length
    ? items
        .map((item) => {
          const time = item.timestamp ? dayjs(item.timestamp).format('HH:mm:ss') : '-'
          const color = THOUGHT_ICON_COLOR[item.type] || '#595959'
          const contentHtml = item.content
            ? `<pre class="content">${escapeHtml(item.content)}</pre>`
            : ''
          return `
  <div class="item">
    <span class="dot" style="border-color:${color}; background:${color};"></span>
    <div class="item-body">
      <div class="item-header">
        <span class="tag" style="color:${color}; border-color:${color};">${THOUGHT_LABEL[item.type]}</span>
        <span class="title">${escapeHtml(item.title)}</span>
        <span class="time">${time}</span>
      </div>
      ${contentHtml}
    </div>
  </div>`
        })
        .join('')
    : '<div class="empty">暂无思维流记录</div>'
  return `<!doctype html>
<html lang="zh">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DeepResearch 思维流导出</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px; color: #1f1f1f; }
    h1 { margin: 0 0 16px; font-size: 20px; }
    .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px 16px; margin-bottom: 20px; }
    .meta-row { display: flex; gap: 8px; font-size: 13px; }
    .meta-label { color: #8c8c8c; min-width: 80px; }
    .meta-value { color: #1f1f1f; }
    .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .summary-card { background: #fff; border: 1px solid #f0f0f0; border-radius: 10px; padding: 10px 12px; }
    .summary-label { font-size: 12px; color: #8c8c8c; margin-bottom: 6px; }
    .summary-value { font-size: 16px; font-weight: 600; color: #1f1f1f; }
    .legend { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-bottom: 20px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; background: #fafafa; border: 1px solid #f0f0f0; border-radius: 999px; padding: 4px 10px; }
    .legend-dot { width: 8px; height: 8px; border-radius: 999px; }
    .legend-count { color: #8c8c8c; }
    .timeline { border-left: 2px solid #f0f0f0; padding-left: 18px; }
    .item { position: relative; margin-bottom: 16px; display: flex; gap: 12px; }
    .dot { position: absolute; left: -11px; top: 6px; width: 10px; height: 10px; border-radius: 999px; border: 2px solid; background: #fff; }
    .item-body { background: #fff; border: 1px solid #f0f0f0; border-radius: 10px; padding: 10px 12px; width: 100%; }
    .item-header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 6px; }
    .tag { border: 1px solid; border-radius: 999px; padding: 2px 8px; font-size: 12px; background: #f6f6f6; }
    .title { font-weight: 600; }
    .time { font-size: 12px; color: #8c8c8c; }
    .content { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 13px; color: #595959; }
    .empty { color: #8c8c8c; font-size: 13px; padding: 12px 0; }
  </style>
</head>
<body>
  <h1>DeepResearch 思维流导出</h1>
  <div class="meta">${metaHtml}</div>
  <div class="summary">${summaryHtml}</div>
  ${legendHtml ? `<div class="legend">${legendHtml}</div>` : ''}
  <div class="timeline">${itemsHtml}</div>
</body>
</html>`
}

function formatDecisionSummary(decision: any) {
  if (!decision) return ''
  const parts: string[] = []
  if (typeof decision.sufficient === 'boolean') {
    parts.push(`sufficient: ${decision.sufficient ? 'yes' : 'no'}`)
  }
  if (typeof decision.should_compare === 'boolean') {
    parts.push(`compare: ${decision.should_compare ? 'yes' : 'no'}`)
  }
  const dimensions = Array.isArray(decision.compare_dimensions)
    ? decision.compare_dimensions.filter(Boolean)
    : []
  if (dimensions.length) {
    parts.push(`dimensions: ${dimensions.join(', ')}`)
  }
  const followups = Array.isArray(decision.followup_questions)
    ? decision.followup_questions.filter(Boolean)
    : []
  if (followups.length) {
    const preview = followups.slice(0, 3).join('; ')
    parts.push(`followups: ${preview}${followups.length > 3 ? ` (+${followups.length - 3})` : ''}`)
  }
  const toolCalls = Array.isArray(decision.tool_calls)
    ? decision.tool_calls.map((call: any) => call?.name).filter(Boolean)
    : []
  if (toolCalls.length) {
    parts.push(`tool_calls: ${toolCalls.join(', ')}`)
  }
  if (decision.rationale) {
    parts.push(`rationale: ${decision.rationale}`)
  }
  return parts.join(' | ')
}

function getThoughtTypeFromMessage(message: string) {
  const text = message.toLowerCase()
  if (text.includes('decision')) return 'decision'
  if (
    text.includes('web search') ||
    text.includes('code execution') ||
    text.includes('compare') ||
    text.includes('follow-up')
  ) {
    return 'tool_call'
  }
  if (text.includes('summary') || text.includes('notes')) return 'note'
  if (text.includes('failed') || text.includes('error') || text.includes('失败') || text.includes('错误')) {
    return 'error'
  }
  return 'progress'
}

function buildThoughtContentFromEvent(event: ProgressEvent) {
  const payload = (event.payload || {}) as Record<string, any>
  if (event.message === 'Decision recorded') {
    return formatDecisionSummary(payload)
  }
  if (event.message === 'Web search completed') {
    return `citations: ${payload.citations ?? 0}`
  }
  if (event.message === 'Code execution completed') {
    return `snippets: ${payload.snippets ?? 0}`
  }
  if (event.message === 'Compare completed') {
    return `citations: ${payload.citations ?? 0}`
  }
  if (event.message === 'Summary compressed') {
    return `notes: ${payload.notes ?? 0}`
  }
  if (event.message === 'Inline follow-ups executed') {
    return `count: ${payload.count ?? 0}`
  }
  if (Object.keys(payload).length) {
    return JSON.stringify(payload)
  }
  return ''
}

function csvEscape(value: string | number | undefined | null) {
  if (value === undefined || value === null) return ''
  const text = String(value)
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`
  }
  return text
}

async function copyText(text: string) {
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

type TraceRecord = {
  block_id: string
  title?: string
  tool_id: string
  tool_type: string
  query?: string
  summary?: string
  raw_answer?: string
  timestamp?: string
  citation_id?: string
}

type DeepResearchLocationState = {
  noteContext?: {
    title?: string
    content?: string
    source?: string
    noteId?: string
    sessionId?: string
  }
}

export default function DeepResearchPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [topic, setTopic] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [mode, setMode] = useState<'queue' | 'tree'>('queue')
  const [depth, setDepth] = useState(DEFAULTS.depth)
  const [breadth, setBreadth] = useState(DEFAULTS.breadth)
  const [maxParallel, setMaxParallel] = useState(DEFAULTS.maxParallel)
  const [maxIterations, setMaxIterations] = useState(DEFAULTS.maxIterations)
  const [topK, setTopK] = useState<number | undefined>(DEFAULTS.topK)
  const [indexMode, setIndexMode] = useState(DEFAULTS.indexMode)
  const [language, setLanguage] = useState('')
  const [reportStyle, setReportStyle] = useState('')
  const [importingToStudio, setImportingToStudio] = useState(false)
  const [useWebSearch, setUseWebSearch] = useState(false)
  const [useCodeExec, setUseCodeExec] = useState(false)
  const [codeSnippetsText, setCodeSnippetsText] = useState('')
  const [metadataText, setMetadataText] = useState('')
  const [researchId, setResearchId] = useState('')
  const [result, setResult] = useState<DeepResearchResponse | null>(null)
  const [snapshot, setSnapshot] = useState<DeepResearchSnapshot | null>(null)
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([])
  const [sessions, setSessions] = useState<SessionOption[]>([])
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [useStream, setUseStream] = useState(false)
  const [streamStatus, setStreamStatus] = useState<'idle' | 'connecting' | 'connected' | 'error'>('idle')
  const [streamRetries, setStreamRetries] = useState(0)
  const [streamNonce, setStreamNonce] = useState(0)
  const [traceTypeFilter, setTraceTypeFilter] = useState<string[]>([])
  const [traceStageFilter, setTraceStageFilter] = useState<string | undefined>(undefined)
  const [traceBlockFilter, setTraceBlockFilter] = useState<string | undefined>(undefined)
  const [traceSearchText, setTraceSearchText] = useState('')
  const [selectedBlock, setSelectedBlock] = useState<TopicBlock | null>(null)
  const [selectedTrace, setSelectedTrace] = useState<any | null>(null)
  const [runList, setRunList] = useState<RunListItem[]>([])
  const [queueStatus, setQueueStatus] = useState<DeepResearchQueueStatus | null>(null)
  const [runStatusFilter, setRunStatusFilter] = useState<string[]>([])
  const [runSearchText, setRunSearchText] = useState('')
  const [runSortKey, setRunSortKey] = useState('started_desc')
  const [runPageSize, setRunPageSize] = useState(8)
  const [priorityInput, setPriorityInput] = useState<number | null>(null)
  const [processTab, setProcessTab] = useState<ProcessTab>('planning')
  const [thoughtAutoFollow, setThoughtAutoFollow] = useState(true)
  const [thoughtHitIndex, setThoughtHitIndex] = useState(-1)
  const [thoughtTypeFilter, setThoughtTypeFilter] = useState<string[]>([])
  const [thoughtSearchText, setThoughtSearchText] = useState('')
  const [archiveLoading, setArchiveLoading] = useState(false)
  const [archiveReplayLoadingId, setArchiveReplayLoadingId] = useState<string | null>(null)
  const [cancelLoadingId, setCancelLoadingId] = useState<string | null>(null)
  const [resumeLoadingId, setResumeLoadingId] = useState<string | null>(null)
  const [blockEvidenceLoading, setBlockEvidenceLoading] = useState(false)
  const [compareRunA, setCompareRunA] = useState<string | undefined>(undefined)
  const [compareRunB, setCompareRunB] = useState<string | undefined>(undefined)
  const noteContextHandledRef = useRef(false)
  const streamRef = useRef<EventSource | null>(null)
  const streamSnapshotCounterRef = useRef(0)
  const streamRetryRef = useRef(0)
  const streamReconnectTimerRef = useRef<number | null>(null)
  const lastStreamEventIdRef = useRef<string>('')
  const progressOffsetRef = useRef(0)
  const reportRef = useRef<HTMLDivElement | null>(null)
  const thoughtStreamRef = useRef<HTMLDivElement | null>(null)
  const thoughtItemRefs = useRef<Map<string, HTMLDivElement | null>>(new Map())

  const activePresetKey = useMemo(
    () =>
      resolvePresetKey({
        depth,
        breadth,
        maxParallel,
        maxIterations,
      }),
    [depth, breadth, maxParallel, maxIterations],
  )

  const activePresetDesc = useMemo(() => {
    const found = PRESET_OPTIONS.find((item) => item.value === activePresetKey)
    return found?.description
  }, [activePresetKey])

  const applyPreset = useCallback((key: PresetKey) => {
    if (key === 'custom') return
    const preset = PRESETS[key]
    if (!preset) return
    setDepth(preset.depth)
    setBreadth(preset.breadth)
    setMaxParallel(preset.maxParallel)
    setMaxIterations(preset.maxIterations)
  }, [])

  useEffect(() => {
    const cached = sessionStorage.getItem(STORAGE_KEY)
    if (!cached) return
    try {
      const parsed = JSON.parse(cached)
      if (typeof parsed.topic === 'string') setTopic(parsed.topic)
      if (typeof parsed.sessionId === 'string') setSessionId(parsed.sessionId)
      if (typeof parsed.mode === 'string') setMode(parsed.mode)
      if (typeof parsed.depth === 'number') setDepth(parsed.depth)
      if (typeof parsed.breadth === 'number') setBreadth(parsed.breadth)
      if (typeof parsed.maxParallel === 'number') setMaxParallel(parsed.maxParallel)
      if (typeof parsed.maxIterations === 'number') setMaxIterations(parsed.maxIterations)
      if (typeof parsed.topK === 'number') setTopK(parsed.topK)
      if (typeof parsed.indexMode === 'string') setIndexMode(parsed.indexMode)
      if (typeof parsed.language === 'string') setLanguage(parsed.language)
      if (typeof parsed.reportStyle === 'string') setReportStyle(parsed.reportStyle)
      if (typeof parsed.useWebSearch === 'boolean') setUseWebSearch(parsed.useWebSearch)
      if (typeof parsed.useCodeExec === 'boolean') setUseCodeExec(parsed.useCodeExec)
      if (typeof parsed.codeSnippetsText === 'string') setCodeSnippetsText(parsed.codeSnippetsText)
      if (typeof parsed.metadataText === 'string') setMetadataText(parsed.metadataText)
      if (typeof parsed.researchId === 'string') setResearchId(parsed.researchId)
    } catch (error) {
      console.warn('Failed to restore deep research state', error)
    }
  }, [])

  useEffect(() => {
    if (!location.search) return
    const params = new URLSearchParams(location.search)
    const topicParam = params.get('topic')
    const sessionParam = params.get('sessionId')
    const researchParam = params.get('researchId')
    if (topicParam) setTopic(topicParam)
    if (sessionParam) setSessionId(sessionParam)
    if (researchParam) setResearchId(researchParam)
  }, [location.search])

  useEffect(() => {
    if (noteContextHandledRef.current) return
    const state = location.state as DeepResearchLocationState | null
    if (!state?.noteContext) return
    noteContextHandledRef.current = true
    const note = state.noteContext
    if (note.title && !topic) setTopic(note.title)
    if (note.sessionId && !sessionId) setSessionId(note.sessionId)
    const content = (note.content || '').trim()
    if (!content) return
    const parsed = safeParseJson(metadataText)
    const base =
      parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    const next = {
      ...base,
      context_text: content,
      context_title: note.title || base.context_title,
      context_source: note.source || 'notebook',
      context_note_id: note.noteId || base.context_note_id,
    }
    setMetadataText(JSON.stringify(next, null, 2))
  }, [location.state, metadataText, sessionId, topic])

  useEffect(() => {
    lastStreamEventIdRef.current = ''
    streamSnapshotCounterRef.current = 0
    progressOffsetRef.current = 0
  }, [researchId])

  useEffect(() => {
    const payload = {
      topic,
      sessionId,
      mode,
      depth,
      breadth,
      maxParallel,
      maxIterations,
      topK,
      indexMode,
      language,
      reportStyle,
      useWebSearch,
      useCodeExec,
      codeSnippetsText,
      metadataText,
      researchId,
    }
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch (error) {
      console.warn('Failed to persist deep research state', error)
    }
  }, [
    topic,
    sessionId,
    mode,
    depth,
    breadth,
    maxParallel,
    maxIterations,
    topK,
    indexMode,
    language,
    reportStyle,
    useWebSearch,
    useCodeExec,
    codeSnippetsText,
    metadataText,
    researchId,
  ])

  useRequest(
    async () => {
      const { data } = await api.session.list({ surface: 'deep_chat' }, { errorToast: false })
      return data?.sessions ?? []
    },
    {
      refreshDeps: [],
      onSuccess(data) {
        setSessions(data as SessionOption[])
      },
    },
  )

  const { run: refreshQueueStatus, loading: queueLoading } = useRequest(
    async () => {
      const { data } = await api.deepResearch.getDeepResearchQueueStatus({ errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setQueueStatus(data as DeepResearchQueueStatus)
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `队列状态获取失败：${detail}` : '队列状态获取失败')
      },
    },
  )

  const { run: refreshRuns, loading: runsLoading } = useRequest(
    async () => {
      const { data } = await api.deepResearch.listDeepResearchRuns({ errorToast: false })
      return data?.items ?? []
    },
    {
      refreshDeps: [],
      onSuccess(data) {
        setRunList(data as RunListItem[])
        refreshQueueStatus()
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `运行历史获取失败：${detail}` : '运行历史获取失败')
      },
    },
  )

  const { run: runSnapshot, loading: snapshotLoading } = useRequest(
    async (id: string) => {
      if (!id) {
        throw new Error('请先输入 Research ID')
      }
      const { data } = await api.deepResearch.getDeepResearchSnapshot(id, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setSnapshot(data)
        refreshRuns()
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `快照获取失败：${detail}` : '快照获取失败')
      },
    },
  )

  const { run: runProgress, loading: progressLoading } = useRequest(
    async ({ id, mode = 'tail' }: { id: string; mode?: 'tail' | 'since' }) => {
      if (!id) {
        throw new Error('请先输入 Research ID')
      }
      if (mode === 'since') {
        const { data } = await api.deepResearch.getDeepResearchProgressSince(
          id,
          progressOffsetRef.current,
          PROGRESS_PAGE_LIMIT,
          { errorToast: false },
        )
        return { ...data, mode }
      }
      const { data } = await api.deepResearch.getDeepResearchProgress(id, {
        errorToast: false,
        params: { tail: PROGRESS_TAIL },
      })
      return { ...data, mode }
    },
    {
      manual: true,
      onSuccess(data) {
        const items = data.items ?? []
        if (data.mode === 'since') {
          if (items.length) {
            setProgressEvents((prev) => [...prev, ...items].slice(-PROGRESS_TAIL))
          }
        } else {
          setProgressEvents(items)
        }
        if (typeof data.next_offset === 'number') {
          progressOffsetRef.current = data.next_offset
        }
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `进度获取失败：${detail}` : '进度获取失败')
      },
    },
  )

  const applySubmittedRun = useCallback(
    (
      payload: DeepResearchSubmitResponse,
      options?: { reset?: boolean; message?: string },
    ) => {
      const id = payload.research_id
      setResearchId(id)
      setResult(null)
      if (options?.reset !== false) {
        setSnapshot(null)
        setProgressEvents([])
      }
    progressOffsetRef.current = 0
      if (!useStream && !autoRefresh) {
        setAutoRefresh(true)
        message.info('已自动开启自动刷新')
      }
    runProgress({ id, mode: 'tail' })
      runSnapshot(id)
      refreshRuns()
      refreshQueueStatus()
      const statusMessage =
        payload.status === 'running'
          ? '任务已开始执行'
          : payload.status === 'queued'
            ? `任务已进入队列${payload.queue_position ? `（第 ${payload.queue_position} 位）` : ''}`
            : '任务已提交'
      message.success(options?.message || statusMessage)
    },
    [autoRefresh, refreshRuns, runProgress, runSnapshot, useStream],
  )

  const handleRunError = useCallback((error: any) => {
    const detail = error?.response?.data?.detail || error?.message
    message.error(detail ? `提交失败：${detail}` : '提交失败')
  }, [])

  const { runAsync: runDeepResearchRequest, loading: runLoading } = useRequest(
    async (payload: DeepResearchRequest) => {
      const { data } = await api.deepResearch.submitDeepResearch(payload, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        applySubmittedRun(data, { reset: true })
      },
      onError: handleRunError,
    },
  )

  const { runAsync: runReplayRequest, loading: replayLoading } = useRequest(
    async (id: string) => {
      const { data } = await api.deepResearch.replayDeepResearch(id, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        applySubmittedRun(data, { reset: true })
      },
      onError: handleRunError,
    },
  )

  const { runAsync: runCancelRequest, loading: cancelLoading } = useRequest(
    async (id: string) => {
      const { data } = await api.deepResearch.cancelDeepResearch(id, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        refreshRuns()
    runProgress({ id: data.research_id, mode: 'since' })
        refreshQueueStatus()
        message.success('已请求取消')
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `取消失败：${detail}` : '取消失败')
      },
    },
  )

  const { runAsync: runResumeRequest, loading: resumeLoading } = useRequest(
    async (id: string) => {
      const { data } = await api.deepResearch.resumeDeepResearch(id, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        applySubmittedRun(data, { reset: false })
        refreshQueueStatus()
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `恢复失败：${detail}` : '恢复失败')
      },
    },
  )

  const { runAsync: runUpdatePriority, loading: priorityLoading } = useRequest(
    async ({ id, priority }: { id: string; priority: number }) => {
      const { data } = await api.deepResearch.updateDeepResearchPriority(
        id,
        { priority },
        { errorToast: false },
      )
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        refreshRuns()
        refreshQueueStatus()
        const positionText = data.queue_position ? `队列第 ${data.queue_position} 位` : '已更新'
        message.success(`优先级已更新（${positionText}）`)
      },
      onError(error: any) {
        const detail = error?.response?.data?.detail || error?.message
        message.error(detail ? `优先级更新失败：${detail}` : '优先级更新失败')
      },
    },
  )

  const handleRunResearch = useCallback(async () => {
    if (!topic.trim()) {
      message.warning('请输入研究主题')
      return
    }
    const metadata = safeParseJson(metadataText)
    if (metadata === null) {
      message.warning('Metadata JSON 格式错误')
      return
    }
    const presetMeta =
      activePresetKey !== 'custom' ? { deep_research_preset: activePresetKey } : {}
    const snippets = useCodeExec ? parseSnippets(codeSnippetsText) : []
    const payload: DeepResearchRequest = {
      topic: topic.trim(),
      mode,
      depth,
      breadth,
      max_parallel: maxParallel,
      max_iterations: maxIterations,
      top_k: topK || undefined,
      index_mode: indexMode || undefined,
      session_id: sessionId || undefined,
      language: language || undefined,
      report_style: reportStyle || undefined,
      use_web_search: useWebSearch,
      use_code_exec: useCodeExec,
      code_exec_snippets: snippets,
      metadata: { ...(metadata || {}), ...presetMeta },
    }
    await runDeepResearchRequest(payload)
  }, [
    topic,
    metadataText,
    activePresetKey,
    useCodeExec,
    codeSnippetsText,
    mode,
    depth,
    breadth,
    maxParallel,
    maxIterations,
    topK,
    indexMode,
    sessionId,
    language,
    reportStyle,
    useWebSearch,
    useCodeExec,
    runDeepResearchRequest,
  ])

  const normalizeRequest = useCallback(
    (raw?: Record<string, any> | null): DeepResearchRequest | null => {
      if (!raw || typeof raw !== 'object') return null
      const topicValue = typeof raw.topic === 'string' ? raw.topic.trim() : ''
      if (!topicValue) return null
      const resolveNumber = (value: any, fallback?: number) => {
        return typeof value === 'number' && Number.isFinite(value) ? value : fallback
      }
      const resolveString = (value: any) => (typeof value === 'string' ? value : undefined)
      const codeExecSnippets = Array.isArray(raw.code_exec_snippets)
        ? raw.code_exec_snippets.filter((item) => typeof item === 'string')
        : []
      const metadata =
        raw.metadata && typeof raw.metadata === 'object' && !Array.isArray(raw.metadata)
          ? raw.metadata
          : {}
      return {
        topic: topicValue,
        mode: raw.mode === 'tree' ? 'tree' : 'queue',
        depth: resolveNumber(raw.depth, DEFAULTS.depth),
        breadth: resolveNumber(raw.breadth, DEFAULTS.breadth),
        max_parallel: resolveNumber(raw.max_parallel, DEFAULTS.maxParallel),
        max_iterations: resolveNumber(raw.max_iterations, DEFAULTS.maxIterations),
        top_k: resolveNumber(raw.top_k, undefined),
        index_mode: resolveString(raw.index_mode),
        session_id: resolveString(raw.session_id),
        language: resolveString(raw.language),
        report_style: resolveString(raw.report_style),
        use_web_search: Boolean(raw.use_web_search),
        use_code_exec: Boolean(raw.use_code_exec),
        code_exec_snippets: codeExecSnippets,
        metadata,
      }
    },
    [],
  )

  const applyRequestToForm = useCallback(
    (
      request: DeepResearchRequest,
      options?: { showMessage?: boolean; scroll?: boolean },
    ) => {
      setTopic(request.topic)
      setMode(request.mode || 'queue')
      setDepth(request.depth ?? DEFAULTS.depth)
      setBreadth(request.breadth ?? DEFAULTS.breadth)
      setMaxParallel(request.max_parallel ?? DEFAULTS.maxParallel)
      setMaxIterations(request.max_iterations ?? DEFAULTS.maxIterations)
      setTopK(request.top_k ?? undefined)
      setIndexMode(request.index_mode ?? '')
      setSessionId(request.session_id || '')
      setLanguage(request.language || '')
      setReportStyle(request.report_style || '')
      setUseWebSearch(Boolean(request.use_web_search))
      setUseCodeExec(Boolean(request.use_code_exec))
      setCodeSnippetsText((request.code_exec_snippets || []).join('\n---\n'))
      setMetadataText(request.metadata ? JSON.stringify(request.metadata, null, 2) : '')
      if (options?.showMessage !== false) {
        message.success('已加载运行参数，可修改后重新执行')
      }
      if (options?.scroll !== false) {
        const anchor = document.getElementById(PARAMS_ANCHOR_ID)
        if (anchor) {
          anchor.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }
    },
    [],
  )

  const fetchArchiveRequest = useCallback(
    async (id: string, options?: { missingMessage?: string; errorMessage?: string }) => {
      try {
        const { data } = await api.deepResearch.getDeepResearchArchive(id, { errorToast: false })
        const request = normalizeRequest(data?.meta?.request as Record<string, any>)
        if (!request) {
          message.warning(options?.missingMessage || '运行档案缺少请求参数')
          return null
        }
        return request
      } catch (error: any) {
        const detail = error?.response?.data?.detail || error?.message
        const fallback = options?.errorMessage || '运行档案读取失败'
        message.error(detail ? `${fallback}：${detail}` : fallback)
        return null
      }
    },
    [normalizeRequest],
  )

  const handleReplayArchive = useCallback(
    async (id?: string) => {
      const targetId = (id || result?.research_id || researchId).trim()
      if (!targetId) {
        message.warning('请先输入 Research ID')
        return
      }
      setArchiveReplayLoadingId(targetId)
      try {
        const request = await fetchArchiveRequest(targetId, {
          missingMessage: '运行档案缺少请求参数，无法复现',
          errorMessage: '运行档案读取失败',
        })
        if (!request) return
        applyRequestToForm(request, { showMessage: false, scroll: false })
        message.success('已从运行档案回填参数')
        await runReplayRequest(targetId)
      } catch {
        // errors are handled by request hooks
      } finally {
        setArchiveReplayLoadingId(null)
      }
    },
    [applyRequestToForm, fetchArchiveRequest, researchId, result?.research_id, runReplayRequest],
  )

  const handleLoadSnapshot = useCallback(() => {
    if (!researchId.trim()) {
      message.warning('请先输入 Research ID')
      return
    }
    progressOffsetRef.current = 0
    runSnapshot(researchId.trim())
    runProgress({ id: researchId.trim(), mode: 'tail' })
  }, [researchId, runSnapshot, runProgress])

  const handleCancelRun = useCallback(
    async (id?: string) => {
      const targetId = (id || result?.research_id || researchId).trim()
      if (!targetId) {
        message.warning('请先输入 Research ID')
        return
      }
      setCancelLoadingId(targetId)
      try {
        await runCancelRequest(targetId)
      } finally {
        setCancelLoadingId(null)
      }
    },
    [researchId, result?.research_id, runCancelRequest],
  )

  const handleResumeRun = useCallback(
    async (id?: string) => {
      const targetId = (id || result?.research_id || researchId).trim()
      if (!targetId) {
        message.warning('请先输入 Research ID')
        return
      }
      setResumeLoadingId(targetId)
      try {
        await runResumeRequest(targetId)
      } finally {
        setResumeLoadingId(null)
      }
    },
    [researchId, result?.research_id, runResumeRequest],
  )

  const handleSelectRun = useCallback(
    (item: RunListItem) => {
      setResearchId(item.research_id)
      setResult(null)
      progressOffsetRef.current = 0
      runSnapshot(item.research_id)
      runProgress({ id: item.research_id, mode: 'tail' })
    },
    [runSnapshot, runProgress],
  )

  const handleRerun = useCallback(
    async (item: RunListItem) => {
      await handleReplayArchive(item.research_id)
    },
    [handleReplayArchive],
  )

  const handleCopyRunId = useCallback(async (id: string) => {
    const ok = await copyText(id)
    if (ok) {
      message.success('已复制 Research ID')
    } else {
      message.error('复制失败，请手动复制')
    }
  }, [])

  const handleLoadRequest = useCallback(
    async (item: RunListItem) => {
      const request =
        normalizeRequest(item.request as Record<string, any>) ||
        (await fetchArchiveRequest(item.research_id, {
          missingMessage: '该运行缺少请求参数，无法加载',
          errorMessage: '运行档案读取失败',
        }))
      if (!request) return
      applyRequestToForm(request)
    },
    [applyRequestToForm, fetchArchiveRequest, normalizeRequest],
  )

  const handleCopyRunRequest = useCallback(async (item: RunListItem) => {
    const request = item.request as DeepResearchRequest | undefined
    if (!request) {
      message.warning('该运行缺少请求参数')
      return
    }
    const ok = await copyText(JSON.stringify(request, null, 2))
    if (ok) {
      message.success('已复制运行参数')
    } else {
      message.error('复制失败，请手动复制')
    }
  }, [])

  useEffect(() => {
    if (!autoRefresh || !researchId.trim() || useStream) return
    const id = window.setInterval(() => {
      runSnapshot(researchId.trim())
      runProgress({ id: researchId.trim(), mode: 'since' })
      refreshQueueStatus()
    }, 5000)
    runSnapshot(researchId.trim())
    runProgress({ id: researchId.trim(), mode: 'tail' })
    return () => window.clearInterval(id)
  }, [autoRefresh, researchId, runSnapshot, runProgress, refreshQueueStatus, useStream])

  useEffect(() => {
    const clearReconnectTimer = () => {
      if (streamReconnectTimerRef.current) {
        window.clearTimeout(streamReconnectTimerRef.current)
        streamReconnectTimerRef.current = null
      }
    }

    const closeStream = () => {
      if (streamRef.current) {
        streamRef.current.close()
        streamRef.current = null
      }
      clearReconnectTimer()
    }

    if (!useStream || !researchId.trim()) {
      closeStream()
      setStreamStatus('idle')
      return
    }

    let cancelled = false

    const openStream = () => {
      if (cancelled) return
      closeStream()
      setStreamStatus('connecting')
      const baseUrl = api.deepResearch.getDeepResearchProgressStreamUrl(researchId.trim())
      const lastEventId = lastStreamEventIdRef.current
      const url = lastEventId
        ? `${baseUrl}&last_event_id=${encodeURIComponent(lastEventId)}`
        : baseUrl
      const source = new EventSource(url)
      streamRef.current = source

      source.onopen = () => {
        streamRetryRef.current = 0
        setStreamRetries(0)
        setStreamStatus('connected')
      }

      source.addEventListener('progress', (event) => {
        const messageEvent = event as MessageEvent<string>
        if (messageEvent.lastEventId) {
          lastStreamEventIdRef.current = messageEvent.lastEventId
        }
        if (!messageEvent.data) return
        try {
          const parsed = JSON.parse(messageEvent.data)
          setProgressEvents((prev) => {
            const next = [...prev, parsed].slice(-300)
            return next
          })
          streamSnapshotCounterRef.current += 1
          if (streamSnapshotCounterRef.current % 3 === 0) {
            runSnapshot(researchId.trim())
          }
        } catch (error) {
          console.warn('Failed to parse progress event', error)
        }
      })

      source.addEventListener('heartbeat', () => {})

      source.onerror = () => {
        if (cancelled) return
        source.close()
        streamRef.current = null
        setStreamStatus('error')
        streamRetryRef.current += 1
        setStreamRetries(streamRetryRef.current)
        const delay = Math.min(20000, 2000 * streamRetryRef.current)
        clearReconnectTimer()
        streamReconnectTimerRef.current = window.setTimeout(() => {
          openStream()
        }, delay)
      }
    }

    openStream()

    return () => {
      cancelled = true
      closeStream()
    }
  }, [useStream, researchId, runSnapshot, streamNonce])

  const currentResearchId = useMemo(
    () => (result?.research_id || researchId).trim(),
    [researchId, result?.research_id],
  )
  const currentRunMeta = useMemo(
    () => runList.find((item) => item.research_id === currentResearchId),
    [currentResearchId, runList],
  )
  const currentStatus =
    currentRunMeta?.status ||
    result?.status ||
    (snapshot?.report as DeepResearchReportPayload | undefined)?.status ||
    ''
  const canCancel = currentStatus === 'running' || currentStatus === 'queued'
  const canResume =
    currentStatus === 'failed' || currentStatus === 'cancelled' || currentStatus === 'running'

  useEffect(() => {
    if (typeof currentRunMeta?.priority === 'number') {
      setPriorityInput(currentRunMeta.priority)
      return
    }
    setPriorityInput(null)
  }, [currentResearchId, currentRunMeta?.priority])

  const handleUpdatePriority = useCallback(async () => {
    const id = currentResearchId
    if (!id) {
      message.warning('请先选择 Research ID')
      return
    }
    if (typeof priorityInput !== 'number' || Number.isNaN(priorityInput)) {
      message.warning('请输入有效优先级（-10~10）')
      return
    }
    await runUpdatePriority({ id, priority: priorityInput })
  }, [currentResearchId, priorityInput, runUpdatePriority])

  const reportMarkdown =
    result?.report_markdown || (snapshot?.report?.report_markdown as string | undefined) || ''
  const reportDetails = useMemo<DeepResearchReportDetails>(() => {
    const fromTrace = result?.trace?.report_details
    if (fromTrace && typeof fromTrace === 'object') {
      return fromTrace as DeepResearchReportDetails
    }
    const fromSnapshot =
      (snapshot?.report?.report_details as DeepResearchReportDetails | undefined) ||
      (snapshot?.report as DeepResearchReportDetails | undefined)
    if (fromSnapshot && typeof fromSnapshot === 'object') {
      return fromSnapshot
    }
    return {}
  }, [result?.trace?.report_details, snapshot?.report])
  const reportOutline = reportDetails.outline || []
  const reportOutlineDetailed = reportDetails.outline_detailed || []
  const reportNotes = reportDetails.notes || []
  const reportCitationTable = reportDetails.citation_table || []
  const reportQuality = reportDetails.quality

  const handleExportReport = useCallback(() => {
    if (!reportMarkdown) {
      message.warning('暂无可导出的报告')
      return
    }
    downloadText(
      `deep-research-${result?.research_id || researchId || 'report'}.md`,
      reportMarkdown,
      'text/markdown;charset=utf-8',
    )
  }, [reportMarkdown, result?.research_id, researchId])

  const handleExportHtml = useCallback(() => {
    if (!reportMarkdown) {
      message.warning('暂无可导出的报告')
      return
    }
    const html = buildHtmlReport(reportMarkdown)
    downloadText(
      `deep-research-${result?.research_id || researchId || 'report'}.html`,
      html,
      'text/html;charset=utf-8',
    )
  }, [reportMarkdown, result?.research_id, researchId])

  const handleExportPdf = useCallback(async () => {
    if (!reportMarkdown) {
      message.warning('暂无可导出的报告')
      return
    }
    if (!reportRef.current) {
      message.warning('报告区域尚未渲染')
      return
    }
    const key = 'deep-research-pdf'
    message.loading({ content: '正在生成 PDF...', key })
    try {
      await exportToPdf(reportRef.current, {
        filename: `deep-research-${result?.research_id || researchId || 'report'}`,
      })
      message.success({ content: 'PDF 导出完成', key })
    } catch (error) {
      console.error('PDF export failed:', error)
      message.error({ content: 'PDF 导出失败', key })
    }
  }, [reportMarkdown, result?.research_id, researchId])

  const handlePrintReport = useCallback(() => {
    if (!reportMarkdown) {
      message.warning('暂无可打印的报告')
      return
    }
    const html = buildHtmlReport(reportMarkdown)
    const win = window.open('', '_blank')
    if (!win) {
      message.warning('请允许弹窗以导出 PDF')
      return
    }
    win.document.open()
    win.document.write(html)
    win.document.close()
    win.focus()
    win.print()
  }, [reportMarkdown])

  const handleOpenInDocStudio = useCallback(async () => {
    if (!reportMarkdown) {
      message.warning('暂无可导入的报告')
      return
    }
    const suffix = result?.research_id || researchId || 'report'
    const workspaceName = `deep-research-${suffix}`
    setImportingToStudio(true)
    try {
      const workspace = await createWorkspace({
        name: workspaceName,
        config: {
          workspace_type: 'doc_studio',
          primary_format: 'markdown',
          supported_formats: ['markdown', 'plaintext'],
          main_file: 'report.md',
        },
      })
      await updateFileContent({
        workspaceId: workspace.workspaceId,
        path: 'report.md',
        content: reportMarkdown,
      })
      message.success('已导入到 Doc Studio')
      navigate(`/doc-studio/${workspace.workspaceId}?file=report.md`)
    } catch (error) {
      message.error('导入到 Doc Studio 失败')
    } finally {
      setImportingToStudio(false)
    }
  }, [navigate, reportMarkdown, researchId, result?.research_id])

  const citations = useMemo<DeepResearchCitation[]>(() => {
    if (result?.citations?.length) return result.citations
    const stored = (snapshot?.citations as any)?.citations
    if (Array.isArray(stored)) return stored as DeepResearchCitation[]
    return []
  }, [result?.citations, snapshot?.citations])

  const citationMap = useMemo(() => {
    const map = new Map<string, DeepResearchCitation>()
    citations.forEach((item) => {
      map.set(item.citation_id, item)
    })
    return map
  }, [citations])

  const blocks = useMemo<TopicBlock[]>(() => {
    const queueBlocks = result?.trace?.queue?.blocks || snapshot?.queue?.blocks
    if (!Array.isArray(queueBlocks)) return []
    return queueBlocks as TopicBlock[]
  }, [result?.trace?.queue?.blocks, snapshot?.queue?.blocks])

  const getStatusColor = useCallback((status: string) => {
    if (status === 'completed') return 'green'
    if (status === 'failed') return 'red'
    if (status === 'cancelled') return 'orange'
    if (status === 'researching' || status === 'running') return 'blue'
    if (status === 'queued') return 'purple'
    if (status === 'skipped') return 'gold'
    return 'default'
  }, [])

  const queueStats = useMemo(() => {
    const stats = {
      pending: 0,
      researching: 0,
      completed: 0,
      failed: 0,
      skipped: 0,
    }
    blocks.forEach((block) => {
      const key = block.status as keyof typeof stats
      if (key in stats) {
        stats[key] += 1
      }
    })
    return stats
  }, [blocks])

  const rootBlock = useMemo(
    () => blocks.find((block) => block.depth === 0) || null,
    [blocks],
  )
  const planningBlocks = useMemo(
    () => blocks.filter((block) => block.depth === 1),
    [blocks],
  )
  const planItems = useMemo<PlanItem[]>(() => {
    const fromTrace = result?.trace?.plan?.items
    if (Array.isArray(fromTrace)) return fromTrace
    const fromSnapshot = snapshot?.outline?.items
    if (Array.isArray(fromSnapshot)) return fromSnapshot
    return []
  }, [result?.trace?.plan?.items, snapshot?.outline?.items])
  const researchBlocks = useMemo(
    () => blocks.filter((block) => block.depth > 0),
    [blocks],
  )
  const latestEventByBlock = useMemo(() => {
    const map = new Map<string, ProgressEvent>()
    progressEvents.forEach((event) => {
      const blockId = (event.payload as any)?.block_id
      if (blockId) {
        map.set(blockId, event)
      }
    })
    return map
  }, [progressEvents])
  const blockChildCounts = useMemo(() => {
    const map = new Map<string, number>()
    blocks.forEach((block) => {
      if (!block.parent_id) return
      map.set(block.parent_id, (map.get(block.parent_id) || 0) + 1)
    })
    return map
  }, [blocks])
  const toolsByBlock = useMemo(() => {
    const map = new Map<string, string[]>()
    blocks.forEach((block) => {
      const tools = new Set<string>()
      ;(block.tool_traces || []).forEach((trace) => {
        if (trace.tool_type) {
          tools.add(trace.tool_type)
        }
      })
      map.set(block.block_id, Array.from(tools))
    })
    return map
  }, [blocks])
  const latestTraceByBlock = useMemo(() => {
    const map = new Map<string, any>()
    blocks.forEach((block) => {
      const traces = block.tool_traces || []
      if (traces.length) {
        map.set(block.block_id, traces[traces.length - 1])
      }
    })
    return map
  }, [blocks])
  const reportStats = useMemo(() => {
    if (!reportMarkdown) {
      return { lines: 0, words: 0, chars: 0 }
    }
    const lines = reportMarkdown.split(/\n/).length
    const words = reportMarkdown.trim().split(/\s+/).filter(Boolean).length
    return { lines, words, chars: reportMarkdown.length }
  }, [reportMarkdown])
  const activeBlock = useMemo(() => {
    if (selectedBlock) return selectedBlock
    const running = researchBlocks.find((block) => block.status === 'researching')
    return running || researchBlocks[0] || null
  }, [researchBlocks, selectedBlock])
  const activeAction = useMemo(() => {
    if (!activeBlock) return ''
    return latestEventByBlock.get(activeBlock.block_id)?.message || ''
  }, [activeBlock, latestEventByBlock])
  const activeLastTrace = useMemo(() => {
    if (!activeBlock) return null
    return latestTraceByBlock.get(activeBlock.block_id) || null
  }, [activeBlock, latestTraceByBlock])
  const activeDecision = useMemo(() => {
    if (!activeBlock?.decisions?.length) return null
    return activeBlock.decisions[activeBlock.decisions.length - 1]
  }, [activeBlock])
  const activeDecisionCompareDimensions = useMemo(() => {
    if (!activeDecision || !Array.isArray(activeDecision.compare_dimensions)) return []
    return activeDecision.compare_dimensions.filter(Boolean)
  }, [activeDecision])
  const activeDecisionFollowups = useMemo(() => {
    if (!activeDecision || !Array.isArray(activeDecision.followup_questions)) return []
    return activeDecision.followup_questions.filter(Boolean)
  }, [activeDecision])
  const activeDecisionToolCalls = useMemo(() => {
    if (!activeDecision || !Array.isArray(activeDecision.tool_calls)) return []
    return activeDecision.tool_calls
      .map((call: any) => call?.name)
      .filter(Boolean)
  }, [activeDecision])
  const activeThoughtItems = useMemo<ThoughtItem[]>(() => {
    if (!activeBlock) return []
    const items: ThoughtItem[] = []
    const blockEvents = progressEvents.filter(
      (event) => (event.payload as any)?.block_id === activeBlock.block_id,
    )
    blockEvents.forEach((event, idx) => {
      items.push({
        id: `event-${idx}`,
        type: getThoughtTypeFromMessage(event.message || ''),
        title: event.message || 'progress',
        content: buildThoughtContentFromEvent(event),
        timestamp: event.timestamp,
      })
    })
    ;(activeBlock.tool_traces || []).forEach((trace: any, idx: number) => {
      items.push({
        id: `trace-${idx}`,
        type: isTraceError(trace) ? 'error' : 'tool_call',
        title: `${trace.tool_type} · ${trace.tool_id}`,
        content: trace.summary || trace.query || '',
        timestamp: trace.timestamp,
      })
    })
    const hasDecisionEvent = blockEvents.some((event) =>
      (event.message || '').toLowerCase().includes('decision'),
    )
    if (activeDecision && !hasDecisionEvent) {
      items.push({
        id: 'decision-snapshot',
        type: 'decision',
        title: 'Decision snapshot',
        content: formatDecisionSummary(activeDecision),
        timestamp: activeBlock.updated_at,
      })
    }
    const toMillis = (value?: string) => (value ? dayjs(value).valueOf() : 0)
    items.sort((a, b) => toMillis(a.timestamp) - toMillis(b.timestamp))
    return items.slice(-120)
  }, [activeBlock, activeDecision, progressEvents])

  useEffect(() => {
    if (!thoughtStreamRef.current) return
    if (!thoughtAutoFollow || thoughtSearchText.trim()) return
    thoughtStreamRef.current.scrollTop = thoughtStreamRef.current.scrollHeight
  }, [activeThoughtItems.length, activeBlock?.block_id, thoughtSearchText, thoughtAutoFollow])

  const thoughtTypeOptions = useMemo(
    () =>
      Array.from(new Set(activeThoughtItems.map((item) => item.type))).map((type) => ({
        label: THOUGHT_LABEL[type],
        value: type,
      })),
    [activeThoughtItems],
  )

  const filteredThoughtItems = useMemo(() => {
    let items = activeThoughtItems
    if (thoughtTypeFilter.length) {
      items = items.filter((item) => thoughtTypeFilter.includes(item.type))
    }
    if (thoughtSearchText.trim()) {
      const keyword = thoughtSearchText.trim().toLowerCase()
      items = items.filter((item) => {
        const haystack = `${item.title} ${item.content || ''}`.toLowerCase()
        return haystack.includes(keyword)
      })
    }
    return items
  }, [activeThoughtItems, thoughtTypeFilter, thoughtSearchText])

  const thoughtMatchStats = useMemo(() => {
    const keyword = thoughtSearchText.trim()
    if (!keyword) {
      return { matchItemIds: [] as string[], totalMatches: 0 }
    }
    const matchItemIds: string[] = []
    let totalMatches = 0
    filteredThoughtItems.forEach((item) => {
      const count =
        countKeywordMatches(item.title, keyword) + countKeywordMatches(item.content, keyword)
      if (count > 0) {
        matchItemIds.push(item.id)
        totalMatches += count
      }
    })
    return { matchItemIds, totalMatches }
  }, [filteredThoughtItems, thoughtSearchText])

  const activeMatchId =
    thoughtHitIndex >= 0 && thoughtHitIndex < thoughtMatchStats.matchItemIds.length
      ? thoughtMatchStats.matchItemIds[thoughtHitIndex]
      : undefined

  const scrollToThoughtItem = useCallback((id: string) => {
    const node = thoughtItemRefs.current.get(id)
    if (!node) return
    node.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [])

  useEffect(() => {
    if (!thoughtSearchText.trim() || !thoughtMatchStats.matchItemIds.length) {
      setThoughtHitIndex(-1)
      return
    }
    setThoughtHitIndex(0)
  }, [thoughtSearchText, thoughtMatchStats.matchItemIds.length, activeBlock?.block_id])

  useEffect(() => {
    if (thoughtHitIndex < 0) return
    const targetId = thoughtMatchStats.matchItemIds[thoughtHitIndex]
    if (!targetId) return
    scrollToThoughtItem(targetId)
  }, [thoughtHitIndex, scrollToThoughtItem, thoughtMatchStats.matchItemIds])

  const handleThoughtScrollToBottom = useCallback(() => {
    if (!thoughtStreamRef.current) return
    thoughtStreamRef.current.scrollTop = thoughtStreamRef.current.scrollHeight
  }, [])

  const handlePrevThoughtMatch = useCallback(() => {
    const total = thoughtMatchStats.matchItemIds.length
    if (!total) return
    setThoughtHitIndex((prev) => {
      if (prev <= 0) return total - 1
      return prev - 1
    })
  }, [thoughtMatchStats.matchItemIds.length])

  const handleNextThoughtMatch = useCallback(() => {
    const total = thoughtMatchStats.matchItemIds.length
    if (!total) return
    setThoughtHitIndex((prev) => {
      if (prev < 0) return 0
      return (prev + 1) % total
    })
  }, [thoughtMatchStats.matchItemIds.length])

  const handleExportThoughtJson = useCallback(() => {
    if (!activeBlock) {
      message.warning('暂无可导出的任务')
      return
    }
    if (!filteredThoughtItems.length) {
      message.warning('暂无可导出的思维流')
      return
    }
    const exportName = buildThoughtExportBaseName(
      result?.research_id || researchId,
      activeBlock.block_id,
    )
    downloadJson(`${exportName}.json`, {
      exported_at: dayjs().toISOString(),
      research_id: result?.research_id || researchId,
      block: {
        block_id: activeBlock.block_id,
        title: activeBlock.title,
        status: activeBlock.status,
      },
      filters: {
        types: thoughtTypeFilter,
        keyword: thoughtSearchText,
      },
      items: filteredThoughtItems,
    })
  }, [
    activeBlock,
    filteredThoughtItems,
    researchId,
    result?.research_id,
    thoughtSearchText,
    thoughtTypeFilter,
  ])

  const handleExportThoughtCsv = useCallback(() => {
    if (!activeBlock) {
      message.warning('暂无可导出的任务')
      return
    }
    if (!filteredThoughtItems.length) {
      message.warning('暂无可导出的思维流')
      return
    }
    const exportName = buildThoughtExportBaseName(
      result?.research_id || researchId,
      activeBlock.block_id,
    )
    const csv = buildThoughtCsv(filteredThoughtItems)
    downloadText(`${exportName}.csv`, csv, 'text/csv;charset=utf-8')
  }, [activeBlock, filteredThoughtItems, researchId, result?.research_id])

  const handleCopyThoughtMarkdown = useCallback(async () => {
    if (!activeBlock) {
      message.warning('暂无可导出的任务')
      return
    }
    if (!filteredThoughtItems.length) {
      message.warning('暂无可导出的思维流')
      return
    }
    const markdown = buildThoughtMarkdown(filteredThoughtItems, {
      researchId: result?.research_id || researchId,
      block: activeBlock,
      filters: {
        types: thoughtTypeFilter,
        keyword: thoughtSearchText,
      },
    })
    const ok = await copyText(markdown)
    if (ok) {
      message.success('已复制 Markdown')
    } else {
      message.error('复制失败')
    }
  }, [
    activeBlock,
    filteredThoughtItems,
    researchId,
    result?.research_id,
    thoughtSearchText,
    thoughtTypeFilter,
  ])

  const handleExportThoughtMarkdown = useCallback(() => {
    if (!activeBlock) {
      message.warning('暂无可导出的任务')
      return
    }
    if (!filteredThoughtItems.length) {
      message.warning('暂无可导出的思维流')
      return
    }
    const exportName = buildThoughtExportBaseName(
      result?.research_id || researchId,
      activeBlock.block_id,
    )
    const markdown = buildThoughtMarkdown(filteredThoughtItems, {
      researchId: result?.research_id || researchId,
      block: activeBlock,
      filters: {
        types: thoughtTypeFilter,
        keyword: thoughtSearchText,
      },
    })
    downloadText(`${exportName}.md`, markdown, 'text/markdown;charset=utf-8')
  }, [
    activeBlock,
    filteredThoughtItems,
    researchId,
    result?.research_id,
    thoughtSearchText,
    thoughtTypeFilter,
  ])

  const handleExportThoughtHtml = useCallback(() => {
    if (!activeBlock) {
      message.warning('暂无可导出的任务')
      return
    }
    if (!filteredThoughtItems.length) {
      message.warning('暂无可导出的思维流')
      return
    }
    const exportName = buildThoughtExportBaseName(
      result?.research_id || researchId,
      activeBlock.block_id,
    )
    const html = buildThoughtHtml(filteredThoughtItems, {
      researchId: result?.research_id || researchId,
      block: activeBlock,
      filters: {
        types: thoughtTypeFilter,
        keyword: thoughtSearchText,
      },
    })
    downloadText(`${exportName}.html`, html, 'text/html;charset=utf-8')
  }, [
    activeBlock,
    filteredThoughtItems,
    researchId,
    result?.research_id,
    thoughtSearchText,
    thoughtTypeFilter,
  ])

  const blockOptions = useMemo(
    () =>
      blocks.map((block) => ({
        label: `${block.title} (${block.block_id})`,
        value: block.block_id,
      })),
    [blocks],
  )

  const toolTraces = useMemo<TraceRecord[]>(() => {
    const traces: TraceRecord[] = []
    blocks.forEach((block) => {
      ;(block.tool_traces || []).forEach((trace) => {
        traces.push({
          ...trace,
          block_id: block.block_id,
          title: block.title,
        })
      })
    })
    return traces
  }, [blocks])

  const traceTypeOptions = useMemo(() => {
    const types = Array.from(new Set(toolTraces.map((trace) => trace.tool_type))).filter(Boolean)
    return types.map((value) => ({ label: value, value }))
  }, [toolTraces])

  const traceStageOptions = useMemo(
    () => [
      { label: '研究', value: 'researching' },
      { label: '报告', value: 'reporting' },
    ],
    [],
  )

  const filteredTraces = useMemo(() => {
    let filtered = toolTraces
    if (traceTypeFilter.length) {
      filtered = filtered.filter((trace) => traceTypeFilter.includes(trace.tool_type))
    }
    if (traceStageFilter) {
      filtered = filtered.filter((trace) => getTraceStage(trace.tool_type) === traceStageFilter)
    }
    if (traceBlockFilter) {
      filtered = filtered.filter((trace) => trace.block_id === traceBlockFilter)
    }
    if (traceSearchText.trim()) {
      const keyword = traceSearchText.trim().toLowerCase()
      filtered = filtered.filter((trace) => {
        const haystack = `${trace.tool_id} ${trace.query} ${trace.summary}`.toLowerCase()
        return haystack.includes(keyword)
      })
    }
    return filtered
  }, [toolTraces, traceTypeFilter, traceStageFilter, traceBlockFilter, traceSearchText])

  const latestEvent = useMemo(
    () => (progressEvents.length ? progressEvents[progressEvents.length - 1] : null),
    [progressEvents],
  )

  const stageOrder = ['planning', 'researching', 'reporting', 'completed']
  const stageLabels: Record<string, string> = {
    planning: '规划',
    researching: '研究',
    reporting: '报告',
    completed: '完成',
  }

  const currentStage = useMemo(() => {
    if (currentStatus === 'completed') return 'completed'
    if (currentStatus === 'failed' || currentStatus === 'cancelled') {
      if (latestEvent?.stage && stageOrder.includes(latestEvent.stage)) return latestEvent.stage
      return 'researching'
    }
    if (latestEvent?.stage && stageOrder.includes(latestEvent.stage)) return latestEvent.stage
    return 'planning'
  }, [currentStatus, latestEvent?.stage])

  const currentStageIndex = stageOrder.indexOf(currentStage)
  type StepStatus = 'wait' | 'process' | 'finish' | 'error'
  const stepItems: Array<{ title: string; status: StepStatus }> = stageOrder.map(
    (stage, index) => {
      let status: StepStatus = 'wait'
      if (index < currentStageIndex) {
        status = 'finish'
      } else if (index === currentStageIndex) {
        status =
          currentStatus === 'failed' || currentStatus === 'cancelled' ? 'error' : 'process'
      }
      return {
        title: stageLabels[stage] || stage,
        status,
      }
    },
  )

  useEffect(() => {
    if (currentStage === 'completed') {
      setProcessTab('reporting')
      return
    }
    if (currentStage === 'planning' || currentStage === 'researching' || currentStage === 'reporting') {
      setProcessTab(currentStage)
    }
  }, [currentStage])

  const derivedSummary = useMemo(() => {
    const blocksByStatus: Record<string, number> = {
      pending: 0,
      researching: 0,
      completed: 0,
      failed: 0,
      skipped: 0,
    }
    blocks.forEach((block) => {
      blocksByStatus[block.status] = (blocksByStatus[block.status] || 0) + 1
    })
    const toolTracesByType: Record<string, number> = {}
    toolTraces.forEach((trace) => {
      toolTracesByType[trace.tool_type] = (toolTracesByType[trace.tool_type] || 0) + 1
    })
    const errors = toolTraces.filter((trace) => isTraceError(trace)).map((trace) => ({
      block_id: trace.block_id,
      tool_id: trace.tool_id,
      tool_type: trace.tool_type,
      summary: trace.summary,
      timestamp: trace.timestamp,
    }))
    return {
      blocks_total: blocks.length,
      blocks_by_status: blocksByStatus,
      citations_total: citations.length,
      tool_traces_total: toolTraces.length,
      tool_traces_by_type: toolTracesByType,
      decisions_total: blocks.reduce((acc, block) => acc + (block.decisions?.length || 0), 0),
      errors,
    }
  }, [blocks, citations.length, toolTraces])

  const runSummary = useMemo(() => {
    return (
      (result?.trace as any)?.summary ||
      (snapshot?.report as any)?.summary ||
      derivedSummary
    )
  }, [derivedSummary, result?.trace, snapshot?.report])

  const handleExportArchive = useCallback(async () => {
    const id = (result?.research_id || researchId).trim()
    if (!id) {
      message.warning('请先输入 Research ID')
      return
    }
    setArchiveLoading(true)
    try {
      const { data } = await api.deepResearch.getDeepResearchArchive(id, { errorToast: false })
      const payload = { ...data, exported_at: dayjs().toISOString() }
      downloadJson(`deep-research-archive-${id}.json`, payload)
      message.success('运行档案已导出')
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message
      message.error(detail ? `运行档案导出失败：${detail}` : '运行档案导出失败')
    } finally {
      setArchiveLoading(false)
    }
  }, [researchId, result?.research_id])

  const handleExportBlockEvidence = useCallback(async () => {
    if (!selectedBlock) {
      message.warning('请先选择 Block')
      return
    }
    const id = (result?.research_id || researchId).trim()
    if (!id) {
      message.warning('请先输入 Research ID')
      return
    }
    setBlockEvidenceLoading(true)
    try {
      const { data } = await api.deepResearch.getDeepResearchBlockEvidence(
        id,
        selectedBlock.block_id,
        { errorToast: false },
      )
      const payload = { ...data, exported_at: dayjs().toISOString() }
      downloadJson(`deep-research-block-${selectedBlock.block_id}.json`, payload)
      message.success('Block 证据包已导出')
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message
      message.error(detail ? `证据包导出失败：${detail}` : '证据包导出失败')
    } finally {
      setBlockEvidenceLoading(false)
    }
  }, [researchId, result?.research_id, selectedBlock])

  const runStatusOptions = useMemo(() => {
    const statuses = Array.from(new Set(runList.map((item) => item.status))).filter(Boolean)
    return statuses.map((status) => ({ label: status, value: status }))
  }, [runList])

  const filteredRunList = useMemo(() => {
    let filtered = runList
    if (runStatusFilter.length) {
      filtered = filtered.filter((item) => runStatusFilter.includes(item.status))
    }
    if (runSearchText.trim()) {
      const keyword = runSearchText.trim().toLowerCase()
      filtered = filtered.filter((item) => {
        const haystack = `${item.topic} ${item.research_id}`.toLowerCase()
        return haystack.includes(keyword)
      })
    }
    return filtered
  }, [runList, runStatusFilter, runSearchText])

  const sortedRunList = useMemo(() => {
    const list = [...filteredRunList]
    const getTimestamp = (value?: string) => (value ? dayjs(value).valueOf() : 0)
    list.sort((a, b) => {
      switch (runSortKey) {
        case 'started_asc':
          return getTimestamp(a.started_at) - getTimestamp(b.started_at)
        case 'duration_desc':
          return (b.duration_seconds || 0) - (a.duration_seconds || 0)
        case 'duration_asc':
          return (a.duration_seconds || 0) - (b.duration_seconds || 0)
        case 'status':
          return a.status.localeCompare(b.status)
        case 'topic':
          return a.topic.localeCompare(b.topic)
        case 'started_desc':
        default:
          return getTimestamp(b.started_at) - getTimestamp(a.started_at)
      }
    })
    return list
  }, [filteredRunList, runSortKey])

  const runSelectOptions = useMemo(
    () =>
      runList.map((item) => ({
        label: `${item.topic} (${item.research_id})`,
        value: item.research_id,
      })),
    [runList],
  )

  const compareA = useMemo(
    () => runList.find((item) => item.research_id === compareRunA),
    [runList, compareRunA],
  )
  const compareB = useMemo(
    () => runList.find((item) => item.research_id === compareRunB),
    [runList, compareRunB],
  )

  const getSummary = useCallback((item?: RunListItem) => {
    return (
      item?.summary || {
        blocks_total: 0,
        blocks_by_status: {},
        citations_total: 0,
        tool_traces_total: 0,
        tool_traces_by_type: {},
        decisions_total: 0,
        errors: [],
      }
    )
  }, [])

  const handleExportRuns = useCallback(() => {
    if (!sortedRunList.length) {
      message.warning('暂无运行记录可导出')
      return
    }
    const header = [
      'research_id',
      'status',
      'topic',
      'mode',
      'started_at',
      'finished_at',
      'duration_seconds',
      'citations_total',
      'tool_traces_total',
      'decisions_total',
      'errors_count',
    ]
    const rows = sortedRunList.map((item) => {
      const summary = getSummary(item)
      return [
        csvEscape(item.research_id),
        csvEscape(item.status),
        csvEscape(item.topic),
        csvEscape(item.mode),
        csvEscape(item.started_at),
        csvEscape(item.finished_at),
        csvEscape(item.duration_seconds ?? ''),
        csvEscape(summary.citations_total ?? ''),
        csvEscape(summary.tool_traces_total ?? ''),
        csvEscape(summary.decisions_total ?? ''),
        csvEscape(summary.errors?.length ?? 0),
      ].join(',')
    })
    const csv = [header.join(','), ...rows].join('\n')
    downloadText('deep-research-runs.csv', csv, 'text/csv;charset=utf-8')
  }, [getSummary, sortedRunList])

  const handleExportRunsJson = useCallback(() => {
    if (!sortedRunList.length) {
      message.warning('暂无运行记录可导出')
      return
    }
    downloadJson('deep-research-runs.json', sortedRunList)
  }, [sortedRunList])

  const handleExportCurrentRequest = useCallback(async () => {
    const metadata = safeParseJson(metadataText)
    if (metadata === null) {
      message.warning('Metadata JSON 格式错误')
      return
    }
    const presetMeta =
      activePresetKey !== 'custom' ? { deep_research_preset: activePresetKey } : {}
    const snippets = useCodeExec ? parseSnippets(codeSnippetsText) : []
    const payload: DeepResearchRequest = {
      topic: topic.trim(),
      mode,
      depth,
      breadth,
      max_parallel: maxParallel,
      max_iterations: maxIterations,
      top_k: topK || undefined,
      index_mode: indexMode || undefined,
      session_id: sessionId || undefined,
      language: language || undefined,
      report_style: reportStyle || undefined,
      use_web_search: useWebSearch,
      use_code_exec: useCodeExec,
      code_exec_snippets: snippets,
      metadata: { ...(metadata || {}), ...presetMeta },
    }
    const ok = await copyText(JSON.stringify(payload, null, 2))
    if (ok) {
      message.success('已复制当前参数')
    } else {
      message.error('复制失败，请手动复制')
    }
  }, [
    metadataText,
    activePresetKey,
    useCodeExec,
    codeSnippetsText,
    topic,
    mode,
    depth,
    breadth,
    maxParallel,
    maxIterations,
    topK,
    indexMode,
    sessionId,
    language,
    reportStyle,
    useWebSearch,
    useCodeExec,
  ])

  const renderCompareValue = useCallback((valueA: number, valueB: number) => {
    const delta = valueA - valueB
    const color = delta === 0 ? 'default' : delta > 0 ? 'green' : 'red'
    const deltaText = delta === 0 ? '0' : delta > 0 ? `+${delta}` : `${delta}`
    return (
      <Space>
        <Text>{valueA}</Text>
        <Text type="secondary">vs</Text>
        <Text>{valueB}</Text>
        <Tag color={color}>{deltaText}</Tag>
      </Space>
    )
  }, [])

  const diagnosisTips = useMemo(() => {
    const tips: string[] = []
    if (!runSummary) {
      return ['暂无运行数据，请先执行 DeepResearch。']
    }
    const blocks = runSummary.blocks_total || 0
    const blocksByStatus = runSummary.blocks_by_status || {}
    const failed = blocksByStatus.failed || 0
    const skipped = blocksByStatus.skipped || 0
    const completed = blocksByStatus.completed || 0
    const citations = runSummary.citations_total || 0
    const toolTraces = runSummary.tool_traces_total || 0
    const decisions = runSummary.decisions_total || 0
    const errors = runSummary.errors?.length || 0
    const toolTypes = runSummary.tool_traces_by_type || {}

    if (blocks === 0) {
      tips.push('未生成研究块，检查规划阶段是否成功输出子主题。')
    }
    if (completed === 0 && blocks > 0) {
      tips.push('没有完成的研究块，检查检索服务或队列调度是否失败。')
    }
    if (failed > 0) {
      tips.push('存在失败块，建议查看工具链错误并重试。')
    }
    if (skipped > 0) {
      tips.push('存在跳过块，通常是缺少 session_id 或索引不可用。')
    }
    if (citations < Math.max(2, blocks)) {
      tips.push('引用数量偏低，建议提高 top_k 或开启 Web Search。')
    }
    if (!toolTypes.search && citations < Math.max(2, blocks)) {
      tips.push('未使用 Web Search，可考虑启用以补充证据来源。')
    }
    if (!toolTypes.compare && citations >= 2) {
      tips.push('未启用 Compare，可尝试开启跨文档对比提升深度。')
    }
    if (toolTraces < blocks) {
      tips.push('工具调用次数偏低，可能存在链路早停或失败。')
    }
    if (decisions === 0) {
      tips.push('决策记录为空，检查 DecisionAgent 是否启用。')
    }
    if (errors > 0) {
      tips.push('存在工具错误，建议查看错误摘要与原始 Trace。')
    }
    if (!tips.length) {
      tips.push('运行正常，暂无明显风险。')
    }
    return tips
  }, [runSummary])

  const progressTimelineItems = useMemo(
    () => buildTimelineItems(progressEvents),
    [progressEvents],
  )
  const blockProgressTimelineItems = useMemo(() => {
    if (!selectedBlock) return []
    return buildTimelineItems(
      progressEvents.filter(
        (event) => (event.payload as any)?.block_id === selectedBlock.block_id,
      ),
    )
  }, [progressEvents, selectedBlock])
  const blockTraceTimelineItems = useMemo(() => {
    if (!selectedBlock?.tool_traces?.length) return []
    const sorted = [...selectedBlock.tool_traces].sort((a: any, b: any) => {
      const aTime = a.timestamp ? dayjs(a.timestamp).valueOf() : 0
      const bTime = b.timestamp ? dayjs(b.timestamp).valueOf() : 0
      return aTime - bTime
    })
    return sorted
      .map((trace: any) => ({
        color: isTraceError(trace) ? 'red' : 'blue',
        children: (
          <Space direction="vertical" size={2}>
            <Space>
              <Tag color={TOOL_TYPE_COLOR[trace.tool_type] || 'default'}>
                {trace.tool_type}
              </Tag>
              {isTraceError(trace) ? <Tag color="red">error</Tag> : null}
              <Text>{trace.tool_id}</Text>
              {trace.timestamp ? (
                <Text type="secondary">
                  {dayjs(trace.timestamp).format('HH:mm:ss')}
                </Text>
              ) : null}
            </Space>
            <Text type="secondary">{trace.summary || trace.query}</Text>
          </Space>
        ),
      }))
      .reverse()
  }, [selectedBlock])
  const planningTimelineItems = useMemo(
    () => buildTimelineItems(progressEvents.filter((item) => item.stage === 'planning')),
    [progressEvents],
  )
  const researchTimelineItems = useMemo(
    () => buildTimelineItems(progressEvents.filter((item) => item.stage === 'researching')),
    [progressEvents],
  )
  const reportTimelineItems = useMemo(
    () => buildTimelineItems(progressEvents.filter((item) => item.stage === 'reporting')),
    [progressEvents],
  )

  const blockTree = useMemo(() => {
    if (!blocks.length) return []
    const map = new Map(blocks.map((item) => [item.block_id, item]))
    const buildNode = (block: TopicBlock): any => ({
      key: block.block_id,
      title: (
        <Space size={6}>
          <Tag color={getStatusColor(block.status)}>{block.status}</Tag>
          <Text strong>{block.title}</Text>
          <Text type="secondary">{block.block_id}</Text>
        </Space>
      ),
      children: (block.child_ids || [])
        .map((id) => map.get(id))
        .filter(Boolean)
        .map((child) => buildNode(child as TopicBlock)),
    })
    return blocks
      .filter((item) => !item.parent_id)
      .map((root) => buildNode(root))
  }, [blocks, getStatusColor])

  const planColumns: ColumnsType<PlanItem> = useMemo(
    () => [
      { title: '标题', dataIndex: 'title', width: 180 },
      { title: '问题', dataIndex: 'question', ellipsis: true },
      { title: '深度', dataIndex: 'depth', width: 70 },
      {
        title: '父主题',
        dataIndex: 'parent_title',
        width: 140,
        render: (value: string | null) => value || '-',
      },
    ],
    [],
  )

  const blockColumns: ColumnsType<TopicBlock> = useMemo(
    () => [
      { title: 'Block', dataIndex: 'block_id', width: 90 },
      { title: '标题', dataIndex: 'title', width: 200 },
      {
        title: '状态',
        dataIndex: 'status',
        width: 100,
        render: (value: string) => <Tag color={getStatusColor(value)}>{value}</Tag>,
      },
      { title: '深度', dataIndex: 'depth', width: 70 },
      {
        title: 'Notes',
        dataIndex: 'notes',
        width: 80,
        render: (notes: string[]) => notes?.length ?? 0,
      },
      {
        title: '引用',
        dataIndex: 'citations',
        width: 80,
        render: (items: string[]) => items?.length ?? 0,
      },
      {
        title: '工具调用',
        dataIndex: 'tool_traces',
        width: 90,
        render: (items: any[]) => items?.length ?? 0,
      },
      {
        title: '决策',
        dataIndex: 'decisions',
        width: 80,
        render: (items: any[]) => items?.length ?? 0,
      },
      { title: '更新时间', dataIndex: 'updated_at' },
    ],
    [getStatusColor],
  )

  const traceColumns: ColumnsType<TraceRecord> = useMemo(
    () => [
      { title: 'Block', dataIndex: 'block_id', width: 80 },
      { title: '主题', dataIndex: 'title', width: 160, ellipsis: true },
      {
        title: '阶段',
        dataIndex: 'tool_type',
        width: 90,
        render: (value: string) => (
          <Tag color="blue">{getTraceStage(value)}</Tag>
        ),
      },
      { title: '工具', dataIndex: 'tool_id', width: 180 },
      {
        title: '类型',
        dataIndex: 'tool_type',
        width: 90,
        render: (value: string) => (
          <Tag color={TOOL_TYPE_COLOR[value] || 'default'}>{value}</Tag>
        ),
      },
      {
        title: '状态',
        dataIndex: 'summary',
        width: 90,
        render: (_: string, record: TraceRecord) => {
          const isError = isTraceError(record)
          return <Tag color={isError ? 'red' : 'green'}>{isError ? 'error' : 'ok'}</Tag>
        },
      },
      {
        title: 'Query',
        dataIndex: 'query',
        ellipsis: true,
      },
      {
        title: '摘要',
        dataIndex: 'summary',
        ellipsis: true,
      },
      { title: '时间', dataIndex: 'timestamp', width: 180 },
    ],
    [],
  )

  const citationColumns: ColumnsType<DeepResearchCitation> = useMemo(
    () => [
      { title: '编号', dataIndex: 'ref_number', width: 70 },
      { title: '标题', dataIndex: 'title', width: 260 },
      {
        title: '链接',
        dataIndex: 'url',
        render: (value: string | undefined) =>
          value ? (
            <a href={value} target="_blank" rel="noreferrer">
              打开
            </a>
          ) : (
            '-'
          ),
      },
      { title: '摘要', dataIndex: 'snippet', ellipsis: true },
    ],
    [],
  )

  const sessionOptions = useMemo(
    () =>
      sessions.map((item) => ({
        label: `${item.session_name || '会话'} (${item.session_id})`,
        value: item.session_id,
      })),
    [sessions],
  )

  return (
    <div className={styles.container}>
      <div className={styles.side}>
        <Card title="研究参数" className={styles.section} id={PARAMS_ANCHOR_ID}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <div>
              <Text>主题</Text>
              <Input
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="例如：Transformer 模型的训练优化"
              />
            </div>
            <div>
              <Text>Session</Text>
              <Select
                value={sessionId || undefined}
                onChange={(value) => setSessionId(value)}
                options={sessionOptions}
                placeholder="选择已有会话（用于 RAG）"
                allowClear
                showSearch
                optionFilterProp="label"
              />
            </div>
            <div className={styles.grid}>
              <div>
                <Text>预设</Text>
                <Select
                  value={activePresetKey}
                  onChange={(value) => applyPreset(value as PresetKey)}
                  options={PRESET_OPTIONS.map((item) => ({
                    label: `${item.label}（${item.description}）`,
                    value: item.value,
                  }))}
                />
                {activePresetDesc ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {activePresetDesc}
                  </Text>
                ) : null}
              </div>
              <div>
                <Text>模式</Text>
                <Select
                  value={mode}
                  onChange={(value) => setMode(value)}
                  options={[
                    { label: 'Queue', value: 'queue' },
                    { label: 'Tree', value: 'tree' },
                  ]}
                />
              </div>
              <div>
                <Text>Depth</Text>
                <InputNumber
                  min={1}
                  max={6}
                  value={depth}
                  onChange={(v) => setDepth(v ?? DEFAULTS.depth)}
                />
              </div>
              <div>
                <Text>Breadth</Text>
                <InputNumber
                  min={1}
                  max={12}
                  value={breadth}
                  onChange={(v) => setBreadth(v ?? DEFAULTS.breadth)}
                />
              </div>
              <div>
                <Text>并发</Text>
                <InputNumber
                  min={1}
                  max={10}
                  value={maxParallel}
                  onChange={(v) => setMaxParallel(v ?? DEFAULTS.maxParallel)}
                />
              </div>
              <div>
                <Text>迭代</Text>
                <InputNumber
                  min={1}
                  max={10}
                  value={maxIterations}
                  onChange={(v) => setMaxIterations(v ?? DEFAULTS.maxIterations)}
                />
              </div>
              <div>
                <Text>TopK</Text>
                <InputNumber
                  min={1}
                  max={50}
                  value={topK}
                  onChange={(v) => setTopK(v ?? DEFAULTS.topK)}
                />
              </div>
              <div>
                <Text>Index</Text>
                <Select
                  value={indexMode}
                  onChange={(value) => setIndexMode(value)}
                  options={[
                    { label: 'auto', value: 'auto' },
                    { label: 'session_only', value: 'session_only' },
                    { label: 'global_only', value: 'global_only' },
                    { label: 'hybrid', value: 'hybrid' },
                  ]}
                />
              </div>
              <div>
                <Text>语言</Text>
                <Input
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                  placeholder="zh / en"
                />
              </div>
            </div>
            <div>
              <Text>Report Style</Text>
              <Input
                value={reportStyle}
                onChange={(event) => setReportStyle(event.target.value)}
                placeholder="例如：学术综述 / 技术分析"
              />
            </div>
            <Space size={12} wrap>
              <span>
                WebSearch <Switch checked={useWebSearch} onChange={setUseWebSearch} />
              </span>
              <span>
                CodeExec <Switch checked={useCodeExec} onChange={setUseCodeExec} />
              </span>
            </Space>
            {useCodeExec ? (
              <div>
                <Text>代码片段（用 --- 分隔）</Text>
                <Input.TextArea
                  className={styles.textarea}
                  value={codeSnippetsText}
                  onChange={(event) => setCodeSnippetsText(event.target.value)}
                  placeholder="print('hello')\n---\nimport math\nprint(math.sqrt(2))"
                />
              </div>
            ) : null}
            <div>
              <Text>Metadata (JSON 可选)</Text>
              <Input.TextArea
                className={styles.textarea}
                value={metadataText}
                onChange={(event) => setMetadataText(event.target.value)}
                placeholder='{"team":"research","priority":"high"}'
              />
            </div>
            <Space>
              <Button type="primary" loading={runLoading} onClick={() => handleRunResearch()}>
                运行 DeepResearch
              </Button>
              <Button
                danger
                loading={cancelLoading && cancelLoadingId === currentResearchId}
                onClick={() => handleCancelRun()}
                disabled={!currentResearchId || !canCancel}
              >
                取消运行
              </Button>
              <Button
                loading={resumeLoading && resumeLoadingId === currentResearchId}
                onClick={() => handleResumeRun()}
                disabled={!currentResearchId || !canResume}
              >
                恢复运行
              </Button>
              <Button onClick={handleExportCurrentRequest}>复制当前参数</Button>
              <Button
                onClick={() => {
                  setResult(null)
                  setSnapshot(null)
                  setProgressEvents([])
                  setUseStream(false)
                }}
              >
                清空结果
              </Button>
            </Space>
          </Space>
        </Card>

        <Card title="快照 / 进度" className={styles.section}>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <Input
              value={researchId}
              onChange={(event) => setResearchId(event.target.value)}
              placeholder="Research ID"
            />
            <Space>
              <Button onClick={handleLoadSnapshot} loading={snapshotLoading || progressLoading}>
                加载快照
              </Button>
              <Button
                onClick={() =>
                  runProgress({ id: researchId.trim(), mode: 'tail' })
                }
                loading={progressLoading}
              >
                刷新进度
              </Button>
            </Space>
            <Space>
              <span>
                自动刷新{' '}
                <Switch
                  checked={autoRefresh}
                  onChange={(checked) => {
                    setAutoRefresh(checked)
                    if (checked) {
                      setUseStream(false)
                    }
                  }}
                />
              </span>
            </Space>
            <Space>
              <span>
                实时流{' '}
                <Switch
                  checked={useStream}
                  onChange={(checked) => {
                    setUseStream(checked)
                    if (checked) {
                      setAutoRefresh(false)
                    }
                  }}
                />
              </span>
            </Space>
            <Space>
              <Tag color={streamStatus === 'connected' ? 'green' : streamStatus === 'error' ? 'red' : 'default'}>
                {streamStatus === 'connected'
                  ? '在线'
                  : streamStatus === 'connecting'
                    ? '连接中'
                    : streamStatus === 'error'
                      ? `断开 (${streamRetries})`
                      : '未启用'}
              </Tag>
              <Button
                size="small"
                disabled={!useStream}
                onClick={() => setStreamNonce((value) => value + 1)}
              >
                重连
              </Button>
            </Space>
          </Space>
        </Card>

        <Card
          title="队列状态"
          className={styles.section}
          extra={
            <Button size="small" onClick={() => refreshQueueStatus()} loading={queueLoading}>
              刷新
            </Button>
          }
        >
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="并发">
              {queueStatus ? `${queueStatus.active_runs}/${queueStatus.max_active_runs}` : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="排队">
              {queueStatus ? queueStatus.pending_runs : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="运行中">
              {queueStatus ? queueStatus.active_items.length : '-'}
            </Descriptions.Item>
          </Descriptions>
          <Divider style={{ margin: '12px 0' }} />
          {queueStatus?.pending_items?.length ? (
            <List
              size="small"
              dataSource={queueStatus.pending_items.slice(0, 5)}
              renderItem={(item) => (
                <List.Item>
                  <Space>
                    <Tag color={getStatusColor(item.status)}>{item.status}</Tag>
                    {typeof item.priority === 'number' ? (
                      <Tag color="purple">P{item.priority}</Tag>
                    ) : null}
                    {typeof item.effective_priority === 'number' ? (
                      <Tag color="cyan">EP{item.effective_priority}</Tag>
                    ) : null}
                    <Text>{item.topic || item.research_id}</Text>
                    {typeof item.wait_seconds === 'number' ? (
                      <Text type="secondary">等待 {formatDuration(item.wait_seconds)}</Text>
                    ) : null}
                  </Space>
                </List.Item>
              )}
            />
          ) : (
            <Text type="secondary">暂无排队任务</Text>
          )}
          <Divider style={{ margin: '12px 0' }} />
          <Space>
            <InputNumber
              min={-10}
              max={10}
              value={priorityInput ?? undefined}
              placeholder="优先级 (-10~10)"
              onChange={(value) => setPriorityInput(typeof value === 'number' ? value : null)}
            />
            <Button
              loading={priorityLoading}
              onClick={() => handleUpdatePriority()}
              disabled={!currentResearchId}
            >
              更新当前优先级
            </Button>
          </Space>
        </Card>

        <Card
          title="运行历史"
          className={styles.section}
          extra={
            <Space>
              <Button size="small" onClick={() => refreshRuns()} loading={runsLoading}>
                刷新
              </Button>
              <Button size="small" onClick={handleExportRuns}>
                导出CSV
              </Button>
              <Button size="small" onClick={handleExportRunsJson}>
                导出JSON
              </Button>
            </Space>
          }
        >
          <Space wrap className={styles.filterRow}>
            <Select
              mode="multiple"
              allowClear
              placeholder="过滤状态"
              options={runStatusOptions}
              value={runStatusFilter}
              onChange={(value) => setRunStatusFilter(value)}
              style={{ minWidth: 160 }}
            />
            <Input
              placeholder="搜索 topic / research_id"
              value={runSearchText}
              onChange={(event) => setRunSearchText(event.target.value)}
              style={{ minWidth: 220 }}
            />
            <Select
              placeholder="排序"
              value={runSortKey}
              onChange={(value) => setRunSortKey(value)}
              style={{ minWidth: 140 }}
              options={[
                { label: '开始时间↓', value: 'started_desc' },
                { label: '开始时间↑', value: 'started_asc' },
                { label: '耗时↓', value: 'duration_desc' },
                { label: '耗时↑', value: 'duration_asc' },
                { label: '状态', value: 'status' },
                { label: '主题', value: 'topic' },
              ]}
            />
            <Select
              placeholder="每页"
              value={runPageSize}
              onChange={(value) => setRunPageSize(value)}
              style={{ minWidth: 90 }}
              options={[4, 8, 12, 20].map((value) => ({ label: String(value), value }))}
            />
            <Button
              onClick={() => {
                setRunStatusFilter([])
                setRunSearchText('')
                setRunSortKey('started_desc')
              }}
            >
              清空
            </Button>
            <Tag>匹配 {sortedRunList.length}</Tag>
          </Space>
          {sortedRunList.length ? (
            <List
              size="small"
              dataSource={sortedRunList}
              pagination={{ pageSize: runPageSize, showSizeChanger: false }}
              renderItem={(item) => (
                <List.Item
                  className={styles.clickableRow}
                  onClick={() => handleSelectRun(item)}
                >
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Space wrap>
                      <Tag color={getStatusColor(item.status)}>{item.status}</Tag>
                      <Text strong>{item.topic}</Text>
                      {typeof item.priority === 'number' ? (
                        <Tag color="purple">P{item.priority}</Tag>
                      ) : null}
                    </Space>
                    <Text type="secondary">{item.research_id}</Text>
                    <Text type="secondary">
                      {item.started_at
                        ? dayjs(item.started_at).format('YYYY-MM-DD HH:mm')
                        : '-'}{' '}
                      · {formatDuration(item.duration_seconds)}
                    </Text>
                    {item.error ? <Text type="danger">error: {item.error}</Text> : null}
                    <Space wrap>
                      <Button
                        size="small"
                        onClick={(event) => {
                          event.stopPropagation()
                          handleSelectRun(item)
                        }}
                      >
                        加载
                      </Button>
                      <Button
                        size="small"
                        danger
                        loading={cancelLoading && cancelLoadingId === item.research_id}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleCancelRun(item.research_id)
                        }}
                        disabled={!['running', 'queued'].includes(item.status)}
                      >
                        取消
                      </Button>
                      <Button
                        size="small"
                        loading={resumeLoading && resumeLoadingId === item.research_id}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleResumeRun(item.research_id)
                        }}
                        disabled={!['failed', 'cancelled', 'running'].includes(item.status)}
                      >
                        恢复
                      </Button>
                      <Button
                        size="small"
                        loading={replayLoading || archiveReplayLoadingId === item.research_id}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleRerun(item)
                        }}
                        disabled={!item.research_id}
                      >
                        复跑
                      </Button>
                      <Button
                        size="small"
                        onClick={(event) => {
                          event.stopPropagation()
                          handleLoadRequest(item)
                        }}
                        disabled={!item.research_id}
                      >
                        加载参数
                      </Button>
                      <Button
                        size="small"
                        onClick={(event) => {
                          event.stopPropagation()
                          handleCopyRunId(item.research_id)
                        }}
                      >
                        复制ID
                      </Button>
                      <Button
                        size="small"
                        onClick={(event) => {
                          event.stopPropagation()
                          handleCopyRunRequest(item)
                        }}
                        disabled={!item.request}
                      >
                        复制参数
                      </Button>
                    </Space>
                  </Space>
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无运行记录" />
          )}
        </Card>
      </div>

      <div className={styles.content}>
        <Card className={styles.section}>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="Research ID">
              {result?.research_id || researchId || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Status">{currentStatus || '-'}</Descriptions.Item>
            <Descriptions.Item label="Blocks">{blocks.length}</Descriptions.Item>
          </Descriptions>
          <Space wrap className={styles.statRow}>
            <Tag color="default">pending {queueStats.pending}</Tag>
            <Tag color="blue">researching {queueStats.researching}</Tag>
            <Tag color="green">completed {queueStats.completed}</Tag>
            <Tag color="red">failed {queueStats.failed}</Tag>
            <Tag color="gold">skipped {queueStats.skipped}</Tag>
            {latestEvent ? (
              <Tooltip title={JSON.stringify(latestEvent.payload || {})}>
                <Tag>latest: {latestEvent.stage}</Tag>
              </Tooltip>
            ) : null}
          </Space>
          <Steps
            size="small"
            current={currentStageIndex >= 0 ? currentStageIndex : 0}
            items={stepItems}
          />
          <Divider style={{ margin: '12px 0' }} />
          <Space wrap>
            <Button onClick={handleExportReport}>导出报告</Button>
            <Button onClick={handleExportHtml}>导出 HTML</Button>
            <Button onClick={handleExportPdf}>导出 PDF</Button>
            <Button onClick={handlePrintReport}>打印 / PDF</Button>
            <Button loading={importingToStudio} onClick={handleOpenInDocStudio}>
              导入到 Doc Studio
            </Button>
            <Button
              onClick={() =>
                downloadJson(
                  'deep-research-trace.json',
                  result?.trace || snapshot?.queue || {},
                )
              }
            >
              导出 Trace
            </Button>
            <Button
              onClick={() =>
                downloadJson(
                  'deep-research-queue.json',
                  result?.trace?.queue || snapshot?.queue || {},
                )
              }
            >
              导出队列
            </Button>
            <Button onClick={() => downloadJson('deep-research-summary.json', runSummary)}>
              导出 Summary
            </Button>
            <Button loading={archiveLoading} onClick={handleExportArchive}>
              导出运行档案
            </Button>
            <Button
              loading={
                replayLoading ||
                archiveReplayLoadingId === (result?.research_id || researchId)
              }
              onClick={() => handleReplayArchive()}
            >
              档案复现
            </Button>
          </Space>
        </Card>

        <Tabs
          items={[
            {
              key: 'process',
              label: '过程',
              children: (
                <Tabs
                  activeKey={processTab}
                  onChange={(key) => setProcessTab(key as ProcessTab)}
                  items={[
                    {
                      key: 'planning',
                      label: `规划 (${planningBlocks.length})`,
                      children: (
                        <div className={styles.summaryGrid}>
                          <Card size="small" title="规划概览">
                            <Descriptions size="small" column={2}>
                              <Descriptions.Item label="研究主题">
                                {topic || rootBlock?.title || '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="模式">{mode}</Descriptions.Item>
                              <Descriptions.Item label="子主题">
                                {planningBlocks.length}
                              </Descriptions.Item>
                              <Descriptions.Item label="阶段">
                                {stageLabels[currentStage] || currentStage}
                              </Descriptions.Item>
                            </Descriptions>
                            <Divider style={{ margin: '12px 0' }} />
                            {planningBlocks.length ? (
                              <div className={styles.tagWrap}>
                                {planningBlocks.map((block) => {
                                  const childrenCount = blockChildCounts.get(block.block_id) || 0
                                  return (
                                    <Tag
                                      key={block.block_id}
                                      color={getStatusColor(block.status)}
                                    >
                                      {block.title}
                                      {childrenCount ? ` (+${childrenCount})` : ''}
                                    </Tag>
                                  )
                                })}
                              </div>
                            ) : (
                              <Empty description="暂无规划子主题" />
                            )}
                          </Card>
                          <Card size="small" title="规划日志">
                            {planningTimelineItems.length ? (
                              <Timeline items={planningTimelineItems} />
                            ) : (
                              <Empty description="暂无规划记录" />
                            )}
                          </Card>
                          <Card size="small" title="规划清单">
                            {planItems.length ? (
                              <Table
                                rowKey={(record, idx) =>
                                  `${record.depth}-${record.title}-${idx ?? 0}`
                                }
                                size="small"
                                pagination={false}
                                columns={planColumns}
                                dataSource={planItems}
                              />
                            ) : (
                              <Empty description="暂无规划条目" />
                            )}
                          </Card>
                        </div>
                      ),
                    },
                    {
                      key: 'researching',
                      label: `研究 (${queueStats.researching}/${blocks.length})`,
                      children: (
                        <>
                          <div className={styles.summaryGrid}>
                            <Card size="small" title="研究概览">
                              <Descriptions size="small" column={3}>
                                <Descriptions.Item label="总 Blocks">
                                  {blocks.length}
                                </Descriptions.Item>
                                <Descriptions.Item label="活跃">
                                  {queueStats.researching}
                                </Descriptions.Item>
                                <Descriptions.Item label="待研究">
                                  {queueStats.pending}
                                </Descriptions.Item>
                                <Descriptions.Item label="已完成">
                                  {queueStats.completed}
                                </Descriptions.Item>
                                <Descriptions.Item label="失败">
                                  {queueStats.failed}
                                </Descriptions.Item>
                                <Descriptions.Item label="工具调用">
                                  {toolTraces.length}
                                </Descriptions.Item>
                              </Descriptions>
                              <Divider style={{ margin: '12px 0' }} />
                              <div className={styles.toolWrap}>
                                {Object.entries(
                                  (runSummary.tool_traces_by_type || {}) as Record<
                                    string,
                                    number
                                  >,
                                ).map(([key, value]) => (
                                  <Tag key={key} color={TOOL_TYPE_COLOR[key] || 'default'}>
                                    {key}: {value}
                                  </Tag>
                                ))}
                              </div>
                            </Card>
                          </div>
                          <div className={styles.taskSplit}>
                            <Card size="small" title={`任务网格 (${researchBlocks.length})`}>
                              {researchBlocks.length ? (
                                <div className={styles.taskGrid}>
                                  {researchBlocks.map((block) => {
                                    const tools = toolsByBlock.get(block.block_id) || []
                                    const latestAction =
                                      latestEventByBlock.get(block.block_id)?.message || '等待执行'
                                    const progressPercent = block.max_iterations
                                      ? Math.min(
                                          100,
                                          Math.round((block.iterations / block.max_iterations) * 100),
                                        )
                                      : 0
                                    return (
                                      <div
                                        key={block.block_id}
                                        className={`${styles.taskCard} ${
                                          block.status === 'researching'
                                            ? styles.taskCardActive
                                            : ''
                                        } ${
                                          selectedBlock?.block_id === block.block_id
                                            ? styles.taskCardSelected
                                            : ''
                                        }`}
                                        onClick={() => setSelectedBlock(block)}
                                      >
                                        <div className={styles.taskHeader}>
                                          <Text strong className={styles.taskTitle}>
                                            {block.title}
                                          </Text>
                                          <Tag color={getStatusColor(block.status)}>
                                            {block.status}
                                          </Tag>
                                        </div>
                                        <div className={styles.taskMeta}>
                                          depth {block.depth} · {block.block_id}
                                        </div>
                                        {block.max_iterations ? (
                                          <Progress
                                            percent={progressPercent}
                                            size="small"
                                            showInfo={false}
                                          />
                                        ) : null}
                                        <div className={styles.taskSummary}>
                                          <Text type="secondary">当前动作：</Text>
                                          <Text>{latestAction}</Text>
                                        </div>
                                        {tools.length ? (
                                          <div className={styles.toolWrap}>
                                            {tools.slice(0, 8).map((tool) => (
                                              <Tag
                                                key={`${block.block_id}-${tool}`}
                                                color={TOOL_TYPE_COLOR[tool] || 'default'}
                                              >
                                                {tool}
                                              </Tag>
                                            ))}
                                            {tools.length > 8 ? (
                                              <Tag>+{tools.length - 8}</Tag>
                                            ) : null}
                                          </div>
                                        ) : (
                                          <Text type="secondary">暂无工具调用</Text>
                                        )}
                                      </div>
                                    )
                                  })}
                                </div>
                              ) : (
                                <Empty description="暂无研究任务" />
                              )}
                            </Card>
                            <div className={styles.taskDetailSticky}>
                              <Card size="small" title="任务详情">
                                {activeBlock ? (
                                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                                  <Space wrap>
                                    <Tag color={getStatusColor(activeBlock.status)}>
                                      {activeBlock.status}
                                    </Tag>
                                    <Text strong>{activeBlock.title}</Text>
                                    <Text type="secondary">{activeBlock.block_id}</Text>
                                  </Space>
                                  <Descriptions size="small" column={2}>
                                    <Descriptions.Item label="Depth">
                                      {activeBlock.depth}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="迭代">
                                      {activeBlock.iterations}/{activeBlock.max_iterations}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="工具调用">
                                      {activeBlock.tool_traces?.length || 0}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="引用">
                                      {activeBlock.citations?.length || 0}
                                    </Descriptions.Item>
                                  </Descriptions>
                                  <div className={styles.taskSummary}>
                                    <Text type="secondary">当前动作：</Text>{' '}
                                    <Text>{activeAction || '等待执行'}</Text>
                                  </div>
                                  {activeLastTrace ? (
                                    <div className={styles.taskSummary}>
                                      <Space wrap>
                                        <Tag
                                          color={
                                            TOOL_TYPE_COLOR[activeLastTrace.tool_type] || 'default'
                                          }
                                        >
                                          {activeLastTrace.tool_type}
                                        </Tag>
                                        <Text>{activeLastTrace.tool_id}</Text>
                                        {activeLastTrace.timestamp ? (
                                          <Text type="secondary">
                                            {dayjs(activeLastTrace.timestamp).format('HH:mm:ss')}
                                          </Text>
                                        ) : null}
                                      </Space>
                                      <div>
                                        <Text type="secondary">摘要：</Text>{' '}
                                        <Text>{activeLastTrace.summary || activeLastTrace.query}</Text>
                                      </div>
                                    </div>
                                  ) : (
                                    <Text type="secondary">暂无工具调用</Text>
                                  )}
                                  <div>
                                    <Text strong>决策</Text>
                                    {activeDecision ? (
                                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                                        <Descriptions size="small" column={2}>
                                          <Descriptions.Item label="充分性">
                                            {activeDecision.sufficient ? '是' : '否'}
                                          </Descriptions.Item>
                                          <Descriptions.Item label="对比">
                                            {activeDecision.should_compare ? '是' : '否'}
                                          </Descriptions.Item>
                                        </Descriptions>
                                        {activeDecision.rationale ? (
                                          <Text type="secondary">{activeDecision.rationale}</Text>
                                        ) : null}
                                        {activeDecisionFollowups.length ? (
                                          <div className={styles.tagWrap}>
                                            {activeDecisionFollowups.map((question, idx) => (
                                              <Tag
                                                key={`${activeBlock.block_id}-followup-${idx}`}
                                              >
                                                {question}
                                              </Tag>
                                            ))}
                                          </div>
                                        ) : (
                                          <Text type="secondary">暂无 follow-up</Text>
                                        )}
                                        {activeDecisionCompareDimensions.length ? (
                                          <div className={styles.tagWrap}>
                                            {activeDecisionCompareDimensions.map((dimension, idx) => (
                                              <Tag
                                                key={`${activeBlock.block_id}-compare-${idx}`}
                                                color="purple"
                                              >
                                                {dimension}
                                              </Tag>
                                            ))}
                                          </div>
                                        ) : null}
                                        {activeDecisionToolCalls.length ? (
                                          <div className={styles.tagWrap}>
                                            {activeDecisionToolCalls.map((name, idx) => (
                                              <Tag
                                                key={`${activeBlock.block_id}-toolcall-${idx}`}
                                                color="processing"
                                              >
                                                {name}
                                              </Tag>
                                            ))}
                                          </div>
                                        ) : null}
                                      </Space>
                                    ) : (
                                      <Text type="secondary">暂无决策记录</Text>
                                    )}
                                  </div>
                                  <div>
                                    <Text strong>思维流</Text>
                                    <div className={styles.thoughtControls}>
                                      <Select
                                        size="small"
                                        mode="multiple"
                                        allowClear
                                        placeholder="过滤类型"
                                        options={thoughtTypeOptions}
                                        value={thoughtTypeFilter}
                                        onChange={(value) => setThoughtTypeFilter(value)}
                                        style={{ minWidth: 160 }}
                                      />
                                      <Input
                                        size="small"
                                        placeholder="搜索内容"
                                        value={thoughtSearchText}
                                        onChange={(event) => setThoughtSearchText(event.target.value)}
                                        onKeyDown={(event) => {
                                          if (event.key !== 'Enter') return
                                          if (!thoughtMatchStats.matchItemIds.length) return
                                          event.preventDefault()
                                          if (event.shiftKey) {
                                            handlePrevThoughtMatch()
                                          } else {
                                            handleNextThoughtMatch()
                                          }
                                        }}
                                        style={{ minWidth: 180 }}
                                      />
                                      {thoughtSearchText.trim() ? (
                                        <Text type="secondary" className={styles.thoughtMatchStats}>
                                          命中 {thoughtMatchStats.totalMatches} 次 ·{' '}
                                          {thoughtMatchStats.matchItemIds.length} 条
                                        </Text>
                                      ) : null}
                                      {thoughtSearchText.trim() && thoughtMatchStats.matchItemIds.length ? (
                                        <Text type="secondary" className={styles.thoughtMatchStats}>
                                          定位 {thoughtHitIndex + 1}/{thoughtMatchStats.matchItemIds.length}
                                        </Text>
                                      ) : null}
                                      {thoughtSearchText.trim() ? (
                                        <Text type="secondary" className={styles.thoughtHint}>
                                          Enter 下一条 · Shift+Enter 上一条
                                        </Text>
                                      ) : null}
                                      <Button
                                        size="small"
                                        onClick={() => {
                                          setThoughtTypeFilter([])
                                          setThoughtSearchText('')
                                        }}
                                      >
                                        清空
                                      </Button>
                                      <Switch
                                        size="small"
                                        checked={thoughtAutoFollow}
                                        onChange={setThoughtAutoFollow}
                                        checkedChildren="跟随"
                                        unCheckedChildren="手动"
                                      />
                                      <Button
                                        size="small"
                                        disabled={!thoughtMatchStats.matchItemIds.length}
                                        onClick={handlePrevThoughtMatch}
                                      >
                                        上一条
                                      </Button>
                                      <Button
                                        size="small"
                                        disabled={!thoughtMatchStats.matchItemIds.length}
                                        onClick={handleNextThoughtMatch}
                                      >
                                        下一条
                                      </Button>
                                      <Button size="small" onClick={handleThoughtScrollToBottom}>
                                        到底部
                                      </Button>
                                      <Button
                                        size="small"
                                        icon={<CopyOutlined />}
                                        disabled={!filteredThoughtItems.length}
                                        onClick={handleCopyThoughtMarkdown}
                                      >
                                        复制 Markdown
                                      </Button>
                                      <Button
                                        size="small"
                                        icon={<DownloadOutlined />}
                                        disabled={!filteredThoughtItems.length}
                                        onClick={handleExportThoughtJson}
                                      >
                                        导出 JSON
                                      </Button>
                                      <Button
                                        size="small"
                                        icon={<DownloadOutlined />}
                                        disabled={!filteredThoughtItems.length}
                                        onClick={handleExportThoughtCsv}
                                      >
                                        导出 CSV
                                      </Button>
                                      <Button
                                        size="small"
                                        icon={<DownloadOutlined />}
                                        disabled={!filteredThoughtItems.length}
                                        onClick={handleExportThoughtMarkdown}
                                      >
                                        导出 Markdown
                                      </Button>
                                      <Button
                                        size="small"
                                        icon={<DownloadOutlined />}
                                        disabled={!filteredThoughtItems.length}
                                        onClick={handleExportThoughtHtml}
                                      >
                                        导出 HTML
                                      </Button>
                                    </div>
                                    <div className={styles.thoughtStream} ref={thoughtStreamRef}>
                                      {filteredThoughtItems.length ? (
                                        filteredThoughtItems.map((item, idx) => (
                                          <div
                                            key={item.id}
                                            className={`${styles.thoughtItem} ${
                                              idx === filteredThoughtItems.length - 1
                                                ? styles.thoughtItemLast
                                                : ''
                                            } ${activeMatchId === item.id ? styles.thoughtItemActive : ''}`}
                                            ref={(node) => {
                                              if (node) {
                                                thoughtItemRefs.current.set(item.id, node)
                                              } else {
                                                thoughtItemRefs.current.delete(item.id)
                                              }
                                            }}
                                          >
                                            <div
                                              className={styles.thoughtIcon}
                                              style={{ color: THOUGHT_ICON_COLOR[item.type] }}
                                            >
                                              {getThoughtIcon(item.type)}
                                            </div>
                                            <div className={styles.thoughtBody}>
                                              <div className={styles.thoughtHeader}>
                                                <Tag color={THOUGHT_TAG_COLOR[item.type]}>
                                                  {THOUGHT_LABEL[item.type]}
                                                </Tag>
                                                <Text strong>
                                                  {renderHighlightedText(item.title, thoughtSearchText)}
                                                </Text>
                                                {item.timestamp ? (
                                                  <Text type="secondary" className={styles.thoughtMeta}>
                                                    {dayjs(item.timestamp).format('HH:mm:ss')}
                                                  </Text>
                                                ) : null}
                                              </div>
                                              {item.content ? (
                                                <Paragraph
                                                  className={styles.thoughtContent}
                                                  ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}
                                                >
                                                  {renderHighlightedText(item.content, thoughtSearchText)}
                                                </Paragraph>
                                              ) : null}
                                            </div>
                                            <span className={styles.thoughtLine} />
                                          </div>
                                        ))
                                      ) : (
                                        <Empty description="暂无执行记录" />
                                      )}
                                    </div>
                                  </div>
                                    <Button size="small" onClick={() => setSelectedBlock(activeBlock)}>
                                      查看详情
                                    </Button>
                                  </Space>
                                ) : (
                                  <Empty description="暂无任务" />
                                )}
                              </Card>
                            </div>
                          </div>
                          <Card size="small" title="研究日志">
                            {researchTimelineItems.length ? (
                              <Timeline items={researchTimelineItems} />
                            ) : (
                              <Empty description="暂无研究记录" />
                            )}
                          </Card>
                        </>
                      ),
                    },
                    {
                      key: 'reporting',
                      label: '报告',
                      children: (
                        <div className={styles.summaryGrid}>
                          <Card size="small" title="报告概览">
                            <Descriptions size="small" column={2}>
                              <Descriptions.Item label="状态">
                                {result?.status || '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="生成时间">
                                {runSummary.generated_at
                                  ? dayjs(runSummary.generated_at).format('YYYY-MM-DD HH:mm')
                                  : '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="字数">
                                {reportStats.words}
                              </Descriptions.Item>
                              <Descriptions.Item label="行数">
                                {reportStats.lines}
                              </Descriptions.Item>
                              <Descriptions.Item label="字符">
                                {reportStats.chars}
                              </Descriptions.Item>
                              <Descriptions.Item label="引用">
                                {citations.length}
                              </Descriptions.Item>
                              <Descriptions.Item label="证据覆盖率">
                                {typeof reportQuality?.citation_paragraph_coverage === 'number'
                                  ? `${Math.round(reportQuality.citation_paragraph_coverage * 100)}%`
                                  : '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="带引用段落">
                                {typeof reportQuality?.paragraphs_with_citations === 'number'
                                  ? reportQuality.paragraphs_with_citations
                                  : '-'}
                                /
                                {typeof reportQuality?.paragraphs_total === 'number'
                                  ? reportQuality.paragraphs_total
                                  : '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="引用标记次数">
                                {typeof reportQuality?.citations_mentions === 'number'
                                  ? reportQuality.citations_mentions
                                  : '-'}
                              </Descriptions.Item>
                              <Descriptions.Item label="无引用段落">
                                {typeof reportQuality?.paragraphs_without_citations === 'number'
                                  ? reportQuality.paragraphs_without_citations
                                  : '-'}
                              </Descriptions.Item>
                            </Descriptions>
                            <Divider style={{ margin: '12px 0' }} />
                            {reportMarkdown ? (
                              <Paragraph className={styles.reportPreview} ellipsis={{ rows: 10 }}>
                                {reportMarkdown}
                              </Paragraph>
                            ) : (
                              <Empty description="暂无报告内容" />
                            )}
                          </Card>
                          <Card size="small" title="报告日志">
                            {reportTimelineItems.length ? (
                              <Timeline items={reportTimelineItems} />
                            ) : (
                              <Empty description="暂无报告阶段记录" />
                            )}
                          </Card>
                          <Card size="small" title="报告大纲">
                            {reportOutline.length ? (
                              <List
                                size="small"
                                dataSource={reportOutline}
                                renderItem={(item) => (
                                  <List.Item>
                                    <Text>{item}</Text>
                                  </List.Item>
                                )}
                              />
                            ) : (
                              <Empty description="暂无报告大纲" />
                            )}
                          </Card>
                          <Card size="small" title="强化大纲（三层）">
                            {reportOutlineDetailed.length ? (
                              <List
                                size="small"
                                dataSource={reportOutlineDetailed}
                                renderItem={(item) => (
                                  <List.Item>
                                    <Text>{item}</Text>
                                  </List.Item>
                                )}
                              />
                            ) : (
                              <Empty description="暂无强化大纲" />
                            )}
                          </Card>
                          <Card size="small" title="笔记汇总">
                            {reportNotes.length ? (
                              <List
                                size="small"
                                dataSource={reportNotes}
                                renderItem={(item) => (
                                  <List.Item>
                                    <Text>{item}</Text>
                                  </List.Item>
                                )}
                              />
                            ) : (
                              <Empty description="暂无笔记汇总" />
                            )}
                          </Card>
                          <Card size="small" title="引用表">
                            {reportCitationTable.length ? (
                              <List
                                size="small"
                                dataSource={reportCitationTable}
                                renderItem={(item) => (
                                  <List.Item>
                                    <Text>{item}</Text>
                                  </List.Item>
                                )}
                              />
                            ) : (
                              <Empty description="暂无引用表" />
                            )}
                          </Card>
                          <Card size="small" title="证据一致性检查（QA）">
                            <Space direction="vertical" size={8} style={{ width: '100%' }}>
                              <Text type="secondary">
                                无引用段落示例（用于定位潜在“无证据断言”）：
                              </Text>
                              {reportQuality?.uncited_examples?.length ? (
                                <List
                                  size="small"
                                  dataSource={reportQuality.uncited_examples}
                                  renderItem={(item) => (
                                    <List.Item>
                                      <Text>{item}</Text>
                                    </List.Item>
                                  )}
                                />
                              ) : (
                                <Empty description="暂无无引用段落示例" />
                              )}
                              {reportQuality?.sections_without_citations?.length ? (
                                <>
                                  <Divider style={{ margin: '8px 0' }} />
                                  <Text type="secondary">无引用章节：</Text>
                                  <div className={styles.tagWrap}>
                                    {reportQuality.sections_without_citations.map((title) => (
                                      <Tag key={title} color="orange">
                                        {title || '(unknown)'}
                                      </Tag>
                                    ))}
                                  </div>
                                </>
                              ) : null}
                            </Space>
                          </Card>
                        </div>
                      ),
                    },
                  ]}
                />
              )
            },
            {
              key: 'report',
              label: '报告',
              children: reportMarkdown ? (
                <div className={styles.report} ref={reportRef}>
                  <Markdown value={reportMarkdown} />
                </div>
              ) : (
                <Empty description="暂无报告" />
              ),
            },
            {
              key: 'citations',
              label: `引用 (${citations.length})`,
              children: citations.length ? (
                <Table
                  rowKey={(record) => record.citation_id}
                  columns={citationColumns}
                  dataSource={citations}
                  pagination={{ pageSize: 6 }}
                />
              ) : (
                <Empty description="暂无引用" />
              ),
            },
            {
              key: 'progress',
              label: `进度 (${progressEvents.length})`,
              children: progressEvents.length ? (
                <Timeline items={progressTimelineItems} />
              ) : (
                <Empty description="暂无进度记录" />
              ),
            },
            {
              key: 'summary',
              label: 'Summary',
              children: (
                <div className={styles.summaryGrid}>
                  <Card size="small" title="运行对比">
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      <Space wrap>
                        <Select
                          allowClear
                          placeholder="选择运行 A"
                          options={runSelectOptions}
                          value={compareRunA}
                          onChange={(value) => setCompareRunA(value)}
                          style={{ minWidth: 220 }}
                        />
                        <Select
                          allowClear
                          placeholder="选择运行 B"
                          options={runSelectOptions}
                          value={compareRunB}
                          onChange={(value) => setCompareRunB(value)}
                          style={{ minWidth: 220 }}
                        />
                        <Button
                          onClick={() => {
                            setCompareRunA(compareRunB)
                            setCompareRunB(compareRunA)
                          }}
                        >
                          交换
                        </Button>
                        <Button
                          onClick={() => {
                            setCompareRunA(undefined)
                            setCompareRunB(undefined)
                          }}
                        >
                          清空
                        </Button>
                      </Space>
                      {compareA && compareB ? (
                        <Descriptions size="small" column={1}>
                          <Descriptions.Item label="Topic">
                            <Text strong>{compareA.topic}</Text>
                            <Text type="secondary"> vs </Text>
                            <Text strong>{compareB.topic}</Text>
                          </Descriptions.Item>
                          <Descriptions.Item label="Blocks">
                            {renderCompareValue(
                              getSummary(compareA).blocks_total || 0,
                              getSummary(compareB).blocks_total || 0,
                            )}
                          </Descriptions.Item>
                          <Descriptions.Item label="Citations">
                            {renderCompareValue(
                              getSummary(compareA).citations_total || 0,
                              getSummary(compareB).citations_total || 0,
                            )}
                          </Descriptions.Item>
                          <Descriptions.Item label="Traces">
                            {renderCompareValue(
                              getSummary(compareA).tool_traces_total || 0,
                              getSummary(compareB).tool_traces_total || 0,
                            )}
                          </Descriptions.Item>
                          <Descriptions.Item label="Decisions">
                            {renderCompareValue(
                              getSummary(compareA).decisions_total || 0,
                              getSummary(compareB).decisions_total || 0,
                            )}
                          </Descriptions.Item>
                          <Descriptions.Item label="Duration">
                            <Text>
                              {formatDuration(compareA.duration_seconds)} vs{' '}
                              {formatDuration(compareB.duration_seconds)}
                            </Text>
                          </Descriptions.Item>
                        </Descriptions>
                      ) : (
                        <Empty description="请选择两次运行进行对比" />
                      )}
                    </Space>
                  </Card>
                  <Card size="small" title="统计">
                    <Descriptions size="small" column={2}>
                      <Descriptions.Item label="Blocks">
                        {runSummary.blocks_total ?? blocks.length}
                      </Descriptions.Item>
                      <Descriptions.Item label="Citations">
                        {runSummary.citations_total ?? citations.length}
                      </Descriptions.Item>
                      <Descriptions.Item label="Traces">
                        {runSummary.tool_traces_total ?? toolTraces.length}
                      </Descriptions.Item>
                      <Descriptions.Item label="Decisions">
                        {runSummary.decisions_total ?? 0}
                      </Descriptions.Item>
                      <Descriptions.Item label="生成时间">
                        {runSummary.generated_at
                          ? dayjs(runSummary.generated_at).format('YYYY-MM-DD HH:mm:ss')
                          : '-'}
                      </Descriptions.Item>
                    </Descriptions>
                  </Card>
                  <Card size="small" title="工具分布">
                    {Object.keys(runSummary.tool_traces_by_type || {}).length ? (
                      <List
                        size="small"
                        dataSource={Object.entries(
                          (runSummary.tool_traces_by_type || {}) as Record<string, number>,
                        )}
                        renderItem={([key, value]: [string, number]) => (
                          <List.Item>
                            <Space>
                              <Tag color={TOOL_TYPE_COLOR[key] || 'default'}>{key}</Tag>
                              <Text>{value}</Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Empty description="暂无工具数据" />
                    )}
                  </Card>
                  <Card size="small" title="错误摘要">
                    {runSummary.errors?.length ? (
                      <List
                        size="small"
                        dataSource={runSummary.errors}
                        renderItem={(item: any) => (
                          <List.Item>
                            <Space direction="vertical" size={2}>
                              <Space>
                                <Tag color="red">{item.tool_type}</Tag>
                                <Text>
                                  {item.block_id} / {item.tool_id}
                                </Text>
                              </Space>
                              <Text type="secondary">{item.summary}</Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Empty description="暂无错误" />
                    )}
                  </Card>
                  <Card size="small" title="诊断建议">
                    <List
                      size="small"
                      dataSource={diagnosisTips}
                      renderItem={(item) => (
                        <List.Item>
                          <Text>{item}</Text>
                        </List.Item>
                      )}
                    />
                  </Card>
                </div>
              ),
            },
            {
              key: 'blocks',
              label: `队列 (${blocks.length})`,
              children: blocks.length ? (
                <Table
                  rowKey={(record) => record.block_id}
                  columns={blockColumns}
                  dataSource={blocks}
                  pagination={{ pageSize: 8 }}
                  rowClassName={styles.clickableRow}
                  onRow={(record) => ({
                    onClick: () => setSelectedBlock(record),
                  })}
                />
              ) : (
                <Empty description="暂无队列数据" />
              ),
            },
            {
              key: 'tree',
              label: '队列树',
              children: blockTree.length ? (
                <Tree
                  treeData={blockTree}
                  onSelect={(keys) => {
                    const selectedKey = keys[0]?.toString()
                    if (!selectedKey) return
                    const target = blocks.find((item) => item.block_id === selectedKey)
                    if (target) setSelectedBlock(target)
                  }}
                />
              ) : (
                <Empty description="暂无树结构" />
              ),
            },
            {
              key: 'traces',
              label: `工具链 (${toolTraces.length})`,
              children: toolTraces.length ? (
                <>
                  <Space wrap className={styles.filterRow}>
                    <Select
                      mode="multiple"
                      allowClear
                      placeholder="过滤工具类型"
                      options={traceTypeOptions}
                      value={traceTypeFilter}
                      onChange={(value) => setTraceTypeFilter(value)}
                      style={{ minWidth: 200 }}
                    />
                    <Select
                      allowClear
                      placeholder="过滤阶段"
                      options={traceStageOptions}
                      value={traceStageFilter}
                      onChange={(value) => setTraceStageFilter(value)}
                      style={{ minWidth: 140 }}
                    />
                    <Select
                      allowClear
                      placeholder="过滤 Block"
                      options={blockOptions}
                      value={traceBlockFilter}
                      onChange={(value) => setTraceBlockFilter(value)}
                      style={{ minWidth: 240 }}
                      showSearch
                      optionFilterProp="label"
                    />
                    <Input
                      placeholder="搜索 tool_id / query / summary"
                      value={traceSearchText}
                      onChange={(event) => setTraceSearchText(event.target.value)}
                      style={{ minWidth: 240 }}
                    />
                    <Button
                      onClick={() => {
                        setTraceTypeFilter([])
                        setTraceStageFilter(undefined)
                        setTraceBlockFilter(undefined)
                        setTraceSearchText('')
                      }}
                    >
                      清空过滤
                    </Button>
                    <Tag>匹配 {filteredTraces.length}</Tag>
                  </Space>
                  <Table
                    rowKey={(record) => `${record.block_id}-${record.tool_id}-${record.timestamp}`}
                    columns={traceColumns}
                    dataSource={filteredTraces}
                    pagination={{ pageSize: 8 }}
                    rowClassName={(record) =>
                      `${styles.clickableRow} ${isTraceError(record) ? styles.errorRow : ''}`
                    }
                    onRow={(record) => ({
                      onClick: () => setSelectedTrace(record),
                    })}
                  />
                </>
              ) : (
                <Empty description="暂无工具调用记录" />
              ),
            },
            {
              key: 'raw',
              label: '原始 Trace',
              children: result?.trace ? (
                <Paragraph className={styles.traceJson} code>
                  {JSON.stringify(result.trace, null, 2)}
                </Paragraph>
              ) : snapshot?.queue ? (
                <Paragraph className={styles.traceJson} code>
                  {JSON.stringify(snapshot.queue, null, 2)}
                </Paragraph>
              ) : (
                <Empty description="暂无 Trace" />
              ),
            },
          ]}
        />
      </div>

      <Drawer
        open={!!selectedBlock}
        onClose={() => setSelectedBlock(null)}
        title="Block 详情"
        width={520}
      >
        {selectedBlock ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space>
              <Button
                size="small"
                icon={<DownloadOutlined />}
                loading={blockEvidenceLoading}
                onClick={handleExportBlockEvidence}
              >
                导出证据包
              </Button>
            </Space>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Block ID">
                {selectedBlock.block_id}
              </Descriptions.Item>
              <Descriptions.Item label="状态">{selectedBlock.status}</Descriptions.Item>
              <Descriptions.Item label="Depth">{selectedBlock.depth}</Descriptions.Item>
              <Descriptions.Item label="Parent">{selectedBlock.parent_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="迭代">
                {selectedBlock.iterations}/{selectedBlock.max_iterations}
              </Descriptions.Item>
              <Descriptions.Item label="Updated">{selectedBlock.updated_at}</Descriptions.Item>
            </Descriptions>
            <Divider />
            <div>
              <Text strong>执行轨迹</Text>
              <Tabs
                size="small"
                items={[
                  {
                    key: 'block-traces',
                    label: `工具调用 (${selectedBlock.tool_traces?.length || 0})`,
                    children: blockTraceTimelineItems.length ? (
                      <Timeline items={blockTraceTimelineItems} />
                    ) : (
                      <Empty description="暂无工具轨迹" />
                    ),
                  },
                  {
                    key: 'block-progress',
                    label: `进度事件 (${blockProgressTimelineItems.length})`,
                    children: blockProgressTimelineItems.length ? (
                      <Timeline items={blockProgressTimelineItems} />
                    ) : (
                      <Empty description="暂无进度事件" />
                    ),
                  },
                ]}
              />
            </div>
            <div>
              <Text strong>Notes</Text>
              {selectedBlock.notes?.length ? (
                <List
                  size="small"
                  dataSource={selectedBlock.notes}
                  renderItem={(item) => <List.Item>{item}</List.Item>}
                />
              ) : (
                <Empty description="暂无 Notes" />
              )}
            </div>
            <div>
              <Text strong>引用</Text>
              {selectedBlock.citations?.length ? (
                <List
                  size="small"
                  dataSource={selectedBlock.citations}
                  renderItem={(cid) => {
                    const cite = citationMap.get(cid)
                    return (
                      <List.Item>
                        <Space direction="vertical" size={2}>
                          <Text>
                            [{cite?.ref_number ?? '-'}] {cite?.title || cid}
                          </Text>
                          {cite?.url ? (
                            <a href={cite.url} target="_blank" rel="noreferrer">
                              {cite.url}
                            </a>
                          ) : null}
                        </Space>
                      </List.Item>
                    )
                  }}
                />
              ) : (
                <Empty description="暂无引用" />
              )}
            </div>
            <div>
              <Text strong>Tool Traces</Text>
              {selectedBlock.tool_traces?.length ? (
                <List
                  size="small"
                  dataSource={selectedBlock.tool_traces}
                  renderItem={(trace: any) => (
                    <List.Item
                      className={styles.clickableRow}
                      onClick={() =>
                        setSelectedTrace({
                          ...trace,
                          block_id: selectedBlock.block_id,
                          title: selectedBlock.title,
                        })
                      }
                    >
                      <Space>
                        <Tag color={TOOL_TYPE_COLOR[trace.tool_type] || 'default'}>
                          {trace.tool_type}
                        </Tag>
                        {isTraceError(trace) ? <Tag color="red">error</Tag> : null}
                        <Text>{trace.tool_id}</Text>
                        <Text type="secondary">{trace.summary}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              ) : (
                <Empty description="暂无工具调用" />
              )}
            </div>
            <div>
              <Text strong>Decisions</Text>
              {selectedBlock.decisions?.length ? (
                selectedBlock.decisions.map((item, idx) => (
                  <Paragraph key={`${selectedBlock.block_id}-decision-${idx}`} code>
                    {JSON.stringify(item, null, 2)}
                  </Paragraph>
                ))
              ) : (
                <Empty description="暂无 Decision" />
              )}
            </div>
          </Space>
        ) : null}
      </Drawer>

      <Drawer
        open={!!selectedTrace}
        onClose={() => setSelectedTrace(null)}
        title="Tool Trace 详情"
        width={520}
      >
        {selectedTrace ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="Tool">{selectedTrace.tool_id}</Descriptions.Item>
              <Descriptions.Item label="Block">{selectedTrace.block_id}</Descriptions.Item>
              <Descriptions.Item label="Type">{selectedTrace.tool_type}</Descriptions.Item>
              <Descriptions.Item label="Stage">
                {getTraceStage(selectedTrace.tool_type)}
              </Descriptions.Item>
              <Descriptions.Item label="Status">
                {isTraceError(selectedTrace) ? (
                  <Tag color="red">error</Tag>
                ) : (
                  <Tag color="green">ok</Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Citation">{selectedTrace.citation_id}</Descriptions.Item>
              <Descriptions.Item label="Time">{selectedTrace.timestamp}</Descriptions.Item>
            </Descriptions>
            <Divider />
            <div>
              <Text strong>Query</Text>
              <Paragraph>{selectedTrace.query}</Paragraph>
            </div>
            <div>
              <Text strong>Summary</Text>
              <Paragraph>{selectedTrace.summary}</Paragraph>
            </div>
            <div>
              <Text strong>Raw</Text>
              <Paragraph className={styles.traceJson} code>
                {selectedTrace.raw_answer}
              </Paragraph>
            </div>
          </Space>
        ) : null}
      </Drawer>
    </div>
  )
}
