from __future__ import annotations

import os
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

from functools import wraps

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from .modelos import PERFIS_VALIDOS, PERFIL_ADMIN, PERFIL_GERENTE, PERFIL_OPERADOR
from .persistencia import (
    atualizar_usuario,
    buscar_usuario_por_email,
    buscar_usuario_por_id,
    criar_usuario,
    deletar_usuario,
    listar_usuarios,
    registrar_acesso,
    salvar_reset_token,
    buscar_usuario_por_reset_token,
    limpar_reset_token,
)

auth_bp = Blueprint("auth", __name__)


# ── Decorador de perfil ──────────────────────────────────────────────────────

def requer_perfil(*perfis: str):
    """Restringe rota a perfis específicos. Retorna 403 JSON para rotas de API."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/"):
                    return jsonify({"erro": "não autenticado"}), 401
                return redirect(url_for("auth.login"))
            if current_user.perfil not in perfis:
                if request.path.startswith("/api/"):
                    return jsonify({"erro": "sem permissão para esta operação"}), 403
                return render_template("sem_permissao.html"), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ── Rotas de autenticação ────────────────────────────────────────────────────


def _enviar_email_reset(destinatario, nome, link):
    import os, smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    # Conta dedicada do sistema; fallback para EMAIL_REMETENTE se nao configurada
    remetente = os.environ.get('SISTEMA_EMAIL_REMETENTE') or os.environ.get('EMAIL_REMETENTE', '')
    senha_app = os.environ.get('SISTEMA_EMAIL_SENHA_APP') or os.environ.get('EMAIL_SENHA_APP', '')
    if not remetente or not senha_app:
        raise RuntimeError('Configure SISTEMA_EMAIL_REMETENTE e SISTEMA_EMAIL_SENHA_APP no .env')
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Recuperacao de senha - Central de Promocoes ML'
    msg['From'] = remetente
    msg['To'] = destinatario
    html_body = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 20px">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0" style="background:#1a1d27;border:1px solid #2a2d3a;border-radius:12px;padding:36px">
        <tr><td>
          <div style="font-size:16px;font-weight:700;color:#e2e8f0;margin-bottom:4px">Central de Promocoes ML</div>
          <div style="font-size:12px;color:#64748b;margin-bottom:28px">Recuperacao de senha</div>
          <p style="color:#e2e8f0;font-size:14px;margin:0 0 16px">Ola, <strong>{nome}</strong>!</p>
          <p style="color:#94a3b8;font-size:13px;line-height:1.6;margin:0 0 24px">
            Recebemos uma solicitacao de redefinicao de senha para sua conta.<br>
            Clique no botao abaixo para criar uma nova senha. Este link expira em <strong>1 hora</strong>.
          </p>
          <div style="text-align:center;margin:28px 0">
            <a href="{link}" style="background:#facc15;color:#000;text-decoration:none;padding:12px 32px;border-radius:8px;font-weight:700;font-size:14px;display:inline-block">
              Redefinir minha senha
            </a>
          </div>
          <p style="color:#64748b;font-size:11px;margin:24px 0 0;line-height:1.6">
            Se voce nao solicitou a troca de senha, ignore este e-mail. Nenhuma alteracao sera feita.
          </p>
          <hr style="border:none;border-top:1px solid #2a2d3a;margin:20px 0">
          <p style="color:#475569;font-size:10px;margin:0">
            Se o botao nao funcionar, copie e cole este link no navegador:<br>
            <span style="color:#3b82f6">{link}</span>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>'''
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.ehlo()
        server.starttls()
        server.login(remetente, senha_app)
        server.sendmail(remetente, destinatario, msg.as_string())


@auth_bp.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    mensagem = None
    erro = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        usuario = buscar_usuario_por_email(email)
        if usuario and usuario.ativo:
            token = secrets.token_urlsafe(32)
            expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            salvar_reset_token(usuario.id, token, expiry)
            link = url_for('auth.resetar_senha', token=token, _external=True)
            try:
                _enviar_email_reset(usuario.email, usuario.nome, link)
            except Exception as e:
                erro = f'Erro ao enviar e-mail: {e}'
        if not erro:
            mensagem = 'Se este e-mail estiver cadastrado, voce recebera as instrucoes em breve. Verifique sua caixa de entrada (e spam).'
    return render_template('esqueci_senha.html', mensagem=mensagem, erro=erro)


@auth_bp.route('/resetar-senha/<token>', methods=['GET', 'POST'])
def resetar_senha(token):
    if current_user.is_authenticated:
        logout_user()
    usuario = buscar_usuario_por_reset_token(token)
    if not usuario or not usuario.reset_token_expiry:
        return render_template('resetar_senha.html', token_invalido=True)
    expiry = usuario.reset_token_expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expiry:
        limpar_reset_token(usuario.id)
        return render_template('resetar_senha.html', token_expirado=True)
    erro = None
    if request.method == 'POST':
        nova = request.form.get('nova_senha', '').strip()
        confirma = request.form.get('confirma_senha', '').strip()
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif nova != confirma:
            erro = 'As senhas nao coincidem.'
        else:
            atualizar_usuario(
                usuario_id=usuario.id,
                senha_hash=generate_password_hash(nova),
                primeiro_login=False,
            )
            limpar_reset_token(usuario.id)
            return render_template('resetar_senha.html', sucesso=True)
    return render_template('resetar_senha.html', token=token, erro=erro, nome=usuario.nome)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    erro = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")
        usuario = buscar_usuario_por_email(email)
        if usuario and usuario.ativo and check_password_hash(usuario.senha_hash, senha):
            login_user(usuario, remember=True)
            registrar_acesso(usuario.id)
            if usuario.primeiro_login:
                return redirect(url_for("auth.trocar_senha"))
            return redirect(url_for("index"))
        erro = "E-mail ou senha incorretos."

    return render_template("login.html", erro=erro)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))



@auth_bp.route('/trocar-senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    erro = None
    if request.method == 'POST':
        nova = request.form.get('nova_senha', '').strip()
        confirma = request.form.get('confirma_senha', '').strip()
        if len(nova) < 6:
            erro = 'A senha deve ter pelo menos 6 caracteres.'
        elif nova != confirma:
            erro = 'As senhas nao coincidem.'
        else:
            atualizar_usuario(
                usuario_id=current_user.id,
                senha_hash=generate_password_hash(nova),
                primeiro_login=False,
            )
            return redirect(url_for('index'))
    return render_template('trocar_senha.html', erro=erro)




# ── Gerenciamento de usuários (só Admin) ─────────────────────────────────────

@auth_bp.route("/api/usuarios", methods=["GET"])
@login_required
@requer_perfil(PERFIL_ADMIN)
def listar():
    usuarios = listar_usuarios()
    return jsonify([
        {
            "id":            u.id,
            "nome":          u.nome,
            "email":         u.email,
            "perfil":        u.perfil,
            "ativo":         u.ativo,
            "criado_em":     u.criado_em.isoformat() if u.criado_em else None,
            "ultimo_acesso": u.ultimo_acesso.isoformat() if u.ultimo_acesso else None,
        }
        for u in usuarios
    ])


@auth_bp.route("/api/usuarios", methods=["POST"])
@login_required
@requer_perfil(PERFIL_ADMIN)
def criar():
    dados = request.get_json(silent=True) or {}
    nome  = (dados.get("nome") or "").strip()
    email = (dados.get("email") or "").strip()
    senha = (dados.get("senha") or "").strip()
    perfil = (dados.get("perfil") or PERFIL_OPERADOR).strip()

    if not nome or not email or not senha:
        return jsonify({"erro": "nome, email e senha são obrigatórios"}), 400
    if perfil not in PERFIS_VALIDOS:
        return jsonify({"erro": f"perfil inválido — use: {', '.join(PERFIS_VALIDOS)}"}), 400
    if buscar_usuario_por_email(email):
        return jsonify({"erro": "e-mail já cadastrado"}), 409

    usuario = criar_usuario(
        nome=nome,
        email=email,
        senha_hash=generate_password_hash(senha),
        perfil=perfil,
    )
    return jsonify({"id": usuario.id, "mensagem": "usuário criado"}), 201


@auth_bp.route("/api/usuarios/<int:uid>", methods=["PUT"])
@login_required
@requer_perfil(PERFIL_ADMIN)
def atualizar(uid: int):
    dados = request.get_json(silent=True) or {}
    senha = (dados.get("senha") or "").strip()
    ok = atualizar_usuario(
        usuario_id=uid,
        nome=dados.get("nome"),
        perfil=dados.get("perfil"),
        ativo=dados.get("ativo"),
        senha_hash=generate_password_hash(senha) if senha else None,
    )
    if not ok:
        return jsonify({"erro": "usuário não encontrado"}), 404
    return jsonify({"mensagem": "atualizado"})


@auth_bp.route("/api/usuarios/<int:uid>", methods=["DELETE"])
@login_required
@requer_perfil(PERFIL_ADMIN)
def desativar(uid: int):
    if uid == current_user.id:
        return jsonify({"erro": "você não pode desativar sua própria conta"}), 400
    ok = atualizar_usuario(usuario_id=uid, ativo=False)
    if not ok:
        return jsonify({"erro": "usuário não encontrado"}), 404
    return jsonify({"mensagem": "usuário desativado"})


@auth_bp.route("/api/usuarios/<int:uid>/remover", methods=["DELETE"])
@login_required
@requer_perfil(PERFIL_ADMIN)
def remover(uid: int):
    """Remove permanentemente o usuário. Não é possível remover a si mesmo."""
    if uid == current_user.id:
        return jsonify({"erro": "você não pode remover sua própria conta"}), 400
    ok = deletar_usuario(uid)
    if not ok:
        return jsonify({"erro": "usuário não encontrado"}), 404
    return jsonify({"mensagem": "usuário removido"})


@auth_bp.route("/api/auth/me")
@login_required
def me():
    return jsonify({
        "id":    current_user.id,
        "nome":  current_user.nome,
        "email": current_user.email,
        "perfil": current_user.perfil,
    })
