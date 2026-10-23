import { ref } from 'vue'

import type { AdminSupplierDetail } from '@/api/admin-suppliers'
import { describeError, isApiError } from '@/utils/errors'

// 협력사 상세의 저장 흐름. 성공하면 새 상세를 넘기고, 버전 충돌이면 최신 상세를 다시 불러오게 알린다.
// 충돌해도 사용자가 입력한 내용은 그대로 두고 다시 확인해서 저장하게 한다 (계약 문서의 수정 규칙).
export function useSupplierSave(onSaved: (detail: AdminSupplierDetail) => void, onStale: () => void) {
  const saving = ref(false)
  const error = ref<string | null>(null)

  async function save(action: () => Promise<AdminSupplierDetail>): Promise<boolean> {
    if (saving.value) return false
    saving.value = true
    error.value = null
    try {
      onSaved(await action())
      return true
    } catch (e) {
      error.value = describeError(e).message
      if (isApiError(e, 'VERSION_CONFLICT')) {
        error.value = '다른 사람이 먼저 수정해서 최신 내용을 다시 불러왔습니다. 입력을 확인한 뒤 다시 저장해 주세요.'
        onStale()
      }
      return false
    } finally {
      saving.value = false
    }
  }

  return { saving, error, save }
}
