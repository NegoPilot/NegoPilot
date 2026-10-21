<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { deleteConversation, listConversations, renameConversation } from '@/api/conversations'
import type { ConversationSummary } from '@/api/types'
import AiAssistantPanel from '@/components/AiAssistantPanel.vue'
import { describeError } from '@/utils/errors'

const assistant = ref<InstanceType<typeof AiAssistantPanel> | null>(null)
const route = useRoute()
const router = useRouter()
const conversationId = computed(() => typeof route.params.conversationId === 'string' ? route.params.conversationId : null)
const initialQuestion = computed(() => typeof route.query.q === 'string' ? route.query.q : '')
const items = ref<ConversationSummary[]>([])
const nextCursor = ref<string | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const editing = ref(false)
const title = ref('')
const deleting = ref(false)
const saving = ref(false)
let listEpoch = 0
let listController: AbortController | null = null
const selectedTitle = computed(() => items.value.find((item) => item.conversation_id === conversationId.value)?.title ?? '현재 대화')
async function loadList(more = false) {
  listController?.abort()
  const controller = new AbortController()
  listController = controller
  const token = ++listEpoch
  loading.value = true
  error.value = null
  try {
    const page = await listConversations(more ? nextCursor.value : null, controller.signal)
    if (token !== listEpoch) return
    const known = new Set(more ? items.value.map((item) => item.conversation_id) : [])
    items.value = [...(more ? items.value : []), ...page.items.filter((item) => !known.has(item.conversation_id))]
    nextCursor.value = page.next_cursor
  } catch (failure) { if (token === listEpoch) error.value = failure }
  finally { if (token === listEpoch) loading.value = false }
}
onMounted(() => loadList())
onBeforeUnmount(() => { listEpoch += 1; listController?.abort() })
function open(id: string | null) {
  editing.value = false
  deleting.value = false
  void router.replace({ name: 'chat', params: id ? { conversationId: id } : {} })
}
function editTitle() { title.value = selectedTitle.value; editing.value = true; deleting.value = false }
async function saveTitle() {
  const id = conversationId.value
  if (!id || saving.value || !title.value.trim() || [...title.value.trim()].length > 100) return
  saving.value = true
  try { await renameConversation(id, title.value.trim()); editing.value = false; await loadList() }
  catch (failure) { error.value = failure }
  finally { saving.value = false }
}
async function remove() {
  const id = conversationId.value
  if (!id || saving.value) return
  saving.value = true
  try { await deleteConversation(id); if (conversationId.value === id) open(null); await loadList() }
  catch (failure) { error.value = failure }
  finally { saving.value = false; deleting.value = false }
}
</script>

<template>
  <header class="page-header"><h1>AI 대화</h1><span class="muted">내 계정에 저장되는 개인 대화</span></header>
  <main class="page-body chat-workspace">
    <aside class="panel history" aria-label="내 대화 기록">
      <div class="history-head"><h2>대화 기록</h2><button class="btn btn-primary" type="button" @click="assistant?.newConversation()">새 대화</button></div>
      <button class="link-btn" type="button" :disabled="loading" @click="loadList()">목록 새로고침</button>
      <p v-if="loading" class="muted" role="status">기록을 불러오는 중…</p>
      <p v-else-if="!items.length && !error" class="muted">아직 저장된 대화가 없습니다.</p>
      <p v-if="error" class="field-error" role="alert">{{ describeError(error).message }}</p>
      <ol class="history-list">
        <li v-for="item in items" :key="item.conversation_id">
          <RouterLink :to="{ name: 'chat', params: { conversationId: item.conversation_id } }" :class="{ selected: item.conversation_id === conversationId }" @click="editing = false; deleting = false">
            <strong>{{ item.title }}</strong><small>{{ new Date(item.updated_at).toLocaleString('ko-KR') }}</small>
          </RouterLink>
        </li>
      </ol>
      <button v-if="nextCursor" class="btn" type="button" :disabled="loading" @click="loadList(true)">대화 더 보기</button>
    </aside>
    <section class="chat-main" aria-label="대화 내용">
      <div v-if="conversationId" class="conversation-actions">
        <strong>{{ selectedTitle }}</strong>
        <button class="link-btn" type="button" :disabled="saving" @click="editTitle">제목 변경</button>
        <button class="link-btn" type="button" :disabled="saving" @click="deleting = true; editing = false">삭제</button>
      </div>
      <form v-if="editing" class="edit-title" @submit.prevent="saveTitle">
        <label for="conversation-title">대화 제목</label><input id="conversation-title" v-model="title" maxlength="100" />
        <button class="btn" :disabled="saving || !title.trim()">저장</button><button class="btn" type="button" @click="editing = false">취소</button>
      </form>
      <div v-if="deleting" class="delete-confirm" role="alert">
        <p>이 대화와 모든 메시지를 영구 삭제합니다.</p>
        <button class="btn" type="button" :disabled="saving" @click="remove">삭제하기</button>
        <button class="btn" type="button" @click="deleting = false">취소</button>
      </div>
      <AiAssistantPanel ref="assistant" :supplier="null" :initial-question="initialQuestion" :rfq="null" :conversation-id="conversationId" :closable="false" :back-path="route.fullPath" @open="open" @changed="loadList()" />
    </section>
  </main>
</template>

<style scoped>
.chat-workspace { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 18px; align-items: start; }
.history { padding: 16px; display: flex; flex-direction: column; gap: 12px; max-height: calc(100vh - 120px); overflow: auto; }
.history-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.history-head h2 { font-size: 15px; }
.history-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.history-list a { display: flex; flex-direction: column; gap: 6px; padding: 12px; border: 1px solid transparent; border-radius: var(--radius); text-decoration: none; color: var(--text); overflow-wrap: anywhere; }
.history-list a:hover, .history-list a.selected { border-color: var(--primary); background: var(--surface-2); }
.history-list small { color: var(--text-3); }
.chat-main { min-width: 0; }
.conversation-actions, .edit-title { display: flex; align-items: center; gap: 12px; padding: 8px 0 14px; flex-wrap: wrap; }
.conversation-actions strong { margin-right: auto; overflow-wrap: anywhere; }
.edit-title input { flex: 1; min-width: 150px; }
.delete-confirm { padding: 12px; margin-bottom: 12px; border: 1px solid var(--border); background: var(--surface); }
@media (max-width: 900px) { .chat-workspace { grid-template-columns: minmax(0, 1fr); } .history { max-height: 260px; } }
</style>
