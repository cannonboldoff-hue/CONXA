// WF-21 2d: member clicks the owner-only buttons (Billing "Choose Starter", Team "Invite"); what does the person see? node wf21_b_click.mjs
import { createRequire } from 'module'; import { execFileSync } from 'child_process'
const require = createRequire('C:/Users/kiran/Desktop/Conxa/runtime/package.json'); const { chromium } = require('playwright')
const tool = 'C:/Users/kiran/Desktop/Conxa/docs/testing/tools'; const SFX = 'g_f7dfRx'
const tok = execFileSync('python', ['-c', `import sys;sys.path.insert(0,'${tool}');import wf21_tok as T;print(T.mint('user_member','org_a','org:member'))`]).toString().trim()
const b = await chromium.launch({ headless: true }); const ctx = await b.newContext({ viewport: { width: 1366, height: 900 } })
const c = (name, value) => ({ name, value, domain: 'localhost', path: '/' })
await ctx.addCookies([c('__session', tok), c(`__session_${SFX}`, tok), c('__client_uat', '1700000000'), c(`__client_uat_${SFX}`, '1700000000'), c('__clerk_db_jwt', 'dvb_x'), c(`__clerk_db_jwt_${SFX}`, 'dvb_x')])
await ctx.route('**/*', r => { const u = new URL(r.request().url()); return (u.hostname === 'localhost' || u.hostname === '127.0.0.1') ? r.continue() : r.abort() })
for (const [p, label] of [['/billing', 'Choose Starter'], ['/team', 'Invite']]) {
  const page = await ctx.newPage(); const calls = []
  page.on('response', r => { if (r.url().includes('/api/v1/') && r.request().method() !== 'GET') calls.push(r.request().method() + ' ' + new URL(r.url()).pathname + ' -> ' + r.status()) })
  await page.goto('http://localhost:3100' + p, { waitUntil: 'load' }); await page.waitForTimeout(1500)
  const before = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' '))
  await page.getByRole('button', { name: label }).first().click({ timeout: 5000 }).catch(e => console.log('click failed', String(e).slice(0, 80)))
  await page.waitForTimeout(2500)
  const after = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' '))
  const added = after.split(' ').filter(w => !before.includes(w)).join(' ')
  console.log(p, label, '| mutating calls:', calls, '| new text:', added.slice(0, 300) || '(none)'); await page.close()
}
await b.close()
