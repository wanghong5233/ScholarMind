import logo from '@/assets/logo.svg'
import { adminAuthActions } from '@/store/adminAuth'
import { Button } from 'antd'
import { useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import './index.scss'

type AdminNavItem = {
  key: string
  label: string
}

const NAV_ITEMS: AdminNavItem[] = [
  { key: '/admin', label: '后台总览' },
  { key: '/admin/debug/parse', label: '解析调试' },
  { key: '/admin/debug/retrieval', label: '检索调试' },
]

export function AdminLayout({ children }: { children?: React.ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()

  const activeKey = useMemo(() => {
    if (location.pathname.startsWith('/admin/debug/parse')) return '/admin/debug/parse'
    if (location.pathname.startsWith('/admin/debug/retrieval')) return '/admin/debug/retrieval'
    return '/admin'
  }, [location.pathname])

  return (
    <div className="admin-layout">
      <aside className="admin-layout__sidebar">
        <div className="admin-layout__brand" onClick={() => navigate('/admin')}>
          <img src={logo} alt="ScholarMind" className="admin-layout__logo" />
          <div className="admin-layout__brand-text">
            <div className="admin-layout__brand-title">ScholarMind</div>
            <div className="admin-layout__brand-subtitle">Admin Console</div>
          </div>
        </div>

        <div className="admin-layout__nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`admin-layout__nav-item ${activeKey === item.key ? 'is-active' : ''}`}
              onClick={() => navigate(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="admin-layout__footer">
          <Button
            size="small"
            onClick={() => {
              adminAuthActions.clear()
              navigate('/admin/login', { replace: true })
            }}
          >
            退出后台
          </Button>
        </div>
      </aside>

      <main className="admin-layout__content">{children}</main>
    </div>
  )
}
