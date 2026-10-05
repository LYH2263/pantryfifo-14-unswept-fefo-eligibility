<template>
  <div>
    <h1>按临期消费</h1>
    <select v-model.number="item_id" @change="loadShelf">
      <option v-for="i in items" :value="i.id">{{ i.name }}</option>
    </select>
    <input type="number" v-model.number="qty" />
    <button @click="go">FEFO 扣减</button>

    <h3>在架批次</h3>
    <p v-if="!lots.length" class="muted">该品项暂无在架批次</p>
    <span v-for="l in lots" :key="l.id" class="lot">
      {{ l.expiry }} · 余 {{ l.qty_remain }}
      <em v-if="isExpired(l)" class="badge-expired">过期未下架</em>
      <em v-if="hitIds.has(l.id)" class="tag-hit">本次已扣</em>
      <em v-else-if="ran && isExpired(l)" class="tag-miss">本次未扣</em>
    </span>

    <template v-if="ran">
      <h3>本次扣减</h3>
      <p v-if="!deductions.length" class="muted">未扣到任何批次</p>
      <span v-for="d in deductions" :key="d.lot_id" class="lot">
        #{{ d.lot_id }} · {{ d.expiry }} · 扣 {{ d.take }} · 扣后余 {{ d.lot_remaining_after }}
        <em v-if="d.expired_unswept" class="badge-expired">过期未下架</em>
      </span>
      <p class="muted">本品余量 {{ summary.item_remaining }} · {{ layerLabel }}余量 {{ summary.layer_remaining }}</p>
    </template>
    <pre v-if="error" class="err">{{ error }}</pre>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { refreshAlerts } from '../alerts'

const items = ref([])
const item_id = ref(1)
const qty = ref(1)
const lots = ref([])
const deductions = ref([])
const summary = ref({})
const ran = ref(false)
const error = ref('')
const layerName = { upper: '上层', mid: '中层', lower: '下层' }

const today = new Date().toISOString().slice(0, 10)
const isExpired = l => l.expiry && l.expiry < today
const hitIds = computed(() => new Set(deductions.value.map(d => d.lot_id)))
const layerLabel = computed(() => layerName[summary.value.layer] || '')

async function loadShelf() {
  const rows = await api('/fridge')
  lots.value = rows.filter(r => r.item_id === item_id.value)
}
onMounted(async () => {
  items.value = await api('/items')
  if (items.value[0]) item_id.value = items.value[0].id
  await loadShelf()
})
async function go() {
  error.value = ''
  try {
    const r = await api('/consume', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value }) })
    deductions.value = r.deductions
    summary.value = r
    ran.value = true
  } catch (e) {
    error.value = e.message
    deductions.value = []
    ran.value = false
  }
  // 无论成败都同步层上与顶条: 并发下架/另一笔消费可能刚改过状态
  await Promise.all([loadShelf(), refreshAlerts()])
}
</script>
