"""Single LLM entry point for the leads lane (LiteLLM first, Gemini fallback)."""
from __future__ import annotations

from app.config import get_settings


def complete_json(system: str, user: str) -> str:
    from app.services.llm import chat_completion_json, llm_configured

    if llm_configured():
        return chat_completion_json(system=system, user=user, temperature=0.1)
    s = get_settings()
    if s.gemini_api_key.strip():
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=s.gemini_api_key)
        resp = client.models.generate_content(
            model=s.gemini_model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return resp.text or ""
    raise RuntimeError("No LLM configured: set LITELLM_API_KEY or GEMINI_API_KEY in .env")
