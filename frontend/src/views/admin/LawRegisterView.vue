<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import FormField from '@/components/form/FormField.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import RequestSavedNotice from '@/components/form/RequestSavedNotice.vue'
import { LEGALIZE_REPO, STORED_LAWS, lawVersionsAsOf } from '@/data/dataCatalog'
import { downloadJson, useDataRequests } from '@/stores/dataRequests'
import {
  LAW_KEY_RE,
  LAW_KINDS,
  type RawLawSummary,
  aliasKeys,
  isLegalizePath,
  readRawLaw,
  suggestLegalizePath,
} from '@/utils/lawForm'

const requests = useDataRequests()
const today = new Intl.DateTimeFormat('sv-SE').format(new Date())

const pendingLawKeys = computed(
  () => new Set(requests.state.items.filter((r) => r.kind === 'law').map((r) => r.target)),
)
const lawRows = computed(() =>
  STORED_LAWS.map((law) => ({ ...law, ...lawVersionsAsOf(law, today) })),
)
// server.py ALIASES와 같은 규칙(공백·가운뎃점 제거)으로 이미 쓰는 이름
const usedAliases = new Set(STORED_LAWS.flatMap((l) => aliasKeys(`${l.name}, ${l.alias}`)))

type Mode = 'legalize' | 'upload'
const mode = ref<Mode>('legalize')
const tried = ref(false)
const done = ref<string | null>(null)

// ── legalize-kr에서 받기 ──
const fetchForm = reactive({ name: '', kind: '법률' as string, lawKey: '', aliases: '', path: '', versions: '6' })
const pathEdited = ref(false)
watch(
  () => [fetchForm.name, fetchForm.kind],
  () => {
    if (!pathEdited.value) fetchForm.path = suggestLegalizePath(fetchForm.name, fetchForm.kind)
  },
)

function keyProblem(key: string): string | null {
  if (!LAW_KEY_RE.test(key)) return '영문 소문자로 시작하는 영문·숫자·밑줄 (예: fair_trade_act)'
  if (STORED_LAWS.some((l) => l.key === key)) return '이미 저장된 법령 키입니다. 개정본 추가는 원문 파일 올리기를 쓰세요.'
  if (pendingLawKeys.value.has(key)) return '같은 키로 등록 요청한 법령이 있습니다.'
  return null
}

const fetchErrors = computed(() => {
  const e: Record<string, string> = {}
  if (!fetchForm.name.trim()) e.name = '법령명을 입력해 주세요.'
  const kp = keyProblem(fetchForm.lawKey.trim())
  if (kp) e.lawKey = kp
  if (!isLegalizePath(fetchForm.path)) e.path = 'kr/법령명/법률.md 형식입니다.'
  const v = Number(fetchForm.versions)
  if (!Number.isInteger(v) || v < 1 || v > 10) e.versions = '1~10'
  const clash = aliasKeys(fetchForm.aliases).find((a) => usedAliases.has(a))
  if (clash) e.aliases = `'${clash}'는 이미 다른 법령의 별칭입니다.`
  return e
})

const fetchPayload = computed(() => {
  const key = fetchForm.lawKey.trim()
  return {
    method: 'legalize-kr',
    law_key: key,
    name: fetchForm.name.trim(),
    kind: fetchForm.kind,
    target: { law_key: key, path: fetchForm.path.trim() },
    aliases: Object.fromEntries(aliasKeys(`${fetchForm.name}, ${fetchForm.aliases}`).map((a) => [a, key])),
    versions: Number(fetchForm.versions),
  }
})

// ── 원문 파일 올리기 ──
const uploadForm = reactive({ lawKey: '', newKey: '' })
const file = ref<{ name: string; size: number; text: string } | null>(null)
const summary = ref<RawLawSummary | null>(null)
const fileError = ref<string | null>(null)
const MAX_FILE = 2 * 1024 * 1024

