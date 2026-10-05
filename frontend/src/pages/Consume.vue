<template>
  <div>
    <h1>按临期消费</h1>
    <select v-model.number="item_id" @change="preview"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>
    <input type="number" v-model.number="qty" />
    <button @click="go">FEFO 扣减</button>

    <div v-if="error" class="box box-short">
      <strong>{{ error.reason === 'short' ? '余量不足，本次未提交任何扣减' : '扣减失败：' + error.reason }}</strong>
      <span v-if="error.short">缺 {{ fmt(error.short) }}</span>
    </div>
    <div v-else-if="done" class="box box-ok">
      扣减成功，共扣 {{ fmt(totalTaken) }}，共 {{ deductions.length }} 批
    </div>

    <h3>该品项批次</h3>
    <p class="muted">
      过期仍在架的批排在新鲜批之后兜底；脏数据批不参与扣减。
    </p>
    <div v-for="x in lotRows" :key="x.id" class="lot" :class="lotClass(x)">
      {{ lotTitle(x) }}
      <span class="tag tag-fresh" v-if="x.eligible_tier === 'fresh'">可扣·新鲜</span>
      <span class="tag tag-expired" v-else-if="x.eligible_tier === 'expired'">过期在架·兜底可扣</span>
      <span class="tag tag-dirty" v-else-if="x.data_quality !== 'clean'">脏数据·不扣</span>
      <span class="tag tag-off" v-else>已{{ statusLabel[x.status] || x.status }}</span>
      <template v-if="hitMap[x.id] && !hitMap[x.id].planned">
        <span class="tag" :class="hitMap[x.id].tier === 'expired' ? 'tag-hit-expired' : 'tag-hit'">
          本次扣 {{ fmt(hitMap[x.id].take) }}（{{ hitMap[x.id].tier === 'expired' ? '过期兜底' : '新鲜' }}）
        </span>
      </template>
      <span class="tag tag-plan" v-else-if="hitMap[x.id] && hitMap[x.id].planned">
        计划扣 {{ fmt(hitMap[x.id].take) }}·未提交
      </span>
      <span class="tag tag-miss" v-else-if="done || error">本次未扣到</span>
      <div class="lot-remain">{{ done ? '扣后余量' : '当前余量' }} {{ fmt(x.qty_remain) }}</div>
    </div>

    <h3>全层余量</h3>
    <div class="layers">
      <span v-for="L in layers" :key="L.layer" class="layer-chip">
        {{ layerLabel[L.layer] || L.layer }}：可用 {{ fmt(L.usable) }}
        <template v-if="L.expired_count"> · 过期在架 {{ L.expired_count }}</template>
        <template v-if="L.dirty_count"> · 脏 {{ L.dirty_count }}</template>
      </span>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, pantryChanged } from '../api'
const items = ref([])
const item_id = ref(1)
const qty = ref(1)
const lotRows = ref([])
const layers = ref([])
const deductions = ref([])
const done = ref(false)
const error = ref(null)
const layerLabel = { upper: '上层', mid: '中层', lower: '下层' }
const statusLabel = { consumed: '扣完', expired: '下架' }
const totalTaken = computed(() => deductions.value.reduce((s, d) => s + Number(d.take || 0), 0))
const hitMap = computed(() => Object.fromEntries(deductions.value.map(d => [d.lot_id, d])))
const fmt = n => Math.round(Number(n || 0) * 1000) / 1000

function lotClass(x) {
  if (x.data_quality !== 'clean') return 'lot-dirty'
  if (x.status !== 'on_shelf') return 'lot-off'
  return x.eligible_tier === 'expired' ? 'lot-expired' : 'lot-fresh'
}
function lotTitle(x) {
  const name = items.value.find(i => i.id === x.item_id)?.name ?? ('#' + x.item_id)
  return `${name} #${x.id} ×${fmt(x.qty_remain)} · ${x.expiry || '无到期日'}`
}

async function preview() {
  // on_shelf lots only, enough to show eligibility before submitting
  const all = await api('/fridge')
  lotRows.value = all.filter(r => r.item_id === item_id.value)
  if (!layers.value.length) layers.value = await api('/layers')
}

function applyResponse(res, isError) {
  lotRows.value = res.item_lots || lotRows.value
  layers.value = res.layers || layers.value
  deductions.value = res.deductions || []
  if (isError && res.reason === 'short') {
    // planned deductions were NOT applied; relabel so nobody reads them as hits
    deductions.value = deductions.value.map(d => ({ ...d, planned: true }))
  }
}

async function go() {
  done.value = false; error.value = null; deductions.value = []
  try {
    const res = await api('/consume', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value }) })
    applyResponse(res, false)
    done.value = true
    pantryChanged()
  } catch (e) {
    // 409 short carries the unapplied plan + current item/layer state
    const body = e.body?.detail && typeof e.body.detail === 'object' ? e.body.detail : e.body
    if (body && (body.item_lots || body.reason === 'short')) {
      applyResponse(body, true)
      error.value = body
    } else {
      error.value = { reason: e.message }
    }
  }
}

onMounted(async () => {
  items.value = await api('/items')
  if (items.value[0]) item_id.value = items.value[0].id
  await preview()
})
</script>
