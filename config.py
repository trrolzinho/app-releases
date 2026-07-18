# =====================================================================
#  config.py  —  lê as configurações do settings.json (editado pelo app
#  painel). Se o settings.json não existir, usa os valores padrão abaixo.
#  Normalmente você NÃO edita este arquivo — use o painel (painel.cmd).
# =====================================================================

import json
import os
import shutil
import sys


def _app_dir():
    """Pasta do programa: ao lado do .exe (quando empacotado) ou do .py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def session_id(phone):
    """Nome do arquivo de sessão a partir do TELEFONE (só dígitos). Assim a
    MESMA conta compartilha o login entre Masmorra e Caçada Dupla."""
    return "".join(c for c in (phone or "") if c.isdigit()) or "conta"


def session_path(app_dir, phone, name=None):
    """Caminho da sessão, SEMPRE por TELEFONE (só dígitos). Cada telefone tem o
    seu próprio arquivo de sessão — então TROCAR o telefone de uma conta exige
    login de verdade (pede o código), sem reaproveitar a sessão de outro número.
    ('name' é ignorado; fica só por compatibilidade com chamadas antigas.)"""
    return os.path.join(app_dir, session_id(phone))

# --- Versão e atualização automática ----------------------------------
# VERSION: versão DESTE build. O botão "Verificar atualização" no painel
# compara com a versão publicada no GitHub Releases e avisa se há uma mais nova.
# BUMP MANUAL: subir este número a cada release (ex: 1.0.0 -> 1.0.1).
VERSION = "1.4.0"
# UPDATE_REPO: repositório do GitHub no formato "usuario/repositorio" onde ficam
# os Releases (com o zip de atualização anexado). Vazio = botão desativado.
UPDATE_REPO = "trrolzinho/app-releases"
# UPDATE_ASSET: nome (ou parte dele) do arquivo .zip anexado no Release que deve
# ser baixado. Serve pra usar um nome PÚBLICO neutro (sem ligação com o jogo).
# Vazio = pega o 1º .zip do Release. O painel salva localmente sempre com o nome
# que o atualizar.cmd espera, então o nome público pode ser qualquer um.
# Nome público neutro escolhido: arquivo "app-vX.Y.Z.zip" (repo "app-releases").
UPDATE_ASSET = "app"

# --- valores PADRÃO (usados se não houver settings.json) --------------
API_ID = 0
API_HASH = ""
BOT_USERNAME = ""
SALA_SENHA = "1234"
ACCOUNTS = [
    {"name": "tank",     "phone": "", "role": "tank",     "char_name": "", "host": True},
    {"name": "suporte",  "phone": "", "role": "suporte",  "char_name": "", "host": False},
    {"name": "magodps",  "phone": "", "role": "dps",      "char_name": "", "host": False},
    {"name": "arqueiro", "phone": "", "role": "arqueiro", "char_name": "", "host": False},
]

# --- Catálogo de TODAS as almas conhecidas por papel: (nome, recarga_em_turnos)
# Fixas (constantes do jogo) — o painel não mexe na lista, só deixa marcar
# quais delas cada CONTA realmente usa (settings.json -> ACCOUNTS[i]["souls"]).
# ORDEM = prioridade de uso quando mais de uma estiver pronta na mesma rodada.
# Pro tank, "Rugido do Rochedo" fica sempre em 1º: é o "provoca" que segura o
# aggro no tank — prioridade não é por recarga aqui, é por função.
SOULS_CATALOG = {
    "tank":      [("Rugido do Rochedo", 4), ("Escudo de Ossos", 5)],
    "suporte":   [("Escudo Arcano", 3), ("Vontade do Lich", 5), ("Orbe Solar", 5)],
    "dps":       [("Tempestade de Areia", 6), ("Poder do Lich", 5), ("Maldição da Bruxa", 4)],
    "arqueiro":  [("Flecha do Djinn", 7), ("Precisão Élfica", 6), ("Picada da Aranha", 4)],
    "lanceiro":  [("Lança Solar", 6), ("Lança do Guardião", 4), ("Lança dos Ventos", 3)],
    "berserker": [("Fúria do Titã", 9), ("Golpe Sombrio", 4), ("Fúria do Lobo", 3)],
}

# Almas que cada papel usava ANTES de existir a seleção por conta — servem só
# de valor padrão pra contas salvas num settings.json antigo (sem "souls") ou
# pra pré-marcar os checkboxes de uma conta nova, sem mudar comportamento de
# quem já estava configurado.
DEFAULT_SOULS = {
    "tank":     [("Rugido do Rochedo", 4)],
    "suporte":  [("Escudo Arcano", 3), ("Vontade do Lich", 5)],
    "dps":      [("Tempestade de Areia", 6), ("Maldição da Bruxa", 4)],
    "arqueiro": [("Flecha do Djinn", 7), ("Picada da Aranha", 4)],
    "lanceiro":  [],
    "berserker": [],
}



# --- Templo do Oásis (Duo, nível mínimo 40) -----------------------------
# Conteúdo dentro do mapa do Oásis: no Oásis, o botão "Masmorra" do menu leva
# à Fenda Solar (em vez da tela normal de Criar Sala/Buscar Salas) — de lá se
# escolhe "Templo do Oásis (Grupo)" pra chegar na sala. MESMA sala/combate da
# Masmorra normal (Criar Sala, Pronto, Iniciar, Atacar/Defender/Consumíveis/
# Almas), só que travado em EXATAMENTE 2 contas. NUNCA roda junto com
# Masmorra/Caçada em Dupla/Cripta/Caçada Solo/Missão Oásis (pedido do
# usuário — só um conteúdo por vez).
#
# MAPA_TEMPLO_OASIS: nome do mapa (tela de Viajar) onde fica a Fenda Solar.
MAPA_TEMPLO_OASIS = "Oásis Perdido"
#
# "grupos": lista de DUPLAS — cada item é uma lista com EXATAMENTE 2 contas.
# Cada grupo roda numa sala/Templo PRÓPRIO, em paralelo com os outros grupos
# (mesma ideia do CACA_DUPLA["grupos"] acima).
TEMPLO_OASIS = {
    "max_execucoes": 0,       # quantas vezes CADA DUPLA completa o Templo e então PARA (0 = sem limite)
    "pocao_vida_minima": 50,  # se, AO BEBER no Templo, o estoque ficar abaixo disso, sai
    "pocao_vida_aviso": 100,  # ANTES de iniciar: se o estoque estiver abaixo disso, avisa e pausa
    "vida_min_pct": 40,       # HP% padrão pra beber poção (cada conta pode sobrescrever o seu, na aba)
    "grupos": [
        [
            {"name": "duo1", "phone": "", "role": "tank", "char_name": "", "host": True},
            {"name": "duo2", "phone": "", "role": "dps", "char_name": "", "host": False},
        ],
    ],
}


def resolve_souls(role, selected_names):
    """Monta a lista (nome, recarga) de uma CONTA. A ORDEM da lista é a
    PRIORIDADE de uso — e agora respeita a ORDEM escolhida pelo usuário no
    painel (1ª alma, 2ª alma), não a ordem fixa do catálogo.
    'selected_names' vem de ACCOUNTS[i]['souls'] (lista ORDENADA):
    - None (save antigo, sem 'souls') -> usa DEFAULT_SOULS do papel.
    - [] ou lista -> usa exatamente essas, NA ORDEM dada (desmarcar tudo = só ataca).
    Só entram almas que existem no catálogo do papel (ignora nome inválido)."""
    cd_por_nome = dict(SOULS_CATALOG.get(role, []))   # nome -> recarga
    if selected_names is None:
        selected_names = [n for n, _ in DEFAULT_SOULS.get(role, [])]
    out = []
    for n in selected_names:
        if n in cd_por_nome and all(n != j for j, _ in out):
            out.append((n, cd_por_nome[n]))
    return out

# --- Regras de cura (fração do HP máximo) — podem vir do settings.json --
TANK_HEAL_RATIO = 0.40
# TANK_CRITICAL_RATIO: NÃO é mais usado no combate do tank (que agora cura
# IMEDIATAMENTE assim que o HP cai abaixo de TANK_HEAL_RATIO, sem esperar o
# Rugido — ver _act_tank). Deixado aqui só pra não quebrar settings.json
# antigos que ainda tenham essa chave salva.
TANK_CRITICAL_RATIO = 0.15
OTHER_HEAL_RATIO = 0.40
BETWEEN_DG_HEAL_RATIO = 0.85

# --- Janela de HP% pro Rugido do Rochedo (combo Rugido -> Escudo) ---------
# O Rugido do Rochedo (aggro) só é USADO se o HP do tank estiver DENTRO
# dessa faixa — fora dela (muito baixo OU muito alto), ele nem tenta. O
# Escudo de Ossos (cura) continua liberado só na rodada IMEDIATAMENTE
# seguinte a um Rugido confirmado (ver Brain.prioridade_tank no hunter.py),
# sem faixa própria — ele "herda" a oportunidade do Rugido.
TANK_RUGIDO_HP_MIN = 40   # % — abaixo disso, não usa Rugido (perigoso demais,
                          #     melhor focar em curar)
TANK_RUGIDO_HP_MAX = 90   # % — acima disso, não usa Rugido (HP já está bem,
                          #     sem necessidade de reforçar aggro agora)

# --- Tempos -----------------------------------------------------------
# ACTION_DELAY = pausa fixa depois de cada clique antes de checar a tela;
# POLL_INTERVAL = de quanto em quanto re-checa se a tela mudou. wait_change já
# faz uma checagem IMEDIATA (sem dormir), então esses tempos podem ser baixos —
# quanto menores, mais ágil é abrir menu/clicar (Almas, Consumíveis, poção...).
ACTION_DELAY = 0.15
UPDATE_TIMEOUT = 12.0
# ACTION_CONFIRM = espera CURTA usada só pro clique da AÇÃO de combate na caçada
# (Atacar/Defender/alma/poção). Depois de agir, a tela da caçada só muda quando
# a RODADA resolve — não faz sentido o bot travar até UPDATE_TIMEOUT (12s)
# esperando isso; o loop de combate já confirma a ação pela ampulheta. Então o
# clique da ação envia e volta rápido, e o bot fica livre pra reagir.
ACTION_CONFIRM = 1.0
# ROUND_TIMEOUT_CACA: quanto tempo esperar a AMPULHETA sumir de vez (rodada
# resolver de verdade) na Caçada em Dupla, antes de desistir e agir de novo.
# Confirmado por print do jogo: a rodada tem um cronômetro de "Turno: 45s" —
# ou seja, ela NÃO resolve na hora que você age, só quando esse tempo fecha
# (deixa espaço pros outros jogadores/monstros agirem). Com um valor MENOR
# que isso (era só ~12s antes), o bot desistia cedo, voltava e agia nas MESMAS
# rodada de novo à toa (gastando alma/clique sem necessidade) — por isso o
# valor aqui é maior que os 45s reais, com folga de segurança.
ROUND_TIMEOUT_CACA = 55.0
# RETRY_ACAO_APOS_CACA: se passar esse tempo SEM NENHUMA mudança na tela depois
# de agir (nem a minha ampulheta sumir, nem evento/HP novo), o clique pode ter
# se perdido (falha silenciosa do Telegram/callback do botão) — o bot tenta
# AGIR DE NOVO em vez de só ficar esperando parado até estourar o tempo todo.
# Confirmado manualmente pelo usuário: no jogo normal a rodada sempre fecha
# rápido (~4s) quando os 2 jogadores agem — então uma trava de dezenas de
# segundos é sinal de clique perdido, não demora normal do jogo.
# Nota (log real 2026-07-11): com a API do Telegram bem lenta (get_messages
# levando 8-18s+ nessa sessão), 4s pode disparar o reforço logo depois da 1ª
# leitura de tela, arriscando um duplo clique. Em vez de aumentar esse
# tempo (o que deixaria o reforço lento até em conexões rápidas), o limite
# de SEGURANÇA agora é a QUANTIDADE de tentativas (MAX_TENTATIVAS_ACAO,
# abaixo) — pedido explícito do usuário.
RETRY_ACAO_APOS_CACA = 4.0
# MAX_TENTATIVAS_ACAO: quantas vezes, NO MÁXIMO, o bot reforça o clique numa
# mesma rodada antes de desistir de tentar de novo e só esperar o resto do
# ROUND_TIMEOUT_CACA normalmente (sem mais nenhum clique extra). Evita
# reforçar clique infinitamente numa conexão lenta (risco de duplo-clique
# repetido e dessincronia com o jogo).
MAX_TENTATIVAS_ACAO = 10
# POLL_INTERVAL: de quanto em quanto o bot consulta a API (get_messages) pra ver
# se a tela mudou. MUITO baixo = chama rápido demais e o Telegram limita
# (FLOOD_WAIT: manda o Telethon DORMIR X segundos) — foi o que travou tudo com
# 0.2s. O valor precisa crescer conforme mais contas rodam AO MESMO TEMPO (cada
# uma consulta a tela nesse intervalo, então N contas = N vezes mais chamadas
# por segundo): calibrado a 0.8s pra 2 contas, subiu pra 1.5s com 4 (2 Caçadas
# em Dupla juntas), e agora 2.0s pensando em até ~5-6 contas (Caçada Solo com
# várias contas independentes) — visto na prática (get_messages levando
# 10-18s com 1.5s e 5 contas). Se usar MENOS contas ao mesmo tempo, pode
# baixar de novo (ex: 1.0-1.2s pra 2-3 contas).
POLL_INTERVAL = 2.0
# POLL_JITTER: variação ALEATÓRIA (± segundos) somada ao POLL_INTERVAL a cada
# consulta (ver poll_sleep() no hunter.py). SEM isso, com várias contas rodando
# em paralelo, todas dormem o MESMO tempo fixo e acordam no MESMO instante —
# gerando um PICO de N chamadas simultâneas à API do Telegram. É esse PICO
# (não a média de chamadas/segundo) que dispara FloodWait com frequência,
# mesmo com POLL_INTERVAL "calibrado" pro nº de contas (visto na prática:
# 4-5 contas, com POLL_INTERVAL=2.0s, tomando FloodWait direto — cada
# get_messages() aparecendo como "demorou 19.5s" no log, porque o Telethon
# dorme o FloodWait por dentro, sem lançar erro — ver flood_sleep_threshold
# na criação do TelegramClient). Com o jitter, cada conta "desalinha" da
# hora das outras, espalhando as chamadas ao longo do tempo.
POLL_JITTER = 0.6
LOBBY_TIMEOUT = 180.0
MAX_ROUNDS = 500
TONICO_INTERVALO = 600   # segundos entre usos do Super Tônico (recarga ~10 min)
ELIXIR_INTERVALO = 1800  # segundos entre usos do Super Elixir de Sabedoria (dura ~30 min)

# STATUS_AO_VIVO_ATIVO: liga/desliga a gravação do status.json (o painel de
# "Status ao vivo" — HP, andar, cronômetro). Desligado = write_status() nem
# roda, ZERO leitura/escrita de disco por essa via (uma via a menos de
# lentidão se o painel estiver rodando com MUITAS contas ao mesmo tempo). O
# painel simplesmente some essa seção quando desligado (ver painel.py).
# Padrão True = mantém o comportamento de sempre; mude pra False só se quiser
# abrir mão do HP/andar ao vivo em troca de um pouco mais de performance.
STATUS_AO_VIVO_ATIVO = True

# LIMPEZA_PROFUNDA_ATIVO: liga/desliga a limpeza PROFUNDA do histórico de
# conversa (apaga TODAS as mensagens antigas no Telegram, não só as 100 mais
# recentes) num início de verdade (não roda em reinício automático). É só
# cosmético (deixa a conversa "limpa" no app do Telegram) — mas como apaga
# TUDO, não sobra nenhuma mensagem com botão pra clicar, e por isso força um
# /start logo em seguida pra fazer o bot mostrar o menu de novo. Desligando
# essa flag, a conta pula a limpeza inteira e também esse /start forçado (o
# menu antigo continua na tela pra clicar direto). Padrão False = pula a
# limpeza (pedido do usuário 2026-07-16); mude pra True se quiser a conversa
# sempre limpinha no início de cada sessão de verdade.
LIMPEZA_PROFUNDA_ATIVO = False

# --- Pausa automática de manutenção (o jogo às vezes fica fora do ar num
# horário fixo) -------------------------------------------------------------
# MANUTENCAO_ATIVA: liga/desliga essa pausa (False = nunca pausa por isso).
# MANUTENCAO_INICIO/FIM: horário LOCAL "HH:MM" (24h). Se o fim for MENOR que
# o início (ex: "23:30"/"00:30"), entende que a janela passa da meia-noite.
# Enquanto estiver dentro da janela, o bot só ESPERA (não clica em nada, não
# desconecta, não sai de sala) — assim que a janela passa, volta a agir
# sozinho, sem precisar clicar Iniciar de novo.
MANUTENCAO_ATIVA = False
MANUTENCAO_INICIO = "05:00"
MANUTENCAO_FIM = "06:00"

# Quantas execuções entram na média rolante (tempo e XP por execução, usada
# nas estimativas de "quanto falta"/"tempo até o próximo nível"). Maior =
# estimativa mais estável mas reage mais devagar a mudanças (trocar de
# masmorra, subir de nível); menor = reage rápido mas oscila mais. Não pesa
# em performance (é só uma lista de números).
MEDIA_JANELA = 10

# --- Mercado: venda automática de itens do inventário ---------------------
# Pedido do usuário (2026-07-15): lista os itens já vistos dropando (banco
# que cresce sozinho, ver hunter.py -> _registrar_itens_no_banco), marca
# quais vender, e o bot vende sozinho de tempos em tempos.
MERCADO_ATIVO = False
MERCADO_INTERVALO_MIN = 30   # minutos entre ciclos de venda
# Níveis de reforço (+0/+1/+2/+3) que o bot deve vender — SÓ vale pra
# equipamento e alma (únicos que têm reforço); outros itens (poção, tônico,
# chave, minério, flor...) não têm reforço, então essa lista não afeta eles
# — esses são vendidos sempre que o nome estiver marcado, sem depender
# disso aqui.
MERCADO_REFORCOS = [0, 1, 2, 3]
MERCADO_ITENS = []    # nomes de itens (do banco_itens) marcados pra vender
MERCADO_CONTAS = []   # telefones das contas que participam da venda
# Nem todo mapa tem mercador (confirmado pelo usuário: Oásis Perdido/Vale das
# Miragens não tem) — se a conta estiver num mapa sem mercador na hora de
# vender, ela viaja pra este aqui, vende, e volta pro mapa original depois.
MERCADO_MAPA_VENDA = "Floresta Sombria"
# Pausa (segundos) entre marcar os itens e CONFIRMAR a venda de verdade
# ("Sim, vender tudo") — pedido do usuário 2026-07-16, testando a seleção:
# dá tempo de ver no jogo/print se marcou os itens certos e, se algo tiver
# marcado errado (ex.: item caro que não devia), clicar em "Parar" no painel
# a tempo — o "Parar" mata o processo na hora, então travar aqui esses
# segundos é o suficiente pra abortar antes da confirmação final.
MERCADO_DELAY_CONFIRMACAO_SEG = 10.0
# Mapas SEM mercador (confirmado pelo usuário: Oásis Perdido/Vale das
# Miragens) — se a conta estiver em um destes, o bot nem tenta clicar em
# "Loja" aqui, já viaja direto pro MERCADO_MAPA_VENDA (mais rápido).
MERCADO_MAPAS_SEM_MERCADOR = ["Oásis Perdido"]

# --- Abas do painel escondidas (pedido do usuário 2026-07-15: "vou passar o
# programa pra um colega") --------------------------------------------------
# Some com abas inteiras do painel (a função continua funcionando por baixo
# dos panos se já estiver configurada — só o ATALHO visual some). Valores
# aceitos na lista: "mercado", "observador". Lista vazia = mostra tudo (padrão).
# Exemplo pra esconder as duas antes de repassar pra alguém:
#   PAINEL_ABAS_OCULTAS = []
PAINEL_ABAS_OCULTAS = []

# Quantas masmorras fazer e então PARAR (0 = sem limite, roda pra sempre).
MAX_DUNGEONS = 0

# Poções de Vida — MASMORRA normal (Templo do Oásis/Cripta/Caçada Dupla já
# tinham o seu próprio; a Masmorra normal usava um valor fixo no código,
# sem aviso antes de começar — agora configurável igual aos outros).
# MASMORRA_POCAO_VIDA_MINIMA: se o estoque cair abaixo disso DURANTE o
# ciclo (entre uma masmorra e outra), o bot pausa e avisa.
# MASMORRA_POCAO_VIDA_AVISO: antes de COMEÇAR, se o estoque já estiver
# abaixo disso, avisa com pop-up e pausa (pra reabastecer antes de rodar).
MASMORRA_POCAO_VIDA_MINIMA = 50
MASMORRA_POCAO_VIDA_AVISO = 100

# Mapa/zona pra onde VIAJAR antes de começar (Menu -> Viajar). "" = não trocar
# (fica no mapa atual). Vale pra masmorra E caçada — todas as contas vão pro
# mesmo mapa. Nomes conhecidos em MAPAS_CONHECIDOS (o painel oferece a lista).
MAPA_DESTINO = ""

# --- MASMORRAS ALTERNATIVAS (Covil de Zul'gor, Santuário de Altheryn, etc) --
# Cada uma é só uma ESCOLHA DE SALA (qual tipo de masmorra criar) — o combate
# em si usa a MESMA lógica adaptativa de sempre (HP/poção/alma automáticos),
# igual à Masmorra Normal. Pra ADICIONAR UMA MASMORRA NOVA no futuro, só
# somar uma entrada nova aqui — não precisa mexer no resto do código:
#   rotulo: nome que aparece no combobox de mapa do painel
#   botao:  texto do botão na tela de escolha de sala (o find_button já
#           ignora maiúsculas/acento)
#   mapa:   mapa onde essa masmorra existe (a conta viaja pra cá antes)
#   skin:   nome da skin (sem "Equipar"/"(F)"/"(M)") que tem que estar
#           EQUIPADA antes de entrar — o bot troca sozinha se precisar.
#           None = não exige skin nenhuma.
#   skin_por_raca: True = a skin exigida MUDA por RAÇA do personagem — o
#           nome final é "<skin> (<Raça>)", resolvido por conta a partir do
#           papel (ver RACA_POR_PAPEL). False/ausente = skin igual pra todo
#           mundo (nome exato, sem variação).
#   skin_unica: nome de uma SEGUNDA skin (fixa, sem variação por raça) que
#           UMA ÚNICA conta do grupo usa, enquanto o resto usa a 'skin'
#           principal (ex: 4 contas com uma skin + 1 com outra). O bot
#           sempre escolhe a MESMA posição (a 1ª conta da lista) pra ficar
#           com essa skin única — não importa QUAL conta é, contanto que a
#           mistura final bata (ausente = não há skin única, só a principal).
MASMORRAS_ALTERNATIVAS = {
    "zulgor": {
        "rotulo": "Zuzu",
        "botao": "covil de zul'gor",
        "mapa": "Planície",
        "skin": "pele de goblin",
    },
    "viadin": {
        "rotulo": "Masmorra do Viadin",
        "botao": "santuário de altheryn",
        "mapa": "Floresta Sombria",
        "skin": "culpa de altheryn",
    },
    "hidra": {
        "rotulo": "Hidra Ancestral",
        # SEM tela de escolha de sala — é a MESMA Masmorra do Pântano de
        # sempre ("Criar sala"/"Buscar salas" direto); o jogo detecta
        # sozinho, DENTRO da sala, que todo mundo está com a skin certa e
        # vira "Masmorra da Hidra Ancestral". Por isso 'botao' é None.
        "botao": None,
        "mapa": "Pântano",
        "skin": "hydra slayer",
        "skin_por_raca": True,
    },
    "ossos": {
        "rotulo": "Covil da Hidra de Ossos",
        # Mesmo estilo da Hidra Ancestral (mesmo mapa, sem tela de escolha),
        # mas com DUAS skins: 4 contas de "Cavaleiro das Sombras" (por raça,
        # igual à Hidra) + 1 conta sozinha de "Osíris" (fixa).
        "botao": None,
        "mapa": "Pântano",
        "skin": "cavaleiro das sombras",
        "skin_por_raca": True,
        "skin_unica": "osíris",
    },
}

# Papel -> RAÇA do personagem (Lanceiro e Arqueiro são a MESMA raça no jogo,
# só com equipamento diferente — por isso caem na mesma variante de skin).
# Usado só quando uma masmorra alternativa tem "skin_por_raca": True (ver
# MASMORRAS_ALTERNATIVAS -> "hidra").
RACA_POR_PAPEL = {
    "tank": "Guerreiro",
    "berserker": "Guerreiro",
    "dps": "Mago",
    "suporte": "Mago",
    "arqueiro": "Arqueiro",
    "lanceiro": "Arqueiro",
}

# "normal" = Masmorra de sempre (sem escolha especial de sala). Qualquer
# outra chave precisa existir em MASMORRAS_ALTERNATIVAS acima.
TIPO_MASMORRA = "normal"

# --- Raridade dos EQUIPAMENTOS (pro Relatório colorir o loot) -------------
# O texto do jogo NÃO indica a raridade de forma padronizada (as estrelinhas
# "✦" só aparecem no Minério, não é um padrão geral) — então aqui é um
# catálogo MANUAL: nome exato do item (como aparece no drop, SEM as
# estrelinhas/símbolos extras) -> raridade. Vá adicionando conforme forem
# aparecendo itens novos nos drops.
#
# Raridades válidas (com a cor que o painel usa): "normal" (verde),
# "incomum" (azul), "raro" (roxo), "epico" (amarelo), "lendario" (laranja).
#
# Itens que NÃO estiverem aqui são tratados como CONSUMÍVEL/RECURSO (poção,
# tônico, chave, minério, etc.) — só entram na caixa de equipamento colorido
# se estiverem cadastrados aqui.
#
# Exemplo de como preencher (descomente e ajuste conforme for descobrindo):
# ITENS_RARIDADE = {
#     "Espada do Dragão": "raro",
#     "Elmo Sombrio": "epico",
#     "Anel do Vazio": "lendario",
# }
ITENS_RARIDADE = {}

MAPAS_CONHECIDOS = ["Planície", "Floresta Sombria", "Pântano", "Cemitério Antigo",
                    "Deserto Escaldante", "Oásis Perdido", "Montanhas Gélidas", "Abismo"]
# A Caçada em Dupla SÓ funciona neste mapa — o bot viaja pra cá sozinho quando o
# modo é caçada (o campo MAPA_DESTINO acima vale só pra masmorra).
CACA_MAPA = "Montanhas Gélidas"

# --- CRIPTA (3º conteúdo: "Cripta do Cemitério", no mapa Cemitério Antigo) ----
# Trazido da versão do colega. Masmorra INFINITA (sem fim) — o bot para no
# 'andar_maximo'. Sala SEM senha. Custo: 1 "Chave de Ossos" por conta. Nº de
# contas CONFIGURÁVEL (2 a 5, escolhidas no painel). Anti-intruso: se entrar
# na sala um personagem que NÃO é das contas configuradas, sai e recria a sala.
# 'nivel' = qual Cripta jogar: "I" (Lv 22-27), "II" (28-35) ou "III" (35-50).
CRIPTA = {
    "andar_maximo": 10,      # para ao alcançar este andar (conteúdo é infinito)
    "alma_min_andar": 0,     # SÓ usa alma a partir deste andar (0=sempre). Andares
                             #   fáceis (HP fica 100%) só atacam -> mais rápido.
    "nivel": "I",            # "I" | "II" | "III"
    "max_criptas": 0,        # quantas criptas fazer e então PARAR (0 = sem limite)
    "contas": [],            # contas escolhidas p/ a Cripta (2 a 5) — vêm do painel
}

# --- POÇÕES da CRIPTA (config própria, independente da Caçada em Dupla) ----
# A Caçada em Dupla mantém os PRÓPRIOS campos de poção dentro de CACA_DUPLA
# (não mexemos nisso). Este bloco aqui é só pra CRIPTA. A Masmorra não usa
# isto (ela cura por papel — TANK/OTHER_HEAL_RATIO).
#   vida_min_pct: HP% p/ beber Poção de Vida em combate (0=nunca). Valor
#     PADRÃO — cada CONTA pode ter o seu (CRIPTA["contas"][i]["vida_min_pct"]).
#   reforco_pct:  "reforço" no INÍCIO: bebe 1 Poção de Vida se entrar abaixo
#     desse % (0=desligado).
#   pocao_vida_minima: se, AO BEBER, o estoque ficar abaixo disso -> sai.
#   pocao_vida_aviso:  ANTES de iniciar, se o estoque < isso -> avisa e pausa.
POCOES = {
    "vida_min_pct": 40,
    "reforco_pct": 0,
    "pocao_vida_minima": 10,
    "pocao_vida_aviso": 100,
}

# --- CAÇADA SOLO (cada conta caça sozinha, em paralelo, sem sala/parceiro) --
# Custa 1 de Energia por tentativa ("Caçar"). Ao entrar, pode dar: combate
# (resolve na hora, clique a clique, sem ampulheta), armadilha (dano direto),
# evento de sorte (só clica "Caçar de novo"), ou um NPC/mercador (ver abaixo).
CACA_SOLO = {
    "mapa": "",              # em qual mapa caçar (viaja pra lá antes de começar).
                             # "Floresta Profunda" é uma opção de mapa que na
                             # verdade é a sub-área de Floresta Sombria — vira
                             # 'viaja pra Floresta Sombria + escolhe Profunda'.
    "energia_minima": 5,     # abaixo disso, para de caçar e reabastece energia
    "energia_alvo": 35,      # reabastece (poção de energia) até este nível
    "vida_min_pct": 40,      # bebe Poção de Vida quando o HP cair abaixo disso
                             # (cada CONTA pode ter o seu — contas[i]["vida_min_pct"])
                             # HP% POR MONSTRO é por CONTA também — cada personagem
                             # tem defesa diferente, o mesmo bicho bate diferente
                             # em cada um. Fica em contas[i]["hp_por_mob"] = {nome
                             # do monstro: %}. Ex: {"Grimmrok, o Eterno Inverno": 60}
                             # pra ESSA conta curar mais cedo contra o chefe.
    "tonico_deserto": "",    # Mercador do Deserto oferece tônico: "" = ignora,
                             # "atk"/"def"/"crit" = compra esse automaticamente
    "max_cacadas": 0,        # quantas tentativas de caçar fazer e então PARAR (0=sem limite)
                             # "SÓ OS BOSSES" no Deserto Escaldante é POR CONTA
                             # (igual hp_por_mob): contas[i]["so_bosses_deserto"]
                             # = True/False. Só aparece/faz efeito quando o mapa
                             # DESSA conta é "Deserto Escaldante" — nos outros
                             # mapas, luta normal com tudo. Ver BOSSES_DESERTO_
                             # ESCALDANTE logo abaixo pra saber quais 3 bosses são.
                             # "ALVO ÚNICO" no Oásis Perdido é POR CONTA também:
                             # contas[i]["alvo_oasis"] = "" (luta com tudo) ou um
                             # dos nomes de MOBS_OASIS_PERDIDO (só luta com ESSE,
                             # foge do resto). Só aparece/faz efeito quando o mapa
                             # dessa conta é "Oásis Perdido".
                             # "FUGIR DO BOSS" na Floresta Profunda é POR CONTA
                             # também: contas[i]["fugir_boss_floresta"] = True/
                             # False. Só aparece/faz efeito quando o mapa dessa
                             # conta é "Floresta Profunda" — ver BOSS_FLORESTA_
                             # PROFUNDA/MOBS_FLORESTA_PROFUNDA logo abaixo.
    "contas": [],            # contas que caçam sozinhas (cada uma independente)
}

# Bosses do Deserto Escaldante (raros, dropam item) — usados pelo filtro
# "Só os bosses" da Caçada Solo (contas[i]["so_bosses_deserto"] = True): com
# ele ligado, a conta só luta contra estes 3 e FOGE de qualquer outro monstro
# do mapa (que não dropa nada de interessante, só faz perder HP/tempo à toa).
# Nome tem que bater com o que aparece no jogo (comparação ignora maiúscula/
# acento/espaço, ver norm() em hunter.py — mas o texto em si precisa ser o
# nome certo do monstro).
BOSSES_DESERTO_ESCALDANTE = [
    "Neith, Arqueira Eterna",
    "Thoth, Arcano Solar",
    "Seth, Guardião do Deserto",
]

# Monstros comuns do Oásis Perdido — usados pelo filtro "Alvo único" da Caçada
# Solo (contas[i]["alvo_oasis"] = um destes nomes): com um escolhido, a conta
# só luta contra ELE e FOGE de qualquer outro monstro do mapa (volta a
# "Caçar de novo" até esse aparecer). Diferente do Deserto Escaldante (3
# bosses fixos, liga/desliga) — aqui é 1 escolhido entre os 5, comum a todos.
MOBS_OASIS_PERDIDO = [
    "Cobra do Deserto",
    "Abutre de Fogo",
    "Karkto Feroz",
    "Lince Saqueadora",
    "Lagarto da Areia",
]

# Item que a busca do Sunred DÁ DE RECOMPENSA ao completar -> monstro que
# precisa ser caçado pra essa busca (é o que dropa o MATERIAL de coleta,
# como "Flor do Karkto Feroz" — mas o jogador escolhe pela RECOMPENSA final,
# que é o que ele sabe de cabeça: "quero as Grevas das Dunas"). O painel
# mostra a recompensa, o bot por trás ainda trabalha com o nome do monstro
# (é o que aparece na tela de combate/vitória pra decidir foge/luta — ver
# act_combate_solo). Nomes exatos conferidos nas telas 'Busca aceita!' do
# Sunred (campo 'Recompensa: ...').
ITENS_MISSAO_OASIS = {
    "Arco da Tempestade Solar": "Cobra do Deserto",
    "Cajado das Dunas Antigas": "Abutre de Fogo",
    "Grevas das Dunas": "Karkto Feroz",
    "Talismã do Sol Fóssil": "Lince Saqueadora",
    "Khopesh do Sol Partido": "Lagarto da Areia",
}

# --- MISSÃO OÁSIS (busca aleatória do Sunred, no Oásis Perdido) -------
# O NPC Sunred oferece uma busca ALEATÓRIA: matar 50x de UM dos monstros do
# Oásis (o jogo escolhe qual, não dá pra saber antes de aceitar) + 200 no
# total (contando os 50). O bot verifica DEPOIS de aceitar se bateu com o
# monstro escolhido aqui — se não bateu, desiste e tenta de novo. Cada CONTA
# tem o seu próprio monstro-alvo (contas[i]["monstro_alvo"], um dos nomes de
# MOBS_OASIS_PERDIDO) — são contas SEPARADAS das da Caçada Solo (aba própria
# no painel), mesmo rodando no mesmo mapa.
MISSAO_OASIS = {
    "energia_minima": 5,      # abaixo disso, para e reabastece energia
    "energia_alvo": 35,       # reabastece (poção de energia) até este nível
    "vida_min_pct": 40,       # bebe Poção de Vida quando o HP cair abaixo disso
                              # (cada CONTA pode ter o seu — contas[i]["vida_min_pct"])
                              # "fazer_nurmora" é SÓ por conta (contas[i]
                              # ["fazer_nurmora"] = True/False) — também aceita/
                              # entrega a quest da Nurmora (Martelo Mágico)
                              # enquanto essa conta procura o Sunred.
    "max_missoes": 0,         # quantas missões (busca completa) fazer e então
                              # PARAR (0 = sem limite)
    "contas": [],             # contas escolhidas p/ essa aba (cada uma independente,
                              # cada uma com seu monstro_alvo)
}

# Modo Observador: NÃO clica em nada, só lê a tela e captura XP/Gold/Loot pro
# Relatório normal (soma em 'diario' igual qualquer outro conteúdo) — pra
# quem prefere jogar na mão mas ainda quer as estatísticas do TofuBot.
OBSERVADOR = {
    "contas": [],   # contas a observar (cada telefone precisa já estar
                     # configurado/logado na aba Configuração)
}

# Monstros conhecidos de Montanhas Gélidas (pro painel oferecer um HP% por
# monstro nessa aba). Só é usado se o mapa escolhido for "Montanhas Gélidas".
MOBS_MONTANHAS_GELIDAS = [
    "Serpente de Cristal", "Urso Glacial", "Aranha de Cristal", "Lince Sombrio",
    "Grimmrok, o Eterno Inverno", "Troll das Fendas", "Caçador Polar",
    "Corvo de Tempestade", "Espectro das Neves",
]

# Monstros conhecidos da FLORESTA PROFUNDA (sub-área de Floresta Sombria —
# ver comentário em CACA_SOLO["mapa"] acima) — pro painel oferecer um HP%
# por monstro nessa aba, igual já existe pra Montanhas Gélidas. Só é usado
# se o mapa escolhido pela conta for "Floresta Profunda". Conferidos nas
# telas 'COMBATE INICIADO' do usuário (2026-07-17). O Boss (bem mais forte,
# ver BOSS_FLORESTA_PROFUNDA abaixo) fica de FORA desta lista — ele tem o
# próprio filtro de "fugir do boss", não HP% por monstro.
MOBS_FLORESTA_PROFUNDA = [
    "Guerreiro Goblin Corrompido", "Berserker Goblin Corrompido",
    "Explorador Goblin Corrompido", "Capitão Goblin Corrompido",
    "Feiticeiro Goblin Corrompido", "Xamã Goblin Corrompido",
]

# Boss da Floresta Profunda — aparece raro, MUITO mais forte que os goblins
# comuns do mapa (1800 HP contra 260-450 dos outros, print do usuário
# 2026-07-17). Usado pelo filtro "Fugir do Boss" da Caçada Solo
# (contas[i]["fugir_boss_floresta"] = True): com ele ligado, a conta foge
# SÓ deste boss (nome tem que bater com o que aparece no jogo — comparação
# ignora maiúscula/acento/espaço, ver norm() em hunter.py) e continua
# lutando normal com os goblins comuns. Desligado (padrão) = luta com tudo,
# igual antes. Só tem efeito na Floresta PROFUNDA — na Floresta Sombria
# comum, ou em qualquer outro mapa, ignora esse campo.
BOSS_FLORESTA_PROFUNDA = "Abominação do Aspecto Caído"

# --- Caçada em Dupla (conteúdo separado da Masmorra, nível mínimo 42) ---
# MODO_CONTEUDO escolhe qual dos dois roda nesta execução — NUNCA junto com a
# Masmorra (pedido do usuário, pra não misturar): "masmorra" (padrão, usa
# ACCOUNTS) ou "caca_dupla" (usa CACA_DUPLA["grupos"]).
#
# "grupos": lista de DUPLAS — cada item é uma lista com EXATAMENTE 2 contas.
# Cada grupo roda numa sala/caçada PRÓPRIA, em paralelo com os outros grupos
# (2 grupos = 2 caçadas em dupla acontecendo ao mesmo tempo, cada uma com seu
# próprio andar/combate). Os ajustes abaixo (andar_maximo, energia_minima
# etc.) valem igualmente pra TODOS os grupos.
MODO_CONTEUDO = "masmorra"
CACA_DUPLA = {
    "andar_maximo": 49,      # ao alcançar esse andar, sai da caçada e recomeça
    "energia_minima": 10,    # se sobrar menos que isso ao voltar, bebe poção
    "pocoes_reforco": 2,     # quantas Poções de Energia bebe de cada vez
    "max_cacadas": 0,        # quantas caçadas CADA DUPLA faz e então PARA (0 = sem limite)
    "pocao_vida_minima": 10, # se, AO BEBER na caçada, o estoque ficar abaixo
                             # disso, bebe uma e sai da caçada
    "pocao_vida_aviso": 100, # ANTES de iniciar: se o estoque estiver abaixo
                             # disso, abre pop-up de aviso e pausa (reabastecer)
    "vida_min_pct": 40,      # bebe Poção de Vida em combate quando o HP cair
                             # ABAIXO desse % (0 = nunca bebe em combate)
    "reforco_pct": 0,        # "reforço" no INÍCIO da caçada: bebe 1 Poção de Vida
                             # se entrar com HP abaixo desse % (0 = desligado)
    "alma_min_andar": 0,     # SÓ usa alma a partir deste andar (0=sempre). Andares
                             #   fáceis (HP fica 100%) só atacam -> mais rápido.
                             #   Mesma ideia da Cripta (CRIPTA['alma_min_andar']).
    "grupos": [
        [
            {"name": "duo1", "phone": "", "role": "tank", "char_name": "", "souls": []},
            {"name": "duo2", "phone": "", "role": "dps", "char_name": "", "souls": []},
        ],
    ],
}


# --- Carrega o settings.json (escrito pelo painel), se existir ---------
_SETTINGS_PATH = os.path.join(_app_dir(), "settings.json")


def _carregar_settings():
    global API_ID, API_HASH, BOT_USERNAME, SALA_SENHA, ACCOUNTS, MAX_DUNGEONS
    global TANK_HEAL_RATIO, TANK_CRITICAL_RATIO, OTHER_HEAL_RATIO, BETWEEN_DG_HEAL_RATIO
    global TANK_RUGIDO_HP_MIN, TANK_RUGIDO_HP_MAX
    global MODO_CONTEUDO, CACA_DUPLA, MAPA_DESTINO, TIPO_MASMORRA
    global CRIPTA, POCOES
    global CACA_SOLO, MISSAO_OASIS
    global TEMPLO_OASIS, MAPA_TEMPLO_OASIS
    global MASMORRA_POCAO_VIDA_MINIMA, MASMORRA_POCAO_VIDA_AVISO
    global MANUTENCAO_ATIVA, MANUTENCAO_INICIO, MANUTENCAO_FIM
    global MEDIA_JANELA
    global MERCADO_ATIVO, MERCADO_INTERVALO_MIN, MERCADO_REFORCOS, MERCADO_ITENS, MERCADO_CONTAS
    global MERCADO_MAPA_VENDA
    global MERCADO_MAPAS_SEM_MERCADOR
    global PAINEL_ABAS_OCULTAS
    if not os.path.exists(_SETTINGS_PATH):
        return
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        return
    try:
        API_ID = int(s.get("API_ID") or 0)
    except (TypeError, ValueError):
        API_ID = 0
    API_HASH = s.get("API_HASH", API_HASH)
    BOT_USERNAME = s.get("BOT_USERNAME", BOT_USERNAME)
    SALA_SENHA = str(s.get("SALA_SENHA", SALA_SENHA))
    if isinstance(s.get("ACCOUNTS"), list) and s["ACCOUNTS"]:
        ACCOUNTS = s["ACCOUNTS"]
    try:
        MAX_DUNGEONS = int(s.get("MAX_DUNGEONS", MAX_DUNGEONS) or 0)
    except (TypeError, ValueError):
        MAX_DUNGEONS = 0
    try:
        MASMORRA_POCAO_VIDA_MINIMA = int(s.get("MASMORRA_POCAO_VIDA_MINIMA", MASMORRA_POCAO_VIDA_MINIMA))
    except (TypeError, ValueError):
        pass
    try:
        MASMORRA_POCAO_VIDA_AVISO = int(s.get("MASMORRA_POCAO_VIDA_AVISO", MASMORRA_POCAO_VIDA_AVISO))
    except (TypeError, ValueError):
        pass
    MAPA_DESTINO = (s.get("MAPA_DESTINO") or "").strip()
    if s.get("TIPO_MASMORRA") in (("normal",) + tuple(MASMORRAS_ALTERNATIVAS.keys())):
        TIPO_MASMORRA = s["TIPO_MASMORRA"]
    # limites de cura são opcionais no settings.json
    for k in ("TANK_HEAL_RATIO", "TANK_CRITICAL_RATIO",
              "OTHER_HEAL_RATIO", "BETWEEN_DG_HEAL_RATIO"):
        if k in s:
            try:
                globals()[k] = float(s[k])
            except (TypeError, ValueError):
                pass
    try:
        TANK_RUGIDO_HP_MIN = int(s.get("TANK_RUGIDO_HP_MIN", TANK_RUGIDO_HP_MIN))
    except (TypeError, ValueError):
        pass
    try:
        TANK_RUGIDO_HP_MAX = int(s.get("TANK_RUGIDO_HP_MAX", TANK_RUGIDO_HP_MAX))
    except (TypeError, ValueError):
        pass
    MANUTENCAO_ATIVA = bool(s.get("MANUTENCAO_ATIVA", MANUTENCAO_ATIVA))
    if isinstance(s.get("MANUTENCAO_INICIO"), str):
        MANUTENCAO_INICIO = s["MANUTENCAO_INICIO"]
    if isinstance(s.get("MANUTENCAO_FIM"), str):
        MANUTENCAO_FIM = s["MANUTENCAO_FIM"]
    try:
        MEDIA_JANELA = max(3, min(200, int(s.get("MEDIA_JANELA", MEDIA_JANELA))))
    except (TypeError, ValueError):
        pass
    MERCADO_ATIVO = bool(s.get("MERCADO_ATIVO", MERCADO_ATIVO))
    try:
        MERCADO_INTERVALO_MIN = max(1, int(s.get("MERCADO_INTERVALO_MIN", MERCADO_INTERVALO_MIN)))
    except (TypeError, ValueError):
        pass
    if isinstance(s.get("MERCADO_REFORCOS"), list):
        try:
            MERCADO_REFORCOS = [int(x) for x in s["MERCADO_REFORCOS"]]
        except (TypeError, ValueError):
            pass
    if isinstance(s.get("MERCADO_ITENS"), list):
        MERCADO_ITENS = [str(x) for x in s["MERCADO_ITENS"]]
    if isinstance(s.get("MERCADO_CONTAS"), list):
        MERCADO_CONTAS = [str(x) for x in s["MERCADO_CONTAS"]]
    if isinstance(s.get("MERCADO_MAPA_VENDA"), str) and s.get("MERCADO_MAPA_VENDA", "").strip():
        MERCADO_MAPA_VENDA = s["MERCADO_MAPA_VENDA"].strip()
    if isinstance(s.get("MERCADO_MAPAS_SEM_MERCADOR"), list):
        MERCADO_MAPAS_SEM_MERCADOR = [str(x) for x in s["MERCADO_MAPAS_SEM_MERCADOR"]]
    if isinstance(s.get("PAINEL_ABAS_OCULTAS"), list):
        PAINEL_ABAS_OCULTAS = [str(x) for x in s["PAINEL_ABAS_OCULTAS"]]
    if s.get("MODO_CONTEUDO") in ("masmorra", "caca_dupla", "cripta", "caca_solo",
                                   "missao_oasis", "templo_oasis"):
        MODO_CONTEUDO = s["MODO_CONTEUDO"]
    cd = s.get("CACA_DUPLA")
    if isinstance(cd, dict):
        novo = dict(CACA_DUPLA)
        for k in ("andar_maximo", "energia_minima", "pocoes_reforco", "max_cacadas",
                  "pocao_vida_minima", "vida_min_pct", "reforco_pct", "pocao_vida_aviso",
                  "alma_min_andar"):
            if k in cd:
                try:
                    novo[k] = int(cd[k])
                except (TypeError, ValueError):
                    pass
        # "grupos": lista de duplas (cada uma = lista de 2 contas). Formato novo.
        grupos = cd.get("grupos")
        if isinstance(grupos, list) and grupos and all(
                isinstance(g, list) and len(g) == 2 for g in grupos):
            novo["grupos"] = grupos
        # Compatibilidade com settings.json ANTIGO (uma única dupla em "duplas",
        # sem "grupos"): migra pra grupos=[duplas] automaticamente.
        elif isinstance(cd.get("duplas"), list) and len(cd["duplas"]) == 2:
            novo["grupos"] = [cd["duplas"]]
        CACA_DUPLA = novo
    to = s.get("TEMPLO_OASIS")
    if isinstance(to, dict):
        novo = dict(TEMPLO_OASIS)
        for k in ("max_execucoes", "pocao_vida_minima", "pocao_vida_aviso", "vida_min_pct"):
            if k in to:
                try:
                    novo[k] = int(to[k])
                except (TypeError, ValueError):
                    pass
        grupos = to.get("grupos")
        if isinstance(grupos, list) and grupos and all(
                isinstance(g, list) and len(g) == 2 for g in grupos):
            novo["grupos"] = grupos
        TEMPLO_OASIS = novo
    MAPA_TEMPLO_OASIS = (s.get("MAPA_TEMPLO_OASIS") or MAPA_TEMPLO_OASIS).strip()
    cr = s.get("CRIPTA")
    if isinstance(cr, dict):
        novo = dict(CRIPTA)
        for k in ("andar_maximo", "alma_min_andar", "max_criptas"):
            if k in cr:
                try:
                    novo[k] = int(cr[k])
                except (TypeError, ValueError):
                    pass
        if cr.get("nivel") in ("I", "II", "III"):
            novo["nivel"] = cr["nivel"]
        if isinstance(cr.get("contas"), list):
            novo["contas"] = cr["contas"]
        CRIPTA = novo
    pc = s.get("POCOES")
    if isinstance(pc, dict):
        novo = dict(POCOES)
        for k in ("vida_min_pct", "reforco_pct", "pocao_vida_minima", "pocao_vida_aviso"):
            if k in pc:
                try:
                    novo[k] = int(pc[k])
                except (TypeError, ValueError):
                    pass
        POCOES = novo
    cs = s.get("CACA_SOLO")
    if isinstance(cs, dict):
        novo = dict(CACA_SOLO)
        novo["mapa"] = (cs.get("mapa") or "").strip()
        for k in ("energia_minima", "energia_alvo", "vida_min_pct", "max_cacadas"):
            if k in cs:
                try:
                    novo[k] = int(cs[k])
                except (TypeError, ValueError):
                    pass
        if cs.get("tonico_deserto") in ("", "atk", "def", "crit"):
            novo["tonico_deserto"] = cs["tonico_deserto"]
        if isinstance(cs.get("contas"), list):
            novo["contas"] = cs["contas"]
        CACA_SOLO = novo

    mo = s.get("MISSAO_OASIS")
    if isinstance(mo, dict):
        novo = dict(MISSAO_OASIS)
        for k in ("energia_minima", "energia_alvo", "vida_min_pct", "max_missoes"):
            if k in mo:
                try:
                    novo[k] = int(mo[k])
                except (TypeError, ValueError):
                    pass
        if isinstance(mo.get("contas"), list):
            novo["contas"] = mo["contas"]
        MISSAO_OASIS = novo


_carregar_settings()
