import { AxiosRequestConfig } from 'axios'
import { userState } from '@/store/user'
import { getDocStudioBase } from './env'
import { request } from './request'

const DOC_STUDIO_BASE = getDocStudioBase()

function withDocStudioConfig(config?: AxiosRequestConfig): AxiosRequestConfig {
  return {
    baseURL: DOC_STUDIO_BASE,
    ...config,
    headers: {
      ...(config?.headers ?? {}),
    },
  }
}

function encodeFilePath(path: string) {
  return path
    .split('/')
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}

export function getAgentAsyncEventsUrl(workspaceId: string, runId: string) {
  const baseUrl = `${DOC_STUDIO_BASE}/workspaces/${workspaceId}/edit/async/${runId}/events`
  const token = typeof userState.token === 'string' ? userState.token.trim() : ''
  if (!token) {
    return baseUrl
  }
  return `${baseUrl}?token=${encodeURIComponent(token)}`
}

type WorkspaceSummaryDTO = {
  workspace_id: string
  name: string
  main_file?: string
  file_count: number
  updated_at: number
}

type FileNodeDTO = {
  name: string
  path: string
  type: 'file' | 'directory'
  size?: number
  modified_at?: number
  children?: FileNodeDTO[]
}

type WorkspaceFilesDTO = {
  workspace_id: string
  files: FileNodeDTO[]
  main_file?: string
  config: Record<string, any>
}

type FileSaveResponseDTO = {
  path: string
  size: number
  modified_at: number
  encoding: string
}

type CompileResponseDTO = {
  success: boolean
  data?: {
    compiled: boolean
    compile_format?: 'latex' | 'markdown' | 'plaintext' | string
    target_path?: string
    preview_source?: string
    pdf_path?: string | null
    errors?: string[]
    warnings?: string[]
    logs?: {
      command: string
      returncode: number
      log: string
    }[]
  }
  error?: string | null
  summary?: string | null
}

function normalizeFileNode(node: FileNodeDTO): DocStudioAPI.FileNode {
  return {
    name: node.name,
    path: node.path,
    type: node.type,
    size: node.size,
    modifiedAt: node.modified_at,
    children: node.children?.map(normalizeFileNode),
  }
}

export async function listWorkspaces(options?: AxiosRequestConfig) {
  const { data } = await request.get<WorkspaceSummaryDTO[]>(
    '/workspaces',
    withDocStudioConfig(options),
  )
  // 防御性检查：确保 data 是数组
  if (!Array.isArray(data)) {
    console.error('listWorkspaces: Expected array but got:', data)
    return []
  }
  return data.map(
    (item): DocStudioAPI.WorkspaceSummary => ({
      workspaceId: item.workspace_id,
      name: item.name,
      mainFile: item.main_file,
      fileCount: item.file_count,
      updatedAt: item.updated_at,
    }),
  )
}

export async function listAgentKnowledgeBases(options?: AxiosRequestConfig) {
  const { data } = await request.get<DocStudioAPI.KnowledgeBaseSummary[]>(
    '/knowledge-bases',
    withDocStudioConfig(options),
  )
  return data
}

