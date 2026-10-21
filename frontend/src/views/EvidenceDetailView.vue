<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { getConversation } from '@/api/conversations'
import { getSupplier } from '@/api/suppliers'
import type { StubEvidence, SupplierDetail } from '@/api/types'
import ErrorNotice from '@/components/ErrorNotice.vue'
import SupplierDetailPanel from '@/components/SupplierDetailPanel.vue'
import { isApiError } from '@/utils/errors'

// 새 탭에서 열리므로 메모리 상태 없이 URL만으로 대화를 다시 불러온다
const props = defineProps<{
  conversationId: string
  responseId: string
  evidenceId: string
}>()

const evidence = ref<StubEvidence | null>(null)
const supplier = ref<SupplierDetail | null>(null)
const question = ref<string | null>(null)
const answer = ref<string | null>(null)
// true면 답변에 실린 카드의 데이터(답변 시점), false면 지금 다시 조회한 데이터
const fromAnswer = ref(true)
const loading = ref(false)
const error = ref<unknown>(null)
const notFound = ref(false)

async function load() {
  loading.value = true
  error.value = null
  notFound.value = false
  evidence.value = null
  supplier.value = null
  try {
    const snapshot = await getConversation(props.conversationId)
    const index = snapshot.messages.findIndex((m) => m.response?.response_id === props.responseId)
    const response = index >= 0 ? snapshot.messages[index]?.response : null
    const found = response?.evidence.find((e) => e.evidence_id === props.evidenceId) ?? null
    if (!response || !found) {
      notFound.value = true
      return
    }
    evidence.value = found
    answer.value = response.presentation.text
    const previous = snapshot.messages[index - 1]
    question.value = previous?.role === 'user' ? previous.content : null

    const card = response.presentation.cards.find((c) => c.evidence_ids.includes(props.evidenceId))
    if (card) {
      supplier.value = card.supplier
      fromAnswer.value = true
    } else {
      supplier.value = await getSupplier(found.supplier_id)
      fromAnswer.value = false
    }
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

watch(() => [props.conversationId, props.responseId, props.evidenceId], load, { immediate: true })
</script>

<template>
  <main class="page">
    <header class="page-head">
      <span class="muted">근거 ID {{ evidenceId }}</span>
      <h1>근거 자료 상세</h1>
    </header>

    <p v-if="loading" aria-live="polite">근거 자료를 불러오는 중…</p>

    <section v-else-if="error" class="panel">
      <ErrorNotice
        :error="error"
        :message="
          isApiError(error, 'CONVERSATION_NOT_FOUND')
            ? '대화가 만료됐거나 없는 대화입니다. 서버를 다시 시작하면 대화 기록이 사라집니다.'
            : null
        "
        :retry="load"
      />
    </section>

    <section v-else-if="notFound" class="panel panel-body">
      이 대화에서 해당 근거를 찾을 수 없습니다.
      <RouterLink :to="{ name: 'chat', params: { conversationId } }">대화로 돌아가기</RouterLink>
    </section>

    <template v-else-if="evidence && supplier">
      <section class="panel" aria-labelledby="ev-source">
        <div class="panel-head"><h2 id="ev-source">출처</h2></div>
        <dl class="kv panel-body">
          <div><dt>도구</dt><dd>{{ evidence.server }} · {{ evidence.tool }}</dd></div>
          <div><dt>협력사</dt><dd>{{ supplier.name }} ({{ evidence.supplier_id }})</dd></div>
          <div>
            <dt>데이터</dt>
            <dd>{{ evidence.source === 'stub' && !evidence.executed ? '테스트 데이터 (도구를 실행하지 않음)' : '도구 실행 결과' }}</dd>
          </div>
          <div><dt>기준</dt><dd>{{ fromAnswer ? '답변에 쓰인 시점의 데이터' : '지금 다시 조회한 데이터' }}</dd></div>
        </dl>
      </section>

      <section v-if="question || answer" class="panel" aria-labelledby="ev-qa">
        <div class="panel-head"><h2 id="ev-qa">이 근거를 쓴 답변</h2></div>
        <div class="panel-body qa">
          <p v-if="question"><span class="muted">질문</span><br />{{ question }}</p>
          <p v-if="answer"><span class="muted">답변</span><br />{{ answer }}</p>
          <RouterLink :to="{ name: 'chat', params: { conversationId } }">대화로 이동</RouterLink>
        </div>
      </section>

      <SupplierDetailPanel :supplier="supplier" />
    </template>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.page-head h1 {
  font-size: 20px;
}
.kv {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px 20px;
}
.kv dt {
  font-size: 12px;
  color: var(--text-3);
}
.kv dd {
  margin: 2px 0 0;
}
.qa {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.qa p {
  margin: 0;
  white-space: pre-line;
}
</style>
