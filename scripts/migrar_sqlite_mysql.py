"""
Migração única: copia dados do SQLite local para o RDS MySQL.

Uso (rodar UMA VEZ, antes de ligar o scheduler na AWS):

    # Na máquina local, com DATABASE_URL apontando para o RDS:
    set DATABASE_URL=mysql+pymysql://user:senha@endpoint-rds:3306/buybox
    python -m scripts.migrar_sqlite_mysql

O script:
  1. Conecta ao SQLite local (db_path do settings.yaml)
  2. Conecta ao MySQL via DATABASE_URL
  3. Cria as tabelas no MySQL (CREATE TABLE IF NOT EXISTS)
  4. Copia snapshots → snapshot_concorrentes → alertas, mantendo integridade referencial
  5. Exibe contagem final para conferência

Seguro rodar mais de uma vez: duplicatas na constraint unique são ignoradas.
"""

import os
import sys
from pathlib import Path

# Garante que o projeto está no sys.path independente de onde é chamado
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERRO: defina DATABASE_URL no .env antes de rodar.")
    sys.exit(1)

import yaml

_CONFIG = _ROOT / "config" / "settings.yaml"
with open(_CONFIG, encoding="utf-8") as f:
    _cfg = yaml.safe_load(f)

_db_path_str = _cfg.get("buybox", {}).get("db_path", "data/buybox.db")
_db_path = Path(_db_path_str) if Path(_db_path_str).is_absolute() else _ROOT / _db_path_str

if not _db_path.exists():
    print(f"ERRO: SQLite não encontrado em {_db_path}")
    sys.exit(1)

from src.buybox.modelos import Base

# ── engines ────────────────────────────────────────────────────────────────
eng_sqlite = create_engine(f"sqlite:///{_db_path}", future=True)

# Temporariamente remove DATABASE_URL do ambiente para que get_engine() não
# interfira, criamos a engine MySQL diretamente aqui.
eng_mysql = create_engine(DATABASE_URL, future=True)

# Cria schema completo no MySQL (idempotente)
Base.metadata.create_all(eng_mysql)

Session_sqlite = sessionmaker(bind=eng_sqlite)
Session_mysql  = sessionmaker(bind=eng_mysql)

# ── leitura do SQLite ───────────────────────────────────────────────────────
print("Lendo dados do SQLite...")

with eng_sqlite.connect() as conn:
    snapshots    = conn.execute(text("SELECT * FROM snapshots ORDER BY id")).mappings().all()
    concorrentes = conn.execute(text("SELECT * FROM snapshot_concorrentes ORDER BY id")).mappings().all()
    alertas      = conn.execute(text("SELECT * FROM alertas ORDER BY id")).mappings().all()

print(f"  {len(snapshots)} snapshots")
print(f"  {len(concorrentes)} concorrentes")
print(f"  {len(alertas)} alertas")

# ── escrita no MySQL ────────────────────────────────────────────────────────
print("\nInserindo no MySQL...")

# Mapeia id antigo (SQLite) → id novo (MySQL) para corrigir snapshot_id dos concorrentes
mapa_ids: dict[int, int] = {}

with Session_mysql() as s:
    for snap in snapshots:
        d = dict(snap)
        antigo_id = d.pop("id")
        try:
            resultado = s.execute(
                text("""
                    INSERT IGNORE INTO snapshots
                    (sku, item_id, coletado_em, preco_atual, nossa_posicao, tem_buybox,
                     status_anuncio, estoque_proprio, is_full, tipo_anuncio,
                     preco_1o, preco_2o, diff_para_1o_rs, diff_para_1o_pct,
                     diff_para_2o_rs, diff_para_2o_pct, qtd_concorrentes,
                     campanha_ativa_id, campanha_ativa_nome, rebate_pct,
                     campanha_min_price, campanha_max_price, campanha_original_price,
                     custo, margem_atual_pct, rc_atual_pct,
                     preco_otimo_sugerido, rc_no_preco_otimo, motivo_sugestao,
                     titulo, url_anuncio, visivel_no_catalogo, preco_cheio)
                    VALUES
                    (:sku, :item_id, :coletado_em, :preco_atual, :nossa_posicao, :tem_buybox,
                     :status_anuncio, :estoque_proprio, :is_full, :tipo_anuncio,
                     :preco_1o, :preco_2o, :diff_para_1o_rs, :diff_para_1o_pct,
                     :diff_para_2o_rs, :diff_para_2o_pct, :qtd_concorrentes,
                     :campanha_ativa_id, :campanha_ativa_nome, :rebate_pct,
                     :campanha_min_price, :campanha_max_price, :campanha_original_price,
                     :custo, :margem_atual_pct, :rc_atual_pct,
                     :preco_otimo_sugerido, :rc_no_preco_otimo, :motivo_sugestao,
                     :titulo, :url_anuncio, :visivel_no_catalogo, :preco_cheio)
                """),
                d,
            )
            novo_id = resultado.lastrowid
            if novo_id:
                mapa_ids[antigo_id] = novo_id
        except Exception as e:
            print(f"  [AVISO] snapshot id={antigo_id}: {e}")
    s.commit()

print(f"  {len(mapa_ids)} snapshots inseridos (restantes já existiam)")

with Session_mysql() as s:
    inseridos_conc = 0
    for c in concorrentes:
        d = dict(c)
        d.pop("id")
        antigo_snap_id = d["snapshot_id"]
        novo_snap_id = mapa_ids.get(antigo_snap_id)
        if novo_snap_id is None:
            continue  # snapshot pai não foi inserido (já existia) — pular
        d["snapshot_id"] = novo_snap_id
        try:
            s.execute(
                text("""
                    INSERT IGNORE INTO snapshot_concorrentes
                    (snapshot_id, posicao, seller_id, seller_nome, preco,
                     tipo_envio, frete_gratis, reputacao, url_anuncio, e_nos)
                    VALUES
                    (:snapshot_id, :posicao, :seller_id, :seller_nome, :preco,
                     :tipo_envio, :frete_gratis, :reputacao, :url_anuncio, :e_nos)
                """),
                d,
            )
            inseridos_conc += 1
        except Exception as e:
            print(f"  [AVISO] concorrente: {e}")
    s.commit()

print(f"  {inseridos_conc} concorrentes inseridos")

with Session_mysql() as s:
    inseridos_al = 0
    for al in alertas:
        d = dict(al)
        d.pop("id")
        try:
            s.execute(
                text("""
                    INSERT IGNORE INTO alertas
                    (sku, item_id, tipo, disparado_em, enviado_em, dados)
                    VALUES
                    (:sku, :item_id, :tipo, :disparado_em, :enviado_em, :dados)
                """),
                d,
            )
            inseridos_al += 1
        except Exception as e:
            print(f"  [AVISO] alerta: {e}")
    s.commit()

print(f"  {inseridos_al} alertas inseridos")

# ── verificação final ───────────────────────────────────────────────────────
print("\nVerificação no MySQL:")
with eng_mysql.connect() as conn:
    for tabela in ("snapshots", "snapshot_concorrentes", "alertas"):
        total = conn.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()
        print(f"  {tabela}: {total} registros")

print("\nMigração concluída.")
