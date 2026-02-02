import type { DeepResearchCitation, ProgressEvent } from '@/api/deepResearch'
import Markdown from '@/components/markdown'
import { Button, Card, Collapse, List, Progress, Space, Spin, Steps, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { useMemo } from 'react'
import styles from './deep-research-card.module.scss'

const { Text, Paragraph } = Typography

const STAGE_ORDER = ['planning', 'researching', 'reporting'] as const

const STATUS_LABEL: Record<string, string> = {
  plan: '计划确认',
  queued: '排队中',
  running: '进行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const STATUS_COLOR: Record<string, string> = {
  plan: 'blue',
  queued: 'orange',
  running: 'processing',
  completed: 'green',
  failed: 'red',
  cancelled: 'default',
}

function resolveStageIndex(stage?: string) {
  if (!stage) return 0
  const index = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number])
  return index === -1 ? 0 : index
}

function formatTimestamp(value?: string) {
  if (!value) return '-'
  return dayjs(value).format('HH:mm:ss')
}

function computeEstimateMinutes(
  planCount: number,
  request: API.DeepResearchCardState['request'],
) {
  const iterations = request.max_iterations ?? 4
  const parallel = request.max_parallel ?? 1
  const toolWeight =
    (request.use_web_search ? 0.6 : 0) +
    (request.use_paper_search ? 0.8 : 0) +
    (request.use_code_exec ? 0.4 : 0)
  const base = planCount * (1.2 + toolWeight) + iterations * 1.5
  const adjusted = base / Math.max(1, parallel)
  return Math.max(3, Math.round(adjusted))
}

