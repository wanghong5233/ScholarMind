import * as api from '@/api'
import type {
  KnowledgeBase as RepositoryKnowledgeBase,
  RepositoryDocument as RepositoryDoc,
  JobInfo,
  JobDetail,
} from '@/api/repository'
import IconDelete from '@/assets/repository/action/delete.svg'
import { PlusOutlined } from '@ant-design/icons'
import { Button, Modal, Popconfirm, Space, Table, Tag, Typography, message, Spin, Form, Input } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { TableRowSelection } from 'antd/es/table/interface'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'

dayjs.extend(utc)
dayjs.extend(timezone)
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useRequest } from 'ahooks'
import { useSnapshot } from 'valtio'
import { userState } from '@/store/user'
import { FileIcon } from './components/file-icon'
import { Status } from './components/status'
import RepositoryUpload, { RepositoryUploadRef } from './components/upload'
import styles from './index.module.scss'

// 格式化 UTC 时间为本地时间
const formatUTCToLocal = (utcTime: string | undefined): string => {
  if (!utcTime) return '-'
  return dayjs.utc(utcTime).local().format('YYYY/MM/DD HH:mm:ss')
}

export default function Index() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const user = useSnapshot(userState)
  const { data: kbList, loading: kbLoading, refresh: refreshKbList } = useRequest(async () => {
    const { data } = await api.repository.listKnowledgeBases()
    const list = (data ?? []) as RepositoryKnowledgeBase[]
    return list.filter((kb: RepositoryKnowledgeBase) => !kb.is_ephemeral)
  })

  const [currentKbId, setCurrentKbId] = useState<number | null>(null)
  const [kbModalOpen, setKbModalOpen] = useState(false)
  const [kbModalMode, setKbModalMode] = useState<'create' | 'edit'>('create')
  const [kbModalLoading, setKbModalLoading] = useState(false)
  const [editingKb, setEditingKb] = useState<RepositoryKnowledgeBase | null>(null)
  const [kbForm] = Form.useForm()

  // 从 URL 参数中读取 kbId 并自动选择
  useEffect(() => {
    const kbIdParam = searchParams.get('kbId')
    if (kbIdParam && !Number.isNaN(Number(kbIdParam))) {
      const targetKbId = Number(kbIdParam)
      if (kbList && kbList.some((kb: RepositoryKnowledgeBase) => kb.id === targetKbId)) {
        setCurrentKbId(targetKbId)
        return
      }
    }

    if (currentKbId !== null) {
      setCurrentKbId(null)
    }
  }, [searchParams, kbList, currentKbId])

  useEffect(() => {
    if (!kbList || kbList.length === 0) {
      if (currentKbId !== null) {
        setCurrentKbId(null)
      }
      return
    }
    if (currentKbId && !kbList.some((kb: RepositoryKnowledgeBase) => kb.id === currentKbId)) {
      setCurrentKbId(null)
    }
  }, [kbList, currentKbId])

  const {
    data: documents,
    refresh: refreshDocuments,
    loading: documentsLoading,
  } = useRequest(
    async () => {
      if (!currentKbId) return [] as RepositoryDoc[]
      const { data } = await api.repository.listDocuments({ kbId: currentKbId })
      return data ?? []
    },
    {
      refreshDeps: [currentKbId],
    },
  )

  const currentKb = useMemo(() => {
    if (!kbList) return null
    return kbList.find((kb: RepositoryKnowledgeBase) => kb.id === currentKbId) ?? null
  }, [kbList, currentKbId])

  type TableItem = RepositoryDoc & {
    $suffix: FileIcon
    status: 'success' | 'failed' | 'unparsed' | 'cancel'
    parser_pipeline?: string | null
  }

  const tableData = useMemo<TableItem[]>(
    () =>
      (documents ?? []).map((item: RepositoryDoc) => ({
        ...item,
        $suffix: 'pdf' as FileIcon,
        status: 'success',
      })),
    [documents],
  )

  const handleSelectKnowledgeBase = useCallback(
    (kbId: number) => {
      setCurrentKbId(kbId)
      navigate(`/repository?kbId=${kbId}`)
    },
    [navigate],
  )

  const handleBackToList = useCallback(() => {
    setCurrentKbId(null)
    navigate('/repository')
  }, [navigate])

  const handleOpenCreateModal = useCallback(() => {
    setKbModalMode('create')
    setEditingKb(null)
    kbForm.resetFields()
    setKbModalOpen(true)
  }, [kbForm])

  const handleOpenEditModal = useCallback(
    (kb: RepositoryKnowledgeBase) => {
      setKbModalMode('edit')
      setEditingKb(kb)
      kbForm.setFieldsValue({
        name: kb.name,
        description: kb.description ?? '',
      })
      setKbModalOpen(true)
    },
    [kbForm],
  )

  const handleCloseKbModal = useCallback(() => {
    if (kbModalLoading) return
    setKbModalOpen(false)
  }, [kbModalLoading])

  const handleSubmitKbModal = useCallback(async () => {
    try {
      const values = await kbForm.validateFields()
      setKbModalLoading(true)
      if (kbModalMode === 'create') {
        const { data } = await api.repository.createKnowledgeBase(values, { errorToast: false })
        message.success('知识库创建成功')
        setKbModalOpen(false)
        kbForm.resetFields()
        await refreshKbList()
        handleSelectKnowledgeBase(data.id)
      } else if (editingKb) {
        await api.repository.updateKnowledgeBase(
          {
            kbId: editingKb.id,
            payload: values,
          },
          { errorToast: false },
        )
        message.success('知识库更新成功')
        setKbModalOpen(false)
        await refreshKbList()
        if (currentKbId === editingKb.id) {
          handleSelectKnowledgeBase(editingKb.id)
        }
      }
    } catch (error: any) {
      if (error?.errorFields) return
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      message.error(
        detail
          ? `${kbModalMode === 'create' ? '知识库创建失败' : '知识库更新失败'}：${detail}`
          : kbModalMode === 'create'
          ? '知识库创建失败'
          : '知识库更新失败',
      )
    } finally {
      setKbModalLoading(false)
    }
  }, [
    kbForm,
    kbModalMode,
    kbModalLoading,
    editingKb,
    refreshKbList,
    handleSelectKnowledgeBase,
    currentKbId,
  ])

  const handleDeleteKnowledgeBase = useCallback(
    async (kb: RepositoryKnowledgeBase) => {
      try {
        await api.repository.deleteKnowledgeBase(kb.id, { errorToast: false })
        message.success('知识库已删除')
        await refreshKbList()
        if (currentKbId === kb.id) {
          handleBackToList()
        }
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        message.error(detail ? `知识库删除失败：${detail}` : '知识库删除失败')
      }
    },
    [refreshKbList, currentKbId, handleBackToList],
  )

  const kbColumns: ColumnsType<RepositoryKnowledgeBase> = useMemo(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        render(value: string) {
          return value || '未命名知识库'
        },
      },
      {
        title: '简介',
        dataIndex: 'description',
        ellipsis: true,
        render(value: string | null) {
          return value || '-'
        },
      },
      {
        title: '创建时间',
        dataIndex: 'created_at',
        width: 180,
        render(value: string) {
          return formatUTCToLocal(value)
        },
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        width: 180,
        render(value: string) {
          return formatUTCToLocal(value)
        },
      },
      {
        title: '操作',
        width: 220,
        render(_: unknown, record: RepositoryKnowledgeBase) {
          return (
            <Space size={12} style={{ display: 'flex' }}>
              <Button type="link" style={{ padding: 0 }} onClick={() => handleSelectKnowledgeBase(record.id)}>
                查看文档
              </Button>
              <Button type="link" style={{ padding: 0 }} onClick={() => handleOpenEditModal(record)}>
                编辑
              </Button>
              <Popconfirm
                title="确定要删除该知识库吗？"
                description="删除后无法恢复其文档，请谨慎操作。"
                onConfirm={() => handleDeleteKnowledgeBase(record)}
              >
                <Button type="link" danger style={{ padding: 0 }}>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          )
        },
      },
    ],
    [handleSelectKnowledgeBase, handleOpenEditModal, handleDeleteKnowledgeBase],
  )

  const columns: ColumnsType<TableItem> = useMemo(
    () => [
      {
        title: '文件名',
        dataIndex: 'title',
        width: 200,
        render(value: TableItem['title'], row: TableItem) {
          const handlePreview = () => {
            if (!currentKbId || !user.token) return
            const previewUrl = api.repository.getDocumentPreviewUrl(currentKbId, row.id, user.token)
            window.open(previewUrl, '_blank')
          }
          
          return (
            <div 
              className={styles['repository-page__file-name']} 
              title={value}
              onClick={handlePreview}
              style={{ cursor: 'pointer' }}
            >
              <FileIcon className={styles['icon']} suffix={row.$suffix} />
              <span style={{ color: '#1890ff' }}>{value}</span>
            </div>
          )
        },
      },
      {
        title: '作者',
        dataIndex: 'authors',
        width: 200,
        ellipsis: true,
        render(value: TableItem['authors']) {
          if (!value || value.length === 0) return '-'
          const authorsText = value.join(', ')
          return <span title={authorsText}>{authorsText}</span>
        },
      },
      {
        title: '年份',
        dataIndex: 'publication_year',
        width: 80,
        render(value: TableItem['publication_year']) {
          return value || '-'
        },
      },
      {
        title: '期刊/会议',
        dataIndex: 'journal_or_conference',
        width: 180,
        ellipsis: true,
        render(value: TableItem['journal_or_conference']) {
          return value ? <span title={value}>{value}</span> : '-'
        },
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        width: 180,
        render(value: TableItem['updated_at']) {
          return formatUTCToLocal(value)
        },
      },
      {
        title: '来源',
        dataIndex: 'ingestion_source',
        width: 120,
        render(value: TableItem['ingestion_source']) {
          const map: Record<string, string> = {
            local_upload: '本地上传',
            online_import: '在线导入',
          }
          return map[value] || value || '未知'
        },
      },
      {
        title: '解析方案',
        dataIndex: 'parser_pipeline',
        width: 220,
        ellipsis: true,
        render(value: TableItem['parser_pipeline']) {
          if (!value) return '默认解析流水线'
          const map: Record<string, string> = {
            mineru: 'MinerU',
            unstructured: 'Unstructured',
            pymupdf: 'PyMuPDF',
          }
          const readable = value
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean)
            .map((item) => map[item] || item)
            .join(' → ')
          return readable || value
        },
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 100,
        render(value: TableItem['status']) {
          return <Status status={value} />
        },
      },
      {
        title: '操作',
        dataIndex: 'action',
        width: 100,
        render(_: unknown, row: TableItem) {
          return (
            <Space>
              <Popconfirm
                title="确定要删除该文件吗？"
                onConfirm={async () => {
                  if (!currentKbId) return
                  await api.repository.remove({
                    kbId: currentKbId,
                    docId: row.id,
                  })
                  refreshDocuments()
                }}
              >
                <Button
                  color="default"
                  variant="text"
                  shape="circle"
                  size="small"
                >
                  <img src={IconDelete} />
                </Button>
              </Popconfirm>
            </Space>
          )
        },
      },
    ],
    [currentKbId, refreshDocuments, user.token],
  )
  const scroll = useMemo(() => {
    return {
      x: columns?.reduce((prev, current) => {
        return prev + parseInt(String(current.width ?? 0))
      }, 0),
    }
  }, [columns])

  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [jobModalOpen, setJobModalOpen] = useState(false)
  const [jobLoading, setJobLoading] = useState(false)
  const [jobList, setJobList] = useState<JobInfo[]>([])
  const [jobDetailModalOpen, setJobDetailModalOpen] = useState(false)
  const [jobDetail, setJobDetail] = useState<JobInfo | null>(null)
  const [jobDetailLoading, setJobDetailLoading] = useState(false)
  const [jobPage, setJobPage] = useState(1)
  const JOB_PAGE_SIZE = 10

  const onSelectChange = (newSelectedRowKeys: React.Key[]) => {
    setSelectedRowKeys(newSelectedRowKeys)
  }
  const rowSelection: TableRowSelection<TableItem> = {
    selectedRowKeys,
    onChange: onSelectChange,
  }

  const parsePayload = (raw: unknown): any => {
    if (!raw) return null
    if (typeof raw === 'string') {
      try {
        return JSON.parse(raw)
      } catch (error) {
        return null
      }
    }
    return raw
  }

  const extractJobDetails = (info?: JobInfo | null): JobDetail[] => {
    if (!info) return []
    const payload = parsePayload(info.payload)
    const payloadDetails = payload?.resultDetails
    if (Array.isArray(payloadDetails)) return payloadDetails as JobDetail[]
    const infoDetails = parsePayload(info.details)
    if (Array.isArray(infoDetails)) return infoDetails as JobDetail[]
    return []
  }

  const fetchJobList = async () => {
    if (!currentKbId) return
    setJobLoading(true)
    try {
      const { data } = await api.job.list({ kbId: currentKbId }, { errorToast: false })
      setJobList(data ?? [])
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      message.error(detail ? `任务列表获取失败：${detail}` : '任务列表获取失败')
    } finally {
      setJobLoading(false)
    }
  }

  const handleOpenJobModal = async () => {
    if (!currentKbId) return
    setJobModalOpen(true)
    setJobPage(1)
    await fetchJobList()
  }

  const handleCloseJobModal = () => {
    setJobModalOpen(false)
    setJobDetailModalOpen(false)
    setJobDetail(null)
  }

  const handleViewJobDetail = useCallback(
    async (job: JobInfo) => {
      setJobDetailModalOpen(true)
      setJobDetail(job)
      const hasDetails = extractJobDetails(job).length > 0

      if (hasDetails) {
        setJobDetailLoading(false)
        return
      }

      setJobDetailLoading(true)
      try {
        const { data } = await api.job.detail(job.id, { errorToast: false })
        if (data) {
          setJobDetail(data)
        }
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        message.error(detail ? `获取任务详情失败：${detail}` : '获取任务详情失败')
      } finally {
        setJobDetailLoading(false)
      }
    },
    [],
  )

  const jobColumns: ColumnsType<JobInfo> = useMemo(
    () => [
      {
        title: '类型',
        dataIndex: 'type',
        render(value: string) {
          const map: Record<string, string> = {
            ingest_online: '在线导入',
            upload_local: '本地上传',
            parse_index: '解析索引',
          }
          return map[value] || value || '-'
        },
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 120,
        render(value: string) {
          const colorMap: Record<string, string> = {
            success: 'success',
            partial: 'warning',
            running: 'processing',
            pending: 'default',
            failed: 'error',
          }
          const labelMap: Record<string, string> = {
            success: '已完成',
            partial: '部分成功',
            running: '运行中',
            pending: '排队中',
            failed: '失败',
          }
          const color = colorMap[value] || 'default'
          const text = labelMap[value] || value
          return <Tag color={color}>{text}</Tag>
        },
      },
      {
        title: '成功/失败',
        dataIndex: 'succeeded',
        width: 140,
        render(_: unknown, record: JobInfo) {
          return `${record.succeeded ?? 0} / ${record.failed ?? 0}`
        },
      },
      {
        title: '创建时间',
        dataIndex: 'created_at',
        width: 180,
        render(value: string | undefined) {
          return formatUTCToLocal(value)
        },
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        width: 180,
        render(value: string | undefined) {
          return formatUTCToLocal(value)
        },
      },
      {
        title: '操作',
        width: 120,
        render(_: unknown, record: JobInfo) {
          return (
            <Space>
              <Button type="link" onClick={() => handleViewJobDetail(record)}>
                查看详情
              </Button>
            </Space>
          )
        },
      },
    ],
    [handleViewJobDetail],
  )

  const jobDetailRows = useMemo(() => extractJobDetails(jobDetail), [jobDetail])

  /* 上传 */
  const [openUpload, setOpenUpload] = useState(false)
  const uploadRef = useRef<RepositoryUploadRef>(null)
  const [uploading, setUploading] = useState(false)

  const [importChooserOpen, setImportChooserOpen] = useState(false)

  return (
    <div className={styles['repository-page']}>
      <div className={styles['repository-page__header']}>
        <div className={styles['title']}>
          {currentKb ? currentKb.name || '未命名知识库' : '知识库'}
        </div>
        <div className={styles['desc']}>
          {currentKb
            ? currentKb.description || '请在下方完成文档管理与导入。'
            : '在开始AI对话之前，请等待文档解析完成。'}
        </div>
      </div>

      <div className={styles['repository-page__body']}>
        {!currentKbId ? (
          <>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
              <Button type="primary" onClick={handleOpenCreateModal}>
                <PlusOutlined />
                新建知识库
              </Button>
            </div>
            <Table
              rowKey="id"
              columns={kbColumns}
              dataSource={kbList}
              loading={kbLoading}
              pagination={false}
            />
          </>
        ) : (
          <>
            <div className={styles['header']}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                <Button type="link" onClick={handleBackToList} style={{ padding: 0 }}>
                  返回知识库列表
                </Button>
                <Space size={16}>
                  {currentKb ? (
                    <>
                      <Button onClick={() => currentKb && handleOpenEditModal(currentKb)}>
                        编辑知识库
                      </Button>
                      <Popconfirm
                        title="确定要删除该知识库吗？"
                        description="删除后无法恢复其文档，请谨慎操作。"
                        onConfirm={() => currentKb && handleDeleteKnowledgeBase(currentKb)}
                      >
                        <Button danger>删除知识库</Button>
                      </Popconfirm>
                    </>
                  ) : null}
                  <Button
                    type="primary"
                    disabled={!currentKbId}
                    onClick={() => setImportChooserOpen(true)}
                  >
                    <PlusOutlined />
                    添加文档
                  </Button>
                  <Button onClick={handleOpenJobModal} disabled={!currentKbId}>
                    查看任务记录
                  </Button>
                </Space>
              </div>
            </div>

            <Table
              rowKey="id"
              columns={columns}
              dataSource={tableData}
              rowSelection={rowSelection}
              scroll={scroll}
              loading={documentsLoading}
              pagination={false}
            />

            <Modal
              title="上传文档"
              open={openUpload}
              width={400}
              destroyOnClose
              onCancel={() => {
                if (uploading) return
                setOpenUpload(false)
              }}
              onOk={async () => {
                setUploading(true)
                try {
                  await uploadRef.current?.submit()
                  setOpenUpload(false)
                  refreshDocuments()
                } finally {
                  setUploading(false)
                }
              }}
            >
              <RepositoryUpload
                beforeUpload={() => false}
                ref={uploadRef}
                kbId={currentKbId}
              />
            </Modal>
            <Modal
              title="选择导入方式"
              open={importChooserOpen}
              onCancel={() => setImportChooserOpen(false)}
              footer={null}
              width={360}
            >
              <Space direction="vertical" style={{ width: '100%' }}>
                <Button
                  block
                  type="primary"
                  onClick={() => {
                    setImportChooserOpen(false)
                    setOpenUpload(true)
                  }}
                >
                  本地导入
                </Button>
                <Button
                  block
                  onClick={() => {
                    setImportChooserOpen(false)
                    if (currentKbId) {
                      navigate(`/repository/${currentKbId}/online-import`)
                    } else {
                      message.warning('请先选择知识库')
                    }
                  }}
                >
                  在线导入
                </Button>
              </Space>
            </Modal>
            <Modal
              title="任务记录"
              open={jobModalOpen}
              onCancel={handleCloseJobModal}
              footer={null}
              width={900}
              destroyOnClose
            >
              <Table
                rowKey={(record) => record.id}
                columns={jobColumns}
                dataSource={jobList}
                loading={jobLoading}
                pagination={{
                  current: jobPage,
                  pageSize: JOB_PAGE_SIZE,
                  total: jobList.length,
                  showSizeChanger: false,
                  onChange(page) {
                    setJobPage(page)
                  },
                }}
                size="middle"
                style={{ marginBottom: 16 }}
              />
              <Space>
                <Button
                  onClick={async () => {
                    await fetchJobList()
                    setJobPage(1)
                  }}
                  loading={jobLoading}
                >
                  刷新
                </Button>
              </Space>
            </Modal>
            <Modal
              title="任务详情"
              open={jobDetailModalOpen}
              onCancel={() => {
                setJobDetailModalOpen(false)
                setJobDetail(null)
              }}
              footer={null}
              width={900}
              destroyOnClose
            >
              {jobDetailLoading ? (
                <div style={{ textAlign: 'center', padding: 32 }}>
                  <Spin />
                </div>
              ) : jobDetailRows.length ? (
                <>
                  <Table
                    rowKey={(record) => `${record.doc_id}-${record.title || ''}`}
                    columns={[
                      { title: '标题', dataIndex: 'title', ellipsis: true },
                      {
                        title: '下载',
                        dataIndex: 'download_status',
                        width: 120,
                        render(value: string | undefined) {
                          if (value === 'downloaded') return <Tag color="success">完成</Tag>
                          if (value === 'skipped') return <Tag color="warning">需人工</Tag>
                          if (value === 'failed') return <Tag color="error">失败</Tag>
                          return value ? <Tag>{value}</Tag> : '-'
                        },
                      },
                      {
                        title: '解析入库',
                        dataIndex: 'parse_status',
                        width: 140,
                        render(value: string | undefined, record: JobDetail) {
                          if (value === 'parsed') {
                            const chunks = (record as any).chunks
                            return (
                              <Tag color="success">
                                已入库{typeof chunks === 'number' ? `（${chunks} 块）` : ''}
                              </Tag>
                            )
                          }
                          if (value === 'failed') return <Tag color="error">失败</Tag>
                          if (value === 'not_applicable') return <Tag>未执行</Tag>
                          return value ? <Tag>{value}</Tag> : '-'
                        },
                      },
                      {
                        title: '备注',
                        dataIndex: 'note',
                        ellipsis: true,
                        render(value: string | undefined, record: JobDetail) {
                          const parseError = (record as any).parse_error
                          const text = value || parseError
                          return text || '-'
                        },
                      },
                      {
                        title: '手动下载',
                        dataIndex: 'manual_download_url',
                        width: 180,
                        render(value: string | undefined) {
                          if (!value) return '-'
                          return (
                            <Typography.Link href={value} target="_blank" rel="noreferrer">
                              打开原文
                            </Typography.Link>
                          )
                        },
                      },
                    ]}
                    dataSource={jobDetailRows as JobDetail[]}
                    pagination={false}
                    size="small"
                  />
                  <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
                    任务状态：{jobDetail?.status}，成功 {jobDetail?.succeeded ?? 0}，失败 {jobDetail?.failed ?? 0}，总计 {jobDetail?.total ?? 0}
                  </Typography.Paragraph>
                  <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
                    创建时间：{formatUTCToLocal(jobDetail?.created_at)}
                    ，更新时间：{formatUTCToLocal(jobDetail?.updated_at)}
                  </Typography.Paragraph>
                </>
              ) : (
                <Typography.Text type="secondary">暂无任务明细。</Typography.Text>
              )}
            </Modal>
          </>
        )}
      </div>

      <Modal
        title={kbModalMode === 'create' ? '新建知识库' : '编辑知识库'}
        open={kbModalOpen}
        onCancel={handleCloseKbModal}
        onOk={handleSubmitKbModal}
        confirmLoading={kbModalLoading}
        okText={kbModalMode === 'create' ? '创建' : '保存'}
        cancelText="取消"
        destroyOnClose
      >
        <Form form={kbForm} layout="vertical">
          <Form.Item
            label="名称"
            name="name"
            rules={[
              { required: true, message: '请输入知识库名称' },
              { max: 100, message: '名称长度不能超过 100 个字符' },
            ]}
          >
            <Input placeholder="请输入知识库名称" allowClear />
          </Form.Item>
          <Form.Item
            label="简介"
            name="description"
            rules={[{ max: 500, message: '简介长度不能超过 500 个字符' }]}
          >
            <Input.TextArea rows={3} placeholder="请输入知识库简介（可选）" allowClear />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
