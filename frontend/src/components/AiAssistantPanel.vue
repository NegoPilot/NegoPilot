<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { createConversation, getConversation, getMessages, listConversations, sendMessage } from '@/api/conversations'
import { ApiError } from '@/api/client'
import type { ConversationSummary, GenerationResponse, StoredMessage } from '@/api/types'
import { describeError, isApiError } from '@/utils/errors'
import { parseSupplierEvidence, splitCitations } from '@/utils/evidence'
import { NO_DATA, formatKrw, formatPercent, formatPpm } from '@/utils/format'
import { provenanceLabel } from '@/utils/provenance'

// 기업 선택 props 없이 질문과 이전 대화로 답한다 (#64).
const props = defineProps<{
  conversationId?: string | null
  closable?: boolean
  /** 홈에서 넘어온 질문. 입력창에 채워 둔다 */
  initialQuestion: string
  rfq: string | null
  /** 근거 상세 탭의 '원래 화면으로 돌아가기' 주소 */
  backPath: string
}>()

const emit = defineEmits<{ close: []; open: [id: string | null]; changed: [] }>()
const router = useRouter()

const SUGGESTED_QUESTIONS = [
  '부산 협력사 찾아줘',
  '해솔정밀에 대해 알려줘',
  '해솔에 대해 알려줘',
  '하도급법상 대금 지급 기한은 며칠이야?',
  '결제 90일로 해도 돼?',
  'RFQ가 뭐야?',
]
const QUERY_MAX = 4000
interface PanelMessage {
  key: string
  role: 'user' | 'assistant'
  text: string
  response?: GenerationResponse
}
const conversationId = ref<string | null>(null)
const messages = ref<PanelMessage[]>([])
const draft = ref(props.initialQuestion)
const sending = ref(false)
const loading = ref(false)
const olderLoading = ref(false)
const loaded = ref(true)
const nextCursor = ref<string | null>(null)
const error = ref<unknown>(null)
const messageList = ref<HTMLOListElement | null>(null)
const input = ref<HTMLTextAreaElement | null>(null)
let epoch = 0
let pendingController: AbortController | null = null
let retry: { id: string; query: string; key: string } | null = null

// 이전 대화 목록 (#174). 대화 왼쪽 사이드바로 열고 닫는다. 열 때와 대화가 바뀔 때 첫 페이지를 다시 조회한다.
const historyOpen = ref(false)
const historyItems = ref<ConversationSummary[]>([])
const historyCursor = ref<string | null>(null)
const historyLoading = ref(false)
const historyError = ref<unknown>(null)
const historyRetryMore = ref(false)
let historyController: AbortController | null = null
const historyErrorText = computed(() => historyError.value ? describeError(historyError.value).message : null)
const updatedFormatter = new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function cancelHistory() {
  historyController?.abort()
  historyController = null
  historyLoading.value = false
}
async function loadHistory(more = false) {
  cancelHistory()
  const controller = new AbortController()
  historyController = controller
  historyLoading.value = true
  historyError.value = null
  historyRetryMore.value = more
  try {
    const page = await listConversations(more ? historyCursor.value : null, controller.signal)
    if (historyController !== controller) return
    // 첫 페이지는 화면을 비우지 않고 응답이 오면 통째로 바꾼다
    const kept = more ? historyItems.value : []
    const existing = new Set(kept.map((item) => item.conversation_id))
    historyItems.value = [...kept, ...page.items.filter((item) => !existing.has(item.conversation_id))]
    historyCursor.value = page.next_cursor
  } catch (failure) { if (historyController === controller) historyError.value = failure }
  finally { if (historyController === controller) { historyLoading.value = false; historyController = null } }
}
function toggleHistory() {
  historyOpen.value = !historyOpen.value
  if (historyOpen.value) void loadHistory()
  else cancelHistory()
}
function conversationChanged() {
  emit('changed')
  if (historyOpen.value) void loadHistory()
}
function selectConversation(id: string) {
  // 좁은 화면에서는 사이드바가 대화를 덮으므로 고른 뒤 닫는다
  if (window.matchMedia('(max-width: 760px)').matches) toggleHistory()
  if (id === conversationId.value) return
  void loadConversation(id)
  emit('open', id)
}
onBeforeUnmount(cancelHistory)

