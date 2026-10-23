<script setup lang="ts">
defineProps<{ cols: 2 | 3 | 4 }>()
</script>

<template>
  <div class="form-grid" :class="`cols-${cols}`"><slot /></div>
</template>

<style scoped>
.form-grid {
  display: grid;
  gap: 14px 12px;
  align-items: start;
}
.cols-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.cols-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.cols-4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
@media (max-width: 760px) {
  .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
