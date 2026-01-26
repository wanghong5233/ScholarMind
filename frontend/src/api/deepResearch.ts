import { AxiosRequestConfig } from 'axios'
import { request } from './request'

const DEEP_RESEARCH_BASE =
  (import.meta.env.VITE_DEEP_RESEARCH_BASE as string | undefined) ||
  (import.meta.env.DEV ? 'http://127.0.0.1:8004/api' : '/api/deep-research')
const DEFAULT_DEEP_RESEARCH_USER_ID =
  (import.meta.env.VITE_DEEP_RESEARCH_DEFAULT_USER_ID as string | undefined) || '1'

function withDeepResearchConfig(config?: AxiosRequestConfig): AxiosRequestConfig {
  return {
    baseURL: DEEP_RESEARCH_BASE,
    ...config,
    headers: {
      'X-User-Id': DEFAULT_DEEP_RESEARCH_USER_ID,
      ...(config?.headers ?? {}),
    },
  }
}

export type DeepResearchMode = 'queue' | 'tree'
export type DeepResearchStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface DeepResearchRequest {
  topic: string
  mode?: DeepResearchMode
  depth?: number
  breadth?: number
  max_parallel?: number
  max_iterations?: number
  use_web_search?: boolean
  use_code_exec?: boolean
  code_exec_snippets?: string[]
  top_k?: number
  index_mode?: string
  session_id?: string
  language?: string
  report_style?: string
  metadata?: Record<string, any>
}

export interface IdeaGenerationRequest {
  topic: string
  idea_count?: number
  session_id?: string
  language?: string
  constraints?: string[]
  top_k?: number
  index_mode?: string
  metadata?: Record<string, any>
}

export type CoWriterTask = 'rewrite' | 'expand' | 'shorten' | 'annotate'

export interface CoWriterRequest {
  task: CoWriterTask
  text: string
  session_id?: string
  language?: string
  instructions?: string
  tone?: string
  top_k?: number
  index_mode?: string
  metadata?: Record<string, any>
}

export interface DeepResearchCitation {
  citation_id: string
  ref_number?: number
  title?: string
  url?: string
  snippet?: string
  source_type?: string
  metadata?: Record<string, any>
}

export interface PlanItem {
  title: string
  question: string
  depth: number
  parent_title?: string | null
}

export interface DeepResearchPlan {
  items: PlanItem[]
}

export interface ToolTrace {
  tool_id: string
  citation_id: string
  tool_type: string
  query: string
  raw_answer: string
  summary: string
  timestamp: string
  raw_answer_truncated?: boolean
  raw_answer_original_size?: number
}

export interface DeepResearchRunSummary {
  blocks_total: number
  blocks_by_status: Record<string, number>
  citations_total: number
  tool_traces_total: number
  tool_traces_by_type: Record<string, number>
  decisions_total: number
  errors: Array<{
    block_id: string
    tool_id: string
    tool_type: string
    summary: string
    timestamp?: string
  }>
  generated_at?: string
}

export interface DeepResearchRunMeta {
  research_id: string
  status: DeepResearchStatus | string
  topic: string
  mode: DeepResearchMode
  priority?: number
  submitted_at?: string
  started_at?: string
  finished_at?: string
  resumed_at?: string
  resume_count?: number
  resume_requested_at?: string
  resume_pending?: boolean
  cancel_requested_at?: string
  last_progress_at?: string
  cancel_reason?: string
  duration_seconds?: number
  user_id?: number
  summary?: DeepResearchRunSummary
  error?: string
  request?: Record<string, any>
}

export interface DeepResearchRunList {
  items: DeepResearchRunMeta[]
}

export interface DeepResearchArchive {
  research_id: string
  meta: DeepResearchRunMeta
  snapshot: DeepResearchSnapshot
  progress: ProgressEvent[]
  summary?: DeepResearchRunSummary
}

export interface DeepResearchBlockEvidence {
  research_id: string
  block_id: string
  block: TopicBlock
  notes: string[]
  citations: string[]
  citation_details: DeepResearchCitation[]
  tool_traces: ToolTrace[]
  decisions: Record<string, any>[]
  progress_events: ProgressEvent[]
}

export interface TopicBlock {
  block_id: string
  title: string
  question: string
  status: string
  depth: number
  parent_id?: string | null
  created_at: string
  updated_at: string
  iterations: number
  max_iterations: number
  followups_generated: boolean
  notes: string[]
  citations: string[]
  tool_traces: ToolTrace[]
  decisions?: Record<string, any>[]
  child_ids: string[]
}

