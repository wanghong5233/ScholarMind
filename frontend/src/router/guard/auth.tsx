import * as api from '@/api'
import { adminAuthActions, adminAuthState } from '@/store/adminAuth'
import { userState } from '@/store/user'
import { Spin } from 'antd'
import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import { useRoute } from '../hook'

type AdminAccessState = 'idle' | 'checking' | 'allowed' | 'denied'
let adminAccessCache: { token: string; allowed: boolean } | null = null

export function AuthGuard({ children }: { children?: React.ReactNode }) {
  const route = useRoute()
  const user = useSnapshot(userState)
  const adminAuth = useSnapshot(adminAuthState)
  const location = useLocation()
  const [adminAccessState, setAdminAccessState] = useState<AdminAccessState>('idle')
  const inAdminNamespace = location.pathname.startsWith('/admin')
  const redirectPath = `${location.pathname}${location.search || ''}`

  useEffect(() => {
    if (!inAdminNamespace || !adminAuth.token) {
      if (!adminAuth.token) {
        adminAccessCache = null
      }
      setAdminAccessState('idle')
      return
    }
    if (
      adminAccessCache &&
      adminAccessCache.token === adminAuth.token &&
      adminAccessCache.allowed
    ) {
      setAdminAccessState(adminAccessCache.allowed ? 'allowed' : 'denied')
      return
    }
    let cancelled = false
    setAdminAccessState('checking')
    api.admin
      .getAdminMe({ errorToast: false })
      .then(({ data }) => {
        if (cancelled) return
        const allowed = Boolean(data?.is_admin)
        adminAccessCache = allowed ? { token: adminAuth.token as string, allowed } : null
        setAdminAccessState(allowed ? 'allowed' : 'denied')
      })
      .catch(() => {
        if (cancelled) return
        adminAccessCache = null
        setAdminAccessState('denied')
      })
    return () => {
      cancelled = true
    }
  }, [inAdminNamespace, adminAuth.token])

  if (!route?.auth) return children

  if (user._persist?.loading) {
    return (
      <div
        style={{
          height: '100%',
          minHeight: '240px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Spin tip="正在恢复登录状态..." />
      </div>
    )
  }

  if (inAdminNamespace) {
    if (!adminAuth.token) {
      return <Navigate to={`/admin/login?redirect=${encodeURIComponent(redirectPath)}`} replace />
    }
    if (adminAccessState === 'checking' || adminAccessState === 'idle') {
      return (
        <div
          style={{
            height: '100%',
            minHeight: '240px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Spin tip="正在校验管理权限..." />
        </div>
      )
    }
    if (adminAccessState !== 'allowed') {
      adminAuthActions.clear()
      return <Navigate to="/admin/forbidden" replace />
    }
    return children
  }

  if (!user.token) {
    return <Navigate to={`/login?redirect=${encodeURIComponent(redirectPath)}`} replace />
  }

  return children
}
