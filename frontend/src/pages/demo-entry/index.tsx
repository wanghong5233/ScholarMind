import * as api from '@/api'
import { userActions, userState } from '@/store/user'
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

/**
 * /demo 是 testuser 的静默自动登录入口。对外不暴露 demo 概念：
 * - 成功：写 token 后跳转到 /chat（与正常登录后的默认目标一致）
 * - 失败：静默回到首页 /，由游客流程接管，不弹错误、不暴露 demo 字样
 */
export default function DemoEntryPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useSnapshot(userState)

  useEffect(() => {
    if (user.token) {
      navigate('/chat', { replace: true })
      return
    }

    let cancelled = false
    const query = new URLSearchParams(location.search)
    const code = (query.get('code') || '').trim()

    const bootstrap = async () => {
      try {
        const { data } = await api.user.demoEntry(
          code ? { code } : undefined,
          {
            loading: false,
            errorToast: false,
            timeout: 12000,
          },
        )
        if (cancelled) return
        userActions.setToken(data.access_token)
        userActions.setUsername(data.username || 'testuser')
        navigate('/chat', { replace: true })
      } catch {
        if (cancelled) return
        navigate('/', { replace: true })
      }
    }

    void bootstrap()

    return () => {
      cancelled = true
    }
  }, [user.token, location.search, navigate])

  return null
}
