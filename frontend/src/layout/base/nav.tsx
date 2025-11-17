import * as api from '@/api'
import { sessionActions, sessionState } from '@/store/session'
import { useRequest } from 'ahooks'
import { DeleteOutlined } from '@ant-design/icons'
import { Button, Collapse, Popconfirm, message } from 'antd'
import dayjs from 'dayjs'
import { MouseEvent, useCallback, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import classNames from 'classnames'
import './nav.scss'

export function Nav() {
  const navigate = useNavigate()
  const location = useLocation()

  const session = useSnapshot(sessionState)

  const handleEnterSession = useCallback(
    (sessionId: string) => {
      navigate(`/chat/${sessionId}`)
    },
    [navigate],
  )

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await api.session.remove(
          { sessionId },
          {
            loading: true,
          },
        )
        sessionActions.remove(sessionId)
        message.success('会话已删除')
        if (window.location.pathname.includes(`/chat/${sessionId}`)) {
          navigate('/repository')
        }
      } catch (error: any) {
        const detail =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message
        message.error(detail ? `删除会话失败：${detail}` : '删除会话失败')
      }
    },
    [],
  )

  const stopPropagation = useCallback((event: MouseEvent<HTMLElement>) => {
    event.stopPropagation()
  }, [])

  useRequest(
    async () => {
      const { data } = await api.session.list(
        {},
        {
          loading: session.list.length ? false : true,
        },
      )
      return data
    },
    {
      refreshDeps: [sessionState.updateKey],
      onSuccess(data) {
        sessionActions.setList(data?.sessions || [])
      },
    },
  )

  const items = useMemo(
    () => [
      {
        key: '1',
        label: '历史对话',
        children: (
          <div>
            {session.list?.map((item) => {
              const isActive = location.pathname === `/chat/${item.session_id}`
              return (
                <div
                  className={classNames('base-layout-nav__item', {
                    'base-layout-nav__item--active': isActive,
                  })}
                  key={item.session_id}
                >
                  <div
                    className="base-layout-nav__item-main"
                    onClick={() => handleEnterSession(item.session_id)}
                  >
                    <div className="time">
                      {dayjs(item.created_at).format('HH:mm YYYY/MM/DD')}
                    </div>
                    <div className="title">{item.session_name}</div>
                  </div>
                  <div className="base-layout-nav__item-actions">
                    <Popconfirm
                      title="确认删除该对话？"
                      description="删除后不可恢复，将同时清空会话内消息。"
                      onConfirm={() => handleDeleteSession(item.session_id)}
                    >
                      <Button
                        type="text"
                        danger
                        size="small"
                        onClick={stopPropagation}
                        icon={<DeleteOutlined />}
                      />
                    </Popconfirm>
                  </div>
                </div>
              )
            })}
          </div>
        ),
      },
    ],
    [session.list, location.pathname, handleEnterSession, handleDeleteSession, stopPropagation],
  )

  return (
    <div className="base-layout-nav">
      <Collapse items={items} accordion />
    </div>
  )
}
