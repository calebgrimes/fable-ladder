// Tests for the pure helpers of the gate marker (hooks/gate-status.mjs).
import { describe, expect, test } from 'claude-code/testing'

import { buttonFor, labelFor, lastConductorRow, logPath, mergeSeen, readTail } from '../hooks/gate-status.mjs'

const lastConductorMode = (text: string, sid: string) => {
  const r = lastConductorRow(text, sid)
  return r === null ? null : r.mode
}

const SID = 'sid-main'
const row = (o: Record<string, unknown>) => JSON.stringify(o)
const R = (mode: string, extra: Record<string, unknown> = {}) =>
  row({ session_id: SID, level: 'conductor', mode, decision: 'DENY', ...extra })

describe('lastConductorMode', () => {
  test('enforce row', () => {
    expect(lastConductorMode(R('enforce'), SID)).toBe('enforce')
  })
  test('guards row', () => {
    expect(lastConductorMode(R('guards'), SID)).toBe('guards')
  })
  test('other modes pass through', () => {
    for (const m of ['log', 'warn', 'off']) {
      expect(lastConductorMode(R(m), SID)).toBe(m)
    }
  })
  test('the last row wins', () => {
    expect(lastConductorMode([R('enforce'), R('guards', { decision: 'MODE' })].join('\n'), SID)).toBe('guards')
  })
  test('no row for the session', () => {
    expect(lastConductorMode('', SID)).toBe(null)
  })
  test('a row from another session is ignored', () => {
    const other = row({ session_id: 'other', level: 'conductor', mode: 'guards' })
    expect(lastConductorMode(other, SID)).toBe(null)
    expect(lastConductorMode([R('enforce'), other].join('\n'), SID)).toBe('enforce')
  })
  test('a suborchestrator row is ignored', () => {
    const sub = row({ session_id: SID, level: 'suborch', mode: 'guards' })
    expect(lastConductorMode(sub, SID)).toBe(null)
    expect(lastConductorMode([R('enforce'), sub].join('\n'), SID)).toBe('enforce')
  })
  test('a bad JSON line is skipped', () => {
    expect(lastConductorMode([R('enforce'), '{not json', '', '123', 'null'].join('\n'), SID)).toBe('enforce')
  })
  test('a cut first line of a tail is skipped', () => {
    expect(lastConductorMode(['ession_id": "x"}', R('guards')].join('\n'), SID)).toBe('guards')
  })
  test('an empty session id gives no mode', () => {
    expect(lastConductorMode(R('enforce'), '')).toBe(null)
  })
})

describe('labels and buttons', () => {
  test('labels carry a shape and a word', () => {
    expect(labelFor('enforce')).toBe('◆ CONDUCTOR')
    expect(labelFor('guards')).toBe('◇ DIRECT')
    expect(labelFor(null)).toBe('○ GATE ?')
    expect(labelFor('warn')).toBe('○ GATE warn')
  })
  test('button fill text for each mode', () => {
    expect(buttonFor('enforce')).toEqual({ label: 'Conductor off', fill: '/ladder:conduct off' })
    expect(buttonFor('guards')).toEqual({ label: 'Conductor on', fill: '/ladder:conduct on' })
    expect(buttonFor(null)).toEqual({ label: 'Conductor on', fill: '/ladder:conduct on' })
    expect(buttonFor('off')).toEqual({ label: 'Conductor on', fill: '/ladder:conduct on' })
  })
})

describe('logPath', () => {
  const env = (vars: Record<string, string>) => ({ env: { get: async (n: string) => vars[n] } })
  test('CLAUDE_CONFIG_DIR wins', async () => {
    expect(await logPath(env({ CLAUDE_CONFIG_DIR: '/c', HOME: '/h' }))).toBe('/c/conductor-gate/log.jsonl')
  })
  test('else HOME/.claude', async () => {
    expect(await logPath(env({ HOME: '/h' }))).toBe('/h/.claude/conductor-gate/log.jsonl')
  })
  test('a relative value is not used', async () => {
    expect(await logPath(env({ CLAUDE_CONFIG_DIR: 'rel', HOME: 'x' }))).toBe(null)
  })
})

describe('readTail', () => {
  const mk = (size: number, text: string | Error, tail?: { exitCode: number; stdout: string }) => {
    const calls: string[] = []
    const $ = {
      fs: {
        stat: async () => {
          if (size < 0) throw new Error('ENOENT')
          return { size }
        },
        read: async () => {
          calls.push('read')
          if (text instanceof Error) throw text
          return text
        },
      },
      process: {
        run: async (argv: readonly string[]) => {
          calls.push(argv.join(' '))
          return tail ?? { exitCode: 1, stdout: '' }
        },
      },
    }
    return { $, calls }
  }
  test('missing log gives null and does not throw', async () => {
    expect(await readTail(mk(-1, 'x').$, '/x/log')).toBe(null)
  })
  test('small log is read whole', async () => {
    const m = mk(100, 'abc')
    expect(await readTail(m.$, '/x/log', 1000)).toBe('abc')
    expect(m.calls).toEqual(['read'])
  })
  test('large log uses tail -c, not a whole read', async () => {
    const m = mk(5000, 'never', { exitCode: 0, stdout: 'TAIL' })
    expect(await readTail(m.$, '/x/log', 1000)).toBe('TAIL')
    expect(m.calls).toEqual(['tail -c 1000 /x/log'])
  })
  test('a failed tail gives null', async () => {
    expect(await readTail(mk(5000, 'x').$, '/x/log', 1000)).toBe(null)
  })
  test('a read error gives null', async () => {
    expect(await readTail(mk(10, new Error('boom')).$, '/x/log', 1000)).toBe(null)
  })
})

describe('row cache', () => {
  const T = (mode: string, time: string) => R(mode, { time })
  const seenOf = (text: string) => lastConductorRow(text, SID)

  test('lastConductorRow returns the time and the mode', () => {
    expect(seenOf(T('guards', '2026-10-19T10:00:00Z'))).toEqual({ time: '2026-10-19T10:00:00Z', mode: 'guards' })
  })
  test('a row seen, then a tail with no row: the cache stays', () => {
    const c1 = mergeSeen(null, seenOf(T('guards', '2026-10-19T10:00:00Z')))
    const c2 = mergeSeen(c1, seenOf(row({ session_id: 'other', level: 'conductor', mode: 'enforce', time: '2026-10-19T11:00:00Z' })))
    expect(c2?.mode).toBe('guards')
    expect(labelFor(c2?.mode ?? null)).toBe('◇ DIRECT')
  })
  test('never seen gives the question mark label', () => {
    expect(labelFor(mergeSeen(null, seenOf(''))?.mode ?? null)).toBe('○ GATE ?')
  })
  test('a newer row with another mode changes the cache', () => {
    const c1 = mergeSeen(null, seenOf(T('guards', '2026-10-19T10:00:00Z')))
    const c2 = mergeSeen(c1, seenOf(T('enforce', '2026-10-19T10:05:00Z')))
    expect(c2?.mode).toBe('enforce')
  })
  test('an older row in a later tail does not replace a newer cache', () => {
    const c1 = mergeSeen(null, seenOf(T('enforce', '2026-10-19T10:05:00Z')))
    const c2 = mergeSeen(c1, seenOf(T('guards', '2026-10-19T09:00:00Z')))
    expect(c2?.mode).toBe('enforce')
  })
  test('a row with no time does not replace a timed cache', () => {
    const c1 = mergeSeen(null, seenOf(T('enforce', '2026-10-19T10:05:00Z')))
    expect(mergeSeen(c1, seenOf(R('guards')))?.mode).toBe('enforce')
  })
})
