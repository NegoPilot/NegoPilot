<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { listMembers, formatMemberDate, type AdminMember } from '@/api/admin-members'
import { describeError } from '@/utils/errors'

const loading = ref(false)
const error = ref<string | null>(null)
const summary = ref<{ total: number; pending: number; active: number } | null>(null)
const requests = ref<AdminMember[]>([])
let controller: AbortController | null = null

async function load() {
  controller?.abort()
  const current = new AbortController()
  controller = current
  loading.value = true
  error.value = null
  summary.value = null
  requests.value = []
  try {
    const [all, pending, active] = await Promise.all([
      listMembers({ size: 1 }, current.signal),
      listMembers({ status: 'PENDING', size: 3 }, current.signal),
      listMembers({ status: 'ACTIVE', size: 1 }, current.signal),
    ])
    if (current.signal.aborted) return
    summary.value = { total: all.total, pending: pending.total, active: active.total }
    requests.value = pending.items
  } catch (failure) {
    if (!current.signal.aborted) error.value = describeError(failure).message
  } finally {
    if (controller === current) loading.value = false
  }
}

onMounted(load)
onUnmounted(() => controller?.abort())
</script>

<template>
  <main class="page-body">
    <div class="panel-title">
      <p class="intro">가입 요청과 회원 상태를 확인하세요.</p>
      <button class="btn" type="button" :disabled="loading" @click="load">새로고침</button>
    </div>
    <p v-if="error" class="field-error" role="alert">{{ error }} 새로고침으로 다시 조회해 주세요.</p>
    <p v-if="loading" role="status">회원 현황을 불러오는 중입니다.</p>

    <div v-if="summary" class="kpis">
      <RouterLink :to="{ name: 'admin-members' }" class="panel kpi-card">
        <span>전체 회원</span><strong>{{ summary.total }}명</strong>
      </RouterLink>
      <RouterLink :to="{ name: 'admin-approvals' }" class="panel kpi-card">
        <span>가입 승인 대기</span><strong>{{ summary.pending }}명</strong>
      </RouterLink>
      <div class="panel kpi-card"><span>활성 회원</span><strong>{{ summary.active }}명</strong></div>
    </div>

    <section v-if="summary" class="panel panel-pad" aria-labelledby="requests-heading">
      <div class="panel-title">
        <div>
          <h2 id="requests-heading">최근 가입 요청</h2>
          <p class="muted">신청자를 확인한 뒤 가입 승인 화면에서 처리해 주세요.</p>
        </div>
        <RouterLink :to="{ name: 'admin-approvals' }">가입 승인으로 이동 →</RouterLink>
      </div>
      <div v-if="requests.length" class="table-wrap">
        <table>
          <thead><tr><th>이름</th><th>이메일</th><th>부서</th><th>신청일</th><th>처리</th></tr></thead>
          <tbody>
            <tr v-for="member in requests" :key="member.id">
              <td>{{ member.name }}</td><td>{{ member.email }}</td><td>{{ member.department }}</td>
              <td>{{ formatMemberDate(member.created_at) }}</td>
              <td><RouterLink :to="{ name: 'admin-members', query: { request: member.email } }">회원 확인 →</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">대기 중인 가입 신청이 없습니다.</p>
    </section>
  </main>
</template>

<style scoped>
.intro { margin: 0; color: var(--text-2); }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.kpi-card { display: flex; flex-direction: column; gap: 10px; padding: 20px; color: var(--text); text-decoration: none; }
.kpi-card strong { font-size: 24px; }
.kpi-card span { color: var(--text-2); }
a.kpi-card:hover { border-color: var(--primary); }
</style>
