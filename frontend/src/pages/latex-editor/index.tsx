import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  Layout,
  List,
  message,
  Modal,
  Popconfirm,
  Progress,
  Segmented,
  Select,
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
  LikeOutlined,
  DislikeOutlined,
} from '@ant-design/icons'
import type { DataNode } from 'antd/es/tree'
import { useSnapshot } from 'valtio'
import Editor, { DiffEditor } from '@monaco-editor/react'
import type React from 'react'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import {
  compileWorkspace,
  createFileOrDirectory,
  createWorkspace,
  deleteFile,
  fetchCompileStatus,
  fetchFileContent,
  fetchWorkspaceFiles,
  listWorkspaces,
  runAgentTask,
  sendAgentFeedback,
  updateFileContent,
  uploadFile,
  downloadPdf,
  downloadFile,
  listAgentKnowledgeBases,
} from '@/api/latexAgent'
import { latexAgentActions, latexAgentState } from '@/store/latexAgent'
import type { LatexChatMessage } from '@/store/latexAgent'
import './index.scss'

const { Sider, Content, Header } = Layout
const { Text } = Typography

const findFirstFile = (nodes: LatexAgentAPI.FileNode[]): string | undefined => {
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

const intentTagMap: Record<
  string,
  {
    label: string
    color: string
  }
> = {
  qa: { label: '问答', color: 'cyan' },
  suggest: { label: '建议', color: 'gold' },
  edit: { label: '编辑', color: 'blue' },
  citation: { label: '引用', color: 'purple' },
  file_op: { label: '文件操作', color: 'magenta' },
}

const quickPromptPresets = [
  {
    label: '优化摘要',
    description: '提升摘要的学术性与逻辑性',
    prompt: '帮我优化当前摘要，使其更有逻辑、更符合学术写作规范。',
  },
  {
    label: '润色段落',
    description: '改善语言表达与衔接',
    prompt: '请润色我选中的段落，改善语法与逻辑，但保持原意不变。',
  },
  {
    label: '检查问题',
    description: '诊断文本中的问题',
    prompt: '帮我检查这段内容有没有不严谨或需要改进的地方，并给出建议。',
  },
  {
    label: '背景问答',
    description: '解释术语或概念',
    prompt: '请解释：',
  },
]

type ReadonlyFileNode = Readonly<
  Omit<LatexAgentAPI.FileNode, 'children'>
> & {
  readonly children?: ReadonlyArray<ReadonlyFileNode>
}

const cloneFileNodes = (
  nodes: ReadonlyArray<ReadonlyFileNode>,
): LatexAgentAPI.FileNode[] =>
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
  const snap = useSnapshot(latexAgentState)
  const [prompt, setPrompt] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const [selection, setSelection] = useState<{ start: number; end: number; text: string }>({
    start: 0,
    end: 0,
    text: '',
  })
  const [workspaceModalOpen, setWorkspaceModalOpen] = useState(false)
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [workspaceSubmitting, setWorkspaceSubmitting] = useState(false)
  const [fileModalOpen, setFileModalOpen] = useState(false)
  const [fileModalType, setFileModalType] = useState<'file' | 'directory'>('file')
  const [fileModalPath, setFileModalPath] = useState('')
  const [fileModalContent, setFileModalContent] = useState('')
  const [fileSubmitting, setFileSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [rightTab, setRightTab] = useState<'chat' | 'history' | 'compile'>('chat')
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const editorRef = useRef<any>(null)
  const chatMessagesEndRef = useRef<HTMLDivElement | null>(null)
  const promptInputRef = useRef<TextAreaRef | null>(null)
  
  // Diff 预览相关状态
  const [diffModalOpen, setDiffModalOpen] = useState(false)
  const [allFileDiffs, setAllFileDiffs] = useState<LatexAgentAPI.FileDiff[]>([])
  const [currentDiffIndex, setCurrentDiffIndex] = useState(0)
  const [acceptedDiffs, setAcceptedDiffs] = useState<Set<number>>(new Set())
  const preferredKbFromUrl = useMemo(() => {
    if (typeof window === 'undefined') return null
    const raw = new URLSearchParams(window.location.search).get('kb_id')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : null
  }, [])
  const [knowledgeBases, setKnowledgeBases] = useState<LatexAgentAPI.KnowledgeBaseSummary[]>([])
  const [knowledgeLoading, setKnowledgeLoading] = useState(false)
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<number | null>(
    preferredKbFromUrl,
  )
  const [diffViewMode, setDiffViewMode] = useState<'split' | 'inline'>('split')
  const [feedbackSubmitting, setFeedbackSubmitting] = useState<Record<string, boolean>>({})

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
  const knowledgeBaseOptions = useMemo(
    () =>
      knowledgeBases.map((item) => ({
        label: item.name,
        value: item.id,
      })),
    [knowledgeBases],
  )
  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.id === selectedKnowledgeBaseId) || null,
    [knowledgeBases, selectedKnowledgeBaseId],
  )
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

  const formatStepTime = useCallback((ts?: number) => {
    if (!ts) return ''
    return new Date(ts * 1000).toLocaleTimeString()
  }, [])

  const historyItems = useMemo(() => {
    const labelMap: Record<string, string> = {
      thought: '思考',
      action: '执行',
      result: '结果',
      reflection: '反思',
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
          <div className="latex-editor__history-card">
            <div className="latex-editor__history-head">
              <Text strong>{`${index + 1}. ${label}`}</Text>
              {step.tool && <Tag>{step.tool}</Tag>}
              {timestampLabel && <Text type="secondary">{timestampLabel}</Text>}
            </div>
            <div className="latex-editor__history-content">{step.content}</div>
            {step.result?.summary && (
              <Text className="latex-editor__history-summary" type="secondary">
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
      if (!latexAgentState.workspaceId) return
      if (!forceReload) {
        const existing = latexAgentState.files[path]
        if (existing && !existing.loading) {
          latexAgentActions.setActiveFile(path)
          return
        }
      }
      latexAgentActions.setActiveFile(path)
      latexAgentActions.setFileLoading(path, true)
      try {
        const file = await fetchFileContent({
          workspaceId: latexAgentState.workspaceId,
          path,
        })
        latexAgentActions.setFileContent(path, file.content, file.encoding)
      } catch (error) {
        message.error(getErrorMessage(error))
      } finally {
        latexAgentActions.setFileLoading(path, false)
      }
    },
    [],
  )

  const loadWorkspaceFiles = useCallback(
    async (workspaceId: string, shouldOpenDefault = true) => {
      try {
        const data = await fetchWorkspaceFiles({ workspaceId })
        latexAgentActions.setFileTree(data.files)
        latexAgentActions.setWorkspaceConfig(data.config)
        // 只在明确需要时才自动打开默认文件（比如首次加载工作区）
        if (shouldOpenDefault) {
          const defaultFile = data.mainFile || findFirstFile(data.files)
          if (defaultFile) {
            await openFile(defaultFile)
          } else {
            latexAgentActions.setActiveFile('')
          }
        }
      } catch (error) {
        message.error(getErrorMessage(error))
      }
    },
    [openFile],
  )

  const loadKnowledgeBases = useCallback(async () => {
    setKnowledgeLoading(true)
    try {
      const data = await listAgentKnowledgeBases()
      const list = Array.isArray(data) ? data : []
      setKnowledgeBases(list)
      setSelectedKnowledgeBaseId((current) => {
        if (current && list.some((item) => item.id === current)) {
          return current
        }
        if (
          preferredKbFromUrl &&
          list.some((item) => item.id === preferredKbFromUrl)
        ) {
          return preferredKbFromUrl
        }
        return list[0]?.id ?? null
      })
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setKnowledgeLoading(false)
    }
  }, [preferredKbFromUrl])

  const loadWorkspaces = useCallback(
    async (targetWorkspace?: string) => {
      latexAgentActions.setWorkspaceLoading(true)
      try {
        const list = await listWorkspaces()
        latexAgentActions.setWorkspaces(list)
        const preferred =
          targetWorkspace ||
          params.workspaceId ||
          list[0]?.workspaceId ||
          ''
        if (preferred) {
          latexAgentActions.setWorkspaceId(preferred)
          await loadWorkspaceFiles(preferred)
        }
      } catch (error) {
        message.error(getErrorMessage(error))
      } finally {
        latexAgentActions.setWorkspaceLoading(false)
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
    const workspaceKbRaw =
      (snap.workspaceConfig?.knowledge_base_id ??
        snap.workspaceConfig?.knowledgeBaseId ??
        snap.workspaceConfig?.kb_id) as number | undefined
    if (!workspaceKbRaw) return
    setSelectedKnowledgeBaseId((current) => current || Number(workspaceKbRaw))
  }, [snap.workspaceConfig])

  // 自动滚动到聊天窗口底部
  useEffect(() => {
    if (chatMessagesEndRef.current) {
      chatMessagesEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [snap.chatMessages])

  const handleWorkspaceChange = (workspaceId: string) => {
    navigate(`/latex-editor/${workspaceId}`)
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
      const workspace = await createWorkspace({ name: newWorkspaceName.trim() })
      setWorkspaceModalOpen(false)
      setNewWorkspaceName('')
      await loadWorkspaces(workspace.workspaceId)
      message.success('创建成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setWorkspaceSubmitting(false)
    }
  }

  const handleTreeSelect = async (keys: React.Key[]) => {
    const path = String(keys[0] || '')
    if (!path || snap.activeFilePath === path) return
    await openFile(path)
  }

  const handleTabChange = async (key: string) => {
    if (!key) return
    await openFile(key)
  }

  const handleTabEdit = (targetKey: string | React.MouseEvent | React.KeyboardEvent, action: 'add' | 'remove') => {
    if (action === 'remove' && typeof targetKey === 'string') {
      latexAgentActions.closeFile(targetKey)
      if (latexAgentState.workspaceId && latexAgentState.activeFilePath && !latexAgentState.files[latexAgentState.activeFilePath]) {
        openFile(latexAgentState.activeFilePath, true)
      }
    }
  }

  const handleEditorChange = useCallback((value?: string) => {
    if (!snap.activeFilePath) return
    // 防止撤销操作导致空白（只有当值真正变化时才更新）
    const currentContent = snap.files[snap.activeFilePath]?.content ?? ''
    if (value !== currentContent) {
      latexAgentActions.updateFileContent(snap.activeFilePath, value ?? '')
    }
  }, [snap.activeFilePath, snap.files])

  const handleEditorMount = useCallback((editorInstance: any) => {
    editorRef.current = editorInstance
    
    // 监听光标选择变化
    editorInstance.onDidChangeCursorSelection(() => {
      const selectionRange = editorInstance.getSelection()
      if (!selectionRange) return
      const model = editorInstance.getModel()
      const text = model?.getValueInRange(selectionRange) ?? ''
      setSelection({
        start: model?.getOffsetAt(selectionRange.getStartPosition()) ?? 0,
        end: model?.getOffsetAt(selectionRange.getEndPosition()) ?? 0,
        text,
      })
    })
    
    // 立即获得焦点
    editorInstance.focus()
    
    // 自定义快捷键：Ctrl+A 全选（确保在某些浏览器/输入法环境下也可用）
    if (typeof window !== 'undefined' && (window as any).monaco) {
      const monaco = (window as any).monaco
      editorInstance.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyA,
        () => {
          const model = editorInstance.getModel()
          if (model) {
            editorInstance.setSelection(model.getFullModelRange())
          }
        },
      )
    }
  }, [])

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
      latexAgentActions.markFileSaved(snap.activeFilePath)
      message.success('保存成功')
    } catch (error) {
      message.error(getErrorMessage(error))
    }
  }

  const handleCompile = async () => {
    if (!snap.workspaceId) {
      message.warning('请选择工作区')
      return
    }
    try {
      const result = await compileWorkspace({
        workspaceId: snap.workspaceId,
        mainFile:
          snap.workspaceConfig?.main_file ||
          snap.workspaceConfig?.mainFile ||
          undefined,
      })
      latexAgentActions.setCompileResult(result)
      setRightTab('compile')
      if (result.success) {
        message.success(result.summary || '编译成功')
      } else {
        const firstError = result.error || result.data?.errors?.[0]
        message.error(firstError ? `编译失败：${firstError}` : '编译失败')
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
      latexAgentActions.setActiveFile('')
      await loadWorkspaceFiles(snap.workspaceId, false)
    } catch (error) {
      message.error(getErrorMessage(error))
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
      message.warning('请输入路径')
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
      if (fileModalType === 'file') {
        await openFile(fileModalPath.trim())
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
    (presetPrompt: string) => {
      const selectionText = selection.text?.trim()
      const nextPrompt = selectionText ? `${presetPrompt}\n\n${selectionText}` : presetPrompt
      setPrompt(nextPrompt)
      setTimeout(() => promptInputRef.current?.focus?.(), 0)
    },
    [selection.text],
  )

  const pushChatMessage = (payload: Omit<LatexChatMessage, 'id' | 'createdAt'>) => {
    latexAgentActions.appendChatMessage(payload)
  }

  const handleFeedbackSubmit = useCallback(
    async (messageId: string, traceId: string | undefined, rating: LatexAgentAPI.AgentFeedbackRating) => {
      if (!traceId) {
        message.warning('该回复缺少 Trace ID，无法反馈')
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
        latexAgentActions.setMessageFeedback(messageId, rating)
        message.success('感谢反馈！')
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
      message.warning('请选择工作区')
      return
    }
    if (!prompt.trim()) {
      message.warning('请输入指令')
      return
    }
    const traceId =
      window.crypto?.randomUUID?.() ??
      `trace-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
    pushChatMessage({ role: 'user', content: prompt, meta: { traceId } })
    setChatLoading(true)
    try {
      latexAgentActions.setAgentStatus({ intentType: undefined, plan: undefined, warnings: [] })
      const contextPayload: Record<string, any> = {}
      if (snap.activeFilePath) {
        contextPayload.file_path = snap.activeFilePath
      }
      if (selection.text && selection.text.trim().length > 0) {
        contextPayload.selection = selection
      }
      const knowledgeBaseId = selectedKnowledgeBaseId ?? undefined
      const knowledgeBaseName = knowledgeBaseId ? selectedKnowledgeBase?.name : undefined
      const response = await runAgentTask({
        workspaceId: snap.workspaceId,
        userIntent: prompt.trim(),
        context: Object.keys(contextPayload).length ? contextPayload : undefined,
        knowledgeBaseId,
        knowledgeBaseName,
      }, { headers: { 'X-Trace-Id': traceId } })
      const changeCount = response.changes?.length || 0
      pushChatMessage({
        role: 'agent',
        content: response.execution_history?.[response.execution_history.length - 1]?.content
          ? response.execution_history[response.execution_history.length - 1].content
          : `完成，检测到 ${changeCount} 处变更`,
        meta: {
          changes: response.changes,
          traceId: response.trace_id || traceId,
        },
      })
      latexAgentActions.setExecutionHistory(response.execution_history)
      latexAgentActions.setAgentStatus({
        intentType: response.intent_type,
        intentConfidence: response.intent_confidence ?? undefined,
        plan: response.plan || undefined,
        warnings: response.warnings || [],
        traceId: response.trace_id || traceId,
      })
      // 如果有文件修改，显示 Diff 预览而不是立即应用
      if (response.file_diffs && response.file_diffs.length > 0) {
        // 后端返回完整的 file_diffs
        setAllFileDiffs(response.file_diffs)
        setCurrentDiffIndex(0)
        setAcceptedDiffs(new Set())
        setDiffModalOpen(true)
      } else if (response.changes && response.changes.length > 0) {
        // 兼容：如果没有 file_diffs 但有 changes，直接刷新文件
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
        message.info(`已应用 ${changeCount} 处修改`)
      }
    } catch (error) {
      message.error(getErrorMessage(error))
    } finally {
      setPrompt('')
      setChatLoading(false)
    }
  }

  return (
    <>
    <div className="latex-editor-page">
        <Layout className="latex-editor">
          <Sider width={260} className="latex-editor__sider">
            <div className="latex-editor__workspace">
              <Select
                value={snap.workspaceId || undefined}
                className="latex-editor__workspace-select"
                placeholder="选择工作区"
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
            <div className="latex-editor__knowledge">
              <Select
                value={selectedKnowledgeBaseId ?? undefined}
                className="latex-editor__knowledge-select"
                placeholder="选择知识库"
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
                    <span>暂无知识库</span>
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
            <div className="latex-editor__file-actions">
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
                新建文件夹
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
              <input
                ref={fileInputRef}
                type="file"
                style={{ display: 'none' }}
                onChange={handleFileInputChange}
              />
            </div>
            <div className="latex-editor__tree-wrapper">
              {treeData.length ? (
                <Tree
                  selectedKeys={snap.activeFilePath ? [snap.activeFilePath] : []}
                  showIcon
                  treeData={treeData}
                  onSelect={handleTreeSelect}
                />
              ) : (
                <Empty
                  description="暂无文件"
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </div>
          </Sider>
          <Layout>
            <Header className="latex-editor__header">
              <div className="latex-editor__header-info">
                <Text className="latex-editor__file-path" ellipsis>
                  {snap.activeFilePath || '请选择文件'}
                </Text>
                {currentFileBuffer?.dirty && <Tag color="gold">未保存</Tag>}
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
                  title="确定删除当前文件？"
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
                  type="primary"
                  ghost
                  icon={<PlayCircleOutlined />}
                  onClick={handleCompile}
                  disabled={!snap.workspaceId}
                >
                  编译
                </Button>
              </Space>
            </Header>
            <Content className="latex-editor__content">
              {snap.openedFiles.length ? (
                <div className="latex-editor__editor-wrapper">
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
                      language="latex"
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
                    <Empty description="选择一个文件以开始编辑" />
                  )}
                  </div>
                </div>
              ) : (
                <Empty description="选择一个文件以开始编辑" />
              )}
            </Content>
          </Layout>
          <Sider width={360} className="latex-editor__right">
            <Tabs
              activeKey={rightTab}
              onChange={(key) => setRightTab(key as 'chat' | 'history' | 'compile')}
              className="latex-editor__tabs"
              items={[
                {
                  key: 'chat',
                  label: 'Agent 聊天',
                  children: (
                    <div className="latex-editor__chat-panel">
                      <div className="latex-editor__mode-switch">
                        <Text type="secondary">不知道怎么问？试试这些示例：</Text>
                        <Space wrap size={[8, 8]} className="latex-editor__quick-prompts">
                          {quickPromptPresets.map((preset) => (
                            <Tooltip title={preset.description} key={preset.label}>
                              <Button
                                size="small"
                                onClick={() => handleQuickPromptApply(preset.prompt)}
                              >
                                {preset.label}
                              </Button>
                            </Tooltip>
                          ))}
                        </Space>
                      </div>
                      {(intentStatus || planTotalSteps > 0) && (
                        <div className="latex-editor__agent-status">
                          <div className="latex-editor__agent-status-tags">
                            {intentStatus && (
                              <Tag color={intentTagMap[intentStatus]?.color ?? 'blue'}>
                                {intentTagMap[intentStatus]?.label ?? intentStatus.toUpperCase()}
                              </Tag>
                            )}
                            {typeof snap.agentStatus.intentConfidence === 'number' && (
                              <Tag color="gold">
                                置信度 {(snap.agentStatus.intentConfidence * 100).toFixed(0)}%
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
                          </div>
                          {planTotalSteps > 0 && (
                            <div className="latex-editor__agent-plan">
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
                      <div className="latex-editor__chat-messages">
                        {snap.chatMessages.length ? (
                          <>
                            {snap.chatMessages.map((msg) => (
                              <div
                                key={msg.id}
                                className={`latex-editor__chat-message latex-editor__chat-message--${msg.role}`}
                              >
                                <div className="latex-editor__chat-meta">
                                  <Text strong>{msg.role === 'user' ? '我' : 'Agent'}</Text>
                                  <Text type="secondary">
                                    {new Date(msg.createdAt).toLocaleTimeString()}
                                  </Text>
                                </div>
                                <div className="latex-editor__chat-content">{msg.content}</div>
                              {msg.role === 'agent' && msg.meta?.traceId && (
                                <div className="latex-editor__chat-feedback">
                                  <Tooltip title="这个回答有帮助">
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
                      <div className="latex-editor__chat-input">
                        {selection.text && (
                          <div className="latex-editor__selection-preview">
                            <Text type="secondary">选中内容：</Text>
                            <Text code>{selection.text.slice(0, 120)}</Text>
                          </div>
                        )}
                        <Input.TextArea
                          ref={promptInputRef}
                          placeholder="输入指令，Ctrl+Enter 发送"
                          value={prompt}
                          onChange={(event) => setPrompt(event.target.value)}
                          autoSize={{ minRows: 3, maxRows: 6 }}
                          onKeyDown={(event) => {
                            if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                              event.preventDefault()
                              handleSend()
                            }
                          }}
                        />
                        <div className="latex-editor__chat-actions">
                          <Button
                            icon={<FileTextOutlined />}
                            disabled={!selection.text}
                            onClick={() => {
                              if (!selection.text) return
                              setPrompt((prev) =>
                                prev ? `${prev}\n\n${selection.text}` : selection.text,
                              )
                            }}
                          >
                            引用选中文本
                          </Button>
                          <Button
                            type="primary"
                            icon={<SendOutlined />}
                            onClick={handleSend}
                            loading={chatLoading}
                            disabled={!snap.workspaceId || !prompt.trim()}
                          >
                            发送
                          </Button>
                        </div>
                      </div>
                    </div>
                  ),
                },
                {
                  key: 'history',
                  label: '执行历史',
                  children: (
                    <div className="latex-editor__history">
                      {historyItems.length ? (
                        <Timeline className="latex-editor__history-timeline" mode="left" items={historyItems} />
                      ) : (
                        <Empty
                          description="暂无执行记录"
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                        />
                      )}
                    </div>
                  ),
                },
                {
                  key: 'compile',
                  label: '编译结果',
                  children: (
                    <div className="latex-editor__compile">
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
                          <div className="latex-editor__compile-actions">
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
                                  latexAgentActions.setCompileResult({
                                    success: status.result.success,
                                    data: status.result.data,
                                    error: status.result.error ?? undefined,
                                    summary: status.result.summary ?? undefined,
                                  })
                                  setRightTab('compile')
                                } else {
                                  message.info('暂无编译状态')
                                }
                              }}
                            >
                              刷新状态
                            </Button>
                          </div>
                          {snap.compileResult.data?.pdf_path && (
                            <div>
                              <Text type="secondary">PDF 路径：</Text>
                              <Text code>{snap.compileResult.data.pdf_path}</Text>
                            </div>
                          )}
                          {snap.compileResult.data?.warnings?.length ? (
                            <div className="latex-editor__compile-section">
                              <Text type="warning">警告：</Text>
                              <ul>
                                {snap.compileResult.data.warnings.map((warning, idx) => (
                                  <li key={`warning-${idx}`}>{warning}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {snap.compileResult.data?.errors?.length ? (
                            <div className="latex-editor__compile-section">
                              <Text type="danger">错误：</Text>
                              <ul>
                                {snap.compileResult.data.errors.map((errorMsg, idx) => (
                                  <li key={`error-${idx}`}>{errorMsg}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                          {snap.compileResult.data?.logs?.length ? (
                            <div className="latex-editor__compile-section latex-editor__compile-logs">
                              <Text strong>编译日志：</Text>
                              {snap.compileResult.data.logs.map((log, idx) => (
                                <div key={`log-${idx}`} className="latex-editor__compile-log-block">
                                  <div className="latex-editor__compile-log-header">
                                    <Tag color={log.returncode === 0 ? 'green' : 'red'}>
                                      退出码 {log.returncode}
                                    </Tag>
                                    <Text type="secondary">{log.command}</Text>
                                  </div>
                                  <pre className="latex-editor__compile-log">
                                    {log.log?.trim() || '(无日志输出)'}
                                  </pre>
                                </div>
                              ))}
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
                  ),
                },
              ]}
            />
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
        title="新建工作区"
        open={workspaceModalOpen}
        onOk={handleCreateWorkspace}
        onCancel={() => setWorkspaceModalOpen(false)}
        confirmLoading={workspaceSubmitting}
      >
        <Form layout="vertical">
          <Form.Item label="工作区名称">
            <Input
              placeholder="例如: paper-demo"
              value={newWorkspaceName}
              onChange={(event) => setNewWorkspaceName(event.target.value)}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={fileModalType === 'file' ? '新建文件' : '新建文件夹'}
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
          // 关闭时检查是否有已接受的修改
          if (acceptedDiffs.size > 0) {
            Modal.confirm({
              title: '确认关闭？',
              content: `您已接受 ${acceptedDiffs.size} 个文件的修改，关闭后将不保存这些修改。`,
              onOk: () => {
                setDiffModalOpen(false)
                setAllFileDiffs([])
                setCurrentDiffIndex(0)
                setAcceptedDiffs(new Set())
              },
            })
          } else {
            setDiffModalOpen(false)
            setAllFileDiffs([])
            setCurrentDiffIndex(0)
            setAcceptedDiffs(new Set())
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
                上一个
              </Button>
              <Button
                disabled={currentDiffIndex === allFileDiffs.length - 1}
                onClick={() => setCurrentDiffIndex(currentDiffIndex + 1)}
              >
                下一个
              </Button>
            </Space>
            <Space>
              <Button
                icon={<CloseOutlined />}
                onClick={() => {
                  // 拒绝当前文件的修改
                  const newAccepted = new Set(acceptedDiffs)
                  newAccepted.delete(currentDiffIndex)
                  setAcceptedDiffs(newAccepted)
                  // 如果还有下一个文件，跳到下一个
                  if (currentDiffIndex < allFileDiffs.length - 1) {
                    setCurrentDiffIndex(currentDiffIndex + 1)
                  } else if (currentDiffIndex > 0) {
                    setCurrentDiffIndex(currentDiffIndex - 1)
                  } else {
                    // 如果只有一个文件且拒绝了，关闭 Modal
                    setDiffModalOpen(false)
                    setAllFileDiffs([])
                    setCurrentDiffIndex(0)
                    setAcceptedDiffs(new Set())
                    message.info('已拒绝所有修改')
                  }
                }}
              >
                拒绝此文件
              </Button>
              <Button
                type={acceptedDiffs.has(currentDiffIndex) ? 'default' : 'primary'}
                icon={<CheckOutlined />}
                onClick={() => {
                  // 标记当前文件为已接受
                  const newAccepted = new Set(acceptedDiffs)
                  newAccepted.add(currentDiffIndex)
                  setAcceptedDiffs(newAccepted)
                  message.success(`已接受 ${allFileDiffs[currentDiffIndex].file_path} 的修改`)
                  // 如果还有下一个文件，自动跳到下一个
                  if (currentDiffIndex < allFileDiffs.length - 1) {
                    setCurrentDiffIndex(currentDiffIndex + 1)
                  }
                }}
              >
                {acceptedDiffs.has(currentDiffIndex) ? '已接受' : '接受此文件'}
              </Button>
              <Button
                type="primary"
                icon={<CheckOutlined />}
                disabled={acceptedDiffs.size === 0}
                onClick={async () => {
                  // 应用所有已接受的修改
                  const appliedFiles: string[] = []
                  for (const index of Array.from(acceptedDiffs)) {
                    const diff = allFileDiffs[index]
                    if (diff) {
                      if (!diff.is_truncated) {
                        latexAgentActions.updateFileContent(
                          diff.file_path,
                          diff.modified_content,
                        )
                      }
                      appliedFiles.push(diff.file_path)
                    }
                  }
                  setDiffModalOpen(false)
                  setAllFileDiffs([])
                  setCurrentDiffIndex(0)
                  setAcceptedDiffs(new Set())
                  message.success(`已应用 ${appliedFiles.length} 个文件的修改`)
                  // 重新加载受影响的文件
                  for (const filePath of appliedFiles) {
                    if (snap.openedFiles.includes(filePath)) {
                      await openFile(filePath)
                    }
                  }
                }}
              >
                应用已接受的修改 ({acceptedDiffs.size})
              </Button>
            </Space>
          </Space>
        }
      >
        <div className="latex-editor__diff-toolbar">
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
        <div className="latex-editor__diff-wrapper">
          <div className="latex-editor__diff-files">
            <List
              size="small"
              dataSource={allFileDiffs}
              renderItem={(diff, idx) => (
                <List.Item
                  key={`${diff.file_path}-${idx}`}
                  className={`latex-editor__diff-file ${
                    currentDiffIndex === idx ? 'latex-editor__diff-file--active' : ''
                  }`}
                  onClick={() => setCurrentDiffIndex(idx)}
                >
                  <Tooltip title={diff.file_path}>
                    <span className="latex-editor__diff-file-name">
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
                      已接受
                    </Tag>
                  )}
                </List.Item>
              )}
            />
          </div>
          <div className="latex-editor__diff-view">
            {allFileDiffs.length > 0 && allFileDiffs[currentDiffIndex] && (
              <DiffEditor
                key={currentDiffIndex}
                height="100%"
                theme="vs-dark"
                language="latex"
                original={allFileDiffs[currentDiffIndex].original_content}
                modified={allFileDiffs[currentDiffIndex].modified_content}
                options={{
                  readOnly: true,
                  renderSideBySide: diffViewMode === 'split',
                  minimap: { enabled: false },
                  fontSize: 14,
                  wordWrap: 'on',
                  automaticLayout: true,
                }}
              />
            )}
            {allFileDiffs[currentDiffIndex]?.is_truncated && (
              <Alert
                style={{ marginTop: 12 }}
                type="info"
                showIcon
                message="仅展示增量片段"
                description="为提升性能，已只展示与本次改动相关的上下文。接受后会自动重新加载完整文件。"
              />
            )}
          </div>
        </div>
        <div style={{ marginTop: 16, padding: 12, background: '#f0f0f0', borderRadius: 4 }}>
          <Text type="secondary">
            <strong>说明：</strong>
            左侧为原始内容，右侧为修改后的内容。红色表示删除，绿色表示新增。
            {allFileDiffs.length > 1 && (
              <span> 当前文件：{allFileDiffs[currentDiffIndex]?.file_path}</span>
            )}
            {acceptedDiffs.size > 0 && (
              <Tag color="green" style={{ marginLeft: 8 }}>
                已接受 {acceptedDiffs.size} / {allFileDiffs.length}
              </Tag>
            )}
          </Text>
        </div>
      </Modal>
    </>
  )
}

export default LatexEditorPage

