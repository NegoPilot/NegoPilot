<script setup lang="ts">
import { computed, ref, useId } from 'vue'

import { SOURCE_LABEL } from '@/api/admin-suppliers'
import { type SupplierRegisterForm, isQualityBlank } from '@/utils/supplierForm'

// 등록 화면 오른쪽: 보낼 내용 요약과 등록 버튼
const props = defineProps<{
  form: SupplierRegisterForm
  /** 검사를 통과했을 때만 만든 요청 본문 */
  requestBody: unknown
  /** 저장을 한 번 눌렀는지. 누르기 전에는 오류 수를 알리지 않는다 */
  tried: boolean
  errorCount: number
  saving: boolean
  saveError: string | null
}>()
defineEmits<{ submit: []; reset: [] }>()

const headingId = useId()
const showJson = ref(false)

const rows = computed(() => [
  { label: '기본 정보 출처', value: SOURCE_LABEL[props.form.source] },
  { label: '생산능력', value: `${props.form.capabilities.length}줄` },
  { label: '품질·납기', value: isQualityBlank(props.form.quality) ? '입력 안 함' : '입력' },
  { label: '견적', value: props.form.quotes.length ? `${props.form.quotes.length}건 (등록 직후 추가)` : '없음' },
])
const rankingWarning = computed(() => isQualityBlank(props.form.quality) || !props.form.quotes.length)
</script>

<template>
  <aside class="panel panel-pad summary" :aria-labelledby="headingId">
    <h2 :id="headingId">등록 요약</h2>
    <p class="sub muted">{{ form.profile.name.trim() || '(협력사명)' }} · ID는 등록할 때 정해집니다</p>
    <dl class="rows">
      <template v-for="r in rows" :key="r.label">
        <dt>{{ r.label }}</dt>
        <dd>{{ r.value }}</dd>
      </template>
    </dl>
    <p v-if="rankingWarning" class="warn">품질이나 견적이 없으면 추천 순위에서 "품질 또는 견적 자료 미등록"으로 제외됩니다.</p>
    <p v-if="tried && errorCount" class="field-error" role="alert">고칠 항목 {{ errorCount }}개가 있습니다.</p>
    <p v-else-if="saveError" class="field-error" role="alert">{{ saveError }}</p>
    <p v-else-if="!errorCount" class="ok">필수 항목을 모두 채웠습니다.</p>

    <button class="btn btn-primary wide" type="button" :disabled="saving" @click="$emit('submit')">{{ saving ? '등록 중…' : '협력사 등록' }}</button>
    <button class="btn wide" type="button" :disabled="saving" @click="$emit('reset')">초기화</button>
    <button v-if="requestBody" class="link-btn" type="button" :aria-expanded="showJson" @click="showJson = !showJson">
      {{ showJson ? '요청 본문 닫기' : '요청 본문(JSON) 보기' }}
    </button>
    <pre v-if="showJson && requestBody" class="json">{{ JSON.stringify(requestBody, null, 2) }}</pre>
    <p class="footnote">
      등록 뒤 협력사 상세에서 기본 정보·생산능력·품질을 수정하고 견적을 더할 수 있습니다. 거래 조건과 재무는 조회만 합니다.
    </p>
  </aside>
</template>

<style scoped>
.summary {
  position: sticky;
  top: calc(var(--admin-header-height, 0px) + 16px);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.summary h2 {
  font-size: 16px;
}
.summary .sub {
  margin: -6px 0 0;
  font-size: 12px;
}
.rows {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 12px;
}
.rows dt {
  color: var(--text-3);
}
.rows dd {
  margin: 0;
  text-align: right;
}
.warn,
.ok,
.field-error {
  margin: 0;
  font-size: 12px;
}
.warn {
  color: var(--text-2);
}
.ok {
  color: var(--success);
}
.wide {
  width: 100%;
}
.link-btn {
  align-self: flex-start;
}
.json {
  max-height: 320px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  font-size: 11px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.footnote {
  margin: 0;
}
@media (max-width: 1100px) {
  .summary {
    position: static;
  }
}
</style>
