<script setup lang="ts">
import CheckOption from '@/components/form/CheckOption.vue'
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import type { SupplierErrorOf, SupplierForm } from '@/utils/supplierForm'

defineProps<{ form: SupplierForm; err: SupplierErrorOf }>()
</script>

<template>
  <PanelSection title="기존 거래 조건" sub="contracts 표 · 거래 이력이 있는 협력사만. 법규 검토(지급기한·연동제)에 쓰입니다.">
    <template #actions>
      <CheckOption v-model="form.hasContract">기존 거래 있음</CheckOption>
    </template>
    <FormGrid v-if="form.hasContract" :cols="4">
      <FormField id="r-pay" label="대금 지급기한 (일)" required :error="err('paymentDays')">
        <input id="r-pay" v-model="form.paymentDays" type="text" inputmode="numeric" placeholder="예: 60" :aria-invalid="!!err('paymentDays')" />
      </FormField>
      <FormField id="r-raw" label="원재료 비중 (%)" required :error="err('rawMaterialPct')">
        <input id="r-raw" v-model="form.rawMaterialPct" type="text" inputmode="decimal" placeholder="예: 40" :aria-invalid="!!err('rawMaterialPct')" />
      </FormField>
      <CheckOption v-model="form.priceLinkage" class="beside-field">납품대금 연동 약정</CheckOption>
      <CheckOption v-model="form.autoRenewal" class="beside-field">자동 갱신</CheckOption>
    </FormGrid>
    <p v-else class="muted">신규 후보는 비워 둡니다.</p>
  </PanelSection>
</template>

<style scoped>
/* 라벨이 있는 입력칸과 높이를 맞춘다 */
.beside-field {
  margin-top: 21px;
}
</style>
