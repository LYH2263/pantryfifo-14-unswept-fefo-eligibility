<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层 · FEFO 消费走「消费」页</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>{{ label[L] }}</h3>
        <span v-for="x in by(L)" :key="x.id" class="lot">
          {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
          <em v-if="x.expiry && x.expiry < today" class="badge-expired">过期未下架</em>
        </span>
      </section>
    </div>
    <button style="margin-top:12px" @click="sweep">过期下架</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { refreshAlerts } from '../alerts'
const rows = ref([])
const layers = ['upper','mid','lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
const today = new Date().toISOString().slice(0, 10)
function by(L) { return rows.value.filter(r => r.layer === L) }
async function load() { rows.value = await api('/fridge') }
async function sweep() {
  await api('/expire-sweep', { method: 'POST', body: '{}' })
  await Promise.all([load(), refreshAlerts()])
}
onMounted(load)
</script>
