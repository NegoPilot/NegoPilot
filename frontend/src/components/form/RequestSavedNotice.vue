<script setup lang="ts">
import { RouterLink } from 'vue-router'

// 데이터 등록 요청을 저장한 뒤 화면 위에 띄우는 안내
defineProps<{ message: string }>()
</script>

<template>
  <p class="message" role="status">
    {{ message }} <RouterLink :to="{ name: 'admin-data' }">등록 요청 목록 보기 →</RouterLink>
  </p>
</template>

<style scoped>
.message {
  margin: 0;
  padding: 10px 14px;
  font-size: 12px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
}
</style>
