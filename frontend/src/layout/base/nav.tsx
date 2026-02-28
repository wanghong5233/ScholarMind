import * as api from '@/api'
import { sessionActions, sessionState } from '@/store/session'
import { useRequest } from 'ahooks'
import { DeleteOutlined, EditOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Dropdown, Input, Modal, Popconfirm, message } from 'antd'
import dayjs from 'dayjs'
import type { MenuProps } from 'antd'
import { MouseEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import classNames from 'classnames'
import './nav.scss'

const MAX_RECENT_SIDEBAR_ITEMS = 10

function normalizeSessionTitle(value?: string) {
  const text = String(value || '').trim()
  return text || '新对话'
}

function toSafeTimestamp(raw?: string) {
  const ts = dayjs(raw).valueOf()
  return Number.isFinite(ts) ? ts : 0
}

export function Nav() {
  const navigate = useNavigate()
  const location = useLocation()
  const session = useSnapshot(sessionState)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [renameModalOpen, setRenameModalOpen] = useState(false)
  const [renameSessionId, setRenameSessionId] = useState('')
  const [renameSessionName, setRenameSessionName] = useState('')
  const [renameSubmitting, setRenameSubmitting] = useState(false)

  const currentSessionId = useMemo(() => {
    const matched = location.pathname.match(/^\/chat\/([^/?#]+)/)
    return matched?.[1] || ''
  }, [location.pathname])

  const handleEnterSession = useCallback(
    (sessionId: string, closeSearch = false) => {
      if (closeSearch) {
        setSearchOpen(false)
      }
      navigate(`/chat/${sessionId}`)
    },
    [navigate],
  )

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        try {
          const { data } = await api.deepResearch.listDeepResearchRunsBySession(
            sessionId,
            80,
            { loading: false, errorToast: false, timeout: 8000 },
          )
          const activeRuns = (data?.items || []).filter((item) => {
            const status = String(item?.status || '').toLowerCase()
            return status === 'running' || status === 'queued'
          })
          if (activeRuns.length) {
            await Promise.allSettled(
              activeRuns.map((run) =>
                api.deepResearch.cancelDeepResearch(run.research_id, {
                  loading: false,
                  errorToast: false,
                  timeout: 8000,
                }),
              ),
            )
          }
        } catch {
          // Best-effort cancellation; do not block session deletion on this step.
        }

        await api.session.remove(
          { sessionId },
          {
            loading: false,
            errorToast: false,
            timeout: 15000,
          },
        )
        sessionActions.remove(sessionId)
        message.success('会话已删除')
        if (window.location.pathname.includes(`/chat/${sessionId}`)) {
          navigate('/chat')
        }
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        message.error(detail ? `删除会话失败：${detail}` : '删除会话失败')
      }
    },
    [navigate],
  )

  const confirmDeleteSession = useCallback(
    (sessionId: string) => {
      Modal.confirm({
        title: '确认删除该对话？',
        content: '删除后不可恢复，将同时清空会话内消息。',
        okText: '删除',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: async () => {
          await handleDeleteSession(sessionId)
        },
      })
    },
    [handleDeleteSession],
  )

  const openRenameModal = useCallback((item: API.Session) => {
    setRenameSessionId(item.session_id)
    setRenameSessionName(normalizeSessionTitle(item.session_name))
    setRenameModalOpen(true)
  }, [])

  const closeRenameModal = useCallback(() => {
    if (renameSubmitting) return
    setRenameModalOpen(false)
    setRenameSessionId('')
    setRenameSessionName('')
  }, [renameSubmitting])

  const handleRenameSession = useCallback(async () => {
    const sessionId = String(renameSessionId || '').trim()
    const nextName = String(renameSessionName || '').trim()
    if (!sessionId) return
    if (!nextName) {
      message.warning('会话名称不能为空')
      return
    }
    setRenameSubmitting(true)
    try {
      await api.session.rename(
        { sessionId, sessionName: nextName },
        { loading: false, errorToast: false },
      )
      sessionActions.setList(
        session.list.map((item) =>
          item.session_id === sessionId ? { ...item, session_name: nextName } : item,
        ),
      )
      sessionActions.updateKey()
      message.success('会话已重命名')
      setRenameModalOpen(false)
      setRenameSessionId('')
      setRenameSessionName('')
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      message.error(detail ? `重命名失败：${detail}` : '重命名失败')
    } finally {
      setRenameSubmitting(false)
    }
  }, [renameSessionId, renameSessionName, session.list])

  const buildSessionContextMenu = useCallback(
    (item: API.Session): MenuProps => ({
      items: [
        {
          key: `rename-${item.session_id}`,
          icon: <EditOutlined />,
          label: '重命名',
          onClick: () => openRenameModal(item),
        },
        {
          key: `delete-${item.session_id}`,
          icon: <DeleteOutlined />,
          label: '删除对话',
          danger: true,
          onClick: () => {
            confirmDeleteSession(item.session_id)
          },
        },
      ],
    }),
    [confirmDeleteSession, openRenameModal],
  )

  const stopPropagation = useCallback((event: MouseEvent<HTMLElement>) => {
    event.stopPropagation()
  }, [])

  useRequest(
    async () => {
      const { data } = await api.session.list(
        { surface: 'deep_chat' },
        {
          loading: session.list.length ? false : true,
        },
      )
      return data
    },
    {
      refreshDeps: [session.updateKey],
      onSuccess(data) {
        sessionActions.setList(data?.sessions || [])
      },
    },
  )

  useEffect(() => {
    if (!searchOpen && searchKeyword) {
      setSearchKeyword('')
    }
  }, [searchOpen, searchKeyword])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const sortedSessions = useMemo(() => {
    const source = Array.isArray(session.list) ? [...session.list] : []
    source.sort((a, b) => {
      const bTs = toSafeTimestamp(b.updated_at || b.created_at)
      const aTs = toSafeTimestamp(a.updated_at || a.created_at)
      return bTs - aTs
    })
    return source
  }, [session.list])

  const sidebarRecentSessions = useMemo(
    () => sortedSessions.slice(0, MAX_RECENT_SIDEBAR_ITEMS),
    [sortedSessions],
  )

  const filteredSessions = useMemo(() => {
    const keyword = searchKeyword.trim().toLowerCase()
    if (!keyword) return sortedSessions
    return sortedSessions.filter((item) => {
      const title = normalizeSessionTitle(item.session_name).toLowerCase()
      return title.includes(keyword) || item.session_id.toLowerCase().includes(keyword)
    })
  }, [searchKeyword, sortedSessions])

  const { todaySessions, earlierSessions } = useMemo(() => {
    const today: API.Session[] = []
    const earlier: API.Session[] = []
    filteredSessions.forEach((item) => {
      const ts = dayjs(item.updated_at || item.created_at)
      if (ts.isValid() && ts.isSame(dayjs(), 'day')) {
        today.push(item)
      } else {
        earlier.push(item)
      }
    })
    return { todaySessions: today, earlierSessions: earlier }
  }, [filteredSessions])

  return (
    <div className="base-layout-nav">
      <div className="base-layout-nav__search-drawer">
        <button
          type="button"
          className="base-layout-nav__search-trigger"
          onClick={() => setSearchOpen(true)}
        >
          <SearchOutlined />
          <span className="base-layout-nav__search-trigger-text">搜索对话</span>
          <span className="base-layout-nav__search-trigger-shortcut">Ctrl K</span>
        </button>

        <div className="base-layout-nav__search-drawer-content">
          <div className="base-layout-nav__search-drawer-title">Today</div>
          <div className="base-layout-nav__search-drawer-list">
            {sidebarRecentSessions.length === 0 && (
              <div className="base-layout-nav__empty">暂无历史对话</div>
            )}
            {sidebarRecentSessions.map((item) => {
              const isActive = currentSessionId === item.session_id
              return (
                <Dropdown
                  key={item.session_id}
                  trigger={['contextMenu']}
                  menu={buildSessionContextMenu(item)}
                >
                  <div
                    className={classNames('base-layout-nav__item', {
                      'base-layout-nav__item--active': isActive,
                    })}
                  >
                    <div
                      className="base-layout-nav__item-main"
                      onClick={() => handleEnterSession(item.session_id)}
                    >
                      <div className="title">{normalizeSessionTitle(item.session_name)}</div>
                    </div>
                  </div>
                </Dropdown>
              )
            })}
          </div>
        </div>
      </div>

      <Modal
        open={searchOpen}
        onCancel={() => setSearchOpen(false)}
        footer={null}
        centered
        width={980}
        destroyOnClose
        className="base-layout-nav__search-modal"
      >
        <div className="base-layout-nav__search-modal-inner">
          <div className="base-layout-nav__search-modal-input-wrap">
            <SearchOutlined className="base-layout-nav__search-modal-icon" />
            <Input
              autoFocus
              variant="borderless"
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
              placeholder="Search..."
            />
          </div>

          <div className="base-layout-nav__search-section">
            <div className="base-layout-nav__search-section-title">Actions</div>
            <button
              type="button"
              className="base-layout-nav__search-action"
              onClick={() => {
                setSearchOpen(false)
                navigate('/chat')
              }}
            >
              <EditOutlined />
              <span>Create New Chat</span>
            </button>
          </div>

          <div className="base-layout-nav__search-section">
            <div className="base-layout-nav__search-section-title">Today</div>
            <div className="base-layout-nav__search-list">
              {todaySessions.length === 0 && (
                <div className="base-layout-nav__search-empty">暂无匹配对话</div>
              )}
              {todaySessions.map((item) => {
                const isActive = currentSessionId === item.session_id
                return (
                  <Dropdown
                    key={`today-${item.session_id}`}
                    trigger={['contextMenu']}
                    menu={buildSessionContextMenu(item)}
                  >
                    <div
                      className={classNames('base-layout-nav__search-item', {
                        'base-layout-nav__search-item--active': isActive,
                      })}
                      onClick={() => handleEnterSession(item.session_id, true)}
                    >
                      <div className="base-layout-nav__search-item-title">
                        {normalizeSessionTitle(item.session_name)}
                      </div>
                      <div className="base-layout-nav__search-item-meta">
                        {dayjs(item.updated_at || item.created_at).format('HH:mm')}
                      </div>
                      <Popconfirm
                        title="确认删除该对话？"
                        description="删除后不可恢复，将同时清空会话内消息。"
                        onConfirm={() => handleDeleteSession(item.session_id)}
                      >
                        <Button
                          type="text"
                          danger
                          size="small"
                          onClick={stopPropagation}
                          icon={<DeleteOutlined />}
                        />
                      </Popconfirm>
                    </div>
                  </Dropdown>
                )
              })}
            </div>
          </div>

          {earlierSessions.length > 0 && (
            <div className="base-layout-nav__search-section">
              <div className="base-layout-nav__search-section-title">Earlier</div>
              <div className="base-layout-nav__search-list">
                {earlierSessions.map((item) => {
                  const isActive = currentSessionId === item.session_id
                  return (
                    <Dropdown
                      key={`earlier-${item.session_id}`}
                      trigger={['contextMenu']}
                      menu={buildSessionContextMenu(item)}
                    >
                      <div
                        className={classNames('base-layout-nav__search-item', {
                          'base-layout-nav__search-item--active': isActive,
                        })}
                        onClick={() => handleEnterSession(item.session_id, true)}
                      >
                        <div className="base-layout-nav__search-item-title">
                          {normalizeSessionTitle(item.session_name)}
                        </div>
                        <div className="base-layout-nav__search-item-meta">
                          {dayjs(item.updated_at || item.created_at).format('MM/DD HH:mm')}
                        </div>
                        <Popconfirm
                          title="确认删除该对话？"
                          description="删除后不可恢复，将同时清空会话内消息。"
                          onConfirm={() => handleDeleteSession(item.session_id)}
                        >
                          <Button
                            type="text"
                            danger
                            size="small"
                            onClick={stopPropagation}
                            icon={<DeleteOutlined />}
                          />
                        </Popconfirm>
                      </div>
                    </Dropdown>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </Modal>
      <Modal
        title="重命名会话"
        open={renameModalOpen}
        onCancel={closeRenameModal}
        onOk={() => {
          void handleRenameSession()
        }}
        okText="保存"
        cancelText="取消"
        okButtonProps={{
          loading: renameSubmitting,
        }}
        destroyOnClose
      >
        <Input
          autoFocus
          maxLength={120}
          value={renameSessionName}
          onChange={(event) => setRenameSessionName(event.target.value)}
          onPressEnter={() => {
            void handleRenameSession()
          }}
          placeholder="请输入会话名称"
        />
      </Modal>
    </div>
  )
}
