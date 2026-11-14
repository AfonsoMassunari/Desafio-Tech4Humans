"""Serviços compartilhados pelos agentes especializados."""
from __future__ import annotations

from typing import Any

from mcp import ClientSession

from config import AGENT_NAME, DOMINIO_EMPRESA, log
from agents.reviewer_agent import ReviewerAgent
from dashboard import STORE
from mcp_client import action_context, call_tool


class AgentContext:
    """Estado compartilhado e operações MCP comuns aos agentes."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.catalog: dict[str, dict[str, Any]] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.faq: list[dict[str, Any]] = []
        self.open_incidents: dict[str, str] = {}
        self.ticket_agent: Any = None
        self.reviewer = ReviewerAgent()

    async def respond(self, event: dict[str, Any], message: str, *, wait: bool = False) -> None:
        reviewed_message = await self.reviewer.review(event, message)
        with action_context("ReviewerAgent", "Revisar a resposta antes do envio ao solicitante."):
            STORE.record_action(
                event["id"],
                "review_response",
                {"original": message[:2000]},
                {"reviewed": reviewed_message[:2000], "agent": "ReviewerAgent"},
                actor="ReviewerAgent",
                reason="Revisar clareza, segurança e preservação dos dados do caso.",
            )
        await call_tool(
            self.session,
            "responder_evento",
            evento_id=event["id"],
            mensagem=reviewed_message[:2000],
            aguardar_resposta=wait,
        )

    async def notify_team(
        self,
        event: dict[str, Any],
        team: str,
        subject: str,
        message: str,
    ) -> None:
        email = self.teams.get(team, {}).get("email")
        if not email:
            log.warning("Time '%s' não encontrado em times.json; não foi possível notificar.", team)
            return
        await call_tool(
            self.session,
            "enviar_mensagem",
            para=email,
            assunto=subject[:300],
            mensagem=message[:2000],
            evento_id=event["id"],
        )

    def team_email(self, team: str) -> str | None:
        return self.teams.get(team, {}).get("email")
