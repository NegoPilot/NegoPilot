<script setup lang="ts">
// 테두리 있는 체크박스. 라벨은 기본 슬롯
const checked = defineModel<boolean>({ required: true })
</script>

<template>
  <label class="check"><input v-model="checked" type="checkbox" /> <slot /></label>
</template>

<style scoped>
.check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface);
  font-size: 13px;
  cursor: pointer;
}
.check input {
  margin: 0;
  width: 16px;
  height: 16px;
  accent-color: var(--primary);
}
</style>
