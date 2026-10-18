import { FlaskConical, Sparkles, TriangleAlert } from 'lucide-react';
import type { Session } from '../types/api';

export function ProviderNotice({ provider, notice }: { provider: Session['analysis_provider']; notice: string }) {
  return <div className={`provider-notice provider-${provider}`} role="status">{provider === 'gemini' ? <Sparkles size={14} /> : provider === 'unavailable' ? <TriangleAlert size={14} /> : <FlaskConical size={14} />}<span><strong>{provider === 'gemini' ? 'Real Gemini analysis' : provider === 'unavailable' ? 'Gemini unavailable' : 'Deterministic demo analysis'}</strong>{notice}</span></div>;
}
