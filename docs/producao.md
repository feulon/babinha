# Colocar o bába no ar

Roteiro para o capitao rodar o bába no proprio notebook (Ubuntu Server), em duas partes:

1. **Parte 1 - v0 local (comece aqui).** O app roda no notebook e os celulares na **mesma wifi**
   abrem `http://IP-DO-NOTEBOOK`. Sem dominio, sem tunel, sem conta em nenhum servico, sem custo
   alem da energia do notebook.
2. **Parte 2 - publicar na internet (opcional, depois).** Cloudflare Tunnel no dominio do
   capitao, para jogar de fora de casa e instalar o app (PWA) no celular. Usa o dominio que ele
   ja tem; o tunel e o plano gratis da Cloudflare.

O bába e amador e e usado so entre amigos: o roteiro aceita o risco de "o notebook caiu" e
procura o que e barato e seguro de verdade: segredos de verdade, modo de producao, porta do banco
fechada, app so na rede local, demonstracao e relogio de teste desligados e um backup simples.

Arquivos deste repositorio usados aqui:

| Arquivo | Para que serve |
| --- | --- |
| `scripts/baba.sh` | um comando para subir, criar o presidente, ver logs, atualizar |
| `compose.prod.yaml` | app + Postgres 17 + comprovantes no disco (parte 1) |
| `compose.prod.tunnel.yaml` | parte 2: adiciona o Cloudflare Tunnel e tira a porta da rede |
| `.env.production.example` | modelo das variaveis; o `baba.sh` cria o `.env.production` a partir dele |
| `scripts/backup.sh`, `scripts/restore.sh` | backup para uma pasta ou disco externo e restauracao |

O `compose.yaml` (desenvolvimento) nao e usado em producao.

---

# Parte 1 - v0 local

## 1. Preparar o notebook

1. **Instale o Docker Engine** com o plugin Compose, pelo guia oficial:
   <https://docs.docker.com/engine/install/ubuntu/>. Depois:

   ```sh
   sudo usermod -aG docker "$USER"      # saia e entre de novo na sessao
   sudo systemctl enable --now docker   # sobe junto com o notebook
   docker run --rm hello-world
   sudo apt install git openssl
   ```

2. **Baixe o repositorio**: `git clone <endereco do repositorio> ~/baba && cd ~/baba`.
3. **Ligue o notebook no cabo de rede** se puder (wifi serve, mas cai mais).
4. **Reserve um IP fixo** para o notebook no roteador (em geral "reserva de DHCP" ou "IP fixo",
   pelo endereco MAC dele). Sem isso o IP pode mudar e o link dos celulares deixa de funcionar.
   Descubra o IP atual com `hostname -I`.

## 2. Subir (um comando)

```sh
cd ~/baba
scripts/baba.sh subir
```

Na primeira vez ele cria o `.env.production` (permissao `600`) com **segredos gerados na hora**
(chave do Django, senha do banco, chaves do armazenamento dos comprovantes, **codigo de convite
do grupo**) e o IP do
notebook em `DJANGO_ALLOWED_HOSTS`, constroi a imagem e sobe tudo. No fim mostra o endereco para
abrir no celular. Confira o `DJANGO_ALLOWED_HOSTS` do arquivo: o IP que os celulares vao digitar
deve estar la, seguido de `localhost` (**mantenha o `localhost`**, e o healthcheck do conteiner).

**Guarde uma copia do `.env.production` num gerenciador de senhas.** Sem a senha do banco e as
chaves nao da para subir uma maquina nova com o mesmo banco.

Se a porta 80 estiver ocupada por outra coisa, ponha `APP_PORT=8000` no `.env.production` e rode
`scripts/baba.sh subir` de novo (o endereco passa a ser `http://IP:8000`).

**Codigo de convite (`BABA_INVITE_CODE`).** O cadastro em `/cadastro/` pede esse codigo, para que
so quem voce convidou crie conta; sem ele o app **nao inicia** em producao (minimo de 6
caracteres). O `subir` inventa um e mostra no fim. Passe o codigo no grupo do bába, nunca em
lugar publico. Para trocar, edite `BABA_INVITE_CODE` no `.env.production` e rode
`scripts/baba.sh subir` de novo: as contas que ja existem nao mudam. Quem digita o codigo errado
conta no limite de cadastros por aparelho (10 por hora). A diretoria tambem pode criar contas pela
tela de Domingo sem o codigo.

