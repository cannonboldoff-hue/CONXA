// WF-21 2b: what a member SEES vs owner on owner-action screens: visible text + enabled buttons. node wf21_b_see.mjs
import { createRequire } from 'module'; import { execFileSync } from 'child_process'; import fs from 'fs'
const require = createRequire('C:/Users/kiran/Desktop/Conxa/runtime/package.json'); const { chromium } = require('playwright')
const tool = 'C:/Users/kiran/Desktop/Conxa/docs/testing/tools'; const SFX = 'g_f7dfRx'; const out = {}
const b = await chromium.launch({ headless: true })
for (const role of ['owner', 'member']) {
  const tok = execFileSync('python', ['-c', `import sys;sys.path.insert(0,'${tool}');import wf21_tok as T;print(T.mint('user_${role}','org_a','${role === 'owner' ? 'org:admin' : 'org:member'}'))`]).toString().trim()
  const ctx = await b.newContext({ viewport: { width: 1366, height: 900 } })
  const c = (name, value) => ({ name, value, domain: 'localhost', path: '/' })
  await ctx.addCookies([c('__session', tok), c(`__session_${SFX}`, tok), c('__client_uat', '1700000000'), c(`__client_uat_${SFX}`, '1700000000'), c('__clerk_db_jwt', 'dvb_x'), c(`__clerk_db_jwt_${SFX}`, 'dvb_x')])
  await ctx.route('**/*', r => { const u = new URL(r.request().url()); return (u.hostname === 'localhost' || u.hostname === '127.0.0.1') ? r.continue() : r.abort() })
  for (const p of ['/packages', '/packages/installer', '/team', '/settings', '/billing', '/audit']) {
    const page = await ctx.newPage(); await page.goto('http://localhost:3100' + p, { waitUntil: 'load' }); await page.waitForTimeout(2000)
    const main = await page.evaluate(() => { const m = document.querySelector('main') || document.body; return { text: m.innerText.replace(/\s+/g, ' ').slice(0, 900), buttons: [...m.querySelectorAll('button,a[role=button]')].map(x => (x.innerText || x.getAttribute('aria-label') || '').trim() + (x.disabled ? '[disabled]' : '')).filter(Boolean) } })
    out[`${role} ${p}`] = main; await page.close()
  }
  await ctx.close()
}
await b.close()
for (const p of ['/packages', '/packages/installer', '/team', '/settings', '/billing', '/audit']) {
  const o = out['owner ' + p], m = out['member ' + p]
  console.log('==', p, '\n OWNER buttons:', o.buttons.join(' | '), '\n MEMBER buttons:', m.buttons.join(' | '), '\n OWNER text:', o.text.slice(0, 260), '\n MEMBER text:', m.text.slice(0, 260))
}
fs.writeFileSync('C:/Users/kiran/Desktop/Conxa/docs/testing/evidence/WF-021/wf21_b_see.json', JSON.stringify(out, null, 1))
