"""Create/reuse Wire Assist through Twilio REST and save its SID to backend/.env.

Run from the repository root: .venv/Scripts/python.exe backend/scripts/setup_twilio_app.py
No calls are placed. A provider denial stops setup without changing accounts or retrying.
"""
import re
import sys
from pathlib import Path

import httpx
from dotenv import set_key
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import ROOT, Settings
from app.services.twilio_calling import public_origin


class SetupError(Exception):
    pass


def response_json(response):
    try:
        body = response.json()
    except ValueError:
        raise SetupError(f"Twilio HTTP {response.status_code}: non-JSON response; setup stopped.") from None
    if not isinstance(body, dict):
        raise SetupError(f"Twilio HTTP {response.status_code}: unexpected response; setup stopped.")
    if not response.is_success:
        # Only provider error fields, never request headers or credentials.
        raise SetupError(
            f"Twilio HTTP {response.status_code}; code {body.get('code', 'not supplied')}: "
            f"{body.get('message', 'No error message supplied')}"
        )
    return body


def setup(settings, env_path, client):
    required = ('twilio_account_sid', 'twilio_auth_token', 'public_backend_url')
    missing = [name.upper() for name in required if not getattr(settings, name).strip()]
    if missing:
        raise SetupError('Missing backend configuration: ' + ', '.join(missing) + '. No requests sent.')
    account = settings.twilio_account_sid.strip()
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}', account):
        raise SetupError('TWILIO_ACCOUNT_SID must be an AC SID. No requests sent.')
    try:
        voice_url = public_origin(settings) + '/twilio/voice'
    except (HTTPException, ValueError):
        raise SetupError('PUBLIC_BACKEND_URL must be a full HTTPS origin without a path. No requests sent.') from None
    if not env_path.is_file():
        raise SetupError('backend/.env must exist before setup. No requests sent.')

    endpoint = f'https://api.twilio.com/2010-04-01/Accounts/{account}/Applications.json'
    auth = httpx.BasicAuth(account, settings.twilio_auth_token.strip())
    # Read access is a preflight, not proof of permission to create. POST is authoritative.
    listing = response_json(client.get(endpoint, auth=auth, params={'PageSize': 1000}))
    apps = listing.get('applications')
    if not isinstance(apps, list) or listing.get('next_page_uri'):
        raise SetupError('Could not inspect the complete application list; setup stopped to avoid duplicates.')
    print('Applications API read access confirmed. Creation permission requires a successful POST.')
    matches = [app for app in apps if isinstance(app, dict)
               and app.get('friendly_name') == 'Wire Assist'
               and app.get('voice_url') == voice_url and app.get('voice_method') == 'POST']
    if matches:
        app = matches[0]
        result = 'Reused existing matching application'
    else:
        app = response_json(client.post(endpoint, auth=auth, data={
            'FriendlyName': 'Wire Assist', 'VoiceUrl': voice_url, 'VoiceMethod': 'POST',
        }))
        result = 'Created application; account creation permission confirmed'
    sid = app.get('sid', '')
    if not isinstance(sid, str) or not re.fullmatch(r'AP[0-9a-fA-F]{32}', sid):
        raise SetupError('Twilio returned no valid Application SID; .env was not changed.')
    print(f'{result}: {sid}')
    if (app.get('account_sid') != account or app.get('friendly_name') != 'Wire Assist'
            or app.get('voice_url') != voice_url or app.get('voice_method') != 'POST'):
        raise SetupError('Returned application settings did not match; .env was not changed.')
    try:
        set_key(str(env_path), 'TWILIO_TWIML_APP_SID', sid, quote_mode='never')
    except OSError:
        raise SetupError(f'Application exists, but saving .env failed. Set TWILIO_TWIML_APP_SID={sid} manually.') from None
    print('Saved TWILIO_TWIML_APP_SID in backend/.env. Restart the backend before testing.')
    return sid


def main():
    try:
        settings = Settings()
    except Exception:
        # Settings validation can include raw environment values in its exception.
        print('Could not load backend configuration. Check backend/.env; no requests sent.')
        return 1
    try:
        # No retries or redirects, especially after a restriction or an ambiguous POST timeout.
        with httpx.Client(timeout=25, follow_redirects=False) as client:
            setup(settings, ROOT / 'backend' / '.env', client)
    except SetupError as error:
        message = str(error)
        for secret in (settings.twilio_auth_token, settings.twilio_api_key_secret):
            if secret.strip():
                message = message.replace(secret.strip(), '[redacted]')
        print(message)
        return 1
    except httpx.RequestError as error:
        print(f'Network failure ({type(error).__name__}); no automatic retry. '
              'If the creation request was sent, its outcome is unknown; inspect applications before retrying.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
