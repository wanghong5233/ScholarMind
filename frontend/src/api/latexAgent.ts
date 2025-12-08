import { AxiosRequestConfig } from 'axios'
import { request } from './request'

const LATEX_AGENT_BASE =
  (import.meta.env.VITE_LATEX_AGENT_BASE as string | undefined) ||
  (import.meta.env.DEV ? 'http://127.0.0.1:8003/api' : '/api/latex-agent')
const DEFAULT_LATEX_USER_ID =
  (import.meta.env.VITE_LATEX_DEFAULT_USER_ID as string | undefined) || '1'

function withLatexConfig(config?: AxiosRequestConfig): AxiosRequestConfig {
  return {
    baseURL: LATEX_AGENT_BASE,
    ...config,
    headers: {
      'X-User-Id': DEFAULT_LATEX_USER_ID,
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

function normalizeFileNode(node: FileNodeDTO): LatexAgentAPI.FileNode {
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
    withLatexConfig(options),
  )
  // 防御性检查：确保 data 是数组
  if (!Array.isArray(data)) {
    console.error('listWorkspaces: Expected array but got:', data)
    return []
  }
  return data.map(
    (item): LatexAgentAPI.WorkspaceSummary => ({
      workspaceId: item.workspace_id,
      name: item.name,
      mainFile: item.main_file,
      fileCount: item.file_count,
      updatedAt: item.updated_at,
    }),
  )
}

export async function listAgentKnowledgeBases(options?: AxiosRequestConfig) {
  const { data } = await request.get<LatexAgentAPI.KnowledgeBaseSummary[]>(
    '/knowledge-bases',
    withLatexConfig(options),
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
    withLatexConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as LatexAgentAPI.WorkspaceDetail
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
  }>(`/workspaces/${params.workspaceId}`, withLatexConfig(options))
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as LatexAgentAPI.WorkspaceDetail
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
    withLatexConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    name: dto.name,
    mainFile: dto.main_file,
    fileCount: dto.file_count,
    updatedAt: dto.updated_at,
    config: dto.config,
  } as LatexAgentAPI.WorkspaceDetail
}

export async function deleteWorkspace(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.delete<{ deleted: boolean; workspace_id: string }>(
    `/workspaces/${params.workspaceId}`,
    withLatexConfig(options),
  )
  return data
}

export async function fetchWorkspaceFiles(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data: dto } = await request.get<WorkspaceFilesDTO>(
    `/workspaces/${params.workspaceId}/files`,
    withLatexConfig(options),
  )
  return {
    workspaceId: dto.workspace_id,
    files: dto.files.map(normalizeFileNode),
    mainFile: dto.main_file,
    config: dto.config,
  } as LatexAgentAPI.WorkspaceFilesResponse
}

export async function fetchFileContent(
  params: { workspaceId: string; path: string },
  options?: AxiosRequestConfig,
) {
  const encodedPath = encodeFilePath(params.path)
  const { data } = await request.get<LatexAgentAPI.FileContentResponse>(
    `/workspaces/${params.workspaceId}/files/${encodedPath}`,
    withLatexConfig(options),
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
    withLatexConfig(options),
  )
  const result: LatexAgentAPI.SaveFileResponse = {
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
  const { data } = await request.post<LatexAgentAPI.AgentResponse>(
    `/workspaces/${workspaceId}/edit`,
    {
      user_intent: userIntent,
      target_location: context,
      options: extraOptions,
      collect_training_data: collectTrainingData ?? false,
      knowledge_base_id: knowledgeBaseId,
      knowledge_base_name: knowledgeBaseName,
    },
    withLatexConfig(requestOptions),
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
    withLatexConfig(options),
  )
  return {
    success: dto.success,
    data: dto.data
      ? {
          compiled: dto.data.compiled,
          pdf_path: dto.data.pdf_path,
          errors: dto.data.errors,
          warnings: dto.data.warnings,
          logs: dto.data.logs,
        }
      : undefined,
    summary: dto.summary,
    error: dto.error,
  } as LatexAgentAPI.CompileResult
}

export async function createFileOrDirectory(
  params: { workspaceId: string; path: string; type: 'file' | 'directory'; content?: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.post<LatexAgentAPI.FileCreateResponse>(
    `/workspaces/${params.workspaceId}/files`,
    {
      path: params.path,
      type: params.type,
      content: params.content,
    },
    withLatexConfig(options),
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
    withLatexConfig(options),
  )
  return data
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
  const { data } = await request.post<LatexAgentAPI.UploadResponse>(
    `/workspaces/${params.workspaceId}/files/upload`,
    formData,
    withLatexConfig({
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
  return `${LATEX_AGENT_BASE}/workspaces/${workspaceId}/download?file_path=${encoded}`
}

export function buildPdfUrl(workspaceId: string, pdfPath?: string) {
  const query = pdfPath ? `?pdf_path=${encodeURIComponent(pdfPath)}` : ''
  return `${LATEX_AGENT_BASE}/workspaces/${workspaceId}/pdf${query}`
}

// 下载 PDF（带 header 的安全请求）
export async function downloadPdf(
  params: { workspaceId: string; pdfPath?: string },
  options?: AxiosRequestConfig,
) {
  const query = params.pdfPath ? `?pdf_path=${encodeURIComponent(params.pdfPath)}` : ''
  const response = await request.get(
    `/workspaces/${params.workspaceId}/pdf${query}`,
    withLatexConfig({
      ...options,
      responseType: 'blob',
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
    withLatexConfig({
      ...options,
      responseType: 'blob',
    }),
  )
  return response.data as Blob
}

export async function sendAgentFeedback(
  params: { traceId: string; rating: LatexAgentAPI.AgentFeedbackRating; comment?: string },
  options?: AxiosRequestConfig,
) {
  return request.post(
    '/feedback',
    {
      trace_id: params.traceId,
      rating: params.rating,
      comment: params.comment,
    },
    withLatexConfig(options),
  )
}

export async function fetchCompileStatus(
  params: { workspaceId: string },
  options?: AxiosRequestConfig,
) {
  const { data } = await request.get<LatexAgentAPI.CompileStatus>(
    `/workspaces/${params.workspaceId}/compile-status`,
    withLatexConfig(options),
  )
  return data
}

