<script setup lang="ts">
import { useId } from 'vue'

// 제목·설명·오른쪽 동작(actions 슬롯)이 있는 패널. 등록 화면의 각 입력 묶음에 쓴다
defineProps<{
  title: string
  sub?: string
  /** 제목 옆에 필수(*) 표시 */
  required?: boolean
}>()

const headingId = useId()
</script>

<template>
  <section class="panel panel-pad" :aria-labelledby="headingId">
    <div class="panel-title">
      <div>
        <h2 :id="headingId">{{ title }} <span v-if="required" class="req">*</span></h2>
        <p v-if="sub" class="sub">{{ sub }}</p>
      </div>
      <slot name="actions" />
    </div>
    <slot />
  </section>
</template>

<style scoped>
.req {
  color: var(--danger);
}
</style>
