import { describe, expect, it } from 'vitest';
import { makeSession } from '../test/fixtures';
import { reconcileSession } from './sessionState';

describe('session update precedence', () => {
  it('never revives a deleted session or overwrites a newly selected session', () => {
    const old = makeSession();
    expect(reconcileSession(null, old, null)).toBeNull();
    const current = makeSession({ session_id: 'session-new' });
    expect(reconcileSession(current, old, 'session-new')).toBe(current);
  });
  it('ignores stale analysis snapshots and accepts a newer same-session revision', () => {
    const current = makeSession({ revision: 5 });
    expect(reconcileSession(current, makeSession({ revision: 3 }), current.session_id)).toBe(current);
    const newer = makeSession({ revision: 6 });
    expect(reconcileSession(current, newer, current.session_id)).toBe(newer);
  });
});
