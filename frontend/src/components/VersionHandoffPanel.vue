<template>
  <section class="handoff-panel" data-testid="version-handoff-panel">
    <header class="handoff-head">
      <h3>子版本交接核对</h3>
      <p class="page-desc">
        子版本先登记为「待续办」交接并绑定已确认产出的指纹；续办时重新核对指纹与当前
        最新确认产出，不一致只登记拒绝缘由、绝不落库，保证产出不会错绑另一份正文。
      </p>
    </header>

    <div class="handoff-toolbar">
      <label class="filter-item">
        <span>按任务编号过滤</span>
        <input v-model="taskFilter" data-testid="handoff-task-filter" placeholder="如 TRCB-0001" />
      </label>
      <button class="btn" type="button" @click="reload">查询交接</button>
      <button class="btn primary" data-testid="open-handoff-create" type="button" @click="openCreate">
        登记子版本交接
      </button>
    </div>

    <p v-if="panelError" class="error-text">{{ panelError }}</p>

    <table class="data-table" data-testid="version-handoff-table">
      <thead>
        <tr>
          <th>交接编号</th><th>关联任务</th><th>版本编号</th><th>版本号</th><th>父版本号</th>
          <th>产出指纹</th><th>交接状态</th><th>可续办</th><th>拒绝缘由</th><th>落库版本</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="handoff in handoffs" :key="handoff.交接编号" :data-status="handoff.交接状态">
          <td>{{ handoff.交接编号 }}</td>
          <td>{{ handoff.关联任务 }}</td>
          <td>{{ handoff.版本编号 }}</td>
          <td>{{ handoff.版本号 }}</td>
          <td>{{ handoff.父版本号 }}</td>
          <td><code>{{ handoff.产出指纹 }}</code></td>
          <td>{{ handoff.交接状态 }}</td>
          <td>{{ handoff.可续办 }}</td>
          <td>{{ handoff.拒绝缘由 }}</td>
          <td>{{ handoff.落库版本编号 }}</td>
          <td class="row-actions">
            <button
              v-if="handoff.交接状态 === '待续办'"
              class="link"
              type="button"
              :data-testid="`resume-${handoff.交接编号}`"
              @click="onResume(handoff)"
            >
              续办落库
            </button>
            <button
              v-if="handoff.交接状态 === '待续办'"
              class="link"
              type="button"
              :data-testid="`reject-${handoff.交接编号}`"
              @click="onReject(handoff)"
            >
              拒绝
            </button>
            <span v-else class="page-desc">—</span>
          </td>
        </tr>
        <tr v-if="!handoffs.length">
          <td colspan="11" class="empty-state">暂无交接记录</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="handoff-create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记子版本交接</h3>
        <label class="create-field">
          <span>关联任务编号</span>
          <input v-model="form.task_id" data-testid="handoff-task-id" required />
        </label>
        <label class="create-field">
          <span>版本编号</span>
          <input v-model="form.version_id" data-testid="handoff-version-id" required />
        </label>
        <label class="create-field">
          <span>版本号</span>
          <input v-model="form.version_no" placeholder="如 v2" data-testid="handoff-version-no" required />
        </label>
        <label class="create-field">
          <span>父版本号</span>
          <input v-model="form.parent" placeholder="无父版填 -" data-testid="handoff-parent" />
        </label>
        <label class="create-field">
          <span>变更行数</span>
          <input v-model.number="form.change_lines" type="number" data-testid="handoff-change-lines" required />
        </label>
        <label class="create-field">
          <span>变更摘要</span>
          <input v-model="form.summary" data-testid="handoff-summary" />
        </label>
        <label class="create-field">
          <span>产出指纹（须为该任务已确认产出）</span>
          <input v-model="form.digest" data-testid="handoff-digest" required />
        </label>
        <button class="btn" type="button" data-testid="fill-current-digest" @click="fillCurrentDigest">
          读取该任务当前确认指纹
        </button>
        <p v-if="formError" class="error-text">{{ formError }}</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-handoff-create" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '登记交接' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  listAllHandoffs,
  listProductions,
  openHandoff,
  rejectHandoff,
  resumeHandoff,
  type HandoffRecord,
} from '@/api/backend-service'

const handoffs = ref<HandoffRecord[]>([])
const taskFilter = ref('')
const panelError = ref('')
const showCreate = ref(false)
const submitting = ref(false)
const formError = ref('')
const form = ref({
  task_id: '',
  version_id: '',
  version_no: '',
  parent: '-',
  change_lines: 0,
  summary: '',
  digest: '',
})

async function reload() {
  panelError.value = ''
  try {
    const view = await listAllHandoffs(taskFilter.value.trim() || undefined)
    handoffs.value = view.items
  } catch (error) {
    panelError.value = error instanceof Error ? error.message : '交接记录读取失败'
  }
}

function openCreate() {
  formError.value = ''
  form.value = {
    task_id: taskFilter.value.trim(),
    version_id: '',
    version_no: '',
    parent: '-',
    change_lines: 0,
    summary: '',
    digest: '',
  }
  showCreate.value = true
}

async function fillCurrentDigest() {
  formError.value = ''
  if (!form.value.task_id.trim()) {
    formError.value = '请先填写关联任务编号'
    return
  }
  try {
    const view = await listProductions(form.value.task_id.trim())
    if (!view.current) {
      formError.value = '该任务尚无已确认产出，请先到转写任务页确认产出'
      return
    }
    form.value.digest = view.current.产出指纹
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '指纹读取失败'
  }
}

async function submitCreate() {
  submitting.value = true
  formError.value = ''
  try {
    const result = await openHandoff(form.value.task_id.trim(), {
      version_id: form.value.version_id.trim(),
      version_no: form.value.version_no.trim(),
      parent: form.value.parent.trim() || '-',
      change_lines: Number(form.value.change_lines),
      summary: form.value.summary,
      digest: form.value.digest.trim(),
    })
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    taskFilter.value = form.value.task_id.trim()
    await reload()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '交接登记失败'
  } finally {
    submitting.value = false
  }
}

async function onResume(handoff: HandoffRecord) {
  panelError.value = ''
  try {
    const result = await resumeHandoff(handoff.交接编号)
    if (!result.ok) {
      panelError.value = result.message
    }
    await reload()
  } catch (error) {
    panelError.value = error instanceof Error ? error.message : '续办失败'
  }
}

async function onReject(handoff: HandoffRecord) {
  panelError.value = ''
  const reason = window.prompt(`拒绝交接 ${handoff.交接编号} 的缘由：`, '正文已更新，废弃该交接')
  if (reason === null) return
  try {
    const result = await rejectHandoff(handoff.交接编号, reason || '未填写拒绝缘由')
    if (!result.ok) panelError.value = result.message
    await reload()
  } catch (error) {
    panelError.value = error instanceof Error ? error.message : '拒绝操作失败'
  }
}

onMounted(reload)
</script>
