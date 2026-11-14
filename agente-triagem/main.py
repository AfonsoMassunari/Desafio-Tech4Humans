"""Entry point do agente de triagem."""
from __future__ import annotations
import asyncio
import sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from triage_agent import TriageAgent
from config import AGENT_DASHBOARD_PORT, EVENT_POLL_TIMEOUT_SECONDS, ESPERAR_SEGUNDOS_EVENTO, MCP_URL, LLM_MODEL, log
from dashboard import set_review_dispatcher, start_dashboard
from mcp_client import MCPToolError, action_context, call_tool

async def run() -> None:
    start_dashboard(port=AGENT_DASHBOARD_PORT)
    log.info("Painel dos agentes disponível em http://localhost:%s/agents", AGENT_DASHBOARD_PORT)
    log.info("Conectando em %s (modelo %s)...", MCP_URL, LLM_MODEL)
    async with streamable_http_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            loop = asyncio.get_running_loop()

            def dispatch_human_response(event_id: str, message: str) -> dict[str, object]:
                async def send() -> dict[str, object]:
                    with action_context(
                        "HumanReview",
                        "Enviar a resposta corrigida após aprovação humana.",
                    ):
                        return await call_tool(
                            session,
                            "responder_evento",
                            evento_id=event_id,
                            mensagem=message,
                            aguardar_resposta=False,
                        )

                future = asyncio.run_coroutine_threadsafe(send(), loop)
                return future.result(timeout=60)

            set_review_dispatcher(dispatch_human_response)
            agent = TriageAgent(session)
            await agent.load_reference_data()
            log.info("Agente de triagem conectado e pronto.")
            while True:
                try:
                    response = await call_tool(session, "proximo_evento", read_only=True, timeout=EVENT_POLL_TIMEOUT_SECONDS, esperar_segundos=ESPERAR_SEGUNDOS_EVENTO)
                except MCPToolError as error:
                    if error.codigo == 410: log.info("O cenário já havia terminado."); return
                    log.error("Erro ao buscar próximo evento: %s - %s", error.codigo, error.mensagem); await asyncio.sleep(2); continue
                if response.get("encerrado"): log.info("Cenário encerrado. Fim da execução."); return
                event=response.get("evento")
                if not event: continue
                try: await agent.process(event)
                except MCPToolError as error:
                    if error.codigo == 410: log.info("O cenário terminou durante o processamento."); return
                    log.error("Erro ao processar %s: %s - %s", event["id"], error.codigo, error.mensagem)
                except Exception: log.exception("Falha inesperada processando %s", event.get("id"))

if __name__ == "__main__":
    try: asyncio.run(run())
    except KeyboardInterrupt: print("\nAgente interrompido.", file=sys.stderr)
