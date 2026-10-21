<script setup lang="ts">
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
</script>

<template>
  <main class="page">
    <EmptyState title="페이지를 찾을 수 없습니다">
      <template #action>
        <RouterLink :to="{ name: 'supplier-search' }" class="btn">협력사 탐색으로</RouterLink>
      </template>
    </EmptyState>
  </main>
</template>
