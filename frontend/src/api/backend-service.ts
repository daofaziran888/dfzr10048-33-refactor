import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 产出确认与子版本交接共享写入边界专用接口 ----

export type ProductionRecord = {
  确认序号: number
  关联任务: string
  输出文本行数: string
  平均置信度: string
  转写耗时: string
  产出指纹: string
  确认状态: string
  更正原因: string
  确认时间: string
}

export type HandoffRecord = {
  交接编号: string
  关联任务: string
  版本编号: string
  版本号: string
  父版本号: string
  变更行数: string
  变更摘要: string
  产出指纹: string
  交接状态: string
  可续办: string
  拒绝缘由: string
  拒绝次数: string
  落库版本编号: string
  创建时间: string
  更新时间: string
}

export async function confirmProduction(
  taskId: string,
  payload: { line_count: number; avg_confidence: number; elapsed_seconds: number },
): Promise<ActionResult> {
  const response = await request(`/api/transcribe-task/${encodeURIComponent(taskId)}/productions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function listProductions(taskId: string): Promise<{
  task_id: string
  current: ProductionRecord | null
  items: ProductionRecord[]
}> {
  const response = await request(`/api/transcribe-task/${encodeURIComponent(taskId)}/productions`)
  if (!response.ok) throw new Error('产出确认记录读取失败')
  return await response.json()
}

export async function listTaskHandoffs(taskId: string): Promise<{
  task_id: string
  items: HandoffRecord[]
  total: number
}> {
  const response = await request(
    `/api/transcribe-task/${encodeURIComponent(taskId)}/version-handoffs`,
  )
  if (!response.ok) throw new Error('交接记录读取失败')
  return await response.json()
}

export async function openHandoff(
  taskId: string,
  payload: {
    version_id: string
    version_no: string
    parent: string
    change_lines: number
    summary: string
    digest: string
  },
): Promise<ActionResult> {
  const response = await request(
    `/api/transcribe-task/${encodeURIComponent(taskId)}/version-handoffs`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  return await response.json()
}

export async function resumeHandoff(
  handoffId: string,
  digest?: string,
): Promise<ActionResult> {
  const response = await request(
    `/api/version-handoffs/${encodeURIComponent(handoffId)}/resume`,
    { method: 'POST', body: JSON.stringify({ digest: digest ?? null }) },
  )
  return await response.json()
}

export async function rejectHandoff(
  handoffId: string,
  reason: string,
): Promise<ActionResult> {
  const response = await request(
    `/api/version-handoffs/${encodeURIComponent(handoffId)}/reject`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  )
  return await response.json()
}

export async function listAllHandoffs(taskId?: string): Promise<{
  items: HandoffRecord[]
  total: number
}> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  const response = await request(`/api/version-handoffs${query}`)
  if (!response.ok) throw new Error('版本交接记录读取失败')
  return await response.json()
}
