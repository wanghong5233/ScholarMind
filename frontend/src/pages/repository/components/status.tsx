import { Tag } from 'antd'
import Color from 'color'
import { useMemo } from 'react'

const map = {
  pending: {
    text: '排队中',
    color: '#909399',
  },
  parsing: {
    text: '解析中',
    color: '#409EFF',
  },
  ready: {
    text: '已完成',
    color: '#67C23A',
  },
  failed: {
    text: '解析失败',
    color: '#F56C6C',
  },
  // Legacy keys kept for backwards compat with anywhere that still passes
  // the pre-state-machine vocabulary.
  unparsed: {
    text: '未解析',
    color: '#909399',
  },
  cancel: {
    text: '已取消',
    color: '#E6A23C',
  },
  success: {
    text: '已完成',
    color: '#67C23A',
  },
}

export type DocumentStatusKey = keyof typeof map

export function Status(props: { status: DocumentStatusKey | string }) {
  const { status } = props
  const { text, color } = useMemo(() => {
    return (
      map[status as DocumentStatusKey] ?? {
        color: '#999',
        text: status,
      }
    )
  }, [status])

  const backgroundColor = useMemo(() => {
    return new Color(color).alpha(0.1).toString()
  }, [color])

  const borderColor = useMemo(() => {
    return new Color(color).alpha(0.3).toString()
  }, [color])

  return (
    <Tag style={{ borderColor, color, backgroundColor }}>{text}</Tag>
  )
}
