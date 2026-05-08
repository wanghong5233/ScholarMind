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

export default forwardRef<RepositoryUploadRef, Props>(function RepositoryUpload(
  props: Props,
  ref,
) {
  const { kbId, ...otherProps } = props

    const [fileList, setFileList] = useState<UploadFile[]>([])

    useImperativeHandle(ref, () => {
      return {
        submit: async () => {
          let hasError = false
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
            try {
              if (!kbId) {
                throw new Error('请选择知识库后再上传文档')
              }
              if ((file.size ?? 0) > MAX_FILE_SIZE_MB * 1024 * 1024) {
                throw new Error(`文件大小不能超过${MAX_FILE_SIZE_MB}M`)
              }
              //上传接口
              await api.repository.upload({
                kbId,
                file: file.originFileObj as File,
              })

              setFileList((prev) =>
                prev.map((item) => {
                  if (item.uid === file.uid) {
                    return {
                      ...item,
                      status: 'done',
                      url: '#',
                    }
                  }
                  return item
                }),
              )
            } catch (error: any) {
              hasError = true
              errors.push(error)
              setFileList((prev) =>
                prev.map((item) => {
                  if (item.uid === file.uid) {
                    return {
                      ...item,
                      status: 'error',
                      response: error?.message,
                    }
                  }
                  return item
                }),
              )
            }
          }

          if (hasError) {
            window.$app.message.error(errors?.[0]?.message)
            throw new Error(errors?.[0]?.message)
          } else {
            window.$app.message.success('上传已完成')
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
          支持单个或批量文件上传。单个文件不超过 {MAX_FILE_SIZE_MB}M，单批最多 {MAX_BATCH_FILES} 个；论文较多时建议分批上传，便于在解析队列中逐批回看进度。
        </p>

        <Upload
          fileList={fileList}
          onChange={(info) => setFileList(info.fileList)}
        />
      </div>
    )
  },
)