function cancelView() {
  epoch += 1
  pendingController?.abort()
  pendingController = null
  sending.value = false
  loading.value = false
  olderLoading.value = false
}
onBeforeUnmount(cancelView)
watch(() => props.initialQuestion, (question) => { if (question) draft.value = question })
function rows(items: StoredMessage[]): PanelMessage[] {
  return items.map((item) => ({ key: item.message_id, role: item.role, text: item.content, ...(item.response ? { response: item.response } : {}) }))
}
function scrollToEnd() {
  nextTick(() => { if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight })
}
async function loadConversation(id: string) {
  cancelView()
  const token = epoch
  const controller = new AbortController()
  pendingController = controller
  conversationId.value = id
  messages.value = []
  draft.value = ''
  retry = null
  error.value = null
  loading.value = true
  loaded.value = false
  nextCursor.value = null
  try {
    const snapshot = await getConversation(id, controller.signal)
    if (token !== epoch) return
    messages.value = rows(snapshot.messages)
    nextCursor.value = snapshot.next_cursor
    loaded.value = true
    scrollToEnd()
  } catch (failure) {
    if (token !== epoch) return
    // 지운 대화나 다른 회원의 대화면 입력창을 잠그지 않고 새 대화로 돌아간다
    if (isApiError(failure, 'CONVERSATION_NOT_FOUND')) newConversation()
    error.value = failure
  } finally { if (token === epoch) loading.value = false }
}
function newConversation() {
  cancelView()
  conversationId.value = null
  messages.value = []
  error.value = null
  nextCursor.value = null
  draft.value = ''
  retry = null
  loaded.value = true
  emit('open', null)
}
defineExpose({ newConversation })
watch(() => props.conversationId, (id) => {
  if ((id ?? null) === conversationId.value) return
  if (id) void loadConversation(id)
  else newConversation()
}, { immediate: true })

async function loadOlder() {
  const id = conversationId.value, cursor = nextCursor.value, token = epoch
  if (!id || !cursor || olderLoading.value) return
  olderLoading.value = true
  try {
    const page = await getMessages(id, cursor, pendingController?.signal)
    if (token !== epoch) return
    const height = messageList.value?.scrollHeight ?? 0
    const existing = new Set(messages.value.map((item) => item.key))
    messages.value = [...rows(page.messages).filter((item) => !existing.has(item.key)), ...messages.value]
    nextCursor.value = page.next_cursor
    nextTick(() => { if (messageList.value) messageList.value.scrollTop += messageList.value.scrollHeight - height })
  } catch (failure) { if (token === epoch) error.value = failure }
  finally { if (token === epoch) olderLoading.value = false }
}
const suggestions = computed(() => messages.value.length || loading.value || !loaded.value ? [] : SUGGESTED_QUESTIONS)
// 결과가 없을 때의 options는 Agent 검토 루프가 권하는 넓힌 검색이다 (agent/README.md 검토 루프)
const optionsTitle = (kind: string) => kind === 'no_result' ? '다른 조건으로 찾아보기' : '추천 답변'
const suggestionsDisabled = computed(() => sending.value || loading.value || olderLoading.value || !loaded.value)
const latestAssistantKey = computed(() => {
  const last = messages.value.at(-1)
  return last?.role === 'assistant' && last.response ? last.key : null
})
const canSend = computed(() => !sending.value && !loading.value && loaded.value && draft.value.trim().length > 0 && [...draft.value.trim()].length <= QUERY_MAX)
const errorText = computed(() => error.value ? describeError(error.value).message : null)
function fillDraft(text: string, messageKey?: string) {
  if (suggestionsDisabled.value || (messageKey !== undefined && messageKey !== latestAssistantKey.value)) return
  draft.value = text
  const token = epoch
  nextTick(() => {
    if (token !== epoch || suggestionsDisabled.value || !input.value) return
    input.value.focus()
    input.value.setSelectionRange(input.value.value.length, input.value.value.length)
  })
}
async function send(text: string) {
  const query = text.trim()
  if (!query || [...query].length > QUERY_MAX || sending.value || loading.value || !loaded.value) return
  const token = epoch
  const controller = new AbortController()
  pendingController = controller
  sending.value = true
  error.value = null
  draft.value = ''
  const userKey = crypto.randomUUID()
  messages.value.push({ key: userKey, role: 'user', text: query })
  scrollToEnd()
  try {
    if (!conversationId.value) {
      const snapshot = await createConversation(controller.signal)
      if (token !== epoch) return
      conversationId.value = snapshot.conversation_id
      emit('open', snapshot.conversation_id)
      conversationChanged()
    }
    const id = conversationId.value
    if (!retry || retry.id !== id || retry.query !== query) {
      retry = { id, query, key: crypto.randomUUID() }
    }
    const res = await sendMessage(id, query, retry.key, controller.signal)
    if (token !== epoch) return
    if (res.execution_status === 'FAILED') {
      throw new ApiError(200, res.error?.code ?? 'GENERATION_FAILED', 'Generation failed.', res.error?.retryable ?? false)
    }
    messages.value.push({ key: res.response_id, role: 'assistant', text: res.answer.text, response: res })
    retry = null
    conversationChanged()
  } catch (failure) {
    if (token !== epoch) return
    messages.value = messages.value.filter((item) => item.key !== userKey)
    error.value = failure
    draft.value = query
  } finally {
    if (token === epoch) { sending.value = false; scrollToEnd() }
  }
}

