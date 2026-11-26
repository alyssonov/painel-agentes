# Gerente do Ano — Diagnóstico e plano de evolução

Data: 26/11/2026. Base: o documento "Prompt mestre" (guardado em
`docs/VISAO_PRODUTO.md`), o código atual e as duas imagens de referência.

## 1. O que existe hoje

| Item | Situação |
|---|---|
| Código | ~10.500 linhas Python, 19 telas, 48 rotas, 31 tabelas (14 migrações), **233 testes passando** |
| Tecnologia | Python 3.12 + FastAPI, banco SQLite (PostgreSQL em produção), telas em Jinja2 + HTMX, agendador separado (worker) |
| Integrações reais | Mercado Livre (leitura + escrita só com aprovação), Olist (somente leitura), Mercado Pago (leitura) |
| Segurança | login com senha (Argon2), CSRF, CSP rígida, tokens criptografados, auditoria só de inserção, modo simulação |
| IA | 8 agentes funcionando (perguntas, pós-venda, preços, anúncios, avaliações, estoque, margem, NF-e) + despacho, reputação, relatório |

Funciona com dados reais da loja: envios de hoje, lucro real por venda (valor
recebido no Mercado Pago − custo do Olist − impostos), previsão de recebimentos,
sugestão de preço com margem mínima, curva ABC, produtos parados, sugestão de
compra, reclamações e avaliações.

## 2. Comparação com o documento novo (19 módulos)

✅ pronto · 🟡 parcial · ⬜ não existe · 🔒 depende de você (ver `PENDENCIAS.md`)

| Módulo | Hoje | O que falta |
|---|---|---|
| 1 Dashboard | 🟡 | faturamento do mês, ticket médio, comparação por marketplace, gráficos no painel principal, publicidade (ROAS/ACOS/TACOS 🔒B5) |
| 2 Vendas / 3 Pedidos | 🟡 | central de pedidos com filtros e busca; picking, conferência, etiquetas em lote |
| 4 Produtos | 🟡 (só leitura do Olist) | cadastro complementar, compatibilidade com motos 🔒A2/B7, histórico de alterações |
| 5 Anúncios | 🟡 | lista central com filtros e ações em massa (pausar, preço, estoque 🔒A3) |
| 6 Estoque | 🟡 | estoque mínimo por produto, histórico de movimentações (leitura do Olist) |
| 7 Precificação | 🟡 | **calculadora** (margem → preço e preço → margem), comparação ML × Shopee, precificação em massa com aprovação |
| 8 Financeiro | 🟡 | despesas fixas 🔒B6, devoluções e reembolsos no resultado, DRE simples |
| 9 Marketing | ⬜ | Mercado Ads 🔒B5 |
| 10 Atendimento | ✅/🟡 | central única (perguntas + mensagens + reclamações), IA com compatibilidade 🔒B7 |
| 11 Clientes | ⬜ | limitado: o Mercado Livre esconde dados pessoais do comprador (LGPD) |
| 12 Fornecedores / 13 Compras | 🟡 | cadastro de fornecedores, pedidos de compra, histórico de custos |
| 14 Relatórios | 🟡 | central de relatórios com exportação Excel/CSV |
| 15 Automações | 🟡 (regras fixas por agente) | tela "quando acontecer X → faça Y" com histórico |
| 16 IA | 🟡 | **Agente Gerente** (chat que responde perguntas sobre a operação com dados reais) |
| 17 Equipe | 🟡 (2 papéis) | permissões por ação (ver, criar, editar, aprovar, financeiro…) checadas no servidor |
| 18 Integrações | 🟡 | Shopee 🔒B3, Onix 🔒B2 |
| 19 Configurações | 🟡 | organizar em uma tela só |
| Auditoria | 🟡 | "valor anterior → valor novo" em toda alteração e histórico por produto/pedido/anúncio |
| SaaS multiempresa | ⬜ | separar dados por empresa 🔒A4 |

## 3. Problemas encontrados

1. **Dois testes quebravam com o passar dos dias** (datas fixas nos dados de
   teste). Corrigido hoje; não havia defeito no painel.
2. **Conflitos entre o documento novo e as regras atuais** (precisam da sua
   decisão, `PENDENCIAS.md` A1–A4): identidade visual, cadastro de produtos e
   estoque (Olist como fonte da verdade), multiempresa.
