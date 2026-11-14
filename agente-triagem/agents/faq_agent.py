"""Agente especializado em deflexão por FAQ."""
from __future__ import annotations

from typing import Any

from agents.agent_context import AgentContext
from models import Decision
from utils import event_text


class FaqAgent:
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def handle(self, event: dict[str, Any], decision: Decision) -> None:
        record = None
        if decision.faq_id:
            record = next((faq for faq in self.context.faq if faq["id"] == decision.faq_id), None)
        if record is None:
            result = await self._consult_faq(event)
            records = result.get("registros", [])
            record = records[0] if records else None

        if record is None:
            if self.context.ticket_agent is None:
                raise RuntimeError("Agente de chamados não configurado para fallback da FAQ.")
            await self.context.ticket_agent.handle(
                event,
                Decision(
                    trilha="ticket",
                    categoria="Outros pedidos",
                    campos={"descricao": event_text(event)[:1000]},
                ),
            )
            return

        await self.context.respond(event, record["resposta"])

    async def _consult_faq(self, event: dict[str, Any]) -> dict[str, Any]:
        from mcp_client import call_tool

        return await call_tool(
            self.context.session,
            "consultar",
            read_only=True,
            base="faq",
            busca=event.get("assunto", ""),
            limite=5,
        )
