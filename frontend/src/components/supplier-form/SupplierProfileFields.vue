<script setup lang="ts">
import { computed } from 'vue'

import CheckOption from '@/components/form/CheckOption.vue'
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import { REGIONS } from '@/data/dataCatalog'
import { type CertDraft, type FieldErrorOf, type ProfileDraft, maskBizNo } from '@/utils/supplierForm'

// 기본 정보와 인증 입력칸. 등록과 상세 수정이 같이 쓴다
const props = defineProps<{
  profile: ProfileDraft
  certs: CertDraft
  err: FieldErrorOf
  /** DART 공시 기업은 기본 정보를 바꿀 수 없다. 인증은 바꿀 수 있다 */
  profileLocked?: boolean
}>()

const maskedBizNo = computed(() => maskBizNo(props.profile.bizNo))
</script>

<template>
  <FormGrid :cols="3">
    <FormField id="r-name" label="협력사명" required :span="2" :error="err('name')">
      <input id="r-name" v-model="profile.name" type="text" maxlength="100" placeholder="예: 한빛정밀" :disabled="profileLocked" :aria-invalid="!!err('name')" />
    </FormField>
    <FormField id="r-region" label="지역" required :error="err('region')">
      <select id="r-region" v-model="profile.region" :disabled="profileLocked" :aria-invalid="!!err('region')">
        <option value="">선택</option>
        <option v-for="r in REGIONS" :key="r" :value="r">{{ r }}</option>
      </select>
    </FormField>

    <FormField id="r-founded" label="설립연도" required :error="err('foundedYear')">
      <input id="r-founded" v-model="profile.foundedYear" type="text" inputmode="numeric" maxlength="4" placeholder="예: 2004" :disabled="profileLocked" :aria-invalid="!!err('foundedYear')" />
    </FormField>
    <FormField
      id="r-biz"
      label="사업자등록번호"
      required
      :error="err('bizNo')"
      :hint="maskedBizNo ? `전송값 ${maskedBizNo} (뒤 5자리는 보내지 않음)` : undefined"
    >
      <input id="r-biz" v-model="profile.bizNo" type="text" autocomplete="off" placeholder="123-45-67890" :disabled="profileLocked" :aria-invalid="!!err('bizNo')" />
    </FormField>
    <FormField id="r-emp" label="직원 수" :error="err('employees')">
      <input id="r-emp" v-model="profile.employees" type="text" inputmode="numeric" placeholder="모르면 비움" :disabled="profileLocked" :aria-invalid="!!err('employees')" />
    </FormField>

    <FormField id="r-rev" label="최근 매출 (억 원)" :error="err('revenueEok')">
      <input id="r-rev" v-model="profile.revenueEok" type="text" inputmode="numeric" placeholder="예: 820" :disabled="profileLocked" :aria-invalid="!!err('revenueEok')" />
    </FormField>
    <FormField id="r-stock" label="종목코드" :error="err('stockCode')">
      <input id="r-stock" v-model="profile.stockCode" type="text" inputmode="numeric" maxlength="6" placeholder="상장사만" :disabled="profileLocked" :aria-invalid="!!err('stockCode')" />
    </FormField>
    <FormField id="r-ksic" label="업종 코드 (KSIC)" :error="err('indutyCode')">
      <input id="r-ksic" v-model="profile.indutyCode" type="text" inputmode="numeric" maxlength="5" placeholder="예: 30332" :disabled="profileLocked" :aria-invalid="!!err('indutyCode')" />
    </FormField>
  </FormGrid>

  <fieldset class="certs">
    <legend>인증</legend>
    <CheckOption v-model="certs.iatf16949">IATF 16949</CheckOption>
    <CheckOption v-model="certs.iso14001">ISO 14001</CheckOption>
  </fieldset>
</template>

<style scoped>
input:disabled,
select:disabled {
  background: var(--surface-2);
  color: var(--text-3);
}
.certs {
  display: flex;
  gap: 10px;
  margin: 16px 0 0;
  padding: 0;
  border: 0;
}
.certs legend {
  float: left;
  margin-right: 8px;
  font-size: 12px;
  font-weight: 600;
  line-height: 38px;
}
</style>