**E-mail do criador (`BABA_CREATOR_EMAIL`, opcional).** A tela "Sobre o app" mostra o contato
"Fale com o criador" so quando essa variavel tem um e-mail. Vazia (o padrao), a tela nao mostra
contato nenhum. Para mudar, edite o `.env.production` e rode `scripts/baba.sh subir` de novo.

Os outros comandos:

```sh
scripts/baba.sh status          # estado de cada servico (todos "healthy")
scripts/baba.sh logs            # acompanha o log do app (ctrl+c sai); logs db, logs s3 para os outros
scripts/baba.sh parar           # para tudo, os dados ficam
scripts/baba.sh atualizar       # backup, git pull, reconstroi e reinicia
scripts/baba.sh apagar-jogador CELULAR   # simula apagar um jogador (LGPD, secao 8)
scripts/baba.sh lista           # a lista do proximo domingo em texto, para colar no WhatsApp (secao 7)
scripts/baba.sh admin --email EMAIL   # liga o administrador oculto de uma conta (--remover desliga)
```

O app **se recusa a iniciar** se alguma variavel obrigatoria faltar ou estiver insegura (chave
curta, `DJANGO_DEBUG` ligado, `localhost` fora dos hosts permitidos...) ou se `BABA_FAKE_NOW`
estiver definido ou `BABA_DEMO` ligado. A mensagem em `scripts/baba.sh logs` lista tudo o que esta
errado.

## 3. Criar o presidente e configurar

```sh
scripts/baba.sh presidente
```

Pergunta o celular (e o login), o e-mail (recuperacao de senha) e o nome, e pede a **senha de 4 caracteres digitando no
terminal**. Nao deixe `BABA_BOOTSTRAP_PASSWORD` nas variaveis. Entre no app e configure em
**Diretoria > Financeiro > Configuracoes**: mensalidade, avulso, multas, dia de vencimento, a chave Pix, o titular do Pix (opcional) e o WhatsApp da
diretoria (opcional; a pagina publica de recuperacao de senha nao o mostra, por seguranca) e o nome do local do jogo
(opcional, aparece na tela inicial). Os
jogadores se cadastram em `/cadastro/` ou a diretoria cria as contas pela tela.

## 4. Testar no celular

Com o celular na mesma wifi, abra `http://IP-DO-NOTEBOOK` (o endereco que o `subir` mostrou).

- [ ] Cadastrar um jogador de teste, entrar na lista e enviar um comprovante de Pix; abrir como
  diretoria e ver o comprovante.
- [ ] Errar a senha 5 vezes e ver o bloqueio (a mensagem diz ate que horas e que a diretoria
  pode dar uma nova senha).
- [ ] Abrir `/cadastro/` sem login: pede o codigo de convite e nao lista nenhum jogador.
- [ ] Nenhuma pagina mostra a faixa amarela do relogio de teste.
- [ ] Reiniciar o notebook e ver tudo voltar sozinho (`scripts/baba.sh status`).
- [ ] Fazer um backup e uma restauracao de ensaio (secao 6).

**Limite desta etapa:** o navegador so deixa instalar um app (PWA) e usar o modo offline em
enderecos **HTTPS**. Em `http://IP` o bába funciona como site normal: no Android (Chrome) use
"Adicionar a tela inicial" para um atalho; no iPhone, Compartilhar > "Adicionar a Tela de
Inicio". O app instalavel de verdade vem com a parte 2.

Sem e-mail configurado, "esqueci a senha" falha para jogadores que cadastraram um e-mail; quem
nao tem e-mail pede a diretoria, que define uma senha provisoria (**Diretoria > Jogadores >
Editar**). Para ligar o e-mail, preencha `EMAIL_*` no `.env.production` com um SMTP gratuito. O mesmo SMTP envia os avisos de pagamento pendente (**Diretoria > Financeiro > A receber > Avisar quem deve**).

## 5. O notebook nao pode dormir

Ubuntu Server nao suspende sozinho, mas confira:

