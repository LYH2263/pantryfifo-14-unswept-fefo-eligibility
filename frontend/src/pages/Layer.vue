<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <span v-for="x in rows" :key="x.id" class="lot">
      {{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}
      <em v-if="x.expiry && x.expiry < today" class="badge-expired">过期未下架</em>
    </span>
  </div>
</template>
<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
const today = new Date().toISOString().slice(0, 10)
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
