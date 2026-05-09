import IconAvatar from '@/assets/chat/avatar.svg'
import { ChatRole, ChatType } from '@/configs'
import {
  CopyOutlined,
  EditOutlined,
  FileOutlined,
  RedoOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Image, Tooltip, message } from 'antd'
import classNames from 'classnames'
import { useCallback, useMemo } from 'react'
import { createChatIdText } from '../shared'
import styles from './chat-message.module.scss'
import DeepResearchCard from './deep-research-card'
import { Result } from './result'
import ChooseFile from './select-file'

function UserMessage(props: {
  item: API.ChatItem
  index: number
  showToolbar?: boolean
  onRetry?: (item: API.ChatItem, index: number) => void
  onResend?: (item: API.ChatItem, index: number) => void
  onCopyPrompt?: (item: API.ChatItem) => void
}) {
  const { item, index, showToolbar, onRetry, onResend, onCopyPrompt } = props
  const messageImages = Array.isArray(item.images) ? item.images : []

  const handleCopyPrompt = useCallback(async () => {
    if (onCopyPrompt) {
      onCopyPrompt(item)
      return
    }
    const text = String(item.content || '').trim()
    if (!text) {
      message.warning('暂无可复制内容')
      return
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        textarea.style.left = '-9999px'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      message.success('提示词已复制')
    } catch {
      message.error('复制失败，请手动复制')
    }
  }, [item, onCopyPrompt])

  return (
    <>
      <div
        className={classNames(
          styles['chat-message-item'],
          styles['chat-message-item--user'],
        )}
      >
        <div className={styles['chat-message-item__bubble']}>
          <div className={styles['chat-message-item__content']}>
            {item.content}
          </div>
          {item.attachments?.length ? (
            <div className={styles['chat-message-item__attachments']}>
              {item.attachments.map((doc) => (
                <div
                  key={doc.id}
                  className={styles['chat-message-item__attachment']}
                  title={doc.title}
                >
                  <FileOutlined />
                  <span>{doc.title}</span>
                </div>
              ))}
            </div>
          ) : null}
          {messageImages.length > 0 ? (
            <div className={styles['chat-message-item__images']}>
              <Image.PreviewGroup>
                {messageImages.map((img) => (
                  <Image
                    key={img.id}
                    src={img.dataUrl}
                    alt={img.name}
                    width={36}
                    height={36}
                    rootClassName={styles['chat-message-item__image-thumb-wrap']}
                    preview={{ mask: '预览' }}
                  />
                ))}
              </Image.PreviewGroup>
            </div>
          ) : null}
        </div>
      </div>

      {showToolbar ? (
        <div className={styles['chat-message-item__toolbar']}>
          <Tooltip title="复制提示词">
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              onClick={handleCopyPrompt}
            />
          </Tooltip>
          <Tooltip title="编辑后继续">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => onRetry?.(item, index)}
            />
          </Tooltip>
          <Tooltip title="立即重发">
            <Button
              type="text"
              size="small"
              icon={<RedoOutlined />}
              onClick={() => onResend?.(item, index)}
            />
          </Tooltip>
        </div>
      ) : null}
    </>
  )
}

function AssistantMessage(props: {
  item: API.ChatItem
  isEnd?: boolean
  onSend?: (text: string) => void
  onOpenCiations?: () => void
  onRefrence?: (index: number) => void
  onDeepResearchConfirm?: (item: API.ChatItem) => void
  onDeepResearchCancel?: (item: API.ChatItem) => void
  onDeepResearchEdit?: (item: API.ChatItem) => void
  onDeepResearchRetryPlan?: (item: API.ChatItem) => void
  onDeepResearchOpenProcess?: (item: API.ChatItem) => void
  onDeepResearchOpenWorkspace?: (item: API.ChatItem) => void
  onDeepResearchExport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onDeepResearchCopy?: (item: API.ChatItem) => void
  onDeepResearchSaveToNotebook?: (item: API.ChatItem) => void
  onDeepResearchInsertSummary?: (item: API.ChatItem, summary: string) => void
  onAssistantFeedback?: (
    item: API.ChatItem,
    rating: 'thumbs_up' | 'thumbs_down',
  ) => void
  feedback?: 'thumbs_up' | 'thumbs_down'
}) {
  const {
    item,
    isEnd,
    onSend,
    onOpenCiations,
    onRefrence,
    onDeepResearchConfirm,
    onDeepResearchCancel,
    onDeepResearchEdit,
    onDeepResearchRetryPlan,
    onDeepResearchOpenProcess,
    onDeepResearchOpenWorkspace,
    onDeepResearchExport,
    onDeepResearchCopy,
    onDeepResearchSaveToNotebook,
    onDeepResearchInsertSummary,
    onAssistantFeedback,
    feedback,
  } = props

  const id = useMemo(() => {
    if (item.type === ChatType.Document) {
      return createChatIdText(item.id)
    }
  }, [item.id, item.type])

  return (
    <div
      id={id}
      className={classNames(
        styles['chat-message-item'],
        styles['chat-message-item--assistant'],
      )}
    >
      <div className={styles['chat-message-item__sender']}>
        <Avatar className={styles['avatar']} src={IconAvatar} />

        <div className={styles['name']}>ScholarMind</div>
      </div>

      <div className={styles['chat-message-item__content']}>
        {(() => {
          switch (item.type) {
            case ChatType.Document:
              if (item.loading && !item.documents?.length) {
                return <ChooseFile.Searching message={item.think} />
              } else if (!item.error) {
                return (
                  <ChooseFile.Complete
                    contractsLength={item.documents?.length ?? 0}
                    citationsLength={item.reference?.length ?? 0}
                    onClick={onOpenCiations}
                  />
                )
              }
              return null
            case ChatType.DeepResearch:
              return (
                <DeepResearchCard
                  item={item}
                  onConfirm={onDeepResearchConfirm}
                  onCancel={onDeepResearchCancel}
                  onEdit={onDeepResearchEdit}
                  onRetryPlan={onDeepResearchRetryPlan}
                  onOpenProcess={onDeepResearchOpenProcess}
                  onOpenWorkspace={onDeepResearchOpenWorkspace}
                  onExportReport={onDeepResearchExport}
                  onCopyReport={onDeepResearchCopy}
                  onSaveToNotebook={onDeepResearchSaveToNotebook}
                  onInsertSummary={onDeepResearchInsertSummary}
                />
              )
          }
        })()}

        {item.type === ChatType.DeepResearch ? null : (
          <Result
            item={item}
            isEnd={isEnd}
            onSend={onSend}
            onRefrence={onRefrence}
            onOpenCitations={onOpenCiations}
            feedback={feedback}
            onFeedback={(rating) => onAssistantFeedback?.(item, rating)}
          />
        )}
      </div>

    </div>
  )
}

