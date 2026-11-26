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

### B2. Onix (loja física, fonte dos custos) — 🟡
- **Descoberto em 26/11 (print da tela do Onix):** sistema **Onix, da Foxconn**,
  versão **1.1.2.5**, instalado no Windows; banco de dados **Firebird**
  (arquivo `.fdb`) num servidor da rede da loja. Firebird tem conector oficial
  para Python: o painel pode **ler** os custos direto, com um usuário **só de
  leitura** — sem nenhuma escrita no Onix.
- **Perguntar ao suporte da Foxconn:**
  1. Autorizam outro sistema a **apenas ler** o banco (produtos e custos)?
  2. Podem criar um **usuário do Firebird só de leitura** (somente SELECT)?
  3. Qual a **versão do Firebird** no servidor?
  4. Em quais **tabelas/campos** ficam código do produto, descrição e **custo**
     (ou um dicionário de dados)?
- **Respondido em 26/11:** o banco do Onix fica em **outro computador**; o
  computador do painel tem o Onix instalado e acessa o banco pela rede da loja
  (o painel pode ler daqui do mesmo jeito). O **código do produto no Onix é
  igual ao SKU do Olist** — é por ele que os dois se ligam.
- **Depois, com o painel na internet (B4):** um pequeno conector neste
  computador lê os custos e envia ao painel (o banco nunca fica exposto).
- **Usuário e senha do banco:** nunca no chat — vão no `.env` (eu explico onde).
- **Enquanto isso (já funciona):** exportar do Onix (menu Estoque → relatório de
  produtos com custo) e enviar em **Produtos → Enviar planilha**.
- **Depende disso:** custos atualizados sozinhos, recálculo de preços, sugestão
  de reajuste.

### B3. Shopee — 🟡
- **26/11:** cadastro enviado na Open Platform — **em análise pela Shopee até
  29/11** (o aviso chega por e-mail). A conexão já está pronta no painel
  (Contas conectadas → Shopee), feita com a documentação oficial de autorização.
- **Quando aprovar:**
  1. Console → App List → **Create App** (tipo "Seller In House System").
  2. No app, **Live Redirect URL Domain** = `alyssonov.github.io`.
  3. No `.env`: `SHOPEE_PARTNER_ID` e `SHOPEE_PARTNER_KEY` (de produção).
     **Não cole as chaves no chat.**
  4. Desligar e ligar o painel → **Conectar a Shopee** → escolher 365 dias.
- **Para a próxima etapa (ler pedidos, taxas e envios):** o texto destas
  páginas oficiais (copiar do navegador e colar no chat):
  `v2.order.get_order_list`, `v2.order.get_order_detail`,
  `v2.payment.get_escrow_detail` e `v2.shop.get_shop_info`.
- **Observação:** o uso real (Go Live) pode exigir o domínio com HTTPS e o IP
  fixo do servidor (B4).

### B4. Domínio e servidor — ⚪ (necessário para colocar no ar e para a Shopee)
- Comprar `gerentedoano.com` e contratar a VPS (Hostinger KVM 1, Ubuntu 24.04,
  Brasil). O endereço do sistema será `app.gerentedoano.com`.

### B5. Mercado Ads (publicidade) — ⚪ (próximas etapas)
- No app de desenvolvedor do Mercado Livre, conferir se a permissão de
  **Publicidade** está marcada e se a conta tem **Mercado Ads ativo**. Depois,
  clicar em "Conectar de novo" no painel.
- **Depende disso:** investimento, ROAS, ACOS, TACOS, campanhas.

### B6. Despesas fixas da empresa — ⚪
- Lista mensal (aluguel, salários, contador, sistemas, embalagens…).
- **Depende disso:** lucro **líquido** de verdade (hoje o painel mostra lucro
  por venda, antes das despesas fixas).

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