export interface DeepResearchTrace {
  mode?: string
  queue?: {
    research_id: string
    max_length?: number | null
    block_counter?: number
    blocks?: TopicBlock[]
  }
  summary?: DeepResearchRunSummary
  plan?: DeepResearchPlan
  report_details?: DeepResearchReportDetails
  [key: string]: any
}

export interface DeepResearchReportDetails {
  outline?: string[]
  outline_detailed?: string[]
  notes?: string[]
  citation_table?: string[]
  draft_markdown?: string
  quality?: {
    paragraphs_total?: number
    paragraphs_with_citations?: number
    paragraphs_without_citations?: number
    citation_paragraph_coverage?: number | null
    citations_mentions?: number
    citations_distinct_count?: number
    citations_distinct?: number[]
    uncited_examples?: string[]
    sections?: Array<{
      title: string
      paragraphs_total?: number
      paragraphs_with_citations?: number
      citation_paragraph_coverage?: number | null
      citations_mentions?: number
    }>
    sections_without_citations?: string[]
  }
}

export interface DeepResearchReportPayload {
  research_id?: string
  status?: string
  report_markdown?: string
  outline?: string[]
  notes?: string[]
  citation_table?: string[]
  draft_markdown?: string
  summary?: DeepResearchRunSummary
  trace?: DeepResearchTrace
  report_details?: DeepResearchReportDetails
}

export interface DeepResearchResponse {
  research_id: string
  status: string
  report_markdown: string
  citations: DeepResearchCitation[]
  trace: DeepResearchTrace
}

export interface DeepResearchSubmitResponse {
  research_id: string
  status: DeepResearchStatus | string
  message?: string
  queue_position?: number
  active_runs?: number
  pending_runs?: number
}

export interface DeepResearchPriorityUpdateRequest {
  priority: number
}

export interface DeepResearchQueueItem {
  research_id: string
  topic: string
  status: DeepResearchStatus | string
  priority?: number
  effective_priority?: number
  wait_seconds?: number
  submitted_at?: string
  started_at?: string
  user_id?: number
}

export interface DeepResearchQueueStatus {
  active_runs: number
  pending_runs: number
  max_active_runs: number
  active_items: DeepResearchQueueItem[]
  pending_items: DeepResearchQueueItem[]
}

export type IdeaGenerationStatus = 'running' | 'completed' | 'failed'

export interface IdeaGenerationResponse {
  idea_id: string
  ideas_markdown: string
  citations: DeepResearchCitation[]
  trace: Record<string, any>
}

export interface IdeaGenerationRunMeta {
  idea_id: string
  status: IdeaGenerationStatus | string
  topic: string
  started_at?: string
  finished_at?: string
  duration_seconds?: number
  user_id?: number
  error?: string
  request?: Record<string, any>
}

export interface IdeaGenerationRunList {
  items: IdeaGenerationRunMeta[]
}

export interface IdeaGenerationRunDetail {
  meta: IdeaGenerationRunMeta
  payload: IdeaGenerationResponse
}

export type CoWriterStatus = 'running' | 'completed' | 'failed'

export interface CoWriterResponse {
  operation_id: string
  result_markdown: string
  citations: DeepResearchCitation[]
  trace: Record<string, any>
}

export interface CoWriterRunMeta {
  operation_id: string
  status: CoWriterStatus | string
  task: CoWriterTask
  started_at?: string
  finished_at?: string
  duration_seconds?: number
  user_id?: number
  error?: string
  request?: Record<string, any>
}

export interface CoWriterRunList {
  items: CoWriterRunMeta[]
}

export interface CoWriterRunDetail {
  meta: CoWriterRunMeta
  payload: CoWriterResponse
}

export interface ProgressEvent {
  research_id: string
  stage: string
  message: string
  timestamp?: string
  payload?: Record<string, any>
}

export interface DeepResearchProgress {
  research_id: string
  items: ProgressEvent[]
  next_offset?: number
}

export interface DeepResearchSnapshot {
  research_id: string
  outline?: DeepResearchPlan
  queue?: DeepResearchTrace['queue']
  citations?: Record<string, any>
  report?: DeepResearchReportPayload
}

export function runDeepResearch(
  payload: DeepResearchRequest,
  options?: AxiosRequestConfig,
) {
  return request.post<DeepResearchResponse>(
    '/deep-research',
    payload,
    withDeepResearchConfig(options),
  )
}

export function submitDeepResearch(
  payload: DeepResearchRequest,
  options?: AxiosRequestConfig,
) {
  return request.post<DeepResearchSubmitResponse>(
    '/deep-research/submit',
    payload,
    withDeepResearchConfig(options),
  )
}

