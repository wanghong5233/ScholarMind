import * as api from '@/api'
import type { RepositoryDocument } from '@/api/repository'
import IconSendThunder from '@/assets/component/send-thunder.svg'
import { FileOutlined } from '@ant-design/icons'
import { useRequest } from 'ahooks'
import { Button, Input, Space } from 'antd'
import classNames from 'classnames'
import { PropsWithChildren, useState } from 'react'
import './index.scss'
import Recorder from './recorder'
import Uploader from './uploader'

export default function ComSender(
  props: PropsWithChildren<{
    className?: string
    loading?: boolean
    onSend?: (value: string) => void | Promise<void>
    onContract?: () => void
    sessionId?: string
  }>,
) {
  const { className, onSend, onContract, loading, sessionId, ...rest } = props
  const [value, setValue] = useState('')

  async function send() {
    if (loading) return
    if (!value) return
    await onSend?.(value)
    setValue('')
  }

  const uploaded = useRequest(
    async () => {
      if (!sessionId) return null
      const { data: sessionDetail } = await api.session.info({ sessionId })
      if (!sessionDetail?.kbId) return null
      const { data: docs } = await api.repository.listDocuments({
        kbId: sessionDetail.kbId,
      })
      return (docs?.[0] as RepositoryDocument | undefined) ?? null
    },
    {
      refreshDeps: [sessionId],
    },
  )

  return (
    <div className={classNames('com-sender', className)} {...rest}>
      <Input.TextArea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="输入你的问题…"
        autoSize={{ minRows: 2 }}
        autoFocus
      />

      <div className="com-sender__actions">
        <Space className="com-sender__actions-left" size={12}>
          <Recorder
            onMessage={(text) => {
              setValue(text)
            }}
          />
        </Space>

        <Space className="com-sender__actions-right" size={12}>
          {sessionId ? (
            uploaded.data ? (
              <Button
                className="com-sender__action--contract"
                variant="text"
                color="default"
                shape="round"
                disabled
                title={uploaded.data.title}
              >
                <FileOutlined style={{ fontSize: 14 }} />
                <span className="document-name">
                  {uploaded.data.title}
                </span>
              </Button>
            ) : (
              <Uploader
                sessionId={sessionId}
                onSuccess={() => {
                  uploaded.refresh()
                }}
              />
            )
          ) : null}
          <Button
            className="com-sender__action--send"
            variant="solid"
            color="primary"
            shape="round"
            onClick={send}
            loading={loading}
          >
            发送
            <img src={IconSendThunder} />
          </Button>
        </Space>
      </div>
    </div>
  )
}
