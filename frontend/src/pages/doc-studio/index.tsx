import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  message,
  Modal,
  Popconfirm,
  Progress,
  Segmented,
  Select,
  Switch,
  Space,
  Spin,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Tree,
  Typography,
} from 'antd'
import {
  FolderOpenOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SaveOutlined,
  PlayCircleOutlined,
  SendOutlined,
  PlusOutlined,
  UploadOutlined,
  DeleteOutlined,
  FileAddOutlined,
  FolderAddOutlined,
  DownloadOutlined,
  SyncOutlined,
  EyeOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  PushpinOutlined,
  PushpinFilled,
  LikeOutlined,
  DislikeOutlined,
  HistoryOutlined,
  BarChartOutlined,
} from '@ant-design/icons'
import type { DataNode } from 'antd/es/tree'
import { useSnapshot } from 'valtio'
import Editor, { DiffEditor } from '@monaco-editor/react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import type React from 'react'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import {
  compileWorkspace,
  createFileOrDirectory,
  createWorkspace,
  deleteFile,
  bindWorkspaceSession,
  getAgentAsyncEventsUrl,
  fetchCompileStatus,
  fetchFileContent,
  fetchWorkspaceFiles,
  listWorkspaces,
  listOperations,
  fetchMetricsSummary,
  fetchLlmHealth,
  fetchOperationSnapshotFile,
  runAgentTask,
  runAgentTaskAsync,
  sendAgentFeedback,
  updateWorkspace,
  updateFileContent,
  revertOperation,
  uploadFile,
  downloadPdf,
  downloadFile,
  listAgentKnowledgeBases,
} from '@/api/docStudio'
import { listMessages as listSessionMessages } from '@/api/session'
import { docStudioActions, docStudioState } from '@/store/docStudio'
import type { DocStudioChatMessage } from '@/store/docStudio'
import './index.scss'

const { Sider, Content, Header } = Layout
const { Text } = Typography

const findFirstFile = (nodes: DocStudioAPI.FileNode[]): string | undefined => {
  for (const node of nodes) {
    if (node.type === 'file') {
      return node.path
    }
    if (node.children?.length) {
      const child = findFirstFile(node.children)
      if (child) return child
    }
  }
  return undefined
}

const getErrorMessage = (error: any) => {
  if (!error) return '请求失败'
  return (
    error?.response?.data?.detail ||
    error?.response?.data?.message ||
    error?.message ||
    '请求失败'
  )
}

const parseRetrievalContent = (value?: string) => {
  if (!value) return undefined
  try {
    return JSON.parse(value)
  } catch (error) {
    console.error('Failed to parse retrieval_content:', error)
    return undefined
  }
}

const intentTagMap: Record<
  string,
  {
    label: string
    color: string
  }
> = {
  qa: { label: '问答', color: 'default' },
  suggest: { label: '建议', color: 'orange' },
  edit: { label: '编辑', color: 'blue' },
  citation: { label: '引用', color: 'purple' },
  custom: { label: '自定�?, color: 'geekblue' },
  file_op: { label: '文件操作', color: 'default' },
}

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const copyTextToClipboard = async (text: string) => {
  if (!text) return
  // 优先使用现代 Clipboard API
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  // 兼容性降级：使用隐藏 textarea + execCommand
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    document.execCommand('copy')
  } finally {
    document.body.removeChild(textarea)
  }
}

const downloadJson = (filename: string, payload: unknown) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

const resolveEditorLanguage = (filePath?: string) => {
  if (!filePath) return 'latex'
  const lower = filePath.toLowerCase()
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown'
  if (lower.endsWith('.txt')) return 'plaintext'
  return 'latex'
}

const generateId = () =>
  window.crypto?.randomUUID?.() ?? `sel-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`

const collectAllFilePaths = (nodes: DocStudioAPI.FileNode[]): string[] => {
  const result: string[] = []
  const walk = (items: DocStudioAPI.FileNode[]) => {
    for (const node of items) {
      if (node.type === 'file') {
        result.push(node.path)
      }
      if (node.children?.length) {
        walk(node.children)
      }
    }
  }
  walk(nodes)
  return result
}

type SelectionFragment = {
  id: string
  start: number
  end: number
  text: string
  filePath?: string
  placeholder: string
}

type PromptIntent = 'edit' | 'suggest' | 'qa' | 'citation' | 'custom'

type PromptLlmOptions = {
  llm_provider?: 'auto' | 'dashscope' | 'openai'
  llm_model?: string
  llm_temperature?: number
  llm_max_tokens?: number
}

type PromptPreset = {
  id?: string
  label: string
  description?: string
  prompt: string | ((hasSelection: boolean) => string)
  intent: PromptIntent
  isCustom?: boolean
  llm_options?: PromptLlmOptions
  pinned?: boolean
}

type CustomPromptPreset = {
  id: string
  label: string
  description?: string
  prompt: string
  intent: PromptIntent
  llm_options?: PromptLlmOptions
  pinned?: boolean
}

const CUSTOM_PROMPT_STORAGE_KEY = 'doc_studio_prompt_presets'

const quickPromptPresets: PromptPreset[] = [
  // 写作类（最常用 - 会直接修改文件）
  {
    label: '生成正文',
    description: '根据用户输入的大致内容，生成专业详尽的论文正�?,
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请基于我选中的内容或提供的要点，生成专业、详尽的学术论文正文。要求：1) 保持严谨的学术写作风格；2) 逻辑结构清晰，层次分明；3) 使用准确的学术术语和表达�?) 内容详实，论证充分；5) 与文档整体风格保持一致。请在当前光标位置插入生成的正文内容�?
        : '请根据我提供的内容要点，生成专业、详尽的学术论文正文。要求：1) 保持严谨的学术写作风格；2) 逻辑结构清晰，层次分明；3) 使用准确的学术术语和表达�?) 内容详实，论证充分；5) 与文档整体风格保持一致。请在当前光标位置插入生成的正文内容�?,
    intent: 'edit',
  },
  {
    label: '智能续写',
    description: '根据已有内容或选中片段继续写作，保持学术风格一�?,
    prompt: (hasSelection: boolean) => 
      hasSelection 
        ? '请基于【片�?】的内容，继续撰写后续段落。要求：1) 保持严谨的学术写作风格和术语使用的一致性；2) 确保逻辑连贯，与前文自然衔接�?) 遵循学术论文的写作规范；4) 内容充实，论证有力。请直接在当前光标位置续写�?
        : '请根据当前光标位置的前后文内容，继续撰写后续段落。要求：1) 保持严谨的学术写作风格和术语使用的一致性；2) 确保逻辑连贯，与已有内容自然衔接�?) 遵循学术论文的写作规范；4) 内容充实，论证有力。请直接在当前光标位置续写�?,
    intent: 'edit',
  },
  // 编辑类（会直接修改文件）
  {
    label: '优化摘要',
    description: '直接优化摘要，提升学术性与逻辑�?,
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请优化【片�?】中的摘要内容。要求：1) 提升逻辑结构的严谨性和条理性；2) 确保符合学术写作规范（如 IEEE/ACM 等标准）�?) 保持原意和核心观点不变；4) 使用更精准的学术表达�?) 确保摘要能够准确概括全文要点。请直接替换选中内容�?
        : '请优化当前摘要部分。要求：1) 提升逻辑结构的严谨性和条理性；2) 确保符合学术写作规范（如 IEEE/ACM 等标准）�?) 保持原意和核心观点不变；4) 使用更精准的学术表达�?) 确保摘要能够准确概括全文要点�?,
    intent: 'edit',
  },
  {
    label: '润色段落',
    description: '直接润色选中段落，改善语言表达',
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请对【片�?】进行学术润色。要求：1) 改善语法准确性和句式多样性；2) 优化专业术语的使用和表达�?) 增强段落内部的逻辑衔接�?) 保持原意和学术风格不变；5) 提升整体表达的流畅性和专业性。请直接替换选中内容�?
        : '请对当前段落进行学术润色。要求：1) 改善语法准确性和句式多样性；2) 优化专业术语的使用和表达�?) 增强段落内部的逻辑衔接�?) 保持原意和学术风格不变；5) 提升整体表达的流畅性和专业性�?,
    intent: 'edit',
  },
  // 建议类（只给建议，不修改文件�?
  {
    label: '检查问�?,
    description: '检查文本问题并给出建议（不修改文件�?,
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请对【片�?】进行全面的学术质量检查。重点关注：1) 逻辑严谨性（是否存在逻辑漏洞、论证不充分等问题）�?) 表达清晰度（是否存在歧义、表述不清等问题）；3) 学术规范性（是否符合 IEEE/ACM 等学术写作规范）�?) 术语准确性（专业术语使用是否准确、一致）。请详细列出发现的问题，并提供具体的改进建议。不要直接修改文本，仅提供分析和建议�?
        : '请对当前内容进行全面的学术质量检查。重点关注：1) 逻辑严谨性（是否存在逻辑漏洞、论证不充分等问题）�?) 表达清晰度（是否存在歧义、表述不清等问题）；3) 学术规范性（是否符合 IEEE/ACM 等学术写作规范）�?) 术语准确性（专业术语使用是否准确、一致）。请详细列出发现的问题，并提供具体的改进建议。不要直接修改文本，仅提供分析和建议�?,
    intent: 'suggest',
  },
  {
    label: '优化建议',
    description: '给出优化建议（不修改文件�?,
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请从多个维度分析【片�?】，提供专业的优化建议。评估维度包括：1) 学术性（理论深度、创新性、学术价值）�?) 逻辑性（论证链条、结构合理性、因果关系）�?) 表达清晰度（可读性、术语使用、句式结构）�?) 规范性（格式、引用、图表说明等）。请针对每个维度提供具体的优化方向和示例。不要直接修改文本，仅提供分析和建议�?
        : '请从多个维度分析当前内容，提供专业的优化建议。评估维度包括：1) 学术性（理论深度、创新性、学术价值）�?) 逻辑性（论证链条、结构合理性、因果关系）�?) 表达清晰度（可读性、术语使用、句式结构）�?) 规范性（格式、引用、图表说明等）。请针对每个维度提供具体的优化方向和示例。不要直接修改文本，仅提供分析和建议�?,
    intent: 'suggest',
  },
  // 问答类（纯知识问答）
  {
    label: '方法调研',
    description: '调研某个方法或技术的研究现状',
    prompt: () => '请帮我深入调研以下方法或技术的研究现状。要求：1) 梳理该方法的理论基础和发展历程；2) 总结相关领域的主要研究成果和代表性文献；3) 分析最新研究进展和技术趋势；4) 指出当前存在的挑战和未来研究方向。请提供结构化的调研报告�?,
    intent: 'qa',
  },
  {
    label: '背景问答',
    description: '解释术语或概�?,
    prompt: () => '请详细解释以下术语或概念。要求：1) 提供准确的定义和核心特征�?) 说明其在相关研究领域中的重要性；3) 阐述其理论基础或技术原理；4) 如适用，提供相关的应用场景或实例。请使用严谨的学术语言进行解释�?,
    intent: 'qa',
  },
  // 引用类（处理参考文献）
  {
    label: '添加引用',
    description: '在选中位置添加引用',
    prompt: (hasSelection: boolean) =>
      hasSelection
        ? '请在【片�?】的适当位置添加相关的学术引用。要求：1) 引用应与选中内容的主题高度相关；2) 优先选择高质量、权威性的文献�?) 确保引用格式符合文档使用的学术规范（�?IEEE/ACM 等）�?) 在参考文献列表中自动添加相应的条目；5) 保持引用风格的统一性。请直接修改文本，添加引用标记�?
        : '请在当前光标位置添加相关的学术引用。要求：1) 引用应与上下文内容主题高度相关；2) 优先选择高质量、权威性的文献�?) 确保引用格式符合文档使用的学术规范（�?IEEE/ACM 等）�?) 在参考文献列表中自动添加相应的条目；5) 保持引用风格的统一性�?,
    intent: 'citation',
  },
  {
    label: '检查引�?,
    description: '检查引用格式和完整�?,
    prompt: () => '请全面检查整个文档中的引用情况。检查内容包括：1) 引用格式是否符合学术规范（IEEE/ACM/APA 等）�?) 是否存在未定义的引用（undefined citations）；3) 参考文献列表是否完整，是否包含所有被引用的文献；4) 引用风格是否统一�?) 是否存在引用错误或遗漏。请详细列出发现的问题并提供修复建议�?,
    intent: 'citation',
  },
]

type ReadonlyFileNode = Readonly<
  Omit<DocStudioAPI.FileNode, 'children'>
> & {
  readonly children?: ReadonlyArray<ReadonlyFileNode>
}

const cloneFileNodes = (
  nodes: ReadonlyArray<ReadonlyFileNode>,
): DocStudioAPI.FileNode[] =>
  nodes.map((node) => ({
    ...node,
    children: node.children ? cloneFileNodes(node.children) : undefined,
  }))

const buildTreeData = (nodes: ReadonlyArray<any>): DataNode[] =>
  nodes.map((node) => ({
    key: node.path,
    title: node.name,
    icon:
      node.type === 'directory' ? <FolderOpenOutlined /> : <FileTextOutlined />,
    isLeaf: node.type === 'file',
    children: node.children ? buildTreeData(node.children) : undefined,
  }))

