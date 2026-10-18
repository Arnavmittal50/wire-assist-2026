# Twilio calling — Phase 1 setup and acceptance test

Phase 1 adds browser-to-phone audio only. The existing Gemini code remains separate. Calling does not create a review session, record audio, stream audio to FastAPI, transcribe, separate speakers, or call an analysis model. You can test the phone call without clicking Start review. No Groq integration was added.

## 1. Backend configuration

The official libraries are installed and declared in the dependency files: Python `twilio` and JavaScript `@twilio/voice-sdk` (v2). If setting up a fresh checkout, run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
cd frontend
npm.cmd ci
cd ..
```

Add these entries to `backend/.env`; preserve the existing Gemini settings. `.env` is ignored by Git. Never place these credentials in a frontend `VITE_*` variable or share their values in screenshots/logs.

```dotenv
TWILIO_ACCOUNT_SID=
TWILIO_API_KEY_SID=
TWILIO_API_KEY_SECRET=
TWILIO_AUTH_TOKEN=
TWILIO_TWIML_APP_SID=
TWILIO_PHONE_NUMBER=
TEST_MEMBER_PHONE_NUMBER=
PUBLIC_BACKEND_URL=
```

- **Account SID / Auth Token:** use your live Twilio account's Console account information. The Account SID begins with `AC`. The Auth Token validates incoming webhook signatures; it is distinct from the API key secret.
- **API Key SID / Secret:** in Twilio Console's API keys and tokens area, create a Standard API key for the same account in US1 for this setup. Copy the `SK...` SID and the secret shown at creation. Do not use Twilio test credentials for an actual ringing phone.
- **Twilio phone number:** use your voice-capable Twilio number as the caller ID.
- **Test member phone:** enter the single phone you want to ring. Both numbers must use E.164 (for example, `+12065550123` is formatting guidance, not a configured destination). For a trial account, verify the destination in Twilio Console and check that voice geographic permissions allow its country.
- Leave the TwiML App SID empty until you create the application in step 3. A missing value makes the token endpoint report setup incomplete; it does not break the rest of Wire Assist.

The backend issues a ten-minute [Voice Access Token](https://www.twilio.com/docs/iam/access-tokens) with an outgoing grant and no incoming-call grant. The existing development bearer authentication protects token issuance and call-status reads. Use a private `DEV_AUTH_TOKEN` of at least 16 characters before exposing the backend through a tunnel, and enter the matching value in the dashboard's Connection settings. The checked-in development token is not a private credential.

## 2. Run the backend and expose it over HTTPS

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Use one backend worker. Avoid auto-reload during a phone test; a restart loses the temporary status cache. If an existing backend already occupies port 8000, stop that process with Ctrl+C in its terminal before starting this one. Verify `http://127.0.0.1:8000/health` returns `status: ok`.

Choose one tunnel, in a separate terminal:

