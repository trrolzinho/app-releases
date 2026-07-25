**TofuBot v1.5.0**  
   
 **🆕 Novidades**  
- **Bot de Controle via Telegram** — um bot de verdade (criado no @BotFather, com botões clicáveis), separado das contas que jogam, rodando num grupo/chat à parte. Liga/pausa o bot, mostra status ao vivo por conta (HP/XP/energia/conteúdo/mob atual) e manda alertas automáticos (item raro, morte, conta travada, recorde pessoal batido, level-up, elixir/tônico expirado) sem precisar abrir o painel gráfico. Autorização via /registrar no grupo (guarda o ID do Telegram de quem manda).  
- **Fortaleza dos Orcs** — novo conteúdo em grupo (1 a 5 contas), sala sem senha, 3 salas + 1 sala de boss ("Fosso de Provas" Lv46+ ou "Trono de Khar'gath" Lv48+). Exige uma skin específica ("Pele de Goblin"/"Pele de Orc"), que o bot confere e equipa sozinho antes de entrar.  
- **Minas Abandonadas nas Montanhas** — 3 novas masmorras em Montanhas Gélidas (Ruínas de Azulgor Lv42, Lago de Kryos Lv44, Túneis Proibidos Lv46), sem senha e sem tela de confirmação — clicar no botão já forma a sala. O host confere se entrou algum intruso antes de iniciar e recria a sala se precisar.  
- **Covil do Lord** — nova masmorra alternativa em Cemitério Antigo, usa a "Chave do Ossuário" (escondida em Inventário → Ferramentas, não é a Chave de Masmorra normal).  
- **Dragão de Cristal de Frost** — mob elite raro da Caçada em Dupla em Montanhas Gélidas: o bot agora registra as derrotas e os itens lendários que ele dropa, com relatório de % de drop por item. Notificação opcional a cada derrota (desligada por padrão).  
- **Masmorra/Templo do Oásis/Fortaleza dos Orcs unificados** — os 3 viraram opções de um único dropdown "Masmorras" dentro da aba Masmorra, em vez de sub-abas separadas. Caçada em Dupla e Templo do Oásis agora suportam até 4 duplas simultâneas (antes só 2).  
- **Cartão de conta redesenhado** — resumo compacto (personagem + almas), campos de edição escondidos atrás de um botão "editar", botões ↑/↓ pra reordenar contas, e HP/XP/Energia/ATK/DEF/CRIT/buff ativo mostrados ao vivo direto no cartão (fica com a última memória do bot mesmo parado/pausado).  
- **Comprar poções sob demanda** — pelo painel ou pelo Telegram, digita a quantidade e o bot vai na loja comprar Poção de Vida, Poção de Energia ou os Super Tônicos (Força/Precisão/Defesa), com ou sem o bot ligado, escolhendo quais contas compram.  
- **Compra automática de poções** — dois toggles opcionais (desligados por padrão) em Configurações: reabastecer Poção de Vida até um alvo, e comprar um lote de Poção de Energia quando o estoque acabar — evita pausar o bot só por falta de consumível.  
- **"⏹️🚪 Parar e Sair"** — novo botão que faz o bot sair da sala/masmorra atual (usando a saída normal, com confirmação) antes de parar de vez, em vez de deixar a conta exposta em combate como o "Parar" normal faz.  
- **Elixir da Fortuna** e  **modo "só no boss quase morto"** — o dropdown de elixir ganhou o Elixir da Fortuna (+Drop) junto dos de Sabedoria, e um novo modo que segura o elixir até o boss atual estar com HP quase zerado, pra render o buff quase inteiro só depois do fim da masmorra.  
- **Fugir do Boss em Montanhas Gélidas** — mesmo princípio já existente pra Floresta Profunda: marca a conta pra fugir só do boss ("Grimmrok, o Eterno Inverno") e continuar caçando os monstros comuns normalmente.  
- **Cemitério Antigo — só Colar da Paz** — modo por conta que foge de todo mob normal só pra esperar o NPC "Coveiro Huaguilli" aparecer e vender o Colar (reduz perda de XP ao morrer).  
- **Planície — alvo único** — checkbox pra caçar só o Orc na Planície, fugindo do resto (mesma ideia já usada no Deserto/Oásis).  
- **Meta de Martelos Mágicos** — na Missão Oásis, modo "Só Nurmora", dá pra definir quantos Martelos coletar antes de parar sozinho.  
- **Relatório mais completo** — ranking de dano diário, "📦 Drops de hoje" agrupado por raridade, XP "real" do dia (baseline capturado à meia-noite, reflete ganhos/perdas de fora do bot também), e alerta de recorde de velocidade pessoal por conteúdo.  
- **Visualizador de log embutido no painel** — quando nenhum terminal do sistema abre (comum em algumas distros Linux), o "Ver log" agora mostra o run.log numa janela própria do painel, atualizando sozinha.  
**🐛 Correções**  
   
 **Leitura de HP / vida das contas**  