const LatexEditorPage = () => {
  const params = useParams<{ workspaceId?: string }>()
  const navigate = useNavigate()
  const snap = useSnapshot(docStudioState)
  const [prompt, setPrompt] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [asyncMode, setAsyncMode] = useState(true)
  const [asyncRunId, setAsyncRunId] = useState<string | null>(null)
  const [asyncRunStatus, setAsyncRunStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const [selections, setSelections] = useState<SelectionFragment[]>([])
  const [workspaceModalOpen, setWorkspaceModalOpen] = useState(false)
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [newWorkspaceType, setNewWorkspaceType] = useState<'latex' | 'markdown'>('latex')
  const [workspaceSubmitting, setWorkspaceSubmitting] = useState(false)
  const [fileModalOpen, setFileModalOpen] = useState(false)
  const [fileModalType, setFileModalType] = useState<'file' | 'directory'>('file')
  const [fileModalPath, setFileModalPath] = useState('')
  const [fileModalContent, setFileModalContent] = useState('')
  const [fileSubmitting, setFileSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [rightTab, setRightTab] = useState<'chat' | 'history' | 'compile'>('chat')
  const [lastPromptLog, setLastPromptLog] = useState<{
    original: string
    final: string
    selectionsCount: number
    timestamp: string
  } | null>(null)
  const [showPromptLog, setShowPromptLog] = useState(false)
  const [customPromptPresets, setCustomPromptPresets] = useState<CustomPromptPreset[]>([])
  const [presetModalOpen, setPresetModalOpen] = useState(false)
  const [presetEditingId, setPresetEditingId] = useState<string | null>(null)
  const [presetName, setPresetName] = useState('')
  const [presetIntent, setPresetIntent] = useState<PromptIntent>('edit')
  const [presetPrompt, setPresetPrompt] = useState('')
  const [presetBindModel, setPresetBindModel] = useState(false)
  const [presetFilter, setPresetFilter] = useState<'all' | PromptIntent>('all')
  const [presetSearch, setPresetSearch] = useState('')
  const [webSearchEnabled, setWebSearchEnabled] = useState(false)
  const [webSearchSaving, setWebSearchSaving] = useState(false)
  const [sessionBindValue, setSessionBindValue] = useState('')
  const [sessionBindLoading, setSessionBindLoading] = useState(false)
  const [llmProvider, setLlmProvider] = useState<'auto' | 'dashscope' | 'openai'>('auto')
  const [llmModel, setLlmModel] = useState('')
  const [llmTemperature, setLlmTemperature] = useState<number | null>(null)
  const [llmMaxTokens, setLlmMaxTokens] = useState<number | null>(null)
  const [llmOptionsReady, setLlmOptionsReady] = useState(false)
  const [promptPresetsReady, setPromptPresetsReady] = useState(false)
  const [workspaceFlagsReady, setWorkspaceFlagsReady] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const presetFileInputRef = useRef<HTMLInputElement | null>(null)
  const editorRef = useRef<any>(null)
  const asyncStreamRef = useRef<EventSource | null>(null)
  const chatMessagesEndRef = useRef<HTMLDivElement | null>(null)
  const lastAutoScrollMessageIdRef = useRef<string | null>(null)
  const promptInputRef = useRef<TextAreaRef | null>(null)
  const llmOptionsAppliedRef = useRef<string>('')
  const promptPresetsAppliedRef = useRef<string>('')
  const workspaceFlagsAppliedRef = useRef<string>('')
  
  // Diff 预览相关状�?
  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [allFileDiffs, setAllFileDiffs] = useState<DocStudioAPI.FileDiff[]>([])
  const [currentDiffIndex, setCurrentDiffIndex] = useState(0)
  const [acceptedDiffs, setAcceptedDiffs] = useState<Set<number>>(new Set())
  const [lastOperationId, setLastOperationId] = useState<string | null>(null)
  const [diffOperationId, setDiffOperationId] = useState<string | null>(null)
  const [undoingLastApply, setUndoingLastApply] = useState(false)
  const [diffReverting, setDiffReverting] = useState(false)
  const [operationHistoryOpen, setOperationHistoryOpen] = useState(false)
  const [operationHistoryLoading, setOperationHistoryLoading] = useState(false)
  const [operationHistory, setOperationHistory] = useState<DocStudioAPI.OperationSummary[]>([])
  const [revertingOperationId, setRevertingOperationId] = useState<string | null>(null)
  const [systemStatsLoading, setSystemStatsLoading] = useState(false)
  const [llmHealth, setLlmHealth] = useState<DocStudioAPI.LlmHealthSummary | null>(null)
  const [metricsSummary, setMetricsSummary] = useState<DocStudioAPI.MetricsSummary | null>(null)
  const [systemStatusOpen, setSystemStatusOpen] = useState(false)
  const [diffContentLoading, setDiffContentLoading] = useState(false)
  const [resolvedOriginal, setResolvedOriginal] = useState('')
  const [resolvedModified, setResolvedModified] = useState('')
  const [lineChanges, setLineChanges] = useState<any[]>([])
  const [acceptedLineChanges, setAcceptedLineChanges] = useState<Set<number>>(new Set())
  const diffEditorRef = useRef<any>(null)
  
  // 可调整宽度的状态（使用 localStorage 持久化）
  const [leftSiderWidth, setLeftSiderWidth] = useState(() => {
    const saved = localStorage.getItem('latex_editor_left_sider_width')
    return saved ? parseInt(saved, 10) : 260
  })
  const [rightSiderWidth, setRightSiderWidth] = useState(() => {
    const saved = localStorage.getItem('latex_editor_right_sider_width')
    return saved ? parseInt(saved, 10) : 360
  })
  
  // 拖拽状�?
  const [isDraggingLeft, setIsDraggingLeft] = useState(false)
  const [isDraggingRight, setIsDraggingRight] = useState(false)
  
  const preferredKbFromUrl = useMemo(() => {
    if (typeof window === 'undefined') return null
    const raw = new URLSearchParams(window.location.search).get('kb_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : null
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const raw = localStorage.getItem('doc_studio_llm_options')
    if (!raw) return
    try {
      const parsed = JSON.parse(raw)
      if (parsed?.llm_provider) {
        setLlmProvider(parsed.llm_provider)
      }
      if (parsed?.llm_model) {
        setLlmModel(parsed.llm_model)
      }
      if (typeof parsed?.llm_temperature === 'number') {
        setLlmTemperature(parsed.llm_temperature)
      }
      if (typeof parsed?.llm_max_tokens === 'number') {
        setLlmMaxTokens(parsed.llm_max_tokens)
      }
    } catch (error) {
      console.warn('Failed to load Doc Studio LLM options', error)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const payload = {
      llm_provider: llmProvider,
      llm_model: llmModel,
      llm_temperature: llmTemperature,
      llm_max_tokens: llmMaxTokens,
    }
    localStorage.setItem('doc_studio_llm_options', JSON.stringify(payload))
  }, [llmProvider, llmModel, llmTemperature, llmMaxTokens])

  const persistCustomPromptPresets = useCallback((items: CustomPromptPreset[]) => {
    setCustomPromptPresets(items)
    if (typeof window !== 'undefined') {
      localStorage.setItem(CUSTOM_PROMPT_STORAGE_KEY, JSON.stringify(items))
    }
  }, [])

  const applyPromptPresetsFromConfig = useCallback((config?: Record<string, any>) => {
    if (!config) return
    const raw = Array.isArray(config.prompt_presets) ? config.prompt_presets : []
    if (!raw.length) return
    const allowedIntents: PromptIntent[] = ['edit', 'suggest', 'qa', 'citation', 'custom']
    const sanitized: CustomPromptPreset[] = raw
      .filter((item: any) => item && typeof item.label === 'string' && typeof item.prompt === 'string')
      .map((item: any) => ({
        id: item.id || generateId(),
        label: item.label,
        description: item.description || '自定义模�?,
        prompt: item.prompt,
        intent: allowedIntents.includes(item.intent) ? item.intent : 'custom',
        llm_options: item.llm_options,
        pinned: Boolean(item.pinned),
      }))
    if (!sanitized.length) return
    promptPresetsAppliedRef.current = JSON.stringify(sanitized)
    setCustomPromptPresets(sanitized)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const raw = localStorage.getItem(CUSTOM_PROMPT_STORAGE_KEY)
    if (!raw) return
    try {
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return
      const sanitized: CustomPromptPreset[] = parsed
        .filter((item) => item && typeof item.label === 'string' && typeof item.prompt === 'string')
        .map((item) => ({
          id: item.id || generateId(),
          label: item.label,
          description: item.description || '自定义模�?,
          prompt: item.prompt,
          intent: item.intent || 'custom',
          llm_options: item.llm_options,
          pinned: Boolean(item.pinned),
        }))
      setCustomPromptPresets(sanitized)
    } catch (error) {
      console.warn('Failed to load custom prompt presets', error)
    }
  }, [])

  const llmOptions = useMemo(() => {
    const options: Record<string, any> = {}
    if (llmProvider !== 'auto') {
      options.llm_provider = llmProvider
    }
    if (llmModel.trim()) {
      options.llm_model = llmModel.trim()
    }
    if (typeof llmTemperature === 'number') {
      options.llm_temperature = llmTemperature
    }
    if (typeof llmMaxTokens === 'number') {
      options.llm_max_tokens = llmMaxTokens
    }
    return options
  }, [llmProvider, llmModel, llmTemperature, llmMaxTokens])

  const llmOptionsConfig = useMemo(() => {
    const options: Record<string, any> = {
      llm_provider: llmProvider,
    }
    if (llmModel.trim()) {
      options.llm_model = llmModel.trim()
    }
    if (typeof llmTemperature === 'number') {
      options.llm_temperature = llmTemperature
    }
    if (typeof llmMaxTokens === 'number') {
      options.llm_max_tokens = llmMaxTokens
    }
    return options
  }, [llmProvider, llmModel, llmTemperature, llmMaxTokens])

  const promptPresetsConfig = useMemo(() => {
    return customPromptPresets.map((preset) => ({
      id: preset.id,
      label: preset.label,
      description: preset.description,
      prompt: preset.prompt,
      intent: preset.intent,
      llm_options: preset.llm_options,
      pinned: preset.pinned,
    }))
  }, [customPromptPresets])

  const workspaceFlagsConfig = useMemo(() => {
    return {
      enable_web_search: webSearchEnabled,
    }
  }, [webSearchEnabled])

  const promptPresets = useMemo<PromptPreset[]>(() => {
    const custom = customPromptPresets.map((preset) => ({
      ...preset,
      isCustom: true,
    }))
    return [...custom, ...quickPromptPresets]
  }, [customPromptPresets])

  const orderedPromptPresets = useMemo(() => {
    return [...promptPresets].sort((a, b) => {
      const pinScore = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
      if (pinScore !== 0) return pinScore
      const customScore = Number(Boolean(b.isCustom)) - Number(Boolean(a.isCustom))
      if (customScore !== 0) return customScore
      return a.label.localeCompare(b.label, 'zh-CN')
    })
  }, [promptPresets])

  const visiblePromptPresets = useMemo(() => {
    let filtered = orderedPromptPresets
    if (presetFilter === 'custom') {
      filtered = filtered.filter((preset) => preset.isCustom)
    } else if (presetFilter !== 'all') {
      filtered = filtered.filter((preset) => preset.intent === presetFilter)
    }
    const keyword = presetSearch.trim().toLowerCase()
    if (!keyword) return filtered
    return filtered.filter((preset) => {
      const promptText = typeof preset.prompt === 'string' ? preset.prompt : ''
      const haystack = `${preset.label} ${preset.description || ''} ${promptText}`.toLowerCase()
      return haystack.includes(keyword)
    })
  }, [orderedPromptPresets, presetFilter, presetSearch])

  const groupedPromptPresets = useMemo(() => {
    const groups: Record<PromptIntent, PromptPreset[]> = {
      edit: [],
      suggest: [],
      qa: [],
      citation: [],
      custom: [],
    }
    for (const preset of visiblePromptPresets) {
      groups[preset.intent]?.push(preset)
    }
    const order: PromptIntent[] = ['edit', 'suggest', 'qa', 'citation', 'custom']
    return order
      .map((intent) => ({ intent, items: groups[intent] }))
      .filter((group) => group.items.length > 0)
  }, [visiblePromptPresets])

  const applyLlmOptionsFromConfig = useCallback(
    (config?: Record<string, any>) => {
      if (!config) return
      const raw =
        config.llm_options && typeof config.llm_options === 'object' ? config.llm_options : config
      const hasAny =
        raw.llm_provider ||
        raw.llm_model ||
        typeof raw.llm_temperature === 'number' ||
        typeof raw.llm_max_tokens === 'number'
      if (!hasAny) return

      const nextProvider =
        raw.llm_provider === 'dashscope' || raw.llm_provider === 'openai' || raw.llm_provider === 'auto'
          ? raw.llm_provider
          : llmProvider
      const nextModel = typeof raw.llm_model === 'string' ? raw.llm_model : llmModel
      const nextTemperature =
        typeof raw.llm_temperature === 'number' ? raw.llm_temperature : llmTemperature
      const nextMaxTokens =
        typeof raw.llm_max_tokens === 'number' ? raw.llm_max_tokens : llmMaxTokens

      const normalized = {
        llm_provider: nextProvider,
        llm_model: nextModel,
        llm_temperature: nextTemperature,
        llm_max_tokens: nextMaxTokens,
      }
      llmOptionsAppliedRef.current = JSON.stringify(normalized)
      setLlmProvider(nextProvider)
      setLlmModel(nextModel || '')
      setLlmTemperature(typeof nextTemperature === 'number' ? nextTemperature : null)
      setLlmMaxTokens(typeof nextMaxTokens === 'number' ? nextMaxTokens : null)
    },
    [llmProvider, llmModel, llmTemperature, llmMaxTokens],
  )

  const applyWorkspaceFlagsFromConfig = useCallback((config?: Record<string, any>) => {
    if (!config) return
    const enableWebSearch =
      typeof config.enable_web_search === 'boolean' ? config.enable_web_search : false
    const sessionId = typeof config.session_id === 'string' ? config.session_id : ''
    const normalized = {
      enable_web_search: enableWebSearch,
    }
    workspaceFlagsAppliedRef.current = JSON.stringify(normalized)
    setWebSearchEnabled(enableWebSearch)
    setSessionBindValue(sessionId)
  }, [])

  useEffect(() => {
    setLlmOptionsReady(false)
    llmOptionsAppliedRef.current = ''
  }, [snap.workspaceId])

  useEffect(() => {
    setPromptPresetsReady(false)
    promptPresetsAppliedRef.current = ''
  }, [snap.workspaceId])

  useEffect(() => {
    setWorkspaceFlagsReady(false)
    workspaceFlagsAppliedRef.current = ''
  }, [snap.workspaceId])

  useEffect(() => {
    if (!snap.workspaceId || !llmOptionsReady) return
    const serialized = JSON.stringify(llmOptionsConfig)
    if (serialized === llmOptionsAppliedRef.current) return
    llmOptionsAppliedRef.current = serialized
    updateWorkspace({
      workspaceId: snap.workspaceId,
      config: { llm_options: llmOptionsConfig },
    }).catch((error) => {
      console.warn('Failed to persist LLM options', error)
    })
  }, [llmOptionsConfig, llmOptionsReady, snap.workspaceId])

  useEffect(() => {
    if (!snap.workspaceId || !promptPresetsReady) return
    const serialized = JSON.stringify(promptPresetsConfig)
    if (serialized === promptPresetsAppliedRef.current) return
    promptPresetsAppliedRef.current = serialized
    updateWorkspace({
      workspaceId: snap.workspaceId,
      config: { prompt_presets: promptPresetsConfig },
    }).catch((error) => {
      console.warn('Failed to persist prompt presets', error)
    })
  }, [promptPresetsConfig, promptPresetsReady, snap.workspaceId])

  useEffect(() => {
    if (!snap.workspaceId || !workspaceFlagsReady) return
    const serialized = JSON.stringify(workspaceFlagsConfig)
    if (serialized === workspaceFlagsAppliedRef.current) return
    workspaceFlagsAppliedRef.current = serialized
    setWebSearchSaving(true)
    updateWorkspace({
      workspaceId: snap.workspaceId,
      config: workspaceFlagsConfig,
    })
      .catch((error) => {
        console.warn('Failed to persist workspace flags', error)
      })
      .finally(() => {
        setWebSearchSaving(false)
      })
  }, [workspaceFlagsConfig, workspaceFlagsReady, snap.workspaceId])
  const preferredFileFromUrl = useMemo(() => {
    if (typeof window === 'undefined') return ''
    const raw = new URLSearchParams(window.location.search).get('file')
    return raw ? raw.trim() : ''
  }, [])
  const [knowledgeBases, setKnowledgeBases] = useState<DocStudioAPI.KnowledgeBaseSummary[]>([])
  const [knowledgeLoading, setKnowledgeLoading] = useState(false)
  // 默认不使用任何知识库，由用户手动选择
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<number | null>(null)
  const [diffViewMode, setDiffViewMode] = useState<'split' | 'inline'>('split')
  const [feedbackSubmitting, setFeedbackSubmitting] = useState<Record<string, boolean>>({})
  
  // 右键菜单状�?
  const [contextMenuVisible, setContextMenuVisible] = useState(false)
  const [contextMenuPosition, setContextMenuPosition] = useState({ x: 0, y: 0 })
  const [contextMenuPath, setContextMenuPath] = useState<string>('')
  const [contextMenuType, setContextMenuType] = useState<'file' | 'directory'>('file')
  
  // Tree 展开状态（默认展开所有目录）
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([])

  const workspaceOptions = useMemo(
    () =>
      snap.workspaces.map((item) => ({
        label: item.name,
        value: item.workspaceId,
      })),
    [snap.workspaces],
  )

  const treeData = useMemo(
    () => buildTreeData(cloneFileNodes(snap.fileTree)),
    [snap.fileTree],
  )

  const isLatexWorkspace = useMemo(() => {
    const config = snap.workspaceConfig || {}
    const workspaceType = config.workspace_type || config.workspaceType
    const primaryFormat = config.primary_format || config.primaryFormat
    const mainFile = config.main_file || config.mainFile
    if (workspaceType === 'latex' || primaryFormat === 'latex') return true
    if (typeof mainFile === 'string' && mainFile.toLowerCase().endsWith('.tex')) return true
    if (snap.activeFilePath?.toLowerCase().endsWith('.tex')) return true
    return false
  }, [snap.activeFilePath, snap.workspaceConfig])
  
  // 自动展开所有目�?
  useEffect(() => {
    const collectDirectoryKeys = (nodes: any[]): string[] => {
      const keys: string[] = []
      for (const node of nodes) {
        if (node.type === 'directory') {
          keys.push(node.path)
          if (node.children) {
            keys.push(...collectDirectoryKeys(node.children))
          }
        }
      }
      return keys
    }
    
    if (snap.fileTree && snap.fileTree.length > 0) {
      const allDirKeys = collectDirectoryKeys(snap.fileTree as any)
      setExpandedKeys(allDirKeys)
    }
  }, [snap.fileTree])
  const knowledgeBaseOptions = useMemo(
    () =>
      // 确保只显示非临时知识库（双重保险�?
      knowledgeBases
        .filter((item) => !item.is_ephemeral)
        .map((item) => ({
          label: item.name,
          value: item.id,
        })),
    [knowledgeBases],
  )
  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId) || null,
    [knowledgeBases, selectedKnowledgeBaseId],
  )

  const totalSelectionChars = useMemo(
    () => selections.reduce((sum, item) => sum + item.text.length, 0),
    [selections],
  )

  const insertPlaceholderAtCursor = useCallback((placeholder: string) => {
    // 统一行为：始终在末尾追加占位符，前面有内容时用空格分�?
    setPrompt((prev) => {
      if (!prev || prev.trim() === '') {
        return placeholder
      }
      // 如果末尾已经有空格或换行，不再添�?
      if (prev.endsWith(' ') || prev.endsWith('\n')) {
        return `${prev}${placeholder}`
      }
      return `${prev} ${placeholder}`
    })
  }, [])

  const addSelectionSnippet = useCallback(() => {
    // 直接从编辑器读取当前选择，避免状态延迟问�?
    const editor = editorRef.current
    if (!editor) {
      message.warning('编辑器未就绪')
      return
    }
    
    const selectionRanges = editor.getSelections() || []
    const targetRange = selectionRanges.find((range: any) => !range.isEmpty())
    
    if (!targetRange) {
      message.warning('请先在编辑器中选中内容')
      return
    }
    
    const model = editor.getModel()
    if (!model) {
      message.warning('编辑器内容未加载')
      return
    }
    
    const text = model.getValueInRange(targetRange).trim()
    if (!text) {
      message.warning('选中内容为空')
      return
    }
    
    // 动态获取当前片段数�?
    setSelections((prev) => {
      const placeholder = `@selection${prev.length + 1}`
      const snippet: SelectionFragment = {
        id: generateId(),
        start: model.getOffsetAt(targetRange.getStartPosition()),
        end: model.getOffsetAt(targetRange.getEndPosition()),
        text,
        filePath: snap.activeFilePath,
        placeholder,
      }
      insertPlaceholderAtCursor(placeholder)
      message.success(`已添加片段：${placeholder}`)
      
      // 添加片段后，聚焦到输入框（延迟确�?DOM 更新完成�?
      requestAnimationFrame(() => {
        promptInputDivRef.current?.focus()
      })
      
      return [...prev, snippet]
    })
  }, [insertPlaceholderAtCursor, snap.activeFilePath])

  const removeSelectionSnippet = useCallback(
    (placeholder: string) => {
      if (!selections.length) return
      const filtered = selections.filter((item) => item.placeholder !== placeholder)
      if (filtered.length === selections.length) return
      let updatedPrompt = prompt.replace(new RegExp(escapeRegExp(placeholder), 'g'), '')
      const normalized = filtered.map((item, idx) => {
        const newPlaceholder = `@selection${idx + 1}`
        if (item.placeholder !== newPlaceholder) {
          const regex = new RegExp(escapeRegExp(item.placeholder), 'g')
          updatedPrompt = updatedPrompt.replace(regex, newPlaceholder)
        }
        return { ...item, placeholder: newPlaceholder }
      })
      setSelections(normalized)
      setPrompt(updatedPrompt)
    },
    [prompt, selections],
  )

  // �?prompt 文本转换为带标签�?HTML（用�?contentEditable�?
  const promptInputDivRef = useRef<HTMLDivElement | null>(null)
  const lastPromptLengthRef = useRef(0)
  
  // �?contentEditable div 中精确提取文本（避免 innerText 产生意外换行�?
  const extractTextFromDiv = useCallback((el: HTMLElement): string => {
    let text = ''
    const extract = (node: ChildNode) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent || ''
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement
        if (element.classList.contains('doc-studio__prompt-tag')) {
          // 占位符标签：读取�?data-placeholder
          text += element.getAttribute('data-placeholder') || ''
        } else if (element.tagName === 'BR') {
          // 保留用户手动输入的换�?
          text += '\n'
        } else if (element.tagName === 'DIV') {
          // div 通常表示新行
          if (text && !text.endsWith('\n')) {
            text += '\n'
          }
          element.childNodes.forEach(extract)
        } else {
          // 其他元素递归处理
          element.childNodes.forEach(extract)
        }
      }
    }
    el.childNodes.forEach(extract)
    return text
  }, [])
  
  useEffect(() => {
    const el = promptInputDivRef.current
    if (!el) return
    
    // 只有当内容真正改变时才更新（避免光标跳动�?
    const currentText = extractTextFromDiv(el)
    if (currentText === prompt) return
    
    // 判断是否是追加操作（新内容比旧内容长�?
    const isAppending = prompt.length > lastPromptLengthRef.current
    lastPromptLengthRef.current = prompt.length
    
    // 构建HTML
    let text = prompt
    if (!text) {
      el.innerHTML = ''
      return
    }
    
    // 构建 HTML：先找出所有占位符的位置，然后分段处理
    const placeholderPattern = /@selection\d+/g
    const placeholders: { match: string; index: number }[] = []
    let match: RegExpExecArray | null
    
    while ((match = placeholderPattern.exec(text)) !== null) {
      placeholders.push({ match: match[0], index: match.index })
    }
    
    // 分段构建 HTML
    let html = ''
    let lastIndex = 0
    
    placeholders.forEach(({ match, index }) => {
      // 添加占位符前的普通文本（需要转义并处理换行�?
      if (index > lastIndex) {
        const plainText = text.slice(lastIndex, index)
        const escapedText = plainText
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/\n/g, '<br>')
        html += escapedText
      }
      
      // 添加占位符标签（单行，无空格�?
      html += `<span class="doc-studio__prompt-tag" contenteditable="false" data-placeholder="${match}"><span class="anticon anticon-file-text"><svg viewBox="64 64 896 896" focusable="false" width="10" height="10" fill="currentColor"><path d="M854.6 288.6L639.4 73.4c-6-6-14.1-9.4-22.6-9.4H192c-17.7 0-32 14.3-32 32v832c0 17.7 14.3 32 32 32h640c17.7 0 32-14.3 32-32V311.3c0-8.5-3.4-16.7-9.4-22.7zM790.2 326H602V137.8L790.2 326zm1.8 562H232V136h302v216a42 42 0 0042 42h216v494z"></path></svg></span><span>${match}</span><span class="anticon anticon-close prompt-tag-close" data-action="remove-${match}"><svg viewBox="64 64 896 896" focusable="false" width="9" height="9" fill="currentColor"><path d="M563.8 512l262.5-312.9c4.4-5.2.7-13.1-6.1-13.1h-79.8c-4.7 0-9.2 2.1-12.3 5.7L511.6 449.8 295.1 191.7c-3-3.6-7.5-5.7-12.3-5.7H203c-6.8 0-10.5 7.9-6.1 13.1L459.4 512 196.9 824.9A7.95 7.95 0 00203 838h79.8c4.7 0 9.2-2.1 12.3-5.7l216.5-258.1 216.5 258.1c3 3.6 7.5 5.7 12.3 5.7h79.8c6.8 0 10.5-7.9 6.1-13.1L563.8 512z"></path></svg></span></span>`
      
      lastIndex = index + match.length
    })
    
    // 添加最后剩余的文本
    if (lastIndex < text.length) {
      const plainText = text.slice(lastIndex)
      const escapedText = plainText
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>')
      html += escapedText
    }
    
    el.innerHTML = html
    
    // 把光标放在末尾（对于追加操作，这是正确的行为�?
    if (isAppending) {
      requestAnimationFrame(() => {
        try {
          const selection = window.getSelection()
          if (selection && el.childNodes.length > 0) {
            const range = document.createRange()
            // 找到最后一个子节点
            const lastChild = el.childNodes[el.childNodes.length - 1]
            
            if (lastChild.nodeType === Node.TEXT_NODE) {
              // 如果最后一个是文本节点，光标放在文本末�?
              range.setStart(lastChild, (lastChild as Text).length)
            } else {
              // 如果最后一个不是文本节点（比如是标签），光标放在它之后
              range.setStartAfter(lastChild)
            }
            
            range.collapse(true)
            selection.removeAllRanges()
            selection.addRange(range)
          }
        } catch (e) {
          // 忽略光标定位错误
        }
      })
    }
  }, [prompt, selections])

  const currentFileBuffer = snap.activeFilePath
    ? snap.files[snap.activeFilePath]
    : undefined

  const intentStatus = snap.agentStatus.intentType
  const planStatus = snap.agentStatus.plan
  const planTotalSteps = planStatus?.steps?.length ?? 0
  const planCompletedSteps = planStatus
    ? Math.min(planStatus.completed_steps ?? 0, planTotalSteps)
    : 0
  const planNextStep =
    planStatus && planCompletedSteps < planTotalSteps ? planStatus.steps[planCompletedSteps] : null
  const planPercent =
    planTotalSteps > 0 ? Math.round((planCompletedSteps / planTotalSteps) * 100) : 0
  const agentWarnings = snap.agentStatus.warnings ?? []

  const llmMetricEntries = useMemo(() => {
    const metrics = metricsSummary?.llm || {}
    return Object.entries(metrics).map(([key, value]) => {
      const parts = key.split('::')
      const provider = parts[0] || key
      const model = parts.slice(1).join('::') || key
      return {
        key,
        provider,
        model,
        ...value,
      }
    })
  }, [metricsSummary])

  const llmTotals = useMemo(() => {
    let tokens = 0
    let cost = 0
    llmMetricEntries.forEach((entry) => {
      tokens += entry.total_tokens || 0
      cost += entry.total_cost || 0
    })
    return { tokens, cost }
  }, [llmMetricEntries])

  const formatStepTime = useCallback((ts?: number) => {
    if (!ts) return ''
    return new Date(ts * 1000).toLocaleTimeString()
  }, [])

  const historyItems = useMemo(() => {
    const labelMap: Record<string, string> = {
      thought: '思�?,
      action: '执行',
      result: '结果',
      reflection: '反�?,
      finish: '完成',
    }
    return snap.executionHistory.map((step, index) => {
      const type = (step.type || '').toLowerCase()
      let color = 'gray'
      if (type === 'action') color = 'blue'
      else if (type === 'result') color = step.result?.success === false ? 'red' : 'green'
      else if (type === 'reflection') color = 'purple'
      else if (type === 'finish') color = 'gray'
      const label = labelMap[type] || step.type || `步骤 ${index + 1}`
      const timestampLabel = formatStepTime(step.timestamp)
      return {
        color,
        children: (
          <div className="doc-studio__history-card">
            <div className="doc-studio__history-head">
              <Text strong>{`${index + 1}. ${label}`}</Text>
              {step.tool && <Tag>{step.tool}</Tag>}
              {timestampLabel && <Text type="secondary">{timestampLabel}</Text>}
            </div>
            <div className="doc-studio__history-content">{step.content}</div>
            {step.result?.summary && (
              <Text className="doc-studio__history-summary" type="secondary">
                {step.result.summary}
              </Text>
            )}
            {step.result?.error && (
              <Alert
                type="error"
                showIcon
                message="工具执行失败"
                description={step.result.error}
                style={{ marginTop: 8 }}
              />
            )}
          </div>
        ),
      }
    })
  }, [snap.executionHistory, formatStepTime])

  const openFile = useCallback(
    async (path: string, forceReload = false) => {
      if (!docStudioState.workspaceId) return
      if (!forceReload) {
        const existing = docStudioState.files[path]
        if (existing && !existing.loading) {
          docStudioActions.setActiveFile(path)
          return
        }
      }
      docStudioActions.setActiveFile(path)
      docStudioActions.setFileLoading(path, true)
      try {
        const file = await fetchFileContent({
          workspaceId: docStudioState.workspaceId,
          path,
        })
        docStudioActions.setFileContent(path, file.content, file.encoding)
      } catch (error) {
        message.error(getErrorMessage(error))
      } finally {
        docStudioActions.setFileLoading(path, false)
      }
    },
    [],
  )

  const loadWorkspaceChatHistory = useCallback(
    async (workspaceId: string, config: Record<string, any>) => {
      const sessionId = config?.session_id ?? config?.sessionId
      if (!sessionId) {
        docStudioActions.setChatMessages([])
        return
      }
      try {
        const { data } = await listSessionMessages({
          sessionId: String(sessionId),
          page: 1,
          pageSize: 200,
        })
        const items = Array.isArray(data?.items) ? data.items : []
        const messages: DocStudioChatMessage[] = []
        items.forEach((item) => {
          const retrievalData = parseRetrievalContent(item.retrieval_content)
          const source = retrievalData?.source
          const sourceWorkspace = retrievalData?.workspace_id
          if (source && source !== 'doc_studio') return
          if (sourceWorkspace && sourceWorkspace !== workspaceId) return
          const createdAt = item.create_time ? Date.parse(item.create_time) : Date.now()
          const baseId = item.message_id || `${createdAt}-${Math.random()}`
          if (item.user_question) {
            messages.push({
              id: `${baseId}-user`,
              role: 'user',
              content: item.user_question,
              createdAt,
              meta: {
                messageId: item.message_id,
                source,
                workspaceId: sourceWorkspace,
                traceId: retrievalData?.trace_id,
              },
            })
          }
          if (item.model_answer) {
            messages.push({
              id: `${baseId}-agent`,
              role: 'agent',
              content: item.model_answer,
              createdAt: createdAt + 1,
              meta: {
                messageId: item.message_id,
                source,
                workspaceId: sourceWorkspace,
                traceId: retrievalData?.trace_id,
              },
            })
          }
        })
        docStudioActions.setChatMessages(messages)
      } catch (error) {
        console.warn('Failed to load session messages', error)
      }
    },
    [],
  )

  const loadWorkspaceFiles = useCallback(
    async (workspaceId: string, shouldOpenDefault = true) => {
      try {
        const data = await fetchWorkspaceFiles({ workspaceId })
        docStudioActions.setFileTree(data.files)
        docStudioActions.setWorkspaceConfig(data.config)
        applyLlmOptionsFromConfig(data.config)
        applyPromptPresetsFromConfig(data.config)
        applyWorkspaceFlagsFromConfig(data.config)
        await loadWorkspaceChatHistory(workspaceId, data.config)
        setLlmOptionsReady(true)
        setPromptPresetsReady(true)
        setWorkspaceFlagsReady(true)
        
        // 调试：打印文件树结构
        const printFileTree = (nodes: any[], indent = '') => {
          for (const node of nodes) {
            console.log(`${indent}${node.type === 'directory' ? '📁' : '📄'} ${node.name} (${node.path})`)
            if (node.children && node.children.length > 0) {
              printFileTree(node.children, indent + '  ')
            }
          }
        }
        console.log('📂 文件树已更新:')
        printFileTree(data.files)

        // 【Cursor 风格】从 localStorage 恢复该工作区上次打开的文件标签页
        if (shouldOpenDefault) {
          const allPaths = collectAllFilePaths(data.files)
          const preferredFile =
            preferredFileFromUrl && allPaths.includes(preferredFileFromUrl)
              ? preferredFileFromUrl
              : ''
          const storageKey = `latex_editor_workspace_state_${workspaceId}`
          let restored = false
          try {
            if (preferredFile) {
              await openFile(preferredFile, true)
              docStudioActions.setActiveFile(preferredFile)
              restored = true
            }
            const raw = localStorage.getItem(storageKey)
            if (raw && !restored) {
              const parsed = JSON.parse(raw) as {
                openedFiles?: string[]
                activeFilePath?: string
              }
              const validOpened = parsed.openedFiles?.filter((p) => allPaths.includes(p)) ?? []

              if (validOpened.length > 0) {
                // 依次打开所有上次的文件标签
                for (const path of validOpened) {
                  // eslint-disable-next-line no-await-in-loop
                  await openFile(path)
                }
                // 如果有记录激活文件且仍然存在，则切换到该文件
                if (parsed.activeFilePath && validOpened.includes(parsed.activeFilePath)) {
                  docStudioActions.setActiveFile(parsed.activeFilePath)
                }
                restored = true
              }
            }
          } catch (e) {
            // 恢复失败不影响正常逻辑，忽略即�?
            // eslint-disable-next-line no-console
            console.warn('恢复工作区文件状态失�?, e)
          }

          // 如果没有可恢复的状态，就不要自动打开任何文件（空标签栏）
          if (!restored) {
            docStudioActions.setActiveFile('')
          }
        }
      } catch (error) {
        message.error(getErrorMessage(error))
      }
    },
    [
      applyLlmOptionsFromConfig,
      applyPromptPresetsFromConfig,
      applyWorkspaceFlagsFromConfig,
      openFile,
      loadWorkspaceChatHistory,
      preferredFileFromUrl,
    ],
  )

  const loadKnowledgeBases = useCallback(async () => {
    setKnowledgeLoading(true)
    try {
      const data = await listAgentKnowledgeBases()
      const list = Array.isArray(data) ? data : []
      // 过滤掉临时知识库（ephemeral），只显示永久知识库
      const permanentBases = list.filter((item) => !item.is_ephemeral)
      setKnowledgeBases(permanentBases)
      // 不自动选择知识库，保持用户当前选择（如果有�?
      setSelectedKnowledgeBaseId((current) => {
        // 如果当前选择的知识库仍然存在，保持选择
        if (current && permanentBases.some((item) => item.id === current)) {
          return current
        }
        // 如果 URL 参数指定了知识库且仍然存在，使用�?
        if (
          preferredKbFromUrl &&
          permanentBases.some((item) => item.id === preferredKbFromUrl)
        ) {
          return preferredKbFromUrl
        }
        // 否则不选择任何知识库（null），由用户手动选择
        return null
      })
    } catch (error) {
      message.error(getErrorMessage(error))
      if (asyncMode) {
        setChatLoading(false)
      }
    } finally {
      setKnowledgeLoading(false)
    }
  }, [preferredKbFromUrl])

  const loadWorkspaces = useCallback(
    async (targetWorkspace?: string) => {
      docStudioActions.setWorkspaceLoading(true)
      try {
        const list = await listWorkspaces()
        docStudioActions.setWorkspaces(list)
        const preferred =
          targetWorkspace ||
          params.workspaceId ||
          list[0]?.workspaceId ||
          ''
        if (preferred) {
          docStudioActions.setWorkspaceId(preferred)
          await loadWorkspaceFiles(preferred)
        }
      } catch (error) {
        message.error(getErrorMessage(error))
      } finally {
        docStudioActions.setWorkspaceLoading(false)
      }
    },
    [loadWorkspaceFiles, params.workspaceId],
  )

  useEffect(() => {
    loadWorkspaces(params.workspaceId)
  }, [loadWorkspaces, params.workspaceId])

  useEffect(() => {
    loadKnowledgeBases()
  }, [loadKnowledgeBases])

  useEffect(() => {
    if (!isLatexWorkspace && rightTab === 'compile') {
      setRightTab('chat')
    }
  }, [isLatexWorkspace, rightTab])

  // 【Cursor 风格】将每个工作区的打开文件状态持久化�?localStorage
  useEffect(() => {
    if (!snap.workspaceId) return
    const storageKey = `latex_editor_workspace_state_${snap.workspaceId}`
    const payload = {
      openedFiles: snap.openedFiles,
      activeFilePath: snap.activeFilePath,
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(payload))
    } catch (e) {
      // 本地存储失败不影响正常使用，忽略即可
      // eslint-disable-next-line no-console
      console.warn('保存工作区文件状态失�?, e)
    }
  }, [snap.workspaceId, snap.openedFiles, snap.activeFilePath])

  // 移除自动�?workspaceConfig 加载知识库的逻辑
  // 知识库选择应该由用户手动设置，不自动继承工作区配置
  // useEffect(() => {
  //   const workspaceKbRaw =
  //     (snap.workspaceConfig?.knowledge_base_id ??
  //       snap.workspaceConfig?.knowledgeBaseId ??
  //       snap.workspaceConfig?.kb_id) as number | undefined
  //   if (!workspaceKbRaw) return
  //   setSelectedKnowledgeBaseId((current) => current || Number(workspaceKbRaw))
  // }, [snap.workspaceConfig])

  const lastChatMessageId =
    snap.chatMessages.length > 0
      ? snap.chatMessages[snap.chatMessages.length - 1]?.id ?? null
      : null

  // 仅在新增消息时自动滚动到底部，避免用户查看历史时被打�?
  useEffect(() => {
    if (!lastChatMessageId) {
      lastAutoScrollMessageIdRef.current = null
      return
    }
    if (lastAutoScrollMessageIdRef.current === lastChatMessageId) {
      return
    }
    lastAutoScrollMessageIdRef.current = lastChatMessageId
    chatMessagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lastChatMessageId])

  // 处理左侧分割线拖�?
  const handleLeftResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDraggingLeft(true)
  }, [])

  // 处理右侧分割线拖�?
  const handleRightResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsDraggingRight(true)
  }, [])

  // 处理拖拽移动
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (isDraggingLeft) {
        const container = document.querySelector('.doc-studio')
        if (container) {
          const containerRect = container.getBoundingClientRect()
          // 计算相对于容器的位置
          const newWidth = e.clientX - containerRect.left
          if (newWidth >= 200 && newWidth <= 600) {
            setLeftSiderWidth(newWidth)
          }
        }
      }
      if (isDraggingRight) {
        const container = document.querySelector('.doc-studio')
        if (container) {
          const containerRect = container.getBoundingClientRect()
          const newWidth = containerRect.right - e.clientX
          if (newWidth >= 250 && newWidth <= 800) {
            setRightSiderWidth(newWidth)
          }
        }
      }
    }

    const handleMouseUp = () => {
      if (isDraggingLeft) {
        localStorage.setItem('latex_editor_left_sider_width', leftSiderWidth.toString())
        setIsDraggingLeft(false)
      }
      if (isDraggingRight) {
        localStorage.setItem('latex_editor_right_sider_width', rightSiderWidth.toString())
        setIsDraggingRight(false)
      }
    }

    if (isDraggingLeft || isDraggingRight) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isDraggingLeft, isDraggingRight, leftSiderWidth, rightSiderWidth])

  const handleWorkspaceChange = (workspaceId: string) => {
    navigate(`/doc-studio/${workspaceId}`)
  }

  const handleKnowledgeBaseChange = (value: number | string) => {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) {
      setSelectedKnowledgeBaseId(parsed)
    } else {
      setSelectedKnowledgeBaseId(null)
    }
  }

  const handleCreateWorkspace = async () => {
    if (!newWorkspaceName.trim()) {
      message.warning('请输入工作区名称')
      return
    }
    setWorkspaceSubmitting(true)
    try {
      const workspaceConfig =
        newWorkspaceType === 'markdown'
          ? {
              workspace_type: 'doc_studio',
              primary_format: 'markdown',
              supported_formats: ['markdown', 'plaintext'],
              main_file: 'notes.md',
            }
          : {
              workspace_type: 'latex',
              primary_format: 'latex',
              supported_formats: ['latex', 'bib'],
              main_file: 'main.tex',
              bibliography_file: 'references.bib',
            }
      const workspace = await createWorkspace({
        name: newWorkspaceName.trim(),
        config: workspaceConfig,
      })
      setWorkspaceModalOpen(false)
      setNewWorkspaceName('')
      setNewWorkspaceType('latex')
      await loadWorkspaces(workspace.workspaceId)
      message.success('创建成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setWorkspaceSubmitting(false)
    }
  }

  // 查找文件树节点的辅助函数
  const findNode = useCallback((nodes: any, targetPath: string): any => {
    if (!nodes || !Array.isArray(nodes)) return null
    for (const node of nodes) {
      if (node.path === targetPath) return node
      if (node.children) {
        const found = findNode(node.children, targetPath)
        if (found) return found
      }
    }
    return null
  }, [])

  const handleTreeSelect = async (keys: React.Key[]) => {
    const path = String(keys[0] || '')
    if (!path) return
    
    const node = findNode(snap.fileTree, path)
    
    // 如果是目录，不做任何操作（Tree 组件会自动处理展开/折叠�?
    if (node && node.type === 'directory') {
      return
    }
    
    // 如果是文件，并且不是当前激活的文件，则打开�?
    if (snap.activeFilePath !== path) {
      await openFile(path)
    }
  }
  
  // 处理右键菜单
  const handleRightClick = (info: any) => {
    const { event, node } = info
    event.preventDefault()
    event.stopPropagation()
    
    const nodeData = findNode(snap.fileTree, node.key as string)
    
    setContextMenuPath(node.key as string)
    setContextMenuType(nodeData?.type || 'file')
    setContextMenuPosition({ x: event.clientX, y: event.clientY })
    setContextMenuVisible(true)
  }
  
  // 在目录中创建文本文件�?tex, .bib 等）
  const handleCreateFileInDirectory = (directoryPath: string) => {
    setFileModalType('file')
    setFileModalPath(directoryPath + '/')  // 在目录路径后添加斜杠表示在此目录下创�?
    setFileModalContent('')
    setFileModalOpen(true)
    setContextMenuVisible(false)
  }
  
  // 上传文件到指定目�?
  const handleUploadToDirectory = (directoryPath: string) => {
    setContextMenuVisible(false)
    // 创建一个临时的文件输入元素
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '*/*'  // 接受所有文件类�?
    input.onchange = async (e: Event) => {
      const target = e.target as HTMLInputElement
      const file = target.files?.[0]
      if (!file || !snap.workspaceId) return
      
      setUploading(true)
      try {
        console.log('📤 开始上传文�?', {
          fileName: file.name,
          fileSize: file.size,
          directory: directoryPath,
          workspaceId: snap.workspaceId,
        })
        
        // 上传到指定目�?
        const result = await uploadFile({ 
          workspaceId: snap.workspaceId, 
          file,
          directory: directoryPath  // 使用 directory 参数指定目标目录
        })
        
        console.log('�?上传成功，服务器返回:', result)
        message.success(`文件已上传到 ${directoryPath || '根目�?}: ${file.name} (${(file.size / 1024).toFixed(2)} KB)`)
        
        // 等待一小段时间确保后端文件系统已更�?
        await new Promise(resolve => setTimeout(resolve, 500))
        
        // 刷新文件�?
        console.log('🔄 刷新文件�?..')
        await loadWorkspaceFiles(snap.workspaceId, false)
        console.log('�?文件树已刷新')
        
        // 确保目标目录展开，让用户能看到上传的文件
        if (directoryPath && !expandedKeys.includes(directoryPath)) {
          setExpandedKeys(prev => [...prev, directoryPath])
        }
        
        // 如果上传的是文本文件，自动打开
        if (
          file.name.endsWith('.tex') ||
          file.name.endsWith('.bib') ||
          file.name.endsWith('.md') ||
          file.name.endsWith('.markdown') ||
          file.name.endsWith('.txt')
        ) {
          const fullPath = directoryPath ? `${directoryPath}/${file.name}` : file.name
          setTimeout(() => openFile(fullPath), 500)  // 稍微延迟以确保文件树已刷�?
        }
      } catch (error) {
        console.error('�?上传失败:', error)
        message.error(`上传失败: ${getErrorMessage(error)}`)
      } finally {
        setUploading(false)
      }
    }
    input.click()
  }
  
  // 隐藏右键菜单
  useEffect(() => {
    const handleClick = () => setContextMenuVisible(false)
    if (contextMenuVisible) {
      document.addEventListener('click', handleClick)
      return () => document.removeEventListener('click', handleClick)
    }
  }, [contextMenuVisible])

  const handleTabChange = async (key: string) => {
    if (!key) return
    await openFile(key)
  }

  const handleTabEdit = (targetKey: string | React.MouseEvent | React.KeyboardEvent, action: 'add' | 'remove') => {
    if (action === 'remove' && typeof targetKey === 'string') {
      docStudioActions.closeFile(targetKey)
      if (docStudioState.workspaceId && docStudioState.activeFilePath && !docStudioState.files[docStudioState.activeFilePath]) {
        openFile(docStudioState.activeFilePath, true)
      }
    }
  }

  const handleEditorChange = useCallback((value?: string) => {
    if (!snap.activeFilePath) return
    // 防止撤销操作导致空白（只有当值真正变化时才更新）
    const currentContent = snap.files[snap.activeFilePath]?.content ?? ''
    if (value !== currentContent) {
    docStudioActions.updateFileContent(snap.activeFilePath, value ?? '')
    }
  }, [snap.activeFilePath, snap.files])

  const handleEditorMount = useCallback(
    (editorInstance: any) => {
      editorRef.current = editorInstance

      // 立即获得焦点
      editorInstance.focus()

      // 自定义快捷键：Ctrl+A / Ctrl+L
      if (typeof window !== 'undefined' && (window as any).monaco) {
        const monaco = (window as any).monaco

        // Ctrl+A 全选（兜底�?
        editorInstance.addCommand(
          monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyA,
          () => {
            const model = editorInstance.getModel()
            if (model) {
              editorInstance.setSelection(model.getFullModelRange())
            }
          },
        )

        // Ctrl+L 添加当前选中文本为片�?
        editorInstance.addCommand(
          monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyL,
          () => {
            addSelectionSnippet()
          },
        )
      }
    },
    [addSelectionSnippet],
  )

  const handleSave = async () => {
    if (!snap.workspaceId || !snap.activeFilePath) {
      message.warning('请选择工作区和文件')
      return
    }
    try {
      await updateFileContent({
        workspaceId: snap.workspaceId,
        path: snap.activeFilePath,
        content: currentFileBuffer?.content || '',
        encoding: currentFileBuffer?.encoding,
      })
      docStudioActions.markFileSaved(snap.activeFilePath)
      message.success('保存成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handleCompile = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    if (!isLatexWorkspace) {
      message.info('当前工作区非 LaTeX，编译功能不可用')
      return
    }

    // 【Cursor 风格】优先编译当前激活的 .tex 文件，其次使用工作区配置中的 main_file
    const activeTexFile =
      snap.activeFilePath && snap.activeFilePath.toLowerCase().endsWith('.tex')
        ? snap.activeFilePath
        : undefined
    const configuredMainFile =
      (snap.workspaceConfig?.main_file as string | undefined) ||
      (snap.workspaceConfig?.mainFile as string | undefined)

    const mainFile = activeTexFile || configuredMainFile

    if (!mainFile) {
      message.warning('请先打开要编译的 .tex 主文�?)
      return
    }

    // 调试日志：显示即将编译的文件
    console.log('🔨 准备编译文件:', {
      activeFilePath: snap.activeFilePath,
      activeTexFile,
      configuredMainFile,
      finalMainFile: mainFile,
    })

    try {
      const result = await compileWorkspace({
        workspaceId: snap.workspaceId,
        mainFile,
      })
      docStudioActions.setCompileResult(result)
      setRightTab('compile')
      if (result.success) {
        message.success(result.summary || '编译成功')
      } else {
        // 提取所有错误，特别关注"文件未找�?错误
        const allErrors = result.data?.errors || []
        const missingFiles = allErrors
          .filter((err: string) => err.includes('not found') || err.includes('文件未找�?))
          .map((err: string) => {
            const match = err.match(/File `([^']+)'|文件未找�?\s*(.+)/)
            return match ? (match[1] || match[2]) : null
          })
          .filter(Boolean) as string[]
        
        if (missingFiles.length > 0) {
          message.error({
            content: (
              <div>
                <div style={{ marginBottom: 8, fontWeight: 'bold' }}>编译失败：缺少以下文�?/div>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {missingFiles.map((file, idx) => (
                    <li key={idx} style={{ marginBottom: 4 }}>
                      <code>{file}</code>
                    </li>
                  ))}
                </ul>
                <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
                  请检查文件是否已上传到正确位置，或修�?.tex 文件中的引用路径
                </div>
              </div>
            ),
            duration: 10,
          })
        } else {
          const firstError = result.error || allErrors[0]
          message.error(firstError ? `编译失败�?{firstError}` : '编译失败')
        }
      }
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handlePreviewPdf = async () => {
    if (!snap.workspaceId) return
    try {
      const blob = await downloadPdf({ workspaceId: snap.workspaceId })
      const url = URL.createObjectURL(blob)
    window.open(url, '_blank')
      // 延迟释放 URL，确保新标签页已加载
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handleDownloadPdf = async () => {
    if (!snap.workspaceId) return
    try {
      const blob = await downloadPdf({ workspaceId: snap.workspaceId })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'output.pdf'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      message.success('PDF 下载成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handleDownloadCurrentFile = async () => {
    if (!snap.workspaceId || !snap.activeFilePath) return
    try {
      const blob = await downloadFile({
        workspaceId: snap.workspaceId,
        filePath: snap.activeFilePath,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = snap.activeFilePath.split('/').pop() || 'file'
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      message.success('文件下载成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handleDeleteCurrentFile = async () => {
    if (!snap.workspaceId || !snap.activeFilePath) return
    try {
      await deleteFile({
        workspaceId: snap.workspaceId,
        path: snap.activeFilePath,
      })
      message.success('删除成功')
      docStudioActions.setActiveFile('')
      await loadWorkspaceFiles(snap.workspaceId, false)
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }
  
  // 从文件树右键菜单删除文件或文件夹
  const handleDeleteFromTree = async (path: string, type: 'file' | 'directory') => {
    if (!snap.workspaceId) return
    try {
      await deleteFile({
        workspaceId: snap.workspaceId,
        path: path,
      })
      message.success(`删除${type === 'file' ? '文件' : '文件�?}成功`)
      
      // 如果删除的是当前打开的文件，关闭�?
      if (type === 'file' && snap.activeFilePath === path) {
        docStudioActions.setActiveFile('')
      }
      
      // 刷新文件�?
      await loadWorkspaceFiles(snap.workspaceId, false)
      setContextMenuVisible(false)
    } catch (error) {
      message.error(getErrorMessage(error))
      setContextMenuVisible(false)
    }
  }

  const openFileModal = (type: 'file' | 'directory') => {
    setFileModalType(type)
    setFileModalPath('')
    setFileModalContent('')
    setFileModalOpen(true)
  }

  const handleCreateFile = async () => {
    if (!snap.workspaceId || !fileModalPath.trim()) {
      message.warning('请输入路�?)
      return
    }
    setFileSubmitting(true)
    try {
      await createFileOrDirectory({
        workspaceId: snap.workspaceId,
        path: fileModalPath.trim(),
        type: fileModalType,
        content: fileModalType === 'file' ? fileModalContent : undefined,
      })
      setFileModalOpen(false)
      message.success('创建成功')
      await loadWorkspaceFiles(snap.workspaceId, false)
      
      // 确保父目录展开
      const pathParts = fileModalPath.trim().split('/')
      if (pathParts.length > 1) {
        const parentPath = pathParts.slice(0, -1).join('/')
        if (parentPath && !expandedKeys.includes(parentPath)) {
          setExpandedKeys(prev => [...prev, parentPath])
        }
      }
      
      if (fileModalType === 'file') {
        setTimeout(() => openFile(fileModalPath.trim()), 300)
      }
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setFileSubmitting(false)
    }
  }

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  const handleFileInputChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    if (!snap.workspaceId) return
    const file = event.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await uploadFile({ workspaceId: snap.workspaceId, file })
      message.success('上传成功')
      await loadWorkspaceFiles(snap.workspaceId, false)
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  const handleQuickPromptApply = useCallback(
    (preset: PromptPreset) => {
      // 1. 检查编辑器中是否有选中文本（但还没有添加到 selections�?
      const editor = editorRef.current
      let newSelection: SelectionFragment | null = null
      
      if (editor) {
        const selectionRanges = editor.getSelections() || []
        const targetRange = selectionRanges.find((range: any) => !range.isEmpty())
        if (targetRange) {
          const model = editor.getModel()
          if (model) {
            const selectedText = model.getValueInRange(targetRange).trim()
            if (selectedText) {
              const start = model.getOffsetAt(targetRange.getStartPosition())
              const end = model.getOffsetAt(targetRange.getEndPosition())
              // 检查这个选中文本是否已经�?selections �?
              const isAlreadyAdded = selections.some(
                (sel) => sel.start === start && sel.end === end && sel.text === selectedText
              )
              if (!isAlreadyAdded) {
                // 创建新的 selection fragment
                const placeholder = `@selection${selections.length + 1}`
                newSelection = {
                  id: generateId(),
                  start,
                  end,
                  text: selectedText,
                  filePath: snap.activeFilePath,
                  placeholder,
                }
              }
            }
          }
        }
      }
      
      // 2. 如果有新的选中文本，先添加�?selections
      const finalSelections = newSelection 
        ? [...selections, newSelection]
        : selections
      
      const hasSelection = finalSelections.length > 0
      const promptText = typeof preset.prompt === 'function'
        ? preset.prompt(hasSelection)
        : preset.prompt

      if (preset.llm_options) {
        applyLlmOptionsFromConfig({ llm_options: preset.llm_options })
      }
      
      // 3. 生成最终提示词，确保包含所有占位符
      let finalPrompt = promptText
      if (hasSelection && finalSelections.length > 0) {
        const placeholders = finalSelections.map((item) => item.placeholder)
        const missingPlaceholders = placeholders.filter((token) => !promptText.includes(token))
        if (missingPlaceholders.length) {
          // 将占位符添加到提示词中（如果提示词不为空，用空格分隔；否则直接使用占位符�?
          finalPrompt = promptText.trim()
            ? `${promptText} ${missingPlaceholders.join(' ')}`
            : missingPlaceholders.join(' ')
        }
      }
      
      // 4. 更新状�?
      if (newSelection) {
        setSelections(finalSelections)
        // 如果 prompt 当前为空，直接设置为包含占位符的提示�?
        if (!prompt.trim()) {
          setPrompt(finalPrompt)
        } else {
          // 否则追加占位符（如果还没有）
          setPrompt((currentPrompt) => {
            const placeholders = finalSelections.map((item) => item.placeholder)
            const hasAllPlaceholders = placeholders.every(p => currentPrompt.includes(p))
            if (hasAllPlaceholders) {
              return currentPrompt
            } else {
              const missing = placeholders.filter(p => !currentPrompt.includes(p))
              return missing.length > 0 
                ? `${currentPrompt} ${missing.join(' ')}`
                : currentPrompt
            }
          })
        }
      } else {
        setPrompt(finalPrompt)
      }
      
      setTimeout(() => {
        promptInputDivRef.current?.focus()
      }, 0)
    },
    [applyLlmOptionsFromConfig, selections, snap.activeFilePath],
  )

  const handleOpenPresetModal = (preset?: CustomPromptPreset) => {
    if (!preset && !prompt.trim()) {
      message.warning('请输入指令后再保存模�?)
      return
    }
    if (preset) {
      setPresetEditingId(preset.id)
      setPresetName(preset.label)
      setPresetIntent(preset.intent || 'custom')
      setPresetPrompt(preset.prompt)
      setPresetBindModel(Boolean(preset.llm_options))
    } else {
      setPresetEditingId(null)
      setPresetName('')
      setPresetIntent('edit')
      setPresetPrompt(prompt.trim())
      setPresetBindModel(false)
    }
    setPresetModalOpen(true)
  }

  const handleSavePreset = () => {
    const name = presetName.trim()
    const content = presetPrompt.trim()
    if (!name) {
      message.warning('请输入模板名�?)
      return
    }
    if (!content) {
      message.warning('模板内容不能为空')
      return
    }
    const existingIndexById = presetEditingId
      ? customPromptPresets.findIndex((item) => item.id === presetEditingId)
      : -1
    const existingIndexByName = customPromptPresets.findIndex((item) => item.label === name)
    const existingIndex = existingIndexById >= 0 ? existingIndexById : existingIndexByName
    const targetId =
      existingIndexById >= 0 && presetEditingId
        ? presetEditingId
        : existingIndexByName >= 0
          ? customPromptPresets[existingIndexByName].id
          : generateId()
    const existingItem =
      existingIndex >= 0 ? customPromptPresets[existingIndex] : undefined
    const payload: CustomPromptPreset = {
      id: targetId,
      label: name,
      description: '自定义模�?,
      prompt: content,
      intent: presetIntent,
      llm_options: presetBindModel ? llmOptionsConfig : undefined,
      pinned: existingItem?.pinned,
    }
    const next = [...customPromptPresets]
    if (existingIndex >= 0) {
      next[existingIndex] = payload
      message.success('已更新同名模�?)
    } else {
      next.push(payload)
      message.success('模板已保�?)
    }
    persistCustomPromptPresets(next)
    setPresetModalOpen(false)
    setPresetEditingId(null)
    setPresetBindModel(false)
  }

  const handleDeletePreset = (id: string) => {
    const next = customPromptPresets.filter((item) => item.id !== id)
    persistCustomPromptPresets(next)
    message.success('已删除模�?)
    if (presetEditingId === id) {
      setPresetModalOpen(false)
      setPresetEditingId(null)
      setPresetBindModel(false)
    }
  }

  const handleTogglePresetPin = (id: string) => {
    const next = customPromptPresets.map((item) =>
      item.id === id ? { ...item, pinned: !item.pinned } : item,
    )
    persistCustomPromptPresets(next)
  }

  const handleBindSession = async (clear?: boolean) => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    const sessionId = clear ? '' : sessionBindValue.trim()
    if (!clear && !sessionId) {
      message.warning('请输�?Session ID')
      return
    }
    setSessionBindLoading(true)
    try {
      const detail = await bindWorkspaceSession({
        workspaceId: snap.workspaceId,
        sessionId: sessionId || null,
      })
      docStudioActions.setWorkspaceConfig(detail.config)
      setSessionBindValue(detail.config?.session_id || '')
      message.success(clear ? '已解绑会�? : '已绑定会�?)
    } catch (error) {
      message.error('绑定会话失败')
    } finally {
      setSessionBindLoading(false)
    }
  }

  const handleUndoLastApply = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    if (!lastOperationId) {
      message.info('暂无可撤销的修�?)
      return
    }
    setUndoingLastApply(true)
    try {
      const result = await revertOperation({
        workspaceId: snap.workspaceId,
        operationId: lastOperationId,
      })
      const revertedFiles = result.reverted_files || []
      for (const filePath of revertedFiles) {
        if (snap.openedFiles.includes(filePath)) {
          await openFile(filePath, true)
        }
      }
      if (result.deleted_files?.length) {
        await loadWorkspaceFiles(snap.workspaceId, false)
      }
      const affectedCount = (result.reverted_files?.length || 0) + (result.deleted_files?.length || 0)
      if (affectedCount) {
        message.success(`已撤销 ${affectedCount} 个文件的修改`)
      } else {
        message.info('未检测到需要撤销的文�?)
      }
      setLastOperationId(null)
    } catch (error) {
      message.error(`撤销失败�?{getErrorMessage(error)}`)
    } finally {
      setUndoingLastApply(false)
    }
  }

  const loadOperationHistory = useCallback(async () => {
    if (!snap.workspaceId) return
    setOperationHistoryLoading(true)
    try {
      const data = await listOperations({ workspaceId: snap.workspaceId })
      setOperationHistory(Array.isArray(data) ? data : [])
    } catch (error) {
      message.error('加载操作历史失败')
    } finally {
      setOperationHistoryLoading(false)
    }
  }, [snap.workspaceId])

  const refreshSystemStats = useCallback(
    async (silent?: boolean) => {
      if (!snap.workspaceId) return
      if (!silent) {
        setSystemStatsLoading(true)
      }
      try {
        const [metrics, health] = await Promise.all([
          fetchMetricsSummary(),
          fetchLlmHealth(),
        ])
        setMetricsSummary(metrics)
        setLlmHealth(health)
      } catch (error) {
        if (!silent) {
          message.error('加载运行统计失败')
        }
      } finally {
        setSystemStatsLoading(false)
      }
    },
    [snap.workspaceId],
  )

  useEffect(() => {
    if (!operationHistoryOpen) return
    loadOperationHistory()
  }, [operationHistoryOpen, loadOperationHistory])

  useEffect(() => {
    if (!snap.workspaceId) return
    refreshSystemStats(true)
  }, [snap.workspaceId, refreshSystemStats])

  useEffect(() => {
    if (!systemStatusOpen) return
    refreshSystemStats()
  }, [systemStatusOpen, refreshSystemStats])

  const refreshLineChanges = useCallback(() => {
    if (!diffEditorRef.current) return
    const changes = diffEditorRef.current.getLineChanges?.() || []
    setLineChanges(changes)
    setAcceptedLineChanges(new Set())
  }, [])

  const loadDiffContent = useCallback(
    async (diff: DocStudioAPI.FileDiff | undefined) => {
      if (!diff) {
        setResolvedOriginal('')
        setResolvedModified('')
        setLineChanges([])
        return
      }
      if (!snap.workspaceId) return
      const fallbackOriginal = diff.original_content || ''
      const fallbackModified = diff.modified_content || ''

      if (!diffOperationId || !diff.file_path) {
        setResolvedOriginal(fallbackOriginal)
        setResolvedModified(fallbackModified)
        return
      }

      setDiffContentLoading(true)
      try {
        let originalContent = fallbackOriginal
        try {
          const snapshot = await fetchOperationSnapshotFile({
            workspaceId: snap.workspaceId,
            operationId: diffOperationId,
            filePath: diff.file_path,
            version: 'before',
          })
          originalContent = snapshot?.content ?? ''
        } catch (error) {
          originalContent = fallbackOriginal
        }

        let modifiedContent = fallbackModified
        try {
          const current = await fetchFileContent({
            workspaceId: snap.workspaceId,
            path: diff.file_path,
          })
          modifiedContent = current?.content ?? fallbackModified
        } catch (error) {
          modifiedContent = fallbackModified
        }

        setResolvedOriginal(originalContent)
        setResolvedModified(modifiedContent)
      } finally {
        setDiffContentLoading(false)
      }
    },
    [diffOperationId, snap.workspaceId],
  )

  useEffect(() => {
    loadDiffContent(allFileDiffs[currentDiffIndex])
  }, [allFileDiffs, currentDiffIndex, loadDiffContent])

  useEffect(() => {
    if (!resolvedOriginal && !resolvedModified) return
    requestAnimationFrame(() => {
      refreshLineChanges()
    })
  }, [resolvedOriginal, resolvedModified, refreshLineChanges])

  const handleRevertOperation = async (operationId: string) => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    setRevertingOperationId(operationId)
    try {
      const result = await revertOperation({
        workspaceId: snap.workspaceId,
        operationId,
      })
      const revertedFiles = result.reverted_files || []
      for (const filePath of revertedFiles) {
        if (snap.openedFiles.includes(filePath)) {
          await openFile(filePath, true)
        }
      }
      if (result.deleted_files?.length) {
        await loadWorkspaceFiles(snap.workspaceId, false)
      }
      const affectedCount = (result.reverted_files?.length || 0) + (result.deleted_files?.length || 0)
      if (affectedCount) {
        message.success(`已回�?${affectedCount} 个文件`)
      } else {
        message.info('未检测到可回滚的文件')
      }
      if (lastOperationId === operationId) {
        setLastOperationId(null)
      }
      await loadOperationHistory()
    } catch (error) {
      message.error(`回滚失败�?{getErrorMessage(error)}`)
    } finally {
      setRevertingOperationId(null)
    }
  }

  const handleFinalizeDiffs = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    if (!diffOperationId) {
      message.error('缺少操作 ID，无法回�?)
      return
    }
    const rejectedFiles = allFileDiffs
      .filter((_, index) => !acceptedDiffs.has(index))
      .map((diff) => diff.file_path)
      .filter(Boolean)

    if (!rejectedFiles.length) {
      setDiffModalOpen(false)
      setAllFileDiffs([])
      setCurrentDiffIndex(0)
      setAcceptedDiffs(new Set())
      setDiffOperationId(null)
      message.success('已保留全部修�?)
      return
    }

    setDiffReverting(true)
    try {
      const result = await revertOperation({
        workspaceId: snap.workspaceId,
        operationId: diffOperationId,
        files: rejectedFiles,
      })
      const revertedFiles = result.reverted_files || []
      for (const filePath of revertedFiles) {
        if (snap.openedFiles.includes(filePath)) {
          await openFile(filePath, true)
        }
      }
      if (result.deleted_files?.length) {
        await loadWorkspaceFiles(snap.workspaceId, false)
      }
      const affectedCount = (result.reverted_files?.length || 0) + (result.deleted_files?.length || 0)
      message.success(`已撤销 ${affectedCount} 个文件的修改`)
      setDiffModalOpen(false)
      setAllFileDiffs([])
      setCurrentDiffIndex(0)
      setAcceptedDiffs(new Set())
      setDiffOperationId(null)
    } catch (error) {
      message.error(`撤销未接受修改失败：${getErrorMessage(error)}`)
    } finally {
      setDiffReverting(false)
    }
  }

  const handleRejectCurrentDiff = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    const target = allFileDiffs[currentDiffIndex]
    if (!target?.file_path) {
      message.warning('未找到当前文�?)
      return
    }
    if (!diffOperationId) {
      message.error('缺少操作 ID，无法回�?)
      return
    }
    if (diffReverting) return
    setDiffReverting(true)
    try {
      const result = await revertOperation({
        workspaceId: snap.workspaceId,
        operationId: diffOperationId,
        files: [target.file_path],
      })
      const revertedFiles = result.reverted_files || []
      for (const filePath of revertedFiles) {
        if (snap.openedFiles.includes(filePath)) {
          await openFile(filePath, true)
        }
      }
      if (result.deleted_files?.length) {
        await loadWorkspaceFiles(snap.workspaceId, false)
      }
      const affectedCount = (result.reverted_files?.length || 0) + (result.deleted_files?.length || 0)
      message.success(affectedCount ? `已回�?${affectedCount} 个文件` : '已回滚该文件')

      const nextDiffs = allFileDiffs.filter((_, index) => index !== currentDiffIndex)
      const nextAccepted = new Set<number>()
      acceptedDiffs.forEach((index) => {
        if (index === currentDiffIndex) return
        nextAccepted.add(index > currentDiffIndex ? index - 1 : index)
      })
      if (!nextDiffs.length) {
        setDiffModalOpen(false)
        setAllFileDiffs([])
        setCurrentDiffIndex(0)
        setAcceptedDiffs(new Set())
        setDiffOperationId(null)
      } else {
        setAllFileDiffs(nextDiffs)
        setAcceptedDiffs(nextAccepted)
        setCurrentDiffIndex(Math.min(currentDiffIndex, nextDiffs.length - 1))
      }
    } catch (error) {
      message.error(`回滚失败�?{getErrorMessage(error)}`)
    } finally {
      setDiffReverting(false)
    }
  }

  const handleKeepCurrentDiff = () => {
    const nextDiffs = allFileDiffs.filter((_, index) => index !== currentDiffIndex)
    const nextAccepted = new Set<number>()
    acceptedDiffs.forEach((index) => {
      if (index === currentDiffIndex) return
      nextAccepted.add(index > currentDiffIndex ? index - 1 : index)
    })
    if (!nextDiffs.length) {
      setDiffModalOpen(false)
      setAllFileDiffs([])
      setCurrentDiffIndex(0)
      setAcceptedDiffs(new Set())
      setDiffOperationId(null)
    } else {
      setAllFileDiffs(nextDiffs)
      setAcceptedDiffs(nextAccepted)
      setCurrentDiffIndex(Math.min(currentDiffIndex, nextDiffs.length - 1))
    }
  }

  const handleAcceptLineChange = (changeIndex: number) => {
    setAcceptedLineChanges((prev) => {
      const next = new Set(prev)
      next.add(changeIndex)
      return next
    })
  }

  const applyLineChangeRevert = (
    change: any,
    originalText: string,
    modifiedText: string,
  ) => {
    const originalLines = originalText.split('\n')
    const modifiedLines = modifiedText.split('\n')
    const oStart = Number(change?.originalStartLineNumber || 0)
    const oEnd = Number(change?.originalEndLineNumber || 0)
    const mStart = Number(change?.modifiedStartLineNumber || 0)
    const mEnd = Number(change?.modifiedEndLineNumber || 0)

    const hasOriginal = oStart > 0 || oEnd > 0
    const hasModified = mStart > 0 || mEnd > 0
    const originalSlice = hasOriginal ? originalLines.slice(Math.max(oStart - 1, 0), oEnd) : []

    if (!hasOriginal && hasModified) {
      // 插入：删除新增内�?
      const start = Math.max(mStart - 1, 0)
      const count = Math.max(mEnd - mStart + 1, 0)
      modifiedLines.splice(start, count)
    } else if (hasOriginal && !hasModified) {
      // 删除：插回原始内�?
      const insertPos = Math.max((mStart || oStart) - 1, 0)
      modifiedLines.splice(insertPos, 0, ...originalSlice)
    } else {
      // 替换
      const start = Math.max(mStart - 1, 0)
      const count = Math.max(mEnd - mStart + 1, 0)
      modifiedLines.splice(start, count, ...originalSlice)
    }

    return modifiedLines.join('\n')
  }

  const handleRejectLineChange = async (changeIndex: number) => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    const diff = allFileDiffs[currentDiffIndex]
    if (!diff?.file_path) {
      message.warning('未找到当前文�?)
      return
    }
    const change = lineChanges[changeIndex]
    if (!change) {
      message.warning('未找到该修改片段')
      return
    }
    if (diffContentLoading) return

    setDiffReverting(true)
    try {
      const nextContent = applyLineChangeRevert(change, resolvedOriginal, resolvedModified)
      await updateFileContent({
        workspaceId: snap.workspaceId,
        path: diff.file_path,
        content: nextContent,
      })
      setResolvedModified(nextContent)
      setAllFileDiffs((prev) =>
        prev.map((item, idx) =>
          idx === currentDiffIndex
            ? { ...item, modified_content: nextContent, is_truncated: false }
            : item,
        ),
      )
      message.success('已回滚该处修�?)
      requestAnimationFrame(() => {
        refreshLineChanges()
      })
    } catch (error) {
      message.error(`回滚失败�?{getErrorMessage(error)}`)
    } finally {
      setDiffReverting(false)
    }
  }

  const handleExportPresets = () => {
    if (!customPromptPresets.length) {
      message.warning('暂无可导出的自定义模�?)
      return
    }
    downloadJson('doc-studio-presets.json', {
      exported_at: new Date().toISOString(),
      presets: customPromptPresets,
    })
  }

  const handleImportPresets = () => {
    presetFileInputRef.current?.click()
  }

  const handlePresetFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const rawPresets = Array.isArray(parsed) ? parsed : parsed?.presets
      if (!Array.isArray(rawPresets)) {
        message.error('模板文件格式无效')
        return
      }
      const allowedIntents: PromptIntent[] = ['edit', 'suggest', 'qa', 'citation', 'custom']
      const sanitized: CustomPromptPreset[] = rawPresets
        .filter((item: any) => item && typeof item.label === 'string' && typeof item.prompt === 'string')
        .map((item: any) => ({
          id: item.id || generateId(),
          label: item.label,
          description: item.description || '自定义模�?,
          prompt: item.prompt,
          intent: allowedIntents.includes(item.intent) ? item.intent : 'custom',
          llm_options: item.llm_options,
          pinned: Boolean(item.pinned),
        }))
      if (!sanitized.length) {
        message.warning('未找到可导入的模�?)
        return
      }
      const merged = [...customPromptPresets]
      const byId = new Map(merged.map((item) => [item.id, item]))
      const byLabel = new Map(merged.map((item) => [item.label, item]))
      let added = 0
      let updated = 0
      sanitized.forEach((item) => {
        const existing = byId.get(item.id) || byLabel.get(item.label)
        if (existing) {
          Object.assign(existing, item)
          updated += 1
        } else {
          merged.push(item)
          added += 1
        }
      })
      persistCustomPromptPresets(merged)
      message.success(`模板导入完成：新�?${added} 条，更新 ${updated} 条`)
    } catch (error) {
      console.warn('Failed to import presets', error)
      message.error('模板导入失败')
    } finally {
      event.target.value = ''
    }
  }

  const closeAsyncStream = useCallback(() => {
    if (asyncStreamRef.current) {
      asyncStreamRef.current.close()
      asyncStreamRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => {
      closeAsyncStream()
    }
  }, [closeAsyncStream])

  useEffect(() => {
    if (!asyncMode) {
      closeAsyncStream()
      setAsyncRunStatus('idle')
    }
  }, [asyncMode, closeAsyncStream])

  const pushChatMessage = (payload: Omit<DocStudioChatMessage, 'id' | 'createdAt'>) => {
    docStudioActions.appendChatMessage(payload)
  }

  const handleAgentResponse = useCallback(
    async (response: DocStudioAPI.AgentResponse, traceId: string) => {
      const changeCount = response.changes?.length || 0
      const operationId = response.operation_id || null
      const hasChanges = Boolean(
        (response.file_diffs && response.file_diffs.length > 0) ||
          (response.changes && response.changes.length > 0),
      )
      pushChatMessage({
        role: 'agent',
        content: response.execution_history?.[response.execution_history.length - 1]?.content
          ? response.execution_history[response.execution_history.length - 1].content
          : `完成，检测到 ${changeCount} 处变更`,
        meta: {
          changes: response.changes,
          traceId: response.trace_id || traceId,
          operationId,
        },
      })
      docStudioActions.setExecutionHistory(response.execution_history)
      docStudioActions.setAgentStatus({
        intentType: response.intent_type,
        intentConfidence: response.intent_confidence ?? undefined,
        plan: response.plan || undefined,
        warnings: response.warnings || [],
        traceId: response.trace_id || traceId,
        operationId,
      })
      if (operationId && hasChanges) {
        setLastOperationId(operationId)
      }
      if (response.file_diffs && response.file_diffs.length > 0) {
        setAllFileDiffs(response.file_diffs)
        setCurrentDiffIndex(0)
        setAcceptedDiffs(new Set(response.file_diffs.map((_, index) => index)))
        setDiffOperationId(operationId)
        setDiffModalOpen(true)
      } else if (response.changes && response.changes.length > 0) {
        const affectedFiles = Array.from(
          new Set(
            (response.changes || [])
              .map((change) => change.file)
              .filter(Boolean) as string[],
          ),
        )
        for (const filePath of affectedFiles) {
          await openFile(filePath)
        }
        message.info(`已应�?${changeCount} 处修改`)
      }
      refreshSystemStats(true)
      setChatLoading(false)
    },
    [openFile, pushChatMessage, refreshSystemStats],
  )

  const handleFeedbackSubmit = useCallback(
    async (messageId: string, traceId: string | undefined, rating: DocStudioAPI.AgentFeedbackRating) => {
      if (!traceId) {
        message.warning('该回复缺�?Trace ID，无法反�?)
      return
    }
      const target = snap.chatMessages.find((item) => item.id === messageId)
      if (target?.meta?.feedback === rating) {
        message.success('已记录该反馈')
        return
      }
      setFeedbackSubmitting((prev) => ({ ...prev, [messageId]: true }))
      try {
        await sendAgentFeedback({ traceId, rating })
        docStudioActions.setMessageFeedback(messageId, rating)
        message.success('感谢反馈�?)
      } catch (error) {
        message.error(getErrorMessage(error))
      } finally {
        setFeedbackSubmitting((prev) => ({ ...prev, [messageId]: false }))
      }
    },
    [snap.chatMessages],
  )

  const handleSend = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作�?)
      return
    }
    if (!prompt.trim()) {
      message.warning('请输入指�?)
      return
    }
    const traceId =
      window.crypto?.randomUUID?.() ??
      `trace-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
    
    // 【Cursor 风格改进】将 @selectionX 替换为更自然的引用，�?LLM 更容易理�?
    let finalPrompt = prompt.trim()
    const contextPayload: Record<string, any> = {}
    
    if (snap.activeFilePath) {
      contextPayload.file_path = snap.activeFilePath
    }
    
    if (selections.length > 0) {
      // 构建结构化的 selections 上下文（包含完整文本和元信息�?
      contextPayload.selections = selections.map((sel, idx) => ({
        id: idx + 1,
        start: sel.start,
        end: sel.end,
        text: sel.text,
        file_path: sel.filePath || snap.activeFilePath,
        placeholder: sel.placeholder || `@selection${idx + 1}`,
      }))
      
      // 为了向后兼容，保留第一个选择的快捷引�?
      contextPayload.selection = {
        start: selections[0].start,
        end: selections[0].end,
        text: selections[0].text,
      }
      
      // 【Cursor 风格】将 @selectionX 替换为更自然的中文引�?
      const originalPrompt = prompt.trim()
      selections.forEach((sel, idx) => {
        const placeholder = sel.placeholder || `@selection${idx + 1}`
        const naturalRef = `【片�?{idx + 1}】`
        // 全局替换占位�?
        finalPrompt = finalPrompt.replace(new RegExp(escapeRegExp(placeholder), 'g'), naturalRef)
      })
      
      // 📊 保存日志，供 UI 显示（方便调试和理解系统行为�?
      setLastPromptLog({
        original: originalPrompt,
        final: finalPrompt,
        selectionsCount: selections.length,
        timestamp: new Date().toLocaleTimeString('zh-CN'),
      })
      
      // 📊 控制台日志：显示替换后的最�?prompt
      console.group('🚀 发送给 Agent 的完整信�?)
      console.log('原始 Prompt:', originalPrompt)
      console.log('替换�?Prompt:', finalPrompt)
      console.log('选中片段�?', selections.length)
      console.log('Context Payload:', contextPayload)
      console.groupEnd()
    }
    
    pushChatMessage({ role: 'user', content: finalPrompt, meta: { traceId } })
    setChatLoading(true)
    try {
      docStudioActions.setAgentStatus({ intentType: undefined, plan: undefined, warnings: [] })
      const knowledgeBaseId = selectedKnowledgeBaseId ?? undefined
      const knowledgeBaseName = knowledgeBaseId ? selectedKnowledgeBase?.name : undefined
      if (asyncMode) {
        closeAsyncStream()
        setAsyncRunStatus('running')
        const asyncResult = await runAgentTaskAsync(
          {
            workspaceId: snap.workspaceId,
            userIntent: finalPrompt,
            context: Object.keys(contextPayload).length ? contextPayload : undefined,
            knowledgeBaseId,
            knowledgeBaseName,
            options: Object.keys(llmOptions).length ? llmOptions : undefined,
          },
          { headers: { 'X-Trace-Id': traceId } },
        )
        if (!asyncResult.runId) {
          throw new Error('Failed to start async run')
        }
        setAsyncRunId(asyncResult.runId)
        const url = getAgentAsyncEventsUrl(snap.workspaceId, asyncResult.runId)
        const source = new EventSource(url)
        asyncStreamRef.current = source

        source.addEventListener('status', (event) => {
          const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
          if (payload?.warning) {
            message.warning(payload.warning)
          }
          if (payload.status === 'running') {
            setAsyncRunStatus('running')
          } else if (payload.status === 'succeeded') {
            setAsyncRunStatus('succeeded')
          } else if (payload.status === 'failed') {
            setAsyncRunStatus('failed')
          }
        })
        source.addEventListener('plan', (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
            if (payload?.steps) {
              const current = docStudioState.agentStatus
              docStudioActions.setAgentStatus({
                intentType: current.intentType,
                intentConfidence: current.intentConfidence,
                warnings: current.warnings,
                traceId: current.traceId,
                operationId: current.operationId,
                plan: payload,
              })
            }
          } catch (error) {
            console.warn('Failed to parse plan event', error)
          }
        })
        source.addEventListener('step', (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
            if (payload?.step) {
              const next = [...docStudioState.executionHistory, payload.step]
              docStudioActions.setExecutionHistory(next)
            }
            if (payload?.plan) {
              const current = docStudioState.agentStatus
              docStudioActions.setAgentStatus({
                intentType: current.intentType,
                intentConfidence: current.intentConfidence,
                warnings: current.warnings,
                traceId: current.traceId,
                operationId: current.operationId,
                plan: payload.plan,
              })
            }
          } catch (error) {
            console.warn('Failed to parse step event', error)
          }
        })
        source.addEventListener('finish', (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
            if (payload?.plan) {
              const current = docStudioState.agentStatus
              docStudioActions.setAgentStatus({
                intentType: current.intentType,
                intentConfidence: current.intentConfidence,
                warnings: current.warnings,
                traceId: current.traceId,
                operationId: current.operationId,
                plan: payload.plan,
              })
            }
          } catch (error) {
            console.warn('Failed to parse finish event', error)
          }
        })
        source.addEventListener('result', async (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
            if (payload?.result) {
              await handleAgentResponse(payload.result, traceId)
            }
            setAsyncRunStatus('succeeded')
          } catch (error) {
            message.error('解析异步结果失败')
          } finally {
            closeAsyncStream()
          }
        })
        source.addEventListener('run_error', (event) => {
          try {
            const payload = JSON.parse((event as MessageEvent<string>).data || '{}')
            message.error(payload?.error || '异步任务失败')
          } catch (error) {
            message.error('异步任务失败')
          } finally {
            setAsyncRunStatus('failed')
            setChatLoading(false)
            closeAsyncStream()
          }
        })
        source.onerror = () => {
          setAsyncRunStatus('failed')
          setChatLoading(false)
          closeAsyncStream()
        }
        return
      }

      const response = await runAgentTask(
        {
          workspaceId: snap.workspaceId,
          userIntent: finalPrompt,
          context: Object.keys(contextPayload).length ? contextPayload : undefined,
          knowledgeBaseId,
          knowledgeBaseName,
          options: Object.keys(llmOptions).length ? llmOptions : undefined,
        },
        { headers: { 'X-Trace-Id': traceId } },
      )
      await handleAgentResponse(response, traceId)
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setPrompt('')
      setSelections([])
      if (!asyncMode) {
        setChatLoading(false)
      }
    }
  }

  return (
    <>
    <div className="doc-studio-page">
        <Layout className="doc-studio">
          <Sider width={leftSiderWidth} className="doc-studio__sider">
            <div className="doc-studio__workspace">
              <Select
                value={snap.workspaceId || undefined}
                className="doc-studio__workspace-select"
                placeholder="选择工作�?
                options={workspaceOptions}
                loading={snap.workspaceLoading}
                onChange={handleWorkspaceChange}
              />
              <Space size="small">
                <Button
                  icon={<PlusOutlined />}
                  size="small"
                  onClick={() => setWorkspaceModalOpen(true)}
                />
                <Button
                  icon={<ReloadOutlined />}
                  size="small"
                  onClick={() => loadWorkspaces(snap.workspaceId)}
                />
              </Space>
            </div>
            <div className="doc-studio__knowledge">
              <Select
                value={selectedKnowledgeBaseId ?? undefined}
                className="doc-studio__knowledge-select"
                placeholder="选择知识�?
                options={knowledgeBaseOptions}
                loading={knowledgeLoading}
                onChange={handleKnowledgeBaseChange}
                disabled={knowledgeLoading}
                allowClear
                showSearch
                optionFilterProp="label"
                notFoundContent={
                  knowledgeLoading ? (
                    <Spin size="small" />
                  ) : (
                    <span>暂无知识�?/span>
                  )
                }
              />
              <Button
                icon={<ReloadOutlined />}
                size="small"
                loading={knowledgeLoading}
                onClick={loadKnowledgeBases}
              />
            </div>
            <div className="doc-studio__session">
              <Input
                value={sessionBindValue}
                className="doc-studio__session-input"
                placeholder="绑定 Session ID（可选）"
                onChange={(event) => setSessionBindValue(event.target.value)}
              />
              <Space size="small" className="doc-studio__session-actions">
                <Button
                  size="small"
                  type="primary"
                  ghost
                  loading={sessionBindLoading}
                  onClick={() => handleBindSession(false)}
                >
                  绑定
                </Button>
                <Button
                  size="small"
                  loading={sessionBindLoading}
                  onClick={() => handleBindSession(true)}
                >
                  解绑
                </Button>
              </Space>
            </div>
            <div className="doc-studio__web-search">
              <Switch
                checked={webSearchEnabled}
                loading={webSearchSaving}
                onChange={(value) => setWebSearchEnabled(value)}
              />
              <Text type="secondary">Web 搜索</Text>
              <Tooltip title="需要配�?WEB_SEARCH_API_KEY 才能生效">
                <Tag color={webSearchEnabled ? 'green' : 'default'}>
                  {webSearchEnabled ? '已启�? : '未启�?}
                </Tag>
              </Tooltip>
            </div>
            <div className="doc-studio__model">
              <Select
                value={llmProvider}
                className="doc-studio__model-select"
                placeholder="模型提供�?
                options={[
                  { label: '自动选择', value: 'auto' },
                  { label: 'DashScope', value: 'dashscope' },
                  { label: 'OpenAI', value: 'openai' },
                ]}
                onChange={(value) => setLlmProvider(value)}
              />
              <Input
                value={llmModel}
                className="doc-studio__model-input"
                placeholder="模型名称（留�?默认�?
                onChange={(event) => setLlmModel(event.target.value)}
              />
            </div>
            <div className="doc-studio__model-advanced">
              <InputNumber
                value={llmTemperature ?? undefined}
                min={0}
                max={2}
                step={0.1}
                placeholder="温度(可�?"
                onChange={(value) => setLlmTemperature(typeof value === 'number' ? value : null)}
              />
              <InputNumber
                value={llmMaxTokens ?? undefined}
                min={128}
                max={16384}
                step={256}
                placeholder="Max tokens(可�?"
                onChange={(value) => setLlmMaxTokens(typeof value === 'number' ? value : null)}
              />
            </div>
            <div className="doc-studio__file-actions">
              <Button
                icon={<FileAddOutlined />}
                block
                size="small"
                onClick={() => openFileModal('file')}
              >
                新建文件
              </Button>
              <Button
                icon={<FolderAddOutlined />}
                block
                size="small"
                onClick={() => openFileModal('directory')}
              >
                新建文件�?
              </Button>
              <Button
                icon={<UploadOutlined />}
                block
                size="small"
                loading={uploading}
                onClick={handleUploadClick}
              >
                上传文件
              </Button>
              <Button
                icon={<ReloadOutlined />}
                block
                size="small"
                onClick={async () => {
                  if (!snap.workspaceId) return
                  await loadWorkspaceFiles(snap.workspaceId, false)
                  message.success('文件树已刷新')
                }}
              >
                刷新文件�?
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                style={{ display: 'none' }}
                onChange={handleFileInputChange}
              />
              <input
                ref={presetFileInputRef}
                type="file"
                accept="application/json"
                style={{ display: 'none' }}
                onChange={handlePresetFileChange}
              />
            </div>
            <div className="doc-studio__tree-wrapper">
              {treeData.length ? (
                <Tree
                  selectedKeys={snap.activeFilePath ? [snap.activeFilePath] : []}
                  expandedKeys={expandedKeys}
                  onExpand={(keys) => setExpandedKeys(keys)}
                  showIcon
                  treeData={treeData}
                  onSelect={handleTreeSelect}
                  onRightClick={handleRightClick}
                />
              ) : (
                <Empty
                  description="暂无文件"
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </div>
          </Sider>
          {/* 左侧分割�?*/}
          <div
            className={`doc-studio__resizer doc-studio__resizer--left ${isDraggingLeft ? 'doc-studio__resizer--dragging' : ''}`}
            onMouseDown={handleLeftResizeStart}
          />
          <Layout>
            <Header className="doc-studio__header">
              <div className="doc-studio__header-info">
                <Text className="doc-studio__file-path" ellipsis>
                  {snap.activeFilePath || '请选择文件'}
                </Text>
                {currentFileBuffer?.dirty && <Tag color="gold">未保�?/Tag>}
              </div>
              <Space>
                <Button
                  icon={<DownloadOutlined />}
                  onClick={handleDownloadCurrentFile}
                  disabled={!snap.activeFilePath}
                >
                  下载
                </Button>
                <Popconfirm
                  title="确定删除当前文件�?
                  onConfirm={handleDeleteCurrentFile}
                  disabled={!snap.activeFilePath}
                >
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    disabled={!snap.activeFilePath}
                  >
                    删除
                  </Button>
                </Popconfirm>
                <Button
                  type="default"
                  icon={<SaveOutlined />}
                  onClick={handleSave}
                  disabled={!snap.activeFilePath || !currentFileBuffer?.dirty}
                >
                  保存
                </Button>
                <Button
                  icon={<SyncOutlined />}
                  onClick={handleUndoLastApply}
                  disabled={!lastOperationId || undoingLastApply}
                >
                  撤销上次操作
                </Button>
                <Button icon={<HistoryOutlined />} onClick={() => setOperationHistoryOpen(true)}>
                  操作历史
                </Button>
                <Button icon={<BarChartOutlined />} onClick={() => setSystemStatusOpen(true)}>
                  系统状�?
                </Button>
                {isLatexWorkspace && (
                  <Button
                    type="primary"
                    ghost
                    icon={<PlayCircleOutlined />}
                    onClick={handleCompile}
                    disabled={!snap.workspaceId}
                  >
                    编译
                  </Button>
                )}
              </Space>
            </Header>
            <Content className="doc-studio__content">
              {snap.openedFiles.length ? (
                <div className="doc-studio__editor-wrapper">
                  <Tabs
                    type="editable-card"
                    hideAdd
                    size="small"
                    activeKey={snap.activeFilePath || undefined}
                    onChange={handleTabChange}
                    onEdit={handleTabEdit}
                    items={snap.openedFiles.map((path) => ({
                      key: path,
                      label: (
                        <span>
                          {path.split('/').pop()}
                          {snap.files[path]?.dirty ? '*' : ''}
                        </span>
                      ),
                    }))}
                  />
                  <div style={{ flex: 1, overflow: 'hidden', minHeight: 0 }}>
                  {snap.activeFilePath ? (
                    <Editor
                      key={snap.activeFilePath}
                      theme="vs-dark"
                      height="100%"
                      language={resolveEditorLanguage(snap.activeFilePath)}
                      loading={<Spin />}
                      value={currentFileBuffer?.content || ''}
                      onChange={handleEditorChange}
                      onMount={handleEditorMount}
                      options={{
                        readOnly: currentFileBuffer?.loading,
                        minimap: { enabled: false },
                        fontSize: 14,
                        wordWrap: 'on',
                        automaticLayout: true,
                        selectOnLineNumbers: true,
                        scrollBeyondLastLine: false,
                        // 确保所有标准编辑快捷键都启用（Ctrl+A, Ctrl+C, Ctrl+V, Ctrl+Z 等）
                        // Monaco Editor 默认已启用这些快捷键，无需额外配置
                      }}
                    />
                  ) : (
                    <Empty description="选择一个文件以开始编�? />
                  )}
                  </div>
                </div>
              ) : (
                <Empty description="选择一个文件以开始编�? />
              )}
            </Content>
          </Layout>
          {/* 右侧分割�?*/}
          <div
            className={`doc-studio__resizer doc-studio__resizer--right ${isDraggingRight ? 'doc-studio__resizer--dragging' : ''}`}
            onMouseDown={handleRightResizeStart}
          />
          <Sider width={rightSiderWidth} className="doc-studio__right">
            {/* 自定�?Tab 实现，解�?Ant Design Tabs 滚动问题 */}
            <div className="doc-studio__custom-tabs">
              <div className="doc-studio__custom-tabs-nav">
                <button
                  className={`doc-studio__custom-tab ${rightTab === 'chat' ? 'doc-studio__custom-tab--active' : ''}`}
                  onClick={() => setRightTab('chat')}
                >
                  Agent 聊天
                </button>
                <button
                  className={`doc-studio__custom-tab ${rightTab === 'history' ? 'doc-studio__custom-tab--active' : ''}`}
                  onClick={() => setRightTab('history')}
                >
                  执行历史
                </button>
                {isLatexWorkspace && (
                  <button
                    className={`doc-studio__custom-tab ${rightTab === 'compile' ? 'doc-studio__custom-tab--active' : ''}`}
                    onClick={() => setRightTab('compile')}
                  >
                    编译结果
                  </button>
                )}
              </div>
              <div className="doc-studio__custom-tabs-content">
                {/* Chat Panel */}
                <div className="doc-studio__chat-panel" style={{ display: rightTab === 'chat' ? 'flex' : 'none' }}>
                      {/* 📊 Prompt 日志面板（调试用�?*/}
                      {lastPromptLog && (
                        <div className="doc-studio__prompt-log">
                          <div 
                            className="doc-studio__prompt-log-header"
                            onClick={() => setShowPromptLog(!showPromptLog)}
                            style={{ cursor: 'pointer', userSelect: 'none' }}
                          >
                            <span style={{ marginRight: 8 }}>
                              {showPromptLog ? '�? : '�?}
                            </span>
                            <span style={{ fontWeight: 500 }}>
                              📊 最后发送的 Prompt ({lastPromptLog.timestamp})
                            </span>
                            <span style={{ marginLeft: 'auto', fontSize: 12, opacity: 0.7 }}>
                              {lastPromptLog.selectionsCount} 个片�?
                            </span>
                          </div>
                          {showPromptLog && (
                            <div className="doc-studio__prompt-log-content">
                              <div style={{ marginBottom: 12 }}>
                                <Text type="secondary" style={{ fontSize: 12 }}>原始 Prompt�?/Text>
                                <div style={{ 
                                  background: '#f5f5f5', 
                                  padding: 8, 
                                  borderRadius: 4, 
                                  marginTop: 4,
                                  fontSize: 13,
                                  fontFamily: 'monospace',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-word'
                                }}>
                                  {lastPromptLog.original}
                                </div>
                              </div>
                              <div>
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  替换�?Prompt（发�?Agent 的实际内容）�?
                                </Text>
                                <div style={{ 
                                  background: '#e6f7ff', 
                                  padding: 8, 
                                  borderRadius: 4, 
                                  marginTop: 4,
                                  fontSize: 13,
                                  fontFamily: 'monospace',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-word',
                                  border: '1px solid #91d5ff'
                                }}>
                                  {lastPromptLog.final}
                                </div>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                      <div className="doc-studio__mode-switch">
                        <Text type="secondary" style={{ fontSize: 13 }}>不知道怎么问？试试这些示例�?/Text>
                        <Space size="small" wrap>
                          <Switch checked={asyncMode} onChange={setAsyncMode} />
                          <Text type="secondary">异步模式（长任务可恢复）</Text>
                          {asyncRunStatus !== 'idle' && (
                            <Tag color={asyncRunStatus === 'running' ? 'blue' : asyncRunStatus === 'failed' ? 'red' : 'green'}>
                              {asyncRunStatus === 'running' ? '进行�? : asyncRunStatus === 'failed' ? '失败' : '完成'}
                            </Tag>
                          )}
                          {asyncRunId && (
                            <Tag color="default">Run: {asyncRunId.slice(0, 8)}</Tag>
                          )}
                        </Space>
                        <Segmented
                          size="small"
                          value={presetFilter}
                          onChange={(value) => setPresetFilter(value as 'all' | PromptIntent)}
                          options={[
                            { label: '全部', value: 'all' },
                            { label: '编辑', value: 'edit' },
                            { label: '建议', value: 'suggest' },
                            { label: '问答', value: 'qa' },
                            { label: '引用', value: 'citation' },
                            { label: '自定�?, value: 'custom' },
                          ]}
                        />
                        <Space wrap size={[8, 8]} className="doc-studio__preset-actions">
                          <Input
                            allowClear
                            value={presetSearch}
                            placeholder="搜索模板"
                            onChange={(event) => setPresetSearch(event.target.value)}
                            style={{ width: 200 }}
                          />
                          <Button size="small" icon={<UploadOutlined />} onClick={handleImportPresets}>
                            导入模板
                          </Button>
                          <Button
                            size="small"
                            icon={<DownloadOutlined />}
                            disabled={!customPromptPresets.length}
                            onClick={handleExportPresets}
                          >
                            导出模板
                          </Button>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            �?{orderedPromptPresets.length} 个模�?
                          </Text>
                        </Space>
                        <div className="doc-studio__prompt-groups">
                          {groupedPromptPresets.map((group) => (
                            <div key={group.intent} className="doc-studio__prompt-group">
                              <div className="doc-studio__prompt-group-title">
                                <Text strong>{intentTagMap[group.intent]?.label || '模板'}</Text>
                                <Tag>{group.items.length}</Tag>
                              </div>
                              <Space wrap size={[8, 8]} className="doc-studio__quick-prompts">
                                {group.items.map((preset) => {
                                  const isEdit = preset.intent === 'edit' || preset.intent === 'citation'
                                  const tooltipText =
                                    preset.description ||
                                    (preset.isCustom ? '自定义模�? : '')
                                  const contentWarning = isEdit
                                    ? '⚠️ 会直接修改文�?
                                    : '💡 只给建议，不修改文件'
                                  const button = (
                                    <Button
                                      size="small"
                                      type={isEdit ? 'primary' : 'default'}
                                      onClick={() => handleQuickPromptApply(preset)}
                                      style={{
                                        fontSize: 13,
                                        height: 28,
                                        padding: '0 12px',
                                      }}
                                    >
                                      {preset.label}
                                    </Button>
                                  )
                                  return (
                                    <span
                                      key={preset.id || preset.label}
                                      className="doc-studio__quick-prompt-item"
                                    >
                                      <Tooltip
                                        title={
                                          <div>
                                            <div style={{ fontWeight: 500 }}>{tooltipText}</div>
                                            <div style={{ fontSize: '11px', marginTop: 4, opacity: 0.85 }}>
                                              {contentWarning}
                                            </div>
                                          </div>
                                        }
                                      >
                                        {button}
                                      </Tooltip>
                                      <Tag color={intentTagMap[preset.intent]?.color || 'default'}>
                                        {intentTagMap[preset.intent]?.label || '模板'}
                                      </Tag>
                                      {preset.isCustom && preset.id && (
                                        <Button
                                          size="small"
                                          type={preset.pinned ? 'primary' : 'text'}
                                          icon={preset.pinned ? <PushpinFilled /> : <PushpinOutlined />}
                                          onClick={() => handleTogglePresetPin(preset.id!)}
                                          className="doc-studio__quick-prompt-delete"
                                        />
                                      )}
                                      {preset.isCustom && (
                                        <Button
                                          size="small"
                                          type="text"
                                          icon={<EditOutlined />}
                                          onClick={() => handleOpenPresetModal(preset as CustomPromptPreset)}
                                          className="doc-studio__quick-prompt-delete"
                                        />
                                      )}
                                      {preset.isCustom && preset.id && (
                                        <Popconfirm
                                          title="确认删除该模板？"
                                          onConfirm={() => handleDeletePreset(preset.id!)}
                                        >
                                          <Button
                                            size="small"
                                            type="text"
                                            icon={<CloseOutlined />}
                                            className="doc-studio__quick-prompt-delete"
                                          />
                                        </Popconfirm>
                                      )}
                                    </span>
                                  )
                                })}
                              </Space>
                            </div>
                          ))}
                        </div>
                      </div>
                      {(intentStatus || planTotalSteps > 0) && (
                        <div className="doc-studio__agent-status">
                          <div className="doc-studio__agent-status-tags">
                            {intentStatus && (
                              <Tag color={intentTagMap[intentStatus]?.color ?? 'blue'}>
                                {intentTagMap[intentStatus]?.label ?? intentStatus.toUpperCase()}
                              </Tag>
                            )}
                            {typeof snap.agentStatus.intentConfidence === 'number' && (
                              <Tag color="gold">
                                置信�?{(snap.agentStatus.intentConfidence * 100).toFixed(0)}%
                              </Tag>
                            )}
                            {planTotalSteps > 0 && (
                              <Tag color="geekblue">
                                计划进度 {planCompletedSteps}/{planTotalSteps}
                              </Tag>
                            )}
                            {snap.agentStatus.traceId && (
                              <Tag color="default">Trace ID: {snap.agentStatus.traceId}</Tag>
                            )}
                            {snap.agentStatus.operationId && (
                              <Tag color="default">Op: {snap.agentStatus.operationId.slice(0, 8)}</Tag>
                            )}
                          </div>
                          {planTotalSteps > 0 && (
                            <div className="doc-studio__agent-plan">
                              <Progress percent={planPercent} size="small" showInfo={false} />
                              <Text type="secondary">
                                {planCompletedSteps >= planTotalSteps
                                  ? '计划已完成，准备总结'
                                  : `下一步：${planNextStep}`}
                              </Text>
                            </div>
                          )}
                          {planStatus?.notes && (
                            <Text type="secondary">{planStatus.notes}</Text>
                          )}
                        </div>
                      )}
                      {agentWarnings.map((warning, index) => (
                        <Alert
                          key={`agent-warning-${index}`}
                          type="warning"
                          showIcon
                          message="Agent 提示"
                          description={warning}
                          style={{ marginBottom: 8 }}
                        />
                      ))}
                      <div className="doc-studio__chat-messages">
                        {snap.chatMessages.length ? (
                          <>
                            {snap.chatMessages.map((msg) => (
                            <div
                              key={msg.id}
                              className={`doc-studio__chat-message doc-studio__chat-message--${msg.role}`}
                            >
                              <div className="doc-studio__chat-meta">
                                <Text strong>{msg.role === 'user' ? '�? : 'Agent'}</Text>
                                <Text type="secondary">
                                  {new Date(msg.createdAt).toLocaleTimeString()}
                                </Text>
                              </div>
                                <div className="doc-studio__chat-content">
                                  {msg.role === 'agent' ? (
                                    <ReactMarkdown
                                      remarkPlugins={[remarkGfm, remarkMath]}
                                      rehypePlugins={[rehypeKatex, rehypeRaw]}
                                    >
                                      {msg.content}
                                    </ReactMarkdown>
                                  ) : (
                                    msg.content
                                  )}
                            </div>
                              {msg.role === 'agent' && msg.meta?.traceId && (
                                <div className="doc-studio__chat-feedback">
                                  <Tooltip title="这个回答有帮�?>
                                    <Button
                                      size="small"
                                      type={msg.meta?.feedback === 'thumbs_up' ? 'primary' : 'default'}
                                      icon={<LikeOutlined />}
                                      onClick={() =>
                                        handleFeedbackSubmit(msg.id, msg.meta?.traceId, 'thumbs_up')
                                      }
                                      loading={!!feedbackSubmitting[msg.id]}
                                    />
                                  </Tooltip>
                                  <Tooltip title="这个回答没有帮助">
                                    <Button
                                      size="small"
                                      type={msg.meta?.feedback === 'thumbs_down' ? 'primary' : 'default'}
                                      icon={<DislikeOutlined />}
                                      onClick={() =>
                                        handleFeedbackSubmit(msg.id, msg.meta?.traceId, 'thumbs_down')
                                      }
                                      loading={!!feedbackSubmitting[msg.id]}
                                    />
                                  </Tooltip>
                                </div>
                              )}
                              </div>
                            ))}
                            <div ref={chatMessagesEndRef} />
                          </>
                        ) : (
                          <Empty
                            description="暂无对话"
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                          />
                        )}
                      </div>
                      <div className="doc-studio__chat-input">
                        {selections.length > 0 && (
                          <div className="doc-studio__selection-preview">
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              已选中 {selections.length} 个片段（�?{totalSelectionChars} 个字符）
                            </Text>
                            <div className="doc-studio__selection-preview-list">
                              {selections.map((sel) => (
                                <div key={sel.id} className="doc-studio__selection-preview-item">
                                  <div className="doc-studio__selection-preview-item-head">
                                    <Tag
                                      color="blue"
                                      closable
                                      onClose={(event) => {
                                        event.preventDefault()
                                        removeSelectionSnippet(sel.placeholder)
                                      }}
                                    >
                                      {sel.placeholder}
                                    </Tag>
                                    <Text type="secondary" style={{ fontSize: 11 }}>
                                      {sel.text.length} 字符
                                    </Text>
                                  </div>
                                  <Text code style={{ fontSize: 11, display: 'block', marginTop: 4 }}>
                                    {sel.text.slice(0, 80)}
                                    {sel.text.length > 80 && '...'}
                                  </Text>
                                </div>
                              ))}
                            </div>
                            <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: 'block', color: '#999' }}>
                              💡 prompt 中的 @selection1、@selection2 会引用对应片段，内容会自动通过上下文发�?
                            </Text>
                          </div>
                        )}
                        <div className="doc-studio__prompt-wrapper">
                          <div
                            ref={(el) => {
                              if (el) {
                                promptInputDivRef.current = el
                                // @ts-ignore
                                if (promptInputRef.current !== el) {
                                  // @ts-ignore
                                  promptInputRef.current = { resizableTextArea: { textArea: el } }
                                }
                              }
                            }}
                            className="doc-studio__prompt-input"
                            contentEditable
                            suppressContentEditableWarning
                            data-placeholder={
                              selections.length
                                ? `输入指令，已选中 ${selections.length} 个片段（自动随上下文发送）`
                                : '输入指令，Ctrl+Enter 发�?
                            }
                            onInput={(e) => {
                              const target = e.currentTarget
                              const text = extractTextFromDiv(target)
                              setPrompt(text)
                            }}
                            onClick={(e) => {
                              const target = e.target as HTMLElement
                              if (target.classList.contains('prompt-tag-close')) {
                                const placeholder = target.getAttribute('data-action')?.replace('remove-', '')
                                if (placeholder) {
                                  removeSelectionSnippet(placeholder)
                                }
                              }
                            }}
                            onKeyDown={(event) => {
                              const lowerKey = event.key.toLowerCase()
                              if ((event.ctrlKey || event.metaKey) && lowerKey === 'enter') {
                                event.preventDefault()
                                handleSend()
                                return
                              }
                              if ((event.ctrlKey || event.metaKey) && lowerKey === 'l') {
                                event.preventDefault()
                                addSelectionSnippet()
                              }
                            }}
                          />
                        </div>
                        <div className="doc-studio__chat-actions">
                          <Button
                            icon={<PlusOutlined />}
                            onClick={addSelectionSnippet}
                            title="或使�?Ctrl+L 快捷�?
                          >
                            添加选中文本
                          </Button>
                          <Button
                            icon={<FileTextOutlined />}
                            disabled={selections.length === 0}
                            onClick={() => {
                              if (!selections.length) return
                              const missingPlaceholders = selections
                                .map((item, idx) => item.placeholder || `@selection${idx + 1}`)
                                .filter((token) => !prompt.includes(token))
                              if (!missingPlaceholders.length) return
                              const placeholders = missingPlaceholders.join(' ')
                              // 始终在末尾追加所有缺失占位符
                              setPrompt((prev) => (prev ? `${prev} ${placeholders}` : placeholders))
                            }}
                            title={`在输入框中插�?${selections.length} 个片段的引用占位符`}
                          >
                            引用片段 ({selections.length})
                          </Button>
                          <Button
                            icon={<SaveOutlined />}
                            disabled={!prompt.trim()}
                            onClick={() => handleOpenPresetModal()}
                          >
                            保存模板
                          </Button>
                          <Button
                            type="primary"
                            icon={<SendOutlined />}
                            onClick={handleSend}
                            loading={chatLoading}
                            disabled={!snap.workspaceId || !prompt.trim()}
                          >
                            发�?
                          </Button>
                        </div>
                      </div>
                    </div>
                {/* History Panel */}
                <div className="doc-studio__history" style={{ display: rightTab === 'history' ? 'block' : 'none' }}>
                      {historyItems.length ? (
                        <Timeline className="doc-studio__history-timeline" mode="left" items={historyItems} />
                      ) : (
                        <Empty
                          description="暂无执行记录"
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                      )}
                    </div>
                {/* Compile Panel */}
                {isLatexWorkspace && (
                  <div className="doc-studio__compile" style={{ display: rightTab === 'compile' ? 'block' : 'none' }}>
                      {snap.compileResult ? (
                        <>
                          <Text type={snap.compileResult.success ? 'success' : 'danger'}>
                            {snap.compileResult.summary || (snap.compileResult.success ? '编译成功' : '编译失败')}
                          </Text>
                          {!snap.compileResult.success && snap.compileResult.error ? (
                            <Alert
                              type="error"
                              showIcon
                              message="编译错误"
                              description={snap.compileResult.error}
                            />
                          ) : null}
                          <div className="doc-studio__compile-actions">
                            <Button
                              type="primary"
                              icon={<EyeOutlined />}
                              size="small"
                              onClick={handlePreviewPdf}
                              disabled={!snap.compileResult.data?.pdf_path && !snap.compileResult.success}
                            >
                              预览 PDF
                            </Button>
                            <Button
                              icon={<DownloadOutlined />}
                              size="small"
                              onClick={handleDownloadPdf}
                              disabled={!snap.compileResult.data?.pdf_path && !snap.compileResult.success}
                            >
                              下载 PDF
                            </Button>
                            <Button
                              icon={<SyncOutlined />}
                              size="small"
                              onClick={async () => {
                                if (!snap.workspaceId) return
                                const status = await fetchCompileStatus({ workspaceId: snap.workspaceId })
                                if (status?.result) {
                                  docStudioActions.setCompileResult({
                                    success: status.result.success,
                                    data: status.result.data,
                                    error: status.result.error ?? undefined,
                                    summary: status.result.summary ?? undefined,
                                  })
                                  setRightTab('compile')
                                } else {
                                  message.info('暂无编译状�?)
                                }
                              }}
                            >
                              刷新状�?
                            </Button>
                          </div>
                          {snap.compileResult.data?.pdf_path && (
                            <div>
                              <Text type="secondary">PDF 路径�?/Text>
                              <Text code>{snap.compileResult.data.pdf_path}</Text>
                            </div>
                          )}
                          {snap.compileResult.data?.warnings?.length ? (
                            <div className="doc-studio__compile-section">
                              <Text type="warning">警告�?/Text>
                              <ul>
                                {snap.compileResult.data.warnings.map((warning, idx) => (
                                  <li key={`warning-${idx}`}>{warning}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {snap.compileResult.data?.errors?.length ? (
                            <div className="doc-studio__compile-section">
                              <Text type="danger">错误�?/Text>
                              <ul>
                                {snap.compileResult.data.errors.map((errorMsg, idx) => (
                                  <li key={`error-${idx}`}>{errorMsg}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {snap.compileResult.data?.logs?.length ? (
                            <div className="doc-studio__compile-section doc-studio__compile-logs">
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                                <Text strong style={{ fontSize: 14 }}>编译日志�?/Text>
                                <Button
                                  size="small"
                                  onClick={async () => {
                                    const allLogs =
                                      snap.compileResult?.data?.logs
                                        ?.map((log) =>
                                          `=== ${log.command} (退出码: ${log.returncode}) ===\n${log.log || '(无日志输�?'}`
                                        )
                                        .join('\n\n') || ''
                                    if (!allLogs) {
                                      message.info('当前没有可复制的编译日志')
                                      return
                                    }
                                    try {
                                      await copyTextToClipboard(allLogs)
                                      message.success('日志已复制到剪贴�?)
                                    } catch (error) {
                                      // 某些环境可能不支�?Clipboard API
                                      // 降级方案已经�?copyTextToClipboard 中处理，这里只提示用�?
                                      message.error('复制失败，请手动选择日志内容复制')
                                    }
                                  }}
                                >
                                  复制全部日志
                                </Button>
                              </div>
                              {snap.compileResult.data.logs.map((log, idx) => {
                                const logLines = (log.log || '').split('\n')
                                // 解析命令名称（从完整命令中提取）
                                const commandName = log.command.split(' ')[0] || 'unknown'
                                const stepName = idx === 0 ? '第一次编�? : 
                                                commandName.includes('bibtex') ? 'BibTeX 处理参考文�? :
                                                '重新编译（更新引用）'
                                return (
                                  <div key={`log-${idx}`} className="doc-studio__compile-log-block">
                                    <div className="doc-studio__compile-log-header">
                                      <Tag color={log.returncode === 0 ? 'green' : 'red'}>
                                        退出码 {log.returncode}
                                      </Tag>
                                      <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                                        {stepName}
                                      </Text>
                                      <Text type="secondary" code style={{ flex: 1, marginLeft: 8, fontSize: 11 }}>
                                        {log.command}
                                      </Text>
                                    </div>
                                    <div className="doc-studio__compile-log">
                                      {logLines.length > 0 ? (
                                        logLines.map((line, lineIdx) => {
                                          const trimmedLine = line.trim()
                                          const isError = trimmedLine.startsWith('!') || 
                                                         trimmedLine.includes('Error') || 
                                                         trimmedLine.includes('Fatal error') ||
                                                         trimmedLine.includes('Missing character')
                                          const isWarning = trimmedLine.includes('Warning') || 
                                                           trimmedLine.includes('LaTeX Warning')
                                          const isInfo = trimmedLine.includes('Output written') ||
                                                        trimmedLine.includes('Transcript written') ||
                                                        trimmedLine.includes('This is')
                                          
                                          let className = ''
                                          if (isError) className = 'doc-studio__compile-log-line--error'
                                          else if (isWarning) className = 'doc-studio__compile-log-line--warning'
                                          else if (isInfo) className = 'doc-studio__compile-log-line--info'
                                          
                                          return (
                                            <div 
                                              key={`line-${lineIdx}`} 
                                              className={className}
                                              style={{ 
                                                padding: '2px 0',
                                                fontFamily: 'SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace',
                                                fontSize: '12px',
                                                lineHeight: '1.5'
                                              }}
                                            >
                                              {line || '\u00A0'}
                                            </div>
                                          )
                                        })
                                      ) : (
                                        <div style={{ color: '#888', fontStyle: 'italic' }}>(无日志输�?</div>
                                      )}
                                    </div>
                                  </div>
                                )
                              })}
                            </div>
                          ) : null}
                          {/* 错误和警告摘�?*/}
                          {snap.compileResult.data?.errors?.length ? (
                            <div className="doc-studio__compile-section" style={{ marginTop: 16 }}>
                              <Text type="danger" strong>错误摘要�?/Text>
                              <ul style={{ marginTop: 8, marginBottom: 0 }}>
                                {snap.compileResult.data.errors.map((errorMsg, idx) => (
                                  <li key={`error-${idx}`} style={{ marginBottom: 4 }}>
                                    <Text type="danger">{errorMsg}</Text>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {snap.compileResult.data?.warnings?.length ? (
                            <div className="doc-studio__compile-section" style={{ marginTop: 12 }}>
                              <Text type="warning" strong>警告摘要�?/Text>
                              <ul style={{ marginTop: 8, marginBottom: 0 }}>
                                {snap.compileResult.data.warnings.map((warning, idx) => (
                                  <li key={`warning-${idx}`} style={{ marginBottom: 4 }}>
                                    <Text type="warning">{warning}</Text>
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <Empty
                          description="尚未编译"
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                      )}
                    </div>
                )}
              </div>
            </div>
          </Sider>
        </Layout>
      </div>

      {snap.workspaceLoading && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'rgba(255, 255, 255, 0.8)',
          zIndex: 1000
        }}>
          <Spin size="large" />
        </div>
      )}

      <Modal
        title="新建工作�?
        open={workspaceModalOpen}
        onOk={handleCreateWorkspace}
        onCancel={() => {
          setWorkspaceModalOpen(false)
          setNewWorkspaceType('latex')
        }}
        confirmLoading={workspaceSubmitting}
      >
        <Form layout="vertical">
          <Form.Item label="工作区类�?>
            <Select
              value={newWorkspaceType}
              onChange={(value) => setNewWorkspaceType(value)}
              options={[
                { label: 'LaTeX 论文工作�?, value: 'latex' },
                { label: 'Markdown/学习笔记', value: 'markdown' },
              ]}
            />
          </Form.Item>
          <Form.Item label="工作区名�?>
            <Input
              placeholder="例如: paper-demo"
              value={newWorkspaceName}
              onChange={(event) => setNewWorkspaceName(event.target.value)}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={fileModalType === 'file' ? '新建文件' : '新建文件�?}
        open={fileModalOpen}
        onOk={handleCreateFile}
        onCancel={() => setFileModalOpen(false)}
        confirmLoading={fileSubmitting}
      >
        <Form layout="vertical">
          <Form.Item label="路径">
            <Input
              placeholder={fileModalType === 'file' ? 'sections/intro.tex' : 'sections'}
              value={fileModalPath}
              onChange={(event) => setFileModalPath(event.target.value)}
            />
          </Form.Item>
          {fileModalType === 'file' && (
            <Form.Item label="初始内容">
              <Input.TextArea
                rows={4}
                value={fileModalContent}
                onChange={(event) => setFileModalContent(event.target.value)}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>
      <Modal
        title={presetEditingId ? '编辑模板' : '保存模板'}
        open={presetModalOpen}
        onOk={handleSavePreset}
        onCancel={() => setPresetModalOpen(false)}
      >
        <Form layout="vertical">
          <Form.Item label="模板名称">
            <Input
              placeholder="例如：方法调研模�?
              value={presetName}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </Form.Item>
          <Form.Item label="模板类型">
            <Select
              value={presetIntent}
              onChange={(value) => setPresetIntent(value)}
              options={[
                { label: '编辑/写作', value: 'edit' },
                { label: '建议/评审', value: 'suggest' },
                { label: '问答/解释', value: 'qa' },
                { label: '引用/文献', value: 'citation' },
              ]}
            />
          </Form.Item>
          <Form.Item label="模板内容">
            <Input.TextArea
              rows={6}
              value={presetPrompt}
              onChange={(event) => setPresetPrompt(event.target.value)}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              支持使用 @selection1、@selection2 引用选中片段
            </Text>
          </Form.Item>
          <Form.Item label="绑定当前模型设置">
            <Space align="center">
              <Switch checked={presetBindModel} onChange={setPresetBindModel} />
              <Text type="secondary">将当前模�?温度/Max tokens 写入模板</Text>
            </Space>
          </Form.Item>
          {presetBindModel && (
            <Form.Item label="当前模型配置">
              <Text type="secondary">
                {llmProvider === 'auto' ? '自动选择' : llmProvider} / {llmModel || '默认模型'}
                {typeof llmTemperature === 'number' ? ` / temp=${llmTemperature}` : ''}
                {typeof llmMaxTokens === 'number' ? ` / max=${llmMaxTokens}` : ''}
              </Text>
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* Agent 修改预览 Modal */}
      <Modal
        title={
          allFileDiffs.length > 0 ? (
            <Space>
              <span>预览修改 - {allFileDiffs[currentDiffIndex]?.file_path || ''}</span>
              <Tag color="blue">
                {currentDiffIndex + 1} / {allFileDiffs.length}
              </Tag>
            </Space>
          ) : (
            '预览修改'
          )
        }
        open={diffModalOpen}
        onCancel={() => {
          if (acceptedDiffs.size < allFileDiffs.length) {
            const rejectedCount = allFileDiffs.length - acceptedDiffs.size
            Modal.confirm({
              title: '关闭预览�?,
              content: `你已拒绝或未确认 ${rejectedCount} 个文件的修改，关闭后这些修改仍会保留。是否撤销未接受的修改？`,
              okText: '撤销未接�?,
              cancelText: '保留修改',
              onOk: () => {
                handleFinalizeDiffs()
              },
              onCancel: () => {
                setDiffModalOpen(false)
                setAllFileDiffs([])
                setCurrentDiffIndex(0)
                setAcceptedDiffs(new Set())
                setDiffOperationId(null)
              },
            })
          } else {
            setDiffModalOpen(false)
            setAllFileDiffs([])
            setCurrentDiffIndex(0)
            setAcceptedDiffs(new Set())
            setDiffOperationId(null)
          }
        }}
        width="90%"
        style={{ top: 20 }}
        footer={
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <Button
                disabled={currentDiffIndex === 0}
                onClick={() => setCurrentDiffIndex(currentDiffIndex - 1)}
              >
                上一�?
              </Button>
              <Button
                disabled={currentDiffIndex === allFileDiffs.length - 1}
                onClick={() => setCurrentDiffIndex(currentDiffIndex + 1)}
              >
                下一�?
              </Button>
            </Space>
            <Space>
              <Button
                icon={<CloseOutlined />}
                disabled={diffReverting}
                onClick={handleRejectCurrentDiff}
              >
                回滚此文�?
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                onClick={handleKeepCurrentDiff}
              >
                保留此文�?
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                loading={diffReverting}
                disabled={diffReverting || allFileDiffs.length === acceptedDiffs.size}
                onClick={handleFinalizeDiffs}
              >
                撤销未接受修�?({allFileDiffs.length - acceptedDiffs.size})
              </Button>
            </Space>
          </Space>
        }
      >
        <div className="doc-studio__diff-toolbar">
          <Segmented
            value={diffViewMode}
            size="small"
            onChange={(value) => setDiffViewMode(value as 'split' | 'inline')}
            options={[
              { label: '并排', value: 'split' },
              { label: '逐行', value: 'inline' },
            ]}
          />
    </div>
        <div className="doc-studio__diff-wrapper">
          <div className="doc-studio__diff-files">
            <List
              size="small"
              dataSource={allFileDiffs}
              renderItem={(diff, idx) => (
                <List.Item
                  key={`${diff.file_path}-${idx}`}
                  className={`doc-studio__diff-file ${
                    currentDiffIndex === idx ? 'doc-studio__diff-file--active' : ''
                  }`}
                  onClick={() => setCurrentDiffIndex(idx)}
                >
                  <Tooltip title={diff.file_path}>
                    <span className="doc-studio__diff-file-name">
                      {diff.file_path}
                    </span>
                  </Tooltip>
                  {diff.is_truncated && (
                    <Tag color="orange" style={{ marginLeft: 4 }}>
                      片段
                    </Tag>
                  )}
                  {acceptedDiffs.has(idx) && (
                    <Tag color="green" style={{ marginLeft: 8 }}>
                      已接�?
                    </Tag>
                  )}
                </List.Item>
              )}
            />
          </div>
          <div className="doc-studio__diff-view">
            {allFileDiffs.length > 0 && allFileDiffs[currentDiffIndex] && (
              <DiffEditor
                key={currentDiffIndex}
                height="100%"
                theme="vs-dark"
                language={resolveEditorLanguage(allFileDiffs[currentDiffIndex]?.file_path)}
                original={resolvedOriginal || allFileDiffs[currentDiffIndex].original_content}
                modified={resolvedModified || allFileDiffs[currentDiffIndex].modified_content}
                options={{
                  readOnly: true,
                  renderSideBySide: diffViewMode === 'split',
                  minimap: { enabled: false },
                  fontSize: 14,
                  wordWrap: 'on',
                  automaticLayout: true,
                }}
                onMount={(editor: any) => {
                  diffEditorRef.current = editor
                  refreshLineChanges()
                }}
              />
            )}
            {allFileDiffs[currentDiffIndex]?.is_truncated && (
              <Alert
                style={{ marginTop: 12 }}
                type="info"
                showIcon
                message="仅展示增量片�?
                description="为提升性能，已只展示与本次改动相关的上下文。接受后会自动重新加载完整文件�?
              />
            )}
            {diffContentLoading && (
              <div style={{ marginTop: 12 }}>
                <Spin size="small" />
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  正在加载完整内容...
                </Text>
              </div>
            )}
            {lineChanges.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text type="secondary">逐处修改</Text>
                <List
                  size="small"
                  dataSource={lineChanges}
                  renderItem={(change: any, idx) => {
                    const oStart = Number(change?.originalStartLineNumber || 0)
                    const oEnd = Number(change?.originalEndLineNumber || 0)
                    const mStart = Number(change?.modifiedStartLineNumber || 0)
                    const mEnd = Number(change?.modifiedEndLineNumber || 0)
                    const label = `�?${oStart}-${oEnd} �?�?${mStart}-${mEnd}`
                    const accepted = acceptedLineChanges.has(idx)
                    return (
                      <List.Item
                        actions={[
                          <Button
                            key="accept"
                            size="small"
                            disabled={accepted}
                            onClick={() => handleAcceptLineChange(idx)}
                          >
                            {accepted ? '已接�? : '接受此处'}
                          </Button>,
                          <Button
                            key="reject"
                            size="small"
                            danger
                            loading={diffReverting}
                            onClick={() => handleRejectLineChange(idx)}
                          >
                            回滚此处
                          </Button>,
                        ]}
                      >
                        <Text type="secondary">{label}</Text>
                      </List.Item>
                    )
                  }}
                />
              </div>
            )}
          </div>
        </div>
        <div style={{ marginTop: 16, padding: 12, background: '#f0f0f0', borderRadius: 4 }}>
          <Text type="secondary">
            <strong>说明�?/strong>
            左侧为原始内容，右侧为修改后的内容。红色表示删除，绿色表示新增�?
            {allFileDiffs.length > 1 && (
              <span> 当前文件：{allFileDiffs[currentDiffIndex]?.file_path}</span>
            )}
            {acceptedDiffs.size > 0 && (
              <Tag color="green" style={{ marginLeft: 8 }}>
                已接�?{acceptedDiffs.size} / {allFileDiffs.length}
              </Tag>
            )}
          </Text>
        </div>
      </Modal>

      <Modal
        title="操作历史"
        open={operationHistoryOpen}
        onCancel={() => setOperationHistoryOpen(false)}
        footer={null}
        width={720}
      >
        <List
          loading={operationHistoryLoading}
          dataSource={operationHistory}
          locale={{ emptyText: '暂无操作记录' }}
          renderItem={(item) => {
            const modifiedCount = item.modified_files?.length || 0
            const timestamp = item.timestamp ? new Date(item.timestamp).toLocaleString() : '未知时间'
            return (
              <List.Item
                actions={[
                  <Popconfirm
                    key="revert"
                    title="确认回滚该操作？"
                    onConfirm={() => handleRevertOperation(item.operation_id)}
                  >
                    <Button
                      size="small"
                      loading={revertingOperationId === item.operation_id}
                      disabled={!modifiedCount}
                    >
                      回滚
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Text strong>{item.user_intent || '操作'}</Text>
                      <Tag color={item.success ? 'green' : 'red'}>
                        {item.success ? '成功' : '失败'}
                      </Tag>
                      {item.intent_type && <Tag>{item.intent_type}</Tag>}
                    </Space>
                  }
                  description={
                    <Space size="middle" wrap>
                      <Text type="secondary">{timestamp}</Text>
                      <Text type="secondary">文件数：{modifiedCount}</Text>
                      {item.trace_id && (
                        <Text type="secondary">Trace: {item.trace_id.slice(0, 8)}</Text>
                      )}
                      <Text type="secondary">Op: {item.operation_id.slice(0, 8)}</Text>
                    </Space>
                  }
                />
              </List.Item>
            )
          }}
        />
      </Modal>

      <Modal
        title="系统状�?
        open={systemStatusOpen}
        onCancel={() => setSystemStatusOpen(false)}
        footer={null}
        width={720}
      >
        <Space align="center" wrap style={{ marginBottom: 12 }}>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={systemStatsLoading}
            onClick={() => refreshSystemStats()}
          >
            刷新
          </Button>
          {llmHealth?.preferred_provider && (
            <Tag color="blue">首选：{llmHealth.preferred_provider}</Tag>
          )}
        </Space>
        {llmHealth?.providers?.length ? (
          <div style={{ marginBottom: 12 }}>
            <Text type="secondary">Provider 健康</Text>
            <Space wrap style={{ marginTop: 6 }}>
              {llmHealth.providers.map((provider) => {
                const inCooldown = provider.in_cooldown
                const color = !provider.available
                  ? 'default'
                  : inCooldown
                    ? 'orange'
                    : 'green'
                const cooldown = provider.cooldown_remaining_seconds || 0
                const tooltipText = provider.last_error
                  ? `失败 ${provider.failures || 0} 次：${provider.last_error}`
                  : inCooldown
                    ? `冷却中（剩余 ${cooldown}s）`
                    : '健康'
                return (
                  <Tooltip key={provider.provider} title={tooltipText}>
                    <Tag color={color}>
                      {provider.provider}
                      {inCooldown ? ` 冷却${cooldown}s` : ' OK'}
                    </Tag>
                  </Tooltip>
                )
              })}
            </Space>
          </div>
        ) : null}
        <div style={{ marginBottom: 12 }}>
          <Text type="secondary">Token / 成本</Text>
          <Space wrap style={{ marginTop: 6 }}>
            <Tag color="geekblue">Tokens 总计：{llmTotals.tokens.toLocaleString()}</Tag>
            <Tag color="purple">估算成本：{llmTotals.cost.toFixed(6)}</Tag>
          </Space>
        </div>
        {llmMetricEntries.length > 0 && (
          <div>
            <Text type="secondary">模型调用概况</Text>
            <Space wrap style={{ marginTop: 6 }}>
              {llmMetricEntries.map((entry) => (
                <Tooltip
                  key={entry.key}
                  title={`成功 ${entry.success} / 失败 ${entry.failure} | tokens ${entry.total_tokens} | cost ${entry.total_cost.toFixed(6)}`}
                >
                  <Tag color={entry.failure ? 'volcano' : 'default'}>
                    {entry.provider}/{entry.model}
                  </Tag>
                </Tooltip>
              ))}
            </Space>
          </div>
        )}
      </Modal>
      
      {/* 文件树右键菜�?*/}
      {contextMenuVisible && (
        <div
          style={{
            position: 'fixed',
            left: contextMenuPosition.x,
            top: contextMenuPosition.y,
            zIndex: 10000,
            background: '#fff',
            border: '1px solid #d9d9d9',
            borderRadius: '4px',
            boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
            minWidth: '160px',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {contextMenuType === 'directory' && (
            <>
              <div
                style={{
                  padding: '8px 16px',
                  cursor: 'pointer',
                  transition: 'background 0.3s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#f5f5f5'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                }}
                onClick={() => handleUploadToDirectory(contextMenuPath)}
              >
                <UploadOutlined style={{ marginRight: '8px' }} />
                上传文件到此目录
              </div>
              <div
                style={{
                  padding: '8px 16px',
                  cursor: 'pointer',
                  transition: 'background 0.3s',
                  borderTop: '1px solid #f0f0f0',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#f5f5f5'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                }}
                onClick={() => handleCreateFileInDirectory(contextMenuPath)}
              >
                <FileAddOutlined style={{ marginRight: '8px' }} />
                创建文本文件
              </div>
              <div
                style={{
                  padding: '8px 16px',
                  cursor: 'pointer',
                  transition: 'background 0.3s',
                  borderTop: '1px solid #f0f0f0',
                  color: '#ff4d4f',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#fff1f0'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                }}
                onClick={() => {
                  setContextMenuVisible(false)
                  Modal.confirm({
                    title: '确认删除文件�?,
                    content: `确定要删除文件夹 "${contextMenuPath}" 吗？此操作将删除文件夹及其所有内容，且无法恢复。`,
                    okText: '删除',
                    okType: 'danger',
                    cancelText: '取消',
                    onOk: () => handleDeleteFromTree(contextMenuPath, 'directory'),
                  })
                }}
              >
                <DeleteOutlined style={{ marginRight: '8px' }} />
                删除文件�?
              </div>
            </>
          )}
          {contextMenuType === 'file' && (
            <div
              style={{
                padding: '8px 16px',
                cursor: 'pointer',
                transition: 'background 0.3s',
                color: '#ff4d4f',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = '#fff1f0'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent'
              }}
              onClick={() => {
                setContextMenuVisible(false)
                Modal.confirm({
                  title: '确认删除文件',
                  content: `确定要删除文�?"${contextMenuPath}" 吗？此操作无法恢复。`,
                  okText: '删除',
                  okType: 'danger',
                  cancelText: '取消',
                  onOk: () => handleDeleteFromTree(contextMenuPath, 'file'),
                })
              }}
            >
              <DeleteOutlined style={{ marginRight: '8px' }} />
              删除文件
            </div>
          )}
        </div>
      )}
    </>
  )
}

export default LatexEditorPage


