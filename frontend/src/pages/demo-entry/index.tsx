import * as api from '@/api'
import { userActions } from '@/store/user'
import { Flex, Spin, Typography } from 'antd'
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export default function DemoEntryPage() {
  const navigate = useNavigate()
  const location = useLocation()

  useEffect(() => {
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
      } catch (error: any) {
        if (cancelled) return
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        window.$app.message.error(detail ? `演示入口不可用：${detail}` : '演示入口不可用')
        navigate('/login', { replace: true })
      }
    }

    void bootstrap()

    return () => {
      cancelled = true
    }
  }, [location.search, navigate])

  return (
    <Flex
      vertical
      align="center"
      justify="center"
      style={{ minHeight: '100vh', gap: 12 }}
    >
      <Spin size="large" />
      <Typography.Text type="secondary">正在进入演示环境...</Typography.Text>
    </Flex>
  )
}

