<script setup lang="ts">
import { reactive, watch } from 'vue'
const emit = defineEmits<{ change: [value: Record<string, unknown>] }>()
defineProps<{ disabled?: boolean }>()
const fields = [
 {key:'transaction_type',label:'거래 유형',options:[['unknown','모름'],['manufacturing','제조 위탁'],['purchase','단순 구매']]},
 {key:'party_requirements_confirmed',label:'하도급 거래 당사자 요건 확인',options:[['','미확인'],['true','확인함'],['false','해당하지 않음']]},
 {key:'price_scope',label:'단가 적용 범위',options:[['unknown','모름'],['new_order','신규 발주'],['existing_order','기존 발주분 변경']]},
 {key:'contracted_on',label:'최초 계약일',type:'date'}, {key:'renewed_on',label:'갱신일',type:'date'},
 {key:'actual_contract_amount',label:'실제 계약금액 (원)',type:'number'},
 {key:'committed_quantity',label:'확정 구매수량',type:'number'},
 {key:'previous_unit_price',label:'기존 발주 확정 단가 (원)',type:'number'},
 {key:'payment_basis',label:'지급 기산점',options:[['unknown','모름'],['receipt','목적물 수령일'],['completion','용역 완료일'],['monthly_agreed','합의한 월별 계산서 발행일']]},
 {key:'receipt_date',label:'목적물 수령일',type:'date'}, {key:'completion_date',label:'용역 완료일',type:'date'},
 {key:'monthly_agreed_date',label:'합의한 월별 계산서 발행일',type:'date'}, {key:'agreed_due_date',label:'약정 지급일',type:'date'},
 {key:'upstream_received_on',label:'기성금·준공금 수령일',type:'date'},
 {key:'upstream_payment_applicable',label:'기성금·준공금 수령에 따른 지급 해당',options:[['','미확인'],['true','해당'],['false','해당하지 않음']]},
 {key:'payment_exception_claimed',label:'지급기한 예외 주장',options:[['false','주장하지 않음'],['true','예외 검토 필요']]},
 {key:'payment_exception_reason',label:'지급기한 예외 사유'},
 {key:'payment_exception_evidence',label:'지급기한 예외 증빙 (쉼표 구분)',array:true},
 {key:'reduction_reason',label:'감액 사유'}, {key:'reduction_evidence',label:'감액 증빙 번호 (쉼표 구분)',array:true},
 {key:'waiver_reason',label:'미연동 합의 사유'}, {key:'waiver_evidence',label:'미연동 합의 증빙 (쉼표 구분)',array:true},
 {key:'evidence_references',label:'거래 사실 증빙 (쉼표 구분)',array:true},
]
const form = reactive<Record<string,string>>(Object.fromEntries(fields.map(f => [f.key,f.options?.[0]?.[0] ?? ''])))
const linkageFields = [
 {key:'name',label:'대상 원가 이름'}, {key:'cost_ratio',label:'대상 원가 비중 (%)',ratio:true},
 {key:'indicator',label:'기준 지표'}, {key:'formula',label:'조정 산식'},
 {key:'trigger_ratio',label:'가격 변동 조정요건 (%)',ratio:true}, {key:'frequency',label:'조정 주기'},
 {key:'reference_period',label:'기준·비교 시점'}, {key:'reflection_date',label:'조정 반영일·방식'},
 {key:'evidence_references',label:'연동 증빙 (쉼표 구분)',array:true},
]
const details = reactive<Array<Record<string,string>>>([])
const split = (s:string) => s.split(',').map(v=>v.trim()).filter(Boolean)
function addDetail() { details.push({category:'material',...Object.fromEntries(linkageFields.map(f=>[f.key,'']))}) }
watch([form,details], () => {
 const value: Record<string, unknown> = Object.fromEntries(fields.map(f => {
   const v=form[f.key] || ''
   return [f.key, f.array ? split(v) : ['party_requirements_confirmed','upstream_payment_applicable','payment_exception_claimed'].includes(f.key) ? (v==='' ? null : v==='true') : f.type==='number' ? (v==='' ? null : Number(v)) : v || null]
 }))
 value.linkage_details=details.map(d=>({category:d.category,...Object.fromEntries(linkageFields.map(f=>[f.key,f.array ? split(d[f.key] || '') : f.ratio ? (d[f.key] ? Number(d[f.key])/100 : null) : d[f.key] || null]))}))
 emit('change',value)
}, {deep:true,immediate:true})
</script>
<template>
 <section class="transaction"><h2>판단에 필요한 거래 사실</h2><p>모르는 사실은 비워 두세요. 입력한 사실은 증빙과 함께 협상 근거로 연결됩니다.</p>
 <fieldset :disabled="disabled" class="fields">
  <label v-for="f in fields" :key="f.key">{{f.label}}
   <select v-if="f.options" v-model="form[f.key]"><option v-for="o in f.options" :key="o[0]" :value="o[0]">{{o[1]}}</option></select>
   <input v-else v-model="form[f.key]" :type="f.type || 'text'" :min="f.type==='number' ? 1 : undefined" :step="f.type==='number' ? 1 : undefined" maxlength="1000" />
  </label>
 </fieldset>
 <h3>연동 약정 상세</h3><p>원재료·에너지별로 추가하세요. 약정 포함 여부와 상세 내용의 완비 여부는 별도로 검토합니다.</p>
 <fieldset v-for="(d,i) in details" :key="i" :disabled="disabled" class="fields">
  <label>연동 대상<select v-model="d.category"><option value="material">원재료</option><option value="energy">에너지</option></select></label>
  <label v-for="f in linkageFields" :key="f.key">{{f.label}}<input v-model="d[f.key]" :type="f.ratio?'number':'text'" :min="f.ratio?0:undefined" :max="f.key==='trigger_ratio'?10:f.ratio?100:undefined" step="any" :required="f.key==='name'" maxlength="1000" /></label>
  <button type="button" @click="details.splice(i,1)">대상 삭제</button>
 </fieldset>
 <button type="button" :disabled="disabled" @click="addDetail">연동 대상 추가</button>
 </section>
</template>
<style scoped>
.transaction{border-top:1px solid var(--border);padding-top:20px;margin-top:20px}.fields{border:0;padding:0;margin:18px 0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}label{display:flex;flex-direction:column;gap:8px;font-size:13px}input,select{padding:9px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text);min-width:0}p{font-size:13px;color:var(--text-muted)}button{padding:8px;margin:8px 0}@media(max-width:800px){.fields{grid-template-columns:1fr}}
</style>
