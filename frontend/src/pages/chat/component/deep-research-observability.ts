import type { ProgressEvent } from '@/api/deepResearch'

export type ResearchActionType =
  | 'phase'
  | 'tool'
  | 'decision'
  | 'followup'
  | 'error'
  | 'control'
  | 'progress'

export const EVENT_TYPE_LABEL: Record<string, string> = {
  'plan.started': '计划开始',
  'plan.progress': '计划处理中',
  'plan.completed': '计划完成',
  'research.started': '研究开始',
  'research.progress': '研究处理中',
  'research.completed': '研究完成',
  'research.failed': '研究失败',
  'report.started': '报告开始',
  'report.progress': '报告处理中',
  'report.completed': '报告完成',
  'report.failed': '报告失败',
  'decision.recorded': '决策记录',
  'followup.progress': '追问处理',
  'tool.started': '工具开始',
  'tool.completed': '工具完成',
  'tool.failed': '工具失败',
  'control.event': '控制事件',
}

export const ACTION_TYPE_LABEL: Record<ResearchActionType, string> = {
  phase: '阶段',
  tool: '工具',
  decision: '决策',
  followup: '追问',
  error: '异常',
  control: '控制',
  progress: '进度',
}

export function localizeProgressMessage(raw: string) {
  const text = String(raw || '').trim()
  if (!text) return '-'
  const map: Record<string, string> = {
    'Planning started': '计划开始',
    'Research started': '研究开始',
    'Research completed': '研究完成',
    'Planning completed': '计划生成完成',
    'Custom plan override applied': '已应用编辑后的计划',
    'Report generation started': '报告生成开始',
    'Draft report generated': '报告草稿已生成',
    'Outline generated': '报告大纲已生成',
    'Notes compiled': '研究笔记已汇总',
    'Citation table generated': '引用表已生成',
    'Report finalized': '报告已定稿',
    'Report quality analyzed': '报告质量分析完成',
    'Reporting completed': '报告流程完成',
    'Decision recorded': '记录了一次决策',
    'Inline follow-ups executed': '执行了追问',
    'Web search completed': '网页检索完成',
    'Paper search completed': '论文检索完成',
    'Code execution completed': '代码执行完成',
    'Compare completed': '对比分析完成',
    'Summary compressed': '摘要压缩完成',
    'Block queued for next iteration': '任务块进入下一轮迭代',
    'Block completed': '任务块完成',
    'Block failed': '任务块失败',
    'Block cancelled': '任务块已取消',
    'Web search unavailable; skipped for this run': 'Web 搜索不可用，本次已跳过',
    'Code execution unavailable; skipped for this run': '代码执行不可用，本次已跳过',
  }
  if (map[text]) return map[text]
  if (text.startsWith('Researching ')) {
    return `研究中：${text.replace('Researching ', '')}`
  }
  if (text.startsWith('Tool started:')) {
    return `工具启动：${text.replace('Tool started:', '').trim()}`
  }
  if (text.startsWith('Tool completed:')) {
    return `工具完成：${text.replace('Tool completed:', '').trim()}`
  }
  if (text.startsWith('Tool failed:')) {
    return `工具失败：${text.replace('Tool failed:', '').trim()}`
  }
  return text
}