export default function ChatMessage(props: {
  list: API.ChatItem[]
  onSend?: (text: string) => void
  onOpenCiations?: (item: API.ChatItem) => void
  onRefrence?: (target: API.Reference) => void
  onRetryUserMessage?: (item: API.ChatItem, index: number) => void
  onResendUserMessage?: (item: API.ChatItem, index: number) => void
  onDeepResearchConfirm?: (item: API.ChatItem) => void
  onDeepResearchCancel?: (item: API.ChatItem) => void
  onDeepResearchEdit?: (item: API.ChatItem) => void
  onDeepResearchRetryPlan?: (item: API.ChatItem) => void
  onDeepResearchOpenProcess?: (item: API.ChatItem) => void
  onDeepResearchOpenWorkspace?: (item: API.ChatItem) => void
  onDeepResearchExport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onDeepResearchCopy?: (item: API.ChatItem) => void
  onDeepResearchSaveToNotebook?: (item: API.ChatItem) => void
  onDeepResearchInsertSummary?: (item: API.ChatItem, summary: string) => void
  onAssistantFeedback?: (
    item: API.ChatItem,
    rating: 'thumbs_up' | 'thumbs_down',
  ) => void
  feedbackByMessageId?: Record<string, 'thumbs_up' | 'thumbs_down' | undefined>
}) {
  const {
    list,
    onSend,
    onOpenCiations,
    onRefrence,
    onRetryUserMessage,
    onResendUserMessage,
    onDeepResearchConfirm,
    onDeepResearchCancel,
    onDeepResearchEdit,
    onDeepResearchRetryPlan,
    onDeepResearchOpenProcess,
    onDeepResearchOpenWorkspace,
    onDeepResearchExport,
    onDeepResearchCopy,
    onDeepResearchSaveToNotebook,
    onDeepResearchInsertSummary,
    onAssistantFeedback,
    feedbackByMessageId,
  } = props

  return (
    <div className={styles['chat-message']}>
      {list.map((item, index) => {
        if (item.role === ChatRole.User) {
          const nextItem = list[index + 1]
          const followsDeepResearchCard =
            nextItem?.role === ChatRole.Assistant && nextItem?.type === ChatType.DeepResearch
          const hasVisibleAssistantReply =
            nextItem?.role === ChatRole.Assistant &&
            Boolean(
              nextItem?.message_id ||
                (typeof nextItem?.content === 'string' && nextItem.content.trim()) ||
                nextItem?.error,
            )
          return (
            <UserMessage
              key={item.id}
              item={item}
              index={index}
              showToolbar={Boolean(
                item.message_id || followsDeepResearchCard || hasVisibleAssistantReply,
              )}
              onRetry={onRetryUserMessage}
              onResend={onResendUserMessage}
            />
          )
        }

        return (
          <AssistantMessage
            key={item.id}
            item={item}
            isEnd={list.length - 1 === index}
            onSend={onSend}
            onOpenCiations={() => onOpenCiations?.(item)}
            onRefrence={(index) => {
              const target = item.reference?.[index]
              if (target) onRefrence?.(target)
            }}
            onDeepResearchConfirm={onDeepResearchConfirm}
            onDeepResearchCancel={onDeepResearchCancel}
            onDeepResearchEdit={onDeepResearchEdit}
            onDeepResearchRetryPlan={onDeepResearchRetryPlan}
            onDeepResearchOpenProcess={onDeepResearchOpenProcess}
            onDeepResearchOpenWorkspace={onDeepResearchOpenWorkspace}
            onDeepResearchExport={onDeepResearchExport}
            onDeepResearchCopy={onDeepResearchCopy}
            onDeepResearchSaveToNotebook={onDeepResearchSaveToNotebook}
            onDeepResearchInsertSummary={onDeepResearchInsertSummary}
            onAssistantFeedback={onAssistantFeedback}
            feedback={item.message_id ? feedbackByMessageId?.[item.message_id] : undefined}
          />
        )
      })}
    </div>
  )
}
