# Central de Promoções ML + MVP Buybox

Automação Python que **(a)** analisa campanhas da Central de Promoções do Mercado Livre e decide aceitar/recusar com base no RC e **(b)** monitora buybox e pricing dos seus anúncios, sugere preço ótimo e dispara alertas por e-mail.

Os dois módulos compartilham credenciais, fórmulas de PDV e o dashboard local.

> 📋 **Histórico de mudanças**: ver [`CHANGELOG.md`](./CHANGELOG.md). Toda mudança relevante deve ser registrada lá e refletida aqui.

---

## Estrutura

```
automacao-campanhas-ml/
├── main.py                       # CLI da Central de Promoções
├── scheduler.py                  # Loop horário do MVP Buybox
├── server.py                     # Flask: /api/campaigns + /api/buybox/*
├── dashboard.html                # Painel com duas abas: Campanhas + Buybox
│
├── config/
│   ├── settings.yaml             # rc_minimo, dry_run, custos, buybox.*
│   ├── skus.yaml                 # CMV + peso + tipo_anuncio por SKU
│   └── frete_tabela.yaml         # Tabela de frete preço × peso
│
├── src/
│   ├── ml_client.py              # OAuth ML + endpoints (itens, campanhas, catálogo)
│   ├── pdv.py                    # Loader de skus.yaml
│   ├── margem.py                 # Fórmula PDV (comissão, frete, RC)
│   ├── decisor.py                # ACEITAR/RECUSAR por RC mínimo
│   ├── notificador.py            # Log JSON-lines + e-mail das campanhas
│   ├── runner.py                 # Orquestrador da automação de campanhas
│   │
│   ├── buybox/                   # MVP Buybox
│   │   ├── modelos.py            # ORM + dataclasses de domínio
│   │   ├── persistencia.py       # SQLite via SQLAlchemy + helpers CRUD + migrações
│   │   ├── catalogo.py           # Top 5 do catálogo público + nosso_preco_efetivo
│   │   ├── pricing.py            # calcular_preco_otimo, calcular_preco_candidato,
│   │   │                         # _calcular_rebate_valor, rebate_aplicavel
│   │   └── coletor.py            # CLI da coleta (chamado pelo scheduler)
│   │
│   └── alertas/                  # Camada de alertas do Buybox
│       ├── regras.py             # A1/A2/A3 (críticos) + B1/B2/B3 (resumo)
│       ├── templates.py          # HTML inline dos e-mails
│       ├── email.py              # SMTP+TLS
│       └── avaliador.py          # Orquestra regras + cooldown + envio
│
├── tests/                        # pytest — 62 testes cobrindo lógica de negócio
│   ├── test_pricing.py           # algoritmo + rebate + faixa de campanha
│   ├── test_catalogo.py          # top 5 + visibilidade no catálogo
│   ├── test_persistencia.py      # CRUD + idempotência
│   ├── test_alertas_regras.py    # A1/A2/A3/B1/B2/B3
│   └── test_avaliador.py         # cooldown, dry-run, SMTP
│
├── scripts/                      # Utilitários de operação/diagnóstico
│   ├── enviar_email_teste.py     # Testa SMTP sem tocar no banco
│   ├── diagnosticar_env.py       # Verifica credenciais .env mascaradas
│   ├── inspecionar_item.py       # Inspeciona campos de preço/campanha de um item
│   └── diagnosticar_campanha.py  # Dump completo de uma campanha started
│
├── CHANGELOG.md                  # Histórico cronológico das iterações
├── README.md
└── CLAUDE.md                     # Contexto persistente para futuras sessões
│
├── data/                         # buybox.db gerado em runtime
└── logs/                         # JSON-lines diários
```

---

## Setup local

### 1. Dependências

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Credenciais

Copie `.env.example` para `.env` e preencha. Os tokens são compartilhados com o projeto base (`Projeto automação Mercado livre`).

```env
ML_APP_ID=
ML_CLIENT_SECRET=
ML_ACCESS_TOKEN=
ML_REFRESH_TOKEN=
ML_SELLER_ID=

EMAIL_REMETENTE=seu@gmail.com
EMAIL_SENHA_APP=  # senha de app do Gmail, sem espaços
```

