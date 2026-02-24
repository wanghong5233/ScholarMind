import * as api from '@/api'
import { adminAuthActions, adminAuthState } from '@/store/adminAuth'
import { Button, Card, Form, Input, Typography } from 'antd'
import { useMemo } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'

const { Title, Text } = Typography

export default function AdminLoginPage() {
  const adminAuth = useSnapshot(adminAuthState)
  const navigate = useNavigate()
  const location = useLocation()
  const redirectPath = useMemo(() => {
    const query = new URLSearchParams(location.search)
    const value = query.get('redirect')
    if (!value || !value.startsWith('/admin')) return '/admin'
    return value
  }, [location.search])

  if (adminAuth.token) {
    return <Navigate to={redirectPath} replace />
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
        background: '#f5f7fb',
      }}
    >
      <Card style={{ width: 420 }}>
        <Title level={3} style={{ marginBottom: 0 }}>
          管理后台登录
        </Title>
        <Text type="secondary">后台与主站用户体系独立，请使用后台管理员账号登录。</Text>
        <Form
          layout="vertical"
          style={{ marginTop: 20 }}
          onFinish={async (values: { username: string; password: string }) => {
            const { data } = await api.admin.adminLogin(values, { errorToast: false })
            adminAuthActions.setUsername(values.username)
            adminAuthActions.setToken(data.access_token)
            window.$app.message.success('后台登录成功')
            navigate(redirectPath, { replace: true })
          }}
        >
          <Form.Item
            label="管理员账号"
            name="username"
            initialValue={adminAuth.username || 'admin'}
            rules={[{ required: true, message: '请输入管理员账号' }]}
          >
            <Input placeholder="请输入管理员账号" size="large" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入密码" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large">
            登录后台
          </Button>
        </Form>
      </Card>
    </div>
  )
}

