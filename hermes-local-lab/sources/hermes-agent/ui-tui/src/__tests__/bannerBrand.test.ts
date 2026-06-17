import { describe, expect, it } from 'vitest'

import { caduceus, logo } from '../banner.js'
import { DARK_THEME } from '../theme.js'

const textOf = (lines: [string, string][]) => lines.map(([, text]) => text).join('\n')

describe('default banner brand art', () => {
  it('uses Taiji Agent logo text instead of the legacy full-width mark', () => {
    const text = textOf(logo(DARK_THEME.color))

    expect(text).toContain('Taiji Agent')
    expect(text).toContain('太极智能体')
    expect(text).not.toContain('HERMES')
  })

  it('uses a taiji dot-matrix hero without legacy caduceus text markers', () => {
    const text = textOf(caduceus(DARK_THEME.color))

    expect(text).toContain('●')
    expect(text).not.toContain('HERMES')
    expect(text).not.toContain('NOUS')
  })
})
