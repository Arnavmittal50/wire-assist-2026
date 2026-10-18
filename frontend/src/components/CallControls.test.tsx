import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CallControls } from './CallControls';
import type { useTwilioCall } from '../hooks/useTwilioCall';

it('offers calling independently of review setup and disables competing microphone use', () => {
  const call: ReturnType<typeof useTwilioCall> = { phase: 'idle', active: false, message: 'Ready', warning: '', muted: false,
    seconds: 0, start: vi.fn(async () => {}), end: vi.fn(), toggleMute: vi.fn() };
  const view = render(<CallControls call={call} microphoneBusy={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Call Member' }));
  expect(call.start).toHaveBeenCalledOnce();
  view.rerender(<CallControls call={call} microphoneBusy />);
  expect(screen.getByRole('button', { name: 'Call Member' })).toBeDisabled();
  view.rerender(<CallControls call={{ ...call, active: true, phase: 'connected', seconds: 65 }} microphoneBusy={false} />);
  expect(screen.getByLabelText('Call duration')).toHaveTextContent('01:05');
  fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
  expect(call.toggleMute).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'End Call' }));
  expect(call.end).toHaveBeenCalledOnce();
});
