<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError } from '@/api/client'
import NumberedPagination from '@/components/NumberedPagination.vue'
import {
  type AdminMember,
  formatMemberDate,
  listMembers,
  updateMemberStatus,
} from '@/api/admin-members'

const route = useRoute()
const draft = reactive({ keyword: '' })
const applied = reactive({ ...draft })

const PAGE_SIZE = 20
const rows = ref<AdminMember[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const saving = ref<{ id: string; status: 'ACTIVE' | 'REJECTED' } | null>(null)
const message = ref<string | null>(null)
const loadError = ref<string | null>(null)
const actionError = ref<string | null>(null)
const busy = computed(() => loading.value || saving.value !== null)
const requestedEmail = computed(() => typeof route.query.request === 'string' ? route.query.request.trim() : '')
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

let controller: AbortController | null = null
let requestSequence = 0
let disposed = false

function errorText(error: unknown, action = false): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '로그인 세션이 만료되었습니다. 다시 로그인해 주세요.'
    if (error.status === 403) return '관리자 권한을 확인할 수 없습니다. 계정 권한을 확인한 뒤 다시 시도해 주세요.'
    if (error.status === 404 || error.status === 409) return '회원 정보가 변경되었거나 처리할 수 없는 상태입니다. 목록을 새로고침한 뒤 확인해 주세요.'
    if (error.code === 'CLIENT_TIMEOUT') return '서버 응답이 지연되고 있습니다. 새로고침으로 처리 결과를 확인한 뒤 다시 시도해 주세요.'
    if (error.code === 'NETWORK_ERROR') return '서버에 연결할 수 없습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.'
  }
  return action ? '가입 요청을 처리하지 못했습니다. 목록을 새로고침한 뒤 다시 시도해 주세요.' : '가입 신청 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'
}

function clearMembers() {
  controller?.abort()
  requestSequence++
  loading.value = false
  rows.value = []
  total.value = 0
}

async function loadMembers() {
  if (disposed) return
  controller?.abort()
  const current = ++requestSequence
  controller = new AbortController()
  loading.value = true
  loadError.value = null
  rows.value = []
  total.value = 0
  try {
    const result = await listMembers({
      search: applied.keyword.trim() || undefined,
      status: 'PENDING',
      page: page.value - 1,
      size: PAGE_SIZE,
    }, controller.signal)
    if (disposed || current !== requestSequence) return
    const lastPage = Math.max(1, Math.ceil(result.total / PAGE_SIZE))
    if (page.value > lastPage) {
      page.value = lastPage
      await loadMembers()
      return
    }
    rows.value = result.items
    total.value = result.total
    page.value = result.page + 1
  } catch (error) {
    if (disposed || current !== requestSequence) return
    loadError.value = errorText(error)
  } finally {
    if (!disposed && current === requestSequence) loading.value = false
  }
}

function refresh() {
  if (busy.value) return
  message.value = null
  actionError.value = null
  void loadMembers()
}

function search() {
  if (busy.value) return
  Object.assign(applied, draft)
  page.value = 1
  refresh()
}

function changePage(next: number) {
  if (busy.value || next < 1 || next > pageCount.value || next === page.value) return
  page.value = next
  refresh()
}

async function decide(member: AdminMember, status: 'ACTIVE' | 'REJECTED') {
  if (busy.value || member.status !== 'PENDING') return
  if (status === 'REJECTED' && !window.confirm(`${member.name} 님의 가입 신청을 반려할까요?`)) return
  saving.value = { id: member.id, status }
  message.value = null
  actionError.value = null
  try {
    await updateMemberStatus(member.id, status)
    if (disposed) return
    message.value = status === 'ACTIVE' ? `${member.name} 님의 가입을 승인했습니다.` : `${member.name} 님의 가입 신청을 반려했습니다.`
    await loadMembers()
  } catch (error) {
    if (disposed) return
    if (error instanceof ApiError && [401, 403].includes(error.status)) clearMembers()
    actionError.value = errorText(error, true)
  } finally {
    if (!disposed) saving.value = null
  }
}

watch(requestedEmail, (email) => {
  Object.assign(draft, { keyword: email })
  Object.assign(applied, draft)
  page.value = 1
  message.value = null
  actionError.value = null
  void loadMembers()
}, { immediate: true })

