import { readonly, ref } from 'vue'

// AI 대화는 별도 화면 없이 업무 화면 오른쪽 패널로 연다. 닫았다 열어도 같은 대화를 이어간다.
const open = ref(false)
const question = ref('')
const conversation = ref<string | null>(null)

export const assistantOpen = readonly(open)
export const assistantQuestion = readonly(question)
export const assistantConversationId = readonly(conversation)

export function openAssistant(initialQuestion = '') {
  question.value = initialQuestion.trim()
  open.value = true
}

export function closeAssistant() {
  open.value = false
}

export function setAssistantConversation(id: string | null) {
  conversation.value = id
}
