import { ref } from 'vue'
import { api } from './api'

// 顶条预警共享一份: 消费/下架后调用 refreshAlerts,
// 已扣尽的过期批不会继续挂在顶条当紧急。
export const alerts = ref([])

export async function refreshAlerts() {
  try { alerts.value = await api('/alerts') } catch { alerts.value = [] }
}
