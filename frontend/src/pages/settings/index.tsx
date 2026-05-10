import { sessionActions } from '@/store/session'
import { userActions, userState } from '@/store/user'
import {
  CUSTOM_LLM_LEGACY_STORAGE_KEY,
  CUSTOM_LLM_PROFILES_UPDATED_EVENT,
  CUSTOM_LLM_PROVIDER_TYPE,
  CUSTOM_LLM_STORAGE_KEY,
  createCustomLlmProfileId,
  type CustomLlmProfile,
  isCustomLlmProfileReady,
  loadCustomLlmProfiles,
  normalizeCustomLlmProfile,
  saveCustomLlmProfiles,
} from '@/utils/custom-llm'
import { clearCustomModelLocalCache } from '@/utils/user-center'
import {
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  LockOutlined,
  LogoutOutlined,
  RightOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { Avatar, Button, Form, Input, Modal, Popconfirm, Space, Switch, Tag, Typography, message } from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import './index.scss'

type SettingsSectionKey = 'account' | 'models' | 'security'
type CustomModelFormValues = {
  providerLabel?: string
  baseUrl: string
  model: string
  apiKey: string
  allowFallback?: boolean
  enabled?: boolean
}

const SETTINGS_SECTIONS: Array<{
  key: SettingsSectionKey
  label: string
  desc: string
  icon: React.ReactNode
}> = [
  {
    key: 'account',
    label: '账户',
    desc: '基础信息与入口',
    icon: <UserOutlined />,
  },
  {
    key: 'models',
    label: '自定义模型',
    desc: '多模型配置与管理',
    icon: <PlusOutlined />,
  },
  {
    key: 'security',
    label: '安全与隐私',
    desc: '本机密钥与登录会话',
    icon: <LockOutlined />,
  },
]

const normalizeSection = (value: string | null): SettingsSectionKey => {
  if (value === 'models') return 'models'
  if (value === 'security') return 'security'
  return 'account'
}

export default function SettingsPage() {
  const navigate = useNavigate()
  const user = useSnapshot(userState)
  const username = user.username || 'unknown'
  const [searchParams, setSearchParams] = useSearchParams()
  const activeSection = normalizeSection(searchParams.get('section'))
  const [customProfiles, setCustomProfiles] = useState<CustomLlmProfile[]>(() =>
    loadCustomLlmProfiles(),
  )
  const [customModelEditorOpen, setCustomModelEditorOpen] = useState(false)
  const [editingCustomModelId, setEditingCustomModelId] = useState<string | null>(null)
  const [customModelForm] = Form.useForm<CustomModelFormValues>()

  const activeSectionMeta = useMemo(
    () => SETTINGS_SECTIONS.find((item) => item.key === activeSection) || SETTINGS_SECTIONS[0],
    [activeSection],
  )
  const customProfilesSummary = useMemo(() => {
    const total = customProfiles.length
    const enabled = customProfiles.filter((item) => item.enabled).length
    const ready = customProfiles.filter((item) => item.enabled && isCustomLlmProfileReady(item)).length
    return { total, enabled, ready }
  }, [customProfiles])

  const syncProfilesFromStorage = useCallback(() => {
    setCustomProfiles(loadCustomLlmProfiles())
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const onStorage = (event: StorageEvent) => {
      if (
        event.key &&
        event.key !== CUSTOM_LLM_STORAGE_KEY &&
        event.key !== CUSTOM_LLM_LEGACY_STORAGE_KEY
      ) {
        return
      }
      syncProfilesFromStorage()
    }
    const onProfilesUpdated = () => {
      syncProfilesFromStorage()
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(CUSTOM_LLM_PROFILES_UPDATED_EVENT, onProfilesUpdated)
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(CUSTOM_LLM_PROFILES_UPDATED_EVENT, onProfilesUpdated)
    }
  }, [syncProfilesFromStorage])

  const handleSwitchSection = (section: SettingsSectionKey) => {
    const next = new URLSearchParams(searchParams)
    next.set('section', section)
    setSearchParams(next, { replace: true })
  }

  const handleLogout = () => {
    userActions.clear()
    sessionActions.clear()
    message.success('已退出登录')
    navigate('/chat', { replace: true })
  }

  const handleClearCustomModelCache = () => {
    try {
      clearCustomModelLocalCache()
      message.success('已清除本机自定义模型密钥缓存')
    } catch {
      message.error('清除缓存失败，请重试')
    }
  }

  const openCreateCustomModel = () => {
    setEditingCustomModelId(null)
    customModelForm.setFieldsValue({
      providerLabel: '自定义模型',
      baseUrl: '',
      model: '',
      apiKey: '',
      allowFallback: false,
      enabled: true,
    })
    setCustomModelEditorOpen(true)
  }

  const openEditCustomModel = (profile: CustomLlmProfile) => {
    setEditingCustomModelId(profile.id)
    customModelForm.setFieldsValue({
      providerLabel: profile.providerLabel,
      baseUrl: profile.baseUrl,
      model: profile.model,
      apiKey: profile.apiKey,
      allowFallback: profile.allowFallback,
      enabled: profile.enabled,
    })
    setCustomModelEditorOpen(true)
  }

  const closeCustomModelEditor = () => {
    setCustomModelEditorOpen(false)
    setEditingCustomModelId(null)
  }

  const handleSaveCustomModel = async () => {
    const values = await customModelForm.validateFields()
    const normalized = normalizeCustomLlmProfile({
      id: editingCustomModelId || createCustomLlmProfileId(),
      providerType: CUSTOM_LLM_PROVIDER_TYPE,
      providerLabel: String(values.providerLabel || '').trim() || '自定义模型',
      baseUrl: String(values.baseUrl || '').trim().replace(/\/+$/, ''),
      model: String(values.model || '').trim(),
      apiKey: String(values.apiKey || '').trim(),
      allowFallback: Boolean(values.allowFallback),
      enabled: values.enabled !== false,
    })
    if (!normalized) {
      message.error('模型配置无效，请检查输入项')
      return
    }
    const nextProfiles = editingCustomModelId
      ? customProfiles.map((item) => (item.id === editingCustomModelId ? normalized : item))
      : [normalized, ...customProfiles]
    saveCustomLlmProfiles(nextProfiles)
    setCustomProfiles(nextProfiles)
    setCustomModelEditorOpen(false)
    setEditingCustomModelId(null)
    message.success(editingCustomModelId ? '模型配置已更新' : '模型配置已新增')
  }

  const handleDeleteCustomModel = (profileId: string) => {
    const nextProfiles = customProfiles.filter((item) => item.id !== profileId)
    saveCustomLlmProfiles(nextProfiles)
    setCustomProfiles(nextProfiles)
    message.success('模型配置已删除')
  }

  return (
    <div className="settings-page">
      <div className="settings-page__header">
        <Typography.Title level={3}>用户中心</Typography.Title>
      </div>

      <div className="settings-page__layout">
        <aside className="settings-page__sidebar">
          {SETTINGS_SECTIONS.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`settings-page__sidebar-item ${
                activeSection === item.key ? 'settings-page__sidebar-item--active' : ''
              }`}
              onClick={() => handleSwitchSection(item.key)}
            >
              <span className="settings-page__sidebar-item-icon">{item.icon}</span>
              <span className="settings-page__sidebar-item-main">
                <span className="settings-page__sidebar-item-title">{item.label}</span>
                <span className="settings-page__sidebar-item-desc">{item.desc}</span>
              </span>
              <RightOutlined className="settings-page__sidebar-item-arrow" />
            </button>
          ))}
        </aside>

        <main className="settings-page__panel">
          <div className="settings-page__panel-header">
            <span className="settings-page__panel-title">{activeSectionMeta.label}</span>
            <span className="settings-page__panel-desc">{activeSectionMeta.desc}</span>
          </div>

          {activeSection === 'account' ? (
            <div className="settings-page__card">
              <div className="settings-page__identity">
                <Avatar size={48} className="settings-page__avatar">
                  {username.slice(0, 1).toUpperCase()}
                </Avatar>
                <div className="settings-page__identity-meta">
                  <span className="settings-page__identity-name">{username}</span>
                  <Tag color="success">已登录</Tag>
                </div>
              </div>

              <div className="settings-page__row">
                <span>快捷入口</span>
                <Space wrap>
                  <Button onClick={() => navigate('/chat')}>Deep Chat</Button>
                  <Button onClick={() => navigate('/repository')}>知识库</Button>
                  <Button onClick={() => navigate('/doc-studio')}>Doc Studio</Button>
                </Space>
              </div>
            </div>
          ) : null}

          {activeSection === 'models' ? (
            <div className="settings-page__card">
              <div className="settings-page__row">
                <span>配置概览</span>
                <Space wrap>
                  <Tag>总数 {customProfilesSummary.total}</Tag>
                  <Tag color="blue">已启用 {customProfilesSummary.enabled}</Tag>
                  <Tag color="green">可用 {customProfilesSummary.ready}</Tag>
                </Space>
              </div>

              <div className="settings-page__models-header">
                <span className="settings-page__row-title">模型列表</span>
                <Button type="primary" icon={<PlusOutlined />} onClick={openCreateCustomModel}>
                  新增模型
                </Button>
              </div>

              {customProfiles.length === 0 ? (
                <Typography.Paragraph className="settings-page__hint">
                  暂无自定义模型，请新增后在 Deep Chat / Doc Studio 中选择使用。
                </Typography.Paragraph>
              ) : (
                <div className="settings-page__models-list">
                  {customProfiles.map((profile) => {
                    const ready = isCustomLlmProfileReady(profile)
                    return (
                      <div className="settings-page__model-item" key={profile.id}>
                        <div className="settings-page__model-main">
                          <div className="settings-page__model-title-line">
                            <span className="settings-page__model-title">
                              {profile.providerLabel || '自定义模型'} · {profile.model || '未配置模型 ID'}
                            </span>
                            <Space size={6}>
                              {profile.enabled ? <Tag color="blue">启用</Tag> : <Tag>停用</Tag>}
                              {ready ? <Tag color="green">可用</Tag> : <Tag color="warning">未完成</Tag>}
                            </Space>
                          </div>
                          <span className="settings-page__model-meta">
                            {profile.baseUrl || '未配置 Base URL'}
                          </span>
                        </div>
                        <Space>
                          <Button icon={<EditOutlined />} onClick={() => openEditCustomModel(profile)}>
                            编辑
                          </Button>
                          <Popconfirm
                            title="确认删除该模型配置？"
                            description="删除后 Deep Chat / Doc Studio 将不再显示该模型。"
                            okText="删除"
                            okButtonProps={{ danger: true }}
                            cancelText="取消"
                            onConfirm={() => handleDeleteCustomModel(profile.id)}
                          >
                            <Button danger icon={<DeleteOutlined />}>
                              删除
                            </Button>
                          </Popconfirm>
                        </Space>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          ) : null}

          {activeSection === 'security' ? (
            <div className="settings-page__card">
              <div className="settings-page__row settings-page__row--stack">
                <span className="settings-page__row-title">本机模型密钥缓存</span>
                <Typography.Paragraph className="settings-page__hint">
                  自定义模型 API Key 仅在当前浏览器本地存储，不写入服务端。建议在共享设备上使用后清理。
                </Typography.Paragraph>
                <Button icon={<DeleteOutlined />} onClick={handleClearCustomModelCache}>
                  清除本机自定义模型密钥缓存
                </Button>
              </div>

              <div className="settings-page__danger-zone">
                <span className="settings-page__danger-title">危险操作</span>
                <Popconfirm
                  title="确认退出登录？"
                  description="退出后将清空当前会话登录状态。"
                  okText="退出登录"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={handleLogout}
                >
                  <Button danger icon={<LogoutOutlined />}>
                    退出登录
                  </Button>
                </Popconfirm>
              </div>
            </div>
          ) : null}
        </main>
      </div>

      <Modal
        title={editingCustomModelId ? '编辑自定义模型' : '新增自定义模型'}
        open={customModelEditorOpen}
        onCancel={closeCustomModelEditor}
        onOk={() => {
          void handleSaveCustomModel()
        }}
        okText="保存"
        cancelText="取消"
        destroyOnClose
        width={620}
      >
        <Form form={customModelForm} layout="vertical">
          <Form.Item
            name="providerLabel"
            label="提供方名称"
            tooltip="仅用于展示，例如 OpenRouter / DeepSeek / vLLM"
          >
            <Input placeholder="例如：OpenRouter" maxLength={80} />
          </Form.Item>
          <Form.Item
            name="baseUrl"
            label="Base URL"
            rules={[
              { required: true, message: '请输入 Base URL' },
              {
                validator: async (_, value) => {
                  const text = String(value || '').trim()
                  if (!text) return
                  if (!/^https?:\/\//i.test(text)) {
                    return Promise.reject(new Error('Base URL 需以 http:// 或 https:// 开头'))
                  }
                },
              },
            ]}
          >
            <Input placeholder="https://api.example.com/v1" />
          </Form.Item>
          <Form.Item
            name="model"
            label="模型 ID"
            rules={[{ required: true, message: '请输入模型 ID' }]}
          >
            <Input placeholder="例如：gpt-4.1-mini / deepseek-chat / qwen3-32b" />
          </Form.Item>
          <Form.Item
            name="apiKey"
            label="API Key"
            rules={[{ required: true, message: '请输入 API Key' }]}
          >
            <Input.Password placeholder="输入 API Key" autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="allowFallback"
            label="失败时自动回退平台模型"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="enabled"
            label="启用该模型"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