function extractSummary(markdown?: string) {
  if (!markdown) return ''
  const lines = markdown.split('\n').map((line) => line.trim())
  const contentLines: string[] = []
  for (const line of lines) {
    if (!line) continue
    if (line.startsWith('#') && contentLines.length) break
    contentLines.push(line.replace(/^#+\s*/, ''))
    if (contentLines.join(' ').length > 800) break
  }
  return contentLines.join('\n').trim()
}

function getLatestEvent(progress?: ProgressEvent[]) {
  if (!progress || !progress.length) return undefined
  return progress[progress.length - 1]
}

function buildCitations(citations?: DeepResearchCitation[]) {
  return (citations || []).map((item, index) => ({
    ...item,
    ref_number: item.ref_number ?? index + 1,
  }))
}

export default function DeepResearchCard(props: {
  item: API.ChatItem
  onConfirm?: (item: API.ChatItem) => void
  onCancel?: (item: API.ChatItem) => void
  onEdit?: (item: API.ChatItem) => void
  onRetryPlan?: (item: API.ChatItem) => void
  onOpenWorkspace?: (item: API.ChatItem) => void
  onExportReport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onCopyReport?: (item: API.ChatItem) => void
  onSaveToNotebook?: (item: API.ChatItem) => void
  onInsertSummary?: (item: API.ChatItem, summary: string) => void
}) {
  const {
    item,
    onConfirm,
    onCancel,
    onEdit,
    onRetryPlan,
    onOpenWorkspace,
    onExportReport,
    onCopyReport,
    onSaveToNotebook,
    onInsertSummary,
  } = props
  const data = item.deepResearch
  if (!data) return null

  const latestEvent = getLatestEvent(data.progress)
  const currentStage = data.lastStage || latestEvent?.stage
  const stageIndex = resolveStageIndex(currentStage)
  const planItems = data.plan?.items ?? []
  const estimateMinutes = useMemo(
    () => computeEstimateMinutes(planItems.length || 1, data.request),
    [planItems.length, data.request],
  )
  const citationItems = useMemo(() => buildCitations(data.citations), [data.citations])
  const reportMarkdown = data.report?.report_markdown || ''
  const summaryText = useMemo(() => extractSummary(reportMarkdown), [reportMarkdown])
  const blockStats = data.blockStats
  const toolCounts = data.toolCounts
  const toolSummary = useMemo(() => {
    if (!toolCounts) return ''
    const entries = Object.entries(toolCounts)
    if (!entries.length) return ''
    return entries
      .sort((a, b) => b[1] - a[1])
      .slice(0, 4)
      .map(([name, count]) => `${name} ${count}`)
      .join(' · ')
  }, [toolCounts])

  const toolTags = useMemo(() => {
    const tags: string[] = ['RAG']
    if (data.request.use_paper_search) tags.push('Paper')
    if (data.request.use_web_search) tags.push('Web')
    if (data.request.use_code_exec) tags.push('Code')
    return tags
  }, [data.request])

  const statusLabel = STATUS_LABEL[data.status] || data.status
  const statusColor = STATUS_COLOR[data.status] || 'default'
  const statusMessage = data.statusMessage || latestEvent?.message
  const progressPercent =
    data.status === 'completed'
      ? 100
      : currentStage === 'reporting'
      ? 90
      : currentStage === 'researching'
      ? 60
      : 25

  const recentEvents = (data.progress || []).slice(-3)

  return (
    <Card className={styles['deep-research-card']} bordered={false}>
      <div className={styles['header']}>
        <div>
          <div className={styles['title']}>🔬 深度研究</div>
          <Text type="secondary">{data.topic}</Text>
        </div>
        <Tag color={statusColor}>{statusLabel}</Tag>
      </div>

      <div className={styles['meta']}>
        <Text type="secondary">
          计划项：{planItems.length || '-'} · 预计耗时 ~{estimateMinutes} 分钟
        </Text>
        <div className={styles['meta-tools']}>
          {toolTags.map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </div>
      </div>

      {data.status === 'plan' ? (
        <div className={styles['section']}>
          {data.planLoading ? (
            <div className={styles['plan-loading']}>
              <Spin size="small" />
              <span>正在生成研究计划…</span>
            </div>
          ) : data.planError ? (
            <div className={styles['plan-error']}>
              <Text type="danger">计划生成失败：{data.planError}</Text>
            </div>
          ) : null}

          {!data.planLoading && planItems.length > 0 ? (
            <List
              size="small"
              dataSource={planItems}
              renderItem={(plan) => (
                <List.Item className={styles['plan-item']}>
                  <div className={styles['plan-item__title']}>{plan.title}</div>
                  <div className={styles['plan-item__meta']}>
                    深度 {plan.depth}
                    {plan.parent_title ? ` · 父主题：${plan.parent_title}` : ''}
                  </div>
                </List.Item>
              )}
            />
          ) : null}

          <div className={styles['actions']}>
            <Space>
              <Button
                type="primary"
                onClick={() => onConfirm?.(item)}
                disabled={data.planLoading}
              >
                确认开始
              </Button>
              <Button onClick={() => onEdit?.(item)}>修改参数</Button>
              <Button danger onClick={() => onCancel?.(item)}>
                取消
              </Button>
              {data.planError ? (
                <Button onClick={() => onRetryPlan?.(item)}>重试计划</Button>
              ) : null}
            </Space>
          </div>
        </div>
      ) : null}

      {data.status !== 'plan' ? (
        <div className={styles['section']}>
          <Steps
            current={stageIndex}
            items={[
              { title: 'Planning' },
              { title: 'Researching' },
              { title: 'Reporting' },
            ]}
            size="small"
          />
          <div className={styles['progress']}>
            <Progress percent={progressPercent} size="small" />
            <div className={styles['progress-meta']}>
              <span>当前阶段：{currentStage || '-'}</span>
              <span>{statusMessage || '-'}</span>
              {blockStats?.total ? (
                <span>
                  进度：{blockStats.completed ?? 0}/{blockStats.total}
                  {blockStats.pending ? ` · 待处理 ${blockStats.pending}` : ''}
                </span>
              ) : null}
              {blockStats?.iteration ? (
                <span>
                  迭代：{blockStats.iteration}
                  {blockStats.maxIterations ? `/${blockStats.maxIterations}` : ''}
                </span>
              ) : null}
              {toolSummary ? <span>工具调用：{toolSummary}</span> : null}
            </div>
          </div>

          {data.queuePosition !== undefined && data.queuePosition !== null ? (
            <Text type="secondary">
              排队位置：{data.queuePosition} · 运行中 {data.activeRuns ?? '-'} · 等待中{' '}
              {data.pendingRuns ?? '-'}
            </Text>
          ) : null}

          {recentEvents.length ? (
            <div className={styles['event-list']}>
              {recentEvents.map((event, idx) => (
                <div key={`${event.timestamp}-${idx}`} className={styles['event-item']}>
                  <span className={styles['event-time']}>{formatTimestamp(event.timestamp)}</span>
                  <span className={styles['event-text']}>{event.message}</span>
                </div>
              ))}
            </div>
          ) : null}

          {data.status === 'running' || data.status === 'queued' ? (
            <div className={styles['actions']}>
              <Space>
                <Button onClick={() => onOpenWorkspace?.(item)}>打开工作区</Button>
                <Button danger onClick={() => onCancel?.(item)}>
                  取消任务
                </Button>
              </Space>
            </div>
          ) : null}
        </div>
      ) : null}

      {data.status === 'completed' && reportMarkdown ? (
        <div className={styles['section']}>
          <Collapse
            defaultActiveKey={['report']}
            items={[
              {
                key: 'report',
                label: '研究报告',
                children: (
                  <div className={styles['report']}>
                    <Markdown value={reportMarkdown} />
                  </div>
                ),
              },
              {
                key: 'citations',
                label: `引用来源 (${citationItems.length})`,
                children: (
                  <List
                    size="small"
                    dataSource={citationItems}
                    renderItem={(citation) => (
                      <List.Item className={styles['citation-item']}>
                        <div className={styles['citation-index']}>
                          [{citation.ref_number}]
                        </div>
                        <div className={styles['citation-body']}>
                          <div className={styles['citation-title']}>
                            {citation.title || citation.url || citation.citation_id}
                          </div>
                          {citation.snippet ? (
                            <Paragraph className={styles['citation-snippet']} ellipsis={{ rows: 2 }}>
                              {citation.snippet}
                            </Paragraph>
                          ) : null}
                          {citation.url ? (
                            <a href={citation.url} target="_blank" rel="noreferrer">
                              查看原文
                            </a>
                          ) : null}
                        </div>
                      </List.Item>
                    )}
                  />
                ),
              },
            ]}
          />

          <div className={styles['actions']}>
            <Space>
              <Button onClick={() => onCopyReport?.(item)}>复制报告</Button>
              <Button onClick={() => onSaveToNotebook?.(item)}>保存到笔记</Button>
              <Button onClick={() => onExportReport?.(item, 'pdf')}>导出 PDF</Button>
              <Button onClick={() => onExportReport?.(item, 'markdown')}>导出 Markdown</Button>
              <Button onClick={() => onOpenWorkspace?.(item)}>打开工作区</Button>
              <Button
                type="primary"
                onClick={() => onInsertSummary?.(item, summaryText)}
                disabled={!summaryText}
              >
                回填摘要到聊天
              </Button>
            </Space>
          </div>
        </div>
      ) : null}
    </Card>
  )
}