Para gerar a senha de app: ative 2FA na conta e acesse https://myaccount.google.com/apppasswords

Validar com:
```powershell
python -m scripts.diagnosticar_env
python -m scripts.enviar_email_teste
```

### 3. Inicializar banco do Buybox

```powershell
python -c "from src.buybox.persistencia import init_db; init_db()"
```
Cria `data/buybox.db` com as 3 tabelas (`snapshots`, `snapshot_concorrentes`, `alertas`) e aplica as migrações automaticamente em bancos pré-existentes.

---

## Uso

### Módulo de campanhas (legado)

```powershell
python main.py                    # roda com config do settings.yaml
python main.py --dry-run --sku WLK004
python main.py --executar         # ativa aceite real (cuidado)
```

### Módulo de Buybox

**Coleta manual** (1 ciclo):
```powershell
python -m src.buybox.coletor                       # todos os SKUs
python -m src.buybox.coletor --sku WLK004          # filtra
python -m src.buybox.coletor --sku WLK004 --com-alertas
```

**Scheduler** (loop horário):
```powershell
python scheduler.py                   # loop contínuo
python scheduler.py --once            # 1 ciclo e sai
python scheduler.py --once --sku WLK004
python scheduler.py --sem-alertas     # coleta sem disparar e-mail
python scheduler.py --forcar-resumo   # força resumo diário (debug)
python scheduler.py --intervalo 5     # 5 min entre ciclos (testes)
```

**Dashboard local**:
```powershell
python server.py
# abrir http://localhost:5000
```

**Seletor de conta** (topo direito): alterna entre contas ML cadastradas (ex: Best Hair / Hair Pro). Os dados são recarregados automaticamente ao trocar.

**RC mínimo editável**: card RC mínimo tem botão ✏️ que abre editor inline e persiste o novo valor em `settings.yaml` via PUT `/api/rc-minimo`.

**⚙️ Config de SKUs**: botão no header abre modal com tabela editável de `custo`, `peso` e `tipo_anuncio` por SKU. Persiste em `skus.yaml` via PUT `/api/skus`.

Aba **Campanhas**: visão da Central de Promoções com cache de 10 min (paralelo, 3 workers). Indicador de idade do cache na barra. Cada linha é clicável e navega para a aba Buybox com o MLB pré-filtrado.

Aba **Buybox**: lista de anúncios com foto, posição, RC, preço ótimo + filtros (Todos / Com buybox / Em risco / Oportunidade / Off-catálogo / Com estoque) + botão **"Coletar agora ▾"** (dropdown com checkboxes de SKUs) + modal de detalhe com:
  - Header com foto + link "ver no ML →"
  - Snapshot atual + chip de posição destacado
  - Sugestão de preço
  - **Breakdown da margem** linha a linha (preço atual × preço candidato)
  - **Top 5 concorrentes** com vendas do seller, **RC pra vencer** e **prazo de entrega para SP** (lazy load)
  - **Campanhas disponíveis** (lazy load — consulta ao ML em tempo real) com vigência + badge "★ MELHOR"; botões **ACEITAR ↗** / **RECUSAR ↗** abrem a Central de Promoções ML filtrada pelo MLB em nova aba
  - Gráficos de posição com filtros **24h / 7d / 30d / Personalizado**
  - **Gráfico Preço × Vendas** dual-eixo com seletor: Preço + Vendas / Top 5 concorrentes; blocos de resumo 7d acima (unidades, receita, ticket médio)
  - Foto do anúncio com badge azul mostrando nº de campanhas `ACEITAR` disponíveis

---

## Como o módulo de Buybox funciona

```
Para cada SKU em config/skus.yaml
  → Resolve os MLBs via API ML
  → Para cada MLB:
      • busca campanhas started (preço efetivo se houver desconto)
      • monta top 5 do catálogo público (/products/{id}/items)
      • identifica nossa posição e se temos buybox
      • calcula margem atual + sugere preço ótimo (R$0,10 abaixo do 2º
        respeitando RC ≥ rc_minimo)
  → Salva snapshot no SQLite + linha por concorrente
  → Avalia A1/A2/A3 comparando com snapshot anterior
  → Se passar do horário do resumo diário (default 8h), envia B1/B2/B3
```