1. **Tampa fechada**: em `/etc/systemd/logind.conf` defina `HandleLidSwitch=ignore`,
   `HandleLidSwitchExternalPower=ignore` e `HandleLidSwitchDocked=ignore` e reinicie o notebook.
   Assim ele fica ligado com a tampa fechada.
2. **Nunca suspender**:
   `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
3. **Energia**: deixe sempre na tomada. Alguns notebooks tem no BIOS/UEFI um limite de carga da
   bateria (por exemplo 80%) e uma opcao de ligar sozinho quando a energia volta: **a verificar
   no seu modelo** (os nomes variam).
4. **Atualizacoes automaticas que reiniciam fora do horario**: `sudo apt install
   unattended-upgrades` e, em `/etc/apt/apt.conf.d/50unattended-upgrades`, ative
   `Unattended-Upgrade::Automatic-Reboot "true";`, `Unattended-Upgrade::Automatic-Reboot-Time
   "04:30";` e `Unattended-Upgrade::Automatic-Reboot-WithUsers "true";`. Assim a maquina so
   reinicia de madrugada, nunca no domingo de manha. Os conteineres voltam sozinhos
   (`restart: unless-stopped` e Docker habilitado no boot); confira com `scripts/baba.sh status`
   depois do primeiro reinicio.
5. **Horarios em que precisa estar no ar**: domingo 10h-12h (jogo), segunda 8h (a lista abre) e
   segunda 8h (o resultado da Bola de Ouro sai junto com a lista). Evite atualizar ou reiniciar nessas janelas.
6. **Disco**: de vez em quando `df -h` e `docker system df`. Os logs dos conteineres ja tem limite
   (10 MB x 5 arquivos por servico).

## 6. Backup e restauracao (simples)

O `scripts/backup.sh` copia o banco (`pg_dump` comprimido) e o volume dos comprovantes para uma
pasta, confere que o arquivo do banco esta completo e apaga copias com mais de 30 dias. **Disco
unico nao e backup**: aponte a pasta para um **disco externo** ou pendrive.

1. Plugue o disco, monte-o (por exemplo em `/mnt/disco-externo`) e crie a pasta
   `sudo mkdir -p /mnt/disco-externo/baba && sudo chown $USER /mnt/disco-externo/baba`.
2. No `.env.production` preencha:

   ```sh
   BABA_BACKUP_DIR=/mnt/disco-externo/baba
   BABA_BACKUP_MOUNTPOINT=/mnt/disco-externo    # nao roda se o disco nao estiver montado
   ```

   (Sem isso o backup vai para `~/baba/backups`, no proprio notebook: melhor que nada, mas nao
   protege de perder a maquina.)
3. Rode e confira:

   ```sh
   scripts/baba.sh backup
   ls -lh /mnt/disco-externo/baba    # baba-db-<data>.sql.gz, baba-receipts-<data>.tar.gz, last-success
   ```

   Os dumps do banco contem os dados e as senhas (em hash) de todos os jogadores: guarde o disco
   em lugar seguro. Para **criptografar** os arquivos, ponha em `BABA_BACKUP_PASSPHRASE_FILE` o
   caminho de um arquivo com uma frase-senha (`openssl rand -base64 32 > ~/baba.pass; chmod 600
   ~/baba.pass`, e guarde uma copia no gerenciador de senhas: sem ela os backups nao abrem). Os
   arquivos passam a terminar em `.gpg` (`sudo apt install gpg`).

### Agendar

Faca o backup **toda noite**, com o disco ligado. Com systemd timer (se o notebook estava
desligado as 03:30, o backup roda quando ele volta; o cron nao faz isso):

```sh
sed "s|USUARIO|$USER|g" scripts/systemd/baba-backup.service | sudo tee /etc/systemd/system/baba-backup.service
sudo cp scripts/systemd/baba-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now baba-backup.timer
sudo systemctl start baba-backup.service     # roda agora, para testar de verdade
```

(O servico supoe o repositorio em `/home/<usuario>/baba`.) Alternativa com cron
(`crontab -e`): `30 3 * * * cd ~/baba && scripts/backup.sh >> ~/baba/backup.log 2>&1`.

### Como saber que rodou

```sh
systemctl list-timers baba-backup.timer         # proxima e ultima execucao
journalctl -u baba-backup.service -n 30         # termina em "backup finished"
cat /mnt/disco-externo/baba/last-success        # data/hora do ultimo sucesso (UTC)
```

Olhe isso toda segunda. Se `last-success` tiver mais de 26 horas, algo falhou (disco fora do lugar,
Docker parado): veja o `journalctl`.

### Restaurar

Teste isto **uma vez antes de confiar no backup**.

```sh
cd ~/baba
scripts/baba.sh parar
docker compose --env-file .env.production -f compose.prod.yaml up -d db   # so o banco
scripts/restore.sh /mnt/disco-externo/baba/baba-db-20261012T063000Z.sql.gz    # pede para digitar "restaurar"
scripts/restore.sh /mnt/disco-externo/baba/baba-receipts-20261012T063000Z.tar.gz   # comprovantes (opcional)
scripts/baba.sh subir
```

A restauracao do banco **substitui** todo o conteudo pelo do arquivo, numa unica transacao: se
algo falhar (por exemplo frase-senha errada), nada muda. **Notebook novo ou perdido**: instale
tudo da secao 1, baixe o repositorio, coloque o `.env.production` da copia guardada e siga os
comandos acima; os dados voltam como estavam no backup.

### Backup para a nuvem (opcional, R2)

Se quiser uma copia fora de casa: crie um bucket R2 privado `baba-backups` e um token so para
ele (a Cloudflare pode pedir um meio de pagamento para ativar o R2: **a verificar**; o uso do
bába cabe na faixa gratuita), instale o `rclone` (`sudo apt install rclone`) e:

```sh
rclone config create r2 s3 provider=Cloudflare access_key_id=... secret_access_key=... \
  endpoint=https://<ACCOUNT_ID>.r2.cloudflarestorage.com acl=private no_check_bucket=true