3. **Fontes externas desconhecidas:** NorteUp (site inacessível daqui e sem
   descrição pública) e Onix (sem documentação pública de integração).
4. **Limitações conhecidas:** lucro exato só nos últimos 35 dias; reclamações de
   vendas em carrinho às vezes sem produto; Shopee só pelo Olist.

## 4. Decisões técnicas recomendadas

**Manter a tecnologia atual (FastAPI + telas no servidor + HTMX).** O documento
sugere Next.js/React para partes novas, mas também pede para não trocar sem
motivo. Trocar agora significaria reescrever 19 telas que funcionam, adicionar
um segundo servidor (Node) e um processo de "build", e duplicar login/segurança.
Com HTMX dá para fazer tabelas com filtros, ações em massa, gráficos e chat sem
recarregar a página. Reavaliar só se o SaaS exigir um aplicativo de celular.

**Multiempresa cedo:** adicionar "empresa" a cada registro agora (uma migração
grande, mas mecânica) evita reconstruir depois. Recomendo fazer na fase 2,
depois da sua aprovação (A4).

**Onix:** o caminho mais seguro, em ordem: (1) exportação de planilha, já,
(2) API do fornecedor, se existir, (3) um pequeno **conector para Windows**
instalado no computador da loja, que lê o banco **somente leitura** e envia os
custos ao Gerente do Ano por conexão segura — nunca expor o banco à internet.

## 5. Referências visuais (análise)

- **Foto 1 (estrutura):** menu lateral claro e enxuto com ícones de traço,
  busca no topo do menu, título de boas-vindas, cards com ícone + valor +
  variação em selo ("↗ 15%"), gráfico de linha grande com dica ao passar o
  mouse, bastante espaço em branco, cantos médios, sombras quase invisíveis.
- **Foto 2 (cores):** fundo quase preto azulado (#0B1220–#111A2E), cards um tom
  acima com borda fina, azul vivo como cor principal (#3B82F6), verde para
  crescimento, vermelho para despesas, cores fortes só em ícones e linhas,
  números em fonte monoespaçada/tabular, assistente de IA em painel lateral.
- **Proposta:** estrutura da Foto 1 + paleta da Foto 2, com **tema escuro como
  padrão e tema claro opcional**, mantendo o robô da logo. Isso substitui o
  azul‑marinho em degradê atual — **só depois da sua confirmação (A1)**.

## 6. Plano de execução

Cada etapa termina com: testes, conferência visual, instruções de teste e
espera da sua confirmação.

| Etapa | Entrega | Depende de |
|---|---|---|
| **1. Precificação — calculadora** ✅ 26/11 | preço a partir da margem e margem a partir do preço, por marketplace (ML Clássico/Premium, Shopee), com taxas oficiais, frete, impostos, publicidade e custos; comparação lado a lado; usa custo do Olist ou digitado | nada (B1 só para ajustar ao NorteUp) |
| **2. Fundação visual** ✅ 26/11 | design system novo (cores, tipografia, componentes, tema escuro/claro), menu reorganizado nos novos módulos, dashboard com mais indicadores e gráficos | A1 |
| **3. Estrutura e segurança** ✅ 26/11 | empresas (multiempresa), papéis e permissões granulares, auditoria com antes/depois e histórico por registro, tela de equipe | A4 |
| **4. Operação** ✅ 26/11 (em teste; etiquetas e ações de anúncio dependem da documentação oficial) | central de pedidos (filtros, picking, etiquetas), central de anúncios (filtros, ações em massa com aprovação), precificação em massa, produtos com cadastro complementar e compatibilidade | A2, A3, B7 |
| **5. Integrações e gestão** | custos do Onix (planilha → conector), fornecedores e compras, financeiro com despesas e DRE, Mercado Ads, Shopee | B2, B6, B5, B3 |
| **6. Inteligência** 🟡 26/11 (Assistente pronto) | Agente Gerente (chat com dados reais e fontes), tela de automações configuráveis, relatórios exportáveis | — |
| **7. Colocar no ar** | domínio, servidor, HTTPS, backup, revisão de segurança | B4 |
| **8. SaaS** | cadastro de empresas, planos, limites, cobrança | depois de rodar bem na loja |
