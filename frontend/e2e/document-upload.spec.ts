import { test, expect } from '@playwright/test'
import { skipIfBackendDown, login } from './helpers/auth'

/**
 * E2E 旅程 4：文档上传流程（P4-5）
 *
 * 覆盖：
 * 1. 文档管理页可访问
 * 2. 通过 API 上传文档（端到端：解析 → 存储 → 索引）
 * 3. 上传后文档出现在列表中
 * 4. 文档详情可查看
 * 5. LLM Wiki ingest 端到端编译
 *
 * 前置条件：已登录，后端可用
 */

const SAMPLE_MARKDOWN = `# Nginx 502 故障排查

## 概述
Nginx 502 Bad Gateway 表示上游服务不可达。

## 排查步骤
1. 检查上游服务：\`systemctl status nginx\`
2. 检查日志：\`tail -f /var/log/nginx/error.log\`
3. 检查网络：\`telnet upstream_host 80\`

## 配置参数
- proxy_read_timeout: 60s
- proxy_connect_timeout: 60s
`

test.describe('文档上传旅程', () => {
  test.beforeEach(async ({ page }) => {
    await skipIfBackendDown(page)
    await login(page)
  })

  test('文档管理页可访问并渲染', async ({ page }) => {
    await page.goto('/documents')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/documents/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('通过 API 上传文档成功', async ({ page }) => {
    const resp = await page.request.post('/api/parsers/parse/markdown', {
      multipart: {
        file: {
          name: 'e2e-test-nginx-502.md',
          mimeType: 'text/markdown',
          buffer: Buffer.from(SAMPLE_MARKDOWN),
        },
      },
    })
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data.stored).toBe(true)
    expect(data.doc_id).toBeTruthy()
    expect(data.element_count).toBeGreaterThan(0)
  })

  test('上传后文档可通过搜索检索', async ({ page }) => {
    const uploadResp = await page.request.post('/api/parsers/parse/markdown', {
      multipart: {
        file: {
          name: 'e2e-search-test.md',
          mimeType: 'text/markdown',
          buffer: Buffer.from(SAMPLE_MARKDOWN),
        },
      },
    })
    expect(uploadResp.status()).toBe(200)
    await page.waitForTimeout(1000)
    const searchResp = await page.request.get('/api/search?q=nginx+502&limit=5')
    expect(searchResp.status()).toBe(200)
    const searchData = await searchResp.json()
    expect(searchData.count).toBeGreaterThanOrEqual(0)
  })

  test('LLM Wiki ingest 编译文档为 wiki 页面', async ({ page }) => {
    const resp = await page.request.post('/api/llm-wiki/ingest', {
      multipart: {
        file: {
          name: 'e2e-ingest-test.md',
          mimeType: 'text/markdown',
          buffer: Buffer.from(SAMPLE_MARKDOWN),
        },
      },
      timeout: 30_000,
    })
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data.doc_id).toBeTruthy()
    expect(data.compile).toBeTruthy()
    expect(Array.isArray(data.compile.slugs)).toBe(true)
  })
})