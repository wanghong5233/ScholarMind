import iconNewchat from '@/assets/layout/newchat.svg'
import iconRepository from '@/assets/layout/repository.svg'
import iconEdit from '@/assets/layout/edit.svg'
import logo from '@/assets/logo.svg'
import * as api from '@/api'
import { NOTEBOOK_WORKSPACE_ID, ensureNotebookWorkspace } from '@/utils/notebook'
import { deviceActions, deviceState } from '@/store/device'
import { userState } from '@/store/user'
import { requireLogin } from '@/utils/auth'
import { isDemoEntryEnabled } from '@/utils/demo'
import { BookOutlined, BulbOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons'
import { Button, Tooltip, message } from 'antd'
import { useCallback, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import { Background } from './background'
import { Footer } from './footer'
import './index.scss'
import { Nav } from './nav'

const TITLE = import.meta.env.VITE_TITLE || 'ScholarMind'
const IDEAGEN_TEMP_DISABLED = true
const IDEAGEN_DISABLED_TIP = 'IdeaGen 功能暂时关闭，后续开放'

export function BaseLayout({ children }: { children?: React.ReactNode }) {
  const navigate = useNavigate()
  const device = useSnapshot(deviceState)
  const user = useSnapshot(userState)
  const location = useLocation()

  const isActive = (path: string) => location.pathname.startsWith(path)
  const isNotebookRoute = location.pathname.startsWith(`/doc-studio/${NOTEBOOK_WORKSPACE_ID}`)
  const isDocStudioRoute = isActive('/doc-studio') && !isNotebookRoute

  const handleOpenNotebook = useCallback(async () => {
    if (device.chatting) return
    if (!requireLogin(user.token, navigate, { redirectPath: `/doc-studio/${NOTEBOOK_WORKSPACE_ID}` })) {
      return
    }
    try {
      await ensureNotebookWorkspace()
      navigate(`/doc-studio/${NOTEBOOK_WORKSPACE_ID}`)
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.response?.data?.message || error?.message
      message.error(detail ? `打开笔记本失败：${detail}` : '打开笔记本失败')
    }
  }, [device.chatting, navigate, user.token])

  const handleOpenIdeaGen = useCallback(() => {
    if (device.chatting) return
    if (IDEAGEN_TEMP_DISABLED) {
      message.info(IDEAGEN_DISABLED_TIP)
      return
    }
    if (!requireLogin(user.token, navigate, { redirectPath: '/idea-generation' })) return
    navigate('/idea-generation')
  }, [device.chatting, navigate, user.token])

  useEffect(() => {
    document.title = TITLE || 'ScholarMind'
  }, [])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === '\\') {
        e.preventDefault()
        deviceActions.toggleSidebar()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => {
    if (!isDemoEntryEnabled() || !user.token) return
    const path = `${location.pathname}${location.search || ''}`
    void api.user.postDemoVisit({ path }).catch(() => {})
  }, [location.pathname, location.search, user.token])

  return (
    <div className={`base-layout ${device.sidebarCollapsed ? 'base-layout--sidebar-collapsed' : ''}`}>
      <div className="base-layout__sidebar">
        <div
          className="base-layout__logo"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <img
              className="logo"
              src={logo}
              onClick={() => (device.chatting ? null : navigate('/chat'))}
            />
            <span className="title">{TITLE}</span>
          </div>
          {!device.sidebarCollapsed && (
            <div style={{ flexShrink: 0, marginLeft: 16 }}>
              <Tooltip title="收起导航">
                <Button
                  type="text"
                  className="base-layout__sidebar-toggle"
                  icon={<MenuFoldOutlined />}
                  onClick={() => deviceActions.toggleSidebar()}
                />
              </Tooltip>
            </div>
          )}
        </div>

        <div className="base-layout__sidebar-main">
          <div className="base-layout__sidebar-main-content scrollbar-style">
            <div
              className="base-layout__new-chat"
              onClick={() => (device.chatting ? null : navigate('/chat'))}
            >
              <img className="base-layout__new-chat-icon" src={iconNewchat} />
              <span className="base-layout__new-chat-title">新对话</span>
            </div>

            <div
              className={`base-layout__nav-header ${isActive('/repository') ? 'is-active' : ''}`}
              onClick={() => {
                if (device.chatting) return
                if (!requireLogin(user.token, navigate, { redirectPath: '/repository' })) return
                navigate('/repository')
              }}
            >
              <img
                className="base-layout__nav-header-icon"
                src={iconRepository}
              />
              <span className="base-layout__nav-header-title">知识库</span>
            </div>

            <div
              className={`base-layout__nav-header ${isActive('/idea-generation') ? 'is-active' : ''} ${
                IDEAGEN_TEMP_DISABLED ? 'is-disabled' : ''
              }`}
              onClick={handleOpenIdeaGen}
            >
              <BulbOutlined className="base-layout__nav-header-icon base-layout__nav-header-icon--antd" />
              <span className="base-layout__nav-header-title">IdeaGen</span>
            </div>

            <div
              className={`base-layout__nav-header ${isNotebookRoute ? 'is-active' : ''}`}
              onClick={() => {
                void handleOpenNotebook()
              }}
            >
              <BookOutlined className="base-layout__nav-header-icon base-layout__nav-header-icon--antd" />
              <span className="base-layout__nav-header-title">笔记本</span>
            </div>

            <div
              className={`base-layout__nav-header ${isDocStudioRoute ? 'is-active' : ''}`}
              onClick={() => {
                if (device.chatting) return
                if (!requireLogin(user.token, navigate, { redirectPath: '/doc-studio' })) return
                navigate('/doc-studio')
              }}
            >
              <img
                className="base-layout__nav-header-icon"
                src={iconEdit}
              />
              <span className="base-layout__nav-header-title">Doc Studio</span>
            </div>

            <Nav />
          </div>

          <Footer />
        </div>
      </div>

      {device.sidebarCollapsed && (
        <Tooltip title="展开导航栏 (Ctrl+\ 可切换)" placement="right">
          <button
            type="button"
            className="base-layout__sidebar-trigger"
            onClick={() => deviceActions.setSidebarCollapsed(false)}
            aria-label="展开导航栏"
          >
            <MenuUnfoldOutlined />
          </button>
        </Tooltip>
      )}

      <div className="base-layout__content">{children}</div>

      <Background />
    </div>
  )
}
