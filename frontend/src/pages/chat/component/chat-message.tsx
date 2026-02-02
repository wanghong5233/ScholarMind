import IconAvatar from '@/assets/chat/avatar.svg'
import { ChatRole, ChatType } from '@/configs'
import { FileOutlined } from '@ant-design/icons'
import { Avatar, Button } from 'antd'
import classNames from 'classnames'
import { useMemo } from 'react'
import { createChatIdText } from '../shared'
import styles from './chat-message.module.scss'
import DeepResearchCard from './deep-research-card'
import { Result } from './result'
import ChooseFile from './select-file'

function UserMessage(props: {
  item: API.ChatItem
  index: number
  onRetry?: (item: API.ChatItem, index: number) => void
  onResend?: (item: API.ChatItem, index: number) => void
}) {
  const { item, index, onRetry, onResend } = props

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
        </div>
      </div>

      {item.message_id ? (
        <div className={styles['chat-message-item__toolbar']}>
          <Button type="text" size="small" onClick={() => onRetry?.(item, index)}>
            重新编辑
          </Button>
          <Button
            type="text"
            size="small"
            onClick={() => onResend?.(item, index)}
          >
            立即重发
          </Button>
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
  onDeepResearchOpenWorkspace?: (item: API.ChatItem) => void
  onDeepResearchExport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onDeepResearchCopy?: (item: API.ChatItem) => void
  onDeepResearchSaveToNotebook?: (item: API.ChatItem) => void
  onDeepResearchInsertSummary?: (item: API.ChatItem, summary: string) => void
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
    onDeepResearchOpenWorkspace,
    onDeepResearchExport,
    onDeepResearchCopy,
    onDeepResearchSaveToNotebook,
    onDeepResearchInsertSummary,
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

        <div className={styles['name']}>Doc Copilet</div>
      </div>

      <div className={styles['chat-message-item__content']}>
        {(() => {
          switch (item.type) {
            case ChatType.Document:
              if (item.loading && !item.documents?.length) {
                return <ChooseFile.Searching />
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
  onDeepResearchOpenWorkspace?: (item: API.ChatItem) => void
  onDeepResearchExport?: (item: API.ChatItem, format: 'pdf' | 'markdown') => void
  onDeepResearchCopy?: (item: API.ChatItem) => void
  onDeepResearchSaveToNotebook?: (item: API.ChatItem) => void
  onDeepResearchInsertSummary?: (item: API.ChatItem, summary: string) => void
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
    onDeepResearchOpenWorkspace,
    onDeepResearchExport,
    onDeepResearchCopy,
    onDeepResearchSaveToNotebook,
    onDeepResearchInsertSummary,
  } = props

  return (
    <div className={styles['chat-message']}>
      {list.map((item, index) => {
        if (item.role === ChatRole.User) {
          return (
            <UserMessage
              key={item.id}
              item={item}
              index={index}
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
            onDeepResearchOpenWorkspace={onDeepResearchOpenWorkspace}
            onDeepResearchExport={onDeepResearchExport}
            onDeepResearchCopy={onDeepResearchCopy}
            onDeepResearchSaveToNotebook={onDeepResearchSaveToNotebook}
            onDeepResearchInsertSummary={onDeepResearchInsertSummary}
          />
        )
      })}
    </div>
  )
}
