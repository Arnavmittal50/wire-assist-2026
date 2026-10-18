import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, AudioLines, ChevronDown, CircleHelp, FlaskConical, History as HistoryIcon, LayoutDashboard, LockKeyhole, Mic, Radio, Settings2, ShieldCheck, Sparkles, UserRound, X } from 'lucide-react';
import { ApiClient, DEFAULT_AS_OF, DEFAULT_TOKEN } from './services/api';
import type { ActionInput, InitialAnalysis, FinancialEvent, Health, Member, Payment, Session, SessionSummary, Speaker, TranscriptInput } from './types/api';
import { useSessionSocket } from './hooks/useSessionSocket';
import { useMicrophone } from './hooks/useMicrophone';
import { CallControls } from './components/CallControls';
import { useTwilioCall } from './hooks/useTwilioCall';
import { ConversationControls } from './components/ConversationControls';
import { Badge } from './components/ui/badge';
import { Button } from './components/ui/button';
import { Card } from './components/ui/card';
import { History } from './components/History';
import { InsightCard } from './components/InsightCard';
import { Transcript } from './components/Transcript';
import { AttentionPanel } from './components/AttentionPanel';
import { ProviderNotice } from './components/ProviderNotice';
import { InitialReview } from './components/InitialReview';
import { ActionDialog } from './components/ActionDialog';
import { EmployeeActions } from './components/EmployeeActions';
import { SessionSummaryCard } from './components/SessionSummaryCard';
import { SessionHistoryDialog } from './components/SessionHistoryDialog';
import { money } from './lib/utils';
import { reconcileSession } from './lib/sessionState';
import { latestOpenPayment } from './lib/payments';

