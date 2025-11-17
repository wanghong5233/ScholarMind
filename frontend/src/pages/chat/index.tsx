import * as api from '@/api'
import IconEdit from '@/assets/chat/edit.svg'
import Markdown from '@/components/markdown'
import ComPageLayout from '@/components/page-layout'
import ComSender from '@/components/sender'
import { ChatRole, ChatType } from '@/configs'
import { deviceActions } from '@/store/device'
import { usePageTransport } from '@/utils'
import { useMount, useRequest, useUnmount } from 'ahooks'
import { Button, Drawer, message } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { proxy, useSnapshot } from 'valtio'
import { sessionActions } from '../../store/session'
import ChatMessage from './component/chat-message'
import Citations from './component/citations'
import Contracts from './component/contracts'
import ChatDrawer from './component/drawer'
import Source from './component/source'
import styles from './index.module.scss'
import { createChatId, createChatIdText, transportToChatEnter } from './shared'
import type { KnowledgeBase } from '@/api/repository'

type ChatItemWithToken = API.ChatItem & { __openToken?: number }

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

export default function Index() {
  const { id } = useParams()
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
  const [editingContext, setEditingContext] = useState<{
    messageId: string
  } | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const readerRef = useRef<ReadableStreamDefaultReader<any> | null>(null)
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
  })

  useEffect(() => {
    if (!id) return
    setSessionDefaults(null)
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
        try {
          // 根据 RAG 开关决定上传方式
          const usingRag = sessionDefaults?.useSessionKnowledgeBase || sessionDefaults?.useUserKnowledgeBase

          if (usingRag) {
            // RAG 模式：后台异步上传入库
            for (const file of filesSnapshot) {
              await api.session.upload({ sessionId: id!, file })
            }
          } else {
            // 直接上下文模式：上传文件到 context_json
            for (const file of filesSnapshot) {
              await api.session.uploadForContext({
                sessionId: id!,
                file,
              })
            }
            
            // 同时后台异步入库（对用户透明）
            for (const file of filesSnapshot) {
              api.session.upload({ sessionId: id!, file }).catch(() => {
                // 静默失败，不影响用户体验
              })
            }
          }
        } catch (error) {
          window.$app.message.error('文件上传失败')
          return
        }
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
          <ComSender
            loading={loading}
            sessionId={id}
            onSend={send}
            onAbort={abortChat}
            onContract={() => openCitationsPanel(null)}
            enableSessionKnowledgeBase={usingSessionKb}
            knowledgeControl={knowledgeControl}
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
          <Button type="text" shape="circle">
            <img src={IconEdit} />
          </Button>
        </div>

        <ChatMessage
          list={list}
          onSend={send}
          onOpenCiations={openCitationsPanel}
          onRefrence={setRead}
          onRetryUserMessage={handleRetryUserMessage}
          onResendUserMessage={handleResendUserMessage}
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
      </div>
    </ComPageLayout>
  )
}
