declare namespace API {
  type Result<T> = T & {
    status: 'success' | 'error'
    message: string
  }
}

declare namespace LatexAgentAPI {
  interface WorkspaceSummary {
    workspaceId: string
    name: string
    mainFile?: string
    fileCount: number
    updatedAt: number
  }

  interface WorkspaceDetail extends WorkspaceSummary {
    config: Record<string, any>
  }

  interface KnowledgeBaseSummary {
    id: number
    name: string
    description?: string | null
    is_ephemeral?: boolean
    created_at?: string
    updated_at?: string
  }

  interface FileNode {
    name: string
    path: string
    type: 'file' | 'directory'
    size?: number
    modifiedAt?: number
    children?: FileNode[]
  }

  interface WorkspaceFilesResponse {
    workspaceId: string
    files: FileNode[]
    mainFile?: string
    config: Record<string, any>
  }

  interface FileContentResponse {
    path: string
    content: string
    encoding: string
  }

  interface SaveFileResponse {
    path: string
    size: number
    modified_at: number
    encoding: string
  }

  interface FileCreateResponse {
    path: string
    type: 'file' | 'directory'
  }

  interface UploadResponse {
    path: string
    size: number
  }

  interface AgentChange {
    file: string
    position?: {
      line?: number
      character?: number
    }
    type?: string
    content?: string
  }

  interface AgentStep {
    type: string
    content: string
    tool?: string
    parameters?: Record<string, any>
    result?: Record<string, any>
    timestamp?: number
  }

  interface FileDiff {
    file_path: string
    original_content: string
    modified_content: string
    is_truncated?: boolean
  }

  interface AgentPlanStatus {
    steps: string[]
    completed_steps: number
    notes?: string | null
  }

  interface AgentResponse {
    success: boolean
    changes: AgentChange[]
    file_diffs?: FileDiff[]  // 完整的文件对比（用于 UI diff 预览）
    bibliography_updates?: Record<string, any>
    execution_history: AgentStep[]
    intent_type?: string
    intent_confidence?: number
    plan?: AgentPlanStatus | null
    warnings?: string[]
    trace_id?: string
  }

  interface CompileLog {
    command: string
    returncode: number
    log: string
  }

  interface CompileResult {
    success: boolean
    data?: {
      compiled: boolean
      pdf_path?: string | null
      errors?: string[]
      warnings?: string[]
      logs?: CompileLog[]
    }
    error?: string | null
    summary?: string | null
  }

  interface CompileStatus {
    status?: string
    timestamp?: number
    result?: {
      success: boolean
      summary?: string | null
      data?: CompileResult['data']
      error?: string | null
    }
  }

  type AgentFeedbackRating = 'thumbs_up' | 'thumbs_down'
}
