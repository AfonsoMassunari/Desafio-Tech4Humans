"""Configuração central do agente de triagem."""
from __future__ import annotations
import logging
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env")); load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("triagem")
MCP_URL=os.getenv("MCP_URL","http://localhost:8765/mcp"); LEGACY_URL=os.getenv("LEGACY_URL","http://localhost:8765")
AGENT_NAME=os.getenv("AGENTE","agente-triagem")[:60]; DOMINIO_EMPRESA="@t4h.exemplo"
LLM_API_KEY=os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
LLM_BASE_URL=os.getenv("LLM_BASE_URL") or None
LLM_MODEL=os.getenv("LLM_MODEL","gpt-4o-mini")
LLM_HABILITADO=bool(LLM_API_KEY)
AGENT_DASHBOARD_PORT=int(os.getenv("AGENT_DASHBOARD_PORT","8766"))
AGENT_MEMORY_PATH=os.getenv(
    "AGENT_MEMORY_PATH",
    os.path.join(os.path.dirname(__file__), "data", "agent_memory.sqlite3"),
)
AGENT_DASHBOARD_PATH=os.getenv(
    "AGENT_DASHBOARD_PATH",
    os.path.join(os.path.dirname(__file__), "data", "dashboard.json"),
)
AGENT_REVIEW_PATH=os.getenv(
    "AGENT_REVIEW_PATH",
    os.path.join(os.path.dirname(__file__), "data", "human_reviews.json"),
)
MAX_RETRIES=4; READ_TIMEOUT_SECONDS=20; ESPERAR_SEGUNDOS_EVENTO=25; EVENT_POLL_TIMEOUT_SECONDS=ESPERAR_SEGUNDOS_EVENTO+READ_TIMEOUT_SECONDS