# Gerente do Ano — painel de agentes para Mercado Livre + Shopee + Olist ERP

Painel web onde agentes de IA ajudam no dia a dia da loja: responder
perguntas, vigiar estoque, conferir notas, calcular a margem real, enviar
relatórios e mais. O **Olist ERP é a fonte da verdade**: o sistema só lê do
Olist e nunca mexe em estoque ou nota fiscal.

> **Situação atual: Fase 5.** Olist (somente leitura), Mercado Livre,
> Respondedor de perguntas, Vigia de estoque, Conferência de NF-e, Margem
> real e Relatório diário por e-mail. WhatsApp e PDFs semanal/mensal vêm a
> seguir; a Shopee entra quando o cadastro de desenvolvedor for aprovado.

## Requisitos

- Python 3.12 ou mais novo. No Windows, ao instalar pelo site python.org, marque
  **"Add python.exe to PATH"**.
- (Produção, Fase 8) Docker e um VPS

## Comandos

Todos os comandos são rodados no terminal, **dentro da pasta do projeto**, com
`python tarefas.py <comando>`. Funciona no Windows, Mac e Linux.

> No Windows, se `python` não for reconhecido, use `py` no lugar
> (ex.: `py tarefas.py install`). Quem tem `make` (Mac/Linux) pode usar
> `make <comando>`, que faz a mesma coisa.

## Instalação (primeira vez)

```bash
python tarefas.py install
```

Cria a pasta `.venv` e instala as dependências (demora alguns minutos).

```bash
python tarefas.py setup
```

Cria o arquivo `.env` com chaves de segurança novas. **Guarde uma cópia segura
do `.env`**, principalmente da `ENCRYPTION_KEY`: sem ela, os tokens das contas
conectadas não podem ser lidos e será preciso reconectar.

```bash
python tarefas.py migrate
```

Cria o banco de dados (arquivo `data/painel.db`).

```bash
python tarefas.py user
```

Pede e-mail, nome e senha (mínimo 10 caracteres, com letras e números). A
senha não aparece enquanto você digita, é normal. Rode de novo com o mesmo
e-mail para trocar a senha; isso também desconecta as sessões abertas.

```bash
python tarefas.py seed-demo
```

Carrega a loja fictícia (produtos, pedidos de hoje, perguntas, alertas).

## Uso (dia a dia) — jeito fácil

No Windows, dê **dois cliques em `Iniciar painel.bat`** (na pasta do projeto).
Ele faz tudo sozinho:

1. busca a versão mais nova no GitHub (se não tiver internet, segue com a atual);
2. instala dependências novas, se houver;
3. atualiza o banco de dados;
4. liga o painel e abre o navegador em http://127.0.0.1:8000.

Deixe a janela preta aberta enquanto usa o painel. Para desligar, feche a
janela (ou `Ctrl+C`).

Dica: clique com o botão direito em `Iniciar painel.bat` → **Mostrar mais
opções** → **Enviar para** → **Área de trabalho (criar atalho)**. Assim o
painel liga direto da área de trabalho.

Em qualquer sistema, o mesmo pode ser feito com:

```bash
python tarefas.py iniciar
```

## Uso (modo desenvolvimento)

```bash
python tarefas.py dev
```

Abra http://localhost:8000 no navegador e entre com o usuário criado. Para
parar o painel, aperte `Ctrl+C` no terminal.

Em outro terminal (opcional na Fase 1):

```bash
python tarefas.py worker
```

Na Fase 1 o worker só renova os dados de simulação quando o dia vira. O próprio
painel também faz isso ao iniciar.

## Conectar o Olist ERP (somente leitura)

1. No Olist: **Menu > Configurações > aba Geral > Aplicativos > + novo
   aplicativo**. Na URL de redirecionamento, coloque
   `http://localhost:8000/integracoes/olist/retorno`. Nas permissões, marque
   só o necessário (pedidos, produtos, estoque, notas fiscais). Salve e copie
   o Client ID e o Client Secret.
2. No arquivo `.env` (abra com `notepad .env` no terminal, na pasta do
   projeto), preencha `OLIST_CLIENT_ID` e `OLIST_CLIENT_SECRET`. **Nunca**
   envie essas chaves por chat ou e-mail.
3. Ligue o painel e clique em **Contas conectadas** (canto inferior esquerdo)
   → **Conectar o Olist**. Entre com o login do Olist e autorize.
4. A importação começa sozinha (pedidos dos últimos 15 dias, produtos,
   estoque e notas). Depois, o painel busca novidades a cada 10 minutos
   enquanto estiver ligado.

