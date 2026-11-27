# Gerente do Ano — Visão do produto (prompt mestre do dono, 26/11/2026)

Resumo fiel do documento entregue pelo usuário. É a referência de "para onde o
produto vai". As regras de segurança e de funcionamento do `CLAUDE.md` continuam
valendo; conflitos ficam registrados em `PENDENCIAS.md` (seção A) até decisão.

## Visão
- Plataforma que centraliza a operação de um e-commerce de **motopeças**
  (Mercado Livre, Shopee, Olist, Onix e planilhas) — "centro de comando".
- Domínio `gerentedoano.com`, aplicação `app.gerentedoano.com`.
  Slogan: **"Sua operação sob controle."**
- Começa atendendo a empresa do usuário; arquitetura pronta para virar **SaaS**.
- Não é só painel: gestão inteligente com agentes de IA.
- **Objetivo do dono (27/11/2026):** a pessoa chega para trabalhar, abre o
  Gerente do Ano e fica nele até ir embora, **sem precisar entrar em nenhum outro
  sistema** (Mercado Livre, Shopee, Olist, Onix). Toda tela nova deve ser pensada
  para eliminar uma ida a outro sistema. A escrita nas plataformas (PENDENCIAS A6)
  entra depois que o painel estiver pronto.

## Como trabalhar
- Analisar antes de mudar; preservar o que funciona; nada destrutivo sem aprovação;
  explicar mudanças estruturais.
- Não inventar APIs, endpoints, credenciais ou integrações; deixar claro o que é
  demonstração. Continuar o que não depende de bloqueios.
- Manter `PENDENCIAS.md` (o quê, por quê, o que fornecer, como conseguir, o que
  depende, situação). Nunca pedir segredos no chat.
- Pedir aprovação antes de: apagar dados, operações destrutivas, mexer em
  produção externa, alterar preços/estoque reais (principalmente em massa),
  publicar em produção, compras/pagamentos, ações irreversíveis.
- Explicar em português simples, passo a passo.

## Design
- Foto 1 = estrutura (menu lateral, cards, tabelas, gráficos, espaçamento).
  Foto 2 = cores (fundo escuro, azul de destaque, verde/vermelho funcionais).
- Identidade própria, premium, minimalista; evitar cara de template, excesso de
  cards, sombras/gradientes exagerados, cantos muito arredondados.
- Design system completo (cores, tipografia, espaçamentos, botões, campos,
  tabelas, cards, gráficos, carregamento, mensagens). Desktop primeiro, responsivo.

## Módulos
Dashboard · Vendas · Pedidos · Produtos · Anúncios · Estoque · Precificação ·
Financeiro · Marketing e publicidade · Atendimento · Clientes · Fornecedores ·
Compras · Relatórios · Automações · Inteligência Artificial · Equipe ·
Integrações · Configurações (menu organizado, sem poluição).

- **Dashboard:** faturamento dia/mês, lucro líquido, margem, pedidos, ticket
  médio, mais vendidos/lucrativos, pendentes/atrasados, estoque baixo/zerado,
  publicidade (investimento, ROAS, ACOS, TACOS), conversão, desempenho por
  marketplace; gráficos; alertas que respondem "o que preciso resolver hoje?".
- **Produtos:** SKU, código de barras/GTIN, NCM, marca, categoria, descrição,
  imagens, custo, preço, margem, estoque, fornecedor, peso, dimensões,
  variações, **compatibilidade peça × moto (marca, modelo, ano, versão)**,
  histórico; busca e filtros avançados; edição em massa.
- **Anúncios:** central multi-marketplace; vínculo produto↔anúncio por SKU,
  código de barras e IDs externos; preço, estoque, status, vendas, problemas;
  ações em massa.
- **Precificação (prioridade):** custo, comissão, taxa fixa, impostos, frete,
  custos operacionais, publicidade, margem/lucro desejado → preço; preço →
  margem real; simulador; comparação entre marketplaces; em massa com prévia
  (atual, custo, recomendado, margem atual/projetada, diferença) e confirmação
  explícita; histórico de preços.
- **Onix (loja física):** fonte confiável de custos. Investigar API, banco
  Firebird, conector local Windows ou exportação; somente leitura; nunca expor o
  banco; funcionar com a última sincronização quando o Onix estiver fora.
- **Estoque e compras:** físico/disponível/reservado/mínimo, parados,
  movimentações, entradas/saídas/ajustes/inventário/transferências, reposição;
  fornecedores, pedidos de compra, giro.
- **Pedidos:** central multi-marketplace, status, atrasos, pagamento,
  expedição, entrega, cancelamentos/devoluções/reembolsos; separação,
  conferência, picking, etiquetas em lote, por transportadora.
- **Financeiro:** receita bruta/líquida, CMV, comissões, taxas, impostos,
  fretes, publicidade, devoluções, despesas, lucro bruto/líquido, margem; por
  produto/categoria/marketplace/período/anúncio/pedido; marcar valores estimados.
- **Marketing:** Mercado Ads primeiro; investimento, impressões, cliques, CPC,
  CTR, vendas, ROAS/ACOS/TACOS; recomendações justificadas; nada automático.
- **Atendimento:** perguntas, mensagens, reclamações, devoluções, garantias,
  disputas, histórico; IA que consulta compatibilidade antes de afirmar.
- **Relatórios:** exportáveis (Excel/CSV).
- **IA:** Agente Gerente (perguntas sobre a operação), Precificação, Estoque,
  Financeiro, Publicidade, Atendimento — dados reais, citar origem, não inventar
  números, explicar, respeitar permissões, registrar ações, pedir aprovação.
- **Automações:** condição → ação → notificação, histórico, liga/desliga.
- **Usuários:** sem cadastro público; superadmin, admin da empresa,
  funcionários; permissões granulares checadas no servidor.
- **Auditoria:** quem, quando, módulo, registro, valor anterior/novo, origem;
  histórico por produto/pedido/anúncio; protegida contra alteração.
- **SaaS:** dados separados por empresa; cadastro de empresas, planos, limites,
  cobrança (arquitetura preparada, cobrança depois).

## Fases sugeridas pelo dono
1 Fundação visual · 2 Estrutura e segurança · 3 Operação · 4 Integrações e
gestão · 5 Inteligência · 6 Comercialização SaaS (ordem pode mudar por
dependência técnica, com explicação).
