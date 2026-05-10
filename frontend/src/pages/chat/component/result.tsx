import IconCopy from '@/assets/chat/copy.svg'
import IconReference from '@/assets/chat/reference.svg'
import IconRefresh from '@/assets/chat/refresh.svg'
import IconShare from '@/assets/chat/share.svg'
import Markdown from '@/components/markdown'
import { ArrowRightOutlined, DislikeOutlined, LikeOutlined } from '@ant-design/icons'
import { Button, Dropdown, Tooltip, message } from 'antd'
import classNames from 'classnames'
import dayjs from 'dayjs'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import styles from './result.module.scss'

export function Result(props: {
  item: API.ChatItem
  isEnd?: boolean
  onSend?: (text: string) => void
  onRefrence?: (index: number) => void
  onOpenCitations?: () => void
  feedback?: 'thumbs_up' | 'thumbs_down'
  onFeedback?: (rating: 'thumbs_up' | 'thumbs_down') => void
}) {
  const { item, isEnd, onSend, onRefrence, onOpenCitations, feedback, onFeedback } = props
  const thinkStreamingRef = useRef<HTMLDivElement | null>(null)
  const contentStreamingRef = useRef<HTMLDivElement | null>(null)
  const thinkStickToBottomRef = useRef(true)
  const contentStickToBottomRef = useRef(true)

  const elapsedLabel = useMemo(() => {
    const raw = Number(item.elapsed_seconds)
    if (!Number.isFinite(raw) || raw <= 0) return ''
    const normalized = raw >= 10 ? raw.toFixed(0) : raw.toFixed(1)
    return `用时 ${normalized}s`
  }, [item.elapsed_seconds])

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

  // 引用 chip 的点击与 hover 交给 Markdown 组件统一处理（onCitationClick），
  // 这里仅保留 onClick 透传以兼容容器层面的事件冒泡需求；不再做事件委托
  // 解析 data-refrence-index——chip 现在是真正的 React 节点。
  const handleCitationClick = useCallback(
    (index: number) => {
      onRefrence?.(index)
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

  useEffect(() => {
    if (!item.loading) return
    if (thinkStickToBottomRef.current && thinkStreamingRef.current) {
      thinkStreamingRef.current.scrollTop = thinkStreamingRef.current.scrollHeight
    }
  }, [item.loading, item.think])

  useEffect(() => {
    if (!item.loading) return
    if (contentStickToBottomRef.current && contentStreamingRef.current) {
      contentStreamingRef.current.scrollTop = contentStreamingRef.current.scrollHeight
    }
  }, [item.loading, item.content])

  const handleThinkStreamingScroll = useCallback(() => {
    const node = thinkStreamingRef.current
    if (!node) return
    const distanceToBottom = node.scrollHeight - node.scrollTop - node.clientHeight
    thinkStickToBottomRef.current = distanceToBottom <= 24
  }, [])

  const handleContentStreamingScroll = useCallback(() => {
    const node = contentStreamingRef.current
    if (!node) return
    const distanceToBottom = node.scrollHeight - node.scrollTop - node.clientHeight
    contentStickToBottomRef.current = distanceToBottom <= 24
  }, [])

  return (
    <div className={styles['chat-message-result']}>
      {item.think ? (
        item.loading ? (
          <div
            ref={thinkStreamingRef}
            onScroll={handleThinkStreamingScroll}
            className={classNames(
              styles['chat-message-result__think'],
              styles['chat-message-result__streaming'],
              styles['chat-message-result__streaming-window'],
              styles['chat-message-result__streaming-window--thinking'],
            )}
          >
            {item.think}
          </div>
        ) : (
          <Markdown
            className={classNames(
              styles['chat-message-result__think'],
              styles['chat-message-result__md'],
              styles['chat-message-result__streaming-window'],
              styles['chat-message-result__streaming-window--thinking'],
            )}
            value={item.think}
            references={item.reference}
            onCitationClick={handleCitationClick}
          />
        )
      ) : null}

      {item.content ? (
        item.loading ? (
          <div
            ref={contentStreamingRef}
            onScroll={handleContentStreamingScroll}
            className={classNames(
              styles['chat-message-result__streaming'],
              styles['chat-message-result__streaming-window'],
            )}
          >
            {item.content}
          </div>
        ) : (
          <Markdown
            className={styles['chat-message-result__md']}
            value={item.content}
            references={item.reference}
            onCitationClick={handleCitationClick}
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
              {elapsedLabel ? (
                <span className={styles['elapsed']}> · {elapsedLabel}</span>
              ) : null}
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

            <Tooltip title="有帮助">
              <Button
                variant="text"
                color="primary"
                shape="circle"
                size="small"
                style={{ color: feedback === 'thumbs_up' ? 'var(--ant-color-primary)' : undefined }}
                onClick={() => onFeedback?.('thumbs_up')}
              >
                <LikeOutlined />
              </Button>
            </Tooltip>

            <Tooltip title="无帮助">
              <Button
                variant="text"
                color="primary"
                shape="circle"
                size="small"
                style={{ color: feedback === 'thumbs_down' ? 'var(--ant-color-primary)' : undefined }}
                onClick={() => onFeedback?.('thumbs_down')}
              >
                <DislikeOutlined />
              </Button>
            </Tooltip>

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