Bom saber:
- A autorização do Olist vale **1 dia sem uso**. Se o painel ficar desligado
  por mais tempo, aparece um alerta: é só clicar em **Conectar de novo**.
- Ao conectar, a loja fictícia da simulação é apagada.
- Em **Contas conectadas** também fica a **alíquota de impostos** usada na
  margem real (informada pela contabilidade).

## Conectar o Mercado Livre

1. Em developers.mercadolivre.com.br/devcenter (com a conta **da loja**), o
   aplicativo deve ter:
   - URI de redirect: `https://alyssonov.github.io/painel-retorno/mercadolivre/`
     (o Mercado Livre exige HTTPS público; essa página, no repositório
     `alyssonov/painel-retorno`, só repassa o login para o painel local);
   - fluxos **Authorization Code** e **Refresh Token**, com **PKCE**;
   - permissões: Usuários (leitura), Comunicações pré e pós-venda (leitura e
     escrita), Publicação (leitura), Métricas (leitura), Venda e envios
     (leitura); o resto sem acesso. Sem tópicos de notificação.
2. No `.env`, preencha `ML_CLIENT_ID` e `ML_CLIENT_SECRET`.
3. Em **Contas conectadas**, clique em **Conectar o Mercado Livre** e autorize.

O painel passa a ler, a cada 5 minutos: perguntas, reputação e a taxa de cada
venda (que entra na margem real). Nesta etapa **nada é respondido**.

## Respondedor de perguntas (Agente 1)

1. Crie uma chave em console.anthropic.com (organização da empresa, cartão em
   Billing e **limite mensal de gasto**) e coloque no `.env`:
   `ANTHROPIC_API_KEY=...`.
2. Em **Agentes**, clique em **Ativar** no Respondedor de perguntas e ajuste
   as **Regras das respostas** (nome da loja, tom, prazo de envio, garantia).
3. A cada 3 minutos ele lê as perguntas novas e sugere uma resposta usando a
   ficha do anúncio, o estoque do Olist e as regras da loja.
4. Em **Perguntas**, você **Aprova**, **Edita** ou **Responde você mesmo**.
   Pedidos de desconto/negociação, confiança baixa ou assuntos fora da ficha
   ficam marcados como **Precisa de você**.
5. Modo **Automático** (opcional, em Agentes): só respostas com confiança
   **alta** saem sozinhas; o resto continua esperando você.

Custo: usa o modelo mais barato (Claude Haiku), com cache de perguntas
repetidas e teto diário (`AI_DAILY_BUDGET_BRL`). O gasto estimado do dia
aparece na Visão geral e em Agentes. Com `DRY_RUN=true`, as respostas
aprovadas **não são enviadas** (ficam marcadas como simulação).

## Vigia de estoque, Conferência de NF-e e Margem real (Fase 4)

Agentes **sem IA** (custo zero), que só **leem e avisam** — nunca alteram
estoque, pedidos ou notas. Ative cada um em **Agentes**; as regras (dias de
alerta, horas sem nota, margem mínima...) ficam em "Regras deste agente".

- **Vigia de estoque** (a cada 30 min): média de vendas por dia, previsão de
  quando cada produto acaba, produtos esgotados e divergência entre o estoque
  do Olist e o dos anúncios do Mercado Livre (anúncios Full ficam de fora).
  Página: **Estoque e preços**.
- **Conferência de NF-e** (a cada 30 min): pedido "Pronto para envio" sem
  nota (urgente), pedido parado sem nota há mais de X horas e notas
  rejeitadas/denegadas. A correção é sempre feita no Olist.
- **Margem real** (a cada hora): lucro por produto e por venda (preço − taxa
  do marketplace − frete − custo do Olist − impostos), avisos de prejuízo,
  margem abaixo da mínima e produtos sem custo no Olist. Página: **Margem
  real** (hoje, 7 ou 30 dias).

- **Alerta de despacho** (a cada 15 min): pedidos do Mercado Livre que
  precisam ser despachados dentro do prazo de aviso (padrão 24 h) e os que já
  passaram do prazo (urgente). O prazo vem do próprio Mercado Livre. Envios
  Full ficam de fora.
- **Termômetro da reputação** (a cada hora): taxas de reclamações, atrasos e
  cancelamentos do Mercado Livre comparadas com o limite do "verde" (ajustável
  em Agentes; padrões 2%, 10% e 1,5%). Avisa ao chegar a 80% do limite e
  alerta quando passa dele.

Os alertas aparecem em "O que pede atenção" e **somem sozinhos** quando o
problema é resolvido.

