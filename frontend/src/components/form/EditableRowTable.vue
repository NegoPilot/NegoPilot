<script setup lang="ts" generic="T">
// 행을 더하고 지우는 입력 표. 각 행의 <td>들은 기본 슬롯으로 받고, 삭제 칸은 여기서 붙인다
const props = defineProps<{
  headers: string[]
  rows: T[]
  /** 이 수보다 적게는 지울 수 없다 */
  minRows?: number
}>()
defineEmits<{ remove: [index: number] }>()
defineSlots<{ default(scope: { row: T; index: number }): unknown }>()

function canRemove(): boolean {
  return props.rows.length > (props.minRows ?? 0)
}
</script>

<template>
  <div class="table-wrap">
    <table class="input-table">
      <thead>
        <tr>
          <th v-for="h in headers" :key="h">{{ h }}</th>
          <th><span class="sr-only">삭제</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="index">
          <slot :row="row" :index="index" />
          <td class="del">
            <button class="link-btn" type="button" :disabled="!canRemove()" @click="$emit('remove', index)">삭제</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.input-table :deep(td) {
  padding: 6px;
  vertical-align: top;
  background: transparent;
}
.input-table :deep(input),
.input-table :deep(select) {
  height: 34px;
}
.input-table :deep(input[type='date']) {
  padding: 0 8px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  font: inherit;
  font-size: 13px;
  width: 100%;
}
.input-table .del {
  width: 48px;
  text-align: center;
  vertical-align: middle;
}
.link-btn:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}
</style>