```

Depois ponha `BABA_BACKUP_REMOTE=r2:baba-backups` no `.env.production` (e use criptografia, pois o
bucket e de terceiros). O `backup.sh` envia as copias e apaga as antigas la tambem. Para
restaurar, baixe o arquivo (`rclone copy r2:baba-backups/<arquivo> .`) e use o `restore.sh`.

## 7. Rotina

**Atualizar o app:** `scripts/baba.sh atualizar` (faz backup, `git pull`, reconstroi a imagem; as
migracoes rodam no inicio). Para voltar atras: `git checkout <versao anterior>` e
`scripts/baba.sh subir`; se uma migracao ja mudou o banco, restaure o backup feito antes.

**Depois de atualizar para a versao em que o domingo so comeca e termina quando o diretor manda:**
o domingo nao termina mais sozinho pelo horario. Qualquer domingo passado que ainda esteja aberto
(normalmente o ultimo) precisa ser finalizado a mao pela diretoria, em Montar o domingo >
Finalizar e cobrar. Isso cria as cobrancas daquele domingo e abre a Bola de Ouro. A migracao
so preenche o inicio dos domingos passados; ela nao finaliza nem cobra nada.

**Ver logs:** `scripts/baba.sh logs` (app), `scripts/baba.sh logs db`. Os links de redefinicao de
senha aparecem nos caminhos dos acessos (`/senha/redefinir/...`): valem 1 hora e uso unico, mas
nao compartilhe os logs.

**Lista do domingo sem abrir o app:** com a internet ruim ou o tunel fora, `scripts/baba.sh lista`
mostra no terminal a lista do proximo domingo em texto puro, pronta para copiar e mandar no
WhatsApp. Ela le o banco direto, na mesma ordem do app (chegada mais as preferencias que o app ja
aplica): cabecalho com a data, o horario e o local, depois Goleiros, Mensalistas, Avulsos e
Agregados, e a espera por ultimo se houver. So aparece quem esta inscrito. Para outro domingo:
`scripts/baba.sh lista --data 25/10/2026`. Precisa dos conteineres ligados (`scripts/baba.sh status`).

**Administrador oculto:** `scripts/baba.sh admin --email voce@exemplo.com` liga o papel numa conta
que ja existe, e `scripts/baba.sh admin --email voce@exemplo.com --remover` desliga. E o mesmo
comando `definir_admin`, sem precisar escrever `docker compose exec`.

**Problemas comuns**

| Sintoma | Causa provavel |
| --- | --- |
| `app` nao inicia, `ImproperlyConfigured` nos logs | variavel faltando ou insegura; a mensagem lista cada uma |
| 400 "Invalid HTTP_HOST" no celular | o IP digitado nao esta em `DJANGO_ALLOWED_HOSTS` (o IP mudou?) |
| Celular nao abre o endereco | outra rede (dados moveis, wifi de visitantes), IP mudou ou porta ocupada |
| Conteiner `app` "unhealthy" | `localhost` fora de `DJANGO_ALLOWED_HOSTS` ou banco indisponivel |

## 8. Seguranca e LGPD, o essencial

- [ ] `.env.production` com permissao `600`, fora do git, copia no gerenciador de senhas.
- [ ] Nada de redirecionamento de porta nem DMZ no roteador para este notebook: o app e so da rede
  de casa. O Postgres nao tem porta publicada. (O Docker ignora o `ufw` para portas publicadas:
  o que protege e a rede de casa e o roteador sem redirecionamento.)
- [ ] Wifi de casa com senha forte; a senha de 4 caracteres dos jogadores so e razoavel porque o
  bloqueio de tentativas funciona e o banco e os backups ficam em maos confiaveis. Guarde o
  disco de backup em lugar seguro (ou criptografe).
- [ ] `DJANGO_DEBUG=false`, sem `BABA_FAKE_NOW` e sem `BABA_DEMO` (o app nem inicia com eles) e
  sem faixa de demonstracao ou amarela.
- [ ] Atualizacoes do sistema automaticas (secao 5) e `scripts/baba.sh atualizar` de vez em quando
  para receber correcoes.
- [ ] **Backup testado**: restauracao feita pelo menos uma vez.
- [ ] Senha provisoria enviada no privado do WhatsApp, nunca no grupo.

**LGPD, o minimo** (nao e parecer juridico). O app guarda nome, telefone, e-mail,
historico de presenca e pagamentos e comprovantes de Pix (nome, banco, valores, parte de CPF/chave)
de cerca de 30 pessoas:

- [ ] Avise os jogadores, em poucas linhas, quais dados o bába guarda e para que (organizar o
  bába) e quem ve o que (celular, e-mail e comprovantes so o proprio jogador e a diretoria).
- [ ] Combine um canal (o WhatsApp da diretoria) para quem quiser ver, corrigir ou apagar os
  proprios dados.
- [ ] Os comprovantes ficam guardados por jogador, sem prazo de validade, para auditorias da
  diretoria (ficha do jogador, com todo o historico de pagamentos); nada os apaga
  automaticamente. **Ponto em aberto para o clube decidir**: por quanto tempo guardar os
  comprovantes (o pedido de um jogador para apagar os dados dele esta logo abaixo).
- [ ] Os backups tambem tem dados pessoais: disco em lugar seguro, mesma retencao.

**Apagar um jogador de verdade** (pedido de remocao dos dados; desativar so esconde). Rode
`scripts/baba.sh backup`, depois `scripts/baba.sh apagar-jogador 11987654321` (o celular dele):
isso so simula e mostra o que sai (linhas por tabela, foto e comprovantes) e o que muda no que
fica (placares dos jogos em que ele marcou, caixa e cobrancas, ranking, Bola de Ouro). Para
apagar, repita com `--confirmar` e digite o celular de novo. O que ele fez como diretoria
(despesas, gols lancados, comprovantes analisados) fica e passa para o presidente; quem ele
indicou fica sem "indicado por". O presidente nao pode ser apagado, e quem esta na lista de um
domingo que vai acontecer precisa sair dela pelo app antes. Se algum arquivo nao sair do
armazenamento, o comando lista os que sobraram para apagar a mao. Sem terminal (em script),
`--confirmar` so vale junto com `--sim-tenho-certeza`. Os backups antigos ainda tem os dados
dele ate sairem pela retencao.

---

# Parte 2 - publicar na internet com Cloudflare Tunnel (opcional)

Faca so depois da parte 1 funcionando. O notebook nao abre porta nenhuma no roteador: o
`cloudflared` (um conteiner) faz uma conexao de saida para a Cloudflare, que entrega o HTTPS. O
app deixa de ser acessivel pela rede de casa (so o tunel chega nele), ganha HTTPS, cookies
seguros e o app instalavel (PWA). Custo: nenhum alem do dominio que o capitao ja tem.

## 9. Dominio na Cloudflare

O tunel so publica enderecos de dominios que estao na Cloudflare, entao o dominio precisa
**usar os servidores de nomes (nameservers) da Cloudflare**.

1. Crie a conta em <https://dash.cloudflare.com/sign-up> e ligue a **verificacao em duas etapas
   (2FA)**.
2. **Add a domain** (plano **Free**). A Cloudflare mostra **dois servidores de nomes**
   (`xxx.ns.cloudflare.com`).
3. No site onde o dominio foi registrado, troque os servidores de nomes pelos dois da Cloudflare.
   - Dominio `.br` no **Registro.br**: o passo a passo exato **nao foi verificado** (a pagina nao
     pode ser lida na pesquisa). **Confira a ajuda do Registro.br antes** e anote aqui o que fez.
   - Outros registradores: procure "Nameservers" / "Servidores DNS" nas configuracoes do dominio.
4. Espere a Cloudflare mostrar o dominio como **Active** (de minutos a algumas horas).
5. Em **SSL/TLS > Edge Certificates**, ligue **Always Use HTTPS**: o app nao redireciona HTTP
   para HTTPS sozinho, quem faz isso e a Cloudflare. (Nome exato do menu: a verificar.)
6. A Cloudflare pode injetar scripts nas paginas e a politica de seguranca do app (CSP) os
   bloqueia: deixe **desligados** Rocket Loader, Auto Minify e Web Analytics automatico.

## 10. Criar o tunel e o token

1. No painel abra **Zero Trust > Networks > Tunnels** (os menus ja mudaram de nome; se nao achar,
   busque "Tunnels"). Na primeira vez a Cloudflare pede para criar a conta Zero Trust (plano
   gratuito).
2. **Create a tunnel > Cloudflared**, nome `baba`.
3. Na tela de instalacao aparecem comandos com um token. **Copie so o token** (a sequencia
   longa que comeca com `eyJ`); nao instale o `cloudflared` no sistema, ele roda em conteiner. O
   token e uma senha.
4. Em **Published application routes** (ou "Public hostname"): subdominio `babinha`, seu dominio,
   **Service: HTTP**, URL **`app:8000`** (o nome do servico no compose, nao `localhost`).

## 11. Ligar o tunel

No `.env.production`, **descomente e preencha** o bloco "PART 2": `TUNNEL_TOKEN`,
`DJANGO_ALLOWED_HOSTS=babinha.SEUDOMINIO.com.br,localhost` (troque a linha da parte 1),
`DJANGO_CSRF_TRUSTED_ORIGINS=https://babinha.SEUDOMINIO.com.br`, `DJANGO_BEHIND_HTTPS_PROXY=true`,
`DJANGO_SECURE_COOKIES=true`, `DJANGO_HSTS_SECONDS=3600` e
`BABA_CLIENT_IP_HEADER=HTTP_CF_CONNECTING_IP` (os valores padrao do `compose.prod.tunnel.yaml`
ja sao estes). Depois:

