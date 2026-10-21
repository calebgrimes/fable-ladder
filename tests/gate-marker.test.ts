// Tests for the gate marker in the live plugin: the status entry, the button,
// and the row cache. The hooks here sit beneath the plugin and stand in for Claude Code.
import { describe, expect, mock, test } from 'claude-code/testing'

const SID = 'sid-main'
const R = (mode: string) =>
  JSON.stringify({ session_id: SID, level: 'conductor', mode, decision: 'DENY' })

// Beneath the plugin: a fake session id, a fake log, and a catch for status and fill.
function world(on: any, logInit: string | null) {
  let logText = logInit
  let sid = SID
  const status: Array<string | undefined> = []
  const fills: string[] = []
  mock.clock(on)
  on('session.start', ($: any, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: sid }))
  on('fs.stat', () =>
    logText === null
      ? { deny: 'ENOENT' }
      : { value: { kind: 'file', size: logText.length, mtimeMs: 0, isLink: false } },
  )
  on('fs.read', () => (logText === null ? { deny: 'ENOENT' } : { value: logText }))
  on('env.get', ($: any, e: any) => ({ value: e.name === 'HOME' ? '/home/u' : undefined }))
  on('command.register', () => ({ value: { command: 'ladder' } }))
  on('store.get', () => ({ value: undefined }))
  on('ui.status', ($: any, e: any) => {
    status.push(e.text)
    return { value: undefined }
  })
  on('prompt.fill', ($: any, e: any) => {
    fills.push(e.text)
    return { isFilled: true }
  })
  return { status, fills, setLog: (t: string | null) => { logText = t },
    setSid: (t: string) => { sid = t } }
}

const start = ($: any) => $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true })

describe('status entry', () => {
  for (const [mode, label] of [
    ['enforce', '◆ CONDUCTOR'],
    ['guards', '◇ DIRECT'],
    ['warn', '○ GATE warn'],
  ] as const) {
    test(`mode ${mode}`, async ($, on) => {
      const w = world(on, R(mode))
      await start($)
      expect(w.status.at(-1)).toBe(label)
    })
  }
  test('no row for this session', async ($, on) => {
    const w = world(on, JSON.stringify({ session_id: 'other', level: 'conductor', mode: 'enforce' }))
    await start($)
    expect(w.status.at(-1)).toBe('○ GATE ?')
  })
  test('missing log does not throw', async ($, on) => {
    const w = world(on, null)
    await start($)
    expect(w.status.at(-1)).toBe('○ GATE ?')
  })
})

describe('button', () => {
  for (const [mode, label, fill] of [
    ['enforce', 'Conductor off', '/ladder:conduct off'],
    ['guards', 'Conductor on', '/ladder:conduct on'],
  ] as const) {
    test(`mode ${mode} fills ${fill}`, async ($, on) => {
      const w = world(on, R(mode))
      await start($)
      const ui = await $.ui.mount({
        plugin: 'ladder',
        surface: 'terminal',
        component: 'AbovePrompt',
        props: { hasSurvey: false, isWorking: false, maxRows: 5 },
      })
      expect((await ui.find({ key: 'toggle' }))?.text).toBe(label)
      await ui.press({ key: 'toggle' })
      expect(w.fills).toEqual([fill])
    })
  }
})

describe('row cache in the live mod', () => {
  test('the label stays when the tail loses the row, then follows a newer row', async ($, on) => {
    const w = world(on, R('guards'))
    await start($)
    expect(w.status.at(-1)).toBe('◇ DIRECT')
    // Other windows pushed this row out of the tail.
    w.setLog(JSON.stringify({ session_id: 'other', level: 'conductor', mode: 'enforce' }))
    await $.turn.complete({} as any).catch(() => undefined)
    await start($)
    expect(w.status.at(-1)).toBe('◇ DIRECT')
    // A newer row for this session with another mode.
    w.setLog(JSON.stringify({ session_id: SID, level: 'conductor', mode: 'enforce', time: '2999-01-01T00:00:00Z' }))
    await start($)
    expect(w.status.at(-1)).toBe('◆ CONDUCTOR')
  })
  test('a new session id drops the old cache', async ($, on) => {
    const w = world(on, R('guards'))
    await start($)
    expect(w.status.at(-1)).toBe('◇ DIRECT')
    // /clear: same window, new session id, no row for it yet.
    w.setSid('sid-new')
    await start($)
    expect(w.status.at(-1)).toBe('○ GATE ?')
    // A row for the new session appears.
    w.setLog(JSON.stringify({ session_id: 'sid-new', level: 'conductor', mode: 'enforce' }))
    await start($)
    expect(w.status.at(-1)).toBe('◆ CONDUCTOR')
  })
})
