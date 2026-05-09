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

export function extractJobDetails(job: JobInfo | null | undefined): JobDetail[] {
  if (!job) return []
  const fromPayload = (job.payload as Record<string, unknown> | null | undefined)?.[
    'resultDetails'
  ] as JobDetail[] | undefined
  if (Array.isArray(fromPayload)) return fromPayload
  if (Array.isArray(job.details)) return job.details
  return []
}