function onKeydown(event: KeyboardEvent) {
  // 한글 조합 중 Enter는 글자 확정이라 보내지 않는다
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  if (canSend.value) send(draft.value)
}

function evidenceHref(res: GenerationResponse, evidenceId: string): string {
  const query: Record<string, string> = { back: props.backPath }
  if (props.rfq) query.rfq = props.rfq
  return router.resolve({
    name: 'evidence-detail',
    params: { conversationId: res.conversation_id, responseId: res.response_id, evidenceId },
    query,
  }).href
}

function citationHref(res: GenerationResponse, n: number): string | null {
  const citation = res.answer.citations.find((c) => c.n === n)
  return citation ? evidenceHref(res, citation.evidence_id) : null
}

/** 인용한 협력사 근거에서 핵심 수치만 뽑는다 */
function supplierSummary(res: GenerationResponse) {
  const cited = new Set(res.answer.citations.map((c) => c.evidence_id))
  for (const evidence of res.answer.evidence) {
    if (!cited.has(evidence.id)) continue
    const s = parseSupplierEvidence(evidence)
    if (!s) continue
    const quotes = [...(s.recent_quotes ?? [])].sort((a, b) => b.quoted_at.localeCompare(a.quoted_at))
    return [
      `${s.name ?? s.supplier_id} 기본 정보: ${provenanceLabel(s.provenance.profile)} / 원출처: ${provenanceLabel(s.provenance.profile_origin)}`,
      `불량률: ${formatPpm(s.quality.defect_ppm)} (${provenanceLabel(s.provenance.quality)})`,
      `납기 준수율: ${formatPercent(s.quality.on_time_delivery_rate)} (${provenanceLabel(s.provenance.quality)})`,
      `최근 견적: ${quotes[0] ? formatKrw(quotes[0].unit_price_krw) : NO_DATA} (${provenanceLabel(quotes[0]?.source)})`,
    ]
  }
  return null
}
</script>