## Relatório diário por e-mail (Fase 5)

1. No Gmail que vai **enviar** os relatórios: myaccount.google.com >
   Segurança > ative a **Verificação em duas etapas** > **Senhas de app** >
   crie "Painel de Agentes" e copie a senha de 16 letras.
2. No `.env`: `SMTP_USER=seu@gmail.com` e `SMTP_PASSWORD=<senha de app>`.
   Feche e abra o painel.
3. Em **Relatórios**, cadastre quem recebe (e-mails separados por vírgula) e
   clique em **Enviar agora (teste)**.
4. Em **Agentes**, ative o **Relatório diário**. O horário (padrão 19h) fica
   em "Regras deste agente".

O resumo traz vendas, faturamento (comparado com ontem), margem real, mais
vendidos, perguntas, o que pede atenção e o que cada agente fez. Se o
computador estiver desligado no horário, sai assim que o painel ligar (até o
fim do dia), e nunca duas vezes. Falha no envio vira alerta. Sem custo de IA.

## Anúncios (Fase 7 — agente Criador e otimizador de anúncios)

No menu **Anúncios**: a nota de qualidade de cada anúncio no Mercado Livre
(Básica, Satisfatória, Profissional) e o que o Mercado Livre pede para
melhorar, com o link oficial para corrigir. O agente escolhe os anúncios
fracos que mais vendem e sugere uma descrição nova (e um título novo, só para
anúncios sem vendas — regra do Mercado Livre), usando apenas a ficha técnica.
Você lê, edita e clica em **Aprovar e publicar** — o agente nunca publica
sozinho, nem no modo automático. Limites (nota mínima e sugestões por semana)
em **Agentes**.

## Preços (Fase 7 — agente Monitor de preços)

O agente compara cada anúncio com as **referências de preço oficiais do Mercado
Livre** e calcula o lucro no preço novo com o custo do Olist. Ele só sugere
baixar se o lucro continuar acima da **margem mínima** (padrão 15%, em
**Agentes → Monitor de preços**) e sugere subir quando você está abaixo da
concorrência. As sugestões aparecem em **Estoque e preços → Sugestões de
preço**: aprove (pode editar o valor) ou descarte. Anúncios em promoção, com
variações ou com a automatização de preços do Mercado Livre ficam de fora.
Nada muda no modo simulação.

## Sugestão de compra

Em **Estoque e preços**, o quadro **Sugestão de compra** mostra o que repor,
quantas unidades e o custo estimado (custo do Olist), com o botão **Baixar
planilha** para mandar ao fornecedor. Em **Agentes → Vigia de estoque** você
ajusta quantos dias o fornecedor leva para entregar e para quantos dias de
vendas quer comprar.

## Produtos parados

Também em **Estoque e preços**: produtos com estoque que não vendem há mais de
60 dias, ou com estoque para mais de 180 dias, e quanto dinheiro está
empatado neles (estoque × custo do Olist). Para isso o painel importa, aos
poucos e em segundo plano, até 120 dias de vendas antigas do Olist
(`OLIST_HISTORY_DAYS`). Os dois limites se ajustam em **Agentes → Vigia de
estoque**.

## Conferir uma venda

Em **Margem real**, digite o número da venda (do Mercado Livre, do pacote ou do
pedido no Olist) em **Conferir uma venda**. O painel mostra a conta linha por
linha: preço, comissão, frete e tarifas, **quanto você recebe** (Mercado Pago),
custo do Olist, impostos e lucro — para comparar com o "detalhe da venda" do
Mercado Livre. Sem número, a página lista as vendas recentes.

## Gráficos

Em **Margem real → Gráficos**: faturamento e lucro real dia a dia (30 ou 90
dias), com o melhor dia, os dias no prejuízo (em vermelho) e os números em
tabela. Passe o mouse em cada barra para ver o valor do dia.

## Previsão de recebimentos

Em **Margem real → Previsão de recebimentos**: quanto dinheiro das vendas do
Mercado Livre fica disponível no Mercado Pago em cada dia dos próximos 30 dias
(valor líquido, já sem taxas e frete), o total a liberar e o que foi liberado
na última semana. As datas vêm do Mercado Pago e podem mudar (entrega atrasada,
reclamação); o painel confere de novo a cada poucas horas. Usa o mesmo acesso
do Mercado Livre conectado: não precisa conectar nada novo.

## Curva ABC

