import * as api from '@/api'
import IconEdit from '@/assets/chat/edit.svg'
import Markdown from '@/components/markdown'
import ComPageLayout from '@/components/page-layout'
import ComSender from '@/components/sender'
import { ChatRole, ChatType } from '@/configs'
import { deviceActions } from '@/store/device'
import { userState } from '@/store/user'
import { setPageTransport, usePageTransport } from '@/utils'
import { NOTEBOOK_WORKSPACE_ID, createNotebookNoteFile } from '@/utils/notebook'
import { MenuUnfoldOutlined } from '@ant-design/icons'
import { useMount, useRequest, useUnmount } from 'ahooks'
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import {
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { proxy, useSnapshot } from 'valtio'
import { sessionActions } from '../../store/session'
import ChatMessage from './component/chat-message'
import Citations from './component/citations'
import Contracts from './component/contracts'
import ChatDrawer from './component/drawer'
import DeepResearchProcessPanel from './component/deep-research-process-panel'
import styles from './index.module.scss'
import { createChatId, transportToChatEnter } from './shared'
import type { KnowledgeBase } from '@/api/repository'
import { getDeepResearchPlanStreamUrl } from '@/api/deepResearch'
import type {
  DeepResearchBlockEvidence,
  DeepResearchCitation,
  DeepResearchPlan,
  PlanItem,
  DeepResearchRequest,
  DeepResearchRunMeta,
  ProgressEvent,
  TopicBlock,
} from '@/api/deepResearch'

type ChatItemWithToken = API.ChatItem & { __openToken?: number }
type RightPanelMode = 'documents' | 'citations' | 'deep_research'

const DEEP_RESEARCH_DEFAULTS = {
  mode: 'queue' as const,
  depth: 2,
  breadth: 5,
  max_parallel: 1,
  max_iterations: 4,
  top_k: 6,
  index_mode: 'auto',
  use_web_search: true,
  use_paper_search: true,
  use_code_exec: false,
}

type DeepResearchPresetKey = 'quick' | 'medium' | 'deep'
type DeepResearchPlanEditFormValues = {
  topic: string
  plan_text: string
}
const DEFAULT_DEEP_RESEARCH_PRESET: DeepResearchPresetKey = 'medium'

const DEEP_RESEARCH_PRESET_PARAMS: Record<
  DeepResearchPresetKey,
  Pick<DeepResearchRequest, 'depth' | 'breadth' | 'max_parallel' | 'max_iterations'>
> = {
  quick: { depth: 1, breadth: 2, max_parallel: 1, max_iterations: 2 },
  medium: { depth: 2, breadth: 5, max_parallel: 1, max_iterations: 4 },
  deep: { depth: 2, breadth: 8, max_parallel: 1, max_iterations: 7 },
}
const DEEP_RESEARCH_PRESET_LABELS: Record<DeepResearchPresetKey, string> = {
  quick: '快速',
  medium: '标准',
  deep: '深度',
}

const MAX_CHAT_IMAGE_COUNT = 4
const MAX_CHAT_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
const DEEP_CHAT_LLM_LOCAL_STORAGE_KEY = 'deep_chat_llm_model'
const DEEP_CHAT_LAST_USED_USER_KB_ID_STORAGE_KEY = 'deep_chat_last_user_kb_id'
const DEEP_CHAT_RESEARCH_PRESET_STORAGE_KEY = 'deep_chat_research_preset'
const DEEP_CHAT_RIGHT_PANEL_WIDTH_STORAGE_KEY = 'deep_chat_right_panel_width'
const DEEP_CHAT_RIGHT_PANEL_DEFAULT_WIDTH = 540
const DEEP_CHAT_RIGHT_PANEL_MIN_WIDTH = 420
const DEEP_CHAT_RIGHT_PANEL_HARD_MIN_WIDTH = 320
const DEEP_CHAT_RIGHT_PANEL_MAX_WIDTH = 860
const DEEP_CHAT_MIN_MAIN_WIDTH = 720
const DEEP_RESEARCH_PROGRESS_BUFFER_LIMIT = 2000
const DEEP_RESEARCH_STREAM_SNAPSHOT_INTERVAL_MS = 4000
const DEEP_RESEARCH_STREAM_SNAPSHOT_INTERVAL_REPORTING_MS = 9000
const DEEP_RESEARCH_RUNNING_REPORT_PREVIEW_MAX_CHARS = 14000
const DEEP_RESEARCH_RUNNING_CITATIONS_LIMIT = 120
const DEEP_RESEARCH_PERSIST_PROGRESS_LIMIT = 200
const DEEP_RESEARCH_PERSIST_CITATIONS_LIMIT = 60
const DEEP_RESEARCH_PERSIST_REPORT_CHARS = 6000
const DEEP_RESEARCH_PERSIST_QUEUE_BLOCKS_LIMIT = 24

const DASHSCOPE_TEXT_MODEL_OPTIONS = [
  { label: 'qwen-plus', value: 'qwen-plus' },
  { label: 'qwen3-max', value: 'qwen3-max' },
  { label: 'qwen-max', value: 'qwen-max' },
  { label: 'qwen-turbo', value: 'qwen-turbo' },
] as const

const DASHSCOPE_VISION_MODEL_OPTIONS = [
  { label: 'qwen-vl-max', value: 'qwen-vl-max' },
  { label: 'qwen-vl-plus', value: 'qwen-vl-plus' },
] as const

const DASHSCOPE_MODEL_OPTIONS = [
  ...DASHSCOPE_TEXT_MODEL_OPTIONS,
  ...DASHSCOPE_VISION_MODEL_OPTIONS,
] as const

const OPENAI_MODEL_OPTIONS = [
  { label: 'gpt-5.2', value: 'gpt-5.2' },
  { label: 'gpt-5', value: 'gpt-5' },
  { label: 'gpt-5-mini', value: 'gpt-5-mini' },
  { label: 'gpt-4.1', value: 'gpt-4.1' },
  { label: 'gpt-4o', value: 'gpt-4o' },
] as const

type LlmProviderValue = 'dashscope' | 'openai'
type LlmModelValue = string
type LlmModelOption = {
  label: string
  value: string
  provider: LlmProviderValue
  isVision: boolean
}
type ChatUsageStats = {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

const DEFAULT_DASHSCOPE_MODEL = 'qwen3-max'
const DEFAULT_DASHSCOPE_VISION_MODEL = 'qwen-vl-max'
const DEFAULT_OPENAI_MODEL = 'gpt-5.2'
const DEFAULT_OPENAI_VISION_MODEL = 'gpt-4o'
const DASHSCOPE_VISION_MODEL_SET = new Set<string>(
  DASHSCOPE_VISION_MODEL_OPTIONS.map((item) => item.value),
)
const OPENAI_VISION_MODEL_SET = new Set<string>(['gpt-4o'])
const LLM_MODEL_OPTIONS: LlmModelOption[] = [
  ...DASHSCOPE_MODEL_OPTIONS.map((item) => ({
    label: `通义 · ${item.label}`,
    value: item.value,
    provider: 'dashscope' as const,
    isVision: DASHSCOPE_VISION_MODEL_SET.has(item.value),
  })),
  ...OPENAI_MODEL_OPTIONS.map((item) => ({
    label: `OpenAI · ${item.label}`,
    value: item.value,
    provider: 'openai' as const,
    isVision: OPENAI_VISION_MODEL_SET.has(item.value),
  })),
]
const LLM_MODEL_SET = new Set<string>(LLM_MODEL_OPTIONS.map((item) => item.value))
const LLM_MODEL_OPTION_MAP = new Map<string, LlmModelOption>(
  LLM_MODEL_OPTIONS.map((item) => [item.value, item]),
)
const MODEL_CONTEXT_WINDOW_HINTS: Record<string, number> = {
  'qwen-plus': 200000,
  'qwen3-max': 200000,
  'qwen-max': 200000,
  'qwen-turbo': 100000,
  'qwen-vl-max': 32000,
  'qwen-vl-plus': 32000,
  'gpt-4o': 128000,
  'gpt-4o-mini': 128000,
  'gpt-4.1': 1048576,
  'gpt-5': 400000,
  'gpt-5-mini': 400000,
  'gpt-5.2': 400000,
}

const { TextArea } = Input

const normalizeLlmProvider = (value: unknown): LlmProviderValue => {
  const normalized = String(value || '').trim().toLowerCase()
  return normalized === 'openai' ? 'openai' : 'dashscope'
}

const resolveProviderByModel = (value: unknown): LlmProviderValue => {
  if (typeof value === 'string') {
    return LLM_MODEL_OPTION_MAP.get(value)?.provider || 'dashscope'
  }
  return 'dashscope'
}

const defaultModelByProvider = (provider: LlmProviderValue): LlmModelValue =>
  provider === 'openai' ? DEFAULT_OPENAI_MODEL : DEFAULT_DASHSCOPE_MODEL

const defaultVisionModelByProvider = (provider: LlmProviderValue): LlmModelValue =>
  provider === 'openai' ? DEFAULT_OPENAI_VISION_MODEL : DEFAULT_DASHSCOPE_VISION_MODEL

const normalizeLlmModel = (value: unknown, providerHint?: unknown): LlmModelValue => {
  if (typeof value === 'string' && LLM_MODEL_SET.has(value)) {
    return value
  }
  return defaultModelByProvider(normalizeLlmProvider(providerHint))
}

const isVisionModel = (value: string) => Boolean(LLM_MODEL_OPTION_MAP.get(value)?.isVision)

const resolveModelLabel = (value: string) => LLM_MODEL_OPTION_MAP.get(value)?.label || value
const estimateLabelUnits = (text: string) =>
  Array.from(text).reduce((sum, ch) => sum + (/[\u4e00-\u9fff]/.test(ch) ? 1.85 : 1), 0)
const calcCompactSelectWidth = (label: string, minPx: number, maxPx: number) => {
  const width = Math.round(38 + estimateLabelUnits(label) * 8.6)
  return `${Math.max(minPx, Math.min(maxPx, width))}px`
}

const resolveDownloadFilename = (
  contentDisposition: string | undefined,
  fallback: string,
) => {
  if (!contentDisposition) return fallback
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].trim())
    } catch {
      // ignore malformed encoding and continue with fallback parsing
    }
  }
  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  if (plainMatch?.[1]) {
    return plainMatch[1].trim()
  }
  return fallback
}

const deriveBlockStatsFromQueue = (
  queue: API.DeepResearchCardState['snapshotQueue'] | undefined,
  prevStats?: API.DeepResearchCardState['blockStats'],
): API.DeepResearchCardState['blockStats'] | undefined => {
  const rawBlocks = Array.isArray(queue?.blocks) ? queue?.blocks : []
  const blocks = rawBlocks.filter((block) => Number(block?.depth ?? 0) > 0)
  const displayBlocks = blocks.length ? blocks : rawBlocks
  if (!displayBlocks.length) return prevStats

  let completed = 0
  let pending = 0
  displayBlocks.forEach((block) => {
    const status = String(block?.status || '').trim().toLowerCase()
    if (status === 'completed') {
      completed += 1
      return
    }
    if (status === 'pending' || status === 'researching' || status === 'queued') {
      pending += 1
    }
  })

  return {
    ...(prevStats ?? {}),
    total: displayBlocks.length,
    completed,
    pending,
  }
}

const normalizeBlockStats = (
  stats: API.DeepResearchCardState['blockStats'] | undefined,
  queue?: API.DeepResearchCardState['snapshotQueue'],
): API.DeepResearchCardState['blockStats'] | undefined => {
  if (!stats) return stats
  const normalized: API.DeepResearchCardState['blockStats'] = { ...stats }
  const queueStats = deriveBlockStatsFromQueue(queue, normalized)
  if (queueStats) {
    normalized.total = queueStats.total
    normalized.completed = queueStats.completed
    normalized.pending = queueStats.pending
    return normalized
  }

  const toSafeInt = (value: unknown) => {
    if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
    return Math.max(0, Math.floor(value))
  }

  let total = toSafeInt(normalized.total)
  let completed = toSafeInt(normalized.completed)
  let pending = toSafeInt(normalized.pending)

  if (total === undefined && (completed !== undefined || pending !== undefined)) {
    total = (completed ?? 0) + (pending ?? 0)
  }
  if (completed === undefined) completed = 0
  if (pending === undefined && total !== undefined) {
    pending = Math.max(0, total - completed)
  }
  if (pending === undefined) pending = 0
  if (total === undefined) total = completed + pending

  if (completed > total) {
    // Mixed summary sources may accidentally include root block in completed count.
    if (pending === 0 && completed === total + 1) {
      completed = total
    } else {
      total = Math.max(total, completed + pending)
    }
  }

  if (completed + pending > total) {
    total = completed + pending
  }

  normalized.total = total
  normalized.completed = completed
  normalized.pending = pending
  return normalized
}

const compactDeepResearchForPersist = (
  deepResearch: API.DeepResearchCardState,
): API.DeepResearchCardState => {
  const compactReport = deepResearch.report
    ? {
        ...deepResearch.report,
        report_markdown:
          typeof deepResearch.report.report_markdown === 'string'
            ? deepResearch.report.report_markdown.slice(-DEEP_RESEARCH_PERSIST_REPORT_CHARS)
            : deepResearch.report.report_markdown,
        draft_markdown:
          typeof deepResearch.report.draft_markdown === 'string'
            ? deepResearch.report.draft_markdown.slice(-DEEP_RESEARCH_PERSIST_REPORT_CHARS)
            : deepResearch.report.draft_markdown,
      }
    : undefined

  const compactBlocks = Array.isArray(deepResearch.snapshotQueue?.blocks)
    ? deepResearch.snapshotQueue!.blocks.slice(0, DEEP_RESEARCH_PERSIST_QUEUE_BLOCKS_LIMIT).map((block) => ({
        block_id: block.block_id,
        title: block.title,
        question: block.question,
        status: block.status,
        depth: block.depth,
        iterations: block.iterations,
        max_iterations: block.max_iterations,
        child_ids: block.child_ids,
      }))
    : undefined

  return {
    ...deepResearch,
    progress: (deepResearch.progress ?? []).slice(-DEEP_RESEARCH_PERSIST_PROGRESS_LIMIT),
    citations: (deepResearch.citations ?? []).slice(0, DEEP_RESEARCH_PERSIST_CITATIONS_LIMIT),
    report: compactReport,
    snapshotQueue: deepResearch.snapshotQueue
      ? {
          ...deepResearch.snapshotQueue,
          blocks: compactBlocks as unknown as TopicBlock[] | undefined,
        }
      : undefined,
  }
}

const readLastUsedUserKnowledgeBaseId = () => {
  if (typeof window === 'undefined') return null
  const raw = localStorage.getItem(DEEP_CHAT_LAST_USED_USER_KB_ID_STORAGE_KEY)
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed <= 0) return null
  return Math.floor(parsed)
}

const persistLastUsedUserKnowledgeBaseId = (kbId: number) => {
  if (typeof window === 'undefined') return
  const normalized = Number(kbId)
  if (!Number.isFinite(normalized) || normalized <= 0) return
  localStorage.setItem(
    DEEP_CHAT_LAST_USED_USER_KB_ID_STORAGE_KEY,
    String(Math.floor(normalized)),
  )
}

const resolvePreferredKnowledgeBaseId = (
  available: KnowledgeBase[],
  candidates: Array<number | null | undefined> = [],
) => {
  if (!available.length) return null
  const preferred = [...candidates, readLastUsedUserKnowledgeBaseId()]
  for (const value of preferred) {
    const numeric = Number(value)
    if (!Number.isFinite(numeric) || numeric <= 0) continue
    const exact = available.find((item) => item.id === numeric)
    if (exact) return exact.id
  }
  return available[0].id
}

const estimateTokenCount = (text: string) => {
  if (!text) return 0
  const zhCount = Array.from(text).filter((char) => char.charCodeAt(0) > 127).length
  const enCount = text.length - zhCount
  return zhCount + Math.floor(enCount / 4)
}

const normalizeUsage = (raw: any): ChatUsageStats | null => {
  if (!raw || typeof raw !== 'object') return null
  const prompt = Number(raw.prompt_tokens ?? 0)
  const completion = Number(raw.completion_tokens ?? 0)
  const total = Number(raw.total_tokens ?? prompt + completion)
  if (!Number.isFinite(prompt) || !Number.isFinite(completion) || !Number.isFinite(total)) {
    return null
  }
  const safePrompt = Math.max(0, Math.floor(prompt))
  const safeCompletion = Math.max(0, Math.floor(completion))
  const safeTotal = Math.max(safePrompt + safeCompletion, Math.floor(total))
  return {
    prompt_tokens: safePrompt,
    completion_tokens: safeCompletion,
    total_tokens: safeTotal,
  }
}

const createClientRunId = (): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const randomHex = (len: number) =>
    Array.from({ length: len }, () => Math.floor(Math.random() * 16).toString(16)).join('')
  const variant = (8 + Math.floor(Math.random() * 4)).toString(16)
  return `${randomHex(8)}-${randomHex(4)}-4${randomHex(3)}-${variant}${randomHex(3)}-${randomHex(12)}`
}

type ChatPendingSendState = {
  targetAssistantId: number
  userMessageId: number
  prompt: string
  attachmentsSnapshot: API.ChatAttachment[]
  filesSnapshot: File[]
  imagesSnapshot: API.ChatImageAttachment[]
  replaceMessageId?: string
  restoreIndex?: number
  restoreItems?: API.ChatItem[]
  committed: boolean
}

type ComposerBootstrapPayload = {
  useRag?: boolean
  pendingAttachments?: API.ChatAttachment[]
  pendingFiles?: File[]
  imageAttachments?: API.ChatImageAttachment[]
}

type LocalReplaceContext = {
  index: number
  itemId: number
}

async function scrollToBottom() {
  await new Promise((resolve) => setTimeout(resolve))

  const threshold = 200
  const distanceToBottom =
    document.documentElement.scrollHeight -
    document.documentElement.scrollTop -
    document.documentElement.clientHeight

  if (distanceToBottom <= threshold) {
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: 'smooth',
    })
  }
}

const readFileAsDataUrl = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      if (typeof result === 'string') {
        resolve(result)
      } else {
        reject(new Error('读取图片失败'))
      }
    }
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })

const DEEP_RESEARCH_INTERNAL_PROMPT_MARKERS = [
  '你是一名研究规划助手',
  '请将用户话题拆解为研究计划',
  '只输出 json',
  'output json only',
  'each item has title, question, depth, parent_title',
]

function looksLikeDeepResearchInternalPrompt(raw: string) {
  const normalized = String(raw || '').toLowerCase()
  if (!normalized.trim()) return false
  return DEEP_RESEARCH_INTERNAL_PROMPT_MARKERS.some((marker) =>
    normalized.includes(marker.toLowerCase()),
  )
}

function extractTopicFromDeepResearchPrompt(raw: string) {
  const text = String(raw || '').trim()
  if (!text) return ''
  const topicMatch = text.match(/(?:^|\n)\s*(?:话题|topic)\s*[:：]\s*(.+)$/im)
  if (topicMatch?.[1]) {
    return topicMatch[1].trim()
  }
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !/^(要求|requirement|constraints?)\s*[:：]?/i.test(line))
    .filter((line) => !/json/i.test(line))
    .filter((line) => !/研究规划助手/i.test(line))
  if (!lines.length) return text
  return lines[lines.length - 1]
}

function sanitizeDeepResearchTopic(raw: string) {
  const text = String(raw || '').trim()
  if (!text) return ''
  if (!looksLikeDeepResearchInternalPrompt(text)) return text
  return extractTopicFromDeepResearchPrompt(text) || text
}

function looksLikeDeepResearchPlanPayload(raw: string) {
  const text = String(raw || '').trim()
  if (!text) return false
  if (!(text.startsWith('[') || text.startsWith('{'))) return false
  try {
    const parsed = JSON.parse(text)
    const items = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === 'object' && Array.isArray((parsed as any).items)
        ? (parsed as any).items
        : null
    if (!Array.isArray(items) || items.length === 0) return false
    const sampled = items.slice(0, 6)
    return sampled.every((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return false
      const row = item as Record<string, unknown>
      return (
        typeof row.title === 'string' &&
        typeof row.question === 'string' &&
        (typeof row.depth === 'number' || typeof row.depth === 'string') &&
        ('parent_title' in row || 'parentTitle' in row)
      )
    })
  } catch {
    return false
  }
}

function nowIsoTimestamp() {
  return new Date().toISOString()
}

function normalizeDeepResearchMetadata(metadata: unknown) {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return {}
  }
  return { ...(metadata as Record<string, any>) }
}

