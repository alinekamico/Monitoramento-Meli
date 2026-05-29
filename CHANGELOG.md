# Histórico de mudanças

Sistema: **Central de Promoções ML + MVP Buybox** — automação Python que (a) analisa campanhas da Central de Promoções do ML e decide ACEITAR/RECUSAR pelo RC e (b) monitora buybox + pricing dos anúncios, sugere preço ótimo e dispara alertas por e-mail. Dashboard web local (Flask).

> **Regra de manutenção**: toda mudança relevante deve refletir aqui e no `README.md`. Esse arquivo é o ponto de entrada para passar contexto rápido em nova sessão.

---

## 2026-05-28 — Multi-conta, cache, RC editável, SKU config, e-mail ativado

**1. Suporte multi-conta (Best Hair + Hair Pro)** (`src/ml_client.py`, `server.py`, `dashboard.html`)
- `_get_env()` recarrega o `.env` via `load_dotenv(override=True)` quando a variável não está no `os.environ` no momento do start do servidor — corrige 404 em contas cujo token chegou depois do boot
- `trocarConta()` limpa `allData`/`bxData` imediatamente e exibe estado de carregamento, eliminando contaminação de dados entre contas
- Parâmetro `?conta=` propagado por todos os endpoints

**2. Performance: campanhas paralelas + cache** (`server.py`, `dashboard.html`)
- `ThreadPoolExecutor(max_workers=3)` para processar SKUs em paralelo → 70 s → ~23 s (sem throttling do ML)
- `_CAMPAIGN_CACHE` module-level com TTL de 10 min — resposta subsequente em ~330 ms
- `?force=true` para ignorar cache ("Atualizar" do dashboard)
- Indicador de idade do cache em "Atualizado às…": "Cache de N min atrás — clique Atualizar para forçar"

**3. RC mínimo editável pelo painel** (`server.py`, `dashboard.html`)
- Botão ✏️ no card RC mínimo abre campo numérico inline
- PUT `/api/rc-minimo` persiste em `config/settings.yaml` e limpa o cache
- Validação 0–300; toast verde/vermelho no feedback

**4. Modal de configuração de SKUs** (`server.py`, `dashboard.html`)
- Botão ⚙️ no header (esquerdo do seletor de conta) abre modal com tabela editável
- Edita `custo`, `peso` e `tipo_anuncio` (Clássico / Premium) por SKU
- GET/PUT `/api/skus` — persiste em `config/skus.yaml` e limpa o cache
- Layout CSS Grid com header fixo fora do scroll (corrige overlap sticky de thead)
- Inputs com borda transparente em idle e glow accent no foco

**5. ACEITAR/RECUSAR → link Central de Promoções ML** (`dashboard.html`)
- Nos cards "Campanhas disponíveis" do modal de detalhe do Buybox, as tags viram `<button>` com `↗`
- `irParaPromocoesMl(itemId)` abre `mercadolivre.com.br/anuncios/lista/promos?search=<MLB_NUM>` em nova aba
- Campanha "participando" continua como `<span>` (sem ação)
- `decisaoTag()` do painel de Campanhas não foi alterada

**6. E-mail de alertas ativado** (`config/settings.yaml`)
- `buybox.email.enabled: true` — alertas A1/A2/A3 e resumo diário B1/B2/B3 passam a enviar para `luiz.pimentel@kamico.com.br`

---

## 2026-05-28 — Bug pricing: subida de preço sem ganho de RC + tooltip Top 5

**1. Bug fix: sugestão de subida só quando RC melhora** (`src/buybox/pricing.py`)
- `calcular_preco_otimo` agora compara RC do candidato com RC atual antes de sugerir subida
- Caso MLB4232704563: preço candidato R$299,90 (subida de R$4,90) saía da campanha e perdia rebate ~R$16 → RC caía → sugestão bloqueada
- Nova constante `MOTIVO_SUBIDA_SEM_GANHO` ("Subida de preço não melhora RC ({rc}% ≤ atual {rc_atual}%) — mantendo")
- 2 novos testes cobrindo o caso de bloqueio e o caso de subida legítima (sem campanha) — **24/24 verdes**

**2. Tooltip estilizado no gráfico Top 5 concorrentes** (`dashboard.html`)
- Mesmo padrão do gráfico Preço × Vendas: card flutuante position:fixed + linha de cursor vertical
- `onPriceChartMove` / `onPriceChartLeave` — ao hover mostra todos os sellers presentes no timestamp com ponto colorido, posição e preço
- `_priceChartData` global armazena séries/timestamps para o handler