- **[Crítico]** Corrigido um bug em que, mesmo sem NUNCA confirmar que o HP subiu depois de beber uma poção (ex: erro do Telegram no meio, botão sumiu), a função de curar ainda retornava sucesso — a conta seguia agindo várias rodadas sem cura confirmada até morrer. Agora, se nenhuma tentativa confirmar a cura, volta pro combate e avisa quem chamou pra agir de novo na mesma rodada.  
- Corrigida a leitura de HP no Templo do Oásis: a etiqueta "NEW" de conta nova aparece colada direto no nome nessa tela (sem colchete, ex: "NEWMorcequinho"), o que bloqueava o reconhecimento do nome e derrubava a leitura de HP.  
- Adicionado um fallback pra leitura do HP do monstro na Caçada em Dupla em Montanhas Gélidas, mapa que não usa o formato "ID:"/"HP:" de sempre.  
- Rede de segurança: quando o HP não consegue ser lido de jeito nenhum (tela presa num submenu, por exemplo), o bot agora usa "levou dano nesta rodada" como sinal pra beber poção por segurança, em vez de simplesmente assumir que está tudo bem — vale pro tank, pros outros papéis e pra Caçada Solo.  
- O "dano sofrido" mostrado no log agora usa o maior valor entre a diferença de HP e o evento de dano na tela, cobrindo casos em que cura e dano acontecem na mesma rodada e mascaravam o valor real.  
**Segurança contra banimento**  
- **[Crítico]** Corrigido um banimento real confirmado em produção: loops de retry podiam mandar vários /start em sequência rápida pra mesma conta, e o Telegram trata isso como flood/spam e bane a conta. Agora existe um intervalo mínimo obrigatório entre /starts pra mesma conta.  
- Corrigidos dois botões (◀ Lojas, 🔁 Voltar à troca) que batiam na busca genérica de "voltar" de tela travada, mas na verdade só voltavam pra DENTRO da própria tela — o bot ficava preso ali e acabava mandando /start sem necessidade.  
**Retomada de conteúdo / travamentos**  
- Corrigido: quando uma conta "perdia a vez" no meio de uma Masmorra (reinício pedido por outra conta), ela ficava presa fisicamente dentro da sala, sem sair — enquanto o resto do código já assumia que ela estava livre no menu.  
- Corrigido: se a Cripta terminava no andar-limite, as contas travavam tentando confirmar a saída e o XP/Gold acabava de fora do relatório.  
- Corrigido: se uma morte acontecia bem quando outra conta tomava um erro de rede, o processo podia encerrar com contas ainda presas dentro da sala, em vez de sair antes de parar.  
- Corrigido: a checagem de "Parar no fim" dependia da conta responsável terminar de ler a tela de conclusão — se essa leitura falhasse, ninguém via o pedido e o bot formava a próxima masmorra mesmo assim.  
- A checagem de Poção de Vida antes de iniciar um conteúdo era pulada corretamente ao retomar uma Masmorra em andamento (mesma correção que já existia pra Caçada em Dupla), evitando telas travadas.  
**Mercado / Loja / Inventário**  
- Corrigido: a tela de Viajar podia mostrar um mapa "Atual:" ERRADO (bug do próprio jogo) — o bot confiava nisso pra decidir se pulava a viagem, e ficava preso no mapa errado. Agora sempre clica de verdade no destino.  
- "Ler inventário agora" passou a ler pela tela de Vender da Loja (lista única, mais rápida e confiável) em vez de percorrer as 6 categorias do Inventário.  
- Pedidos manuais de Vender/Ler/Comprar agora persistem em disco quais contas já atenderam cada pedido — antes só ficava na memória da sessão, e um reinício do bot fazia um pedido antigo (de dias atrás) disparar de novo sozinho.  
- Corrigido: se o jogo exigisse mais energia pra entrar numa Caçada em Dupla do que o limite configurado, a conta ficava presa tentando entrar pra sempre; agora bebe Poção de Energia até atingir o que o jogo pediu.  
**Drops e raridade de itens**  
- Corrigido: Almas (ex: Muralha Orc) dropam com o texto "obteve Item (ALMA)", formato diferente do resto dos itens ("encontrou um/uma Item") — não eram contabilizadas na Cripta nem na Fortaleza dos Orcs.  
- Corrigida a raridade do Totem Obscuro e de itens sem "(raridade)" explícito no texto de drop das Minas.  
- Corrigido um alerta de item raro duplicado no Telegram (chegava 2 vezes pro mesmo drop).  
- Corrigido: quando 2 itens dropavam na mesma linha (separados por vírgula), o bot tratava como se fosse o nome de 1 item só; agora separa e limpa cada um.  
- Corrigido um ícone de classe de arma (🏹, 🗡️ etc.) que ficava colado no nome do item em certos eventos, fazendo o mesmo item virar 2 entradas diferentes no banco de itens.  
**Fortaleza dos Orcs / Covil do Lord / Minas**  
- Corrigido: o XP/Gold da Fortaleza dos Orcs estava sendo somado por todas as contas do grupo, inflando o valor (ex: 4x maior que o real) — a recompensa é individual e igual pra todos, agora usa o valor de 1 conta só.  
- Corrigido: o Covil do Lord usava a Chave do Ossuário (escondida em Inventário → Ferramentas), mas o bot lia a contagem de Chaves de Masmorra normais, do menu principal — nunca reconhecia que a chave certa existia.  
- Corrigido: o Templo do Oásis sempre usava a ordem configurada pra escolher o host, sem olhar quem tinha mais Chave de Masmorra (diferente da Masmorra normal, que já fazia isso).  
- Corrigido um falso positivo de "morte detectada no grupo" nas Minas: a mensagem de morte do MOB (na transição automática de andar) estava sendo lida como morte de jogador.  
**Painel**  
- Erros ao atualizar o Relatório agora aparecem numa mensagem na tela em vez de falhar silenciosamente sem deixar rastro.  
- Corrigida uma condição de corrida em que o painel podia ler o relatorio.json pela metade (bem no instante em que o hunter.py estava gravando) e mostrar "nada" sem erro nenhum; agora tenta de novo antes de desistir.  
- Corrigido: os registros da Fortaleza dos Orcs no relatório detalhado não eram ordenados por data junto com os da Masmorra, sempre aparecendo no topo mesmo quando mais antigos.  
- Corrigido um tooltip (balão de ajuda) que ficava preso na tela pra sempre depois de passar o mouse em cima uma primeira vez (ícone "Parar no fim").  
- Corrigido um falso "bot já rodando" ao abrir o painel numa pasta copiada: a checagem de PID não conferia se o processo era mesmo desta pasta, só se parecia com um bot de qualquer pasta do PC.  
- Corrigido o alinhamento de barras de HP/texto no Linux: o app usava a fonte "Segoe UI" (exclusiva do Windows) fixa no código; agora detecta e usa uma fonte disponível de verdade no sistema.  
- "Ver log" ganhou suporte a mais terminais Linux (GNOME Console, ptyxis, kitty, alacritty, wezterm, foot, entre outros) além do visualizador interno de log como último recurso.  
*Versão anterior publicada: v1.4.0.*  
