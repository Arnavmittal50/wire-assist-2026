import type { Session } from '../types/api';

export function reconcileSession(current: Session | null, incoming: Session, activeId: string | null): Session | null {
  if (incoming.session_id !== activeId || current?.session_id !== incoming.session_id) return current;
  return incoming.revision >= current.revision ? incoming : current;
}