export function summarizeProgressPayload(payload?: Record<string, unknown>) {
  if (!payload || typeof payload !== 'object') return ''
  const parts: string[] = []
  if (payload.block_title) parts.push(`任务: ${String(payload.block_title)}`)
  else if (payload.block_id) parts.push(`Block: ${String(payload.block_id)}`)
  if (payload.tool) parts.push(`工具: ${String(payload.tool)}`)
  if (payload.query) parts.push(`Query: ${String(payload.query).slice(0, 100)}`)
  if (payload.summary_preview) parts.push(`结果: ${String(payload.summary_preview).slice(0, 120)}`)
  if (typeof payload.iteration === 'number') {
    parts.push(`迭代: ${payload.iteration}/${payload.max_iterations || '?'}`)
  }
  if (typeof payload.citations === 'number') parts.push(`引用: ${payload.citations}`)
  if (typeof payload.traces === 'number') parts.push(`轨迹: ${payload.traces}`)
  if (typeof payload.quality_score === 'number') parts.push(`质量分: ${payload.quality_score}`)
  if (typeof payload.decision_rounds === 'number') parts.push(`决策轮次: ${payload.decision_rounds}`)
  if (payload.rationale_preview) parts.push(`决策理由: ${String(payload.rationale_preview).slice(0, 80)}`)
  if (typeof payload.snippets === 'number') parts.push(`代码片段: ${payload.snippets}`)
  const toolCalls = Array.isArray(payload.tool_calls)
    ? payload.tool_calls.map((value) => String(value)).filter(Boolean)
    : []
  if (toolCalls.length) {
    parts.push(`建议工具: ${toolCalls.join(', ')}`)
  }
  if (payload.error) parts.push(`错误: ${String(payload.error)}`)
  return parts.join(' · ')
}

export function resolveEventTypeLabel(event: ProgressEvent) {
  const eventType = String(event.event_type || '').trim()
  if (!eventType) return event.stage || 'progress'
  return EVENT_TYPE_LABEL[eventType] || eventType
}

export function resolveActionType(event: ProgressEvent): ResearchActionType {
  const eventType = String(event.event_type || '').trim()
  const message = String(event.message || '').toLowerCase()
  if (eventType === 'control.event') return 'control'
  if (eventType === 'decision.recorded') return 'decision'
  if (eventType.startsWith('tool.')) {
    return eventType === 'tool.failed' ? 'error' : 'tool'
  }
  if (eventType === 'followup.progress') return 'followup'
  if (
    eventType.endsWith('.failed') ||
    message.includes('failed') ||
    message.includes('error') ||
    message.includes('失败') ||
    message.includes('错误')
  ) {
    return 'error'
  }
  if (eventType.endsWith('.started') || eventType.endsWith('.completed')) return 'phase'
  return eventType.endsWith('.progress') ? 'progress' : 'progress'
}

export function resolveActionTypeColor(type: ResearchActionType) {
  if (type === 'tool') return 'blue'
  if (type === 'decision') return 'purple'
  if (type === 'followup') return 'cyan'
  if (type === 'error') return 'red'
  if (type === 'control') return 'gold'
  if (type === 'phase') return 'geekblue'
  return 'default'
}

export function resolveAgentLabel(event: ProgressEvent) {
  const stage = String(event.stage || '').toLowerCase()
  const eventType = String(event.event_type || '').toLowerCase()
  if (eventType === 'control.event') return 'Manager'
  if (stage === 'planning') return 'Planner'
  if (stage === 'researching') return 'Researcher'
  if (stage === 'reporting') return 'Reporter'
  return 'System'
}

export function resolveNextActionHint(events: ProgressEvent[]) {
  if (!events.length) return '等待下一步动作...'
  const latest = events[events.length - 1]
  const latestType = String(latest.event_type || '')
  const latestPayload = (latest.payload || {}) as Record<string, unknown>
  if (latestType === 'decision.recorded') {
    const toolCalls = Array.isArray(latestPayload.tool_calls)
      ? latestPayload.tool_calls.map((value) => String(value)).filter(Boolean)
      : []
    if (toolCalls.length) {
      return `下一步将调用：${toolCalls.join('、')}`
    }
    return '下一步将执行决策后的检索与汇总'
  }
  if (latestType === 'tool.started') return '正在等待工具返回结果'
  if (latestType === 'tool.completed') return '将基于工具结果更新笔记并继续决策'
  if (latestType === 'report.started') return '正在组织结构并撰写报告草稿'
  if (latestType === 'report.progress') return '正在完善报告内容与引用'
  if (latestType === 'report.completed') return '报告已完成，可查看与导出'
  const localized = localizeProgressMessage(latest.message || '')
  return localized === '-' ? '等待下一步动作...' : `下一步：${localized}`
}

