<script setup lang="ts">
import EditableRowTable from '@/components/form/EditableRowTable.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import { MATERIALS, PROCESSES, type Process } from '@/data/dataCatalog'
import { type SupplierErrorOf, type SupplierForm, emptyCapability } from '@/utils/supplierForm'

defineProps<{ form: SupplierForm; err: SupplierErrorOf }>()

function materialsFor(process: string): string[] {
  return MATERIALS[process as Process] ?? []
}
</script>

<template>
  <PanelSection title="생산능력" required sub="capabilities 표 · 공정·소재 조합마다 한 줄. 협력사 검색은 이 표로 찾습니다.">
    <template #actions>
      <button class="btn" type="button" @click="form.capabilities.push(emptyCapability())">+ 공정 추가</button>
    </template>
    <p v-if="err('capabilities')" class="field-error">{{ err('capabilities') }}</p>
    <EditableRowTable
      v-slot="{ row: c, index: i }"
      :headers="['공정', '소재', '월 생산능력 (개)']"
      :rows="form.capabilities"
      :min-rows="1"
      @remove="(i) => form.capabilities.splice(i, 1)"
    >
      <td>
        <select v-model="c.process" :aria-label="`${i + 1}번째 공정`" :aria-invalid="!!err(`cap.${i}.process`)">
          <option value="">선택</option>
          <option v-for="p in PROCESSES" :key="p" :value="p">{{ p }}</option>
        </select>
      </td>
      <td>
        <input v-model="c.material" type="text" :list="`cap-mat-${i}`" :aria-label="`${i + 1}번째 소재`" placeholder="예: ADC12" :aria-invalid="!!err(`cap.${i}.material`)" />
        <datalist :id="`cap-mat-${i}`"><option v-for="m in materialsFor(c.process)" :key="m" :value="m" /></datalist>
        <span v-if="err(`cap.${i}.material`)" class="field-error">{{ err(`cap.${i}.material`) }}</span>
      </td>
      <td>
        <input v-model="c.monthlyCapacity" type="text" inputmode="numeric" :aria-label="`${i + 1}번째 월 생산능력`" placeholder="예: 40,000" :aria-invalid="!!err(`cap.${i}.monthlyCapacity`)" />
      </td>
    </EditableRowTable>
  </PanelSection>
</template>
