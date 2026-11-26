# PENDÊNCIAS — o que depende de você

Tudo que o desenvolvimento precisa de você (informação, acesso, decisão ou
autorização) fica aqui. Atualizado a cada entrega. **Nunca cole senhas, tokens
ou chaves no chat**: quando for preciso, o passo a passo diz onde colocar
(arquivo `.env`, que não vai para o GitHub).

Situações: 🔴 bloqueia algo · 🟡 em andamento · ⚪ ainda não necessário · ✅ resolvido

Última atualização: 26/11/2026

---

## A. Decisões (preciso da sua resposta antes de mexer)

| # | Decisão | Por que importa | Situação |
|---|---|---|---|
| A1 | **Nova identidade visual** — as imagens novas (layout claro do "Qo_Shop" + paleta escura do "Pi ERP") substituem a identidade atual? | Muda TODAS as telas. | ✅ 26/11: **sim, tema escuro** (estrutura da Foto 1 + cores da Foto 2), tema claro opcional — etapa 2 entregue em 26/11 |
| A2 | **Cadastro de produtos no Gerente do Ano** — hoje o Olist é a fonte da verdade e o painel só LÊ. O Gerente do Ano pode ter cadastro próprio *complementar* (compatibilidade com motos, fornecedor, custo do Onix…) **sem escrever no Olist**? | O documento novo pede cadastro/edição de produtos; a regra proíbe alterar o Olist. | ✅ 26/11: **sim, cadastro complementar**; o Olist continua sem nenhuma escrita |
| A3 | **Estoque nos anúncios** — o painel pode propor (com aprovação) mudar o estoque de um anúncio no Mercado Livre (ex.: pausar quando zera no Onix/Olist)? Ou isso continua só pela integração Olist → Mercado Livre? | Hoje a regra é "o sistema nunca altera estoque". | ✅ 26/11: **não** — estoque continua com o Olist; o painel só avisa divergências |
| A4 | **Estrutura multiempresa (SaaS) agora** — preparar o banco para várias empresas já na próxima etapa? | É bem mais barato fazer agora (31 tabelas) do que depois. | ✅ 26/11: **sim, agora** — feito na etapa 3 |
| A5 | **Desligar o modo simulação** (`DRY_RUN=false`) | Só então respostas, mensagens, preços e anúncios aprovados saem de verdade. | ⚪ quando você se sentir seguro |

### Etapa 3 — o que conferir (26/11)
- Ao ligar o painel, ele faz sozinho uma **cópia de segurança do banco** em
  `data/backups/` e atualiza a estrutura (todos os dados passam a pertencer à
  empresa "Minha loja"). Confira se tudo continua aparecendo igual.
- Menu **Administração → Equipe**: se quiser, cadastre o dono ou um
  funcionário com outro papel e teste entrar com ele.
- Menu **Administração → Histórico**: mudanças aparecem com "antes → depois".

### Etapa 4 — o que conferir (26/11)
- **Pedidos** (busca, filtros, "Atrasado", planilha) e **Lista de separação**
  (imprimir).
- **Produtos**: preencher compatibilidade/local de alguns itens e testar a
  busca pela moto; se tiver, enviar a planilha de custos do Onix.
- **Preços em massa**: ver a prévia com uma regra (ex.: margem 20%), criar as
  mudanças e aprovar uma em lote (no modo simulação nada é enviado).
- Ficaram para depois, porque dependem da documentação oficial: **etiquetas**
  de envio e **pausar/reativar/editar anúncios em massa**.

### Etapa 5 (parte sem dependências) — o que conferir (26/11)
- **Financeiro → Despesas:** lançar as despesas mensais da loja; ver o
  **DRE do mês** com o lucro líquido.
- **Operação → Compras:** cadastrar os fornecedores (com prazo) e registrar um
  pedido usando a sugestão; ver a coluna "A caminho" na sugestão de compra.

## B. Informações e acessos

### B1. NorteUp — ✅ (calculadora feita; o resto do app não é necessário por agora — decisão de 26/11)
- **Recebido 26/11:** print da calculadora fácil → reproduzida no menu
  **Precificação** (custo, custo fixo/rateio, margem 10/20/30/personalizada,
  impostos MEI/Simples/CPF, anúncios pagos, Shopee/ML Clássico/ML
  Premium/TikTok Shop, taxa de devolução da Shopee, desconto, afiliados, preço
  final ,90, simuladores e últimas 10 simulações).
- **Também confira:** a **tabela de taxas** da Shopee e do TikTok Shop
  (Precificação → Tabela de taxas). Os valores vieram de fontes públicas de
  2026 que **divergem** em alguns pontos (ex.: taxa fixa da Shopee até
  R$ 79,99: R$ 4,00 ou R$ 4,50). Compare com a Central do Vendedor e corrija lá
  mesmo. O Mercado Livre usa a tarifa **oficial** pela API (quando conectado).

### B2. Onix (loja física, fonte dos custos) — 🟡 (leitura pronta; falta ligar)
- **26/11:** o fornecedor do Onix mandou a API de produtos (consulta por
  código) e a explicação dos campos. O painel já lê custo, marca, qtd. mínima
  e código de fábrica de cada produto (só leitura, nada muda no Onix).
