import { describe, expect, it } from 'vitest';

import { isTerminal } from '../../lib/types';

describe('isTerminal', () => {
  it('stops polling only after a document reaches a terminal state', () => {
    expect(isTerminal('completed')).toBe(true);
    expect(isTerminal('partial')).toBe(true);
    expect(isTerminal('failed')).toBe(true);
    expect(isTerminal('quarantined')).toBe(true);
    expect(isTerminal('processing')).toBe(false);
  });
});
