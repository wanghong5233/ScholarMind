import * as api from '@/api'
import IconEdit from '@/assets/chat/edit.svg'
import Markdown from '@/components/markdown'
import ComPageLayout from '@/components/page-layout'
import ComSender from '@/components/sender'
import { ChatRole, ChatType } from '@/configs'
import { deviceActions } from '@/store/device'
import { usePageTransport } from '@/utils'
import { createNotebookNoteFile } from '@/utils/notebook'
import { useMount, useRequest, useUnmount } from 'ahooks'
import {
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { proxy, useSnapshot } from 'valtio'
import { sessionActions } from '../../store/session'
import ChatMessage from './component/chat-message'
import Citations from './component/citations'
import Contracts from './component/contracts'
import ChatDrawer from './component/drawer'
import NotebookDrawer from './component/notebook-drawer'
import Source from './component/source'
import styles from './index.module.scss'
import { createChatId, createChatIdText, transportToChatEnter } from './shared'
import type { KnowledgeBase } from '@/api/repository'
import type { DeepResearchCitation, DeepResearchRequest, ProgressEvent } from '@/api/deepResearch'

type ChatItemWithToken = API.ChatItem & { __openToken?: number }

const DEEP_RESEARCH_DEFAULTS = {
  mode: 'queue' as const,
  depth: 2,
  breadth: 5,
  max_parallel: 1,
  max_iterations: 4,
  top_k: 6,
  index_mode: 'auto',
  use_web_search: false,
  use_paper_search: false,
  use_code_exec: false,
}

const DEEP_RESEARCH_INDEX_OPTIONS = [
  { label: 'auto', value: 'auto' },
  { label: 'session_only', value: 'session_only' },
  { label: 'global_only', value: 'global_only' },
  { label: 'hybrid', value: 'hybrid' },
]

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

function escapeYaml(value: string) {
  return (value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function extractReportSummary(markdown: string) {
  const lines = markdown
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  if (!lines.length) return ''
  const summaryLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('#') && summaryLines.length) break
    summaryLines.push(line.replace(/^#+\s*/, ''))
    if (summaryLines.join(' ').length > 360) break
  }
  return summaryLines.join(' ').trim()
}

function buildCitationBlock(citations: DeepResearchCitation[] = []) {
  if (!citations.length) return ''
  const lines = ['## 引用']
  citations.forEach((item, index) => {
    const ref = item.ref_number ?? index + 1
    const title = item.title || item.url || item.citation_id
    const suffix = item.url ? ` - ${item.url}` : ''
    lines.push(`- [${ref}] ${title}${suffix}`)
  })
  return lines.join('\n')
}

function buildDeepResearchNoteMarkdown(payload: {
  topic: string
  reportMarkdown: string
  summary: string
  sessionId?: string
  researchId?: string
  citations?: DeepResearchCitation[]
}) {
  const createdAt = dayjs().toISOString()
  const tags = ['deepresearch', 'report']
  const tagsYaml = tags.map((tag) => `"${escapeYaml(tag)}"`).join(', ')
  const frontMatter = [
    '---',
    `title: "${escapeYaml(payload.topic)}"`,
    `summary: "${escapeYaml(payload.summary || payload.topic)}"`,
    `tags: [${tagsYaml}]`,
    `session_id: "${escapeYaml(payload.sessionId || '')}"`,
    `research_id: "${escapeYaml(payload.researchId || '')}"`,
    `created_at: "${createdAt}"`,
    `source_excerpt: "${escapeYaml(payload.summary || payload.topic)}"`,
    '---',
  ].join('\n')
  const reportBody = payload.reportMarkdown.trim()
  const citationBlock = buildCitationBlock(payload.citations)
  return [frontMatter, reportBody, citationBlock].filter(Boolean).join('\n\n').trim()
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
  const [pendingAttachments, setPendingAttachments] = useState<
    API.ChatAttachment[]
  >([])
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [sessionDefaults, setSessionDefaults] =
    useState<API.SessionDefaults | null>(null)
  const [composerValue, setComposerValue] = useState('')
  const [composerFocusKey, setComposerFocusKey] = useState(0)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [updatingDefaults, setUpdatingDefaults] = useState(false)
  const [researchMode, setResearchMode] = useState<'chat' | 'deep'>('chat')
  const [notebookOpen, setNotebookOpen] = useState(false)
  const [researchSuggestion, setResearchSuggestion] = useState<{
    topic: string
    reason: string
  } | null>(null)
  const [editingResearchItem, setEditingResearchItem] =
    useState<API.ChatItem | null>(null)
  const [researchForm] = Form.useForm<DeepResearchRequest>()
  const [editingContext, setEditingContext] = useState<{
    messageId: string
  } | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<any> | null>(null)
  const researchStreamRef = useRef<Map<number, EventSource>>(new Map())
  const researchStreamTimerRef = useRef<Map<number, number>>(new Map())
  const researchStreamEventIdRef = useRef<Map<number, string>>(new Map())
  const researchStreamRetryRef = useRef<Map<number, number>>(new Map())
  const researchPersistTimerRef = useRef<number | null>(null)
  const researchRestorePendingRef = useRef(false)
  const suggestionDismissedRef = useRef<Set<string>>(new Set())
  const lastSuggestionTopicRef = useRef<string>('')
  const openCitationsPanel = useCallback(
    (item: API.ChatItem | null) => {
      if (!item) {
        setCurrentChatItemState(null)
        return
      }
      setCurrentChatItemState({ ...item, __openToken: Date.now() })
    },
    [],
  )

  const getSelectionText = useCallback(() => {
    if (typeof window === 'undefined') return ''
    return window.getSelection()?.toString() || ''
  }, [])

  const handleOpenNotebook = useCallback(() => {
    setNotebookOpen(true)
  }, [])

  const handleOpenIdeaGen = useCallback(() => {
    if (!id) {
      message.warning('需要会话 ID 才能发起 IdeaGen')
      return
    }
    const selection = getSelectionText().trim()
    navigate('/idea-generation', {
      state: {
        prefill: { sessionId: id },
        selection: selection || undefined,
      },
    })
  }, [getSelectionText, id, navigate])

  const researchPersistKey = useMemo(
    () => (id ? `deep-research-cards:${id}` : ''),
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
        data.forEach((item) => {
          if (item.user_question) {
            // 尝试从 retrieval_content 中提取 context_files
            let attachments: API.ChatAttachment[] | undefined
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
            
            chat.list.push({
              id: createChatId(),
              role: ChatRole.User,
              type: ChatType.Text,
              content: item.user_question,
              attachments: attachments,
              message_id: item.message_id,
            })
          }

          if (item.model_answer) {
            const map = new Map<string, API.Document>()
            let reference: API.Reference[] = []
            let recommended_questions: string[] = []
            let retrievalData: Record<string, any> | undefined
            let fallbackKbId: number | undefined
            if (item.retrieval_content) {
              try {
                retrievalData = JSON.parse(item.retrieval_content)
                const kbIdValue = retrievalData?.knowledge_base_id
                if (
                  typeof kbIdValue === 'number' ||
                  (typeof kbIdValue === 'string' && kbIdValue)
                ) {
                  fallbackKbId = Number(kbIdValue)
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
              content: item.model_answer,
              think: item.think,
              reference: reference,
              documents: documents?.length ? documents : undefined,
              recommended_questions: recommended_questions?.length
                ? recommended_questions
                : undefined,
              message_id: item.message_id,
            })
          }
        })

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
        setSessionDefaults(data ?? null)
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
    if (researchPersistTimerRef.current) {
      window.clearTimeout(researchPersistTimerRef.current)
      researchPersistTimerRef.current = null
    }
  })

  useEffect(() => {
    if (!id) return
    setSessionDefaults(null)
    setResearchSuggestion(null)
    lastSuggestionTopicRef.current = ''
    researchRestorePendingRef.current = true
    runLoadDefaults()
    runLoadKnowledgeBases()
  }, [id, runLoadDefaults, runLoadKnowledgeBases])

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

  const handleToggleSessionKb = useCallback(
    async (checked: boolean) => {
      if (!sessionDefaults || updatingDefaults) return
      try {
        await applyDefaults({
          useSessionKnowledgeBase: checked,
        })
      } catch {
        // 已在 applyDefaults 内处理错误提示
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
        const targetId =
          sessionDefaults.userKnowledgeBaseId &&
          available.some((kb) => kb.id === sessionDefaults.userKnowledgeBaseId)
            ? sessionDefaults.userKnowledgeBaseId
            : available[0].id
        try {
          await applyDefaults({
            useUserKnowledgeBase: true,
            userKnowledgeBaseId: targetId ?? available[0].id,
          })
        } catch {
          // applyDefaults 已处理
        }
      } else {
        try {
          await applyDefaults({
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
          useUserKnowledgeBase: true,
          userKnowledgeBaseId: value,
        })
      } catch {
        // applyDefaults 已处理
      }
    },
    [sessionDefaults, updatingDefaults, applyDefaults],
  )

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

  const abortChat = useCallback(() => {
    if (readerRef.current) {
      readerRef.current.cancel().catch(() => {})
      readerRef.current = null
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    // 找到正在loading的item并停止loading
    const loadingItem = chat.list.find((item) => item.loading)
    if (loadingItem) {
      const index = chat.list.indexOf(loadingItem)
      if (index !== -1) {
        chat.list[index] = { ...loadingItem, loading: false }
      }
    }
  }, [chat])

  const sendChat = useCallback(
    async (
      target: API.ChatItem,
      message: string,
      extra?: { userItem?: API.ChatItem; replaceMessageId?: string },
    ) => {
      openCitationsPanel(target)
      target.loading = true
      let needReload = false
      abortControllerRef.current = new AbortController()
      try {
        //后端接口
        const res = await api.session.chat(
          {
            id: id!,
            question: message,
            replaceFromMessageId: extra?.replaceMessageId,
          },
          {
            signal: abortControllerRef.current.signal,
          },
        )
        sessionActions.updateKey()

        const reader = res.data.getReader()
        if (!reader) return
        readerRef.current = reader

        await read(reader)
      } catch (error: any) {
        if (error.name === 'AbortError' || abortControllerRef.current?.signal.aborted) {
          // 用户主动中断，不显示错误
          return
        }
        target.error = error?.message ?? 'Unknown error'
        throw error
      } finally {
        readerRef.current = null
        abortControllerRef.current = null
        const index = chat.list.indexOf(target)
        if (index !== -1) {
          chat.list[index] = { ...target, loading: false }
        }
        if (needReload) {
          chat.list.splice(0, chat.list.length)
          await history.run()
        }
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
          const { value, done } = await reader.read()
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
            const line = slice.trim()
            // 解析 SSE：记录 event 名称
            if (line.startsWith('event:')) {
              currentEvent = line.replace(/^event\s*:\s*/, '').trim()
              continue
            }
            // 只处理 data 行
            if (line.startsWith('data:')) {
              const isCompletion = currentEvent === 'completion'
              parseData(line)
              scrollToBottom()
              if (isCompletion) {
                needReload = true
                return
              }
            }
          }

          if (done) {
            needReload = true
            break
          }
        }
      }

      function parseData(slice: string) {
        const raw = slice.trim()
        if (!raw.startsWith('data:')) {
          return
        }
        const str = raw.replace(/^data\s*:\s*/, '').trim()
        if (!str || str === '[DONE]') {
          return
        }

        let json: any = null
        try {
          json = JSON.parse(str)
        } catch (error) {
          // 纯文本流式内容
          target.content = `${target.content || ''}${str}`
          return
        }

        if (json?.content) {
          if (json.thinking) {
            target.think = `${target.think || ''}${json.content || ''}`
          } else {
            target.content = `${target.content || ''}${json.content || ''}`
          }
        } else if (typeof json === 'string') {
          target.content = `${target.content || ''}${json}`
        }

        if (Array.isArray(json?.documents) && json.documents.length) {
          target.reference = json.documents

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
          target.documents = docs
          setDocuments(docs)
        }

        const fallbackKbId =
          json?.debug?.kb_id ??
          json?.debug?.kbId ??
          sessionDefaults?.userKnowledgeBaseId

        if (Array.isArray(json?.citations) && json.citations.length) {
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

          target.reference = refs
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
          target.documents = docs
          setDocuments(docs)
        }

        if (Array.isArray(json?.recommended_questions)) {
          target.recommended_questions = json.recommended_questions
        }

        if (json?.message_id) {
          target.message_id = json.message_id
          if (extra?.userItem) {
            extra.userItem.message_id = json.message_id
          }
        }
      }
    },
    [chat, id, openCitationsPanel, setDocuments, history, sessionDefaults],
  )

  const handleFileSelected = useCallback((file: File) => {
    setPendingFiles((prev) => [...prev, file])
  }, [])

  const handleRemovePendingAttachment = useCallback((id: number) => {
    setPendingFiles((prev) => prev.filter((_, index) => index !== id))
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
      } catch (error) {
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
      const records = chat.list
        .filter((item) => item.deepResearch)
        .map((item) => {
          const deepResearch = item.deepResearch!
          return {
            userMessage: deepResearch.userMessage || deepResearch.topic,
            deepResearch: {
              ...deepResearch,
              progress: (deepResearch.progress ?? []).slice(-200),
            },
          }
        })
      if (records.length) {
        sessionStorage.setItem(
          researchPersistKey,
          JSON.stringify({ version: 1, items: records }),
        )
      } else {
        sessionStorage.removeItem(researchPersistKey)
      }
    }, 500)
  }, [chat.list, researchPersistKey])

  const send = useCallback(
    async (message: string, options?: { replaceMessageId?: string }) => {
      if (loadingRef.current) return
      if (!message) return

      const attachmentsSnapshot = pendingAttachments.map((item) => ({
        ...item,
      }))
      const filesSnapshot = [...pendingFiles]
      const replaceMessageId =
        options?.replaceMessageId ?? editingContext?.messageId
      let insertIndex: number | undefined

      if (replaceMessageId) {
        const editIdx = chat.list.findIndex(
          (item) => item.message_id === replaceMessageId,
        )
        if (editIdx !== -1) {
          chat.list.splice(editIdx)
          insertIndex = editIdx
          openCitationsPanel(null)
          setDocuments([])
        }
        setEditingContext(null)
      }

      // 如果有待发送的文件，先上传
      if (filesSnapshot.length > 0) {
        const usingRag =
          sessionDefaults?.useSessionKnowledgeBase ||
          sessionDefaults?.useUserKnowledgeBase
        const ok = await uploadPendingFiles(filesSnapshot, !!usingRag)
        if (!ok) return
      }

      const appendAtTail = insertIndex === undefined || insertIndex < 0
      const userMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.User,
        type: ChatType.Text,
        content: message,
        attachments: attachmentsSnapshot.length
          ? attachmentsSnapshot
          : undefined,
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

      await sendChat(target, message, {
        userItem: userMessage,
        replaceMessageId,
      })
      setPendingAttachments([])
      setPendingFiles([])
    },
    [
      chat,
      sendChat,
      pendingAttachments,
      pendingFiles,
      id,
      sessionDefaults,
      editingContext,
      openCitationsPanel,
      setDocuments,
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
  }, [])

  const fetchDeepResearchSnapshot = useCallback(
    async (itemId: number, researchId: string) => {
      try {
        const { data } = await api.deepResearch.getDeepResearchSnapshot(researchId, {
          errorToast: false,
        })
        const report = data?.report as API.DeepResearchCardState['report']
        const citationsPayload = data?.citations as { citations?: any[] } | undefined
        const citations = Array.isArray(citationsPayload?.citations)
          ? (citationsPayload?.citations as API.DeepResearchCardState['citations'])
          : []
        if (report?.report_markdown) {
          updateDeepResearchItem(itemId, (state) => {
            state.report = report
            state.citations = citations
            state.status = 'completed'
            state.statusMessage = '报告已完成'
          })
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
        const url = lastEventId
          ? `${baseUrl}&last_event_id=${encodeURIComponent(lastEventId)}`
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
            updateDeepResearchItem(itemId, (state) => {
              const next = [...(state.progress ?? []), parsed].slice(-200)
              state.progress = next
              state.lastStage = parsed.stage
              state.statusMessage = parsed.message
              state.updatedAt = parsed.timestamp
              const payload = parsed.payload || {}
              const stats = { ...(state.blockStats ?? {}) }
              if (typeof payload.blocks === 'number') stats.total = payload.blocks
              if (typeof payload.completed === 'number') stats.completed = payload.completed
              if (typeof payload.pending === 'number') stats.pending = payload.pending
              if (typeof payload.iteration === 'number') stats.iteration = payload.iteration
              if (typeof payload.max_iterations === 'number') {
                stats.maxIterations = payload.max_iterations
              }
              if (typeof payload.citations === 'number') stats.citations = payload.citations
              if (Object.keys(stats).length) {
                state.blockStats = stats
              }
              const toolCounts = { ...(state.toolCounts ?? {}) }
              const toolCalls = Array.isArray(payload.tool_calls) ? payload.tool_calls : []
              toolCalls.forEach((tool) => {
                if (!tool) return
                const name = String(tool)
                toolCounts[name] = (toolCounts[name] ?? 0) + 1
              })
              if (payload.tool_type) {
                const name = String(payload.tool_type)
                toolCounts[name] = (toolCounts[name] ?? 0) + 1
              }
              if (Object.keys(toolCounts).length) {
                state.toolCounts = toolCounts
              }
              if (state.status === 'queued') {
                state.status = 'running'
              }
            })
            if (
              parsed.stage === 'reporting' &&
              parsed.message.toLowerCase().includes('completed')
            ) {
              fetchDeepResearchSnapshot(itemId, researchId)
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

  const restoreDeepResearchCards = useCallback(() => {
    if (!researchPersistKey) return
    try {
      const raw = sessionStorage.getItem(researchPersistKey)
      if (!raw) return
      const parsed = JSON.parse(raw) as {
        version?: number
        items?: Array<{ userMessage?: string; deepResearch?: API.DeepResearchCardState }>
      }
      const items = parsed.items ?? []
      items.forEach((record) => {
        const deepResearch = record.deepResearch
        if (!deepResearch) return
        const userText = record.userMessage || deepResearch.userMessage || deepResearch.topic
        if (userText) {
          chat.list.push({
            id: createChatId(),
            role: ChatRole.User,
            type: ChatType.Text,
            content: userText,
          })
        }
        const assistantItem: API.ChatItem = {
          id: createChatId(),
          role: ChatRole.Assistant,
          type: ChatType.DeepResearch,
          deepResearch,
        }
        chat.list.push(assistantItem)
        if (
          deepResearch.researchId &&
          (deepResearch.status === 'queued' || deepResearch.status === 'running')
        ) {
          openDeepResearchStream(assistantItem.id, deepResearch.researchId)
        }
        if (deepResearch.researchId && deepResearch.status === 'completed' && !deepResearch.report) {
          fetchDeepResearchSnapshot(assistantItem.id, deepResearch.researchId)
        }
      })
    } catch (error) {
      console.warn('Failed to restore deep research cards', error)
    }
  }, [chat.list, fetchDeepResearchSnapshot, openDeepResearchStream, researchPersistKey])

  useEffect(() => {
    if (history.loading) {
      researchRestorePendingRef.current = true
    }
  }, [history.loading])

  useEffect(() => {
    if (!history.loading && researchRestorePendingRef.current) {
      researchRestorePendingRef.current = false
      restoreDeepResearchCards()
    }
  }, [history.loading, restoreDeepResearchCards])

  const buildDeepResearchRequest = useCallback(
    (topic: string, overrides?: Partial<DeepResearchRequest>) => {
      const language = sessionDefaults?.language || 'zh'
      const topK = sessionDefaults?.topK ?? DEEP_RESEARCH_DEFAULTS.top_k
      const metadata = {
        source: 'chat',
        ...(overrides?.metadata ?? {}),
      }
      return {
        ...DEEP_RESEARCH_DEFAULTS,
        topic,
        session_id: id,
        language,
        top_k: topK,
        metadata,
        ...overrides,
      }
    },
    [id, sessionDefaults?.language, sessionDefaults?.topK],
  )

  const requestDeepResearchPlan = useCallback(
    async (itemId: number, request: DeepResearchRequest) => {
      updateDeepResearchItem(itemId, (state) => {
        state.planLoading = true
        state.planError = undefined
      })
      try {
        const { data } = await api.deepResearch.previewDeepResearchPlan(request)
        updateDeepResearchItem(itemId, (state) => {
          state.plan = data
          state.planLoading = false
          state.status = 'plan'
        })
      } catch (error) {
        updateDeepResearchItem(itemId, (state) => {
          state.planLoading = false
          state.planError = resolveErrorMessage(error, '计划生成失败')
        })
      }
    },
    [resolveErrorMessage, updateDeepResearchItem],
  )

  const sendDeepResearch = useCallback(
    async (
      topic: string,
      options?: { source?: 'composer' | 'suggestion'; userLabel?: string },
    ) => {
      if (!id) {
        message.warning('缺少会话信息，无法发起深度研究')
        return
      }
      if (!topic.trim()) return
      const source = options?.source ?? 'composer'
      const userLabel =
        options?.userLabel || (source === 'suggestion' ? `深度研究：${topic}` : topic)
      const attachmentsSnapshot = pendingAttachments.map((item) => ({ ...item }))
      const filesSnapshot = [...pendingFiles]
      const ok = await uploadPendingFiles(filesSnapshot, true)
      if (!ok) return

      const userMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.User,
        type: ChatType.Text,
        content: userLabel,
        attachments: attachmentsSnapshot.length ? attachmentsSnapshot : undefined,
      }
      const request = buildDeepResearchRequest(topic, {
        metadata: {
          trigger: source,
        },
      })
      const assistantMessage: API.ChatItem = {
        id: createChatId(),
        role: ChatRole.Assistant,
        type: ChatType.DeepResearch,
        deepResearch: {
          status: 'plan',
          topic,
          request,
          source,
          userMessage: userLabel,
          planLoading: true,
        },
      }
      chat.list.push(userMessage)
      chat.list.push(assistantMessage)
      scrollToBottom()
      schedulePersistDeepResearchCards()
      await requestDeepResearchPlan(assistantMessage.id, request)
      setPendingAttachments([])
      setPendingFiles([])
      setComposerValue('')
    },
    [
      id,
      pendingAttachments,
      pendingFiles,
      uploadPendingFiles,
      buildDeepResearchRequest,
      chat.list,
      requestDeepResearchPlan,
      schedulePersistDeepResearchCards,
    ],
  )

  const handleComposerSend = useCallback(
    async (text: string) => {
      if (!text.trim()) return
      if (researchMode === 'deep' && !editingContext) {
        setResearchSuggestion(null)
        await sendDeepResearch(text, { source: 'composer' })
        return
      }
      await send(text)
      const reason = evaluateDeepResearchSuggestion(text)
      if (
        reason &&
        !suggestionDismissedRef.current.has(text) &&
        lastSuggestionTopicRef.current !== text
      ) {
        setResearchSuggestion({ topic: text, reason })
        lastSuggestionTopicRef.current = text
      }
    },
    [editingContext, evaluateDeepResearchSuggestion, researchMode, send, sendDeepResearch],
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
      if (!item.deepResearch?.request) return
      updateDeepResearchItem(item.id, (state) => {
        state.status = 'queued'
        state.statusMessage = '已提交任务'
      })
      try {
        const { data } = await api.deepResearch.submitDeepResearch(item.deepResearch.request)
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
      } catch (error) {
        updateDeepResearchItem(item.id, (state) => {
          state.status = 'failed'
          state.statusMessage = resolveErrorMessage(error, '提交失败')
        })
      }
    },
    [openDeepResearchStream, resolveErrorMessage, updateDeepResearchItem],
  )

  const handleDeepResearchCancel = useCallback(
    async (item: API.ChatItem) => {
      const researchId = item.deepResearch?.researchId
      if (researchId) {
        try {
          await api.deepResearch.cancelDeepResearch(researchId)
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
      await requestDeepResearchPlan(item.id, request)
    },
    [requestDeepResearchPlan],
  )

  const handleDeepResearchEdit = useCallback(
    (item: API.ChatItem) => {
      if (!item.deepResearch?.request) return
      const request = item.deepResearch.request
      researchForm.setFieldsValue({
        topic: request.topic,
        depth: request.depth,
        breadth: request.breadth,
        max_parallel: request.max_parallel,
        max_iterations: request.max_iterations,
        top_k: request.top_k,
        index_mode: request.index_mode,
        language: request.language,
        report_style: request.report_style,
        use_web_search: request.use_web_search,
        use_paper_search: request.use_paper_search,
        use_code_exec: request.use_code_exec,
      })
      setEditingResearchItem(item)
    },
    [researchForm],
  )

  const handleResearchEditSave = useCallback(async () => {
    if (!editingResearchItem?.deepResearch?.request) return
    let values: Partial<DeepResearchRequest>
    try {
      values = await researchForm.validateFields()
    } catch {
      return
    }
    const nextRequest = {
      ...editingResearchItem.deepResearch.request,
      ...values,
    }
    updateDeepResearchItem(editingResearchItem.id, (state) => {
      state.request = nextRequest
      state.topic = nextRequest.topic
    })
    setEditingResearchItem(null)
    await requestDeepResearchPlan(editingResearchItem.id, nextRequest)
  }, [editingResearchItem, requestDeepResearchPlan, researchForm, updateDeepResearchItem])

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
    (item: API.ChatItem, format: 'pdf' | 'markdown') => {
      const researchId = item.deepResearch?.researchId
      if (!researchId) {
        message.warning('暂无可导出的研究结果')
        return
      }
      const url = api.deepResearch.getDeepResearchExportUrl(
        researchId,
        format === 'pdf' ? 'pdf' : 'markdown',
      )
      window.open(url, '_blank', 'noopener')
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
    const summary = extractReportSummary(reportMarkdown) || topic
    const markdown = buildDeepResearchNoteMarkdown({
      topic,
      reportMarkdown,
      summary,
      sessionId: data?.request?.session_id,
      researchId: data?.researchId,
      citations: data?.citations,
    })
    try {
      await createNotebookNoteFile(markdown, topic)
      message.success('报告已保存到笔记本')
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `保存失败：${detail}` : '保存笔记失败')
    }
  }, [])

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

  const handleRetryUserMessage = useCallback(
    (item: API.ChatItem, _index: number) => {
      if (!item.message_id) {
        message.warning('消息尚未保存，暂无法编辑')
        return
      }
      const text = item.content || ''
      setComposerValue(text)
      setComposerFocusKey((key) => key + 1)
      setEditingContext({ messageId: item.message_id })
    },
    [],
  )

  const handleResendUserMessage = useCallback(
    async (item: API.ChatItem, _index: number) => {
      const text = item.content || ''
      if (!text) return
      if (!item.message_id) {
        message.warning('消息尚未保存，暂无法重发')
        return
      }
      await send(text, { replaceMessageId: item.message_id })
      setComposerValue('')
    },
    [send],
  )
  const editingInfo = useMemo(() => {
    if (!editingContext) return null
    const idx = list.findIndex(
      (item) => item.message_id === editingContext.messageId,
    )
    if (idx === -1) return null
    return {
      index: idx,
      snippet: list[idx]?.content ?? '',
    }
  }, [editingContext, list])

  const cancelEditing = useCallback(() => {
    setEditingContext(null)
    setComposerValue('')
  }, [])
  useMount(async () => {
    if (ctx?.data.message) {
      send(ctx.data.message)
    } else {
      history.run()
    }
  })

  useEffect(() => {
    const handleScroll = () => {
      const anchors: {
        id: string
        top: number
        item: API.ChatItem
      }[] = []

      chat.list
        .filter((o) => o.type === ChatType.Document)
        .forEach((item, index) => {
          const id = createChatIdText(item.id)
          const dom = document.getElementById(id)
          if (!dom) return

          const top = dom.offsetTop
          if (index === 0 || top < window.scrollY) {
            anchors.push({ id, top, item })
          }
        })

      if (anchors.length) {
        const current = anchors.reduce((prev, curr) =>
          curr.top > prev.top ? curr : prev,
        )

        openCitationsPanel(current.item)
      }
    }

    window.addEventListener('scroll', handleScroll)

    return () => {
      window.removeEventListener('scroll', handleScroll)
    }
  }, [])

  const title = useMemo(() => {
    return list[0]?.content ?? '新对话'
  }, [list[0]])

  const [read, setRead] = useState<API.Reference | null>(null)
  const usingSessionKb = sessionDefaults
    ? sessionDefaults.useSessionKnowledgeBase
    : true
  const usingUserKb = sessionDefaults
    ? sessionDefaults.useUserKnowledgeBase
    : false
  const kbOptions = useMemo(
    () => {
    const options: Array<{ value: number; label: string; disabled?: boolean }> = knowledgeBases.map((item) => ({
      value: item.id,
      label: item.name,
    }))
    if (
      sessionDefaults?.userKnowledgeBaseId &&
      !knowledgeBases.some(
        (item) => item.id === sessionDefaults.userKnowledgeBaseId,
      )
    ) {
      options.push({
        value: sessionDefaults.userKnowledgeBaseId,
        label: `ID ${sessionDefaults.userKnowledgeBaseId}（不可用）`,
        disabled: true,
      })
    }
    return options
    },
    [knowledgeBases, sessionDefaults?.userKnowledgeBaseId],
  )
  const knowledgeControl = useMemo(() => {
    if (!sessionDefaults) return undefined
    return {
      usingSession: usingSessionKb,
      usingUser: usingUserKb,
      selectValue: sessionDefaults.userKnowledgeBaseId ?? undefined,
      options: kbOptions,
      showSelect: usingUserKb,
      loadingSession: updatingDefaults || defaultsLoading,
      loadingUser: updatingDefaults || kbReq.loading,
      disableUserToggle: kbReq.loading && !usingUserKb,
      disableSelect: updatingDefaults,
      onToggleSession: handleToggleSessionKb,
      onToggleUser: handleToggleUserKb,
      onSelectUserKb: handleSelectUserKb,
    }
  }, [
    sessionDefaults,
    usingSessionKb,
    usingUserKb,
    kbOptions,
    updatingDefaults,
    defaultsLoading,
    kbReq.loading,
    handleToggleSessionKb,
    handleToggleUserKb,
    handleSelectUserKb,
  ])
  const ragModeControl = useMemo(() => {
    if (!sessionDefaults) return undefined
    const strategy = sessionDefaults.retrievalStrategy
    const value: 'fast' | 'deep' =
      strategy === 'graph' || strategy === 'multimodal_graph' ? 'deep' : 'fast'
    return {
      value,
      loading: updatingDefaults || defaultsLoading,
      disabled: updatingDefaults,
      onChange: handleRagModeChange,
    }
  }, [sessionDefaults, updatingDefaults, defaultsLoading, handleRagModeChange])

  const researchModeControl = useMemo(
    () => ({
      value: researchMode,
      disabled: !!editingContext,
      onChange: setResearchMode,
    }),
    [editingContext, researchMode],
  )

  return (
    <ComPageLayout
      sender={
        <>
          {documents.length > 0 && <Source list={documents} />}
          {editingInfo ? (
            <div className={styles['chat-page__editing-tip']}>
              <div className={styles['chat-page__editing-text']}>
                正在编辑历史消息
                {editingInfo.snippet ? (
                  <span className={styles['chat-page__editing-snippet']}>
                    {editingInfo.snippet}
                  </span>
                ) : null}
              </div>
              <Button type="link" size="small" onClick={cancelEditing}>
                取消编辑
              </Button>
            </div>
          ) : null}
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
            enableSessionKnowledgeBase={usingSessionKb}
            knowledgeControl={knowledgeControl}
            ragModeControl={ragModeControl}
            researchModeControl={researchModeControl}
            pendingAttachments={pendingAttachments}
            onRemovePendingAttachment={handleRemovePendingAttachment}
            onFileSelected={handleFileSelected}
            value={composerValue}
            onValueChange={setComposerValue}
            focusKey={composerFocusKey}
          />
        </>
      }
      right={
        <>
          {currentChatItem && currentChatItem.reference?.length ? (
            <ChatDrawer title="引文">
              <Citations list={currentChatItem.reference} />
            </ChatDrawer>
          ) : (
            <ChatDrawer title="文档">
              <Contracts list={documents} />
            </ChatDrawer>
          )}
        </>
      }
    >
      <div className={styles['chat-page']}>
        <div className={styles['chat-page__header']}>
          <div className={styles['chat-page__header-title']}>{title}</div>
          <div className={styles['chat-page__header-actions']}>
            <Button size="small" onClick={handleOpenIdeaGen}>
              IdeaGen
            </Button>
            <Button size="small" onClick={handleOpenNotebook}>
              笔记本
            </Button>
            <Button type="text" shape="circle">
              <img src={IconEdit} />
            </Button>
          </div>
        </div>

        <ChatMessage
          list={list}
          onSend={send}
          onOpenCiations={openCitationsPanel}
          onRefrence={setRead}
          onRetryUserMessage={handleRetryUserMessage}
          onResendUserMessage={handleResendUserMessage}
          onDeepResearchConfirm={handleDeepResearchConfirm}
          onDeepResearchCancel={handleDeepResearchCancel}
          onDeepResearchEdit={handleDeepResearchEdit}
          onDeepResearchRetryPlan={handleDeepResearchRetryPlan}
          onDeepResearchOpenWorkspace={handleDeepResearchOpenWorkspace}
          onDeepResearchExport={handleDeepResearchExport}
          onDeepResearchCopy={handleDeepResearchCopy}
          onDeepResearchSaveToNotebook={handleDeepResearchSaveToNotebook}
          onDeepResearchInsertSummary={handleDeepResearchInsertSummary}
        />

        <NotebookDrawer
          open={notebookOpen}
          sessionId={id}
          onClose={() => setNotebookOpen(false)}
          getSelectionText={getSelectionText}
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
          title="调整深度研究参数"
          open={!!editingResearchItem}
          onCancel={() => setEditingResearchItem(null)}
          onOk={handleResearchEditSave}
          okText="更新计划"
          cancelText="取消"
          destroyOnClose
        >
          <Form form={researchForm} layout="vertical">
            <Form.Item
              name="topic"
              label="研究主题"
              rules={[{ required: true, message: '请输入研究主题' }]}
            >
              <Input placeholder="输入研究主题" />
            </Form.Item>
            <Space size={12} wrap>
              <Form.Item
                name="depth"
                label="深度"
                rules={[{ required: true, message: '请输入深度' }]}
              >
                <InputNumber min={1} max={6} />
              </Form.Item>
              <Form.Item
                name="breadth"
                label="广度"
                rules={[{ required: true, message: '请输入广度' }]}
              >
                <InputNumber min={1} max={12} />
              </Form.Item>
              <Form.Item
                name="max_parallel"
                label="并发"
                rules={[{ required: true, message: '请输入并发数' }]}
              >
                <InputNumber min={1} max={10} />
              </Form.Item>
              <Form.Item
                name="max_iterations"
                label="迭代"
                rules={[{ required: true, message: '请输入迭代次数' }]}
              >
                <InputNumber min={1} max={10} />
              </Form.Item>
              <Form.Item name="top_k" label="TopK">
                <InputNumber min={1} max={50} />
              </Form.Item>
              <Form.Item name="index_mode" label="Index Mode">
                <Select options={DEEP_RESEARCH_INDEX_OPTIONS} />
              </Form.Item>
            </Space>
            <Space size={12} wrap>
              <Form.Item name="use_web_search" label="WebSearch" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="use_paper_search" label="PaperSearch" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="use_code_exec" label="CodeExec" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Space>
            <Form.Item name="language" label="语言">
              <Input placeholder="zh / en" />
            </Form.Item>
            <Form.Item name="report_style" label="报告风格">
              <Input placeholder="例如：学术综述 / 技术分析" />
            </Form.Item>
          </Form>
        </Modal>
      </div>
    </ComPageLayout>
  )
}
