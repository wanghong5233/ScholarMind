import { AxiosRequestConfig } from 'axios'
import { request } from './request'

export function list(params?: {}, options?: AxiosRequestConfig) {
  return request.get<{
    sessions: API.Session[]
  }>('history/get_sessions', {
    ...options,
    params,
  })
}

export function detail(
  params: {
    session_id: string
  },
  options?: AxiosRequestConfig,
) {
  return request.get<
    {
      created_at: string
      message_id: string
      session_id: string
      user_question: string
      model_answer: string
      think?: string
      documents?: string
      recommended_questions?: string
      retrieval_content?: string
    }[]
  >('history/get_messages', {
    ...options,
    params,
  })
}

export function info(
  params: { sessionId: string },
  options?: AxiosRequestConfig,
) {
  const { sessionId, ...rest } = params
  return request.get<API.SessionDetail>(`sessions/${sessionId}`, {
    ...options,
    params: rest,
  })
}

export function getDefaults(
  params: { sessionId: string },
  options?: AxiosRequestConfig,
) {
  const { sessionId, ...rest } = params
  return request.get<API.SessionDefaults>(`sessions/${sessionId}/defaults`, {
    ...options,
    params: rest,
  })
}

export function updateDefaults(
  params: { sessionId: string; defaults: API.SessionDefaults },
  options?: AxiosRequestConfig,
) {
  const { sessionId, defaults } = params
  return request.put<API.SessionDefaults>(
    `sessions/${sessionId}/defaults`,
    defaults,
    options,
  )
}

export function create(
  params: {
    kbId?: number
    ephemeral?: boolean
    defaults?: Partial<API.SessionDefaults>
  } = {
    ephemeral: true,
  },
  options?: AxiosRequestConfig,
) {
  const payload = {
    ephemeral: params?.kbId ? false : params.ephemeral ?? true,
    kbId: params?.kbId,
    defaults: params?.defaults,
  }
  return request.post<API.CreateSessionResponse>('sessions', payload, options)
}

export function chat(
  params: {
    id: string
    question: string
    stream?: boolean
    focusDocIds?: number[]
    topK?: number
    temperature?: number
    maxTokens?: number
    compressHistory?: boolean
    indexMode?: 'auto' | 'session_only' | 'global_only' | 'hybrid'
    replaceFromMessageId?: string
  },
  options?: AxiosRequestConfig,
) {
  const { id, ...body } = params
  return request.post<ReadableStream>(
    `sessions/${id}/ask`,
    {
      stream: true,
      ...body,
    },
    {
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      responseType: 'stream',
      adapter: 'fetch',
      loading: false,
      ...options,
    },
  )
}

export function upload(
  params: {
    sessionId: string
    file: File
  },
  options?: AxiosRequestConfig,
) {
  const { sessionId, file } = params
  const formData = new FormData()
  formData.append('file', file)
  return request.post(`sessions/${sessionId}/upload`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    ...options,
  })
}

export function uploadForContext(
  params: {
    sessionId: string
    file: File
  },
  options?: AxiosRequestConfig,
) {
  const { sessionId, file } = params
  const formData = new FormData()
  formData.append('file', file)
  return request.post<{ filename: string; content: string }>(
    `sessions/${sessionId}/upload-for-context`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      ...options,
    },
  )
}

export function remove(
  params: { sessionId: string },
  options?: AxiosRequestConfig,
) {
  const { sessionId, ...rest } = params
  return request.delete<{ deleted: boolean; messages_deleted?: number }>(
    `sessions/${sessionId}`,
    {
      ...options,
      params: rest,
    },
  )
}
