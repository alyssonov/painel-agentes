# Colocar o Gerente do Ano no ar (servidor + domínio)

Guia da Etapa 7, passo a passo. Com o painel num servidor ligado 24 horas:

- as contas **não desconectam mais**, porque o servidor renova os acessos sozinho. A exceção é a Shopee, que pede nova autorização uma vez por ano;
- você acessa de qualquer lugar, inclusive pelo celular, em `https://app.gerentedoano.com`;
- não precisa mais deixar a janela preta aberta no computador.

O que já vem pronto no projeto:

| Arquivo | Para quê |
|---|---|
| `Dockerfile` | monta o painel para o servidor |
| `docker-compose.yml` | liga tudo: banco (PostgreSQL), painel, agendador, HTTPS (Caddy) e cópia diária do banco |
| `deploy/instalar.sh` | primeira instalação (um comando) |
| `deploy/atualizar.sh` | atualização automática (roda sozinho a cada 5 minutos) |
| `deploy/backup.sh` | cópia do banco todo dia, guardando os últimos 14 dias |
| `python -m app.cli copiar-banco` | leva os dados do computador para o servidor |

Tudo isso foi testado com os 5 contêineres ligados: HTTPS, login, todas as páginas,
cópia dos dados, atualização e volta de um backup.

---

## 0. O que comprar

**Servidor (VPS) recomendado:** Hostinger, com data center em **São Paulo**.

- **KVM 1** (4 GB de memória) dá conta do painel com folga para uma loja.
- **KVM 2** (8 GB, cerca de **R$ 43 por mês** no preço de tabela) é para quando vierem mais empresas (Etapa 8). Dá para trocar de plano depois sem reinstalar.
- Os preços mais baixos valem para contratos longos, e a renovação sai mais cara. Confira no checkout.
- Sistema: **Ubuntu 24.04**, versão "limpa", sem painel de controle e sem aplicativo pré-instalado.
- Anote o **IP do servidor** e a **senha do root** que a Hostinger mostrar.

Alternativa sem contrato longo: Vultr em São Paulo (cerca de US$ 20 por mês com 4 GB, pago mês a mês).

**Domínio:** `gerentedoano.com`. Pode ser comprado na própria Hostinger, o que facilita o passo 1.
O painel fica em **`app.gerentedoano.com`**. O endereço principal fica livre para uma página de apresentação no futuro.
Vale registrar também o `gerentedoano.com.br` (registro.br, cerca de R$ 40 por ano) para proteger a marca.

## 1. Apontar o domínio para o servidor

No painel do domínio, na parte de **DNS**, crie um registro do tipo **A**:

| Tipo | Nome | Aponta para |
|---|---|---|
| A | `app` | IP do servidor |

Pode levar de alguns minutos a algumas horas para valer.

## 2. Entrar no servidor

No Windows, abra o **PowerShell** e digite (troque pelo IP do seu servidor):

```
ssh root@IP_DO_SERVIDOR
```

Responda `yes` na primeira vez e digite a senha do root. Ela não aparece enquanto você digita, e isso é normal.

## 3. Instalar o Docker e fechar as portas que não são usadas

Dentro do servidor, cole um comando de cada vez:

```
curl -fsSL https://get.docker.com | sh
```

```
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

O primeiro comando é o instalador oficial do Docker. O segundo deixa abertas só a entrada do SSH e a do site (80 e 443).

## 4. Baixar o painel

```
git clone https://github.com/alyssonov/painel-agentes.git /opt/gerente
```

## 5. Levar o `.env` do computador

Num **segundo** PowerShell, no seu computador, dentro da pasta do projeto:

```
scp .env root@IP_DO_SERVIDOR:/opt/gerente/.env
```

O `.env` precisa ser **o mesmo** do computador. A chave `ENCRYPTION_KEY` é o que permite ler as contas conectadas que estão guardadas no banco.

## 6. Instalar

De volta ao PowerShell do servidor:

```
cd /opt/gerente && sh deploy/instalar.sh app.gerentedoano.com
```

O script faz o seguinte:
- acerta o `.env` para o servidor: domínio, senha do banco (gerada sozinha) e endereços de retorno do login;
- liga tudo;
- programa a atualização automática.

Na primeira vez leva alguns minutos. Depois, abra `https://app.gerentedoano.com`: o painel aparece com o cadeado. Ele ainda está com a loja de simulação, porque os seus dados chegam no próximo passo.

