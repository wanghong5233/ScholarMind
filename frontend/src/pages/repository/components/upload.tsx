import * as api from '@/api'
import IconUpload from '@/assets/repository/upload.svg'
import { Upload, UploadFile, UploadProps } from 'antd'
import { forwardRef, useImperativeHandle, useState } from 'react'
import styles from './upload.module.scss'

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
              // 检查文件大小
              const LIMIT_MB = 50
              if ((file.size ?? 0) > LIMIT_MB * 1024 * 1024) {
                throw new Error(`文件大小不能超过${LIMIT_MB}M`)
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
          maxCount={10}
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
          支持单个或批量文件上传。文件大小不能超过50M，最多支持10个文件。
        </p>

        <Upload
          fileList={fileList}
          onChange={(info) => setFileList(info.fileList)}
        />
      </div>
    )
  },
)
