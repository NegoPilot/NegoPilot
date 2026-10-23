<script setup lang="ts">
import { RouterLink, type RouteLocationRaw } from 'vue-router'

// 저장한 뒤 화면 위에 띄우는 안내. 일부만 처리됐으면 warning에 남은 일을 적는다
defineProps<{
  message: string
  to?: RouteLocationRaw
  linkText?: string
  warning?: string | null
}>()
</script>

<template>
  <div class="message" role="status">
    <p>{{ message }} <RouterLink v-if="to" :to="to">{{ linkText }}</RouterLink></p>
    <p v-if="warning" class="warning">{{ warning }}</p>
  </div>
</template>

<style scoped>
.message {
  padding: 10px 14px;
  font-size: 12px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
}
.message p {
  margin: 0;
}
.warning {
  margin-top: 4px !important;
  color: var(--danger);
}
</style>