async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const f = input.files?.[0]
  file.value = null
  summary.value = null
  fileError.value = null
  if (!f) return
  if (!/\.md$/i.test(f.name)) {
    fileError.value = 'Markdown(.md) 파일만 올릴 수 있습니다.'
    return
  }
  if (f.size > MAX_FILE) {
    fileError.value = '2MB 이하 파일만 올릴 수 있습니다.'
    return
  }
  const text = await f.text()
  file.value = { name: f.name, size: f.size, text }
  summary.value = readRawLaw(text)
}

const uploadKey = computed(() => (uploadForm.lawKey === '__new' ? uploadForm.newKey.trim() : uploadForm.lawKey))
const uploadErrors = computed(() => {
  const e: Record<string, string> = {}
  if (!uploadForm.lawKey) e.lawKey = '법령을 골라 주세요.'
  else if (uploadForm.lawKey === '__new') {
    const kp = keyProblem(uploadForm.newKey.trim())
    if (kp) e.newKey = kp
  }
  if (!file.value || !summary.value) e.file = fileError.value ?? '원문 파일을 올려 주세요.'
  else if (summary.value.problems.length) e.file = '원문 형식을 확인해 주세요.'
  return e
})
const rawPath = computed(() =>
  summary.value?.effective && uploadKey.value ? `data/raw/${uploadKey.value}/${summary.value.effective}_manual.md` : null,
)
const uploadPayload = computed(() => ({
  method: 'upload',
  law_key: uploadKey.value,
  file_name: file.value?.name ?? null,
  raw_path: rawPath.value,
  title: summary.value?.title ?? null,
  promulgated: summary.value?.promulgated ?? null,
  effective: summary.value?.effective ?? null,
  article_count: summary.value?.articleCount ?? 0,
  content: file.value?.text ?? null,
}))

// ── 공통 ──
const errors = computed(() => (mode.value === 'legalize' ? fetchErrors.value : uploadErrors.value))
function err(key: string): string | undefined {
  return tried.value ? errors.value[key] : undefined
}
const payload = computed(() => (mode.value === 'legalize' ? fetchPayload.value : uploadPayload.value))

function setMode(next: Mode) {
  mode.value = next
  tried.value = false
}

function submit() {
  tried.value = true
  done.value = null
  if (Object.keys(errors.value).length) return
  if (mode.value === 'legalize') {
    const p = fetchPayload.value
    requests.add('law', p.law_key, `${p.name} · legalize-kr 최근 ${p.versions}개 판`, p)
    done.value = `${p.name} 등록 요청을 저장했습니다.`
    Object.assign(fetchForm, { name: '', kind: '법률', lawKey: '', aliases: '', path: '', versions: '6' })
    pathEdited.value = false
  } else {
    const p = uploadPayload.value
    requests.add('law', p.law_key, `${p.title ?? p.file_name} · 시행 ${p.effective} 원문`, p)
    done.value = `${p.title ?? p.file_name} 원문 등록 요청을 저장했습니다.`
    Object.assign(uploadForm, { lawKey: '', newKey: '' })
    file.value = null
    summary.value = null
  }
  tried.value = false
}

const downloadName = computed(() => `law-${(mode.value === 'legalize' ? fetchForm.lawKey : uploadKey.value) || 'draft'}.json`)
</script>

