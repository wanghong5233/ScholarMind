import * as api from '@/api'
import type {
  DeepResearchCitation,
  IdeaGenerationRequest,
  IdeaGenerationNoteInput,
  IdeaGenerationResponse,
  IdeaGenerationRunMeta,
} from '@/api/deepResearch'
import Markdown from '@/components/markdown'
import { NOTEBOOK_WORKSPACE_ID, createNotebookNoteFile } from '@/utils/notebook'
import { useRequest } from 'ahooks'
import {
  Button,
  Card,
  Divider,
  Empty,
  Input,
  InputNumber,
  List,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import styles from './index.module.scss'

const { Text } = Typography

const STATUS_COLORS: Record<string, string> = {
  running: 'processing',
  completed: 'green',
  failed: 'red',
}

const FRONT_MATTER_REGEX = /^---\s*[\r\n]+[\s\S]*?\r?\n---\s*/m

type IdeaGenLocationState = {
  prefill?: {
    sessionId?: string
    topic?: string
  }
  notes?: Array<{
    id?: string
    title?: string
    content?: string
    tags?: string[]
    source?: string
  }>
  selection?: string
}

type NoteInput = IdeaGenerationNoteInput & { id: string }

type DeepResearchIdeaContext = {
  ideaKey?: string
  ideaTitle?: string
  ideaDescription?: string
  knowledgePoint?: string
  pointDescription?: string
  dimension?: string
  novelty?: string
  feasibility?: string
  reason?: string
}

function getStatusColor(status?: string) {
  if (!status) return 'default'
  return STATUS_COLORS[status.toLowerCase()] || 'default'
}

export default function IdeaGenerationPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const prefillHandledRef = useRef(false)
  const [topic, setTopic] = useState('')
  const [ideaCount, setIdeaCount] = useState<number | null>(5)
  const [language, setLanguage] = useState('')
  const [constraints, setConstraints] = useState<string[]>([])
  const [constraintInput, setConstraintInput] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [topK, setTopK] = useState<number | null>(null)
  const [indexMode, setIndexMode] = useState('')
  const [noteInputs, setNoteInputs] = useState<NoteInput[]>([])
  const [editingIdea, setEditingIdea] = useState<{
    key: string
    ideaTitle: string
    ideaDescription?: string
    knowledgePoint?: string
    pointDescription?: string
    dimension?: string
    novelty?: string
    feasibility?: string
    reason?: string
  } | null>(null)
  const [editingIdeaTitle, setEditingIdeaTitle] = useState('')
  const [savingNoteIds, setSavingNoteIds] = useState<Record<string, boolean>>({})

  const [result, setResult] = useState<IdeaGenerationResponse | null>(null)
  const [selectedMeta, setSelectedMeta] = useState<IdeaGenerationRunMeta | null>(null)
  const [runList, setRunList] = useState<IdeaGenerationRunMeta[]>([])

  const { run: refreshRuns, loading: listLoading } = useRequest(
    async () => {
      const { data } = await api.deepResearch.listIdeaGenerationRuns({ errorToast: false })
      return data?.items ?? []
    },
    {
      manual: true,
      onSuccess(items) {
        setRunList(items ?? [])
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `获取历史记录失败：${detail}` : '获取历史记录失败')
      },
    },
  )

  const { runAsync: runIdeaGeneration, loading: runLoading } = useRequest(
    async (payload: IdeaGenerationRequest) => {
      const { data } = await api.deepResearch.runIdeaGeneration(payload, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setResult(data)
        setSelectedMeta(null)
        message.success('想法生成完成')
        refreshRuns()
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `想法生成失败：${detail}` : '想法生成失败')
      },
    },
  )

  const { runAsync: loadRunDetail, loading: detailLoading } = useRequest(
    async (ideaId: string) => {
      const { data } = await api.deepResearch.getIdeaGenerationRun(ideaId, { errorToast: false })
      return data
    },
    {
      manual: true,
      onSuccess(data) {
        setSelectedMeta(data.meta)
        setResult(data.payload)
        message.success('已载入历史记录')
      },
      onError(error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `载入失败：${detail}` : '载入失败')
      },
    },
  )

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  useEffect(() => {
    if (prefillHandledRef.current) return
    const state = location.state as IdeaGenLocationState | null
    if (!state) return
    prefillHandledRef.current = true

    if (state.prefill?.sessionId) {
      setSessionId(state.prefill.sessionId)
    }
    if (state.prefill?.topic && !topic) {
      setTopic(state.prefill.topic)
    }

    const nextNotes: NoteInput[] = []
    const buildId = () =>
      window.crypto?.randomUUID?.() ?? `note-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`

    const selection = state.selection?.trim()
    if (selection) {
      const title = selection.split(/\r?\n/)[0]?.slice(0, 24) || '对话选段'
      nextNotes.push({
        id: buildId(),
        title,
        content: selection,
        source: 'chat_selection',
      })
    }
    if (Array.isArray(state.notes)) {
      state.notes.forEach((note) => {
        const rawContent = (note.content || '').trim()
        const content = rawContent.replace(FRONT_MATTER_REGEX, '').trim()
        if (!content) return
        nextNotes.push({
          id: note.id || buildId(),
          title: (note.title || '').trim() || '笔记条目',
          content,
          source: note.source || 'notebook',
          tags: note.tags,
        })
      })
    }
    if (nextNotes.length) {
      setNoteInputs(nextNotes)
      if (!topic) {
        setTopic(nextNotes[0].title || '')
      }
    }
  }, [location.state, topic])

  const sortedRuns = useMemo(() => {
    return [...runList].sort((a, b) => {
      const left = a.started_at || a.finished_at || ''
      const right = b.started_at || b.finished_at || ''
      return right.localeCompare(left)
    })
  }, [runList])

  const currentMeta = useMemo(() => {
    if (selectedMeta) return selectedMeta
    if (!result?.idea_id) return null
    return runList.find((item) => item.idea_id === result.idea_id) ?? null
  }, [result?.idea_id, runList, selectedMeta])

  const handleAddConstraint = useCallback(() => {
    const value = constraintInput.trim()
    if (!value) return
    if (constraints.includes(value)) {
      message.warning('该约束已存在')
      return
    }
    setConstraints((prev) => [...prev, value])
    setConstraintInput('')
  }, [constraintInput, constraints])

  const handleRemoveConstraint = useCallback((value: string) => {
    setConstraints((prev) => prev.filter((item) => item !== value))
  }, [])

  const handleRemoveNote = useCallback((id: string) => {
    setNoteInputs((prev) => prev.filter((note) => note.id !== id))
  }, [])

  const buildNoteContextText = useCallback(() => {
    if (!noteInputs.length) return ''
    const lines: string[] = ['## 笔记上下文']
    noteInputs.forEach((note, index) => {
      const title = note.title?.trim() || `笔记 ${index + 1}`
      lines.push(`### ${title}`)
      if (note.tags?.length) {
        lines.push(`标签：${note.tags.join(', ')}`)
      }
      if (note.source) {
        lines.push(`来源：${note.source}`)
      }
      const content = (note.content || '').trim()
      if (content) {
        const excerpt = content.length > 1200 ? `${content.slice(0, 1200)}...` : content
        lines.push(excerpt)
      }
    })
    return lines.join('\n').trim()
  }, [noteInputs])

  const buildIdeaContextText = useCallback(
    (context?: DeepResearchIdeaContext) => {
      const sections: string[] = []
      const ideaLines: string[] = []
      const ideaTitle = context?.ideaTitle?.trim()
      if (ideaTitle) {
        ideaLines.push('## IdeaGen 结果')
        ideaLines.push(`想法标题：${ideaTitle}`)
      }
      if (context?.ideaDescription) {
        ideaLines.push(`想法描述：${context.ideaDescription}`)
      }
      if (context?.knowledgePoint) {
        ideaLines.push(`关联知识点：${context.knowledgePoint}`)
      }
      if (context?.pointDescription) {
        ideaLines.push(`知识点说明：${context.pointDescription}`)
      }
      if (context?.dimension) {
        ideaLines.push(`维度：${context.dimension}`)
      }
      if (context?.novelty) {
        ideaLines.push(`创新点：${context.novelty}`)
      }
      if (context?.feasibility) {
        ideaLines.push(`可行性：${context.feasibility}`)
      }
      if (context?.reason) {
        ideaLines.push(`筛选原因：${context.reason}`)
      }
      if (ideaLines.length) {
        sections.push(ideaLines.join('\n'))
      }
      const noteContext = buildNoteContextText()
      if (noteContext) {
        sections.push(noteContext)
      }
      return sections.join('\n\n').trim()
    },
    [buildNoteContextText],
  )

  const handleStartDeepResearch = useCallback(
    (value: string, context?: DeepResearchIdeaContext) => {
      if (!sessionId.trim()) {
        message.warning('需要会话 ID 才能发起 DeepResearch')
        return
      }
      const topicValue = value.trim()
      if (!topicValue) {
        message.warning('请输入研究主题')
        return
      }
      const contextText = buildIdeaContextText({
        ...context,
        ideaTitle: context?.ideaTitle || topicValue,
      })
      const state =
        contextText && contextText.length
          ? {
              noteContext: {
                title: topicValue,
                content: contextText,
                source: 'ideagen',
                noteId: context?.ideaKey,
                sessionId,
              },
            }
          : undefined
      navigate(
        `/deep-research?topic=${encodeURIComponent(topicValue)}&sessionId=${encodeURIComponent(
          sessionId,
        )}`,
        state ? { state } : undefined,
      )
    },
    [buildIdeaContextText, navigate, sessionId],
  )

  const handleEditIdea = useCallback((payload: {
    key: string
    ideaTitle: string
    ideaDescription?: string
    knowledgePoint?: string
    pointDescription?: string
    dimension?: string
    novelty?: string
    feasibility?: string
    reason?: string
  }) => {
    setEditingIdea(payload)
    setEditingIdeaTitle(payload.ideaTitle)
  }, [])

  const handleConfirmEditIdea = useCallback(() => {
    if (!editingIdea) return
    handleStartDeepResearch(editingIdeaTitle, {
      ...editingIdea,
      ideaTitle: editingIdeaTitle,
    })
    setEditingIdea(null)
  }, [editingIdea, editingIdeaTitle, handleStartDeepResearch])

  const escapeYaml = useCallback((value: string) => {
    return (value || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  }, [])

  const buildIdeaNoteMarkdown = useCallback(
    (payload: {
      ideaTitle: string
      ideaDescription?: string
      knowledgePoint?: string
      pointDescription?: string
      dimension?: string
      novelty?: string
      feasibility?: string
    }) => {
      const createdAt = dayjs().toISOString()
      const summary =
        payload.ideaDescription ||
        payload.pointDescription ||
        payload.knowledgePoint ||
        payload.ideaTitle
      const tags = [
        'ideagen',
        payload.dimension,
        payload.knowledgePoint,
      ].filter(Boolean) as string[]
      const tagsYaml = tags.map((tag) => `"${escapeYaml(tag)}"`).join(', ')
      const frontMatter = [
        '---',
        `title: "${escapeYaml(payload.ideaTitle)}"`,
        `summary: "${escapeYaml(summary || payload.ideaTitle)}"`,
        `tags: [${tagsYaml}]`,
        `session_id: "${escapeYaml(sessionId || '')}"`,
        `created_at: "${createdAt}"`,
        `source_excerpt: "${escapeYaml(summary || payload.ideaTitle)}"`,
        '---',
      ].join('\n')

      const lines = [
        `# ${payload.ideaTitle}`,
        '',
        '## 想法概述',
        payload.ideaDescription || '待补充',
        '',
        '## 关联知识点',
        payload.knowledgePoint ? `- ${payload.knowledgePoint}` : '- 待补充',
      ]
      if (payload.pointDescription) {
        lines.push(`- ${payload.pointDescription}`)
      }
      lines.push('', '## 评估')
      if (payload.dimension) {
        lines.push(`- 维度：${payload.dimension}`)
      }
      if (payload.novelty) {
        lines.push(`- 创新点：${payload.novelty}`)
      }
      if (payload.feasibility) {
        lines.push(`- 可行性：${payload.feasibility}`)
      }
      return `${frontMatter}\n\n${lines.join('\n')}`.trim()
    },
    [escapeYaml, sessionId],
  )

  const openNotebookInLatex = useCallback((path: string) => {
    if (typeof window === 'undefined') return
    const url = new URL(
      `${import.meta.env.BASE_URL || '/'}doc-studio/${NOTEBOOK_WORKSPACE_ID}`,
      window.location.origin,
    )
    url.searchParams.set('file', path)
    window.open(url.toString(), '_blank', 'noopener')
  }, [])

  const handleSaveIdeaToNotebook = useCallback(
    async (
      key: string,
      payload: Parameters<typeof buildIdeaNoteMarkdown>[0],
      options?: { openAfterSave?: boolean },
    ) => {
      try {
        setSavingNoteIds((prev) => ({ ...prev, [key]: true }))
        const markdown = buildIdeaNoteMarkdown(payload)
        const savedPath = await createNotebookNoteFile(markdown, payload.ideaTitle)
        message.success('已保存到笔记本')
        if (options?.openAfterSave && savedPath) {
          openNotebookInLatex(savedPath)
        }
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail || error?.response?.data?.message || error?.message
        message.error(detail ? `保存失败：${detail}` : '保存笔记失败')
      } finally {
        setSavingNoteIds((prev) => ({ ...prev, [key]: false }))
      }
    },
    [buildIdeaNoteMarkdown, openNotebookInLatex],
  )

  const handleSubmit = useCallback(async () => {
    const trimmedTopic = topic.trim()
    const hasNotes = noteInputs.length > 0
    if (!trimmedTopic && !hasNotes) {
      message.warning('请输入研究主题或添加笔记上下文')
      return
    }
    if (!sessionId.trim()) {
      message.warning('请输入会话 ID')
      return
    }
    let resolvedTopic = trimmedTopic
    if (!resolvedTopic && hasNotes) {
      const firstNote = noteInputs[0]
      resolvedTopic =
        firstNote?.title?.trim() ||
        firstNote?.content?.trim().split(/\r?\n/)[0]?.slice(0, 40) ||
        '研究方向提炼'
      setTopic(resolvedTopic)
    }

    const payload: IdeaGenerationRequest = {
      topic: resolvedTopic || undefined,
      idea_count: ideaCount ?? undefined,
      language: language.trim() || undefined,
      constraints,
      notes: noteInputs.length
        ? noteInputs.map((note) => ({
            title: note.title,
            content: note.content,
            tags: note.tags,
            source: note.source,
          }))
        : undefined,
      session_id: sessionId.trim(),
      top_k: topK ?? undefined,
      index_mode: indexMode.trim() || undefined,
    }

    await runIdeaGeneration(payload)
  }, [
    constraints,
    ideaCount,
    indexMode,
    language,
    noteInputs,
    runIdeaGeneration,
    sessionId,
    topK,
    topic,
  ])

  const renderCitations = useCallback((items: DeepResearchCitation[] = []) => {
    if (!items.length) {
      return <Text type="secondary">暂无引用</Text>
    }
    return (
      <List
        size="small"
        dataSource={items}
        renderItem={(item, index) => (
          <List.Item>
            <Space direction="vertical" size={4}>
              <Space size={8}>
                <Tag color="blue">#{item.ref_number ?? index + 1}</Tag>
                <Text strong>{item.title || item.url || '未命名引用'}</Text>
                {item.source_type ? <Tag>{item.source_type}</Tag> : null}
              </Space>
              {item.url ? (
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.url}
                </a>
              ) : null}
              {item.snippet ? <Text type="secondary">{item.snippet}</Text> : null}
            </Space>
          </List.Item>
        )}
      />
    )
  }, [])

  return (
    <div className={styles.container}>
      <div className={styles.side}>
        <Card title="研究想法生成" className={styles.section}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Input.TextArea
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
              placeholder="研究主题（可选，或使用笔记上下文）"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
            <Space wrap>
              <InputNumber
                min={1}
                max={20}
                value={ideaCount ?? undefined}
                onChange={(value) => setIdeaCount(value ?? null)}
                placeholder="想法数量"
              />
              <Input
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                placeholder="输出语言（可选）"
              />
            </Space>
            <Space wrap>
              <Input
                value={sessionId}
                onChange={(event) => setSessionId(event.target.value)}
                placeholder="会话 ID（必填）"
              />
              <InputNumber
                min={1}
                max={50}
                value={topK ?? undefined}
                onChange={(value) => setTopK(value ?? null)}
                placeholder="top_k"
              />
              <Input
                value={indexMode}
                onChange={(event) => setIndexMode(event.target.value)}
                placeholder="索引模式（可选）"
              />
            </Space>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                value={constraintInput}
                onChange={(event) => setConstraintInput(event.target.value)}
                placeholder="添加约束条件"
                onPressEnter={handleAddConstraint}
              />
              <Button onClick={handleAddConstraint}>添加</Button>
            </Space.Compact>
            {constraints.length ? (
              <div className={styles.tagWrap}>
                {constraints.map((item) => (
                  <Tag key={item} closable onClose={() => handleRemoveConstraint(item)}>
                    {item}
                  </Tag>
                ))}
              </div>
            ) : (
              <Text type="secondary">暂无约束条件</Text>
            )}
            <div className={styles.noteSection}>
              <Text strong>笔记/选段上下文</Text>
              {noteInputs.length ? (
                <div className={styles.noteList}>
                  {noteInputs.map((note) => (
                    <div key={note.id} className={styles.noteItem}>
                      <div className={styles.noteHeader}>
                        <Text strong>{note.title || '笔记条目'}</Text>
                        <Button type="link" size="small" onClick={() => handleRemoveNote(note.id)}>
                          移除
                        </Button>
                      </div>
                      <Text type="secondary" className={styles.noteExcerpt}>
                        {note.content.slice(0, 120)}
                        {note.content.length > 120 ? '…' : ''}
                      </Text>
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">暂无笔记上下文</Text>
              )}
            </div>
            <Button type="primary" onClick={handleSubmit} loading={runLoading}>
              生成想法
            </Button>
          </Space>
        </Card>

        <Card
          title="历史记录"
          className={styles.section}
          extra={
            <Button size="small" onClick={() => refreshRuns()} loading={listLoading}>
              刷新
            </Button>
          }
        >
          {sortedRuns.length ? (
            <List<IdeaGenerationRunMeta>
              size="small"
              dataSource={sortedRuns}
              loading={listLoading}
              renderItem={(item) => (
                <List.Item
                  key={item.idea_id}
                  className={styles.listItem}
                  onClick={() => loadRunDetail(item.idea_id)}
                >
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Space size={8} wrap>
                      <Tag color={getStatusColor(item.status)}>{item.status}</Tag>
                      <Text strong>{item.topic}</Text>
                    </Space>
                    <Space size={8} wrap>
                      <Text type="secondary">ID: {item.idea_id}</Text>
                      {item.started_at ? (
                        <Text type="secondary">
                          开始：{dayjs(item.started_at).format('YYYY-MM-DD HH:mm')}
                        </Text>
                      ) : null}
                      {item.finished_at ? (
                        <Text type="secondary">
                          完成：{dayjs(item.finished_at).format('YYYY-MM-DD HH:mm')}
                        </Text>
                      ) : null}
                    </Space>
                  </Space>
                </List.Item>
              )}
            />
          ) : (
            <Empty description="暂无历史记录" />
          )}
          {detailLoading ? <Text type="secondary">载入中...</Text> : null}
        </Card>
      </div>

      <div className={styles.content}>
        <Card title="生成结果" className={styles.section}>
          {!result ? (
            <Empty description="暂无输出结果" />
          ) : (
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <div className={styles.metaRow}>
                <Text type="secondary">ID: {result.idea_id}</Text>
                {currentMeta?.status ? (
                  <Tag color={getStatusColor(currentMeta.status)}>{currentMeta.status}</Tag>
                ) : null}
                {currentMeta?.duration_seconds ? (
                  <Text type="secondary">
                    耗时：{currentMeta.duration_seconds.toFixed(1)}s
                  </Text>
                ) : null}
              </div>
              {result.ideas?.length ? (
                <>
                  <div className={styles.structuredHeader}>
                    <Text strong>结构化想法</Text>
                  </div>
                  <div className={styles.structuredIdeas}>
                    {result.ideas.map((item, index) => {
                      const groupKey = `${index}-${item.knowledge_point}`
                      return (
                        <Card key={groupKey} size="small" className={styles.ideaGroup}>
                          <div className={styles.ideaGroupHeader}>
                            <Text strong>{item.knowledge_point}</Text>
                            {item.description ? (
                              <Text type="secondary">{item.description}</Text>
                            ) : null}
                          </div>
                          <List
                            size="small"
                            dataSource={item.research_ideas || []}
                            locale={{ emptyText: '暂无想法条目' }}
                            renderItem={(idea) => {
                              const ideaKey = `${groupKey}-${idea.title}`
                              const isKept = item.kept_ideas?.includes(idea.title)
                              const isRejected = item.rejected_ideas?.includes(idea.title)
                              const reason = item.reasons?.[idea.title]
                              return (
                                <List.Item
                                  className={styles.ideaItem}
                                  actions={[
                                    <Button
                                      key="research"
                                      type="link"
                                      onClick={() =>
                                        handleStartDeepResearch(idea.title, {
                                          ideaKey,
                                          ideaTitle: idea.title,
                                          ideaDescription: idea.description,
                                          knowledgePoint: item.knowledge_point,
                                          pointDescription: item.description,
                                          dimension: idea.dimension,
                                          novelty: idea.novelty,
                                          feasibility: idea.feasibility,
                                          reason,
                                        })
                                      }
                                    >
                                      深入调研
                                    </Button>,
                                    <Button
                                      key="edit"
                                      type="link"
                                      onClick={() =>
                                        handleEditIdea({
                                          key: ideaKey,
                                          ideaTitle: idea.title,
                                          ideaDescription: idea.description,
                                          knowledgePoint: item.knowledge_point,
                                          pointDescription: item.description,
                                          dimension: idea.dimension,
                                          novelty: idea.novelty,
                                          feasibility: idea.feasibility,
                                          reason,
                                        })
                                      }
                                    >
                                      编辑后调研
                                    </Button>,
                                    <Button
                                      key="save"
                                      type="link"
                                      loading={!!savingNoteIds[ideaKey]}
                                      onClick={() =>
                                        handleSaveIdeaToNotebook(ideaKey, {
                                          ideaTitle: idea.title,
                                          ideaDescription: idea.description,
                                          knowledgePoint: item.knowledge_point,
                                          pointDescription: item.description,
                                          dimension: idea.dimension,
                                          novelty: idea.novelty,
                                          feasibility: idea.feasibility,
                                        })
                                      }
                                    >
                                      保存到笔记
                                    </Button>,
                                    <Button
                                      key="save-edit"
                                      type="link"
                                      loading={!!savingNoteIds[ideaKey]}
                                      onClick={() =>
                                        handleSaveIdeaToNotebook(
                                          ideaKey,
                                          {
                                            ideaTitle: idea.title,
                                            ideaDescription: idea.description,
                                            knowledgePoint: item.knowledge_point,
                                            pointDescription: item.description,
                                            dimension: idea.dimension,
                                            novelty: idea.novelty,
                                            feasibility: idea.feasibility,
                                          },
                                          { openAfterSave: true },
                                        )
                                      }
                                    >
                                      保存并编辑
                                    </Button>,
                                  ]}
                                >
                                  <div className={styles.ideaMeta}>
                                    <div className={styles.ideaTitleRow}>
                                      <Text strong>{idea.title}</Text>
                                      {isKept ? <Tag color="green">保留</Tag> : null}
                                      {isRejected ? <Tag color="red">淘汰</Tag> : null}
                                      {idea.dimension ? <Tag>{idea.dimension}</Tag> : null}
                                    </div>
                                    {idea.description ? (
                                      <Text type="secondary">{idea.description}</Text>
                                    ) : null}
                                    <div className={styles.ideaInsights}>
                                      {idea.novelty ? (
                                        <Text type="secondary">创新点：{idea.novelty}</Text>
                                      ) : null}
                                      {idea.feasibility ? (
                                        <Text type="secondary">可行性：{idea.feasibility}</Text>
                                      ) : null}
                                      {reason ? (
                                        <Text type="secondary">筛选原因：{reason}</Text>
                                      ) : null}
                                    </div>
                                  </div>
                                </List.Item>
                              )
                            }}
                          />
                          {item.statement_markdown ? (
                            <div className={styles.ideaStatement}>
                              <Markdown value={item.statement_markdown} />
                            </div>
                          ) : null}
                        </Card>
                      )
                    })}
                  </div>
                  <Divider />
                </>
              ) : null}
              <Markdown value={result.ideas_markdown} />
              <Divider />
              <div>
                <Text strong>引用</Text>
                {renderCitations(result.citations)}
              </div>
              <Divider />
              <div>
                <Text strong>Trace</Text>
                <pre className={styles.traceBox}>
                  {JSON.stringify(result.trace ?? {}, null, 2)}
                </pre>
              </div>
            </Space>
          )}
        </Card>
      </div>

      <Modal
        title="编辑研究主题"
        open={!!editingIdea}
        onCancel={() => setEditingIdea(null)}
        onOk={handleConfirmEditIdea}
        okText="开始调研"
        cancelText="取消"
        destroyOnClose
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Input
            value={editingIdeaTitle}
            onChange={(event) => setEditingIdeaTitle(event.target.value)}
            placeholder="输入研究主题"
          />
          {editingIdea?.ideaDescription ? (
            <Text type="secondary">原始描述：{editingIdea.ideaDescription}</Text>
          ) : null}
        </Space>
      </Modal>
    </div>
  )
}
