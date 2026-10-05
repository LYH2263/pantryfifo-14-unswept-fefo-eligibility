<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层 · FEFO 消费走「消费」页</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>
          {{ label[L] }}
          <span class="shelf-qty">可用 {{ fmt(layerUsable(L)) }}</span>
          <span class="tag tag-expired" v-if="layerExpired(L)">过期在架 {{ layerExpired(L) }}</span>
          <span class="tag tag-dirty" v-if="layerDirty(L)">脏 {{ layerDirty(L) }}</span>
        </h3>
        <span v-for="x in by(L)" :key="x.id" class="lot" :class="lotClass(x)">
          {{ x.name }} ×{{ fmt(x.qty_remain) }} · {{ x.expiry }}
          <span class="tag tag-expired" v-if="x.eligible_tier === 'expired'">过期在架·兜底可扣</span>
          <span class="tag tag-dirty" v-if="x.data_quality !== 'clean'">脏数据·不扣</span>
        </span>
        <span v-if="!by(L).length" class="muted">空</span>
      </section>
    </div>
    <button style="margin-top:12px" @click="sweep" :disabled="busy">{{ busy ? '下架中…' : '过期下架' }}</button>
    <span v-if="sweptIds.length" class="muted"> 本次下架 {{ sweptIds.length }} 批</span>
  </div>
</template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api, pantryChanged, onPantryChanged } from '../api'
const rows = ref([])
const layerRows = ref([])
const busy = ref(false)
const sweptIds = ref([])
const layers = ['upper','mid','lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
const fmt = n => Math.round(Number(n || 0) * 1000) / 1000
function by(L) { return rows.value.filter(r => r.layer === L) }
function lotClass(x) {
  if (x.data_quality !== 'clean') return 'lot-dirty'
  return x.eligible_tier === 'expired' ? 'lot-expired' : ''
}
function layerUsable(L) { return layerRows.value.find(r => r.layer === L)?.usable ?? 0 }
function layerExpired(L) { return layerRows.value.find(r => r.layer === L)?.expired_count || 0 }
function layerDirty(L) { return layerRows.value.find(r => r.layer === L)?.dirty_count || 0 }
async function load() {
  rows.value = await api('/fridge')
  layerRows.value = await api('/layers')
}
async function sweep() {
  busy.value = true
  try {
    const res = await api('/expire-sweep', { method: 'POST', body: '{}' })
    sweptIds.value = res.expired_ids || []
    layerRows.value = res.layers || layerRows.value
    await load()
    pantryChanged()
  } finally { busy.value = false }
}
let off
onMounted(async () => { await load(); off = onPantryChanged(load) })
onUnmounted(() => off && off())
</script>
