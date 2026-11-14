"""Orquestrador dos agentes especializados de triagem."""
from __future__ import annotations

from typing import Any

from mcp import ClientSession

from agents.agent_context import AgentContext
from classifier import Classifier
from config import log
from dashboard import STORE
from agents.faq_agent import FaqAgent
from agents.incident_agent import IncidentAgent
from agents.routing_agent import RoutingAgent
from agents.security_agent import SecurityAgent
from agents.ticket_agent import TicketAgent
from agents.vacation_agent import VacationAgent
from mcp_client import MCPToolError, action_context, call_tool
from memory import AgentMemory
from utils import normalize_key


class TriageAgent:
    """Orquestra classificação, despacho e memória mínima do atendimento."""

    def __init__(self, session: ClientSession) -> None:
        self.context = AgentContext(session)
        self.classifier: Classifier | None = None
        self.processed: set[str] = set()
        self.run_id = f"run-{id(self)}"
        self.memory = AgentMemory()
        self.faq_agent: FaqAgent | None = None
        self.security_agent: SecurityAgent | None = None
        self.vacation_agent: VacationAgent | None = None
        self.incident_agent: IncidentAgent | None = None
        self.routing_agent: RoutingAgent | None = None
        self.ticket_agent: TicketAgent | None = None

    async def load_reference_data(self) -> None:
        catalog = await call_tool(
            self.context.session, "consultar", read_only=True, base="catalogo", limite=100
        )
        teams = await call_tool(
            self.context.session, "consultar", read_only=True, base="times", limite=100
        )
        faq = await call_tool(
            self.context.session, "consultar", read_only=True, base="faq", limite=100
        )
        incidents = await call_tool(
            self.context.session, "consultar", read_only=True, base="incidentes", limite=100
        )

        self.context.catalog = {item["tipo"]: item for item in catalog.get("registros", [])}
        self.context.teams = {item["time"]: item for item in teams.get("registros", [])}
        self.context.faq = faq.get("registros", [])
        for item in incidents.get("registros", []):
            if item.get("status") == "aberto" and item.get("produto"):
                self.context.open_incidents[normalize_key(item["produto"])] = item["id"]

        self.classifier = Classifier(
            self.context.catalog,
            self.context.teams,
            self.context.faq,
        )
        self.ticket_agent = TicketAgent(self.context)
        self.context.ticket_agent = self.ticket_agent
        self.faq_agent = FaqAgent(self.context)
        self.security_agent = SecurityAgent(self.context)
        self.vacation_agent = VacationAgent(self.context)
        self.incident_agent = IncidentAgent(self.context)
        self.routing_agent = RoutingAgent(self.context)
        log.info(
            "Referência carregada: %d tipos de catálogo, %d times, %d FAQs, %d incidentes abertos.",
            len(self.context.catalog),
            len(self.context.teams),
            len(self.context.faq),
            len(self.context.open_incidents),
        )

    async def process(self, event: dict[str, Any]) -> None:
        event_id = event["id"]
        if event_id in self.processed:
            return
        self.processed.add(event_id)
        self.memory.save_event(self.run_id, event)

        if self.classifier is None:
            raise RuntimeError("Classificador não inicializado.")
        classification_event = await self._with_parent_context(event)
        decision = await self.classifier.classify(classification_event)
        if event.get("responde_a"):
            decision.motivo = (
                f"Continuação de {event['responde_a']}. "
                f"{decision.motivo}".strip()
            )
        handler_name = {
            "faq": "FaqAgent",
            "security": "SecurityAgent",
            "vacation": "VacationAgent",
            "incident": "IncidentAgent",
            "team_route": "RoutingAgent",
        }.get(decision.trilha, "TicketAgent")
        STORE.record_decision(event, decision, handler_name)
        self.memory.save_decision(self.run_id, event_id, decision)
        log.info(
            "[%s] %s -> %s %s",
            event_id,
            event.get("assunto", "(sem assunto)"),
            decision.trilha,
            decision.categoria or decision.time_destino,
        )

        handlers = {
            "faq": self.faq_agent,
            "security": self.security_agent,
            "vacation": self.vacation_agent,
            "incident": self.incident_agent,
            "team_route": self.routing_agent,
        }
        handler = handlers.get(decision.trilha)
        if handler is None:
            if self.ticket_agent is None:
                raise RuntimeError("Agente de chamados não inicializado.")
            handler = self.ticket_agent
        with action_context(
            handler_name,
            f"Executar a trilha '{decision.trilha}' após classificação do evento.",
        ):
            await handler.handle(event, decision)

    async def _with_parent_context(self, event: dict[str, Any]) -> dict[str, Any]:
        parent_id = str(event.get("responde_a") or "").strip()
        if not parent_id:
            return event
        try:
            result = await call_tool(
                self.context.session,
                "consultar",
                read_only=True,
                base="eventos",
                filtro={"id": parent_id},
                limite=1,
            )
        except MCPToolError as error:
            log.warning(
                "Não foi possível carregar o evento pai %s para %s: %s - %s",
                parent_id,
                event["id"],
                error.codigo,
                error.mensagem,
            )
            return event
        parent = next(iter(result.get("registros", [])), None)
        if not parent:
            return event
        merged = dict(event)
        merged["assunto"] = f"{parent.get('assunto', '')} | {event.get('assunto', '')}"
        merged["corpo"] = f"{parent.get('corpo', '')}\n\nResposta recebida:\n{event.get('corpo', '')}"
        return merged
