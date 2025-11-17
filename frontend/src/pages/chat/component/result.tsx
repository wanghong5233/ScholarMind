import IconCopy from '@/assets/chat/copy.svg'
import IconReference from '@/assets/chat/reference.svg'
import IconRefresh from '@/assets/chat/refresh.svg'
import IconShare from '@/assets/chat/share.svg'
import IconTip from '@/assets/chat/tip.svg'
import Markdown from '@/components/markdown'
import { ArrowRightOutlined } from '@ant-design/icons'
import { Button, Dropdown, Tooltip, message } from 'antd'
import classNames from 'classnames'
import dayjs from 'dayjs'
import { useCallback, useMemo } from 'react'
import styles from './result.module.scss'

export function Result(props: {
  item: API.ChatItem
  isEnd?: boolean
  onSend?: (text: string) => void
  onRefrence?: (index: number) => void
  onOpenCitations?: () => void
}) {
  const { item, isEnd, onSend, onRefrence, onOpenCitations } = props

  const shareMenu = useMemo(() => {
    return [
      {
        key: 'pdf',
        label: '导出为 TXT',
        onClick: async () => {
          const url = `data:text/plain;charset=utf-8,${encodeURIComponent(item.content ?? '')}`
          const a = document.createElement('a')
          a.href = url
          a.download = 'output.txt'
          a.click()
        },
      },
      {
        key: 'email',
        label: '发送到 Email',
      },
    ]
  }, [item.content])

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement
      const index = target.getAttribute('data-refrence-index')
      if (index) {
        onRefrence?.(Number(index))
      }
    },
    [onRefrence],
  )

  const handleCopyContent = useCallback(async () => {
    const text = item.content || item.think || ''
    if (!text) {
      message.warning('暂无可复制的内容')
      return
    }

    const tryClipboard = async () => {
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text)
          return true
        }
      } catch {
        return false
      }
      return false
    }

    const fallbackCopy = () => {
      try {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        textarea.style.left = '-9999px'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        const succeeded = document.execCommand('copy')
        document.body.removeChild(textarea)
        return succeeded
      } catch {
        return false
      }
    }

    const success = (await tryClipboard()) || fallbackCopy()
    if (success) {
      message.success('回答内容已复制')
    } else {
      message.error('复制失败，请手动选择文本')
    }
  }, [item.content, item.think])

  return (
    <div className={styles['chat-message-result']}>
      {item.think ? (
        item.loading ? (
          <div
            className={classNames(
              styles['chat-message-result__think'],
              styles['chat-message-result__streaming'],
            )}
          >
            {item.think}
          </div>
        ) : (
          <Markdown
            className={classNames(
              styles['chat-message-result__think'],
              styles['chat-message-result__md'],
            )}
            value={item.think}
            onClick={handleClick}
          />
        )
      ) : null}

      {item.content ? (
        item.loading ? (
          <div className={styles['chat-message-result__streaming']}>
            {item.content}
          </div>
        ) : (
          <Markdown
            className={styles['chat-message-result__md']}
            value={item.content}
            onClick={handleClick}
          />
        )
      ) : null}

      {item.error ? (
        <div className={styles['chat-message-result__error']}>{item.error}</div>
      ) : null}

      {item.loading ? null : (
        <>
          <div className={styles['chat-message-result__actions']}>
            <div className={styles['date']}>
              {dayjs().format('HH:mm YYYY/MM/DD')}
            </div>

            {isEnd ? null : (
              <Button
                variant="text"
                color="primary"
                shape="circle"
                size="small"
                style={{ color: 'var(--ant-color-primary)' }}
              >
                <img src={IconRefresh} />
              </Button>
            )}

            <Button
              variant="text"
              color="primary"
              shape="circle"
              size="small"
              style={{ color: 'var(--ant-color-primary)' }}
            >
              <img src={IconTip} />
            </Button>

            <Button
              variant="text"
              color="primary"
              shape="circle"
              size="small"
              style={{ color: 'var(--ant-color-primary)' }}
              onClick={handleCopyContent}
            >
              <img src={IconCopy} />
            </Button>

            <Dropdown menu={{ items: shareMenu }}>
              <Button
                variant="text"
                color="primary"
                shape="circle"
                size="small"
                style={{ color: 'var(--ant-color-primary)' }}
              >
                <img src={IconShare} />
              </Button>
            </Dropdown>

            {item.reference?.length ? (
              <Tooltip title="查看该回答的参考引文">
                <Button
                  variant="text"
                  color="primary"
                  shape="circle"
                  size="small"
                  style={{ color: 'var(--ant-color-primary)' }}
                  onClick={onOpenCitations}
                >
                  <img src={IconReference} />
                </Button>
              </Tooltip>
            ) : null}
          </div>

          {isEnd ? (
            <div className={styles['chat-message-result__quick-reply']}>
              {item.recommended_questions?.map((item) => (
                <Button
                  className={styles['item']}
                  key={item}
                  onClick={() => onSend?.(item)}
                >
                  <span className={styles['text']}>🔎 {item}</span>
                  <ArrowRightOutlined className={styles['arrow']} />
                </Button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
