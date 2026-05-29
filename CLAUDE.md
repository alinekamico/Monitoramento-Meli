# CLAUDE.md — Contexto persistente para futuras sessões

## O que é este projeto

Dois módulos sob o mesmo guarda-chuva, compartilhando credenciais ML e PDV:

1. **Central de Promoções ML** (legado, dry-run em produção) — analisa
   campanhas `candidate` via API ML, calcula RC, loga ACEITAR/RECUSAR.
2. **MVP Buybox & Pricing** (operacional, em produção via AWS) — coleta
   snapshots do catálogo público a cada hora, sugere preço ótimo,
   dispara alertas por e-mail.

Mesma raiz: `automacao-campanhas-ml/`. Não há plano de separar.

## Regras absolutas

- **Nunca editar** arquivos fora de `automacao-campanhas-ml/`.
- **Nunca criar `.env`** — apenas `.env.example`. Credenciais reais ficam no `.env` local OU em AWS Secrets Manager quando em produção.
- **Dry-run por padrão no módulo legado** — `dry_run: true` em `config/settings.yaml`. Só alterar com confirmação explícita.
- **Nenhum valor fixo no código** — tudo em YAML ou `.env`.
- Sempre perguntar antes de assumir quando faltar informação.
- **Reaproveitar** `margem.calcular_margem`, `pdv.load_skus`, `ml_client.*` — não duplicar fórmulas.
- **Português** em identificadores, comentários, docstrings e logs.
- **Atualizar `CHANGELOG.md` e `README.md` a cada mudança relevante**. O `CHANGELOG.md` é o ponto de entrada de contexto para nova sessão — mantenha-o conciso e cronológico.

## Estrutura atual

```
automacao-campanhas-ml/
├── main.py                          # CLI campanhas legacy
├── scheduler.py                     # Loop horário do Buybox
├── server.py                        # Flask + endpoints / e /api/buybox/*
├── dashboard.html                   # Painel com 2 abas (Campanhas + Buybox)
├── config/
│   ├── settings.yaml                # rc_minimo=60 + seção buybox.*
│   ├── skus.yaml                    # 23 SKUs com CMV + peso + tipo_anuncio
│   └── frete_tabela.yaml
├── src/
│   ├── ml_client.py                 # OAuth + endpoints (campanhas, /items, /products, /users)
│   ├── pdv.py                       # carrega skus.yaml
│   ├── margem.py                    # fórmula PDV — reuso obrigatório
│   ├── decisor.py                   # ACEITAR/RECUSAR para campanhas
│   ├── notificador.py               # log + e-mail das campanhas (legado)
│   ├── runner.py                    # orquestrador campanhas
│   ├── buybox/
│   │   ├── modelos.py               # ORM (Snapshot, SnapshotConcorrente, Alerta) + dataclasses
│   │   ├── persistencia.py          # SQLAlchemy + init_db + CRUD + _migrar_schema
│   │   ├── catalogo.py              # montar_top5, nosso_preco_efetivo, e_visivel_ao_cliente
│   │   ├── pricing.py               # calcular_preco_otimo, calcular_preco_candidato,
│   │   │                            # _calcular_rebate_valor, rebate_aplicavel
│   │   └── coletor.py               # CLI da coleta
│   └── alertas/
│       ├── regras.py                # A1/A2/A3 + avaliar_resumo_diario
│       ├── templates.py             # HTML inline dos e-mails
│       ├── email.py                 # SMTP+TLS
│       └── avaliador.py             # cooldown + envio + persistência
├── tests/                           # 62 testes pytest
├── scripts/                         # diagnóstico operacional
├── data/buybox.db                   # SQLite gerado em runtime
└── logs/buybox-YYYY-MM-DD.log       # JSON-lines
```

## Fórmula PDV (planilha PDV Campanhas Meli.xlsx) — reusada nos dois módulos

```
comissão  = preço × 14% (Clássico) / 19% (Premium)
frete     = tabela(preço, peso) [bisect_right em frete_tabela.yaml]
imposto   = preço × 5%
insumo    = R$ 2,00 fixo (R$ 0 quando is_full)
reversa   = preço × 0,5%

lucro_bruto = preço − custo − comissão − frete − imposto − insumo − reversa + rebate_ML
margem_pct  = lucro_bruto / preço × 100
rc_pct      = lucro_bruto / custo × 100   ← métrica canônica de decisão
```