function resolveDeepResearchPresetKey(
  value: unknown,
  fallback: DeepResearchPresetKey = DEFAULT_DEEP_RESEARCH_PRESET,
): DeepResearchPresetKey {
  if (value === 'quick' || value === 'medium' || value === 'deep') {
    return value
  }
  return fallback
}

function normalizeDeepResearchRequestForExecution(
  request: DeepResearchRequest,
  presetKey?: DeepResearchPresetKey,
): DeepResearchRequest {
  const metadata = normalizeDeepResearchMetadata(request.metadata)
  const resolvedPreset = resolveDeepResearchPresetKey(
    presetKey ?? metadata.deep_research_preset,
    DEFAULT_DEEP_RESEARCH_PRESET,
  )
  return {
    ...request,
    ...DEEP_RESEARCH_PRESET_PARAMS[resolvedPreset],
    // DeepResearch runtime model routing is controlled by backend policy.
    llm_provider: undefined,
    llm_model: undefined,
    use_web_search: true,
    use_paper_search: true,
    use_code_exec: false,
    metadata: {
      ...metadata,
      deep_research_preset: resolvedPreset,
      deep_research_preset_force: true,
    },
  }
}

function serializePlanItemsForEditor(items: PlanItem[] = []) {
  if (!items.length) return ''
  return items
    .map((item) => {
      const depth = Math.max(1, Number(item.depth || 1))
      const title = String(item.title || '').trim()
      const question = String(item.question || item.title || '').trim()
      const parentTitle = depth > 1 ? String(item.parent_title || '').trim() : ''
      return `${depth} | ${title} | ${question} | ${parentTitle}`
    })
    .join('\n')
}

function parsePlanItemsFromEditorText(rawText: string) {
  const lines = String(rawText || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/^\d+\.\s*/, '').replace(/^[-*]\s*/, ''))

  if (!lines.length) {
    return { items: [] as PlanItem[], error: '计划内容为空，请至少保留 1 行计划项' }
  }

  const parsed: PlanItem[] = []
  let lastLevelOneTitle = ''
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const columns = line.split('|').map((segment) => segment.trim())
    if (columns.length < 2) {
      return {
        items: [] as PlanItem[],
        error: `第 ${index + 1} 行格式错误，请使用：标题 | 研究问题 | 父主题（可选）`,
      }
    }
    let depth = 1
    let title = ''
    let question = ''
    let parentTitle = ''

    if (/^\d+$/.test(columns[0])) {
      depth = Math.max(1, Math.min(6, Number(columns[0])))
      title = columns[1] || ''
      question = columns[2] || title
      parentTitle = columns[3] || ''
    } else {
      title = columns[0] || ''
      question = columns[1] || title
      parentTitle = columns[2] || ''
      depth = parentTitle ? 2 : 1
    }

    title = title.trim()
    question = (question || title).trim()
    parentTitle = parentTitle.trim()

    if (!title) {
      return {
        items: [] as PlanItem[],
        error: `第 ${index + 1} 行缺少标题，请补全后再试`,
      }
    }

    if (depth <= 1) {
      depth = 1
      parentTitle = ''
      lastLevelOneTitle = title
    } else if (!parentTitle) {
      parentTitle = lastLevelOneTitle
    }

    parsed.push({
      depth,
      title,
      question,
      parent_title: parentTitle || undefined,
    })
  }

  const levelOneCount = parsed.filter((item) => item.depth === 1).length
  if (!levelOneCount) {
    return { items: [] as PlanItem[], error: '至少需要 1 个一级主题（depth=1）' }
  }

  return { items: parsed, error: '' }
}

type DeepResearchPlanStreamProgress = {
  research_id?: string
  stage?: string
  event_type?: string
  message?: string
  timestamp?: string
  payload?: Record<string, any>
}

async function streamDeepResearchPlanPreview(
  requestPayload: DeepResearchRequest,
  handlers: {
    onProgress?: (event: DeepResearchPlanStreamProgress) => void
    onPlan?: (plan: DeepResearchPlan) => void
  },
) {
  const url = getDeepResearchPlanStreamUrl()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = typeof userState.token === 'string' ? userState.token.trim() : ''
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  let response: Response | null = null
  let requestError: unknown = null
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestPayload),
      })
      break
    } catch (error) {
      requestError = error
      if (attempt < 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 240))
      }
    }
  }
  if (!response) {
    if (requestError instanceof Error) throw requestError
    throw new Error('计划流请求失败')
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const text = await response.text()
      if (text) {
        try {
          const json = JSON.parse(text)
          const msg = String(json?.detail || json?.message || '').trim()
          detail = msg || detail
        } catch {
          detail = text.trim() || detail
        }
      }
    } catch {
      // ignore
    }
    throw new Error(detail)
  }

  if (!response.body) {
    throw new Error('计划流未返回可读数据')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let currentEvent = ''
  while (true) {
    const { value, done } = await reader.read()
    if (value) {
      buffer += decoder.decode(value, { stream: !done })
    }
    if (done && buffer.trim()) {
      buffer += '\n'
    }

    while (true) {
      const lineBreakIndex = buffer.indexOf('\n')
      if (lineBreakIndex === -1) break
      const rawLine = buffer.slice(0, lineBreakIndex)
      buffer = buffer.slice(lineBreakIndex + 1)
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
      const normalized = line.trimStart()
      if (!normalized) {
        currentEvent = ''
        continue
      }
      if (normalized.startsWith('event:')) {
        currentEvent = normalized.replace(/^event\s*:\s*/, '').trim()
        continue
      }
      if (!normalized.startsWith('data:')) continue
      const payloadText = normalized.replace(/^data\s*:\s?/, '')
      if (!payloadText) continue
      if (currentEvent === 'completion' && payloadText === '[DONE]') {
        return
      }
      let parsed: any
      try {
        parsed = JSON.parse(payloadText)
      } catch {
        continue
      }
      if (currentEvent === 'plan') {
        handlers.onPlan?.(parsed as DeepResearchPlan)
        continue
      }
      handlers.onProgress?.(parsed as DeepResearchPlanStreamProgress)
    }

    if (done) {
      break
    }
  }
}

function isPlaceholderSessionTitle(value?: string) {
  const text = String(value || '').trim()
  if (!text) return true
  if (/^session[_\s-]/i.test(text)) return true
  if (/^session kb \d+$/i.test(text)) return true
  if (/^session for kb \d+$/i.test(text)) return true
  if (/^message-only session /i.test(text)) return true
  if (/^对话 [a-z0-9_-]{4,}$/i.test(text)) return true
  return false
}

