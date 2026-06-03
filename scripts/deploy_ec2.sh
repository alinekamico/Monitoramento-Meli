#!/usr/bin/env bash
# deploy_ec2.sh — instala/atualiza o Buybox na EC2 Ubuntu
#
# Uso (rodar NA EC2, dentro da pasta do projeto copiado):
#   chmod +x scripts/deploy_ec2.sh
#   sudo bash scripts/deploy_ec2.sh
#
# O que faz:
#   1. Instala dependências do sistema (python3-venv, pip)
#   2. Copia arquivos para /opt/buybox
#   3. Cria virtualenv e instala requirements.txt
#   4. Instala serviços systemd (scheduler + server)
#   5. Orienta sobre configuração do .env

set -euo pipefail

DEPLOY_DIR="/opt/buybox"
VENV_DIR="$DEPLOY_DIR/venv"
SERVICE_USER="ubuntu"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Buybox EC2 Deploy ==="
echo "Origem : $PROJECT_DIR"
echo "Destino: $DEPLOY_DIR"
echo ""

# 1. Dependências do sistema
echo "[1/5] Instalando dependências do sistema..."
apt-get update -q
apt-get install -y -q python3-venv python3-pip rsync

# 2. Criar diretório de deploy
echo "[2/5] Sincronizando arquivos..."
mkdir -p "$DEPLOY_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DEPLOY_DIR"

rsync -av --delete \
  --exclude='.env' \
  --exclude='data/buybox.db' \
  --exclude='logs/' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='venv' \
  --exclude='.git' \
  "$PROJECT_DIR/" "$DEPLOY_DIR/"

chown -R "$SERVICE_USER:$SERVICE_USER" "$DEPLOY_DIR"

# 3. Virtualenv e dependências Python
echo "[3/5] Criando virtualenv e instalando pacotes..."
sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$DEPLOY_DIR/requirements.txt" -q

# Cria diretórios necessários em runtime
mkdir -p "$DEPLOY_DIR/logs" "$DEPLOY_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DEPLOY_DIR/logs" "$DEPLOY_DIR/data"

# 4. Serviços systemd
echo "[4/5] Instalando serviços systemd..."
cp "$DEPLOY_DIR/systemd/buybox-scheduler.service" /etc/systemd/system/
cp "$DEPLOY_DIR/systemd/buybox-server.service"    /etc/systemd/system/
systemctl daemon-reload
systemctl enable buybox-scheduler buybox-server

# 5. .env
echo "[5/5] Verificando .env..."
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
  chown "$SERVICE_USER:$SERVICE_USER" "$DEPLOY_DIR/.env"
  chmod 600 "$DEPLOY_DIR/.env"
  echo ""
  echo "⚠️  ATENÇÃO: .env criado a partir do .env.example."
  echo "   Edite $DEPLOY_DIR/.env e preencha:"
  echo "     DATABASE_URL=mysql+pymysql://user:senha@endpoint-rds:3306/buybox"
  echo "     ML_APP_ID, ML_CLIENT_SECRET, ML_ACCESS_TOKEN, ML_REFRESH_TOKEN"
  echo "     EMAIL_REMETENTE, EMAIL_SENHA_APP"
  echo ""
  echo "   Depois rode:"
  echo "     sudo systemctl start buybox-scheduler buybox-server"
else
  echo "   .env já existe — não sobrescrito."
  echo ""
  echo "Reiniciando serviços..."
  systemctl restart buybox-scheduler buybox-server
  echo ""
  echo "Status:"
  systemctl is-active buybox-scheduler && echo "  buybox-scheduler: ativo" || echo "  buybox-scheduler: INATIVO"
  systemctl is-active buybox-server    && echo "  buybox-server:    ativo" || echo "  buybox-server:    INATIVO"
fi

echo ""
echo "=== Deploy concluído ==="
echo ""
echo "Comandos úteis na EC2:"
echo "  sudo journalctl -u buybox-scheduler -f    # logs do scheduler"
echo "  sudo journalctl -u buybox-server -f       # logs do dashboard"
echo "  sudo systemctl restart buybox-scheduler   # reiniciar scheduler"
echo "  curl http://localhost:5000                 # testar dashboard"
