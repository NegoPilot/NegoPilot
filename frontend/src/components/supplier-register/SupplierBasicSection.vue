<script setup lang="ts">
import { computed } from 'vue'

import CheckOption from '@/components/form/CheckOption.vue'
import FormField from '@/components/form/FormField.vue'
import FormGrid from '@/components/form/FormGrid.vue'
import PanelSection from '@/components/form/PanelSection.vue'
import { REGIONS } from '@/data/dataCatalog'
import { type SupplierErrorOf, type SupplierForm, maskBizNo } from '@/utils/supplierForm'

const props = defineProps<{ form: SupplierForm; err: SupplierErrorOf }>()

const maskedBizNo = computed(() => maskBizNo(props.form.bizNo))
</script>

<template>
  <PanelSection title="기본 정보" sub="suppliers 표 · DART 기업은 공시 값, MOCK은 실습용 가상 기업">
    <FormGrid :cols="3">
      <FormField id="r-id" label="협력사 ID" required :error="err('supplierId')">
        <input id="r-id" v-model="form.supplierId" type="text" :aria-invalid="!!err('supplierId')" />
      </FormField>
      <FormField id="r-source" label="기본 정보 출처" required>
        <select id="r-source" v-model="form.source">
          <option value="MOCK">MOCK (가상 기업)</option>
          <option value="DART">DART (공시 기업)</option>
        </select>
      </FormField>
      <FormField id="r-corp" label="DART 고유번호" :required="form.source === 'DART'" :error="err('corpCode')">
        <input id="r-corp" v-model="form.corpCode" type="text" inputmode="numeric" maxlength="8" :disabled="form.source !== 'DART'" placeholder="8자리" :aria-invalid="!!err('corpCode')" />
      </FormField>

      <FormField id="r-name" label="협력사명" required :span="2" :error="err('name')">
        <input id="r-name" v-model="form.name" type="text" maxlength="100" placeholder="예: 한빛정밀" :aria-invalid="!!err('name')" />
      </FormField>
      <FormField id="r-region" label="지역" required :error="err('region')">
        <select id="r-region" v-model="form.region" :aria-invalid="!!err('region')">
          <option value="">선택</option>
          <option v-for="r in REGIONS" :key="r" :value="r">{{ r }}</option>
        </select>
      </FormField>

      <FormField id="r-founded" label="설립연도" required :error="err('foundedYear')">
        <input id="r-founded" v-model="form.foundedYear" type="text" inputmode="numeric" maxlength="4" placeholder="예: 2004" :aria-invalid="!!err('foundedYear')" />
      </FormField>
      <FormField
        id="r-biz"
        label="사업자등록번호"
        required
        :error="err('bizNo')"
        :hint="maskedBizNo ? `저장값 ${maskedBizNo} (뒤 5자리는 저장하지 않음)` : undefined"
      >
        <input id="r-biz" v-model="form.bizNo" type="text" autocomplete="off" placeholder="123-45-67890" :aria-invalid="!!err('bizNo')" />
      </FormField>
      <FormField id="r-emp" label="직원 수" :error="err('employees')">
        <input id="r-emp" v-model="form.employees" type="text" inputmode="numeric" placeholder="모르면 비움" :aria-invalid="!!err('employees')" />
      </FormField>

      <FormField id="r-rev" label="최근 매출 (억 원)" :error="err('revenueEok')">
        <input id="r-rev" v-model="form.revenueEok" type="text" inputmode="numeric" placeholder="예: 820" :aria-invalid="!!err('revenueEok')" />
      </FormField>
      <FormField id="r-stock" label="종목코드" :error="err('stockCode')">
        <input id="r-stock" v-model="form.stockCode" type="text" inputmode="numeric" maxlength="6" placeholder="상장사만" :aria-invalid="!!err('stockCode')" />
      </FormField>
      <FormField id="r-ksic" label="업종 코드 (KSIC)" :error="err('indutyCode')">
        <input id="r-ksic" v-model="form.indutyCode" type="text" inputmode="numeric" maxlength="5" placeholder="예: 30332" :aria-invalid="!!err('indutyCode')" />
      </FormField>
    </FormGrid>

    <fieldset class="certs">
      <legend>인증</legend>
      <CheckOption v-model="form.iatf16949">IATF 16949</CheckOption>
      <CheckOption v-model="form.iso14001">ISO 14001</CheckOption>
    </fieldset>
  </PanelSection>
</template>

<style scoped>
input:disabled {
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
