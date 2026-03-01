import * as api from '@/api'
import { useRequest } from 'ahooks'
import { Card, Space, Statistic, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'

const { Title, Text } = Typography

export default function AdminDemoStatsPage() {
  const {
    data,
    loading,
    refresh,
  } = useRequest(
    async () => {
      const { data: res } = await api.admin.getAdminDemoStats(
        { page: 1, page_size: 200 },
        { errorToast: false },
      )
      return res
    },
    { refreshDeps: [] },
  )

  const columns: ColumnsType<api.admin.AdminDemoStatsItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: 'IP', dataIndex: 'ip', width: 140 },
    { title: '路径', dataIndex: 'path', ellipsis: true },
    {
      title: 'User-Agent',
      dataIndex: 'user_agent',
      ellipsis: true,
      render: (v: string | null) => (v ? <Text type="secondary" style={{ fontSize: 12 }}>{v}</Text> : '-'),
    },
    {
      title: '访问时间',
      dataIndex: 'visited_at',
      width: 180,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '-'),
    },
  ]

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Title level={4}>Demo 访问统计</Title>
      <Text type="secondary">
        临时用于追踪简历/GitHub 等入口的 demo 体验情况。记录 IP、路径、访问时间。
      </Text>

      {data?.by_ip && data.by_ip.length > 0 && (
        <Card title="按 IP 汇总（访问次数 Top）" size="small">
          <Space wrap>
            {data.by_ip.slice(0, 20).map(({ ip, count }) => (
              <Statistic key={ip} title={ip} value={count} />
            ))}
          </Space>
        </Card>
      )}

      <Card title={`访问记录（共 ${data?.total ?? 0} 条）`}>
        <Table
          loading={loading}
          columns={columns}
          dataSource={data?.items ?? []}
          rowKey="id"
          size="small"
          pagination={false}
        />
      </Card>
    </Space>
  )
}
