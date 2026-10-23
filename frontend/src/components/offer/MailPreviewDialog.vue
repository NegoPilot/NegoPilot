<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { MAX_MAIL_MESSAGE, splitMailBody, type MailPreview, type OfferWarning } from '@/api/offer-proposals'

const props = defineProps<{ preview: MailPreview | null; loading: boolean; sending: boolean; error: string; warnings: OfferWarning[] }>()
const emit = defineEmits<{ close: []; send: [message: string] }>()
// mail.md 1절: CHECK_NEEDED면 경고를 확인한 뒤에만 보낸다
const acknowledged = ref(false)
// 본문은 mail-mcp가 정한 문장을 그대로 보여 주고, 가운데 추가 문구만 담당자가 쓴다(mail.md 2절)
const message = ref('')
const parts = computed(() => splitMailBody(props.preview?.body ?? ''))
const canSend = computed(() => !!props.preview && !props.sending && (props.warnings.length === 0 || acknowledged.value))
const kb = (bytes: number) => `${Math.max(1, Math.round(bytes / 1024))}KB`

function onKey(event: KeyboardEvent) { if (event.key === 'Escape' && !props.sending) emit('close') }
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="backdrop" @click.self="!sending && emit('close')">
    <section class="dialog" role="dialog" aria-modal="true" aria-labelledby="mail-preview-title">
      <header class="head">
        <h2 id="mail-preview-title">메일 미리보기</h2>
        <button type="button" class="link-btn close" :disabled="sending" aria-label="닫기" @click="emit('close')">✕</button>
      </header>
      <p v-if="loading" role="status" class="muted">보낼 메일을 만드는 중입니다…</p>
      <template v-else-if="preview">
        <dl class="fields">
          <dt>받는 사람</dt><dd>{{ preview.to.supplier_name }} {{ preview.to.name }} &lt;{{ preview.to.email }}&gt;</dd>
          <dt>보내는 사람</dt><dd>{{ preview.from }}</dd>
          <dt>제목</dt><dd class="subject">{{ preview.subject }}</dd>
          <dt>첨부</dt><dd><span v-for="a in preview.attachments" :key="a.filename" class="chip">📎 {{ a.filename }} · {{ kb(a.bytes) }}</span></dd>
        </dl>
        <div class="body" aria-label="메일 내용">
          <p class="fixed">{{ parts.head }}</p>
          <label for="mail-message" class="sr-only">추가 문구</label>
          <textarea id="mail-message" v-model="message" class="message" rows="4" :maxlength="MAX_MAIL_MESSAGE" :disabled="sending"
            placeholder="협력사에 전할 말을 덧붙일 수 있어요 (선택)" />
          <p class="fixed">{{ parts.tail }}</p>
        </div>
        <p class="footnote">회색 문장은 자동으로 들어갑니다. 추가 문구에는 단가·지급 조건 같은 숫자를 쓰지 말고 첨부한 제안서로 전달해 주세요. 내부 협상 브리핑은 메일에 들어가지 않습니다.
          <span class="count">{{ message.length }} / {{ MAX_MAIL_MESSAGE }}</span></p>
        <div v-if="warnings.length" class="warn">
          <p v-for="w in warnings" :key="w.code">⚠ {{ w.message }}</p>
          <label><input v-model="acknowledged" type="checkbox" /> 확인 필요 항목을 검토했습니다</label>
        </div>
      </template>
      <p v-if="error" role="alert" class="field-error">{{ error }}</p>
      <footer class="foot">
        <button type="button" class="btn" :disabled="sending" @click="emit('close')">취소</button>
        <button type="button" class="btn btn-primary" :disabled="!canSend" @click="emit('send', message)">{{ sending ? '발송 중…' : '발송' }}</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.backdrop{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(17,24,39,.35)}
.dialog{width:min(640px,100%);max-height:calc(100vh - 32px);overflow:auto;display:flex;flex-direction:column;gap:14px;padding:20px 22px;
  background:var(--surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 18px 48px rgba(17,24,39,.22)}
.head{display:flex;align-items:center;justify-content:space-between}.head h2{font-size:16px}.close{font-size:16px;color:var(--text-3)}
.fields{margin:0;display:grid;grid-template-columns:max-content 1fr;gap:8px 16px;font-size:13px}.fields dt{color:var(--text-3)}.fields dd{margin:0}
.subject{font-weight:600}.chip{margin-right:6px}
.body{display:flex;flex-direction:column;gap:10px;padding:14px;font-size:13px;line-height:1.7;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius)}
.fixed{margin:0;white-space:pre-wrap;color:var(--text-3)}.message{font-size:13px;line-height:1.7;padding:8px 10px}.count{float:right}
.warn{padding:10px 12px;border:1px solid #f0d2a8;background:#fdf6ec;border-radius:var(--radius);font-size:12px;color:var(--warning)}.warn p{margin:0 0 6px}
.foot{display:flex;justify-content:flex-end;gap:8px}
</style>
