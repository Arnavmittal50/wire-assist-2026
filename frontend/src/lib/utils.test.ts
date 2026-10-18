import { describe, expect, it } from 'vitest';
import { dateLabel, dateTimeLabel } from './utils';

describe('Pacific event display', () => {
  it('preserves the dataset local calendar date across the UTC midnight boundary', () => {
    expect(dateLabel('2026-09-27T02:25:00Z')).toBe('Sep 26');
    expect(dateTimeLabel('2026-09-27T02:25:00Z')).toBe('Sep 26, 2026, 7:25 PM PT');
  });
});
