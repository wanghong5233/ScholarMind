import iconNewchat from '@/assets/layout/newchat.svg'
import iconRepository from '@/assets/layout/repository.svg'
import iconDebug from '@/assets/layout/debug.svg'
import iconEdit from '@/assets/layout/edit.svg'
import iconResearch from '@/assets/layout/debug.svg'
import logo from '@/assets/logo.svg'
import { deviceState } from '@/store/device'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import { Background } from './background'
import { Footer } from './footer'
import './index.scss'
import { Nav } from './nav'

const TITLE = import.meta.env.VITE_TITLE

export function BaseLayout({ children }: { children?: React.ReactNode }) {
  const navigate = useNavigate()
  const device = useSnapshot(deviceState)
  const location = useLocation()

  const isActive = (path: string) => location.pathname.startsWith(path)

  return (
    <div className="base-layout">
      <div className="base-layout__sidebar">
        <div className="base-layout__logo">
          <img
            className="logo"
            src={logo}
            onClick={() => (device.chatting ? null : navigate('/'))}
          />
          <span className="title">{TITLE}</span>
        </div>

        <div className="base-layout__sidebar-main scrollbar-style">
          <div className="base-layout__sidebar-main-content">
            <div
              className="base-layout__nav-header"
              onClick={() => (device.chatting ? null : navigate('/'))}
            >
              <img className="base-layout__nav-header-icon" src={iconNewchat} />
              <span className="base-layout__nav-header-title">新对话</span>
            </div>

            <Nav />

          <div
            className={`base-layout__nav-header ${isActive('/repository') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/repository'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconRepository}
              />
              <span className="base-layout__nav-header-title">知识库</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/latex-editor') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/latex-editor'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconEdit}
              />
              <span className="base-layout__nav-header-title">LaTeX 编辑器</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/deep-research') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/deep-research'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconResearch}
              />
              <span className="base-layout__nav-header-title">DeepResearch</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/idea-generation') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/idea-generation'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconResearch}
              />
              <span className="base-layout__nav-header-title">研究想法生成</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/co-writer') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/co-writer'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconEdit}
              />
              <span className="base-layout__nav-header-title">交互式想法生成</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/debug/parse') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/debug/parse'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconDebug}
              />
              <span className="base-layout__nav-header-title">解析调试</span>
            </div>

          <div
            className={`base-layout__nav-header ${isActive('/debug/retrieval') ? 'is-active' : ''}`}
            onClick={() => (device.chatting ? null : navigate('/debug/retrieval'))}
          >
              <img
                className="base-layout__nav-header-icon"
                src={iconDebug}
              />
              <span className="base-layout__nav-header-title">检索调试</span>
            </div>
          </div>

          <Footer />
        </div>
      </div>

      <div className="base-layout__content">{children}</div>

      <Background />
    </div>
  )
}