**ngrok:** install/authenticate ngrok using its [official setup guide](https://ngrok.com/docs/start), then run:

```powershell
ngrok http http://127.0.0.1:8000
```

Copy the public **HTTPS forwarding URL** displayed by ngrok, not the local inspector URL. Set, for example:

```dotenv
PUBLIC_BACKEND_URL=https://your-assigned-domain.ngrok-free.app
```

**Cloudflare alternative:** install `cloudflared`, then start a [Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/):

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Use the printed `https://...trycloudflare.com` origin as `PUBLIC_BACKEND_URL`. Keep the tunnel running. Do not add an interactive login page in front of the webhook paths; Twilio must be able to POST to them.

`PUBLIC_BACKEND_URL` must include `https://` and the hostname, with no path, query, credentials, or fragment. Restart FastAPI after editing `.env`. When a temporary tunnel URL changes, update both `.env` and the Console Voice Request URL and restart FastAPI.

## 3. Create the TwiML Application

The setup script uses Twilio's [official Applications REST API](https://www.twilio.com/docs/usage/api/applications). Configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `PUBLIC_BACKEND_URL` in `backend/.env`, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe backend/scripts/setup_twilio_app.py
```

It checks Applications API read access, then creates **Wire Assist** with `PUBLIC_BACKEND_URL + "/twilio/voice"` and method **POST**. Read access alone does not establish creation permission; a successful creation response does. An existing application with exactly that name, URL, and method is reused. The script prints the Application SID and saves `TWILIO_TWIML_APP_SID` directly in `backend/.env`, preserving other settings. Restart FastAPI afterward. It does not place a call.

A Twilio rejection stops setup and prints its HTTP status, error code, and message without changing the app SID. There is no restriction bypass, account switching, or automatic retry. After a network timeout, check the account's applications before retrying because a creation request may have succeeded. A Console upgrade prompt alone does not establish whether REST creation is permitted for your account.

### Manual Console alternative

1. Sign in to the **same account** in Twilio Console.
2. Open **Builder tools → TwiML host & config → TwiML apps**. In the legacy Console use **Voice → Manage → TwiML apps**. Console navigation may vary; search for “TwiML apps” if necessary. [Twilio's application setup instructions](https://www.twilio.com/docs/voice/tutorials/how-to-make-outbound-phone-calls) describe both navigation paths.
3. Create an app named, for example, **Wire Assist Phase 1**.
4. Under **Voice configuration**, set **Request URL** to your full public origin followed by `/twilio/voice`, for example `https://your-assigned-domain.ngrok-free.app/twilio/voice`. Select **HTTP POST**. There is no literal `PUBLIC_BACKEND_URL` in the Console URL and no second `https://` prefix.
5. Save the application. Open its details and copy its **Application SID**, beginning with `AP`.
6. Set `TWILIO_TWIML_APP_SID` to that actual SID in `backend/.env`, then restart FastAPI. If you used the script above, this value is already saved.

The outgoing grant selects this application; you do not need to assign it as the incoming-call handler of your Twilio number. The returned TwiML automatically supplies `/twilio/status` and `/twilio/dial-complete` callback URLs. No transcription, recording, or Media Streams URL should be added.

**Stop here until the real TwiML application, credentials, test number, and tunnel are configured.** The implementation's automated checks do not prove that your phone rings. After configuration, complete the test below and approve Phase 1 before any next-phase work.

## 4. Test browser-to-phone calling

In another terminal:

```powershell
cd frontend
npm.cmd run dev
```

Open `http://127.0.0.1:5173` in Chrome or Edge. Use a headset to reduce feedback. The Call Member toolbar is in the existing Conversation card and works before starting a review. It always calls the backend's configured test phone, regardless of the synthetic member selected in the dashboard. Do not start the separate transcription microphone during this test; the interface prevents simultaneous capture.

1. Click **Call Member** and allow microphone access. It should progress from **Idle** to **Connecting** and **Ringing**. No connected state is simulated.
2. Confirm that only the configured phone rings and the caller ID is your configured Twilio number. Trial accounts may play a Twilio trial announcement.
3. Answer. The dashboard shows **Connected** after Twilio confirms the phone leg answered, and the timer starts. A voicemail answer also counts as an answered phone leg; this phase does not detect humans versus machines.
4. Speak in both directions. Confirm the headset hears the phone and the phone hears the teller.
5. Click **Mute**: the phone should stop hearing the teller while the teller still hears the phone. Click **Unmute** to restore teller audio.
6. Click **End Call**: verify the phone disconnects, the timer stops, and the browser releases the microphone.
7. Call again and hang up on the phone: verify the dashboard ends the call. Also test unanswered and busy/declined calls; the provider's final status should appear when available.
8. Deny microphone permission on a fresh attempt and confirm an actionable error, no timer, and no simulated success. Cancel a call while it is connecting and confirm it does not connect later.
9. Confirm that no transcript or AI finding was produced by the call. Report two-way audio, mute/unmute, both hang-up directions, and statuses before approving the next phase.

## Security and implementation details

- `GET /api/twilio/token` uses the app's existing bearer authentication. The frontend receives a short-lived JWT, identity, and expiry only. Token refresh keeps the same development teller identity.
- `POST /twilio/voice` validates `X-Twilio-Signature` with the official Python `RequestValidator`, then uses `VoiceResponse` / `Dial` / `Number`. It ignores caller-supplied destination fields. Caller ID and destination come only from backend settings.
- All three Twilio POST endpoints validate signatures and the account SID. Browser bearer authentication is not a substitute for a Twilio signature.
- Signature input is the configured public HTTPS origin plus the actual request path and original query string, with all form values preserved. This works when ngrok/Cloudflare terminates HTTPS and forwards HTTP locally. Untrusted `Host` / `X-Forwarded-*` headers cannot change the signing URL. This setup requires root-mounted webhook paths; custom proxy path rewriting is not supported. [Twilio signature-validation reference](https://www.twilio.com/docs/usage/security).
- The [SDK's `accept` event](https://www.twilio.com/docs/voice/sdks/javascript/twiliocall) means browser media setup. The dashboard uses [signed Number status callbacks](https://www.twilio.com/docs/voice/twiml/number) to confirm the phone answered. Duplicate/out-of-order callbacks cannot regress state. Metadata is bounded and expires after two hours; it is not a durable call history.
- `CallControls.tsx` / `useTwilioCall.ts` own the phone lifecycle. `twilio_calling.py` owns tokens, TwiML, and status. No call audio is handled by Python or attached to Gemini. The SDK uses the actual browser microphone and remote playback; a short permission check releases its stream before SDK acquisition.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Setup incomplete | Fill the named backend variables, including the actual AP SID, and restart FastAPI. |
| Browser token request returns 401 | Match the dashboard's development token to `DEV_AUTH_TOKEN`. |
| Webhook returns 403 | Confirm `TWILIO_AUTH_TOKEN`, account SID, the exact public HTTPS origin and Console POST URL. Do not disable signature validation. Unsigned curl/browser requests should fail. |
| Twilio application error / 11200 | Inspect Twilio Console call logs and webhook request inspector. Ensure tunnel and FastAPI are running and the URL is reachable by Twilio. |
| Phone never rings | Verify the destination on trial accounts, voice-enabled caller number, account balance, allowed destination country, and same-account API key/App SID. |
| Connected to browser but waiting for phone status | Check `/twilio/status` requests in Twilio's inspector. Correct public URL and signatures; keep one backend worker. |
| Permission denied or no audio | Allow this site's microphone, check Windows microphone privacy settings, select the correct system headset, and unmute. Check browser output volume and Twilio's documented network/firewall requirements. |
| Ringing reaches voicemail | Voicemail is a real answered call; no answering-machine detection is enabled. |
| Status disappears after restart | The Phase 1 status cache is in memory. End the call and retry after the backend is stable. |

Run offline verification with `.\.venv\Scripts\python.exe -m pytest backend/tests/test_twilio_calling.py`, `npm.cmd test` and `npm.cmd run build` in `frontend`. The Playwright `twilio-phase1.spec.ts` intercepts token issuance so it cannot accidentally dial a phone. Real phone ringing and two-way audio remain manual acceptance steps.
