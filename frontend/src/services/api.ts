import type { ActionInput, CreateSession, FinancialEvent, Health, Member, Session, SessionSummary, TranscriptInput } from '../types/api';

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
export const DEFAULT_TOKEN = import.meta.env.VITE_DEV_AUTH_TOKEN || 'wire-assist-local-dev';
export const DEFAULT_AS_OF = '2026-09-27T02:25:00Z';

export class ApiClient {
  constructor(readonly token: string, readonly baseUrl = API_BASE_URL) {}
  async request<T>(path: string, method = 'GET', body?: unknown, timeout = 20000): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.token}` },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(timeout),
      });
    } catch { throw new Error('Cannot reach the backend. Check that FastAPI is running and try again.'); }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : `Request failed (${response.status}). Please check your input and try again.`);
    }
    return response.status === 204 ? undefined as T : response.json() as Promise<T>;
  }
  health = () => this.request<Health>('/health');
  twilioToken = () => this.request<{ token: string; identity: string; expires_in: number }>('/api/twilio/token');
  twilioCallStatus = (sid: string) => this.request<{ status: string; connected_at: string | null; ended_at: string | null }>(`/api/twilio/calls/${encodeURIComponent(sid)}`, 'GET', undefined, 5000);
  members = () => this.request<Member[]>('/members');
  events = (id: string, asOf: string) => this.request<FinancialEvent[]>(`/members/${encodeURIComponent(id)}/events?as_of=${encodeURIComponent(asOf)}`);
  createSession = (input: CreateSession) => this.request<Session>('/sessions', 'POST', input);
  session = (id: string) => this.request<Session>(`/sessions/${id}`);
  sessions = () => this.request<Session[]>('/sessions');
  summary = (id: string) => this.request<SessionSummary>(`/sessions/${id}/summary`);
  transcript = (id: string, input: TranscriptInput) => this.request<Session>(`/sessions/${id}/transcript`, 'POST', input);
  correctSpeaker = (id: string, segmentId: string, speaker: 'member' | 'teller' | 'unknown', scope: 'segment' | 'speaker' = 'segment') => this.request<Session>(`/sessions/${id}/transcript/${encodeURIComponent(segmentId)}/speaker`, 'POST', { speaker, scope });
  action = (id: string, input: ActionInput) => this.request<Session>(`/sessions/${id}/actions`, 'POST', input);
  retryGuidance = (id: string) => this.request<Session>(`/sessions/${id}/guidance/retry`, 'POST');
  end = (id: string) => this.request<Session>(`/sessions/${id}/end`, 'POST', undefined, 60000);
  deleteSession = (id: string) => this.request<void>(`/sessions/${id}`, 'DELETE');
  socketUrl = (id: string) => `${this.baseUrl.replace(/^http/, 'ws')}/ws/sessions/${id}`;
}