onUnmounted(() => {
  disposed = true
  controller?.abort()
})
</script>

<template>
  <main class="page-body">
    <p class="intro">승인 대기 중인 가입 신청을 확인하고 처리합니다. 승인된 가입자는 일반 회원으로 이용할 수 있습니다.</p>
    <p v-if="message" class="message" role="status">{{ message }}</p>
    <div v-if="actionError" class="error-message" role="alert">
      <span>{{ actionError }}</span>
      <button class="btn" type="button" :disabled="busy" @click="refresh">목록 다시 조회</button>
    </div>

    <section class="panel panel-pad" aria-labelledby="all-req-h" :aria-busy="busy">
      <div class="panel-title">
        <h2 id="all-req-h">가입 승인 요청 <span v-if="!loading && !loadError">{{ total }}건</span></h2>
        <button class="btn" type="button" :disabled="busy" @click="refresh">새로고침</button>
      </div>
      <form class="filters" role="search" @submit.prevent="search">
        <div class="field">
          <label for="approval-keyword">신청자 검색</label>
          <input id="approval-keyword" v-model="draft.keyword" type="search" maxlength="254" placeholder="이름 또는 이메일" :disabled="busy" />
        </div>
        <button class="btn btn-primary" type="submit" :disabled="busy">검색</button>
      </form>
      <p v-if="loading" class="state-message muted" role="status">가입 신청 목록을 불러오는 중입니다.</p>
      <div v-else-if="loadError" class="error-message" role="alert">
        <span>{{ loadError }}</span>
        <button class="btn" type="button" :disabled="busy" @click="refresh">다시 시도</button>
      </div>
      <p v-else-if="!rows.length" class="state-message muted">{{ applied.keyword ? '검색 조건에 맞는 가입 신청이 없습니다.' : '대기 중인 가입 신청이 없습니다.' }}</p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th scope="col">신청자</th><th scope="col">이메일</th><th scope="col">부서</th><th scope="col">직책</th><th scope="col">가입 신청일</th><th scope="col">처리</th></tr>
          </thead>
          <tbody>
            <tr v-for="member in rows" :key="member.id">
              <td>{{ member.name }}</td>
              <td class="email">{{ member.email }}</td>
              <td>{{ member.department || '-' }}</td>
              <td>{{ member.job_title || '-' }}</td>
              <td class="num">{{ formatMemberDate(member.created_at) }}</td>
              <td>
                <div v-if="member.status === 'PENDING'" class="row-actions">
                  <button class="btn" type="button" :disabled="busy" :aria-label="`${member.name} 가입 반려`" @click="decide(member, 'REJECTED')">{{ saving?.id === member.id && saving.status === 'REJECTED' ? '반려 중…' : '반려' }}</button>
                  <button class="btn btn-primary" type="button" :disabled="busy" :aria-label="`${member.name} 가입 승인`" @click="decide(member, 'ACTIVE')">{{ saving?.id === member.id && saving.status === 'ACTIVE' ? '승인 중…' : '가입 승인' }}</button>
                </div>
                <span v-else class="muted">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <NumberedPagination v-if="!loading && !loadError && total" :page="page - 1" :total-pages="pageCount" :total="total" :loading="busy" label="가입 신청 페이지" @select="(p) => changePage(p + 1)" />
    </section>
  </main>
</template>

<style scoped>
.intro { margin: 0; font-size: 12px; color: var(--text-2); }
.message { margin: 0; padding: 10px 14px; font-size: 12px; background: var(--primary-soft); color: var(--primary); border-radius: var(--radius); }
.error-message { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; color: var(--danger); background: var(--surface-2); border-radius: var(--radius); font-size: 12px; }
.filters { display: grid; grid-template-columns: minmax(180px, 1fr) auto; gap: 12px; align-items: end; margin-bottom: 16px; }
.filters .field > label { font-weight: 600; color: var(--text); }
.state-message { padding: 24px 0; text-align: center; }
.email { overflow-wrap: anywhere; }
.row-actions { display: flex; gap: 6px; white-space: nowrap; }
.row-actions .btn { min-height: 30px; padding: 4px 10px; font-size: 12px; }
button:disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 760px) { .filters { grid-template-columns: minmax(0, 1fr); } }
</style>