**3. Gráfico Preço × Vendas — simplificação do seletor** (`dashboard.html`)
- Removidas opções "Vendas un", "Vendas R$" e "Só preço" — mantidas apenas "Preço + Vendas" e "Top 5 concorrentes"
- `<select>` com `width: auto` (alinhado ao texto, não estica)
- Tooltip de "Preço + Vendas" passa a exibir: Vendas (un) + Receita (R$) + Preço

---

## 2026-05-28 — Gráfico Preço × Vendas v2 + vigência (investigação)

**1. Gráfico Preço × Vendas — redesign completo** (`dashboard.html`)
- Barras = vendas (unidades ou receita), linha = nosso preço — invertido em relação à v1
- Seletor de visualização virou `<select>` dropdown (Preço + Vendas / Vendas un / Vendas R$ / Só preço / Top 5 concorrentes)
- Filtro de período independente no bloco de resumo: Hoje (24h) / 7 dias / 30 dias (não interfere no filtro de posição da modal)
- Card "Ticket médio" expansível (`<details>`): clique abre tabela de unidades vendidas por faixa de preço com percentual
- Tooltip de hover estilizado ao passar o mouse (card flutuante com data/hora, ponto colorido por métrica e valor em negrito) — implementado via zona transparente SVG + `position:fixed`
- Linha de cursor vertical acompanha o mouse dentro do gráfico

**2. Vigência das campanhas — diagnóstico ampliado** (`src/ml_client.py`, `server.py`)
- `_parse_promotion()` agora verifica também objetos aninhados (`deal`, `conditions`, `schedule`, `promotion`, `validity`) e nomes adicionais (`date_start/date_end`, `effective_start/effective_end`, `valid_from/valid_until`, `from_date/to_date`)
- Endpoint de diagnóstico `GET /api/debug/raw-campanhas/<item_id>` retorna JSON bruto da API ML com a lista de chaves por campanha — permite identificar quais campos estão presentes para diferentes tipos de campanha

---

## 2026-05-28 — Integração Campanhas↔Buybox + gráfico Preço × Vendas

**1. Correção de vigência das campanhas** (`src/ml_client.py`)
- `_parse_promotion()` agora verifica múltiplos nomes de campo (`start_date`, `starts_at`, `date_from`, `promotion_start_date` / `finish_date`, `ends_at`, `finishes_at`, `date_to`, `promotion_end_date`) — diferentes tipos de campanha do ML usam nomes diferentes
- Nova helper `_normalize_date()` converte separador espaço → `T` para ISO 8601 correto

**2. Linhas da aba Campanhas navegam para Buybox** (`dashboard.html`)
- `<tr>` virou `<tr class="camp-row" onclick="navegarParaBuybox(itemId)">` — cursor pointer + hover highlight
- Nova função `navegarParaBuybox(itemId)`: troca aba para Buybox e pré-aplica o filtro de busca com o MLB clicado

**3. Badge de campanhas no painel Buybox** (`dashboard.html`)
- Foto do anúncio ganha badge azul circular no canto superior esquerdo com a contagem de campanhas `CANDIDATA` + decisão `ACEITAR`
- `_buildCampaignBadges()` constrói o mapa `{item_id → count}` toda vez que os dados de campanhas são carregados
- Badge só aparece quando a aba Campanhas já foi carregada (lazy — não faz chamada extra ao backend)

**4. Gráfico Preço × Vendas** (`dashboard.html` + `server.py` + `src/ml_client.py`)
- Novo endpoint `GET /api/buybox/sku/<sku>/vendas?item_id=MLB...&periodo=7d` que alinha pedidos (`/orders/search`) a janelas de snapshot e devolve buckets com `preco_medio`, `unidades`, `receita`
- `ml_client.get_orders_for_item(item_id, desde_iso, ate_iso)` com paginação automática
- Gráfico dual-eixo SVG: barras = nosso preço (eixo Y esquerdo, cor accent), linha com área = vendas (eixo Y direito, azul)
- Seletor de modo: **Preço + Vendas**, **Só preço**, **Vendas (un)**, **Vendas (R$)**, **Top 5 concorrentes**
- Blocos de resumo acima do gráfico: total unidades (7d), total receita (7d), ticket médio
- Tabela colapsável de vendas por faixa de preço
- Preço médio ponderado quando há mudança de preço no mesmo intervalo

---

## 2026-05-28 — Pacote UX (Sprints 1 + 2 + 3)

