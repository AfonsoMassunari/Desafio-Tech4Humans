"""Agente revisor das respostas antes do envio ao solicitante."""
from __future__ import annotations

import json
from typing import Any

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, log

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment, misc]


class ReviewerAgent:
    """Revisa clareza e segurança sem inventar dados nem mudar decisões."""

    def __init__(self) -> None:
        self.client = (
            AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
            if LLM_API_KEY and AsyncOpenAI is not None
            else None
        )

    async def review(
        self,
        event: dict[str, Any],
        message: str,
        *,
        guidance: str = "",
    ) -> str:
        if self.client is None:
            return message
        prompt = (
            "Revise a resposta de um agente de triagem N1. Retorne SOMENTE JSON "
            'no formato {"resposta":"..."}. Preserve protocolos, ids, datas, '
            "valores e instruções. Não invente informações, não remova pedidos "
            "de dados obrigatórios e não inclua dados pessoais desnecessários. "
            "Se a resposta já estiver adequada, devolva-a sem mudanças.\n\n"
            f"Assunto: {event.get('assunto', '')}\n"
            f"Resposta: {message[:2000]}\n"
            f"Orientação humana: {guidance[:1000] or 'Nenhuma orientação adicional.'}"
        )
        try:
            result = await self.client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Você é o revisor final. Seja conservador e objetivo.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = result.choices[0].message.content
            reviewed = json.loads(content or "{}").get("resposta")
            if isinstance(reviewed, str) and reviewed.strip():
                return reviewed[:2000]
        except Exception as error:
            log.warning("Revisão da resposta falhou; mantendo texto original: %s", error)
        return message
