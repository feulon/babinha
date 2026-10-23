# Babinha - plano do produto

Aplicativo para gerenciar o bába semanal de domingo de um grupo de amigos, sempre no mesmo campo e horario. Futebol society: 5 de linha e 1 no gol. Hoje tudo acontece pelo WhatsApp.

## Plataforma

- Web app instalavel (PWA), mobile first, somente em portugues (pt-BR).
- Uma unica bába (sem multi-grupo).
- Hospedagem inicial em servicos gerenciados, mas construido de forma portatil para rodar depois num notebook: banco Postgres padrao, armazenamento de arquivos compativel com S3, app em conteiner. Evitar recursos que prendam a um unico provedor.
- Rodando no notebook exige: tunel para acesso externo (ex.: Cloudflare Tunnel), HTTPS, backup externo do banco e dos comprovantes.
- Sem notificacoes automaticas na primeira versao. Os avisos continuam no WhatsApp (a unica excecao e o aviso de pagamento pendente por e-mail, enviado pela diretoria; ver Dinheiro), entao a tela inicial precisa mostrar com clareza o estado da pessoa (na lista, na espera, pagamento pendente).

## Pessoas e acesso

- Cadastro: celular (obrigatorio), senha de 4 caracteres, nome ou apelido e quem indicou (texto livre e opcional no cadastro; depois, no perfil, a pessoa liga a indicacao a um jogador cadastrado). So a indicacao ligada a um jogador vale na montagem dos times: na primeira bába de um jogador ele fica no time de quem o indicou (ver Dia do jogo). O cadastro mostra as regras (multas, prioridade e demais, com os valores atuais) e exige o "estou ciente", registrado com a data. Foto de perfil opcional (recomendada, para a turma identificar quem e quem), no cadastro ou depois; so visivel para quem esta logado.
- O celular (brasileiro, com DDD) e o login e e obrigatorio; o e-mail tambem e obrigatorio no cadastro (avisos de pagamento e recuperacao de senha), mas contas antigas sem e-mail seguem validas. Entra-se com o celular ou com o e-mail. Cada um e unico quando preenchido. Quem se cadastrou antes do celular existir informa o numero no proximo acesso. So o proprio jogador e a diretoria veem celular e e-mail, nunca a lista publica nem o perfil publico.
- Login com bloqueio temporario apos tentativas erradas (ex.: 5 erros por aparelho, espera de 15 minutos; alem disso, 20 erros por hora em uma conta, de qualquer aparelho, bloqueiam o login ate passar o prazo ou a diretoria definir uma nova senha). Recuperacao de senha por link no e-mail; quem nao tem e-mail pede a diretoria, que define uma nova senha provisoria na edicao do jogador e avisa a pessoa fora do app, pelo WhatsApp. Sem SMS nem verificacao do numero.
- Padrao do novato: linha, avulso e pote 3.
- Tipos fixos de jogador: avulso e agregado, definidos pela diretoria; o agregado vira avulso apos N futeboes (configuravel) e o primeiro login leva as Regras. Para a adesao mensal, ver [Mensalidade no README](../README.md#dinheiro-resumo).
- Posicao: linha ou goleiro. Definida pela diretoria.
- Diretoria (administradores) e presidente tem os mesmos poderes. O presidente e o dono do Pix mostrado no app.
- Poderes da diretoria: tipo e posicao dos jogadores, potes, comprovantes (pago ou recusado), despesas, cancelar domingo, valores, Pix, editar a lista, operar placar, perdoar multa, nomear outros diretores, encerrar temporada.
- Potes: so a diretoria ve e edita. Jogadores veem os times formados, nunca o pote.

## Lista semanal

- Abre na segunda de manha para mensalistas e avulsos.
- Abre na quarta as 20h para agregados, so se ainda houver vaga.
- Mensalista tem prioridade: se a lista enche, ele toma a vaga do ultimo nao mensalista.
- Limite de 4 goleiros. Vagas de linha: 20 com 2 a 4 goleiros, 21 com 1 goleiro, 22 com nenhum. O total fica entre 22 e 24 pessoas (22 com ate 2 goleiros, 23 com 3, 24 com 4).
- Fila de espera automatica por ordem de chegada, respeitando posicao (vaga de goleiro so por goleiro).
- Quem e promovido da fila pode recusar sem multa ate sabado 23h59.
- Desistencia sem custo ate sabado 23h59.
- Lista de presenca em cada futebol, marcada pela diretoria durante o futebol (nunca trava os jogos).

## Dinheiro

- Mesmo valor para todos. Os valores (mensalidade, avulso, multas) serao definidos depois e devem ser configuraveis pela diretoria.
- A vaga vale ao confirmar. O pagamento do domingo so e cobrado depois do jogo (12h30 de domingo ou quando um diretor finaliza o jogo), com prazo (ate sabado). Quem nao pagou aparece como pendente para a diretoria.
- Mensalidade: ver as regras de adesao, vencimento e renovacao no [README](../README.md#dinheiro-resumo).
- O jogador envia foto ou PDF do comprovante do Pix. A diretoria marca pago ou recusado (com motivo). Chave Pix unica, configuravel, mostrada no app junto com o valor.
- Multa: desistencia depois do prazo, falta sem aviso (pela lista de presenca) ou atraso. O atraso tem valor fixo por faixa (padrao: 15 min = R$ 10, 30 min = R$ 25; editavel pela diretoria), fora da escada das faltas, e a diretoria multa no fim do domingo. A multa de falta parte de um valor base (R$ 50) e sobe pelo multiplicador (padrao 1,5x, editavel pela diretoria) a cada ocorrencia (50, 75, 112,50...), por jogador, e a contagem nunca zera. Paga pelo mesmo fluxo de Pix e comprovante.
- Qualquer diretor pode perdoar a multa. O perdao cancela a cobranca e apaga a ocorrencia da contagem. O app registra quem perdoou e o motivo.
- Aviso de pagamento pendente: em "Avisar quem deve" (Financeiro > A receber) a diretoria escolhe devedores e envia um e-mail de cobranca. So quem tem e-mail recebe; quem ja foi avisado recentemente e pulado, a menos que a diretoria marque para repetir. Quem nao tem e-mail e avisado fora do app, pelo WhatsApp: "Cobrar no grupo" (na mesma tela) monta uma mensagem para o grupo do WhatsApp marcando o telefone de cada devedor; o app so monta o texto, quem compartilha e o diretor.
- Despesas: a diretoria lanca a mao (valor, descricao, data).
- Analise financeira aberta: todos veem so os totais (arrecadado, despesas, saldo do mes). A diretoria ve tambem quem pagou e quem esta pendente.
- Domingo cancelado: a diretoria marca, sem cobranca nem devolucao automatica.

## Dia do jogo

- Times gerados por sorteio balanceado por pote (1 melhor, 5 pior), com a soma dos potes de cada time a mais proxima possivel (a mesma quantidade de cada pote por time desempata). A diretoria ajusta a mao antes de confirmar. Times novos toda semana.
- Montagem dos times numa tela so: sortear, trocar com um toque e confirmar, com as regras recolhidas. Regras: o estreante (nunca teve presenca marcada em um domingo anterior) joga no time de quem o indicou quando os dois estao na lista como linha, com um interruptor por domingo e a liberacao de um estreante (deixar jogar em outro time) so naquele domingo; e grupos de 2 a 5 jogadores que a diretoria quer no mesmo time. Grupos nunca sao separados pelo sorteio nem pelo ajuste. Se as regras nao cabem (um grupo maior que o time, ou grupos que juntos nao cabem), a diretoria ve o motivo e escolhe uma saida junto ao grupo (deixar o estreante jogar em outro time, tirar alguem do grupo, aceitar um time maior), nao sorteia enquanto houver conflito e o app nao deixa confirmar. Jogador de linha so vai ao gol alem dos 20 dos times. Times com quantidade ou forca desiguais so geram aviso: a diretoria pode confirmar mesmo assim.
- Quatro times de 5 jogadores de linha. Goleiros nao tem pote. Goleiros sao compartilhados e rodam por jogo. Jogadores de linha excedentes (1 ou 2) completam o gol, e o app sorteia quem entre eles.
- Os jogos seguem a fila: o vencedor fica, entra o time que espera ha mais tempo e, no empate, saem os dois e entram os dois que estavam de fora. A diretoria chama o proximo jogo e ajusta o goleiro de cada partida. O app registra os resultados.
- Placar operado por um diretor no celular, com cronometro de 7 minutos. A partida acaba com 2 gols ou no fim do tempo (empate encerra como empate). Registra o autor do gol e, em seguida, a assistencia (um jogador, nenhum ou nao se aplica). Todos veem o placar ao vivo.

## Comunidade

- Bola de Ouro por domingo: quem jogou vota em tres jogadores (1o, 2o, 3o, pontos 5, 3 e 2), sem votar em si mesmo, voto anonimo para todos. Abre apos o encerramento do futebol do domingo e fica aberta ate a segunda 8h, quando a lista da semana abre: so entao sai o resultado, mesmo que todos ja tenham votado.
- Historico completo por jogador e ranking acumulado: Bolas de Ouro, gols, assistencias, presenca e vitorias. A diretoria encerra a temporada a mao.

## Fases de entrega

1. **Base:** cadastro e login, lista semanal com os tres tipos, vagas de linha e goleiro, fila de espera, desistencia e multas.
2. **Dinheiro:** Pix com comprovante, confirmacao pela diretoria, mensalidade, despesas e analise financeira.
3. **Dia do jogo:** times pelos potes, tabela, placar com cronometro, lista de presenca.
4. **Comunidade:** Bola de Ouro, historico e ranking da temporada.

## Em aberto

- Valores (mensalidade, avulso, multas 1a, 2a e 3a).
- Data desejada para usar no domingo.
- Custo mensal maximo.
- Botao de compartilhar a lista no WhatsApp (opcional, depois).
