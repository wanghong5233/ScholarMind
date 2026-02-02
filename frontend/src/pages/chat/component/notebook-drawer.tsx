import * as api from '@/api'
import { fetchFileContent, fetchWorkspaceFiles } from '@/api/latexAgent'
import {
  NOTEBOOK_WORKSPACE_ID,
  createNotebookNoteFile,
  ensureNotebookWorkspace,
} from '@/utils/notebook'
import {
  Button,
  Checkbox,
  Drawer,
  Input,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import styles from './notebook-drawer.module.scss'

const { Text } = Typography

const NOTEBOOK_MAX_LIST = 100

type NotebookNoteEntry = {
  id: string
  path: string
  title: string
  summary: string
  tags: string[]
  createdAt?: string
  updatedAt?: number
  content: string
  sourceExcerpt?: string
}

type NotebookDrawerProps = {
  open: boolean
  sessionId?: string
  onClose: () => void
  getSelectionText: () => string
}

const FRONT_MATTER_REGEX = /^---\s*[\r\n]+([\s\S]*?)\r?\n---\s*/m

function stripQuotes(value: string) {
  return value.replace(/^['"]|['"]$/g, '').trim()
}

function parseTagsFromMeta(metaRaw: string, fallback: string) {
  const inlineMatch = metaRaw.match(/tags:\s*\[(.*)\]/)
  if (inlineMatch?.[1]) {
    return inlineMatch[1]
      .split(',')
      .map((item) => stripQuotes(item.trim()))
      .filter(Boolean)
  }
  const blockMatch = metaRaw.match(/tags:\s*\n((?:\s*-\s*.+\n?)+)/)
  if (blockMatch?.[1]) {
    return blockMatch[1]
      .split(/\r?\n/)
      .map((line) => line.replace(/^\s*-\s*/, '').trim())
      .filter(Boolean)
  }
  if (!fallback) return []
  return fallback
    .split(',')
    .map((item) => stripQuotes(item.trim()))
    .filter(Boolean)
}

function parseNoteMeta(content: string) {
  const match = content.match(FRONT_MATTER_REGEX)
  const metaRaw = match?.[1] || ''
  const body = match ? content.slice(match[0].length) : content
  const meta: Record<string, string> = {}
  metaRaw.split(/\r?\n/).forEach((line) => {
    const idx = line.indexOf(':')
    if (idx === -1) return
    const key = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim()
    if (key) meta[key] = stripQuotes(value)
  })
  const title =
    meta.title ||
    body.match(/^#\s+(.+)$/m)?.[1]?.trim() ||
    meta.summary ||
    '未命名笔记'
  const summary = meta.summary || body.trim().split(/\r?\n/).slice(0, 3).join(' ')
  const tags = parseTagsFromMeta(metaRaw, meta.tags || '')
  return {
    title,
    summary,
    tags,
    createdAt: meta.created_at,
    sourceExcerpt: meta.source_excerpt,
  }
}

function flattenFiles(nodes: LatexAgentAPI.FileNode[]): LatexAgentAPI.FileNode[] {
  const result: LatexAgentAPI.FileNode[] = []
  const walk = (items: LatexAgentAPI.FileNode[]) => {
    items.forEach((node) => {
      if (node.type === 'file') {
        result.push(node)
      } else if (node.children?.length) {
        walk(node.children)
      }
    })
  }
  walk(nodes)
  return result
}

export default function NotebookDrawer(props: NotebookDrawerProps) {
  const { open, onClose, sessionId, getSelectionText } = props
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [notes, setNotes] = useState<NotebookNoteEntry[]>([])
  const [searchText, setSearchText] = useState('')
  const [activeTag, setActiveTag] = useState<string | undefined>()
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [draftOpen, setDraftOpen] = useState(false)
  const [draftLoading, setDraftLoading] = useState(false)
  const [draftContent, setDraftContent] = useState('')
  const [draftSaving, setDraftSaving] = useState(false)

  const loadNotebookNotes = useCallback(async () => {
    setLoading(true)
    try {
      await ensureNotebookWorkspace()
      const data = await fetchWorkspaceFiles({ workspaceId: NOTEBOOK_WORKSPACE_ID })
      const files = flattenFiles(data.files)
        .filter((node) => node.path.toLowerCase().endsWith('.md'))
        .sort((a, b) => (b.modifiedAt || 0) - (a.modifiedAt || 0))
        .slice(0, NOTEBOOK_MAX_LIST)
      const items = await Promise.all(
        files.map(async (file) => {
          const content = await fetchFileContent({
            workspaceId: NOTEBOOK_WORKSPACE_ID,
            path: file.path,
          })
          const meta = parseNoteMeta(content.content)
          return {
            id: file.path,
            path: file.path,
            title: meta.title,
            summary: meta.summary,
            tags: meta.tags,
            createdAt: meta.createdAt,
            sourceExcerpt: meta.sourceExcerpt,
            updatedAt: file.modifiedAt,
            content: content.content,
          } as NotebookNoteEntry
        }),
      )
      setNotes(items)
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `加载笔记失败：${detail}` : '加载笔记失败')
    } finally {
      setLoading(false)
    }
  }, [ensureNotebookWorkspace])

  useEffect(() => {
    if (open) {
      loadNotebookNotes()
    }
  }, [open, loadNotebookNotes])

  const availableTags = useMemo(() => {
    const tagSet = new Set<string>()
    notes.forEach((note) => {
      note.tags.forEach((tag) => tagSet.add(tag))
    })
    return Array.from(tagSet)
  }, [notes])

  const filteredNotes = useMemo(() => {
    const keyword = searchText.trim().toLowerCase()
    return notes.filter((note) => {
      if (activeTag && !note.tags.includes(activeTag)) return false
      if (!keyword) return true
      const haystack = `${note.title} ${note.summary} ${note.tags.join(' ')}`.toLowerCase()
      return haystack.includes(keyword)
    })
  }, [notes, searchText, activeTag])

  const selectedNotes = useMemo(() => {
    return notes.filter((note) => selectedIds.has(note.id))
  }, [notes, selectedIds])

  const handleToggleSelection = useCallback((noteId: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) {
        next.add(noteId)
      } else {
        next.delete(noteId)
      }
      return next
    })
  }, [])

  const handleGenerateNote = useCallback(async () => {
    const selection = getSelectionText().trim()
    if (!selection) {
      message.warning('请先选中对话内容')
      return
    }
    if (!sessionId) {
      message.warning('需要会话 ID 才能生成笔记')
      return
    }
    setDraftOpen(true)
    setDraftLoading(true)
    try {
      const { data } = await api.deepResearch.generateNotebookNote({
        selection,
        session_id: sessionId,
      })
      setDraftContent(data.note_markdown || '')
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `生成笔记失败：${detail}` : '生成笔记失败')
      setDraftOpen(false)
    } finally {
      setDraftLoading(false)
    }
  }, [getSelectionText, sessionId])

  const handleSaveDraft = useCallback(async () => {
    if (!draftContent.trim()) {
      message.warning('笔记内容为空')
      return
    }
    setDraftSaving(true)
    try {
      const title = parseNoteMeta(draftContent).title
      await createNotebookNoteFile(draftContent, title)
      message.success('笔记已保存')
      setDraftOpen(false)
      setDraftContent('')
      await loadNotebookNotes()
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `保存失败：${detail}` : '保存笔记失败')
    } finally {
      setDraftSaving(false)
    }
  }, [draftContent, loadNotebookNotes])

  const handleOpenNote = useCallback(
    (note: NotebookNoteEntry) => {
      navigate(
        `/latex-editor/${NOTEBOOK_WORKSPACE_ID}?file=${encodeURIComponent(note.path)}`,
      )
    },
    [navigate],
  )

  const handleStartDeepResearch = useCallback(
    (note: NotebookNoteEntry) => {
      if (!sessionId) {
        message.warning('需要会话 ID 才能发起 DeepResearch')
        return
      }
      const topic = note.title || '研究主题'
      navigate(
        `/deep-research?topic=${encodeURIComponent(topic)}&sessionId=${encodeURIComponent(
          sessionId,
        )}`,
        {
          state: {
            noteContext: {
              title: note.title,
              content: note.content,
              source: 'notebook',
              noteId: note.id,
              sessionId,
            },
          },
        },
      )
    },
    [navigate, sessionId],
  )

  const handleStartIdeaGen = useCallback(() => {
    if (!sessionId) {
      message.warning('需要会话 ID 才能发起 IdeaGen')
      return
    }
    if (!selectedNotes.length) {
      message.warning('请先选择笔记')
      return
    }
    navigate('/idea-generation', {
      state: {
        prefill: { sessionId },
        notes: selectedNotes.map((note) => ({
          id: note.id,
          title: note.title,
          content: note.content,
          tags: note.tags,
          source: note.path,
        })),
      },
    })
  }, [navigate, selectedNotes, sessionId])

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(filteredNotes.map((note) => note.id)))
  }, [filteredNotes])

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set())
  }, [])

  return (
    <Drawer
      title="笔记本"
      open={open}
      onClose={onClose}
      width={520}
      destroyOnClose
      className={styles.drawer}
    >
      <div className={styles.headerActions}>
        <Space wrap>
          <Button type="primary" onClick={handleGenerateNote}>
            选中内容生成笔记
          </Button>
          <Button onClick={handleStartIdeaGen} disabled={!selectedNotes.length}>
            IdeaGen
          </Button>
          <Button onClick={loadNotebookNotes} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>
      <div className={styles.filters}>
        <Input
          placeholder="搜索标题 / 摘要 / 标签"
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
          allowClear
        />
        <Select
          allowClear
          placeholder="按标签过滤"
          value={activeTag}
          onChange={(value) => setActiveTag(value)}
          options={availableTags.map((tag) => ({ label: tag, value: tag }))}
        />
      </div>
      <div className={styles.selectionBar}>
        <Space wrap size={8}>
          <Text type="secondary">已选 {selectedNotes.length} 条</Text>
          <Button size="small" onClick={handleSelectAll} disabled={!filteredNotes.length}>
            全选
          </Button>
          <Button size="small" onClick={handleClearSelection} disabled={!selectedNotes.length}>
            清空
          </Button>
        </Space>
      </div>
      <div className={styles.listContainer}>
        {loading ? (
          <div className={styles.loading}>
            <Spin />
          </div>
        ) : filteredNotes.length ? (
          <List
            dataSource={filteredNotes}
            renderItem={(note) => (
              <List.Item
                className={styles.noteItem}
                actions={[
                  <Button key="edit" type="link" onClick={() => handleOpenNote(note)}>
                    编辑
                  </Button>,
                  <Button key="research" type="link" onClick={() => handleStartDeepResearch(note)}>
                    深入调研
                  </Button>,
                ]}
              >
                <div className={styles.noteRow}>
                  <Checkbox
                    checked={selectedIds.has(note.id)}
                    onChange={(event) => handleToggleSelection(note.id, event.target.checked)}
                  />
                  <div className={styles.noteContent}>
                    <div className={styles.noteTitle}>{note.title}</div>
                    <div className={styles.noteSummary}>{note.summary}</div>
                    <div className={styles.noteMeta}>
                      {note.tags.map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                      {note.createdAt ? (
                        <Text type="secondary">
                          {dayjs(note.createdAt).format('YYYY-MM-DD HH:mm')}
                        </Text>
                      ) : null}
                    </div>
                  </div>
                </div>
              </List.Item>
            )}
          />
        ) : (
          <Text type="secondary">暂无笔记</Text>
        )}
      </div>
      <Modal
        title="生成笔记"
        open={draftOpen}
        onCancel={() => setDraftOpen(false)}
        onOk={handleSaveDraft}
        okText="保存笔记"
        cancelText="取消"
        confirmLoading={draftSaving}
        destroyOnClose
      >
        <Spin spinning={draftLoading}>
          <Input.TextArea
            value={draftContent}
            onChange={(event) => setDraftContent(event.target.value)}
            autoSize={{ minRows: 10, maxRows: 18 }}
            placeholder="生成的笔记将显示在这里"
          />
        </Spin>
      </Modal>
    </Drawer>
  )
}
