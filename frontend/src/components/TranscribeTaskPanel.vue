<template>
  <section class="handoff-panel" data-testid="task-production-panel">
    <header class="handoff-head">
      <h3>产出确认与子版本交接</h3>
      <p class="page-desc">
        在原「确认产出」按钮录入行数、平均置信度与耗时；每次确认生成正文指纹，
        引擎重跑更正会保留旧确认缘由，子版本交接只允许绑定已确认指纹。
      </p>
    </header>

    <div v-if="!selectedTask" class="empty-state">请在上方列表选择一条转写任务查看产出确认与交接记录</div>

    <template v-else>
      <div class="handoff-toolbar">
        <strong>当前任务：{{ selectedTask['任务编号'] }}</strong>
        <button class="btn primary" data-testid="open-production-confirm" type="button" @click="openConfirm">
          确认产出
        </button>
        <button class="btn ghost" type="button" @click="reload">刷新记录</button>
      </div>

      <h4>确认记录（含引擎更正历史）</h4>
      <table class="data-table" data-testid="production-table">
        <thead>
          <tr>
            <th>序号</th><th>行数</th><th>平均置信度</th><th>耗时</th>
            <th>产出指纹</th><th>确认状态</th><th>更正原因</th><th>确认时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in productions" :key="String(record.确认序号)" :data-status="record.确认状态">
            <td>{{ record.确认序号 }}</td>
            <td>{{ record.输出文本行数 }}</td>
            <td>{{ record.平均置信度 }}</td>
            <td>{{ record.转写耗时 }}</td>
            <td><code>{{ record.产出指纹 }}</code></td>
            <td>{{ record.确认状态 }}</td>
            <td>{{ record.更正原因 }}</td>
            <td>{{ record.确认时间 }}</td>
          </tr>
          <tr v-if="!productions.length">
            <td colspan="8" class="empty-state">该任务尚未确认过产出</td>
          </tr>
        </tbody>
      </table>

      <h4>子版本交接（哪次可以继续、拒绝缘由）</h4>
      <table class="data-table" data-testid="task-handoff-table">
        <thead>
          <tr>
            <th>交接编号</th><th>版本编号</th><th>版本号</th><th>父版本号</th>
            <th>产出指纹</th><th>交接状态</th><th>可续办</th><th>拒绝缘由</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="handoff in handoffs" :key="handoff.交接编号">
            <td>{{ handoff.交接编号 }}</td>
            <td>{{ handoff.版本编号 }}</td>
            <td>{{ handoff.版本号 }}</td>
            <td>{{ handoff.父版本号 }}</td>
            <td><code>{{ handoff.产出指纹 }}</code></td>
            <td>{{ handoff.交接状态 }}</td>
            <td>{{ handoff.可续办 }}</td>
            <td>{{ handoff.拒绝缘由 }}</td>
          </tr>
          <tr v-if="!handoffs.length">
            <td colspan="8" class="empty-state">暂无交接记录</td>
          </tr>
        </tbody>
      </table>
    </template>

    <div v-if="showConfirm" class="modal-backdrop" data-testid="production-confirm-modal">
      <form class="modal" @submit.prevent="submitConfirm">
        <h3>确认产出 · {{ selectedTask?.['任务编号'] }}</h3>
        <label class="create-field">
          <span>输出文本行数</span>
          <input v-model.number="form.line_count" data-testid="confirm-line-count" type="number" min="0" required />
        </label>
        <label class="create-field">
          <span>平均置信度</span>
          <input v-model.number="form.avg_confidence" data-testid="confirm-avg-confidence"
                 type="number" step="0.01" min="0" max="1" required />
        </label>
        <label class="create-field">
          <span>转写耗时（秒）</span>
          <input v-model.number="form.elapsed_seconds" data-testid="confirm-elapsed"
                 type="number" min="0" required />
        </label>
        <p v-if="confirmError" class="error-text">{{ confirmError }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showConfirm = false">取消</button>
          <button class="btn primary" data-testid="submit-production-confirm" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认产出' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import {
  confirmProduction,
  listProductions,
  listTaskHandoffs,
  type HandoffRecord,
  type ProductionRecord,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ selectedTask: EntryRow | null }>()
const emit = defineEmits<{ (event: 'reload'): void }>()

const productions = ref<ProductionRecord[]>([])
const handoffs = ref<HandoffRecord[]>([])
const panelError = ref('')
const showConfirm = ref(false)
const submitting = ref(false)
const confirmError = ref('')
const form = ref({ line_count: 0, avg_confidence: 0.9, elapsed_seconds: 0 })

async function reload() {
  panelError.value = ''
  const task = props.selectedTask
  if (!task) {
    productions.value = []
    handoffs.value = []
    return
  }
  const taskId = String(task['任务编号'])
  try {
    const [productionView, handoffView] = await Promise.all([
      listProductions(taskId),
      listTaskHandoffs(taskId),
    ])
    productions.value = productionView.items
    handoffs.value = handoffView.items
  } catch (error) {
    panelError.value = error instanceof Error ? error.message : '交接记录读取失败'
  }
}

function openConfirm() {
  const task = props.selectedTask
  confirmError.value = ''
  form.value = {
    line_count: Number(task?.['输出文本行数'] ?? 0),
    avg_confidence: Number(task?.['平均置信度'] ?? 0.9),
    elapsed_seconds: Number(task?.['转写耗时'] ?? 0),
  }
  showConfirm.value = true
}

async function submitConfirm() {
  const task = props.selectedTask
  if (!task) return
  submitting.value = true
  confirmError.value = ''
  try {
    const result = await confirmProduction(String(task['任务编号']), { ...form.value })
    if (!result.ok) throw new Error(result.message)
    showConfirm.value = false
    await reload()
    emit('reload')
  } catch (error) {
    confirmError.value = error instanceof Error ? error.message : '产出确认失败'
  } finally {
    submitting.value = false
  }
}

watch(() => props.selectedTask?.id, reload, { immediate: true })

defineExpose({ openConfirm, reload })
</script>
