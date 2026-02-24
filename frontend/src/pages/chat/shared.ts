import { PageTransportKey } from '@/utils'

export type ChatEnterData = {
  message: string
  mode?: 'chat' | 'deep'
  deepResearchPreset?: 'quick' | 'medium' | 'deep'
  useRag?: boolean
  ragMode?: 'fast' | 'deep'
  userKnowledgeBaseId?: number | null
  pendingAttachments?: API.ChatAttachment[]
  pendingFiles?: File[]
  imageAttachments?: API.ChatImageAttachment[]
}

export const transportToChatEnter = Symbol() as PageTransportKey<{
  data: ChatEnterData
}>

let id = 0

export const createChatId = () => {
  return ++id
}

export function createChatIdText(id: number) {
  return `chat-item-${id}`
}
