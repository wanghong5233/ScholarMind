import * as api from '@/api'
import type {
  KnowledgeBase as RepositoryKnowledgeBase,
  RepositoryDocument as RepositoryDoc,
  JobInfo,
  JobDetail,
} from '@/api/repository'
import IconDelete from '@/assets/repository/action/delete.svg'
import { PlusOutlined } from '@ant-design/icons'
import { Button, Modal, Popconfirm, Space, Table, Tag, Tooltip, Typography, message, Spin, Form, Input, Select } from 'antd'
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

const normalizeRagProvider = (value?: string | null): string => {
  const normalized = (value || 'multi_stage').trim().toLowerCase()
  return normalized || 'multi_stage'
}

const ragProviderMeta = {
  multi_stage: { label: '标准', color: 'default' as const, desc: '多阶段检索' },
  graph: { label: '图谱增强', color: 'geekblue' as const, desc: '文本图谱检索' },
  multimodal_graph: { label: '多模态图谱', color: 'purple' as const, desc: '图/表/公式增强' },
}

const getRagProviderMeta = (value?: string | null) => {
  const key = normalizeRagProvider(value) as keyof typeof ragProviderMeta
  return ragProviderMeta[key] ?? ragProviderMeta.multi_stage
}

const ragProviderOptions = [
  { value: 'multi_stage', label: '标准（多阶段检索）' },
  { value: 'graph', label: '图谱增强（文本）' },
  { value: 'multimodal_graph', label: '多模态图谱（图/表/公式）' },
]