<template>
  <aside class="panel ai-panel" :class="{ 'history-open': historyOpen }" aria-labelledby="ai-h">
    <nav id="ai-history" class="history" :class="{ open: historyOpen }" aria-label="이전 대화" :inert="!historyOpen">
      <div class="history-inner">
        <div class="history-head">
          <h3>이전 대화</h3>
        </div>
        <div class="history-body">
        <ul v-if="historyItems.length" class="history-list" aria-live="polite">
          <li v-for="item in historyItems" :key="item.conversation_id">
            <button
              class="history-item"
              :class="{ current: item.conversation_id === conversationId }"
              type="button"
              :aria-current="item.conversation_id === conversationId ? 'true' : undefined"
              :disabled="sending"
              @click="selectConversation(item.conversation_id)"
            >
              <span class="history-title" :title="item.title">{{ item.title }}</span>
              <span class="history-meta">{{ updatedFormatter.format(new Date(item.updated_at)) }} · 메시지 {{ item.message_count }}개</span>
            </button>
          </li>
        </ul>
        <p v-if="historyLoading && !historyItems.length" class="muted">불러오는 중입니다…</p>
        <p v-else-if="!historyItems.length && !historyError" class="muted">아직 저장된 대화가 없습니다.</p>
        <p v-if="historyErrorText" class="error" role="alert">{{ historyErrorText }}</p>
        <button v-if="historyError && !historyLoading" class="btn" type="button" @click="loadHistory(historyRetryMore)">다시 시도</button>
        <button v-else-if="historyCursor" class="btn" type="button" :disabled="historyLoading" @click="loadHistory(true)">
          {{ historyLoading ? '불러오는 중…' : '더 보기' }}
        </button>
        </div>
      </div>
    </nav>

    <div class="chat">
    <header class="ai-head">
      <div class="head-title">
        <button
          class="icon-btn"
          type="button"
          :aria-label="historyOpen ? '이전 대화 목록 닫기' : '이전 대화 목록 열기'"
          :title="historyOpen ? '목록 닫기' : '이전 대화 목록'"
          aria-controls="ai-history"
          :aria-expanded="historyOpen"
          @click="toggleHistory"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
            <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
          </svg>
        </button>
        <div>
          <h2 id="ai-h"><span aria-hidden="true">✦</span> NegoPilot</h2>
          <p class="muted">기업 선택 없이 질문하고 이전 대화를 이어갈 수 있습니다.</p>
        </div>
      </div>
      <div class="head-actions">
        <button v-if="conversationId || messages.length" class="link-btn" type="button" @click="newConversation">새 대화</button>
        <button v-if="closable !== false" class="close" type="button" aria-label="NegoPilot 닫기" @click="emit('close')">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
        </svg>
        </button>
      </div>
    </header>

    <ol ref="messageList" class="messages" aria-live="polite">
      <li v-if="loading" class="muted">대화를 불러오는 중입니다…</li>
      <li v-if="nextCursor"><button class="btn" type="button" :disabled="olderLoading" @click="loadOlder">{{ olderLoading ? '불러오는 중…' : '이전 메시지 더 보기' }}</button></li>
      <li v-if="!messages.length && !loading && loaded" class="empty muted">
        기업 이름이나 지역 또는 법령으로 바로 물어보세요. 질문과 이전 대화에서 필요한 정보를 찾습니다.
      </li>

      <template v-for="m in messages" :key="m.key">
        <li v-if="m.role === 'user'" class="bubble">{{ m.text }}</li>

        <li v-else-if="m.response" class="answer">
          <div class="who">
            <span aria-hidden="true">✦</span> NegoPilot
            <span v-if="!m.response.answer.grounded" class="badge badge-gray">근거 자료 없음</span>
            <span v-if="m.response.mode === 'stub'" class="badge badge-gray">테스트 응답</span>
          </div>

          <p class="answer-text" :class="{ failed: m.response.answer.kind === 'failed' }">
            <template v-for="(part, i) in splitCitations(m.response.answer.text)" :key="i">
              <template v-if="part.type === 'text'">{{ part.value }}</template>
              <a
                v-else-if="citationHref(m.response, part.n)"
                :href="citationHref(m.response, part.n) ?? undefined"
                target="_blank"
                rel="noopener noreferrer"
                class="cite"
                :aria-label="`근거 ${part.n} 새 탭에서 보기`"
              >[{{ part.n }}]</a>
              <template v-else>[{{ part.n }}]</template>
            </template>
          </p>

          <div v-if="supplierSummary(m.response)" class="summary">
            <h3>조회 요약</h3>
            <ul>
              <li v-for="line in supplierSummary(m.response)" :key="line">{{ line }}</li>
            </ul>
            <p class="origin-note">실제 Agent 실행에도 가상 데이터나 관리자 입력이 포함될 수 있습니다. 인증과 생산능력 등 각 항목의 출처는 근거 자료에서 확인하세요.</p>
          </div>

          <div v-if="m.response.answer.citations.length" class="evidence">
            <h3>근거 자료 · 클릭 시 새 탭 <span aria-hidden="true">↗</span></h3>
            <a
              v-for="c in m.response.answer.citations"
              :key="c.n"
              :href="evidenceHref(m.response, c.evidence_id)"
              target="_blank"
              rel="noopener noreferrer"
              class="evidence-link"
            >
              <span aria-hidden="true">↗</span>
              [{{ c.n }}] {{ c.label }}
              <span class="sr-only">(새 탭에서 열림)</span>
            </a>
          </div>

          <section v-if="m.response.answer.options.length" class="reply-suggestions" :aria-label="optionsTitle(m.response.answer.kind)">
            <h3>{{ optionsTitle(m.response.answer.kind) }}</h3>
            <div v-if="m.key === latestAssistantKey" class="options">
              <button v-for="(o, index) in m.response.answer.options" :key="index" class="option" type="button" :disabled="suggestionsDisabled" @click="fillDraft(o, m.key)">
                {{ o }}
              </button>
            </div>
            <ol v-else class="past-suggestions">
              <li v-for="(o, index) in m.response.answer.options" :key="index">{{ o }}</li>
            </ol>
          </section>
          <section v-else-if="m.response.answer.kind === 'answer' && m.response.answer.suggested_questions?.length" class="reply-suggestions" aria-label="추천 질문">
            <h3>추천 질문</h3>
            <div v-if="m.key === latestAssistantKey" class="options">
              <button v-for="q in m.response.answer.suggested_questions" :key="q" class="option" type="button" :disabled="suggestionsDisabled" @click="fillDraft(q, m.key)">
                {{ q }}
              </button>
            </div>
            <ul v-else class="past-suggestions">
              <li v-for="q in m.response.answer.suggested_questions" :key="q">{{ q }}</li>
            </ul>
          </section>
        </li>
      </template>

      <li v-if="sending" class="answer pending" role="status">
        <div class="who"><span aria-hidden="true">✦</span> NegoPilot</div>
        <div class="typing-bubble"><span class="typing" aria-hidden="true"><i></i><i></i><i></i></span></div>
        <p class="muted pending-text">답변을 만드는 중입니다… 실제 모델은 수십 초 걸릴 수 있습니다.</p>
      </li>
    </ol>

    <div v-if="suggestions.length" class="suggestions" role="group" aria-label="예시 질문">
      <button v-for="q in suggestions" :key="q" class="option" type="button" :disabled="suggestionsDisabled" @click="fillDraft(q)">{{ q }}</button>
    </div>

    <p v-if="errorText" class="error" role="alert">{{ errorText }}</p>
    <button v-if="!loaded && !loading && conversationId" class="btn" type="button" @click="loadConversation(conversationId)">대화 다시 불러오기</button>
    <p class="footnote note">※ 기업 정보와 법령 원문은 근거 자료 탭에서 확인합니다.</p>

    <form class="composer" @submit.prevent="send(draft)">
      <label for="ai-input" class="sr-only">추가 질문</label>
      <textarea
        id="ai-input"
        ref="input"
        v-model="draft"
        rows="1"
        :maxlength="QUERY_MAX"
        placeholder="기업이나 법령에 대해 질문하세요"
        :disabled="sending || loading || !loaded"
        @keydown="onKeydown"
      />
      <button class="send" type="submit" aria-label="전송" title="전송" :disabled="!canSend">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" />
        </svg>
      </button>
    </form>
    </div>
  </aside>
