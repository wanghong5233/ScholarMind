import IconSendThunder from '@/assets/component/send-thunder.svg'
import { CloseOutlined, FileOutlined } from '@ant-design/icons'
import { Button, Input, Select, Space, Switch, Tooltip } from 'antd'
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
    enableSessionKnowledgeBase?: boolean
    knowledgeControl?: {
      usingSession: boolean
      usingUser: boolean
      selectValue?: number
      options: { value: number; label: string; disabled?: boolean }[]
      showSelect: boolean
      loadingSession?: boolean
      loadingUser?: boolean
      disableUserToggle?: boolean
      disableSelect?: boolean
      onToggleSession: (checked: boolean) => void
      onToggleUser: (checked: boolean) => void
      onSelectUserKb: (value: number) => void
    }
    onAttachmentsChange?: (files: API.ChatAttachment[]) => void
    pendingAttachments?: API.ChatAttachment[]
    onRemovePendingAttachment?: (id: number) => void
    onFileSelected?: (file: File) => void
  }>,
) {
  const {
    className,
    onSend,
    onContract,
    loading,
    sessionId,
    enableSessionKnowledgeBase = true,
    knowledgeControl,
    onAttachmentsChange,
    pendingAttachments = [],
    onRemovePendingAttachment,
    onFileSelected,
    ...rest
  } = props
  const [value, setValue] = useState('')

  async function send() {
    if (loading) return
    if (!value) return
    await onSend?.(value)
    setValue('')
  }
  return (
    <div className={classNames('com-sender', className)} {...rest}>
      {pendingAttachments.length > 0 && (
        <div className="com-sender__pending-attachments">
          {pendingAttachments.map((att) => (
            <div key={att.id} className="com-sender__pending-chip">
              <FileOutlined style={{ fontSize: 12, color: '#0862fe' }} />
              <Tooltip title={att.title}>
                <span className="com-sender__pending-name">{att.title}</span>
              </Tooltip>
              <Button
                type="text"
                size="small"
                icon={<CloseOutlined style={{ fontSize: 10 }} />}
                className="com-sender__pending-remove"
                onClick={() => onRemovePendingAttachment?.(att.id)}
              />
            </div>
          ))}
        </div>
      )}
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
          {knowledgeControl ? (
            <>
              <Space size={6} align="center">
                <Switch
                  size="small"
                  checked={knowledgeControl.usingSession}
                  loading={knowledgeControl.loadingSession}
                  onChange={knowledgeControl.onToggleSession}
                />
                <Tooltip title="仅使用本次对话上传的临时资料进行检索">
                  <span className="com-sender__kb-label">临时知识库</span>
                </Tooltip>
              </Space>
              <Space size={6} align="center">
                <Switch
                  size="small"
                  checked={knowledgeControl.usingUser}
                  loading={knowledgeControl.loadingUser}
                  disabled={knowledgeControl.disableUserToggle}
                  onChange={knowledgeControl.onToggleUser}
                />
                <Tooltip title="启用后会同时检索所选知识库中的文档">
                  <span className="com-sender__kb-label">关联知识库</span>
                </Tooltip>
                {knowledgeControl.showSelect ? (
                  <Select
                    size="small"
                    className="com-sender__kb-select"
                    value={knowledgeControl.selectValue}
                    options={knowledgeControl.options}
                    placeholder="选择知识库"
                    disabled={knowledgeControl.disableSelect}
                    onChange={(value) =>
                      knowledgeControl.onSelectUserKb(Number(value))
                    }
                    showSearch
                    optionFilterProp="label"
                  />
                ) : null}
              </Space>
            </>
          ) : null}
        </Space>

        <Space className="com-sender__actions-right" size={12}>
          {sessionId ? (
            <Uploader onFileSelected={(file) => onFileSelected?.(file)} />
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