Limite atual: **RC >= 60%** (subiu de 50 com o MVP, confirmado pelo usuário).

## Cálculo do rebate (regra crítica — refinada em 3 iterações)

O rebate ML é **valor FIXO em R$**, calculado pelo ML como:
```
rebate_R$ = original_price × meli_percentage / 100
```
**NÃO** se recalcula proporcional ao preço atual (esse foi um bug grande corrigido).

`pricing.rebate_aplicavel(preco, campanha)` decide se vale, em **4 camadas**:

1. Sem `rebate_pct` ou rebate_pct <= 0 → não aplica
2. Tem `min_price` ou `max_price` definidos (SMART/DEAL) → usa a faixa
3. Sem faixa mas tem `preco_aplicado` (SELLER_CAMPAIGN) → rebate só vale se `preco ≈ preco_aplicado` (tolerância R$ 0,05). Razão de negócio: o usuário sai da campanha externa e usa a campanha **PRÓPRIA** dele (sem rebate ML) quando muda o preço
4. Sem nada disso (snapshots antigos pré-migração) → aplica (compatibilidade)

`pricing._calcular_rebate_valor(campanha)` calcula o valor em R$:
- Base = `original_price` da campanha
- Fallback: `preco_aplicado` se `original_price` não veio

Os testes em `test_pricing.py` cobrem todos os ramos.

## SKUs rastreados (23 — Clássicos)

WLK004, WL008, WL029, WL028, WL010, WLK005, WLK006, WLK026, WLK169,
WL033, WL035, WL039, WL043, WLK018, WLK019, WLK020, WL009, WL041,
WL036, WL034, WL025, WL013, WLK047.

## Endpoints ML usados

Campanhas (legado):
- `GET /users/{seller_id}/items/search?seller_sku=SKU`
- `GET /items?ids=...`
- `GET /seller-promotions/items/{item_id}?app_version=v2`

Catálogo público (Buybox):
- `GET /products/{product_id}` — buy_box_winner
- `GET /products/{product_id}/items` — **fonte da verdade do preço visível ao cliente**
- `GET /users/{seller_id}` — nome + reputação

Tokens: renovados em 401 via `refresh_access_token`. Credenciais compartilhadas com o projeto base.

## Como o coletor monta um snapshot

1. `ml_client.get_item_ids_by_sku(seller_id, sku)` → MLBs
2. `ml_client.get_items_details(item_ids)` → detail
3. `_campanha_ativa(item_id)` → campanha started com `price > 0` (não filtra por `meli_percentage` — SELLER_CAMPAIGN sem rebate ML também conta para identificar preço efetivo)
4. `catalogo.montar_top5(detail, seller_id)`:
   - Se `e_visivel_ao_cliente(detail)` é False (pausado/sem estoque) → NÃO insere nossa entrada artificialmente
   - Top 5 vem ordenado por preço
5. `_preco_base(detail, top5, campanha, fonte)`:
   - **Top5[nosso].preço** primeiro (fonte da verdade, já reflete descontos)
   - Fallback: `campanha.price`
   - Fallback: `detail.price`
6. `pricing.calcular_preco_otimo(...)` com `visivel_no_catalogo=...`
7. Persiste via `salvar_snapshot(dom)` — idempotente em `(sku, item_id, coletado_em)`

## Schema do banco (com migrações automáticas)

Banco: **MySQL** (um banco por conta: `best_hair_buybox`, `hair_pro_buybox`).
Credenciais do servidor em `MYSQL_HOST/PORT/USER/PASSWORD` no `.env`.

Tabela `snapshots`:
- Campos básicos: `sku`, `item_id`, `coletado_em`, `preco_atual`, `preco_1o`, `preco_2o`, etc.
- **Campos novos da iteração de campanhas** (adicionados via `_migrar_schema`):
  - `custo` — CMV no momento da coleta (auditoria)
  - `campanha_min_price`, `campanha_max_price` — faixa válida (SMART/DEAL)
  - `campanha_original_price` — base para rebate fixo em R$

