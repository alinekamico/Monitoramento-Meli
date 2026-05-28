"""
Algoritmo de preço ótimo do MVP Buybox.

Dado o snapshot da competição e os dados de PDV, sugere o preço que
maximiza margem mantendo posição vencedora (buybox). Não sugere mudança
quando:
  - É o único vendedor no catálogo
  - Diferença para o 2º colocado é menor que o ruído configurado
  - RC no preço candidato fica abaixo de rc_minimo
  - Custo zerado ou faltando

Reusa src.margem.calcular_margem — não duplica fórmula PDV.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import margem


@dataclass
class ResultadoPricing:
    """Resultado da função calcular_preco_otimo()."""

    preco_otimo_sugerido: Optional[float]
    rc_no_preco_otimo: Optional[float]
    margem_no_preco_otimo: Optional[float]
    motivo: str

    def to_dict(self) -> dict:
        return {
            "preco_otimo_sugerido": self.preco_otimo_sugerido,
            "rc_no_preco_otimo":    self.rc_no_preco_otimo,
            "margem_no_preco_otimo": self.margem_no_preco_otimo,
            "motivo":               self.motivo,
        }


# Mensagens de motivo — strings curtas para caber no Snapshot.motivo_sugestao
MOTIVO_UNICO_VENDEDOR  = "Único vendedor: sem referência de mercado"
MOTIVO_RUIDO           = "Diferença para concorrente abaixo do ruído configurado"
MOTIVO_RC_INVIAVEL     = "RC no preço candidato ({rc:.1f}%) abaixo do mínimo ({minimo:.0f}%)"
MOTIVO_RC_FORA_CAMPANHA = (
    "RC no preço candidato ({rc:.1f}%) abaixo do mínimo ({minimo:.0f}%) "
    "— ao mudar de R$ {preco_aplicado:.2f} para o candidato, sai da "
    "campanha externa e perde o rebate de {rebate_pct:.0f}%"
)
MOTIVO_DEFENDER_BUYBOX = "Defender buybox: R$ {passo:.2f} abaixo do 2º colocado"
MOTIVO_RETOMAR_BUYBOX  = "Retomar buybox: R$ {passo:.2f} abaixo do 2º colocado"
MOTIVO_PASSAR_1O       = "Passar o 1º colocado por R$ {passo:.2f}"
MOTIVO_MANTER          = "Sem concorrência ativa: manter preço atual"
MOTIVO_CUSTO_INVALIDO  = "Custo zerado ou faltando — sem cálculo"
MOTIVO_EMPATE_1O       = "Empate em 1º: ML não dá buybox por empate, descer R$ {passo:.2f} do 1º"
MOTIVO_OFF_CATALOGO    = "Anúncio não visível ao cliente (pausado/sem estoque) — reativar primeiro"
MOTIVO_SUBIDA_SEM_GANHO = (
    "Subida de preço não melhora RC ({rc:.1f}% ≤ atual {rc_atual:.1f}%) — mantendo"
)


# Tolerância para considerar dois preços "iguais" no contexto da campanha
# externa. R$ 0,10 cobre o passo padrão do pricing sem falso-positivo
# por arredondamento.
_TOLERANCIA_PRECO_RS = 0.05


def rebate_aplicavel(preco: float, campanha: Optional[dict]) -> bool:
    """
    Decide se o rebate ML continua valendo no `preco` informado.

    Hierarquia de regras (do mais específico para o mais geral):

    1. Sem campanha ou rebate_pct <= 0 → não há rebate para aplicar.
    2. Se a campanha expõe `min_price` / `max_price` (caso SMART/DEAL
       bem comportadas): o preço precisa estar dentro da faixa.
    3. Sem faixa explícita (caso SELLER_CAMPAIGN/FLEXIBLE_PERCENTAGE):
       o rebate só vale exatamente no `preco_aplicado` (o preço onde
       a campanha foi vinculada). Mudar de preço significa sair dessa
       campanha externa e entrar na campanha PRÓPRIA do seller, que
       não tem rebate ML.
    4. Sem faixa e sem `preco_aplicado` conhecido (compatibilidade com
       snapshots antigos): aplica o rebate (mantém o comportamento
       anterior para não quebrar histórico).
    """
    if not campanha or not campanha.get("rebate_pct"):
        return False

    min_p = float(campanha.get("min_price") or 0)
    max_p = float(campanha.get("max_price") or 0)
    if min_p > 0 or max_p > 0:
        if min_p > 0 and preco < min_p:
            return False
        if max_p > 0 and preco > max_p:
            return False
        return True

    # Caso comum no e-commerce do usuário: campanha externa sem faixa.
    preco_aplicado = float(campanha.get("preco_aplicado") or 0)
    if preco_aplicado > 0:
        return abs(preco - preco_aplicado) <= _TOLERANCIA_PRECO_RS

    # Sem nenhum metadado de faixa nem preço aplicado: assume válido
    # (preserva comportamento antigo de snapshots pré-migração).
    return True


def _calcular_rebate_valor(campanha: Optional[dict]) -> float:
    """
    Valor FIXO em R$ que o ML subsidia quando a campanha está vigente.

    O ML calcula esse subsídio como `original_price × meli_percentage / 100`
    (preço cheio × pct, não preço da campanha × pct). Esse é o mesmo
    cálculo que o painel de Campanhas exibe — o resultado é o que cai
    na sua conta a cada venda durante a vigência.

    Fallback: se `original_price` não veio (raro em campanhas SMART, comum
    em SELLER_CAMPAIGN sem registro de preço cheio), usa o `preco_aplicado`
    (o preço atual onde a campanha está aplicada) para evitar retornar
    zero quando há rebate de fato.
    """
    if not campanha:
        return 0.0
    pct = float(campanha.get("rebate_pct") or 0)
    if pct <= 0:
        return 0.0

    base = float(campanha.get("original_price") or 0)
    if base <= 0:
        base = float(campanha.get("preco_aplicado") or 0)
    if base <= 0:
        return 0.0

    return round(base * pct / 100.0, 2)


def _rebate_em_reais(preco: float, campanha: Optional[dict]) -> float:
    """
    Rebate em R$ no `preco` informado — zero se o preço sai da campanha,
    o valor fixo do ML caso contrário (NÃO recalcula proporcional).
    """
    if not rebate_aplicavel(preco, campanha):
        return 0.0
    return _calcular_rebate_valor(campanha)


def calcular_preco_candidato(
    *,
    preco_atual: float,
    preco_1o: Optional[float],
    preco_2o: Optional[float],
    nossa_posicao: Optional[int],
    tem_buybox: bool,
    passo: float = 0.10,
) -> Optional[float]:
    """
    Devolve apenas o preço-alvo que o algoritmo TESTARIA — sem checar
    RC, ruído ou viabilidade. Útil para o dashboard mostrar o candidato
    no breakdown mesmo quando a sugestão foi descartada por RC inviável.

    A lógica abaixo é a MESMA do passo 1 de `calcular_preco_otimo` —
    qualquer mudança em uma deve refletir na outra.
    """
    estou_em_primeiro = (nossa_posicao == 1) or tem_buybox

    if estou_em_primeiro:
        # Em 1º: queremos subir até R$ 0,10 abaixo do 2º colocado real
        if preco_2o is not None and preco_2o > 0:
            return round(preco_2o - passo, 2)
        return None  # sem 2º: manter preço

    # Fora do buybox: retomar passando o 1º colocado
    if preco_1o is None or preco_1o <= 0:
        return None
    return round(preco_1o - passo, 2)


def calcular_preco_otimo(
    *,
    preco_atual: float,
    preco_1o: Optional[float],
    preco_2o: Optional[float],
    nossa_posicao: Optional[int],
    tem_buybox: bool,
    custo: float,
    peso: float,
    tipo_anuncio: str,
    settings: dict,
    campanha_ativa: Optional[dict] = None,
    is_full: bool = False,
    visivel_no_catalogo: bool = True,
) -> ResultadoPricing:
    """
    Calcula preço ótimo seguindo o algoritmo da especificação do MVP.

    Parâmetros (todos por palavra-chave para clareza no call site)
    --------------------------------------------------------------
    preco_atual    : seu preço hoje
    preco_1o       : preço do 1º colocado (pode ser igual ao seu se você está em 1º)
    preco_2o       : preço do 2º colocado (None se só você no catálogo)
    nossa_posicao  : 1, 2, 3, … ou None se estiver fora do top 5
    tem_buybox     : True se atualmente segura o buybox
    custo          : CMV do SKU
    peso           : peso em kg
    tipo_anuncio   : "Clássico" ou "Premium"
    settings       : dict completo de config/settings.yaml (precisa de
                     rc_minimo, comissao_*, imposto_pct, reversa_pct,
                     insumo_fixo e buybox.diferenca_ruido_rs, passo_abaixo_rs)
    campanha_ativa : dict com 'rebate_pct' (0..100) ou None
    is_full        : se True, insumo_fixo é zerado (mesmo padrão do runner)

    Retorna ResultadoPricing.
    """
    if custo <= 0:
        return ResultadoPricing(None, None, None, MOTIVO_CUSTO_INVALIDO)

    if not visivel_no_catalogo:
        # Sem visibilidade ao cliente não há buybox a preservar nem
        # venda acontecendo — sugerir preço é ruído. O problema é
        # operacional (reativar/repor estoque), não de pricing.
        return ResultadoPricing(None, None, None, MOTIVO_OFF_CATALOGO)

    cfg_buybox = settings.get("buybox", {}) or {}
    rc_minimo: float = float(settings.get("rc_minimo", 60.0))
    passo: float = float(cfg_buybox.get("passo_abaixo_rs", 0.10))
    ruido: float = float(cfg_buybox.get("diferenca_ruido_rs", 1.00))

    # Insumo fixo zerado para Full (mesma regra do runner de campanhas)
    cfg_calc = {**settings, "insumo_fixo": 0.0} if is_full else settings

    # ----- Passo 1: definir preço candidato -----
    preco_candidato: Optional[float] = None
    motivo_template: Optional[str] = None

    estou_em_primeiro = (nossa_posicao == 1) or tem_buybox

    if estou_em_primeiro:
        if preco_2o is not None and preco_2o > 0:
            # Filtro de ruído: se já estou bem perto, não mexer
            diff = abs(preco_2o - preco_atual)
            if diff < ruido:
                return ResultadoPricing(None, None, None, MOTIVO_RUIDO)
            preco_candidato = round(preco_2o - passo, 2)
            motivo_template = MOTIVO_DEFENDER_BUYBOX
        else:
            return ResultadoPricing(None, None, None, MOTIVO_MANTER)
    else:
        # Estou fora do buybox
        if preco_1o is None or preco_1o <= 0:
            return ResultadoPricing(None, None, None, MOTIVO_UNICO_VENDEDOR)

        if preco_2o is not None and preco_2o > 0 and preco_2o < preco_1o:
            # Estado transitório: existe 2º distinto do 1º — passo o 2º
            preco_candidato = round(preco_2o - passo, 2)
            motivo_template = MOTIVO_RETOMAR_BUYBOX
        elif preco_2o is not None and preco_2o > 0 and preco_2o == preco_1o:
            # Empate em 1º: ML não dá buybox a ninguém por empate
            preco_candidato = round(preco_1o - passo, 2)
            motivo_template = MOTIVO_EMPATE_1O
        else:
            preco_candidato = round(preco_1o - passo, 2)
            motivo_template = MOTIVO_PASSAR_1O

    # ----- Passo 2: calcular RC no preço candidato -----
    rebate_rs = _rebate_em_reais(preco_candidato, campanha_ativa)
    m = margem.calcular_margem(
        preco_campanha=preco_candidato,
        custo=custo,
        rebate=rebate_rs,
        peso=peso,
        tipo_anuncio=tipo_anuncio,
        cfg=cfg_calc,
    )
    rc = float(m.get("rc_pct", 0.0))
    margem_pct = float(m.get("margem_pct", 0.0))

    # ----- Passo 3: decidir -----
    if rc < rc_minimo:
        # Mensagem mais informativa se a inviabilidade vem da perda da
        # campanha (preço saiu da faixa)
        if (campanha_ativa
                and campanha_ativa.get("rebate_pct", 0) > 0
                and not rebate_aplicavel(preco_candidato, campanha_ativa)):
            return ResultadoPricing(
                None, None, None,
                MOTIVO_RC_FORA_CAMPANHA.format(
                    rc=rc, minimo=rc_minimo,
                    preco_aplicado=campanha_ativa.get("preco_aplicado") or 0,
                    rebate_pct=campanha_ativa.get("rebate_pct") or 0,
                ),
            )
        return ResultadoPricing(
            None, None, None,
            MOTIVO_RC_INVIAVEL.format(rc=rc, minimo=rc_minimo),
        )

    # Subida de preço só vale se o RC melhora de fato.
    # Caso contrário — ex.: 2º colocado mais caro mas subir faz perder rebate —
    # a sugestão pioraria a margem, não ajudaria.
    if preco_candidato > preco_atual:
        rebate_rs_atual = _rebate_em_reais(preco_atual, campanha_ativa)
        m_atual = margem.calcular_margem(
            preco_campanha=preco_atual,
            custo=custo,
            rebate=rebate_rs_atual,
            peso=peso,
            tipo_anuncio=tipo_anuncio,
            cfg=cfg_calc,
        )
        rc_atual = float(m_atual.get("rc_pct", 0.0))
        if rc <= rc_atual:
            return ResultadoPricing(
                None, None, None,
                MOTIVO_SUBIDA_SEM_GANHO.format(rc=rc, rc_atual=rc_atual),
            )

    assert motivo_template is not None
    motivo = motivo_template.format(passo=passo)
    return ResultadoPricing(
        preco_otimo_sugerido=preco_candidato,
        rc_no_preco_otimo=round(rc, 2),
        margem_no_preco_otimo=round(margem_pct, 2),
        motivo=motivo,
    )


def calcular_margem_atual(
    *,
    preco_atual: float,
    custo: float,
    peso: float,
    tipo_anuncio: str,
    settings: dict,
    campanha_ativa: Optional[dict] = None,
    is_full: bool = False,
) -> dict:
    """
    Calcula margem/RC no preço atual do anúncio.

    Atalho para o coletor não precisar replicar a lógica de rebate_rs +
    insumo_full por toda parte. Retorna o mesmo dict de margem.calcular_margem.
    """
    if custo <= 0 or preco_atual <= 0:
        return margem.calcular_margem(
            preco_campanha=0.0, custo=custo or 1, rebate=0.0,
            peso=peso, tipo_anuncio=tipo_anuncio, cfg=settings,
        )

    cfg_calc = {**settings, "insumo_fixo": 0.0} if is_full else settings
    rebate_rs = _rebate_em_reais(preco_atual, campanha_ativa)
    return margem.calcular_margem(
        preco_campanha=preco_atual,
        custo=custo,
        rebate=rebate_rs,
        peso=peso,
        tipo_anuncio=tipo_anuncio,
        cfg=cfg_calc,
    )
