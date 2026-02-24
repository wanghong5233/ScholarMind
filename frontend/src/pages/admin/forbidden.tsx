import { Button, Card, Result, Space } from 'antd'
import { useNavigate } from 'react-router-dom'

export default function AdminForbiddenPage() {
  const navigate = useNavigate()

  return (
    <div style={{ padding: 24 }}>
      <Card>
        <Result
          status="403"
          title="无管理后台权限"
          subTitle="当前登录态不具备后台权限，请使用后台管理员账号重新登录。"
          extra={
            <Space>
              <Button type="primary" onClick={() => navigate('/admin/login?redirect=%2Fadmin')}>
                前往后台登录
              </Button>
            </Space>
          }
        />
      </Card>
    </div>
  )
}
