import api from './index'

// ────────── Versions（F11 版本控制） ──────────

export interface VersionMeta {
  id: number
  doc_key: string
  version: number
  title?: string
  checksum: string
  author: string
  change_summary?: string
  created_at: string
}

export interface VersionDetail extends VersionMeta {
  content: string
}

export interface VersionListResponse {
  doc_key: string
  versions: VersionMeta[]
  count: number
}

export interface DiffResponse {
  doc_key: string
  v1: number
  v2: number
  added_lines: number
  removed_lines: number
  diff: string
  error?: string
}

export interface SaveVersionResult {
  doc_key: string
  version: number
  title?: string
  checksum: string
  created_at: string
  skipped?: boolean
  reason?: string
}

/**
 * 列出某 doc_key 的所有版本（不含 content）
 * GET /versions/{doc_key}
 */
export function listVersions(docKey: string) {
  return api.get<any, VersionListResponse>(
    `/versions/${encodeURIComponent(docKey)}`,
  )
}

/**
 * 获取指定版本的完整内容
 * GET /versions/{doc_key}/{version}
 */
export function getVersion(docKey: string, version: number) {
  return api.get<any, VersionDetail>(
    `/versions/${encodeURIComponent(docKey)}/${version}`,
  )
}

/**
 * 对比两个版本（unified diff）
 * GET /versions/{doc_key}/diff/{v1}/{v2}
 * 注意：版本不存在时后端返回 200 + {error: "..."}，前端需检查 error 字段
 */
export function diffVersions(docKey: string, v1: number, v2: number) {
  return api.get<any, DiffResponse>(
    `/versions/${encodeURIComponent(docKey)}/diff/${v1}/${v2}`,
  )
}

/**
 * 保存新版本（认证必需）
 * POST /versions/{doc_key}/save?title=&content=&change_summary=
 * 注意：title / content / change_summary 是 query 参数（不是 body）
 */
export function saveVersion(
  docKey: string,
  payload: { title: string; content: string; change_summary?: string; author?: string },
) {
  return api.post<any, SaveVersionResult>(
    `/versions/${encodeURIComponent(docKey)}/save`,
    null,
    {
      params: {
        title: payload.title,
        content: payload.content,
        change_summary: payload.change_summary ?? '',
        author: payload.author ?? 'user',
      },
    },
  )
}

/**
 * 回滚到指定版本（认证必需）
 * POST /versions/{doc_key}/rollback/{target_version}
 * 行为：以目标版本内容创建一个新版本，不删除历史
 */
export function rollbackVersion(docKey: string, targetVersion: number) {
  return api.post<any, SaveVersionResult>(
    `/versions/${encodeURIComponent(docKey)}/rollback/${targetVersion}`,
  )
}

// ────────── Wiki 文档浏览（用于 F11 选 doc_key） ──────────

export interface WikiDocSummary {
  slug: string
  title: string
  version: number
  updated_at?: string
}

/**
 * 列出已发布的 Wiki 文档（基于版本表 wiki:* 前缀）
 * GET /wiki?limit=&offset=
 * 用于在 F11 中选择 doc_key
 */
export function listWikiDocs(limit = 50, offset = 0) {
  return api.get<any, { documents: WikiDocSummary[]; count: number }>(
    '/wiki',
    { params: { limit, offset } },
  )
}

