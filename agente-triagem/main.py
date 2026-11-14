"""Entry point do agente de triagem."""
from __future__ import annotations
import asyncio
import sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from triage_agent import TriageAgent
from config import AGENT_DASHBOARD_PORT, EVENT_POLL_TIMEOUT_SECONDS, ESPERAR_SEGUNDOS_EVENTO, MCP_URL, LLM_MODEL, log
from dashboard import start_dashboard
from mcp_client import MCPToolError, call_tool

async def run() -> None:
    start_dashboard(port=AGENT_DASHBOARD_PORT)
    log.info("Painel dos agentes disponível em http://localhost:%s/agents", AGENT_DASHBOARD_PORT)
    log.info("Conectando em %s (modelo %s)...", MCP_URL, LLM_MODEL)
    async with streamable_http_client(MCP_URL) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
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