export default function App() {
  const [token, setToken] = useState(DEFAULT_TOKEN);
  const [settings, setSettings] = useState(false);
  const api = useMemo(() => new ApiClient(token), [token]);
  const call = useTwilioCall(api);
  const [processing, setProcessing] = useState(false);
  const [preAnalysis, setPreAnalysis] = useState<InitialAnalysis | null>(null);
  const [prePending, setPrePending] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [memberId, setMemberId] = useState('M1002');
  const [events, setEvents] = useState<FinancialEvent[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [retention, setRetention] = useState(false);
  const [mode, setMode] = useState<'demo' | 'live'>('demo');
  const modeChosen = useRef(false);
  const [payment, setPayment] = useState<Payment>({ amount: 1500, rail: 'Wire', recipient_id: 'R1002-09', purpose: '' });
  const [asOf, setAsOf] = useState(DEFAULT_AS_OF);
  const [speaker, setSpeaker] = useState<Speaker>('auto');
  const [pendingAction, setPendingAction] = useState<Omit<ActionInput, 'note'> | null>(null);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [showSessions, setShowSessions] = useState(false);
  const [savedSessions, setSavedSessions] = useState<Session[]>([]);
  const activeSessionId = useRef<string | null>(null);
  const sessionGeneration = useRef(0);
  const processedCallSession = useRef<string | null>(null);
  const pendingTranscript = useRef<(TranscriptInput & { session_id: string }) | null>(null);
  const selectedMember = members.find(member => member.member_id === memberId);
  const applySession = useCallback((next: Session) => {
    setSession(previous => reconcileSession(previous, next, activeSessionId.current));
  }, []);
  const activateSession = useCallback((next: Session) => {
    activeSessionId.current = next.session_id; setSession(next); setSummary(previous => previous?.session_id === next.session_id ? previous : null);
    setMemberId(next.member_id); setAsOf(next.as_of); setPayment(next.payment); setMode(next.mode);
    setConsent(next.consent); setRetention(next.retain_transcript); setSpeaker('auto');
    localStorage.setItem('wire-assist-session', next.session_id);
  }, []);
  const { connection, partial, send, sendAudio } = useSessionSocket(api, session?.session_id, applySession, setError);
  const microphone = useMicrophone({ enabled: !call.active && session?.status === 'active' && session.consent, live: session?.mode === 'live', connection, speaker, serverAudioStatus: session?.audio_status ?? 'stopped', sendControl: send, sendAudio, onError: setError });
  useEffect(() => { if (error) microphone.stop(); }, [error, microphone.stop]);

  useEffect(() => {
    const remembered = localStorage.getItem('wire-assist-session');
    if (!remembered || activeSessionId.current) return;
    let cancelled = false;
    const generation = sessionGeneration.current;
    void api.session(remembered).then(next => {
      if (!cancelled && generation === sessionGeneration.current) activateSession(next);
    }).catch(() => { if (!cancelled) localStorage.removeItem('wire-assist-session'); });
    return () => { cancelled = true; };
  }, [api, activateSession]);

  useEffect(() => {
    if (!session || session.status !== 'ended') return;
    const id = session.session_id;
    let cancelled = false;
    void api.summary(id).then(next => { if (!cancelled && activeSessionId.current === id) setSummary(next); }).catch((failure: Error) => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
  }, [api, session?.session_id, session?.status]);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([api.health(), api.members()]).then(([nextHealth, nextMembers]) => {
      if (cancelled) return;
      setHealth(nextHealth); if (!activeSessionId.current && !modeChosen.current) setMode(['groq', 'gemini'].includes(nextHealth.analysis_provider) ? 'live' : 'demo'); setMembers(nextMembers); setError('');
      if (nextMembers.length && !nextMembers.some(member => member.member_id === memberId)) setMemberId(nextMembers[0].member_id);
    }).catch((failure: Error) => { if (!cancelled) setError(failure.message); });
    return () => { cancelled = true; };
  }, [api]);

  useEffect(() => {
    let cancelled = false;
    setEvents([]); setLoadingHistory(true);
    void api.events(memberId, asOf).then(next => { if (!cancelled) setEvents(next); }).catch((failure: Error) => { if (!cancelled) setError(failure.message); }).finally(() => { if (!cancelled) setLoadingHistory(false); });
    return () => { cancelled = true; };
  }, [api, memberId, asOf]);

  useEffect(() => {
    if (session || !selectedMember || !api.preAnalysis) return;
    let cancelled = false;
    setPreAnalysis(null); setPrePending(true);
    const timer = setTimeout(() => {
      void api.preAnalysis({ member_id: memberId, as_of: asOf, consent: false, retain_transcript: false, mode: 'live', payment })
        .then(result => { if (!cancelled) setPreAnalysis(result); })
        .catch(() => { if (!cancelled) setPreAnalysis({ provider: 'unavailable', model: null, event_count: events.length, insights: [], summary: [], notice: 'Pre-call analysis is unavailable. You can still call the member.' }); })
        .finally(() => { if (!cancelled) setPrePending(false); });
    }, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, memberId, asOf, selectedMember?.member_id, payment.amount, payment.rail, payment.recipient_id, session?.session_id]);

  async function startPhoneCall() {
    if (!processing) { await call.start(); return; }
    setBusy(true); setError('');
    try {
      let review = session;
      if (!review || review.status === 'ended') {
        review = await api.createSession({ member_id: memberId, as_of: asOf, consent: true, retain_transcript: retention, mode: 'live', payment });
        activateSession(review);
      }
      if (review.mode !== 'live') throw new Error('End the demo review and start a new live call to enable AI processing.');
      const prepared = await api.prepareCall(review.session_id);
      processedCallSession.current = review.session_id;
      await call.start(prepared.ticket);
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    const id = processedCallSession.current;
    if (!id || !['ended', 'error'].includes(call.phase)) return;
    processedCallSession.current = null;
    void api.end(id).then(applySession).catch((failure: Error) => setError(failure.message));
  }, [api, call.phase, applySession]);

  async function retryGuidance() {
    if (!session || session.status !== 'active') return;
    setBusy(true);
    try { applySession(await api.retryGuidance(session.session_id)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function startReview() {
    setBusy(true); setError('');
    const generation = ++sessionGeneration.current;
    try { const next = await api.createSession({ member_id: memberId, as_of: asOf, consent, retain_transcript: retention, mode, payment }); if (generation === sessionGeneration.current) activateSession(next); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function submitTranscript(text: string) {
    if (!session || session.status !== 'active') return false;
    setBusy(true); setError('');
    const saved = pendingTranscript.current;
    const input = saved?.session_id === session.session_id && saved.text === text && saved.speaker === speaker ? saved : { session_id: session.session_id, segment_id: crypto.randomUUID(), sequence: session.next_sequence ?? Math.max(-1, ...session.segments.map(segment => segment.sequence)) + 1, text, speaker };
    pendingTranscript.current = input;
    try {
      applySession(await api.transcript(session.session_id, { segment_id: input.segment_id, sequence: input.sequence, text, speaker }));
      pendingTranscript.current = null;
      return true;
    } catch (failure) {
      setError((failure as Error).message);
      if ((failure as Error).message.includes('sequence')) { pendingTranscript.current = null; void api.session(session.session_id).then(applySession).catch(() => undefined); }
      return false;
    }
    finally { setBusy(false); }
  }
  async function correctSpeaker(segmentId: string, role: 'member' | 'teller' | 'unknown') {
    if (!session || session.status !== 'active') return;
    setBusy(true); setError('');
    try { applySession(await api.correctSpeaker(session.session_id, segmentId, role, session.segments.find(s => s.segment_id === segmentId)?.speaker_id ? 'speaker' : 'segment')); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function recordAction(input: ActionInput) {
    if (!session || session.status !== 'active') return false;
    setBusy(true); setError('');
    try { applySession(await api.action(session.session_id, input)); setPendingAction(null); return true; }
    catch (failure) { setError((failure as Error).message); return false; }
    finally { setBusy(false); }
  }
  async function endReview() {
    if (!session) return;
    call.end(); microphone.stop(); setBusy(true); setError('');
    try { applySession(await api.end(session.session_id)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  function newReview() {
    microphone.stop(); sessionGeneration.current++; activeSessionId.current = null;
    setSession(null); setSummary(null); setConsent(false); setRetention(false); setPendingAction(null); setError(''); pendingTranscript.current = null;
    localStorage.removeItem('wire-assist-session');
  }
  async function deleteReview() {
    if (!session) return;
    setBusy(true); setError('');
    const id = session.session_id;
    try { await api.deleteSession(id); if (activeSessionId.current === id) newReview(); setSavedSessions(previous => previous.filter(item => item.session_id !== id)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function browseSessions() {
    setShowSessions(true); setBusy(true); setError('');
    try { setSavedSessions(await api.sessions()); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  async function loadReview(id: string) {
    if (session?.status === 'active' && session.session_id !== id) return;
    microphone.stop(); setBusy(true); setError('');
    const generation = ++sessionGeneration.current;
    try { const next = await api.session(id); if (generation === sessionGeneration.current) { activateSession(next); setShowSessions(false); } }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  function selectMember(id: string) {
    setMemberId(id); setPayment(previous => ({ ...previous, recipient_id: '', purpose: '' }));
  }
  function demoSetup(id: 'M1002' | 'M1057') {
    modeChosen.current = true; setMemberId(id); setConsent(false); setMode('demo');
    setAsOf(id === 'M1002' ? DEFAULT_AS_OF : '2026-10-19T16:41:00Z');
    setPayment({ amount: id === 'M1002' ? 1500 : 51586.52, rail: 'Wire', recipient_id: id === 'M1002' ? 'R1002-09' : 'R1057-10', purpose: '' });
  }
  const latestPending = latestOpenPayment(events);
  const primary = session?.insights.filter(insight => insight.status === 'active' || insight.status === 'acknowledged').sort((a, b) => ({ high: 3, medium: 2, low: 1 }[b.priority] - { high: 3, medium: 2, low: 1 }[a.priority]))[0];
  const attention = session?.attention;

  return <div className="app-shell">
    <aside className="rail"><a className="brand-mark" href="#workspace" aria-label="Wire Assist workspace"><AudioLines size={26} /></a><span className="rail-divider" /><a className="rail-item active" href="#workspace" aria-label="Workspace"><LayoutDashboard size={21} /></a><button className="rail-item" onClick={() => void browseSessions()} aria-label="Saved sessions"><HistoryIcon size={21} /></button><button className="rail-item" onClick={() => setSettings(!settings)} aria-label="Connection settings"><Settings2 size={21} /></button><div className="rail-bottom"><span className="rail-lab" title="Synthetic data lab"><FlaskConical size={20} /></span><span className="teller-avatar" title="Development teller">T</span></div></aside>
    <div className="main-shell" id="workspace"><header className="topbar"><a className="wordmark" href="#workspace">wire<span>assist</span><span className="version">2.0</span></a><div className="topbar-meta"><span className="synthetic-label"><LockKeyhole size={12} /> Synthetic data workspace</span><Badge tone={mode === 'demo' ? 'purple' : 'green'}>{mode === 'demo' ? 'DEMO MODE' : 'LIVE MODE'}</Badge><span className="user-chip"><span>T</span>Teller workspace</span></div></header>
      <main><div className="page-heading"><div><span className="eyebrow">MEMBER CARE, IN THE MOMENT</span><h1>Every conversation counts.</h1><p>A little context. The right question. A more confident next step.</p></div><div className="session-heading-actions"><div className="session-state"><span className={`status-dot ${session?.status === 'active' ? 'green' : ''}`} /><span>{session?.status === 'active' ? 'Review in progress' : session?.status === 'ended' ? 'Review completed' : 'Ready for a new review'}</span></div>{session?.status === 'active' && <Button variant="secondary" size="sm" onClick={() => void endReview()} disabled={busy}>{busy ? 'Processing…' : 'End review'}</Button>}<Button className="mobile-sessions" variant="ghost" size="sm" onClick={() => void browseSessions()}>Saved sessions</Button></div></div>
      {settings && <Card className="settings-card"><label>Development access token<input type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="off" disabled={!!session || call.active} /></label><p className="fine-print">Matches DEV_AUTH_TOKEN on your local backend. Gemini credentials stay on the backend.</p><Button variant="ghost" size="sm" onClick={() => setSettings(false)}>Close settings</Button></Card>}
      {error && <div className="error-banner" role="alert"><CircleHelp size={19} /><span>{error}</span><Button variant="ghost" size="icon" onClick={() => setError('')} aria-label="Dismiss error"><X size={16} /></Button></div>}
      {summary && <SessionSummaryCard summary={summary} busy={busy} onNew={newReview} onDelete={() => void deleteReview()} />}
      <Card className="member-strip"><div className="member-field"><span className="member-avatar"><UserRound size={22} /></span><div><label htmlFor="member-select" className="eyebrow">SYNTHETIC MEMBER</label><select id="member-select" value={memberId} onChange={event => selectMember(event.target.value)} disabled={!!session || call.active || busy}><option hidden value="M1002">M1002</option>{members.map(member => <option key={member.member_id} value={member.member_id}>{member.label}</option>)}</select></div></div><div className="strip-divider" /><div className="payment-heading"><span className="eyebrow">CURRENT REQUEST</span><strong>{money(session?.payment.amount ?? payment.amount)} <span>· {payment.rail}</span></strong></div><div className="strip-divider" /><div className="review-date"><span className="eyebrow">REVIEW AS OF</span><strong>{new Date(asOf).toLocaleString('en-US', { timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })} PT</strong></div><div className="attention-pill"><span className={`status-dot ${attention?.level === 'elevated' ? 'amber' : attention?.level === 'review' ? 'purple' : 'green'}`} />{attention?.level === 'elevated' ? 'Elevated attention' : attention?.level === 'review' ? 'Review suggested' : 'Routine attention'}</div></Card>
      {!session && <div className="demo-presets"><span>Demo review setups</span><Button variant="ghost" size="sm" disabled={busy || !members.some(member => member.member_id === 'M1002')} onClick={() => demoSetup('M1002')}>M1002 · Sep 26</Button><Button variant="ghost" size="sm" disabled={busy || !members.some(member => member.member_id === 'M1057')} onClick={() => demoSetup('M1057')}>M1057 · Oct 19</Button><Button variant="secondary" size="sm" disabled={busy || !latestPending} onClick={() => latestPending && setPayment({ amount: latestPending.amount!, rail: latestPending.payment_rail!, recipient_id: latestPending.recipient_id || '', purpose: '' })}>Use latest pending payment</Button></div>}
      {!session && <details className="disclosure request-details"><summary><span><Settings2 size={16} /> Review setup</span><ChevronDown size={16} /></summary><div className="setup-fields"><label>Payment amount<input type="number" min="1" max="10000000" value={payment.amount} onChange={event => setPayment({ ...payment, amount: Number(event.target.value) })} /></label><label>Payment rail<select value={payment.rail} onChange={event => setPayment({ ...payment, rail: event.target.value as Payment['rail'] })}>{['Wire', 'ACH', 'External Transfer', 'P2P', 'Cash'].map(rail => <option key={rail}>{rail}</option>)}</select></label><label>Recipient identifier<input maxLength={100} value={payment.recipient_id} onChange={event => setPayment({ ...payment, recipient_id: event.target.value })} /></label><label>As-of timestamp (UTC)<input value={asOf} onChange={event => setAsOf(event.target.value)} /></label><label className="wide">Member's stated purpose<input maxLength={500} placeholder="Optional — ask the member" value={payment.purpose} onChange={event => setPayment({ ...payment, purpose: event.target.value })} /></label></div></details>}
      <div className="workspace-grid"><div className="conversation-column"><Card className="conversation-card"><div className="card-heading"><span className="section-title"><AudioLines size={18} /> Conversation</span><span className={`connection-label ${connection === 'disconnected' ? 'connection-error' : ''}`}><span className={`status-dot ${connection === 'connected' ? 'green' : ''}`} />{connection === 'connected' ? 'Connected' : connection === 'disconnected' ? 'Disconnected · reconnecting' : connection === 'connecting' ? 'Connecting…' : 'Not started'}</span></div>
        <label className="check-label call-consent"><input type="checkbox" checked={processing} disabled={call.active || busy} onChange={event => setProcessing(event.target.checked)} /><span>Enable AI call assistance. The member and I consent to live transcription and AI analysis.</span></label>
        <CallControls call={call} onStart={() => void startPhoneCall()} preparing={busy} microphoneBusy={['recording', 'starting', 'connecting'].includes(microphone.status)} />
        {!session ? <div className="consent-panel"><div className="consent-icon"><Mic size={24} /></div><h2>Make space for a conversation.</h2><p>Choose a synthetic member, confirm consent, and start a private review.</p><label className="check-label"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>The member consents to this conversation being processed, including audio if enabled.</span></label><label className="check-label retention-label"><input type="checkbox" checked={retention} onChange={event => setRetention(event.target.checked)} /><span>Retain finalized transcript text for this demonstration.</span></label><div className="start-row"><label className="sr-only" htmlFor="mode-select">Analysis mode</label><select id="mode-select" value={mode} onChange={event => { modeChosen.current = true; setMode(event.target.value as 'demo' | 'live'); }}><option value="demo">Demo · local analysis</option><option value="live" disabled={!['gemini', 'groq'].includes(health?.analysis_provider ?? '')}>Live · Gemini analysis</option></select><Button onClick={startReview} disabled={!consent || busy || !selectedMember}>{busy ? 'Starting…' : 'Start review'}<ArrowRight size={16} /></Button></div><p className="fine-print">Demo mode needs no API key. Raw audio is never retained by default.</p></div> : <>{session.transcription_notice && <p className="speaker-notice" role="status">{session.transcription_notice}</p>}<Transcript segments={session.segments} partial={partial} speakerNotice={session.speaker_notice} diarizationPending={session.diarization_pending} diarizationNotice={session.diarization_notice} onCorrectSpeaker={session.status === 'active' ? correctSpeaker : undefined} disabled={busy} emptyMessage={session.status === 'ended' && !session.retain_transcript ? 'Conversation text was not retained.' : undefined} /><ConversationControls microphoneDisabled={call.active} status={microphone.status} live={session.mode === 'live'} speaker={speaker} setSpeaker={setSpeaker} onStart={() => { setError(''); void microphone.start(); }} onPause={microphone.pause} onStop={microphone.stop} onSubmit={submitTranscript} disabled={session.status !== 'active'} busy={busy} providerReady={health?.transcription_provider === 'gemini_live' && connection === 'connected'} /></>}
      </Card><History events={events} loading={loadingHistory} />{session && <EmployeeActions session={session} busy={busy} onAction={action_type => setPendingAction({ action_type })} />}</div>
      <aside className="guidance-column">{session ? <InitialReview session={session} /> : <InitialReview session={{ initial_analysis: preAnalysis, payment, as_of: asOf, analysis_pending: prePending }} />}{session && <ProviderNotice provider={session.analysis_provider} notice={session.provider_notice} pending={session.analysis_pending} />}<InsightCard onRetry={session ? () => void retryGuidance() : undefined} reviewed={!!session?.analyzed_sentences?.length} waitingForSpeaker={session?.guidance_status === 'waiting_for_speaker'} unavailable={session?.analysis_provider === 'unavailable'} insight={primary} pending={session?.analysis_pending ?? false} disabled={busy || session?.status !== 'active'} onAcknowledge={primary ? () => void recordAction({ action_type: 'ACKNOWLEDGE', insight_id: primary.insight_id, note: 'Guidance reviewed by the teller.' }) : undefined} onDismiss={primary ? () => setPendingAction({ action_type: 'DISMISS', insight_id: primary.insight_id }) : undefined} />{attention && <AttentionPanel attention={attention} disabled={busy || session?.status !== 'active'} onResolve={signal => setPendingAction({ action_type: 'RESOLVE_SIGNAL', signal_id: signal.signal_id })} onDismiss={signal => setPendingAction({ action_type: 'DISMISS_SIGNAL', signal_id: signal.signal_id })} />}{session && session.insights.length > 1 && <details className="disclosure"><summary>Previous guidance</summary>{session.insights.filter(item => item.insight_id !== primary?.insight_id).slice(-10).reverse().map(item => <div className="context-body" key={item.insight_id}><strong>{item.title}</strong><p>{item.observation}</p><p className="fine-print">{item.status}</p></div>)}</details>}<Card className="context-card"><div className="card-heading"><span className="section-title"><ShieldCheck size={17} /> A thoughtful review</span></div><div className="context-body"><p>Understand the story behind the payment.</p><div className="context-row"><span className="context-icon"><UserRound size={15} /></span><div><strong>Member-led decisions</strong><span>Questions support the member’s autonomy.</span></div></div><div className="context-row"><span className="context-icon"><Radio size={15} /></span><div><strong>Grounded in evidence</strong><span>History and finalized conversation only.</span></div></div><div className="context-row"><span className="context-icon"><Sparkles size={15} /></span><div><strong>Clear about uncertainty</strong><span>Attention is not a fraud probability.</span></div></div></div></Card></aside></div>
      <footer className="page-footer"><span><FlaskConical size={13} /> {health?.data_source?.includes('fixture') ? 'Seeded sample fixtures · source CSV unavailable' : 'HuskyHack synthetic CSV · proof of concept'}</span><span>Decision support only. Review actions are simulated; phone calls are real.</span></footer>
    </main></div>
    {pendingAction && <ActionDialog action={pendingAction} busy={busy} onClose={() => setPendingAction(null)} onConfirm={note => recordAction({ ...pendingAction, note })} />}
    {showSessions && <SessionHistoryDialog sessions={savedSessions} activeSessionId={session?.status === 'active' ? session.session_id : undefined} busy={busy} onLoad={id => void loadReview(id)} onClose={() => setShowSessions(false)} />}
  </div>;
}


