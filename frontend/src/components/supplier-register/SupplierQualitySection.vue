<script setup lang="ts">
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import { AUDIT_GRADES } from '@/data/dataCatalog'
import type { SupplierErrorOf, SupplierForm } from '@/utils/supplierForm'

defineProps<{
  form: SupplierForm
  err: SupplierErrorOf
  /** DB에 저장될 납기 준수율 (0..1) */
  onTimeRate: number | null
}>()
</script>

<template>
  <PanelSection title="품질·납기 실적" required sub="quality 표 · 이 값이 없으면 협력사 요약(supplier_overview)에 나오지 않습니다.">
    <FormGrid :cols="4">
      <FormField id="r-ppm" label="불량률 (ppm)" required :error="err('defectPpm')">
        <input id="r-ppm" v-model="form.defectPpm" type="text" inputmode="numeric" placeholder="예: 450" :aria-invalid="!!err('defectPpm')" />
      </FormField>
      <FormField id="r-ontime" label="납기 준수율 (%)" required :error="err('onTimeRatePct')" :hint="onTimeRate !== null ? `저장값 ${onTimeRate}` : undefined">
        <input id="r-ontime" v-model="form.onTimeRatePct" type="text" inputmode="decimal" placeholder="예: 97.5" :aria-invalid="!!err('onTimeRatePct')" />
      </FormField>
      <FormField id="r-grade" label="품질 감사 등급" required :error="err('auditGrade')">
        <select id="r-grade" v-model="form.auditGrade" :aria-invalid="!!err('auditGrade')">
          <option value="">선택</option>
          <option v-for="g in AUDIT_GRADES" :key="g" :value="g">{{ g }}</option>
        </select>
      </FormField>
      <FormField id="r-years" label="거래 기간 (년)" required :error="err('yearsDealing')" hint="신규 후보는 0">
        <input id="r-years" v-model="form.yearsDealing" type="text" inputmode="numeric" :aria-invalid="!!err('yearsDealing')" />
      </FormField>
    </FormGrid>
  </PanelSection>
</template>
