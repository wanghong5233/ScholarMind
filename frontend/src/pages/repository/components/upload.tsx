import * as api from '@/api'
import IconUpload from '@/assets/repository/upload.svg'
import { Upload, UploadFile, UploadProps } from 'antd'
import { forwardRef, useImperativeHandle, useState } from 'react'
import styles from './upload.module.scss'

// 一批上传的硬上限。50 = 一次建库的典型规模（一篇综述的参考文献、
// 一个会议某 track 的论文集），同时避免误拖整个文件夹打爆浏览器/后端。
const MAX_BATCH_FILES = 50
const MAX_FILE_SIZE_MB = 50
// 并发上限 3：
//   - 浏览器对同 host 默认 6 路 HTTP/1.1 连接，留 3 个给文档列表轮询、
//     job 详情拉取等被动 GET，避免相互排队。
//   - 后端 LocalUploadHandler 每个 job 主要是 hash + 落盘 + 一行 INSERT，
//     ECS 2C2G 上 3 个并发完全压得住；ParseIndexHandler 后续解析仍是
//     单 worker 串行，无影响。
const UPLOAD_CONCURRENCY = 3

export type RepositoryUploadRef = {
  submit: () => Promise<void>
}

type Props = UploadProps & {
  kbId?: number | null
}

type UploadOutcome = 'ok' | 'duplicate' | 'failed' | 'pending'

export default forwardRef<RepositoryUploadRef, Props>(function RepositoryUpload(
  props: Props,
  ref,
) {
  const { kbId, ...otherProps } = props

    const [fileList, setFileList] = useState<UploadFile[]>([])

    useImperativeHandle(ref, () => {
      return {
        submit: async () => {
          let okCount = 0
          let duplicateCount = 0
          let failedCount = 0
          let pendingCount = 0
          const errors: Error[] = []

          // 单文件的完整上传流程：HTTP POST → 等后端 job 终态 → 更新行状态 + 计数器。
          // 由外层 worker-pool 控制并发，函数内部不需要锁（单线程 JS 下计数器
          // 读改写都是同步操作）。
          const uploadOne = async (file: UploadFile) => {
            setFileList((prev) =>
              prev.map((item) =>
                item.uid === file.uid
                  ? { ...item, status: 'uploading' }
                  : item,
              ),
            )

            let outcome: UploadOutcome = 'failed'
            let outcomeMessage: string | undefined

            try {
              if (!kbId) {
                throw new Error('请选择知识库后再上传文档')
              }
              if ((file.size ?? 0) > MAX_FILE_SIZE_MB * 1024 * 1024) {
                throw new Error(`文件大小不能超过${MAX_FILE_SIZE_MB}M`)
              }

              const { data: job } = await api.repository.upload({
                kbId,
                file: file.originFileObj as File,
              })

              const finalJob = job?.id
                ? await api.job.waitForJobCompletion(job.id)
                : null

              if (!finalJob) {
                outcome = 'pending'
              } else {
                const details = api.job.extractJobDetails(finalJob)
                const detail = details[0]
                if (detail?.status === 'duplicate') {
                  outcome = 'duplicate'
                  outcomeMessage = '已存在于该知识库'
                } else if (detail?.status === 'failed') {
                  outcome = 'failed'
                  outcomeMessage = detail.error || '后端处理失败'
                } else if (
                  detail?.status === 'ok' ||
                  finalJob.succeeded > 0
                ) {
                  outcome = 'ok'
                } else {
                  outcome = 'failed'
                  outcomeMessage = finalJob.error || '未拿到处理结果'
                }
              }
            } catch (error: any) {
              outcome = 'failed'
              outcomeMessage = error?.message
              errors.push(error)
            }

            setFileList((prev) =>
              prev.map((item) => {
                if (item.uid !== file.uid) return item
                if (outcome === 'failed') {
                  return {
                    ...item,
                    status: 'error',
                    response: outcomeMessage,
                  }
                }
                return {
                  ...item,
                  status: 'done',
                  url: '#',
                  response: outcomeMessage,
                }
              }),
            )

            if (outcome === 'ok') okCount += 1
            else if (outcome === 'duplicate') duplicateCount += 1
            else if (outcome === 'pending') pendingCount += 1
            else failedCount += 1
          }

          // worker-pool：起 N 个 worker 并发跑 uploadOne，每个 worker 用
          // 共享队列 shift 下一个任务，直到清空。比 Promise.all(map) 简单且
          // 自然满足"任意一个失败不影响其他、整体并发恒定"两个约束。
          const queue = fileList.filter((f) => f.status !== 'done').slice()
          const workerCount = Math.min(UPLOAD_CONCURRENCY, queue.length)
          const workers = Array.from({ length: workerCount }, async () => {
            while (queue.length) {
              const next = queue.shift()
              if (!next) break
              await uploadOne(next)
            }
          })
          await Promise.all(workers)

          const summaryParts: string[] = []
          if (okCount > 0) summaryParts.push(`${okCount} 篇新增`)
          if (duplicateCount > 0) summaryParts.push(`${duplicateCount} 篇已存在跳过`)
          if (pendingCount > 0) summaryParts.push(`${pendingCount} 篇仍在处理`)
          if (failedCount > 0) summaryParts.push(`${failedCount} 篇失败`)

          const summary = summaryParts.join('，') || '本次没有需要上传的文件'

          if (failedCount > 0) {
            window.$app.message.error(`上传完成：${summary}`)
            throw new Error(errors[0]?.message || summary)
          }
          if (duplicateCount > 0 && okCount === 0) {
            window.$app.message.info(summary)
          } else if (duplicateCount > 0 || pendingCount > 0) {
            window.$app.message.warning(summary)
          } else {
            window.$app.message.success(summary)
          }
        },
      }
    })

    return (
      <div className={styles['repository-upload']}>
        <Upload.Dragger
          {...otherProps}
          showUploadList={false}
          multiple
          maxCount={MAX_BATCH_FILES}
          fileList={fileList}
          onChange={(info) => setFileList(info.fileList)}
        >
          <img src={IconUpload} />
          <p
            className="ant-upload-text"
            style={{
              color: '#666',
            }}
          >
            拖拽文件到此 或{' '}
            <span style={{ color: '#409EFF' }}>点击上传</span>
          </p>
        </Upload.Dragger>

        <p className={styles['repository-upload__desc']}>
          支持单个或批量文件上传。单个文件不超过 {MAX_FILE_SIZE_MB}M，单批最多 {MAX_BATCH_FILES} 个；
          论文较多时建议分批上传，便于在解析队列中逐批回看进度。
          重复文件（按文件内容哈希判断）会自动跳过，不会重复入库。
        </p>

        <Upload
          fileList={fileList}
          onChange={(info) => setFileList(info.fileList)}
        />
      </div>
    )
  },
)