Pacote de 11 melhorias no dashboard, em 3 frentes:

**Painel principal (lista Buybox)**
- 🖼️ Foto do anúncio antes do SKU (lazy load com placeholder)
- ⭐ Relevância do produto: estrelas + nº avaliações abaixo do título
- ✨ Rebate ≥ 5% ganha badge verde brilhante destacado

**Modal de detalhe**
- Top 5 ganha colunas: vendas do seller (`+1 mil vendas`...), **RC pra vencer** (RC nosso no preço dele), **Prazo SP** (entrega p/ CEP 01310-100, lazy load)
- thead do top 5 agora estático (não rola junto)
- Card "Campanha" mostra **vigência**, com destaque amarelo se expira em <24h
- Foto maior no cabeçalho do modal
- Gráficos (posição + preços) ganham **filtros 24h / 7d / 30d / Personalizado** com seletor de datas
- Gráfico de posição: borda esquerda colorida em cada barra (verde = subiu, vermelho = desceu)
- Eixos X se adaptam: HH:MM em períodos curtos, DD/MM em longos
- Mini-tabela de campanhas: coluna **Vigência** + badge "★ MELHOR" na campanha com maior RC

**Aba Campanhas (legado)**
- Coluna Vigência com destaque amarelo se expira em <24h

**Backend**
- 7 colunas novas migradas automaticamente (`thumbnail_url`, `reviews_rating`, `reviews_total`, `campanha_start_date`, `campanha_finish_date`, `total_vendas` em concorrentes, `prazo_entrega_dias` em concorrentes)
- `snapshots_periodo(sku, horas/desde/ate)` substitui `snapshots_24h` (mantido como alias)
- Endpoints novos: `GET /api/buybox/sku/<sku>/prazos` (lazy load do frete), `GET /api/buybox/sku/<sku>?periodo=24h|7d|30d|custom`
- `ml_client.get_seller_info()` captura `transactions.total` (vendas)
- `ml_client.get_prazo_entrega_dias(item_id, cep)` consulta shipping_options
- `server._calcular_breakdown()` reusado pra calcular "RC pra vencer" de cada seller
- Nova config: `buybox.cep_referencia` (default `"01310100"` — Av. Paulista)

**Testes**: 62/62 verde.

---

## 2026-05-18 — Bug do rebate + breakdown da margem + campanhas no detalhe

**Bug crítico**: rebate ML estava sendo calculado proporcional ao preço atual quando o ML usa **valor FIXO em R$** sobre `original_price`. Caso real: MLB3272862433 mostrava R$ 8,23 quando o real era R$ 16,17.

- `pricing._calcular_rebate_valor(campanha)` calcula `original_price × pct/100` (mesmo cálculo do painel legado)
- Persistido `campanha_original_price` no snapshot
- Heurística `rebate_aplicavel()` em 4 camadas:
  1. Sem rebate_pct → não aplica
  2. Com `min_price/max_price` (SMART/DEAL) → usa a faixa
  3. Sem faixa mas com `preco_aplicado` (SELLER_CAMPAIGN) → rebate só vale no preço onde a campanha está vinculada (tolerância R$ 0,05). Mudar preço = sair pra campanha própria, sem rebate ML
  4. Sem nada disso → aplica (snapshots antigos)
- `pricing.calcular_preco_candidato()` extraído como função pura — usada tanto pelo algoritmo quanto pelo breakdown do modal (corrige bug do "candidato = nós mesmos")

**Breakdown da margem** no modal de detalhe: cards lado a lado com cálculo linha a linha (preço, custo, comissão, frete, imposto, insumo, reversa, rebate, lucro), tanto para preço atual quanto para preço candidato descartado.

**Campanhas no detalhe**: nova seção dentro do modal Buybox que consulta `/api/buybox/sku/<sku>/campanhas` ao vivo e mostra tabela compacta com tudo (Campanha, Status, Preço, Rebate, Margem, RC, Decisão).

---

## 2026-05-15 — Botão "Coletar agora" + filtros + UX

- Botão "Coletar agora ▾" com dropdown de checkboxes (lê `/api/buybox/skus-configurados`)
- Filtro "Com estoque" na barra de filtros
- Header das colunas **sticky** (cola abaixo das tabs ao rolar)
- Chip destacado para a coluna Posição (verde com glow se buybox, amarelo se top 3, vermelho fora)
- Mini-gráfico de posição corrigido (era estourado quando havia poucos snapshots)
- Cores da margem amarradas ao RC (não à margem em si)

---

