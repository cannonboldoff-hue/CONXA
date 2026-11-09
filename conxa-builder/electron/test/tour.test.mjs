import test from 'node:test'
import assert from 'node:assert'
import { DEMOS, getDemo, stepsFor } from '../renderer/src/lib/tourScripts.ts'

test('four demos, each with a full path ending in a finish button', () => {
  assert.strictEqual(DEMOS.length, 4)
  for (const d of DEMOS) {
    const steps = stepsFor(d)
    assert.ok(steps.length > 8)
    assert.deepStrictEqual(steps.at(-1).advance, { button: 'Finish tour' })
    assert.ok(d.startPath.startsWith('/practice/'))
    assert.ok(getDemo(d.id))
  }
})

test('every locked step names a target; free-roam steps are never locked', () => {
  for (const s of stepsFor(DEMOS[0])) {
    if (s.lock) assert.ok(s.target?.length, s.id)
    if (['record', 'questions', 'done'].includes(s.id)) assert.strictEqual(s.lock, false)
  }
})

test('tour text uses no technical words', () => {
  const bad = /selector|step id|\blog\b|stack|config|json|xpath/i
  for (const d of DEMOS) for (const s of stepsFor(d)) assert.ok(!bad.test(s.text), s.text)
})