### Algoritmo de pricing

```
Se temos buybox (1º colocado):
  → candidato = preço_2º - R$ 0,10  (subir, defender buybox)
  → se diff < ruído (R$ 1): mantém preço
  → se RC no candidato < rc_minimo: NÃO sugere

Se estamos fora do buybox:
  → candidato = preço_1º - R$ 0,10  (retomar buybox)
  → se RC no candidato < rc_minimo: NÃO sugere

Casos especiais:
  - Único vendedor → mantém preço
  - Empate em 1º → ML não dá buybox; sugere descer mesmo assim
  - Anúncio off-catálogo (pausado/sem estoque) → não sugere
```

### Como o rebate ML é tratado

O ML calcula o rebate como **valor fixo em R$** = `original_price × meli_percentage / 100`. Não recalcula com base no preço atual. O sistema aplica a regra:

1. **Sem rebate na campanha** → não há nada a aplicar
2. **Campanha SMART/DEAL com faixa** (`min_price`/`max_price` da API) → preço precisa estar dentro da faixa
3. **Campanha externa sem faixa** (SELLER_CAMPAIGN típica) → rebate só vale exatamente no preço onde a campanha está aplicada. Mudar de preço significa sair dela e entrar na sua própria campanha (sem rebate ML)
4. **Snapshot antigo sem `preco_aplicado`** → compatibilidade: aplica o rebate

### Regras de alerta

**Críticos** (e-mail imediato, com cooldown anti-spam):

| Tipo | Disparo | Cooldown |
|---|---|---|
| **A1** Perdi buybox | tinha buybox no snapshot anterior, perdeu agora | 6h |
| **A2** Ameaça | tenho buybox, mas 2º está ≤ 2% acima do meu preço | 2h |
| **A3** Concorrente sumiu | seller do top 3 ausente em 2 ciclos consecutivos | 4h |

**Resumo diário** (1 e-mail consolidado às 8h):

| Tipo | Disparo |
|---|---|
| **B1** Problema | anúncio com status != active OU sem estoque no último snapshot |
| **B2** Margem baixa | margem < 20% em ≥ 50% dos snapshots do dia |
| **B3** Oportunidade subir | tem buybox + RC > 70% + preço ótimo > preço atual |

### Cores do dashboard

A célula **Margem** e o **RC** seguem a mesma escala:
- RC ≥ 60% → verde
- 50% ≤ RC < 60% → amarelo
- RC < 50% → vermelho

A coluna **Posição** usa chip destacado:
- 1º com buybox → verde com glow + "BUYBOX"
- 2º ou 3º sem buybox → amarelo
- 4º+ → vermelho
- Off-catálogo → cinza

---

## Configuração

Tudo em `config/settings.yaml`. Trechos relevantes:

```yaml
rc_minimo: 60.0                      # RC mínimo para aceitar campanha e sugerir preço

buybox:
  intervalo_coleta_minutos: 60
  cooldown_a1_horas: 6               # perdi buybox
  cooldown_a2_horas: 2               # ameaça
  cooldown_a3_horas: 4               # concorrente sumiu
  margem_minima_b2_pct: 20.0
  rc_oportunidade_b3_pct: 70.0
  diferenca_ruido_rs: 1.00
  passo_abaixo_rs: 0.10
  ciclos_confirmacao_a3: 2
  hora_resumo_diario: 8
  db_path: "data/buybox.db"
  fonte_preco: "suggested_price"     # ou "preco_atual"
  cep_referencia: "01310100"         # CEP usado no cálculo de prazo do top 5 (Av. Paulista)

  email:
    enabled: true                    # alertas ativos — desative com false se necessário
    smtp_host: smtp.gmail.com
    smtp_port: 587
    remetente_env: EMAIL_REMETENTE
    senha_env: EMAIL_SENHA_APP
    destinatarios:
      - luiz.pimentel@kamico.com.br
```

