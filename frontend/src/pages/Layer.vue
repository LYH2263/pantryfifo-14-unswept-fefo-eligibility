<template>
  <div>
    <h1>{{ label[props.layer] || props.layer }} 层</h1>
    <p class="muted" v-if="summary">
      可用余量 {{ fmt(summary.usable) }}
      <template v-if="summary.expired_count"> · 过期在架 {{ summary.expired_count }}（兜底可扣）</template>
      <template v-if="summary.dirty_count"> · 脏数据 {{ summary.dirty_count }}（不扣）</template>
    </p>
    <span v-for="x in rows" :key="x.id" class="lot" :class="x.data_quality !== 'clean' ? 'lot-dirty' : (x.eligible_tier === 'expired' ? 'lot-expired' : '')">
      {{ x.name }} ×{{ fmt(x.qty_remain) }} · {{ x.expiry }}
      <span class="tag tag-expired" v-if="x.eligible_tier === 'expired'">过期在架·兜底可扣</span>
      <span class="tag tag-dirty" v-if="x.data_quality !== 'clean'">脏数据·不扣</span>
    </span>
    <span v-if="!rows.length" class="muted">空</span>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api, onPantryChanged } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
const allLayers = ref([])
const label = { upper: '上层', mid: '中层', lower: '下层' }
const fmt = n => Math.round(Number(n || 0) * 1000) / 1000
const summary = computed(() => allLayers.value.find(L => L.layer === props.layer))
async function load() {
  rows.value = await api('/fridge?layer=' + props.layer)
  allLayers.value = await api('/layers')
}
let off
watch(() => props.layer, load)
onMounted(async () => { await load(); off = onPantryChanged(load) })
onUnmounted(() => off && off())
</script>