- **Para ligar (no computador da loja):** no `.env`, preencha
  `ONIX_API_URL` com o endereço que o fornecedor passou, **só a parte inicial**
  (ex.: `http://servidor:8020`, sem `/api-produto/...`). Feche e ligue o painel.
  Depois: Contas conectadas → Onix → **Atualizar custos agora**.
- ✅ **Custo a usar (confirmado em 26/11):** o campo **"Custo"** do Onix
  (`custo_prod`) — já é o padrão; não precisa mexer em `ONIX_COST_FIELD`.
- **Segurança — decisão do dono (26/11):** a API do Onix fica **sem senha**
  (aberta na internet, http). Risco aceito: quem tiver o endereço vê os custos.
  Se mudar de ideia, o painel aceita uma chave quando o fornecedor criar.
- **Melhoria opcional (pedir ao fornecedor):** uma consulta que liste vários
  produtos de uma vez (ou só os alterados desde uma data) deixaria a leitura
  bem mais rápida que um produto por vez.

### B3. Shopee — 🟡 (aguardando a liberação do app, até 24 h)
- **26/11:** perfil de desenvolvedor **aprovado**; app "Atacadao" criado (tipo
  Seller In House System, domínio de retorno `https://alyssonov.github.io`, sem
  dados pessoais de compradores) e **enviado para liberação (Go Live)**. O
  resultado chega por e-mail em até 24 h.
- **Quando liberar:** a Shopee entrega o **Partner ID e a Key de produção**
  (os de hoje no `.env` são os de **teste**). Trocar `SHOPEE_PARTNER_ID` e
  `SHOPEE_PARTNER_KEY` no `.env` (Mac e/ou Windows), desligar e ligar o painel
  → **Conectar a Shopee** → 365 dias. **Não cole a Key no chat.**
- **Lista de IPs liberados (IP Whitelist):** a Shopee exige para dados
  sensíveis. No computador o IP da internet pode mudar; no servidor (B4) é fixo.
- ✅ **Leitura pronta (26/11):** pedidos (situação e prazo de despacho), taxas e
  valor líquido de cada venda — começa sozinha assim que a Shopee for conectada.
  Depois de conectar, confira em **Contas conectadas** (Última atualização da
  Shopee) e no quadro **Envios de hoje** com o filtro Shopee.

### B4. Domínio e servidor — 🟡 (tudo pronto no projeto; falta comprar)
- **Pronto (26/11):** o painel já roda em servidor (PostgreSQL + HTTPS + cópia
  diária do banco + atualização automática), testado de ponta a ponta. Guia
  passo a passo: **`docs/IMPLANTACAO.md`**.
- **Decisão do dono (26/11):** domínio `gerentedoano.com`, comprado em
  **dezembro/2026**; por enquanto só o dono acessa de fora.
- **Falta:** comprar o domínio e a VPS (Hostinger KVM 1, São Paulo, Ubuntu
  24.04) e seguir o guia. O painel fica em `app.gerentedoano.com`.
- **Resolve:** contas que desconectam (o Olist cai depois de 1 dia com o
  computador desligado) e acesso pelo celular.

### B5. Mercado Ads (publicidade) — ⚪ (próximas etapas)
- No app de desenvolvedor do Mercado Livre, conferir se a permissão de
  **Publicidade** está marcada e se a conta tem **Mercado Ads ativo**. Depois,
  clicar em "Conectar de novo" no painel.
- **Depende disso:** investimento, ROAS, ACOS, TACOS, campanhas.

### B6. Despesas fixas da empresa — 🟡
- **Pronto no painel (26/11):** Financeiro → **Despesas** (lançar) e **DRE do
  mês** (lucro líquido de verdade).
- **Falta:** lançar as despesas mensais da loja (aluguel, salários, contador,
  sistemas, embalagens, publicidade…). Quem tiver o papel Financeiro ou Gerente
  também pode lançar.

### B7. Compatibilidade das motopeças — 🟡
- **Pronto no painel (26/11):** campo de compatibilidade em cada produto e na
  planilha (Produtos). A busca já encontra pela moto.
- **Falta saber:** vocês já têm essa informação em algum lugar (planilha, Onix,
  fichas do Mercado Livre)? Se estiver numa planilha, dá para enviar de uma vez.
  O Mercado Livre e a Shopee têm APIs oficiais de compatibilidade de autopeças
  que podem servir de base depois (consulta à documentação antes).

### B8. Relatório diário por e-mail — ⚪
- Senha de app do Gmail e e-mails de quem recebe (passo a passo no README).

### B9. WhatsApp — ⚪
- Conta no WhatsApp Business Platform (Meta) para o relatório por WhatsApp.

## C. Resolvido

| Item | Quando |
|---|---|
| Conexão Mercado Livre e Olist | 11/2026 |
| Lucro real = valor recebido (Mercado Pago) − custo − impostos | 11/11/2026 |
| Leitura de reclamações, avaliações, referências de preço | 11/2026 |
