import { CloseOutlined, FileOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'
import { Button, Input, Select, Space, Switch, Tooltip } from 'antd'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import classNames from 'classnames'
import { PropsWithChildren, useEffect, useRef, useState } from 'react'
import './index.scss'
import Recorder from './recorder'
import Uploader from './uploader'

export default function ComSender(
  props: PropsWithChildren<{
    className?: string
    loading?: boolean
    onSend?: (value: string) => void | Promise<void>
    onAbort?: () => void
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
    ragModeControl?: {
      value: 'fast' | 'deep'
      loading?: boolean
      disabled?: boolean
      onChange: (value: 'fast' | 'deep') => void
    }
    researchModeControl?: {
      value: 'chat' | 'deep'
      disabled?: boolean
      onChange: (value: 'chat' | 'deep') => void
    }
    onAttachmentsChange?: (files: API.ChatAttachment[]) => void
    pendingAttachments?: API.ChatAttachment[]
    onRemovePendingAttachment?: (id: number) => void
    onFileSelected?: (file: File) => void
    value?: string
    onValueChange?: (value: string) => void
    focusKey?: number
  }>,
) {
  const {
    className,
    onSend,
    onAbort,
    onContract,
    loading,
    sessionId,
    enableSessionKnowledgeBase = true,
    knowledgeControl,
    ragModeControl,
    researchModeControl,
    onAttachmentsChange,
    pendingAttachments = [],
    onRemovePendingAttachment,
    onFileSelected,
    value: controlledValue,
    onValueChange,
    focusKey,
    ...rest
  } = props
  const [innerValue, setInnerValue] = useState('')
  const textareaRef = useRef<TextAreaRef>(null)
  const isControlled = typeof controlledValue === 'string'
  const value = isControlled ? controlledValue! : innerValue

  useEffect(() => {
    if (typeof focusKey === 'number' && textareaRef.current) {
      textareaRef.current.focus()
    }
  }, [focusKey])

  const updateValue = (next: string) => {
    if (onValueChange) onValueChange(next)
    if (!isControlled) setInnerValue(next)
  }

  async function send() {
    if (loading) return
    if (!value) return
    await onSend?.(value)
    updateValue('')
  }

  function handleSendClick() {
    if (loading && onAbort) {
      onAbort()
    } else {
      send()
    }
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
        ref={textareaRef}
        value={value}
        onChange={(e) => updateValue(e.target.value)}
        placeholder="输入你的问题…"
        autoSize={{ minRows: 2 }}
        autoFocus
      />

      <div className="com-sender__actions">
        <Space className="com-sender__actions-left" size={12}>
          <Recorder
            onMessage={(text) => {
              updateValue(text)
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
          {ragModeControl ? (
            <Space size={6} align="center">
              <Tooltip title="快速模式更省时，深度模式启用图谱与多模态增强">
                <span className="com-sender__kb-label">检索模式</span>
              </Tooltip>
              <Select
                size="small"
                className="com-sender__rag-select"
                value={ragModeControl.value}
                disabled={ragModeControl.disabled}
                loading={ragModeControl.loading}
                options={[
                  { label: '快速', value: 'fast' },
                  { label: '深度', value: 'deep' },
                ]}
                onChange={(value) => ragModeControl.onChange(value as 'fast' | 'deep')}
              />
            </Space>
          ) : null}
          {researchModeControl ? (
            <Space size={6} align="center">
              <Tooltip title="深度研究会执行规划、检索与报告生成">
                <span className="com-sender__kb-label">研究模式</span>
              </Tooltip>
              <Select
                size="small"
                className="com-sender__research-select"
                value={researchModeControl.value}
                disabled={researchModeControl.disabled}
                options={[
                  { label: '对话', value: 'chat' },
                  { label: '深度研究', value: 'deep' },
                ]}
                onChange={(value) =>
                  researchModeControl.onChange(value as 'chat' | 'deep')
                }
              />
            </Space>
          ) : null}
        </Space>

        <Space className="com-sender__actions-right" size={12}>
          {sessionId ? (
            <Uploader onFileSelected={(file) => onFileSelected?.(file)} />
          ) : null}
          <Button
            className="com-sender__action--send"
            type="primary"
            shape="circle"
            onClick={handleSendClick}
            disabled={!loading && !value?.trim()}
          >
            {loading ? <StopOutlined /> : <SendOutlined />}
          </Button>
        </Space>
      </div>
    </div>
  )
}
