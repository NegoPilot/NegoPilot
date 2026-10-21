<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { createConversation, getConversation, sendMessage } from '@/api/conversations'
import { getSupplier } from '@/api/suppliers'
import type { ConversationSnapshot } from '@/api/types'
import ChatMessage from '@/components/ChatMessage.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorNotice from '@/components/ErrorNotice.vue'

const props = defineProps<{ conversationId?: string }>()
const router = useRouter()

// supplier-chat v0.2 계약이 정한 정규 질문 3개. 그 밖의 질문은 NEEDS_INPUT으로 돌아온다
const SUPPORTED_QUERIES = ['이 회사에 대해 알려줘', '이 회사의 재무 상태를 알려줘', '납기와 품질은 어때?']
const QUERY_MAX = 4000

const snapshot = ref<ConversationSnapshot | null>(null)
const supplierName = ref<string | null>(null)
const loading = ref(false)
const loadError = ref<unknown>(null)

const draft = ref('')
const sending = ref(false)
const pendingQuery = ref<string | null>(null)
const sendError = ref<unknown>(null)
const restarting = ref(false)
const listEnd = ref<HTMLElement | null>(null)

const supplierId = computed(() => snapshot.value?.context.selected_supplier_ids[0] ?? null)

const suggestions = computed(() => {
  const messages = snapshot.value?.messages ?? []
  if (messages.length === 0) return SUPPORTED_QUERIES
  return []
})

const trimmedDraft = computed(() => draft.value.trim())
const canSend = computed(
  () => !sending.value && trimmedDraft.value.length > 0 && [...trimmedDraft.value].length <= QUERY_MAX,
)

async function load() {
  snapshot.value = null
  supplierName.value = null
  loadError.value = null
  sendError.value = null
  if (!props.conversationId) return
  loading.value = true
  try {
    snapshot.value = await getConversation(props.conversationId)
    scrollToEnd()
  } catch (e) {
    loadError.value = e
  } finally {
    loading.value = false
  }
  // 헤더용 이름. 실패해도 대화는 쓸 수 있다
  if (supplierId.value) {
    try {
      supplierName.value = (await getSupplier(supplierId.value)).name
    } catch {
      supplierName.value = null
    }
  }
}

watch(() => props.conversationId, load, { immediate: true })

function scrollToEnd() {
  nextTick(() => listEnd.value?.scrollIntoView({ block: 'end' }))
}

async function send(text: string) {
  const query = text.trim()
  if (!props.conversationId || !snapshot.value || sending.value || !query) return
  sending.value = true
  sendError.value = null
  pendingQuery.value = query
  draft.value = ''
  scrollToEnd()
  try {
    const res = await sendMessage(props.conversationId, query)
    // Spring은 성공한 질문과 답을 함께 저장한다. 화면도 같은 순서로 붙인다
    snapshot.value.messages.push(
      { message_id: `local-${res.request_id}`, role: 'user', content: query, response: null },
      { message_id: res.response_id, role: 'assistant', content: res.presentation.text, response: res },
    )
  } catch (e) {
    // 실패하면 Spring도 저장하지 않으므로 입력을 되돌려 다시 보낼 수 있게 한다
    sendError.value = e
    draft.value = query
  } finally {
    sending.value = false
    pendingQuery.value = null
    scrollToEnd()
  }
}

function onKeydown(event: KeyboardEvent) {
  // 한글 조합 중 Enter는 글자 확정이라 보내지 않는다
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  if (canSend.value) send(draft.value)
}

async function startNewConversation() {
  if (!supplierId.value || restarting.value) return
  restarting.value = true
  try {
    const next = await createConversation(supplierId.value)
    await router.push({ name: 'chat', params: { conversationId: next.conversation_id } })
  } catch (e) {
    sendError.value = e
  } finally {
    restarting.value = false
  }
}
</script>

<template>
  <main class="page chat-page">
    <EmptyState v-if="!conversationId" title="대화할 협력사를 먼저 고르세요">
      협력사 상세 화면에서 'AI에게 이 회사 질문하기'를 누르면 대화가 시작됩니다.
      <template #action>
        <RouterLink :to="{ name: 'supplier-search' }" class="btn btn-primary">협력사 탐색</RouterLink>
      </template>
    </EmptyState>

    <p v-else-if="loading" aria-live="polite">대화를 불러오는 중…</p>

    <section v-else-if="loadError" class="panel">
      <ErrorNotice :error="loadError" :retry="load" />
      <p class="panel-body"><RouterLink :to="{ name: 'supplier-search' }">협력사 탐색으로 돌아가기</RouterLink></p>
    </section>

    <template v-else-if="snapshot">
      <header class="chat-head">
        <div>
          <span class="muted">대화 중인 협력사</span>
          <h1>
            <RouterLink v-if="supplierId" :to="{ name: 'supplier-detail', params: { supplierId } }">
              {{ supplierName ?? supplierId }}
            </RouterLink>
          </h1>
        </div>
        <button class="btn" type="button" :disabled="restarting || sending" @click="startNewConversation">새 대화</button>
      </header>

      <section class="panel chat-panel" aria-label="대화">
        <ol class="messages" aria-live="polite">
          <ChatMessage
            v-for="m in snapshot.messages"
            :key="m.message_id"
            :message="m"
            :conversation-id="snapshot.conversation_id"
            :disabled="sending"
            @option="send"
          />
          <li v-if="pendingQuery" class="pending-user">{{ pendingQuery }}</li>
          <li v-if="sending" class="typing muted">답변을 준비하고 있습니다…</li>
        </ol>
        <div ref="listEnd" />

        <div v-if="suggestions.length" class="suggestions" role="group" aria-label="이렇게 물어보세요">
          <button v-for="q in suggestions" :key="q" class="chip" type="button" :disabled="sending" @click="send(q)">
            {{ q }}
          </button>
        </div>

        <ErrorNotice v-if="sendError" :error="sendError" />

        <form class="composer" @submit.prevent="send(draft)">
          <label for="chat-input" class="sr-only">질문</label>
          <textarea
            id="chat-input"
            v-model="draft"
            rows="2"
            :maxlength="QUERY_MAX"
            placeholder="이 협력사에 대해 물어보세요 (Enter 보내기, Shift+Enter 줄바꿈)"
            :disabled="sending"
            @keydown="onKeydown"
          />
          <button class="btn btn-primary" type="submit" :disabled="!canSend">{{ sending ? '보내는 중…' : '보내기' }}</button>
        </form>
      </section>
    </template>
  </main>
</template>

<style scoped>
.chat-page {
  max-width: 900px;
}
.chat-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
}
.chat-head h1 {
  font-size: 20px;
  margin-top: 2px;
}
.chat-head h1 a {
  color: var(--text);
  text-decoration: none;
}
.chat-head h1 a:hover {
  color: var(--primary);
  text-decoration: underline;
}
.chat-panel {
  display: flex;
  flex-direction: column;
}
.messages {
  margin: 0;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 240px;
}
.pending-user {
  list-style: none;
  align-self: flex-end;
  max-width: 78%;
  padding: 10px 14px;
  background: #eef0f2;
  border-radius: var(--radius);
  opacity: 0.7;
  white-space: pre-line;
}
.typing {
  list-style: none;
}
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 20px 12px;
}
.chip {
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  background: var(--surface);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.chip:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding: 12px 20px 16px;
  border-top: 1px solid var(--border);
}
.composer textarea {
  flex: 1;
  min-width: 0;
}
.composer .btn {
  height: 44px;
}
</style>
