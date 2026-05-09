import * as api from '@/api'
import IconUpload from '@/assets/repository/upload.svg'
import { Upload, UploadFile, UploadProps } from 'antd'
import { forwardRef, useImperativeHandle, useState } from 'react'
import styles from './upload.module.scss'

// 一批上传的硬上限。50 = 一次建库的典型规模（一篇综述的参考文献、
// 一个会议某 track 的论文集），同时避免误拖整个文件夹打爆浏览器/后端。
// 单文件 50MB × 50 = 一次最多 2.5GB 串行上传，配合后端 worker=1 的
// 解析队列在合理时间内能消化完。
const MAX_BATCH_FILES = 50
const MAX_FILE_SIZE_MB = 50

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

          for (const file of fileList) {
            if (file.status === 'done') continue

            setFileList((prev) =>
              prev.map((item) => {
                if (item.uid === file.uid) {
                  return {
                    ...item,
                    status: 'uploading',
                  }
                }
                return item
              }),
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
                // 后端确认任务已创建但前端没等到终态，按"待处理"对待，
                // 不当作失败 —— 后台仍在跑，避免误导用户。
                outcome = 'pending'
              } else {
                const details = api.job.extractJobDetails(finalJob)
                // LocalUploadHandler 单 job 只处理 1 个文件 → details 长度 0/1
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
                // ok / duplicate / pending 都不阻塞用户，统一标 done；
                // 蒙层提示由下方汇总 message 给出。
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