## 2026-05-14 — Fases 5 e 6 + ajustes pós-coleta real

**Fase 5 — Dashboard**
- Aba Buybox no `dashboard.html` (Campanhas preservada 100%)
- Tabela com 5 filtros + busca + modal de detalhe
- Endpoints: `/api/buybox/lista`, `/api/buybox/sku/<sku>`, `/api/buybox/sku/<sku>/alertas`, `POST /api/buybox/forcar-coleta`
- Modal: top 5, gráfico de posição 24h, gráfico de linhas com preços por concorrente, histórico de alertas

**Fase 6 — Polimento + docs**
- 6 testes do avaliador (cooldown, dry-run, SMTP)
- README + CLAUDE.md completos
- Smoke test: 12,1s para 1 SKU completo

**Hotfix coleta real (Fase 2)**
- Top 5 do catálogo virou **fonte da verdade** do preço efetivo (`item.price` vinha cheio em alguns casos)
- Anúncios pausados / sem estoque marcados OFF-CATÁLOGO (não inseridos artificialmente no top 5)
- Adicionado `visivel_no_catalogo` e `preco_cheio` no snapshot

**E-mail validado** com Gmail SMTP + senha de app + scripts de diagnóstico (`diagnosticar_env.py`, `enviar_email_teste.py`).

---

## 2026-05-13 — Fases 1 a 4 (MVP Buybox inicial)

**Fase 1 — Setup**
- 3 modelos ORM (`Snapshot`, `SnapshotConcorrente`, `Alerta`) com dataclasses de domínio
- SQLite via SQLAlchemy + migrações idempotentes (`_migrar_schema`)
- `config/settings.yaml` ganha seção `buybox.*` (cooldowns, limiares, e-mail)
- `rc_minimo` subiu de 50 para 60

**Fase 2 — Coletor**
- `ml_client.py` ganha funções de catálogo público: `get_top_sellers_for_product`, `get_product_id_from_item`, `get_seller_info`, com cache em memória
- `src/buybox/pricing.py` com `calcular_preco_otimo` (4 ramos: em 1º, fora, único vendedor, off-catálogo)
- `src/buybox/catalogo.py` com `montar_top5` + `derivar_competicao`
- `src/buybox/coletor.py` com CLI `--sku XYZ --com-alertas`

**Fase 3 — Alertas**
- Regras A1 (perdi buybox), A2 (ameaça), A3 (concorrente sumiu, 2 ciclos)
- Regras B1/B2/B3 (resumo diário)
- `src/alertas/avaliador.py` com cooldown (só conta envios reais), dry-run, auditoria completa
- Templates HTML inline (Gmail-friendly)

**Fase 4 — Scheduler**
- `scheduler.py` com loop horário alinhado em hora cheia (sleep até `HH+1:00:05`)
- CLI: `--once`, `--sku`, `--sem-alertas`, `--forcar-resumo`, `--intervalo`
- Stamp em `data/.ultimo_resumo_diario` evita envio duplicado entre reinícios
- Logs JSON-lines em `logs/buybox-YYYY-MM-DD.log`

---

## Estado atual (estável)

**Funcional**
- Coleta horária via `scheduler.py` ou sob demanda pelo dashboard
- 23 SKUs rastreáveis em `config/skus.yaml`
- Pricing com 4 camadas de heurística do rebate ML
- E-mail SMTP funcional (atualmente `enabled: false` por estar em ambiente local)
- Dashboard local em http://localhost:5000 com 2 abas (Campanhas + Buybox)
- 62 testes pytest verdes

**Limitações conhecidas**
- Pricing automático via `PUT /items/{id}` ainda não implementado (sistema só sugere)
- Aceite real de campanha (`runner._aceitar_campanha`) é stub
- Custo dinâmico via Tiny ERP não integrado (vem do YAML)
- Frontend tem URLs hardcoded em `localhost:5000` (ajustar pra deploy externo)

**Próximos passos planejados**
- Deploy AWS (receita pronta no README — EC2 t3.small + systemd + nginx)
- `PUT /items/{id}` para aplicar preço sugerido automaticamente
- Custo dinâmico via Tiny
- Notificação Telegram (stub existe em `notificador.py`)

---

## Como passar contexto a uma nova sessão

1. Compartilhe este `CHANGELOG.md`
2. Compartilhe o `README.md` (resumo completo + endpoints + setup + deploy)
3. Compartilhe o `CLAUDE.md` (decisões de design + regras absolutas)

Esses 3 juntos dão todo o contexto necessário.
