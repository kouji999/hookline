import { describe, expect, it } from 'vitest'
import { chapterLines, extractVideoId, fmt, fmtRange, plainLines, titledLines } from '../time'
import type { Clip } from '../../types'

const clips: Clip[] = [
  { start: 3723, end: 3753, title: 'Beta', reason: '', quote: '', score: 0.9 },
  { start: 83, end: 113, title: 'Alpha', reason: '', quote: '', score: 0.8 },
]

describe('extractVideoId', () => {
  it('parses watch/shorts/embed/youtu.be/id forms', () => {
    expect(extractVideoId('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(extractVideoId('https://youtu.be/dQw4w9WgXcQ?t=1')).toBe('dQw4w9WgXcQ')
    expect(extractVideoId('https://youtube.com/shorts/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(extractVideoId('dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
  })
  it('returns null for garbage', () => {
    expect(extractVideoId('https://example.com?v=abc')).toBeNull()
    expect(extractVideoId('')).toBeNull()
  })
})

describe('time formatting', () => {
  it('fmt renders m:ss, mm:ss, h:mm:ss', () => {
    expect(fmt(0)).toBe('0:00')
    expect(fmt(83)).toBe('1:23')
    expect(fmt(600)).toBe('10:00')
    expect(fmt(3723)).toBe('1:02:03')
  })
  it('fmtRange joins start and end', () => {
    expect(fmtRange(clips[0])).toBe('1:02:03 - 1:02:33')
  })
})

describe('copy formats', () => {
  it('plain lines keep card order', () => {
    expect(plainLines(clips)).toBe('1:02:03 - 1:02:33\n1:23 - 1:53')
  })
  it('titled lines append titles', () => {
    expect(titledLines(clips)).toBe('1:02:03 - 1:02:33 | Beta\n1:23 - 1:53 | Alpha')
  })
  it('chapter lines sort by start', () => {
    expect(chapterLines(clips)).toBe('1:23 Alpha\n1:02:03 Beta')
  })
})
