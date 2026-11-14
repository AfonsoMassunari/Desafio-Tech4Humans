"""Agente especializado no sistema legado de férias."""
from __future__ import annotations

import asyncio
from typing import Any

from agents.agent_context import AgentContext
from legacy import post_vacation
from models import Decision
from utils import event_text, extract_dates


class VacationAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        start = str(decision.campos.get("data_de_inicio") or "")
        end = str(decision.campos.get("data_de_fim") or "")
        if not start or not end:
            dates = extract_dates(event_text(event))
            start = start or (dates[0] if dates else "")
            end = end or (dates[1] if len(dates) > 1 else "")

        if not start or not end:
            await self.context.respond(
                event,
                "Para lançar suas férias preciso da data de início e da data de fim, no formato DD/MM/AAAA.",
                wait=True,
            )
            return

        try:
            protocol = await asyncio.to_thread(post_vacation, event, start, end)
        except Exception as error:
            await self.context.respond(
                event,
                f"Não consegui lançar suas férias automaticamente: {error}. "
                "Confira as datas e me avise para tentarmos de novo.",
                wait=True,
            )
            return

        await self.context.respond(event, f"Férias lançadas com sucesso no sistema. Protocolo: {protocol}.")
