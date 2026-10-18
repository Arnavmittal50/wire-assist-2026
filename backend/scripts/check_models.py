"""One tiny structured-output call per analysis model; never prints credentials."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google import genai
from google.genai import types
from app.schemas.contracts import AnalysisResult, InitialAnalysisResult
from app.services.analysis import gemini_response_schema
from app.core.config import Settings


async def main():
    settings = Settings()
    if not settings.gemini_api_key.strip():
        print('No backend GEMINI_API_KEY configured; no requests sent.')
        return 1
    client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(
        timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)))
    failed = False
    try:
        for model, schema in ((settings.gemini_analysis_model, InitialAnalysisResult),
                              (settings.gemini_update_model, AnalysisResult)):
            try:
                response = await client.aio.models.generate_content(
                    model=model, contents='Return an empty insights list and an empty summary list if the schema provides summary.',
                    config=types.GenerateContentConfig(response_mime_type='application/json',
                        response_schema=gemini_response_schema(schema), max_output_tokens=512,
                        thinking_config=types.ThinkingConfig(thinking_level='low')))
                schema.model_validate_json(response.text or '')
                print(f'{model}: application analysis schema verified')
            except Exception as error:
                failed = True
                code = getattr(error, 'code', None)
                # Raw provider errors can contain request details. Print only class and numeric code.
                print(f'{model}: FAILED ({type(error).__name__}; HTTP {code if isinstance(code, int) else "unavailable"})')
    finally:
        await client.aio.aclose()
        client.close()
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
