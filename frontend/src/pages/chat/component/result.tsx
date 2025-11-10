import IconCopy from '@/assets/chat/copy.svg'
import IconRefresh from '@/assets/chat/refresh.svg'
import IconShare from '@/assets/chat/share.svg'
import IconTip from '@/assets/chat/tip.svg'
import Markdown from '@/components/markdown'
import { ArrowRightOutlined } from '@ant-design/icons'
import { Button, Dropdown } from 'antd'
import classNames from 'classnames'
import dayjs from 'dayjs'
import { useCallback, useMemo } from 'react'
import styles from './result.module.scss'

export function Result(props: {
  item: API.ChatItem
  isEnd?: boolean
  onSend?: (text: string) => void
  onRefrence?: (index: number) => void
}) {
  const { item, isEnd, onSend, onRefrence } = props

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
