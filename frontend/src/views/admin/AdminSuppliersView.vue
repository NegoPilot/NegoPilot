<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  type AdminSupplierListItem,
  SOURCE_LABEL,
  STATUS_LABEL,
  type SupplierSource,
  type SupplierStatus,
  listSuppliers,
} from '@/api/admin-suppliers'
import NumberedPagination from '@/components/NumberedPagination.vue'
import { describeError } from '@/utils/errors'
import { formatDateTime, formatPercent, formatPpm } from '@/utils/format'

// 검색 조건과 페이지는 query에 둔다. 상세에서 돌아와도 같은 목록을 본다
const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 20
const draft = reactive({ q: '', source: '' as SupplierSource | '', status: '' as SupplierStatus | '' })
const rows = ref<AdminSupplierListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const loadError = ref<string | null>(null)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function queryText(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}
function pick<T extends string>(value: string, allowed: Record<T, string>): T | '' {
  return value in allowed ? (value as T) : ''
}

let controller: AbortController | null = null
let requestSequence = 0

async function load() {
  controller?.abort()
  const current = ++requestSequence
  controller = new AbortController()
  const applied = {
    q: queryText('q').trim(),
    source: pick(queryText('source'), SOURCE_LABEL),
    status: pick(queryText('status'), STATUS_LABEL),
    page: Math.max(1, Number.parseInt(queryText('page'), 10) || 1),
  }
  Object.assign(draft, { q: applied.q, source: applied.source, status: applied.status })
  page.value = applied.page
  loading.value = true
  loadError.value = null
  try {
    const result = await listSuppliers({
      q: applied.q || undefined,
      source: applied.source || undefined,
      status: applied.status || undefined,
      page: applied.page - 1,
      size: PAGE_SIZE,
    }, controller.signal)
    if (current !== requestSequence) return
    rows.value = result.items
    total.value = result.total
  } catch (error) {
    if (current !== requestSequence) return
    rows.value = []
    total.value = 0
    loadError.value = describeError(error).message
  } finally {
    if (current === requestSequence) loading.value = false
  }
}

watch(() => route.query, () => void load(), { immediate: true })
onUnmounted(() => controller?.abort())

function pushQuery(next: { q?: string; source?: string; status?: string; page?: number }) {
  const query: Record<string, string> = {}
  if (next.q) query.q = next.q
  if (next.source) query.source = next.source
  if (next.status) query.status = next.status
  if (next.page && next.page > 1) query.page = String(next.page)
  void router.push({ query })
}

function search() {
  pushQuery({ q: draft.q.trim(), source: draft.source, status: draft.status })
}
function changePage(next: number) {
  pushQuery({ q: queryText('q'), source: queryText('source'), status: queryText('status'), page: next })
}
</script>

<template>
  <main class="page-body">
    <p class="intro">협력사 DB의 기업을 찾고 상세에서 기본 정보·생산능력·품질·견적과 상태를 관리합니다. 비활성 기업은 검색과 추천에서 빠집니다.</p>

    <section class="panel panel-pad" aria-labelledby="suppliers-h" :aria-busy="loading">
      <div class="panel-title">
        <h2 id="suppliers-h">협력사 목록 <span v-if="!loading && !loadError">{{ total }}곳</span></h2>
        <div class="actions">
          <button class="btn" type="button" :disabled="loading" @click="load">새로고침</button>
          <RouterLink class="btn btn-primary" :to="{ name: 'admin-supplier-new' }">+ 협력사 등록</RouterLink>
        </div>
      </div>
      <form class="filters" role="search" @submit.prevent="search">
        <div class="field">
          <label for="s-q">협력사 검색</label>
          <input id="s-q" v-model="draft.q" type="search" maxlength="100" placeholder="협력사명 또는 ID" />
        </div>
        <div class="field">
          <label for="s-source">출처</label>
          <select id="s-source" v-model="draft.source">
            <option value="">전체</option>
            <option v-for="(label, key) in SOURCE_LABEL" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div class="field">
          <label for="s-status">상태</label>
          <select id="s-status" v-model="draft.status">
            <option value="">전체</option>
            <option v-for="(label, key) in STATUS_LABEL" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <button class="btn btn-primary" type="submit" :disabled="loading">검색</button>
      </form>

      <p v-if="loading" class="state-message muted" role="status">협력사 목록을 불러오는 중입니다.</p>
      <div v-else-if="loadError" class="error-message" role="alert">
        <span>{{ loadError }}</span>
        <button class="btn" type="button" @click="load">다시 시도</button>
      </div>
      <p v-else-if="!rows.length" class="state-message muted">조건에 맞는 협력사가 없습니다. 검색어나 필터를 바꿔 주세요.</p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">ID</th><th scope="col">협력사명</th><th scope="col">지역</th><th scope="col">출처</th><th scope="col">상태</th>
              <th scope="col">공정</th><th scope="col">불량률</th><th scope="col">납기 준수율</th><th scope="col">인증</th><th scope="col">수정 시각</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in rows" :key="s.supplier_id" :class="{ inactive: s.status === 'INACTIVE' }">
              <td><code>{{ s.supplier_id }}</code></td>
              <td><RouterLink :to="{ name: 'admin-supplier-detail', params: { supplierId: s.supplier_id } }">{{ s.name }}</RouterLink></td>
              <td>{{ s.region }}</td>
              <td><span class="badge" :class="s.source === 'MANUAL' ? '' : 'badge-gray'">{{ SOURCE_LABEL[s.source] }}</span></td>
              <td><span class="badge" :class="s.status === 'ACTIVE' ? 'badge-gray' : 'badge-warning'">{{ STATUS_LABEL[s.status] }}</span></td>
              <td class="num">{{ s.capability_count }}개</td>
              <td class="num">{{ formatPpm(s.defect_ppm) }}</td>
              <td class="num">{{ formatPercent(s.on_time_rate === null ? null : s.on_time_rate * 100) }}</td>
              <td>{{ [s.iatf16949 && 'IATF', s.iso14001 && 'ISO'].filter(Boolean).join(' · ') || '-' }}</td>
              <td class="num">{{ formatDateTime(s.updated_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <NumberedPagination v-if="!loading && !loadError && total" :page="page - 1" :total-pages="pageCount" :total="total" :loading="loading" label="협력사 목록 페이지" @select="(p) => changePage(p + 1)" />
    </section>
  </main>
</template>

<style scoped>
.intro { margin: 0; font-size: 12px; color: var(--text-2); }
.actions { display: flex; gap: 8px; }
.error-message { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; color: var(--danger); background: var(--surface-2); border-radius: var(--radius); font-size: 12px; }
.filters { display: grid; grid-template-columns: minmax(180px, 2.2fr) repeat(2, minmax(110px, 1fr)) minmax(100px, 1fr); gap: 12px; align-items: end; margin-bottom: 16px; }
.filters .field > label { font-weight: 600; color: var(--text); }
.state-message { padding: 24px 0; text-align: center; }
.inactive td { color: var(--text-3); }
code { font-size: 12px; }
@media (max-width: 760px) { .filters { grid-template-columns: minmax(0, 1fr); } }
</style>