```sh
scripts/baba.sh subir       # com TUNNEL_TOKEN preenchido ele inclui compose.prod.tunnel.yaml sozinho
scripts/baba.sh status      # db, app e cloudflared "healthy"; o app sem porta publicada
```

Abra `https://babinha.SEUDOMINIO.com.br/saude/` no celular **com dados moveis** (fora da wifi de
casa): deve mostrar `{"status": "ok"}`. O endereco `http://IP-DO-NOTEBOOK` deixa de funcionar
(a porta saiu da rede de casa de proposito); se algo der errado, comente o `TUNNEL_TOKEN` e
rode `subir` para voltar a parte 1.

O app **recusa iniciar** se, no modo HTTPS, faltar `CSRF_TRUSTED_ORIGINS` em `https://`, os
cookies nao forem seguros ou o cabecalho do IP real estiver vazio (sem ele todos os jogadores
dividiriam o mesmo limite de tentativas de login).

**HSTS gradual**: comece com `3600`; depois de uma semana sem problemas use `86400` e, mais uma
semana depois, `31536000` (`scripts/baba.sh subir` aplica). Depois de subir, o navegador recusa
HTTP por esse tempo.

Teste de novo (ainda vale a lista da secao 4) e mais:

- [ ] Instalar o PWA: Android (Chrome) "Instalar app"; iPhone (Safari) Compartilhar > "Adicionar a
  Tela de Inicio".