</template>

<style scoped>
.ai-panel {
  display: flex;
  position: sticky;
  top: 16px;
  min-height: 640px;
  max-height: calc(100vh - 120px);
  overflow: hidden;
}
.chat {
  flex: none;
  width: min(460px, calc(100vw - 32px));
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.ai-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border);
}
.ai-head h2 {
  font-size: 16px;
}
.ai-head p {
  margin: 4px 0 0;
}
.head-title {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.icon-btn {
  flex: none;
  width: 28px;
  height: 28px;
  margin-top: -3px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.icon-btn:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
/* 대화 목록 사이드바: 펼치면 패널이 왼쪽으로 넓어지고 대화 영역 너비는 그대로다 */
.history {
  flex: none;
  width: 0;
  overflow: hidden;
  background: var(--surface-2);
  border-right: 1px solid transparent;
  transition: width 0.2s ease;
}
.history.open {
  width: 248px;
  border-right-color: var(--border);
}
.history-inner {
  width: 248px;
  height: 100%;
  display: flex;
  flex-direction: column;
}
/* 제목은 고정하고 아래 목록만 스크롤한다 */
.history-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 10px;
  border-bottom: 1px solid var(--border);
}
.history-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.history-head h3 {
  font-size: 13px;
}
.history p {
  margin: 0;
  padding: 0 4px;
  font-size: 12px;
}
.history .error {
  margin: 0;
}
.history-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.history-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 0;
  border-radius: var(--radius);
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.history-item:hover:not(:disabled) {
  background: var(--primary-soft);
}
.history-item.current {
  background: var(--surface);
  box-shadow: inset 0 0 0 1px var(--border-strong);
}
.history-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
/* 제목이 한 줄을 넘으면 말줄임으로 자른다. 버튼 안 flex 항목이라 너비를 명시해야 잘린다 */
.history-title {
  display: block;
  min-width: 0;
  max-width: 100%;
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-meta {
  font-size: 11px;
  color: var(--text-3);
}
@media (prefers-reduced-motion: reduce) {
  .history {
    transition: none;
  }
}
/* 좁은 화면에서는 패널을 넓힐 수 없어 목록이 대화 위를 덮는다 */
@media (max-width: 760px) {
  .history {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 2;
  }
  .history.open {
    box-shadow: 4px 0 16px rgba(30, 50, 90, 0.18);
  }
}
.close {
  width: 32px;
  height: 32px;
  border: 0;
  background: none;
  color: var(--text-2);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.messages {
  flex: 1;
  overflow-y: auto;
  margin: 0;
  padding: 16px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.empty {
  padding: 8px 0;
}
.bubble {
  align-self: flex-end;
  max-width: 88%;
  padding: 12px 14px;
  background: var(--surface-2);
  border-radius: var(--radius);
  font-size: 12px;
  white-space: pre-line;
}
.answer {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
/* 답변 대기: 답변이 들어올 자리에 점 3개를 띄운다 */
.pending {
  gap: 6px;
}
.typing-bubble {
  align-self: flex-start;
  padding: 12px 14px;
  background: var(--surface-2);
  border-radius: var(--radius);
  color: var(--text-3);
}
.pending-text {
  margin: 0;
  font-size: 11px;
}
.who {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--primary);
  font-weight: 700;
  font-size: 13px;
}
.answer-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-line;
}
.answer-text.failed {
  color: var(--danger);
}
.cite {
  font-weight: 600;
  text-decoration: none;
}
.summary {
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
}
.summary h3,
.evidence h3 {
  font-size: 12px;
  margin-bottom: 8px;
}
.summary ul {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: var(--text-2);
}
.origin-note { margin: 8px 0 0; font-size: 12px; color: var(--text-2); line-height: 1.6; }
.evidence {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.evidence-link {
  display: flex;
  gap: 6px;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 12px;
  text-decoration: none;
}
.evidence-link:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
.note-line {
  align-self: center;
  font-size: 11px;
  color: var(--text-3);
}
.options,
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.reply-suggestions h3 {
  margin: 0 0 8px;
  font-size: 12px;
}
.past-suggestions {
  margin: 0;
  padding-left: 20px;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.suggestions {
  padding: 0 16px 12px;
}
.option {
  max-width: 100%;
  min-height: 28px;
  padding: 4px 10px;
  border: 1px solid var(--border-strong);
  border-radius: 14px;
  background: var(--surface);
  font: inherit;
  font-size: 12px;
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
  cursor: pointer;
}
.option:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.error {
  margin: 0 16px 8px;
  font-size: 12px;
  color: var(--danger);
}
.note {
  margin: 0 16px 10px;
}
.composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  margin: 0 16px 16px;
  padding: 10px 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
}
.composer textarea {
  flex: 1;
  border: 0;
  padding: 4px 0;
  min-height: 28px;
  font-size: 12px;
  resize: none;
}
.composer textarea:focus-visible {
  outline: none;
}
.composer:focus-within {
  outline: 2px solid var(--primary);
  outline-offset: 1px;
}
/* 보낼 수 있을 때만 색을 채워 눌러도 되는 상태임을 보여 준다 */
.send {
  flex: none;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.15s ease;
}
.send:hover:not(:disabled) {
  background: var(--primary-hover);
}
.send:disabled {
  background: var(--border);
  color: var(--text-3);
  cursor: not-allowed;
}
</style>
