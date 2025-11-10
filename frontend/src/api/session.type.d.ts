declare namespace API {
  interface Session {
    created_at: string
    session_id: string
    session_name: string
    updated_at: string
    // user_id: string
  }

  interface SessionDefaults {
    retrievalStrategy: 'multi_stage'
    rerankerStrategy: 'none' | 'supervised' | 'rl'
    topK: number
    language: 'zh' | 'en'
    streaming: boolean
    useSessionKnowledgeBase: boolean
    useUserKnowledgeBase: boolean
    userKnowledgeBaseId?: number | null
  }

  interface CreateSessionResponse {
    sessionId: string
    kbId?: number | null
    ephemeral: boolean
    defaults: SessionDefaults
  }

  interface SessionDetail {
    sessionId: string
    kbId?: number | null
    sessionName: string
  }

  interface ChatItem {
    id: number
    role: import('@/configs').ChatRole
    type: import('@/configs').ChatType
    loading?: boolean
    error?: string
    content?: string
    think?: string

    documents?: Document[]
    reference?: Reference[]
    recommended_questions?: string[]
    attachments?: ChatAttachment[]
  }

  interface ChatAttachment {
    id: number
    title: string
    knowledgeBaseId?: number
  }

  interface Document {
    document_id: string
    document_name: string
    content_with_weight: string
  }

  interface Reference {
    id: string
    document_id: string
    document_name: string
    content_with_weight: string
    positions: number[][]
  }
}
