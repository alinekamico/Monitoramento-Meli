"""
Templates HTML simples para os e-mails de alerta.

Mantém-se intencionalmente minimalista: sem dependência de Jinja, sem
CSS externo, compatível com clientes de e-mail conservadores (Gmail
desktop e mobile, Outlook). Cada função devolve `(assunto, html)`.

Os templates assumem o dict `dados` produzido por
`alertas.regras.AlertaPendente.dados` e
`alertas.regras.avaliar_resumo_diario`.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Optional


_BASE_STYLE = (
    "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', "
    "Roboto, sans-serif; color: #1f2937; line-height: 1.5;"
)
_TABLE_STYLE = (
    "border-collapse: collapse; width: 100%; margin-top: 12px;"
)
_TH_STYLE = (
    "background: #f3f4f6; padding: 8px 12px; text-align: left; "
    "border: 1px solid #e5e7eb; font-size: 13px;"
)
_TD_STYLE = "padding: 8px 12px; border: 1px solid #e5e7eb; font-size: 13px;"


def _formatar_br(valor: Optional[float], casas: int = 2) -> str:
    if valor is None:
        return "—"
    return f"R$ {valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _link(url: Optional[str], texto: str = "Ver no ML") -> str:
    if not url:
        return ""
    return f'<a href="{escape(url)}" style="color:#2563eb;">{escape(texto)}</a>'


def _wrap(corpo_html: str) -> str:
    return (
        f'<div style="{_BASE_STYLE}">'
        f'{corpo_html}'
        '<hr style="border:none; border-top:1px solid #e5e7eb; margin:24px 0 12px;">'
        '<p style="font-size:11px; color:#6b7280;">'
        'MVP Buybox — Central de Promoções ML'
        '</p>'
        '</div>'
    )


def _pct(valor: Optional[float]) -> str:
    """Formata percentual com 1 casa decimal ou '—'."""
    if valor is None:
        return "—"
    return f"{valor:.1f}%"


def _cor_rc(valor: Optional[float], minimo: float = 60.0) -> str:
    if valor is None:
        return "#6b7280"
    return "#16a34a" if valor >= minimo else "#dc2626"


def _bloco_rc(dados: dict) -> str:
    """
    Bloco destacado de RC atual / RC para ganhar / posição / campanha ativa.
    Aparece em todos os alertas A1/A2/A3 logo após o preço.
    """
    rc_atual  = dados.get("rc_atual_pct")
    rc_ganhar = dados.get("rc_no_preco_otimo")
    posicao   = dados.get("nossa_posicao") or dados.get("nossa_posicao_atual")
    campanha  = dados.get("campanha_ativa_nome")

    partes: list[str] = []
    if rc_atual is not None:
        cor = _cor_rc(rc_atual)
        partes.append(
            f'<b>RC atual:</b> '
            f'<span style="color:{cor};font-weight:700">{_pct(rc_atual)}</span>'
        )
    if rc_ganhar is not None:
        partes.append(f'<b>RC p/ ganhar:</b> {_pct(rc_ganhar)}')
    if posicao is not None:
        partes.append(f'<b>Posição:</b> {posicao}º')
    if campanha:
        partes.append(f'<b>Campanha ativa:</b> {escape(str(campanha))}')

    if not partes:
        return ""

    return (
        '<p style="background:#f9fafb; border-left:3px solid #d1d5db; '
        'padding:8px 14px; border-radius:0 4px 4px 0; margin:10px 0; '
        'font-size:13px;">'
        + " &nbsp;&nbsp;|&nbsp;&nbsp; ".join(partes)
        + "</p>"
    )


# ============================================================
# Alertas críticos (A1/A2/A3)
# ============================================================


def template_a1(sku: str, item_id: str, dados: dict) -> tuple[str, str]:
    """A1 — perdi buybox."""
    titulo = dados.get("titulo") or sku
    quem = dados.get("quem_pegou") or {}
    preco_otimo = dados.get("preco_otimo_sugerido")
    rc_otimo = dados.get("rc_no_preco_otimo")
    url = dados.get("url_anuncio")

    assunto = f"[A1] Perdi buybox — {sku} ({item_id})"

    sugestao_html = ""
    if preco_otimo is not None:
        sugestao_html = (
            f'<p><b>Preço ótimo sugerido:</b> {_formatar_br(preco_otimo)} '
            f'(RC esperado {rc_otimo:.1f}%)</p>'
            f'<p style="color:#6b7280;font-size:13px;">'
            f'{escape(dados.get("motivo_sugestao") or "")}</p>'
        )

    quem_html = ""
    if quem.get("seller_nome"):
        quem_html = (
            f'<p><b>Quem pegou:</b> {escape(quem["seller_nome"])} '
            f'@ {_formatar_br(quem.get("preco"))} '
            f'{_link(quem.get("url_anuncio"), "ver anúncio")}</p>'
        )

    corpo = (
        f'<h2 style="color:#dc2626;margin:0;">⚠ Perdi buybox</h2>'
        f'<p><b>SKU:</b> {escape(sku)} &nbsp;|&nbsp; <b>Anúncio:</b> {escape(item_id)} '
        f'{_link(url)}</p>'
        f'<p><b>Produto:</b> {escape(titulo)}</p>'
        f'<p><b>Seu preço:</b> {_formatar_br(dados.get("preco_atual"))}</p>'
        f'{_bloco_rc(dados)}'
        f'{quem_html}'
        f'{sugestao_html}'
    )
    return assunto, _wrap(corpo)


def template_a2(sku: str, item_id: str, dados: dict) -> tuple[str, str]:
    """A2 — ameaça ao buybox."""
    titulo = dados.get("titulo") or sku
    preco_otimo = dados.get("preco_otimo_sugerido")
    rc_otimo = dados.get("rc_no_preco_otimo")
    url = dados.get("url_anuncio")
    motivo = dados.get("motivo_sugestao") or ""

    assunto = f"[A2] Ameaça ao buybox — {sku}"

    sugestao_html = ""
    if preco_otimo is not None:
        sugestao_html = (
            f'<p><b>Preço para defender:</b> {_formatar_br(preco_otimo)} '
            f'(RC {rc_otimo:.1f}%)</p>'
            f'<p style="color:#6b7280;font-size:13px;">{escape(motivo)}</p>'
        )

    corpo = (
        f'<h2 style="color:#d97706;margin:0;">⚡ Ameaça ao buybox</h2>'
        f'<p><b>SKU:</b> {escape(sku)} &nbsp;|&nbsp; <b>Anúncio:</b> {escape(item_id)} '
        f'{_link(url)}</p>'
        f'<p><b>Produto:</b> {escape(titulo)}</p>'
        f'<p><b>Seu preço:</b> {_formatar_br(dados.get("preco_atual"))}</p>'
        f'{_bloco_rc(dados)}'
        f'<p><b>1º:</b> {_formatar_br(dados.get("preco_1o"))} '
        f'&nbsp;|&nbsp; <b>2º:</b> {_formatar_br(dados.get("preco_2o"))}</p>'
        f'{sugestao_html}'
    )
    return assunto, _wrap(corpo)


def template_a3(sku: str, item_id: str, dados: dict) -> tuple[str, str]:
    """A3 — concorrente sumiu do top 3."""
    titulo = dados.get("titulo") or sku
    sumido = dados.get("concorrente_sumido") or {}
    preco_otimo = dados.get("preco_otimo_sugerido")
    rc_otimo = dados.get("rc_no_preco_otimo")
    url = dados.get("url_anuncio")

    assunto = f"[A3] Oportunidade — concorrente sumiu — {sku}"

    sugestao_html = ""
    if preco_otimo is not None:
        sugestao_html = (
            f'<p><b>Novo preço ótimo:</b> {_formatar_br(preco_otimo)} '
            f'(RC {rc_otimo:.1f}%)</p>'
        )

    corpo = (
        f'<h2 style="color:#16a34a;margin:0;">🎯 Oportunidade</h2>'
        f'<p><b>SKU:</b> {escape(sku)} &nbsp;|&nbsp; <b>Anúncio:</b> {escape(item_id)} '
        f'{_link(url)}</p>'
        f'<p><b>Produto:</b> {escape(titulo)}</p>'
        f'<p><b>Seu preço:</b> {_formatar_br(dados.get("preco_atual"))}</p>'
        f'{_bloco_rc(dados)}'
        f'<p><b>Sumiu:</b> {escape(sumido.get("seller_nome") or "—")} '
        f'(estava em {sumido.get("posicao")}º @ {_formatar_br(sumido.get("preco"))})</p>'
        f'<p><b>Sua posição agora:</b> {dados.get("nossa_posicao_atual") or "—"}</p>'
        f'{sugestao_html}'
    )
    return assunto, _wrap(corpo)


# Map tipo → função
_TEMPLATES_CRITICOS = {
    "A1": template_a1,
    "A2": template_a2,
    "A3": template_a3,
}


def renderizar_critico(tipo: str, sku: str, item_id: str, dados: dict) -> tuple[str, str]:
    """Despacha para o template correto pelo tipo do alerta."""
    fn = _TEMPLATES_CRITICOS.get(tipo)
    if fn is None:
        raise ValueError(f"Tipo de alerta desconhecido: {tipo}")
    return fn(sku, item_id, dados)


# ============================================================
# Resumo diário (B1 + B2 + B3 em um único e-mail)
# ============================================================


def _secao_b1(itens: list[dict]) -> str:
    if not itens:
        return ""
    linhas = "".join(
        f'<tr>'
        f'<td style="{_TD_STYLE}">{escape(i["sku"])}</td>'
        f'<td style="{_TD_STYLE}">{escape(i.get("titulo") or "—")}</td>'
        f'<td style="{_TD_STYLE}">{escape(i["motivo"])}</td>'
        f'<td style="{_TD_STYLE}">{_link(i.get("url_anuncio"))}</td>'
        f'</tr>'
        for i in itens
    )
    return (
        f'<h3 style="color:#dc2626;">B1 — Anúncios com problema ({len(itens)})</h3>'
        f'<table style="{_TABLE_STYLE}">'
        f'<tr>'
        f'<th style="{_TH_STYLE}">SKU</th>'
        f'<th style="{_TH_STYLE}">Produto</th>'
        f'<th style="{_TH_STYLE}">Motivo</th>'
        f'<th style="{_TH_STYLE}">Link</th>'
        f'</tr>'
        f'{linhas}'
        f'</table>'
    )


def _secao_b2(itens: list[dict]) -> str:
    if not itens:
        return ""
    linhas = "".join(
        f'<tr>'
        f'<td style="{_TD_STYLE}">{escape(i["sku"])}</td>'
        f'<td style="{_TD_STYLE}">{escape(i.get("titulo") or "—")}</td>'
        f'<td style="{_TD_STYLE}">{i["margem_media"]:.1f}%</td>'
        f'<td style="{_TD_STYLE}">{i["rc_medio"]:.1f}%</td>'
        f'<td style="{_TD_STYLE}">{i["snapshots_ruins"]} / {i["snapshots"]}</td>'
        f'<td style="{_TD_STYLE}">{_link(i.get("url_anuncio"))}</td>'
        f'</tr>'
        for i in itens
    )
    return (
        f'<h3 style="color:#d97706;">B2 — Margem baixa ({len(itens)})</h3>'
        f'<table style="{_TABLE_STYLE}">'
        f'<tr>'
        f'<th style="{_TH_STYLE}">SKU</th>'
        f'<th style="{_TH_STYLE}">Produto</th>'
        f'<th style="{_TH_STYLE}">Margem média</th>'
        f'<th style="{_TH_STYLE}">RC médio</th>'
        f'<th style="{_TH_STYLE}">Snapshots ruins</th>'
        f'<th style="{_TH_STYLE}">Link</th>'
        f'</tr>'
        f'{linhas}'
        f'</table>'
    )


def _secao_b3(itens: list[dict]) -> str:
    if not itens:
        return ""
    linhas = "".join(
        f'<tr>'
        f'<td style="{_TD_STYLE}">{escape(i["sku"])}</td>'
        f'<td style="{_TD_STYLE}">{escape(i.get("titulo") or "—")}</td>'
        f'<td style="{_TD_STYLE}">{_formatar_br(i["preco_atual"])}</td>'
        f'<td style="{_TD_STYLE}">{_formatar_br(i["preco_otimo_sugerido"])}</td>'
        f'<td style="{_TD_STYLE}">+{_formatar_br(i["ganho_rs"])}</td>'
        f'<td style="{_TD_STYLE}">{i["rc_no_preco_otimo"]:.1f}%</td>'
        f'<td style="{_TD_STYLE}">{_link(i.get("url_anuncio"))}</td>'
        f'</tr>'
        for i in itens
    )
    return (
        f'<h3 style="color:#16a34a;">B3 — Oportunidades de subir preço ({len(itens)})</h3>'
        f'<table style="{_TABLE_STYLE}">'
        f'<tr>'
        f'<th style="{_TH_STYLE}">SKU</th>'
        f'<th style="{_TH_STYLE}">Produto</th>'
        f'<th style="{_TH_STYLE}">Preço atual</th>'
        f'<th style="{_TH_STYLE}">Preço ótimo</th>'
        f'<th style="{_TH_STYLE}">Ganho/un.</th>'
        f'<th style="{_TH_STYLE}">RC esperado</th>'
        f'<th style="{_TH_STYLE}">Link</th>'
        f'</tr>'
        f'{linhas}'
        f'</table>'
    )


def template_c1_campanhas(
    itens: list[dict],
    conta: str = "best_hair",
) -> tuple[str, str]:
    """
    C1 — e-mail consolidado de campanhas com RC acima do mínimo disponíveis
    para aceitar.

    Cada linha mostra:
      - Estado atual do anúncio (preço atual, RC atual, posição buybox,
        se já participa de outra campanha)
      - Dados da nova campanha disponível (preço, rebate, RC c/ campanha)
    """
    total = len(itens)
    plural    = "s" if total > 1 else ""
    plural_vel = "eis" if total > 1 else "el"
    assunto = (
        f"[C1] {total} campanha{plural} disponív{plural_vel} para aceitar"
        f" — {conta}"
    )

    _TD  = _TD_STYLE
    _TH  = _TH_STYLE
    _TH_G = _TH_STYLE + "background:#dcfce7;"   # cabeçalho verde (campanha)
    _TH_B = _TH_STYLE + "background:#eff6ff;"   # cabeçalho azul (estado atual)

    def _rc_td(val: Optional[float]) -> str:
        """Célula de RC colorida."""
        if val is None:
            return f'<td style="{_TD}">—</td>'
        cor = _cor_rc(val)
        return (
            f'<td style="{_TD};color:{cor};font-weight:700">'
            f'{val:.1f}%</td>'
        )

    def _campanha_td(em: bool, nome: Optional[str]) -> str:
        if em:
            label = escape(nome or "Sim")
            return f'<td style="{_TD};color:#d97706;">✔ {label}</td>'
        return f'<td style="{_TD};color:#6b7280;">Não</td>'

    def _pos_td(pos: Optional[int]) -> str:
        if pos is None:
            return f'<td style="{_TD}">—</td>'
        cor = "#16a34a" if pos == 1 else "#1f2937"
        return f'<td style="{_TD};color:{cor};font-weight:{"700" if pos==1 else "400"}">{pos}º</td>'

    def _estoque_td(val: Optional[int]) -> str:
        if val is None:
            return f'<td style="{_TD}">—</td>'
        cor = "#dc2626" if val == 0 else "#1f2937"
        return f'<td style="{_TD};color:{cor};font-weight:{"700" if val==0 else "400"}">{val}</td>'

    linhas = "".join(
        f"<tr>"
        f'<td style="{_TD}">{escape(i["sku"])}</td>'
        f'<td style="{_TD}">{escape(i["item_id"])}</td>'
        f'<td style="{_TD}">{escape(i.get("campanha_nome") or "—")}</td>'
        # — Estado atual —
        f'<td style="{_TD}">{_formatar_br(i.get("preco_atual"))}</td>'
        + _rc_td(i.get("rc_atual"))
        + _pos_td(i.get("posicao_buybox"))
        + _estoque_td(i.get("estoque"))
        + _campanha_td(i.get("ja_em_campanha", False), i.get("campanha_ativa_nome"))
        # — Campanha disponível —
        + f'<td style="{_TD}">{_formatar_br(i.get("preco_campanha"))}</td>'
        f'<td style="{_TD}">{_formatar_br(i.get("rebate"))}</td>'
        + _rc_td(i.get("rc_campanha"))
        + f'<td style="{_TD}">{escape(i.get("vigencia_fim") or "—")}</td>'
        f"</tr>"
        for i in itens
    )

    corpo = (
        f'<h2 style="color:#16a34a;margin:0;">🏷 {total} campanha{plural} para aceitar</h2>'
        f"<p>Anúncios com campanhas de rebate e RC acima do mínimo aguardando confirmação:</p>"
        f'<table style="{_TABLE_STYLE}">'
        f"<tr>"
        # Identificação
        f'<th style="{_TH}">SKU</th>'
        f'<th style="{_TH}">MLB</th>'
        f'<th style="{_TH}">Campanha</th>'
        # Estado atual
        f'<th style="{_TH_B}">Preço atual</th>'
        f'<th style="{_TH_B}">RC atual</th>'
        f'<th style="{_TH_B}">Posição BB</th>'
        f'<th style="{_TH_B}">Estoque</th>'
        f'<th style="{_TH_B}">Já em campanha</th>'
        # Nova campanha
        f'<th style="{_TH_G}">Preço c/ camp.</th>'
        f'<th style="{_TH_G}">Rebate</th>'
        f'<th style="{_TH_G}">RC c/ camp.</th>'
        f'<th style="{_TH_G}">Vigência até</th>'
        f"</tr>"
        f"{linhas}"
        f"</table>"
        f'<p style="margin-top:16px;font-size:12px;color:#6b7280;">'
        f'Acesse a <a href="https://www.mercadolivre.com.br/anuncios/lista/promos"'
        f' style="color:#2563eb;">Central de Promoções ML</a> para participar.</p>'
    )
    return assunto, _wrap(corpo)


def template_resumo_diario(resumo: dict, data_referencia: datetime | None = None) -> tuple[str, str]:
    """
    Constrói o e-mail único do resumo diário.

    Se todas as listas estiverem vazias, devolve assunto/corpo padrão
    para que o avaliador possa optar por não enviar.
    """
    ref = data_referencia or datetime.now()
    data_str = ref.strftime("%d/%m/%Y")

    b1 = resumo.get("b1_problemas") or []
    b2 = resumo.get("b2_margem_baixa") or []
    b3 = resumo.get("b3_oportunidades") or []

    total = len(b1) + len(b2) + len(b3)
    assunto = (
        f"[Buybox] Resumo {data_str} — "
        f"{len(b1)} problemas / {len(b2)} margem baixa / {len(b3)} oportunidades"
    )

    if total == 0:
        corpo = (
            f'<h2 style="margin:0;">Resumo diário — {escape(data_str)}</h2>'
            f'<p>Nenhum alerta de resumo no dia. '
            f'{resumo.get("total_skus_avaliados", 0)} anúncio(s) avaliado(s).</p>'
        )
        return assunto, _wrap(corpo)

    corpo = (
        f'<h2 style="margin:0;">Resumo diário — {escape(data_str)}</h2>'
        f'<p>{resumo.get("total_skus_avaliados", 0)} anúncio(s) avaliado(s) hoje.</p>'
        f'{_secao_b1(b1)}'
        f'{_secao_b2(b2)}'
        f'{_secao_b3(b3)}'
    )
    return assunto, _wrap(corpo)