---

## Endpoints HTTP

| Método | URL | Descrição |
|---|---|---|
| GET | `/api/health` | Sanidade |
| GET | `/api/campaigns?conta=` | Estado das campanhas (legado) — cache 10 min, `?force=true` para ignorar |
| GET/PUT | `/api/rc-minimo` | Lê ou grava o RC mínimo em `settings.yaml` (PUT: `{"rc_minimo": 65.0}`) |
| GET/PUT | `/api/skus` | Lê ou grava custo/peso/tipo_anuncio por SKU em `skus.yaml` |
| GET | `/api/buybox/lista` | Último snapshot de cada (SKU, MLB) + summary |
| GET | `/api/buybox/sku/<sku>?item_id=...&periodo=24h\|7d\|30d` | Detalhe + top 5 (com RC pra vencer) + histórico no período + série de preços + **breakdown da margem**. Aceita `?desde=...&ate=...` ISO |
| GET | `/api/buybox/sku/<sku>/campanhas?item_id=...` | Campanhas started + candidatas (LIVE no ML) com vigência e flag `melhor_rc` |
| GET | `/api/buybox/sku/<sku>/prazos?item_id=...` | Prazo de entrega de cada concorrente do top 5 para o CEP de referência (LIVE no ML) |
| GET | `/api/buybox/sku/<sku>/vendas?item_id=...&periodo=7d` | Histórico de vendas alinhado com snapshots (unidades + receita por janela de tempo) |
| GET | `/api/buybox/sku/<sku>/alertas` | Histórico de alertas dos últimos 7 dias |
| GET | `/api/buybox/skus-configurados` | Lista SKUs do `skus.yaml` (alimenta o dropdown do botão "Coletar agora") |
| POST | `/api/buybox/forcar-coleta` | Dispara coleta on-demand (body opcional `{"skus":["WLK004"]}`) |

---

## Schema do banco

3 tabelas no SQLite (`data/buybox.db`):

**snapshots** — estado por (sku, item_id, momento). Campos relevantes:
- `preco_atual`, `preco_cheio`, `preco_1o`, `preco_2o`, `diff_para_*`
- `nossa_posicao`, `tem_buybox`, `qtd_concorrentes`
- `margem_atual_pct`, `rc_atual_pct`
- `preco_otimo_sugerido`, `rc_no_preco_otimo`, `motivo_sugestao`
- `campanha_ativa_id`, `campanha_ativa_nome`, `rebate_pct`
- `campanha_min_price`, `campanha_max_price`, `campanha_original_price`
- `campanha_start_date`, `campanha_finish_date` (vigência)
- `custo` (CMV no momento da coleta — para auditoria)
- `thumbnail_url`, `reviews_rating`, `reviews_total` (relevância do produto)
- `visivel_no_catalogo`, `status_anuncio`, `estoque_proprio`, `is_full`
- Constraint única em `(sku, item_id, coletado_em)` — idempotente

**snapshot_concorrentes** — top 5 por snapshot. Linha por seller no catálogo. Inclui `total_vendas` (transactions.total da API ML) e `prazo_entrega_dias` (preenchido sob demanda via endpoint `/prazos`).

**alertas** — auditoria completa. Inclui alertas enviados E suprimidos (com `motivo_supressao` no JSON de `dados`).

Migrações: `init_db()` aplica `ALTER TABLE` automaticamente para bancos pré-existentes (a função `_migrar_schema` em `persistencia.py` é idempotente).

---

## Testes

```powershell
pytest tests/ -v
```

**62 testes** cobrindo:
- `test_pricing.py` — algoritmo de preço ótimo (todos os ramos) + heurística do rebate em 4 camadas + cálculo sobre original_price
- `test_catalogo.py` — montagem do top 5 e fonte do preço efetivo
- `test_persistencia.py` — CRUD básico e idempotência
- `test_alertas_regras.py` — regras A1/A2/A3/B1/B2/B3
- `test_avaliador.py` — cooldown, dry-run, falha SMTP

---

## Deploy em AWS

