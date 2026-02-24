import * as api from '@/api'
import { useRequest } from 'ahooks'
import {
  Alert,
  Button,
  Card,
  Col,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const { Title, Text } = Typography

function parseApiError(error: any, fallback: string): string {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail)
  }
  return error?.message || fallback
}

export default function AdminPage() {
  const navigate = useNavigate()
  const [roleFilter, setRoleFilter] = useState<string>()
  const [jobStatusFilter, setJobStatusFilter] = useState<string>()
  const [jobTypeFilter, setJobTypeFilter] = useState<string>()
  const [deepResearchStatusFilter, setDeepResearchStatusFilter] = useState<string>()
  const [updatingUserId, setUpdatingUserId] = useState<number>()
  const [actingJobId, setActingJobId] = useState<number>()
  const [actingResearchId, setActingResearchId] = useState<string>()

  const { data: meData } = useRequest(
    async () => {
      const { data } = await api.admin.getAdminMe({ errorToast: false })
      return data
    },
    { refreshDeps: [] },
  )

  const {
    data: overviewData,
    loading: overviewLoading,
    error: overviewError,
    refresh: refreshOverview,
  } = useRequest(
    async () => {
      const { data } = await api.admin.getAdminOverview({ errorToast: false })
      return data
    },
    { refreshDeps: [] },
  )

  const { data: opsData, loading: opsLoading, refresh: refreshOps } = useRequest(
    async () => {
      const { data } = await api.admin.getAdminOpsMetrics({ errorToast: false })
      return data
    },
    { refreshDeps: [] },
  )

  const {
    data: userData,
    loading: userLoading,
    refresh: refreshUsers,
  } = useRequest(
    async () => {
      const { data } = await api.admin.listAdminUsers(
        {
          page: 1,
          page_size: 20,
          role: roleFilter,
        },
        { errorToast: false },
      )
      return data
    },
    { refreshDeps: [roleFilter] },
  )

  const {
    data: jobData,
    loading: jobLoading,
    refresh: refreshJobs,
  } = useRequest(
    async () => {
      const { data } = await api.admin.listAdminJobs(
        {
          page: 1,
          page_size: 20,
          status: jobStatusFilter,
          type: jobTypeFilter,
        },
        { errorToast: false },
      )
      return data
    },
    { refreshDeps: [jobStatusFilter, jobTypeFilter] },
  )

  const {
    data: deepResearchData,
    loading: deepResearchLoading,
    refresh: refreshDeepResearch,
  } = useRequest(
    async () => {
      const { data } = await api.admin.listAdminDeepResearchRuns(
        {
          page: 1,
          page_size: 20,
          status: deepResearchStatusFilter,
        },
        { errorToast: false },
      )
      return data
    },
    { refreshDeps: [deepResearchStatusFilter] },
  )

  const {
    data: deepResearchQueueData,
    loading: deepResearchQueueLoading,
    refresh: refreshDeepResearchQueue,
  } = useRequest(
    async () => {
      const { data } = await api.admin.getAdminDeepResearchQueue({ errorToast: false })
      return data
    },
    { refreshDeps: [] },
  )

  const {
    data: auditData,
    loading: auditLoading,
    refresh: refreshAudit,
  } = useRequest(
    async () => {
      const { data } = await api.admin.listAdminAuditLogs(
        {
          page: 1,
          page_size: 30,
        },
        { errorToast: false },
      )
      return data
    },
    { refreshDeps: [] },
  )

  const refreshAll = () => {
    refreshOverview()
    refreshOps()
    refreshUsers()
    refreshJobs()
    refreshDeepResearch()
    refreshDeepResearchQueue()
    refreshAudit()
  }

  const sessionSurfaceItems = useMemo(() => {
    return Object.entries(overviewData?.breakdown?.sessions_by_surface || {}).map(([surface, count]) => ({
      key: surface,
      label: surface,
      count,
    }))
  }, [overviewData?.breakdown?.sessions_by_surface])

  const jobStatusItems = useMemo(() => {
    return Object.entries(overviewData?.breakdown?.jobs_by_status || {}).map(([status, count]) => ({
      key: status,
      label: status,
      count,
    }))
  }, [overviewData?.breakdown?.jobs_by_status])

  const userColumns = useMemo<ColumnsType<api.admin.AdminUserItem>>(
    () => [
      { title: 'ID', dataIndex: 'id', width: 70 },
      { title: '用户名', dataIndex: 'username', width: 160 },
      {
        title: '角色',
        dataIndex: 'role',
        width: 130,
        render: (role: string) => {
          const color = role === 'super_admin' ? 'red' : role === 'admin' ? 'green' : 'default'
          return <Tag color={color}>{role}</Tag>
        },
      },
      {
        title: '状态',
        dataIndex: 'is_active',
        width: 100,
        render: (isActive: boolean) =>
          isActive ? <Tag color="green">active</Tag> : <Tag color="red">disabled</Tag>,
      },
      {
        title: '操作',
        key: 'actions',
        width: 360,
        render: (_, record) => (
          <Space wrap>
            <Button
              size="small"
              loading={updatingUserId === record.id}
              disabled={record.role === 'user'}
              onClick={async () => {
                try {
                  setUpdatingUserId(record.id)
                  await api.admin.updateAdminUserRole(record.id, { role: 'user' }, { errorToast: false })
                  message.success(`用户 ${record.username} 已设为 user`)
                  refreshUsers()
                  refreshAudit()
                } catch (error: any) {
                  message.error(parseApiError(error, '更新失败'))
                } finally {
                  setUpdatingUserId(undefined)
                }
              }}
            >
              设为 user
            </Button>
            <Button
              size="small"
              loading={updatingUserId === record.id}
              disabled={record.role === 'admin'}
              onClick={async () => {
                try {
                  setUpdatingUserId(record.id)
                  await api.admin.updateAdminUserRole(record.id, { role: 'admin' }, { errorToast: false })
                  message.success(`用户 ${record.username} 已设为 admin`)
                  refreshUsers()
                  refreshAudit()
                } catch (error: any) {
                  message.error(parseApiError(error, '更新失败'))
                } finally {
                  setUpdatingUserId(undefined)
                }
              }}
            >
              设为 admin
            </Button>
            {record.is_active ? (
              <Popconfirm
                title="确认封禁该用户？"
                onConfirm={async () => {
                  try {
                    setUpdatingUserId(record.id)
                    await api.admin.updateAdminUserStatus(
                      record.id,
                      { is_active: false, reason: 'Disabled from admin console' },
                      { errorToast: false },
                    )
                    message.success(`用户 ${record.username} 已封禁`)
                    refreshUsers()
                    refreshAudit()
                  } catch (error: any) {
                    message.error(parseApiError(error, '封禁失败'))
                  } finally {
                    setUpdatingUserId(undefined)
                  }
                }}
                okButtonProps={{ loading: updatingUserId === record.id }}
              >
                <Button size="small" danger disabled={updatingUserId === record.id}>
                  封禁
                </Button>
              </Popconfirm>
            ) : (
              <Button
                size="small"
                type="primary"
                loading={updatingUserId === record.id}
                onClick={async () => {
                  try {
                    setUpdatingUserId(record.id)
                    await api.admin.updateAdminUserStatus(
                      record.id,
                      { is_active: true, reason: 'Enabled from admin console' },
                      { errorToast: false },
                    )
                    message.success(`用户 ${record.username} 已解封`)
                    refreshUsers()
                    refreshAudit()
                  } catch (error: any) {
                    message.error(parseApiError(error, '解封失败'))
                  } finally {
                    setUpdatingUserId(undefined)
                  }
                }}
              >
                解封
              </Button>
            )}
          </Space>
        ),
      },
    ],
    [refreshAudit, refreshUsers, updatingUserId],
  )

  const jobColumns = useMemo<ColumnsType<api.admin.AdminJobItem>>(
    () => [
      { title: '任务ID', dataIndex: 'id', width: 90 },
      { title: '用户ID', dataIndex: 'user_id', width: 90 },
      { title: '知识库ID', dataIndex: 'knowledge_base_id', width: 100 },
      { title: '类型', dataIndex: 'type', width: 130 },
      {
        title: '状态',
        dataIndex: 'status',
        width: 120,
        render: (status: string) => {
          const colorMap: Record<string, string> = {
            success: 'green',
            failed: 'red',
            partial: 'orange',
            running: 'blue',
            pending: 'default',
            cancelled: 'purple',
          }
          return <Tag color={colorMap[status] || 'default'}>{status}</Tag>
        },
      },
      {
        title: '进度',
        key: 'progress_text',
        width: 120,
        render: (_, record) => `${record.progress ?? 0}% (${record.succeeded ?? 0}/${record.total ?? 0})`,
      },
      {
        title: '操作',
        key: 'actions',
        width: 200,
        render: (_, record) => (
          <Space>
            <Popconfirm
              title="确认取消该任务？"
              onConfirm={async () => {
                try {
                  setActingJobId(record.id)
                  await api.admin.cancelAdminJob(
                    record.id,
                    { reason: 'Cancelled from admin console' },
                    { errorToast: false },
                  )
                  message.success(`任务 ${record.id} 已取消`)
                  refreshJobs()
                  refreshAudit()
                  refreshOverview()
                  refreshOps()
                } catch (error: any) {
                  message.error(parseApiError(error, '取消失败'))
                } finally {
                  setActingJobId(undefined)
                }
              }}
              okButtonProps={{ loading: actingJobId === record.id }}
            >
              <Button size="small" disabled={actingJobId === record.id}>
                取消
              </Button>
            </Popconfirm>
            <Button
              size="small"
              type="primary"
              loading={actingJobId === record.id}
              onClick={async () => {
                try {
                  setActingJobId(record.id)
                  await api.admin.retryAdminJob(record.id, { errorToast: false })
                  message.success(`任务 ${record.id} 已发起重试`)
                  refreshJobs()
                  refreshAudit()
                  refreshOverview()
                  refreshOps()
                } catch (error: any) {
                  message.error(parseApiError(error, '重试失败'))
                } finally {
                  setActingJobId(undefined)
                }
              }}
            >
              重试
            </Button>
          </Space>
        ),
      },
    ],
    [actingJobId, refreshAudit, refreshJobs, refreshOps, refreshOverview],
  )

  const deepResearchColumns = useMemo<ColumnsType<api.admin.AdminDeepResearchRunItem>>(
    () => [
      { title: 'ResearchID', dataIndex: 'research_id', width: 220 },
      { title: '用户ID', dataIndex: 'user_id', width: 90 },
      { title: '主题', dataIndex: 'topic', width: 240, ellipsis: true },
      {
        title: '状态',
        dataIndex: 'status',
        width: 120,
        render: (status: string) => {
          const colorMap: Record<string, string> = {
            completed: 'green',
            failed: 'red',
            running: 'blue',
            queued: 'gold',
            cancelled: 'purple',
          }
          return <Tag color={colorMap[status] || 'default'}>{status}</Tag>
        },
      },
      {
        title: '提交时间',
        dataIndex: 'submitted_at',
        width: 180,
        render: (value: string | undefined) => value || '-',
      },
      {
        title: '操作',
        key: 'actions',
        width: 220,
        render: (_, record) => (
          <Space>
            <Popconfirm
              title="确认取消该 DeepResearch 任务？"
              onConfirm={async () => {
                try {
                  setActingResearchId(record.research_id)
                  await api.admin.cancelAdminDeepResearchRun(
                    record.research_id,
                    { reason: 'Cancelled from admin console' },
                    { errorToast: false },
                  )
                  message.success(`任务 ${record.research_id} 已取消`)
                  refreshDeepResearch()
                  refreshDeepResearchQueue()
                  refreshAudit()
                  refreshOps()
                } catch (error: any) {
                  message.error(parseApiError(error, '取消失败'))
                } finally {
                  setActingResearchId(undefined)
                }
              }}
              okButtonProps={{ loading: actingResearchId === record.research_id }}
            >
              <Button size="small" disabled={actingResearchId === record.research_id}>
                取消
              </Button>
            </Popconfirm>
            <Button
              size="small"
              type="primary"
              loading={actingResearchId === record.research_id}
              onClick={async () => {
                try {
                  setActingResearchId(record.research_id)
                  await api.admin.retryAdminDeepResearchRun(record.research_id, {
                    errorToast: false,
                  })
                  message.success(`任务 ${record.research_id} 已发起重试`)
                  refreshDeepResearch()
                  refreshDeepResearchQueue()
                  refreshAudit()
                  refreshOps()
                } catch (error: any) {
                  message.error(parseApiError(error, '重试失败'))
                } finally {
                  setActingResearchId(undefined)
                }
              }}
            >
              重试
            </Button>
          </Space>
        ),
      },
    ],
    [actingResearchId, refreshAudit, refreshDeepResearch, refreshDeepResearchQueue, refreshOps],
  )

  const auditColumns = useMemo<ColumnsType<api.admin.AdminAuditLogItem>>(
    () => [
      { title: 'ID', dataIndex: 'id', width: 90 },
      { title: '管理员', dataIndex: 'admin_username', width: 140 },
      { title: '动作', dataIndex: 'action', width: 180 },
      { title: '目标', dataIndex: 'target_type', width: 120 },
      { title: '目标ID', dataIndex: 'target_id', width: 140 },
      {
        title: '时间',
        dataIndex: 'created_at',
        width: 200,
        render: (value: string | undefined | null) => value || '-',
      },
    ],
    [],
  )

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space align="center" style={{ justifyContent: 'space-between', width: '100%' }}>
            <Title level={4} style={{ margin: 0 }}>
              管理后台（MVP）
            </Title>
            <Button onClick={refreshAll} disabled={overviewLoading || opsLoading}>
              刷新
            </Button>
          </Space>
          <Text type="secondary">
            当前登录：{meData?.username || '-'} · 角色：{meData?.role || '-'} · 权限：
            {meData?.is_admin ? <Tag color="green">Admin</Tag> : <Tag color="default">User</Tag>}
          </Text>
        </Space>
      </Card>

      {overviewError ? (
        <Alert
          type="error"
          showIcon
          message="后台数据加载失败"
          description="请确认当前账号在管理员白名单中，并检查后端服务状态。"
        />
      ) : null}

      <Spin spinning={overviewLoading}>
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="用户总数" value={overviewData?.metrics?.users || 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="知识库总数" value={overviewData?.metrics?.knowledge_bases || 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="文档总数" value={overviewData?.metrics?.documents || 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="会话总数" value={overviewData?.metrics?.sessions || 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="任务总数" value={overviewData?.metrics?.jobs || 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} md={8} lg={6}>
            <Card>
              <Statistic title="服务运行秒数" value={overviewData?.uptime_secs || 0} />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginTop: 8 }}>
          <Col xs={24} md={12}>
            <Card title="会话分布（Surface）">
              <List
                dataSource={sessionSurfaceItems}
                locale={{ emptyText: '暂无数据' }}
                renderItem={(item) => (
                  <List.Item>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <span>{item.label}</span>
                      <Tag color="blue">{item.count}</Tag>
                    </Space>
                  </List.Item>
                )}
              />
            </Card>
          </Col>
          <Col xs={24} md={12}>
            <Card title="任务状态分布">
              <List
                dataSource={jobStatusItems}
                locale={{ emptyText: '暂无数据' }}
                renderItem={(item) => (
                  <List.Item>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <span>{item.label}</span>
                      <Tag color="geekblue">{item.count}</Tag>
                    </Space>
                  </List.Item>
                )}
              />
            </Card>
          </Col>
        </Row>

        <Card title="运维指标（MVP）" style={{ marginTop: 16 }}>
          <Spin spinning={opsLoading}>
            <Row gutter={[16, 16]}>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic title="QPS(1m)" value={opsData?.runtime?.qps_1m || 0} precision={3} />
              </Col>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic
                  title="5xx 错误率"
                  value={(opsData?.runtime?.error_rate_5xx || 0) * 100}
                  precision={2}
                  suffix="%"
                />
              </Col>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic
                  title="平均延迟(ms)"
                  value={opsData?.runtime?.avg_latency_ms || 0}
                  precision={2}
                />
              </Col>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic title="作业积压" value={opsData?.jobs?.queue_backlog || 0} />
              </Col>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic title="DR 队列等待" value={opsData?.deep_research?.queue?.pending_runs || 0} />
              </Col>
              <Col xs={24} sm={12} md={8} lg={6}>
                <Statistic
                  title="DR Token 成本(USD)"
                  value={opsData?.deep_research?.token_usage?.estimated_cost_usd || 0}
                  precision={4}
                />
              </Col>
            </Row>
          </Spin>
        </Card>

        <Card title="管理入口（MVP）" style={{ marginTop: 16 }}>
          <Space wrap>
            <Button type="primary" onClick={() => navigate('/admin/debug/parse')}>
              解析调试
            </Button>
            <Button type="primary" onClick={() => navigate('/admin/debug/retrieval')}>
              检索调试
            </Button>
          </Space>
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">
              Phase2 预留模块：{(overviewData?.phase2_reserved_modules || []).join(', ') || '-'}
            </Text>
          </div>
        </Card>
      </Spin>

      <Card
        title="用户管理（MVP）"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="按角色筛选"
              style={{ width: 180 }}
              value={roleFilter}
              onChange={(value) => setRoleFilter(value)}
              options={[
                { label: 'user', value: 'user' },
                { label: 'admin', value: 'admin' },
                { label: 'super_admin', value: 'super_admin' },
              ]}
            />
            <Button onClick={refreshUsers} disabled={userLoading}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          loading={userLoading}
          columns={userColumns}
          dataSource={userData?.items || []}
          pagination={false}
          size="small"
          scroll={{ x: 1000 }}
        />
      </Card>

      <Card
        title="任务运维（知识库作业）"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 140 }}
              value={jobStatusFilter}
              onChange={(value) => setJobStatusFilter(value)}
              options={[
                { label: 'pending', value: 'pending' },
                { label: 'running', value: 'running' },
                { label: 'partial', value: 'partial' },
                { label: 'success', value: 'success' },
                { label: 'failed', value: 'failed' },
                { label: 'cancelled', value: 'cancelled' },
              ]}
            />
            <Select
              allowClear
              placeholder="类型"
              style={{ width: 160 }}
              value={jobTypeFilter}
              onChange={(value) => setJobTypeFilter(value)}
              options={[
                { label: 'upload_local', value: 'upload_local' },
                { label: 'ingest_online', value: 'ingest_online' },
                { label: 'parse_index', value: 'parse_index' },
              ]}
            />
            <Button onClick={refreshJobs} disabled={jobLoading}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          loading={jobLoading}
          columns={jobColumns}
          dataSource={jobData?.items || []}
          pagination={false}
          size="small"
          scroll={{ x: 900 }}
        />
      </Card>

      <Card
        title="任务运维（DeepResearch）"
        extra={
          <Space>
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 140 }}
              value={deepResearchStatusFilter}
              onChange={(value) => setDeepResearchStatusFilter(value)}
              options={[
                { label: 'queued', value: 'queued' },
                { label: 'running', value: 'running' },
                { label: 'completed', value: 'completed' },
                { label: 'failed', value: 'failed' },
                { label: 'cancelled', value: 'cancelled' },
              ]}
            />
            <Button onClick={refreshDeepResearch} disabled={deepResearchLoading}>
              刷新列表
            </Button>
            <Button onClick={refreshDeepResearchQueue} disabled={deepResearchQueueLoading}>
              刷新队列
            </Button>
          </Space>
        }
      >
        <div style={{ marginBottom: 12 }}>
          <Text type="secondary">
            队列状态：运行中 {deepResearchQueueData?.active_runs || 0} / 待执行{' '}
            {deepResearchQueueData?.pending_runs || 0} / 最大并发{' '}
            {deepResearchQueueData?.max_active_runs || 0}
          </Text>
        </div>
        <Table
          rowKey="research_id"
          loading={deepResearchLoading}
          columns={deepResearchColumns}
          dataSource={deepResearchData?.items || []}
          pagination={false}
          size="small"
          scroll={{ x: 1200 }}
        />
      </Card>

      <Card
        title="审计日志（MVP）"
        extra={
          <Button onClick={refreshAudit} disabled={auditLoading}>
            刷新
          </Button>
        }
      >
        <Table
          rowKey="id"
          loading={auditLoading}
          columns={auditColumns}
          dataSource={auditData?.items || []}
          pagination={false}
          size="small"
          scroll={{ x: 900 }}
        />
      </Card>
    </div>
  )
}
