// WF-21 phase 2: load each dashboard screen in headless Chromium as owner / member (mock Clerk session cookies), time it, collect console errors,
// block every non-localhost request (mock Clerk cannot serve clerk-js) and list what was blocked. usage: node wf21_b_dash.mjs [owner|member|anon]
import { createRequire } from 'module'
import { execFileSync } from 'child_process'
import fs from 'fs'
const require = createRequire('C:/Users/kiran/Desktop/Conxa/runtime/package.json')
const { chromium } = require('playwright')
const role = process.argv[2] || 'owner'
const tool = 'C:/Users/kiran/Desktop/Conxa/docs/testing/tools'
const SFX = 'g_f7dfRx'
const tok = role === 'anon' ? '' : execFileSync('python', ['-c', `import sys;sys.path.insert(0,'${tool}');import wf21_tok as T;print(T.mint('user_${role}','org_a','${role === 'owner' ? 'org:admin' : 'org:member'}'))`]).toString().trim()
const screens = ['/', '/pricing', '/docs', '/sign-in', '/dashboard', '/dashboard/workflows', '/dashboard/impact', '/dashboard/healing', '/fleet', '/audit', '/packages', '/packages/installer', '/billing', '/team', '/settings', '/report-bug']
const b = await chromium.launch({ headless: true })
const ctx = await b.newContext({ viewport: { width: 1366, height: 800 } })
if (tok) {
  const c = (name, value) => ({ name, value, domain: 'localhost', path: '/' })
  await ctx.addCookies([c('__session', tok), c(`__session_${SFX}`, tok), c('__client_uat', '1700000000'), c(`__client_uat_${SFX}`, '1700000000'), c('__clerk_db_jwt', 'dvb_x'), c(`__clerk_db_jwt_${SFX}`, 'dvb_x')])
}
const blocked = new Set()
await ctx.route('**/*', r => { const u = new URL(r.request().url()); if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') return r.continue(); blocked.add(u.host); return r.abort() })
const rows = []
for (const p of screens) {
  const page = await ctx.newPage(); const errs = []; const apiFail = []
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)) })
  page.on('pageerror', e => errs.push('PAGEERROR ' + String(e).slice(0, 160)))
  page.on('response', r => { if (r.url().includes('/api/v1/') && r.status() >= 400) apiFail.push(r.status() + ' ' + new URL(r.url()).pathname) })
  const t = Date.now(); let status = 0, end = ''
  try { const r = await page.goto('http://localhost:3100' + p, { waitUntil: 'load', timeout: 20000 }); status = r.status() } catch (e) { end = 'GOTO ' + String(e).slice(0, 80) }
  const ms = Date.now() - t
  await page.waitForTimeout(1500)
  const url = new URL(page.url()).pathname
  const text = (await page.evaluate(() => document.body.innerText).catch(() => '')).replace(/\s+/g, ' ').slice(0, 110)
  const clerkErrs = errs.filter(e => /clerk|ERR_FAILED|net::|Failed to load resource/i.test(e)); const other = errs.filter(e => !clerkErrs.includes(e))
  rows.push({ p, status, loadMs: ms, finalPath: url, errors: errs.length, nonNetworkErrors: other, apiFail, end, text })
  console.log(role, p, status, ms + 'ms', '->' + url, 'errs=' + errs.length, 'other=' + other.length, apiFail.length ? 'api:' + apiFail.join(',') : '', '|', text.slice(0, 70))
  if (other.length) console.log('   other errors:', other.slice(0, 3).map(s => s.slice(0, 90)))
  if (p === '/') console.log('   baseline errors on /:', [...new Set(errs.map(s => s.slice(0, 110)))])
  await page.close()
}
console.log('blocked external hosts:', [...blocked])
fs.writeFileSync(`C:/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-021/wf21_b_dash_${role}.json`, JSON.stringify({ rows, blocked: [...blocked] }, null, 1))
await b.close()