<template>
  <main class="page-body">
    <p class="intro">
      law-mcp는 DB가 아니라 법령 원문 파일을 색인해 읽습니다. 새 법령은 legalize-kr(국가법령정보센터 원문)에서 받거나 같은 형식의 원문 파일을 올려 추가합니다.
    </p>
    <RequestSavedNotice v-if="done" :message="done" />

    <PanelSection :title="`저장된 법령 ${STORED_LAWS.length}개`" :sub="`기준일 ${today} · 시행 예정 판이 있으면 오늘 기준 답변은 현재 시행 판으로 합니다.`">
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>법령 키</th><th>법령명</th><th>별칭</th><th>현재 시행 판</th><th>시행 예정 판</th><th>저장 판</th></tr>
          </thead>
          <tbody>
            <tr v-for="l in lawRows" :key="l.key">
              <td><code>{{ l.key }}</code></td>
              <td>{{ l.name }}</td>
              <td>{{ l.alias }}</td>
              <td class="num">{{ l.current ?? '-' }}</td>
              <td class="num">
                <span v-if="l.upcoming.length" class="badge badge-warning">{{ l.upcoming.join(', ') }}</span>
                <template v-else>-</template>
              </td>
              <td class="num">{{ l.versions.length }}개</td>
            </tr>
          </tbody>
        </table>
      </div>
    </PanelSection>

    <div class="layout">
      <PanelSection title="법령 추가" sub="새 법령을 받거나, 이미 있는 법령의 개정본 원문을 올립니다.">
        <div class="tabs" role="tablist" aria-label="추가 방법">
          <button type="button" role="tab" class="tab" :class="{ on: mode === 'legalize' }" :aria-selected="mode === 'legalize'" @click="setMode('legalize')">legalize-kr에서 받기</button>
          <button type="button" role="tab" class="tab" :class="{ on: mode === 'upload' }" :aria-selected="mode === 'upload'" @click="setMode('upload')">원문 파일 올리기</button>
        </div>

        <form v-if="mode === 'legalize'" class="grid" novalidate @submit.prevent="submit">
          <FormField id="l-name" label="법령명" required span="full" :error="err('name')">
            <input id="l-name" v-model="fetchForm.name" type="text" placeholder="예: 독점규제 및 공정거래에 관한 법률" :aria-invalid="!!err('name')" />
          </FormField>
          <FormField id="l-kind" label="구분" required>
            <select id="l-kind" v-model="fetchForm.kind">
              <option v-for="k in LAW_KINDS" :key="k" :value="k">{{ k }}</option>
            </select>
          </FormField>
          <FormField id="l-key" label="법령 키" required :error="err('lawKey')" hint="Agent가 도구를 부를 때 쓰는 이름">
            <input id="l-key" v-model="fetchForm.lawKey" type="text" autocomplete="off" placeholder="예: fair_trade_act" :aria-invalid="!!err('lawKey')" />
          </FormField>
          <FormField id="l-alias" label="별칭" span="full" :error="err('aliases')" hint="질문에 '공정거래법'처럼 써도 이 법령을 찾습니다. 법령명은 자동으로 들어갑니다.">
            <input id="l-alias" v-model="fetchForm.aliases" type="text" placeholder="쉼표로 구분 (예: 공정거래법)" :aria-invalid="!!err('aliases')" />
          </FormField>
          <FormField id="l-path" label="legalize-kr 경로" required span="full" :error="err('path')">
            <input id="l-path" v-model="fetchForm.path" type="text" placeholder="kr/법령명/법률.md" :aria-invalid="!!err('path')" @input="pathEdited = true" />
            <template #hint>법령명과 구분으로 채웠습니다. <a :href="LEGALIZE_REPO" target="_blank" rel="noopener noreferrer">저장소에서 확인 ↗</a></template>
          </FormField>
          <FormField id="l-ver" label="받을 개정본 수" :error="err('versions')" hint="시행 예정 판까지 포함해 최근 순">
            <input id="l-ver" v-model="fetchForm.versions" type="text" inputmode="numeric" maxlength="2" :aria-invalid="!!err('versions')" />
          </FormField>
        </form>

        <form v-else class="grid" novalidate @submit.prevent="submit">
          <FormField id="u-key" label="법령" required :error="err('lawKey')">
            <select id="u-key" v-model="uploadForm.lawKey" :aria-invalid="!!err('lawKey')">
              <option value="">선택</option>
              <option v-for="l in STORED_LAWS" :key="l.key" :value="l.key">{{ l.alias }} ({{ l.key }}) 개정본</option>
              <option value="__new">새 법령</option>
            </select>
          </FormField>
          <FormField v-if="uploadForm.lawKey === '__new'" id="u-newkey" label="새 법령 키" required :error="err('newKey')">
            <input id="u-newkey" v-model="uploadForm.newKey" type="text" autocomplete="off" placeholder="예: fair_trade_act" :aria-invalid="!!err('newKey')" />
          </FormField>
          <FormField
            id="u-file"
            label="원문 파일 (.md)"
            required
            span="full"
            :error="err('file')"
            hint="legalize-kr과 같은 형식: frontmatter(제목, 공포일자, 시행일자, 출처) + '##### 제N조 (제목)'"
          >
            <input id="u-file" class="file" type="file" accept=".md,text/markdown" :aria-invalid="!!err('file')" @change="onFile" />
          </FormField>

          <div v-if="summary" class="info-box span2">
            <h3>원문 확인</h3>
            <dl>
              <dt>제목</dt><dd>{{ summary.title ?? '정보 없음' }}</dd>
              <dt>공포일자</dt><dd>{{ summary.promulgated ?? '정보 없음' }}</dd>
              <dt>시행일자</dt>
              <dd>
                {{ summary.effective ?? '정보 없음' }}
                <span v-if="summary.effective && summary.effective > today" class="badge badge-warning">시행 예정</span>
              </dd>
              <dt>조문 수</dt><dd>{{ summary.articleCount }}개</dd>
              <dt>출처</dt><dd class="break">{{ summary.source ?? '정보 없음' }}</dd>
              <dt>저장 위치</dt><dd><code>{{ rawPath ?? '-' }}</code></dd>
            </dl>
            <ul v-if="summary.problems.length" class="problems">
              <li v-for="p in summary.problems" :key="p">{{ p }}</li>
            </ul>
          </div>
        </form>
      </PanelSection>

      <aside class="panel panel-pad side" aria-labelledby="apply-h">
        <h2 id="apply-h">반영 방법</h2>
        <ol class="steps">
          <template v-if="mode === 'legalize'">
            <li><code>scripts/fetch_laws.py</code>의 <code>TARGETS</code>에 경로 추가</li>
            <li><code>server.py</code>의 <code>ALIASES</code>에 별칭 추가</li>
            <li><code>fetch_laws.py</code> → <code>build_index.py</code> 실행</li>
          </template>
          <template v-else>
            <li>원문을 <code>{{ rawPath ?? 'data/raw/{법령 키}/{시행일자}_manual.md' }}</code>로 저장</li>
            <li><code>data/raw/manifest.json</code>에 판 추가</li>
            <li><code>build_index.py</code> 실행</li>
          </template>
          <li>law-mcp 재시작 후 <code>list_laws</code>로 확인</li>
        </ol>
        <button class="btn btn-primary wide" type="button" @click="submit">등록 요청 저장</button>
        <button class="btn wide" type="button" @click="downloadJson(downloadName, payload)">JSON 내려받기</button>
        <p class="footnote">
          법령 등록 API가 아직 없어 law-mcp에 바로 반영되지 않습니다. 법령 원문은 저작권법상 보호받지 않는 저작물이지만, 공정위 지침·가이드북은 공공누리 유형을 확인하세요.
        </p>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.intro {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
code {
  font-size: 12px;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 18px;
  align-items: start;
}
.tabs {
  margin-bottom: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 12px;
}
.span2 {
  grid-column: 1 / -1;
}
.file {
  font: inherit;
  font-size: 12px;
}
.break {
  word-break: break-all;
}
.problems {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--danger);
}
.side {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.side h2 {
  font-size: 16px;
}
.steps {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--text-2);
}
.steps code {
  font-size: 11px;
  word-break: break-all;
}
.wide {
  width: 100%;
}
.footnote {
  margin: 0;
}
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .side {
    position: static;
  }
}
@media (max-width: 640px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