Recomendação para tirar o projeto da máquina local. Os passos abaixo cobrem o caminho mais simples (1 EC2 + SQLite) — adequado para o volume atual (~25 SKUs, 1 ciclo/h). Para volume maior, migre para Postgres no RDS (vide seção final).

### Arquitetura sugerida

```
┌─────────────────────────────────────────────────────────────┐
│  EC2 t3.small (Linux Ubuntu 24.04)                          │
│                                                              │
│   systemd unit ─────► python scheduler.py  (loop horário)   │
│   systemd unit ─────► gunicorn server:app  (porta 5000)     │
│                                                              │
│   /opt/buybox/  ──── código + .env + data/buybox.db         │
│   /opt/buybox/logs/ ── JSON-lines diários                   │
│                                                              │
│   Nginx (opcional) ── porta 80 → 5000                       │
│   Security Group ─── HTTPS via Cloudflare/ALB               │
└─────────────────────────────────────────────────────────────┘
              ▲
              │  acesso a outbound: api.mercadolibre.com, smtp.gmail.com
              ▼
        Mercado Livre + Gmail SMTP
```

### Passos

#### 1. Provisionar EC2
- Instância: **t3.small** (2 vCPU, 2 GB) — suficiente
- AMI: Ubuntu Server 24.04 LTS
- Storage: 20 GB gp3
- Security Group: permite SSH (22) só do seu IP e HTTPS (443) público se for expor o dashboard

#### 2. Setup inicial (SSH)
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git nginx
sudo mkdir -p /opt/buybox && sudo chown ubuntu:ubuntu /opt/buybox

# Clone ou rsync do projeto
cd /opt/buybox
git clone <SEU_REPO> .   # ou: rsync da máquina local

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install gunicorn   # servir o Flask em produção
```

#### 3. Credenciais (sem checkar `.env` no Git)

Crie `/opt/buybox/.env` manualmente com os tokens ML + Gmail. **Alternativa mais segura**: usar AWS Secrets Manager / SSM Parameter Store e carregar via `boto3` em um wrapper. Para começar, o `.env` direto serve.

```bash
chmod 600 /opt/buybox/.env   # só o owner lê
```

#### 4. Inicializar banco
```bash
cd /opt/buybox
.venv/bin/python -c "from src.buybox.persistencia import init_db; init_db()"
```

#### 5. systemd: scheduler como serviço

`/etc/systemd/system/buybox-scheduler.service`:
```ini
[Unit]
Description=MVP Buybox — scheduler horário
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/buybox
EnvironmentFile=/opt/buybox/.env
ExecStart=/opt/buybox/.venv/bin/python scheduler.py
Restart=always
RestartSec=10
StandardOutput=append:/opt/buybox/logs/scheduler.out
StandardError=append:/opt/buybox/logs/scheduler.err

[Install]
WantedBy=multi-user.target
```

#### 6. systemd: dashboard (gunicorn)

`/etc/systemd/system/buybox-server.service`:
```ini
[Unit]
Description=MVP Buybox — dashboard Flask
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/buybox
EnvironmentFile=/opt/buybox/.env
ExecStart=/opt/buybox/.venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 server:app
Restart=always

[Install]
WantedBy=multi-user.target
```

#### 7. Ativar e validar
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now buybox-scheduler buybox-server

sudo systemctl status buybox-scheduler   # deve estar "active (running)"
sudo systemctl status buybox-server

journalctl -u buybox-scheduler -f         # acompanha logs em tempo real
```

#### 8. Nginx (acesso ao dashboard)

`/etc/nginx/sites-available/buybox`:
```nginx
server {
    listen 80;
    server_name buybox.seu-dominio.com.br;

    # Autenticação básica (proteja o painel; ou use Cloudflare Access)
    auth_basic "Painel Buybox";
    auth_basic_user_file /etc/nginx/.htpasswd_buybox;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/buybox /etc/nginx/sites-enabled/
sudo htpasswd -c /etc/nginx/.htpasswd_buybox luiz
sudo nginx -t && sudo systemctl reload nginx
```

