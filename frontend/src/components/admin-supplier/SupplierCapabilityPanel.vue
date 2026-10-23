<script setup lang="ts">
import { computed, ref } from 'vue'

import { type AdminSupplierDetail, SOURCE_LABEL, replaceCapabilities } from '@/api/admin-suppliers'
import EditablePanel from '@/components/form/EditablePanel.vue'
import CapabilityRowsTable from '@/components/supplier-form/CapabilityRowsTable.vue'
import { useSupplierSave } from '@/composables/useSupplierSave'
import { formatCapacity } from '@/utils/format'
import { type CapabilityRow, type FieldErrors, capabilitiesInput, capabilityRows, emptyCapability, validateCapabilities } from '@/utils/supplierForm'

const props = defineProps<{ detail: AdminSupplierDetail }>()
const emit = defineEmits<{ saved: [detail: AdminSupplierDetail]; stale: [] }>()

const editing = ref(false)
const tried = ref(false)
const rows = ref<CapabilityRow[]>([])
const errors = computed(() => {
  const e: FieldErrors = {}
  validateCapabilities(rows.value, e)
  return e
})
function err(key: string) {
  return tried.value ? errors.value[key] : undefined
}
const { saving, error, save } = useSupplierSave((d) => emit('saved', d), () => emit('stale'))

function startEdit() {
  rows.value = capabilityRows(props.detail)
  if (!rows.value.length) rows.value.push(emptyCapability())
  tried.value = false
  error.value = null
  editing.value = true
}

async function submit() {
  tried.value = true
  if (Object.keys(errors.value).length) return
  const s = props.detail.supplier
  if (await save(() => replaceCapabilities(s.supplier_id, s.version, capabilitiesInput(rows.value)))) editing.value = false
}
</script>

<template>
  <EditablePanel
    title="생산능력"
    :sub="`출처 ${SOURCE_LABEL[detail.supplier.capability_source]} · 저장하면 전체 목록을 바꿉니다.`"
    :editing="editing"
    :saving="saving"
    :error="error"
    @edit="startEdit"
    @cancel="editing = false"
    @save="submit"
  >
    <div v-if="detail.capabilities.length" class="table-wrap">
      <table>
        <thead><tr><th scope="col">공정</th><th scope="col">소재</th><th scope="col">월 생산능력</th></tr></thead>
        <tbody>
          <tr v-for="c in detail.capabilities" :key="`${c.process}/${c.material}`">
            <td>{{ c.process }}</td>
            <td>{{ c.material }}</td>
            <td class="num">{{ formatCapacity(c.monthly_capacity) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted">생산능력 정보 없음</p>
    <template #edit>
      <CapabilityRowsTable :rows="rows" :err="err" />
      <button class="btn add" type="button" @click="rows.push(emptyCapability())">+ 공정 추가</button>
    </template>
  </EditablePanel>
</template>

<style scoped>
.add {
  margin-top: 10px;
}
</style>
