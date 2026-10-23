<script setup lang="ts">
import { AUDIT_ACTION_LABEL, type AdminAuditEvent } from '@/api/admin-suppliers'
import PanelSection from '@/components/form/PanelSection.vue'
import { formatDateTime } from '@/utils/format'

// 관리자 변경 이력. 서버가 최신 100건을 최신 순으로 준다
defineProps<{ audit: AdminAuditEvent[] }>()
</script>

<template>
  <PanelSection title="변경 이력" sub="관리자가 바꾼 내용입니다. 최신 100건까지 보입니다.">
    <div v-if="audit.length" class="table-wrap">
      <table>
        <thead><tr><th scope="col">시각</th><th scope="col">작업</th><th scope="col">수정자 ID</th><th scope="col">변경 내용</th></tr></thead>
        <tbody>
          <tr v-for="a in audit" :key="a.event_id">
            <td class="num">{{ formatDateTime(a.created_at) }}</td>
            <td>{{ AUDIT_ACTION_LABEL[a.action] ?? a.action }}</td>
            <td><code :title="a.actor_user_id">{{ a.actor_user_id.slice(0, 8) }}</code></td>
            <td>
              <details>
                <summary>보기</summary>
                <div class="diff">
                  <div><strong>이전</strong><pre>{{ a.before_data === null ? '-' : JSON.stringify(a.before_data, null, 2) }}</pre></div>
                  <div><strong>이후</strong><pre>{{ JSON.stringify(a.after_data, null, 2) }}</pre></div>
                </div>
              </details>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted">관리자가 바꾼 기록이 없습니다.</p>
  </PanelSection>
</template>

<style scoped>
summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--primary);
}
.diff {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 6px;
}
.diff strong {
  font-size: 11px;
}
.diff pre {
  max-height: 220px;
  overflow: auto;
  margin: 4px 0 0;
  padding: 8px;
  font-size: 11px;
  background: var(--surface-2);
  border-radius: var(--radius);
}
code {
  font-size: 12px;
}
</style>
