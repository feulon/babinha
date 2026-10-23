# Babinha

Gerenciador do bába semanal de domingo: lista, pagamentos Pix, times por potes, placar e Bola de Ouro.
Web app instalavel (PWA), mobile first, em portugues.

Licenca: MIT, ver [LICENSE](LICENSE).

O plano do produto esta em [docs/plan.md](docs/plan.md). Esta versao entrega a **Fase 1 (Base)**:
cadastro e login, lista semanal com mensalistas, avulsos e agregados, vagas de linha e goleiro,
fila de espera, desistencia e multas, e as telas da diretoria. E a **Fase 2 (Dinheiro)**: Pix com
comprovante, confirmacao pela diretoria, mensalidade, despesas e o caixa aberto. E a
**Fase 3 (Dia do jogo)**: times sorteados pelos potes, tabela de jogos, placares e gols, e a
lista de presenca. E a **Fase 4 (Comunidade)**: Bola de Ouro, historico por jogador e ranking
da temporada.

## Stack

- Python 3.13 + Django 5.2 (LTS), templates no servidor com HTMX 2.0.4 (`src/static/js/htmx.min.js`, sem CDN) para atualizar a tela sem recarregar, CSS e JS sem build.
- PostgreSQL (migracoes do Django), Gunicorn + WhiteNoise no conteiner.
- uv para dependencias (`pyproject.toml` + `uv.lock`).
- Comprovantes do Pix num bucket privado compativel com S3 (django-storages + boto3).
- Docker Compose: app, Postgres, Mailpit (captura os e-mails de recuperacao de senha) e um
  gateway S3 ([Versity S3 Gateway](https://github.com/versity/versitygw)) para os comprovantes.

## Rodar localmente com Docker

```sh
cp .env.example .env          # ajuste se quiser; o compose ja tem padroes locais
docker compose up -d --build
docker compose exec app python manage.py criar_presidente --telefone "(11) 98765-4321" --email voce@exemplo.com --nome "Seu nome"
```

- App: http://localhost:8000
- E-mails (Mailpit): http://localhost:8025
- S3 dos comprovantes: http://localhost:7070 (so com assinatura; o app e o unico cliente)

O comando `criar_presidente` cria a primeira conta (sem indicacao); o celular (`--telefone`) e o
e-mail (`--email`, validado) sao obrigatorios. Ele pede a senha de 4 caracteres no terminal, ou le de
`BABA_BOOTSTRAP_PASSWORD`. Os outros jogadores se cadastram em `/cadastro/` com o celular e o
e-mail, com o nome de quem os indicou em texto livre (depois, no perfil, ligam a indicacao a um jogador da lista). Na aba **Origem** do proprio perfil, **Enviar convite** compartilha um link para `/cadastro/` que ja vem com o nome de quem convidou e o codigo do grupo preenchidos. O cadastro termina com as regras (multas, prioridade e demais) e a caixa "Li e estou ciente das regras", obrigatoria; a data do aceite fica no jogador. Quem se cadastra pode enviar uma foto (opcional, mas recomendada para a turma saber quem e quem; da para colocar depois). Em **Diretoria > Jogadores**, "Novo jogador" cadastra alguem sem coloca-lo na lista de nenhum domingo
(o login, com o celular e a senha provisoria de 4 caracteres, aparece uma unica vez para ser repassado
ao jogador, com um atalho para o WhatsApp; nenhuma mensagem nem e-mail e enviado) e
"Editar" corrige o nome e desativa quem saiu do baba (campo Ativo: some das listas e do cadastro, o
historico fica; "So desativados" nos filtros traz a pessoa de volta). Nomes repetidos pedem confirmacao.

Em **Diretoria > A receber**, tocar numa cobranca mostra "Marcar como pago", que registra quem pagou sem comprovante, como
dinheiro: a cobranca fica paga e entra no Caixa, com quem marcou e como.
**Cobrar no grupo**, na mesma tela, monta uma mensagem para o grupo do WhatsApp marcando o telefone de cada pessoa que deve; o app nao envia nada, a diretoria compartilha a mensagem.
"Confirmar pagamento" e "Dispensar" pedem confirmacao, e "Desfazer confirmacao" devolve o
comprovante para analise ate 10 minutos depois. Inicio e Jogos do jogador se atualizam sozinhos
(a cada 15 s, so com a aba visivel); Jogos tem um botao "Atualizar" (some quando o domingo encerra), o Inicio nao.

Celular e login: o jogador entra com o celular (com ou sem mascara, `+55` opcional) ou com o
e-mail. O e-mail e obrigatorio no cadastro (avisos de pagamento e link de nova senha); contas antigas
sem e-mail pedem a diretoria, que define uma senha provisoria em **Diretoria > Jogadores > Editar > Definir nova
senha** e avisa a pessoa pelo WhatsApp. Quem se cadastrou antes do celular existir informa o
numero no primeiro acesso depois da atualizacao. Celular e e-mail so aparecem para o proprio
jogador e para a diretoria.

O app tem dois tutoriais, so de leitura (sem tour no primeiro acesso nem estado salvo por conta). O da
diretoria fica em **/diretoria/tutorial/** (link **Tutorial da diretoria** no fim do Painel): lista e faltas, jogos pela fila
de espera, gols e assistencia, Bola de Ouro, multas de atraso e de falta, dinheiro (A receber, Caixa,
Configuracoes), Pix e convite por link. O Painel so tem gestao; conta, regras, Sobre e Feedback ficam em **Mais**, igual para todos. Os tutoriais e as regras compartilham os textos comuns em partials. O do jogador fica em **/tutorial/** (**Mais > Tutorial**): entrar na
lista e na fila, Pix e comprovante, multas, acompanhar o domingo, Bola de Ouro, turma e perfil, convite e
Sobre o app.

As portas podem ser trocadas com `APP_PORT`, `POSTGRES_PORT`, `MAILPIT_UI_PORT` e `S3_PORT`.

## Demonstracao: testar os domingos a qualquer dia e hora

Um unico comando sobe o app em modo demonstracao, com banco, comprovantes e uma lista de domingo
ja montada. Nao precisa editar `.env`; o stack e separado do `compose.yaml` e usa a porta 8100:

```sh
docker compose -f compose.demo.yaml up --build     # depois: http://localhost:8100
```

Toda tela mostra a faixa **"Demonstracao: os horarios do dia do jogo estao liberados"**. Entre com
o celular ou o e-mail e a senha `1234`: `presidente@demo.baba` (presidente e diretoria; ve a
aba **Diretoria** na barra de baixo), `baixinho@demo.baba` (mensalista na
lista), `tatu@demo.baba` (avulso fora da lista), `gatinho@demo.baba` (goleiro agregado fora da
lista), `sabia@demo.baba` (topo da espera), `canhota@demo.baba`, `batata@demo.baba` e
`mineiro@demo.baba` (pagamentos e multas). `--listar` mostra todas as contas.

Para trocar a situacao do domingo (apaga tudo e monta de novo):

```sh
docker compose -f compose.demo.yaml exec app python manage.py demo --listar
docker compose -f compose.demo.yaml exec app python manage.py demo times-confirmados --reset
```

| Situacao | O que o domingo mostra |
| --- | --- |
| `lista` (a que sobe com o Docker) | lista cheia: 20 de linha, 2 goleiros e dois na espera |
| `times-sorteados` | times sorteados, ainda sem confirmar |
| `times-confirmados` | times confirmados, cada jogador ve o seu |
| `jogos-em-andamento` | dois jogos encerrados, um em quadra, presenca e atrasos marcados |
| `partida-encerrada` | todos os jogos encerrados, o futebol finalizado e a Bola de Ouro aberta (parte ja votou) |
| `bola-de-ouro-ultimo-voto` | so falta o voto do presidente: ao votar, o resultado aparece (so na demonstracao; de verdade, o resultado sai na segunda 8h) |
| `cobranca` | jogo finalizado: Pix pendente, em analise, pago e recusado, e uma multa |
| `borda-um-goleiro`, `borda-novato`, `borda-desistencia-tardia`, `borda-cancelado` | casos de borda |

O comando monta tudo pelos mesmos servicos das telas (o que as regras proibirem, ele nao cria).
Sem `--reset` ele recusa rodar num banco que ja tem dados, e recusa apagar contas que nao sao
`@demo.baba` sem `--force`. `docker compose -f compose.demo.yaml down -v` apaga o banco da demonstracao.

O que a chave `BABA_DEMO` libera e o que continua valendo:

- **Liberado** (as regras de horario do dia do jogo): abertura e fechamento da lista, times,
  presenca, gols, fim de partida, finalizar o jogo e cobrar, votacao da Bola de Ouro.
- **Continua valendo**: vagas, prioridades, fila de espera, permissoes, valores e multas.
- Sem o calendario, o estado do domingo decide as fases: o prazo sem multa (e a prioridade na
  lista) dura ate a diretoria confirmar os times; a lista fecha quando a diretoria finaliza o
  jogo; a Bola de Ouro abre quando a diretoria finaliza o futebol e, so aqui, fecha assim que todos que jogaram votaram, sem esperar a segunda 8h (as telas dizem isso).
- O domingo da demonstracao e sempre o proximo (ou o de hoje, mesmo depois das 12h).
- Os cookies tem nome proprio (`baba_demo_sessionid`, `baba_demo_csrftoken`),
  entao a demonstracao nao colide com outro app em `localhost` ("sua sessao expirou").
- **O app se recusa a iniciar em producao com `BABA_DEMO` ligado.**

## Desenvolvimento sem Docker para o app

```sh
uv sync
docker compose up -d db mailpit s3
export DJANGO_DEBUG=true
export BABA_S3_ENDPOINT_URL=http://localhost:7070
export BABA_S3_ACCESS_KEY_ID=baba BABA_S3_SECRET_ACCESS_KEY=baba-local-secret
uv run python manage.py migrate
uv run python manage.py criar_presidente --telefone "(11) 98765-4321" --email voce@exemplo.com --nome "Seu nome"
uv run python manage.py runserver
```

## Testes e lint

Os testes de integracao e o E2E usam o Postgres do compose (`DATABASE_URL`, padrao
`postgres://baba:baba@localhost:5432/baba`; o Django cria o banco `test_baba`).

```sh
docker compose up -d db
uv run playwright install chromium   # uma vez, para o teste E2E
uv run pytest                        # unitarios, integracao e E2E
uv run pytest -n auto --dist loadfile  # o mesmo em paralelo (um banco test_baba_gwN por processo)
uv run pytest tests/unit             # so as regras puras (sem banco)
uv run ruff check . && uv run ruff format --check .
```

Os testes guardam os comprovantes em memoria. `tests/integration/test_s3_storage.py` usa um S3
de verdade e so roda com as variaveis abaixo (o CI sempre roda):

```sh
docker compose up -d s3
BABA_TEST_S3_ENDPOINT_URL=http://localhost:7070 BABA_TEST_S3_ACCESS_KEY_ID=baba \
  BABA_TEST_S3_SECRET_ACCESS_KEY=baba-local-secret uv run pytest tests/integration/test_s3_storage.py
```

- `tests/unit`: regras da lista, vagas, prioridades, fila, horarios, chave Pix, arquivos, sorteio
  dos times, placar, votacao da Bola de Ouro, estatisticas e ranking.
- `tests/integration`: servicos, cobrancas, comprovantes, concorrencia, login/bloqueio,
  recuperacao de senha e telas.
- `tests/e2e`: cadastro -> login -> entrar na lista, pagar por Pix -> diretoria confirma, e
  sortear os times -> montar a tabela -> registrar os gols, e votar na Bola de Ouro -> ver o
  ranking -> encerrar a temporada, num navegador real (Playwright).

`tests/integration/test_backup_restore.py` exercita `scripts/backup.sh` e `scripts/restore.sh` num
Postgres descartavel (precisa de Docker e `gpg`; com `rclone` instalado testa tambem o upload).

O CI (GitHub Actions) roda lint (inclusive shellcheck dos scripts), testes, o build da imagem Docker e a validacao dos compose de producao e da demonstracao.

Um PR so de texto (todos os arquivos em `src/templates/**/*.html`, `docs/**` ou `*.md`) roda o CI
leve: lint, `tests/unit/test_templates_compile.py` (compila todos os templates) e os testes de
integracao que citam os templates alterados; pula e2e, navegador e Docker. O job termina sempre
com sucesso ou falha (nunca skipped). Push para `main` e qualquer outro PR rodam tudo. Quem decide
e `scripts/classify_changes.py` (imprime `text-only` ou `full` para a lista de arquivos).

## Configuracao

Tudo por variaveis de ambiente; veja `.env.example`. As principais:

| Variavel | Para que serve |
| --- | --- |
| `DJANGO_SECRET_KEY` | obrigatoria em producao |
| `BABA_PRODUCTION` | `true` (o `compose.prod.yaml` define): o app so inicia se as variaveis de producao estiverem completas e seguras (`src/baba/production_checks.py`) |
| `DATABASE_URL` | Postgres |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | SMTP da recuperacao de senha |
| `BABA_INVITE_CODE` | codigo do grupo pedido no cadastro; obrigatorio em producao (vazio = cadastro aberto, so em desenvolvimento) |
| `BABA_LIST_OPENS_AT` | hora de segunda em que a lista abre (padrao `08:00`) |
| `BABA_DEMO` | so para a demonstracao (`compose.demo.yaml`, ver acima): libera os horarios do dia do jogo e mostra uma faixa em toda tela; o app nao inicia em producao com ele ligado |
| `BABA_FAKE_NOW` | so para testes: relogio de teste, vale apenas com `DJANGO_DEBUG`; nunca em producao |
| `BABA_S3_ENDPOINT_URL`, `BABA_S3_BUCKET`, `BABA_S3_REGION` | onde ficam os comprovantes (endpoint vazio = AWS S3) |
| `BABA_S3_ACCESS_KEY_ID`, `BABA_S3_SECRET_ACCESS_KEY` | credenciais do bucket (vazias = cadeia padrao do boto3) |
| `BABA_S3_ADDRESSING_STYLE` | `path` (padrao) ou `virtual`, conforme o provedor |
| `BABA_CLIENT_IP_HEADER` | cabecalho com o IP real atras de proxy/tunel (ex.: `HTTP_CF_CONNECTING_IP`); em cabecalhos com varios valores (`HTTP_X_FORWARDED_FOR`) vale o ultimo, adicionado pelo proxy |
| `BABA_CREATOR_EMAIL` | opcional: e-mail de contato na tela "Sobre o app" (vazio = sem contato) |
| `DJANGO_SECURE_COOKIES`, `DJANGO_BEHIND_HTTPS_PROXY`, `DJANGO_HSTS_SECONDS` | quando servido por HTTPS |

Os valores em dinheiro (mensalidade, avulso, multa de falta com seu multiplicador e multa de atraso de 15 e 30 min), o dia de vencimento da mensalidade, a
chave Pix e o WhatsApp da diretoria (opcional; a pagina publica de recuperacao de senha nao o mostra, por seguranca) ficam no banco e a
diretoria edita em **Diretoria > Configuracoes**, junto com o nome do local do jogo (opcional), mostrado na tela inicial. Valor 0,00 desliga a cobranca. A tela
publica `/regras/` ("Regras do babinha") le esses valores e explica prazos, prioridade e desistencia.

## Producao

O roteiro para rodar no notebook do capitao esta em [docs/producao.md](docs/producao.md): primeiro
uma v0 local (`scripts/baba.sh subir`, celulares na mesma wifi, comprovantes no disco, backup para
uma pasta ou disco externo) e, depois, a publicacao opcional com Cloudflare Tunnel
(`compose.prod.tunnel.yaml`). Em producao o app so inicia com as variaveis completas e seguras.

## Regras da lista (resumo)

Horarios sempre em America/Sao_Paulo.

- Abre segunda (hora configuravel) para mensalistas e avulsos; quarta 20h para agregados.
- Ate 4 goleiros. Linha: 22 vagas sem goleiro, 21 com 1, 20 com 2 a 4.
- Ate sabado 23h59: mensalista toma a vaga do ultimo nao mensalista a entrar; o 1o e o 2o
  goleiro retomam as vagas 21 e 22 do ultimo jogador de linha a entrar. Quem perde a vaga vai
  para o topo da espera, sem multa.
- Fila de espera por ordem de chegada e por posicao, com promocao automatica ate o jogo comecar.
- A diretoria edita a lista inteira a qualquer hora, mesmo depois do prazo de sabado, sem multa
  para quem ela move: na lista de jogadores da tela do domingo, tocar no nome abre Subir, Descer,
  Lugar (ir direto a uma posicao), Para a fila / Para a lista e Para o gol / Para a linha. O
  lugar escolhido pela diretoria (selo "fixo") vale sobre a ordem de chegada e as prioridades
  enquanto o jogador ficar naquela parte da lista: as prioridades nunca o tiram da vaga e as
  regras automaticas seguem ordenando os demais em volta dele. Quem fica alem do limite de vagas
  vai para o topo da espera, sem multa; mandar alguem para a fila passa a vaga ao proximo da
  espera. O Inicio do jogador nao lembra o prazo de desistencia (para nao incentivar); as regras e
  o cadastro continuam explicando.
- Desistir ou recusar promocao e gratis ate sabado 23h59; depois gera multa. Falta marcada na
  lista de presenca tambem gera multa. O valor sobe a cada
  ocorrencia pelo multiplicador (padrao 1,5x: R$ 50, 75, 112,50...; a diretoria edita); o perdao da diretoria tira a ocorrencia da contagem. O atraso e multado a parte, com valor fixo pelo tempo (padrao: 15 min = R$ 10,
  30 min = R$ 25; a diretoria edita), uma vez por jogador por domingo, e nao entra na escala das faltas.

O motor da lista fica em `src/games/domain/` (funcoes puras, testadas sem banco). Cada operacao
trava a linha do domingo (`SELECT ... FOR UPDATE`), entao inscricoes simultaneas nunca passam
do limite de vagas.

## Dinheiro (resumo)

- Mesmo valor para todos. Nada e cobrado enquanto o valor estiver zerado.
- Domingo: quem esta na lista como avulso ou agregado deve o avulso, mas so depois do jogo: as
  12h30 de domingo ou quando um diretor aperta "Finalizar jogo e cobrar" na tela do domingo.
  Antes disso ninguem aparece com pagamento pendente por causa do domingo (a multa por
  desistencia tardia ou falta segue as regras dela). Depois do fim, sair da lista (ou perder a
  vaga) cancela a cobranca ainda nao paga. Domingo cancelado nao cobra ninguem e nao devolve
  nada automaticamente.
- Mensalidade: mensalista nao e um tipo fixo, vale por um mes civil. O jogador aperta "Virar
  mensalista" (inicio ou Pagamentos, qualquer dia do mes) e fica mensalista ate o fim do mes; a
  adesao abre a cobranca da mensalidade, que vence no dia configurado,
  e o app leva a enviar o comprovante, mas pode ser depois: a adesao vale e a cobranca
  fica pendente ate a diretoria confirmar. No dia 1o ninguem renova sozinho: o mes de referencia
  vem do relogio do app (America/Sao_Paulo, e o relogio de teste vale), sem job agendado, e todo
  mundo volta ao tipo fixo (avulso ou agregado) ate aderir de novo. Quem nao aderiu paga o
  domingo como avulso e a mensalidade nao paga do mes anterior continua devida. A diretoria ve
  quem aderiu, pagou e esta pendente em A receber e no Caixa, cobra em "Avisar quem deve", e tambem registra
  a adesao em Jogadores > Editar ("Mensalista deste mes"). A adesao nao pode ser retirada antes
  do fim do mes. A virada do mes preserva pagamentos, dividas e a cobertura dos domingos
  anteriores. A adesao nao apaga cobrancas avulsas anteriores: para cobrir um domingo, precisa
  ocorrer ate as 12h30 dele ou sua finalizacao pela diretoria, o que vier primeiro.
- Multa: vira cobranca, com vencimento no sabado seguinte. Paga continua contando para a
  escalada; so o perdao tira da contagem.
- O jogador faz o Pix para a chave do presidente e envia a foto (JPG, PNG, WebP) ou o PDF do
  comprovante, ate 10 MB. O tipo e conferido pelo conteudo do arquivo. A diretoria confirma ou
  recusa com motivo; recusado, o jogador envia outro. Enquanto o comprovante esta em analise o
  jogador pode ver o que enviou e trocar por outro ("Enviar outro"); o antigo fica no historico.
- Cobranca com comprovante em analise ou paga nunca e cancelada automaticamente: a diretoria
  decide. A diretoria tambem pode dispensar uma cobranca a mao, com motivo.
- Financeiro da diretoria, em tres destinos: **A receber** (cobrancas em aberto, com os filtros
  Todas, A conferir e Multas, mais o chip Sem comprovante na lista; o atalho "Conferir comprovante" continua na linha de
  quem enviou, e tocar numa cobranca abre Marcar como pago e Dispensar), **Caixa** (entradas,
  despesas com "Lancar despesa" e saldo; mensalistas, pendentes e pagaram ficam recolhidos) e
  **Configuracoes** (valores e Pix). Os enderecos antigos (`/diretoria/comprovantes/`,
  `/diretoria/multas/`, `/diretoria/despesas/`, `/diretoria/avisos/`, `/diretoria/valores/`)
  continuam, dentro do destino a que pertencem.
- Quando pagar: a cobranca do domingo (avulsos e agregados) aparece depois do futebol, com o
  vencimento escrito nela. O texto vem de um so lugar (`finance/_when_to_pay.html`) e aparece em
  Regras, no Inicio e em Pagamentos. No Inicio, o que se deve e uma linha ("R$ 35,00 a pagar,
  vence 24/10, Ver Pix"); a chave Pix, o comprovante e o plano mensal ficam em Pagamentos.
- Caixa: todos veem arrecadado (com o que veio de mensalistas, multas e avulsos), despesas e saldo
  do mes (pelo mes a que cada cobranca se refere) em **Mais > Caixa**, igual para todos; o Caixa
  da diretoria, em **Diretoria > Caixa** (**/diretoria/caixa/**), mostra tambem quem pagou, quem
  esta pendente e as despesas.
- Avisos: em **Diretoria > A receber > Avisar quem deve** a diretoria escolhe devedores, ve a previa e envia o e-mail de
  cobranca. So quem tem e-mail recebe; quem foi avisado recentemente e pulado, a menos que se
  marque para repetir. Em desenvolvimento os e-mails vao para o Mailpit (http://localhost:8025).

Os comprovantes sao privados: o bucket nunca e publico e o app so entrega o arquivo ao dono da
cobranca e a diretoria.

### Backup dos comprovantes

No compose, cada comprovante e um arquivo comum no volume `s3-data` (pasta
`baba-comprovantes/comprovantes/AAAA/MM/`). Para copiar tudo para fora do notebook, basta
sincronizar essa pasta com qualquer ferramenta de backup, por exemplo:

```sh
docker run --rm -v baba_s3-data:/data:ro -v "$PWD/backup":/backup alpine \
  sh -c 'cp -a /data/baba-comprovantes /backup/'
```

Em um provedor gerenciado (S3, R2, B2), use o versionamento ou a replicacao do proprio provedor,
ou `rclone sync` para outro bucket.

## Dia do jogo (resumo)

- A diretoria sorteia os times em **Diretoria > Times**, a partir de quem esta na lista (quem foi
  marcado com falta fica de fora). Quatro times de 5 jogadores de linha, com a soma dos potes de
  cada time o mais proxima possivel (a diferenca entre o time de maior e o de menor soma e
  minimizada, mantendo grupos inteiros); entre sorteios igualmente proximos vale a mesma
  quantidade de cada pote por time; o resto e aleatorio, entao os times mudam toda semana.
  Goleiros nao tem pote e nao entram nos times. Jogadores de linha alem dos 20 (1 ou 2)
  completam o gol, por sorteio.
- **Diretoria > Times** e uma tela so: sortear, ver os 4 times, trocar jogadores com um toque e
  confirmar. O sorteio por potes trata cada grupo como uma unidade e nunca o separa. A tela abre
  com a faixa "Equilibrio" (uma barra com a soma dos potes de cada time e, se houver, os avisos, uma
  vez so) e os 4 times em grade 2x2: o nome do time e a soma numa linha, depois cada jogador uma vez,
  com o pote em letra miuda (P1 a P5) e um ponto "E" para o estreante; um grupo ou estreante com quem
  convidou fica junto, uma barra ao lado dos nomes. Os goleiros ficam numa linha logo abaixo
  ("Goleiros: A, B (rodam por jogo)"; o cartao "No gol" so ganha cabecalho quando ha jogador de linha
  nele). A quantidade de jogadores de um time so aparece quando esta errada. Para trocar, toca-se num
  jogador e depois num time ("Mover para ca") ou em outro jogador ("trocar"); estreante e quem o
  convidou (ou um grupo) vao sempre juntos, e "Soltar" deixa o estreante jogar em outro time so naquele
  domingo. Jogador de linha so vai ao gol alem dos 20 dos times (com 20 de linha, ninguem). Times
  com quantidade ou forca desiguais so geram um aviso curto, a diretoria pode confirmar mesmo assim;
  um grupo dividido ou maior que o time bloqueia a confirmacao.
- **Regras do sorteio** ficam recolhidas na mesma tela (antes do sorteio, e em "Sortear de novo e
  regras" depois): o interruptor "Estreantes jogam com quem convidou" (estreante e quem nunca teve
  presenca marcada em um domingo anterior e nao esta no bába desde antes do primeiro domingo do app,
  campo "No bába desde" em Jogadores > Editar; vale quando quem convidou tambem joga na linha neste
  domingo) e os grupos de 2 a 5 jogadores que precisam ficar no mesmo time. Se as regras nao cabem (por
  exemplo, um grupo de 5 mais um estreante que joga com um deles, e o time tem 5 vagas), a tela mostra o
  motivo no lugar do botao de sortear e oferece as saidas: deixar o estreante jogar em outro time
  (sugerida), tirar alguem do grupo ou aceitar um time maior; depois de aplicar, a mensagem diz o que
  mudou.
- Os times comecam como rascunho: a diretoria ve os potes, ajusta a mao, da nome aos times e
  confirma. So entao os jogadores veem os times (nunca os potes) em **/jogo/** e na tela inicial.
  Em **/jogo/** o time do jogador abre primeiro e em destaque; os outros tres ficam recolhidos, com a
  previa dos nomes, e abrem ao toque. Estreante leva um ponto "E" (com legenda), os goleiros ficam numa
  linha so ("rodam por jogo") e a tabela de jogos vazia vira uma linha no fim. O card do Inicio mostra o
  time do jogador com os nomes.
  Se a lista (ou o pote de alguem) mudar depois do sorteio, a tela avisa e pede o ajuste antes de
  confirmar. Com os times ja confirmados, o Painel acende "Ajustar times", a tela Times mostra
  "Confirmados, mas precisa de ajuste" e os jogadores veem "Times em ajuste" no time que perdeu gente.
  Antes disso o jogador le "times saem por volta das 9h". Com os times confirmados, Jogos
  destaca o jogo em andamento (ou o proximo) com o placar, e o proximo
  jogo do proprio jogador; a tabela completa fica recolhida e, aberta, e uma faixa rolavel com
  todos os jogos em miniatura: o jogo atual abre grande embaixo (placar e gols), um por vez, com
  "jogo anterior". Se os times estao desequilibrados, a
  confirmacao mostra o aviso na propria pagina e o botao vira "Confirmar times mesmo assim".
- **Acompanhar o domingo**: o cartao do proximo jogo, no Inicio do jogador, tem a situacao do jogador na lista
  (entrar, sair, fila de espera) e o botao "Acompanhar o domingo" (e so ele: o cartao do jogo do dia nao tem outro botao para os times). O botao fica bloqueado (com cadeado, sem link) ate chegar o domingo, a partir das 00h; no modo demonstracao ja nasce aberto. Quando o domingo acaba (a diretoria encerra, ou chegam as 12h30, a mesma hora em que a Bola de Ouro abre), o cartao passa a dizer "Domingo encerrado" com o botao "Ver como foi o domingo", e o cabecalho de **/jogo/** tambem. O botao abre **/jogo/** com tudo do domingo em blocos: times (o do jogador
  em destaque), tabela de jogos e placares, presenca (a do proprio jogador e "Quem vai"; some quando o domingo encerra, junto com o botao "Atualizar"), Bola de
  Ouro (so depois que o futebol encerra: votar, ou o resultado a partir de segunda 8h). Sao as
  mesmas telas de antes (Jogos, votacao), sem regra nova. Quem chega
  pelo botao leva `?next=` de tela em tela: a aba de baixo continua a de onde o jogador saiu (Inicio); so caminhos do proprio
  site valem. O mesmo destino e a aba **Domingo**, junto do historico.
- Na **Tabela de jogos** a diretoria chama o proximo jogo com um botao, sem montar rodada: o primeiro
  e entre os dois primeiros times; depois o vencedor fica e entra o time que espera ha mais tempo, e
  num empate (no fim do tempo) saem os dois e entram os dois que estavam de fora. O goleiro de cada
  lado sai da fila entre os goleiros da lista e os jogadores de linha sorteados para o gol (quem
  fica em quadra mantem o seu) e pode ser trocado na tela do jogo. Os jogadores veem o proximo jogo
  no Inicio e em **/jogo/** assim que o anterior termina.
- Um diretor registra os gols de cada partida: ao tocar em quem marcou, o app pergunta quem deu a
  assistencia (um jogador, "Nenhum" ou "Nao se aplica"; `Goal.assist_not_applicable`, migracao
  `matchday.0006`), ou registra um gol contra. A
  partida acaba quando um time faz 2 gols, ou quando o diretor encerra no fim do tempo (empate
  vale). O cronometro de 7 minutos roda so no celular do diretor; nao ha placar ao vivo, os
  jogadores veem os resultados ao abrir a tela.
  O autor e a assistencia podem ser qualquer jogador da lista (o time que marcou aparece
  primeiro), e os times podem ser refeitos ou ajustados a qualquer hora: o gol guarda o time, o
  autor e a assistencia como foram registrados, entao o placar nao muda.
- Como o futebol e informal, tudo e editavel a qualquer momento, inclusive depois que a partida
  acabou: editar ou apagar um gol (trocar autor, assistencia, time ou virar gol contra, ate com a
  partida encerrada), adicionar gol a partida encerrada, trocar os times de uma partida, subir ou
  descer a partida na tabela e apagar a partida com os gols dela. Os limites do placar (7 minutos,
  2 gols) continuam valendo.
- Para uma troca so numa partida (alguem cobrindo outro time), use **Quem jogou esta partida**
  na tela da partida; a escalacao do domingo nao muda. Cada partida guarda quem a jogou
  (`MatchPlayer`; os goleiros ficam na propria partida): e isso que a fase 4 conta para as
  vitorias. Partidas sem gol e nao encerradas acompanham os ajustes dos times; ao registrar o
  primeiro gol, encerrar ou editar a escalacao da partida, ela passa a guardar o que foi jogado.
  Quem faz ou da o gol por um time entra na escalacao dele na partida.
- **Diretoria > Montar o domingo** e uma tela de quatro passos, nesta ordem: **Times** (montar e confirmar;
  a lista de jogadores fica recolhida dentro do passo, com Adicionar jogador so antes das 10h do
  domingo), **Primeiros jogos** (trancado ate os times estarem confirmados; nunca espera a
  presenca), **Presenca** (marcada durante o futebol, so a partir das 10h, agrupada por time, cada
  grupo recolhido; nunca trava os times nem os jogos) e **Finalizar e cobrar** (trancado ate as 10h;
  lista quem faltou com a multa e junta Multar por atraso e Finalizar jogo e cobrar).
  So o passo da vez abre sozinho; o que a pessoa abre ou fecha fica assim ate sair da tela.
  Cancelar este domingo fica no fim da tela, separado dos passos, e pede um motivo e confirmacao
  (anula as multas pendentes e as cobrancas em aberto do domingo). Falta gera
  multa e o sorteio ignora quem faltou, mas so ate os times serem confirmados: depois disso uma
  falta so gera a multa e os times publicados nao mudam. O botao de relogio ao lado de cada nome
  marca o atraso (15, 30, ou toque de novo para limpar) e **Multar por atraso**, no passo Finalizar e cobrar, multa de
  uma vez quem foi marcado. A barra de baixo da diretoria nao tem aba Times: os times abrem pelo
  passo Times.

O sorteio fica em `src/matchday/domain/` (funcoes puras, testadas sem banco). Os gols, as
assistencias, os times de cada domingo e os resultados alimentam o ranking e o historico da [Comunidade](#comunidade-resumo).

## Comunidade (resumo)

- **Bola de Ouro**: depois que o futebol e finalizado (pela diretoria ou pelo encerramento
  automatico do domingo as 12h30) e ate segunda 8h, o mesmo instante em que a lista da semana abre
  (`BABA_LIST_OPENS_AT`), quem jogou o domingo vota em tres jogadores (1o, 2o e 3o, valendo 5, 3 e 2 pontos) em **/bola-de-ouro/**.
  Nao da para votar em si mesmo nem repetir jogador, e o voto nao pode ser alterado. Jogam e
  recebem votos todos que estiveram no domingo (linha e goleiros), exceto quem foi marcado com
  falta. A tela de votacao mostra so o contador "N de M votaram", sem revelar escolhas nem quem
  falta, e um voto recusado volta ao formulario com as escolhas preservadas. O resultado so
  aparece na segunda 8h, quando a votacao fecha (mesmo que todos ja tenham votado), em podio (1o, 2o e 3o). O card da tela inicial so convida
  a votar quem pode votar. Empate em pontos desempata por
  mais 1os lugares, depois 2os e 3os; se ainda empatar, dividem a Bola de Ouro. Sem votos nao
  ha Bola de Ouro.
- **Voto anonimo de verdade**: o banco guarda apenas que a pessoa votou (`Ballot`) e um
  contador de votos por jogador e lugar (`PlaceTally`). Nao existe registro de quem votou em
  quem, nem para a diretoria.
- **Ranking** (**/ranking/**): Bolas de Ouro, gols, assistencias, presenca (domingos jogados) e
  vitorias, por temporada ou geral, ordenavel por cada coluna. Contam as partidas encerradas para jogos e vitorias; todos os gols e assistencias contam. O
  lado de cada jogador numa partida vem do goleiro da partida, do time sorteado ou do gol
  marcado (quem entrou no lugar de alguem). Os numeros sao calculados na hora a partir dos
  gols, partidas e listas, entao corrigir um gol corrige o ranking.
- **Um app so**: a barra de baixo e a mesma para todos (**Inicio**, **Domingo**, **Turma** e
  **Mais**), e quem e da diretoria ve uma aba a mais, **Diretoria**. Ela abre o **Painel** (so
  gestao: Montar o domingo, Times, Jogos e placares, Comprovantes, A receber, Caixa,
  Configuracoes, Jogadores, Bola de Ouro e Temporada); com um jogo em andamento, abre direto no
  placar desse jogo. As abas Domingo e Mais sao iguais para todos, sem controles de gestao. O
  avatar no topo leva ao proprio perfil e o logo ao Inicio. Cada tela da diretoria continua
  exigindo a permissao no servidor. O antigo `/modo/` so redireciona de volta a tela.
- **Turma e perfil**: **/jogadores/**, uma aba da barra de baixo (**Mais**, **/mais/**, tem Minha conta (foto, celular e e-mail, senha), Dinheiro (Pagamentos e Pix, Historico de multas, Caixa), Ajuda (Tutorial, Regras, Sobre, Reportar bug ou sugestao) e **Sair**; o Ranking e a Bola de Ouro ficam a partir de Turma; o mensalista ganha uma bolinha "M" no avatar; enquanto a votacao da Bola de Ouro esta aberta, o card de voto abre o Inicio), e o diretorio (busca por nome e
  filtro por tipo ou goleiros). Cada jogador tem uma pagina em **/jogador/<id>/**, com abas
  escolhidas por `?aba=` e trocadas por HTMX: **Resumo** (totais, presenca em % e ultimos
  domingos, com filtro de temporada em `?temporada=`), **Domingos** (placar de cada partida do
  ponto de vista do jogador), **Bolas de Ouro** (Bolas de Ouro e podios de 1o, 2o e 3o lugar, so depois que
  a votacao fecha) e **Origem** (quem indicou, desde quando esta no bába e quem trouxe). Quem
  esta logado ve tudo isso de todos; mensalidade, multas e Pix so aparecem para o proprio
  jogador e para a diretoria (o perfil do proprio jogador e enxuto: mostra so se ha cobranca em aberto, e o jogador chega a Pagamentos por Mais e por um cartao na tela inicial quando ha pagamento pendente; o historico de multas fica em Mais), e celular, e-mail e pote so para a diretoria, com o botao Editar quando o perfil foi aberto a partir da aba Diretoria (o proprio jogador ve o seu celular e e-mail e os altera em **Mais > Celular e e-mail**, junto de Senha; a indicacao fica na aba Origem do perfil). O voto da
  Bola de Ouro continua anonimo: o proprio jogador so ve se votou. Na tela de edicao do
  jogador a diretoria corrige o **indicado por** (opcional para quem nao tem
  convidante; a conta criada pela diretoria guarda quem a criou) e o **no bába desde**, opcional, para quem ja jogava antes do app. A **foto** (JPG, PNG ou WebP, ate 10 MB) e opcional: entra no cadastro ou depois em **/foto/**, e a diretoria troca ou remove ao criar ou editar o jogador. O app recorta em quadrado, reduz e regrava como JPEG (sem EXIF), no mesmo bucket privado dos comprovantes, e so quem esta logado a ve (no perfil do jogador, tocar na foto abre ela ampliada). O CSS fica todo em
  `app.css`, porque a CSP bloqueia estilo inline.
- **Temporada**: o presidente encerra a mao em **Diretoria > Temporada** (**/diretoria/temporada/**). O dia do
  encerramento ainda pertence a temporada que acabou e a proxima comeca do zero; as anteriores
  continuam consultaveis no ranking.

A leitura de quem jogou cada partida fica em `src/community/facts.py` e as regras puras em
`src/community/domain/` (votacao, estatisticas, ranking e temporadas).
