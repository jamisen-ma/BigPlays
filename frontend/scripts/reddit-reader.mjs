// Public, visible Reddit DOM only. No login automation, CAPTCHA solving or stealth.
import { chromium } from '@playwright/test'
import { pathToFileURL } from 'node:url'

export function readPosts() {
  return [...document.querySelectorAll('shreddit-post')].slice(0, 100).map(el => ({
    id: (el.getAttribute('id') || '').replace(/^t3_/, ''),
    title: el.getAttribute('post-title') || '',
    created_utc: Date.parse(el.getAttribute('created-timestamp') || '') / 1000,
    over_18: el.getAttribute('nsfw') === 'true',
  })).filter(p => p.id && p.title && Number.isFinite(p.created_utc))
}

export function readComments(threadId) {
  return [...document.querySelectorAll('shreddit-comment')].slice(0, 100).map(el => {
    const own = selector => [...el.querySelectorAll(selector)].find(n => n.closest('shreddit-comment') === el)
    return {
      id: (el.getAttribute('thingid') || el.getAttribute('id') || '').replace(/^t1_/, ''),
      link_id: el.getAttribute('postid') || 't3_' + threadId,
      author: el.getAttribute('author') || '',
      body: (own('[slot="comment"]')?.textContent || '').trim().slice(0, 800),
      created_utc: Date.parse(el.getAttribute('created') || own('time[datetime]')?.getAttribute('datetime') || '') / 1000,
    }
  }).filter(c => c.id && c.body && Number.isFinite(c.created_utc))
}

async function main() {
  const [mode, value] = process.argv.slice(2)
  if (!(mode === 'threads' && ['CFB', 'nfl', 'nba'].includes(value)) &&
      !(mode === 'comments' && /^[a-z0-9]{1,16}$/.test(value || ''))) {
    throw new Error('Invalid browser read request')
  }
  const url = mode === 'threads'
    ? `https://www.reddit.com/r/${value}/search/?q=%22game%20thread%22&restrict_sr=1&sort=new&t=day`
    : `https://www.reddit.com/comments/${value}/?sort=new`
  const browser = await chromium.launch({
    ...(process.env.REDDIT_BROWSER_CHANNEL === 'chromium' ? {} : { channel: 'chrome' }),
    headless: true,
  })
  const stop = () => { void browser.close() }
  process.once('SIGTERM', stop)
  try {
    const page = await browser.newPage()
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(2000)
    const body = await page.locator('body').innerText()
    if ([401, 403, 429].includes(response?.status()) ||
        /prove your humanity|blocked by network security|verify you are human|complete the challenge/i.test(body)) {
      return { ok: false, status: 'browser_access_blocked' }
    }
    if (!response?.ok()) return { ok: false, status: 'browser_unavailable' }
    const items = mode === 'threads' ? await page.evaluate(readPosts) : await page.evaluate(readComments, value)
    if (!items.length) return { ok: false, status: 'browser_no_timestamped_content' }
    return { ok: true, items }
  } finally { process.removeListener('SIGTERM', stop); await browser.close() }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { console.log(JSON.stringify(await main())) }
  catch { console.log(JSON.stringify({ ok: false, status: 'browser_unavailable' })) }
}
