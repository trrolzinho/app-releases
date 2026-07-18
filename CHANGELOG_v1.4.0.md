**TofuBot v1.4.0**  
**🆕 Novidades**  
- **Fugir do Boss (Floresta Profunda)** — na Caçada Solo, quando o mapa da conta é "Floresta Profunda" (sub-área de Floresta Sombria), agora dá pra marcar uma opção pra fugir só do Boss ("Abominação do Aspecto Caído", 1800 HP) e continuar caçando os goblins comuns normalmente (260–450 HP). Vem com quadro de %HP por monstro próprio pra esse mapa, separado do de Montanhas Gélidas.  
- **Retomar conteúdo ativo** — parou o bot no meio de uma Masmorra/Cripta/Caçada em Dupla/Templo do Oásis/Caçada Solo/Missão Oásis (ou fechou pra atualizar) e iniciou de novo? Agora ele detecta que já tá em combate e  **continua de onde parou**, em vez de sair da sala e começar tudo de novo. Faz um check forçado do cooldown das almas logo na 1ª ação, pra não ficar um tempo achando que uma alma ainda tá recarregando à toa.  
- Delay configurável antes de confirmar uma venda no Mercado (MERCADO_DELAY_CONFIRMACAO_SEG, padrão 10s) — dá tempo de conferir os itens marcados e abortar (botão Parar) antes da venda ser confirmada de verdade.  
- Flag LIMPEZA_PROFUNDA_ATIVO (padrão desligada) — controla se a limpeza que apaga TODO o histórico da conversa roda no início de cada sessão. Desligada, a conta pula tanto a limpeza quanto o /start que ela exigia.  
**🐛 Correções**  
**Leitura de HP / vida das contas**  
- **[Crítico]** Corrigido um bug em que o HP de uma conta podia ser lido errado se o nome dela fosse substring de um nome de monstro do jogo (ex.: "Pri" dentro de "Yeti  **Pri**mordial") — o bot chegou a acompanhar o HP do monstro em vez do da própria conta, o que podia atrasar/errar a decisão de beber poção.  
- Corrigido o mesmo tipo de problema pra nomes terminados em pontuação (ex.: "Léozão S.") — a correspondência por fronteira de palavra não fechava certo nesses casos; trocada por uma checagem baseada em caracteres alfanuméricos nas pontas do nome.  
- Atacar/Defender: se o botão não for encontrado (conta presa num submenu como "Escolha uma alma", sobra de uma ação anterior que não fechou direito), agora volta pro combate antes de tentar de novo — antes, o bot ficava repetindo a mesma ação contra um botão inexistente até quase estourar o tempo da rodada e perder o turno.  
**Mercado (Vender/Ler inventário)**  
- Corrigido loop infinito ao marcar itens pra vender (o bot ficava clicando o mesmo item repetidamente sem avançar).  
- Corrigido: itens duplicados (mesmo nome/reforço, cópias diferentes) só vendiam 1 cópia.  
- Ampliada a detecção de "próxima/anterior página" pra telas que não usam a palavra "página" no botão.  
- Corrigido: leitura do inventário pulava a categoria "Armas" quando ela já vinha aberta por padrão.  
**Retomada de conteúdo / travamentos**  
- Corrigido: uma etapa de viagem genérica no início da execução podia tirar uma conta do combate ativo antes da detecção de retomada rodar, fazendo o bot sair da sala e criar uma nova sem necessidade.  
- Corrigido: uma checagem de "Poções de Vida no estoque" (que abre o Inventário) rodava mesmo numa retomada manual, com o mesmo efeito de tirar a conta do combate.  
- Corrigido: notificações avulsas sobrepondo a tela de combate (ex.: aviso de troca cancelada) podiam fazer o bot desistir do combate achando que tinha acabado, sem ninguém olhando o HP da conta durante a espera — causou uma morte relatada. Agora reconhece esse tipo de tela pelo formato (não só por frases específicas) e confere o HP de emergência em toda tentativa de espera, não só no fim.  
- Corrigido: na Missão Oásis (quest da Nurmora, Vale das Miragens), a escolha de trilha não conferia energia antes de entrar — ao zerar, ficava em loop infinito sem nunca voltar ao Oásis pra reabastecer.  
- Corrigido: na Floresta Profunda, o botão pra continuar caçando depois de uma vitória tem um texto diferente do padrão ("Caçar de novo") — o bot não reconhecia e ficava parado.  
- Corrigido: em Montanhas Gélidas, o andar avança dentro da mesma tela de combate (sem transição/botão), então o contador de segurança de rodadas nunca resetava e o bot saía achando que tinha travado, mesmo com o combate indo bem.  
- Reduzido o uso de /start (motivo de banimento real já relatado) em vários pontos — o bot agora tenta "sair do lobby" e "voltar" antes de recorrer a ele.  
**Painel**  
- Aba Mercado enxugada (textos viraram ícones de ajuda com tooltip).  
- Botões que cortavam texto longo (ex.: "Ler inventário agora") agora crescem certo.  
- Corrigido: valores de %HP por monstro entre Montanhas Gélidas e Floresta Profunda podiam se sobrescrever ao trocar de mapa.  
- Corrigido um vão vazio no layout quando o quadro de "%HP poção (geral)" ficava escondido.  
![](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAnEAAAACCAYAAAA3pIp+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAANUlEQVR4nO3OMQ2AABAAsSNhZscVjnidKEAGFtgISaugy8zs1RkAAH9xr9VWHV9PAAB47XoAor8EPg1yCpUAAAAASUVORK5CYII=)  
*Versão anterior publicada: v1.3.1.*  
