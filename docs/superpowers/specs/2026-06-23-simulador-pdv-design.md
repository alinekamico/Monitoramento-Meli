# Simulador de PDV — Design Spec
**Data:** 2026-06-23  
**Status:** Aprovado

## Resumo

Nova aba "Simulador" no dashboard.html para simular preços e rebates de produtos. Cards estilo notas, organizados em pastas, persistidos no localStorage. Cálculo de PDV via servidor (`/api/calcular-pdv`).

## Arquitetura

- **Frontend:** Nova aba + pane em `dashboard.html` (HTML/CSS/JS puro, sem arquivos adicionais)
- **Backend:** Endpoint `POST /api/calcular-pdv` em `server.py` — recebe `{preco, custo, rebate, peso, tipo_anuncio}` e retorna o breakdown completo via `margem.calcular_margem()`
- **Persistência:** localStorage — chaves `sim_notes` (array de simulações) e `sim_folders` (array de pastas)

## Componentes

### Barra de pastas
- Chips horizontais: "Todas" (default) + uma por pasta criada
- Pasta ativa filtra os cards exibidos
- Clique direito (ou botão ⋯) na chip: Renomear / Excluir pasta
- Excluir pasta não exclui cards — eles ficam sem pasta atribuída

### Botão "Novo" com dropdown
- Abre dropdown com duas opções: "Nova simulação" | "Nova pasta"
- Nova pasta: input inline para nome
- Nova simulação: abre o modal de edição

### Modal de simulação
- Toggle "SKU cadastrado / Produto livre":
  - SKU cadastrado: seletor preenche custo, peso e tipo automaticamente (via `GET /api/skus`)
  - Produto livre: todos os campos editáveis
- Campos: preço de venda, custo, peso, tipo de anúncio (Clássico/Premium), rebate ML (R$), pasta
- Breakdown ao vivo: debounced 400ms → `POST /api/calcular-pdv` → exibe resultado
- Nome do card: gerado automaticamente como `SKU · R$ PREÇO`

### Cards (grade e lista)
- **Grade:** 3 colunas, cards com título (SKU + preço), RC% em destaque, margem%
- **Lista:** uma linha por card com colunas: SKU, preço, RC%, margem%, pasta, lixeira
- RC colorido: verde se `rc_pct >= rc_minimo`, vermelho se abaixo — valor de `rc_minimo` vem do servidor junto com o cálculo
- Borda vermelha no card quando RC < rc_minimo
- Lixeira aparece no hover (grade) / sempre visível (lista)
- **1 clique:** expande breakdown inline (grade) / não aplicável (lista abre direto inline)
- **2 cliques / duplo clique:** reabre o modal de edição
- Menu ⋯ no card: Editar | Mover para pasta… | Excluir

### Toggle grade/lista
- Ícones no canto superior direito, persiste preferência no localStorage

## Fluxo de dados

```
Usuário altera campo → debounce 400ms
  → POST /api/calcular-pdv {preco, custo, rebate, peso, tipo_anuncio}
  → margem.calcular_margem() + rc_minimo de settings.yaml
  → {preco_campanha, custo, comissao, frete, imposto, insumo, reversa, rebate, lucro_bruto, margem_pct, rc_pct, rc_minimo}
  → renderiza breakdown no modal

Usuário salva
  → grava objeto no localStorage (sim_notes)
  → fecha modal, re-renderiza grid/lista
```

## Endpoint /api/calcular-pdv

```
POST /api/calcular-pdv
Body: { preco, custo, rebate, peso, tipo_anuncio }
Response: { preco_campanha, custo, comissao, frete, imposto, insumo, reversa, rebate,
            lucro_bruto, margem_pct, rc_pct, rc_minimo }
```

## localStorage schema

```json
// sim_notes
[{
  "id": "uuid-v4-like",
  "sku": "WL008",        // null para produto livre
  "nome": "WL008 · R$ 147,00",
  "preco": 147.0,
  "custo": 54.3,
  "rebate": 12.0,
  "peso": 1.5,
  "tipo_anuncio": "Clássico",
  "pasta_id": "folder-abc",   // null se sem pasta
  "resultado": { ...breakdown },
  "criado_em": "2026-06-23T10:00:00.000Z"
}]

// sim_folders
[{ "id": "folder-abc", "nome": "Promoções verão", "criado_em": "..." }]

// sim_view: "grid" | "list"
```
