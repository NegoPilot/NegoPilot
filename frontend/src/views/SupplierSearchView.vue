<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { getCatalog, searchSuppliers } from '@/api/suppliers'
import type { Catalog, SupplierList } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import ErrorNotice from '@/components/ErrorNotice.vue'
import SupplierSearchForm from '@/components/SupplierSearchForm.vue'
import { isApiError } from '@/utils/errors'
import { formatCapacity } from '@/utils/format'
import {
  type SearchFormValues,
  searchValuesFromQuery,
  searchValuesToParams,
  searchValuesToQuery,
} from '@/utils/search'

const route = useRoute()
const router = useRouter()

const catalog = ref<Catalog | null>(null)
const catalogError = ref(false)

const values = computed(() => searchValuesFromQuery(route.query))
const result = ref<SupplierList | null>(null)
const loading = ref(false)
const error = ref<unknown>(null)
const sentCapacity = ref(false)
let latest = 0

// min_monthly_capacity는 계약 변경(#43) 전까지 Spring이 400으로 거절한다
const errorMessage = computed(() =>
  sentCapacity.value && isApiError(error.value, 'VALIDATION_ERROR')
    ? '최소 월 생산능력 조건은 아직 서버에서 지원하지 않습니다. 이 조건을 비우고 다시 검색해 주세요.'
    : null,
)

async function runSearch() {
  const params = searchValuesToParams(values.value)
  if (!params) {
    result.value = null
    error.value = null
    return
  }
  const id = ++latest
  loading.value = true
  error.value = null
  sentCapacity.value = params.min_monthly_capacity !== undefined
  try {
    const data = await searchSuppliers(params)
    if (id === latest) result.value = data
  } catch (e) {
    if (id === latest) {
      error.value = e
      result.value = null
    }
  } finally {
    if (id === latest) loading.value = false
  }
}

watch(() => route.query, runSearch, { immediate: true })

onMounted(async () => {
  try {
    catalog.value = await getCatalog()
  } catch {
    catalogError.value = true
  }
})

function onSubmit(next: SearchFormValues) {
  const query = searchValuesToQuery(next)
  // 같은 조건으로 다시 누르면 URL이 그대로라 직접 다시 조회한다
  if (JSON.stringify(query) === JSON.stringify(searchValuesToQuery(values.value))) {
    runSearch()
  } else {
    router.push({ query })
  }
}

function onReset() {
  router.push({ query: {} })
}
</script>

<template>
  <main class="page">
    <header class="page-head">
      <h1>협력사 탐색</h1>
      <p class="muted">공정은 꼭 입력해 주세요. 나머지 조건은 비워 두면 전체로 검색합니다.</p>
    </header>

    <SupplierSearchForm
      :catalog="catalog"
      :catalog-error="catalogError"
      :initial="values"
      :loading="loading"
      @submit="onSubmit"
      @reset="onReset"
    />

    <section class="panel" aria-labelledby="result-h" :aria-busy="loading">
      <div class="panel-head">
        <h2 id="result-h">
          검색 결과
          <span v-if="result" class="muted">({{ result.returned_count }})</span>
        </h2>
        <span v-if="result && result.returned_count === result.limit" class="muted">
          최대 {{ result.limit }}곳까지 보여 줍니다. 조건을 좁혀 보세요.
        </span>
      </div>

      <p v-if="loading" class="panel-body" aria-live="polite">검색 중…</p>
      <ErrorNotice v-else-if="error" :error="error" :message="errorMessage" :retry="runSearch" />
      <EmptyState v-else-if="!result" title="공정을 입력하고 검색하세요" />
      <EmptyState v-else-if="result.items.length === 0" title="조건에 맞는 협력사가 없습니다">
        조건을 줄이거나 다른 공정으로 검색해 보세요.
      </EmptyState>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>협력사</th>
              <th>지역</th>
              <th>IATF 16949</th>
              <th>조건에 맞는 생산능력</th>
              <th><span class="sr-only">상세</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in result.items" :key="s.supplier_id">
              <td>
                <RouterLink :to="{ name: 'supplier-detail', params: { supplierId: s.supplier_id } }" class="name">
                  {{ s.name }}
                </RouterLink>
                <div class="muted">{{ s.supplier_id }}</div>
              </td>
              <td>{{ s.region }}</td>
              <td>{{ s.iatf16949 ? '인증' : '없음' }}</td>
              <td>
                <ul class="caps">
                  <li v-for="c in s.matched_capabilities" :key="`${c.process}-${c.material}`">
                    {{ c.process }} · {{ c.material }}
                    <span class="cap-num">{{ formatCapacity(c.monthly_capacity) }}</span>
                  </li>
                </ul>
              </td>
              <td class="num">
                <RouterLink :to="{ name: 'supplier-detail', params: { supplierId: s.supplier_id } }" class="btn">
                  상세 보기
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.page-head h1 {
  font-size: 20px;
}
.page-head p {
  margin: 0;
}
.name {
  font-weight: 500;
  color: var(--text);
  text-decoration: none;
}
.name:hover {
  color: var(--primary);
  text-decoration: underline;
}
.caps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cap-num {
  margin-left: 8px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
</style>
