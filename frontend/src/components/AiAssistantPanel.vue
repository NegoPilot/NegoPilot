<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { createConversation, sendMessage } from '@/api/conversations'
import type { GenerationResponse } from '@/api/types'
import { describeError, isApiError } from '@/utils/errors'
import { parseSupplierEvidence, splitCitations } from '@/utils/evidence'
import { NO_DATA, formatKrw, formatPercent, formatPpm } from '@/utils/format'

const props = defineProps<{
  supplier: { id: string; name: string } | null
  /** 홈에서 넘어온 질문. 입력창에 채워 둔다 */
  initialQuestion: string
  rfq: string | null
  /** 근거 상세 탭의 '원래 화면으로 돌아가기' 주소 */
  backPath: string
}>()

const emit = defineEmits<{ close: [] }>()
const router = useRouter()

// stub 모드에서 답할 수 있는 질문 (docs/contracts/direct-chat.md 자동 검증 예시, agent/.../generation_stubs.py)
const SUPPLIER_QUESTIONS = ['이 회사에 대해 알려줘', '이 회사의 재무 상태를 알려줘', '납기와 품질은 어때?']
const GENERAL_QUESTIONS = ['부산 협력사 찾아줘', '해솔에 대해 알려줘', '하도급법상 대금 지급 기한은 며칠이야?', 'RFQ가 뭐야?']
const QUERY_MAX = 4000

interface PanelMessage {
  key: string
  /** note: 대화 중 기준 협력사가 바뀌었다는 화면 안내 (서버에 저장하지 않음) */
  role: 'user' | 'assistant' | 'note'
  text: string
  response?: GenerationResponse
}

const conversationId = ref<string | null>(null)
const messages = ref<PanelMessage[]>([])
const draft = ref(props.initialQuestion)
const sending = ref(false)
const error = ref<unknown>(null)
const listEnd = ref<HTMLElement | null>(null)

watch(
  () => props.initialQuestion,
  (q) => {
    if (q) draft.value = q
  },
)

// 협력사를 바꿔도 대화는 이어진다. 다음 메시지부터 바뀐 선택을 context로 보낸다 (direct-chat 계약)
watch(
  () => props.supplier,
  (next, prev) => {
    if (!messages.value.length || next?.id === prev?.id) return
    messages.value.push({
      key: `n-${Date.now()}`,
      role: 'note',
      text: next ? `이제부터 ${next.name} 기준으로 답합니다.` : '선택한 협력사 없이 이어서 답합니다.',
    })
    scrollToEnd()
  },
)

/** 새 대화는 사용자가 직접 시작한다 */
function newConversation() {
  conversationId.value = null
  messages.value = []
  error.value = null
}

const suggestions = computed(() =>
  messages.value.length ? [] : props.supplier ? [...SUPPLIER_QUESTIONS, ...GENERAL_QUESTIONS.slice(0, 2)] : GENERAL_QUESTIONS,
)
const canSend = computed(() => {
  const q = draft.value.trim()
  return !sending.value && q.length > 0 && [...q].length <= QUERY_MAX
})

const errorText = computed(() => {
  if (!error.value) return null
  if (isApiError(error.value, 'VALIDATION_ERROR')) {
    return '이 질문은 처리할 수 없습니다. 테스트 모드에서는 예시 질문만 답할 수 있습니다.'
  }
  return describeError(error.value).message
})

function scrollToEnd() {
  nextTick(() => listEnd.value?.scrollIntoView({ block: 'end' }))
}

