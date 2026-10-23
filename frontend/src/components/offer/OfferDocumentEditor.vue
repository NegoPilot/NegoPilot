<script setup lang="ts">
import { computed, ref } from 'vue'
import type { InternalBriefing, OfferDocument } from '@/api/offer-proposals'
import { formatInteger } from '@/utils/format'

// 화면에서 고친 값은 그대로 두고, 확정할 때 render API가 다시 검사한다
const document = defineModel<OfferDocument>('document', { required: true })
const briefing = defineModel<InternalBriefing>('briefing', { required: true })
defineProps<{ locked: boolean }>()
const tab = ref<'proposal' | 'briefing'>('proposal')

const notes = computed({
  get: () => document.value.delivery_notes ?? '',
  set: (value: string) => { document.value.delivery_notes = value.trim() ? value : null },
})
</script>

<template>
  <div class="tabs" role="tablist">
    <button type="button" role="tab" class="tab" :class="{ on: tab === 'proposal' }" :aria-selected="tab === 'proposal'" @click="tab = 'proposal'">협력사 발송용 제안서</button>
    <button type="button" role="tab" class="tab" :class="{ on: tab === 'briefing' }" :aria-selected="tab === 'briefing'" @click="tab = 'briefing'">내부 협상 브리핑 · 대외비</button>
  </div>

  <div class="doc-area">
    <fieldset v-if="tab === 'proposal'" :disabled="locked" class="doc" aria-label="제안서 미리보기">
      <p class="doc-label">제안서 미리보기 <span class="muted">· 칸을 눌러 바로 고칠 수 있어요</span></p>
      <div class="paper">
        <input v-model="document.title" class="title" aria-label="제목" maxlength="100" />
        <p class="meta">수신: {{ document.supplier_name }} ({{ document.supplier_id }}) 영업 담당자님</p>
        <p class="meta">발신: {{ document.sender.company }} · {{ document.sender.department }} {{ document.sender.contact_name }} {{ document.sender.job_title }} (로그인 회원 정보)</p>
        <textarea v-model="document.cover_letter" class="letter" rows="3" maxlength="1500" aria-label="인사말" />
        <hr />
        <ol class="terms">
          <li>대상 품목: {{ document.rfq_id }} {{ document.part_name }}<template v-if="document.material"> ({{ document.material }})</template>, 연간 {{ formatInteger(document.annual_volume) }} EA</li>
          <li>제안 단가: <input v-model.number="document.unit_price" type="number" min="1" step="1" class="num-input" aria-label="제안 단가" /> 원 / EA</li>
          <li>대금 지급: 납품 후 <input v-model.number="document.payment_days" type="number" min="1" step="1" class="num-input short" aria-label="지급 일수" /> 일 이내 {{ document.payment_method }} 지급</li>
          <li>납품대금 연동:
            <select v-model="document.price_linkage" aria-label="연동 약정" class="inline-select"><option :value="true">포함</option><option :value="false">없음</option></select>
          </li>
          <li>거래 기간: <input v-model.number="document.contract_days" type="number" min="1" step="1" class="num-input short" aria-label="거래 기간" /> 일</li>
        </ol>
        <label class="row">납품·비고<textarea v-model="notes" rows="2" maxlength="500" /></label>
        <label class="row inline">회신 요청일 <input v-model="document.reply_by" type="date" required /></label>
      </div>
    </fieldset>

    <fieldset v-else :disabled="locked" class="doc" aria-label="내부 협상 브리핑">
      <p class="doc-label">내부 협상 브리핑 <span class="badge badge-warning">대외비 · 발송 안 함</span></p>
      <div class="paper">
        <h3>양보 순서</h3>
        <input v-for="(_, i) in briefing.concessions" :key="'c' + i" v-model="briefing.concessions[i]" :aria-label="`양보 ${i + 1}`" class="line" />
        <h3>예상 쟁점과 대응</h3>
        <div v-for="(o, i) in briefing.objections" :key="'o' + i" class="pair">
          <input v-model="o.issue" :aria-label="`쟁점 ${i + 1}`" />
          <input v-model="o.response" :aria-label="`대응 ${i + 1}`" />
        </div>
        <h3>협상 전략 · AI 초안</h3>
        <textarea v-model="briefing.strategy" rows="3" aria-label="협상 전략" />
        <h3>제안 멘트 · AI 초안</h3>
        <textarea v-model="briefing.opening_message" rows="3" aria-label="제안 멘트" />
      </div>
    </fieldset>
  </div>
</template>

<style scoped>
.doc-area{margin-top:14px;padding:14px 16px;background:var(--surface-2);border-radius:var(--radius)}
.doc{border:0;margin:0;padding:0;min-width:0}.doc-label{margin:0 0 12px;font-size:13px;display:flex;gap:8px;align-items:center}
.paper{display:flex;flex-direction:column;gap:8px;padding:24px 28px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.paper input,.paper textarea,.paper select{border:1px solid transparent;background:transparent;font:inherit;font-size:13px;color:var(--text);border-radius:4px}
.paper input:hover,.paper textarea:hover,.paper select:hover{border-color:var(--border)}
.paper input:focus,.paper textarea:focus,.paper select:focus{border-color:var(--primary);background:var(--surface)}
.doc:disabled input,.doc:disabled textarea,.doc:disabled select{border-color:transparent;color:var(--text);opacity:1;-webkit-text-fill-color:var(--text)}
.title{font-size:20px !important;font-weight:700;height:36px;padding:0 4px}.meta{margin:0 4px;font-size:13px;color:var(--text-2)}
.letter{padding:6px 4px;line-height:1.6}hr{width:100%;border:0;border-top:1px solid var(--border);margin:4px 0}
.terms{margin:0;padding-left:22px;display:flex;flex-direction:column;gap:6px;font-size:13px}
.num-input{width:80px;height:28px;padding:0 6px}.num-input.short{width:64px}.inline-select{width:auto;height:28px}
.row{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text-2)}.row.inline{flex-direction:row;align-items:center;gap:8px}
.row.inline input{width:auto;height:28px}
h3{font-size:12px;color:var(--text-3);font-weight:600;margin-top:6px}.line{height:30px;padding:0 6px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:8px}.pair input{height:30px;padding:0 6px}
.paper textarea{padding:6px;line-height:1.6}
@media(max-width:800px){.paper{padding:16px}.pair{grid-template-columns:1fr}}
</style>
