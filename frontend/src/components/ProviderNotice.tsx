import { FlaskConical, Sparkles, TriangleAlert } from 'lucide-react';
import type { Session } from '../types/api';

export function ProviderNotice({ provider, notice, pending = false }: { provider: Session['analysis_provider']; notice: string; pending?: boolean }) {
  if (pending && notice === 'Preparing the initial member-history review.') return <div className="provider-notice" role="status"><Sparkles size={14} /><span><strong>Gemini review in progress</strong>{notice}</span></div>;
  return <div className={`provider-notice provider-${provider}`} role="status">{provider === 'gemini' ? <Sparkles size={14} /> : provider === 'unavailable' ? <TriangleAlert size={14} /> : <FlaskConical size={14} />}<span><strong>{provider === 'gemini' ? 'Real Gemini analysis' : provider === 'unavailable' ? 'Gemini unavailable' : 'Deterministic demo analysis'}</strong>{notice}</span></div>;
}