Em **Margem real → Curva ABC dos produtos**: os produtos do que mais vende
para o que menos vende, separados em **A** (os poucos que fazem 80% do
faturamento), **B** (os próximos 15%) e **C** (o resto). Dá para ver por
faturamento ou por lucro real, em 30, 90 ou 120 dias, e baixar a planilha.

## Avaliações e devoluções (Fase 7)

Em **Pós-venda → Avaliações e devoluções por produto**: para cada produto,
nota das avaliações, avaliações negativas (com o texto), reclamações e
devoluções por motivo e a taxa de problema (reclamações ÷ vendidos), nos
últimos 90 dias. Quando um produto acumula queixas, a IA resume uma vez por
semana o que se repete e sugere o que corrigir. Ative o agente **Avaliações e
devoluções** em **Agentes**. Ele só lê e avisa.

## Pós-venda (Fase 6)

Com o Mercado Livre conectado, o painel lê as **mensagens dos compradores** e
as **reclamações abertas** (sem marcar nada como lido no Mercado Livre).

1. Em **Agentes**, ative o **Pós-venda** (padrão: manual).
2. Em **Pós-venda** aparecem:
   - **Reclamações abertas**: o problema, se é a sua vez de agir, até quando e
     se afeta a reputação. Elas são resolvidas no próprio Mercado Livre.
   - **Mensagens aguardando você**: a conversa, o pedido, a situação do envio
     e a resposta sugerida. **Aprovar**, **Editar**, **Responder eu mesmo** ou
     **Tirar da fila**. Limite de 350 caracteres (regra do Mercado Livre).
3. Troca, devolução, defeito, cancelamento e pedidos com reclamação sempre vão
   para você, sem sugestão automática.

No modo simulação nada é enviado ao comprador.

## Modo simulação (`DRY_RUN`)

- Ligado por padrão (`DRY_RUN=true` no `.env`).
- O painel mostra uma faixa amarela **MODO SIMULAÇÃO** no topo.
- **Nenhuma** ação real é enviada a clientes ou lojas.
- Sem conta conectada, mostra uma loja fictícia. Com o Olist conectado,
  mostra os dados reais (só leitura) e a faixa avisa isso.
- Os dados fictícios ficam marcados (`is_demo`) e a simulação se recusa a
  rodar se o `DRY_RUN` estiver desligado ou se houver uma conta real
  conectada.
- Só desligue quando o dono da loja decidir, editando o `.env`.

## Configuração (`.env`)

Todas as opções estão comentadas em `.env.example`. As principais:

| Variável | Para que serve |
|---|---|
| `DRY_RUN` | Modo simulação (padrão `true`) |
| `SECRET_KEY` | Assina o cookie de login |
| `ENCRYPTION_KEY` | Criptografa os tokens das contas conectadas |
| `SESSION_COOKIE_SECURE` | `true` em produção (HTTPS) |
| `DATABASE_URL` | SQLite no desenvolvimento, PostgreSQL em produção |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCK_MINUTES` | Bloqueio após senhas erradas |
| `AI_DAILY_BUDGET_BRL` | Teto de gasto diário com IA |

O `.env` **nunca** vai para o git (está no `.gitignore`).

## Testes

```bash
python tarefas.py test
```

Os testes usam bancos temporários e nunca acessam a internet.

## Segurança (o que já existe)

- Senhas com hash Argon2, bloqueio temporário após 5 tentativas erradas,
  mensagem de erro que não revela se o e-mail existe.
- Sessão em cookie assinado, `HttpOnly`, `SameSite=Lax` (e `Secure` em
  produção), expira em 12 horas; trocar a senha derruba as sessões antigas.
- Proteção CSRF em todos os formulários.
- Cabeçalhos de segurança (CSP restrita, sem iframes, sem cache de páginas com
  dados).
- Tokens OAuth guardados criptografados (usado a partir da Fase 2).
- Registro de auditoria só de inserção: logins, criação de usuário, carga da
  simulação. Tentativas de alterar ou apagar são bloqueadas.

## Estrutura

```
app/
  config.py         configurações (.env)
  main.py           painel web (FastAPI)
  worker.py         agendador dos agentes
  cli.py            comandos de administração
  core/             banco, segurança, auditoria, datas, dinheiro
  models/           tabelas do banco
  services/         regras de negócio (visão geral, margem, regras da loja)
  agents/           agentes (registro; lógica a partir da Fase 3)
  fake_data/        dados de simulação
  web/              rotas, templates, CSS, htmx
alembic/            migrações do banco
tests/              testes automáticos
docs/               arquitetura e (na Fase 8) guia de implantação
tarefas.py          atalhos dos comandos (substitui o make)
```