async function send(text: string) {
  const query = text.trim()
  if (!query || sending.value) return
  sending.value = true
  error.value = null
  draft.value = ''
  messages.value.push({ key: `u-${Date.now()}`, role: 'user', text: query })
  scrollToEnd()
  try {
    if (!conversationId.value) {
      const snapshot = await createConversation(props.supplier?.id ?? null)
      conversationId.value = snapshot.conversation_id
    }
    const res = await sendMessage(conversationId.value, query, {
      selected_supplier_ids: props.supplier ? [props.supplier.id] : [],
    })
    messages.value.push({ key: res.response_id, role: 'assistant', text: res.answer.text, response: res })
  } catch (e) {
    // 실패하면 Spring도 저장하지 않으므로 질문을 입력창으로 되돌린다
    messages.value.pop()
    error.value = e
    draft.value = query
  } finally {
    sending.value = false
    scrollToEnd()
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
      `불량률: ${formatPpm(s.quality?.defect_ppm)}`,
      `납기 준수율: ${formatPercent(s.quality?.on_time_delivery_rate)}`,
      `최근 견적: ${quotes[0] ? formatKrw(quotes[0].unit_price_krw) : NO_DATA}`,
    ]
  }
  return null
}
</script>

<template>
  <aside class="panel ai-panel" aria-labelledby="ai-h">
    <header class="ai-head">
      <div>
        <h2 id="ai-h"><span aria-hidden="true">✦</span> AI 도우미</h2>
        <p class="muted">현재 선택: {{ supplier?.name ?? '선택한 협력사 없음' }}</p>
      </div>
      <div class="head-actions">
        <button v-if="messages.length" class="link-btn" type="button" :disabled="sending" @click="newConversation">새 대화</button>
        <button class="close" type="button" aria-label="AI 도우미 닫기" @click="emit('close')">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
          <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
        </svg>
        </button>
      </div>
    </header>

    <ol class="messages" aria-live="polite">
      <li v-if="!messages.length" class="empty muted">
        {{ supplier ? '선택한 협력사나 법령에 대해 물어보세요.' : '협력사를 고르지 않아도 기업 이름이나 지역, 법령으로 바로 물어볼 수 있습니다.' }}
      </li>

      <template v-for="m in messages" :key="m.key">
        <li v-if="m.role === 'user'" class="bubble">{{ m.text }}</li>
        <li v-else-if="m.role === 'note'" class="note-line">{{ m.text }}</li>

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

          <div v-if="m.response.answer.options.length" class="options" role="group" aria-label="이어서 고르기">
            <button v-for="o in m.response.answer.options" :key="o" class="option" type="button" :disabled="sending" @click="send(o)">
              {{ o }}
            </button>
          </div>
        </li>
      </template>

      <li v-if="sending" class="muted">답변을 만드는 중입니다… 실제 모델은 수십 초 걸릴 수 있습니다.</li>
    </ol>
    <div ref="listEnd" />

    <div v-if="suggestions.length" class="suggestions" role="group" aria-label="예시 질문">
      <button v-for="q in suggestions" :key="q" class="option" type="button" :disabled="sending" @click="send(q)">{{ q }}</button>
    </div>

    <p v-if="errorText" class="error" role="alert">{{ errorText }}</p>
    <p class="footnote note">※ 기업 정보와 법령 원문은 근거 자료 탭에서 확인합니다.</p>

    <form class="composer" @submit.prevent="send(draft)">
      <label for="ai-input" class="sr-only">추가 질문</label>
      <textarea
        id="ai-input"
        v-model="draft"
        rows="1"
        :maxlength="QUERY_MAX"
        placeholder="추가 질문을 입력하세요..."
        :disabled="sending"
        @keydown="onKeydown"
      />
      <button class="send" type="submit" :disabled="!canSend">전송 ↑</button>
    </form>
  </aside>
</template>

<style scoped>
.ai-panel {
  display: flex;
  flex-direction: column;
  min-height: 640px;
  max-height: calc(100vh - 120px);
  position: sticky;
  top: 16px;
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
.suggestions {
  padding: 0 16px 12px;
}
.option {
  min-height: 28px;
  padding: 4px 10px;
  border: 1px solid var(--border-strong);
  border-radius: 14px;
  background: var(--surface);
  font: inherit;
  font-size: 12px;
  text-align: left;
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
.send {
  border: 0;
  background: none;
  color: var(--primary);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.send:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}
</style>
