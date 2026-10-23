<script setup lang="ts">
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import { AUDIT_GRADES } from '@/data/dataCatalog'
import type { FieldErrorOf, QualityDraft } from '@/utils/supplierForm'

// 품질·납기 실적 네 칸. 납기 준수율은 백분율로 받는다
defineProps<{
  quality: QualityDraft
  err: FieldErrorOf
  /** 비워 둘 수 있으면 필수(*) 표시를 하지 않는다 */
  optional?: boolean
}>()
</script>

<template>
  <FormGrid :cols="4">
    <FormField id="r-ppm" label="불량률 (ppm)" :required="!optional" :error="err('defectPpm')">
      <input id="r-ppm" v-model="quality.defectPpm" type="text" inputmode="numeric" placeholder="예: 450" :aria-invalid="!!err('defectPpm')" />
    </FormField>
    <FormField id="r-ontime" label="납기 준수율 (%)" :required="!optional" :error="err('onTimeRatePct')" hint="소수 첫째 자리까지">
      <input id="r-ontime" v-model="quality.onTimeRatePct" type="text" inputmode="decimal" placeholder="예: 97.5" :aria-invalid="!!err('onTimeRatePct')" />
    </FormField>
    <FormField id="r-grade" label="품질 감사 등급" :required="!optional" :error="err('auditGrade')">
      <select id="r-grade" v-model="quality.auditGrade" :aria-invalid="!!err('auditGrade')">
        <option value="">선택</option>
        <option v-for="g in AUDIT_GRADES" :key="g" :value="g">{{ g }}</option>
      </select>
    </FormField>
    <FormField id="r-years" label="거래 기간 (년)" :required="!optional" :error="err('yearsDealing')" hint="신규 후보는 0">
      <input id="r-years" v-model="quality.yearsDealing" type="text" inputmode="numeric" :aria-invalid="!!err('yearsDealing')" />
    </FormField>
  </FormGrid>
</template>
