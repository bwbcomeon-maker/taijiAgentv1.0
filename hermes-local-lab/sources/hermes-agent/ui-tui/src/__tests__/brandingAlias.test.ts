import { describe, expect, it } from 'vitest'

import { displaySkillNameForBranding } from '../components/branding.js'

describe('displaySkillNameForBranding', () => {
  it('aliases the legacy internal skill id only at the display layer', () => {
    expect(displaySkillNameForBranding('hermes-agent')).toBe('taiji-agent')
    expect(displaySkillNameForBranding('kanban-codex')).toBe('kanban-codex')
  })
})
