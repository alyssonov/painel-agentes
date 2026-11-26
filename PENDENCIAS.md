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
| A1 | **Nova identidade visual** — as imagens novas (layout claro do "Qo_Shop" + paleta escura do "Pi ERP") substituem a identidade atual? | Muda TODAS as telas. | ✅ 26/11: **sim, tema escuro** (estrutura da Foto 1 + cores da Foto 2), tema claro opcional — etapa 2 entregue em 26/11, **aguardando sua aprovação** |
| A2 | **Cadastro de produtos no Gerente do Ano** — hoje o Olist é a fonte da verdade e o painel só LÊ. O Gerente do Ano pode ter cadastro próprio *complementar* (compatibilidade com motos, fornecedor, custo do Onix…) **sem escrever no Olist**? | O documento novo pede cadastro/edição de produtos; a regra proíbe alterar o Olist. | ✅ 26/11: **sim, cadastro complementar**; o Olist continua sem nenhuma escrita |
| A3 | **Estoque nos anúncios** — o painel pode propor (com aprovação) mudar o estoque de um anúncio no Mercado Livre (ex.: pausar quando zera no Onix/Olist)? Ou isso continua só pela integração Olist → Mercado Livre? | Hoje a regra é "o sistema nunca altera estoque". | ✅ 26/11: **não** — estoque continua com o Olist; o painel só avisa divergências |
| A4 | **Estrutura multiempresa (SaaS) agora** — preparar o banco para várias empresas já na próxima etapa? | É bem mais barato fazer agora (31 tabelas) do que depois. | ✅ 26/11: **sim, agora** — etapa 3 do plano |
| A5 | **Desligar o modo simulação** (`DRY_RUN=false`) | Só então respostas, mensagens, preços e anúncios aprovados saem de verdade. | ⚪ quando você se sentir seguro |

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

### B2. Onix (loja física, fonte dos custos) — 🔴
- **O que preciso:**
  1. Nome exato do sistema e da empresa que fornece (aparece em "Sobre" / "Ajuda").
  2. Versão.
  3. Se o fornecedor oferece **API**, **exportação automática** (CSV/Excel) ou
     **acesso ao banco de dados** (perguntar ao suporte: "o Onix usa Firebird?
     Vocês autorizam leitura do banco por outro sistema?").
  4. Se o Onix roda num computador da loja que fica ligado durante o dia.
- **Por quê:** não existe informação pública sobre integração com o Onix. Sem
  saber o que o fornecedor permite, não dá para escolher o caminho seguro.
- **Depende disso:** custos atualizados, recálculo de preços, sugestão de reajuste.
- **Alternativa enquanto isso:** importar uma **planilha de custos exportada do
  Onix** (botão "Enviar planilha" no painel) — funciona já.

### B3. Shopee — 🟡
- Vincular a loja na Open Platform (código de verificação não chegava). Seguir
  o roteiro já combinado (região BR, login principal, conferir e-mail/celular no
  Seller Centre, chamado no suporte).
- **Depende disso:** pedidos/taxas exatas/perguntas/envios da Shopee.
- **Observação:** o uso real (Go Live) exige o domínio com HTTPS e o IP fixo do
  servidor (B4).

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

### B7. Compatibilidade das motopeças — ⚪
- Vocês já têm essa informação em algum lugar (planilha, Onix, fichas do
  Mercado Livre)? O Mercado Livre tem uma API oficial de compatibilidades de
  autopeças que pode servir de base.

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
