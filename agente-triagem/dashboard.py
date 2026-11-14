"""Painel local para acompanhar os agentes e suas ações por evento."""
from __future__ import annotations

import json
import logging
import os
import asyncio
import threading
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from config import AGENT_DASHBOARD_PATH, AGENT_REVIEW_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DashboardStore:
    def __init__(self, path: str = AGENT_DASHBOARD_PATH) -> None:
        self._lock = threading.RLock()
        self._events: dict[str, dict[str, Any]] = {}
        self._reviews: dict[str, dict[str, Any]] = {}
        self.path = path
        self.review_path = AGENT_REVIEW_PATH
        self._load()
        self._load_reviews()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as file:
                records = json.load(file)
            if isinstance(records, list):
                self._events = {
                    str(item["id"]): item
                    for item in records
                    if isinstance(item, dict) and item.get("id")
                }
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            logging.getLogger("triagem").warning(
                "Não foi possível carregar o painel persistido: %s", error
            )

    def _persist_locked(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = f"{self.path}.tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(list(self._events.values()), file, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def _load_reviews(self) -> None:
        try:
            with open(self.review_path, encoding="utf-8") as file:
                records = json.load(file)
            if isinstance(records, list):
                self._reviews = {
                    str(item["event_id"]): item
                    for item in records
                    if isinstance(item, dict) and item.get("event_id")
                }
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            logging.getLogger("triagem").warning(
                "Não foi possível carregar a fila de revisão humana: %s", error
            )

    def _persist_reviews_locked(self) -> None:
        directory = os.path.dirname(self.review_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = f"{self.review_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(list(self._reviews.values()), file, ensure_ascii=False, indent=2)
        os.replace(temporary, self.review_path)

    def record_event(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("id", ""))
        if not event_id:
            return
        with self._lock:
            item = self._events.setdefault(
                event_id,
                {
                    "id": event_id,
                    "event": {},
                    "decision": None,
                    "agents": [],
                    "actions": [],
                    "updated_at": _now(),
                },
            )
            item["event"] = deepcopy(event)
            item["updated_at"] = _now()
            self._persist_locked()

    def record_decision(
        self,
        event: dict[str, Any],
        decision: Any,
        handler_agent: str,
    ) -> None:
        self.record_event(event)
        with self._lock:
            item = self._events[event["id"]]
            item["decision"] = {
                "trilha": decision.trilha,
                "categoria": decision.categoria,
                "campos": deepcopy(decision.campos),
                "resumo": decision.resumo,
                "time_destino": decision.time_destino,
                "motivo": decision.motivo,
                "faq_id": decision.faq_id,
            }
            item["severity"] = (
                decision.categoria
                if decision.trilha == "incident"
                else "SECURITY"
                if decision.trilha == "security"
                else None
            )
            item["agents"] = sorted(set(item["agents"]) | {"Classifier", handler_agent})
            item["updated_at"] = _now()
            severity = item["severity"]
            sensitive_text = json.dumps(event, ensure_ascii=False).lower()
            sensitive_case = any(
                marker in sensitive_text
                for marker in ("cpf", "cid", "dados bancários", "dados bancarios", "conta bancária", "conta bancaria")
            )
            if severity in ("P0", "P1", "SECURITY") or sensitive_case:
                self.request_review(
                    event,
                    reason=(
                        "Incidente crítico requer validação humana."
                        if severity in ("P0", "P1")
                        else "Caso de segurança ou dado sensível requer decisão humana."
                    ),
                    decision=item["decision"],
                )
            self._persist_locked()

    def request_review(
        self,
        event: dict[str, Any],
        *,
        reason: str,
        decision: dict[str, Any],
    ) -> None:
        event_id = str(event.get("id", ""))
        if not event_id:
            return
        with self._lock:
            if event_id in self._reviews:
                return
            self._reviews[event_id] = {
                "event_id": event_id,
                "event": deepcopy(event),
                "decision": deepcopy(decision),
                "reason": reason,
                "status": "pending",
                "comment": "",
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._persist_reviews_locked()

    def request_event_review(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._events.get(event_id)
            if item is None:
                return None
            decision = item.get("decision") or {}
            existing = self._reviews.get(event_id)
            if existing is not None:
                existing["status"] = "pending"
                existing["reason"] = "Revisão manual solicitada pelo operador."
                existing["updated_at"] = _now()
                self._persist_reviews_locked()
                return deepcopy(existing)
            self.request_review(
                item.get("event") or {"id": event_id},
                reason="Revisão manual solicitada pelo operador.",
                decision=decision,
            )
            return deepcopy(self._reviews.get(event_id))

    def event_snapshot(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._events.get(event_id)
            return deepcopy(item) if item else None

    def set_reprocessed_response(self, event_id: str, message: str) -> dict[str, Any] | None:
        with self._lock:
            review = self._reviews.get(event_id)
            if review is None:
                return None
            review["corrected_response"] = message[:2000]
            review["status"] = "pending"
            review["reason"] = "Resposta refeita pelo modelo e aguardando aprovação humana."
            review["updated_at"] = _now()
            self._persist_reviews_locked()
            self.record_action(
                event_id,
                "human_llm_review",
                {"message": message[:2000]},
                {"status": "pending"},
            )
            return deepcopy(review)

    def reviews_snapshot(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            reviews = list(self._reviews.values())
            if status:
                reviews = [item for item in reviews if item["status"] == status]
            return list(reversed(deepcopy(reviews)))

    def update_review(
        self,
        event_id: str,
        status: str,
        comment: str,
        message: str = "",
    ) -> dict[str, Any] | None:
        if status not in ("approved", "rejected", "needs_info"):
            raise ValueError("status inválido")
        with self._lock:
            review = self._reviews.get(event_id)
            if review is None:
                return None
            review["status"] = status
            review["comment"] = comment[:2000]
            if message.strip():
                review["corrected_response"] = message[:2000]
            review["updated_at"] = _now()
            self._persist_reviews_locked()
            self.record_action(
                event_id,
                "human_review",
                {"status": status, "comment": comment[:2000], "message": message[:2000]},
                {"status": status, "corrected_response": message[:2000]},
            )
            return deepcopy(review)

    def record_action(
        self,
        event_id: str,
        tool: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if not event_id:
            return
        with self._lock:
            item = self._events.setdefault(
                event_id,
                {
                    "id": event_id,
                    "event": {},
                    "decision": None,
                    "agents": [],
                    "actions": [],
                    "updated_at": _now(),
                },
            )
            action: dict[str, Any] = {
                "at": _now(),
                "tool": tool,
                "arguments": deepcopy({k: v for k, v in arguments.items() if k != "agente"}),
            }
            if result is not None:
                action["result"] = deepcopy(result)
            if error is not None:
                action["error"] = error
            item["actions"].append(action)
            item["updated_at"] = _now()
            self._persist_locked()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(deepcopy(list(self._events.values()))))


STORE = DashboardStore()


HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel dos agentes</title>
<style>
:root { color-scheme: dark; font-family: Inter, Segoe UI, sans-serif; }
body { margin: 0; background:#10141c; color:#e7edf5; }
header { padding:20px 28px; background:#182231; border-bottom:1px solid #2d3b4e; }
h1 { margin:0 0 6px; font-size:24px; } .muted { color:#9eacbd; font-size:13px; }
.toolbar { display:flex; gap:10px; padding:16px 28px; flex-wrap:wrap; background:#141c28; }
input, select { background:#202c3b; color:#fff; border:1px solid #3a4b61; border-radius:6px; padding:9px; }
main { padding:0 28px 30px; } .summary { display:flex; gap:12px; margin:18px 0; flex-wrap:wrap; }
.badge { background:#202c3b; border:1px solid #34465c; border-radius:8px; padding:10px 14px; }
.case { background:#182231; border:1px solid #2d3b4e; border-radius:10px; margin:12px 0; overflow:hidden; }
.case-head { display:flex; justify-content:space-between; gap:15px; padding:15px; cursor:pointer; }
.case-head strong { color:#7dd3fc; } .pill { border-radius:20px; padding:4px 9px; background:#29425d; font-size:12px; }
.review-link { display:inline-block; margin-top:10px; padding:12px 18px; border-radius:8px; background:#2563eb; color:#fff; font-weight:700; text-decoration:none; }
.severity { font-weight:700; } .severity.p0 { color:#ff6b6b; } .severity.p1 { color:#ffb454; } .severity.p2 { color:#7dd3fc; } .severity.security { color:#d8b4fe; }
.body { display:none; border-top:1px solid #2d3b4e; padding:15px; }
.case.open .body { display:block; } .grid { display:grid; grid-template-columns:1fr 1fr; gap:15px; }
section { background:#131b27; border-radius:8px; padding:12px; margin-bottom:12px; }
pre { white-space:pre-wrap; overflow:auto; color:#c9d5e3; font-size:12px; }
.action { border-left:3px solid #5b8def; margin:9px 0; padding:8px 10px; background:#1b2736; }
.action b { color:#a5c8ff; } .error { border-color:#ef6c6c; } @media(max-width:800px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><h1>Painel de agentes</h1><div class="muted">Acompanhamento por evento, decisão, agentes envolvidos e ações MCP</div><a href="/review" class="review-link">Abrir revisão humana</a></header>
<div class="toolbar">
<input id="search" placeholder="Buscar evento, assunto, remetente ou ação">
<select id="track"><option value="">Todas as trilhas</option><option>faq</option><option>ticket</option><option>incident</option><option>security</option><option>vacation</option><option>team_route</option></select>
<select id="tool"><option value="">Todas as ações</option><option>responder_evento</option><option>registrar</option><option>atualizar</option><option>enviar_mensagem</option></select>
</div>
<main><div id="summary" class="summary"></div><div id="cases"></div></main>
<script>
let data=[], lastPayload='', openCases=new Set();
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = x => esc(JSON.stringify(x, null, 2));
function render() {
  document.querySelectorAll('.case.open').forEach(x=>openCases.add(x.dataset.id));
  const q=document.querySelector('#search').value.toLowerCase(), track=document.querySelector('#track').value, tool=document.querySelector('#tool').value;
  const filtered=data.filter(x => {
    const text=JSON.stringify(x).toLowerCase();
    return (!q || text.includes(q)) && (!track || (x.decision && x.decision.trilha===track)) && (!tool || x.actions.some(a=>a.tool===tool));
  });
  const counts={}; data.forEach(x=>{const t=x.decision?.trilha||'pendente'; counts[t]=(counts[t]||0)+1;});
  const severities={P0:0,P1:0,P2:0,SECURITY:0}; data.forEach(x=>{if (x.severity) severities[x.severity]++;});
  document.querySelector('#summary').innerHTML=Object.entries(counts).map(([k,v])=>`<div class="badge"><b>${esc(k)}</b><br>${v} evento(s)</div>`).join('')+
    Object.entries(severities).filter(([,v])=>v).map(([k,v])=>`<div class="badge severity ${k.toLowerCase()}"><b>${k}</b><br>${v} incidente(s)</div>`).join('');
  document.querySelector('#cases').innerHTML=filtered.map((x,i)=>{
    const e=x.event||{}, d=x.decision||{};
    const actions=x.actions.map(a=>`<div class="action ${a.error?'error':''}"><b>${esc(a.tool)}</b> <span class="muted">${esc(a.at)}</span><pre>${json(a.arguments)}${a.result?'\nResultado:\n'+json(a.result):''}${a.error?'\nErro: '+esc(a.error):''}</pre></div>`).join('');
    const severity=x.severity?`<span class="pill severity ${x.severity.toLowerCase()}">${esc(x.severity)}</span>`:'';
    return `<article class="case ${openCases.has(x.id)?'open':''}" data-id="${esc(x.id)}"><div class="case-head"><div><strong>${esc(x.id)}</strong> — ${esc(e.assunto||'(sem assunto)')}<div class="muted">${esc(e.de)} · ${esc(e.origem||'')}</div></div><div>${severity} ${d.trilha?`<span class="pill">${esc(d.trilha)}</span>`:''} <span class="pill">${x.actions.length} ação(ões)</span></div></div><div class="body"><div class="grid"><section><h3>Evento inicial</h3><pre>${json(e)}</pre></section><section><h3>Decisão do classificador</h3><pre>${json(d)}</pre><b>Agentes:</b> ${x.agents.map(esc).join(', ')}</section></div><section><h3>Histórico de ações</h3>${actions||'<div class="muted">Nenhuma ação registrada.</div>'}</section></div></article>`;
  }).join('') || '<p class="muted">Nenhum evento corresponde aos filtros.</p>';
  document.querySelectorAll('.case').forEach(x=>x.querySelector('.case-head').addEventListener('click',()=> {
    x.classList.toggle('open');
    if (x.classList.contains('open')) openCases.add(x.dataset.id); else openCases.delete(x.dataset.id);
  }));
}
async function load(){
  try {
    const r=await fetch('/api/agents', {cache:'no-store'});
    const payload=await r.text();
    if (payload !== lastPayload) { lastPayload=payload; data=JSON.parse(payload); render(); }
  } catch (error) { console.warn('Não foi possível atualizar o painel:', error); }
}
['search','track','tool'].forEach(id=>document.querySelector('#'+id).addEventListener('input',render));
load(); setInterval(load,5000);
</script>
</body></html>"""


REVIEW_HTML = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisão humana</title>
<style>
:root { color-scheme: dark; font-family: Inter, Segoe UI, sans-serif; }
body { margin:0; background:#10141c; color:#e7edf5; }
header { padding:20px 28px; background:#182231; border-bottom:1px solid #2d3b4e; }
main { padding:20px 28px; max-width:1100px; margin:auto; }
.toolbar { display:flex; gap:10px; margin-bottom:18px; }
select, textarea, button { background:#202c3b; color:#fff; border:1px solid #3a4b61; border-radius:6px; padding:9px; }
button { cursor:pointer; font-weight:700; } button.approve { background:#166534; } button.reject { background:#991b1b; } button.info { background:#854d0e; } button.llm { background:#2563eb; }
.review { background:#182231; border:1px solid #2d3b4e; border-radius:10px; margin:12px 0; padding:16px; }
.head { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.pill { border-radius:20px; padding:4px 9px; background:#29425d; font-size:12px; }
.p0 { color:#ff6b6b; font-weight:700; } .p1 { color:#ffb454; font-weight:700; } .security { color:#d8b4fe; font-weight:700; }
pre { white-space:pre-wrap; color:#c9d5e3; font-size:12px; background:#131b27; padding:10px; border-radius:6px; }
textarea { width:100%; min-height:70px; box-sizing:border-box; margin:10px 0; }
.actions { display:flex; gap:8px; flex-wrap:wrap; } .muted { color:#9eacbd; font-size:13px; }
</style>
</head>
<body>
<header><h1>Revisão humana</h1><div class="muted">Revise manualmente qualquer resposta emitida pelo agente ou trate casos críticos</div></header>
<main>
<div class="toolbar"><select id="status"><option value="">Todas</option><option value="pending">Pendentes</option><option value="approved">Aprovadas</option><option value="rejected">Rejeitadas</option><option value="needs_info">Aguardando informação</option></select><a href="/agents" class="muted">Voltar ao painel de agentes</a></div>
<div id="reviews"></div>
</main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json=x=>esc(JSON.stringify(x,null,2));
let records=[], events=[];
function responseOf(item){
  const actions=item.actions||[];
  const response=[...actions].reverse().find(x=>x.tool==='responder_evento');
  if(!response) return null;
  const reviewed=[...actions].reverse().find(x=>x.tool==='review_response');
  return {original:response.arguments?.mensagem||'', reviewed:reviewed?.result?.reviewed||response.arguments?.mensagem||''};
}
function render(){
  const status=document.querySelector('#status').value;
  const reviewById=Object.fromEntries(records.map(x=>[x.event_id,x]));
  const filtered=events.map(item=>({item,response:responseOf(item),review:reviewById[item.id]}))
    .filter(x=>x.response && (!status || (x.review?.status||'not_requested')===status));
  document.querySelector('#reviews').innerHTML=filtered.map(({item,response,review})=>{
    const severity=review?.decision?.categoria||item.severity||'MANUAL';
    const current=review?.corrected_response||response.reviewed||response.original;
    const reviewStatus=review?.status||'not_requested';
    const controls=`<textarea id="message-${esc(item.id)}">${esc(current)}</textarea><textarea id="comment-${esc(item.id)}" placeholder="Observação ou orientação humana">${esc(review?.comment||'')}</textarea><div class="actions"><button class="info" onclick="requestReview('${esc(item.id)}')">Colocar na fila</button><button class="llm" onclick="reprocess('${esc(item.id)}')">Refazer com LLM</button><button class="approve" onclick="decide('${esc(item.id)}','approved')">Aprovar resposta</button><button class="reject" onclick="decide('${esc(item.id)}','rejected')">Rejeitar resposta</button><button class="info" onclick="decide('${esc(item.id)}','needs_info')">Pedir informação</button></div>`;
    return `<article class="review"><div class="head"><div><strong>${esc(item.id)}</strong> — ${esc(item.event?.assunto||'(sem assunto)')}<div class="muted">Status: ${esc(reviewStatus)} · agentes: ${esc((item.agents||[]).join(', '))}</div></div><div class="${severity.toLowerCase()}">${esc(severity)}</div></div><h3>Resposta original do agente</h3><pre>${esc(response.original)}</pre><h3>Resposta revisada pelo ReviewerAgent</h3><pre>${esc(response.reviewed)}</pre><h3>Revisão manual</h3>${controls}</article>`;
  }).join('')||'<p class="muted">Nenhuma resposta encontrada para este filtro.</p>';
}
async function load(){
  const result=await Promise.all([fetch('/api/reviews',{cache:'no-store'}),fetch('/api/agents',{cache:'no-store'})]);
  records=await result[0].json(); events=await result[1].json(); render();
}
async function requestReview(eventId){
  await fetch('/api/reviews/'+encodeURIComponent(eventId),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'request'})});
  await load();
}
async function decide(eventId,status){
  const comment=document.querySelector('#comment-'+eventId)?.value||'';
  const message=document.querySelector('#message-'+eventId)?.value||'';
  await fetch('/api/reviews/'+encodeURIComponent(eventId),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status,comment,message})});
  await load();
}
async function reprocess(eventId){
  const message=document.querySelector('#message-'+eventId)?.value||'';
  const response=await fetch('/api/reviews/'+encodeURIComponent(eventId),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reprocess',message})});
  if(!response.ok){ alert('Não foi possível chamar o modelo novamente.'); return; }
  await load();
}
document.querySelector('#status').addEventListener('change',render); load(); setInterval(load,5000);
</script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/agents", "/agents/"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif path in ("/review", "/review/"):
            body = REVIEW_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif path == "/api/agents":
            body = json.dumps(STORE.snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        elif path == "/api/reviews":
            body = json.dumps(STORE.reviews_snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        else:
            self.send_error(404)
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/reviews/"
        if not path.startswith(prefix):
            self.send_error(404)
            return
        event_id = path[len(prefix):]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if payload.get("action") == "request":
                review = STORE.request_event_review(event_id)
            elif payload.get("action") == "reprocess":
                review = STORE.request_event_review(event_id)
                event_record = STORE.event_snapshot(event_id)
                if review is not None and event_record is not None:
                    from agents.reviewer_agent import ReviewerAgent

                    message = str(payload.get("message", "")).strip()
                    if not message:
                        raise ValueError("message é obrigatório para refazer a resposta")
                    rewritten = asyncio.run(
                        ReviewerAgent().review(event_record.get("event") or {}, message)
                    )
                    review = STORE.set_reprocessed_response(event_id, rewritten)
            else:
                review = STORE.update_review(
                    event_id,
                    str(payload.get("status", "")),
                    str(payload.get("comment", "")),
                    str(payload.get("message", "")),
                )
            if review is None:
                self.send_error(404)
                return
            body = json.dumps(review, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_error(400, str(error))

    def log_message(self, *_args: Any) -> None:
        return


def start_dashboard(host: str = "127.0.0.1", port: int = 8766) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="agent-dashboard", daemon=True)
    thread.start()
    return server
