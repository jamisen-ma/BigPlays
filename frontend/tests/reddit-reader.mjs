import { chromium } from '@playwright/test'
import assert from 'node:assert/strict'
import { readComments, readPosts } from '../scripts/reddit-reader.mjs'

const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  const page = await browser.newPage()
  await page.setContent(`<shreddit-post id="t3_abc" post-title="[Game Thread] Away at Home"
    created-timestamp="2026-09-26T23:00:00Z"></shreddit-post>
    <shreddit-comment thingid="t1_one" postid="t3_abc" author="first" created="2026-09-26T23:05:00Z">
      <div slot="comment">OMG that catch!</div>
      <shreddit-comment thingid="t1_two" postid="t3_abc" author="second" created="2026-09-26T23:05:01Z">
        <div slot="comment">crazyyy</div>
      </shreddit-comment>
    </shreddit-comment>
    <shreddit-comment thingid="t1_missing" author="third"><div slot="comment">No exact timestamp</div></shreddit-comment>`)
  const posts = await page.evaluate(readPosts)
  assert.equal(posts[0].id, 'abc')
  assert.equal(posts[0].created_utc, Date.parse('2026-09-26T23:00:00Z') / 1000)
  const comments = await page.evaluate(readComments, 'abc')
  assert.deepEqual(comments.map(c => c.body), ['OMG that catch!', 'crazyyy'])
  assert.equal(comments.length, 2, 'Timestamp-less comments must not enter reaction windows')
  console.log('Public DOM parser fixtures passed; this does not establish live Reddit access.')
} finally { await browser.close() }
