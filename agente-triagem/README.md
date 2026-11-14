# Agente de Triagem N1

Sistema multiagente para o desafio de Triagem N1 da T4H. O agente recebe
eventos no formato original de e-mail pelo MCP, decide a trilha de atendimento
e executa a ação adequada sem expor dados sensíveis.

## Problema e valor

Pedidos de acesso, equipamentos, férias, reembolso e falhas de produto chegam
misturados na caixa de entrada. A triagem manual causa retrabalho, atrasos de
SLA e risco de deixar incidentes ou golpes sem escalonamento. O sistema reduz
esse custo respondendo FAQs, validando chamados, agrupando incidentes e
encaminhando os casos que exigem uma pessoa.

## Time de agentes

| Agente | Papel |
| --- | --- |
| `Classifier` | Interpreta o evento, identifica a trilha, extrai campos e estima criticidade. |
| `FaqAgent` | Responde dúvidas cobertas pelo FAQ; se não houver resposta, passa ao `TicketAgent`. |
| `TicketAgent` | Valida catálogo, detecta duplicidade, coleta campos faltantes e registra chamados. |
| `IncidentAgent` | Classifica P0/P1/P2, cria incidentes e correlaciona novos relatos. |
| `SecurityAgent` | Trata phishing, comprometimento de contas e encaminhamento à Segurança. |
| `VacationAgent` | Preenche o sistema legado de férias usando Playwright. |
| `RoutingAgent` | Encaminha pedidos fora do catálogo para o time correto. |
| `ReviewerAgent` | Revisa respostas sem inventar dados nem remover instruções obrigatórias. |
| `HumanReview` | Aprova, rejeita ou corrige respostas sensíveis e incidentes críticos. |

O fluxo normal é:

```text
evento -> Classifier -> agente especializado -> ReviewerAgent -> MCP
                                      \-> HumanReview quando necessário
```

## Regras críticas

- P0: indisponibilidade para todos ou comprometimento grave de segurança.
- P1: função importante indisponível para muitos clientes.
- P2: falha parcial, atraso ou degradação com contorno.
- P3/P4: problema individual, dúvida ou melhoria.
- Phishing externo nunca recebe resposta no endereço suspeito.
- Dados pessoais sensíveis são encaminhados para uma pessoa.
- Férias são lançadas somente no sistema legado.
- O agente nunca aprova sozinho uma solicitação que exige gestor.

## Resiliência

O cliente MCP:

- repete `503` com backoff;
- aguarda respostas lentas;
- repete leituras após `504`;
- consulta o estado antes de repetir uma escrita após `504` ou `409`;
- registra tentativas, reconciliações, agente e motivo no painel.

## Execução

O mundo deve rodar em Docker:

```powershell
Set-Location "t4h-desafios\Hackathon - UNIFEI 2026\Servidor MCP (Compartilhado)"
$env:TEMA="triagem"
$env:ERROS="0"
docker compose up --build mundo
```

Em outro terminal:

```powershell
Set-Location "agente-triagem"
.\.venv\Scripts\python.exe main.py
```

Painéis:

- Mundo: <http://localhost:8765>
- Agentes: <http://localhost:8766/agents>
- Revisão humana: <http://localhost:8766/review>

## Demonstração sugerida

Em cinco minutos, mostrar:

1. Uma dúvida respondida pelo FAQ.
2. Um chamado incompleto pedindo dados.
3. Dois relatos correlacionados no mesmo incidente P1.
4. Um phishing encaminhado à Segurança sem resposta externa.
5. Uma resposta revisada pelo `ReviewerAgent` e aprovada em `HumanReview`.

Métricas para acompanhar no log: eventos com ação, taxa de resolução correta,
duplicidades evitadas, incidentes P0/P1 escalados e tempo até a primeira ação.