## 7. Levar os seus dados do computador

1. **Feche o painel no computador** (a janela preta).
2. No PowerShell do **computador**, na pasta do projeto:
   ```
   scp data\painel.db root@IP_DO_SERVIDOR:/opt/gerente/data/painel-do-computador.db
   ```
3. No PowerShell do **servidor**:
   ```
   cd /opt/gerente && docker compose stop web worker
   ```
   ```
   docker compose run --rm --no-deps web python -m app.cli copiar-banco --de /app/data/painel-do-computador.db --apagar-destino
   ```
   ```
   docker compose start web worker
   ```

A opção `--apagar-destino` apaga do servidor só a loja de simulação. O comando mostra quantos registros copiou. Seus usuários, senhas, cadastro complementar, despesas, compras e histórico passam todos.

## 8. Atualizar os endereços de retorno nas lojas

O login das contas agora volta direto para o domínio. Em cada aplicativo, troque o endereço de retorno:

| Onde | O que colocar |
|---|---|
| **Olist** → Configurações → Aplicativos → seu aplicativo → URL de redirecionamento | `https://app.gerentedoano.com/integracoes/olist/retorno` |
| **Mercado Livre** → DevCenter → seu aplicativo → URIs de redirect (acrescente) | `https://app.gerentedoano.com/integracoes/mercadolivre/retorno` |
| **Shopee** → Console → App → Live Redirect URL Domain | `app.gerentedoano.com` |

Depois, em **Contas conectadas** no painel novo, clique em **"Conectar de novo"** no Olist e no Mercado Livre. A Shopee também, se já estiver conectada.

## 9. Aposentar o painel do computador

A partir daqui use só o endereço `https://app.gerentedoano.com`.

**Não ligue o painel do computador ao mesmo tempo que o do servidor.** O Mercado Livre e a Shopee aceitam cada renovação de acesso uma vez só. Dois painéis renovando derrubam a conexão um do outro.

O arquivo `data\painel.db` do computador fica como cópia antiga. Pode guardar.

---

## Dia a dia

- **Atualizações:** chegam sozinhas. A cada 5 minutos o servidor confere o GitHub e, havendo versão nova, faz uma cópia do banco e religa. É só recarregar a página (F5).
- **Cópia do banco:** feita todo dia às 00:30 (horário de Brasília) e antes de cada atualização, na pasta `/opt/gerente/backups` do servidor. São guardados os últimos 14 dias.
- **Cópia fora do servidor:** uma vez por mês, no PowerShell do computador:
  ```
  scp -r root@IP_DO_SERVIDOR:/opt/gerente/backups .
  ```
  Vale também ligar os **backups/snapshots** do próprio VPS no painel da Hostinger.

## Se precisar

| Situação | Comando (no servidor, dentro de `/opt/gerente`) |
|---|---|
| Ver o que o painel está fazendo | `docker compose logs -f web` (sair: Ctrl+C) |
| Ver o agendador (leituras, agentes) | `docker compose logs -f worker` |
| Ver se está tudo ligado | `docker compose ps` |
| Religar tudo | `docker compose restart` |
| Forçar atualização agora | `sh deploy/atualizar.sh --forcar` |
| Criar usuário / trocar senha | `docker compose exec web python -m app.cli create-user` |

**Voltar uma cópia do banco.** Troque `ARQUIVO` pelo nome do arquivo que está em `backups/`:

```
docker compose stop web worker
```
```
docker compose exec -T db pg_restore -U painel -d painel --clean --if-exists --no-owner < backups/ARQUIVO.dump
```
```
docker compose start web worker
```

## Segurança

- O `.env` do servidor fica legível só pelo root (o instalador acerta isso). Nunca o coloque no GitHub.
- Só as portas 22 (SSH), 80 e 443 ficam abertas. O banco não fica exposto na internet.
- O HTTPS é renovado sozinho. O cookie de login só trafega com HTTPS, e o navegador é instruído a sempre usar HTTPS.
- Recomendado: trocar a senha do root por uma **chave SSH**. A Hostinger tem essa opção no painel da VPS.
- O modo simulação (`DRY_RUN=true`) continua valendo no servidor até você decidir desligar.
