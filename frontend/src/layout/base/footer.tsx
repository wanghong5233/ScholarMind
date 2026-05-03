import { sessionActions } from '@/store/session'
import { userActions, userState } from '@/store/user'
import { buildLoginPath } from '@/utils/auth'
import { isDemoUser } from '@/utils/demo'
import { MoreOutlined } from '@ant-design/icons'
import { Avatar, Button, Dropdown, type MenuProps, Tag } from 'antd'
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import './footer.scss'

export function Footer() {
  const user = useSnapshot(userState)
  const navigate = useNavigate()
  const username = user.username || 'unknown'
  const demoUser = isDemoUser(user.username)
  const redirectPath =
    typeof window === 'undefined'
      ? '/chat'
      : `${window.location.pathname}${window.location.search || ''}`

  const menuItems = useMemo<MenuProps['items']>(() => {
    const items: NonNullable<MenuProps['items']> = [
      {
        key: 'account',
        label: `当前账号：${username}`,
        disabled: true,
      },
    ]
    if (demoUser) {
      items.push({
        key: 'demo',
        label: '当前为 Demo 体验账号',
        disabled: true,
      })
    }
    items.push({ type: 'divider' })
    items.push({
      key: 'logout',
      label: '退出登录',
      danger: true,
    })
    return items
  }, [demoUser, username])

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    if (key !== 'logout') return
    userActions.clear()
    sessionActions.clear()
    window.$app.message.success('已退出登录')
    navigate('/chat', { replace: true })
  }

  if (!user.token) {
    return (
      <div className="base-layout-footer">
        <div className="base-layout-footer__guest">
          <Button
            type="primary"
            block
            onClick={() => navigate(buildLoginPath(redirectPath))}
          >
            登录
          </Button>
          <Button
            block
            onClick={() => navigate(`${buildLoginPath(redirectPath)}&tab=register`)}
          >
            注册
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="base-layout-footer">
      <div className="base-layout-footer__content">
        <Avatar className="base-layout-footer__avatar">
          {user.username?.slice(0, 1).toUpperCase()}
        </Avatar>
        <div className="base-layout-footer__user-meta">
          <span className="base-layout-footer__username">{user.username}</span>
          {demoUser && (
            <Tag className="base-layout-footer__demo-tag" color="gold">
              Demo
            </Tag>
          )}
        </div>
        <Dropdown
          placement="topRight"
          trigger={['click']}
          menu={{
            items: menuItems,
            onClick: handleMenuClick,
          }}
        >
          <button type="button" className="base-layout-footer__more-button" aria-label="用户菜单">
            <MoreOutlined className="base-layout-footer__more" />
          </button>
        </Dropdown>
      </div>
    </div>
  )
}