export async function createWorkspace(
  params: { name: string; workspaceId?: string; config?: Record<string, any> },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.post<{
    workspace_id: string
    name: string
    main_file?: string
    file_count: number
    updated_at: number
    config: Record<string, any>
  }>(
    '/workspaces',
    {
      name: params.name,
      workspace_id: params.workspaceId,
      config: params.config,
    },
    withDocStudioConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as DocStudioAPI.WorkspaceDetail
}

export async function fetchWorkspace(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.get<{
    workspace_id: string
    name: string
    main_file?: string
    file_count: number
    updated_at: number
    config: Record<string, any>
  }>(`/workspaces/${params.workspaceId}`, withDocStudioConfig(options))
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as DocStudioAPI.WorkspaceDetail
}

export async function updateWorkspace(
  params: { workspaceId: string; name?: string; config?: Record<string, any> },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.put<{
    workspace_id: string
    name: string
    main_file?: string
    file_count: number
    updated_at: number
    config: Record<string, any>
  }>(
    `/workspaces/${params.workspaceId}`,
    {
      name: params.name,
      config: params.config,
    },
    withDocStudioConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as DocStudioAPI.WorkspaceDetail
}

export async function bindWorkspaceSession(
  params: { workspaceId: string; sessionId?: string | null },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.put<{
    workspace_id: string
    name: string
    main_file?: string
    file_count: number
    updated_at: number
    config: Record<string, any>
  }>(
    `/workspaces/${params.workspaceId}/session`,
    {
      session_id: params.sessionId || null,
    },
    withDocStudioConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as DocStudioAPI.WorkspaceDetail
}

export async function deleteWorkspace(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.delete<{ deleted: boolean; workspace_id: string }>(
    `/workspaces/${params.workspaceId}`,
    withDocStudioConfig(options),
  )
  return data
}

export async function listWorkspaceMessages(
  params: { workspaceId: string; sessionId: string; page?: number; pageSize?: number },
  options?: AxiosRequestConfig,
) {
  const { workspaceId, sessionId, page = 1, pageSize = 200 } = params
  const { data } = await request.get<{
    total: number
    page: number
    pageSize: number
    items: {
      message_id: string
      session_id: string
      user_question: string
      model_answer: string
      create_time: string
      retrieval_content?: string
    }[]
  }>(
    `/workspaces/${workspaceId}/messages`,
    withDocStudioConfig({
      ...options,
      params: { session_id: sessionId, page, page_size: pageSize },
    }),
  )
  return data
}

/** 调试：获取 Agent 消息原始内容及换行分析，用于排查 Markdown 渲染间距问题 */
export async function getWorkspaceMessagesDebug(
  params: { workspaceId: string; sessionId: string },
  options?: AxiosRequestConfig,
) {
  const { workspaceId, sessionId } = params
  const { data } = await request.get<{
    session_id: string
    error?: string
    items: {
      message_id: string
      content_length: number
      newline_count: number
      double_newline_count: number
      triple_plus_newline_count: number
      raw_repr_sample: string
      raw_with_markers: string
    }[]
  }>(
    `/workspaces/${workspaceId}/messages/debug`,
    withDocStudioConfig({ ...options, params: { session_id: sessionId } }),
  )
  return data
}

export async function fetchWorkspaceFiles(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.get<WorkspaceFilesDTO>(
    `/workspaces/${params.workspaceId}/files`,
    withDocStudioConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    files: dto.files.map(normalizeFileNode),
    mainFile: dto.main_file,
    config: dto.config,
  } as DocStudioAPI.WorkspaceFilesResponse
}

export async function fetchFileContent(
  params: { workspaceId: string; path: string },
  options?: AxiosRequestConfig,
) {
  const encodedPath = encodeFilePath(params.path)
  const { data } = await request.get<DocStudioAPI.FileContentResponse>(
    `/workspaces/${params.workspaceId}/files/${encodedPath}`,
    withDocStudioConfig(options),
  )
  return data
}

export async function updateFileContent(
  params: { workspaceId: string; path: string; content: string; encoding?: string },
  options?: AxiosRequestConfig,
) {
  const encodedPath = encodeFilePath(params.path)
  const { data: dto } = await request.put<FileSaveResponseDTO>(
    `/workspaces/${params.workspaceId}/files/${encodedPath}`,
    {
      content: params.content,
      encoding: params.encoding || 'utf-8',
    },
    withDocStudioConfig(options),
  )
  const result: DocStudioAPI.SaveFileResponse = {
    path: dto.path,
    size: dto.size,
    modified_at: dto.modified_at,
    encoding: dto.encoding,
  }
  return result
}

export async function runAgentTask(
  params: {
    workspaceId: string
    userIntent: string
    context?: Record<string, any>
    options?: Record<string, any>
    collectTrainingData?: boolean
    knowledgeBaseId?: number
    knowledgeBaseName?: string
  },
  requestOptions?: AxiosRequestConfig,
) {
  const {
    workspaceId,
    userIntent,
    context,
    collectTrainingData,
    knowledgeBaseId,
    knowledgeBaseName,
    options: extraOptions,
  } = params
  const { data } = await request.post<DocStudioAPI.AgentResponse>(
    `/workspaces/${workspaceId}/edit`,
    {
      user_intent: userIntent,
      target_location: context,
      options: extraOptions,
      collect_training_data: collectTrainingData ?? false,
      knowledge_base_id: knowledgeBaseId,
      knowledge_base_name: knowledgeBaseName,
    },
    withDocStudioConfig(requestOptions),
  )
  return data
}

export async function runAgentTaskAsync(
  params: {
    workspaceId: string
    userIntent: string
    context?: Record<string, any>
    options?: Record<string, any>
    collectTrainingData?: boolean
    knowledgeBaseId?: number
    knowledgeBaseName?: string
  },
  requestOptions?: AxiosRequestConfig,
) {
  const {
    workspaceId,
    userIntent,
    context,
    collectTrainingData,
    knowledgeBaseId,
    knowledgeBaseName,
    options: extraOptions,
  } = params
  const { data } = await request.post<{
    run_id?: string
    runId?: string
    status?: string
  }>(
    `/workspaces/${workspaceId}/edit/async`,
    {
      user_intent: userIntent,
      target_location: context,
      options: extraOptions,
      collect_training_data: collectTrainingData ?? false,
      knowledge_base_id: knowledgeBaseId,
      knowledge_base_name: knowledgeBaseName,
    },
    withDocStudioConfig(requestOptions),
  )
  return {
    runId: data.runId || data.run_id || '',
    status: data.status,
  }
}

export async function fetchAgentRunStatus(
  params: { workspaceId: string; runId: string },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.get<{
    run_id: string
    status: string
    result?: DocStudioAPI.AgentResponse
    error?: string
    updated_at?: number
  }>(`/workspaces/${params.workspaceId}/edit/async/${params.runId}`, withDocStudioConfig(requestOptions))
  return data
}

export async function cancelAgentRun(
  params: { workspaceId: string; runId: string },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.post<{
    run_id?: string
    runId?: string
    status?: string
  }>(
    `/workspaces/${params.workspaceId}/edit/async/${params.runId}/cancel`,
    {},
    withDocStudioConfig(requestOptions),
  )
  return {
    runId: data.runId || data.run_id || '',
    status: data.status || 'cancelled',
  }
}

export async function respondAgentRunInteraction(
  params: {
    workspaceId: string
    runId: string
    interactionId: string
    decision: string
    note?: string
  },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.post<{
    run_id?: string
    status?: string
    accepted?: boolean
    decision?: string
  }>(
    `/workspaces/${params.workspaceId}/edit/async/${params.runId}/interactions/respond`,
    {
      interaction_id: params.interactionId,
      decision: params.decision,
      note: params.note,
    },
    withDocStudioConfig(requestOptions),
  )
  return {
    runId: data.run_id || params.runId,
    status: data.status || 'unknown',
    accepted: Boolean(data.accepted),
    decision: String(data.decision || params.decision),
  }
}

// Backward-compatible API alias.
export async function confirmAgentRunAction(
  params: {
    workspaceId: string
    runId: string
    confirmationId: string
    decision: 'approve' | 'reject'
    note?: string
  },
  requestOptions?: AxiosRequestConfig,
) {
  return respondAgentRunInteraction(
    {
      workspaceId: params.workspaceId,
      runId: params.runId,
      interactionId: params.confirmationId,
      decision: params.decision,
      note: params.note,
    },
    requestOptions,
  )
}

export async function listOperations(
  params: { workspaceId: string },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.get<DocStudioAPI.OperationSummary[]>(
    `/workspaces/${params.workspaceId}/operations`,
    withDocStudioConfig(requestOptions),
  )
  return data
}

export async function revertOperation(
  params: { workspaceId: string; operationId: string; files?: string[] },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.post<DocStudioAPI.RevertOperationResponse>(
    `/workspaces/${params.workspaceId}/operations/${params.operationId}/revert`,
    {
      files: params.files,
    },
    withDocStudioConfig(requestOptions),
  )
  return data
}

export async function restoreCheckpoint(
  params: { workspaceId: string; runId: string },
  requestOptions?: AxiosRequestConfig,
) {
  const { data } = await request.post<{
    run_id: string
    restored_files: string[]
    skipped_files: string[]
  }>(
    `/workspaces/${params.workspaceId}/edit/async/${params.runId}/restore-checkpoint`,
    {},
    withDocStudioConfig(requestOptions),
  )
  return data
}

export async function rewindConversation(
  params: { workspaceId: string; keepUserTurns?: number; beforeMessageId?: string },
  requestOptions?: AxiosRequestConfig,
) {
  const payload: Record<string, any> = {}
  if (params.beforeMessageId) {
    payload.before_message_id = params.beforeMessageId
  } else {
    payload.keep_user_turns = Math.max(0, Math.floor(params.keepUserTurns || 0))
  }
  const { data } = await request.post<{
    session_id?: string
    total_turns?: number
    kept_turns?: number
    deleted_turns?: number
  }>(
    `/workspaces/${params.workspaceId}/conversation/rewind`,
    payload,
    withDocStudioConfig(requestOptions),
  )
  return data
}

export async function fetchMetricsSummary(options?: AxiosRequestConfig) {
  const { data } = await request.get<DocStudioAPI.MetricsSummary>(
    '/metrics/summary',
    withDocStudioConfig(options),
  )
  return data
}

export async function fetchLlmHealth(options?: AxiosRequestConfig) {
  const { data } = await request.get<DocStudioAPI.LlmHealthSummary>(
    '/llm/health',
    withDocStudioConfig(options),
  )
  return data
}

export async function fetchOperationSnapshotFile(
  params: { workspaceId: string; operationId: string; filePath: string; version?: 'before' | 'after' },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.get<DocStudioAPI.FileContentResponse>(
    `/workspaces/${params.workspaceId}/operations/${params.operationId}/snapshot`,
    withDocStudioConfig({
      ...options,
      params: {
        file_path: params.filePath,
        version: params.version || 'before',
      },
    }),
  )
  return data
}

export async function compileWorkspace(
  params: { workspaceId: string; mainFile?: string; compiler?: string },
  options?: AxiosRequestConfig,
) {
  const { workspaceId, ...body } = params
  const { data: dto } = await request.post<CompileResponseDTO>(
    `/workspaces/${workspaceId}/compile`,
    body,
    withDocStudioConfig(options),
  )
  return {
    success: dto.success,
    data: dto.data
      ? {
          compiled: dto.data.compiled,
            compile_format: dto.data.compile_format,
            target_path: dto.data.target_path,
            preview_source: dto.data.preview_source,
          pdf_path: dto.data.pdf_path,
          errors: dto.data.errors,
          warnings: dto.data.warnings,
          logs: dto.data.logs,
        }
      : undefined,
    summary: dto.summary,
    error: dto.error,
  } as DocStudioAPI.CompileResult
}

export async function createFileOrDirectory(
  params: { workspaceId: string; path: string; type: 'file' | 'directory'; content?: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.post<DocStudioAPI.FileCreateResponse>(
    `/workspaces/${params.workspaceId}/files`,
    {
      path: params.path,
      type: params.type,
      content: params.content,
    },
    withDocStudioConfig(options),
  )
  return data
}

export async function deleteFile(
  params: { workspaceId: string; path: string },
  options?: AxiosRequestConfig,
) {
  const encodedPath = encodeFilePath(params.path)
  const { data } = await request.delete<{ deleted: boolean; path: string }>(
    `/workspaces/${params.workspaceId}/files/${encodedPath}`,
    withDocStudioConfig(options),
  )
  return data
}

export async function renameFileOrDirectory(
  params: { workspaceId: string; sourcePath: string; targetPath: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.post<{
    moved: boolean
    source_path: string
    target_path: string
    type: 'file' | 'directory'
  }>(
    `/workspaces/${params.workspaceId}/files/rename`,
    {
      source_path: params.sourcePath,
      target_path: params.targetPath,
    },
    withDocStudioConfig(options),
  )
  return {
    moved: data.moved,
    sourcePath: data.source_path,
    targetPath: data.target_path,
    type: data.type,
  }
}

export async function uploadFile(
  params: { workspaceId: string; directory?: string; file: File },
  options?: AxiosRequestConfig,
) {
  const formData = new FormData()
  formData.append('file', params.file)
  if (params.directory) {
    formData.append('directory', params.directory)
  }
  const { data } = await request.post<DocStudioAPI.UploadResponse>(
    `/workspaces/${params.workspaceId}/files/upload`,
    formData,
    withDocStudioConfig({
      ...options,
      headers: {
        'Content-Type': 'multipart/form-data',
        ...(options?.headers ?? {}),
      },
    }),
  )
  return data
}

export function buildDownloadUrl(workspaceId: string, filePath: string) {
  const encoded = encodeURIComponent(filePath)
  return `${DOC_STUDIO_BASE}/workspaces/${workspaceId}/download?file_path=${encoded}`
}

export function buildPdfUrl(workspaceId: string, pdfPath?: string) {
  const query = pdfPath ? `?pdf_path=${encodeURIComponent(pdfPath)}` : ''
  return `${DOC_STUDIO_BASE}/workspaces/${workspaceId}/pdf${query}`
}

// 下载 PDF（带 header 的安全请求）
export async function downloadPdf(
  params: { workspaceId: string; pdfPath?: string },
  options?: AxiosRequestConfig,
) {
  // 添加时间戳参数防止缓存
  const timestamp = Date.now()
  const query = params.pdfPath 
    ? `?pdf_path=${encodeURIComponent(params.pdfPath)}&_t=${timestamp}`
    : `?_t=${timestamp}`
  const response = await request.get(
    `/workspaces/${params.workspaceId}/pdf${query}`,
    withDocStudioConfig({
      ...options,
      responseType: 'blob',
      headers: {
        'Cache-Control': 'no-cache',
        ...options?.headers,
      },
    }),
  )
  return response.data as Blob
}

// 下载文件（带 header 的安全请求）
export async function downloadFile(
  params: { workspaceId: string; filePath: string },
  options?: AxiosRequestConfig,
) {
  const encoded = encodeURIComponent(params.filePath)
  const response = await request.get(
    `/workspaces/${params.workspaceId}/download?file_path=${encoded}`,
    withDocStudioConfig({
      ...options,
      responseType: 'blob',
    }),
  )
  return response.data as Blob
}

export async function sendAgentFeedback(
  params: { traceId: string; rating: DocStudioAPI.AgentFeedbackRating; comment?: string },
  options?: AxiosRequestConfig,
) {
  return request.post(
    '/feedback',
    {
      trace_id: params.traceId,
      rating: params.rating,
      comment: params.comment,
    },
    withDocStudioConfig(options),
  )
}

export async function fetchCompileStatus(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.get<DocStudioAPI.CompileStatus>(
    `/workspaces/${params.workspaceId}/compile-status`,
    withDocStudioConfig(options),
  )
  return data
}