`persistencia._migrar_schema(engine)` aplica `ALTER TABLE ADD COLUMN` para bancos pré-existentes. **Idempotente** — usa `SQLAlchemy inspect()` para checar colunas existentes (funciona com MySQL e SQLite).

`_MIGRACOES` é uma lista de `(tabela, coluna, tipo)`. Adicione novas linhas se precisar de outras colunas no futuro. Os tipos `REAL`, `TEXT` e `INTEGER` são válidos em MySQL.

## Decisões de design importantes

1. **Top 5 é fonte da verdade do preço efetivo** — `item.price` muitas vezes vem cheio quando há campanha SELLER_CAMPAIGN.
2. **Anúncios off-catálogo não recebem sugestão de preço** — viraria ruído (não há buybox a preservar).
3. **Cooldown só conta envios reais** — alertas suprimidos por dry-run/email-off não bloqueiam o próximo envio.
4. **Auditoria completa** — toda detecção registra na tabela `alertas`, mesmo suprimida (`dados.motivo_supressao` explica por quê).
5. **A3 exige confirmação de 2 ciclos** — anti-falso-positivo na inicialização e em flutuações do endpoint público.
6. **Margem no dashboard usa cor do RC** — coerência visual com a métrica de decisão (não da margem em si).
7. **`is_full` zera `insumo_fixo`** — mesma regra do `runner.py` legado, aplicada também no pricing do buybox.
8. **`SnapshotDom` (dataclass) ≠ `Snapshot` (ORM)** — domínio desacoplado de sessão, igual ao padrão `_parse_promotion` do legado.
9. **Cache de sellers module-level** em `ml_client._seller_cache` — limpo entre ciclos via `limpar_cache_sellers()`.
10. **Sleep alinhado em hora cheia** — `_segundos_ate_proxima_execucao(60)` calcula até `HH+1:00:05`, evita drift.
11. **`calcular_preco_candidato` desacoplada** — `calcular_preco_otimo` usa internamente a mesma função que o `server.py` chama para mostrar o "preço candidato descartado" no breakdown. **Sempre alterar as duas juntas** se mudar a lógica de qual preço-alvo testar.
12. **Rebate ML é valor FIXO em R$** sobre `original_price`, NÃO proporcional ao preço atual. Esse foi um bug histórico — não voltar a recalcular como `preço × pct`.
13. **Subida de preço só se RC melhora** — `calcular_preco_otimo` bloqueia sugestão de alta quando `rc_candidato ≤ rc_atual`, mesmo que `rc_candidato ≥ rc_minimo`. Motivo: mudar de preço pode fazer perder o rebate de campanha, reduzindo RC apesar do preço maior.

## Estado atual

- **6 fases concluídas + várias iterações de refinamento**: setup, coletor, alertas, scheduler, dashboard, polimento + ajustes de pricing (rebate, faixa de campanha, campanha externa, breakdown).
- **62 testes verdes** (pricing, catálogo, persistência, regras, avaliador).
- **E-mail de alertas ATIVO** (`buybox.email.enabled: true`) — envia para `luiz.pimentel@kamico.com.br` via Gmail SMTP.
- **Multi-conta** (Best Hair + Hair Pro) — seletor no dashboard, `?conta=` em todos os endpoints, `_get_env()` recarrega `.env` sob demanda.
- **Cache de campanhas** — 10 min TTL por conta, `?force=true` para bypassar; processamento paralelo com 3 workers (~23 s → 330 ms).
- **RC mínimo editável** pelo dashboard — ✏️ no card, PUT `/api/rc-minimo`, persiste em `settings.yaml`.
- **Modal ⚙️ de SKUs** — edita `custo`/`peso`/`tipo_anuncio` pelo dashboard, PUT `/api/skus`, persiste em `skus.yaml`.
- **Aba Buybox no dashboard** com:
  - Tabela com filtros (Todos / Buybox / Em risco / Oportunidade / Off-catálogo / Com estoque) + busca
  - Header das colunas **sticky** (cola abaixo das tabs ao rolar)
  - Chip destacado para a coluna Posição
  - Botão **"Coletar agora ▾"** com dropdown de checkboxes (lê `/api/buybox/skus-configurados`)
  - Modal de detalhe com: snapshot atual, sugestão, breakdown da margem (cards lado a lado), top 5, campanhas disponíveis (lazy load) com botões **ACEITAR ↗ / RECUSAR ↗** que abrem a Central de Promoções ML filtrada pelo MLB, gráfico de posição, gráfico Preço × Vendas dual-eixo
  - Toast de feedback (verde/vermelho/azul) para ações assíncronas

