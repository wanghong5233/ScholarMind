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
  const [currentChatItem, setCurrentChatItem] = useState<API.ChatItem | null>(
    null,
  )
  const [pendingAttachments, setPendingAttachments] = useState<
    API.ChatAttachment[]
  >([])
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [sessionDefaults, setSessionDefaults] =
    useState<API.SessionDefaults | null>(null)
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [updatingDefaults, setUpdatingDefaults] = useState(false)

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
            if (item.retrieval_content) {
              try {
                const retrievalData = JSON.parse(item.retrieval_content)
                if (retrievalData.context_files && Array.isArray(retrievalData.context_files)) {
                  attachments = retrievalData.context_files.map((file: any, idx: number) => ({
                    id: idx,
                    title: file.filename || '未知文件',
                    knowledgeBaseId: 0,
                  }))
                }
              } catch (error) {
                console.error('Failed to parse retrieval_content:', error)
              }
            }
            
            chat.list.push({
              id: createChatId(),
              role: ChatRole.User,
              type: ChatType.Text,
              content: item.user_question,
              attachments: attachments,
            })
          }

          if (item.model_answer) {
            const map = new Map<string, API.Document>()
            let reference: API.Reference[] = []
            let recommended_questions: string[] = []

            if (item.documents) {
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
              map.set(chunk.document_id, {
                document_id: chunk.document_id,
                document_name: chunk.document_name,
                content_with_weight: chunk.content_with_weight,
              })
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
            })
          }
        })

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

  const sendChat = useCallback(
    async (target: API.ChatItem, message: string) => {
      setCurrentChatItem(target)
      target.loading = true
      let needReload = false
      try {
        //后端接口
        const res = await api.session.chat({
          id: id!,
          question: message,
        })
        sessionActions.updateKey()

        const reader = res.data.getReader()
        if (!reader) return

        await read(reader)
      } catch (error: any) {
        target.error = error?.message ?? 'Unknown error'
        throw error
      } finally {
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
          const { value, done } = await reader.read()
          temp += decoder.decode(value)

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
            map.set(chunk.document_id, {
              document_id: chunk.document_id,
              document_name: chunk.document_name,
              content_with_weight: chunk.content_with_weight,
            })
          })
          const docs = Array.from(map.values())
          target.documents = docs
          setDocuments(docs)
        }

        if (Array.isArray(json?.citations) && json.citations.length) {
          const refs: API.Reference[] = json.citations.map(
            (item: any, idx: number) => {
              const docId = String(item.document_id ?? '')
              return {
                id: `${docId}-${item.chunk_id ?? idx}`,
                document_id: docId,
                document_name: item.document_title || `文档 ${docId || idx + 1}`,
                content_with_weight: item.snippet ?? '',
                positions: item.page ? [[item.page, 0]] : [],
              }
            },
          )

          target.reference = refs
          const map = new Map<string, API.Document>()
          refs.forEach((chunk) => {
            map.set(chunk.document_id, {
              document_id: chunk.document_id,
              document_name: chunk.document_name,
              content_with_weight: chunk.content_with_weight,
            })
          })
          const docs = Array.from(map.values())
          target.documents = docs
          setDocuments(docs)
        }

        if (Array.isArray(json?.recommended_questions)) {
          target.recommended_questions = json.recommended_questions
        }
      }
    },
    [chat, id, setCurrentChatItem, setDocuments, history],
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
    async (message: string) => {
      if (loadingRef.current) return
      if (!message) return

      const attachmentsSnapshot = pendingAttachments.map((item) => ({
        ...item,
      }))
      const filesSnapshot = [...pendingFiles]

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

      if (chat.list.length === 0) {
        chat.list.push({
          id: createChatId(),
          role: ChatRole.User,
          type: ChatType.Text,
          content: message,
          attachments: attachmentsSnapshot.length
            ? attachmentsSnapshot
            : undefined,
        })

        chat.list.push({
          id: createChatId(),
          role: ChatRole.Assistant,
          type: ChatType.Document,
          documents: [],
        })

        const target = chat.list[chat.list.length - 1]

        await sendChat(target, message)
        setPendingAttachments([])
        setPendingFiles([])
      } else {
        chat.list.push({
          id: createChatId(),
          role: ChatRole.User,
          type: ChatType.Text,
          content: message,
          attachments: attachmentsSnapshot.length
            ? attachmentsSnapshot
            : undefined,
        })

        chat.list.push({
          id: createChatId(),
          role: ChatRole.Assistant,
          type: ChatType.Document,
          content: '',
        })
        scrollToBottom()

        const target = chat.list[chat.list.length - 1]

        await sendChat(target, message)
        setPendingAttachments([])
        setPendingFiles([])
      }
    },
    [chat, sendChat, pendingAttachments, pendingFiles, id, sessionDefaults],
  )
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

        setCurrentChatItem(current.item)
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
          <ComSender
            loading={loading}
            sessionId={id}
            onSend={send}
            onContract={() => setCurrentChatItem(null)}
            enableSessionKnowledgeBase={usingSessionKb}
            knowledgeControl={knowledgeControl}
            pendingAttachments={pendingAttachments}
            onRemovePendingAttachment={handleRemovePendingAttachment}
            onFileSelected={handleFileSelected}
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
          onOpenCiations={setCurrentChatItem}
          onRefrence={setRead}
        />

        <Drawer
          title={read?.document_name ?? ''}
          width={800}
          onClose={() => setRead(null)}
          open={!!read}
          destroyOnClose
        >
          <Markdown value={read?.content_with_weight ?? ''} />
        </Drawer>
      </div>
    </ComPageLayout>
  )
}