- [ ] Errar a senha 5 vezes em dois celulares em redes diferentes: o bloqueio vale so para cada
  um (confirma que o IP real chega ao app).
- [ ] Os jogadores usam o endereco novo (avise o grupo; os atalhos da parte 1 deixam de valer).

## 12. Comprovantes no R2 (opcional)

Por padrao os comprovantes ficam no disco do notebook (e no backup). Se preferir um bucket
privado da Cloudflare R2 (sobrevive a perder o notebook; a Cloudflare pode pedir um meio de
pagamento: **a verificar**; o uso do bába cabe na faixa gratuita de 10 GB): crie o bucket
`baba-comprovantes` **sem acesso publico** (nem `r2.dev`) e um token **Object Read & Write
so nesse bucket**, apague a linha `COMPOSE_PROFILES=local-receipts` do `.env.production` e
preencha `BABA_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`,
`BABA_S3_REGION=auto` e as chaves do token. **Os comprovantes antigos nao migram sozinhos**: copie
o volume para o bucket antes (por exemplo com o `rclone`) ou mantenha o disco. O teste de
integracao do repositorio valida um bucket S3 real (`tests/integration/test_s3_storage.py` com as
variaveis `BABA_TEST_S3_*`, veja o README).

## 13. Checklist extra para a parte 2