## O que ainda falta

1. **Pricing automático via API** — endpoint `PUT /items/{id}` para aplicar o preço sugerido. Hoje só sugere.
2. **Aceite real de campanha** — `runner._aceitar_campanha` ainda é stub.
3. **Custo dinâmico via Tiny ERP** — `pdv.py` tem o ponto de troca.
4. **Notificação Telegram** — `notificador.py` tem stub.
5. **Resumo diário** — funciona, mas deveria ter teste de fim a fim com 24h reais de dados.
6. **Múltiplos destinatários por tipo de alerta** — hoje é uma lista global em `buybox.email.destinatarios`.
7. **Deploy AWS** — README tem a receita completa (systemd + nginx + RDS opcional). Próximo passo é executar.

## Configuração estendida (settings.yaml)

```yaml
rc_minimo: 60.0

buybox:
  intervalo_coleta_minutos: 60
  cooldown_a1_horas: 6
  cooldown_a2_horas: 2
  cooldown_a3_horas: 4
  margem_minima_b2_pct: 20.0
  rc_oportunidade_b3_pct: 70.0
  fracao_snapshots_b2: 0.5
  diferenca_ruido_rs: 1.00
  passo_abaixo_rs: 0.10
  ciclos_confirmacao_a3: 2
  hora_resumo_diario: 8
  db_path: "data/buybox.db"
  fonte_preco: "suggested_price"

  email:
    enabled: true        # alertas ativos — destinatário: luiz.pimentel@kamico.com.br
    smtp_host: smtp.gmail.com
    smtp_port: 587
    remetente_env: EMAIL_REMETENTE
    senha_env: EMAIL_SENHA_APP
    destinatarios:
      - luiz.pimentel@kamico.com.br
```

## Atalhos úteis (CLI)

```powershell
# Coleta
python -m src.buybox.coletor --sku WLK004 --com-alertas

# Scheduler
python scheduler.py --once --sku WLK004
python scheduler.py                    # loop produção

# Diagnóstico
python -m scripts.diagnosticar_env
python -m scripts.enviar_email_teste
python -m scripts.inspecionar_item MLB4422127791
python -m scripts.diagnosticar_campanha MLB4232704563

# Dashboard
python server.py                       # http://localhost:5000

# Testes
pytest tests/ -v
```

## Logs e observabilidade

- `logs/buybox-YYYY-MM-DD.log` — JSON-lines por evento (`snapshot_salvo`, `alerta_*`, `ciclo_*`, `erro_*`)
- MySQL (`best_hair_buybox` / `hair_pro_buybox`) — 3 tabelas — abrir com DBeaver/Beekeeper/MySQL Workbench para inspeção
- `data/.ultimo_resumo_diario` — stamp YYYY-MM-DD para evitar resumo duplicado
- Tabela `alertas` — auditoria completa: enviados + suprimidos (com motivo)

## Como prosseguir em nova sessão

Se você (Claude) está pegando este projeto pela 1ª vez, leia **na ordem**:

1. **`CHANGELOG.md`** — cronologia compacta do que foi feito (visão de 1 minuto)
2. **Este arquivo (`CLAUDE.md`)** — decisões de design + regras absolutas
3. **`README.md`** — CLI, endpoints, configuração, deploy AWS

Pontos de atenção ao trabalhar no código:
- Antes de mexer em `pricing.py`, releia os itens 11 e 12 de "Decisões de design importantes"
- Antes de adicionar colunas no banco, releia "Schema do banco (com migrações automáticas)" — use `_MIGRACOES` em `persistencia.py`
- Toda mudança em `calcular_preco_otimo` (escolha do preço-alvo) precisa refletir em `calcular_preco_candidato` — são deliberadamente espelhadas
- **Ao final de cada mudança relevante**, atualize `CHANGELOG.md` (cronológico inverso) e `README.md` (descrição estável)
