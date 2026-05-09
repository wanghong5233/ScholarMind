import type { AxiosRequestConfig } from 'axios'
import { request } from './request'
import type { JobDetail, JobInfo } from './repository'

export function list(params?: { kbId?: number }, options?: AxiosRequestConfig) {
  const query = params?.kbId ? { kb_id: params.kbId } : undefined
  return request.get<JobInfo[]>('jobs/', {
    ...options,
    params: query,
  })
}

export function detail(jobId: number, options?: AxiosRequestConfig) {
  return request.get<JobInfo>(`jobs/${jobId}`, options)
}

const TERMINAL_JOB_STATUSES = new Set(['success', 'failed', 'partial', 'cancelled'])

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// 后端 job 是后台异步执行的（BackgroundTasks → handler.run），创建接口
// 拿到的 JobInDB 还是初始态。前端要展示真实结果（成功/失败/重复跳过）必须
// 轮询 jobs/{id}。这里集中实现，避免每个调用方各写一份。
//
// 默认 10 次 × 1500ms = 最长等 15s。本地上传 handler 通常几百毫秒就完成，
// 在线导入要走 PDF 下载 + 解析，可能多次失败重试也跑得完，不够时返回 null
// 让上层降级提示"请在任务中心查看"。
export async function waitForJobCompletion(
  jobId: number,
  options: { maxAttempts?: number; intervalMs?: number } = {},
): Promise<JobInfo | null> {
  const maxAttempts = options.maxAttempts ?? 10
  const intervalMs = options.intervalMs ?? 1500

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (attempt > 0) {
      await sleep(intervalMs)
    }
    try {
      const { data } = await detail(jobId, { errorToast: false, loading: false })
      if (!data) continue
      if (TERMINAL_JOB_STATUSES.has((data.status || '').toLowerCase())) {
        return data
      }
    } catch {
      return null
    }
  }
  return null
}

function normalizeLegacyDetails(raw: unknown, jobStatus?: string): JobDetail[] {
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

export function extractJobDetails(job: JobInfo | null | undefined): JobDetail[] {
  if (!job) return []
  const payload = job.payload as Record<string, unknown> | null | undefined
  const fromPayload = normalizeLegacyDetails(payload?.[
    'resultDetails'
  ], job.status)
  if (fromPayload.length) return fromPayload
  const fromComputed = normalizeLegacyDetails(job.details, job.status)
  if (fromComputed.length) return fromComputed
  const fromDocs = normalizeLegacyDetails(payload?.['docs'] ?? payload?.['documents'], job.status)
  if (fromDocs.length) return fromDocs
  return []
}