Para HTTPS: use **Let's Encrypt** (`sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx`) ou ponha o EC2 atrás do **Cloudflare** (mais simples, SSL automático).

### Pontos de atenção

#### Timezone
EC2 Ubuntu vem em UTC. Ajuste:
```bash
sudo timedatectl set-timezone America/Sao_Paulo
```
Importante porque o `hora_resumo_diario: 8` no settings.yaml usa hora **local**.

#### Backup do banco
SQLite é arquivo único. Faça snapshot diário com cron:
```cron
0 3 * * * cp /opt/buybox/data/buybox.db /opt/buybox/data/buybox.db.$(date +\%Y\%m\%d) && find /opt/buybox/data/buybox.db.* -mtime +14 -delete
```
Ou (melhor): sync diário para um bucket S3.

#### Quando migrar para RDS Postgres
O SQLite cobre tranquilo até ~100 SKUs / ciclos horários. Quando passar disso ou precisar de múltiplos serviços lendo ao mesmo tempo:

1. Provisione RDS Postgres (`db.t4g.micro` já basta)
2. Mude `db_path` no `settings.yaml` para uma URL de conexão (`postgresql://user:pass@host/db`)
3. Ajuste `get_engine()` em `persistencia.py` para detectar e usar a URL
4. Rode `init_db()` no banco novo — SQLAlchemy cria tudo do zero
5. Migração de dados: `pgloader` faz SQLite→Postgres sem dor

A camada de persistência foi feita justamente para essa troca ser indolor — todas as queries usam SQLAlchemy ORM, não SQL puro.

#### Credenciais (recomendado)
Em vez de `.env` em arquivo, use **AWS Secrets Manager**:
- ML_APP_ID, ML_CLIENT_SECRET, ML_REFRESH_TOKEN, EMAIL_SENHA_APP → secret
- IAM role da EC2 com permissão `secretsmanager:GetSecretValue`
- Wrapper Python que popula os env vars no boot

Vale o esforço quando o time crescer; para uso individual, `.env` com `chmod 600` é aceitável.

#### Custo aproximado (us-east-1)
- t3.small + 20 GB gp3: ~**US$ 17/mês**
- Tráfego: ~US$ 0 (volume desprezível)
- RDS db.t4g.micro (se migrar): +~US$ 13/mês
- **Total esperado**: US$ 17–35/mês

---

## Logs

Cada execução grava em `logs/buybox-YYYY-MM-DD.log` (JSON-lines):

```json
{"evento":"snapshot_salvo","sku":"WLK004","item_id":"MLB4422127791","preco_atual":317.33,"tem_buybox":true,"preco_otimo_sugerido":332.9,"ts":"..."}
{"evento":"alerta_a1_enviado","sku":"WLK004","destinatarios":["..."],"ts":"..."}
{"evento":"ciclo_fim","duracao_s":12.4,"coleta":{...},"alertas":{...}}
```

Também escreve `data/.ultimo_resumo_diario` (stamp YYYY-MM-DD) para evitar envio duplicado do resumo após reinício.

---

## Limitações conhecidas

- **Pricing automático via API ainda não implementado** — o sistema só sugere; quem altera é o operador.
- **Custo via Tiny ERP não integrado** — vem do YAML; trocar requer 1 PR.
- **Sem Telegram** — só e-mail.
- **Histórico do dashboard limitado a 24h** — backend pode estender para 7d trocando o método `snapshots_24h`.
- **Frontend depende de `localhost:5000` hardcoded** — ajustar `API` no JS para deploy externo, ou servir dashboard pelo próprio Flask (já é o caso) e usar URL relativa.

---

## Roadmap

- [ ] Aceite automático de campanha (`runner._aceitar_campanha`)
- [ ] PUT /items/{id} para alterar preço diretamente
- [ ] Custo dinâmico via Tiny ERP
- [ ] Notificação Telegram (`notificador.py` já tem stub)
- [ ] Categorização de SKUs por marca/grupo no dashboard
- [ ] Detecção de tendência (subida/queda de preço dos concorrentes)
- [ ] Multi-tenant: vários sellers no mesmo deploy