function buildSessionTitleFromPrompt(rawPrompt?: string) {
  const normalized = String(rawPrompt || '')
    .replace(/\[已附带图片\s*\d+\s*张\]/g, ' ')
    .replace(/@selection\d+/gi, ' ')
    .replace(/@file\d+/gi, ' ')
    .replace(/`+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!normalized) return ''

  const politePrefixRegex =
    /^(请你|请帮我|请|帮我|麻烦你|麻烦|可以帮我|可以|能不能|能否|我想|我需要|我希望)\s*/i
  const weakStartRegex = /^(这里|这段|这个|当前|现在)\s*/i
  const actionHints = [
    '修改',
    '改写',
    '重写',
    '润色',
    '优化',
    '修复',
    '排查',
    '分析',
    '总结',
    '解释',
    '完善',
    '重构',
    '补充',
    '新增',
    '删除',
    '调整',
    'replace',
    'rewrite',
    'refactor',
    'fix',
    'summarize',
    'analyze',
    'optimize',
  ]

  const sanitizeSegment = (segment: string) =>
    segment
      .replace(politePrefixRegex, '')
      .replace(weakStartRegex, '')
      .replace(/\s+/g, ' ')
      .trim()

  const segments = normalized
    .split(/[。！？!?；;：:\n]/)
    .map((item) => sanitizeSegment(item))
    .filter(Boolean)

  let candidate =
    segments.find((item) =>
      actionHints.some((hint) => item.toLowerCase().includes(hint.toLowerCase())),
    ) ||
    segments[0] ||
    normalized

  if (candidate.length < 10 && segments.length > 1) {
    candidate = `${candidate} · ${segments[1]}`
  }

  candidate = candidate
    .replace(/^[，,。.；;：:\-\s]+/, '')
    .replace(/[，,。.；;：:\-\s]+$/, '')
    .trim()

  if (!candidate) candidate = normalized

  const softLimit = 36
  const hardLimit = 120
  if (candidate.length > hardLimit) {
    return `${candidate.slice(0, hardLimit).trim()}...`
  }
  if (candidate.length > softLimit) {
    return `${candidate.slice(0, softLimit).trim()}...`
  }
  return candidate
}

export default function Index() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: ctx } = usePageTransport(transportToChatEnter)

  const [chat] = useState(() => {
    return proxy({
      list: [] as API.ChatItem[],
    })
  })
  const { list } = useSnapshot(chat) as { list: API.ChatItem[] }
  const [documents, setDocuments] = useState<API.Document[]>([])
  const [currentChatItem, setCurrentChatItemState] =
    useState<ChatItemWithToken | null>(null)
  const [rightPanelVisible, setRightPanelVisible] = useState(true)
  const [rightPanelMode, setRightPanelMode] = useState<RightPanelMode>('documents')
  const [rightPanelWidth, setRightPanelWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return DEEP_CHAT_RIGHT_PANEL_DEFAULT_WIDTH
    const saved = Number(localStorage.getItem(DEEP_CHAT_RIGHT_PANEL_WIDTH_STORAGE_KEY))
    if (Number.isFinite(saved) && saved > 0) return Math.round(saved)
    return DEEP_CHAT_RIGHT_PANEL_DEFAULT_WIDTH
  })
  const [isDraggingRightPanel, setIsDraggingRightPanel] = useState(false)
  const [activeDeepResearchItemId, setActiveDeepResearchItemId] =
    useState<number | null>(null)
  const [activeDeepResearchBlockId, setActiveDeepResearchBlockId] =
    useState<string | null>(null)
  const [activeDeepResearchEvidence, setActiveDeepResearchEvidence] =
    useState<DeepResearchBlockEvidence | null>(null)
  const [activeDeepResearchEvidenceLoading, setActiveDeepResearchEvidenceLoading] =
    useState(false)
  const [deepResearchUnreadByItemId, setDeepResearchUnreadByItemId] = useState<
    Record<number, number>
  >({})
  const [pendingAttachments, setPendingAttachments] = useState<
    API.ChatAttachment[]
  >([])
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [chatImageAttachments, setChatImageAttachments] = useState<
    API.ChatImageAttachment[]
  >([])
  const [sessionDefaults, setSessionDefaults] =
    useState<API.SessionDefaults | null>(null)
  const [llmModel, setLlmModel] = useState<LlmModelValue>(() => {
    if (typeof window === 'undefined') return DEFAULT_OPENAI_MODEL
    const saved = localStorage.getItem(DEEP_CHAT_LLM_LOCAL_STORAGE_KEY)
    return normalizeLlmModel(saved, 'openai')
  })
  const [composerValue, setComposerValue] = useState('')
  const [composerFocusKey, setComposerFocusKey] = useState(0)
  const [sessionDisplayTitle, setSessionDisplayTitle] = useState('新对话')
  const [feedbackByMessageId, setFeedbackByMessageId] = useState<
    Record<string, 'thumbs_up' | 'thumbs_down' | undefined>
  >({})
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [draftRagEnabled, setDraftRagEnabled] = useState(false)
  const [draftUserKnowledgeBaseId, setDraftUserKnowledgeBaseId] =
    useState<number | null>(null)
  const [draftRagMode, setDraftRagMode] = useState<'fast' | 'deep'>('fast')
  const [updatingDefaults, setUpdatingDefaults] = useState(false)
  const [researchMode, setResearchMode] = useState<'chat' | 'deep'>('chat')
  const [deepResearchPreset, setDeepResearchPreset] = useState<DeepResearchPresetKey>(() => {
    if (typeof window === 'undefined') return DEFAULT_DEEP_RESEARCH_PRESET
    const saved = localStorage.getItem(DEEP_CHAT_RESEARCH_PRESET_STORAGE_KEY)
    return resolveDeepResearchPresetKey(saved, DEFAULT_DEEP_RESEARCH_PRESET)
  })
  const deepResearchPresetRef = useRef<DeepResearchPresetKey>(deepResearchPreset)
  const [systemStatusOpen, setSystemStatusOpen] = useState(false)
  const [latestUsage, setLatestUsage] = useState<ChatUsageStats | null>(null)
  const [researchSuggestion, setResearchSuggestion] = useState<{
    topic: string
    reason: string
  } | null>(null)
  const [editingResearchItem, setEditingResearchItem] =
    useState<API.ChatItem | null>(null)
  const [researchPlanForm] = Form.useForm<DeepResearchPlanEditFormValues>()
  const [editingContext, setEditingContext] = useState<{
    messageId: string
  } | null>(null)
  const [localReplaceContext, setLocalReplaceContext] = useState<LocalReplaceContext | null>(
    null,
  )
  const abortControllerRef = useRef<AbortController | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<any> | null>(null)
  const activeAskRunIdRef = useRef('')
  const researchStreamRef = useRef<Map<number, EventSource>>(new Map())
  const researchStreamTimerRef = useRef<Map<number, number>>(new Map())
  const researchStreamEventIdRef = useRef<Map<number, string>>(new Map())
  const researchStreamRetryRef = useRef<Map<number, number>>(new Map())
  const researchStreamSnapshotCounterRef = useRef<Map<number, number>>(new Map())
  const researchStreamLastSnapshotAtRef = useRef<Map<number, number>>(new Map())
  const researchStreamSnapshotPendingRef = useRef<Set<number>>(new Set())
  const researchPersistTimerRef = useRef<number | null>(null)
  const researchRestorePendingRef = useRef(false)
  const deepResearchRestoreInFlightRef = useRef(false)
  const branchResetAbortRef = useRef(false)
  const deepResearchSubmitLockRef = useRef<Set<number>>(new Set())
  const suggestionDismissedRef = useRef<Set<string>>(new Set())
  const lastSuggestionTopicRef = useRef<string>('')
  const pendingSendRef = useRef<ChatPendingSendState | null>(null)
  const sessionNameRef = useRef('')
  const autoTitledSessionRef = useRef<Record<string, boolean>>({})
  const rightPanelVisibleRef = useRef(rightPanelVisible)
  const rightPanelModeRef = useRef<RightPanelMode>(rightPanelMode)
  const activeDeepResearchItemIdRef = useRef<number | null>(activeDeepResearchItemId)
  const openCitationsPanel = useCallback(
    (item: API.ChatItem | null, options?: { openPanel?: boolean }) => {
      if (!item) {
        setCurrentChatItemState(null)
        return
      }
      setRightPanelMode(item.reference?.length ? 'citations' : 'documents')
      if (options?.openPanel) {
        setRightPanelVisible(true)
      }
      setCurrentChatItemState({ ...item, __openToken: Date.now() })
    },
    [],
  )

  useEffect(() => {
    rightPanelVisibleRef.current = rightPanelVisible
  }, [rightPanelVisible])

  useEffect(() => {
    rightPanelModeRef.current = rightPanelMode
  }, [rightPanelMode])

  useEffect(() => {
    activeDeepResearchItemIdRef.current = activeDeepResearchItemId
  }, [activeDeepResearchItemId])

  const getChatLayoutWidth = useCallback(() => {
    if (typeof document === 'undefined') return 0
    const el = document.querySelector(`.${styles['chat-page-layout']}`) as HTMLElement | null
    if (!el) return 0
    return el.getBoundingClientRect().width
  }, [])

  const resolveRightPanelBounds = useCallback(
    (containerWidth?: number) => {
      const layoutWidth = containerWidth && containerWidth > 0 ? containerWidth : getChatLayoutWidth()
      const available = layoutWidth > 0 ? layoutWidth - DEEP_CHAT_MIN_MAIN_WIDTH : 0
      const maxWidth = Math.min(
        DEEP_CHAT_RIGHT_PANEL_MAX_WIDTH,
        Math.max(DEEP_CHAT_RIGHT_PANEL_HARD_MIN_WIDTH, available),
      )
      const minWidth = Math.min(DEEP_CHAT_RIGHT_PANEL_MIN_WIDTH, maxWidth)
      return { minWidth, maxWidth }
    },
    [getChatLayoutWidth],
  )

  const clampRightPanelWidth = useCallback(
    (next: number, containerWidth?: number) => {
      const { minWidth, maxWidth } = resolveRightPanelBounds(containerWidth)
      return Math.round(Math.max(minWidth, Math.min(maxWidth, next)))
    },
    [resolveRightPanelBounds],
  )

  const handleRightPanelResizeStart = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDraggingRightPanel(true)
  }, [])

  useEffect(() => {
    if (!isDraggingRightPanel) return

    const handleMouseMove = (event: MouseEvent) => {
      const layoutEl = document.querySelector(`.${styles['chat-page-layout']}`) as HTMLElement | null
      if (!layoutEl) return
      const rect = layoutEl.getBoundingClientRect()
      const widthFromCursor = rect.right - event.clientX
      setRightPanelWidth(clampRightPanelWidth(widthFromCursor, rect.width))
    }

    const handleMouseUp = () => {
      setIsDraggingRightPanel(false)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [clampRightPanelWidth, isDraggingRightPanel])

  useEffect(() => {
    const clampOnResize = () => {
      setRightPanelWidth((current) => clampRightPanelWidth(current))
    }
    clampOnResize()
    window.addEventListener('resize', clampOnResize)
    return () => window.removeEventListener('resize', clampOnResize)
  }, [clampRightPanelWidth])

  useEffect(() => {
    localStorage.setItem(DEEP_CHAT_RIGHT_PANEL_WIDTH_STORAGE_KEY, String(Math.round(rightPanelWidth)))
  }, [rightPanelWidth])

  const pageLayoutStyle = useMemo(
    () =>
      ({
        ['--chat-right-panel-width' as any]: `${rightPanelWidth}px`,
      }) as CSSProperties,
    [rightPanelWidth],
  )

  const pageLayoutClassName = useMemo(() => {
    return isDraggingRightPanel
      ? `${styles['chat-page-layout']} ${styles['chat-page-layout--resizing']}`
      : styles['chat-page-layout']
  }, [isDraggingRightPanel])

  const researchPersistKey = useMemo(
    () => (id ? `deep-research-cards:${id}` : ''),
    [id],
  )
  const researchSuppressedRunsKey = useMemo(
    () => (id ? `deep-research-suppressed-runs:${id}` : ''),
    [id],
  )

  useEffect(() => {
    if (!id) return
    autoTitledSessionRef.current = {}
    let cancelled = false
    api.session
      .info({ sessionId: id }, { loading: false, errorToast: false })
      .then(({ data }) => {
        if (cancelled) return
        const name = String(data?.sessionName || '').trim()
        sessionNameRef.current = name
        if (name) {
          setSessionDisplayTitle(name)
        } else {
          setSessionDisplayTitle('新对话')
        }
      })
      .catch(() => {
        if (cancelled) return
        sessionNameRef.current = ''
      })
    return () => {
      cancelled = true
    }
  }, [id])

  const tryAutoRenameSession = useCallback(
    async (prompt?: string) => {
      const sessionId = String(id || '').trim()
      if (!sessionId) return
      if (autoTitledSessionRef.current[sessionId]) return
      const nextTitle = buildSessionTitleFromPrompt(prompt)
      if (!nextTitle) return

      let currentTitle = String(sessionNameRef.current || '').trim()
      if (!currentTitle) {
        try {
          const { data } = await api.session.info(
            { sessionId },
            { loading: false, errorToast: false },
          )
          currentTitle = String(data?.sessionName || '').trim()
          sessionNameRef.current = currentTitle
          if (currentTitle) {
            setSessionDisplayTitle(currentTitle)
          }
        } catch {
          // 忽略读取失败，继续尝试自动命名
        }
      }
      if (currentTitle && !isPlaceholderSessionTitle(currentTitle)) {
        autoTitledSessionRef.current[sessionId] = true
        return
      }
      try {
        await api.session.rename(
          { sessionId, sessionName: nextTitle },
          { loading: false, errorToast: false },
        )
        autoTitledSessionRef.current[sessionId] = true
        sessionNameRef.current = nextTitle
        setSessionDisplayTitle(nextTitle)
        sessionActions.updateKey()
      } catch {
        // 不影响主流程
      }
    },
    [id],
  )

  const history = useRequest(
    async () => {
      const { data } = await api.session.detail({
        session_id: id!,
      })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        chat.list.splice(0, chat.list.length)
        let latestUsageFromHistory: ChatUsageStats | null = null
        data.forEach((item) => {
          const historyUserQuestionRaw = String(item.user_question || '')
          const historyModelAnswerRaw = String(item.model_answer || '')
          const isPlanLikeAnswer = looksLikeDeepResearchPlanPayload(historyModelAnswerRaw)
          const shouldSkipLegacyDeepResearchRecord =
            Boolean(historyModelAnswerRaw) &&
            (looksLikeDeepResearchInternalPrompt(historyUserQuestionRaw) ||
              (isPlanLikeAnswer &&
                !String(item.documents || '').trim() &&
                !String(item.recommended_questions || '').trim()))
          if (shouldSkipLegacyDeepResearchRecord) {
            return
          }

          if (item.user_question) {
            // 尝试从 retrieval_content 中提取 context_files
            let attachments: API.ChatAttachment[] | undefined
            let historyImages: API.ChatImageAttachment[] | undefined
            let retrievalData: Record<string, any> | undefined
            if (item.retrieval_content) {
              try {
                retrievalData = JSON.parse(item.retrieval_content)
              } catch (error) {
                console.error('Failed to parse retrieval_content:', error)
              }
            }
            if (retrievalData?.context_files && Array.isArray(retrievalData.context_files)) {
              attachments = retrievalData.context_files.map((file: any, idx: number) => ({
                id: idx,
                title: file.filename || '未知文件',
                knowledgeBaseId: 0,
              }))
            }
            const historyImagesRaw = Array.isArray(retrievalData?.images)
              ? retrievalData.images
              : Array.isArray(retrievalData?.image_attachments)
              ? retrievalData.image_attachments
              : Array.isArray(retrievalData?.imageAttachments)
              ? retrievalData.imageAttachments
              : []
            if (historyImagesRaw.length) {
              historyImages = historyImagesRaw
                .map((img: any, idx: number) => {
                  const dataUrl =
                    typeof img?.dataUrl === 'string'
                      ? img.dataUrl
                      : typeof img?.data_url === 'string'
                      ? img.data_url
                      : ''
                  if (!dataUrl) return null
                  return {
                    id: String(img?.id || `history-img-${idx + 1}`),
                    name: String(img?.name || `image-${idx + 1}`),
                    dataUrl,
                    mimeType: String(img?.mimeType || img?.mime_type || 'image/png'),
                    size: Number(img?.size || 0),
                  } as API.ChatImageAttachment
                })
                .filter(Boolean) as API.ChatImageAttachment[]
            }
            const historyQuestionDisplay = sanitizeDeepResearchTopic(historyUserQuestionRaw)

            chat.list.push({
              id: createChatId(),
              role: ChatRole.User,
              type: ChatType.Text,
              content: historyQuestionDisplay,
              attachments: attachments,
              images: historyImages?.length ? historyImages : undefined,
              message_id: item.message_id,
            })
          }

          if (item.model_answer) {
            const map = new Map<string, API.Document>()
            let reference: API.Reference[] = []
            let recommended_questions: string[] = []
            let usage: ChatUsageStats | undefined
            let elapsedSeconds: number | undefined
            let retrievalData: Record<string, any> | undefined
            let fallbackKbId: number | undefined
            if (item.retrieval_content) {
              try {
                retrievalData = JSON.parse(item.retrieval_content)
                usage = normalizeUsage(retrievalData?.usage) || undefined
                if (usage) {
                  latestUsageFromHistory = usage
                }
                const kbIdValue = retrievalData?.knowledge_base_id
                if (
                  typeof kbIdValue === 'number' ||
                  (typeof kbIdValue === 'string' && kbIdValue)
                ) {
                  fallbackKbId = Number(kbIdValue)
                }
                const totalMsRaw =
                  Number(retrievalData?.timing?.total_ms) ||
                  Number(retrievalData?.debug?.timing?.total_ms)
                if (Number.isFinite(totalMsRaw) && totalMsRaw > 0) {
                  elapsedSeconds = Number((totalMsRaw / 1000).toFixed(1))
                }
              } catch (error) {
                console.error('Failed to parse retrieval_content:', error)
              }
            }

            if (Array.isArray(retrievalData?.citations)) {
              reference = retrievalData.citations.map((c: any, idx: number) => {
                const docId = String(c.document_id ?? '')
                const positions = Array.isArray(c.positions)
                  ? c.positions
                  : c.page
                  ? [[c.page, 0]]
                  : []
                return {
                  id: c.id ?? `${docId}-${c.chunk_id ?? idx}`,
                  document_id: docId,
                  document_name: c.document_name || c.document_title || `文档 ${docId || idx + 1}`,
                  document_title: c.document_title || c.document_name || '',
                  doi: c.doi ?? undefined,
                  content_with_weight: c.source_text || c.snippet || '',
                  source_text: c.source_text || '',
                  snippet: c.snippet || '',
                  positions,
                  page: c.page ?? null,
                  score: c.score ?? null,
                  chunk_id: c.chunk_id ?? undefined,
                  knowledge_base_id:
                    c.knowledge_base_id ??
                    c.kb_id ??
                    (fallbackKbId !== undefined ? fallbackKbId : undefined),
                  page_range: c.page_range ?? null,
                  structure_path: c.structure_path ?? '',
                  structure_title: c.structure_title ?? '',
                  logical_type: c.logical_type ?? '',
                  element_type: c.element_type ?? '',
                  structure_chunk_index: c.structure_chunk_index ?? null,
                  structure_chunk_total: c.structure_chunk_total ?? null,
                  bbox_list: c.bbox_list ?? null,
                  offsets: c.offsets ?? undefined,
                  alignment_status: c.alignment_status ?? undefined,
                  source: c.source ?? undefined,
                  parser_engine: c.parser_engine ?? undefined,
                }
              })
            } else if (item.documents) {
              try {
                reference = JSON.parse(item.documents) as API.Reference[]
              } catch (error) {
                console.error(error)
              }
            }

            if (item.recommended_questions) {
              try {
                recommended_questions = JSON.parse(
                  item.recommended_questions,
                ) as string[]
              } catch (error) {
                console.error(error)
              }
            }

            reference?.forEach((chunk) => {
              const docId = chunk.document_id ?? ''
              map.set(docId, {
                document_id: docId,
                document_name: chunk.document_name ?? '',
                content_with_weight:
                  chunk.source_text || chunk.content_with_weight || '',
              })
              if (
                (chunk.knowledge_base_id === undefined ||
                  chunk.knowledge_base_id === null) &&
                fallbackKbId !== undefined
              ) {
                chunk.knowledge_base_id = fallbackKbId
              }
            })
            const documents = Array.from(map.values())

            chat.list.push({
              id: createChatId(),
              role: ChatRole.Assistant,
              type: ChatType.Document,
              content: historyModelAnswerRaw,
              think: item.think,
              reference: reference,
              documents: documents?.length ? documents : undefined,
              recommended_questions: recommended_questions?.length
                ? recommended_questions
                : undefined,
              usage,
              elapsed_seconds: elapsedSeconds,
              message_id: item.message_id,
            })
          }
        })
        setLatestUsage(latestUsageFromHistory)

        const latestWithReference = [...chat.list]
          .reverse()
          .find(
            (chatItem) =>
              chatItem.role === ChatRole.Assistant &&
              Array.isArray(chatItem.reference) &&
              chatItem.reference.length > 0,
          )
        if (latestWithReference) {
          openCitationsPanel(latestWithReference)
          if (latestWithReference.documents?.length) {
            setDocuments(latestWithReference.documents)
          } else if (latestWithReference.reference?.length) {
            const map = new Map<string, API.Document>()
            latestWithReference.reference.forEach((chunk) => {
              const docId = chunk.document_id ?? ''
              map.set(docId, {
                document_id: docId,
                document_name: chunk.document_name ?? '',
                content_with_weight:
                  chunk.source_text || chunk.content_with_weight || '',
              })
            })
            const docs = Array.from(map.values())
            if (docs.length) {
              latestWithReference.documents = docs
              setDocuments(docs)
            }
          }
        } else {
          openCitationsPanel(null)
          setDocuments([])
        }
        const firstPrompt = data.find(
          (row) => typeof row.user_question === 'string' && row.user_question.trim(),
        )?.user_question
        if (firstPrompt) {
          void tryAutoRenameSession(firstPrompt)
        }

        setTimeout(() => {
          window.scrollTo({
            top: document.documentElement.scrollHeight,
          })
        })
      },
    },
  )

  const defaultsReq = useRequest(
    async () => {
      if (!id) return null
      const { data } = await api.session.getDefaults({
        sessionId: id,
      })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        const next = data ?? null
        setSessionDefaults(next)
        // Sync the RAG toggle to match the session's persisted setting so the
        // UI reflects reality when opening/switching sessions.
        setDraftRagEnabled(
          Boolean(next?.useSessionKnowledgeBase || next?.useUserKnowledgeBase),
        )
        const fallbackModel =
          typeof window === 'undefined'
            ? DEFAULT_OPENAI_MODEL
            : localStorage.getItem(DEEP_CHAT_LLM_LOCAL_STORAGE_KEY)
        const nextModel = normalizeLlmModel(
          next?.llmModel || fallbackModel,
          next?.llmProvider,
        )
        setLlmModel(nextModel)
      },
    },
  )

  const kbReq = useRequest(
    async () => {
      const { data } = await api.repository.listKnowledgeBases()
      return (data ?? []).filter((item) => !item.is_ephemeral)
    },
    {
      manual: true,
      onSuccess(data) {
        setKnowledgeBases(data ?? [])
      },
    },
  )
  const { run: runLoadDefaults, loading: defaultsLoading } = defaultsReq
  const { run: runLoadKnowledgeBases } = kbReq

  const loading = useMemo(() => {
    return list.some((o) => o.loading) || history.loading
  }, [list, history.loading])
  const loadingRef = useRef(loading)
  loadingRef.current = loading
  useEffect(() => {
    deviceActions.setChatting(loading)
  }, [loading])
  useUnmount(() => {
    deviceActions.setChatting(false)
    researchStreamRef.current.forEach((source) => source.close())
    researchStreamRef.current.clear()
    researchStreamTimerRef.current.forEach((timer) => window.clearTimeout(timer))
    researchStreamTimerRef.current.clear()
    researchStreamRetryRef.current.clear()
    researchStreamEventIdRef.current.clear()
    researchStreamSnapshotCounterRef.current.clear()
    researchStreamLastSnapshotAtRef.current.clear()
    researchStreamSnapshotPendingRef.current.clear()
    if (researchPersistTimerRef.current) {
      window.clearTimeout(researchPersistTimerRef.current)
      researchPersistTimerRef.current = null
    }
  })

  useEffect(() => {
    setSessionDefaults(null)
    setResearchSuggestion(null)
    setChatImageAttachments([])
    setFeedbackByMessageId({})
    pendingSendRef.current = null
    deepResearchSubmitLockRef.current.clear()
    lastSuggestionTopicRef.current = ''
    setLocalReplaceContext(null)
    if (id) {
      researchRestorePendingRef.current = false
      runLoadDefaults()
    } else {
      researchRestorePendingRef.current = false
    }
    runLoadKnowledgeBases()
  }, [id, runLoadDefaults, runLoadKnowledgeBases])

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(DEEP_CHAT_LLM_LOCAL_STORAGE_KEY, llmModel)
  }, [llmModel])

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(DEEP_CHAT_RESEARCH_PRESET_STORAGE_KEY, deepResearchPreset)
  }, [deepResearchPreset])

  useEffect(() => {
    deepResearchPresetRef.current = deepResearchPreset
  }, [deepResearchPreset])

  const applyDefaults = useCallback(
    async (next: Partial<API.SessionDefaults>) => {
      if (!id || !sessionDefaults) return
      setUpdatingDefaults(true)
      const payload = { ...sessionDefaults, ...next } as API.SessionDefaults
      try {
        const { data } = await api.session.updateDefaults({
          sessionId: id,
          defaults: payload,
        })
        setSessionDefaults(data ?? payload)
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        message.error(
          detail ? `更新知识库设置失败：${detail}` : '更新知识库设置失败',
        )
        throw error
      } finally {
        setUpdatingDefaults(false)
      }
    },
    [id, sessionDefaults],
  )

  const handleLlmModelChange = useCallback(
    async (value: string) => {
      const normalized = normalizeLlmModel(value)
      const provider = resolveProviderByModel(normalized)
      setLlmModel(normalized)
      if (!sessionDefaults || updatingDefaults) return
      if (
        sessionDefaults.llmModel === normalized &&
        normalizeLlmProvider(sessionDefaults.llmProvider) === provider
      ) {
        return
      }
      try {
        await applyDefaults({
          llmProvider: provider,
          llmModel: normalized,
        })
      } catch {
        // applyDefaults 内已统一提示，这里不重复弹窗
      }
    },
    [sessionDefaults, updatingDefaults, applyDefaults],
  )

  const handleToggleUserKb = useCallback(
    async (checked: boolean) => {
      if (!sessionDefaults || updatingDefaults) return
      if (checked) {
        const available = knowledgeBases
        if (!available.length) {
          message.warning('暂无可用知识库，请先在知识库页面创建。')
          return
        }
        const targetId = resolvePreferredKnowledgeBaseId(available, [
          sessionDefaults.userKnowledgeBaseId,
        ])
        if (targetId == null) {
          message.warning('暂无可用知识库，请先在知识库页面创建。')
          return
        }
        try {
          await applyDefaults({
            useSessionKnowledgeBase: true,
            useUserKnowledgeBase: true,
            userKnowledgeBaseId: targetId,
          })
          persistLastUsedUserKnowledgeBaseId(targetId)
        } catch {
          // applyDefaults 已处理
        }
      } else {
        try {
          await applyDefaults({
            useSessionKnowledgeBase: false,
            useUserKnowledgeBase: false,
            userKnowledgeBaseId: null,
          })
        } catch {
          // applyDefaults 已处理
        }
      }
    },
    [sessionDefaults, knowledgeBases, updatingDefaults, applyDefaults],
  )

  const handleSelectUserKb = useCallback(
    async (value: number) => {
      if (!sessionDefaults || updatingDefaults) return
      if (value === sessionDefaults.userKnowledgeBaseId) return
      try {
        await applyDefaults({
          useSessionKnowledgeBase: true,
          useUserKnowledgeBase: true,
          userKnowledgeBaseId: value,
        })
        persistLastUsedUserKnowledgeBaseId(value)
      } catch {
        // applyDefaults 已处理
      }
    },
    [sessionDefaults, updatingDefaults, applyDefaults],
  )

  const handleDraftToggleUserKb = useCallback(
    (checked: boolean) => {
      if (checked) {
        if (!knowledgeBases.length) {
          message.warning('暂无可用知识库，请先在知识库页面创建。')
          return
        }
        const targetId = resolvePreferredKnowledgeBaseId(knowledgeBases, [
          draftUserKnowledgeBaseId,
        ])
        if (targetId == null) {
          message.warning('暂无可用知识库，请先在知识库页面创建。')
          return
        }
        setDraftRagEnabled(true)
        setDraftUserKnowledgeBaseId(targetId)
        persistLastUsedUserKnowledgeBaseId(targetId)
        return
      }
      setDraftRagEnabled(false)
      setDraftUserKnowledgeBaseId(null)
    },
    [knowledgeBases, draftUserKnowledgeBaseId],
  )

  const handleDraftSelectUserKb = useCallback((value: number) => {
    setDraftRagEnabled(true)
    setDraftUserKnowledgeBaseId(value)
    persistLastUsedUserKnowledgeBaseId(value)
  }, [])

  const handleDraftRagModeChange = useCallback((value: 'fast' | 'deep') => {
    setDraftRagMode(value)
  }, [])

  const handleDeepResearchPresetChange = useCallback((value: DeepResearchPresetKey) => {
    const next = resolveDeepResearchPresetKey(value, DEFAULT_DEEP_RESEARCH_PRESET)
    deepResearchPresetRef.current = next
    setDeepResearchPreset(next)
  }, [])

  useEffect(() => {
    if (!draftRagEnabled) return
    if (draftUserKnowledgeBaseId != null) {
      const exists = knowledgeBases.some((item) => item.id === draftUserKnowledgeBaseId)
      if (exists) return
    }
    if (!knowledgeBases.length) return
    const preferred = resolvePreferredKnowledgeBaseId(knowledgeBases, [
      draftUserKnowledgeBaseId,
    ])
    if (preferred != null) {
      setDraftUserKnowledgeBaseId(preferred)
    }
  }, [draftRagEnabled, draftUserKnowledgeBaseId, knowledgeBases])

  useEffect(() => {
    if (!sessionDefaults) return
    if (!(sessionDefaults.useSessionKnowledgeBase || sessionDefaults.useUserKnowledgeBase)) return
    const kbId = Number(sessionDefaults.userKnowledgeBaseId)
    if (!Number.isFinite(kbId) || kbId <= 0) return
    persistLastUsedUserKnowledgeBaseId(kbId)
  }, [
    sessionDefaults?.useSessionKnowledgeBase,
    sessionDefaults?.useUserKnowledgeBase,
    sessionDefaults?.userKnowledgeBaseId,
  ])

  const handleRagModeChange = useCallback(
    async (value: 'fast' | 'deep') => {
      if (!sessionDefaults || updatingDefaults) return
      const strategy = value === 'deep' ? 'multimodal_graph' : 'multi_stage'
      try {
        await applyDefaults({
          retrievalStrategy: strategy,
        })
      } catch {
        // applyDefaults 已处理错误提示
      }
    },
    [sessionDefaults, updatingDefaults, applyDefaults],
  )

  const rollbackPendingSendToComposer = useCallback(() => {
    const pending = pendingSendRef.current
    if (!pending || pending.committed) return false
    pendingSendRef.current = null

    const removeIdx = chat.list.findIndex((item) => item.id === pending.userMessageId)
    if (removeIdx !== -1) {
      chat.list.splice(removeIdx, 1)
    }
    const assistantIdx = chat.list.findIndex(
      (item) => item.id === pending.targetAssistantId,
    )
    if (assistantIdx !== -1) {
      chat.list.splice(assistantIdx, 1)
    }
    if (
      Array.isArray(pending.restoreItems) &&
      pending.restoreItems.length > 0 &&
      typeof pending.restoreIndex === 'number' &&
      pending.restoreIndex >= 0
    ) {
      chat.list.splice(pending.restoreIndex, 0, ...pending.restoreItems)
    }

    setComposerValue(pending.prompt)
    setComposerFocusKey((value) => value + 1)
    if (pending.replaceMessageId) {
      setEditingContext({ messageId: pending.replaceMessageId })
    }
    setPendingAttachments(pending.attachmentsSnapshot.map((item) => ({ ...item })))
    setPendingFiles([...pending.filesSnapshot])
    setChatImageAttachments(pending.imagesSnapshot.map((item) => ({ ...item })))
    openCitationsPanel(null)
    message.info('已中断，可继续编辑后重新发送')
    return true
  }, [chat.list, openCitationsPanel])

  const abortChat = useCallback(() => {
    const runId = activeAskRunIdRef.current
    if (id && runId) {
      void api.session.chatCancel({
        id,
        runId,
      }).catch(() => {})
    }
    if (readerRef.current) {
      readerRef.current.cancel().catch(() => {})
      readerRef.current = null
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    const rolledBack = rollbackPendingSendToComposer()
    if (!rolledBack) {
      const loadingItem = chat.list.find((item) => item.loading)
      if (loadingItem) {
        const index = chat.list.indexOf(loadingItem)
        if (index !== -1) {
          chat.list[index] = { ...loadingItem, loading: false }
        }
      }
    }
  }, [chat.list, id, rollbackPendingSendToComposer])

  const cancelActiveAskRunSilently = useCallback(() => {
    const runId = activeAskRunIdRef.current
    const hasActive = Boolean(runId || readerRef.current || abortControllerRef.current)
    if (!hasActive) return
    branchResetAbortRef.current = true
    if (id && runId) {
      void api.session.chatCancel({
        id,
        runId,
      }).catch(() => {})
    }
    if (pendingSendRef.current) {
      pendingSendRef.current.committed = true
    }
    if (readerRef.current) {
      readerRef.current.cancel().catch(() => {})
      readerRef.current = null
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
  }, [id])

  const sendChat = useCallback(
    async (
      target: API.ChatItem,
      message: string,
      extra?: {
        userItem?: API.ChatItem
        replaceMessageId?: string
        llmModel?: string
        llmProvider?: 'dashscope' | 'openai' | 'local'
        useRag?: boolean
      },
    ): Promise<{ rolledBack: boolean }> => {
      const targetId = target.id
      const resolveTargetIndex = () =>
        chat.list.findIndex((item) => item.id === targetId)
      const updateTarget = (mutator: (draft: API.ChatItem) => void) => {
        const idx = resolveTargetIndex()
        if (idx === -1) return
        const draft = { ...(chat.list[idx] as API.ChatItem) }
        mutator(draft)
        chat.list[idx] = draft
      }

      openCitationsPanel(target)
      updateTarget((draft) => {
        draft.loading = true
      })
      let needReload = false
      let rolledBack = false
      let lastEventSeq = -1
      const answerStartTs = Date.now()
      const requestRunId = createClientRunId()
      let streamRunId = requestRunId
      let replayAttempts = 0
      let sawCompletion = false
      const requestRetrievalDisabled = extra?.useRag === false
      activeAskRunIdRef.current = requestRunId
      abortControllerRef.current = new AbortController()
      const markPendingCommitted = () => {
        if (pendingSendRef.current?.targetAssistantId === targetId) {
          pendingSendRef.current.committed = true
        }
      }

      const canReplay = () =>
        Boolean(
          id &&
            streamRunId &&
            replayAttempts < 2 &&
            !abortControllerRef.current?.signal.aborted,
        )

      async function reconnectByReplay(): Promise<boolean> {
        if (!canReplay()) return false
        replayAttempts += 1
        try {
          const replayRes = await api.session.chatReplay(
            {
              id: id!,
              runId: streamRunId,
              sinceSeq: lastEventSeq,
            },
            {
              signal: abortControllerRef.current?.signal,
            },
          )
          const replayReader = replayRes.data?.getReader?.()
          if (!replayReader) return false
          readerRef.current = replayReader
          await read(replayReader)
          return true
        } catch {
          return false
        }
      }

      try {
        //后端接口
        const payload: Parameters<typeof api.session.chat>[0] = {
          id: id!,
          question: message,
          replaceFromMessageId: extra?.replaceMessageId,
          runId: requestRunId,
          indexMode: requestRetrievalDisabled ? 'disabled' : undefined,
          llmProvider: extra?.llmProvider,
          llmModel: extra?.llmModel,
          imageAttachments: Array.isArray(extra?.userItem?.images)
            ? extra.userItem.images.map((item) => ({
                id: item.id,
                name: item.name,
                dataUrl: item.dataUrl,
                mimeType: item.mimeType,
                size: item.size,
              }))
            : undefined,
        }
        const res = await api.session.chat(payload, {
          signal: abortControllerRef.current.signal,
        })
        sessionActions.updateKey()

        const reader = res.data.getReader()
        if (!reader) return { rolledBack: false }
        readerRef.current = reader

        await read(reader)
      } catch (error: any) {
        if (error.name === 'AbortError' || abortControllerRef.current?.signal.aborted) {
          if (branchResetAbortRef.current) {
            branchResetAbortRef.current = false
            needReload = false
            return { rolledBack: false }
          }
          const pending = pendingSendRef.current
          if (
            pending &&
            pending.targetAssistantId === targetId &&
            !pending.committed
          ) {
            rolledBack = rollbackPendingSendToComposer()
            needReload = false
            return { rolledBack }
          }
          // 后端可能已经完成并异步落库；中断后主动做一次历史同步，避免必须手动刷新
          needReload = true
          return { rolledBack: false }
        }
        const replayed = await reconnectByReplay()
        if (replayed) {
          return { rolledBack: false }
        }
        updateTarget((draft) => {
          draft.error = error?.message ?? 'Unknown error'
        })
        throw error
      } finally {
        activeAskRunIdRef.current = ''
        readerRef.current = null
        abortControllerRef.current = null
        updateTarget((draft) => {
          draft.loading = false
          if (draft.elapsed_seconds == null) {
            const hasVisibleContent = Boolean(
              (draft.content && String(draft.content).trim()) ||
                (draft.think && String(draft.think).trim()) ||
                draft.reference?.length ||
                draft.documents?.length,
            )
            if (hasVisibleContent) {
              const elapsed = Number(((Date.now() - answerStartTs) / 1000).toFixed(1))
              draft.elapsed_seconds = elapsed > 0 ? elapsed : 0.1
            }
          }
        })
        if (needReload) {
          chat.list.splice(0, chat.list.length)
          await history.run()
        }
        branchResetAbortRef.current = false
      }

      async function read(reader: ReadableStreamDefaultReader<any>) {
        let temp = ''
        const decoder = new TextDecoder('utf-8')
        let currentEvent: string | null = null
        while (true) {
          // 检查是否已中断
          if (abortControllerRef.current?.signal.aborted) {
            reader.cancel().catch(() => {})
            return
          }
          let readResult:
            | ReadableStreamReadResult<Uint8Array>
            | ReadableStreamReadResult<any>
          try {
            readResult = await reader.read()
          } catch (readError) {
            const replayed = await reconnectByReplay()
            if (replayed) return
            throw readError
          }
          const { value, done } = readResult
          // 再次检查中断状态
          if (abortControllerRef.current?.signal.aborted) {
            return
          }
          if (value) {
            temp += decoder.decode(value)
          }

          while (true) {
            const index = temp.indexOf('\n')
            if (index === -1) break

            const slice = temp.slice(0, index)
            temp = temp.slice(index + 1)
            const line = slice.endsWith('\r') ? slice.slice(0, -1) : slice
            const normalizedLine = line.trimStart()
            // 解析 SSE：记录 event 名称
            if (normalizedLine.startsWith('event:')) {
              currentEvent = normalizedLine.replace(/^event\s*:\s*/, '').trim()
              continue
            }
            // 只处理 data 行
            if (normalizedLine.startsWith('data:')) {
              const isCompletion = currentEvent === 'completion'
              parseData(normalizedLine, currentEvent || undefined)
              scrollToBottom()
              if (isCompletion || sawCompletion) {
                // 始终同步一次历史，兜底处理 SSE 丢片/中途断流导致的“已生成但未渲染”
                needReload = true
                return
              }
            }
          }

          if (done) {
            if (!sawCompletion) {
              const replayed = await reconnectByReplay()
              if (replayed) return
            }
            // 非 completion 结束，回落一次历史同步，确保最终状态一致
            needReload = true
            break
          }
        }
      }

      function parseData(slice: string, eventName?: string) {
        const raw = slice.endsWith('\r') ? slice.slice(0, -1) : slice
        if (!raw.startsWith('data:')) {
          return
        }
        const payload = raw.replace(/^data\s*:\s?/, '')
        if (payload === '[DONE]') {
          return
        }
        if (!payload.length) {
          return
        }

        let json: any = null
        try {
          json = JSON.parse(payload)
        } catch {
          // 纯文本流式内容
          markPendingCommitted()
          updateTarget((draft) => {
            draft.content = `${draft.content || ''}${payload}`
          })
          return
        }

        const targetIndex = resolveTargetIndex()
        if (targetIndex === -1) return

        const seq = Number(json?.seq)
        if (Number.isFinite(seq)) {
          if (seq <= lastEventSeq) return
          lastEventSeq = seq
        }
        if (typeof json?.run_id === 'string' && json.run_id.trim()) {
          streamRunId = json.run_id.trim()
          activeAskRunIdRef.current = streamRunId
        }
        if (eventName === 'completion' || json?.type === 'completion') {
          sawCompletion = true
        }

        const nextTarget = { ...(chat.list[targetIndex] as API.ChatItem) }
        const totalMsFromPayload =
          Number(json?.timing?.total_ms) ||
          Number(json?.debug?.timing?.total_ms)
        if (Number.isFinite(totalMsFromPayload) && totalMsFromPayload > 0) {
          nextTarget.elapsed_seconds = Number((totalMsFromPayload / 1000).toFixed(1))
        }

        if (typeof json?.stage === 'string') {
          const indexModeFromPayload = String(
            json?.index_mode || json?.debug?.index_mode || '',
          ).toLowerCase()
          const retrievalDisabled = indexModeFromPayload
            ? indexModeFromPayload === 'disabled'
            : requestRetrievalDisabled
          const stageTextMap: Record<string, string> = {
            accepted: retrievalDisabled
              ? '⏳ 请求已接收，正在准备回答...'
              : '⏳ 请求已接收，正在准备检索...',
            retrieving: retrievalDisabled
              ? '⏳ 正在分析问题并组织上下文，请稍候...'
              : '⏳ 正在检索知识库，请稍候...',
            retrieved: retrievalDisabled
              ? '⏳ 上下文准备完成，正在生成回答...'
              : '⏳ 检索完成，正在生成回答...',
          }
          const upstreamMessage =
            typeof json?.message === 'string' ? String(json.message).trim() : ''
          const statusText = (() => {
            if (!upstreamMessage) return stageTextMap[json.stage] || ''
            if (!retrievalDisabled) return upstreamMessage
            if (json.stage === 'retrieving' && /检索|retriev/i.test(upstreamMessage)) {
              return stageTextMap.retrieving
            }
            if (json.stage === 'retrieved' && /检索|retriev/i.test(upstreamMessage)) {
              return stageTextMap.retrieved
            }
            if (json.stage === 'accepted' && /检索|retriev/i.test(upstreamMessage)) {
              return stageTextMap.accepted
            }
            return upstreamMessage
          })()
          if (statusText && !json?.content) {
            nextTarget.think = statusText
          }
        }

        if (json?.content) {
          markPendingCommitted()
          if (json.thinking) {
            nextTarget.think = `${nextTarget.think || ''}${json.content || ''}`
          } else {
            if (
              typeof nextTarget.think === 'string' &&
              nextTarget.think.startsWith('⏳')
            ) {
              nextTarget.think = ''
            }
            nextTarget.content = `${nextTarget.content || ''}${json.content || ''}`
          }
        } else if (typeof json === 'string') {
          markPendingCommitted()
          nextTarget.content = `${nextTarget.content || ''}${json}`
        } else if (typeof json?.answer === 'string' && json.answer) {
          // completion 事件兜底：如果流式片段没到达，直接回填最终答案
          markPendingCommitted()
          nextTarget.content = nextTarget.content || json.answer
        }

        if (Array.isArray(json?.documents) && json.documents.length) {
          markPendingCommitted()
          nextTarget.reference = json.documents

          const map = new Map<string, API.Document>()
          json.documents.forEach((chunk: API.Reference) => {
            const docId = chunk.document_id ?? ''
            map.set(docId, {
              document_id: docId,
              document_name: chunk.document_name ?? '',
              content_with_weight: chunk.content_with_weight ?? '',
            })
          })
          const docs = Array.from(map.values())
          nextTarget.documents = docs
          setDocuments(docs)
        }

        const fallbackKbId =
          json?.debug?.kb_id ??
          json?.debug?.kbId ??
          sessionDefaults?.userKnowledgeBaseId

        if (Array.isArray(json?.citations) && json.citations.length) {
          markPendingCommitted()
          const refs: API.Reference[] = json.citations.map(
            (item: any, idx: number) => {
              const docId = String(item.document_id ?? '')
              const positions = Array.isArray(item.positions)
                ? item.positions
                : item.page
                ? [[item.page, 0]]
                : []
              return {
                id: `${docId}-${item.chunk_id ?? idx}`,
                document_id: docId,
                document_name:
                  item.document_name ||
                  item.document_title ||
                  `文档 ${docId || idx + 1}`,
                document_title: item.document_title || item.document_name || '',
                doi: item.doi ?? undefined,
                content_with_weight: item.snippet ?? item.source_text ?? '',
                source_text: item.source_text ?? '',
                page: item.page ?? null,
                score: item.score ?? null,
                positions,
                page_range: item.page_range ?? null,
                structure_title: item.structure_title ?? '',
                structure_path: item.structure_path ?? '',
                structure_chunk_index: item.structure_chunk_index ?? null,
                structure_chunk_total: item.structure_chunk_total ?? null,
                element_type: item.element_type ?? '',
                logical_type: item.logical_type ?? '',
                bbox_list: item.bbox_list ?? null,
                offsets: item.offsets ?? undefined,
                alignment_status: item.alignment_status ?? undefined,
                source: item.source ?? undefined,
                parser_engine: item.parser_engine ?? undefined,
                knowledge_base_id:
                  item.knowledge_base_id ??
                  item.kb_id ??
                  (typeof fallbackKbId === 'number'
                    ? fallbackKbId
                    : undefined),
              }
            },
          )

          nextTarget.reference = refs
          const map = new Map<string, API.Document>()
          refs.forEach((chunk) => {
            const docId = chunk.document_id ?? ''
            map.set(docId, {
              document_id: docId,
              document_name: chunk.document_name ?? '',
              content_with_weight:
                chunk.source_text || chunk.content_with_weight || '',
            })
          })
          const docs = Array.from(map.values())
          nextTarget.documents = docs
          setDocuments(docs)
        }

        if (Array.isArray(json?.recommended_questions)) {
          nextTarget.recommended_questions = json.recommended_questions
        }

        const usage = normalizeUsage(json?.usage)
        if (usage) {
          nextTarget.usage = usage
          setLatestUsage(usage)
        }

        if (json?.type === 'error' && typeof json?.message === 'string') {
          nextTarget.error = json.message
        }

        if (json?.message_id) {
          markPendingCommitted()
          nextTarget.message_id = json.message_id
          if (extra?.userItem) {
            extra.userItem.message_id = json.message_id
          }
        }

        chat.list[targetIndex] = nextTarget
      }
      return { rolledBack }
    },
    [
      chat,
      id,
      openCitationsPanel,
      setDocuments,
      history,
      rollbackPendingSendToComposer,
      sessionDefaults,
    ],
  )

  const handleFileSelected = useCallback((file: File) => {
    setPendingFiles((prev) => [...prev, file])
  }, [])

  const handleRemovePendingAttachment = useCallback((id: number) => {
    setPendingFiles((prev) => prev.filter((_, index) => index !== id))
  }, [])

  const buildChatImageAttachmentsFromFiles = useCallback(async (files: File[]) => {
    const imageFiles = files.filter((file) => file.type.startsWith('image/'))
    const results: API.ChatImageAttachment[] = []
    for (const file of imageFiles) {
      if (file.size > MAX_CHAT_IMAGE_SIZE_BYTES) {
        message.warning(`图片 ${file.name} 超过 10MB，已跳过`)
        continue
      }
      const dataUrl = await readFileAsDataUrl(file)
      results.push({
        id: window.crypto?.randomUUID?.() ?? `img-${Date.now()}-${Math.random()}`,
        name: file.name || `image-${Date.now()}.png`,
        dataUrl,
        mimeType: file.type || 'image/png',
        size: file.size || 0,
      })
    }
    return results
  }, [])

  const appendChatImageFiles = useCallback(
    async (files: File[]) => {
      if (!files.length) return
      const incoming = await buildChatImageAttachmentsFromFiles(files)
      if (!incoming.length) return
      setChatImageAttachments((prev) => {
        const remain = MAX_CHAT_IMAGE_COUNT - prev.length
        if (remain <= 0) {
          message.warning(`最多添加 ${MAX_CHAT_IMAGE_COUNT} 张图片`)
          return prev
        }
        const deduped: API.ChatImageAttachment[] = []
        const used = new Set(prev.map((item) => `${item.name}::${item.size}`))
        for (const item of incoming) {
          const key = `${item.name}::${item.size}`
          if (used.has(key)) continue
          used.add(key)
          deduped.push(item)
          if (deduped.length >= remain) break
        }
        if (!deduped.length) return prev
        if (incoming.length > deduped.length) {
          message.warning(`最多添加 ${MAX_CHAT_IMAGE_COUNT} 张图片`)
        }
        return [...prev, ...deduped]
      })
    },
    [buildChatImageAttachmentsFromFiles],
  )

  const handleRemoveChatImageAttachment = useCallback((id: string) => {
    setChatImageAttachments((prev) => prev.filter((item) => item.id !== id))
  }, [])

  // 同步 pendingFiles 到 pendingAttachments
  useEffect(() => {
    const attachments = pendingFiles.map((file, index) => ({
      id: index,
      title: file.name,
      knowledgeBaseId: 0,
    }))
    setPendingAttachments(attachments)
  }, [pendingFiles])

  const uploadPendingFiles = useCallback(
    async (files: File[], usingRag: boolean) => {
      if (!files.length) return true
      try {
        if (usingRag) {
          for (const file of files) {
            await api.session.upload({ sessionId: id!, file })
          }
          return true
        }
        for (const file of files) {
          await api.session.uploadForContext({
            sessionId: id!,
            file,
          })
        }
        for (const file of files) {
          api.session.upload({ sessionId: id!, file }).catch(() => {})
        }
        return true
      } catch {
        window.$app.message.error('文件上传失败')
        return false
      }
    },
    [id],
  )

  const schedulePersistDeepResearchCards = useCallback(() => {
    if (!researchPersistKey) return
    if (researchPersistTimerRef.current) {
      window.clearTimeout(researchPersistTimerRef.current)
    }
    researchPersistTimerRef.current = window.setTimeout(() => {
      const normalizePersistKey = (value: string) =>
        String(value || '')
          .replace(/\s+/g, ' ')
          .trim()
      const seenPersistKeys = new Set<string>()
      const records = chat.list
        .filter((item) => item.deepResearch)
        .map((item) => {
          const deepResearch = item.deepResearch!
          const promptKey = normalizePersistKey(
            sanitizeDeepResearchTopic(
              String(deepResearch.userMessage || deepResearch.topic || ''),
            ),
          )
          const persistKey = String(deepResearch.researchId || '').trim() || promptKey
          if (persistKey && seenPersistKeys.has(persistKey)) {
            return null
          }
          if (persistKey) {
            seenPersistKeys.add(persistKey)
          }
          return {
            userMessage: deepResearch.userMessage || deepResearch.topic,
            deepResearch: compactDeepResearchForPersist(deepResearch),
          }
        })
        .filter(Boolean) as Array<{
        userMessage?: string
        deepResearch: API.DeepResearchCardState
      }>
      if (records.length) {
        sessionStorage.setItem(
          researchPersistKey,
          JSON.stringify({ version: 1, items: records }),
        )
      } else {
        sessionStorage.removeItem(researchPersistKey)
      }
    }, 1200)
  }, [chat.list, researchPersistKey])

  const loadSuppressedResearchIds = useCallback(() => {
    if (!researchSuppressedRunsKey) return new Set<string>()
    try {
      const raw = sessionStorage.getItem(researchSuppressedRunsKey)
      if (!raw) return new Set<string>()
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return new Set<string>()
      return new Set(
        parsed
          .map((value) => String(value || '').trim())
          .filter(Boolean),
      )
    } catch {
      return new Set<string>()
    }
  }, [researchSuppressedRunsKey])

  const persistSuppressedResearchIds = useCallback(
    (ids: Set<string>) => {
      if (!researchSuppressedRunsKey) return
      if (!ids.size) {
        sessionStorage.removeItem(researchSuppressedRunsKey)
        return
      }
      sessionStorage.setItem(researchSuppressedRunsKey, JSON.stringify(Array.from(ids)))
    },
    [researchSuppressedRunsKey],
  )

  const suppressResearchIds = useCallback(
    (researchIds: string[]) => {
      const candidates = researchIds
        .map((value) => String(value || '').trim())
        .filter(Boolean)
      if (!candidates.length) return
      const next = loadSuppressedResearchIds()
      let changed = false
      candidates.forEach((researchId) => {
        if (!next.has(researchId)) {
          next.add(researchId)
          changed = true
        }
      })
      if (changed) {
        persistSuppressedResearchIds(next)
      }
    },
    [loadSuppressedResearchIds, persistSuppressedResearchIds],
  )

  const resolveBranchReplaceMessageId = useCallback(
    (fromIndex: number): string => {
      if (
        !Number.isFinite(fromIndex) ||
        fromIndex < 0 ||
        fromIndex >= chat.list.length
      ) {
        return ''
      }
      return (
        chat.list
          .slice(fromIndex)
          .map((item) => String(item.message_id || '').trim())
          .find(Boolean) || ''
      )
    },
    [chat.list],
  )

  const resolveKeepMessagesBeforeIndex = useCallback(
    (fromIndex: number): number => {
      if (
        !Number.isFinite(fromIndex) ||
        fromIndex <= 0
      ) {
        return 0
      }
      const uniqueMessageIds = new Set<string>()
      chat.list.slice(0, fromIndex).forEach((item) => {
        const messageId = String(item.message_id || '').trim()
        if (messageId) {
          uniqueMessageIds.add(messageId)
        }
      })
      return uniqueMessageIds.size
    },
    [chat.list],
  )

  const send = useCallback(
    async (
      promptText: string,
      options?: {
        replaceMessageId?: string
        bootstrap?: ComposerBootstrapPayload
        localReplaceIndex?: number
        localReplaceItemId?: number
      },
    ) => {
      if (loadingRef.current) return
      const normalizedMessage = String(promptText || '').trim()
      const bootstrap = options?.bootstrap
      const imagesSnapshot = Array.isArray(bootstrap?.imageAttachments)
        ? bootstrap!.imageAttachments!.map((item) => ({ ...item }))
        : chatImageAttachments.map((item) => ({ ...item }))
      if (!normalizedMessage && imagesSnapshot.length === 0) return
      if (!normalizedMessage && imagesSnapshot.length > 0) {
        message.warning('请先输入问题，再附带图片发送')
        return
      }
      let effectiveLlmModel: LlmModelValue = llmModel
      if (imagesSnapshot.length > 0 && !isVisionModel(effectiveLlmModel)) {
        effectiveLlmModel = defaultVisionModelByProvider(
          resolveProviderByModel(effectiveLlmModel),
        )
        message.info(`检测到图片输入，本次请求自动切换模型为 ${effectiveLlmModel}`)
      }

      const attachmentsSnapshot = Array.isArray(bootstrap?.pendingAttachments)
        ? bootstrap!.pendingAttachments!.map((item) => ({ ...item }))
        : pendingAttachments.map((item) => ({ ...item }))
      const filesSnapshot = Array.isArray(bootstrap?.pendingFiles)
        ? [...bootstrap!.pendingFiles!]
        : [...pendingFiles]
      // draftRagEnabled is the authoritative source for the current user toggle state.
      // sessionDefaults reflects the session's persisted config but must NOT override
      // the user's explicit per-message toggle choice.
      const usingRag =
        typeof bootstrap?.useRag === 'boolean' ? bootstrap.useRag : draftRagEnabled
      const explicitReplaceMessageId = String(
        options?.replaceMessageId ?? editingContext?.messageId ?? '',
      ).trim()
      let effectiveReplaceMessageId = explicitReplaceMessageId
      let insertIndex: number | undefined
      let restoreItems: API.ChatItem[] | undefined
      let restoreIndex: number | undefined
      let trimIndex = -1

      if (effectiveReplaceMessageId) {
        const editIdx = chat.list.findIndex(
          (item) => item.message_id === effectiveReplaceMessageId,
        )
        if (editIdx !== -1) {
          trimIndex = editIdx
        } else {
          // message_id 可能来自旧上下文或临时节点，兜底回退到本地索引截断。
          effectiveReplaceMessageId = ''
        }
      }
      if (trimIndex < 0) {
        let localIndex = -1
        if (typeof options?.localReplaceItemId === 'number') {
          localIndex = chat.list.findIndex((item) => item.id === options.localReplaceItemId)
        }
        if (
          localIndex < 0 &&
          typeof options?.localReplaceIndex === 'number' &&
          options.localReplaceIndex >= 0 &&
          options.localReplaceIndex < chat.list.length
        ) {
          localIndex = options.localReplaceIndex
        }
        if (localIndex >= 0) trimIndex = localIndex
      }
      if (!effectiveReplaceMessageId && trimIndex >= 0) {
        const fallbackReplaceMessageId = resolveBranchReplaceMessageId(trimIndex)
        if (fallbackReplaceMessageId) {
          effectiveReplaceMessageId = fallbackReplaceMessageId
        }
      }
      if (trimIndex >= 0 && !effectiveReplaceMessageId && id) {
        const keepMessages = resolveKeepMessagesBeforeIndex(trimIndex)
        try {
          await api.session.rewind(
            {
              sessionId: id,
              keepMessages,
            },
            {
              loading: false,
            },
          )
        } catch (error: any) {
          const detail =
            error?.response?.data?.detail ||
            error?.response?.data?.message ||
            error?.message ||
            '历史分支回卷失败，请重试'
          message.error(detail)
          return
        }
      }

      // 如果有待发送的文件，先上传
      if (filesSnapshot.length > 0) {
        const ok = await uploadPendingFiles(filesSnapshot, !!usingRag)
        if (!ok) return
      }

      if (trimIndex >= 0) {
        cancelActiveAskRunSilently()
        const removedItems = chat.list.slice(trimIndex)
        const removedResearchIds = removedItems
          .map((item) => item.deepResearch?.researchId)
          .filter((value): value is string => Boolean(value))
        suppressResearchIds(removedResearchIds)
        restoreItems = removedItems.map((item) => ({ ...item }))
        chat.list.splice(trimIndex)
        insertIndex = trimIndex
        restoreIndex = trimIndex
        openCitationsPanel(null)
        setDocuments([])
      }
      if (effectiveReplaceMessageId) {
        setEditingContext(null)
      }
      if (trimIndex >= 0) {
        setLocalReplaceContext(null)
      }

      const appendAtTail = insertIndex === undefined || insertIndex < 0
      const userMessageText =
        imagesSnapshot.length > 0
          ? `${normalizedMessage}\n\n[已附带图片 ${imagesSnapshot.length} 张]`
          : normalizedMessage
      const userMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.User,
        type: ChatType.Text,
        content: userMessageText,
        attachments: attachmentsSnapshot.length
          ? attachmentsSnapshot
          : undefined,
        images: imagesSnapshot.length ? imagesSnapshot : undefined,
      }
      const assistantMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.Assistant,
        type: ChatType.Document,
        content: '',
        documents: [] as API.Document[] | undefined,
      }

      if (appendAtTail && chat.list.length === 0) {
        chat.list.push(userMessage)
        chat.list.push(assistantMessage)
      } else if (appendAtTail) {
        chat.list.push(userMessage)
        chat.list.push(assistantMessage)
        scrollToBottom()
      } else {
        const insertionPoint =
          typeof insertIndex === 'number' && insertIndex >= 0
            ? insertIndex
            : chat.list.length
        chat.list.splice(insertionPoint, 0, userMessage, assistantMessage)
      }

      const target = assistantMessage
      pendingSendRef.current = {
        targetAssistantId: assistantMessage.id,
        userMessageId: userMessage.id,
        prompt: normalizedMessage,
        attachmentsSnapshot,
        filesSnapshot,
        imagesSnapshot,
        replaceMessageId: effectiveReplaceMessageId || undefined,
        restoreIndex,
        restoreItems,
        committed: false,
      }
      void tryAutoRenameSession(userMessageText)

      try {
        const result = await sendChat(target, normalizedMessage, {
          userItem: userMessage,
          replaceMessageId: effectiveReplaceMessageId || undefined,
          llmProvider: resolveProviderByModel(effectiveLlmModel),
          llmModel: effectiveLlmModel,
          useRag: usingRag,
        })
        if (result.rolledBack) return
        setPendingAttachments([])
        setPendingFiles([])
        setChatImageAttachments([])
      } finally {
        if (pendingSendRef.current?.targetAssistantId === assistantMessage.id) {
          pendingSendRef.current = null
        }
      }
    },
    [
      chat,
      tryAutoRenameSession,
      sendChat,
      llmModel,
      chatImageAttachments,
      pendingAttachments,
      pendingFiles,
      id,
      sessionDefaults,
      editingContext,
      cancelActiveAskRunSilently,
      openCitationsPanel,
      suppressResearchIds,
      setDocuments,
      resolveBranchReplaceMessageId,
      resolveKeepMessagesBeforeIndex,
    ],
  )

  const resolveErrorMessage = useCallback((error: any, fallback: string) => {
    return (
      error?.response?.data?.detail ||
      error?.response?.data?.message ||
      error?.message ||
      fallback
    )
  }, [])

  const evaluateDeepResearchSuggestion = useCallback((text: string) => {
    const normalized = text.trim()
    if (!normalized) return null
    const keywords = [
      '调研',
      '研究',
      '综述',
      '对比',
      '比较',
      '最新',
      '进展',
      '路线图',
      '论文',
      '文献',
      'survey',
      'state of the art',
      'benchmark',
      'sota',
    ]
    const hitKeyword = keywords.find((keyword) =>
      normalized.toLowerCase().includes(keyword.toLowerCase()),
    )
    if (hitKeyword) {
      return `包含“${hitKeyword}”等研究关键词，适合深度研究流程`
    }
    if (normalized.length >= 60) {
      return '问题较复杂，建议用深度研究进行系统性梳理'
    }
    if ((normalized.match(/[，,;；]/g) || []).length >= 2) {
      return '问题包含多个维度，深度研究更容易覆盖全面'
    }
    return null
  }, [])

  const updateDeepResearchItem = useCallback(
    (itemId: number, updater: (data: API.DeepResearchCardState) => void) => {
      const target = chat.list.find((item) => item.id === itemId)
      if (!target?.deepResearch) return
      updater(target.deepResearch)
      schedulePersistDeepResearchCards()
    },
    [chat, schedulePersistDeepResearchCards],
  )

  const appendDeepResearchProgressEvent = useCallback(
    (
      itemId: number,
      payload: {
        stage: string
        message: string
        event_type?: string
        payload?: Record<string, any>
        timestamp?: string
        research_id?: string
      },
    ) => {
      const timestamp = payload.timestamp || nowIsoTimestamp()
      updateDeepResearchItem(itemId, (state) => {
        const event: ProgressEvent = {
          research_id: payload.research_id || state.researchId || `local-${itemId}`,
          stage: payload.stage,
          message: payload.message,
          event_type: payload.event_type,
          payload: payload.payload || {},
          timestamp,
        }
        state.progress = [...(state.progress || []), event].slice(
          -DEEP_RESEARCH_PROGRESS_BUFFER_LIMIT,
        )
        state.lastStage = payload.stage
        state.statusMessage = payload.message
        state.updatedAt = timestamp
      })
    },
    [updateDeepResearchItem],
  )

  const closeDeepResearchStream = useCallback((itemId: number) => {
    const source = researchStreamRef.current.get(itemId)
    if (source) {
      source.close()
      researchStreamRef.current.delete(itemId)
    }
    const timer = researchStreamTimerRef.current.get(itemId)
    if (timer) {
      window.clearTimeout(timer)
      researchStreamTimerRef.current.delete(itemId)
    }
    researchStreamRetryRef.current.delete(itemId)
    researchStreamEventIdRef.current.delete(itemId)
    researchStreamSnapshotCounterRef.current.delete(itemId)
    researchStreamLastSnapshotAtRef.current.delete(itemId)
    researchStreamSnapshotPendingRef.current.delete(itemId)
  }, [])

  const fetchDeepResearchSnapshot = useCallback(
    async (itemId: number, researchId: string) => {
      try {
        const { data } = await api.deepResearch.getDeepResearchSnapshot(researchId, {
          loading: false,
          errorToast: false,
          params: {
            compact: true,
          },
        })
        const outline = data?.outline as API.DeepResearchCardState['snapshotOutline']
        const queue = data?.queue as API.DeepResearchCardState['snapshotQueue']
        const report = data?.report as API.DeepResearchCardState['report']
        const runMeta = data?.meta as DeepResearchRunMeta | undefined
        const citationsPayload = data?.citations as { citations?: any[] } | undefined
        const citations: DeepResearchCitation[] = Array.isArray(citationsPayload?.citations)
          ? (citationsPayload?.citations as DeepResearchCitation[])
          : []
        const runStatus = String(runMeta?.status || '').trim().toLowerCase()
        const runError = String(runMeta?.error || '').trim()
        const cancelReason = String(runMeta?.cancel_reason || '').trim()
        const isRunningLike = runStatus === 'running' || runStatus === 'queued'
        const reportStatus = String(report?.status || '').trim().toLowerCase()
        const normalizedReport =
          report && isRunningLike
            ? ({
                ...report,
                // Keep in-progress report payload compact to avoid UI jank/OOM
                // while still preserving a live preview.
                draft_markdown:
                  typeof report.draft_markdown === 'string' &&
                  report.draft_markdown.length > DEEP_RESEARCH_RUNNING_REPORT_PREVIEW_MAX_CHARS
                    ? report.draft_markdown.slice(-DEEP_RESEARCH_RUNNING_REPORT_PREVIEW_MAX_CHARS)
                    : report.draft_markdown,
                report_markdown:
                  typeof report.report_markdown === 'string' &&
                  report.report_markdown.length > DEEP_RESEARCH_RUNNING_REPORT_PREVIEW_MAX_CHARS
                    ? report.report_markdown.slice(-DEEP_RESEARCH_RUNNING_REPORT_PREVIEW_MAX_CHARS)
                    : report.report_markdown,
              } as API.DeepResearchCardState['report'])
            : report
        const visibleCitations =
          isRunningLike && citations.length > DEEP_RESEARCH_RUNNING_CITATIONS_LIMIT
            ? citations.slice(0, DEEP_RESEARCH_RUNNING_CITATIONS_LIMIT)
            : citations
        let shouldCloseStream = false
        updateDeepResearchItem(itemId, (state) => {
          if (outline?.items?.length) {
            state.snapshotOutline = outline
          }
          if (queue?.blocks?.length) {
            state.snapshotQueue = queue
            const nextStats = deriveBlockStatsFromQueue(queue, state.blockStats)
            if (nextStats) {
              state.blockStats = normalizeBlockStats(nextStats, queue)
            }
          }
          if (normalizedReport) {
            state.report = normalizedReport
          }
          if (Array.isArray(citationsPayload?.citations)) {
            state.citations = visibleCitations
          }
          const summary = runMeta?.summary as Record<string, unknown> | undefined
          if (summary && typeof summary === 'object') {
            const stats = { ...(state.blockStats ?? {}) }
            const blocksByStatus =
              summary.blocks_by_status && typeof summary.blocks_by_status === 'object'
                ? (summary.blocks_by_status as Record<string, unknown>)
                : null
            if (typeof summary.blocks_total === 'number') {
              stats.total = Number(summary.blocks_total)
            }
            if (typeof summary.blocks_completed === 'number') {
              stats.completed = Number(summary.blocks_completed)
            } else if (typeof blocksByStatus?.completed === 'number') {
              stats.completed = Number(blocksByStatus.completed)
            }
            if (typeof summary.blocks_pending === 'number') {
              stats.pending = Number(summary.blocks_pending)
            } else if (
              blocksByStatus &&
              (typeof blocksByStatus.pending === 'number' ||
                typeof blocksByStatus.researching === 'number' ||
                typeof blocksByStatus.queued === 'number')
            ) {
              stats.pending =
                Number(blocksByStatus.pending || 0) +
                Number(blocksByStatus.researching || 0) +
                Number(blocksByStatus.queued || 0)
            }
            if (typeof summary.citations_total === 'number') {
              stats.citations = Number(summary.citations_total)
            }
            if (Object.keys(stats).length) {
              state.blockStats = normalizeBlockStats(stats, state.snapshotQueue)
            }
          }
          if (runMeta?.last_progress_at) {
            state.updatedAt = runMeta.last_progress_at
          }
          if (normalizedReport?.report_markdown && reportStatus === 'completed') {
            state.status = 'completed'
            state.statusMessage = '报告已完成'
            shouldCloseStream = true
          } else if (runStatus === 'failed') {
            state.status = 'failed'
            state.statusMessage = runError || '任务执行失败'
            shouldCloseStream = true
          } else if (runStatus === 'cancelled') {
            state.status = 'cancelled'
            state.statusMessage = cancelReason || '任务已取消'
            shouldCloseStream = true
          } else if (
            normalizedReport?.report_markdown &&
            reportStatus &&
            reportStatus !== 'completed'
          ) {
            // Sectional report preview may persist partial markdown while run is still active.
            state.status = 'running'
          } else if (runStatus === 'running') {
            state.status = 'running'
          } else if (runStatus === 'queued') {
            state.status = 'queued'
          }
        })
        if (shouldCloseStream) {
          closeDeepResearchStream(itemId)
        }
      } catch (error) {
        console.warn('Failed to fetch deep research snapshot', error)
      }
    },
    [closeDeepResearchStream, updateDeepResearchItem],
  )

  const openDeepResearchStream = useCallback(
    (itemId: number, researchId: string) => {
      closeDeepResearchStream(itemId)
      const connect = () => {
        const baseUrl = api.deepResearch.getDeepResearchProgressStreamUrl(researchId)
        const lastEventId = researchStreamEventIdRef.current.get(itemId)
        const querySep = baseUrl.includes('?') ? '&' : '?'
        const url = lastEventId
          ? `${baseUrl}${querySep}last_event_id=${encodeURIComponent(lastEventId)}`
          : baseUrl
        const source = new EventSource(url)
        researchStreamRef.current.set(itemId, source)

        source.addEventListener('progress', (event) => {
          const messageEvent = event as MessageEvent<string>
          if (messageEvent.lastEventId) {
            researchStreamEventIdRef.current.set(itemId, messageEvent.lastEventId)
          }
          if (!messageEvent.data) return
          try {
            const parsed = JSON.parse(messageEvent.data) as ProgressEvent
            const eventType = String(parsed.event_type || '').trim().toLowerCase()
            const eventMessage = String(parsed.message || '').trim()
            const eventMessageLower = eventMessage.toLowerCase()
            const stageLower = String(parsed.stage || '').trim().toLowerCase()
            const payload = (parsed.payload || {}) as Record<string, unknown>
            const isFinalReportingCompletedEvent =
              stageLower === 'reporting' &&
              eventType === 'report.completed' &&
              (payload.final === true ||
                eventMessageLower.includes('reporting completed') ||
                eventMessage.includes('报告流程完成') ||
                eventMessage.includes('报告完成'))
            const isFailedEvent = eventType === 'run.failed'
            const isCancelledEvent =
              eventType.includes('cancel') ||
              eventMessageLower.includes('cancelled') ||
              eventMessage.includes('取消')
            let shouldCloseStreamByEvent = false
            updateDeepResearchItem(itemId, (state) => {
              const next = [...(state.progress ?? []), parsed].slice(
                -DEEP_RESEARCH_PROGRESS_BUFFER_LIMIT,
              )
              state.progress = next
              state.lastStage = parsed.stage
              state.statusMessage = parsed.message
              state.updatedAt = parsed.timestamp
              const stats = { ...(state.blockStats ?? {}) }
              if (typeof payload.blocks === 'number') stats.total = payload.blocks
              if (typeof payload.completed === 'number') stats.completed = payload.completed
              if (typeof payload.pending === 'number') stats.pending = payload.pending
              if (typeof payload.iteration === 'number') stats.iteration = payload.iteration
              if (typeof payload.max_iterations === 'number') {
                stats.maxIterations = payload.max_iterations
              }
              // NOTE: payload.citations is intentionally NOT used here to update
              // stats.citations.  Research-stage tool events use citations_found
              // (per-call raw count) which is misleadingly large (e.g. 120 RAG
              // results).  The authoritative final count comes from citations_total
              // in the report.completed summary event and from the snapshot fetch.
              if (Object.keys(stats).length) {
                state.blockStats = normalizeBlockStats(stats, state.snapshotQueue)
              }
              const toolCounts = { ...(state.toolCounts ?? {}) }
              const toolCalls = Array.isArray(payload.tool_calls) ? payload.tool_calls : []
              toolCalls.forEach((tool) => {
                if (!tool) return
                const name = String(tool)
                toolCounts[name] = (toolCounts[name] ?? 0) + 1
              })
              if (payload.tool) {
                const name = String(payload.tool)
                toolCounts[name] = (toolCounts[name] ?? 0) + 1
              } else if (payload.tool_type) {
                const name = String(payload.tool_type)
                toolCounts[name] = (toolCounts[name] ?? 0) + 1
              }
              if (Object.keys(toolCounts).length) {
                state.toolCounts = toolCounts
              }
              if (isFailedEvent) {
                state.status = 'failed'
                state.statusMessage = eventMessage || state.statusMessage || '任务执行失败'
                shouldCloseStreamByEvent = true
              } else if (isCancelledEvent) {
                state.status = 'cancelled'
                state.statusMessage = eventMessage || state.statusMessage || '任务已取消'
                shouldCloseStreamByEvent = true
              } else if (isFinalReportingCompletedEvent) {
                state.status = 'completed'
                state.statusMessage = eventMessage || '报告已完成'
                const summary = payload.summary
                if (summary && typeof summary === 'object') {
                  const summaryRecord = summary as Record<string, unknown>
                  const blocksByStatus =
                    summaryRecord.blocks_by_status &&
                    typeof summaryRecord.blocks_by_status === 'object'
                      ? (summaryRecord.blocks_by_status as Record<string, unknown>)
                      : null
                  if (typeof summaryRecord.blocks_total === 'number') {
                    stats.total = Number(summaryRecord.blocks_total)
                  }
                  if (typeof blocksByStatus?.completed === 'number') {
                    stats.completed = Number(blocksByStatus.completed)
                  }
                  if (
                    blocksByStatus &&
                    (typeof blocksByStatus.pending === 'number' ||
                      typeof blocksByStatus.researching === 'number' ||
                      typeof blocksByStatus.queued === 'number')
                  ) {
                    stats.pending =
                      Number(blocksByStatus.pending || 0) +
                      Number(blocksByStatus.researching || 0) +
                      Number(blocksByStatus.queued || 0)
                  }
                  if (typeof summaryRecord.citations_total === 'number') {
                    stats.citations = Number(summaryRecord.citations_total)
                  }
                }
                state.blockStats = normalizeBlockStats(stats, state.snapshotQueue)
                shouldCloseStreamByEvent = true
              } else if (state.status === 'queued' || state.status === 'plan') {
                state.status = 'running'
              }
            })
            const snapshotCount =
              (researchStreamSnapshotCounterRef.current.get(itemId) ?? 0) + 1
            researchStreamSnapshotCounterRef.current.set(itemId, snapshotCount)
            const isReportingStage = String(parsed.stage || '').trim().toLowerCase() === 'reporting'
            const snapshotEvery = isReportingStage ? 10 : 5
            const snapshotIntervalMs = isReportingStage
              ? DEEP_RESEARCH_STREAM_SNAPSHOT_INTERVAL_REPORTING_MS
              : DEEP_RESEARCH_STREAM_SNAPSHOT_INTERVAL_MS
            const nowMs = Date.now()
            const lastSnapshotAt = researchStreamLastSnapshotAtRef.current.get(itemId) ?? 0
            const shouldRefreshSnapshotImmediately =
              eventType === 'research.completed' ||
              eventType === 'research.failed' ||
              eventType === 'tool.failed' ||
              eventType === 'report.completed' ||
              eventType === 'run.failed'
            const shouldRefreshSnapshotByCadence = snapshotCount % snapshotEvery === 0
            const canRefreshByTime = nowMs - lastSnapshotAt >= snapshotIntervalMs
            const shouldRefreshSnapshot =
              shouldRefreshSnapshotImmediately || (shouldRefreshSnapshotByCadence && canRefreshByTime)
            const shouldForceFinalSnapshot = isFinalReportingCompletedEvent || shouldCloseStreamByEvent
            if (
              shouldRefreshSnapshot &&
              !shouldForceFinalSnapshot &&
              !researchStreamSnapshotPendingRef.current.has(itemId)
            ) {
              researchStreamSnapshotPendingRef.current.add(itemId)
              researchStreamLastSnapshotAtRef.current.set(itemId, nowMs)
              void fetchDeepResearchSnapshot(itemId, researchId).finally(() => {
                researchStreamSnapshotPendingRef.current.delete(itemId)
              })
            }
            const isViewingProcessPanel =
              rightPanelVisibleRef.current &&
              rightPanelModeRef.current === 'deep_research' &&
              activeDeepResearchItemIdRef.current === itemId
            if (!isViewingProcessPanel) {
              setDeepResearchUnreadByItemId((prev) => ({
                ...prev,
                [itemId]: (prev[itemId] ?? 0) + 1,
              }))
            }
            if (shouldForceFinalSnapshot) {
              // Final state should win over any stale pending flag so the UI can
              // immediately converge without requiring a manual page refresh.
              researchStreamSnapshotPendingRef.current.delete(itemId)
              researchStreamSnapshotPendingRef.current.add(itemId)
              researchStreamLastSnapshotAtRef.current.set(itemId, Date.now())
              void fetchDeepResearchSnapshot(itemId, researchId).finally(() => {
                researchStreamSnapshotPendingRef.current.delete(itemId)
              })
            }
            if (shouldCloseStreamByEvent) {
              closeDeepResearchStream(itemId)
            }
          } catch (error) {
            console.warn('Failed to parse research progress', error)
          }
        })

        source.addEventListener('heartbeat', () => {})

        source.onerror = () => {
          source.close()
          researchStreamRef.current.delete(itemId)
          const retry = (researchStreamRetryRef.current.get(itemId) ?? 0) + 1
          researchStreamRetryRef.current.set(itemId, retry)
          const delay = Math.min(20000, 2000 * retry)
          const timer = window.setTimeout(() => connect(), delay)
          researchStreamTimerRef.current.set(itemId, timer)
        }
      }

      connect()
    },
    [closeDeepResearchStream, fetchDeepResearchSnapshot, updateDeepResearchItem],
  )

  const openDeepResearchProcessPanel = useCallback(
    (item: API.ChatItem | null, options?: { openPanel?: boolean }) => {
      if (!item?.deepResearch) return
      setActiveDeepResearchItemId(item.id)
      setRightPanelMode('deep_research')
      setDeepResearchUnreadByItemId((prev) => ({ ...prev, [item.id]: 0 }))
      if (options?.openPanel ?? true) {
        setRightPanelVisible(true)
      }
      if (item.deepResearch.researchId) {
        void fetchDeepResearchSnapshot(item.id, item.deepResearch.researchId)
      } else {
        setActiveDeepResearchEvidenceLoading(false)
        setActiveDeepResearchEvidence(null)
      }
    },
    [fetchDeepResearchSnapshot],
  )

  const activeDeepResearchItem = useMemo(() => {
    if (activeDeepResearchItemId == null) return null
    const target = chat.list.find((entry) => entry.id === activeDeepResearchItemId)
    if (!target?.deepResearch) return null
    return target
  }, [chat.list, activeDeepResearchItemId])

  const activeDeepResearchBlocks = useMemo<TopicBlock[]>(() => {
    const queueBlocks = activeDeepResearchItem?.deepResearch?.snapshotQueue?.blocks
    if (!Array.isArray(queueBlocks)) return []
    return queueBlocks as TopicBlock[]
  }, [activeDeepResearchItem?.deepResearch?.snapshotQueue?.blocks])

  const refreshActiveDeepResearchSnapshot = useCallback(async () => {
    const target = activeDeepResearchItem
    const researchId = target?.deepResearch?.researchId
    if (!target || !researchId) return
    await fetchDeepResearchSnapshot(target.id, researchId)
  }, [activeDeepResearchItem, fetchDeepResearchSnapshot])

  const refreshActiveDeepResearchEvidence = useCallback(
    async (blockIdOverride?: string) => {
      const target = activeDeepResearchItem
      const researchId = target?.deepResearch?.researchId
      const blockId = blockIdOverride || activeDeepResearchBlockId
      if (!target || !researchId || !blockId) {
        setActiveDeepResearchEvidence(null)
        return
      }
      setActiveDeepResearchEvidenceLoading(true)
      try {
        const { data } = await api.deepResearch.getDeepResearchBlockEvidence(
          researchId,
          blockId,
          { loading: false, errorToast: false },
        )
        setActiveDeepResearchEvidence(data as DeepResearchBlockEvidence)
      } catch (error) {
        console.warn('Failed to fetch deep research block evidence', error)
        setActiveDeepResearchEvidence(null)
      } finally {
        setActiveDeepResearchEvidenceLoading(false)
      }
    },
    [activeDeepResearchBlockId, activeDeepResearchItem],
  )

  const handleDeepResearchOpenProcess = useCallback(
    (item: API.ChatItem) => {
      openDeepResearchProcessPanel(item, { openPanel: true })
    },
    [openDeepResearchProcessPanel],
  )

  useEffect(() => {
    const researchId = activeDeepResearchItem?.deepResearch?.researchId
    if (!researchId) {
      setActiveDeepResearchBlockId(null)
      setActiveDeepResearchEvidence(null)
      return
    }
    const candidates = activeDeepResearchBlocks.filter((block) => block.depth > 0)
    if (!candidates.length) {
      setActiveDeepResearchBlockId(null)
      setActiveDeepResearchEvidence(null)
      return
    }
    setActiveDeepResearchBlockId((prev) => {
      if (prev && candidates.some((block) => block.block_id === prev)) return prev
      return candidates[0].block_id
    })
  }, [
    activeDeepResearchBlocks,
    activeDeepResearchItem?.deepResearch?.researchId,
    activeDeepResearchItem?.id,
  ])

  useEffect(() => {
    if (!rightPanelVisible || rightPanelMode !== 'deep_research') return
    if (!activeDeepResearchItem?.deepResearch?.researchId || !activeDeepResearchBlockId) return
    void refreshActiveDeepResearchEvidence(activeDeepResearchBlockId)
  }, [
    activeDeepResearchBlockId,
    activeDeepResearchItem?.deepResearch?.researchId,
    refreshActiveDeepResearchEvidence,
    rightPanelMode,
    rightPanelVisible,
  ])

  const restoreDeepResearchCards = useCallback(async () => {
    if (deepResearchRestoreInFlightRef.current) return
    deepResearchRestoreInFlightRef.current = true
    try {
    const existingResearchIds = new Set(
      chat.list
        .map((item) => item.deepResearch?.researchId)
        .filter((value): value is string => Boolean(value)),
    )
    const suppressedResearchIds = loadSuppressedResearchIds()
    const normalizePromptKey = (value: string) =>
      String(value || '')
        .replace(/\s+/g, ' ')
        .trim()
    const existingUserPromptCounts = new Map<string, number>()
    chat.list.forEach((item) => {
      if (item.role !== ChatRole.User) return
      const key = normalizePromptKey(String(item.content || ''))
      if (!key) return
      existingUserPromptCounts.set(key, (existingUserPromptCounts.get(key) ?? 0) + 1)
    })

    const appendDeepResearchEntry = (
      deepResearch: API.DeepResearchCardState,
      userTextRaw?: string,
    ) => {
      const researchId = deepResearch.researchId
      if (researchId) {
        const existsInChat = chat.list.some(
          (item) => item.deepResearch?.researchId === researchId,
        )
        if (existsInChat) {
          existingResearchIds.add(researchId)
          return
        }
      }
      if (researchId && existingResearchIds.has(researchId)) return
      const rawUserText = userTextRaw || deepResearch.userMessage || deepResearch.topic
      const userText = sanitizeDeepResearchTopic(String(rawUserText || '').trim())
      const promptKey = normalizePromptKey(userText)
      let shouldAppendUserPrompt = Boolean(promptKey)
      if (promptKey) {
        const rest = existingUserPromptCounts.get(promptKey) ?? 0
        if (rest > 0) {
          existingUserPromptCounts.set(promptKey, rest - 1)
          shouldAppendUserPrompt = false
        }
      }
      if (shouldAppendUserPrompt) {
        chat.list.push({
          id: createChatId(),
          role: ChatRole.User,
          type: ChatType.Text,
          content: userText,
        })
      }
      const normalizedDeepResearch: API.DeepResearchCardState =
        userText && !deepResearch.userMessage
          ? {
              ...deepResearch,
              userMessage: userText,
            }
          : deepResearch
      const assistantItem: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.Assistant,
        type: ChatType.DeepResearch,
        // DeepResearch 结果卡片会挂在已有会话消息后，不再额外伪造用户消息，避免重复与错位。
        deepResearch: normalizedDeepResearch,
      }
      chat.list.push(assistantItem)
      if (researchId) {
        existingResearchIds.add(researchId)
      }
      if (researchId && (deepResearch.status === 'queued' || deepResearch.status === 'running')) {
        openDeepResearchStream(assistantItem.id, researchId)
      }
      if (researchId && deepResearch.status === 'completed' && !deepResearch.report) {
        void fetchDeepResearchSnapshot(assistantItem.id, researchId)
      }
    }

    const normalizeRunStatus = (status: unknown): API.DeepResearchCardState['status'] => {
      const normalized = String(status || '').trim().toLowerCase()
      if (normalized === 'queued') return 'queued'
      if (normalized === 'running') return 'running'
      if (normalized === 'completed') return 'completed'
      if (normalized === 'failed') return 'failed'
      if (normalized === 'cancelled') return 'cancelled'
      return 'running'
    }

    const toTimestamp = (meta: DeepResearchRunMeta) => {
      const value = dayjs(meta.submitted_at || meta.started_at || meta.finished_at || '').valueOf()
      return Number.isFinite(value) ? value : 0
    }

    const buildRequestFromMeta = (
      meta: DeepResearchRunMeta,
      topic: string,
    ): DeepResearchRequest => {
      const requestRaw =
        meta.request && typeof meta.request === 'object'
          ? (meta.request as Partial<DeepResearchRequest>)
          : {}
      const metadataRaw =
        requestRaw.metadata && typeof requestRaw.metadata === 'object'
          ? (requestRaw.metadata as Record<string, any>)
          : {}
      return {
        ...DEEP_RESEARCH_DEFAULTS,
        ...requestRaw,
        topic,
        session_id: requestRaw.session_id || id,
        metadata: metadataRaw,
      }
    }

    const resolveRunUserPrompt = (meta: DeepResearchRunMeta, fallbackTopic: string) => {
      const requestRaw =
        meta.request && typeof meta.request === 'object'
          ? (meta.request as Record<string, any>)
          : {}
      const metadataRaw =
        requestRaw.metadata && typeof requestRaw.metadata === 'object'
          ? (requestRaw.metadata as Record<string, any>)
          : {}
      const rawUserPrompt = String(
        metadataRaw.deep_research_user_prompt ||
          metadataRaw.user_prompt ||
          requestRaw.topic ||
          fallbackTopic,
      ).trim()
      return sanitizeDeepResearchTopic(rawUserPrompt || fallbackTopic)
    }

    let restoredFromServer = false
    if (id) {
      try {
        const { data } = await api.deepResearch.listDeepResearchRunsBySession(id, 80, {
          loading: false,
          errorToast: false,
        })
        const serverRuns = (data?.items || [])
          .slice()
          .sort((a, b) => toTimestamp(a) - toTimestamp(b))
        const latestRunByPromptKey = new Map<string, DeepResearchRunMeta>()
        serverRuns.forEach((meta) => {
          const fallbackTopic =
            String(meta.topic || '').trim() ||
            String((meta.request as Record<string, any> | undefined)?.topic || '').trim() ||
            '深度研究'
          const promptKey = normalizePromptKey(resolveRunUserPrompt(meta, fallbackTopic))
          if (!promptKey) return
          // 同一会话同一提示词只恢复最新一次运行，避免历史异常 run 造成重复卡片。
          latestRunByPromptKey.set(promptKey, meta)
        })
        const dedupedServerRuns = serverRuns.filter((meta) => {
          const fallbackTopic =
            String(meta.topic || '').trim() ||
            String((meta.request as Record<string, any> | undefined)?.topic || '').trim() ||
            '深度研究'
          const promptKey = normalizePromptKey(resolveRunUserPrompt(meta, fallbackTopic))
          if (!promptKey) return true
          return latestRunByPromptKey.get(promptKey) === meta
        })
        dedupedServerRuns.forEach((meta) => {
          const researchId = String(meta.research_id || '').trim()
          if (!researchId || existingResearchIds.has(researchId)) return
          if (suppressedResearchIds.has(researchId)) return
          const topic =
            String(meta.topic || '').trim() ||
            String((meta.request as Record<string, any> | undefined)?.topic || '').trim() ||
            '深度研究'
          const request = buildRequestFromMeta(meta, topic)
          const metadata =
            request.metadata && typeof request.metadata === 'object'
              ? (request.metadata as Record<string, any>)
              : {}
          const rawUserPrompt = resolveRunUserPrompt(meta, topic)
          const source =
            metadata.trigger === 'suggestion' ? ('suggestion' as const) : ('composer' as const)
          const summary = meta.summary as Record<string, any> | undefined
          const blockStats = normalizeBlockStats(
            summary
              ? {
                  total:
                    typeof summary.blocks_total === 'number' ? Number(summary.blocks_total) : undefined,
                  completed:
                    typeof summary.blocks_completed === 'number'
                      ? Number(summary.blocks_completed)
                      : undefined,
                  pending:
                    typeof summary.blocks_pending === 'number'
                      ? Number(summary.blocks_pending)
                      : undefined,
                  citations:
                    typeof summary.citations_total === 'number'
                      ? Number(summary.citations_total)
                      : undefined,
                }
              : undefined,
          )
          const toolCounts =
            summary && summary.tool_traces_by_type && typeof summary.tool_traces_by_type === 'object'
              ? (summary.tool_traces_by_type as Record<string, number>)
              : undefined
          const deepResearch: API.DeepResearchCardState = {
            status: normalizeRunStatus(meta.status),
            topic,
            request,
            source,
            userMessage: rawUserPrompt,
            researchId,
            progress: [],
            blockStats,
            toolCounts,
            updatedAt: meta.finished_at || meta.started_at || meta.submitted_at,
          }
          appendDeepResearchEntry(deepResearch, deepResearch.userMessage)
          restoredFromServer = true
        })
      } catch (error) {
        console.warn('Failed to restore deep research cards from backend', error)
      }
    }

    if (restoredFromServer) {
      schedulePersistDeepResearchCards()
      return
    }

    if (!researchPersistKey) return
    try {
      const raw = sessionStorage.getItem(researchPersistKey)
      if (!raw) return
      const parsed = JSON.parse(raw) as {
        version?: number
        items?: Array<{ userMessage?: string; deepResearch?: API.DeepResearchCardState }>
      }
      const items = parsed.items ?? []
      const cacheSeenKeys = new Set<string>()
      items.forEach((record) => {
        const deepResearch = record.deepResearch
        if (!deepResearch) return
        const promptKey = normalizePromptKey(
          sanitizeDeepResearchTopic(
            String(record.userMessage || deepResearch.userMessage || deepResearch.topic || ''),
          ),
        )
        const cacheKey = String(deepResearch.researchId || '').trim() || promptKey
        if (cacheKey && cacheSeenKeys.has(cacheKey)) return
        if (cacheKey) cacheSeenKeys.add(cacheKey)
        appendDeepResearchEntry(deepResearch, record.userMessage)
      })
    } catch (error) {
      console.warn('Failed to restore deep research cards from session cache', error)
    }
    } finally {
      deepResearchRestoreInFlightRef.current = false
    }
  }, [
    chat.list,
    deepResearchRestoreInFlightRef,
    fetchDeepResearchSnapshot,
    id,
    loadSuppressedResearchIds,
    openDeepResearchStream,
    researchPersistKey,
    schedulePersistDeepResearchCards,
  ])

  useEffect(() => {
    if (history.loading) {
      researchRestorePendingRef.current = true
    }
  }, [history.loading])

  useEffect(() => {
    if (!history.loading && researchRestorePendingRef.current) {
      researchRestorePendingRef.current = false
      void restoreDeepResearchCards()
    }
  }, [history.loading, restoreDeepResearchCards])

  const buildDeepResearchRequest = useCallback(
    (topic: string, overrides?: Partial<DeepResearchRequest>) => {
      const language = sessionDefaults?.language || 'zh'
      const topK = sessionDefaults?.topK ?? DEEP_RESEARCH_DEFAULTS.top_k
      const overrideMetadata = normalizeDeepResearchMetadata(overrides?.metadata)
      const presetKey = resolveDeepResearchPresetKey(
        overrideMetadata.deep_research_preset,
        deepResearchPreset,
      )
      const metadata = {
        source: 'chat',
        deep_research_preset: presetKey,
        deep_research_preset_force: true,
        ...overrideMetadata,
      }
      const presetParams = DEEP_RESEARCH_PRESET_PARAMS[presetKey]
      return normalizeDeepResearchRequestForExecution({
        ...DEEP_RESEARCH_DEFAULTS,
        ...presetParams,
        topic,
        session_id: id,
        language,
        top_k: topK,
        metadata,
        ...overrides,
      }, presetKey)
    },
    [
      deepResearchPreset,
      id,
      sessionDefaults?.language,
      sessionDefaults?.topK,
    ],
  )

  const requestDeepResearchPlan = useCallback(
    async (itemId: number, request: DeepResearchRequest) => {
      updateDeepResearchItem(itemId, (state) => {
        state.planLoading = true
        state.planError = undefined
        state.status = 'plan'
      })
      try {
        let streamedPlan: DeepResearchPlan | null = null
        await streamDeepResearchPlanPreview(request, {
          onProgress: (event) => {
            const messageText = String(event.message || '').trim()
            if (!messageText) return
            appendDeepResearchProgressEvent(itemId, {
              stage: String(event.stage || 'planning').trim() || 'planning',
              event_type: String(event.event_type || 'plan.progress').trim() || 'plan.progress',
              message: messageText,
              payload:
                event.payload && typeof event.payload === 'object'
                  ? (event.payload as Record<string, any>)
                  : {},
              timestamp: event.timestamp,
              research_id: event.research_id,
            })
          },
          onPlan: (plan) => {
            streamedPlan = plan
          },
        })
        if (!streamedPlan) {
          const { data } = await api.deepResearch.previewDeepResearchPlan(request, {
            loading: false,
            errorToast: false,
          })
          streamedPlan = data
        }
        updateDeepResearchItem(itemId, (state) => {
          state.plan = streamedPlan || undefined
          state.planLoading = false
          state.status = 'plan'
          state.statusMessage = `计划已生成，共 ${streamedPlan?.items?.length || 0} 个任务项`
        })
      } catch (streamError) {
        const streamErrorText = resolveErrorMessage(streamError, '流式计划失败')
        console.warn('DeepResearch plan stream failed, fallback to REST preview:', streamErrorText)
        appendDeepResearchProgressEvent(itemId, {
          stage: 'planning',
          event_type: 'plan.progress',
          message: '计划流式预览异常，正在回退标准预览...',
          payload: { error: streamErrorText },
        })
        try {
          const { data } = await api.deepResearch.previewDeepResearchPlan(request, {
            loading: false,
            errorToast: false,
          })
          updateDeepResearchItem(itemId, (state) => {
            state.plan = data
            state.planLoading = false
            state.status = 'plan'
            state.statusMessage = `计划已生成，共 ${data.items?.length || 0} 个任务项`
          })
          appendDeepResearchProgressEvent(itemId, {
            stage: 'planning',
            event_type: 'plan.completed',
            message: '计划生成完成',
            payload: {
              items: data.items?.length || 0,
              fallback: 'rest_preview',
            },
          })
        } catch (error) {
          const errorText = resolveErrorMessage(error, '计划生成失败')
          updateDeepResearchItem(itemId, (state) => {
            state.planLoading = false
            state.planError = errorText
            state.statusMessage = errorText
          })
          appendDeepResearchProgressEvent(itemId, {
            stage: 'planning',
            event_type: 'plan.failed',
            message: errorText,
            payload: {
              cause: streamErrorText,
            },
          })
        }
      }
    },
    [appendDeepResearchProgressEvent, resolveErrorMessage, updateDeepResearchItem],
  )

  const sendDeepResearch = useCallback(
    async (
      topic: string,
      options?: {
        source?: 'composer' | 'suggestion'
        userLabel?: string
        bootstrap?: ComposerBootstrapPayload
        replaceFromIndex?: number
        replaceFromItemId?: number
        preset?: DeepResearchPresetKey
      },
    ) => {
      if (!id) {
        message.warning('缺少会话信息，无法发起深度研究')
        return
      }
      const topicForResearch = sanitizeDeepResearchTopic(topic)
      if (!topicForResearch.trim()) return
      const source = options?.source ?? 'composer'
      const presetKey = resolveDeepResearchPresetKey(
        options?.preset,
        deepResearchPresetRef.current,
      )
      const userLabel = sanitizeDeepResearchTopic(options?.userLabel || topicForResearch)
      const bootstrap = options?.bootstrap
      const usingRag =
        typeof bootstrap?.useRag === 'boolean' ? bootstrap.useRag : draftRagEnabled
      const attachmentsSnapshot = Array.isArray(bootstrap?.pendingAttachments)
        ? bootstrap!.pendingAttachments!.map((item) => ({ ...item }))
        : pendingAttachments.map((item) => ({ ...item }))
      const filesSnapshot = Array.isArray(bootstrap?.pendingFiles)
        ? [...bootstrap!.pendingFiles!]
        : [...pendingFiles]
      const ok = await uploadPendingFiles(filesSnapshot, true)
      if (!ok) return

      let insertionIndex = -1
      if (typeof options?.replaceFromItemId === 'number') {
        insertionIndex = chat.list.findIndex((item) => item.id === options.replaceFromItemId)
      }
      if (
        insertionIndex < 0 &&
        typeof options?.replaceFromIndex === 'number' &&
        options.replaceFromIndex >= 0 &&
        options.replaceFromIndex < chat.list.length
      ) {
        insertionIndex = options.replaceFromIndex
      }
      if (insertionIndex >= 0) {
        const rewindBeforeMessageId = resolveBranchReplaceMessageId(insertionIndex)
        const rewindKeepMessages = resolveKeepMessagesBeforeIndex(insertionIndex)
        cancelActiveAskRunSilently()
        if (rewindBeforeMessageId || rewindKeepMessages >= 0) {
          try {
            await api.session.rewind(
              {
                sessionId: id,
                beforeMessageId: rewindBeforeMessageId || undefined,
                keepMessages: rewindBeforeMessageId ? undefined : rewindKeepMessages,
              },
              {
                loading: false,
              },
            )
          } catch (error: any) {
            const detail =
              error?.response?.data?.detail ||
              error?.response?.data?.message ||
              error?.message ||
              '历史分支回卷失败，请重试'
            message.error(detail)
            return
          }
        }
        const removedItems = chat.list.slice(insertionIndex)
        const removedResearchIds = removedItems
          .map((item) => item.deepResearch?.researchId)
          .filter((value): value is string => Boolean(value))
        suppressResearchIds(removedResearchIds)
        chat.list.splice(insertionIndex)
        openCitationsPanel(null)
        setDocuments([])
      }

      const userMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.User,
        type: ChatType.Text,
        content: userLabel,
        attachments: attachmentsSnapshot.length ? attachmentsSnapshot : undefined,
      }
      const request = buildDeepResearchRequest(topicForResearch, {
        index_mode: usingRag ? DEEP_RESEARCH_DEFAULTS.index_mode : 'disabled',
        metadata: {
          trigger: source,
          deep_research_user_prompt: userLabel,
          deep_research_preset: presetKey,
          deep_research_preset_force: true,
          deep_research_use_rag: usingRag,
        },
      })
      const assistantMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.Assistant,
        type: ChatType.DeepResearch,
        deepResearch: {
          status: 'plan',
          topic: topicForResearch,
          request,
          source,
          userMessage: userLabel,
          planLoading: true,
        },
      }
      if (insertionIndex >= 0) {
        chat.list.splice(insertionIndex, 0, userMessage, assistantMessage)
      } else {
        chat.list.push(userMessage)
        chat.list.push(assistantMessage)
      }
      openDeepResearchProcessPanel(assistantMessage, { openPanel: true })
      sessionActions.updateKey()
      scrollToBottom()
      schedulePersistDeepResearchCards()
      await requestDeepResearchPlan(assistantMessage.id, request)
      setPendingAttachments([])
      setPendingFiles([])
      setChatImageAttachments([])
      setComposerValue('')
      setEditingContext(null)
      setLocalReplaceContext(null)
    },
    [
      id,
      pendingAttachments,
      pendingFiles,
      uploadPendingFiles,
      buildDeepResearchRequest,
      chat.list,
      cancelActiveAskRunSilently,
      openCitationsPanel,
      openDeepResearchProcessPanel,
      requestDeepResearchPlan,
      schedulePersistDeepResearchCards,
      suppressResearchIds,
      resolveBranchReplaceMessageId,
      resolveKeepMessagesBeforeIndex,
      draftRagEnabled,
      sessionDefaults,
    ],
  )

  const handleComposerSend = useCallback(
    async (text: string) => {
      const normalizedText = String(text || '').trim()
      if (!normalizedText && chatImageAttachments.length === 0) return
      if (!normalizedText && chatImageAttachments.length > 0) {
        message.warning('请先输入问题，再附带图片发送')
        return
      }
      const deepResearchPromptLeak = looksLikeDeepResearchInternalPrompt(normalizedText)
      const deepResearchLeakTopic = sanitizeDeepResearchTopic(normalizedText)
      const shouldDeepResearch = researchMode === 'deep' && !editingContext
      let imageTransfer = chatImageAttachments.map((item) => ({ ...item }))
      if (shouldDeepResearch && imageTransfer.length > 0) {
        message.warning('深度研究工具暂不支持图片输入，已忽略图片')
        imageTransfer = []
        setChatImageAttachments([])
      }
      if (deepResearchPromptLeak && !editingContext) {
        setResearchSuggestion(null)
        await sendDeepResearch(deepResearchLeakTopic || normalizedText, {
          source: 'composer',
          replaceFromIndex: localReplaceContext?.index,
          replaceFromItemId: localReplaceContext?.itemId,
        })
        setLocalReplaceContext(null)
        message.info('检测到研究规划提示词，已自动转为深度研究任务')
        return
      }
      if (!id) {
        try {
          const selectedKbId = draftRagEnabled
            ? resolvePreferredKnowledgeBaseId(knowledgeBases, [draftUserKnowledgeBaseId])
            : null
          if (draftRagEnabled && selectedKbId == null) {
            message.warning('请先在知识库页面创建可用知识库，再开启 RAG')
            return
          }
          const { data } = await api.session.create({
            surface: 'deep_chat',
            defaults: {
              llmProvider: resolveProviderByModel(llmModel),
              llmModel,
              useSessionKnowledgeBase: draftRagEnabled,
              useUserKnowledgeBase: draftRagEnabled,
              userKnowledgeBaseId: draftRagEnabled ? selectedKbId : null,
              retrievalStrategy:
                draftRagEnabled && draftRagMode === 'deep'
                  ? 'multimodal_graph'
                  : 'multi_stage',
            },
          })
          const createdSessionId = String(data?.sessionId || '').trim()
          if (!createdSessionId) {
            message.error('创建会话失败，请重试')
            return
          }
          setPageTransport(transportToChatEnter, {
            data: {
              message: normalizedText,
              mode: shouldDeepResearch ? 'deep' : 'chat',
              deepResearchPreset: shouldDeepResearch ? deepResearchPresetRef.current : undefined,
              useRag: draftRagEnabled,
              ragMode: draftRagMode,
              userKnowledgeBaseId: draftRagEnabled ? selectedKbId : null,
              pendingAttachments: pendingAttachments.map((item) => ({ ...item })),
              pendingFiles: [...pendingFiles],
              imageAttachments: imageTransfer,
            },
          })
          navigate(`/chat/${createdSessionId}`, { replace: true })
        } catch (error: any) {
          const detail =
            error?.response?.data?.detail ||
            error?.response?.data?.message ||
            error?.message ||
            '创建会话失败'
          message.error(detail)
        }
        return
      }

      if (shouldDeepResearch) {
        setResearchSuggestion(null)
        await sendDeepResearch(normalizedText, {
          source: 'composer',
          replaceFromIndex: localReplaceContext?.index,
          replaceFromItemId: localReplaceContext?.itemId,
        })
        setLocalReplaceContext(null)
        return
      }
      await send(normalizedText, {
        localReplaceIndex: localReplaceContext?.index,
        localReplaceItemId: localReplaceContext?.itemId,
      })
      setLocalReplaceContext(null)
      const reason = evaluateDeepResearchSuggestion(normalizedText)
      if (
        reason &&
        !suggestionDismissedRef.current.has(normalizedText) &&
        lastSuggestionTopicRef.current !== normalizedText
      ) {
        setResearchSuggestion({ topic: normalizedText, reason })
        lastSuggestionTopicRef.current = normalizedText
      }
    },
    [
      chatImageAttachments,
      draftRagEnabled,
      draftRagMode,
      draftUserKnowledgeBaseId,
      editingContext,
      evaluateDeepResearchSuggestion,
      id,
      knowledgeBases,
      llmModel,
      navigate,
      pendingAttachments,
      pendingFiles,
      localReplaceContext,
      deepResearchPreset,
      researchMode,
      send,
      sendDeepResearch,
    ],
  )

  const handleAcceptSuggestion = useCallback(async () => {
    if (!researchSuggestion) return
    setResearchSuggestion(null)
    await sendDeepResearch(researchSuggestion.topic, { source: 'suggestion' })
  }, [researchSuggestion, sendDeepResearch])

  const handleDismissSuggestion = useCallback(() => {
    if (researchSuggestion?.topic) {
      suggestionDismissedRef.current.add(researchSuggestion.topic)
    }
    setResearchSuggestion(null)
  }, [researchSuggestion])

  const handleDeepResearchConfirm = useCallback(
    async (item: API.ChatItem) => {
      const latestItem = chat.list.find((entry) => entry.id === item.id) || item
      if (!latestItem.deepResearch?.request) return
      if (latestItem.deepResearch.status !== 'plan') return
      if (deepResearchSubmitLockRef.current.has(item.id)) return
      deepResearchSubmitLockRef.current.add(item.id)
      const executionRequest = normalizeDeepResearchRequestForExecution(latestItem.deepResearch.request)
      updateDeepResearchItem(item.id, (state) => {
        state.request = executionRequest
      })
      openDeepResearchProcessPanel(latestItem, { openPanel: true })
      setResearchMode('chat')
      updateDeepResearchItem(item.id, (state) => {
        state.status = 'queued'
        state.statusMessage = '已提交任务'
      })
      try {
        const { data } = await api.deepResearch.submitDeepResearch(
          executionRequest,
          { loading: false },
        )
        updateDeepResearchItem(item.id, (state) => {
          state.researchId = data.research_id
          state.queuePosition = data.queue_position ?? null
          state.activeRuns = data.active_runs ?? null
          state.pendingRuns = data.pending_runs ?? null
          state.status = data.status === 'running' ? 'running' : 'queued'
          state.statusMessage = data.message || '任务已进入队列'
        })
        if (data.research_id) {
          openDeepResearchStream(item.id, data.research_id)
        }
        const target = chat.list.find((entry) => entry.id === item.id) || item
        openDeepResearchProcessPanel(target, { openPanel: true })
      } catch (error) {
        updateDeepResearchItem(item.id, (state) => {
          state.status = 'failed'
          state.statusMessage = resolveErrorMessage(error, '提交失败')
        })
      } finally {
        deepResearchSubmitLockRef.current.delete(item.id)
      }
    },
    [
      chat.list,
      openDeepResearchProcessPanel,
      openDeepResearchStream,
      resolveErrorMessage,
      setResearchMode,
      updateDeepResearchItem,
    ],
  )

  const handleDeepResearchCancel = useCallback(
    async (item: API.ChatItem) => {
      const researchId = item.deepResearch?.researchId
      if (researchId) {
        try {
          await api.deepResearch.cancelDeepResearch(researchId, {
            loading: false,
            errorToast: false,
          })
        } catch (error) {
          message.error(resolveErrorMessage(error, '取消失败'))
        }
      }
      updateDeepResearchItem(item.id, (state) => {
        state.status = 'cancelled'
        state.statusMessage = '任务已取消'
      })
      closeDeepResearchStream(item.id)
    },
    [closeDeepResearchStream, resolveErrorMessage, updateDeepResearchItem],
  )

  const handleDeepResearchRetryPlan = useCallback(
    async (item: API.ChatItem) => {
      const request = item.deepResearch?.request
      if (!request) return
      await requestDeepResearchPlan(item.id, normalizeDeepResearchRequestForExecution(request))
    },
    [requestDeepResearchPlan],
  )

  const handleDeepResearchEdit = useCallback(
    (item: API.ChatItem) => {
      if (!item.deepResearch?.request) return
      const request = item.deepResearch.request
      const planItems =
        item.deepResearch.plan?.items ||
        item.deepResearch.snapshotOutline?.items ||
        (normalizeDeepResearchMetadata(request.metadata).plan_override_items as PlanItem[] | undefined) ||
        []
      const fallbackTopic = String(request.topic || item.deepResearch.topic || '研究主题').trim()
      const fallbackPlanText = `1 | ${fallbackTopic} 的核心问题 | ${fallbackTopic} 的核心问题 |`
      researchPlanForm.setFieldsValue({
        topic: request.topic,
        plan_text: serializePlanItemsForEditor(planItems) || fallbackPlanText,
      })
      setEditingResearchItem(item)
    },
    [researchPlanForm],
  )

  const handleResearchEditSave = useCallback(async () => {
    if (!editingResearchItem?.deepResearch?.request) return
    let values: DeepResearchPlanEditFormValues
    try {
      values = await researchPlanForm.validateFields()
    } catch {
      return
    }
    const topic = String(values.topic || '').trim()
    if (!topic) {
      message.warning('请先输入研究主题')
      return
    }
    const parsed = parsePlanItemsFromEditorText(values.plan_text)
    if (parsed.error) {
      message.warning(parsed.error)
      return
    }
    const oldRequest = editingResearchItem.deepResearch.request
    const oldMetadata = normalizeDeepResearchMetadata(oldRequest.metadata)
    const presetKey = resolveDeepResearchPresetKey(oldMetadata.deep_research_preset, deepResearchPreset)
    const preset = DEEP_RESEARCH_PRESET_PARAMS[presetKey] || DEEP_RESEARCH_PRESET_PARAMS.medium
    const nextMetadata = {
      ...oldMetadata,
      deep_research_preset: presetKey,
      deep_research_preset_force: true,
      plan_override_items: parsed.items,
      plan_override_source: 'chat_plan_editor',
    }
    const nextRequest = normalizeDeepResearchRequestForExecution({
      ...oldRequest,
      ...preset,
      topic,
      metadata: nextMetadata,
    })
    updateDeepResearchItem(editingResearchItem.id, (state) => {
      state.request = nextRequest
      state.topic = nextRequest.topic
      state.plan = { items: parsed.items }
      state.planError = undefined
      state.status = 'plan'
      state.statusMessage = `计划已更新，共 ${parsed.items.length} 个任务项`
    })
    setEditingResearchItem(null)
    await requestDeepResearchPlan(editingResearchItem.id, nextRequest)
    message.success('计划已更新')
  }, [
    deepResearchPreset,
    editingResearchItem,
    requestDeepResearchPlan,
    researchPlanForm,
    updateDeepResearchItem,
  ])

  const handleDeepResearchOpenWorkspace = useCallback((item: API.ChatItem) => {
    const request = item.deepResearch?.request
    const researchId = item.deepResearch?.researchId
    const url = new URL(
      `${import.meta.env.BASE_URL || '/'}deep-research`,
      window.location.origin,
    )
    if (request?.topic) url.searchParams.set('topic', request.topic)
    if (request?.session_id) url.searchParams.set('sessionId', request.session_id)
    if (researchId) url.searchParams.set('researchId', researchId)
    window.open(url.toString(), '_blank', 'noopener')
  }, [])

  const handleDeepResearchExport = useCallback(
    async (item: API.ChatItem, format: 'pdf' | 'markdown') => {
      const researchId = item.deepResearch?.researchId
      if (!researchId) {
        message.warning('暂无可导出的研究结果')
        return
      }
      try {
        const exportFormat = format === 'pdf' ? 'pdf' : 'markdown'
        const response = await api.deepResearch.exportDeepResearchReport(researchId, exportFormat, {
          loading: false,
          errorToast: false,
        })
        const blob =
          response.data instanceof Blob
            ? response.data
            : new Blob([response.data], {
                type:
                  exportFormat === 'pdf'
                    ? 'application/pdf'
                    : 'text/markdown;charset=utf-8',
              })
        const fallbackName = `${researchId}.${exportFormat === 'markdown' ? 'md' : exportFormat}`
        const filename = resolveDownloadFilename(
          String(response.headers?.['content-disposition'] || ''),
          fallbackName,
        )
        const url = URL.createObjectURL(blob)
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = filename
        document.body.appendChild(anchor)
        anchor.click()
        document.body.removeChild(anchor)
        URL.revokeObjectURL(url)
        message.success(`已开始下载 ${filename}`)
      } catch (error: any) {
        const detail =
          String(error?.response?.data?.detail || error?.message || '').trim() || '导出失败'
        message.error(detail)
      }
    },
    [],
  )

  const handleDeepResearchCopy = useCallback((item: API.ChatItem) => {
    const content = item.deepResearch?.report?.report_markdown || ''
    if (!content) {
      message.warning('暂无可复制的报告内容')
      return
    }
    const tryClipboard = async () => {
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(content)
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
        textarea.value = content
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
    tryClipboard()
      .then((ok) => ok || fallbackCopy())
      .then((ok) => {
        if (ok) {
          message.success('报告内容已复制')
        } else {
          message.error('复制失败，请手动选择文本')
        }
      })
  }, [])

  const handleDeepResearchSaveToNotebook = useCallback(async (item: API.ChatItem) => {
    const data = item.deepResearch
    const reportMarkdown = data?.report?.report_markdown || ''
    if (!reportMarkdown) {
      message.warning('暂无可保存的研究报告')
      return
    }
    const topic = data?.topic || data?.request?.topic || '深度研究报告'
    try {
      const savedPath = await createNotebookNoteFile(reportMarkdown, topic)
      message.success('报告已导入笔记本，正在打开文档...')
      navigate(
        `/doc-studio/${NOTEBOOK_WORKSPACE_ID}?file=${encodeURIComponent(savedPath)}&auto_compile=1`,
      )
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `保存失败：${detail}` : '保存笔记失败')
    }
  }, [navigate])

  const handleDeepResearchInsertSummary = useCallback(
    (_item: API.ChatItem, summary: string) => {
      if (!summary) {
        message.warning('摘要为空，无法回填')
        return
      }
      chat.list.push({
        id: createChatId(),
        role: ChatRole.Assistant,
        type: ChatType.Document,
        content: summary,
      })
      scrollToBottom()
    },
    [chat.list],
  )

  const handleAssistantFeedback = useCallback(
    (item: API.ChatItem, rating: 'thumbs_up' | 'thumbs_down') => {
      const messageId = String(item.message_id || '').trim()
      if (!messageId) {
        message.warning('当前回答暂不支持反馈')
        return
      }
      setFeedbackByMessageId((prev) => {
        const current = prev[messageId]
        const next = current === rating ? undefined : rating
        return { ...prev, [messageId]: next }
      })
      message.success('反馈已记录')
    },
    [],
  )

  const handleRetryUserMessage = useCallback(
    (item: API.ChatItem, index: number) => {
      const text = String(item.content || '').trim()
      if (!text) return
      const nextItem = chat.list[index + 1]
      const isDeepResearchPrompt =
        nextItem?.role === ChatRole.Assistant && nextItem?.type === ChatType.DeepResearch
      if (isDeepResearchPrompt) {
        setComposerValue(text)
        setComposerFocusKey((key) => key + 1)
        setEditingContext(null)
        setResearchSuggestion(null)
        setLocalReplaceContext({ index, itemId: item.id })
        return
      }
      if (!item.message_id) {
        message.warning('消息尚未保存，暂无法编辑')
        return
      }
      setComposerValue(text)
      setComposerFocusKey((key) => key + 1)
      setEditingContext({ messageId: item.message_id })
      setLocalReplaceContext(null)
    },
    [chat.list],
  )

  const handleResendUserMessage = useCallback(
    async (item: API.ChatItem, index: number) => {
      const text = String(item.content || '').trim()
      if (!text) return
      const resendAsDeepResearch = researchMode === 'deep'
      if (resendAsDeepResearch) {
        setResearchSuggestion(null)
        await sendDeepResearch(text, {
          source: 'composer',
          replaceFromIndex: index,
          replaceFromItemId: item.id,
        })
        setComposerValue('')
        setLocalReplaceContext(null)
        return
      }
      await send(text, {
        replaceMessageId: item.message_id,
        localReplaceIndex: index,
        localReplaceItemId: item.id,
      })
      setComposerValue('')
      setLocalReplaceContext(null)
    },
    [researchMode, send, sendDeepResearch],
  )
  useMount(async () => {
    if (!id) {
      return
    }
    if (ctx?.data.message) {
      const bootstrap: ComposerBootstrapPayload = {
        useRag:
          typeof ctx.data.useRag === 'boolean'
            ? ctx.data.useRag
            : undefined,
        pendingAttachments: Array.isArray(ctx.data.pendingAttachments)
          ? ctx.data.pendingAttachments.map((item) => ({ ...item }))
          : undefined,
        pendingFiles: Array.isArray(ctx.data.pendingFiles)
          ? [...ctx.data.pendingFiles]
          : undefined,
        imageAttachments: Array.isArray(ctx.data.imageAttachments)
          ? ctx.data.imageAttachments.map((item) => ({ ...item }))
          : undefined,
      }
      if (ctx.data.mode === 'deep') {
        const bootstrapPreset = resolveDeepResearchPresetKey(
          ctx.data.deepResearchPreset,
          deepResearchPreset,
        )
        deepResearchPresetRef.current = bootstrapPreset
        setDeepResearchPreset(bootstrapPreset)
        await sendDeepResearch(ctx.data.message, {
          source: 'composer',
          bootstrap,
          preset: bootstrapPreset,
        })
      } else {
        await send(ctx.data.message, { bootstrap })
      }
      return
    }
    history.run()
  })

  const title = useMemo(() => {
    const stored = String(sessionDisplayTitle || '').trim()
    if (stored && !isPlaceholderSessionTitle(stored)) {
      return stored
    }
    return (list[0]?.content ?? stored) || '新对话'
  }, [sessionDisplayTitle, list])

  const activeLlmModel = useMemo(
    () => normalizeLlmModel(sessionDefaults?.llmModel || llmModel, sessionDefaults?.llmProvider),
    [sessionDefaults?.llmModel, sessionDefaults?.llmProvider, llmModel],
  )
  const activeLlmProvider = useMemo(
    () => resolveProviderByModel(activeLlmModel),
    [activeLlmModel],
  )

  const sessionUsageTotals = useMemo(() => {
    let prompt = 0
    let completion = 0
    list.forEach((item) => {
      if (item.role !== ChatRole.Assistant || !item.usage) return
      prompt += item.usage.prompt_tokens || 0
      completion += item.usage.completion_tokens || 0
    })
    return {
      prompt_tokens: prompt,
      completion_tokens: completion,
      total_tokens: prompt + completion,
    }
  }, [list])

  const contextTokenEstimate = useMemo(() => {
    const merged = list
      .map((item) => String(item.content || item.think || '').trim())
      .filter(Boolean)
      .join('\n')
    return estimateTokenCount(merged)
  }, [list])

  const modelContextWindowHint = useMemo(
    () => MODEL_CONTEXT_WINDOW_HINTS[activeLlmModel] || 0,
    [activeLlmModel],
  )

  const contextUsagePercent = useMemo(() => {
    if (!modelContextWindowHint) return null
    return Math.min(100, (contextTokenEstimate / modelContextWindowHint) * 100)
  }, [contextTokenEstimate, modelContextWindowHint])

  const autoVisionHint = useMemo(
    () => chatImageAttachments.length > 0 && !isVisionModel(activeLlmModel),
    [chatImageAttachments.length, activeLlmModel],
  )

  const [read, setRead] = useState<API.Reference | null>(null)
  const effectiveUsingUserKb = sessionDefaults
    ? sessionDefaults.useSessionKnowledgeBase || sessionDefaults.useUserKnowledgeBase
    : draftRagEnabled
  const effectiveUserKnowledgeBaseId = sessionDefaults
    ? sessionDefaults.userKnowledgeBaseId ?? null
    : draftUserKnowledgeBaseId
  const kbOptions = useMemo(
    () => {
    const options: Array<{ value: number; label: string; disabled?: boolean }> = knowledgeBases.map((item) => ({
      value: item.id,
      label: item.name,
    }))
    if (
      effectiveUserKnowledgeBaseId &&
      !knowledgeBases.some(
        (item) => item.id === effectiveUserKnowledgeBaseId,
      )
    ) {
      options.push({
        value: effectiveUserKnowledgeBaseId,
        label: `ID ${effectiveUserKnowledgeBaseId}（不可用）`,
        disabled: true,
      })
    }
    return options
    },
    [knowledgeBases, effectiveUserKnowledgeBaseId],
  )
  const selectedKbLabel = useMemo(() => {
    const currentId = effectiveUserKnowledgeBaseId
    if (currentId == null) return '知识库'
    return kbOptions.find((item) => item.value === currentId)?.label || '知识库'
  }, [kbOptions, effectiveUserKnowledgeBaseId])
  const kbSelectWidth = useMemo(
    () => calcCompactSelectWidth(selectedKbLabel, 66, 168),
    [selectedKbLabel],
  )
  const deepResearchPresetSelectWidth = useMemo(
    () =>
      calcCompactSelectWidth(
        DEEP_RESEARCH_PRESET_LABELS[deepResearchPreset] || DEEP_RESEARCH_PRESET_LABELS.medium,
        66,
        122,
      ),
    [deepResearchPreset],
  )
  const knowledgeControl = useMemo(() => {
    if (sessionDefaults) {
      return {
        usingUser: effectiveUsingUserKb,
        selectValue: sessionDefaults.userKnowledgeBaseId ?? undefined,
        options: kbOptions,
        showSelect: effectiveUsingUserKb,
        selectWidth: kbSelectWidth,
        loadingUser: updatingDefaults || kbReq.loading,
        disableUserToggle: kbReq.loading && !effectiveUsingUserKb,
        disableSelect: updatingDefaults,
        onToggleUser: handleToggleUserKb,
        onSelectUserKb: handleSelectUserKb,
      }
    }
    return {
      usingUser: draftRagEnabled,
      selectValue: draftUserKnowledgeBaseId ?? undefined,
      options: kbOptions,
      showSelect: draftRagEnabled,
      selectWidth: kbSelectWidth,
      loadingUser: kbReq.loading,
      disableUserToggle: kbReq.loading,
      disableSelect: kbReq.loading,
      onToggleUser: handleDraftToggleUserKb,
      onSelectUserKb: handleDraftSelectUserKb,
    }
  }, [
    sessionDefaults,
    effectiveUsingUserKb,
    kbOptions,
    kbSelectWidth,
    draftRagEnabled,
    draftUserKnowledgeBaseId,
    updatingDefaults,
    kbReq.loading,
    handleToggleUserKb,
    handleSelectUserKb,
    handleDraftToggleUserKb,
    handleDraftSelectUserKb,
  ])
  const ragModeControl = useMemo(() => {
    if (sessionDefaults) {
      const ragEnabled =
        sessionDefaults.useSessionKnowledgeBase || sessionDefaults.useUserKnowledgeBase
      if (!ragEnabled) return undefined
      const strategy = sessionDefaults.retrievalStrategy
      const value: 'fast' | 'deep' =
        strategy === 'graph' || strategy === 'multimodal_graph' ? 'deep' : 'fast'
      return {
        value,
        loading: updatingDefaults || defaultsLoading,
        disabled: updatingDefaults,
        width: calcCompactSelectWidth(value === 'deep' ? '深度' : '快速', 62, 94),
        onChange: handleRagModeChange,
      }
    }
    if (!draftRagEnabled) return undefined
    return {
      value: draftRagMode,
      loading: false,
      disabled: false,
      width: calcCompactSelectWidth(draftRagMode === 'deep' ? '深度' : '快速', 62, 94),
      onChange: handleDraftRagModeChange,
    }
  }, [
    sessionDefaults,
    updatingDefaults,
    defaultsLoading,
    handleRagModeChange,
    draftRagEnabled,
    draftRagMode,
    handleDraftRagModeChange,
  ])

  const researchModeControl = useMemo(
    () => ({
      enabled: researchMode === 'deep',
      disabled: !!editingContext,
      onToggle: (enabled: boolean) => setResearchMode(enabled ? 'deep' : 'chat'),
      preset: deepResearchPreset,
      presetDisabled: !!editingContext,
      presetWidth: deepResearchPresetSelectWidth,
      onPresetChange: handleDeepResearchPresetChange,
    }),
    [
      deepResearchPreset,
      deepResearchPresetSelectWidth,
      editingContext,
      researchMode,
      handleDeepResearchPresetChange,
    ],
  )

  const modelControl = useMemo(
    () => {
      const options = LLM_MODEL_OPTIONS.map((item) => ({
        label: item.label,
        value: item.value,
      }))
      const currentLabel = resolveModelLabel(activeLlmModel)
      return {
        value: activeLlmModel,
        options,
        width: calcCompactSelectWidth(currentLabel, 120, 220),
        disabled: updatingDefaults || defaultsLoading,
        onChange: handleLlmModelChange,
      }
    },
    [activeLlmModel, updatingDefaults, defaultsLoading, handleLlmModelChange],
  )

  const systemStatusControl = useMemo(
    () => ({
      title: '系统状态',
      onClick: () => setSystemStatusOpen(true),
      disabled: !id,
    }),
    [id],
  )

  const deepResearchUnreadTotal = useMemo(
    () =>
      Object.values(deepResearchUnreadByItemId).reduce(
        (sum, count) => sum + (Number.isFinite(count) ? count : 0),
        0,
      ),
    [deepResearchUnreadByItemId],
  )

  const handleOpenRightPanel = useCallback(() => {
    if (deepResearchUnreadTotal > 0) {
      const targetItemId = Object.entries(deepResearchUnreadByItemId)
        .filter(([, count]) => Number(count) > 0)
        .sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0]
      if (targetItemId) {
        const matched = chat.list.find((entry) => entry.id === Number(targetItemId))
        if (matched?.deepResearch) {
          openDeepResearchProcessPanel(matched, { openPanel: true })
          return
        }
      }
    }
    setRightPanelVisible(true)
  }, [
    chat.list,
    deepResearchUnreadByItemId,
    deepResearchUnreadTotal,
    openDeepResearchProcessPanel,
  ])

  return (
    <ComPageLayout
      className={pageLayoutClassName}
      style={pageLayoutStyle}
      sender={
        <>
          {researchSuggestion ? (
            <div className={styles['chat-page__research-suggestion']}>
              <div className={styles['chat-page__research-suggestion-text']}>
                <strong>建议升级为深度研究：</strong>
                {researchSuggestion.reason}
              </div>
              <Space size={8}>
                <Button type="primary" size="small" onClick={handleAcceptSuggestion}>
                  一键升级
                </Button>
                <Button size="small" onClick={handleDismissSuggestion}>
                  忽略
                </Button>
              </Space>
            </div>
          ) : null}
          <ComSender
            loading={loading}
            sessionId={id}
            onSend={handleComposerSend}
            onAbort={abortChat}
            onContract={() => openCitationsPanel(null)}
            knowledgeControl={knowledgeControl}
            ragModeControl={ragModeControl}
            researchModeControl={researchModeControl}
            modelControl={modelControl}
            systemStatusControl={systemStatusControl}
            pendingAttachments={pendingAttachments}
            onRemovePendingAttachment={handleRemovePendingAttachment}
            onFileSelected={handleFileSelected}
            imageAttachments={chatImageAttachments}
            onImageFilesSelected={appendChatImageFiles}
            onRemoveImageAttachment={handleRemoveChatImageAttachment}
            value={composerValue}
            onValueChange={setComposerValue}
            focusKey={composerFocusKey}
          />
        </>
      }
      right={
        rightPanelVisible ? (
          <div className={styles['chat-page__right-panel-shell']}>
            <div
              className={`${styles['chat-page__right-panel-resizer']} ${isDraggingRightPanel ? styles['chat-page__right-panel-resizer--dragging'] : ''}`}
              onMouseDown={handleRightPanelResizeStart}
              role="separator"
              aria-label="调整右侧面板宽度"
              aria-orientation="vertical"
            />
            {rightPanelMode === 'deep_research' ? (
              <ChatDrawer title="研究过程" onClose={() => setRightPanelVisible(false)}>
                <DeepResearchProcessPanel
                  item={activeDeepResearchItem}
                  blocks={activeDeepResearchBlocks}
                  selectedBlockId={activeDeepResearchBlockId}
                  evidence={activeDeepResearchEvidence}
                  evidenceLoading={activeDeepResearchEvidenceLoading}
                  onSelectBlock={(blockId) => setActiveDeepResearchBlockId(blockId)}
                  onRefreshSnapshot={() => {
                    void refreshActiveDeepResearchSnapshot()
                  }}
                  onRefreshEvidence={(blockId) => {
                    void refreshActiveDeepResearchEvidence(blockId)
                  }}
                  onOpenWorkspace={handleDeepResearchOpenWorkspace}
                  onExportReport={(item, format) => {
                    void handleDeepResearchExport(item, format)
                  }}
                />
              </ChatDrawer>
            ) : currentChatItem && currentChatItem.reference?.length ? (
              <ChatDrawer title="引文" onClose={() => setRightPanelVisible(false)}>
                <Citations list={currentChatItem.reference} />
              </ChatDrawer>
            ) : (
              <ChatDrawer title="文档" onClose={() => setRightPanelVisible(false)}>
                <Contracts list={documents} />
              </ChatDrawer>
            )}
          </div>
        ) : null
      }
    >
      <div className={styles['chat-page']}>
        {!rightPanelVisible ? (
          <button
            type="button"
            className={styles['chat-page__right-panel-trigger']}
            onClick={handleOpenRightPanel}
            aria-label="展开右侧面板"
          >
            <MenuUnfoldOutlined className={styles['chat-page__right-panel-trigger-icon']} />
            {deepResearchUnreadTotal > 0 ? (
              <span className={styles['chat-page__right-panel-trigger-badge']}>
                {deepResearchUnreadTotal > 99 ? '99+' : deepResearchUnreadTotal}
              </span>
            ) : null}
          </button>
        ) : null}
        <div className={styles['chat-page__header']}>
          <div className={styles['chat-page__header-title']}>{title}</div>
          <div className={styles['chat-page__header-actions']}>
            <Button type="text" shape="circle">
              <img src={IconEdit} />
            </Button>
          </div>
        </div>

        <ChatMessage
          list={list}
          onSend={send}
          onOpenCiations={(item) => openCitationsPanel(item, { openPanel: true })}
          onRefrence={setRead}
          onRetryUserMessage={handleRetryUserMessage}
          onResendUserMessage={handleResendUserMessage}
          onDeepResearchConfirm={handleDeepResearchConfirm}
          onDeepResearchCancel={handleDeepResearchCancel}
          onDeepResearchEdit={handleDeepResearchEdit}
          onDeepResearchRetryPlan={handleDeepResearchRetryPlan}
          onDeepResearchOpenProcess={handleDeepResearchOpenProcess}
          onDeepResearchOpenWorkspace={handleDeepResearchOpenWorkspace}
          onDeepResearchExport={handleDeepResearchExport}
          onDeepResearchCopy={handleDeepResearchCopy}
          onDeepResearchSaveToNotebook={handleDeepResearchSaveToNotebook}
          onDeepResearchInsertSummary={handleDeepResearchInsertSummary}
          onAssistantFeedback={handleAssistantFeedback}
          feedbackByMessageId={feedbackByMessageId}
        />

        <Drawer
          title={read?.document_name ?? ''}
          width={800}
          onClose={() => setRead(null)}
          open={!!read}
          destroyOnClose
        >
          <Markdown
            value={
              read?.source_text ||
              read?.content_with_weight ||
              read?.snippet ||
              ''
            }
          />
        </Drawer>

        <Modal
          title="系统状态"
          open={systemStatusOpen}
          footer={null}
          width={720}
          onCancel={() => setSystemStatusOpen(false)}
        >
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Space wrap>
              <Tag color="blue">Provider: {activeLlmProvider}</Tag>
              <Tag color="geekblue">模型: {activeLlmModel}</Tag>
              {isVisionModel(activeLlmModel) ? <Tag color="purple">视觉模型</Tag> : <Tag>文本模型</Tag>}
              {autoVisionHint ? (
                <Tag color="orange">
                  当前输入含图片，发送时将自动切换到{' '}
                  {defaultVisionModelByProvider(activeLlmProvider)}
                </Tag>
              ) : null}
            </Space>
            <div>
              <Typography.Text type="secondary">会话上下文估算</Typography.Text>
              <Space wrap style={{ marginTop: 6 }}>
                <Tag color="cyan">消息数: {list.length}</Tag>
                <Tag color="geekblue">上下文估算 Tokens: {contextTokenEstimate.toLocaleString()}</Tag>
                {modelContextWindowHint ? (
                  <Tag color="purple">
                    窗口占用: {(contextUsagePercent || 0).toFixed(1)}% / {modelContextWindowHint.toLocaleString()}
                  </Tag>
                ) : (
                  <Tag>窗口大小未知</Tag>
                )}
              </Space>
            </div>
            <div>
              <Typography.Text type="secondary">Token 消耗统计</Typography.Text>
              <Space wrap style={{ marginTop: 6 }}>
                <Tag color="green">会话累计 Prompt: {sessionUsageTotals.prompt_tokens.toLocaleString()}</Tag>
                <Tag color="blue">会话累计 Completion: {sessionUsageTotals.completion_tokens.toLocaleString()}</Tag>
                <Tag color="purple">会话累计 Total: {sessionUsageTotals.total_tokens.toLocaleString()}</Tag>
              </Space>
              {latestUsage ? (
                <Space wrap style={{ marginTop: 6 }}>
                  <Tag>最近一轮 Prompt: {latestUsage.prompt_tokens.toLocaleString()}</Tag>
                  <Tag>最近一轮 Completion: {latestUsage.completion_tokens.toLocaleString()}</Tag>
                  <Tag color="gold">最近一轮 Total: {latestUsage.total_tokens.toLocaleString()}</Tag>
                </Space>
              ) : (
                <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6 }}>
                  暂无最近一轮 token 统计（发送一次消息后可见）
                </Typography.Text>
              )}
            </div>
          </Space>
        </Modal>

        <Modal
          title="修改研究计划"
          open={!!editingResearchItem}
          onCancel={() => setEditingResearchItem(null)}
          onOk={handleResearchEditSave}
          okText="保存计划"
          cancelText="取消"
          destroyOnClose
          width={860}
        >
          <Form form={researchPlanForm} layout="vertical">
            <Form.Item
              name="topic"
              label="研究主题"
              rules={[{ required: true, message: '请输入研究主题' }]}
            >
              <Input placeholder="输入研究主题" />
            </Form.Item>
            <Form.Item
              name="plan_text"
              label="计划内容"
              rules={[{ required: true, message: '请至少保留 1 行计划项' }]}
            >
              <TextArea
                autoSize={{ minRows: 8, maxRows: 16 }}
                placeholder={
                  '每行一个计划项：标题 | 研究问题 | 父主题（可选）\n示例：\n背景与定义 | 梳理核心概念 |\n方法对比 | 比较主流技术路线 | 背景与定义'
                }
              />
            </Form.Item>
            <Typography.Text type="secondary">
              系统默认自动执行（Web / 论文 / 代码工具已预配置）。如需干预，仅建议微调计划内容。
            </Typography.Text>
          </Form>
        </Modal>
      </div>
    </ComPageLayout>
  )
}
