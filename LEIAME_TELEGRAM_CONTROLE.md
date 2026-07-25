# LEIAME — Controle do TofuBot via Telegram

Isso é um **complemento** do painel — dá pra ligar/pausar o bot, ver status,
relatório e receber avisos automáticos direto pelo Telegram, num grupo
privado, sem precisar deixar o painel gráfico aberto.

Só funciona se o TofuBot "normal" (painel + bot) já estiver configurado e
funcionando pelo menos uma vez (contas, API ID/API HASH etc.) — o controle
reaproveita essa mesma configuração, não precisa configurar as contas de
novo.

## Passo a passo

### 1. Criar o bot de controle no Telegram

1. Abra uma conversa com **@BotFather** no Telegram.
2. Mande `/newbot`, escolha um nome e um `@usuario` pro bot (ex:
   `MeuGrupoTofuBot`).
3. O BotFather te devolve um **token**, parecido com
   `123456789:ABCdefGHIjklMNOpqrstUVwxyz`. Copie ele — é secreto, não
   compartilhe (quem tiver o token consegue controlar o bot pelo Telegram).

### 2. Colar o token no painel

1. Abra o `painel.py` (ou `painel.exe`) normalmente.
2. No cabeçalho, clique no ícone **✈** (avião de papel).
3. Cole o token no campo "Token do bot" e clique em **💾 Salvar**.

### 3. Criar o grupo e adicionar o bot

1. Crie um grupo novo no Telegram (pode ser só você, ou você + os amigos que
   também vão controlar o bot).
2. Adicione o bot que você criou no passo 1 como membro do grupo.

### 4. Rodar o controle

O controle roda **separado** do bot que joga — é um processo à parte, na
MESMA pasta do `hunter.py`/`painel.py`/`config.py`.

- **Windows**: dê 2 cliques em `controle.cmd`.
- **Linux**: rode `./controle.sh` no terminal.

Deixe essa janela aberta (pode minimizar) — é ela que fica de olho no bot e
manda os avisos automáticos. Se fechar, o controle para (o bot que joga
continua rodando normalmente, só o controle via Telegram que some).

### 5. Autorizar quem vai usar

No grupo que você criou, cada pessoa que for controlar o bot manda, **uma
única vez**:

```
/registrar
```

Depois disso, ela já pode usar `/menu` e os botões normalmente. Não tem
limite de quantas pessoas podem se registrar no mesmo grupo.

## O que dá pra fazer pelo Telegram

Mande `/menu` no grupo pra ver o painel de botões. Alguns destaques:

- **🚀 Iniciar / ⏹ Parar agora / ⏳ Parar no fim** — liga/desliga o bot sem
  precisar abrir o painel gráfico.
- **📊 Status / 👤 Status contas / 🩺 HP% contas** — acompanhar em tempo
  real o que cada conta está fazendo.
- **📈 Relatório / 📊 Estatísticas** — XP, gold, drops do dia, ranking,
  progresso de nível (com barra), eficiência por conteúdo, gráficos, etc.
  (mesmos números do Relatório do painel).
- **🛒 Mercado** — vender itens agora, ler inventário, comprar poções/tônico
  sem precisar mexer no jogo na mão.
- **🗺️ Mapas / 🎮 Conteúdo** — ver e trocar o conteúdo/mapa atual.
- **📟 Ver log** — as últimas linhas do log de Atividade, direto no chat.

## Avisos automáticos (⚙️ Configurações)

O bot manda mensagem sozinho no grupo quando algo acontece — cada um pode
ser ligado/desligado individualmente em `/menu` → **⚙️ Configurações**:

- 🔴 Bot parou sozinho
- ⏸️ Bot pausou (com o motivo)
- 🎁 Item raro na hora
- 💀 Morte em tempo real
- 📈 Resumo diário automático
- ⚠️ Conta travada
- 🏆 Recorde pessoal
- 🧪 Compra de poções concluída
- 🎉 Subiu de nível
- 🐲 Toda derrota do Dragão de Frost
- 🍀 Elixir/Tônico expirou
- 📦 Estoque baixo de Poção de Vida
- 🔑 Chaves de Masmorra acabando
- 👑 VIP vencendo em breve
- ⚡ Energia cheia

Também dá pra configurar um **🌙 Modo silencioso** (uma janela de horário em
que nenhum aviso é mandado na hora — tudo é acumulado e sai num resumo só
quando a janela termina), na mesma tela de Configurações.

## Perguntas comuns

**Preciso deixar o controle rodando o tempo todo?**
Só se quiser receber avisos automáticos e poder usar `/menu` a qualquer
hora. Sem ele rodando, o bot que joga continua funcionando normalmente —
você só perde o controle/avisos pelo Telegram (o painel gráfico continua
funcionando igual).

**O token do bot é a mesma coisa que a senha da minha conta do Telegram?**
Não — é só um código de acesso ao BOT que você criou no BotFather, nada a
ver com sua conta pessoal. Mesmo assim, trate como senha: não mande esse
token pra ninguém, nem cole em grupos públicos.

**Posso usar o mesmo bot de controle em mais de uma pasta/instância do
TofuBot?**
Não ao mesmo tempo — cada bot do Telegram só pode ter UM processo
"escutando" ele por vez. Se você rodar o TofuBot em pastas diferentes,
crie um bot separado (outro `/newbot`) pra cada uma.
