<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <div class="empty-state">
    <p class="empty-title">{{ title }}</p>
    <p v-if="$slots.default" class="muted"><slot /></p>
    <div v-if="$slots.action" class="empty-action"><slot name="action" /></div>
  </div>
</template>

<style scoped>
.empty-state {
  padding: 32px 16px;
  text-align: center;
}
.empty-title {
  margin: 0 0 4px;
  font-weight: 500;
}
.empty-state p.muted {
  margin: 0;
  font-size: 13px;
}
.empty-action {
  margin-top: 12px;
}
</style>