export default function Index() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const user = useSnapshot(userState)
  const { data: kbList, loading: kbLoading, refresh: refreshKbList } = useRequest(async () => {
    const { data } = await api.repository.listKnowledgeBases()
    const list = (data ?? []) as RepositoryKnowledgeBase[]
    return list.filter((kb: RepositoryKnowledgeBase) => !kb.is_ephemeral)
  }, {
    ready: Boolean(user.token),
    refreshDeps: [user.token],
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
    loading: documentsLoading,
    mutate: mutateDocuments,
  } = useRequest(
    async () => {
      if (!currentKbId || !user.token) return [] as RepositoryDoc[]
      const { data } = await api.repository.listDocuments({ kbId: currentKbId })
      return data ?? []
    },
    {
      ready: Boolean(user.token && currentKbId),
      refreshDeps: [currentKbId, user.token],
    },
  )

  const documentsReloadingRef = useRef(false)
  const reloadDocuments = useCallback(async () => {
    if (!currentKbId || !user.token || documentsReloadingRef.current) return
    documentsReloadingRef.current = true
    try {
      const { data } = await api.repository.listDocuments(
        { kbId: currentKbId },
        {
          errorToast: false,
          loading: false,
          headers: {
            'Cache-Control': 'no-cache',
            Pragma: 'no-cache',
          },
          params: {
            _r: Date.now(),
          },
        },
      )
      mutateDocuments(data ?? [])
    } finally {
      documentsReloadingRef.current = false
    }
  }, [currentKbId, mutateDocuments, user.token])

  // Conditional refresh via plain setInterval. We avoid ahooks' built-in
  // pollingInterval here because dynamically toggling it from undefined to a
  // number does not always restart the polling timer, leaving documents stuck
  // at "排队中" until manual refresh.
  const hasInflightDocs = useMemo(
    () =>
      (documents ?? []).some(
        (doc) =>
          doc.processing_status === 'pending' || doc.processing_status === 'parsing',
      ),
    [documents],
  )

  useEffect(() => {
    if (!hasInflightDocs) return
    void reloadDocuments()
    const timer = setInterval(() => {
      void reloadDocuments()
    }, 3000)
    return () => clearInterval(timer)
  }, [hasInflightDocs, reloadDocuments])

  // Do not start the document request before currentKbId is restored from URL
  // + kbList. Otherwise the first request returns [] and the table briefly says
  // "暂无数据", which users read as "the knowledge base is empty".
  // Background polling uses reloadDocuments() + mutateDocuments(), so it does
  // not flip this loading flag and will not flash the table.
  const tableLoading = Boolean(currentKbId) && documentsLoading && documents === undefined
  const documentEmptyText = tableLoading ? '正在加载文档列表...' : '暂无文档'

  const currentKb = useMemo(() => {
    if (!kbList) return null
    return kbList.find((kb: RepositoryKnowledgeBase) => kb.id === currentKbId) ?? null
  }, [kbList, currentKbId])
  const currentProviderMeta = useMemo(
    () => getRagProviderMeta(currentKb?.rag_provider),
    [currentKb?.rag_provider],
  )

  type TableItem = RepositoryDoc & {
    $suffix: FileIcon
  }

  const tableData = useMemo<TableItem[]>(
    () =>
      (documents ?? []).map((item: RepositoryDoc) => ({
        ...item,
        $suffix: 'pdf' as FileIcon,
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
    kbForm.setFieldsValue({ rag_provider: 'multi_stage' })
    setKbModalOpen(true)
  }, [kbForm])

  const handleOpenEditModal = useCallback(
    (kb: RepositoryKnowledgeBase) => {
      setKbModalMode('edit')
      setEditingKb(kb)
      kbForm.setFieldsValue({
        name: kb.name,
        description: kb.description ?? '',
        rag_provider: normalizeRagProvider(kb.rag_provider),
      })
      setKbModalOpen(true)
    },
    [kbForm],
  )

  const handleCloseKbModal = useCallback(() => {
    if (kbModalLoading) return
    setKbModalOpen(false)
  }, [kbModalLoading])

  const requestParseIndex = useCallback(async (kbId: number, docIds?: number[]) => {
    try {
      const payload = docIds && docIds.length ? { doc_ids: docIds } : {}
      await api.repository.parseIndexDocuments(
        {
          kbId,
          payload,
        },
        { errorToast: false },
      )
      message.success('解析任务已创建，可在任务记录查看进度')
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      message.error(detail ? `解析任务创建失败：${detail}` : '解析任务创建失败')
    }
  }, [])

  const handleSubmitKbModal = useCallback(async () => {
    try {
      const values = await kbForm.validateFields()
      const payload = {
        ...values,
        rag_provider: normalizeRagProvider(values.rag_provider),
      }
      setKbModalLoading(true)
      if (kbModalMode === 'create') {
        const { data } = await api.repository.createKnowledgeBase(payload, { errorToast: false })
        message.success('知识库创建成功')
        setKbModalOpen(false)
        kbForm.resetFields()
        await refreshKbList()
        handleSelectKnowledgeBase(data.id)
      } else if (editingKb) {
        const previousProvider = normalizeRagProvider(editingKb.rag_provider)
        const nextProvider = normalizeRagProvider(payload.rag_provider)
        await api.repository.updateKnowledgeBase(
          {
            kbId: editingKb.id,
            payload,
          },
          { errorToast: false },
        )
        message.success('知识库更新成功')
        setKbModalOpen(false)
        await refreshKbList()
        if (currentKbId === editingKb.id) {
          handleSelectKnowledgeBase(editingKb.id)
        }
        if (previousProvider !== nextProvider) {
          Modal.confirm({
            title: '检索模式已变更',
            content: '切换检索模式后建议重新解析文档生成索引，是否立即执行？',
            okText: '立即重新解析',
            cancelText: '稍后再说',
            onOk: async () => {
              await requestParseIndex(editingKb.id)
            },
          })
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
    requestParseIndex,
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
        title: '检索模式',
        dataIndex: 'rag_provider',
        width: 140,
        render(value: string | null) {
          const meta = getRagProviderMeta(value)
          return <Tag color={meta.color}>{meta.label}</Tag>
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
        title: '状态',
        dataIndex: 'processing_status',
        width: 130,
        render(_value: unknown, row: TableItem) {
          const status = row.processing_status || 'pending'
          if (status === 'failed' && row.failure_reason) {
            const stage = row.failure_stage ? `${row.failure_stage} 阶段` : '处理过程中'
            return (
              <Tooltip
                title={
                  <div style={{ maxWidth: 320 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>{stage}失败：</div>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{row.failure_reason}</div>
                  </div>
                }
              >
                <span><Status status={status} /></span>
              </Tooltip>
            )
          }
          if (status === 'ready' && row.chunk_count > 0) {
            return (
              <Tooltip title={`已索引 ${row.chunk_count} 块`}>
                <span><Status status={status} /></span>
              </Tooltip>
            )
          }
          return <Status status={status} />
        },
      },
      {
        title: '操作',
        dataIndex: 'action',
        width: 140,
        render(_: unknown, row: TableItem) {
          return (
            <Space>
              {row.processing_status === 'failed' && row.local_pdf_path && (
                <Button
                  type="link"
                  size="small"
                  onClick={async () => {
                    if (!currentKbId) return
                    try {
                      await api.repository.retryDocument({ kbId: currentKbId, docId: row.id })
                      message.success('已重新加入解析队列')
                      await reloadDocuments()
                    } catch (e: any) {
                      message.error(e?.response?.data?.detail || '重试失败')
                    }
                  }}
                >
                  重试
                </Button>
              )}
              <Popconfirm
                title="确定要删除该文件吗？"
                onConfirm={async () => {
                  if (!currentKbId) return
                  await api.repository.remove({
                    kbId: currentKbId,
                    docId: row.id,
                  })
                  await reloadDocuments()
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
    [currentKbId, reloadDocuments, user.token],
  )
  const scroll = useMemo(() => {
    return {
      x: columns?.reduce((prev, current) => {
        return prev + parseInt(String(current.width ?? 0))
      }, 0),
    }
  }, [columns])

  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const selectedDocIds = useMemo(() => {
    return selectedRowKeys
      .map((key) => Number(key))
      .filter((value) => Number.isFinite(value))
  }, [selectedRowKeys])
  const [batchDeleting, setBatchDeleting] = useState(false)
  // 受控分页：观察到生产环境中只传 defaultPageSize 时，antd Table 的内部
  // useState init-once 仍会渲染成 10 / 页（pagination 对象每渲染都重建，
  // 但 useState init 只跑一次）。直接受控管 page + pageSize 才能让 15 稳定生效。
  const [docPage, setDocPage] = useState(1)
  const [docPageSize, setDocPageSize] = useState(15)

  useEffect(() => {
    setSelectedRowKeys([])
    setDocPage(1)
    mutateDocuments(undefined)
  }, [currentKbId, mutateDocuments])

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
  const handleReparseConfirm = useCallback(() => {
    if (!currentKbId) return
    const hasSelection = selectedDocIds.length > 0
    const scopeLabel = hasSelection
      ? `已选择的 ${selectedDocIds.length} 篇文档`
      : '当前知识库全部文档'
    Modal.confirm({
      title: '重新解析入库',
      content: `将重新解析${scopeLabel}，以刷新索引与图谱能力。是否继续？`,
      okText: '开始解析',
      cancelText: '取消',
      onOk: async () => {
        await requestParseIndex(currentKbId, hasSelection ? selectedDocIds : undefined)
      },
    })
  }, [currentKbId, selectedDocIds, requestParseIndex])

  const handleBatchDeleteConfirm = useCallback(async () => {
    if (!currentKbId || selectedDocIds.length === 0 || batchDeleting) return
    setBatchDeleting(true)
    try {
      const targets = [...selectedDocIds]
      const results = await Promise.allSettled(
        targets.map((docId) =>
          api.repository.remove(
            {
              kbId: currentKbId,
              docId,
            },
            {
              errorToast: false,
              loading: false,
            },
          ),
        ),
      )
      const failedDocIds: number[] = []
      let firstErrorDetail: string | null = null

      results.forEach((result, index) => {
        if (result.status === 'rejected') {
          failedDocIds.push(targets[index])
          if (!firstErrorDetail) {
            const reason: any = result.reason
            firstErrorDetail =
              reason?.response?.data?.detail ||
              reason?.response?.data?.message ||
              reason?.message ||
              null
          }
        }
      })

      const okCount = results.length - failedDocIds.length
      if (okCount > 0) {
        message.success(`已删除 ${okCount} 篇文档`)
      }
      if (failedDocIds.length > 0) {
        message.error(
          firstErrorDetail
            ? `有 ${failedDocIds.length} 篇删除失败：${firstErrorDetail}`
            : `有 ${failedDocIds.length} 篇删除失败`,
        )
      }

      if (okCount > 0) {
        await reloadDocuments()
      }
      setSelectedRowKeys(failedDocIds)
    } finally {
      setBatchDeleting(false)
    }
  }, [batchDeleting, currentKbId, reloadDocuments, selectedDocIds])

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

  const normalizeJobDetailRows = (raw: unknown, jobStatus?: string): JobDetail[] => {
    if (!Array.isArray(raw)) return []
    const status: JobDetail['status'] =
      (jobStatus || '').toLowerCase() === 'running' ? 'running' : 'pending'
    return raw
      .map((item): JobDetail | null => {
        if (item && typeof item === 'object') return item as JobDetail
        if (typeof item === 'number' && Number.isInteger(item) && item > 0) {
          return { doc_id: item, status }
        }
        if (typeof item === 'string') {
          const parsed = Number(item)
          if (Number.isInteger(parsed) && parsed > 0) {
            return { doc_id: parsed, status }
          }
        }
        return null
      })
      .filter((item): item is JobDetail => item !== null)
  }

  const extractJobDetails = (info?: JobInfo | null): JobDetail[] => {
    if (!info) return []
    const payload = parsePayload(info.payload)
    const payloadDetails = normalizeJobDetailRows(payload?.resultDetails, info.status)
    if (payloadDetails.length) return payloadDetails
    const infoDetails = normalizeJobDetailRows(parsePayload(info.details), info.status)
    if (infoDetails.length) return infoDetails
    const payloadDocs = normalizeJobDetailRows(payload?.docs ?? payload?.documents, info.status)
    if (payloadDocs.length) return payloadDocs
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
        title: '进度',
        dataIndex: 'succeeded',
        width: 140,
        render(_: unknown, record: JobInfo) {
          const succeeded = record.succeeded ?? 0
          const failed = record.failed ?? 0
          const total = record.total ?? succeeded + failed
          if (total <= 0) return '-'
          return (
            <Space size={6}>
              <span>
                {succeeded} / {total}
              </span>
              {failed > 0 && (
                <Tag color="error" style={{ marginInlineEnd: 0 }}>
                  失败 {failed}
                </Tag>
              )}
            </Space>
          )
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
        {currentKb ? (
          <div className={styles['meta']}>
            <Space size={8}>
              <Tag color={currentProviderMeta.color}>{currentProviderMeta.label}</Tag>
              <Typography.Text type="secondary">
                检索模式：{currentProviderMeta.desc}
              </Typography.Text>
            </Space>
          </div>
        ) : null}
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
                  <Button
                    disabled={!currentKbId || (documents?.length ?? 0) === 0}
                    onClick={handleReparseConfirm}
                  >
                    重新解析入库
                  </Button>
                  {selectedDocIds.length > 0 ? (
                    <Popconfirm
                      title="确定批量删除选中文档吗？"
                      description={`共 ${selectedDocIds.length} 篇，删除后不可恢复。`}
                      okText="确认删除"
                      cancelText="取消"
                      okButtonProps={{ loading: batchDeleting }}
                      cancelButtonProps={{ disabled: batchDeleting }}
                      onConfirm={handleBatchDeleteConfirm}
                    >
                      <Button danger loading={batchDeleting}>
                        批量删除（{selectedDocIds.length}）
                      </Button>
                    </Popconfirm>
                  ) : null}
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
              loading={tableLoading}
              locale={{ emptyText: documentEmptyText }}
              pagination={{
                current: docPage,
                pageSize: docPageSize,
                showSizeChanger: true,
                pageSizeOptions: [15, 30, 50, 100],
                showTotal: (total) => `共 ${total} 篇论文`,
                onChange: (page, size) => {
                  setDocPage(page)
                  setDocPageSize(size)
                },
              }}
            />

            <Modal
              title="上传文档"
              open={openUpload}
              width={400}
              destroyOnClose
              confirmLoading={uploading}
              cancelButtonProps={{ disabled: uploading }}
              maskClosable={!uploading}
              keyboard={!uploading}
              onCancel={() => {
                if (uploading) return
                setOpenUpload(false)
              }}
              onOk={async () => {
                if (uploading) return
                setUploading(true)
                try {
                  await uploadRef.current?.submit()
                  setOpenUpload(false)
                  await reloadDocuments()
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
                      {
                        title: '标题',
                        dataIndex: 'title',
                        ellipsis: true,
                        render(value: string | undefined, record: JobDetail) {
                          return value || record.filename || (record.doc_id ? `文档 #${record.doc_id}` : '-')
                        },
                      },
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
                          if ((record as any).status === 'running') return <Tag color="processing">处理中</Tag>
                          if ((record as any).status === 'pending') return <Tag>排队中</Tag>
                          if ((record as any).status === 'duplicate') return <Tag color="warning">重复已合并</Tag>
                          const effectiveParseStatus =
                            value || ((record as any).status === 'ok' ? 'parsed' : (record as any).status)
                          if (effectiveParseStatus === 'parsed') {
                            const chunks = (record as any).chunks
                            return (
                              <Tag color="success">
                                已入库{typeof chunks === 'number' ? `（${chunks} 块）` : ''}
                              </Tag>
                            )
                          }
                          if (effectiveParseStatus === 'failed') return <Tag color="error">失败</Tag>
                          if (effectiveParseStatus === 'not_applicable') return <Tag>未执行</Tag>
                          return effectiveParseStatus ? <Tag>{effectiveParseStatus}</Tag> : '-'
                        },
                      },
                      {
                        title: '备注',
                        dataIndex: 'note',
                        ellipsis: true,
                        render(value: string | undefined, record: JobDetail) {
                          const parseError = (record as any).parse_error
                          const error = (record as any).error
                          const status = (record as any).status
                          const processingNote =
                            status === 'running'
                              ? '解析任务正在执行，请稍后刷新。'
                              : status === 'pending'
                                ? '任务尚未开始执行。'
                                : undefined
                          const text = value || parseError || error || processingNote
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
                <Typography.Text type="secondary">暂无任务明细（任务可能仍在排队或运行，请刷新后重试）。</Typography.Text>
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
          <Form.Item
            label="检索模式"
            name="rag_provider"
            rules={[{ required: true, message: '请选择检索模式' }]}
            extra="深度模式依赖图谱与多模态解析，切换后建议重新解析文档生成索引。"
          >
            <Select options={ragProviderOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