export function replayDeepResearch(researchId: string, options?: AxiosRequestConfig) {
  return request.post<DeepResearchSubmitResponse>(
    `/deep-research/${encodeURIComponent(researchId)}/replay`,
    undefined,
    withDeepResearchConfig(options),
  )
}

export function cancelDeepResearch(researchId: string, options?: AxiosRequestConfig) {
  return request.post<DeepResearchSubmitResponse>(
    `/deep-research/${encodeURIComponent(researchId)}/cancel`,
    undefined,
    withDeepResearchConfig(options),
  )
}

export function resumeDeepResearch(researchId: string, options?: AxiosRequestConfig) {
  return request.post<DeepResearchSubmitResponse>(
    `/deep-research/${encodeURIComponent(researchId)}/resume`,
    undefined,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchProgress(researchId: string, options?: AxiosRequestConfig) {
  return request.get<DeepResearchProgress>(
    `/deep-research/${researchId}/progress`,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchProgressSince(
  researchId: string,
  offset: number,
  limit?: number,
  options?: AxiosRequestConfig,
) {
  return request.get<DeepResearchProgress>(
    `/deep-research/${encodeURIComponent(researchId)}/progress/since`,
    withDeepResearchConfig({
      ...options,
      params: {
        ...(options?.params ?? {}),
        offset,
        limit,
      },
    }),
  )
}

export function getDeepResearchSnapshot(researchId: string, options?: AxiosRequestConfig) {
  return request.get<DeepResearchSnapshot>(
    `/deep-research/${researchId}/snapshot`,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchArchive(researchId: string, options?: AxiosRequestConfig) {
  return request.get<DeepResearchArchive>(
    `/deep-research/${researchId}/archive`,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchBlockEvidence(
  researchId: string,
  blockId: string,
  options?: AxiosRequestConfig,
) {
  return request.get<DeepResearchBlockEvidence>(
    `/deep-research/${researchId}/blocks/${encodeURIComponent(blockId)}/evidence`,
    withDeepResearchConfig(options),
  )
}

export function runIdeaGeneration(payload: IdeaGenerationRequest, options?: AxiosRequestConfig) {
  return request.post<IdeaGenerationResponse>(
    '/idea-generation',
    payload,
    withDeepResearchConfig(options),
  )
}

export function listIdeaGenerationRuns(options?: AxiosRequestConfig) {
  return request.get<IdeaGenerationRunList>(
    '/idea-generation/runs',
    withDeepResearchConfig(options),
  )
}

export function getIdeaGenerationRun(ideaId: string, options?: AxiosRequestConfig) {
  return request.get<IdeaGenerationRunDetail>(
    `/idea-generation/${encodeURIComponent(ideaId)}`,
    withDeepResearchConfig(options),
  )
}

export function runCoWriter(payload: CoWriterRequest, options?: AxiosRequestConfig) {
  return request.post<CoWriterResponse>('/co-writer', payload, withDeepResearchConfig(options))
}

export function listCoWriterRuns(options?: AxiosRequestConfig) {
  return request.get<CoWriterRunList>('/co-writer/runs', withDeepResearchConfig(options))
}

export function getCoWriterRun(operationId: string, options?: AxiosRequestConfig) {
  return request.get<CoWriterRunDetail>(
    `/co-writer/${encodeURIComponent(operationId)}`,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchProgressStreamUrl(researchId: string, userId?: string) {
  const base = DEEP_RESEARCH_BASE.replace(/\/$/, '')
  const uid = encodeURIComponent(userId || DEFAULT_DEEP_RESEARCH_USER_ID)
  return `${base}/deep-research/${encodeURIComponent(researchId)}/progress/stream?user_id=${uid}`
}

export function listDeepResearchRuns(options?: AxiosRequestConfig) {
  return request.get<DeepResearchRunList>('/deep-research/runs', withDeepResearchConfig(options))
}

export function getDeepResearchQueueStatus(options?: AxiosRequestConfig) {
  return request.get<DeepResearchQueueStatus>(
    '/deep-research/queue',
    withDeepResearchConfig(options),
  )
}

export function updateDeepResearchPriority(
  researchId: string,
  payload: DeepResearchPriorityUpdateRequest,
  options?: AxiosRequestConfig,
) {
  return request.patch<DeepResearchSubmitResponse>(
    `/deep-research/${encodeURIComponent(researchId)}/priority`,
    payload,
    withDeepResearchConfig(options),
  )
}

export function getDeepResearchMeta(researchId: string, options?: AxiosRequestConfig) {
  return request.get<DeepResearchRunMeta>(
    `/deep-research/${encodeURIComponent(researchId)}`,
    withDeepResearchConfig(options),
  )
}
