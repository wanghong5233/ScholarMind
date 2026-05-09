import * as api from '@/api'
import type {
  KnowledgeBase as RepositoryKnowledgeBase,
  OnlineDocumentCandidate,
  JobInfo,
  JobDetail,
} from '@/api/repository'
import { ArrowLeftOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { SortOrder, TableRowSelection } from 'antd/es/table/interface'
import { useRequest } from 'ahooks'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

type OnlineRankBy = 'hybrid' | 'relevance' | 'recent' | 'citations'

const compareMissingLast = (
  leftMissing: boolean,
  rightMissing: boolean,
  sortOrder?: SortOrder,
) => {
  if (leftMissing && rightMissing) return 0
  if (sortOrder === 'descend') {
    return leftMissing ? -1 : 1
  }
  return leftMissing ? 1 : -1
}

const compareNullableNumber = (
  left: number | null | undefined,
  right: number | null | undefined,
  sortOrder?: SortOrder,
) => {
  const leftMissing = left === null || left === undefined
  const rightMissing = right === null || right === undefined
  if (leftMissing || rightMissing) {
    return compareMissingLast(leftMissing, rightMissing, sortOrder)
  }
  return left - right
}

export default function RepositoryOnlineImport() {
  const navigate = useNavigate()
  const params = useParams<{ kbId: string }>()
  const kbId = Number(params.kbId)

  const { data: kbList, loading: kbLoading } = useRequest(async () => {
    const { data } = await api.repository.listKnowledgeBases()
    return (data ?? []) as RepositoryKnowledgeBase[]
  })

  const currentKb = useMemo(() => {
    if (!kbList || Number.isNaN(kbId)) return undefined
    return kbList.find((item) => item.id === kbId)
  }, [kbList, kbId])

  useEffect(() => {
    if (!kbLoading && !currentKb && !Number.isNaN(kbId)) {
      message.error('未找到指定知识库，已返回列表')
      navigate('/repository')
    }
  }, [kbLoading, currentKb, kbId, navigate])
  
  const handleBackToRepository = () => {
    if (kbId && !Number.isNaN(kbId)) {
      navigate(`/repository?kbId=${kbId}`)
    } else {
      navigate('/repository')
    }
  }

  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [results, setResults] = useState<OnlineDocumentCandidate[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const selectedCount = selectedRowKeys.length
  const [previewPaper, setPreviewPaper] = useState<OnlineDocumentCandidate | null>(null)
  const [resultDetails, setResultDetails] = useState<JobDetail[] | null>(null)
  const [resultJob, setResultJob] = useState<JobInfo | null>(null)

  const resolveSourceLabel = (record: OnlineDocumentCandidate) => {
    const url = String(record.source_url || '').toLowerCase()
    if (url.includes('arxiv.org')) return 'arXiv'
    if (url.includes('semanticscholar.org')) return 'Semantic Scholar'
    if (record.semantic_scholar_id) return 'Semantic Scholar'
    return '外部来源'
  }

  // Hosts that frequently block automated downloads (login wall, async 202
  // file prep, cloudflare, etc.). Even if the URL looks like a direct PDF,
  // we surface it as "可能需手动" so the UI does not over-promise.
  const FLAKY_HOSTS = [
    'figshare.com',
    'researchgate.net',
    'ssrn.com',
    'sci-hub',
    'ieeexplore.ieee.org',
    'dl.acm.org',
    'sciencedirect.com',
    'springer.com',
    'wiley.com',
  ]

  const resolveAccessHint = (record: OnlineDocumentCandidate) => {
    const url = String(record.source_url || '').toLowerCase()
    if (!url) return { label: '缺少链接', color: 'default' as const }
    if (url.includes('arxiv.org/pdf/') || url.includes('arxiv.org/abs/')) {
      return { label: '可自动下载', color: 'success' as const }
    }
    if (FLAKY_HOSTS.some((host) => url.includes(host))) {
      return { label: '可能需手动', color: 'warning' as const }
    }
    if (url.includes('semanticscholar.org/paper/')) {
      return { label: '需手动', color: 'warning' as const }
    }
    if (url.endsWith('.pdf') || url.includes('/pdf/')) {
      return { label: '尝试自动', color: 'processing' as const }
    }
    return { label: '尝试解析', color: 'processing' as const }
  }

  const currentYear = useMemo(() => new Date().getFullYear(), [])
  const defaultYearStart = useMemo(() => Math.max(1900, currentYear - 5), [currentYear])
  const yearOptions = useMemo(
    () => [
      { label: 'Any time', value: 'any' },
      { label: `Since ${currentYear}`, value: `since_${currentYear}` },
      { label: `Since ${currentYear - 1}`, value: `since_${currentYear - 1}` },
      { label: `Since ${currentYear - 4}`, value: `since_${currentYear - 4}` },
      { label: 'Custom range...', value: 'custom' },
    ],
    [currentYear],
  )

  useEffect(() => {
    form.setFieldsValue({
      query: undefined,
      limit: 10,
      yearPreset: 'any',
      providers: ['semantic_scholar'],
      rankBy: 'hybrid',
      yearStart: defaultYearStart,
      yearEnd: currentYear,
    })
  }, [form, defaultYearStart, currentYear])

  const columns: ColumnsType<OnlineDocumentCandidate> = [
    {
      title: '标题',
      dataIndex: 'title',
      width: 280,
      render(value: string, record) {
        return (
          <Space direction="vertical" size={4} style={{ maxWidth: 280 }}>
            <Typography.Text strong ellipsis>
              {value || 'N/A'}
            </Typography.Text>
            {record.authors?.length ? (
              <Typography.Text type="secondary" ellipsis>
                {record.authors.join(', ')}
              </Typography.Text>
            ) : null}
          </Space>
        )
      },
    },
    {
      title: '年份',
      dataIndex: 'publication_year',
      width: 90,
      sorter: (a, b, sortOrder) => compareNullableNumber(a.publication_year, b.publication_year, sortOrder),
      render(value: number | null | undefined) {
        return value ?? '-'
      },
    },
    {
      title: '会议/期刊',
      dataIndex: 'journal_or_conference',
      width: 180,
      ellipsis: true,
      render(value: string | null | undefined) {
        return value || '-'
      },
    },
    {
      title: '质量',
      dataIndex: 'quality_label',
      width: 130,
      sorter: (a, b, sortOrder) => {
        const leftMissing = !a.quality_label
        const rightMissing = !b.quality_label
        if (leftMissing || rightMissing) {
          return compareMissingLast(leftMissing, rightMissing, sortOrder)
        }
        return (a.quality_score || 0) - (b.quality_score || 0)
      },
      render(value: string | null | undefined, record) {
        const labels = record.quality_labels?.length
          ? record.quality_labels
          : value
            ? [{ source: record.quality_source || '', rank: record.quality_rank || '', label: value }]
            : []
        if (!labels.length) return '-'
        return (
          <Space size={[4, 4]} wrap>
            {labels.map((item) => (
              <Tag key={item.label} color={item.source === 'CCF' ? 'gold' : 'blue'}>
                {item.label}
              </Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '引用数',
      dataIndex: 'citation_count',
      width: 90,
      sorter: (a, b, sortOrder) => compareNullableNumber(a.citation_count, b.citation_count, sortOrder),
      render(value: number | null | undefined) {
        return value ?? '-'
      },
    },
    {
      title: (
        <Tooltip title="搜索阶段无法获得论文真正的关键词，这里展示来自 Semantic Scholar 的 fieldsOfStudy 或 arXiv 的学科分类，可作为领域过滤参考；论文级 keywords 会在 PDF 入库后由解析阶段补回">
          <span>领域/分类</span>
        </Tooltip>
      ),
      dataIndex: 'fields_of_study',
      ellipsis: true,
      width: 200,
      render(value: string[] | null | undefined) {
        if (!value?.length) return '-'
        return (
          <Space size={[4, 4]} wrap>
            {value.slice(0, 3).map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </Space>
        )
      },
    },
    {
      title: '摘要',
      dataIndex: 'abstract',
      ellipsis: true,
      width: 220,
      render(value: string | null | undefined, record) {
        if (!value) return '-'
        const normalized = value.replace(/\s+/g, ' ').trim()
        const preview = normalized.slice(0, 70)
        const suffix = normalized.length > 70 ? '…' : ''
        return (
          <Typography.Link onClick={() => setPreviewPaper(record)}>
            {preview}
            {suffix}
          </Typography.Link>
        )
      },
    },
    {
      title: '来源',
      dataIndex: 'source_url',
      width: 190,
      sorter: (a, b) => resolveSourceLabel(a).localeCompare(resolveSourceLabel(b)),
      render(value: string | null | undefined, record) {
        const access = resolveAccessHint(record)
        if (!value) return '-'
        return (
          <Space direction="vertical" size={2}>
            <Typography.Link href={value} target="_blank" rel="noreferrer">
              {resolveSourceLabel(record)}
            </Typography.Link>
            <Tag color={access.color}>{access.label}</Tag>
          </Space>
        )
      },
    },
  ]

  const rowSelection: TableRowSelection<OnlineDocumentCandidate> = {
    selectedRowKeys,
    onChange: (keys) => {
      setSelectedRowKeys(keys)
    },
    getCheckboxProps: (record) => {
      const key = record.semantic_scholar_id || record.doi || record.title
      return {
        value: key,
      } as any
    },
  }

  const handleSearch = async (rankByOverride?: OnlineRankBy) => {
    if (Number.isNaN(kbId)) {
      message.error('知识库不存在')
      return
    }
    try {
      const values = await form.validateFields()
      const { query, limit, yearPreset, yearStart, yearEnd } = values as {
        query: string
        limit: number
        yearPreset: string
        providers?: string[]
        rankBy?: string
        yearStart?: number
        yearEnd?: number
      }
      const rankBy = rankByOverride || values.rankBy || 'hybrid'
      setLoading(true)
      let year: string | undefined
      if (yearPreset === 'custom') {
        const startYear = Number(yearStart)
        const endYear = Number(yearEnd)
        if (!Number.isNaN(startYear) && !Number.isNaN(endYear)) {
          year = `${startYear}-${endYear}`
        }
      } else if (yearPreset.startsWith('since_')) {
        const presetYear = Number(yearPreset.replace('since_', ''))
        if (!Number.isNaN(presetYear)) {
          year = `${presetYear}-${currentYear}`
        }
      }

      const { data } = await api.repository.searchOnlineDocuments({
        kbId,
        payload: {
          query,
          limit,
          year,
          providers: values.providers?.length ? values.providers : ['semantic_scholar'],
          rank_by: rankBy,
        },
      })
      setResults(data ?? [])
      setSelectedRowKeys([])
      if (!data?.length) {
        message.info('未找到相关论文，请尝试调整关键词')
      }
    } catch (error: any) {
      if (error?.errorFields) return
      const reason =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      if (reason) {
        message.error(`检索失败：${reason}`)
      } else {
        message.error('检索失败，请稍后重试')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleImport = async () => {
    if (Number.isNaN(kbId)) return
    if (!selectedRowKeys.length) {
      message.warning('请至少选择一篇论文')
      return
    }

    const docs = results.filter((item) => {
      const key = item.semantic_scholar_id || item.doi || item.title
      return selectedRowKeys.includes(key)
    })

    if (!docs.length) {
      message.warning('所选论文无效，请重新选择')
      return
    }

    try {
      setImporting(true)
      const { data: job } = await api.repository.addOnlineDocuments({
        kbId,
        documents: docs,
      })
      const finalJob = job?.id ? await api.job.waitForJobCompletion(job.id) : null
      const details: JobDetail[] = api.job.extractJobDetails(finalJob ?? job)
      const skipped = details.filter((item) => item.status === 'skipped_pdf')
      const failed = details.filter((item) => item.status === 'failed')

      if (!finalJob) {
        message.info('已提交导入任务，请稍后在任务中心查看结果')
      } else {
        setResultDetails(details)
        setResultJob(finalJob)
        const succeeded = finalJob.succeeded || 0
        const summaryParts: string[] = []
        if (succeeded > 0) {
          summaryParts.push(`成功 ${succeeded} 篇`)
        }
        if (failed.length) {
          const titles = failed.map((item) => item.title).join('，')
          summaryParts.push(`失败 ${failed.length} 篇：${titles}`)
        }
        if (skipped.length) {
          summaryParts.push(`需手动下载 ${skipped.length} 篇`)
        }

        const summary = summaryParts.join('；') || '未获取到任务结果'

        if (failed.length) {
          message.error(`导入完成但有失败项：${summary}`)
        } else if (skipped.length) {
          message.warning(`导入完成，${skipped.length} 篇需要手动处理`)
        } else {
          message.success(`导入结果：${summary}`)
        }
      }
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message
      if (detail) {
        message.error(`提交导入失败：${detail}`)
      } else {
        message.error('提交导入任务失败，请稍后再试')
      }
    } finally {
      setImporting(false)
    }
  }

  if (Number.isNaN(kbId)) {
    return null
  }

  return (
    <div style={{ padding: 24 }}>
      <Space size={16} direction="vertical" style={{ width: '100%' }}>
        <Space align="center">
          <Button icon={<ArrowLeftOutlined />} onClick={handleBackToRepository}>
            返回知识库
          </Button>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {currentKb?.name || '知识库'}
            </Typography.Title>
            <Typography.Text type="secondary">
              {currentKb?.description || '支持在线检索并挑选要导入的论文。'}
            </Typography.Text>
          </div>
        </Space>

        <Form form={form} layout="inline" onFinish={() => handleSearch()}>
          <Form.Item
            name="query"
            rules={[{ required: true, message: '请输入检索关键词' }]}
          >
            <Input placeholder="如：GNN edge inference" allowClear style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="limit" label="数量" initialValue={10} style={{ marginInlineEnd: 12 }}>
            <InputNumber min={1} max={200} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="yearPreset" label="年份" initialValue="any" style={{ marginInlineEnd: 12 }}>
            <Select
              style={{ width: 200 }}
              options={yearOptions}
              onChange={(value: string) => {
                if (value === 'custom') {
                  form.setFieldsValue({ yearStart: defaultYearStart, yearEnd: currentYear })
                } else {
                  form.setFieldsValue({ yearStart: undefined, yearEnd: undefined })
                }
              }}
            />
          </Form.Item>
          <Form.Item
            name="providers"
            label="来源"
            initialValue={['semantic_scholar']}
            style={{ marginInlineEnd: 12 }}
          >
            <Select
              mode="multiple"
              style={{ width: 260 }}
              options={[
                { value: 'semantic_scholar', label: 'Semantic Scholar' },
                { value: 'arxiv', label: 'arXiv' },
              ]}
            />
          </Form.Item>
          <Form.Item name="rankBy" label="排序" initialValue="hybrid" style={{ marginInlineEnd: 12 }}>
            <Select
              style={{ width: 140 }}
              onChange={(value: OnlineRankBy) => {
                if (results.length) {
                  void handleSearch(value)
                }
              }}
              options={[
                { value: 'hybrid', label: '综合' },
                { value: 'relevance', label: '相关性' },
                { value: 'recent', label: '最新' },
                { value: 'citations', label: '引用数' },
              ]}
            />
          </Form.Item>
          <Form.Item shouldUpdate>
            {() =>
              form.getFieldValue('yearPreset') === 'custom' ? (
                <Space align="center" style={{ marginInlineEnd: 12 }}>
                  <Form.Item
                    name="yearStart"
                    rules={[{ required: true, message: '请输入起始年份' }]}
                    noStyle
                  >
                    <InputNumber min={1900} max={currentYear} placeholder="起始年" style={{ width: 120 }} />
                  </Form.Item>
                  <span>—</span>
                  <Form.Item
                    name="yearEnd"
                    rules={[{
                      validator: (_: unknown, value: number | undefined) => {
                        const start: number | undefined = form.getFieldValue('yearStart')
                        if (!value) return Promise.resolve()
                        if (!start) return Promise.reject(new Error('请先填写起始年份'))
                        if (value < start) return Promise.reject(new Error('截止年份需 ≥ 起始年份'))
                        if (value > currentYear) return Promise.reject(new Error(`不可超过${currentYear}`))
                        return Promise.resolve()
                      },
                    }]}
                    noStyle
                  >
                    <InputNumber min={1900} max={currentYear} placeholder="截止年" style={{ width: 120 }} />
                  </Form.Item>
                </Space>
              ) : null
            }
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading}>
              开始检索
            </Button>
          </Form.Item>
        </Form>

        <Table
          rowKey={(record) => record.semantic_scholar_id || record.doi || record.title}
          columns={columns}
          dataSource={results}
          rowSelection={rowSelection}
          loading={loading}
          pagination={{ pageSize: 10 }}
          sortDirections={['descend', 'ascend', 'descend']}
          size="middle"
        />

        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
          <Typography.Text type="secondary">
            已选择 {selectedCount} 篇（支持跨页累计选择）
          </Typography.Text>
          <Space>
            <Button onClick={handleBackToRepository}>取消</Button>
            <Button type="primary" loading={importing} onClick={handleImport} disabled={!selectedCount}>
              导入选中文献（{selectedCount}）
            </Button>
          </Space>
        </Space>

        <Typography.Text type="secondary">
          提示：导入任务需要一定时间，完成后会在页面内弹出结果汇总。
        </Typography.Text>
      </Space>

      <Modal
        title={previewPaper?.title || '摘要详情'}
        open={!!previewPaper}
        onCancel={() => setPreviewPaper(null)}
        footer={null}
        width={720}
        destroyOnClose
      >
        {previewPaper ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {previewPaper.authors?.length ? (
              <Typography.Text>
                作者：{previewPaper.authors.join('，')}
              </Typography.Text>
            ) : null}
            <Typography.Text>
              年份：{previewPaper.publication_year ?? '-'}
            </Typography.Text>
            <Typography.Text>
              会议/期刊：{previewPaper.journal_or_conference || '-'}
            </Typography.Text>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
              {previewPaper.abstract || '暂无摘要'}
            </Typography.Paragraph>
            {previewPaper.keywords?.length ? (
              <Space size={[8, 8]} wrap>
                {previewPaper.keywords.map((keyword) => (
                  <Tag key={keyword}>{keyword}</Tag>
                ))}
              </Space>
            ) : null}
            <Typography.Text type="secondary">
              引用数：{previewPaper.citation_count ?? '-'}
            </Typography.Text>
            {previewPaper.fields_of_study?.length ? (
              <Typography.Text type="secondary">
                研究领域：{previewPaper.fields_of_study.join(' / ')}
              </Typography.Text>
            ) : null}
            {previewPaper.doi ? (
              <Typography.Text type="secondary">
                DOI：{previewPaper.doi}
              </Typography.Text>
            ) : null}
            {previewPaper.source_url ? (
              <Typography.Link href={previewPaper.source_url} target="_blank" rel="noreferrer">
                查看原文
              </Typography.Link>
            ) : null}
          </Space>
        ) : null}
      </Modal>

      <Modal
        title="在线导入结果"
        open={!!resultDetails}
        onCancel={() => {
          setResultDetails(null)
          setResultJob(null)
        }}
        footer={null}
        width={820}
        destroyOnClose
      >
        {resultDetails && resultDetails.length ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type={resultDetails.some((item) => item.status === 'failed') ? 'error' : resultDetails.some((item) => item.status === 'skipped_pdf') ? 'warning' : 'success'}
              showIcon
              message={
                resultDetails.some((item) => item.status === 'skipped_pdf')
                  ? '部分论文未提供可直接抓取的 PDF'
                  : '在线导入任务已完成'
              }
              description="系统会优先使用 open-access PDF 或 arXiv PDF 直链自动导入；仅当远程来源只返回论文详情页、没有可验证 PDF 时，才标记为手动处理。"
            />
            <Space size={12} wrap>
              <Card size="small">
                <Typography.Text type="secondary">已自动导入</Typography.Text>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {resultDetails.filter((item) => item.status === 'ok').length}
                </Typography.Title>
              </Card>
              <Card size="small">
                <Typography.Text type="secondary">需手动处理</Typography.Text>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {resultDetails.filter((item) => item.status === 'skipped_pdf').length}
                </Typography.Title>
              </Card>
              <Card size="small">
                <Typography.Text type="secondary">失败</Typography.Text>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {resultDetails.filter((item) => item.status === 'failed').length}
                </Typography.Title>
              </Card>
            </Space>
            <Table
              rowKey={(item) => `${item.doc_id}-${item.status}`}
              dataSource={resultDetails}
              pagination={false}
              size="small"
              columns={[
                { title: '标题', dataIndex: 'title', ellipsis: true },
                {
                  title: '状态',
                  dataIndex: 'status',
                  width: 130,
                  render(value: string) {
                    if (value === 'ok') return <Tag color="success">自动导入</Tag>
                    if (value === 'skipped_pdf') return <Tag color="warning">需手动下载</Tag>
                    return <Tag color="error">导入失败</Tag>
                  },
                },
                {
                  title: '说明',
                  dataIndex: 'note',
                  ellipsis: true,
                  render(value: string | undefined) {
                    return value || 'PDF 已下载并进入解析入库流程'
                  },
                },
                {
                  title: '操作',
                  dataIndex: 'manual_download_url',
                  width: 120,
                  render(value: string | undefined) {
                    if (!value) return '-'
                    return (
                      <Typography.Link href={value} target="_blank" rel="noreferrer">
                        打开来源
                      </Typography.Link>
                    )
                  },
                },
              ]}
            />
          </Space>
        ) : (
          <Typography.Text type="secondary">暂无详细结果，请稍后重试或查看任务列表。</Typography.Text>
        )}
        {resultJob ? (
          <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
            任务状态：{resultJob.status}，成功 {resultJob.succeeded}，失败 {resultJob.failed}，总计 {resultJob.total}
          </Typography.Paragraph>
        ) : null}
      </Modal>
    </div>
  )
}

