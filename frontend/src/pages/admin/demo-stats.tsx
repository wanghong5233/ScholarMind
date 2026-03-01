import * as api from '@/api'
import { useRequest } from 'ahooks'
import React from 'react'
import { Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { Dayjs } from 'dayjs'

const { Title, Text } = Typography
const { RangePicker } = DatePicker

export default function AdminDemoStatsPage() {
  const [dateRange, setDateRange] = React.useState<[Dayjs | null, Dayjs | null] | null>(
    null,
  )
  const {
    data,
    loading,
    refresh,
  } = useRequest(
    async () => {
      const params: Parameters<typeof api.admin.getAdminDemoStats>[0] = {
        page: 1,
        page_size: 200,
      }
      if (dateRange?.[0]) params.date_from = dateRange[0].format('YYYY-MM-DD')
      if (dateRange?.[1]) params.date_to = dateRange[1].format('YYYY-MM-DD')
      const { data: res } = await api.admin.getAdminDemoStats(
        params,
        { errorToast: false },
      )
      return res
    },
    { refreshDeps: [dateRange] },
  )

  const columns: ColumnsType<api.admin.AdminDemoStatsItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '访客编号',
      dataIndex: 'visitor_id',
      width: 90,
      render: (v: number | undefined) => (v != null ? `#${v}` : '-'),
    },
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
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Space wrap size="middle">
        <Title level={4} style={{ margin: 0 }}>Demo 访问统计</Title>
        <RangePicker
          allowClear
          value={dateRange}
          onChange={(val) => setDateRange(val ?? null)}
        />
        <Button size="small" onClick={refresh} loading={loading}>刷新</Button>
      </Space>
      <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
        临时用于追踪简历/GitHub 等入口的 demo 体验情况。同一 IP 分配相同访客编号，支持按日期筛选。
      </Text>

      {data && (
        <Row gutter={[24, 16]}>
          <Col span={6}>
            <Card size="small" style={{ marginBottom: 0 }}>
              <Statistic title="总访问次数" value={data.total ?? 0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" style={{ marginBottom: 0 }}>
              <Statistic title="独立访客" value={data.summary?.unique_ips ?? 0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" style={{ marginBottom: 0 }}>
              <Statistic title="今日访问" value={data.summary?.today_visits ?? 0} />
            </Card>
          </Col>
        </Row>
      )}

      {data?.by_day && data.by_day.length > 0 && (
        <Card title="按天统计" size="small">
          <Table
            size="small"
            pagination={false}
            dataSource={data.by_day}
            rowKey="day"
            columns={[
              { title: '日期', dataIndex: 'day', width: 120 },
              { title: '访问次数', dataIndex: 'visits', width: 100 },
              { title: '独立 IP', dataIndex: 'unique_ips', width: 100 },
            ]}
          />
        </Card>
      )}

      {data?.by_ip && data.by_ip.length > 0 && (
        <Card title="按 IP 汇总（访问次数 Top）" size="small">
          <Space wrap size="large">
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
