<script setup lang="ts">
import PanelSection from './PanelSection.vue'

// 보기(기본 슬롯)와 수정(edit 슬롯)을 오가는 패널. 수정 중에는 취소·저장 버튼과 저장 오류를 보여 준다
defineProps<{
  title: string
  sub?: string
  editing: boolean
  saving?: boolean
  error?: string | null
  /** 수정 버튼 문구 (기본: 수정) */
  editLabel?: string
  /** 수정할 수 없으면 이유를 적는다. 버튼 대신 이유를 보여 준다 */
  locked?: string | null
}>()
defineEmits<{ edit: []; cancel: []; save: [] }>()
</script>

<template>
  <PanelSection :title="title" :sub="sub">
    <template #actions>
      <div v-if="editing" class="actions">
        <button class="btn" type="button" :disabled="saving" @click="$emit('cancel')">취소</button>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="$emit('save')">{{ saving ? '저장 중…' : '저장' }}</button>
      </div>
      <span v-else-if="locked" class="locked">{{ locked }}</span>
      <button v-else class="btn" type="button" @click="$emit('edit')">{{ editLabel ?? '수정' }}</button>
    </template>
    <p v-if="editing && error" class="field-error save-error" role="alert">{{ error }}</p>
    <slot v-if="editing" name="edit" />
    <slot v-else />
  </PanelSection>
</template>

<style scoped>
.actions {
  display: flex;
  gap: 8px;
}
.locked {
  font-size: 12px;
  color: var(--text-3);
}
.save-error {
  margin: 0 0 12px;
}
</style>
