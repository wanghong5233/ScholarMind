import { userState } from '@/store/user'
import { Avatar } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import { useSnapshot } from 'valtio'
import './footer.scss'

export function Footer() {
  const user = useSnapshot(userState)

  return (
    <div className="base-layout-footer">
      <div className="base-layout-footer__content">
        <Avatar className="base-layout-footer__avatar">
          {user.username?.slice(0, 1).toUpperCase()}
        </Avatar>
        <span className="base-layout-footer__username">{user.username}</span>
        <MoreOutlined className="base-layout-footer__more" />
      </div>
    </div>
  )
}