- [ ] 2FA na Cloudflare, no registrador do dominio, no GitHub e no e-mail da conta.
- [ ] Portas 80/8000 e 5432 **fechadas** na rede de casa (so o `cloudflared` entra no app).
- [ ] HTTPS ativo, Always Use HTTPS ligado, HSTS subindo aos poucos.
- [ ] Token do tunel guardado como segredo; se vazar, gire-o no painel do tunel e atualize o
  `.env.production`.
- [ ] Se usar R2: bucket privado, sem `r2.dev`, token restrito ao bucket.
- [ ] LGPD: avisar tambem que a Cloudflare processa o trafego (servidores fora do Brasil).

| Sintoma (parte 2) | Causa provavel |
| --- | --- |
| Pagina de erro 502/1033 da Cloudflare | `cloudflared` fora do ar, ou a rota nao aponta para `app:8000` |
| `cloudflared` reiniciando com "token is not valid" | `TUNNEL_TOKEN` errado ou truncado |
| 403 CSRF ao entrar | `DJANGO_CSRF_TRUSTED_ORIGINS` sem o `https://dominio` exato |
| Todos os jogadores bloqueados juntos no login | `BABA_CLIENT_IP_HEADER` vazio ou errado |

---

## 14. O que nao foi verificado

- Os passos do **Registro.br** para usar servidores de nomes da Cloudflare.
- O nome atual dos menus da Cloudflare (Zero Trust, Tunnels, Published application routes,
  Always Use HTTPS) e se o R2 exige meio de pagamento na ativacao.
- O tunel de verdade: nenhum token real foi usado. O healthcheck do `cloudflared`
  (`cloudflared tunnel ready`) e o comando da documentacao da Cloudflare, mas so foi exercitado
  sem token valido; e que o tunel entrega ao app os cabecalhos `X-Forwarded-Proto` e
  `CF-Connecting-IP` como a Cloudflare descreve. Os testes da secao 11 confirmam.
- A parte 1 foi exercitada de ponta a ponta num ambiente de teste (subir, criar presidente,
  entrar pelo IP da rede, gravar comprovante, backup, restauracao), mas nao no notebook do
  capitao. O `scripts/backup.sh` sob o systemd tambem so foi testado diretamente: use
  `sudo systemctl start baba-backup.service` e confira o `journalctl` na primeira vez.
- Opcoes de BIOS de limite de carga e de religar na volta da energia do seu notebook.
