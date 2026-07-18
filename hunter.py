# =====================================================================
#  hunter.py  —  Bot de MASMORRA em grupo para o jogo "Teletofus".
#
#  ATENÇÃO: primeira versão do modo Masmorra, escrita a partir de prints
#  reais mas NUNCA rodada contra o jogo. Onde eu tive que supor, o log
#  imprime o texto EXATO da tela — use isso pra corrigir, não adivinhe.
#
#  Fluxo:
#    1) A conta HOST cria a sala COM senha e lê o código gerado.
#    2) As outras 3 entram nessa sala (Buscar salas -> código -> senha).
#    3) Todas clicam "Pronto"; a HOST clica "Iniciar".
#    4) Combate por rodadas simultâneas (~45s): cada conta age no seu papel.
#
#  Rodar:  python hunter.py   (login 1 conta por vez no primeiro uso)
# =====================================================================

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import traceback
import unicodedata
from datetime import datetime

from telethon import TelegramClient

import config


def app_dir():
    """Pasta do programa: ao lado do .exe (empacotado) ou do .py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()

# Console em UTF-8: no .exe o console usa cp1252 e quebra ao imprimir emoji.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Mostra avisos do Telethon no console (ex: FloodWaitError -> "Sleeping for Ns
# on <request>") — é assim que dá pra CONFIRMAR se a lentidão é o Telegram
# limitando o bot por excesso de consultas à API. IMPORTANTE: o Telethon manda
# esse aviso de FloodWait no nível INFO (não WARNING) — com level=WARNING essa
# mensagem ficava escondida mesmo quando acontecia, por isso nunca aparecia no
# log. Com INFO agora dá pra ver de verdade.
logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] [telethon] %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
# Os módulos internos do Telethon (rede, atualização de estado etc.) são MUITO
# barulhentos em INFO — sobem só o logger raiz "telethon" pra WARNING de novo,
# e afinamos APENAS o pedaço que realmente avisa de FloodWait/lentidão de rede
# (client.telegrambaseclient e network.mtprotosender) pra INFO.
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("telethon.client.telegrambaseclient").setLevel(logging.INFO)
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.INFO)


# ---------------------------------------------------------------------
#  Utilidades de texto / log
# ---------------------------------------------------------------------

def norm(s: str) -> str:
    """minúsculas + sem acento, pra casar texto sem depender de acento/emoji."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


LOG_FILE = os.path.join(APP_DIR, "run.log")
RELATORIO_FILE = os.path.join(APP_DIR, "relatorio.json")
SESSAO_BASELINE_FILE = os.path.join(APP_DIR, "sessao_baseline.txt")
SESSAO_CONTINUAR_FLAG = os.path.join(APP_DIR, "sessao_continuar.flag")
# Sinal de PARADA SUAVE ("⏸ Parar no fim" do painel): o bot TERMINA o conteúdo
# atual (masmorra/caçada/cripta) e NÃO começa o próximo. Checado só na
# fronteira entre um conteúdo e outro — nunca interrompe no meio do combate.
PARAR_NO_FIM_FLAG = os.path.join(APP_DIR, "parar_no_fim.flag")
# "🛒 Vender agora" (pedido do usuário 2026-07-15): botão no painel que
# dispara uma venda avulsa no Mercado, sem precisar esperar o intervalo
# automático nem ativar o Mercado de vez. Guarda um TIMESTAMP no arquivo (não
# só existe/não existe) — cada conta lembra o último timestamp que já
# atendeu (s._ultimo_pedido_venda_atendido) e só age de novo se o arquivo
# tiver um valor MAIS NOVO. Isso resolve "várias contas, cada uma no seu
# próprio ritmo" sem precisar apagar o arquivo (que ficaria complicado saber
# quando TODAS já atenderam) — e também já deixa o clique 'auto-desligar'
# sozinho: sem clicar de novo, nenhuma conta dispara outra venda.
VENDER_AGORA_FLAG = os.path.join(APP_DIR, "vender_agora.flag")
# "🛒 Vender agora" com o bot DESLIGADO (pedido do usuário 2026-07-15: "não
# dá pra esse botão ativar o bot pra rodar em segundo plano, fazer a venda e
# se desligar?") — o painel cria este arquivo e LANÇA o processo; main()
# detecta e roda só o login + venda das contas do Mercado, sem entrar em
# nenhum conteúdo normal, e encerra sozinho ao final (ver _rodar_vender_e_sair).
VENDER_E_SAIR_FLAG = os.path.join(APP_DIR, "vender_e_sair.flag")
# "📦 Ler inventário agora" (pedido do usuário 2026-07-15): mesmo esquema do
# 'Vender agora' — LER_INVENTARIO_FLAG (timestamp) pro bot já rodando,
# LER_INVENTARIO_E_SAIR_FLAG pra ligar/ler/desligar sozinho.
LER_INVENTARIO_FLAG = os.path.join(APP_DIR, "ler_inventario.flag")
LER_INVENTARIO_E_SAIR_FLAG = os.path.join(APP_DIR, "ler_inventario_e_sair.flag")


def vender_agora_timestamp():
    """Lê o timestamp do pedido de venda avulsa ('🛒 Vender agora' no
    painel), ou None se o arquivo não existir/estiver corrompido."""
    try:
        with open(VENDER_AGORA_FLAG, encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return None


def ler_inventario_timestamp():
    """Lê o timestamp do pedido de leitura avulsa de inventário ('📦 Ler
    inventário agora' no painel), ou None se o arquivo não existir/estiver
    corrompido."""
    try:
        with open(LER_INVENTARIO_FLAG, encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return None

# PID deste bot (multi-instância): cada PASTA é uma instância independente, e o
# painel DESTA pasta controla SÓ o bot cujo PID está aqui.
BOT_PID_FILE = os.path.join(APP_DIR, "bot.pid")


def _remover_pid_file() -> None:
    try:
        os.remove(BOT_PID_FILE)
    except OSError:
        pass


def parar_no_fim_pedido() -> bool:
    """True se o usuário clicou '⏸ Parar no fim' no painel (que cria o flag).
    Sobrevive a reinício automático de propósito: se o bot caiu e voltou no
    meio, o pedido segue valendo e é atendido ao concluir o conteúdo."""
    return os.path.exists(PARAR_NO_FIM_FLAG)


def limpar_parar_no_fim() -> None:
    try:
        os.remove(PARAR_NO_FIM_FLAG)
    except OSError:
        pass


def _progresso_dupla_file(grupo_idx: int) -> str:
    """Arquivo que guarda quantas caçadas ESTA dupla (grupo_idx) já concluiu
    na execução atual — cada dupla tem o seu, pra rodar 2+ duplas em paralelo
    sem uma contagem 'vazar' pra outra, e pra sobreviver a um reinício
    automático (max_cacadas continua contando de onde parou)."""
    return os.path.join(APP_DIR, f"sessao_progresso_dupla{grupo_idx}.txt")


def _progresso_dupla_templo_file(grupo_idx: int) -> str:
    """Mesma ideia de _progresso_dupla_file, mas pro Templo do Oásis (Duo) —
    arquivo PRÓPRIO (não compartilha contador com a Caçada em Dupla, mesmo
    que os índices de grupo coincidam)."""
    return os.path.join(APP_DIR, f"sessao_progresso_templo{grupo_idx}.txt")


def _ler_progresso_dupla(grupo_idx: int) -> int:
    try:
        return int(open(_progresso_dupla_file(grupo_idx)).read().strip())
    except Exception:
        return 0


def _salvar_progresso_dupla(grupo_idx: int, valor: int) -> None:
    try:
        with open(_progresso_dupla_file(grupo_idx), "w") as f:
            f.write(str(valor))
    except Exception:
        pass


def _ler_progresso_dupla_templo(grupo_idx: int) -> int:
    try:
        return int(open(_progresso_dupla_templo_file(grupo_idx)).read().strip())
    except Exception:
        return 0


def _salvar_progresso_dupla_templo(grupo_idx: int, valor: int) -> None:
    try:
        with open(_progresso_dupla_templo_file(grupo_idx), "w") as f:
            f.write(str(valor))
    except Exception:
        pass
STATUS_FILE = os.path.join(APP_DIR, "status.json")


def write_status(name: str, hp: int, hp_max: int, progresso: str = None,
                 hp_monstro: int = None, hp_monstro_max: int = None,
                 inicio_ts: float = None, nivel: int = None, xp_faltam: int = None,
                 eta_proximo_nivel_seg: float = None) -> None:
    """Grava o HP atual de UMA conta em status.json, pro painel desenhar a
    barra de vida. 'progresso' (opcional): texto curto tipo 'Andar 25' ou
    'Sala 2/4' — em que ponto essa conta está agora, independente do
    conteúdo (Masmorra/Cripta/Caçada em Dupla/Templo). 'hp_monstro'/
    'hp_monstro_max' (opcional): HP do monstro/boss ATUAL que essa conta está
    enfrentando. 'inicio_ts' (opcional): time.time() de quando essa
    masmorra/caçada/cripta/missão começou — o painel calcula "há quanto
    tempo" a partir disso. 'nivel'/'xp_faltam'/'eta_proximo_nivel_seg'
    (opcionais, pedido do usuário 2026-07-15): nível atual, XP faltando pro
    próximo nível, e estimativa de tempo pra chegar lá (calculados por
    atualizar_perfil_e_estimativa, lido do Perfil periodicamente — não a
    cada rodada, caro demais). Lê+grava tudo de novo a cada chamada (sem lock):
    como só roda em pontos síncronos do asyncio (sem 'await' no meio), não
    há disputa real entre as contas; na pior hipótese uma escrita fica velha
    por 1 rodada e se autocorrige na próxima.
    Desligado por completo quando config.STATUS_AO_VIVO_ATIVO = False: nem
    chega a checar se o arquivo existe."""
    if not config.STATUS_AO_VIVO_ATIVO:
        return
    dados = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            dados = {}
    dados[name] = {"hp": hp, "hp_max": hp_max, "progresso": progresso,
                   "hp_monstro": hp_monstro, "hp_monstro_max": hp_monstro_max,
                   "inicio_ts": inicio_ts, "nivel": nivel, "xp_faltam": xp_faltam,
                   "eta_proximo_nivel_seg": eta_proximo_nivel_seg, "ts": time.time()}
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f)
    except Exception:
        pass


def write_status_missao_oasis(name: str, item_nome: str, kills_atual: int, kills_meta: int,
                              itens_atual: int, itens_meta: int) -> None:
    """Grava o progresso AO VIVO da busca do Sunred de UMA conta (mesmo
    arquivo status.json do HP, chave 'missao_oasis' à parte) — pro painel
    mostrar na aba Relatório 'faltam Xkills / Yitens' SEM precisar esperar a
    missão terminar. Atualizado a cada vitória (ver run_missao_oasis_conta)."""
    dados = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            dados = {}
    conta = dados.setdefault(name, {})
    conta["missao_oasis"] = {
        "item": item_nome, "kills": kills_atual, "kills_meta": kills_meta,
        "itens": itens_atual, "itens_meta": itens_meta, "ts": time.time(),
    }
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f)
    except Exception:
        pass


def _ler_relatorio_total() -> int:
    """Lê só o total acumulado do relatorio.json (histórico de todas as execuções)."""
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                return int(json.load(f).get("total", 0))
        except Exception:
            pass
    return 0


def _ler_relatorio_total_caca() -> int:
    """Total acumulado de CAÇADAS concluídas (histórico) — pra baseline do limite."""
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                return int(json.load(f).get("cacadas_total", 0))
        except Exception:
            pass
    return 0


def _ler_relatorio_total_cripta() -> int:
    """Total acumulado de CRIPTAS concluídas (histórico) — pra baseline do limite."""
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                return int(json.load(f).get("criptas_total", 0))
        except Exception:
            pass
    return 0


def _ler_relatorio_total_caca_solo() -> int:
    """Total acumulado de tentativas de CAÇADA SOLO concluídas (histórico)."""
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                return int(json.load(f).get("caca_solo_total", 0))
        except Exception:
            pass
    return 0


def _somar_por_conta_diario(diario: dict, gold_por_conta: dict = None, xp_por_conta: dict = None) -> None:
    """Acumula XP/gold POR CONTA no resumo diário (diario['por_conta']),
    somando TODOS os conteúdos juntos (Masmorra/Caçada/Templo/Solo/Oásis) —
    é o que alimenta 'quanto cada personagem ganhou hoje' na aba 'Por dia'
    do relatório. 'gold_por_conta'/'xp_por_conta': dict {nome: valor} do que
    ESSA execução específica deu (podem ter contas diferentes entre si — a
    função trata cada dict de forma independente, não precisa bater 1:1)."""
    pc = diario.setdefault("por_conta", {})
    for nome, valor in (gold_por_conta or {}).items():
        pc.setdefault(nome, {"xp": 0, "gold": 0})["gold"] += int(valor or 0)
    for nome, valor in (xp_por_conta or {}).items():
        pc.setdefault(nome, {"xp": 0, "gold": 0})["xp"] += int(valor or 0)


def _atualizar_tempo_medio(dados: dict, chave: str, duracao_segundos, manter: int = None):
    """Guarda a duração de UMA execução concluída numa lista rolante (só as
    últimas 'manter' — config.MEDIA_JANELA, padrão 10) por chave — ex:
    'masmorra:Deserto Escaldante', 'cripta', 'templo_oasis', 'caca_dupla'.
    Serve pra estimar quanto tempo falta pro alvo configurado (ex: 'quero
    fazer 30 masmorras do Deserto') — o painel lê isso e faz a conta
    sozinho, sem precisar que o usuário meça nada na mão. Retorna a média
    atual (segundos) dessa chave, ou None se 'duracao_segundos' não foi
    informado dessa vez."""
    if duracao_segundos is None:
        return None
    if manter is None:
        manter = getattr(config, "MEDIA_JANELA", 10)
    tm = dados.setdefault("tempo_medio", {})
    lst = tm.setdefault(chave, [])
    lst.append(round(duracao_segundos, 1))
    del lst[:max(0, len(lst) - manter)]
    return sum(lst) / len(lst)


def _atualizar_xp_medio(dados: dict, chave: str, xp_ganho, manter: int = None):
    """Mesma ideia de _atualizar_tempo_medio, mas pro XP ganho em CADA
    execução concluída (mesma chave, ex: 'masmorra:Deserto Escaldante') —
    junto com o tempo médio, dá pra calcular XP/segundo e estimar quanto
    tempo falta pro PRÓXIMO NÍVEL de cada personagem (pedido do usuário
    2026-07-15). Retorna a média atual, ou None se 'xp_ganho' não foi
    informado."""
    if xp_ganho is None:
        return None
    if manter is None:
        manter = getattr(config, "MEDIA_JANELA", 10)
    xm = dados.setdefault("xp_medio", {})
    lst = xm.setdefault(chave, [])
    lst.append(xp_ganho)
    del lst[:max(0, len(lst) - manter)]
    return sum(lst) / len(lst)


def _salvar_estimativa(modo_label: str, chave_tempo: str, feitas: int, alvo: int,
                       media_segundos) -> None:
    """Grava (em 'estimativa.json', separado do relatorio.json pra não
    disputar escrita à toa) o progresso ATUAL de execuções desde que o bot
    começou + a média de duração por execução dessa mesma chave — o painel
    usa isso pra mostrar 'faltam ~X min pras Y execuções restantes' na aba
    de cada conteúdo, contando pra baixo ao vivo."""
    try:
        caminho = os.path.join(APP_DIR, "estimativa.json")
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"modo": modo_label, "chave": chave_tempo, "feitas": feitas,
                       "alvo": alvo, "media_segundos": media_segundos, "ts": time.time()}, f)
    except Exception:
        pass


def _mapear_nomes_para_conta(dic, sessions):
    """Troca as chaves de um dict (nome -> valor) pelo APELIDO DA CONTA
    (s.name) sempre que a chave bater com o PERSONAGEM (s.char) OU já com o
    próprio apelido de alguma sessão. Resolve a bagunça relatada pelo
    usuário: o Relatório misturava nome de PERSONAGEM (vindo de telas de
    jogo, tipo a tela final da Masmorra — ex: 'Trrool') com apelido de CONTA
    (vindo de outras fontes, tipo Caçada Solo — ex: 'trol'), fazendo a MESMA
    pessoa aparecer como 2 entradas diferentes no resumo. Se a chave não
    bater com NENHUMA sessão conhecida (raro — normalmente só aconteceria
    com uma conta de fora do grupo, tipo um intruso), mantém como veio, pra
    não perder o dado. Funciona com dict de número (soma) ou dict de
    dict/lista (mescla) — usado tanto pra 'gold'/'xp' simples quanto pro
    formato {'gold':N,'drops':[...]} da Masmorra/Templo do Oásis."""
    if not dic or not sessions:
        return dic
    resultado = {}
    for nome_bruto, valor in dic.items():
        nome_final = nome_bruto
        for s in sessions:
            if norm(getattr(s, "char", "") or "") == norm(nome_bruto) or \
               norm(getattr(s, "name", "") or "") == norm(nome_bruto):
                nome_final = s.name
                break
        if nome_final not in resultado:
            resultado[nome_final] = valor
            continue
        # já existe uma entrada pra essa conta (2 chaves brutas diferentes
        # apontando pra mesma pessoa) -> mescla em vez de sobrescrever.
        atual = resultado[nome_final]
        if isinstance(valor, dict) and isinstance(atual, dict):
            mesclado = dict(atual)
            for k, v in valor.items():
                if isinstance(v, list) and isinstance(mesclado.get(k), list):
                    mesclado[k] = mesclado[k] + v
                elif isinstance(v, (int, float)) and isinstance(mesclado.get(k), (int, float)):
                    mesclado[k] = mesclado.get(k, 0) + v
                else:
                    mesclado[k] = v
            resultado[nome_final] = mesclado
        elif isinstance(valor, list) and isinstance(atual, list):
            resultado[nome_final] = atual + valor
        elif isinstance(valor, (int, float)) and isinstance(atual, (int, float)):
            resultado[nome_final] = atual + valor
        else:
            resultado[nome_final] = valor
    return resultado


def mesclar_acumulado_com_loot_final(acumulado, loot_final):
    """Soma shared['acumulado'] (recompensas transitórias de CADA SALA,
    exceto o chefe final) com loot_final (a recompensa do CHEFE, lida da
    tela de conclusão 'Loot do Boss Final') — juntos formam o total real da
    masmorra inteira.
    BUG REAL corrigido (prints do usuário 2026-07-15, Pirâmide do Deserto):
    o chefe final NÃO gera um bloco transitório 'Recompensas (vs Mob):'
    como as salas 1-3 — a recompensa dele só aparece na tela de conclusão.
    Uma correção anterior passou a usar SÓ o acumulado das salas (achando
    que ele já era completo) e isso fez o XP/gold/loot do CHEFE sumir do
    relatório. Agora soma os dois: acumulado (salas) + loot_final (chefe)."""
    base = {"xp_total": (acumulado or {}).get("xp_total", 0), "jogadores": {}}
    for nome, dados in (acumulado or {}).get("jogadores", {}).items():
        base["jogadores"][nome] = {"gold": dados.get("gold", 0),
                                    "drops": list(dados.get("drops", []))}
    if loot_final:
        base["xp_total"] += loot_final.get("xp_total", 0)
        for nome, dados in loot_final.get("jogadores", {}).items():
            alvo = base["jogadores"].setdefault(nome, {"gold": 0, "drops": []})
            alvo["gold"] += dados.get("gold", 0)
            alvo["drops"].extend(dados.get("drops", []))
    return base


def registrar_masmorra(loot_text: str, dano: dict, acumulado: dict,
                        duracao_segundos: float = None, raridades: dict = None,
                        mapa: str = None) -> tuple:
    """Incrementa o contador de masmorras e guarda um resumo ESTRUTURADO no
    relatorio.json: dano por jogador (Ranking de dano da tela final), ouro e
    drops por jogador e XP total da masmorra. 'acumulado' vem de preferência
    de parse_loot_final_masmorra (a tela '🏆 Dungeon concluída! / Loot do
    Boss Final' — confiável, mostra TODO mundo) — só cai pra shared['acumulado']
    (capturado durante o combate, menos confiável) se a tela final não bater
    com esse formato. 'raridades' (vindo do mesmo parser, pelas bolinhas
    coloridas) alimenta o catálogo global usado pra colorir o Loot no
    painel. Também soma no resumo 'diario' (XP total por data).
    'duracao_segundos' é quanto tempo essa masmorra levou do início do
    combate até a conclusão. 'mapa' é o mapa onde essa masmorra rodou (pra
    separar a média de tempo por mapa — 'Deserto Escaldante' costuma ser bem
    mais rápido que 'Covil de Zul'gor', por exemplo). Retorna
    (total_acumulado, média_de_duração_em_segundos_dessa_chave)."""
    dados = {"total": 0, "masmorras": [], "diario": {}}
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass
    dados.setdefault("diario", {})
    if raridades:
        _registrar_itens_no_banco(dados, raridades, origem=f"masmorra:{mapa}" if mapa else "masmorra")
    dados["total"] = int(dados.get("total", 0)) + 1
    loot = "\n".join(l.strip() for l in (loot_text or "").splitlines() if l.strip())
    xp_total = int((acumulado or {}).get("xp_total", 0))
    jogadores = (acumulado or {}).get("jogadores", {})
    gold = {nome: info.get("gold", 0) for nome, info in jogadores.items()}
    drops = {nome: info.get("drops", []) for nome, info in jogadores.items()}
    agora = datetime.now()
    registro = {
        "n": dados["total"],
        "hora": agora.strftime("%d/%m %H:%M"),
        "dano": dano or {},
        "xp_total": xp_total,
        "gold": gold,
        "drops": drops,
        "loot": loot[:1200],
    }
    if mapa:
        registro["mapa"] = mapa
    chave_tempo = f"masmorra:{mapa}" if mapa else "masmorra"
    media_atual = None
    if duracao_segundos is not None:
        registro["duracao_segundos"] = round(duracao_segundos, 1)
        registro["tempo"] = _formatar_duracao(duracao_segundos)
        media_atual = _atualizar_tempo_medio(dados, chave_tempo, duracao_segundos)
    _atualizar_xp_medio(dados, chave_tempo, xp_total)
    dados.setdefault("masmorras", []).append(registro)
    dados["masmorras"] = dados["masmorras"][-3000:]
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["masmorras"] = diario.get("masmorras", 0) + 1
    diario["xp_masmorra"] = diario.get("xp_masmorra", 0) + xp_total
    diario["gold_masmorra"] = diario.get("gold_masmorra", 0) + sum(gold.values())
    _somar_por_conta_diario(diario, gold, {nome: xp_total for nome in gold})
    try:
        with open(RELATORIO_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return dados["total"], media_atual


def _ler_relatorio() -> dict:
    dados = {"total": 0, "masmorras": [], "diario": {}, "cacadas_total": 0}
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass
    dados.setdefault("diario", {})
    dados.setdefault("cacadas_total", 0)
    return dados


def _salvar_relatorio(dados: dict) -> None:
    try:
        with open(RELATORIO_FILE, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _formatar_duracao(segundos: float) -> str:
    """Formata segundos em texto legível: '45s', '12min 34s', '1h 05min'."""
    segundos = int(round(segundos or 0))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h}h {m:02d}min"
    if m:
        return f"{m}min {s:02d}s"
    return f"{s}s"


def registrar_cacada(acumulado: dict = None, grupo_idx: int = 1,
                      duracao_segundos: float = None, andar_final: int = None,
                      raridades: dict = None) -> int:
    """Incrementa o contador de Caçadas em Dupla concluídas e guarda um resumo
    ESTRUTURADO (xp/gold/drops por conta, vindos do acumulado de recompensas da
    caçada) + soma no resumo diário separado por modo. 'grupo_idx' marca QUAL
    dupla completou esta caçada (1, 2, ...) — pro painel mostrar o relatório
    separado por dupla. 'duracao_segundos' é quanto tempo essa caçada levou do
    início até sair (chegar no andar máximo); 'andar_final' é até que andar ela
    foi de verdade. 'raridades' é {item: raridade} aprendido AGORA (pela cor da
    bolinha no resumo) — é gravado num catálogo PERMANENTE em relatorio.json
    (dados["raridades"]), que só cresce: uma vez aprendida a raridade de um
    item, ela nunca precisa ser descoberta de novo. Retorna o total de caçadas
    (histórico, de TODAS as duplas somadas)."""
    dados = _ler_relatorio()
    dados["cacadas_total"] = int(dados.get("cacadas_total", 0)) + 1
    xp_total = int((acumulado or {}).get("xp_total", 0))
    jogadores = (acumulado or {}).get("jogadores", {})
    gold = {nome: info.get("gold", 0) for nome, info in jogadores.items()}
    drops = {nome: info.get("drops", []) for nome, info in jogadores.items()}
    agora = datetime.now()
    registro = {
        "n": dados["cacadas_total"],
        "grupo": grupo_idx,
        "hora": agora.strftime("%d/%m %H:%M"),
        "xp_total": xp_total,
        "gold": gold,
        "drops": drops,
    }
    if duracao_segundos is not None:
        registro["duracao_segundos"] = round(duracao_segundos, 1)
        registro["tempo"] = _formatar_duracao(duracao_segundos)
    if andar_final is not None:
        registro["andar"] = andar_final
    if raridades:
        _registrar_itens_no_banco(dados, raridades, origem="caca_dupla")
    dados.setdefault("cacadas", []).append(registro)
    dados["cacadas"] = dados["cacadas"][-3000:]
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["cacadas"] = diario.get("cacadas", 0) + 1
    diario["xp_caca"] = diario.get("xp_caca", 0) + xp_total
    diario["gold_caca"] = diario.get("gold_caca", 0) + sum(gold.values())
    _somar_por_conta_diario(diario, gold, {nome: xp_total for nome in gold})
    media_atual = _atualizar_tempo_medio(dados, "caca_dupla", duracao_segundos)
    _salvar_relatorio(dados)
    return dados["cacadas_total"], media_atual


def _ler_relatorio_total_templo_oasis() -> int:
    """Total acumulado (histórico) de execuções concluídas do Templo do
    Oásis (Duo) — pra baseline do limite, mesmo padrão das outras."""
    return int(_ler_relatorio().get("templo_oasis_total", 0))


def registrar_templo_oasis(loot_text: str, dano: dict, acumulado: dict, grupo_idx: int = 1,
                           duracao_segundos: float = None, raridades: dict = None) -> tuple:
    """Mesma ideia de registrar_masmorra, mas contabiliza o Templo do Oásis
    (Duo) SEPARADO no relatorio.json (chave 'templo_oasis_total' / lista
    'temploses'), pro painel mostrar sem misturar com a Masmorra normal.
    'grupo_idx' marca qual dupla completou esta execução (1, 2, ...), igual
    à Caçada em Dupla. 'raridades' (vindo de parse_loot_final_templo, quando
    a bolinha de cor aparecer) alimenta o mesmo catálogo global da Masmorra.
    Retorna (total_acumulado, média_de_duração_segundos)."""
    dados = _ler_relatorio()
    if raridades:
        _registrar_itens_no_banco(dados, raridades, origem="templo_oasis")
    dados["templo_oasis_total"] = int(dados.get("templo_oasis_total", 0)) + 1
    loot = "\n".join(l.strip() for l in (loot_text or "").splitlines() if l.strip())
    xp_total = int((acumulado or {}).get("xp_total", 0))
    jogadores = (acumulado or {}).get("jogadores", {})
    gold = {nome: info.get("gold", 0) for nome, info in jogadores.items()}
    drops = {nome: info.get("drops", []) for nome, info in jogadores.items()}
    agora = datetime.now()
    registro = {
        "n": dados["templo_oasis_total"],
        "grupo": grupo_idx,
        "hora": agora.strftime("%d/%m %H:%M"),
        "dano": dano or {},
        "xp_total": xp_total,
        "gold": gold,
        "drops": drops,
        "loot": loot[:1200],
    }
    media_atual = None
    if duracao_segundos is not None:
        registro["duracao_segundos"] = round(duracao_segundos, 1)
        registro["tempo"] = _formatar_duracao(duracao_segundos)
        media_atual = _atualizar_tempo_medio(dados, "templo_oasis", duracao_segundos)
    dados.setdefault("temploses", []).append(registro)
    dados["temploses"] = dados["temploses"][-3000:]
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["templo_oasis"] = diario.get("templo_oasis", 0) + 1
    diario["xp_templo_oasis"] = diario.get("xp_templo_oasis", 0) + xp_total
    diario["gold_templo_oasis"] = diario.get("gold_templo_oasis", 0) + sum(gold.values())
    _somar_por_conta_diario(diario, gold, {nome: xp_total for nome in gold})
    _salvar_relatorio(dados)
    return dados["templo_oasis_total"], media_atual


def registrar_cripta(gold_por_conta: dict = None, xp_por_conta: dict = None, drops_por_conta: dict = None,
                      duracao_segundos: float = None, andar_final: int = None) -> int:
    """Incrementa o contador de Criptas concluídas (chegou no andar máximo) e
    guarda o XP/gold da rodada + soma no resumo diário (xp_cripta). 'gold',
    'xp' e 'drops' são gravados POR CONTA de verdade — confirmado pelo
    usuário: a tela de saída da Cripta ('📦 Progresso acumulado que você vai
    levar: X XP / Y Gold') mostra o ganho INDIVIDUAL de cada personagem, e o
    evento de drop ao vivo ('[NEW]Pri encontrou um Saco das Almas!') já diz
    quem encontrou — nada disso é "do grupo" de verdade (correção de uma
    versão anterior que assumia XP/gold iguais pra todo mundo e drops sem
    dono). 'duracao_segundos' é quanto tempo essa Cripta levou do início até
    sair; 'andar_final' é até que andar ela foi de verdade. Retorna o total
    histórico de criptas."""
    dados = _ler_relatorio()
    dados["criptas_total"] = int(dados.get("criptas_total", 0)) + 1
    gold_por_conta = {nome: int(v or 0) for nome, v in (gold_por_conta or {}).items()}
    xp_por_conta = {nome: int(v or 0) for nome, v in (xp_por_conta or {}).items()}
    drops_por_conta = {nome: list(itens) for nome, itens in (drops_por_conta or {}).items()}
    xp_total = sum(xp_por_conta.values())
    gold_total = sum(gold_por_conta.values())
    agora = datetime.now()
    registro = {
        "n": dados["criptas_total"], "hora": agora.strftime("%d/%m %H:%M"),
        "xp_total": xp_total, "gold_total": gold_total,
        "gold": gold_por_conta, "drops": drops_por_conta,
    }
    if duracao_segundos is not None:
        registro["duracao_segundos"] = round(duracao_segundos, 1)
        registro["tempo"] = _formatar_duracao(duracao_segundos)
    if andar_final is not None:
        registro["andar"] = andar_final
    dados.setdefault("criptas", []).append(registro)
    dados["criptas"] = dados["criptas"][-3000:]
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["criptas"] = diario.get("criptas", 0) + 1
    diario["xp_cripta"] = diario.get("xp_cripta", 0) + xp_total
    diario["gold_cripta"] = diario.get("gold_cripta", 0) + gold_total
    _somar_por_conta_diario(diario, gold_por_conta, xp_por_conta)
    media_atual = _atualizar_tempo_medio(dados, "cripta", duracao_segundos)
    _salvar_relatorio(dados)
    return dados["criptas_total"], media_atual


def registrar_caca_solo(nome_conta: str, xp: int = 0, gold: int = 0,
                        drops: list = None, raridades: dict = None,
                        duracao_segundos: float = None) -> int:
    """Incrementa o contador de Caçadas Solo concluídas. Cada conta caça
    SOZINHA (sem parceiro), então gold/drops são gravados por CONTA de
    verdade (não uma chave genérica 'grupo' como na Cripta). 'raridades'
    (aprendida na hora, pela cor do item) é gravada no MESMO catálogo
    permanente que a Caçada em Dupla já usa.
    'duracao_segundos' (pedido do usuário 2026-07-15: "pode calcular a
    média igual a masmorra, acredito que seja mais preciso"): tempo desde o
    ÚLTIMO kill dessa MESMA conta até este — alimenta tempo_medio/xp_medio
    na chave 'caca_solo:<nome_conta>' (por CONTA, não uma média geral, já
    que cada conta pode ter ATK/nível diferentes e matar num ritmo bem
    diferente um do outro). Usado por atualizar_perfil_e_estimativa pra uma
    estimativa de tempo-até-o-próximo-nível mais precisa que a genérica
    (baseada só na diferença entre duas leituras de Perfil). Retorna o total
    histórico."""
    dados = _ler_relatorio()
    dados["caca_solo_total"] = int(dados.get("caca_solo_total", 0)) + 1
    xp = int(xp or 0)
    gold = int(gold or 0)
    drops = list(drops or [])
    agora = datetime.now()
    dados.setdefault("caca_solo", []).append({
        "n": dados["caca_solo_total"], "hora": agora.strftime("%d/%m %H:%M"),
        "xp_total": xp, "gold": {nome_conta: gold}, "drops": {nome_conta: drops}})
    dados["caca_solo"] = dados["caca_solo"][-3000:]
    if raridades:
        _registrar_itens_no_banco(dados, raridades, origem="caca_solo")
    if duracao_segundos is not None:
        _chave_conta = f"caca_solo:{nome_conta}"
        _atualizar_tempo_medio(dados, _chave_conta, duracao_segundos)
        _atualizar_xp_medio(dados, _chave_conta, xp)
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["caca_solo"] = diario.get("caca_solo", 0) + 1
    diario["xp_caca_solo"] = diario.get("xp_caca_solo", 0) + xp
    diario["gold_caca_solo"] = diario.get("gold_caca_solo", 0) + gold
    _somar_por_conta_diario(diario, {nome_conta: gold}, {nome_conta: xp})
    _salvar_relatorio(dados)
    return dados["caca_solo_total"]


def _ler_relatorio_total_missao_oasis() -> int:
    """Total acumulado de Missões do Oásis (Sunred) concluídas (histórico)."""
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                return int(json.load(f).get("missao_oasis_total", 0))
        except Exception:
            pass
    return 0


def registrar_morte(modo: str, nome_conta: str = "") -> None:
    """Registra 1 morte no resumo DIÁRIO do relatório, pro conteúdo 'modo'
    ('masmorra', 'cripta', 'caca_dupla', 'templo_oasis', 'caca_solo',
    'missao_oasis') — antes disso, uma morte só aparecia no run.log
    ('💀 MORREU...'), sem ficar registrada em lugar nenhum permanente.
    'nome_conta' (se informado) também soma no total POR CONTA do dia (aba
    'Por dia') — só faz sentido informar em conteúdos onde dá pra saber COM
    CERTEZA quem morreu (Caçada Solo/Missão Oásis, cada conta é
    independente); em conteúdos de GRUPO (Masmorra/Cripta/Caçada em Dupla/
    Templo do Oásis), todo mundo sai junto quando alguém morre, então fica
    só no total do modo, sem apontar uma conta específica."""
    dados = _ler_relatorio()
    dia = datetime.now().strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    chave = f"mortes_{modo}"
    diario[chave] = diario.get(chave, 0) + 1
    if nome_conta:
        pc = diario.setdefault("por_conta", {})
        entry = pc.setdefault(nome_conta, {"xp": 0, "gold": 0})
        entry["mortes"] = entry.get("mortes", 0) + 1
    _salvar_relatorio(dados)


def registrar_missao_oasis_xp(xp: int = 0, gold: int = 0, nome_conta: str = "") -> None:
    """Soma XP/gold de UMA vitória da Trilha Instável no resumo DIÁRIO da
    Missão Oásis — chamado a cada monstro morto (igual a Caçada Solo faz pra
    ela mesma), SEPARADO do contador de missões CONCLUÍDAS (registrar_
    missao_oasis, que só incrementa quando bate os 50+200 e entrega). Assim
    o XP/gold ganho caçando pra completar a busca aparece no card 'Oásis' do
    relatório, sem se misturar com os números da Caçada Solo comum.
    'nome_conta' (se informado) também soma no total POR CONTA do dia (aba
    'Por dia' do relatório)."""
    xp = int(xp or 0)
    gold = int(gold or 0)
    if xp == 0 and gold == 0:
        # ANTES, isso era 100% silencioso — se a tela de vitória não bater
        # com o regex esperado (+X XP / +X Gold), o kill simplesmente
        # sumia do relatório sem nenhum aviso. Agora loga pra dar
        # visibilidade real do que está acontecendo.
        log(nome_conta or "oasis", "⚠️ vitória na Missão Oásis leu XP=0 e Gold=0 "
                                    "— não vou somar nada no relatório (a tela pode "
                                    "ter um formato diferente do esperado).")
        return
    dados = _ler_relatorio()
    dia = datetime.now().strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["xp_missao_oasis"] = diario.get("xp_missao_oasis", 0) + xp
    diario["gold_missao_oasis"] = diario.get("gold_missao_oasis", 0) + gold
    if nome_conta:
        _somar_por_conta_diario(diario, {nome_conta: gold}, {nome_conta: xp})
    _salvar_relatorio(dados)


def registrar_missao_oasis(nome_conta: str, monstro_alvo: str, recompensa: str = "") -> int:
    """Incrementa o contador de Missões do Oásis (Sunred) CONCLUÍDAS (bateu
    os 50 do monstro-alvo E os 200 no total). Cada conta é independente
    (igual Caçada Solo), então guarda por CONTA de verdade. Retorna o total
    histórico. O XP/gold de cada monstro morto é contado à parte, em
    registrar_missao_oasis_xp (chamado por kill, não por missão concluída)."""
    dados = _ler_relatorio()
    dados["missao_oasis_total"] = int(dados.get("missao_oasis_total", 0)) + 1
    agora = datetime.now()
    dados.setdefault("missao_oasis", []).append({
        "n": dados["missao_oasis_total"], "hora": agora.strftime("%d/%m %H:%M"),
        "conta": nome_conta, "monstro_alvo": monstro_alvo,
        "gold": {nome_conta: 0}, "drops": {nome_conta: [recompensa] if recompensa else []}})
    dados["missao_oasis"] = dados["missao_oasis"][-3000:]
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["missao_oasis"] = diario.get("missao_oasis", 0) + 1
    _salvar_relatorio(dados)
    return dados["missao_oasis_total"]


def registrar_martelo_magico(nome_conta: str) -> int:
    """Incrementa o contador de 'Martelo Mágico' recebidos da Nurmora (quest
    opcional, sem relação com a busca do Sunred — ver is_npc_nurmora).
    Pedido do usuário (2026-07-15): ela é a forma mais fácil de conseguir o
    Martelo do Gibby, e o modo '🎯 Só Nurmora' permite farmar vários fazendo
    a conta ficar só na Trilha Silenciosa. Guarda total geral + por dia +
    por conta, no mesmo padrão dos outros contadores do relatório."""
    dados = _ler_relatorio()
    dados["martelo_magico_total"] = int(dados.get("martelo_magico_total", 0)) + 1
    agora = datetime.now()
    dia = agora.strftime("%Y-%m-%d")
    diario = dados["diario"].setdefault(dia, {})
    diario["martelo_magico"] = diario.get("martelo_magico", 0) + 1
    pc = diario.setdefault("por_conta", {})
    entry = pc.setdefault(nome_conta, {"xp": 0, "gold": 0})
    entry["martelo_magico"] = entry.get("martelo_magico", 0) + 1
    _salvar_relatorio(dados)
    return dados["martelo_magico_total"]


MOTIVOS_PAUSA = {
    "morte": "Um personagem morreu",
    "limite_masmorras": "Atingiu o limite de masmorras configurado",
    "limite_cacadas": "Atingiu o limite de caçadas configurado",
    "limite_criptas": "Atingiu o limite de criptas configurado",
    "limite_caca_solo": "Atingiu o limite de caçadas solo configurado",
    "pocao_vida_baixa": "Menos de 50 Poções de Vida no estoque",
    "pocao_energia_indisponivel": "Não consegui repor Poções de Energia",
    "muitos_reinicios": "Muitos reinícios automáticos seguidos (possível erro repetido)",
    "parar_no_fim": "Parado a pedido (Parar no fim)",
    "travou": "Uma conta travou (flood/rede) e o grupo saiu por segurança",
}


def registrar_pausa(motivo: str, detalhe: str = "") -> None:
    """Grava em relatorio.json o motivo da ÚLTIMA pausa do bot, pro painel
    mostrar na aba Relatório (sobrescreve a pausa anterior — só a mais
    recente importa)."""
    dados = _ler_relatorio()
    dados["ultima_pausa"] = {
        "motivo": motivo,
        "descricao": MOTIVOS_PAUSA.get(motivo, motivo),
        "detalhe": detalhe,
        "quando": datetime.now().strftime("%d/%m %H:%M"),
    }
    _salvar_relatorio(dados)


# CORRIGIDO (trazido do build "só Caçada em Dupla" v1.3.7-caca, pedido do
# usuário: "o que mais dá pra tirar, pra o código ficar mais leve?"): log() é
# a função mais chamada de TODO o bot — uma única execução longa pode gerar
# dezenas de milhares de linhas. Antes, CADA chamada abria o arquivo,
# escrevia 1 linha e fechava de novo (síncrono, sem await), travando o loop
# assíncrono a cada vez. Agora mantém UM handle aberto (buffering=1 = buffer
# por linha, então cada '\n' já força o flush pro disco na hora — mesmo
# comportamento de "aparece no run.log imediatamente" de antes, só sem
# reabrir o arquivo do zero a cada linha).
_log_fh = None


def _log_handle():
    global _log_fh
    if _log_fh is None:
        try:
            _log_fh = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        except Exception:
            _log_fh = False   # sentinela: falhou 1x, não tenta abrir de novo
    return _log_fh or None    # False (sentinela) também cai pra None aqui


def log(name: str, msg: str) -> None:
    linha = f"[{datetime.now():%H:%M:%S}] [{name}] {msg}"
    print(linha, flush=True)
    fh = _log_handle()
    if fh is not None:
        try:
            fh.write(linha + "\n")
        except Exception:
            pass


def popup_aviso(titulo: str, msg: str) -> None:
    """Pop-up NATIVO do Windows (aviso, fica na frente). Bloqueia até o OK.
    Silencioso em outros SO / se não der pra abrir."""
    try:
        import ctypes
        # MB_ICONWARNING (0x30) | MB_SETFOREGROUND (0x10000) | MB_TOPMOST (0x40000)
        ctypes.windll.user32.MessageBoxW(0, msg, titulo, 0x30 | 0x10000 | 0x40000)
    except Exception as e:
        log("bot", f"(não consegui abrir o pop-up de aviso: {e!r})")


# ---------------------------------------------------------------------
#  Leitura de tela
# ---------------------------------------------------------------------

HP_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
ROOM_CODE_RE = re.compile(r"\[([A-Za-z0-9]{4,10})\]")            # lobby: "... [D1C12E]"
ID_RE = re.compile(r"ID:\s*([A-Za-z0-9]{4,10})", re.IGNORECASE)  # combate: "ID: D1C12E"


def find_room_code(text: str):
    """Código da sala: no lobby vem entre colchetes; no combate vem após 'ID:'."""
    m = ROOM_CODE_RE.search(text or "")
    if m:
        return m.group(1)
    m = ID_RE.search(text or "")
    return m.group(1) if m else None


def _nome_bate(alvo: str, texto: str) -> bool:
    """Confere se 'alvo' (nome já normalizado, ex.: 'pri') aparece como um
    NOME DE VERDADE em 'texto' (também já normalizado) — não só como
    substring solta dentro de outra palavra. BUG REAL corrigido 2026-07-16
    (usuário: HP da conta 'Pri' lido completamente errado durante uma Caçada
    em Dupla, acompanhando o HP do monstro 'Yeti Primordial' em vez do dela
    — porque 'pri' é substring de 'pri-mordial', e o código antes só fazia
    'alvo in texto').

    BUG REAL corrigido 2026-07-17 (usuário: HP da conta 'Léozão S.' nunca
    lido no Templo do Oásis — a causa NÃO era retomada nem corrida de tela,
    era o PONTO no final do nome de verdade do personagem, "Léozão S."; log
    de diagnóstico confirmou a linha 'AKT Léozão S. Nv.45' na tela).
    Antes usava \\b (fronteira de PALAVRA) nas duas pontas, mas \\b só marca
    fronteira numa transição entre um caractere de palavra e um que não é
    — e quando o nome termina em pontuação (o '.' de 'S.') seguida de
    espaço na tela, as duas pontas são "não-palavra", sem transição
    nenhuma ali, e o \\b nunca fecha o casamento (fica sempre False).
    Troquei por lookaround baseado em alfanumérico
    ((?<![a-z0-9])...(?![a-z0-9])), que exige só que NENHUM caractere
    alfanumérico esteja colado nas pontas do nome — funciona igual pra
    nomes normais (Tom, Pri) e agora também pra nomes com pontuação no
    final/início (Léozão S.)."""
    if not alvo:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(alvo)}(?![a-z0-9])", texto) is not None


def player_hp(text: str, char_name: str):
    """
    HP (atual, max) do personagem 'char_name', achando a linha do nome dele
    na lista de Grupo/Membros e pegando o próximo 'X/Y'. None se não achar.
    """
    if not text or not char_name:
        return None
    alvo = norm(char_name)
    linhas = text.splitlines()
    for i, linha in enumerate(linhas):
        if _nome_bate(alvo, norm(linha)):
            # o HP costuma estar na MESMA linha ou na de baixo
            for j in (i, i + 1, i + 2):
                if j < len(linhas):
                    m = HP_RE.search(linhas[j])
                    if m:
                        return int(m.group(1)), int(m.group(2))
    return None


def monster_alive(text: str) -> bool:
    """
    Há monstro vivo? A linha do monstro tem 'HP:' junto de 'ID:'. Se o HP
    dessa linha for > 0, tem monstro. (Combate segue enquanto houver.)
    """
    for linha in (text or "").splitlines():
        if "id:" in norm(linha) and "hp:" in norm(linha):
            m = HP_RE.search(linha)
            if m and int(m.group(1)) > 0:
                return True
    return False


def monster_hp(text: str):
    """(HP atual, HP máximo) do monstro/boss ATUAL, ou None se não achar —
    mesma linha que monster_alive usa (tem 'ID:' junto de 'HP:', diferente
    da linha do jogador que só tem 'HP:'). Pro painel mostrar o HP do boss
    no 'Status ao vivo', junto com o do próprio personagem."""
    for linha in (text or "").splitlines():
        if "id:" in norm(linha) and "hp:" in norm(linha):
            m = HP_RE.search(linha)
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


# ---------------------------------------------------------------------
#  Caçada em Dupla (conteúdo separado da Masmorra): cabeçalho confirmado
#  por print "CAÇADA EM DUPLA | <Zona> [ELITE] (<andar> 💀)" — o [ELITE] e
#  o 💀 só aparecem em andares de chefe, o resto do cabeçalho é igual em
#  todo andar (usuário confirmou: só muda o HP do mob).
# ---------------------------------------------------------------------

# limite de até 60 caracteres entre "cacada em dupla" e o número, pra evitar
# capturar um parêntese com número de OUTRA parte da tela (ex: "(100%)" de
# algum indicador bem mais abaixo) — antes usava DOTALL sem limite, o que
# podia "pular" pra qualquer parêntese-com-número no resto do texto inteiro.
ANDAR_RE = re.compile(r"cacada em dupla.{0,60}?\(\s*(\d+)", re.IGNORECASE | re.DOTALL)
ENERGIA_RE = re.compile(r"energia:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)


def parse_andar(text: str):
    """Andar atual da Caçada em Dupla lido do cabeçalho, ou None se não achar."""
    m = ANDAR_RE.search(norm(text))
    return int(m.group(1)) if m else None


def energia_atual(text: str):
    """(atual, máxima) de Energia lida no menu ('⚡ Energia: 40/40'), ou None."""
    m = ENERGIA_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


HOURGLASS = ("⏳", "⌛")   # ampulheta na linha do personagem = ainda NÃO agiu


def my_turn_state(text: str, char_name: str) -> str:
    """
    Estado do MEU turno lendo a minha linha na lista de Grupo:
      "waiting"  -> tem ampulheta ⏳ (não agi ainda nesta rodada) -> devo agir
      "acted"    -> ampulheta sumiu (minha ação já registrou) -> aguardo
      "unknown"  -> não achei minha linha
    """
    if not char_name:
        return "unknown"
    alvo = norm(char_name)
    for line in (text or "").splitlines():
        nl = norm(line)
        if "nv." in nl and alvo in nl:
            return "waiting" if any(h in line for h in HOURGLASS) else "acted"
    return "unknown"


def my_turn_state_caca(text: str, char_name: str) -> str:
    """Estado do MEU turno na CAÇADA. A caçada TAMBÉM tem a ampulheta ⏳ na
    linha de quem ainda não agiu (mesmo sem tank) — igual à masmorra. Só que a
    linha da caçada é 'Nome ❤️ X/Y' (sem 'Nv.'), então acho a MINHA linha pelo
    nome + HP 'X/Y' (o 'X/Y' distingue a linha do personagem do log de 'Últimos
    Eventos', que cita o nome mas não tem X/Y). Retorna waiting/acted/unknown."""
    return my_turn_state_caca_debug(text, char_name)[0]


def my_turn_state_caca_debug(text: str, char_name: str):
    """Igual a my_turn_state_caca, mas TAMBÉM devolve a linha exata que foi
    considerada 'a minha linha' (ou None se não achou nenhuma) — usado só pra
    depurar quando o bot fica muito tempo achando que ainda é pra esperar."""
    if not char_name:
        return "unknown", None
    alvo = norm(char_name)
    for line in (text or "").splitlines():
        if _nome_bate(alvo, norm(line)) and HP_RE.search(line):
            estado = "waiting" if any(h in line for h in HOURGLASS) else "acted"
            return estado, line
    return "unknown", None


def i_lost_turn(text: str, char_name: str) -> bool:
    """Meu personagem aparece com 'perdeu a vez' no log de eventos?"""
    if not char_name:
        return False
    alvo = norm(char_name)
    for line in (text or "").splitlines():
        nl = norm(line)
        if "perdeu a vez" in nl and alvo in nl:
            return True
    return False


def monsters_hp_sig(text: str):
    """
    Assinatura do HP de TODOS os monstros (linhas com 'ID:'). Muda quando a
    rodada resolve (dano aplicado) ou o monstro troca. É o sinal de 'nova
    rodada' — bem mais confiável que o texto todo (que muda a cada jogador).
    """
    vals = []
    for linha in (text or "").splitlines():
        if "id:" in norm(linha):
            m = HP_RE.search(linha)
            if m:
                vals.append(int(m.group(1)))
    return tuple(vals)


def is_combat_screen(message) -> bool:
    """Tela de combate = tem os botões de ação de combate."""
    return find_button(message, "atacar") is not None and \
           find_button(message, "defender") is not None


def is_lobby_screen(message) -> bool:
    """Tela de LOBBY (sala montada, esperando o host clicar Iniciar): tem
    'Iniciar'/'Pronto'/'Despronto'. NÃO é fim de combate — o combate ainda vai
    começar. BUG REAL 2026-07-04: uma conta agia cedo demais, pegava o lobby e
    o bot achava que 'saiu do combate' -> voltava ao ciclo sozinho ->
    dessincronizava das outras 3 -> travava na barreira -> reinício em LOOP
    (ficou 30 min preso). Agora, no lobby, o combat_loop ESPERA em vez de sair."""
    return (find_button(message, "iniciar") is not None
            or find_button(message, "pronto") is not None
            or find_button(message, "despronto") is not None)


def is_submenu_combate(message) -> bool:
    """Submenu de combate aberto (Almas/Consumíveis: '✨ Escolha uma alma…'):
    tem 'Voltar' e NÃO é a tela de combate nem o lobby. Deve VOLTAR pro
    combate, não sair (mesmo bug do lobby: o bot saía por engano)."""
    if is_combat_screen(message) or is_lobby_screen(message):
        return False
    return find_button(message, "voltar", "atras", "⬅", "◀", "🔙") is not None


def outras_em_combate(shared, meu_nome, janela=90) -> bool:
    """True se ALGUMA OUTRA conta esteve em combate ATIVO nos últimos 'janela'
    segundos. Sinal de que a masmorra AINDA está ATIVA e PROGREDINDO — se eu me
    perdi (caí no menu/lobby), devo ESPERAR o grupo terminar em vez de formar
    nova masmorra / reiniciar (pedido do usuário 2026-07-04). Cada conta publica
    em shared['em_combate'][nome] o TIMESTAMP da última rodada de combate (0 =
    fora de combate). Uso o timestamp (não um bool) de propósito: assim a espera
    dura o tempo que a masmorra precisar (curta ou longa) MAS desiste rápido
    (~90s) se o grupo TRAVAR de vez (para de atualizar o timestamp) — melhor que
    um teto fixo em minutos, que seria curto demais numa masmorra longa e longo
    demais num travamento."""
    agora = time.time()
    em = (shared or {}).get("em_combate", {})
    return any(ts and (agora - ts) < janela
               for nome, ts in em.items() if nome != meu_nome)


def conta_travada_no_combate(shared, meu_nome):
    """Detecta uma OUTRA conta que estava EM COMBATE e TRAVOU (parou de agir por
    tempo demais — FloodWait, queda de rede, reinício). SEGURANÇA (pedido do
    usuário 2026-07-04): se uma conta congela no meio da luta, o grupo corre
    risco de MORTE — o tank travado não defende (aggro vaza), o suporte travado
    não cura. Então é melhor o grupo TODO sair e reagrupar do que continuar
    arriscando morte durante os minutos que a conta fica presa (o flood pode
    travá-la por até 6 min).
    COMO detecta rápido e sem falso positivo: cada conta atualiza o seu
    timestamp em shared['em_combate'] a cada ~0.8s do loop de combate; então um
    timestamp que ficou VELHO só pode ser conta travada. Uso limites curtos
    (tank 30s, outros 45s — bem acima do ~10s do pior caso normal de uma
    rodada com ação+confirmação, e MUITO abaixo dos minutos de um flood). O tank
    tem limite menor por ser o mais crítico (sem ele todos tomam dano).
    Retorna (nome, papel) da 1ª conta travada, ou None se está tudo ok."""
    roles = (shared or {}).get("roles", {})
    em = (shared or {}).get("em_combate", {})
    agora = time.time()
    for nome, ts in em.items():
        if nome == meu_nome or not ts:
            continue   # sou eu, ou está fora de combate (menu -> outro caminho)
        limite = 30 if roles.get(nome) == "tank" else 45
        if agora - ts > limite:
            return nome, roles.get(nome, "?")
    return None


def waiting_actions(text: str) -> bool:
    return "aguardando acoes" in norm(text)


def round_signature(text: str) -> str:
    """
    Assinatura da rodada IGNORANDO o cronômetro (que muda a cada segundo).
    Muda só quando a rodada de fato avança (log de eventos / HP mudam), pra
    cada conta agir UMA vez por rodada.
    """
    n = norm(text)
    n = re.sub(r"aguardando acoes[.\s]*\d+\s*s", "", n)  # tira "aguardando ações... 45s"
    return n


def round_signature_caca(text: str) -> str:
    """
    Assinatura da rodada na CAÇADA EM DUPLA. A tela da caçada é diferente da
    masmorra: NÃO tem 'Aguardando ações' nem ampulheta/'Nv.' por jogador — tem
    um cronômetro 'Turno: Ns' que conta pra baixo. Tiramos o cronômetro pra a
    assinatura mudar SÓ quando a rodada de fato avança (HP do monstro, XP e o
    log de 'Últimos Eventos' mudam). Assim cada conta age 1x por rodada, logo
    que a tela muda — sem o atraso de esperar 'por segurança'.
    """
    n = norm(text)
    n = re.sub(r"turno:?\s*\d+\s*s", "", n)              # tira "Turno: 45s"
    n = re.sub(r"aguardando acoes[.\s]*\d+\s*s", "", n)
    return n


def keys_count(text: str) -> int:
    """Quantas 'Chaves de Masmorra' a conta tem (lido do menu principal)."""
    m = re.search(r"chaves de masmorra:\s*(\d+)", norm(text))
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------
#  CRIPTA (3º conteúdo) — parsers (trazidos da versão do colega)
# ---------------------------------------------------------------------

def keys_count_ossos(text: str) -> int:
    """Quantas 'Chaves de Ossos' a conta tem (custo pra entrar na Cripta)."""
    m = re.search(r"chaves de ossos:\s*(\d+)", norm(text))
    return int(m.group(1)) if m else 0


# Andar/progresso da Cripta LIDO DA TELA DE COMBATE. CONFIRMADO por print: o
# contador aparece como "Kill: N" no topo ("🦴 CRIPTA I  Kill: 1  🦴") — cada mob
# morto avança 1. O 'kill:' exige os dois-pontos pra NÃO casar com nomes tipo
# "HitKill". Mantidos formatos alternativos como reserva.
ANDAR_CRIPTA_RES = [
    re.compile(r"kill\s*:\s*(\d+)", re.IGNORECASE),
    re.compile(r"andar\s*[:\-]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"profundeza\s*[:\-]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"nivel\s*[:\-]?\s*(\d+)", re.IGNORECASE),
]


def parse_andar_cripta(text: str):
    """Andar atual da Cripta (ou None se não achar em nenhum formato conhecido)."""
    n = norm(text or "")
    for rx in ANDAR_CRIPTA_RES:
        m = rx.search(n)
        if m:
            return int(m.group(1))
    return None


# "Sala: 2/4" aparece no cabeçalho da Masmorra normal e do Templo do Oásis
# (ex: "🗝 PIRÂMIDE DO DESERTO  Sala: 2/4 🗝").
SALA_RE = re.compile(r"sala\s*:?\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)


def progresso_atual_texto(text: str):
    """Tenta ler 'em que ponto' a conta está AGORA (andar da Cripta, andar da
    Caçada em Dupla, ou Sala X/Y da Masmorra/Templo do Oásis) — pro painel
    poder mostrar isso junto com o HP ao vivo, sem se importar em qual
    conteúdo a conta está rodando no momento. Retorna uma string curta pronta
    pra mostrar (ex: 'Andar 25', 'Sala 2/4') ou None se não achou nada."""
    m = SALA_RE.search(norm(text or ""))
    if m:
        return f"Sala {m.group(1)}/{m.group(2)}"
    andar_cripta = parse_andar_cripta(text)
    if andar_cripta is not None:
        return f"Andar {andar_cripta}"
    andar_caca = parse_andar(text)
    if andar_caca is not None:
        return f"Andar {andar_caca}"
    return None


CACA_CODIGO_RE_CRIPTA = re.compile(r"cripta\s+[ivx]+\s+([A-Za-z0-9]{4,10})", re.IGNORECASE)


def find_cripta_code(text: str):
    """Código da sala da Cripta no lobby ('🦴 Cripta I C6BF6C')."""
    m = CACA_CODIGO_RE_CRIPTA.search(text or "")
    if m:
        return m.group(1)
    return find_room_code(text)   # tenta os formatos genéricos ([code]/ID:)


# Loot raro que aparece nos EVENTOS AO VIVO da Cripta ("[NEW]Pri encontrou um
# Saco das Almas!"), diferente de Masmorra/Caçada em Dupla (que só mostram
# drops no resumo final de saída) — a Cripta não tem esse resumo com lista de
# itens, só esse log durante o combate. Aceita "um"/"uma" antes do nome.
ENCONTROU_ITEM_CRIPTA_RE = re.compile(r"(.+?)\s+encontrou\s+um[a]?\s+(.+?)!", re.IGNORECASE)


def parse_drops_evento_cripta(text: str):
    """Lê a seção 'Eventos:' da tela de combate da Cripta e devolve
    [(nome_personagem, item), ...] de linhas tipo '[NEW]Pri encontrou um
    Saco das Almas!'. O próprio evento já diz QUEM encontrou (confirmado
    pelo usuário 2026-07-11) — antes isso era descartado e o drop virava um
    'balaio' só do grupo; agora dá pra atribuir à conta certa, igual XP/gold.
    Quem CHAMA é responsável por deduplicar entre as várias contas do grupo
    (todas veem o MESMO evento) — ver combat_loop_cripta."""
    pares = []
    for linha in (text or "").splitlines():
        m = ENCONTROU_ITEM_CRIPTA_RE.search(linha)
        if m:
            nome = re.sub(r"^[^\wÀ-ÿ\[\{]+", "", m.group(1)).strip()
            item = m.group(2).strip()
            if nome and item:
                pares.append((nome, item))
    return pares


def parse_membros_cripta(text: str):
    """Lê a lista 'Membros (N/5):' do lobby da Cripta e devolve as linhas de
    cada membro (uma por membro). A linha inteira (normalizada) serve pra
    checar se o personagem é um dos nossos (por nome) ou um INTRUSO."""
    linhas = (text or "").splitlines()
    ini = next((i for i, l in enumerate(linhas) if "membros" in norm(l) and "(" in l), None)
    if ini is None:
        return []
    out = []
    for l in linhas[ini + 1:]:
        nl = norm(l)
        if not nl:
            continue
        if "todos precisam" in nl or "marque-se" in nl or "chave de ossos" in nl:
            break
        if "lv" in nl:
            out.append(l.strip())
    return out


def intruso_na_sala(text: str, nomes_nossos) -> bool:
    """True se ALGUM membro do lobby NÃO é uma das nossas contas (comparando o
    nome do personagem). 'nomes_nossos' = lista dos char_name configurados."""
    alvos = [norm(n) for n in nomes_nossos if n]
    for linha in parse_membros_cripta(text):
        nl = norm(linha)
        if not any(a and a in nl for a in alvos):
            return True
    return False


def parse_saida_cripta(text: str):
    """Lê a tela de saída da Cripta ('📦 Progresso acumulado que você vai levar:
    📘 33219 XP / 💰 1015 Gold / 🦴 0 Pó de Ossos') -> (xp, gold)."""
    n = norm(text or "")
    mxp = re.search(r"([\d.,]+)\s*xp", n)
    mgold = re.search(r"([\d.,]+)\s*gold", n)
    xp = int(re.sub(r"[.,]", "", mxp.group(1))) if mxp else 0
    gold = int(re.sub(r"[.,]", "", mgold.group(1))) if mgold else 0
    return xp, gold


DEATH_WORDS = ("morreu", "foi derrotado", "caiu em combate", "tombou", "foi morto",
               "eliminado")


def someone_died(text: str, nomes=None) -> bool:
    """
    Detecta morte de algum JOGADOR: por palavra no log OU por um membro do
    grupo com HP 0 (ignorando a linha do monstro, que tem 'ID:').
    'nomes' = lista dos personagens do grupo (opcional). Quando passada, a
    palavra de morte no log SÓ conta se estiver na MESMA linha de um dos
    NOSSOS personagens — evita confundir morte de MOB (comum na Cripta, que
    mata mobs sem parar: "X foi derrotado") com morte de jogador. Sem
    'nomes' (Masmorra/Caçada, como sempre foi), o comportamento é o de
    sempre (procura em qualquer parte do texto).
    Um caso à parte: '❌ Eliminado' pode vir SOZINHO na linha de baixo do
    nome do personagem (formato visto num print do usuário: '💀
    [NEW]Pri (Líder) — Nv. 46' numa linha, '❌ Eliminado' na de baixo, sem
    repetir o nome) — só ESSA palavra específica é aceita na linha seguinte,
    e só quando a linha de baixo NÃO TEM MAIS NADA além dela.
    BUG REAL corrigido (print do usuário 2026-07-16: "consta que a nati
    morreu, mas ela não morreu" — na Cripta): a correção anterior (2026-07-15)
    tinha ampliado essa checagem da linha seguinte pra TODAS as
    DEATH_WORDS, não só 'eliminado' — e isso quebrou a Cripta, onde é
    normalíssimo ter uma linha tipo 'Natii causa 126 em Golem de Gelo'
    seguida de 'Golem de Gelo foi derrotado!' no log de eventos: a linha de
    cima tem o nome da jogadora, a de baixo tem uma DEATH_WORD (mas é do
    MONSTRO, não dela) — e o bot achava que ela tinha morrido. Restrito de
    volta pra só 'eliminado' sozinho na linha, que é o único formato onde
    isso realmente acontece (o nome fica de verdade sem repetir na linha
    de baixo)."""
    alvos = [norm(x) for x in (nomes or []) if x]
    if not alvos:
        if any(w in norm(text) for w in DEATH_WORDS):
            return True
    else:
        linhas = (text or "").splitlines()
        for i, linha in enumerate(linhas):
            nl = norm(linha)
            if "id:" in nl:
                continue
            if not any(a in nl for a in alvos):
                continue
            if any(w in nl for w in DEATH_WORDS):
                return True
            proxima = norm(linhas[i + 1]).strip() if i + 1 < len(linhas) else ""
            # só conta se a linha seguinte NÃO TIVER MAIS NADA além do
            # marcador de eliminação (evita casar com frases de combate
            # tipo "X foi eliminado por Y!", que têm mais texto na linha)
            proxima_limpa = re.sub(r"^[^\wÀ-ÿ]+", "", proxima).strip()
            if proxima_limpa == "eliminado":
                return True
    for linha in (text or "").splitlines():
        nl = norm(linha)
        if "id:" in nl:            # linha do monstro, não conta
            continue
        if "❤" in linha or "hp:" in nl:
            m = HP_RE.search(linha)
            if m and int(m.group(1)) == 0:
                return True
    return False


def damage_to_me(text: str, char_name: str) -> int:
    """
    Dano que o monstro causou EM MIM nesta tela, pelo log de eventos —
    reconhece DOIS formatos diferentes (confirmado por print do usuário
    2026-07-16, a Cripta usa um formato diferente da Masmorra/Caçada):
      1) '<Monstro> causa X em <MeuNome>'                  (Masmorra/Caçada)
      2) '<MeuNome> defendeu/atacou e recebeu X de dano'   (Cripta)
    BUG REAL corrigido: 'levou X de dano' nunca aparecia no log da Cripta
    porque essa função só reconhecia o formato 1 — a Cripta nunca usa
    'causa X em Y', sempre usa o formato 2. Retorna o maior valor encontrado
    (entre os dois formatos), ou 0."""
    if not text or not char_name:
        return 0
    alvo = norm(char_name)
    maior = 0
    nt = norm(text)
    for m in re.finditer(r"causa\s+(\d+)\s+em\s+([^\n*]+)", nt):
        if _nome_bate(alvo, norm(m.group(2))):
            maior = max(maior, int(m.group(1)))
    for m in re.finditer(r"([^\n*]+?)\s+\w+\s+e\s+recebeu\s+(\d+)\s+de\s+dano", nt):
        if _nome_bate(alvo, norm(m.group(1))):
            maior = max(maior, int(m.group(2)))
    return maior


# ---------------------------------------------------------------------
#  Relatório: recompensas por mob (ouro/XP/drop) e ranking de dano final
#  (formato confirmado por print do usuário 2026-07-01, tela de combate da
#  Masmorra, bloco "🏆 Recompensas (vs <Monstro>): 👤 <nv> <nome>: 💰 Xg
#  ⭐ Y XP • <item ou 'Nenhum item'>"). Esse bloco aparece a cada mob morto
#  no log de Últimos Eventos (não só no chefe) — por isso é lido a CADA
#  refresh do combate, não só na tela final.
# ---------------------------------------------------------------------

RECOMPENSA_HEADER_RE = re.compile(r"recompensas\s*\(vs\s+(.+?)\)\s*:", re.IGNORECASE)
RECOMPENSA_LINHA_RE = re.compile(
    r"\d+\s*(?:\[new\]\s*)?(.+?):\s*.*?(\d+)\s*g\b.*?(\d+)\s*xp\b\s*[•·]?\s*(.*)",
    re.IGNORECASE)
RANKING_LINHA_RE = re.compile(
    r"^\s*\d+\.\s*\S+\s+(?:\d+\s+)?(?:\[new\]\s*)?(.+?)\s*[-—]\s*dano:\s*(\d+)",
    re.IGNORECASE | re.MULTILINE)


def parse_recompensas(text: str):
    """Lê TODOS os blocos 'Recompensas (vs X): ...' presentes no texto.
    Retorna lista de {"monstro": str, "jogadores": [{"nome","gold","xp","item","raridade"}]}.
    'item' é None quando a linha diz 'Nenhum item'. 'raridade' vem da
    bolinha colorida antes do item (ver EMOJI_RARIDADE, definido mais abaixo
    mas resolvido em tempo de execução) — cada SALA de uma masmorra de
    várias salas (ex: Pirâmide do Deserto) tem seu próprio drop com sua
    própria cor, mostrado na passagem pra sala seguinte, então essa raridade
    precisa ser capturada AQUI (não só na tela final do chefe)."""
    blocos = []
    linhas = (text or "").splitlines()
    i = 0
    while i < len(linhas):
        m = RECOMPENSA_HEADER_RE.search(linhas[i])
        if not m:
            i += 1
            continue
        monstro = m.group(1).strip()
        jogadores = []
        j = i + 1
        while j < len(linhas):
            lm = RECOMPENSA_LINHA_RE.search(linhas[j])
            if not lm:
                break
            nome = lm.group(1).strip()
            gold = int(lm.group(2))
            xp = int(lm.group(3))
            resto_bruto = (lm.group(4) or "").strip()
            raridade = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in resto_bruto), None)
            resto = resto_bruto.lstrip("⚪•· ").strip()
            for emoji in EMOJI_RARIDADE:   # tira o emoji de raridade do nome do item
                resto = resto.replace(emoji, "").strip()
            # BUG REAL corrigido (print do usuário 2026-07-15: 'Arco de Caça'
            # e outros aparecendo DUPLICADOS no banco de itens): sobrava um
            # emoji de CLASSE da arma (🏹🗡️🪓📖✨...) colado no início do nome
            # nessa fonte de captura, e só ali — a tela de Vender já limpava
            # isso certinho (_item_venda_info), então o mesmo item virava 2
            # chaves diferentes no banco ('Arco de Caça' e '🏹Arco de Caça').
            # Tira qualquer caractere que não seja letra/número do começo.
            resto = re.sub(r"^[^\wÀ-ÿ]+", "", resto).strip()
            item = None if not resto or "nenhum item" in norm(resto) else resto
            jogadores.append({"nome": nome, "gold": gold, "xp": xp, "item": item,
                              "raridade": raridade if item else None})
            j += 1
        if jogadores:
            blocos.append({"monstro": monstro, "jogadores": jogadores})
        i = j if j > i else i + 1
    return blocos


def _recompensa_hash(bloco) -> str:
    """Assinatura estável de um bloco de recompensa, pra não contar 2x o
    mesmo evento enquanto ele continuar visível no log em rodadas seguintes."""
    partes = [bloco["monstro"]] + [
        f'{j["nome"]}:{j["gold"]}:{j["xp"]}:{j["item"]}' for j in bloco["jogadores"]
    ]
    return hashlib.md5("|".join(partes).encode("utf-8")).hexdigest()


def atualizar_recompensas(shared, text: str) -> None:
    """Acumula em shared['acumulado'] os blocos de recompensa NOVOS (ainda
    não vistos) encontrados neste texto. Chamado a cada refresh do combate,
    por qualquer uma das 4 contas — o dedup é por hash, então não importa
    quantas contas leiam o mesmo bloco. Também acumula em
    shared['raridades_recompensas'] a cor de cada item visto em QUALQUER
    sala (não só a tela final do chefe) — confirmado pelo usuário 2026-07-15:
    cada sala de uma masmorra de várias salas (Pirâmide do Deserto) tem seu
    próprio drop colorido, mostrado na passagem pra sala seguinte."""
    vistos = shared.setdefault("recompensas_vistas", set())
    acumulado = shared.setdefault("acumulado", {"xp_total": 0, "jogadores": {}})
    raridades = shared.setdefault("raridades_recompensas", {})
    for bloco in parse_recompensas(text):
        h = _recompensa_hash(bloco)
        if h in vistos:
            continue
        vistos.add(h)
        if bloco["jogadores"]:
            # XP é a mesma recompensa pra todo mundo (é da masmorra, não de
            # quem bateu mais) — conta uma vez só por mob, não 4x.
            acumulado["xp_total"] += bloco["jogadores"][0]["xp"]
        for j in bloco["jogadores"]:
            pj = acumulado["jogadores"].setdefault(j["nome"], {"gold": 0, "drops": []})
            pj["gold"] += j["gold"]
            if j["item"]:
                pj["drops"].append(j["item"])
                if j.get("raridade"):
                    raridades[j["item"]] = j["raridade"]


RESUMO_CACA_HEADER_RE = re.compile(r"resumo da cacada em dupla", re.IGNORECASE)
RESUMO_CACA_XP_RE = re.compile(r"xp recebido:\s*([\d.,]+)", re.IGNORECASE)
RESUMO_CACA_GOLD_RE = re.compile(r"gold recebido:\s*([\d.,]+)", re.IGNORECASE)

# Cor da bolinha que o jogo mostra na frente de cada item = raridade dele.
# Confirmado por print do usuário 2026-07-06 (a mesma bolinha aparece em
# consumíveis E equipamentos — a cor é da RARIDADE do item, não do "tipo").
EMOJI_RARIDADE = {
    "🟢": "normal", "🔵": "incomum", "🟣": "raro", "🟡": "epico", "🟠": "lendario",
}
EMOJI_POR_RARIDADE = {v: k for k, v in EMOJI_RARIDADE.items()}   # "lendario" -> "🟠"


def _registrar_itens_no_banco(dados: dict, raridades: dict, origem: str = None) -> None:
    """Base de itens que cresce SOZINHA (pedido do usuário 2026-07-15: "não
    era pra ser manual, era pra ir adicionando conforme vai vendo os itens
    sendo dropado") — grava em dados['banco_itens'] cada item novo visto
    dropando (via a bolinha de cor), sem precisar cadastrar nada na mão
    (isso é diferente/além do config.ITENS_RARIDADE, que é só um catálogo
    manual opcional). Guarda, por item:
      raridade: a última cor vista pra esse item (ex: 'lendario')
      emoji: a bolinha correspondente (🟠), pra já vir pronta pra exibir
      primeira_vez: data/hora da 1ª vez que esse item foi visto
      vezes_visto: contador, incrementado a cada aparição
      origens: lista dos conteúdos onde esse item já apareceu (ex:
               'masmorra:Deserto Escaldante', 'templo_oasis') — sem repetir
    Mantém também dados['raridades'] (nome -> cor simples) pra não quebrar
    nada que já lia dali."""
    if not raridades:
        return
    banco = dados.setdefault("banco_itens", {})
    agora_txt = datetime.now().strftime("%d/%m/%Y %H:%M")
    for nome, raridade in raridades.items():
        entry = banco.setdefault(nome, {"raridade": raridade,
                                        "emoji": EMOJI_POR_RARIDADE.get(raridade, ""),
                                        "primeira_vez": agora_txt, "vezes_visto": 0,
                                        "origens": []})
        entry["raridade"] = raridade
        entry["emoji"] = EMOJI_POR_RARIDADE.get(raridade, entry.get("emoji", ""))
        entry["vezes_visto"] = entry.get("vezes_visto", 0) + 1
        if origem:
            origens = entry.setdefault("origens", [])
            if origem not in origens:
                origens.append(origem)
    dados.setdefault("raridades", {}).update(raridades)


LOOT_FINAL_HEADER_RE = re.compile(r"loot do boss final|dungeon conclu", re.IGNORECASE)
LOOT_FINAL_JOGADOR_RE = re.compile(r"^👤\s*(.+)$")
LOOT_FINAL_GOLD_XP_RE = re.compile(
    r"([\d.,]+)\s*gold.*?([\d.,]+)\s*xp", re.IGNORECASE)


def parse_loot_final_masmorra(text: str):
    """Lê a tela final '🏆 Dungeon concluída! / 🎁 Loot do Boss Final: ...'
    (confirmado por print do usuário 2026-07-11) — mostra ouro/XP/drops de
    CADA jogador de forma confiável e completa, ao contrário dos blocos
    transitórios 'Recompensas (vs Mob):' que o bot tentava capturar durante
    o combate (e que podiam se perder se viessem como mensagem avulsa sem
    botão — ver Session.texto_recompensas). Bolinha colorida antes do item =
    raridade, mesmo padrão já usado em parse_resumo_caca (Caçada em Dupla).
    Formato (um bloco por jogador, começando em '👤'):
        👤 35 {ODT} Borges
        💰 756 gold ⭐ 5625 XP
        🟢 Poção de Vida x3
        🟣 Chave de Masmorra
        🟠 Hórus
    Retorna {"xp_total", "jogadores": {nome: {"gold", "drops":[...]}},
    "raridades": {item: raridade}}, ou None se a tela não for essa."""
    t = text or ""
    if not LOOT_FINAL_HEADER_RE.search(norm(t)):
        return None
    linhas = t.splitlines()
    jogadores = {}
    raridades = {}
    xp_total = 0
    nome_atual = None
    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue
        if "ranking de dano" in norm(linha):
            break
        m_nome = LOOT_FINAL_JOGADOR_RE.match(linha)
        if m_nome:
            # tira emojis/ícone de papel (🏹/🛡/etc.) do começo, fica só o nome.
            nome_atual = re.sub(r"^[^\wÀ-ÿ\[\{]+", "", m_nome.group(1)).strip()
            # BUG REAL corrigido (relatado pelo usuário — "por personagem"
            # duplicado no Relatório): tira TAMBÉM o nível ("35 ") e a tag de
            # clã/grupo ("[NEW] "/"{ODT} ") que vêm juntos, senão a MESMA
            # pessoa vira uma entrada NOVA toda vez que sobe de nível (ex:
            # "37 Dona Irene" e "38 Dona Irene" contavam como 2 pessoas
            # diferentes no resumo diário).
            nome_atual = re.sub(r"^\d+\s*(?:[\[{][^\]}]*[\]}]\s*)?", "", nome_atual).strip()
            if nome_atual:
                jogadores.setdefault(nome_atual, {"gold": 0, "drops": []})
            continue
        if not nome_atual:
            continue
        m_gx = LOOT_FINAL_GOLD_XP_RE.search(norm(linha))
        if m_gx and ("gold" in norm(linha)) and ("xp" in norm(linha)):
            try:
                gold = int(re.sub(r"[.,]", "", m_gx.group(1)))
                xp = int(re.sub(r"[.,]", "", m_gx.group(2)))
                jogadores[nome_atual]["gold"] += gold
                xp_total = max(xp_total, xp)   # XP é o mesmo pra todo mundo na masmorra
            except ValueError:
                pass
            continue
        cor = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in linha), None)
        if cor:
            mi = re.match(r"^[^\wÀ-ÿ]*(.+?)(?:\s*x\s*(\d+))?$", linha, re.IGNORECASE)
            if mi:
                item = mi.group(1).strip()
                if item:
                    qtd = int(mi.group(2)) if mi.group(2) else 1
                    jogadores[nome_atual]["drops"].extend([item] * qtd)
                    raridades[item] = cor
    if not jogadores:
        return None
    return {"xp_total": xp_total, "jogadores": jogadores, "raridades": raridades}


TEMPLO_XP_RE = re.compile(r"([\d.,]+)\s*xp", re.IGNORECASE)
TEMPLO_GOLD_RE = re.compile(r"([\d.,]+)\s*gold", re.IGNORECASE)


def parse_loot_final_templo(text: str):
    """Lê a tela final '🌞 Templo do Oásis — Vitória!' (📊 Resultados: 👤
    Nome: ⭐ X XP | 💰 Y Gold, 🎁 Item) — mostra XP/Gold/drop de CADA
    jogador de forma confiável, igual ao 'Loot do Boss Final' da Masmorra
    normal (parse_loot_final_masmorra acima), mas com um formato DIFERENTE
    (confirmado por print do usuário 2026-07-14):
      - ordem XP/Gold invertida ('⭐ XP | 💰 Gold', não '💰 gold ⭐ xp' —
        por isso usa 2 regex independentes, sem exigir ordem nenhuma)
      - o nome do jogador não tem nível na frente, só a tag opcional e
        termina com ':' (ex: '👤 [AKT]Rudy SilveiraJr:')
      - o drop usa o emoji 🎁, sem bolinha de raridade colorida — mas o
        Templo do Oásis SÓ dropa item LENDÁRIO (confirmado pelo usuário),
        então aqui já marca direto como "lendario", sem depender de
        aprender a cor em outro lugar (o item pode nunca aparecer com
        bolinha em lugar nenhum, já que só dropa aqui)
    Retorna {"xp_total", "jogadores": {nome: {"gold", "drops":[...]}},
    "raridades": {item: "lendario"}}, ou None se a tela não for essa."""
    t = text or ""
    if not ("templo do oasis" in norm(t) and "vitoria" in norm(t)):
        return None
    linhas = t.splitlines()
    jogadores = {}
    raridades = {}
    xp_total = 0
    nome_atual = None
    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue
        if "ranking de dano" in norm(linha):
            break
        m_nome = LOOT_FINAL_JOGADOR_RE.match(linha)
        if m_nome:
            nome_atual = re.sub(r"^[^\wÀ-ÿ\[\{]+", "", m_nome.group(1)).strip()
            # mesma limpeza da Masmorra (nível+tag), MAIS o ':' final que
            # esse formato deixa no fim do nome ('...SilveiraJr:'). AQUI o
            # '\d*' é OPCIONAL (em vez de exigir '\d+'): esse formato do
            # Templo não mostra o nível antes da tag do clã (ex:
            # '[AKT]Rudy SilveiraJr:', sem número na frente) — exigir o
            # dígito deixava a tag colada no nome e quebrava o mapeamento
            # pro apelido da conta (_mapear_nomes_para_conta).
            nome_atual = re.sub(r"^\d*\s*(?:[\[{][^\]}]*[\]}]\s*)?", "", nome_atual).strip()
            nome_atual = nome_atual.rstrip(":").strip()
            if nome_atual:
                jogadores.setdefault(nome_atual, {"gold": 0, "drops": []})
            continue
        if not nome_atual:
            continue
        nl = norm(linha)
        m_xp = TEMPLO_XP_RE.search(nl)
        m_gold = TEMPLO_GOLD_RE.search(nl)
        if m_xp or m_gold:
            if m_gold:
                try:
                    jogadores[nome_atual]["gold"] += int(re.sub(r"[.,]", "", m_gold.group(1)))
                except ValueError:
                    pass
            if m_xp:
                try:
                    xp_total = max(xp_total, int(re.sub(r"[.,]", "", m_xp.group(1))))
                except ValueError:
                    pass
            continue
        if "🎁" in linha:
            # O Templo do Oásis SÓ dropa item LENDÁRIO (confirmado pelo
            # usuário) — e essa tela nunca mostra a bolinha de cor (só o
            # 🎁), então marca direto como lendário aqui, sem depender de
            # aprender a raridade em outro lugar (o item pode NUNCA
            # aparecer com bolinha em lugar nenhum, já que só dropa aqui).
            item = re.sub(r"^[^\wÀ-ÿ]*", "", linha).strip()
            if item:
                jogadores[nome_atual]["drops"].append(item)
                raridades[item] = "lendario"
            continue
        cor = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in linha), None)
        if cor:
            mi = re.match(r"^[^\wÀ-ÿ]*(.+?)(?:\s*x\s*(\d+))?$", linha, re.IGNORECASE)
            if mi:
                item = mi.group(1).strip()
                if item:
                    qtd = int(mi.group(2)) if mi.group(2) else 1
                    jogadores[nome_atual]["drops"].extend([item] * qtd)
                    raridades[item] = cor
    if not jogadores:
        return None
    return {"xp_total": xp_total, "jogadores": jogadores, "raridades": raridades}


def parse_resumo_caca(text: str):
    """Lê a tela '🏆 RESUMO DA CAÇADA EM DUPLA' que aparece ao SAIR da caçada.
    IMPORTANTE: cada CONTA vê o SEU PRÓPRIO resumo — o XP é igual pras duas,
    mas o Gold varia um pouco e os DROPS são individuais. Formato:
        XP recebido: 232,123 XP
        Gold recebido: 136,032
        Drops:
        🟢 Poção de Vida ×5
        🔵 Tônico de Precisão ×2
        🟣 Minério do Dragão ✦✦
        ...
    A bolinha colorida na frente de cada item é a RARIDADE dele (aprendida
    automaticamente aqui — não precisa cadastrar item por item na mão).
    Retorna {"xp_total", "gold_total", "drops": [itens, repetidos pela
    quantidade], "raridades": {item: "normal"/"incomum"/"raro"/"epico"/
    "lendario"}}, ou None se a tela não for essa."""
    t = text or ""
    if not RESUMO_CACA_HEADER_RE.search(norm(t)):
        return None

    def _num(m):
        return int(re.sub(r"[.,]", "", m.group(1))) if m else 0

    xp_total = _num(RESUMO_CACA_XP_RE.search(t))
    gold_total = _num(RESUMO_CACA_GOLD_RE.search(t))

    drops = []
    raridades = {}
    capturando = False
    for linha in t.splitlines():
        l = linha.strip()
        if not capturando:
            if "drops" in norm(l):
                capturando = True
            continue
        if not l or "criar nova" in norm(l) or "menu" in norm(l):
            break
        # a bolinha de cor vem ANTES do nome — pega ela pra saber a raridade,
        # depois limpa (não deixa nenhum símbolo/emoji sobrando no nome).
        cor_raridade = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in l), None)
        m = re.match(r"^[^\wÀ-ÿ]*(.+?)(?:\s*×\s*(\d+))?$", l)
        if not m:
            continue
        nome = m.group(1).strip()
        if not nome:
            continue
        qtd = int(m.group(2)) if m.group(2) else 1
        drops.extend([nome] * qtd)
        if cor_raridade:
            raridades[nome] = cor_raridade

    return {"xp_total": xp_total, "gold_total": gold_total, "drops": drops, "raridades": raridades}


def parse_ranking_dano(text: str) -> dict:
    """Lê o 'Ranking de dano' da tela final -> {nome: dano}."""
    return {m.group(1).strip(): int(m.group(2)) for m in RANKING_LINHA_RE.finditer(text or "")}


# ---------------------------------------------------------------------
#  Botões
# ---------------------------------------------------------------------

def iter_buttons(message):
    if not message or not message.buttons:
        return
    for row in message.buttons:
        for b in row:
            yield b


def button_texts(message):
    return [b.text for b in iter_buttons(message)]


def find_button(message, *substrings):
    wanted = [norm(s) for s in substrings]
    for b in iter_buttons(message):
        bt = norm(b.text)
        if any(w in bt for w in wanted):
            return b
    return None


# Recarga de alma NA MASMORRA aparece como "4t", "2t" (turnos), às vezes com
# um relógio "🕐". Fora da masmorra pode ser "(CD: N)". Cobrimos os dois.
SOUL_CD_RE = re.compile(r"\d+\s*t\b")


def soul_on_cooldown(bt_norm: str) -> bool:
    return ("(cd" in bt_norm
            or "🕐" in bt_norm
            or "⏳" in bt_norm
            or bool(SOUL_CD_RE.search(bt_norm)))


def find_soul_button(message, soul_name):
    """Botão de uma alma PRONTA (nome bate e NÃO está em recarga)."""
    alvo = norm(soul_name)
    for b in iter_buttons(message):
        bt = norm(b.text)
        if alvo in bt and not soul_on_cooldown(bt):
            return b
    return None


SOUL_CD_NUM_RE = re.compile(r"(\d+)\s*t\b")


def soul_cd_remaining(message, soul_name):
    """
    Turnos de recarga da alma LIDOS DA TELA (menu de Almas aberto):
      0    -> pronta
      N    -> faltam N turnos
      None -> botão não encontrado
    """
    alvo = norm(soul_name)
    for b in iter_buttons(message):
        bt = norm(b.text)
        if alvo in bt:
            m = SOUL_CD_NUM_RE.search(bt)
            if m:
                return int(m.group(1))
            m2 = re.search(r"cd:\s*(\d+)", bt)   # formato alternativo "(CD: N)"
            if m2:
                return int(m2.group(1))
            return 0
    return None


# ---------------------------------------------------------------------
#  poll_sleep(): espera entre uma consulta e outra à API (get_messages),
#  com JITTER (variação aleatória) em vez de um POLL_INTERVAL fixo.
# ---------------------------------------------------------------------

def _hhmm_para_minutos(hhmm: str):
    """Converte 'HH:MM' em minutos desde meia-noite, ou None se inválido."""
    try:
        h, m = map(int, (hhmm or "").strip().split(":"))
        return h * 60 + m
    except Exception:
        return None


def _dentro_da_janela_manutencao() -> bool:
    """True se o horário ATUAL (local) está dentro da janela de manutenção
    configurada (config.MANUTENCAO_INICIO/FIM, 'HH:MM'). Se o fim for MENOR
    que o início, entende que a janela passa da meia-noite (ex: 23:30 ->
    00:30 cobre 23:30-23:59 E 00:00-00:30). Sempre False se
    config.MANUTENCAO_ATIVA = False."""
    if not getattr(config, "MANUTENCAO_ATIVA", False):
        return False
    ini = _hhmm_para_minutos(getattr(config, "MANUTENCAO_INICIO", ""))
    fim = _hhmm_para_minutos(getattr(config, "MANUTENCAO_FIM", ""))
    if ini is None or fim is None:
        return False
    agora = datetime.now()
    agora_min = agora.hour * 60 + agora.minute
    if ini <= fim:
        return ini <= agora_min < fim
    return agora_min >= ini or agora_min < fim


_aviso_manutencao_logado = False


async def aguardar_fim_manutencao():
    """Se estivermos DENTRO da janela de manutenção configurada, ESPERA (sem
    clicar em nada) até ela passar — e só então devolve o controle pra quem
    chamou. Chamada tanto no início do main() (pega o caso de já iniciar
    dentro da janela) quanto de dentro do poll_sleep() (pega o caso de a
    manutenção COMEÇAR com o bot já rodando — nesse caso ele só "congela"
    ali mesmo até passar; como o próprio jogo fica fora do ar nesse
    intervalo, não haveria clique nenhum acontecendo de qualquer jeito)."""
    global _aviso_manutencao_logado
    if not _dentro_da_janela_manutencao():
        _aviso_manutencao_logado = False
        return
    if not _aviso_manutencao_logado:
        _aviso_manutencao_logado = True
        log("bot", f"🛠️ dentro da janela de manutenção configurada "
                   f"({config.MANUTENCAO_INICIO}–{config.MANUTENCAO_FIM}) — "
                   f"pausando sozinho até acabar, sem precisar reiniciar.")
    while _dentro_da_janela_manutencao():
        await asyncio.sleep(60)
    log("bot", "✅ janela de manutenção passou — voltando a jogar normalmente.")


def _media_segundos_relatorio(chave_tempo: str):
    """Lê a média de duração (segundos) das últimas execuções dessa chave,
    gravada em relatorio.json a cada conclusão (ver _atualizar_tempo_medio).
    None se ainda não há dado nenhum pra essa chave (ex: 1ª execução)."""
    try:
        lst = (_ler_relatorio().get("tempo_medio") or {}).get(chave_tempo)
    except Exception:
        return None
    if not lst:
        return None
    return sum(lst) / len(lst)


def _minutos_ate_proximo_inicio_manutencao():
    """Quantos minutos faltam pro PRÓXIMO início da janela de manutenção
    (0.0 se JÁ estamos dentro dela agora). None se a pausa de manutenção
    estiver desativada ou mal configurada."""
    if not getattr(config, "MANUTENCAO_ATIVA", False):
        return None
    ini = _hhmm_para_minutos(getattr(config, "MANUTENCAO_INICIO", ""))
    if ini is None:
        return None
    if _dentro_da_janela_manutencao():
        return 0.0
    agora = datetime.now()
    agora_min = agora.hour * 60 + agora.minute + agora.second / 60
    return (ini - agora_min) % 1440


async def evitar_novo_conteudo_por_manutencao(chave_tempo: str, rotulo: str = "") -> None:
    """Se a pausa de manutenção estiver ativa e o tempo até ela COMEÇAR for
    MENOR que a duração MÉDIA da última execução desse conteúdo (histórico
    em relatorio.json — ver _media_segundos_relatorio), NÃO deixa começar
    um(a) novo(a): espera a janela de manutenção passar antes, sem
    interromper nada que já esteja rodando (chamada só depois de concluir o
    conteúdo atual, antes de decidir formar/entrar num novo). Sem histórico
    ainda pra essa chave, não dá pra estimar — segue normal."""
    minutos_ate = _minutos_ate_proximo_inicio_manutencao()
    if minutos_ate is None:
        return
    media = _media_segundos_relatorio(chave_tempo)
    if media is None:
        return
    if minutos_ate * 60 > media:
        return
    log("bot", f"🛠️ faltam ~{minutos_ate:.0f}min pra manutenção e a média de "
               f"'{rotulo or chave_tempo}' é ~{media / 60:.0f}min — não vou começar "
               f"um(a) novo(a) agora, esperando a janela de manutenção passar…")
    while not _dentro_da_janela_manutencao():
        await asyncio.sleep(min(60, max(1, minutos_ate * 60)))
        minutos_ate = _minutos_ate_proximo_inicio_manutencao() or 0
    await aguardar_fim_manutencao()


async def poll_sleep(extra: float = 0.0):
    """Dorme ~POLL_INTERVAL segundos antes da próxima consulta à API.
    SEM jitter, com N contas rodando em paralelo (cada uma dormindo o MESMO
    tempo fixo), todas acordam no MESMO instante e disparam um PICO de N
    chamadas simultâneas ao Telegram — é esse pico, não a média de chamadas/
    segundo, que dispara FloodWait com frequência (visto na prática: 4-5
    contas, mesmo com POLL_INTERVAL já calibrado pra essa quantidade, ainda
    tomando FloodWait direto). Somando um atraso ALEATÓRIO (± POLL_JITTER)
    a cada ciclo, cada conta "desalinha" da hora das outras, espalhando as
    chamadas ao longo do tempo em vez de todas juntas.
    'extra': segundos a mais pra somar nesse sleep específico, se precisar
    (ex: esperar mais depois de um erro, sem mudar o padrão global)."""
    await aguardar_fim_manutencao()
    jitter = random.uniform(-config.POLL_JITTER, config.POLL_JITTER)
    espera = max(0.05, config.POLL_INTERVAL + jitter + extra)
    await asyncio.sleep(espera)


# ---------------------------------------------------------------------
#  Sessão de uma conta (clicar + esperar o bot redesenhar)
# ---------------------------------------------------------------------

class Session:
    def __init__(self, client, bot, acc):
        self.client = client
        self.bot = bot
        self.acc = acc
        self.name = acc["name"]
        self.role = acc["role"]
        self.char = acc["char_name"]
        self.souls = config.resolve_souls(self.role, acc.get("souls"))
        self.message = None
        self.pocoes_estimadas = None   # estoque de Poção de Vida rastreado na caçada
        self.pocao_minima_caca = 0     # limite pra sair da caçada (0 = não checa)
        self.sair_caca_pocao = False   # marcado ao beber poção e cair abaixo do limite
        self.modo_caca = False         # True na Caçada: cura só por HP baixo (sem tank)
        self.caca_vida_ratio = 0.0     # HP (0-1) abaixo do qual bebe poção na caçada
        self.caca_reforco_ratio = 0.0  # HP (0-1) do reforço de início na caçada (0=off)
        self.tank_alma_ratio = 0.60    # HP (0-1) abaixo do qual o TANK usa alma na caçada
        self.tonico = (acc.get("tonico") or "")   # "forca" / "defesa" / "" (nenhum)
        self._prox_tonico = 0.0        # próximo time.time() de usar o tônico (0 = na 1ª vez)
        self.elixir = (acc.get("elixir") or "")   # "" / "normal" / "super" (Elixir de Sabedoria)
        self._prox_elixir = 0.0        # próximo time.time() de usar o elixir (0 = na 1ª vez)
        # Dict de "batimento" de combate (shared da masmorra / estado da dupla /
        # da cripta). Enquanto está EM COMBATE, cada leitura de tela publica o
        # horário em _combat_hb["em_combate"][self.name] — assim uma rodada com
        # navegação de menu (Almas/Consumíveis) NÃO parece "travada" pras
        # outras contas (antes só atualizava 1x por volta do loop principal,
        # não a cada refresh — detecção de trava mais lenta/grossa).
        # None = fora de combate (formação/menu): não publica nada.
        self._combat_hb = None

    def _bump_heartbeat(self):
        """Publica o horário atual como 'estou vivo e em combate' — chamado a
        CADA leitura de tela enquanto _combat_hb está ligado E a tela é de
        combate. Sem custo perceptível (uma escrita em dict) e sem afetar a
        velocidade. Fora de combate (ou em submenu) não faz nada."""
        hb = self._combat_hb
        if hb is not None and is_combat_screen(self.message):
            hb.setdefault("em_combate", {})[self.name] = time.time()

    async def refresh(self):
        _t0 = time.time()
        msgs = await self.client.get_messages(self.bot, limit=8)
        _dt = time.time() - _t0
        if _dt > 2.0:
            # medição DIRETA (não depende do logger interno do Telethon, que
            # pode estar num nome de logger diferente da versão instalada) —
            # se isso aparecer com frequência, a lentidão é mesmo a CHAMADA à
            # API do Telegram demorando (rede/limite), não o jogo em si.
            log(self.name, f"🐢 get_messages demorou {_dt:.1f}s (API do Telegram lenta/limitada).")
        # Guarda o texto de TODAS as últimas 8 mensagens (não só a "oficial"
        # com botão que vira self.message/self.text) — o jogo às vezes manda
        # o aviso de 'Recompensas (vs Mob): ...' como mensagem AVULSA, SEM
        # botão, que fica pra trás assim que a mensagem principal do combate
        # é editada de novo. Sem isso, esses avisos eram perdidos silenciosamente
        # (visto de verdade: itens dropando no jogo que sumiam do relatório).
        self._msgs_recentes_texto = "\n".join(
            (m.message or "") for m in msgs if getattr(m, "message", None))
        for m in msgs:
            if m.buttons:
                self.message = m
                self._bump_heartbeat()
                return m
        if msgs:
            self.message = msgs[0]
        self._bump_heartbeat()
        return self.message

    @property
    def texto_recompensas(self):
        """Texto de busca pra recompensas: combina a tela 'oficial' atual
        (self.text) com as últimas mensagens avulsas recebidas (ver
        refresh()), pra não perder um 'Recompensas (vs Mob): ...' que veio
        como mensagem separada sem botão."""
        extra = getattr(self, "_msgs_recentes_texto", "") or ""
        base = self.text
        return f"{base}\n{extra}" if extra and extra not in base else (extra or base)

    def _sig(self, m):
        if m is None:
            return None
        e = getattr(m, "edit_date", None)
        return (m.id, e.timestamp() if e else None, m.message)

    async def wait_change(self, before_sig, timeout=None):
        timeout = timeout if timeout is not None else config.UPDATE_TIMEOUT
        deadline = asyncio.get_event_loop().time() + timeout
        # 1ª checagem IMEDIATA (o click já esperou ACTION_DELAY): na maioria das
        # vezes a tela JÁ mudou — evita perder um POLL_INTERVAL inteiro à toa.
        m = await self.refresh()
        if self._sig(m) != before_sig:
            return m
        while asyncio.get_event_loop().time() < deadline:
            await poll_sleep()
            m = await self.refresh()
            if self._sig(m) != before_sig:
                return m
        return self.message

    async def click(self, button, label="", timeout=None):
        before = self._sig(self.message)
        try:
            await button.click()
        except Exception as e:
            # "Encrypted data invalid" = o botão é de uma mensagem VELHA
            # (callback expirado, comum logo após um reinício). Atualiza a
            # tela na hora pra o chamador reavaliar o estado REAL, em vez de
            # seguir agindo sobre a mensagem antiga em cache.
            log(self.name, f"❌ erro ao clicar '{label or button.text}': {e}")
            try:
                await self.refresh()
            except Exception:
                pass
            return self.message
        await asyncio.sleep(config.ACTION_DELAY)
        return await self.wait_change(before, timeout=timeout)

    async def click_text(self, *subs, label="", required=True, timeout=None):
        b = find_button(self.message, *subs)
        if b is None:
            if required:
                log(self.name,
                    f"❌ botão {subs} não achado.\n"
                    f"    texto: {self.message.message if self.message else '(vazio)'}\n"
                    f"    botões: {button_texts(self.message)}")
            return None
        return await self.click(b, label=label or subs[0], timeout=timeout)

    async def send_start(self):
        await self.client.send_message(self.bot, "/start")
        await asyncio.sleep(config.ACTION_DELAY)
        await self.refresh()

    async def send_text(self, texto: str):
        """Manda uma mensagem de texto qualquer pro bot (ex: digitar um código
        que não tem botão pronto na tela). Retorna (mudou, msg_enviada) —
        'msg_enviada' é a MENSAGEM NOSSA de verdade (a conta pode apagá-la
        depois, ex: o código da sala, que senão fica pra sempre no histórico
        — pedido do usuário 2026-07-15, print mostrando o código '5A4CBF'
        acumulado na conversa)."""
        before = self._sig(self.message)
        msg_enviada = await self.client.send_message(self.bot, texto)
        await asyncio.sleep(config.ACTION_DELAY)
        mudou = await self.wait_change(before)
        return mudou, msg_enviada

    @property
    def text(self):
        return self.message.message if self.message else ""


# ---------------------------------------------------------------------
#  Digitar a senha no teclado (4 dígitos)
# ---------------------------------------------------------------------

async def type_password(s: Session, senha: str):
    """Clica os dígitos da senha no teclado numérico da tela.
    ESPERA o teclado carregar antes de digitar: depois de clicar 'Com senha',
    a tela às vezes ainda mostra 'Criar sala/Buscar salas' por um instante
    (bug real 2026-07-03: '❌ não achei a tecla 2' porque digitou cedo demais).
    Só desiste se o teclado não aparecer em ~6 refreshes."""
    prim = senha[0] if senha else "1"
    for _ in range(6):
        if find_button(s.message, prim):
            break
        await poll_sleep()
        await s.refresh()
    else:
        log(s.name, f"❌ teclado da senha não apareceu a tempo.\n"
                    f"    botões: {button_texts(s.message)}")
        return False
    log(s.name, f"Digitando senha ({len(senha)} dígitos).")
    for d in senha:
        btn = find_button(s.message, d)
        if btn is None:
            log(s.name, f"❌ não achei a tecla '{d}' no teclado.\n"
                        f"    botões: {button_texts(s.message)}")
            return False
        await s.click(btn, label=f"tecla {d}")
    return True


# ---------------------------------------------------------------------
#  Formação do grupo
# ---------------------------------------------------------------------

async def open_masmorra(s: Session):
    """
    Chega na tela de Masmorra (Criar/Buscar), lidando com o que estiver na
    frente: tela de conclusão (botão Menu), menu principal (botão Masmorra) ou
    estado intermediário. Com retry — robusto a timing.
    """
    for _ in range(5):
        await s.refresh()
        if find_button(s.message, "criar sala", "buscar salas"):
            return True
        mb = find_button(s.message, "menu")
        if mb:
            await s.click(mb, label="Menu")
            continue
        mm = find_button(s.message, "masmorra")
        if mm:
            await s.click(mm, label="Masmorra")
            continue
        if await _tentar_evitar_start(s):
            continue
        await s.send_start()
    return find_button(s.message, "criar sala", "buscar salas") is not None


async def host_create_room(s: Session, senha: str, abrir=None):
    """HOST: cria sala com senha e devolve o código lido do lobby.
    'abrir': função async(s)->bool que leva até a tela com "Criar sala"/
    "Buscar salas" — por padrão open_masmorra (Masmorra normal); o Templo do
    Oásis passa open_fenda_solar (mesma sala, tela diferente)."""
    abrir = abrir or open_masmorra
    if not await abrir(s):
        log(s.name, "❌ não cheguei na tela de criação de sala (host).")
        return None
    # Alguns mapas pedem escolher o TIPO de masmorra antes de criar a sala
    # ("Masmorra Normal", "Covil de Zul'gor", "Santuário de Altheryn", etc —
    # ver config.MASMORRAS_ALTERNATIVAS, fácil de somar mais no futuro). Se
    # essa tela aparecer, escolhe conforme config.TIPO_MASMORRA. Se não
    # aparecer (outros mapas sem essa escolha), segue direto pro "Criar
    # sala" de sempre.
    tipo = getattr(config, "TIPO_MASMORRA", "normal")
    alternativas = getattr(config, "MASMORRAS_ALTERNATIVAS", {})
    alt = alternativas.get(tipo)
    # 'botao' pode ser None (ex: Hidra Ancestral) — não tem tela de escolha
    # de sala nenhuma, é a Masmorra Normal de sempre; só a SKIN equipada
    # (checada à parte, antes de chegar aqui) decide o que a sala vira.
    b_alt = find_button(s.message, alt["botao"]) if (alt and alt.get("botao")) else None
    b_normal = find_button(s.message, "masmorra normal")
    if alt and b_alt:
        await s.click(b_alt, label=alt["rotulo"])
    elif b_normal:
        await s.click(b_normal, label="Masmorra Normal")
    # Em alguns mapas (ex: Planície/Zul'gor), escolher o tipo só MARCA a
    # sala — ainda precisa clicar "Criar sala" depois. Em outros (ex:
    # Floresta Sombria/Santuário de Altheryn), o próprio botão do tipo JÁ
    # cria a sala e cai direto em "Aberta"/"Com senha" — só clica "Criar
    # sala" se essa tela ainda aparecer.
    if find_button(s.message, "criar sala"):
        await s.click_text("criar sala", label="Criar sala")
    await s.click_text("com senha", label="Com senha")
    if not await type_password(s, senha):
        return None
    await s.refresh()
    code = find_room_code(s.text)
    if code:
        log(s.name, f"✅ sala criada. Código: {code}")
    else:
        log(s.name, f"⚠️ criei a sala mas não achei o código.\n    texto: {s.text}")
    return code


async def joiner_enter_room(s: Session, code: str, senha: str, abrir=None):
    """CONTA COMUM: entra na sala 'code' pela lista de Buscar salas + senha.
    'abrir': ver host_create_room acima."""
    abrir = abrir or open_masmorra
    if not await abrir(s):
        log(s.name, "❌ não cheguei na tela de criação de sala (join).")
        return False
    await s.click_text("buscar salas", label="Buscar salas")
    # procura, paginando com "Próximo" se preciso, o botão que contém o código
    for _ in range(10):
        alvo = find_button(s.message, code)
        if alvo:
            await s.click(alvo, label=f"sala {code}")
            break
        prox = find_button(s.message, "proximo", "próximo")
        if prox:
            await s.click(prox, label="Próximo")
        else:
            log(s.name, f"❌ não achei a sala {code} na lista.\n"
                        f"    botões: {button_texts(s.message)}")
            return False
    # pode aparecer o teclado de senha
    if find_button(s.message, "1") and find_button(s.message, "2"):
        if not await type_password(s, senha):
            return False
        await s.refresh()
    log(s.name, "✅ entrei na sala.")
    return True


# ---------------------------------------------------------------------
#  Templo do Oásis (Duo) — mesma sala/combate da Masmorra, tela diferente
# ---------------------------------------------------------------------

async def open_fenda_solar(s: Session) -> bool:
    """Chega na tela 'Templo do Oásis' (Criar Sala/Buscar Salas), dentro da
    Fenda Solar do mapa do Oásis: viaja pro mapa (se preciso) -> Menu ->
    Masmorra -> Fenda Solar -> 'Templo do Oásis (Grupo)'. Retry robusto a
    timing, no mesmo padrão de open_masmorra/open_cacar."""
    mapa = getattr(config, "MAPA_TEMPLO_OASIS", "") or "Oásis Perdido"
    if not await viajar_para(s, mapa):
        log(s.name, f"⚠️ não consegui confirmar viagem para '{mapa}' — "
                    f"seguindo mesmo assim (pode já estar lá).")
    for _ in range(7):
        await s.refresh()
        if find_button(s.message, "criar sala", "buscar salas"):
            return True
        # Presa numa sala/combate de uma execução anterior do Templo (ex:
        # o host já criou a sala, mas o joiner falhou em entrar e o loop
        # tentou formar tudo de novo — sem isso, o bot ficava sem achar
        # 'Criar sala'/'Buscar salas' e mandando /start à toa, em LOOP, o
        # mesmo bug já corrigido no open_cacar). Sai antes de seguir.
        if is_combat_screen(s.message) or is_lobby_screen(s.message) or find_button(s.message, "sair"):
            await leave_room(s)
            continue
        tg = find_button(s.message, "templo do oasis", "templo do oásis")
        if tg:
            await s.click(tg, label="Templo do Oásis (Grupo)")
            continue
        mb = find_button(s.message, "menu")
        if mb:
            await s.click(mb, label="Menu")
            continue
        mm = find_button(s.message, "masmorra")
        if mm:
            await s.click(mm, label="Masmorra")
            continue
        if await _tentar_evitar_start(s):
            continue
        await s.send_start()
    return find_button(s.message, "criar sala", "buscar salas") is not None


async def host_criar_templo(s: Session):
    """HOST: cria a sala do Templo do Oásis (Duo). DIFERENTE da Masmorra
    normal: aqui NÃO existe etapa de senha — clica só 'Criar Sala' e já cai
    direto no lobby (confirmado em produção 2026-07-10: o bot ficava preso
    tentando clicar 'Com senha'/digitar senha que nunca aparecem, e a sala já
    tinha sido criada). Como o lobby não mostra um código de sala visível,
    retorna o NOME DO PERSONAGEM do host — é por ele que o joiner encontra a
    sala certa na lista 'Buscar Salas' (rótulo tipo '1218D8 — [TAG]NomeHost
    (1/2)')."""
    if not await open_fenda_solar(s):
        log(s.name, "❌ não cheguei na tela do Templo do Oásis (host).")
        return None
    if not await s.click_text("criar sala", label="Criar sala"):
        return None
    await s.refresh()
    if not is_lobby_screen(s.message):
        log(s.name, f"⚠️ criei a sala do Templo do Oásis, mas a tela não parece "
                    f"o lobby esperado.\n    texto: {s.text}\n"
                    f"    botões: {button_texts(s.message)}")
    log(s.name, f"✅ sala do Templo do Oásis criada (host: {s.char}).")
    return s.char


async def joiner_entrar_templo(s: Session, host_char: str):
    """2ª CONTA: entra na sala do Templo do Oásis (Duo) criada por 'host_char'.
    SEM senha (ver host_criar_templo) — acha a sala procurando, na lista
    'Buscar Salas', o botão cujo rótulo contém o NOME DO PERSONAGEM do host
    (não há código de sala pra copiar aqui)."""
    if not await open_fenda_solar(s):
        log(s.name, "❌ não cheguei na tela do Templo do Oásis (join).")
        return False
    if not await s.click_text("buscar salas", label="Buscar salas"):
        return False
    for _ in range(10):
        await s.refresh()
        alvo = find_button(s.message, host_char)
        if alvo:
            await s.click(alvo, label=f"sala de {host_char}")
            break
        prox = find_button(s.message, "proximo", "próximo")
        if prox:
            await s.click(prox, label="Próximo")
        else:
            log(s.name, f"❌ não achei a sala de '{host_char}' na lista.\n"
                        f"    botões: {button_texts(s.message)}")
            return False
    else:
        log(s.name, f"❌ não achei a sala de '{host_char}' na lista (tentativas esgotadas).")
        return False
    log(s.name, f"✅ entrei na sala do Templo do Oásis (host: {host_char}).")
    return True


def lobby_ready_count(text: str) -> int:
    """Quantos membros já estão prontos (✅) no lobby."""
    return (text or "").count("✅")


async def ready_up(s: Session):
    await s.click_text("pronto", label="Pronto", required=True)


async def host_start(s: Session, n_expected: int):
    """HOST: espera todos prontos e clica Iniciar."""
    deadline = asyncio.get_event_loop().time() + config.LOBBY_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await s.refresh()
        ready = lobby_ready_count(s.text)
        if ready >= n_expected:
            log(s.name, f"Todos prontos ({ready}/{n_expected}). Iniciando!")
            await s.click_text("iniciar", label="Iniciar")
            return True
        log(s.name, f"Aguardando prontos... {ready}/{n_expected}")
        await asyncio.sleep(2.0)
    log(s.name, "⚠️ timeout esperando todos ficarem prontos.")
    return False


# ---------------------------------------------------------------------
#  Ações de combate (cada uma consome a ação da rodada)
# ---------------------------------------------------------------------

def _confirm_timeout(s: Session):
    """Na CAÇADA, o clique da AÇÃO (Atacar/Defender/alma/poção) volta rápido
    (não trava esperando a rodada resolver — o loop confirma pela ampulheta).
    Na masmorra, usa o timeout normal (None -> UPDATE_TIMEOUT)."""
    return config.ACTION_CONFIRM if getattr(s, "modo_caca", False) else None


async def act_defender(s: Session):
    log(s.name, "🛡️ Defender")
    r = await s.click_text("defender", label="Defender", timeout=_confirm_timeout(s))
    if r is None:
        # Mesma recuperação e mesmo motivo do act_atacar logo abaixo (BUG REAL
        # corrigido 2026-07-17): o tank pode ficar preso num submenu (ex:
        # 'Escolha uma alma') igualzinho quando a ação da rodada é Defender.
        log(s.name, "⚠️ 'Defender' não achado — provavelmente presa num "
                    "submenu (ex: 'Escolha uma alma'); voltando pro combate "
                    "antes da próxima tentativa.")
        await go_back(s)


async def act_atacar(s: Session):
    log(s.name, "⚔️ Atacar")
    r = await s.click_text("atacar", label="Atacar", timeout=_confirm_timeout(s))
    if r is None:
        # BUG REAL corrigido 2026-07-17 (relato do usuário: "o tank perdeu o
        # turno" — log real confirmou a causa: 8 tentativas seguidas, ~35s
        # de uma rodada de 45s, todas falhando com "botão ('atacar',) não
        # achado" porque a tela de VERDADE era 'Escolha uma alma' — sobrou
        # de uma tentativa de usar alma que não fechou o menu direito (ex:
        # erro transitório do Telegram tipo 'Encrypted data invalid' no meio
        # do fluxo). O go_back() já existia e já resolvia esse MESMO padrão
        # em outros pontos (dentro do fluxo de alma), mas não era chamado
        # aqui — então, uma vez preso, o bot só ficava repetindo 'Atacar'
        # contra um botão que não existe naquela tela até quase estourar o
        # tempo da rodada (mais 1-2 tentativas e perderia o turno de vez).
        # Agora, se 'Atacar' não foi achado, volta pro combate — a PRÓXIMA
        # tentativa (o combat_loop já reforça sozinho) parte da tela certa.
        log(s.name, "⚠️ 'Atacar' não achado — provavelmente presa num submenu "
                    "(ex: 'Escolha uma alma'); voltando pro combate antes da "
                    "próxima tentativa.")
        await go_back(s)


async def act_fugir(s: Session):
    """Foge do combate ATUAL (botão próprio na tela, junto com Atacar/
    Consumíveis/Almas — visto na Caçada Solo). Usado pro filtro de
    'monstros-alvo' da Caçada Solo: contra monstro que NÃO é um dos alvos
    configurados, foge em vez de gastar HP/tempo lutando à toa."""
    log(s.name, "🏃 Fugir")
    await s.click_text("fugir", label="Fugir", timeout=_confirm_timeout(s))


async def go_back(s: Session):
    """Volta de um submenu (Almas/Consumíveis) sem sair da masmorra. NUNCA
    clica 'Sair'. Tenta de novo (até 4x) se o clique falhar — ex: erro
    transitório do Telegram tipo 'Encrypted data invalid' (visto em log
    real). Sem essa confirmação, a conta ficava PRESA no submenu pra sempre,
    tentando ações que não existem ali (ex: 'Atacar' nunca encontrado porque
    a tela continuava sendo 'Escolha uma alma')."""
    for _ in range(4):
        b = find_button(s.message, "voltar", "atras", "⬅", "◀", "🔙")
        if not b:
            return   # já não tem botão de voltar -> já não está mais no submenu
        await s.click(b, label="voltar")
        await asyncio.sleep(config.ACTION_DELAY)
        await s.refresh()


async def leave_room(s: Session, tentativas: int = 6) -> bool:
    """Sai da sala ATUAL (a caçada em dupla, a masmorra, a cripta, o templo).
    NÃO tem nada a ver com o outro modo: só sai da sala em que a conta está
    agora. O MOTIVO da saída é logado por quem chama.

    BUG REAL corrigido (2 incidentes em produção, 2026-07-12): depois de
    clicar 'Sair', o jogo mostra uma tela EXTRA de confirmação ('⚠️
    CONFIRMAR SAÍDA — Você receberá todo o XP e Gold acumulados. Tem
    certeza?' com botões '✅ Sim, sair e receber...' / '❌ Cancelar'). O
    código antigo só tentava confirmar essa tela UMA vez, sem esperar ela
    carregar direito nem confirmar que a conta realmente saiu — em 2 casos
    reais isso deixou a conta PRESA nessa tela achando (o código) que já
    tinha saído: numa, a conta ficou exposta sozinha em combate e morreu;
    noutra, um '/start' foi mandado por cima dessa tela travada e bugou o
    estado do bot ainda mais.

    Agora RETRY até confirmar de verdade (o botão 'Sair' E a tela de
    confirmação terem sumido de vez, E o HP do próprio personagem não
    aparecer mais no grupo — sinal de que realmente não está mais na sala).
    Retorna True só quando tem certeza que saiu; False se não conseguiu
    confirmar depois de várias tentativas (quem chama NÃO deve assumir que
    já saiu nesse caso — mais seguro tentar de novo ou pausar do que seguir
    como se nada tivesse acontecido)."""
    log(s.name, "🚪 saindo da sala.")
    for tentativa in range(tentativas):
        await s.refresh()
        b = find_button(s.message, "sair")
        if b:
            await s.click(b, label="Sair")
            await asyncio.sleep(config.ACTION_DELAY)
            await s.refresh()
        # tela de confirmação: "✅ Sim, sair e receber..." / "❌ Cancelar"
        conf = find_button(s.message, "sim, sair", "sim sair")
        if conf:
            await s.click(conf, label="Sim, sair")
            await asyncio.sleep(config.ACTION_DELAY)
            await s.refresh()
        # CONFIRMA de verdade: nem o botão 'Sair', nem a tela de confirmação,
        # nem o próprio HP aparecendo mais no grupo (ainda dentro da sala).
        ainda_na_sala = (find_button(s.message, "sair") is not None
                        or find_button(s.message, "sim, sair", "sim sair") is not None
                        or player_hp(s.text, s.char) is not None)
        if not ainda_na_sala:
            return True
        await poll_sleep()
    log(s.name, "⚠️ não consegui confirmar que saí da sala depois de várias "
                "tentativas — pode ainda estar dentro. Não vou assumir que saí.")
    return False


async def use_soul_from_priority(s: Session, brain, priority, forcar: bool = False) -> bool:
    """
    priority = [(nome, recarga), ...]. Usa a primeira alma que ACREDITAMOS
    estar pronta (recarga rastreada na memória) E confirmamos na tela.
    Se acreditamos que NENHUMA está pronta, NEM abre o menu (retorna False,
    o chamador ataca/defende) — evita clique à toa e "perder a vez".

    REESCRITA (baseada numa versão de referência que roda bem na Cripta,
    comparada e adotada aqui): em vez de só clicar e TORCER que funcionou (e
    confiar num timeout externo pra desistir), essa versão CONFIRMA de
    verdade que a alma saiu (o menu 'Escolha uma alma' precisa SUMIR da tela)
    antes de considerar sucesso — e relê o botão FRESCO a cada tentativa (um
    botão "velho" da tela anterior o jogo ignora quando clicado). Em
    QUALQUER caso de desistência, sempre faz go_back() antes de retornar —
    nunca deixa a conta presa no submenu (era exatamente esse o bug: o clique
    "parecia" ter funcionado, o chamador tentava Atacar na rodada seguinte e
    não achava o botão porque a tela continuava sendo a de Almas).
    """
    if not priority:
        return False
    # Filtro de andar (só a Cripta/Dupla setam s.alma_min_andar>0) — vale
    # IGUAL pra TODO MUNDO, incluindo o tank (a pedido do usuário: antes o
    # tank ficava isento achando que precisava do Rugido/Escudo mesmo nos
    # andares fáceis, mas ele quer o controle total — se configurou "só usa
    # alma a partir do andar X", ninguém usa alma antes disso, sem exceção).
    # Andar desconhecido -> deixa usar (mais seguro que bloquear à toa).
    min_andar = getattr(s, "alma_min_andar", 0) or 0
    if min_andar > 0:
        andar = getattr(s, "_andar_atual", None)
        if andar is not None and andar < min_andar:
            # ANTES, esse bloqueio era 100% silencioso — se o "andar atual"
            # fosse lido errado por qualquer motivo (parser meio frágil,
            # pode pegar um número de parênteses errado na tela), a alma
            # simplesmente não era usada e NADA aparecia no log explicando
            # por quê. Agora loga (só 1x por conta, pra não spammar toda
            # rodada) — se isso disparar com um andar claramente errado
            # (ex: muito abaixo do que a dupla já alcançou de verdade), é
            # sinal de que o parser de andar está lendo algo errado.
            if getattr(s, "_log_andar_bloqueio_uma_vez", True):
                s._log_andar_bloqueio_uma_vez = False
                log(s.name, f"ℹ️ alma bloqueada por andar: atual={andar}, "
                            f"configurado 'a partir do andar {min_andar}' — "
                            f"não uso alma antes disso (esse aviso só aparece 1x).")
            return False
    # otimização: se acreditamos que NENHUMA está pronta, nem abre o menu —
    # EXCETO se 'forcar' pedir uma reconferência periódica (ver
    # RESYNC_ALMA_RODADAS/deve_forcar_resync_alma): sem isso, um desvio na
    # contagem interna de recarga pode travar o uso da alma pra sempre, já
    # que o menu só reabre (e só resincroniza a memória) quando a própria
    # memória já acredita que tem alguma pronta.
    if not forcar and not any(brain.believe_ready(name) for name, _cd in priority):
        return False
    # required=False: o botão 'Almas' pode não estar visível num instante de
    # transição de tela; nesse caso volta e ataca/defende (o loop re-tenta) em
    # vez de logar um "❌ botão não achado" que parece erro grave sem ser.
    if not await s.click_text("almas", label="Almas", required=False,
                              timeout=_confirm_timeout(s)):
        return False
    # Chegamos até aqui -> vamos MESMO conferir a tela agora (seja porque a
    # memória já acreditava, seja porque foi forçado). Zera o contador de
    # rodadas "no escuro", independente do resultado final desta chamada.
    brain.marcar_resync_alma()
    # RESSINCRONIZA a recarga pela tela (lê o "Nt" real) e escolhe a 1ª pronta.
    # Só considera pronta se a MEMÓRIA também acreditar — a tela às vezes
    # LISTA a alma mesmo em recarga (sem marca que o bot reconheça), e sem
    # esse "E" o bot clicava numa alma em recarga, o jogo recusava e o MENU
    # FICAVA ABERTO.
    cd_por_alma = dict(priority)
    escolhida = None
    for name, _cd in priority:
        rem = soul_cd_remaining(s.message, name)
        if rem is None:
            continue
        # CORRIGIDO: antes, só considerava a alma pronta se a CRENÇA antiga
        # TAMBÉM já achasse que sim (dupla exigência) — na prática, se a
        # crença estivesse atrasada (o motivo de todo esse bug), uma alma
        # REALMENTE pronta na tela era ignorada mesmo depois de abrir o menu
        # pra conferir, porque a crença antiga dizia "não". Agora a LEITURA
        # REAL DA TELA sempre resincroniza a memória primeiro — e a escolha
        # usa essa memória já corrigida, não a antiga.
        brain.set_ready_in(name, rem)
        if rem <= 0 and escolhida is None:
            escolhida = name
    if escolhida is None:
        await go_back(s)          # nenhuma realmente pronta -> volta pra bater
        return False
    cd = cd_por_alma.get(escolhida, 3)
    tela_pos = None
    # Clica a alma RELENDO o botão FRESCO a cada tentativa: a tela se
    # atualiza sozinha (timer/eventos), e o botão lido pode "envelhecer"
    # entre ler e clicar. 2 tentativas: a 1ª cobre o caso normal; a 2ª cobre
    # o botão que envelheceu. Quando a alma REALMENTE não vai (recarga que a
    # tela esconde), corta a retentativa e já parte pro ataque.
    for tentativa in range(2):
        b = find_soul_button(s.message, escolhida)
        if b is None:
            # o botão sumiu do menu: se o menu TAMBÉM fechou, a alma lançou
            # numa tentativa anterior -> sucesso. Se o menu ainda está
            # aberto, desiste desta alma.
            if "escolha uma alma" not in norm(s.text):
                brain.mark_used(escolhida, cd)
                return True
            break
        if tentativa == 0:
            log(s.name, f"✨ Alma: {b.text}")
        await s.click(b, label=b.text, timeout=_confirm_timeout(s))
        # 1) checa NA HORA, reaproveitando o refresh do click (caso comum).
        if "escolha uma alma" not in norm(s.text):
            brain.mark_used(escolhida, cd)
            return True                       # lançou de verdade
        # 2) ainda no menu -> a tela pode estar se auto-atualizando; dá um
        #    poll curto (2x) pra deixar assentar antes de re-clicar.
        fechou = False
        for _ in range(2):
            await poll_sleep()
            await s.refresh()
            if tela_pos is None:              # 1ª tela logo APÓS o 1º clique
                tela_pos = (s.text, button_texts(s.message))
            if "escolha uma alma" not in norm(s.text):
                fechou = True
                break
        if fechou:
            brain.mark_used(escolhida, cd)
            return True                       # lançou de verdade
        # ainda no menu -> re-lê o botão (fresco) e tenta de novo
    # BUG REAL corrigido (2026-07-12, relatado pelo usuário: "alma disponível
    # mas não é usada, ataca no lugar"): antes, aqui marcava a alma como
    # USADA com o cooldown CHEIO (cd rodadas) mesmo quando ela NUNCA lançou
    # de verdade (clique não confirmou — comum quando a conexão/Telegram
    # está instável, ver "Encrypted data invalid" no log). Resultado: a
    # alma ficava "gasta" na memória sem ter sido usada de verdade, e o bot
    # atacava no lugar (dano bem menor) até o cooldown FALSO expirar — o
    # personagem perdia várias rodadas de dano de alma à toa. Agora só
    # bloqueia por 1 rodada (mark_seen_on_cd), não o cooldown inteiro —
    # ainda evita ficar tentando a MESMA alma quebrada toda hora na mesma
    # rodada, mas tenta de novo bem mais cedo (na próxima rodada de
    # verdade) em vez de esperar o cooldown inteiro passar à toa.
    brain.mark_seen_on_cd(escolhida)
    log(s.name, f"⚠️ '{escolhida}' estava pronta mas não lançou (clique não confirmou) "
                f"— atacando normal desta vez, tenta de novo na próxima rodada.")
    if getattr(s, "_log_alma_uma_vez", True):
        s._log_alma_uma_vez = False
        log(s.name, "🔎 ALMA NÃO LANÇOU após as tentativas — tela após o clique:\n"
                    f"    TEXTO: {tela_pos[0] if tela_pos else '(?)'}\n"
                    f"    BOTÕES: {tela_pos[1] if tela_pos else '(?)'}")
    await go_back(s)
    return False


async def act_potion(s: Session) -> bool:
    """Abre Consumíveis e bebe a Poção de Vida se houver. True se usou.
    ROBUSTO ao card da caçada que se auto-atualiza (timer/rodada): procura o
    botão por algumas atualizações e, se preciso, entra em Inventário. Lê a
    CONTAGEM REAL do botão ('...x47') pra rastrear/checar o estoque na caçada."""
    if not await s.click_text("consumiveis", "consumíveis", label="Consumíveis"):
        return False
    pot = None
    for _ in range(6):
        await s.refresh()
        pot = find_button(s.message, "pocao de vida", "poção de vida")
        if pot:
            break
        inv = find_button(s.message, "inventario", "inventário")
        if inv:
            await s.click(inv, label="Inventário")
            continue
        cons = find_button(s.message, "consumiveis", "consumíveis")
        if cons:
            await s.click(cons, label="Consumíveis")
            continue
        await poll_sleep()
    if not pot:
        log(s.name, f"⚠️ sem Poção de Vida no estoque! botões: {button_texts(s.message)}")
        await go_back(s)
        # SEM POÇÃO DE VERDADE quando precisava — marca a saída segura (o
        # mesmo sinal que a checagem de estoque baixo já usa) pra Masmorra,
        # Caçada em Dupla e Cripta pararem tudo, em vez de continuar
        # atacando/defendendo sem conseguir se curar (pedido do usuário
        # 2026-07-09: nunca arriscar morrer por falta dessa checagem).
        s.sair_caca_pocao = True
        return False
    # contagem REAL lida do botão (ex 'Poção de Vida x47'); None se não vier
    m = POCAO_QTD_RE.search(norm(pot.text))
    qtd = int(m.group(1)) if m else None
    log(s.name, "💚 Poção de Vida" + (f" (tinha {qtd})" if qtd is not None else ""))
    # CONFIRMA que o HP subiu de verdade (BUG REAL corrigido 2026-07-12: sob
    # lag pesado da API do Telegram, o clique podia não registrar de verdade
    # (wait_change() estourava o timeout silenciosamente) e o código seguia
    # em frente achando que tinha curado — visto em produção: a mesma conta
    # ficou lendo o MESMO HP baixo por 4 tentativas seguidas de "beber
    # poção" sem o HP nunca subir). Agora tenta até 3 vezes, só desiste de
    # verdade se o HP realmente não mudar em NENHUMA tentativa.
    hp_antes = player_hp(s.text, s.char)
    for tentativa in range(3):
        await s.click(pot, label=pot.text, timeout=_confirm_timeout(s))
        hp_depois = player_hp(s.text, s.char)
        se_subiu = (hp_antes is None or hp_depois is None or hp_depois[0] > hp_antes[0])
        if se_subiu:
            break
        log(s.name, f"⚠️ bebi a poção mas o HP não subiu (tentativa {tentativa + 1}/3) "
                    f"— o clique pode não ter registrado, tentando de novo.")
        pot_novo = find_button(s.message, "pocao de vida", "poção de vida")
        if not pot_novo:
            log(s.name, "⚠️ não achei mais o botão da Poção de Vida pra tentar de novo.")
            break
        pot = pot_novo
    # CHECAGEM da caçada no ato de curar: usa a contagem REAL (senão, o estimado).
    if s.pocao_minima_caca:
        base = qtd if qtd is not None else s.pocoes_estimadas
        if base is not None:
            s.pocoes_estimadas = base - 1
            # "verifica quantas tem; se < mínimo, bebe uma e sai da caçada"
            if base < s.pocao_minima_caca:
                s.sair_caca_pocao = True
                onde = "caçada" if getattr(s, "modo_caca", False) else "masmorra"
                log(s.name, f"🧪 Poções de Vida ({base}) abaixo de {s.pocao_minima_caca} "
                            f"— bebi uma e vou sair da {onde}.")
    elif s.pocoes_estimadas is not None:
        s.pocoes_estimadas -= 1
    return True


# Nome do botão do Tônico por escolha do usuário. Cada stat (força/defesa/
# precisão) existe em DUAS versões: SUPER (dura 10 min) e NORMAL (dura 30
# min) — visualmente parecidas ('Super Tônico de Força' vs 'Tônico de
# Força'). O texto 'tônico de força' fica CONTIDO dentro de 'super tônico de
# força', então find_tonico_button (abaixo) exige ou exclui a palavra
# 'super' explicitamente — um find_button genérico por substring pegaria o
# errado.
TONICO_SUBS = {
    "super_forca": "forca", "super_defesa": "defesa", "super_precisao": "precisao",
    "forca": "forca", "defesa": "defesa", "precisao": "precisao",
}
TONICO_DURACAO_MIN = {
    "super_forca": 10, "super_defesa": 10, "super_precisao": 10,
    "forca": 30, "defesa": 30, "precisao": 30,
}


def find_tonico_button(message, tipo: str):
    """Acha o botão certo do Tônico. 'super_X' exige a palavra 'super' no
    texto; 'X' (normal) EXCLUI qualquer botão que tenha 'super' — senão
    'tônico de força' (substring) casaria com 'super tônico de força' por
    engano, e vice-versa em telas com as duas variantes visíveis juntas."""
    stat = TONICO_SUBS.get(tipo)
    if not stat:
        return None
    quer_super = tipo.startswith("super_")
    alvo = f"tonico de {stat}"
    for b in iter_buttons(message):
        bt = norm(b.text)
        if alvo in bt and ("super" in bt) == quer_super:
            return b
    return None


async def act_tonico(s: Session) -> bool:
    """Abre Consumíveis e usa o Tônico escolhido pra essa conta (força/
    defesa/precisão, Super ou normal). Consome a ação da rodada. True se
    usou. Navega páginas do Consumíveis (o tônico pode estar na 2ª página) e
    é robusto ao card que se auto-atualiza. Chamado ~a cada 10 ou 30 min
    (recarga do tônico, conforme o tipo — ver TONICO_DURACAO_MIN)."""
    if s.tonico not in TONICO_SUBS:
        return False
    if not await s.click_text("consumiveis", "consumíveis", label="Consumíveis"):
        # 'Consumíveis' não está direto na tela (ex: estamos no MENU principal
        # da Caçada Solo, não no meio do combate) — o caminho aí é diferente:
        # Menu -> Inventário -> (aí sim aparece) Consumíveis.
        if not await s.click_text("inventario", "inventário", label="Inventário"):
            return False
        await asyncio.sleep(config.ACTION_DELAY)
        if not await s.click_text("consumiveis", "consumíveis", label="Consumíveis"):
            return False
    b = None
    for _ in range(8):
        await s.refresh()
        b = find_tonico_button(s.message, s.tonico)
        if b:
            break
        # tônico costuma estar na página 2 dos Consumíveis -> avança
        prox = find_button(s.message, "proxima", "próxima", "➡️", "➡", "avancar", "avançar")
        if prox:
            await s.click(prox, label="próxima página")
            continue
        inv = find_button(s.message, "inventario", "inventário")
        if inv:
            await s.click(inv, label="Inventário")
            continue
        cons = find_button(s.message, "consumiveis", "consumíveis")
        if cons:
            await s.click(cons, label="Consumíveis")
            continue
        await poll_sleep()
    if not b:
        log(s.name, f"⚠️ não achei o Tônico configurado ({s.tonico}). botões: {button_texts(s.message)}")
        await go_back(s)
        return False
    log(s.name, f"🧪 {b.text}")
    await s.click(b, label=b.text, timeout=_confirm_timeout(s))
    return True


TONICO_ATIVO_RE = re.compile(r"\+\s*\d+\s*(?:atk|def|crit|precisao|precis\u00e3o)\s*\(\s*(\d+)\s*min\s*\)",
                             re.IGNORECASE)


def tonico_ativo_minutos(text: str):
    """Minutos restantes do Super Tônico, lidos do indicador que aparece no
    MENU quando ele está ATIVO ('+10 ATK (5min)') — ou None se não achar (a
    indicação só aparece no menu, não durante o combate)."""
    m = TONICO_ATIVO_RE.search(norm(text or ""))
    return int(m.group(1)) if m else None


async def try_tonico(s: Session) -> bool:
    """Usa o Tônico da conta (Super ou normal, força/defesa/precisão) SE já
    passou o intervalo (10 min pro Super, 30 min pro normal — ver
    TONICO_DURACAO_MIN). CONSOME a rodada quando usa de verdade (confirmado
    pelo usuário 2026-07-12: beber qualquer consumível gasta o turno — a
    premissa antiga de que era "de graça" estava errada). O contador de
    rodadas usado pra recarga das ALMAS é incrementado a cada turno seu que
    passa, INDEPENDENTE da ação escolhida — ou seja, um turno gasto bebendo
    Tônico/Elixir/Poção também "anda" a recarga da alma normalmente (mesma
    contagem, não é uma coisa à parte). O intervalo do PRÓPRIO Tônico/Elixir
    (10/30 min) é por TEMPO REAL, sem nenhuma relação com o contador de
    rodadas das almas. Se não tem tônico configurado ou ainda não deu o
    tempo, retorna False (só um "não fiz nada", segue a ação normal igual).
    Se a tela ATUAL mostrar o indicador '+10 ATK (Xmin)' (só aparece no
    menu), confia NELE em vez do cronômetro interno — é a fonte da verdade
    de verdade (o cronômetro é só uma estimativa pra quando não dá pra ver
    esse indicador, tipo no meio do combate)."""
    if s.tonico not in TONICO_SUBS:
        return False
    duracao_min = TONICO_DURACAO_MIN.get(s.tonico, 10)
    minutos = tonico_ativo_minutos(s.text)
    if minutos is not None:
        if minutos > 0:
            s._prox_tonico = time.time() + minutos * 60
            return False
        # indicador sumiu/zerou: segue pra usar de novo, ignora o cronômetro
    elif time.time() < s._prox_tonico:
        return False
    if await act_tonico(s):
        s._prox_tonico = time.time() + duracao_min * 60
        return True
    # NÃO achou o tônico (acabou no inventário / botão não apareceu): não trava,
    # não repete a toda hora — recua 5 min antes de tentar de novo (se você
    # reabastecer, ele volta a usar sozinho). A ação NORMAL da rodada segue.
    s._prox_tonico = time.time() + 300
    return False


def find_elixir_button(message, tipo: str):
    """Acha o botão do elixir certo — 'normal' ou 'super'. Cuidado: "elixir de
    sabedoria" é SUBSTRING de "super elixir de sabedoria", então uma busca
    simples por substring pegaria o Super quando eu queria o normal (e
    vice-versa nunca acontece, mas o contrário sim). Aqui filtra excluindo
    'super' quando o tipo pedido é o normal."""
    for b in iter_buttons(message):
        bt = norm(b.text)
        if "elixir de sabedoria" not in bt:
            continue
        eh_super = "super" in bt
        if (tipo == "super") == eh_super:
            return b
    return None


async def act_elixir(s: Session) -> bool:
    """Abre Consumíveis e usa o Elixir de Sabedoria (normal OU Super,
    conforme s.elixir). Mesma navegação do Super Tônico (Consumíveis direto
    no combate, ou Menu -> Inventário -> Consumíveis fora dele) — só muda o
    item procurado."""
    tipo = s.elixir   # "normal" ou "super"
    if not tipo:
        return False
    nome_legivel = "Super Elixir de Sabedoria" if tipo == "super" else "Elixir de Sabedoria"
    if not await s.click_text("consumiveis", "consumíveis", label="Consumíveis"):
        if not await s.click_text("inventario", "inventário", label="Inventário"):
            return False
        await asyncio.sleep(config.ACTION_DELAY)
        if not await s.click_text("consumiveis", "consumíveis", label="Consumíveis"):
            return False
    b = None
    for _ in range(8):
        await s.refresh()
        b = find_elixir_button(s.message, tipo)
        if b:
            break
        prox = find_button(s.message, "proxima", "próxima", "➡️", "➡", "avancar", "avançar")
        if prox:
            await s.click(prox, label="próxima página")
            continue
        inv = find_button(s.message, "inventario", "inventário")
        if inv:
            await s.click(inv, label="Inventário")
            continue
        cons = find_button(s.message, "consumiveis", "consumíveis")
        if cons:
            await s.click(cons, label="Consumíveis")
            continue
        await poll_sleep()
    if not b:
        log(s.name, f"⚠️ não achei o {nome_legivel}. botões: {button_texts(s.message)}")
        await go_back(s)
        return False
    log(s.name, f"🍀 {b.text}")
    await s.click(b, label=b.text, timeout=_confirm_timeout(s))
    return True


ELIXIR_ATIVO_RE = re.compile(r"\+\s*\d+\s*%\s*xp\s*\(\s*(\d+)\s*min\s*\)", re.IGNORECASE)


def elixir_ativo_minutos(text: str):
    """Minutos restantes do Elixir de Sabedoria (normal ou Super) — o
    indicador que aparece no MENU quando está ATIVO ('+50% XP (29min)') tem
    o MESMO formato pros dois (só muda a % de XP) — ou None se não achar (só
    aparece no menu, não durante o combate)."""
    m = ELIXIR_ATIVO_RE.search(norm(text or ""))
    return int(m.group(1)) if m else None


async def try_elixir(s: Session) -> bool:
    """Usa o Super Elixir de Sabedoria SE já passou o intervalo (30 min,
    diferente do Tônico que é 10 min). CONSOME a rodada quando usa de
    verdade (mesma correção do Tônico — ver try_tonico). Se a conta não
    tiver essa opção marcada, ou ainda não deu o tempo, retorna False (segue
    a ação normal igual).
    Se a tela ATUAL mostrar o indicador '+50% XP (Xmin)' (só aparece no
    menu), confia NELE em vez do cronômetro interno — mesma lógica do
    Tônico (tonico_ativo_minutos)."""
    if not s.elixir:
        return False
    minutos = elixir_ativo_minutos(s.text)
    if minutos is not None:
        if minutos > 0:
            s._prox_elixir = time.time() + minutos * 60
            return False
        # indicador sumiu/zerou: segue pra usar de novo, ignora o cronômetro
    elif time.time() < s._prox_elixir:
        return False
    if await act_elixir(s):
        s._prox_elixir = time.time() + config.ELIXIR_INTERVALO
        return True
    s._prox_elixir = time.time() + 300
    return False


# ---------------------------------------------------------------------
#  Cérebro de cada papel (decide UMA ação por rodada)
# ---------------------------------------------------------------------

# CORRIGIDO (trazido do build "só Caçada em Dupla" v1.3.2-caca, relatado
# pelo usuário: "alma disponível e pronta na tela, mas o bot ataca várias
# vezes antes de usar"): a recarga da alma é rastreada por uma CRENÇA na
# memória (Brain.soul_ready_at), e essa crença só era resincronizada com a
# tela DEPOIS de abrir o menu "Almas" — só que o menu só abria se a própria
# crença já achasse que tinha alma pronta. Se a crença ficasse atrasada em
# relação ao jogo por qualquer motivo, o bot ficava preso acreditando errado
# pra sempre (nunca reabria o menu pra se corrigir) e atacava no lugar da
# alma, rodada após rodada. Força uma reconferência REAL na tela a cada
# RESYNC_ALMA_RODADAS rodadas (o cooldown da alma mais rápida, "Fúria do
# Lobo" = 3), mesmo quando a crença acha que nada está pronto.
RESYNC_ALMA_RODADAS = 3


class Brain:
    def __init__(self, s: Session):
        self.s = s
        self.last_hp = None          # HP conhecido na rodada anterior
        self.round_num = 0           # nº da rodada (pra rastrear recarga)
        self.soul_ready_at = {}      # nome da alma -> rodada em que fica pronta
        self.topped_up = False       # já fez o reforço de HP de início de masmorra?
        self.rodadas_desde_resync_alma = 0   # rodadas reais desde a última vez
                                              # que REALMENTE conferimos a tela
        self._ultimo_round_contado = -1      # evita contar 2x a mesma rodada
                                              # quando o chamador reforça a ação
                                              # (retry) sem a rodada ter avançado
        self.rugido_usado_na_rodada = None   # nº da rodada em que "Rugido do
                                              # Rochedo" foi CONFIRMADO usado
                                              # (ver ESCUDO_REQUER_RUGIDO abaixo)

    def believe_ready(self, name):
        """Acreditamos que a alma está fora de recarga? (rastreio na memória)"""
        return self.round_num >= self.soul_ready_at.get(name, 0)

    def deve_forcar_resync_alma(self):
        """A cada RESYNC_ALMA_RODADAS rodadas reais, força reconferir a tela
        mesmo que a memória ache que nenhuma alma está pronta — evita que um
        desvio na contagem trave o uso da alma pra sempre."""
        return self.rodadas_desde_resync_alma >= RESYNC_ALMA_RODADAS

    def marcar_resync_alma(self):
        """Chamado quando o menu Almas foi realmente aberto/conferido na
        tela — zera o contador de rodadas 'no escuro'."""
        self.rodadas_desde_resync_alma = 0

    def prioridade_tank(self, priority, ratio):
        """Filtra a lista de almas do TANK:
        - 'Rugido do Rochedo' (aggro) só entra se o HP estiver DENTRO da
          janela configurada no painel (config.TANK_RUGIDO_HP_MIN/MAX) —
          fora dela (baixo demais ou alto demais), nem tenta.
        - 'Escudo de Ossos' (cura) só entra na rodada IMEDIATAMENTE seguinte
          a um Rugido confirmado — funciona como um combo (Rugido primeiro,
          Escudo no turno seguinte), não uma alma independente, e não tem
          janela de HP própria (herda a oportunidade do Rugido)."""
        pode_escudo = (self.rugido_usado_na_rodada is not None
                       and self.round_num == self.rugido_usado_na_rodada + 1)
        hp_min = getattr(config, "TANK_RUGIDO_HP_MIN", 0) / 100.0
        hp_max = getattr(config, "TANK_RUGIDO_HP_MAX", 100) / 100.0
        out = []
        for n, cd in priority:
            if n == "Rugido do Rochedo":
                if ratio is None or not (hp_min <= ratio <= hp_max):
                    continue
            elif n == "Escudo de Ossos":
                if not pode_escudo:
                    continue
            out.append((n, cd))
        return out

    def mark_used(self, name, cd):
        """Marca a alma como usada agora -> em recarga por 'cd' rodadas."""
        self.soul_ready_at[name] = self.round_num + cd
        if name == "Rugido do Rochedo":
            self.rugido_usado_na_rodada = self.round_num

    def mark_seen_on_cd(self, name):
        """A tela mostrou a alma em recarga apesar do palpite -> tenta depois."""
        self.soul_ready_at[name] = self.round_num + 1

    def set_ready_in(self, name, turns):
        """Ressincroniza pela tela: fica pronta em 'turns' turnos (0 = já pronta)."""
        self.soul_ready_at[name] = self.round_num + max(0, turns)

    def _hp_ratio(self):
        hp = player_hp(self.s.text, self.s.char)
        if not hp or hp[1] == 0:
            return None, None, None
        return hp[0], hp[1], hp[0] / hp[1]

    def _took_damage(self):
        cur = player_hp(self.s.text, self.s.char)
        dmg_log = damage_to_me(self.s.text, self.s.char) > 0
        dropped = (self.last_hp is not None and cur is not None and cur[0] < self.last_hp)
        return dmg_log or dropped

    async def act(self, round_num):
        self.round_num = round_num
        # CORRIGIDO (trazido do build v1.3.4-caca, relatado pelo usuário:
        # "achei que o jogo ficou mais lento" com o Status ao vivo ativo):
        # só conta como "mais 1 rodada" quando a rodada REALMENTE avançou —
        # o chamador re-invoca act() com o MESMO round_num quando está só
        # reforçando um clique que pode ter falhado (retry), e isso não pode
        # inflar o contador de resync nem regravar o status.json à toa.
        rodada_nova = round_num != self._ultimo_round_contado
        if rodada_nova:
            self._ultimo_round_contado = round_num
            self.rodadas_desde_resync_alma += 1
        cur, hp_max, ratio = self._hp_ratio()
        # write_status() faz leitura+escrita SÍNCRONA de status.json (sem
        # lock, sem await) — antes rodava em TODA chamada de act(), inclusive
        # nos retries de "sem nenhuma mudança, tentando de novo" (até 10
        # tentativas numa única rodada quando o Telegram tá lento). Cada
        # retry reescrevia o arquivo inteiro de novo, travando o loop
        # assíncrono e atrasando as OUTRAS contas rodando em paralelo. Agora
        # só grava 1x por RODADA REAL — os retries continuam agindo
        # normalmente, só não gravam status de novo à toa.
        if cur is not None and rodada_nova:
            hp_mob = monster_hp(self.s.text)
            write_status(self.s.name, cur, hp_max, progresso_atual_texto(self.s.text),
                         hp_monstro=(hp_mob[0] if hp_mob else None),
                         hp_monstro_max=(hp_mob[1] if hp_mob else None),
                         inicio_ts=getattr(self.s, "_t_inicio_conteudo", None),
                         nivel=getattr(self.s, "_nivel", None),
                         xp_faltam=getattr(self.s, "_xp_faltam", None),
                         eta_proximo_nivel_seg=getattr(self.s, "_eta_proximo_nivel_seg", None))

        # Reforço de início: quem entrou com HP baixo bebe 1 poção ANTES de
        # partir pra luta (feito uma vez, na 1ª ação). SÓ pros NÃO-TANKS: o
        # tank não precisa desse reforço aqui porque, em combate, ele já cura
        # IMEDIATAMENTE assim que o HP cai abaixo do limite (ver _act_tank) —
        # não existe mais espera por Rugido antes de curar.
        #   MASMORRA: limite fixo BETWEEN_DG_HEAL_RATIO (config geral "Reforço").
        #   CAÇADA: limite CONFIGURÁVEL "HP% reforço" (0 = desligado).
        if not self.topped_up:
            self.topped_up = True
            if getattr(self.s, "modo_caca", False):
                reforco = getattr(self.s, "caca_reforco_ratio", 0.0) or 0.0
            else:
                reforco = config.BETWEEN_DG_HEAL_RATIO
            if (self.s.role != "tank" and reforco > 0
                    and ratio is not None and ratio < reforco):
                log(self.s.name, f"🩹 entrei com HP {ratio:.0%} — poção de reforço.")
                if await act_potion(self.s):
                    c = player_hp(self.s.text, self.s.char)
                    if c:
                        self.last_hp = c[0]
                    return

        role = self.s.role
        took = self._took_damage()

        # Na CAÇADA EM DUPLA não há mecânica de tank/aggro (2 contas, turno
        # simultâneo): TODA conta ataca e usa as almas selecionadas — por isso
        # usa _act_other mesmo o "tank" ali. Na MASMORRA o tank sempre usa
        # _act_tank (defende/segura aggro com o Rugido). Na CRIPTA (também
        # modo_caca=True, mas com 'tank_ativo' ligado) o tank SEMPRE defende
        # igual à masmorra — é o 'tank_ativo' que decide isso, não o modo_caca.
        if role == "tank" and (not getattr(self.s, "modo_caca", False)
                                or getattr(self.s, "tank_ativo", False)):
            await self._act_tank(cur, ratio, took)
        else:
            await self._act_other(cur, ratio, took)

        # atualiza o HP conhecido pra próxima rodada
        c = player_hp(self.s.text, self.s.char)
        if c:
            self.last_hp = c[0]

    def _limite_hp_conta(self) -> float:
        """HP% (0-1) abaixo do qual ESTA conta bebe poção na Masmorra —
        configurável POR CONTA (campo 'HP% poção' no cartão, aba
        Configuração), substituindo os limites globais por PAPEL (Tank/
        Outros) que tratavam todo mundo do mesmo papel igual, mesmo com HP
        máximo bem diferente entre personagens. Cai pro valor padrão global
        por papel só se a conta não tiver esse campo salvo (configs antigas,
        de antes dessa mudança)."""
        pct = (self.s.acc or {}).get("vida_min_pct")
        if pct is not None:
            try:
                return max(0, min(100, int(pct))) / 100.0
            except (TypeError, ValueError):
                pass
        return config.TANK_HEAL_RATIO if self.s.role == "tank" else config.OTHER_HEAL_RATIO

    def _limite_atual(self) -> float:
        """Limite de HP% (0-1) abaixo do qual bebe poção, no modo ATUAL —
        caça usa caca_vida_ratio (configurado por conta na aba); masmorra
        usa o HP% por conta/papel (_limite_hp_conta)."""
        if getattr(self.s, "modo_caca", False):
            return getattr(self.s, "caca_vida_ratio", 0.0) or 0.0
        return self._limite_hp_conta()

    async def _checar_curar_antes(self, contexto: str) -> bool:
        """Reconfere o HP IMEDIATAMENTE antes de QUALQUER ação (Super Tônico,
        Elixir, Alma, Atacar/Defender) — pedido explícito do usuário: toda
        ação gasta um tempo real (cliques, idas-e-vindas de tela), então o HP
        visto um passo atrás pode já estar desatualizado quando chega a vez
        de agir de verdade. Bebe a Poção de Vida na hora se precisar. Retorna
        True se bebeu (quem chamou deve dar return, a rodada já foi usada
        nisso); False se está seguro OU se precisava e não tinha poção (nesse
        caso ABORTA a ação planejada — ver nota abaixo).

        CORRIGIDO (removido o refresh redundante, achado comparando com a
        versão antiga que rodava ~10min mais rápido pro mesmo andar): antes,
        essa reconferência forçava um `await self.s.refresh()` — um
        `get_messages()` de VERDADE pro Telegram — TODA VEZ que era chamada
        (até 4x por rodada, por conta), batendo com os avisos "🐢
        get_messages demorou Xs" no log. Removido porque é REDUNDANTE: toda
        ação real (click() -> wait_change()) já força seu próprio refresh()
        por dentro — então self.s.text, aqui, já está tão atualizado quanto
        um refresh novo estaria. Continua protegendo igual, sem o custo extra.

        CORRIGIDO (sem Poção de Vida no estoque, ABORTA em vez de seguir):
        antes, sem poção, o bot seguia com a ação planejada mesmo assim
        (atacava/usava tônico/alma com o HP já crítico), arriscando morrer
        antes de sair do grupo. Agora, se realmente não tem poção (act_potion
        já marcou s.sair_caca_pocao=True pra isso), a conta ABORTA a ação
        desta rodada por completo — o combat_loop (Masmorra/Caçada em
        Dupla/Cripta) já checa esse sinal logo em seguida e tira o grupo
        inteiro de forma segura, sem esperar a rodada resolver."""
        c = player_hp(self.s.text, self.s.char)
        ratio = (c[0] / c[1]) if c else None
        dano = damage_to_me(self.s.text, self.s.char)
        limite = self._limite_atual()
        if ratio is None or limite <= 0 or ratio > limite:
            return False
        dano_txt = f" (levou {dano} de dano)" if dano > 0 else ""
        log(self.s.name, f"🩺 HP={c[0]} ratio={ratio:.0%} limite={limite:.0%}{dano_txt} -> caiu "
                         f"antes de [{contexto}] — bebendo poção em vez de {contexto.lower()}.")
        if await act_potion(self.s):
            return True
        log(self.s.name, f"🛑 HP baixo antes de [{contexto}] e SEM Poção de Vida — "
                         f"abortando a ação (o grupo sai em seguida).")
        return True

    async def _act_tank(self, cur, ratio, took):
        # Poção é IMEDIATA quando o HP está baixo — NUNCA espera o Rugido
        # antes. Antes disso o bot tentava o Rugido PRIMEIRO (achando que
        # segurar aggro era mais urgente), mas isso é PERIGOSO na prática:
        # enquanto o Rugido é tentado/resolve (alguns segundos, mais ainda se
        # a API do Telegram estiver lenta), passa uma rodada inteira SEM
        # curar com o HP já baixo — e o tank pode morrer nesse intervalo
        # (confirmado pelo usuário: já morreu assim). Rugido só é tentado
        # quando o HP está BEM (ACIMA do limite) — vira só uma ação extra pra
        # segurar aggro enquanto não há perigo nenhum, nunca um substituto de
        # cura.
        # REDE DE SEGURANÇA: se a leitura do HP FALHOU (ratio None — ex: uma
        # variação de layout, tipo a masmorra do deserto, que o player_hp()
        # não reconheça direito), o código ANTIGO assumia "tá tudo bem" e
        # seguia pro Rugido/Defender, sem checar nada — um jeito de morrer
        # em silêncio, sem nenhum erro aparecer no log. Agora, se não deu pra
        # ler o HP MAS o log de eventos mostra que tomou dano nessa rodada
        # (mesmo sinal que os outros papéis já usam), trata como se
        # precisasse curar por segurança, em vez de assumir que está bem.
        precisa_curar = (ratio is not None and ratio <= self._limite_hp_conta()) or (
            ratio is None and took)
        dano_tank = damage_to_me(self.s.text, self.s.char)
        log(self.s.name, f"🩺 HP={cur} "
                         f"ratio={('%.0f%%' % (ratio * 100)) if ratio is not None else 'NÃO LIDO'} "
                         f"limite={self._limite_hp_conta() * 100:.0f}%"
                         f"{f' · levou {dano_tank} de dano' if dano_tank > 0 else ''} -> "
                         f"{'BEBER poção' if precisa_curar else 'ok, não bebe'} (tank)")
        if precisa_curar:
            if ratio is None:
                log(self.s.name, "⚠️ não consegui ler o HP do tank, mas o log mostra dano "
                                 "nessa rodada — bebendo poção por segurança.")
            if await act_potion(self.s):
                return
            # sem poção: ABORTA sem fazer nada (nem Defender) — o combat_loop
            # detecta o sinal (s.sair_caca_pocao) logo em seguida e tira o
            # grupo, sem arriscar mais uma ação com o HP já crítico.
            log(self.s.name, "🛑 sem Poção de Vida — abortando (nem Defender), o grupo sai em seguida.")
            return
        # HP OK (acima do limite): usa o tempo livre pra manter aggro com
        # Rugido (se pronto) e pro Super Tônico/Elixir (ações de graça).
        # Pedido do usuário: como CADA ação gasta um tempo real (não é
        # instantâneo), confere o HP de novo IMEDIATAMENTE ANTES de cada uma
        # (Tônico, Elixir, Rugido, Defender) — não só uma vez no início.
        # CORRIGIDO (2026-07-12): Tônico/Elixir NÃO são de graça — confirmado
        # pelo usuário que beber consome o turno de verdade (a rodada fechava
        # ali, sem sobrar ataque depois). Antes disso, eu tinha essa premissa
        # ao contrário (achava que era de graça) por causa de um bug antigo
        # onde o bot ficava preso esperando a rodada "resolver" — a causa
        # real desse travamento era outra coisa, não o tônico ser de graça.
        if await self._checar_curar_antes("Super Tônico"):
            return
        if await try_tonico(self.s):
            return
        if await self._checar_curar_antes("Elixir"):
            return
        if await try_elixir(self.s):
            return
        if await self._checar_curar_antes("Rugido/Alma"):
            return
        if await use_soul_from_priority(self.s, self, self.prioridade_tank(self.s.souls, ratio),
                                         forcar=self.deve_forcar_resync_alma()):
            return
        if await self._checar_curar_antes("Defender"):
            return
        await act_defender(self.s)

    async def _act_other(self, cur, ratio, took):
        # 1) POÇÃO — decisão 100% por HP (nada a ver com alma).
        #    CAÇADA: bebe SÓ quando o HP cai abaixo do % configurado
        #    ("HP% p/ poção"); 'took' (tomou dano) é ignorado aqui, porque não
        #    há tank segurando aggro e o log de eventos mantém o dano antigo na
        #    tela — se olhasse 'took', bebia poção toda rodada.
        #    MASMORRA: mantém o comportamento antigo (tomar dano = aggro vazou
        #    do tank -> cura; ou HP abaixo do limite "Outros").
        caca = getattr(self.s, "modo_caca", False)
        dano_rodada = damage_to_me(self.s.text, self.s.char)
        dano_rodada_txt = f" · levou {dano_rodada} de dano" if dano_rodada > 0 else ""
        if caca:
            limite = getattr(self.s, "caca_vida_ratio", 0.0) or 0.0
            precisa_pocao = (limite > 0 and ratio is not None and ratio <= limite)
            # Log do HP e da decisão de poção na caçada (mantido — dá visibilidade
            # do que o bot vê; NÃO afeta o comportamento, é só um log).
            log(self.s.name, f"🩺 HP={cur} "
                             f"ratio={('%.0f%%' % (ratio * 100)) if ratio is not None else 'NÃO LIDO'} "
                             f"limite={limite * 100:.0f}%{dano_rodada_txt} -> "
                             f"{'BEBER poção' if precisa_pocao else 'ok, não bebe'}")
        else:
            precisa_pocao = took or (ratio is not None and ratio <= self._limite_hp_conta())
        if precisa_pocao:
            if await act_potion(self.s):
                return
            # sem poção de verdade: ABORTA a ação por completo (não cai pra
            # Tônico/Elixir/Alma/Atacar com o HP já crítico) — o combat_loop
            # detecta o sinal (s.sair_caca_pocao) logo em seguida e tira o
            # grupo, sem esperar a rodada resolver.
            log(self.s.name, "🛑 sem Poção de Vida — abortando a ação, o grupo sai em seguida.")
            return
        # Pedido do usuário: CADA ação (Tônico/Elixir/Alma/Atacar) gasta um
        # tempo real (cliques, telas), então confere o HP de novo
        # IMEDIATAMENTE ANTES de cada uma — não só uma vez no início da
        # rodada, e não só depois de todas elas. CORRIGIDO (2026-07-12):
        # Tônico/Elixir NÃO são de graça — confirmado pelo usuário que beber
        # consome o turno de verdade (a rodada fechava ali, sem sobrar
        # ataque depois).
        if await self._checar_curar_antes("Super Tônico"):
            return
        if await try_tonico(self.s):
            return
        if await self._checar_curar_antes("Elixir"):
            return
        if await try_elixir(self.s):
            return
        # 2) alma do papel (só abre o menu se acreditar que há alma pronta).
        #    'Alma a partir do andar X' (Cripta) é checado DENTRO de
        #    use_soul_from_priority (com isenção pro tank) — não precisa
        #    repetir aqui.
        #    TANK na Caçada: só USA alma quando o HP está <= "HP% alma (tank)"
        #    (personagens com defesa maior não precisam gastar Rugido/Escudo
        #    toda hora). A ORDEM Rugido -> Escudo de Ossos já vem sozinha do
        #    catálogo (Rugido tem recarga menor e fica 1º na lista de
        #    prioridade — use_soul_from_priority tenta a 1ª pronta); acima do
        #    HP% alma, o tank pula pra Defender direto, sem gastar alma à toa.
        pode_usar_alma = True
        if caca and self.s.role == "tank":
            limite_alma = getattr(self.s, "tank_alma_ratio", 0.60) or 0.0
            pode_usar_alma = (ratio is not None and ratio <= limite_alma)
        if pode_usar_alma:
            if await self._checar_curar_antes("Alma"):
                return
            # Alma FECHA O TURNO sozinha (confirmado pelo usuário 2026-07-09:
            # "toda alma, seja de dano ou de buff, já usa o turno") — então dá
            # return aqui, sem cair pra atacar/defender depois na MESMA
            # rodada. Pro TANK, aplica o mesmo pré-requisito Rugido->Escudo
            # usado na Masmorra (ver Brain.prioridade_tank).
            prioridade = self.prioridade_tank(self.s.souls, ratio) if self.s.role == "tank" else self.s.souls
            if await use_soul_from_priority(self.s, self, prioridade,
                                             forcar=self.deve_forcar_resync_alma()):
                return
        # RECONFERE o HP antes de atacar/defender — mesma lógica, agora bem
        # em cima da hora (a ação final é a que realmente fecha o turno).
        if await self._checar_curar_antes("Atacar" if not (caca and self.s.role == "tank") else "Defender"):
            return
        # 4) sem alma pronta (todas em CD): na CAÇADA o TANK DEFENDE (as outras
        #    contas atacam). Na masmorra, cai aqui só quem não é tank (o tank
        #    tem o seu próprio _act_tank), então ataca normal.
        if caca and self.s.role == "tank":
            await act_defender(self.s)
        else:
            await act_atacar(self.s)


# ---------------------------------------------------------------------
#  Loop de combate de uma conta
# ---------------------------------------------------------------------

async def combat_loop(s: Session, leave_event: asyncio.Event, restart_event: asyncio.Event, shared,
                       marcadores_fim=("conclu",)):
    """'marcadores_fim': lista de substrings (já normalizadas, minúsculas sem
    acento) que, se aparecerem na tela, indicam CONCLUSÃO real do conteúdo
    (não combate). A Masmorra normal usa 'conclu' (tela "...concluída!"); o
    Templo do Oásis (Duo) usa 'vitoria' (tela "Templo do Oásis — Vitória!") —
    mesmo layout de combate, só muda o texto da tela de conclusão."""
    brain = Brain(s)
    # RETOMADA (pedido do usuário 2026-07-16): se quem chamou marcou esta
    # sessão como uma retomada de conteúdo já ativo (ver run_account/
    # run_templo_oasis_dupla), força conferir o cooldown REAL das almas já
    # na 1ª ação — a crença de recarga do Brain começa vazia numa sessão
    # nova, então sem isso ele levaria RESYNC_ALMA_RODADAS rodadas pra
    # confirmar de verdade contra a tela.
    if getattr(s, "_retomando_conteudo", False):
        s._retomando_conteudo = False
        brain.rodadas_desde_resync_alma = RESYNC_ALMA_RODADAS
    rounds = 0
    sem_linha = 0   # nº de vezes seguidas que não achei minha linha (fallback)
    lobby_espera = 0   # nº de vezes seguidas que vi o lobby (combate não começou)
    # ANTI-LOOP de "perdi a vez": depois de um reinício+resume, a tela ainda pode
    # mostrar um "perdeu a vez" ANTIGO no log de eventos. Só confio nele DEPOIS
    # que uma rodada NOVA aconteceu desde que entrei neste combate (senão o bot
    # reiniciava de novo em cima do evento velho -> loop). Guardo a assinatura da
    # 1ª tela e só libero o gatilho quando ela muda (rodada avançou).
    sig_inicial = None
    houve_rodada_nova = False
    _ultima_limpeza_rounds = 0   # pra limpar o histórico periodicamente durante
                                 # a luta também (não só na transição lobby->combate)
    # liga o batimento por leitura de tela (ver Session._bump_heartbeat)
    s._combat_hb = shared

    async def _sair_pocao_agora() -> bool:
        """Item 2 (2026-07-16): chamado tanto no topo do loop quanto
        IMEDIATAMENTE depois de CADA brain.act() (incluindo os retries) —
        antes, só era checado 1x por volta do loop, então entre um brain.act()
        detectar 'sem poção' e a próxima checagem, o bot podia esperar a
        rodada inteira 'resolver' (até ROUND_TIMEOUT_CACA segundos, com
        retries chamando brain.act() de novo) com o HP crítico e sem cura —
        risco real de morrer nesse intervalo. Retorna True se saiu (o
        chamador deve encerrar a função na hora)."""
        if not s.sair_caca_pocao:
            return False
        log(s.name, "🧪 Poções de Vida abaixo do mínimo — acionando saída "
                    "de todos e pausando o bot.")
        leave_event.set()
        shared["stop"].set()
        shared.setdefault("em_combate", {})[s.name] = 0
        registrar_pausa("pocao_vida_baixa", f"{s.name}: acabando durante a masmorra")
        await leave_room(s)
        await asyncio.to_thread(
            popup_aviso, "TofuBot — Masmorra",
            f"As Poções de Vida acabaram (ou ficaram abaixo do mínimo "
            f"configurado) DURANTE a masmorra!\n\n"
            f"Conta {s.name}. O grupo já saiu da sala. "
            f"Reabasteça e clique Iniciar de novo.")
        return True

    while True:
        if restart_event.is_set():   # outra conta pediu reinício
            shared.setdefault("em_combate", {})[s.name] = 0
            return

        # alguém do grupo morreu (detectado por qualquer conta)? sai todo mundo.
        if leave_event.is_set():
            shared.setdefault("em_combate", {})[s.name] = 0
            await leave_room(s)
            return

        await s.refresh()
        txt = s.text
        # publica o TIMESTAMP da minha última rodada de combate (0 = fora de
        # combate) pras outras contas saberem se a masmorra está viva e
        # PROGREDINDO (ver outras_em_combate). Obs: _bump_heartbeat já publica a
        # cada leitura de tela; isto aqui garante o 0 quando NÃO é combate.
        shared.setdefault("em_combate", {})[s.name] = time.time() if is_combat_screen(s.message) else 0

        # rastreia se JÁ houve uma rodada nova desde que entrei (anti-loop de
        # "perdi a vez"): guarda a 1ª assinatura de combate e marca quando muda.
        if is_combat_screen(s.message):
            _sig_atual = round_signature(txt)
            if sig_inicial is None:
                sig_inicial = _sig_atual
            elif not houve_rodada_nova and _sig_atual != sig_inicial:
                houve_rodada_nova = True
        atualizar_recompensas(shared, s.texto_recompensas)   # ouro/XP/drop de cada mob, a cada refresh

        if someone_died(txt):
            log(s.name, "💀 morte detectada no grupo — acionando saída de todos e "
                        "pausando o bot (não inicia outra masmorra sozinho).")
            leave_event.set()
            shared["stop"].set()
            shared.setdefault("em_combate", {})[s.name] = 0
            # DEDUP: várias contas do grupo detectam a MESMA morte quase ao
            # mesmo tempo (cada uma roda esse loop de forma independente) —
            # sem essa trava, a morte seria contada 1x por conta que notasse,
            # inflando o contador do relatório. Só a PRIMEIRA a chegar aqui
            # registra de verdade.
            if not shared.get("morte_registrada"):
                shared["morte_registrada"] = True
                registrar_pausa("morte", f"detectado por {s.name} na masmorra")
                try:
                    registrar_morte("templo_oasis" if "vitoria" in marcadores_fim else "masmorra")
                except Exception as e:
                    log(s.name, f"(não consegui registrar a morte: {e!r})")
            await leave_room(s)
            return

        # SEGURANÇA: alguma OUTRA conta travou no meio da luta (FloodWait, rede,
        # reinício)? Sem o tank o aggro vaza; sem o suporte ninguém cura -> risco
        # de MORTE. Depois de já ter jogado umas rodadas (pra o grupo entrar no
        # combate), se alguém ficou parado tempo demais, o grupo TODO sai e
        # reagrupa — NÃO pausa o bot (é tropeço, não erro): leave_event faz todos
        # saírem e, como não seto 'stop', o ciclo monta uma masmorra nova.
        if rounds >= 3:
            travada = conta_travada_no_combate(shared, s.name)
            if travada:
                nome_t, papel_t = travada
                log(s.name, f"⚠️ '{nome_t}' ({papel_t}) travou no combate "
                            f"(flood/rede/reinício?) — saindo todos pra reagrupar, "
                            f"pra ninguém morrer.")
                leave_event.set()
                shared.setdefault("em_combate", {})[s.name] = 0
                await leave_room(s)
                return

        # poção de vida caiu abaixo do limite AO CURAR (marcado em act_potion,
        # mesmo mecanismo já usado na Caçada em Dupla): sai TODO MUNDO junto
        # (igual morte) — não deixa só essa conta abandonar sozinha o grupo.
        if await _sair_pocao_agora():
            return

        # perdi a vez? (dessincronia residual) -> reinício automático do bot.
        # só depois de já ter jogado umas rodadas E de ter passado uma RODADA
        # NOVA desde que entrei (houve_rodada_nova): logo após um resume, a tela
        # ainda pode mostrar um "perdeu a vez" ANTIGO — sem essa trava o bot
        # reiniciava em cima do evento velho, de novo e de novo (loop). Assim só
        # confio no aviso quando ele é da rodada ATUAL, não de antes do reinício.
        if rounds >= 2 and houve_rodada_nova and i_lost_turn(txt, s.char):
            log(s.name, "🔁 perdi a vez — solicitando REINÍCIO automático do bot.")
            shared.setdefault("em_combate", {})[s.name] = 0
            restart_event.set()
            return

        # NÃO é a tela de combate? Decide o que é ANTES de sair — sair por
        # engano do lobby/submenu é o que causava o loop de reinício.
        if not is_combat_screen(s.message):
            # 1) avançar de sala/tela
            if find_button(s.message, "proximo", "próximo", "continuar", "avancar", "avançar"):
                log(s.name, "➡️ avançando de sala/tela.")
                await s.click_text("proximo", "próximo", "continuar",
                                   "avancar", "avançar", label="Próximo", required=False)
                lobby_espera = 0
                continue
            # 2) CONCLUSÃO real da masmorra -> aí sim sai (log enxuto: eram 4x).
            #    GUARDA o texto da tela final AGORA (ranking/recompensas) pra o
            #    registro não depender de reler a tela depois — se o jogo avançar
            #    sozinho da tela de conclusão, o run_account leria tarde demais e
            #    PERDERIA o registro (contagem/XP/loot). Um refresh extra aqui
            #    (só no FIM da masmorra, não por rodada) pega a tela mais completa.
            if any(m in norm(txt) for m in marcadores_fim):
                await s.refresh()
                shared.setdefault("conclusao", {})[s.name] = s.text
                shared.setdefault("em_combate", {})[s.name] = 0
                log(s.name, "🏁 conteúdo concluído — voltando ao menu.")
                return
            # 3) LOBBY (combate ainda vai começar): ESPERA, não sai. Com limite
            #    de segurança pra não travar pra sempre se o host não iniciar.
            if is_lobby_screen(s.message):
                lobby_espera += 1
                if lobby_espera > 150:   # ~2 min preso no lobby -> algo travou
                    log(s.name, "⚠️ tempo demais no lobby (o combate não começou) — saindo.")
                    return
                await poll_sleep()
                continue
            # 4) SUBMENU aberto (Almas/Consumíveis) -> volta pro combate
            if is_submenu_combate(s.message):
                await go_back(s)
                continue
            # 5) tela INESPERADA -> antes de desistir, mesma proteção aplicada
            # na Caçada em Dupla (bug real, morte relatada): às vezes aparece
            # uma notificação de OUTRA sala/masmorra ("expirou por
            # inatividade") sem relação com o combate atual — parece uma
            # mensagem antiga/de outra sessão sobrepondo por um instante. Dá
            # mais algumas chances de a tela real reaparecer sozinha antes de
            # desistir, e confere o HP por segurança antes de sair de vez.
            # AMPLIADO 2026-07-16 (morte relatada: notificação "A troca foi
            # cancelada pelo outro jogador" — sem relação NENHUMA com a
            # masmorra): detecta pelo FORMATO (tela não reconhecida com um
            # único botão 'Menu'), não só pela frase específica — cobre
            # qualquer notificação avulsa parecida, além de tentar 'Voltar'
            # se existir e checar o HP em TODA tentativa (não só 1x no fim).
            _botoes_tela = button_texts(s.message)
            eh_notificacao_transitoria = (
                bool(re.search(r"expirou por inatividade", norm(txt)))
                or (len(_botoes_tela) == 1 and find_button(s.message, "menu") is not None))
            if eh_notificacao_transitoria:
                recuperou = False
                for _ in range(12):
                    hp_emergencia = player_hp(s.text, s.char)
                    if hp_emergencia and hp_emergencia[1]:
                        ratio_emerg = hp_emergencia[0] / hp_emergencia[1]
                        limite_emerg = brain._limite_atual() or 0.4
                        if ratio_emerg <= limite_emerg:
                            log(s.name, f"🩺 emergência enquanto espera a tela real voltar: "
                                        f"HP em {ratio_emerg:.0%} — bebendo poção por segurança.")
                            await act_potion(s)
                    if find_button(s.message, "voltar", "atras", "⬅", "◀", "🔙"):
                        await go_back(s)
                    await poll_sleep()
                    await s.refresh()
                    if is_combat_screen(s.message):
                        recuperou = True
                        break
                if recuperou:
                    continue
            hp_emergencia = player_hp(s.text, s.char)
            if hp_emergencia and hp_emergencia[1]:
                ratio_emerg = hp_emergencia[0] / hp_emergencia[1]
                # só uma reserva de segurança (não é a lógica normal de cura,
                # que já rodou antes de chegar aqui) — limite fixo e
                # conservador, só pra evitar abandonar alguém com HP crítico.
                if ratio_emerg <= 0.4:
                    log(s.name, f"🩺 emergência antes de desistir da tela: HP em "
                                f"{ratio_emerg:.0%} — tentando beber poção por segurança.")
                    await act_potion(s)
            log(s.name, "🏁 saí da tela de combate. Texto:\n"
                        f"    {txt}\n    botões: {button_texts(s.message)}")
            return
        lobby_espera = 0

        # Age EXATAMENTE quando a MINHA ampulheta ⏳ está na tela (não agi ainda).
        # Some depois que a ação registra -> não age de novo. Se continuar, tenta
        # de novo (confirma que a ação foi feita).
        # 'waiting_actions' checa o texto "Aguardando ações" (Masmorra normal);
        # como fallback, também entra aqui se a MINHA linha já mostra a ampulheta
        # (my_turn_state == "waiting") mesmo sem essa frase — outras telas de
        # combate no mesmo formato (ex: Templo do Oásis) podem não repetir a
        # frase mas têm a mesma ampulheta por personagem.
        estado_preliminar = my_turn_state(txt, s.char)
        if waiting_actions(txt) or estado_preliminar == "waiting":
            estado = estado_preliminar
            if estado == "waiting":
                sem_linha = 0
                rounds += 1
                if rounds > config.MAX_ROUNDS:
                    log(s.name, "⚠️ passei do limite de rodadas. Parando.")
                    return
                # Limpeza periódica (pedido do usuário): mesmo já limpando na
                # transição lobby->combate, uma masmorra longa acumula MUITAS
                # telas antigas de rodada — limpa de novo a cada 20 rodadas
                # reais, sempre preservando a tela atual (manter=1).
                if rounds - _ultima_limpeza_rounds >= 20:
                    _ultima_limpeza_rounds = rounds
                    await limpar_historico(s)
                    # BUG REAL corrigido (relatado pelo usuário 2026-07-15,
                    # "The specified message ID is invalid"): a limpeza faz
                    # sua PRÓPRIA leitura fresca do chat — se tinha chegado
                    # uma mensagem NOVA entre o último refresh e a limpeza,
                    # ela podia apagar a mensagem que a conta ainda ia usar
                    # pra agir (que, pra ela, já não era mais "a mais
                    # recente"). Sem refrescar de novo aqui, o clique
                    # seguinte tentava usar um botão de uma mensagem já
                    # apagada. Um refresh extra aqui resolve — o custo é
                    # desprezível (roda só 1x a cada 20 rodadas).
                    await s.refresh()
                    txt = s.text
                _t0 = time.time()
                await brain.act(rounds)   # decide: poção > tônico(10min) > alma > ação — o
                                          # Covil de Zul'gor usa a MESMA IA da masmorra normal
                                          # (o roteiro fixo por posição foi removido, não funcionava).
                if await _sair_pocao_agora():   # item 2: aborta JÁ, sem esperar a rodada resolver
                    return
                _t_acao = time.time() - _t0
                # confirma que a ação REGISTROU: espera a minha ampulheta sumir,
                # REFORÇANDO o clique se nada mudar (mesmo mecanismo já usado na
                # Caçada em Dupla — RETRY_ACAO_APOS_CACA/ROUND_TIMEOUT_CACA): se
                # o clique se perder silenciosamente (falha do Telegram), o bot
                # tenta agir de novo em vez de só ficar esperando parado até
                # estourar o tempo todo (era o que acontecia antes aqui, com um
                # limite fixo de 6 tentativas de poll sem reforçar o clique).
                _t1 = time.time()
                _deadline = _t1 + config.ROUND_TIMEOUT_CACA
                _texto_antes = s.text
                _mudou = False
                _tentativas_retry = 0
                _ultima_tentativa = _t1
                while time.time() < _deadline:
                    await s.refresh()
                    if is_combat_screen(s.message) and my_turn_state(s.text, s.char) != "waiting":
                        # MINHA ampulheta sumiu — ação confirmada. O
                        # 'is_combat_screen' AQUI é crítico: sem ele, um clique
                        # que falhe (ex: 'Encrypted data invalid' — a conta cai
                        # numa tela velha, tipo o lobby "Pronto/Iniciar") também
                        # bate "!= waiting" (my_turn_state não reconhece
                        # ampulheta NENHUMA fora do combate) e o bot ACHAVA que
                        # tinha agido — só que na real não fez nada naquela
                        # rodada, e a conta ficava "sumida" (sem lutar de
                        # verdade) até algo mais grave estourar (perda de vez,
                        # HP crítico). Confirmado em produção: a tela capturada
                        # no erro era literalmente o lobby da sala, não o
                        # combate. Agora, se a tela não for de combate mesmo,
                        # cai pro retry abaixo, que reforça o clique de novo.
                        _mudou = True
                        break
                    if s.text != _texto_antes:
                        # a TELA mudou (evento novo, HP diferente, rodada nova
                        # com ampulheta resetada) — solta o loop pra reavaliar
                        # do zero, em vez de ficar preso esperando.
                        _mudou = True
                        break
                    if (time.time() - _ultima_tentativa >= config.RETRY_ACAO_APOS_CACA
                            and _tentativas_retry < config.MAX_TENTATIVAS_ACAO):
                        _tentativas_retry += 1
                        log(s.name, f"🔁 sem nenhuma mudança em {config.RETRY_ACAO_APOS_CACA:.0f}s — "
                                    f"o clique pode ter falhado, tentando agir de novo "
                                    f"(tentativa {_tentativas_retry}/{config.MAX_TENTATIVAS_ACAO}).")
                        await brain.act(rounds)
                        if await _sair_pocao_agora():   # item 2: aborta JÁ
                            return
                        _texto_antes = s.text
                        _ultima_tentativa = time.time()
                        continue
                    await poll_sleep()
                if not _mudou:
                    log(s.name, f"🔍 DEBUG: {config.ROUND_TIMEOUT_CACA:.0f}s e ainda via 'waiting'. "
                                f"tela inteira: {ascii(s.text)}")
                _t_confirm = time.time() - _t1
                log(s.name, f"⏱️ esperei a vez | agi em {_t_acao:.1f}s | "
                            f"rodada resolveu em {_t_confirm:.1f}s")
                continue
            if estado == "unknown":
                sem_linha += 1
                if sem_linha >= 8:   # não achei minha linha -> age por segurança
                    log(s.name, f"⚠️ não achei minha linha ('{s.char}') — agindo por segurança.")
                    sem_linha = 0
                    rounds += 1
                    await brain.act(rounds)
                    if await _sair_pocao_agora():   # item 2: aborta JÁ
                        return
                    continue

        await poll_sleep()


# ---------------------------------------------------------------------
#  Orquestração: forma o grupo e roda o combate das 4 contas
# ---------------------------------------------------------------------

async def _tentar_evitar_start(s: Session) -> bool:
    """Item 8 (2026-07-16): antes de recorrer ao /start como último recurso —
    que já causou BANIMENTO REAL de horas no Telegram (visto em produção,
    inclusive relatado por jogadores manuais, SEM bot nenhum) — tenta 2
    saídas mais baratas e seguras:
    1) 'sair do lobby': sobra de uma caçada/sala em dupla anterior (a conta
       fica numa tela só com esse botão) — nenhum reconhecimento de tela
       específico bate com isso, e sem essa tentativa o bot caía direto no
       /start repetidas vezes sem nunca resolver de verdade.
    2) um 'voltar' genérico: cobre qualquer tela de conteúdo diferente (ex:
       "Sem energia para explorar [conteúdo]", só com um botão 'Voltar') —
       é sempre um passo seguro em direção ao menu.
    Retorna True se clicou algo (o chamador deve tentar de novo, dando
    espaço pra tela reagir, em vez de já cair no /start)."""
    b = find_button(s.message, "sair do lobby", "sair lobby")
    if b:
        log(s.name, "🚪 preso num lobby anterior — saindo antes de tentar de novo.")
        await s.click(b, label=b.text)
        return True
    b = find_button(s.message, "voltar", "atras", "atrás", "⬅️", "⬅", "◀️", "◀", "🔙")
    if b:
        log(s.name, "◀️ tela travada — clicando Voltar antes do /start.")
        await s.click(b, label=b.text)
        return True
    return False


async def back_to_menu(s: Session):
    """Volta pro menu principal: clica 'Menu' se houver. Só manda /start se
    AINDA não chegou no menu depois disso — antes mandava /start SEMPRE, o que
    poluía a conversa e gastava requisição à toa (pedido do usuário 2026-07-03:
    'o bot fica dando /start várias vezes sem necessidade')."""
    await s.refresh()
    b = find_button(s.message, "menu")
    if b:
        await s.click(b, label="Menu")
        await asyncio.sleep(config.ACTION_DELAY)
        await s.refresh()
    if _no_menu_principal(s.message):
        return
    # Item 8: tenta as 2 saídas mais baratas antes do /start (ver
    # _tentar_evitar_start) — se algo bateu, dá mais uma chance ao 'Menu'
    # antes de finalmente recorrer ao /start.
    if await _tentar_evitar_start(s):
        await asyncio.sleep(config.ACTION_DELAY)
        await s.refresh()
        b = find_button(s.message, "menu")
        if b:
            await s.click(b, label="Menu")
            await asyncio.sleep(config.ACTION_DELAY)
            await s.refresh()
        if _no_menu_principal(s.message):
            return
    await s.send_start()


async def limpar_historico(s: Session, manter: int = 1):
    """Apaga as mensagens ANTIGAS da conversa com o bot (mantém só as 'manter'
    mais recentes = a tela atual). Motivo (pedido do usuário 2026-07-03): sem
    isso, o histórico acumula telas VELHAS de combate que ainda têm botões, e
    um refresh futuro pode pegar uma delas -> o bot clica num botão expirado
    ('Encrypted data invalid') ou acha uma 'masmorra ativa' que já acabou.
    Chamada tanto no MENU (entre conteúdos) quanto agora TAMBÉM na transição
    lobby->combate e periodicamente DURANTE a luta (pedido do usuário
    2026-07-15, depois de um caso real onde o grupo parou de agir e foi
    morrendo — a hipótese é um refresh pegando um botão de tela antiga no
    meio de uma masmorra longa). Chamar durante o combate é seguro DESDE QUE
    logo depois de um s.refresh() (garante que 'manter=1' preserva
    exatamente a tela de combate atual, só apaga o que for mais velho que
    ela) — nunca no meio de um clique/confirmação em andamento.
    Best-effort: se o bot não deixar apagar algo, ignora. Apaga UM lote (até
    100) — o suficiente pra o bot não se confundir."""
    try:
        msgs = await s.client.get_messages(s.bot, limit=100)
        ids = [m.id for m in msgs[manter:]]
        if ids:
            await s.client.delete_messages(s.bot, ids, revoke=True)
            log(s.name, f"🧹 limpei {len(ids)} mensagem(ns) antiga(s) da conversa.")
    except Exception as e:
        log(s.name, f"(limpeza de histórico ignorada: {e!r})")


async def limpar_historico_completo(s: Session, max_lotes: int = 40):
    """Apaga TODO o histórico acumulado da conversa com o bot, em lotes de 100
    (o Telegram só deixa apagar 100 por vez). A limpeza normal (acima) só
    apaga as 100 mais recentes por vez — o suficiente pro bot, MAS deixa as
    mensagens BEM antigas de horas/dias visíveis no Telegram. Esta versão
    roda ao INICIAR o bot pra deixar a conversa realmente limpa (pedido do
    usuário 2026-07-03). Pausa curta entre lotes pra não tomar FloodWait.
    Limite de max_lotes (~4000 msgs) pra nunca travar eternamente."""
    apagadas = 0
    for _ in range(max_lotes):
        try:
            msgs = await s.client.get_messages(s.bot, limit=100)
        except Exception as e:
            log(s.name, f"(limpeza completa: erro ao ler — {e!r})")
            break
        ids = [m.id for m in msgs]
        if not ids:
            break
        try:
            await s.client.delete_messages(s.bot, ids, revoke=True)
            apagadas += len(ids)
        except Exception as e:
            log(s.name, f"(limpeza completa: erro ao apagar — {e!r})")
            break
        if len(ids) < 100:   # último lote
            break
        # pausa MAIOR entre lotes (1.5s) pra não concentrar requisições no
        # início (a limpeza profunda é só cosmética — não precisa ser rápida —
        # e as 4 contas rodam em paralelo; espaçar reduz o risco de FloodWait).
        await asyncio.sleep(1.5)
    if apagadas:
        log(s.name, f"🧹 limpei o histórico da conversa ({apagadas} mensagem(ns)).")


MAPA_ATUAL_RE = re.compile(r"atual:\s*(.+)", re.IGNORECASE)
# Item 9 (2026-07-16): o cabeçalho do MENU PRINCIPAL já mostra o mapa atual
# (ex: "🗺️ Cemitério Antigo (Lv 22)", "🏔️ Montanhas Gélidas (Lv 42)") — um
# formato BEM diferente do "Atual: X" de dentro da tela de Viajar (por isso
# parse_mapa_atual não reconhecia), o que fazia viajar_para SEMPRE abrir a
# lista de Viajar de verdade só pra descobrir que já estava no lugar certo
# (custo extra de cliques/refreshes à toa).
MAPA_MENU_RE = re.compile(r"^[^\w]*([^\(\n]+?)\s*\(lv\s*\d+\)", re.IGNORECASE)
MENU_MAPA_RE = re.compile(r"^[^\wÀ-ÿ]*(.+?)\s*\(lv\s*\d+\)", re.IGNORECASE)


def parse_mapa_do_menu(text: str):
    """Lê o nome do mapa atual direto da 1ª linha do MENU principal (ex:
    '🏜️ Oásis Perdido (Lv 35)  👥 156') — bem mais rápido que abrir a tela
    de Viajar só pra descobrir em qual mapa a conta está agora. None se essa
    linha não bater com o formato esperado (ex: não é a tela de menu)."""
    primeira_linha = (text or "").splitlines()[0] if text else ""
    m = MENU_MAPA_RE.match(primeira_linha)
    return m.group(1).strip() if m else None


def parse_mapa_atual(text: str):
    """Lê o mapa atual da tela de Viajar ('Atual: Deserto Escaldante') ou None."""
    for line in (text or "").splitlines():
        m = MAPA_ATUAL_RE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _no_menu_principal(message) -> bool:
    """True se a tela é o MENU principal (tem Caçar E Inventário juntos), NÃO a
    lista de Viajar. Serve pra não confundir o menu (que também mostra o mapa
    atual no topo) com a tela de Viajar de verdade."""
    return (find_button(message, "caçar", "cacar") is not None
            and find_button(message, "inventário", "inventario") is not None)


def parse_mapa_no_menu(text: str):
    """Item 9: lê o mapa atual direto do cabeçalho do MENU PRINCIPAL (1ª
    linha, formato '🗺️ Nome do Mapa (Lv N)') — SÓ deve ser chamado depois de
    já confirmar (via _no_menu_principal) que a tela é mesmo o menu, pra não
    confundir com nome de monstro ou outra coisa que também use '(Lv N)'."""
    linhas = (text or "").splitlines()
    if not linhas:
        return None
    m = MAPA_MENU_RE.search(linhas[0])
    return m.group(1).strip() if m else None


def _na_tela_viajar(s: "Session") -> bool:
    """Estamos na LISTA de Viajar? Ela mostra 'Atual: <mapa>' e não é o menu."""
    return parse_mapa_atual(s.text) is not None and not _no_menu_principal(s.message)


async def _abrir_viajar(s: "Session") -> bool:
    """Abre a lista de Viajar e ESPERA ela carregar de verdade. O 1º clique às
    vezes lê a tela velha (o menu) rápido demais — então confere e, se ainda
    estiver no menu, reclica 'Viajar'. True se a lista abriu."""
    for _ in range(3):
        if _na_tela_viajar(s):
            return True
        b = find_button(s.message, "viajar")
        if b is None:
            # sem botão 'Viajar' na tela: a mensagem pode estar desatualizada
            # (ex: logo após limpar o histórico da conversa) — dá UMA chance
            # a mais depois do refresh antes de desistir de vez.
            await s.refresh()
            if _na_tela_viajar(s):
                return True
            b = find_button(s.message, "viajar")
            if b is None:
                log(s.name, "⚠️ não achei o botão 'Viajar' no menu.")
                return False
        await s.click(b, label="Viajar")
        # espera a lista aparecer (checa já, depois vai dormindo até ~4s)
        for _ in range(6):
            if _na_tela_viajar(s):
                return True
            await poll_sleep()
            await s.refresh()
    return _na_tela_viajar(s)


async def viajar_para(s: Session, mapa: str) -> bool:
    """Vai pro Menu -> Viajar, lê o mapa ATUAL e, se for diferente de 'mapa',
    navega as páginas e clica no mapa escolhido pra viajar. Se já estiver nele,
    não faz nada. True se está no mapa certo ao final (ou já estava)."""
    if not mapa:
        return True
    alvo = norm(mapa)
    await back_to_menu(s)
    # Item 9 (2026-07-16): atalho — se já estamos mesmo no menu principal (não
    # confunde com outra tela que também tenha '(Lv N)'), tenta ler o mapa
    # atual direto do cabeçalho ANTES de abrir a lista de Viajar de verdade.
    # Só cai no fluxo antigo (abrir Viajar) se esse atalho não bater ou não
    # conseguir ler — evita o custo de cliques/refreshes extra só pra
    # confirmar onde a conta já está.
    await s.refresh()
    if _no_menu_principal(s.message):
        mapa_menu = parse_mapa_no_menu(s.text)
        if mapa_menu and norm(mapa_menu) == alvo:
            log(s.name, f"🗺️ já está em {mapa_menu} (confirmado pelo menu, sem abrir Viajar).")
            return True
    if not await _abrir_viajar(s):
        log(s.name, f"⚠️ não consegui abrir a lista de Viajar. "
                    f"botões: {button_texts(s.message)}")
        await s.click_text("menu", label="Menu", required=False)
        return False
    atual = parse_mapa_atual(s.text)
    if atual and norm(atual) == alvo:
        log(s.name, f"🗺️ já está em {atual}.")
        await s.click_text("menu", label="Menu", required=False)
        return True
    # BUG REAL 2026-07-16 (usuário: "só anda na seta pra direita, a esquerda
    # simplesmente ignora"): o código só sabia clicar 'Próximo' — se a lista
    # de Viajar abre já no meio (ex.: perto do mapa atual, não
    # necessariamente na página 1) e o mapa alvo está numa página ANTERIOR,
    # nunca era achado. Agora primeiro volta pra 1ª página de verdade
    # (clicando 'Anterior' até não ter mais botão de voltar), só depois
    # busca pra frente — garante que a busca cobre a lista inteira.
    for _ in range(6):
        anterior = find_button(s.message, "anterior", "voltar página", "◀️", "◀", "⬅️", "⬅")
        if not anterior:
            break
        await s.click(anterior, label="página anterior")
    # procura o botão do mapa navegando as páginas (Próximo). Máx ~6 páginas.
    # s.message já está fresco (o click de Próximo espera a página mudar).
    for _ in range(6):
        b = find_button(s.message, mapa)
        if b:
            if "🔒" in b.text or "cadeado" in norm(b.text):
                log(s.name, f"🔒 o mapa '{mapa}' está bloqueado (nível baixo?) — não viajei.")
                await s.click_text("menu", label="Menu", required=False)
                return False
            log(s.name, f"🗺️ viajando para {mapa}…")
            await s.click(b, label=b.text)
            await asyncio.sleep(config.ACTION_DELAY)
            return True
        prox = find_button(s.message, "proximo", "próximo", "➡️", "➡", "avancar", "avançar")
        if not prox:
            break
        await s.click(prox, label="Próximo")
    log(s.name, f"⚠️ não achei o mapa '{mapa}' na lista de Viajar. "
                f"botões: {button_texts(s.message)}")
    await s.click_text("menu", label="Menu", required=False)
    return False


async def garantir_skin_equipada(s: Session, skin: str) -> bool:
    """Garante que a conta está com a SKIN certa equipada antes de entrar
    numa masmorra alternativa que exige isso (ver
    config.MASMORRAS_ALTERNATIVAS -> 'skin'). Abre Menu -> Inventário ->
    Skins, confere se já está equipada (o botão 'Desequipar <skin>' marca a
    que está com ⭐ hoje) e, se não estiver, procura 'Equipar <skin>'
    navegando as páginas — aceita tanto a versão (F) quanto (M): o nome em
    'skin' vem SEM o sufixo de gênero, então casa com qualquer uma das duas.
    Chamada só 1x no início (a skin fica equipada, não precisa repetir a
    cada masmorra)."""
    skin_norm = norm(skin)
    await back_to_menu(s)
    if not await s.click_text("inventario", "inventário", label="Inventário"):
        log(s.name, "⚠️ não achei 'Inventário' pra trocar a skin.")
        return False
    if not await s.click_text("skins", label="Skins"):
        log(s.name, "⚠️ não achei 'Skins' no inventário.")
        await s.click_text("menu", label="Menu", required=False)
        return False
    # A tela de Skins pode abrir no MEIO/FIM da lista (não necessariamente na
    # 1ª página) — sem isso, se só existisse o botão "⬅️" (voltar) na tela
    # inicial, o código desistia sem nunca ter visto as páginas anteriores.
    # Aqui retrocede primeiro até não ter mais "⬅️", garantindo que a varredura
    # pra frente (abaixo) começa SEMPRE do início de verdade.
    for _ in range(12):
        await s.refresh()
        voltar = find_button(s.message, "⬅️", "⬅", "anterior")
        if not voltar:
            break
        await s.click(voltar, label="página anterior")
    for _ in range(12):
        await s.refresh()
        # já está com a skin certa? (botão 'Desequipar <skin>' = equipada)
        desequipar = find_button(s.message, "desequipar")
        if desequipar and skin_norm in norm(desequipar.text):
            log(s.name, f"🎨 já está com a skin certa ({desequipar.text}).")
            await s.click_text("menu", label="Menu", required=False)
            return True
        equipar = None
        for b in iter_buttons(s.message):
            bt = norm(b.text)
            # BUG REAL corrigido: o texto do botão vem com um emoji ANTES de
            # "Equipar" (ex: "🟠 Equipar Culpa de Altheryn (F) [Lv1]"), então
            # checar bt.startswith("equipar") nunca batia — o bot passava por
            # todas as páginas sem nunca reconhecer o botão, mesmo com ele
            # bem ali. Agora procura "equipar" em QUALQUER posição do texto,
            # só excluindo "desequipar" (que também contém "equipar" como
            # substring) pra não confundir os dois.
            if "equipar" in bt and "desequipar" not in bt and skin_norm in bt:
                equipar = b
                break
        if equipar:
            log(s.name, f"🎨 equipando skin: {equipar.text}")
            await s.click(equipar, label=equipar.text)
            await s.click_text("menu", label="Menu", required=False)
            return True
        prox = find_button(s.message, "proxima", "próxima", "➡️", "➡", "avancar", "avançar")
        if not prox:
            break
        await s.click(prox, label="próxima página")
    log(s.name, f"⚠️ não achei a skin '{skin}' no inventário (nem já equipada, "
                f"nem pra equipar). botões: {button_texts(s.message)}")
    await s.click_text("menu", label="Menu", required=False)
    return False


async def read_keys_at_menu(s: Session) -> int:
    """Volta pro menu e lê quantas Chaves de Masmorra a conta tem."""
    await back_to_menu(s)
    return keys_count(s.text)


async def heal_at_menu_if_low(s: Session, ratio=None):
    """
    No MENU (entre masmorras/caçadas), se o HP estiver ABAIXO de 'ratio', cura
    1 poção pelo Inventário: menu -> Inventário -> Poção de Vida -> volta.
    'ratio' (0-1): limite do reforço. Padrão = BETWEEN_DG_HEAL_RATIO (masmorra).
    A Caçada passa o seu HP% reforço (0 = desligado, não cura no menu).
    Defensivo: se não achar a poção, loga os botões e segue sem travar.
    """
    if ratio is None:
        ratio = config.BETWEEN_DG_HEAL_RATIO
    if ratio <= 0:
        return
    await back_to_menu(s)
    hp = player_hp(s.text, s.char)
    if not hp or hp[1] == 0 or hp[0] / hp[1] >= ratio:
        return
    log(s.name, f"🩹 HP {hp[0]}/{hp[1]} no menu — curando pelo Inventário antes da próxima.")
    # Navega menu -> Inventário -> Consumíveis -> Poção, avaliando a tela a cada
    # passo (robusto a timing). Ordem de checagem: poção > consumíveis > inventário.
    usou = False
    for _ in range(6):
        await s.refresh()
        pot = find_button(s.message, "pocao de vida", "poção de vida")
        if pot:
            await s.click(pot, label=pot.text)   # o botão já usa a poção direto
            log(s.name, "💚 usei Poção de Vida no Inventário.")
            usou = True
            break
        cons = find_button(s.message, "consumiveis", "consumíveis")
        if cons and find_button(s.message, "inventario", "inventário") is None:
            await s.click(cons, label="Consumíveis")   # estamos no Inventário
            continue
        inv = find_button(s.message, "inventario", "inventário")
        if inv:
            await s.click(inv, label="Inventário")     # estamos no menu principal
            continue
    if not usou:
        log(s.name, f"⚠️ não consegui usar a poção pelo Inventário. botões: {button_texts(s.message)}")
    await back_to_menu(s)


async def curar_repetido_no_menu(s: Session, alvo_ratio: float, max_tentativas: int = 8) -> None:
    """Depois de uma DERROTA, o jogo restaura um HP FIXO (ex: 52) que pode já
    estar bem abaixo do %HP configurado — se a conta voltasse a caçar direto
    nesse estado, um segundo golpe forte podia matar de novo quase na hora
    (visto de verdade num log real: 3 mortes na mesma conta em ~10 minutos).
    Essa função bebe Poção de Vida REPETIDAMENTE no Inventário até o HP ficar
    em 'alvo_ratio' ou mais (ou acabarem as poções/tentativas), ANTES de
    voltar a caçar."""
    if alvo_ratio <= 0:
        return
    await back_to_menu(s)
    for tentativa in range(max_tentativas):
        hp = player_hp(s.text, s.char)
        if not hp or hp[1] == 0:
            break
        cur, hp_max = hp
        if cur / hp_max >= alvo_ratio:
            if tentativa > 0:
                log(s.name, f"🩹 HP {cur}/{hp_max} ({cur / hp_max:.0%}) — seguro pra "
                            f"voltar a caçar.")
            return
        log(s.name, f"🩹 HP {cur}/{hp_max} ({cur / hp_max:.0%}) ainda abaixo de "
                    f"{alvo_ratio:.0%} depois de morrer — bebendo mais uma Poção de "
                    f"Vida antes de voltar a caçar.")
        usou = False
        for _ in range(6):
            await s.refresh()
            pot = find_button(s.message, "pocao de vida", "poção de vida")
            if pot:
                await s.click(pot, label=pot.text)
                usou = True
                break
            cons = find_button(s.message, "consumiveis", "consumíveis")
            if cons and find_button(s.message, "inventario", "inventário") is None:
                await s.click(cons, label="Consumíveis")
                continue
            inv = find_button(s.message, "inventario", "inventário")
            if inv:
                await s.click(inv, label="Inventário")
                continue
            await poll_sleep()
        if not usou:
            log(s.name, f"⚠️ sem mais Poção de Vida pra recuperar depois da morte. "
                        f"botões: {button_texts(s.message)}")
            break
        await back_to_menu(s)
    else:
        log(s.name, f"⚠️ bebi {max_tentativas} poção(ões) e ainda não cheguei em "
                    f"{alvo_ratio:.0%} — voltando a caçar mesmo assim.")


POCAO_QTD_RE = re.compile(r"vida\s*x\s*(\d+)", re.IGNORECASE)

# Tela de Perfil: "Lv 47  XP: 70669500  (Faltam: 16421863)" — o jogo já
# calcula "quanto falta pro próximo nível" sozinho, não precisa saber o
# total exigido por nível.
PERFIL_XP_RE = re.compile(r"lv\s*(\d+).*?xp\s*:?\s*([\d.,]+).*?faltam\s*:?\s*([\d.,]+)",
                          re.IGNORECASE | re.DOTALL)


async def ler_perfil(s: Session):
    """No MENU, vai em 'Perfil' e lê nível atual + XP faltando pro PRÓXIMO
    nível (formato 'Lv 47  XP: 70669500  (Faltam: 16421863)', confirmado por
    print do usuário 2026-07-15). Usado pra estimativa de tempo até subir de
    nível (ver estimar_tempo_ate_proximo_nivel). Retorna (nivel, xp_atual,
    xp_faltam) ou (None, None, None) se não conseguir confirmar a tempo."""
    await back_to_menu(s)
    for _ in range(6):
        await s.refresh()
        m = PERFIL_XP_RE.search(norm(s.text))
        if m:
            try:
                nivel = int(m.group(1))
                xp_atual = int(re.sub(r"[.,]", "", m.group(2)))
                xp_faltam = int(re.sub(r"[.,]", "", m.group(3)))
            except ValueError:
                nivel = xp_atual = xp_faltam = None
            await back_to_menu(s)
            return nivel, xp_atual, xp_faltam
        perfil = find_button(s.message, "perfil")
        if perfil:
            await s.click(perfil, label="Perfil")
            continue
        await poll_sleep()
    log(s.name, f"⚠️ não consegui ler o Perfil (nível/XP) a tempo. "
                f"botões: {button_texts(s.message)}")
    await back_to_menu(s)
    return None, None, None


def _taxa_xp_por_segundo_da_chave(chave_tempo: str):
    """Lê a média de tempo/XP por execução (rolling, últimas 'config.MEDIA_
    JANELA') pra essa chave (ex: 'caca_solo:Trrool') e calcula XP/segundo.
    None se não tiver dado suficiente ainda (0 execuções registradas com
    duração pra essa chave)."""
    dados = _ler_relatorio()
    lst_tempo = (dados.get("tempo_medio") or {}).get(chave_tempo)
    lst_xp = (dados.get("xp_medio") or {}).get(chave_tempo)
    if not lst_tempo or not lst_xp:
        return None
    media_tempo = sum(lst_tempo) / len(lst_tempo)
    media_xp = sum(lst_xp) / len(lst_xp)
    if media_tempo <= 0:
        return None
    return media_xp / media_tempo


async def atualizar_perfil_e_estimativa(s: Session, chave_tempo: str = None) -> None:
    """Lê o Perfil (nível/XP/faltam) e calcula a taxa de XP dessa conta,
    guardando em s._nivel/s._xp_faltam/s._eta_proximo_nivel_seg — o
    Brain.act() lê esses atributos a cada write_status(), sem precisar reler
    o Perfil toda hora (caro demais pra fazer a cada rodada).
    'chave_tempo' (opcional, pedido do usuário 2026-07-15: "caçada solo pode
    calcular a média igual a masmorra, é mais preciso"): quando informada
    (ex: 'caca_solo:Trrool', uma chave POR CONTA — cada uma mata num ritmo
    diferente) e já há histórico suficiente (ver
    _taxa_xp_por_segundo_da_chave), usa a média de XP/tempo por kill REAL
    em vez da estimativa genérica (baseada só na diferença entre duas
    leituras de Perfil, mais sujeita a ruído no início). Sem 'chave_tempo'
    ou sem histórico ainda pra ela (cai pro genérico automaticamente): usa
    a taxa OBSERVADA comparando a leitura de AGORA com a ANTERIOR guardada
    na própria sessão (funciona pra QUALQUER conteúdo, inclusive os que não
    têm uma 'execução' bem definida, tipo Missão Oásis — mistura de busca
    com caçada, com exceções, então não dá pra ter uma chave confiável)."""
    nivel, xp_atual, xp_faltam = await ler_perfil(s)
    agora = time.time()
    if nivel is not None and xp_atual is not None:
        xp_anterior = getattr(s, "_xp_perfil_anterior", None)
        ts_anterior = getattr(s, "_xp_perfil_anterior_ts", None)
        if xp_anterior is not None and ts_anterior is not None and agora > ts_anterior:
            delta_xp = xp_atual - xp_anterior
            delta_seg = agora - ts_anterior
            if delta_xp > 0 and delta_seg > 0:
                taxa_nova = delta_xp / delta_seg   # XP por segundo, OBSERVADO de verdade
                taxa_anterior = getattr(s, "_xp_por_segundo", None)
                s._xp_por_segundo = taxa_nova if taxa_anterior is None \
                    else (taxa_anterior * 0.5 + taxa_nova * 0.5)
        s._xp_perfil_anterior = xp_atual
        s._xp_perfil_anterior_ts = agora
    s._nivel = nivel
    s._xp_faltam = xp_faltam
    taxa = _taxa_xp_por_segundo_da_chave(chave_tempo) if chave_tempo else None
    if taxa is None:
        taxa = getattr(s, "_xp_por_segundo", None)
    if xp_faltam is not None and xp_faltam <= 0:
        s._eta_proximo_nivel_seg = 0
    elif taxa and taxa > 0 and xp_faltam is not None:
        s._eta_proximo_nivel_seg = xp_faltam / taxa
    else:
        s._eta_proximo_nivel_seg = None   # ainda sem taxa suficiente pra estimar (1ª leitura)
    if nivel is not None:
        xp_faltam_fmt = f"{xp_faltam:,}".replace(",", ".") if xp_faltam is not None else "?"
        eta_txt = (f" (~{_formatar_duracao(s._eta_proximo_nivel_seg)} estimado)"
                   if s._eta_proximo_nivel_seg else "")
        log(s.name, f"📊 Perfil: Lv {nivel}, faltam {xp_faltam_fmt} XP pro próximo nível{eta_txt}.")


POCAO_VIDA_MINIMA = 50


def _item_venda_info(texto_botao: str):
    """A partir do texto de um botão da tela 'Vender' (Loja), extrai (nome,
    tem_reforco, reforco):
      nome: nome "limpo" do item (sem bolinha/emoji, sem '(+N)', sem
            '[LvNN]', sem 'xQTD', sem preço) — usado pra casar com a lista
            marcada em config.MERCADO_ITENS.
      tem_reforco: True se esse item TEM conceito de reforço (equipamento
            ou alma — únicos que o usuário confirmou terem "+N"). Reconhecido
            por ter '[LvNN]' (equipamento) OU não ter quantidade 'xN'
            nenhuma (alma/item único, não empilha). Itens com 'xN' e SEM
            '[Lv' (poção, tônico, chave, minério, flor...) não têm reforço
            — o filtro config.MERCADO_REFORCOS não vale pra eles.
      reforco: o número dentro de '(+N)', ou 0 se o item não mostrar esse
            sufixo (inclui o caso de já estar em '+0' de verdade)."""
    t = texto_botao or ""
    tem_lv = bool(re.search(r"\[lv\s*\d+\]", t, re.IGNORECASE))
    tem_qtd = bool(re.search(r"\bx\d+\b", t, re.IGNORECASE))
    tem_reforco = tem_lv or not tem_qtd
    m_reforco = re.search(r"\(\+(\d+)\)", t)
    reforco = int(m_reforco.group(1)) if m_reforco else 0
    corte = len(t)
    for pad in (r"\(\+\d+\)", r"\[lv\s*\d+\]", r"\bx\d+\b", r"—"):
        mm = re.search(pad, t, re.IGNORECASE)
        if mm and mm.start() < corte:
            corte = mm.start()
    nome = t[:corte].strip()
    nome = re.sub(r"^[^\wÀ-ÿ]+", "", nome).strip()   # tira bolinha/ícone do início
    nome = re.sub(r"[✦\s]+$", "", nome).strip()       # tira estrelinhas de qualidade do fim (ex: Minério ✦✦)
    return nome, tem_reforco, reforco


_PREFIXOS_ACAO_INVENTARIO = ("desequipar", "equipar", "usar")   # 'des...' ANTES de 'equipar'


def _item_inventario_info(texto_sem_bolinha: str):
    """Como _item_venda_info, mas pro texto dos botões do INVENTÁRIO (ex:
    'Equipar Colar da Cabeça da Hidra (+1) [Lv22] (ATK+9, HP+7)', 'Usar
    Poção de Vida x47', 'Desequipar Machado do Deserto [Lv32]...') — tira
    também o verbo de ação (Equipar/Desequipar/Usar) do início, que a tela
    de Vender não tem."""
    t = re.sub(r"^[^\wÀ-ÿ]+", "", texto_sem_bolinha or "").strip()
    tl = norm(t)
    for prefixo in _PREFIXOS_ACAO_INVENTARIO:
        if tl.startswith(prefixo):
            t = t[len(prefixo):].strip()
            break
    return _item_venda_info(t)


async def ler_itens_inventario(s: Session) -> int:
    """LER INVENTÁRIO (pedido do usuário 2026-07-15: "coloca uma janelinha
    pra marcar 'ler itens do inventário', ele vai lá, vê os itens que tem e
    joga na lista do Mercado"): abre o Inventário e lê cada categoria
    (Armas, Armaduras, Joias, Almas, Consumíveis, Ferramentas), registrando
    todo item com bolinha de raridade no MESMO banco que já cresce sozinho
    vendo dropar (ver _registrar_itens_no_banco) — sem precisar esperar
    esses itens dropar de novo pra aparecerem na lista de venda. NUNCA
    clica em Equipar/Desequipar/Usar em nada, só LÊ o texto dos botões.
    Retorna quantos itens (distintos, por categoria/página) foram vistos."""
    dados = _ler_relatorio()
    categorias = ["armas", "armaduras", "joias", "almas", "consumiveis", "ferramentas"]
    total = 0
    for cat in categorias:
        await back_to_menu(s)
        if not await s.click_text("inventario", "inventário", label="Inventário", required=False):
            log(s.name, "⚠️ Ler inventário: não achei o botão 'Inventário'.")
            break
        await s.refresh()
        # BUG REAL 2026-07-16 (usuário notou: não lia as Armas): o Inventário
        # já abre direto na aba Armas — ali o botão 'Armas' pode nem existir
        # como algo clicável (já é a aba ativa), então click_text falhava e
        # o 'continue' antigo pulava a leitura dessa categoria por completo.
        # Agora SEMPRE lê o que estiver na tela, tenha o clique na aba
        # funcionado ou não (pior caso: reler uma categoria à toa, o que é
        # inofensivo — bem melhor que perder uma categoria inteira).
        await s.click_text(cat, required=False)
        await s.refresh()
        for _pagina in range(20):
            raridades = {}
            for b in iter_buttons(s.message):
                bt = b.text or ""
                cor = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in bt), None)
                if not cor:
                    continue
                sem_bolinha = bt
                for emoji in EMOJI_RARIDADE:
                    sem_bolinha = sem_bolinha.replace(emoji, "")
                nome, _tem_reforco, _reforco = _item_inventario_info(sem_bolinha)
                if nome:
                    raridades[nome] = cor
            if raridades:
                _registrar_itens_no_banco(dados, raridades, origem="inventario")
                total += len(raridades)
            # Usa o MESMO padrão já comprovado em outras telas do bot (ex.:
            # linha ~2749) — só o emoji '➡' às vezes não bate com o glifo
            # exato dessa tela; procurando também as palavras "próxima"/
            # "avançar" cobre isso.
            avancar = find_button(s.message, "proxima", "próxima", "➡️", "➡", "avancar", "avançar")
            if not avancar:
                break
            await s.click(avancar, label="próxima página")
    _salvar_relatorio(dados)
    await back_to_menu(s)
    log(s.name, f"📦 Ler inventário: {total} item(ns) registrado(s)/atualizado(s) no banco do Mercado.")
    return total


async def vender_itens_mercado(s: Session) -> int:
    """MERCADO (pedido do usuário 2026-07-15): navega Menu -> Loja -> (o
    primeiro mercador disponível — vender é do INVENTÁRIO do jogador, não
    depende de qual loja) -> Vender, e marca pra vender todo item cujo nome
    bata com config.MERCADO_ITENS — respeitando config.MERCADO_REFORCOS
    (+0/+1/+2/+3) SÓ pra equipamento/alma (ver _item_venda_info). Percorre
    TODAS as páginas (as seleções persistem entre elas, confirmado pelo
    usuário via print) e confirma a venda em lote no final ('SIM, VENDER
    TUDO'). Retorna quantos itens foram marcados/vendidos (0 se nada bateu
    ou algo falhou — nesse caso volta pro menu sem vender nada)."""
    itens_alvo = {norm(n) for n in (getattr(config, "MERCADO_ITENS", None) or []) if n}
    if not itens_alvo:
        return 0
    reforcos_ok = set(getattr(config, "MERCADO_REFORCOS", None) or [0, 1, 2, 3])
    # Quase todo mapa já mostra 'Loja' direto no menu principal (confirmado
    # pelo usuário via print — só o Oásis Perdido/Vale das Miragens não tem).
    # Então só viaja se o botão realmente NÃO aparecer aqui — não precisa
    # checar o mapa atual toda vez, é só tentar 'Loja' primeiro.
    mapa_venda = getattr(config, "MERCADO_MAPA_VENDA", "Floresta Sombria")
    mapas_sem_mercador = {norm(m) for m in (getattr(config, "MERCADO_MAPAS_SEM_MERCADOR", None) or [])}
    await back_to_menu(s)

    async def _achar_mercador_aqui():
        """Clica em 'Loja' (existe em quase todo mapa) e procura uma opção
        de mercador de VERDADE na tela de escolha ('Loja da Vila'/
        'Mercador...'). Print do usuário confirmou: o Oásis Perdido também
        tem o botão 'Loja', mas a tela só oferece 'Matadores' e 'Castelo' —
        nenhum dos dois vende nada, só o mercador de verdade vende.
        Retorna o botão do mercador achado, ou None (mapa sem mercador)."""
        if not await s.click_text("loja", label="Loja", required=False):
            return None
        await s.refresh()
        for _ in range(4):
            btn = next((b for b in iter_buttons(s.message)
                       if "mercador" in norm(b.text) or "loja da vila" in norm(b.text)), None)
            if btn:
                return btn
            # essa tela não tem paginação nem demora normalmente — só uma
            # segunda checagem rápida, caso o 1º refresh tenha vindo cedo demais
            await poll_sleep()
            await s.refresh()
        return None

    # Checa o mapa direto na 1ª linha do MENU (rápido — sem abrir Viajar nem
    # clicar em Loja à toa) — se já sabemos que esse mapa não tem mercador
    # (config.MERCADO_MAPAS_SEM_MERCADOR, ex: Oásis Perdido), nem perde tempo
    # tentando 'Loja' aqui, já vai direto pro mapa de venda.
    mapa_menu_atual = parse_mapa_do_menu(s.text)
    sem_mercador_conhecido = bool(mapa_menu_atual) and norm(mapa_menu_atual) in mapas_sem_mercador
    loja_btn = None if sem_mercador_conhecido else await _achar_mercador_aqui()
    mapa_original = None
    precisa_voltar = False
    if not loja_btn:
        if sem_mercador_conhecido:
            mapa_original = mapa_menu_atual
        else:
            await s.click_text("menu", label="Menu", required=False)   # sai da tela de Lojas sem escolher nada
            if await _abrir_viajar(s):
                mapa_original = parse_mapa_atual(s.text)
            await s.click_text("menu", label="Menu", required=False)
        precisa_voltar = bool(mapa_original) and norm(mapa_original) != norm(mapa_venda)
        log(s.name, f"🗺️ Mercado: '{mapa_original or 'esse mapa'}' não tem um mercador de "
                    f"verdade — indo pra '{mapa_venda}' vender, depois volta.")
        if not await viajar_para(s, mapa_venda):
            log(s.name, f"⚠️ Mercado: não consegui viajar pra {mapa_venda} pra vender.")
            return 0
        # A viagem pode deixar a conta numa tela de transição por um instante
        # (confirmado pelo usuário via log: 'não achei o botão Loja' logo
        # depois de chegar) — garante que voltou pro MENU de verdade antes
        # de tentar clicar em qualquer coisa.
        await back_to_menu(s)
        loja_btn = await _achar_mercador_aqui()
        if not loja_btn:
            log(s.name, f"⚠️ Mercado: '{mapa_venda}' também não tem mercador — desisti.")
            if precisa_voltar:
                await viajar_para(s, mapa_original)
            return 0
    try:
        await s.click(loja_btn, label=loja_btn.text)
        await s.refresh()
        if not await s.click_text("vender", label="Vender", required=False):
            log(s.name, "⚠️ Mercado: não achei o botão 'Vender'.")
            return 0
        marcados = 0
        # BUG REAL 2026-07-16 (log do usuário: 45 cliques seguidos no MESMO
        # item, sem nunca sair da página 1): o skip de "já marcado" só
        # reconhecia o glifo '✅' — o jogo pode usar outro glifo de "marcado"
        # nesse botão (ex.: '☑'/'✔️'), então o código nunca via o item como
        # já selecionado, clicava de novo (o que DESMARCA), e ficava nesse
        # ciclo pra sempre. Agora, além de reconhecer mais variantes de
        # "marcado", o bot também lembra o que ELE MESMO já clicou nesta
        # execução e nunca repete — trava o loop de vez, independente de
        # qual glifo exato o jogo usa.
        # AJUSTE 2026-07-16 (usuário relatou: 2 cópias do MESMO item na loja,
        # só vendeu 1): a 1ª versão dessa trava usava (nome, reforço) como
        # identidade — mas isso faz DUAS cópias idênticas do mesmo item
        # parecerem "a mesma coisa" pro bot, então a 2ª nunca era marcada.
        # Agora usa o callback_data bruto do botão (um ID interno do jogo,
        # único por item de verdade, mesmo que dois tenham nome e reforço
        # iguais) como identidade — só cai pra (nome, reforço) se por algum
        # motivo esse dado não vier no botão.
        _GLIFOS_MARCADO = ("✅", "☑", "✔", "🗹")
        ja_marcados = set()

        def _chave_botao(b, nome, reforco):
            dados_cb = getattr(getattr(b, "button", None), "data", None)
            if dados_cb:
                return ("cb", dados_cb)
            return ("nome", norm(nome), reforco)

        async def _marcar_pagina_atual(rotulo_pagina):
            """Marca TODOS os itens que baterem na página atual (rescaneando
            depois de cada clique, já que a lista pode reordenar). Retorna
            quantos itens foram marcados nesta passada por essa página."""
            nonlocal marcados
            marcados_aqui = 0
            mudou = True
            while mudou:
                mudou = False
                for b in iter_buttons(s.message):
                    bt = b.text or ""
                    btl = bt.strip()
                    if norm(bt).startswith("vender selecionados") or btl.startswith(_GLIFOS_MARCADO):
                        continue
                    nome, tem_reforco, reforco = _item_venda_info(bt)
                    if not nome or norm(nome) not in itens_alvo:
                        continue
                    if tem_reforco and reforco not in reforcos_ok:
                        continue
                    chave = _chave_botao(b, nome, reforco)
                    if chave in ja_marcados:
                        continue
                    ja_marcados.add(chave)
                    await s.click(b, label=f"marcar p/ vender: {bt}")
                    marcados += 1
                    marcados_aqui += 1
                    # Log de progresso (pedido do usuário 2026-07-16: com a API do
                    # Telegram lenta, cada clique pode levar 12s+ pra confirmar —
                    # sem isso, minutos passavam sem NENHUMA linha nova no log,
                    # e parecia que tinha travado quando só tava devagar mesmo.
                    log(s.name, f"🛒 Mercado: marcado p/ vender — {nome}"
                                f"{f' (+{reforco})' if tem_reforco else ''} "
                                f"({rotulo_pagina}, total nesta execução: {marcados}).")
                    mudou = True
                    await s.refresh()
                    break   # a lista pode reordenar após marcar — refaz a varredura
            return marcados_aqui

        def _botao_pagina(anterior: bool):
            """Acha o botão de virar página (Anterior/Próxima) SEM confundir
            com o '⬅ Lojas' (voltar/sair da loja) — esse botão também usa
            seta pra esquerda, e na página 1 (sem 'Anterior' de verdade pra
            clicar) a busca genérica acabava clicando nele e saindo do
            mercado inteiro. Agora ignora qualquer botão com 'loja' no texto."""
            glifos = ("◀️", "◀", "⬅️", "⬅") if anterior else ("➡️", "➡")
            palavras = ("anterior",) if anterior else ("proxima", "próxima", "avancar", "avançar")
            for b in iter_buttons(s.message):
                t = b.text or ""
                tn = norm(t)
                if "loja" in tn:
                    continue
                if any(g in t for g in glifos) or any(p in tn for p in palavras):
                    return b
            return None

        # Garante que a varredura sempre começa da 1ª página de verdade —
        # a tela de Vender pode abrir já no meio (perto de onde parou da
        # última vez), então volta com 'Anterior' até não ter mais como
        # voltar, só depois varre pra frente até o fim.
        await s.refresh()
        for _ in range(12):
            anterior = _botao_pagina(anterior=True)
            if not anterior:
                break
            await s.click(anterior, label="página anterior")

        for _pagina in range(12):
            await s.refresh()
            await _marcar_pagina_atual(f"pág. {_pagina + 1}")
            avancar = _botao_pagina(anterior=False)
            if not avancar:
                break
            log(s.name, f"🛒 Mercado: página {_pagina + 1} concluída "
                        f"({marcados} marcado(s) até agora) — indo pra próxima…")
            await s.click(avancar, label="próxima página")
        await s.refresh()
        vender_btn = find_button(s.message, "vender selecionados")
        if not vender_btn or marcados == 0:
            log(s.name, "🛒 Mercado: nenhum item novo pra vender neste ciclo.")
            return 0
        await s.click(vender_btn, label=vender_btn.text)
        await s.refresh()
        delay_confirmacao = max(0.0, getattr(config, "MERCADO_DELAY_CONFIRMACAO_SEG", 10.0))
        if delay_confirmacao:
            log(s.name, f"🛒 Mercado: {marcados} item(ns) marcado(s) — esperando "
                        f"{delay_confirmacao:.0f}s antes de confirmar a venda "
                        f"(dá tempo de conferir e clicar em 'Parar' se algo tiver "
                        f"marcado errado).")
            await asyncio.sleep(delay_confirmacao)
        if await s.click_text("sim, vender tudo", "sim vender tudo", label="Confirmar venda", required=False):
            log(s.name, f"🛒 Mercado: vendeu {marcados} item(ns).")
            return marcados
        log(s.name, "⚠️ Mercado: cliquei em 'Vender selecionados' mas não achei a confirmação.")
        return 0
    finally:
        await back_to_menu(s)
        if precisa_voltar:
            log(s.name, f"🗺️ Mercado: voltando pra '{mapa_original}'.")
            await viajar_para(s, mapa_original)


async def talvez_vender_no_mercado(s: Session) -> None:
    """Chamado nos pontos 'a conta está livre' (entre execuções de cada
    conteúdo — nunca no meio de uma masmorra/caçada, pedido do usuário) de
    TODOS os modos. Vende de verdade se QUALQUER um destes bater:
      1) Pedido MANUAL ('🛒 Vender agora' no painel, ver
         vender_agora_timestamp) mais NOVO que o último que esta conta já
         atendeu — dispara MESMO com o Mercado desativado ou fora do
         intervalo normal, e por si só "desliga sozinho" depois (não
         dispara de novo pra essa conta até alguém clicar outra vez).
      2) Mercado ATIVO, essa conta marcada em config.MERCADO_CONTAS, e já
         passou config.MERCADO_INTERVALO_MIN minutos desde a última venda
         dessa conta (o ciclo automático de sempre)."""
    pedido_ts = vender_agora_timestamp()
    veio_de_pedido_manual = bool(pedido_ts) and pedido_ts > getattr(s, "_ultimo_pedido_venda_atendido", 0)
    contas_ok = getattr(config, "MERCADO_CONTAS", None) or []
    if contas_ok and s.acc.get("phone") not in contas_ok and s.name not in contas_ok:
        return
    if not veio_de_pedido_manual:
        if not getattr(config, "MERCADO_ATIVO", False):
            return
        agora = time.time()
        ultima = getattr(s, "_ultima_venda_mercado", 0)
        intervalo_seg = max(60, getattr(config, "MERCADO_INTERVALO_MIN", 30) * 60)
        if agora - ultima < intervalo_seg:
            return
    s._ultima_venda_mercado = time.time()
    if veio_de_pedido_manual:
        s._ultimo_pedido_venda_atendido = pedido_ts
        log(s.name, "🛒 Mercado: atendendo pedido manual de 'Vender agora'.")
    try:
        await vender_itens_mercado(s)
    except Exception as e:
        log(s.name, f"(Mercado: erro ao tentar vender: {e!r})")


async def talvez_ler_inventario(s: Session) -> None:
    """Chamado nos mesmos pontos 'a conta está livre' que talvez_vender_no_
    mercado — só lê o inventário quando há um pedido MANUAL pendente ('📦
    Ler inventário agora' no painel, ver ler_inventario_timestamp) mais
    NOVO que o último que esta conta já atendeu. Sem intervalo automático
    de propósito (ler inventário é bem mais raro de precisar do que vender —
    só quando o usuário quer popular a lista do Mercado na mão)."""
    pedido_ts = ler_inventario_timestamp()
    if not pedido_ts or pedido_ts <= getattr(s, "_ultimo_pedido_inventario_atendido", 0):
        return
    contas_ok = getattr(config, "MERCADO_CONTAS", None) or []
    if contas_ok and s.acc.get("phone") not in contas_ok and s.name not in contas_ok:
        return
    s._ultimo_pedido_inventario_atendido = pedido_ts
    log(s.name, "📦 Mercado: atendendo pedido manual de 'Ler inventário agora'.")
    try:
        await ler_itens_inventario(s)
    except Exception as e:
        log(s.name, f"(Ler inventário: erro — {e!r})")


async def contar_pocoes_vida(s: Session):
    """No MENU, vai em Inventário > Consumíveis e lê quantas Poção de Vida
    tem (do texto do botão, ex 'Usar Poção de Vida x47').
    Retorna None se NÃO CONSEGUIU CONFIRMAR (não achou o botão a tempo —
    ex: tela ainda carregando, conta num estado inesperado). NÃO usar None
    como se fosse 0: 0 só quando o botão foi achado e realmente diz zero.
    (bug real reportado pelo usuário 2026-07-03: o arqueiro tinha poção de
    vida de verdade, mas o bot logou "0" porque não achou o botão a tempo —
    quem chama precisa tratar None como 'não sei' e NÃO pausar por isso.)"""
    await back_to_menu(s)
    for _ in range(6):
        await s.refresh()
        pot = find_button(s.message, "pocao de vida", "poção de vida")
        if pot:
            m = POCAO_QTD_RE.search(norm(pot.text))
            # ACHOU o botão mas SEM o "xN" no texto (tela ainda montando)?
            # Isso NÃO é estoque zero — é leitura não confirmada. Retorna None
            # (bug real 2026-07-03: suporte tinha 676 poções e o bot leu "0"
            # porque o botão veio sem o número num refresh, pausando à toa).
            if not m:
                continue
            await back_to_menu(s)
            return int(m.group(1))
        cons = find_button(s.message, "consumiveis", "consumíveis")
        if cons and find_button(s.message, "inventario", "inventário") is None:
            await s.click(cons, label="Consumíveis")
            continue
        inv = find_button(s.message, "inventario", "inventário")
        if inv:
            await s.click(inv, label="Inventário")
            continue
        await poll_sleep()
    log(s.name, f"⚠️ não consegui confirmar a quantidade de Poção de Vida "
                f"(não achei o botão a tempo). botões: {button_texts(s.message)}")
    await back_to_menu(s)
    return None


async def pocoes_vida_ok(sessions, minimo=POCAO_VIDA_MINIMA) -> bool:
    """Checa TODAS as contas antes de iniciar masmorra/caçada: se ALGUMA
    tiver menos que 'minimo' Poções de Vida, pausa o bot (retorna False e já
    registra o motivo). Também guarda o estoque em s.pocoes_estimadas (usado
    pra rastrear durante a Caçada em Dupla). EM PARALELO (cada conta lê o
    próprio Inventário ao mesmo tempo) — bem mais rápido que uma de cada vez."""
    async def _checar(s):
        qtd = await contar_pocoes_vida(s)
        if qtd is not None:
            s.pocoes_estimadas = qtd
        log(s.name, f"🧪 Poções de Vida no estoque: {qtd if qtd is not None else 'não confirmado'}.")
        return s, qtd
    resultados = await asyncio.gather(*(_checar(s) for s in sessions))
    ok = True
    for s, qtd in resultados:
        # None = não conseguiu ler (NÃO é a mesma coisa que 0 de verdade) —
        # não pausa por causa disso, só avisa e segue (bug real corrigido
        # 2026-07-03: uma conta com poção de sobra foi pausada por engano
        # porque a leitura falhou e "0" foi tratado como estoque zerado).
        if qtd is not None and qtd < minimo:
            log(s.name, f"⚠️ menos de {minimo} Poções de Vida — pausando o bot "
                        f"antes de iniciar. Reponha o estoque e clique Iniciar de novo.")
            registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd} poções")
            ok = False
    return ok


async def wait_combat_started(s: Session) -> bool:
    for _ in range(int(config.LOBBY_TIMEOUT / config.POLL_INTERVAL)):
        await s.refresh()
        if is_combat_screen(s.message):
            # LIMPEZA no exato momento de transição lobby -> combate (o
            # instante de MAIOR risco de clicar num botão velho: confirmado
            # pelo usuário via log — 'Encrypted data invalid' bem aqui,
            # porque a tela de lobby ainda tinha um botão "vivo" no
            # histórico). manter=1 preserva a tela de combate que acabamos
            # de confirmar, só apaga o que for mais velho que ela.
            await limpar_historico(s)
            return True
        await poll_sleep()
    return False


# ---------------------------------------------------------------------
#  Caçada em Dupla — conteúdo SEPARADO da Masmorra (nível mínimo 42),
#  2 contas, gasta Energia (não Chave), avança 1 andar por mob morto.
#  Confirmado por prints do usuário 2026-07-01: Menu -> "Caçar" -> escolhe
#  "Criar Caçada..." (host) ou "Entrar em Ca..." (2ª conta) -> lobby com
#  código "🔑 Código: XXXXXX" -> combate com os MESMOS botões da masmorra
#  (Atacar/Defender/Consumíveis/Almas/Sair). NÃO confirmado por print: como
#  a 2ª conta digita o código (sem tela disso ainda) — melhor esforço abaixo,
#  com log bem detalhado se não bater.
# ---------------------------------------------------------------------

CACA_CODIGO_RE = re.compile(r"c[oó]digo:\s*([A-Za-z0-9]{4,10})", re.IGNORECASE)


def find_caca_code(text: str):
    m = CACA_CODIGO_RE.search(text or "")
    return m.group(1) if m else None


async def open_cacar(s: Session):
    """Chega na tela 'Escolha o modo de caçada' (Menu -> Caçar). Retry
    robusto a timing, no mesmo padrão de open_masmorra."""
    for _ in range(7):
        await s.refresh()
        if find_button(s.message, "criar cacada", "criar caçada") or \
           find_button(s.message, "entrar em cacada", "entrar em caçada"):
            return True
        # Presa numa sala/combate de uma caçada anterior (ex: depois de um
        # reinício automático)? Sai dela primeiro pra conseguir voltar ao menu.
        if is_combat_screen(s.message) or find_button(s.message, "sair e receber"):
            await leave_room(s)
            continue
        mb = find_button(s.message, "menu")
        if mb:
            await s.click(mb, label="Menu")
            continue
        cc = find_button(s.message, "cacar", "caçar")
        if cc:
            await s.click(cc, label="Caçar")
            continue
        if await _tentar_evitar_start(s):
            continue
        await s.send_start()
    return find_button(s.message, "criar cacada", "criar caçada") is not None


async def host_criar_cacada(s: Session):
    """HOST: cria a Caçada em Dupla e devolve o código do lobby."""
    if not await open_cacar(s):
        log(s.name, "❌ não cheguei na tela de Caçar (host da caçada).")
        return None
    if not await s.click_text("criar cacada", "criar caçada", label="Criar Caçada"):
        return None
    await s.refresh()
    code = find_caca_code(s.text)
    if code:
        log(s.name, f"✅ caçada criada. Código: {code}")
    else:
        log(s.name, f"⚠️ criei a caçada mas não achei o código.\n    texto: {s.text}")
    return code


async def joiner_entrar_cacada(s: Session, code: str):
    """2ª CONTA: entra na Caçada em Dupla com o código do host.
    MELHOR ESFORÇO (sem print confirmado): tenta achar um botão com o
    código (lista, igual masmorra); se não achar, manda o código como
    mensagem de texto (bots do Telegram costumam aceitar assim quando não
    há teclado pronto). Loga o texto exato se nada bater, pra ajustar depois."""
    if not await open_cacar(s):
        log(s.name, "❌ não cheguei na tela de Caçar (join da caçada).")
        return False
    if not await s.click_text("entrar em cacada", "entrar em caçada", label="Entrar em Caçada"):
        return False
    await s.refresh()
    alvo = find_button(s.message, code)
    if alvo:
        await s.click(alvo, label=f"caçada {code}")
        log(s.name, "✅ entrei na caçada (cliquei o código na lista).")
        return True
    log(s.name, f"ℹ️ não achei botão com o código '{code}' — vou tentar mandar como "
                f"texto. Tela atual:\n    {s.text}\n    botões: {button_texts(s.message)}")
    _mudou, _msg_codigo = await s.send_text(code)
    await asyncio.sleep(config.ACTION_DELAY)
    await s.refresh()
    if "aguardando" in norm(s.text) or is_combat_screen(s.message) or "jogadores" in norm(s.text):
        log(s.name, "✅ entrei na caçada (digitei o código como mensagem).")
        # Apaga o texto do código que a PRÓPRIA conta mandou — sem isso, ele
        # fica pra sempre acumulado na conversa (print do usuário 2026-07-15
        # mostrou vários códigos velhos ainda visíveis no histórico).
        try:
            await _msg_codigo.delete()
        except Exception as e:
            log(s.name, f"(não consegui apagar a mensagem do código: {e!r})")
        return True
    log(s.name, f"❌ não confirmei entrada na caçada — verificar print desta tela.\n"
                f"    texto: {s.text}\n    botões: {button_texts(s.message)}")
    return False


async def host_iniciar_cacada(s: Session) -> bool:
    """HOST: com a dupla no lobby (2/2), clica 'Iniciar Caçada!' pra começar.
    (Sem esse passo o lobby ficava parado esperando — bug reportado.)"""
    for _ in range(int(config.LOBBY_TIMEOUT / config.POLL_INTERVAL)):
        await s.refresh()
        if is_combat_screen(s.message):
            return True
        b = find_button(s.message, "iniciar cacada", "iniciar caçada", "iniciar")
        if b:
            await s.click(b, label="Iniciar Caçada")
            return True
        await poll_sleep()
    log(s.name, "⚠️ não achei o botão 'Iniciar Caçada' no lobby.")
    return False


async def read_energia_at_menu(s: Session):
    """Volta pro menu e lê a Energia atual (atual, máxima), ou None."""
    await back_to_menu(s)
    return energia_atual(s.text)


async def energia_reforco_se_baixo(s: Session, energia_minima: int, pocoes_reforco: int) -> bool:
    """No MENU, ao final de uma caçada: se a energia estiver abaixo de
    'energia_minima', bebe 'pocoes_reforco' Poções de Energia pelo
    Inventário (mesmo padrão de heal_at_menu_if_low, com Poção de Energia).
    Retorna False se precisava beber e NÃO conseguiu (acabaram as poções) —
    quem chama deve pausar o bot nesse caso."""
    await back_to_menu(s)
    en = energia_atual(s.text)
    if not en or en[0] >= energia_minima:
        return True
    log(s.name, f"⚡ energia {en[0]}/{en[1]} abaixo de {energia_minima} — bebendo "
                f"{pocoes_reforco} Poção(ões) de Energia.")
    bebidas = 0
    for _ in range(pocoes_reforco):
        usou = False
        for _ in range(6):
            await s.refresh()
            pot = find_button(s.message, "pocao de energia", "poção de energia")
            if pot:
                await s.click(pot, label=pot.text)
                usou = True
                break
            cons = find_button(s.message, "consumiveis", "consumíveis")
            if cons and find_button(s.message, "inventario", "inventário") is None:
                await s.click(cons, label="Consumíveis")
                continue
            inv = find_button(s.message, "inventario", "inventário")
            if inv:
                await s.click(inv, label="Inventário")
                continue
        if not usou:
            log(s.name, f"⚠️ não consegui achar Poção de Energia. botões: {button_texts(s.message)}")
            break
        bebidas += 1
        await back_to_menu(s)
    log(s.name, f"⚡ bebi {bebidas}/{pocoes_reforco} Poção(ões) de Energia.")
    return bebidas >= pocoes_reforco


async def energia_encher_ate(s: Session, energia_alvo: int) -> bool:
    """No MENU: bebe Poções de Energia (uma por vez, pelo Inventário) até a
    energia chegar em 'energia_alvo' (ou acabarem as poções). Usado pela
    Caçada Solo (diferente da Caçada em Dupla, que bebe uma QUANTIDADE fixa —
    aqui é 'encher até X', pedido do usuário). Retorna False se ainda ficou
    abaixo do alvo por falta de poção (quem chama deve pausar nesse caso)."""
    await back_to_menu(s)
    en = energia_atual(s.text)
    if not en:
        return True
    bebidas = 0
    while en and en[0] < energia_alvo:
        usou = False
        for _ in range(6):
            await s.refresh()
            pot = find_button(s.message, "pocao de energia", "poção de energia")
            if pot:
                await s.click(pot, label=pot.text)
                usou = True
                break
            cons = find_button(s.message, "consumiveis", "consumíveis")
            if cons and find_button(s.message, "inventario", "inventário") is None:
                await s.click(cons, label="Consumíveis")
                continue
            inv = find_button(s.message, "inventario", "inventário")
            if inv:
                await s.click(inv, label="Inventário")
                continue
        if not usou:
            log(s.name, f"⚠️ não consegui achar Poção de Energia (parei em {en[0]}/{en[1]}).")
            break
        bebidas += 1
        await back_to_menu(s)
        en = energia_atual(s.text)
    log(s.name, f"⚡ bebi {bebidas} Poção(ões) de Energia — energia agora: "
                f"{en[0] if en else '?'}/{en[1] if en else '?'}.")
    return bool(en and en[0] >= energia_alvo)


# ---------------------------------------------------------------------
#  CAÇADA SOLO (cada conta caça sozinha, em paralelo, sem sala/parceiro)
# ---------------------------------------------------------------------

def is_combat_screen_solo(message) -> bool:
    """Combate da Caçada Solo: tem 'Atacar' e 'Almas', mas NÃO 'Defender' —
    diferente da Masmorra/Caçada em Dupla (que são por rodada, com Defender).
    Cada clique resolve na hora, sem esperar ampulheta/turno."""
    return (find_button(message, "atacar") is not None
            and find_button(message, "defender") is None
            and find_button(message, "almas") is not None)


def is_sem_energia_solo(text: str) -> bool:
    return "sem energia para cacar" in norm(text or "")


def is_sem_energia_trilha(text: str) -> bool:
    """Tela 'Sem energia para explorar a Trilha X' (Vale das Miragens, Missão
    Oásis) — BUG REAL corrigido 2026-07-16 (usuário: contas ficaram presas em
    loop, nunca voltaram ao Oásis pra recuperar energia): o código que
    escolhe a trilha (Trilha Instável/Silenciosa) nunca conferia a energia
    antes de entrar; ao zerar, caía nessa tela (só com botão 'Voltar'), o
    fallback genérico clicava Voltar, e o código escolhia a MESMA trilha de
    novo sem checar energia — loop infinito. Frase diferente da usada na
    Caçada Solo ('sem energia para caçar'), por isso não era reconhecida."""
    return "sem energia para explorar" in norm(text or "")


def is_fuga_solo(text: str) -> bool:
    """Tela de 'Você fugiu da batalha' (depois de act_fugir) — só tem botão
    'Menu', nenhum dos outros marcadores (vitória/armadilha/combate/energia).
    SEM esse reconhecimento, a tela cai no fallback de 'não reconheci' e o
    bot fica mandando /start à toa em vez de só clicar Menu e continuar."""
    return "fugiu da batalha" in norm(text or "")


def is_armadilha_solo(text: str) -> bool:
    return "armadilha" in norm(text or "")


def is_vitoria_solo(text: str) -> bool:
    return "vitoria" in norm(text or "")


def is_derrota_solo(text: str) -> bool:
    """Tela de MORTE na Caçada Solo/Missão Oásis ('💀 DERROTA\\nPerdeu X XP.\\n
    HP restaurado para Y.', só com botão 'Menu'). ANTES disso não existia —
    a tela caía no fallback genérico de 'tela não reconhecida' e só
    aparecia no log por acidente (o dump de texto cru desse fallback),
    sem o bot saber de verdade que a conta tinha morrido nem contra qual
    monstro.
    BUG REAL CORRIGIDO: a 1ª versão fazia 'derrota' in texto (substring
    solta) — isso confundia com a palavra 'derrotados' (que aparece
    normalmente na tela de STATUS do Sunred: 'Monstros derrotados:
    189/200'), fazendo o bot achar que tinha morrido nessa tela, tentar
    clicar 'Menu' (que não existe ali), falhar, cair num /start, e voltar
    pra MESMA tela do Sunred — um loop infinito de falsa morte. Agora exige
    a palavra 'derrota' ISOLADA (\\bderrota\\b, não casa com 'derrotados')
    E 'restaurado' no mesmo texto (só aparece na tela de morte de verdade)."""
    n = norm(text or "")
    return bool(re.search(r"\bderrota\b", n)) and "restaurado" in n


DERROTA_SOLO_RE = re.compile(
    r"perdeu\s+([\d.,]+)\s*xp.*?restaurado\s+para\s+(\d+)", re.IGNORECASE | re.DOTALL)


def parse_derrota_solo(text: str):
    """Extrai (xp_perdido, hp_restaurado) da tela de derrota. Retorna
    (None, None) se não achar (formato mudou/tela diferente)."""
    m = DERROTA_SOLO_RE.search(text or "")
    if not m:
        return None, None
    try:
        xp_perdido = int(m.group(1).replace(".", "").replace(",", ""))
    except ValueError:
        xp_perdido = None
    try:
        hp_restaurado = int(m.group(2))
    except ValueError:
        hp_restaurado = None
    return xp_perdido, hp_restaurado


def frase_ultimo_hp_antes_morte(s: "Session") -> str:
    """Monta 'última leitura antes do golpe fatal: X/Y (Z%)' pro log de
    derrota — não existe log de dano por golpe na Caçada Solo/Missão Oásis,
    então isso é o melhor indício disponível (ver comentário em
    act_combate_solo)."""
    cur = getattr(s, "_ultimo_hp_cur", None)
    hp_max = getattr(s, "_ultimo_hp_max", None)
    if cur is None or not hp_max:
        return ""
    ratio = cur / hp_max
    return f" (última leitura antes do golpe fatal: {cur}/{hp_max}, {ratio:.0%})"


def is_mercador_deserto_solo(text: str) -> bool:
    return "mercador do deserto" in norm(text or "")


def is_mercador_viajante_solo(text: str) -> bool:
    return "mercador viajante" in norm(text or "")


def is_goblin_gibby_solo(text: str) -> bool:
    n = norm(text or "")
    return "goblin gibby" in n or ("encontro raro" in n and "martelo" in n)


# ---------------------------------------------------------------------
#  MISSÃO OÁSIS (busca do Sunred, no Oásis Perdido — só a Trilha Silenciosa
#  tem os NPCs; a Trilha Instável é o combate de verdade, igual à Caçada
#  Solo comum)
# ---------------------------------------------------------------------

def is_vale_miragens(message) -> bool:
    """Tela de escolha de trilha no Oásis Perdido (ao clicar 'Caçar' lá) —
    tem os botões Trilha Instável / Trilha Silenciosa / Voltar ao Oásis."""
    return (find_button(message, "trilha instavel", "trilha instável") is not None
            and find_button(message, "trilha silenciosa") is not None)


def is_npc_nurmora(text: str) -> bool:
    """Nurmora, a Primeira Forjadora — NPC da quest do Martelo Mágico
    (opcional, sem relação com a busca do Sunred). Exige o TÍTULO completo
    (não só 'nurmora' solto) — evita falso positivo em telas que apenas
    MENCIONAM o nome de passagem."""
    return "nurmora, a primeira forjadora" in norm(text or "")


def is_npc_lana(text: str) -> bool:
    """Lana, a Guardiã do Oásis — só um evento de exploração, sem decisão
    de verdade (clica 'Continuar explorando' e segue)."""
    return "lana, a guardia do oasis" in norm(text or "")


def is_npc_sunred(text: str) -> bool:
    """Sunred, o Maior Aventureiro — o NPC da busca de matar monstros. Cobre
    TODAS as telas dele: oferta ('Sunred, o Maior Aventureiro'), 'Busca
    aceita!' (essa NÃO tem o título — só menciona 'Sunred' na fala solta),
    status ('Busca ativa: ...') e conclusão.
    BUG CORRIGIDO (2x): a 1ª versão exigia só 'sunred' solto, o que confundia
    com a tela de VITÓRIA da Trilha Instável (que também MENCIONA 'Busca
    Sunred: ...' de passagem, sem ser a tela do NPC) — mandava pro estado
    'desconhecido' e prendia a conta em loop. A 2ª versão exigiu o TÍTULO
    completo pra resolver isso, mas aí quebrou o reconhecimento da tela
    'Busca aceita!' (que não tem esse título) — o bot mandava /start nela
    sem nunca clicar 'Continuar'. Solução: aceita 'sunred' em QUALQUER
    lugar do texto, EXCETO quando for a tela de vitória de verdade (sempre
    tem 'Vitória!' no cabeçalho — único caso realmente ambíguo)."""
    n = norm(text or "")
    if "sunred" not in n:
        return False
    return "vitoria" not in n


def sunred_estado(message, texto: str = "") -> str:
    """Em qual das telas do Sunred estamos:
      'oferta'          -> tem 'Aceitar Busca'/'Ignorar' (sem busca ativa;
                           NÃO dá pra saber qual monstro é ANTES de aceitar).
      'aceita'          -> acabou de aceitar AGORA ('Busca aceita!'), só tem
                           'Continuar' — é AQUI que dá pra ler qual monstro veio.
      'ativa'           -> busca em andamento (AINDA NÃO bateu 50+200), tem
                           'Desistir da busca'/'Continuar'.
      'pronta_entregar' -> bateu os 50+200 ('✅ Condições cumpridas! Entregue
                           a recompensa.'), tem 'Entregar recompensa' — uma
                           tela A MAIS que eu não conhecia: só depois de
                           clicar aqui é que vem a tela final ('concluida').
      'concluida'       -> busca CONCLUÍDA de vez ('BUSCA SUNRED CONCLUÍDA!
                           Você recebeu <recompensa>!') — só tem 'Menu'.
                           Checado primeiro pelo TEXTO (frase bem específica,
                           'busca sunred concluida') em vez de só pelo botão —
                           mais à prova de qualquer diferença de emoji/espaço
                           no texto do botão 'Menu' que possa escapar do
                           find_button.
      'desconhecido'    -> nenhum desses (loga pra investigar, não quebra)."""
    if "busca sunred concluida" in norm(texto):
        return "concluida"
    if find_button(message, "aceitar busca") is not None:
        return "oferta"
    if find_button(message, "entregar recompensa") is not None:
        return "pronta_entregar"
    if find_button(message, "desistir da busca") is not None:
        return "ativa"
    if find_button(message, "continuar") is not None:
        return "aceita"
    if find_button(message, "menu") is not None:
        return "concluida"
    return "desconhecido"


QUEST_ITEM_RE = re.compile(r"colete:?\s*(\d+)\s*x\s+(.+?)(?:\s+durante\b|\s*$)",
                           re.IGNORECASE)


def parse_quest_item(text: str):
    """Acha a linha 'Colete Nx <item>' (tela 'Busca aceita!' OU a de status
    'busca ativa') -> (quantidade, nome_item), ou None se não achar. O nome
    do item sempre contém o nome do monstro (ex: 'Flor do Karkto Feroz'
    contém 'Karkto Feroz') — não precisa de tabela item->monstro, só conferir
    se o monstro configurado está CONTIDO no nome do item."""
    for linha in (text or "").splitlines():
        m = QUEST_ITEM_RE.search(linha)
        if m:
            return int(m.group(1)), m.group(2).strip()
    return None


QUEST_KILLS_RE = re.compile(r"monstros derrotados:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
QUEST_ITENS_RE = re.compile(r"itens no invent[aá]rio:\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)


def parse_quest_status(text: str):
    """Da tela de STATUS da busca ATIVA do Sunred (a que tem 'Desistir da
    busca'): ((kills_atual, kills_meta) ou None, (itens_atual, itens_meta)
    ou None). É a fonte de verdade pra confirmar o progresso de verdade
    (o bot também mantém a própria contagem, corrigida por aqui sempre que
    possível — ver run_missao_oasis_conta)."""
    n = text or ""
    mk = QUEST_KILLS_RE.search(n)
    mi = QUEST_ITENS_RE.search(n)
    kills = (int(mk.group(1)), int(mk.group(2))) if mk else None
    itens = (int(mi.group(1)), int(mi.group(2))) if mi else None
    return kills, itens


QUEST_VITORIA_RE = re.compile(
    r"busca sunred:.*?\((\d+)\s*/\s*(\d+),?\s*kills:\s*(\d+)\s*/\s*(\d+)\)",
    re.IGNORECASE | re.DOTALL)


def parse_quest_vitoria(text: str):
    """Lê a linha 'Busca Sunred: ... (X/METAX, kills: Y/METAY)' da tela de
    VITÓRIA da Trilha Instável. CONFIRMADO pelo usuário: essa linha só
    aparece ao matar o monstro DA BUSCA, ou quando os kills TOTAIS batem um
    múltiplo de 50 — a maioria das vitórias (monstro comum, fora desses
    múltiplos) NÃO mostra nada. Por isso o bot mantém a PRÓPRIA contagem
    (incrementada a cada vitória) e só CORRIGE pelos valores daqui quando a
    linha aparece (fonte de verdade). Devolve (itens_atual, itens_meta,
    kills_atual, kills_meta) ou None se a linha não estiver nessa vitória."""
    m = QUEST_VITORIA_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def is_compra_realizada_solo(text: str) -> bool:
    return "compra realizada" in norm(text or "")


def parse_monstro_nome_solo(text: str):
    """Nome do MONSTRO na tela de combate solo. Ele aparece ANTES de 'Você',
    numa linha com um emoji na frente, seguida (na mesma linha ou na próxima)
    do HP dele — que é a PRIMEIRA linha com 'X/Y' do texto (o monstro sempre
    vem listado antes do jogador). Usado pra aplicar um limite de HP%
    específico por monstro (por CONTA — s.acc['hp_por_mob'], já que cada
    personagem tem defesa diferente e o mesmo bicho bate diferente em cada
    um), já que alguns
    batem bem mais forte que outros."""
    linhas = (text or "").splitlines()
    for i, l in enumerate(linhas):
        if "energia" in norm(l):
            continue
        if HP_RE.search(l):
            for k in range(i - 1, -1, -1):
                cand = linhas[k].strip()
                if not cand:
                    continue
                nome = re.sub(r"^[^\wÀ-ÿ]+", "", cand).strip()
                if nome and "combate" not in norm(nome) and "turno" not in norm(nome):
                    return nome
                return None
            return None
    return None


HP_RESTANTE_RE = re.compile(r"hp restante:\s*(\d+)", re.IGNORECASE)


def parse_hp_voce_solo(text: str, s: "Session" = None):
    """HP do JOGADOR na Caçada Solo. O jogo às vezes mostra 'Você' (na tela de
    combate) e às vezes o NOME do personagem (na tela de Vitória/Armadilha) —
    então NÃO dá pra confiar só na palavra 'você'. Estratégia:
      1) Se achar uma linha com 'você', pega o HP 'X/Y' logo depois dela.
      2) Senão, pega a ÚLTIMA linha que tenha 'hp' ou '❤' (nunca 'energia' —
         que tem o MESMO formato 'X/Y' e ficava sendo confundida com HP).
      3) A tela de ARMADILHA não mostra fração nenhuma, só 'HP restante: 24'
         (SEM o máximo) — nesse caso usa o MÁXIMO da última leitura boa desta
         conta (cacheado em s._ultimo_hp_max) pra ainda calcular a % e poder
         curar (senão a checagem de HP baixo nunca disparava depois de uma
         armadilha — bug real visto em print do usuário, quase matou a conta).
    'a' opcional: se vier, guarda/usa o cache do HP máximo NESSA conta."""
    linhas = (text or "").splitlines()
    for i, l in enumerate(linhas):
        if "voce" in norm(l) or "você" in norm(l):
            for j in range(i, min(i + 3, len(linhas))):
                if "energia" in norm(linhas[j]):
                    continue
                m = HP_RE.search(linhas[j])
                if m:
                    cur, hp_max = int(m.group(1)), int(m.group(2))
                    if s is not None:
                        s._ultimo_hp_max = hp_max
                    return cur, hp_max
    candidatos = []
    for l in linhas:
        nl = norm(l)
        if "energia" in nl:
            continue
        if "hp" in nl or "❤" in l:
            m = HP_RE.search(l)
            if m:
                candidatos.append((int(m.group(1)), int(m.group(2))))
    if candidatos:
        cur, hp_max = candidatos[-1]
        if s is not None:
            s._ultimo_hp_max = hp_max
        return cur, hp_max
    # sem fração X/Y em lugar nenhum — tenta 'HP restante: N' (Armadilha) +
    # o último máximo conhecido desta conta.
    m_rest = HP_RESTANTE_RE.search(text or "")
    if m_rest:
        cur = int(m_rest.group(1))
        hp_max = getattr(s, "_ultimo_hp_max", None) if s is not None else None
        if hp_max:
            return cur, hp_max
        return cur, None
    return None, None


def parse_resultado_caca_solo(text: str):
    """Lê a tela de '🏆 Vitória!' (+XP, +Gold, e Drops se houver, cor de
    raridade igual à Caçada em Dupla) -> (xp, gold, drops, raridades).
    Dois formatos de item vistos: uma seção 'Drops:' com uma linha por item
    (igual a Caçada em Dupla), OU uma linha solta '🎁 Item: Nome (DEF+7,
    HP+15)' (equipamento, sem seção 'Drops:' nenhuma) — os dois são pegos."""
    n = norm(text or "")
    mxp = re.search(r"\+\s*([\d.,]+)\s*xp", n)
    mgold = re.search(r"\+\s*([\d.,]+)\s*gold", n)
    xp = int(re.sub(r"[.,]", "", mxp.group(1))) if mxp else 0
    gold = int(re.sub(r"[.,]", "", mgold.group(1))) if mgold else 0
    drops = []
    raridades = {}

    # formato "🎁 Item: Nome (stats)" — pega em QUALQUER linha, sem precisar
    # de uma seção "Drops:" (a tela de Vitória da Caçada Solo usa esse jeito
    # pra equipamentos, diferente da Caçada em Dupla).
    for linha in (text or "").splitlines():
        m_item = re.match(r"^[^\wÀ-ÿ]*item\s*:\s*(.+)$", linha.strip(), re.IGNORECASE)
        if m_item:
            nome_item = m_item.group(1).strip()
            if nome_item:
                cor = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in linha), None)
                drops.append(nome_item)
                if cor:
                    raridades[nome_item] = cor

    capturando = False
    for linha in (text or "").splitlines():
        l = linha.strip()
        if not capturando:
            if "drops" in norm(l):
                capturando = True
            continue
        if not l or "cacar de novo" in norm(l) or "menu" in norm(l):
            break
        cor = next((r for emoji, r in EMOJI_RARIDADE.items() if emoji in l), None)
        m = re.match(r"^[^\wÀ-ÿ]*(.+?)(?:\s*×\s*(\d+))?$", l)
        if not m:
            continue
        nome = m.group(1).strip()
        if not nome:
            continue
        qtd = int(m.group(2)) if m.group(2) else 1
        drops.extend([nome] * qtd)
        if cor:
            raridades[nome] = cor
    return xp, gold, drops, raridades


async def _tentar_curar_se_precisar(s: Session, limite: float, contexto: str):
    """Reconfere o HP imediatamente antes de CADA ação (Atacar, Alma, Super
    Tônico) — não só uma vez no início da rodada. Pedido explícito do
    usuário: o personagem sempre age primeiro que o adversário, então se ele
    morreu logo ao entrar na luta, é sinal de que o bot atacou/usou alma sem
    checar o HP de novo depois de uma ação anterior ter custado tempo de
    rede. A Poção de Vida cura o HP por completo, então não precisa de
    margem extra — só precisa checar SEMPRE, na hora exata antes de agir.

    CORRIGIDO (item 7 — removido o refresh redundante, mesma correção já
    aplicada em Brain._checar_curar_antes): antes, essa reconferência forçava
    um `await s.refresh()` — um get_messages() de VERDADE pro Telegram —
    TODA VEZ que era chamada. Isso é redundante: toda ação real (click() ->
    wait_change()) já força seu próprio refresh por dentro, então s.text,
    aqui, já está tão atualizado quanto um refresh novo estaria. Continua
    protegendo igual, sem o custo extra de rede.

    Retorna:
      None  -> HP seguro, o chamador segue com a ação que ia fazer.
      True  -> precisava curar E curou (a rodada foi usada nisso).
      False -> precisava curar e NÃO TINHA Poção de Vida (parar por segurança).
    """
    cur, hp_max = parse_hp_voce_solo(s.text, s)
    ratio = (cur / hp_max) if (cur is not None and hp_max) else None
    if cur is not None and hp_max:
        s._ultimo_hp_cur, s._ultimo_hp_max = cur, hp_max
    if ratio is not None and ratio <= limite:
        dano_solo = damage_to_me(s.text, s.char)
        dano_solo_txt = f" · levou {dano_solo} de dano" if dano_solo > 0 else ""
        log(s.name, f"🩺 HP={cur} ratio={ratio * 100:.0f}% limite={limite * 100:.0f}%"
                    f"{dano_solo_txt} -> caiu antes de [{contexto}] — bebendo poção "
                    f"(cura completa) em vez de {contexto.lower()}.")
        if await act_potion(s):
            return True
        log(s.name, f"🛑 HP em {ratio:.0%} (abaixo de {limite:.0%}) e SEM Poção de "
                    f"Vida — parando por segurança, não vou arriscar continuar.")
        return False
    return None


async def act_combate_solo(s: Session, brain) -> bool:
    """1 ação de combate na Caçada Solo (cada clique resolve na hora, sem
    ampulheta). dps/lanceiro/arqueiro/berserker atacam E usam alma; tank/
    suporte só atacam (papel deles aqui é só aguentar/curar, sem alma de
    aggro — a Caçada Solo não tem mecânica de grupo). Todos curam quando o
    HP cai abaixo do % configurado pra essa conta. Também tenta usar Super
    Tônico e Elixir de Sabedoria a cada rodada (se configurados e já tiver
    passado o intervalo de cada um — ver try_tonico/try_elixir).
    Se a conta tiver 'so_bosses_deserto' ligado E estiver caçando no Deserto
    Escaldante, só luta contra os 3 bosses (config.BOSSES_DESERTO_ESCALDANTE)
    — contra qualquer outro monstro, FOGE direto (ver filtro logo no início).
    Em qualquer outro mapa, ou com o flag desligado, o filtro NÃO se aplica
    (luta normal com tudo). Da mesma forma, se a conta tiver 'alvo_oasis'
    preenchido E estiver caçando no Oásis Perdido, só luta contra ESSE
    monstro escolhido — foge de todos os outros do mapa.
    Retorna False se PRECISAVA curar (HP abaixo do limite) e NÃO CONSEGUIU
    (acabaram as Poções de Vida) — nesse caso o chamador deve PARAR o bot na
    HORA, sem continuar atacando/caçando sem conseguir se curar (é assim que
    a conta quase morreu antes). True nos outros casos (agiu normalmente, ou
    fugiu de um monstro fora da lista de alvos)."""
    cur, hp_max = parse_hp_voce_solo(s.text, s)
    # Item 1 (resync forçado da alma) conta rodadas dentro de Brain.act() — mas
    # a Caçada Solo/Missão Oásis nunca chama brain.act() (usa este combate
    # próprio), então sem incrementar aqui o contador nunca andava e o resync
    # nunca disparava. Cada chamada aqui já representa uma rodada de combate.
    brain.rodadas_desde_resync_alma += 1
    ratio = (cur / hp_max) if (cur is not None and hp_max) else None
    # guarda o ÚLTIMO HP confirmado (antes de qualquer ação desta rodada) —
    # não existe um log de "causou X de dano" na Caçada Solo/Missão Oásis
    # (diferente da Masmorra), então isso é o melhor indício disponível pra
    # entender uma morte depois: se a última leitura já estava baixa, foi
    # "sangramento" normal; se estava alta, o golpe seguinte foi um baque forte.
    if cur is not None and hp_max:
        s._ultimo_hp_cur, s._ultimo_hp_max = cur, hp_max
    limite = getattr(s, "caca_vida_ratio", 0.40)
    nome_mob = parse_monstro_nome_solo(s.text)
    if nome_mob:
        # guarda SEMPRE o último monstro visto (pra citar na tela de
        # derrota, mesmo que essa troca de log abaixo não dispare) e loga
        # só quando MUDA de monstro (evita spam a cada rodada da mesma luta).
        s._ultimo_mob_combate = nome_mob
        if nome_mob != getattr(s, "_mob_logado", None):
            s._mob_logado = nome_mob
            log(s.name, f"🎯 lutando contra: {nome_mob}")
    hp_por_mob = s.acc.get("hp_por_mob") or {}
    if nome_mob and nome_mob in hp_por_mob:
        limite = max(0, min(100, hp_por_mob[nome_mob])) / 100.0

    # Log do HP/ratio/limite em TODA rodada (bebendo ou não) — mesmo formato
    # já usado no _act_other da Masmorra/Caçada em Dupla ('🩺 HP=... ratio=...
    # limite=... -> ...'), que faltava aqui. Só visibilidade, não muda a
    # decisão (ela é tomada logo abaixo, igual antes).
    log(s.name, f"🩺 HP={cur if cur is not None else '?'} "
                f"ratio={('%.0f%%' % (ratio * 100)) if ratio is not None else 'NÃO LIDO'} "
                f"limite={limite * 100:.0f}% -> "
                f"{'BEBER poção' if (ratio is not None and ratio <= limite) else 'ok, não bebe'}")

    # --- Filtro DESERTO ESCALDANTE, POR CONTA: 3 modos possíveis —
    # 'geral' (luta com tudo, padrão), 'bosses' (só os 3 bosses raros, foge
    # do resto) ou 'poeira' (Caçar Poeira Estrelar: foge de TUDO, inclusive
    # os bosses — a conta só quer evitar combate e recolher a Poeira
    # Estrelar que aparece sozinha no mapa, sem lutar com nada). Só tem
    # efeito se a conta estiver mesmo caçando no Deserto Escaldante; em
    # qualquer outro mapa, luta normal com tudo, mesmo com o modo != geral.
    deserto_modo = s.acc.get("deserto_modo") or ("bosses" if s.acc.get("so_bosses_deserto") else "geral")
    no_deserto = getattr(s, "mapa_caca_solo", "") == "Deserto Escaldante"
    if deserto_modo == "poeira" and no_deserto and nome_mob:
        log(s.name, f"✨ modo Poeira Estrelar — fugindo de '{nome_mob}' (não luta com nada aqui).")
        await act_fugir(s)
        return True
    so_bosses = (deserto_modo == "bosses")
    if so_bosses and no_deserto and nome_mob:
        alvos = getattr(config, "BOSSES_DESERTO_ESCALDANTE", [])
        if norm(nome_mob) not in {norm(a) for a in alvos}:
            log(s.name, f"🏃 '{nome_mob}' não é um dos 3 bosses — fugindo.")
            await act_fugir(s)
            return True

    # --- Filtro "ALVO ÚNICO", SÓ NO OÁSIS PERDIDO (contas[i]["alvo_oasis"] =
    # nome de um monstro): a conta só luta contra ESSE monstro escolhido e
    # FOGE de qualquer outro do mapa — depois de fugir, o loop de fora já
    # clica 'Caçar de novo' sozinho, então ela vai voltar a procurar até o
    # alvo aparecer de novo. alvo_oasis="" (padrão) = luta com tudo, igual
    # antes. Só tem efeito se a conta estiver mesmo caçando no Oásis Perdido;
    # em qualquer outro mapa, ignora esse campo.
    alvo_oasis = (s.acc.get("alvo_oasis") or "").strip()
    no_oasis = getattr(s, "mapa_caca_solo", "") == "Oásis Perdido"
    if alvo_oasis and no_oasis and nome_mob:
        if norm(nome_mob) != norm(alvo_oasis):
            log(s.name, f"🏃 '{nome_mob}' não é o alvo escolhido ({alvo_oasis}) — fugindo.")
            await act_fugir(s)
            return True

    # --- Filtro "FUGIR DO BOSS", SÓ NA FLORESTA PROFUNDA (contas[i]
    # ["fugir_boss_floresta"] = True/False): pedido do usuário 2026-07-17,
    # junto com o print mostrando o Boss da Floresta Profunda ('Abominação
    # do Aspecto Caído', 1800 HP) muito mais forte que os goblins comuns do
    # mapa (260-450 HP, vem de config.MOBS_FLORESTA_PROFUNDA). Com o flag
    # ligado, foge SÓ desse boss e continua lutando normal com os goblins
    # comuns — sem precisar abrir mão de caçar o mapa todo só pra evitar o
    # boss. Desligado (padrão) = luta com tudo, igual antes. Só tem efeito
    # na Floresta PROFUNDA de verdade (s._solo_sub_area == "profunda") — na
    # Floresta Sombria comum, ou em qualquer outro mapa, ignora esse campo.
    fugir_boss_floresta = bool(s.acc.get("fugir_boss_floresta"))
    na_floresta_profunda = (getattr(s, "mapa_caca_solo", "") == "Floresta Sombria"
                             and getattr(s, "_solo_sub_area", "") == "profunda")
    if fugir_boss_floresta and na_floresta_profunda and nome_mob:
        boss_floresta = getattr(config, "BOSS_FLORESTA_PROFUNDA", "")
        if boss_floresta and norm(nome_mob) == norm(boss_floresta):
            log(s.name, f"🏃 '{nome_mob}' é o Boss da Floresta Profunda — fugindo "
                        f"(config: fugir do boss ligado).")
            await act_fugir(s)
            return True

    # --- Filtro DINÂMICO da MISSÃO OÁSIS (contas rodando esse modo — ver
    # run_missao_oasis_conta, que seta s.missao_oasis_ativa/s.missao_alvo).
    # A busca do Sunred pede 50x de UM monstro específico E 200 no total
    # (contando os 50). Regra: mata QUALQUER monstro (ajuda o total) até a
    # 'folga' pro total acabar — ou seja, até (200 - total_matados) ficar
    # MENOR OU IGUAL a (50 - alvo_matados). A partir daí, matar outro
    # monstro seria DESPERDÍCIO (não ajuda mais a fechar os 50 a tempo) —
    # foge de tudo que não for o alvo. Confirmado pelo usuário: passar um
    # pouco de 200 não é problema, só perca de tempo — por isso o gate é
    # "<=" (troca ASSIM QUE fica arriscado), não só depois de estourar 200.
    if getattr(s, "missao_oasis_ativa", False) and nome_mob:
        alvo_missao = getattr(s, "missao_alvo", "") or ""
        if alvo_missao and norm(nome_mob) != norm(alvo_missao):
            total = getattr(s, "_missao_kills_totais", 0)
            do_alvo = getattr(s, "_missao_kills_alvo", 0)
            folga = 200 - total
            falta_alvo = 50 - do_alvo
            if folga <= falta_alvo:
                log(s.name, f"🏃 focando só em '{alvo_missao}' (faltam {max(0, falta_alvo)}, "
                            f"folga {max(0, folga)}) — fugindo de '{nome_mob}'.")
                await act_fugir(s)
                return True

    r = await _tentar_curar_se_precisar(s, limite, "início da rodada")
    if r is not None:
        return r

    # SUPER TÔNICO / ELIXIR DE SABEDORIA (a cada 10/30 min) — CONSOMEM o
    # turno de verdade (confirmado pelo usuário 2026-07-12: a rodada fechava
    # ali, sem sobrar ataque depois — a premissa anterior, "de graça", tinha
    # vindo de um bug antigo com outra causa). Chamadas AQUI, a cada rodada
    # de combate, porque a Caçada Solo pode ficar horas clicando 'Caçar de
    # novo' SEM NUNCA voltar pro menu principal enquanto a energia não
    # acabar — e era só ALI (no menu) que esse try_tonico/try_elixir rodava
    # de novo. Resultado prático: os dois só eram usados 1x no início da
    # conta e ficavam parados até a energia esgotar (que pode demorar bem
    # mais que 10/30 min), dando a impressão de que não estavam sendo
    # usados. Com a chamada aqui, cada um já se auto-regula (só age de
    # verdade quando o próprio intervalo já passou — ver try_tonico/
    # try_elixir). Reconfere o HP ANTES de cada uma (pedido do usuário):
    # qualquer ação anterior gasta tempo de rede, e o adversário pode ter
    # agido nesse meio-tempo.
    r = await _tentar_curar_se_precisar(s, limite, "Super Tônico")
    if r is not None:
        return r
    if await try_tonico(s):
        return True

    r = await _tentar_curar_se_precisar(s, limite, "Elixir de Sabedoria")
    if r is not None:
        return r
    if await try_elixir(s):
        return True

    if s.role in ("dps", "lanceiro", "arqueiro", "berserker"):
        r = await _tentar_curar_se_precisar(s, limite, "Alma")
        if r is not None:
            return r
        if await use_soul_from_priority(s, brain, s.souls, forcar=brain.deve_forcar_resync_alma()):
            return True

    r = await _tentar_curar_se_precisar(s, limite, "Atacar")
    if r is not None:
        return r
    await act_atacar(s)
    return True


async def limpar_confirmacoes_compra_solo(s: Session) -> None:
    """A confirmação 'Compra realizada!' vem numa mensagem SEM BOTÃO — o
    refresh() normal (que sempre foca na mensagem mais recente COM botão,
    tipo a de Vitória) pula ela, então ela nunca vira s.text/s.message e a
    checagem de is_compra_realizada_solo nunca alcançava essa mensagem de
    verdade. Aqui a gente lê as últimas mensagens DIRETO (sem esse filtro) e
    apaga qualquer 'Compra realizada' encontrada, mesmo que não seja a
    mensagem 'ativa' no momento."""
    try:
        msgs = await s.client.get_messages(s.bot, limit=6)
    except Exception:
        return
    apagar = [m.id for m in msgs if getattr(m, "text", None) and is_compra_realizada_solo(m.text)]
    if apagar:
        try:
            await s.client.delete_messages(s.bot, apagar, revoke=True)
            log(s.name, f"🧹 apaguei {len(apagar)} confirmação(ões) de compra solta(s) na conversa.")
        except Exception as e:
            log(s.name, f"(não consegui apagar confirmação de compra: {e!r})")


async def tratar_evento_solo(s: Session) -> None:
    """Depois de 'Caçar', a tela pode ser um NPC/mercador em vez de combate/
    armadilha/evento de sorte comuns (esses não precisam de ação especial,
    só clicar 'Caçar de novo' depois). Trata cada NPC conhecido:
      - Mercador do Deserto: vende Super Tônico — compra o escolhido em
        config.CACA_SOLO['tonico_deserto'] ou ignora.
      - Mercador Viajante: cura completa por pouco gold — compra SE o HP
        estiver abaixo do limite configurado, senão ignora.
      - Goblin Gibby (Martelo Mágico): SEMPRE compra (raro, vale a pena).
      - Mensagem 'Compra realizada' (sem botão de ação): apaga (ver
        limpar_confirmacoes_compra_solo, chamada logo abaixo — essa mensagem
        NUNCA vira s.text/s.message porque não tem botão, então não dá pra
        detectar ela só olhando pra s.text como as outras)."""
    await limpar_confirmacoes_compra_solo(s)
    txt = s.text
    if is_compra_realizada_solo(txt):
        try:
            await s.client.delete_messages(s.bot, [s.message.id], revoke=True)
            log(s.name, "🧹 apaguei a mensagem de confirmação de compra.")
        except Exception as e:
            log(s.name, f"(não consegui apagar a confirmação de compra: {e!r})")
        await asyncio.sleep(config.ACTION_DELAY)
        return
    if is_mercador_deserto_solo(txt):
        # Por CONTA agora (antes era um valor único, geral, em config.
        # CACA_SOLO['tonico_deserto'] — valia igual pra todo mundo). Mantém
        # o global como fallback só pra configs salvas antes dessa mudança.
        pref = s.acc.get("tonico_deserto")
        if pref is None:
            pref = getattr(config, "CACA_SOLO", {}).get("tonico_deserto", "")
        rotulo = {"atk": "super atk", "def": "super def", "crit": "super crit"}.get(pref)
        b = find_button(s.message, rotulo) if rotulo else None
        if b:
            log(s.name, f"🛒 Mercador do Deserto: comprando {b.text}.")
            await s.click(b, label=b.text)
        else:
            await s.click_text("ignorar", label="Ignorar", required=False)
        return
    if is_mercador_viajante_solo(txt):
        cur, hp_max = parse_hp_voce_solo(txt, s)
        ratio = (cur / hp_max) if (cur is not None and hp_max) else None
        limite = getattr(s, "caca_vida_ratio", 0.40)
        if ratio is not None and ratio <= limite:
            log(s.name, "🛒 Mercador Viajante: HP baixo, comprando a cura.")
            await s.click_text("comprar", label="Comprar (cura)", required=False)
        else:
            await s.click_text("ignorar", label="Ignorar", required=False)
        return
    if is_goblin_gibby_solo(txt):
        log(s.name, "🛒 Goblin Gibby apareceu — comprando o Martelo Mágico.")
        await s.click_text("comprar", label="Comprar (Martelo)", required=False)
        return


async def escolher_submenu_caca_solo(s: Session) -> bool:
    """Alguns mapas mostram uma tela EXTRA depois de clicar 'Caçar', antes da
    caçada de verdade — trata os casos conhecidos (vai crescendo conforme
    aparecerem mapas novos). Retorna True se tratou algo (o chamador deve dar
    um refresh e reavaliar em seguida, pode vir mais de uma tela em sequência):
      - Montanhas Gélidas ('Escolha o modo de caçada: Caçar Solo / Criar
        Caçada em Dupla / Entrar em Caçada') -> clica 'Caçar Solo'.
      - Floresta Sombria ('Onde deseja caçar? Floresta Sombria / Floresta
        Profunda') -> escolhe conforme a preferência DESTA conta (s._solo_sub_area,
        ("" = Sombria, "profunda" = Profunda).
      - Vale das Miragens/Oásis ('Trilha Instável' / 'Trilha Silenciosa') ->
        SEMPRE 'Trilha Instável' (a outra é de missão, não tem caçada)."""
    n = norm(s.text)
    if "escolha o modo de cacada" in n:
        b = find_button(s.message, "cacar solo", "caçar solo")
        if b:
            log(s.name, "🔀 escolhendo 'Caçar Solo' (mapa oferece Solo/Dupla).")
            await s.click(b, label="Caçar Solo")
            return True
    if "onde deseja cacar" in n and find_button(s.message, "floresta sombria"):
        quer_profunda = getattr(s, "_solo_sub_area", "") == "profunda"
        if quer_profunda and find_button(s.message, "floresta profunda"):
            b = find_button(s.message, "floresta profunda")
            log(s.name, "🔀 escolhendo 'Floresta Profunda'.")
        else:
            b = find_button(s.message, "floresta sombria")
            log(s.name, "🔀 escolhendo 'Floresta Sombria'.")
        await s.click(b, label=b.text)
        return True
    if find_button(s.message, "trilha instavel", "trilha instável"):
        b = find_button(s.message, "trilha instavel", "trilha instável")
        log(s.name, "🔀 escolhendo 'Trilha Instável' (a única com caçada nesse mapa).")
        await s.click(b, label="Trilha Instável")
        return True
    return False


def _botao_caca_de_novo(s: Session):
    """Botão pra continuar caçando depois de Vitória/Armadilha/etc — quase
    sempre 'Caçar de novo', MAS na Floresta Profunda o jogo troca o texto
    desse botão pelo nome do próprio submapa ('🌑 Floresta Profunda') em vez
    de 'Caçar de novo' (BUG REAL corrigido 2026-07-17, print do usuário:
    'na floresta profunda, ao matar um mob é diferente dos outros mapas').
    Sem esse fallback, o find_button nunca achava nada nessa sub-área
    específica — a tela de Vitória era reconhecida (por is_vitoria_solo),
    mas o bot não clicava em NADA depois, ficando parado pra sempre.
    Tenta primeiro o texto padrão; só cai pro nome da sub-área se a conta
    estiver mesmo na Floresta Profunda (s._solo_sub_area == 'profunda')."""
    b = find_button(s.message, "cacar de novo", "caçar de novo")
    if b:
        return b
    if getattr(s, "_solo_sub_area", "") == "profunda":
        return find_button(s.message, "floresta profunda")
    return None


async def run_caca_solo_conta(s: Session, baseline: int = 0) -> bool:
    """Roda a Caçada Solo pra UMA conta, de forma independente (sem sala, sem
    parceiro — cada conta cuida só de si mesma). Retorna True se precisa
    reiniciar o bot (erro), False se parou de propósito (limite/energia sem
    poção/parar no fim)."""
    cfg = config.CACA_SOLO
    energia_minima = int(cfg.get("energia_minima", 5))
    energia_alvo = int(cfg.get("energia_alvo", 35))
    vida_min_pct = int(cfg.get("vida_min_pct", 40))
    max_cacadas = int(cfg.get("max_cacadas", 0))
    vida_pct_conta = s.acc.get("vida_min_pct")
    vida_pct_conta = vida_min_pct if vida_pct_conta is None else int(vida_pct_conta)
    s.caca_vida_ratio = max(0, min(100, vida_pct_conta)) / 100.0
    s.pocao_minima_caca = int(config.POCOES.get("pocao_vida_minima", 10))
    s.modo_caca = True
    brain = Brain(s)
    s._t_inicio_conteudo = time.time()
    feitas = 0
    tag = f"solo-{s.name}"
    sem_reconhecer = 0

    # RETOMADA (pedido do usuário 2026-07-16, mesmo princípio já usado na
    # Masmorra/Cripta/Caçada Dupla): se a conta JÁ ESTÁ em combate ativo
    # (bot parado no meio e iniciado de novo, ou PC reiniciou), pula a
    # preparação de navegação abaixo (back_to_menu/viajar_para) — ela
    # poderia clicar 'Menu'/'Viajar' que não existem durante o combate e
    # cair num /start indevido. Vai direto pro loop principal, que já
    # reconhece a tela de combate e continua normalmente.
    await s.refresh()
    ja_em_combate = is_combat_screen_solo(s.message)

    # Mapa desta CONTA (cada uma pode caçar num lugar diferente) — se ela não
    # tiver um mapa próprio escolhido, usa o mapa "geral" da aba Caçada Solo.
    # "Floresta Profunda" não é um mapa de verdade — é a SUB-ÁREA de dentro de
    # Floresta Sombria (o jogo pergunta qual das duas ao entrar); aqui vira
    # 'viaja pra Floresta Sombria' + 'escolhe a sub-área Profunda' pra ESTA conta.
    # Calculado SEMPRE (mesmo retomando) — os filtros de Deserto Escaldante/
    # Oásis Perdido em act_combate_solo dependem de s.mapa_caca_solo estar
    # certo, mesmo quando a viagem de verdade é pulada por já estar em combate.
    mapa_conta = (s.acc.get("mapa") or "").strip() or (cfg.get("mapa") or "").strip()
    if mapa_conta == "Floresta Profunda":
        s._solo_sub_area = "profunda"
        mapa_conta = "Floresta Sombria"
    else:
        s._solo_sub_area = ""
    s.mapa_caca_solo = mapa_conta

    if ja_em_combate:
        log(tag, "▶️ Caçada Solo ATIVA detectada — retomando o combate de onde parou.")
        # Força conferir o cooldown REAL das almas já na 1ª ação (pedido do
        # usuário: "mesmo que tenha que passar um check nas almas, pra ver
        # os cd") — a crença de recarga do Brain começa vazia numa sessão
        # nova, então sem isso ele levaria RESYNC_ALMA_RODADAS rodadas pra
        # confirmar de verdade contra a tela.
        brain.rodadas_desde_resync_alma = RESYNC_ALMA_RODADAS
    else:
        # Chega no menu principal antes de seguir — usa back_to_menu (Menu / sair
        # do lobby / voltar, só recorrendo a /start como ÚLTIMO recurso) em vez de
        # mandar /start direto: antes esse /start era incondicional porque
        # assumia que a limpeza profunda do histórico tinha acabado de rodar
        # (deixando a conversa sem nenhum botão pra clicar) — agora que essa
        # limpeza é opcional (config.LIMPEZA_PROFUNDA_ATIVO, padrão desligada),
        # na maioria das vezes ainda sobra uma tela/botão utilizável, e forçar
        # /start sempre seria desperdício (e risco de FloodWait/banimento à toa).
        await back_to_menu(s)

        if mapa_conta:
            log(tag, f"🗺️ indo pro mapa '{mapa_conta}'…")
            await viajar_para(s, mapa_conta)
            # Limpa a conversa aqui (momento SEGURO — ainda não entrou em combate):
            # depois de viajar, fica um resíduo da tela "Destino definido" com os
            # próprios botões (Caçar aqui/Masmorra/Voltar/Menu) — se ficar ali, um
            # refresh futuro pode se confundir com ela. Não precisa de /start.
            await asyncio.sleep(config.ACTION_DELAY)
            await limpar_historico(s, manter=1)

        log(tag, f"🏹 Caçada Solo: HP% poção {vida_min_pct}, energia mín {energia_minima}, "
                 f"reabastece até {energia_alvo}, limite {max_cacadas or 'sem limite'}.")

        # Super Tônico ANTES de começar a caçar de verdade — alguns mapas (ex:
        # Montanhas Gélidas) vão direto pro combate depois de escolher 'Caçar
        # Solo', sem passar pelo menu principal, então a checagem que fica lá
        # dentro (mais abaixo, no loop) nunca era alcançada nesses casos. Aqui
        # SEMPRE roda uma vez, garantido, antes da 1ª caçada.
        await back_to_menu(s)
        await try_tonico(s)
        await try_elixir(s)

    while True:
        try:
            await s.refresh()

            if await escolher_submenu_caca_solo(s):
                sem_reconhecer = 0
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if _no_menu_principal(s.message):
                sem_reconhecer = 0
                # Super Tônico: TAMBÉM vale pra Caçada Solo (mesma lógica de
                # masmorra/caça em dupla) — try_tonico já checa sozinho se já
                # passou o intervalo (10 min) antes de usar, e sabe navegar
                # pra 2ª página dos Consumíveis se o tônico estiver lá.
                await try_tonico(s)
                await try_elixir(s)
                await s.refresh()
                en = energia_atual(s.text)
                if en and en[0] < energia_minima:
                    log(tag, f"⚡ energia {en[0]}/{en[1]} abaixo de {energia_minima} — reabastecendo.")
                    if not await energia_encher_ate(s, energia_alvo):
                        registrar_pausa("pocao_energia_indisponivel",
                                        f"{s.name}: sem Poção de Energia")
                        return False
                    continue
                b = find_button(s.message, "cacar", "caçar")
                if not b:
                    await poll_sleep()
                    continue
                await s.click(b, label="Caçar")
                brain.round_num = 0
                brain.soul_ready_at = {}
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            await s.refresh()
            txt = s.text

            if is_sem_energia_solo(txt):
                sem_reconhecer = 0
                log(tag, "⚡ sem energia pra caçar — voltando ao menu.")
                await s.click_text("menu", label="Menu", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if is_fuga_solo(txt):
                sem_reconhecer = 0
                log(tag, "🏃 fugiu da batalha — voltando ao menu pra caçar de novo.")
                await s.click_text("menu", label="Menu", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if is_derrota_solo(txt):
                sem_reconhecer = 0
                xp_perdido, hp_restaurado = parse_derrota_solo(txt)
                mob = getattr(s, "_ultimo_mob_combate", "") or "monstro não identificado"
                log(tag, f"💀 MORREU contra '{mob}'! Perdeu "
                         f"{xp_perdido if xp_perdido is not None else '?'} XP, "
                         f"HP restaurado para {hp_restaurado if hp_restaurado is not None else '?'}."
                         f"{frase_ultimo_hp_antes_morte(s)}")
                try:
                    registrar_morte("caca_solo", nome_conta=s.name)
                except Exception as e:
                    log(tag, f"(não consegui registrar a morte: {e!r})")
                s._mob_logado = None   # próxima luta loga o monstro de novo
                await s.click_text("menu", label="Menu", required=False)
                alvo_seguranca = min(1.0, getattr(s, "caca_vida_ratio", 0.40) + 0.20)
                await curar_repetido_no_menu(s, alvo_seguranca)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if is_combat_screen_solo(s.message):
                sem_reconhecer = 0
                texto_antes = s.text
                if not await act_combate_solo(s, brain):
                    registrar_pausa("pocao_vida_baixa",
                                    f"{s.name}: sem Poção de Vida com HP baixo EM COMBATE")
                    return False
                # Confirma que a tela mudou de verdade (mesma lógica da Caçada
                # em Dupla): se o clique se perder (ex: 'Encrypted data
                # invalid' do Telegram), a tela fica igual — reage de novo
                # depois de RETRY_ACAO_APOS_CACA segundos sem mudança nenhuma,
                # em vez de ficar preso esperando pra sempre.
                deadline = time.time() + config.ROUND_TIMEOUT_CACA
                ultima_tentativa = time.time()
                while time.time() < deadline:
                    await s.refresh()
                    if s.text != texto_antes:
                        break
                    if time.time() - ultima_tentativa >= config.RETRY_ACAO_APOS_CACA:
                        log(tag, f"🔁 sem nenhuma mudança em {config.RETRY_ACAO_APOS_CACA:.0f}s "
                                 f"— o clique pode ter falhado, reagindo de novo.")
                        if not await act_combate_solo(s, brain):
                            registrar_pausa("pocao_vida_baixa",
                                            f"{s.name}: sem Poção de Vida com HP baixo EM COMBATE")
                            return False
                        ultima_tentativa = time.time()
                        continue
                    await poll_sleep()
                continue

            reconheceu = (is_vitoria_solo(txt) or is_armadilha_solo(txt)
                          or is_compra_realizada_solo(txt) or is_mercador_deserto_solo(txt)
                          or is_mercador_viajante_solo(txt) or is_goblin_gibby_solo(txt)
                          or bool(_botao_caca_de_novo(s)))
            if not reconheceu:
                # Tela não reconhecida (ex: chat ficou vazio depois da limpeza
                # de histórico, ou algum evento novo que não conhecemos ainda)
                # — em vez de ficar parado pra sempre, manda /start pra fazer
                # o jogo mostrar o menu de novo, depois de umas tentativas.
                sem_reconhecer += 1
                if sem_reconhecer >= 5:
                    if await _tentar_evitar_start(s):
                        sem_reconhecer = 0
                        continue
                    log(tag, "⚠️ não reconheci a tela há um tempo — mandando /start "
                             "pra tentar voltar ao menu.\n"
                             f"    texto atual: {txt!r}\n"
                             f"    botões: {button_texts(s.message)}")
                    await s.send_start()
                    sem_reconhecer = 0
                else:
                    await poll_sleep()
                continue
            sem_reconhecer = 0

            if is_vitoria_solo(txt):
                xp, gold, drops, raridades = parse_resultado_caca_solo(txt)
                agora_kill = time.time()
                duracao_kill = None
                t_ultimo_kill = getattr(s, "_t_ultimo_kill_solo", None)
                if t_ultimo_kill is not None:
                    duracao_kill = agora_kill - t_ultimo_kill
                s._t_ultimo_kill_solo = agora_kill
                try:
                    total = registrar_caca_solo(s.name, xp, gold, drops, raridades,
                                                duracao_segundos=duracao_kill)
                except Exception as e:
                    log(tag, f"(não consegui registrar a caçada: {e!r})")
                    total = _ler_relatorio_total_caca_solo()
                feitas += 1
                log(tag, f"🏁 caçada #{total} concluída ⭐ {xp} XP 💰 {gold} gold "
                         f"({feitas} desta conta desde que iniciou).")
            elif is_armadilha_solo(txt):
                log(tag, "⚠️ caí numa armadilha.")

            await tratar_evento_solo(s)

            # confere o HP de novo (armadilha/evento podem ter deixado baixo)
            # ANTES de clicar 'Caçar de novo'. Duas checagens: a normal (%HP
            # configurado) E uma EXTRA por valor REAL de HP (não %) — pedido
            # do usuário especificamente pra armadilha, como camada extra de
            # segurança (0 = desligada).
            await s.refresh()
            cur, hp_max = parse_hp_voce_solo(s.text, s)
            ratio = (cur / hp_max) if (cur is not None and hp_max) else None
            hp_min_armadilha = int(getattr(config, "CACA_SOLO", {}).get("hp_minimo_armadilha", 0) or 0)
            precisa_curar = (ratio is not None and ratio <= s.caca_vida_ratio) or (
                hp_min_armadilha > 0 and cur is not None and cur <= hp_min_armadilha)
            if precisa_curar:
                if await act_potion(s):
                    await asyncio.sleep(config.ACTION_DELAY)
                    await s.refresh()
                else:
                    detalhe_hp = f"{cur} ({ratio:.0%} do máximo)" if (cur is not None and ratio is not None) else str(cur)
                    log(tag, f"🛑 HP em {detalhe_hp} e SEM Poção de Vida — parando por "
                             f"segurança, NÃO vou caçar de novo assim.")
                    registrar_pausa("pocao_vida_baixa",
                                    f"{s.name}: sem Poção de Vida com HP baixo antes de caçar de novo")
                    return False

            if max_cacadas and feitas >= max_cacadas:
                log(tag, f"🎯 atingiu o limite de {max_cacadas} caçada(s) — parando.")
                registrar_pausa("limite_caca_solo", f"{s.name}: {feitas}/{max_cacadas}")
                return False
            if parar_no_fim_pedido():
                log(tag, "⏸ 'Parar no fim' atendido — parando.")
                registrar_pausa("parar_no_fim", f"{s.name}: após concluir a caçada atual")
                return False

            s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
            if s._contador_perfil % 3 == 0:
                await atualizar_perfil_e_estimativa(s, chave_tempo=f"caca_solo:{s.name}")
            await talvez_vender_no_mercado(s)
            await talvez_ler_inventario(s)
            _c = player_hp(s.text, s.char)
            if _c:
                write_status(s.name, _c[0], _c[1], f"{feitas} caça(s)",
                             inicio_ts=getattr(s, "_t_inicio_conteudo", None),
                             nivel=getattr(s, "_nivel", None), xp_faltam=getattr(s, "_xp_faltam", None),
                             eta_proximo_nivel_seg=getattr(s, "_eta_proximo_nivel_seg", None))

            b = _botao_caca_de_novo(s)
            if b:
                await s.click(b, label="Caçar de novo")
                brain.round_num = 0
                brain.soul_ready_at = {}
            await asyncio.sleep(config.ACTION_DELAY)
        except Exception as e:
            log(tag, f"💥 erro na Caçada Solo: {e!r} — REINICIANDO pra continuar de onde parou.")
            log(tag, "🔎 detalhe do erro:\n" + traceback.format_exc())
            return True


async def run_caca_solo(sessions, baseline: int = 0) -> bool:
    """Roda a Caçada Solo com N contas, cada uma TOTALMENTE independente (sem
    sala, sem parceiro) — todas em paralelo. Retorna True se ALGUMA precisou
    reiniciar o bot (erro), False se todas pararam de propósito."""
    if not sessions:
        return False
    resultados = await asyncio.gather(*(run_caca_solo_conta(s, baseline) for s in sessions))
    return any(resultados)


def registrar_observado(nome_conta: str, gold: int, xp: int, drop: str = None,
                        raridade: str = None, monstro: str = None) -> None:
    """Registra XP/gold/drop capturado no MODO OBSERVADOR (o usuário joga na
    mão; o bot só LÊ a tela, nunca clica em nada) — soma no resumo diário
    igual qualquer outro conteúdo (aparece em 'Por dia' no Relatório), e
    guarda um histórico próprio ('observado') pra conferência. Como o
    observador não sabe quando uma masmorra/caçada/cripta começa ou termina
    (só vê os blocos 'Recompensas (vs X)' aparecerem conforme os monstros
    morrem), não conta como uma 'execução' de nenhum conteúdo específico —
    só soma XP/gold/drops mesmo, que é o que foi pedido."""
    dados = _ler_relatorio()
    dia = datetime.now().strftime("%Y-%m-%d")
    diario = dados.setdefault("diario", {}).setdefault(dia, {})
    diario["xp_observado"] = diario.get("xp_observado", 0) + int(xp or 0)
    diario["gold_observado"] = diario.get("gold_observado", 0) + int(gold or 0)
    _somar_por_conta_diario(diario, {nome_conta: gold or 0}, {nome_conta: xp or 0})
    if drop:
        obs = dados.setdefault("observado", [])
        obs.append({
            "n": len(obs) + 1,
            "hora": datetime.now().strftime("%d/%m %H:%M"),
            "conta": nome_conta,
            "monstro": monstro or "",
            "item": drop,
            "gold": gold or 0,
            "xp": xp or 0,
        })
        dados["observado"] = obs[-3000:]
        if raridade and drop:
            _registrar_itens_no_banco(dados, {drop: raridade}, origem="observador")
    _salvar_relatorio(dados)


async def observar_conta(s: Session) -> None:
    """MODO OBSERVADOR: fica só LENDO a tela dessa conta pra sempre — NUNCA
    clica em nada, nunca ataca, nunca bebe poção. Serve pra quem prefere
    jogar na mão mas ainda quer que o TofuBot capture XP/Gold/Loot pro
    Relatório normal enquanto isso. Funciona em QUALQUER conteúdo do jogo
    (Masmorra, Caçada em Dupla, Cripta, Templo do Oásis, Caçada Solo, Missão
    Oásis) porque usa o mesmo bloco 'Recompensas (vs <Monstro>)' que aparece
    no log de Últimos Eventos toda vez que um monstro morre, em qualquer um
    desses — não precisa saber diferenciar qual conteúdo é qual."""
    vistos = set()
    log(s.name, "👁️ observando (só lendo — não vou clicar em nada).")
    while True:
        try:
            await s.refresh()
            for bloco in parse_recompensas(s.text):
                h = _recompensa_hash(bloco)
                if h in vistos:
                    continue
                vistos.add(h)
                for j in bloco["jogadores"]:
                    registrar_observado(j["nome"], j["gold"], j["xp"], j["item"],
                                       monstro=bloco["monstro"])
                    if j["item"]:
                        log(s.name, f"✨ {j['nome']} ganhou '{j['item']}' "
                                    f"(vs {bloco['monstro']}) — registrado.")
        except Exception as e:
            log(s.name, f"⚠️ observador: erro lendo a tela ({e!r}) — tentando de novo.")
        await asyncio.sleep(config.POLL_INTERVAL)


async def run_observador(sessions) -> bool:
    """Roda o modo Observador em paralelo pra todas as contas marcadas.
    NUNCA clica em nada (só chama observar_conta, que só lê). Não tem
    'return True/False' de reinício por erro específico — como não faz
    nenhuma ação, o pior que pode acontecer é perder uma leitura, e o loop
    interno de observar_conta já se recupera sozinho disso."""
    log("bot", f"👁️ Modo Observador: {len(sessions)} conta(s), só capturando "
               f"XP/Gold/Loot pro Relatório — sem clicar em nada.")
    await asyncio.gather(*(observar_conta(s) for s in sessions))
    return False



async def run_missao_oasis_conta(s: Session, baseline: int = 0) -> bool:
    """Roda a Missão Oásis (busca do Sunred) pra UMA conta, independente
    (sem sala/parceiro, igual à Caçada Solo). Fluxo:
      1) Menu -> Caçar -> 'Vale das Miragens' (2 trilhas).
      2) SEM a busca CERTA ativa: 'Trilha Silenciosa' repetidamente — cada
         tentativa pode dar Nurmora (opcional), Lana (só explora) ou Sunred
         (o importante). No Sunred: se ele está OFERECENDO uma busca nova,
         aceita (não dá pra saber qual monstro é ANTES) e confere na tela
         seguinte; se bateu com o configurado no painel, marca a busca como
         'certa' — senão, na próxima vez que encontrar o Sunred (com a busca
         ERRADA já ativa), desiste dela e volta a tentar.
      3) COM a busca certa ativa: 'Trilha Instável' pra caçar de verdade
         (é a MESMA mecânica de combate da Caçada Solo comum). O filtro
         dinâmico (ver act_combate_solo) decide sozinho quando focar só no
         monstro da busca.
      4) Ao suspeitar que já bateu os 50+200 (pela própria contagem), para
         de caçar e vai conferir com o Sunred de verdade antes de entregar
         (a tela de status dele é a fonte da verdade, corrige qualquer
         contagem própria que tenha desalinhado).
    Retorna True se precisa REINICIAR o bot (erro), False se parou de
    propósito (limite/energia sem poção/parar no fim)."""
    tag = f"oasis-{s.name}"
    monstro_alvo = (s.acc.get("monstro_alvo") or "").strip()
    if not monstro_alvo:
        log(tag, "⚠️ nenhum monstro-alvo configurado pra essa conta — não vou fazer nada "
                 "(escolha um em 'Missão Oásis' no painel).")
        return False
    cfg = config.MISSAO_OASIS
    fazer_nurmora = bool(s.acc.get("fazer_nurmora", False))
    # "Focar na Nurmora" (pedido do usuário 2026-07-15): foge de QUALQUER
    # monstro comum (explora mais rápido, sem gastar tempo lutando) e SEMPRE
    # aceita a Nurmora quando ela aparecer — sem mexer em NADA da lógica do
    # Sunred (aceitar oferta nova, busca ativa, cancelar busca errada, etc
    # continuam EXATAMENTE iguais, pra nunca cancelar uma missão em
    # andamento por engano). Liga isso também força fazer_nurmora=True (não
    # faria sentido "focar" nela e ainda assim recusar quando aparecer).
    focar_nurmora = bool(s.acc.get("focar_nurmora", False))
    if focar_nurmora:
        fazer_nurmora = True
    energia_minima = int(cfg.get("energia_minima", 5))
    energia_alvo = int(cfg.get("energia_alvo", 35))
    max_missoes = int(cfg.get("max_missoes", 0))
    vida_min_pct = int(cfg.get("vida_min_pct", 40))
    vida_pct_conta = s.acc.get("vida_min_pct")
    vida_pct_conta = vida_min_pct if vida_pct_conta is None else int(vida_pct_conta)
    s.caca_vida_ratio = max(0, min(100, vida_pct_conta)) / 100.0
    s.pocao_minima_caca = int(config.POCOES.get("pocao_vida_minima", 10))
    s.modo_caca = True
    s.missao_oasis_ativa = True
    s.missao_alvo = monstro_alvo
    s._missao_kills_totais = 0
    s._missao_kills_alvo = 0
    s._ultimo_mob_missao = ""
    s._missao_item_nome = ""
    brain = Brain(s)
    s._t_inicio_conteudo = time.time()
    feitas = baseline
    sem_reconhecer = 0
    busca_certa_ativa = False   # sabemos que a busca CERTA está ativa agora?
    verificando_fim = False     # suspeita de ter completado -> vai conferir com o Sunred

    log(tag, f"🏜️ Missão Oásis: alvo '{monstro_alvo}', energia mín {energia_minima}, "
             f"reabastece até {energia_alvo}, limite {max_missoes or 'sem limite'}.")

    # RETOMADA (pedido do usuário 2026-07-16, mesmo princípio da Caçada Solo/
    # Masmorra/Cripta/Caçada Dupla): se a conta JÁ ESTÁ em combate ativo, pula
    # a viagem/preparação (que poderia clicar 'Menu'/'Viajar' inexistentes
    # durante o combate e cair num /start indevido) e vai direto pro loop.
    # Observação: os contadores da busca (kills totais/do alvo) recomeçam do
    # zero nesse caso — é uma escolha SEGURA de propósito (a lógica só usa
    # esses números pra decidir quando focar só no alvo por segurança de
    # tempo; recontar do zero só atrasa um pouco essa decisão, nunca faz
    # desistir de algo que já tinha sido conquistado).
    await s.refresh()
    ja_em_combate = is_combat_screen_solo(s.message)
    if ja_em_combate:
        log(tag, "▶️ Missão Oásis ATIVA detectada — retomando o combate de onde parou.")
        brain.rodadas_desde_resync_alma = RESYNC_ALMA_RODADAS
    else:
        # viajar_para já chama back_to_menu() por dentro (Menu / sair do lobby /
        # voltar, só recorrendo a /start como ÚLTIMO recurso) — antes havia um
        # /start incondicional aqui, que só fazia sentido quando a limpeza
        # profunda do histórico (agora opcional, config.LIMPEZA_PROFUNDA_ATIVO)
        # sempre rodava antes e deixava a conversa sem nenhum botão.
        await viajar_para(s, "Oásis Perdido")
        await asyncio.sleep(config.ACTION_DELAY)
        await limpar_historico(s, manter=1)
        await back_to_menu(s)
        await try_tonico(s)
        await try_elixir(s)

    while True:
        try:
            await s.refresh()

            if _no_menu_principal(s.message):
                sem_reconhecer = 0
                await try_tonico(s)
                await try_elixir(s)
                await s.refresh()
                en = energia_atual(s.text)
                if en and en[0] < energia_minima:
                    log(tag, f"⚡ energia {en[0]}/{en[1]} abaixo de {energia_minima} — reabastecendo.")
                    if not await energia_encher_ate(s, energia_alvo):
                        registrar_pausa("pocao_energia_indisponivel", f"{s.name}: sem Poção de Energia")
                        return False
                    continue
                b = find_button(s.message, "cacar", "caçar")
                if not b:
                    await poll_sleep()
                    continue
                await s.click(b, label="Caçar")
                brain.round_num = 0
                brain.soul_ready_at = {}
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            await s.refresh()
            txt = s.text

            if is_sem_energia_solo(txt):
                sem_reconhecer = 0
                log(tag, "⚡ sem energia — voltando ao menu.")
                await s.click_text("menu", label="Menu", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if is_fuga_solo(txt):
                sem_reconhecer = 0
                await s.click_text("menu", label="Menu", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            if is_derrota_solo(txt):
                sem_reconhecer = 0
                xp_perdido, hp_restaurado = parse_derrota_solo(txt)
                mob = getattr(s, "_ultimo_mob_combate", "") or "monstro não identificado"
                log(tag, f"💀 MORREU contra '{mob}'! Perdeu "
                         f"{xp_perdido if xp_perdido is not None else '?'} XP, "
                         f"HP restaurado para {hp_restaurado if hp_restaurado is not None else '?'}."
                         f"{frase_ultimo_hp_antes_morte(s)}")
                try:
                    registrar_morte("missao_oasis", nome_conta=s.name)
                except Exception as e:
                    log(tag, f"(não consegui registrar a morte: {e!r})")
                s._mob_logado = None
                await s.click_text("menu", label="Menu", required=False)
                alvo_seguranca = min(1.0, getattr(s, "caca_vida_ratio", 0.40) + 0.20)
                await curar_repetido_no_menu(s, alvo_seguranca)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Sem energia pra explorar a trilha (Vale das Miragens) — BUG
            # REAL corrigido 2026-07-16 (contas presas em loop, nunca
            # recuperavam energia): antes essa tela nem era reconhecida, o
            # fallback genérico só clicava 'Voltar' (voltando pro Vale das
            # Miragens), e o código escolhia a MESMA trilha de novo sem
            # checar energia — loop infinito. Agora volta até o MENU
            # principal do Oásis de vez (não só 1 tela pra trás), pra cair no
            # ramo de cima que já sabe reabastecer energia de verdade.
            if is_sem_energia_trilha(txt):
                sem_reconhecer = 0
                log(tag, "⚡ sem energia pra explorar a trilha — voltando pro "
                         "Oásis Perdido pra reabastecer.")
                await s.click_text("voltar", label="Voltar", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                await s.refresh()
                await s.click_text("voltar ao oasis", "voltar ao oásis", "voltar",
                                   label="Voltar ao Oásis", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Vale das Miragens: escolhe a trilha certa conforme o estado.
            # Se suspeita de ter completado a busca (verificando_fim), força
            # Trilha Silenciosa mesmo com busca_certa_ativa=True, pra ir
            # CONFERIR com o Sunred antes de entregar.
            # "Focar na Nurmora": ela só aparece (com Lana e Sunred) na
            # Trilha Silenciosa — esse mapa NÃO TEM monstro nenhum lá,
            # confirmado pelo usuário. Por isso força SEMPRE Silenciosa,
            # nunca entra na Instável (que é só combate, sem NPC nenhum) —
            # sem mexer no estado da busca do Sunred, só não avança ela.
            if is_vale_miragens(s.message):
                sem_reconhecer = 0
                if focar_nurmora:
                    await s.click_text("trilha silenciosa", label="Trilha Silenciosa")
                elif busca_certa_ativa and not verificando_fim:
                    await s.click_text("trilha instavel", "trilha instável",
                                       label="Trilha Instável")
                else:
                    await s.click_text("trilha silenciosa", label="Trilha Silenciosa")
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- NPC Nurmora (Martelo Mágico) — totalmente opcional, sem
            # relação com a busca do Sunred. Ambos os estados dela (nova
            # oferta OU quest já ativa) usam textos de botão parecidos o
            # bastante pra tentar nessa ordem sem precisar diferenciar. A
            # tela final ('Você recebeu Martelo Mágico!') só tem 'Voltar' —
            # sem esse fallback o bot ficava parado sem reconhecer nada ali.
            if is_npc_nurmora(txt):
                sem_reconhecer = 0
                # Tela de CONCLUSÃO da quest ('Você recebeu Martelo Mágico!')
                # — registra no relatório antes de só clicar Voltar (pedido
                # do usuário 2026-07-15: contador de quantos já conseguiu).
                if "recebeu" in norm(txt) and "martelo" in norm(txt):
                    try:
                        total_martelo = registrar_martelo_magico(s.name)
                        log(tag, f"🔨 recebeu o Martelo Mágico da Nurmora! (#{total_martelo} no total)")
                    except Exception as e:
                        log(tag, f"(não consegui registrar o Martelo Mágico: {e!r})")
                    await s.click_text("voltar", label="Voltar", required=False)
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if fazer_nurmora:
                    feita = (await s.click_text("entregar", required=False)
                              or await s.click_text("aceitar quest", required=False)
                              or await s.click_text("continuar", required=False))
                    if not feita:
                        feita = await s.click_text("agora nao", "agora não", required=False)
                    if not feita:
                        await s.click_text("voltar", label="Voltar", required=False)
                else:
                    feita = (await s.click_text("agora nao", "agora não", required=False)
                              or await s.click_text("continuar", "desistir da quest", required=False))
                    if not feita:
                        await s.click_text("voltar", label="Voltar", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Confirmação 'Quest aceita!' da Nurmora ("Nurmora bate o
            # martelo na bigorna...") — só tem 'Continuar'. Essa tela NÃO tem
            # o título 'Nurmora, a Primeira Forjadora' (só menciona o nome na
            # fala), por isso não cai no bloco de cima — sem esse
            # reconhecimento à parte, ficava presa/lenta até o /start.
            if "quest aceita" in norm(txt):
                sem_reconhecer = 0
                await s.click_text("continuar", label="Continuar", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- NPC Lana — só um evento de exploração, sem decisão real.
            # A fala dela é FIXA (o mesmo texto em vários encontros diferentes)
            # — por isso não dá pra confirmar "mudou de verdade" comparando o
            # texto (um encontro novo legítimo pode ficar idêntico ao
            # anterior). Só clica e segue; se de fato não tiver avançado, o
            # próprio loop principal vai reconhecer a Lana de novo na volta
            # seguinte e tenta outra vez, sem essa conta ficar presa esperando
            # confirmação que nunca chega.
            if is_npc_lana(txt):
                sem_reconhecer = 0
                await s.click_text("continuar explo", "continuar", label="Continuar explorando",
                                   required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- NPC Sunred: o coração da Missão Oásis.
            if is_npc_sunred(txt):
                sem_reconhecer = 0
                estado = sunred_estado(s.message, txt)
                if estado == "oferta":
                    # SEM visibilidade do item ANTES de aceitar — aceita e só
                    # confere na tela seguinte ('Busca aceita!').
                    await s.click_text("aceitar busca", label="Aceitar Busca")
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if estado == "aceita":
                    item = parse_quest_item(txt)
                    bateu = item is not None and norm(monstro_alvo) in norm(item[1])
                    if bateu:
                        log(tag, f"✅ busca CERTA aceita: {item[1]} (alvo: {monstro_alvo}).")
                        busca_certa_ativa = True
                        verificando_fim = False
                        s._missao_kills_totais = 0
                        s._missao_kills_alvo = 0
                        s._missao_item_nome = item[1]
                        write_status_missao_oasis(s.name, item[1], 0, 200, 0, 50)
                    else:
                        log(tag, f"❌ busca ERRADA aceita ({item[1] if item else '?'}) — vou "
                                 f"desistir dela na próxima vez que encontrar o Sunred.")
                        busca_certa_ativa = False
                    await s.click_text("continuar", label="Continuar")
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if estado == "ativa":
                    item = parse_quest_item(txt)
                    kills, itens = parse_quest_status(txt)
                    bateu = item is not None and norm(monstro_alvo) in norm(item[1])
                    if not bateu:
                        log(tag, f"❌ busca ATIVA é a errada ({item[1] if item else '?'}) — "
                                 f"desistindo.")
                        busca_certa_ativa = False
                        verificando_fim = False
                        await s.click_text("desistir da busca", label="Desistir da busca")
                        await asyncio.sleep(config.ACTION_DELAY)
                        continue
                    busca_certa_ativa = True
                    if kills:
                        s._missao_kills_totais = kills[0]
                    if itens:
                        s._missao_kills_alvo = itens[0]
                    s._missao_item_nome = item[1]
                    write_status_missao_oasis(
                        s.name, item[1], s._missao_kills_totais, (kills[1] if kills else 200),
                        s._missao_kills_alvo, (itens[1] if itens else 50))
                    # Nota: na prática, uma vez que bate 50+200 o jogo já troca
                    # os BOTÕES pro estado 'pronta_entregar' (tratado abaixo) —
                    # esse 'completou' aqui é só uma rede de segurança caso
                    # apareça uma variação com os botões antigos mesmo já completo.
                    completou = (itens is not None and itens[0] >= itens[1]
                                 and kills is not None and kills[0] >= kills[1])
                    if completou:
                        log(tag, f"🎉 busca CONFIRMADA completa ({itens[0]}/{itens[1]}, "
                                 f"kills {kills[0]}/{kills[1]}) — mas essa tela não tem botão de "
                                 f"entrega; procurando o Sunred de novo pra receber.")
                        # Continua explorando — o Sunred deve mostrar o estado
                        # 'pronta_entregar' da próxima vez (ver sunred_estado).
                    verificando_fim = False
                    await s.click_text("continuar", label="Continuar")
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if estado == "pronta_entregar":
                    # '✅ Condições cumpridas! Entregue a recompensa.' — bateu
                    # os 50+200, só falta clicar pra receber. Depois desse
                    # clique vem a tela 'concluida' (só Menu), tratada abaixo.
                    log(tag, "✅ condições cumpridas — entregando a recompensa.")
                    await s.click_text("entregar recompensa", label="Entregar recompensa")
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if estado == "concluida":
                    m = re.search(r"voc[eê] recebeu\s+(.+?)!", txt, re.IGNORECASE)
                    recompensa = m.group(1).strip() if m else ""
                    log(tag, f"🎁 busca concluída — recebeu {recompensa or '(?)'}.")
                    await s.click_text("menu", label="Menu", required=False)
                    try:
                        total = registrar_missao_oasis(s.name, monstro_alvo, recompensa)
                    except Exception as e:
                        log(tag, f"(não consegui registrar a missão: {e!r})")
                        total = _ler_relatorio_total_missao_oasis()
                    feitas += 1
                    log(tag, f"🏁 Missão Oásis #{total} concluída ({monstro_alvo}) "
                             f"({feitas} desta conta desde que iniciou).")
                    busca_certa_ativa = False
                    verificando_fim = False
                    s._missao_kills_totais = 0
                    s._missao_kills_alvo = 0
                    s._missao_item_nome = ""
                    write_status_missao_oasis(s.name, "", 0, 200, 0, 50)
                    if max_missoes and feitas >= max_missoes:
                        log(tag, f"🎯 atingiu o limite de {max_missoes} missão(ões) — parando.")
                        registrar_pausa("limite_missao_oasis", f"{s.name}: {feitas}/{max_missoes}")
                        return False
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                # 'desconhecido': loga 1x e volta, sem arriscar clicar errado.
                log(tag, f"🔎 tela do Sunred não reconhecida — investigar:\n"
                         f"    TEXTO: {txt}\n    BOTÕES: {button_texts(s.message)}")
                await go_back(s)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Confirmação de 'Busca cancelada' (depois de Desistir da busca)
            if "busca cancelada" in norm(txt):
                sem_reconhecer = 0
                await s.click_text("voltar", label="Voltar", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Confirmação de 'Progresso atualizado!' (depois de entregar
            # ouro/poções pra Nurmora) — só tem 'Voltar', e essa tela NÃO
            # menciona 'Nurmora' no texto (por isso não cai no bloco dela lá
            # em cima) — sem esse reconhecimento, o bot ficava mandando
            # /start à toa em vez de só voltar.
            if "progresso atualizado" in norm(txt):
                sem_reconhecer = 0
                await s.click_text("voltar", label="Voltar", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            # --- Combate de verdade (Trilha Instável) — MESMA mecânica da
            # Caçada Solo comum (Atacar/Consumíveis/Almas/Fugir). Com
            # focar_nurmora=True a conta nunca entra aqui de propósito (fica
            # sempre na Trilha Silenciosa, que não tem monstro nenhum — ver
            # bloco 'Vale das Miragens' acima), mas o bloco continua existindo
            # como está pra não mudar o comportamento normal da Missão Oásis.
            if is_combat_screen_solo(s.message):
                sem_reconhecer = 0
                nome_mob_atual = parse_monstro_nome_solo(s.text)
                if nome_mob_atual:
                    s._ultimo_mob_missao = nome_mob_atual
                texto_antes = s.text
                if not await act_combate_solo(s, brain):
                    registrar_pausa("pocao_vida_baixa",
                                    f"{s.name}: sem Poção de Vida com HP baixo EM COMBATE")
                    return False
                deadline = time.time() + config.ROUND_TIMEOUT_CACA
                ultima_tentativa = time.time()
                while time.time() < deadline:
                    await s.refresh()
                    if s.text != texto_antes:
                        break
                    if time.time() - ultima_tentativa >= config.RETRY_ACAO_APOS_CACA:
                        log(tag, f"🔁 sem nenhuma mudança em {config.RETRY_ACAO_APOS_CACA:.0f}s "
                                 f"— o clique pode ter falhado, reagindo de novo.")
                        if not await act_combate_solo(s, brain):
                            registrar_pausa("pocao_vida_baixa",
                                            f"{s.name}: sem Poção de Vida com HP baixo EM COMBATE")
                            return False
                        ultima_tentativa = time.time()
                        continue
                    await poll_sleep()
                continue

            reconheceu = (is_vitoria_solo(txt) or is_armadilha_solo(txt)
                          or is_compra_realizada_solo(txt) or is_mercador_deserto_solo(txt)
                          or is_mercador_viajante_solo(txt) or is_goblin_gibby_solo(txt)
                          or bool(_botao_caca_de_novo(s)))
            if not reconheceu:
                sem_reconhecer += 1
                # Antes de partir pro /start, tenta um recurso mais BARATO:
                # se a tela tiver um botão 'Menu' (ex: presa num submenu
                # perdido tipo Inventário, aberto pelo try_tonico/try_elixir),
                # clica nele — na prática resolve a maioria dos casos sem
                # precisar de /start. Só manda /start se isso também não
                # resolver depois de várias tentativas.
                b_menu = find_button(s.message, "menu")
                if b_menu and sem_reconhecer < 5:
                    log(tag, f"↩️ tela não reconhecida ({sem_reconhecer}/5) — clicando 'Menu' "
                             f"pra tentar voltar.\n    texto: {txt!r}\n"
                             f"    botões: {button_texts(s.message)}")
                    await s.click(b_menu, label="Menu")
                    await asyncio.sleep(config.ACTION_DELAY)
                    continue
                if sem_reconhecer >= 5:
                    if await _tentar_evitar_start(s):
                        sem_reconhecer = 0
                        continue
                    log(tag, "⚠️ não reconheci a tela há um tempo — mandando /start "
                             "pra tentar voltar ao menu.\n"
                             f"    texto atual: {txt!r}\n"
                             f"    botões: {button_texts(s.message)}")
                    await s.send_start()
                    sem_reconhecer = 0
                else:
                    await poll_sleep()
                continue
            sem_reconhecer = 0

            if is_vitoria_solo(txt):
                xp, gold, drops, raridades = parse_resultado_caca_solo(txt)
                try:
                    registrar_missao_oasis_xp(xp, gold, nome_conta=s.name)
                except Exception as e:
                    log(tag, f"(não consegui registrar o XP/gold da vitória: {e!r})")
                vit_quest = parse_quest_vitoria(txt)
                if vit_quest:
                    itens_atual, itens_meta, kills_atual, kills_meta = vit_quest
                    s._missao_kills_alvo = itens_atual
                    s._missao_kills_totais = kills_atual
                    log(tag, f"🏁 vitória ⭐ {xp} XP 💰 {gold} gold — {monstro_alvo}: "
                             f"{itens_atual}/{itens_meta}, total: {kills_atual}/{kills_meta} "
                             f"(confirmado pela tela).")
                    if itens_atual >= itens_meta and kills_atual >= kills_meta:
                        verificando_fim = True
                else:
                    # sem a linha da busca nessa vitória: soma por conta própria
                    # (é a maioria dos casos — só monstro comum, longe de 50/200).
                    mob_desta_vitoria = getattr(s, "_ultimo_mob_missao", "") or "monstro não identificado"
                    log(tag, f"⚔️ vitória ⭐ {xp} XP 💰 {gold} gold — {mob_desta_vitoria} "
                             f"(sem confirmação da busca nessa tela — contagem própria: "
                             f"{s._missao_kills_totais + 1}/200 total, "
                             f"{s._missao_kills_alvo + (1 if norm(mob_desta_vitoria) == norm(monstro_alvo) else 0)}/50 do alvo).")
                    s._missao_kills_totais += 1
                    if s._ultimo_mob_missao and norm(s._ultimo_mob_missao) == norm(monstro_alvo):
                        s._missao_kills_alvo += 1
                    if s._missao_kills_alvo >= 50 and s._missao_kills_totais >= 200:
                        verificando_fim = True
                write_status_missao_oasis(s.name, s._missao_item_nome, s._missao_kills_totais,
                                          200, s._missao_kills_alvo, 50)
            elif is_armadilha_solo(txt):
                log(tag, "⚠️ caí numa armadilha.")

            await tratar_evento_solo(s)

            await s.refresh()
            cur, hp_max = parse_hp_voce_solo(s.text, s)
            ratio = (cur / hp_max) if (cur is not None and hp_max) else None
            # mesma checagem extra por HP real (não %) da Caçada Solo — usa o
            # mesmo valor configurado lá (é o mesmo tipo de armadilha).
            hp_min_armadilha = int(getattr(config, "CACA_SOLO", {}).get("hp_minimo_armadilha", 0) or 0)
            precisa_curar = (ratio is not None and ratio <= s.caca_vida_ratio) or (
                hp_min_armadilha > 0 and cur is not None and cur <= hp_min_armadilha)
            if precisa_curar:
                if await act_potion(s):
                    await asyncio.sleep(config.ACTION_DELAY)
                    await s.refresh()
                else:
                    detalhe_hp = f"{cur} ({ratio:.0%} do máximo)" if (cur is not None and ratio is not None) else str(cur)
                    log(tag, f"🛑 HP em {detalhe_hp} e SEM Poção de Vida — parando por segurança.")
                    registrar_pausa("pocao_vida_baixa",
                                    f"{s.name}: sem Poção de Vida com HP baixo antes de caçar de novo")
                    return False

            if parar_no_fim_pedido():
                log(tag, "⏸ 'Parar no fim' atendido — parando.")
                registrar_pausa("parar_no_fim", f"{s.name}: após concluir a caçada atual")
                return False

            s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
            if s._contador_perfil % 3 == 0:
                await atualizar_perfil_e_estimativa(s)
            await talvez_vender_no_mercado(s)
            await talvez_ler_inventario(s)
            # Missão Oásis nunca alimentava o Status ao vivo — corrigido
            # junto (pedido do usuário 2026-07-15: "pra todos os conteúdos").
            _c = player_hp(s.text, s.char)
            if _c:
                write_status(s.name, _c[0], _c[1], "Missão Oásis",
                             inicio_ts=getattr(s, "_t_inicio_conteudo", None),
                             nivel=getattr(s, "_nivel", None), xp_faltam=getattr(s, "_xp_faltam", None),
                             eta_proximo_nivel_seg=getattr(s, "_eta_proximo_nivel_seg", None))

            if verificando_fim:
                # Suspeita de ter completado — volta ao Menu pra ir conferir
                # com o Sunred (Trilha Silenciosa) em vez de caçar de novo.
                await s.click_text("menu", label="Menu", required=False)
                await asyncio.sleep(config.ACTION_DELAY)
                continue

            b = _botao_caca_de_novo(s)
            if b:
                await s.click(b, label="Caçar de novo")
                brain.round_num = 0
                brain.soul_ready_at = {}
            await asyncio.sleep(config.ACTION_DELAY)
        except Exception as e:
            log(tag, f"💥 erro na Missão Oásis: {e!r} — REINICIANDO pra continuar de onde parou.")
            log(tag, "🔎 detalhe do erro:\n" + traceback.format_exc())
            return True


async def run_missao_oasis(sessions, baseline: int = 0) -> bool:
    """Roda a Missão Oásis com N contas, cada uma TOTALMENTE independente
    (sem sala, sem parceiro, cada uma com seu próprio monstro-alvo) — todas
    em paralelo. Retorna True se ALGUMA precisou reiniciar o bot (erro),
    False se todas pararam de propósito."""
    if not sessions:
        return False
    resultados = await asyncio.gather(*(run_missao_oasis_conta(s, baseline) for s in sessions))
    return any(resultados)


async def combat_loop_caca(s: Session, andar_maximo: int, recompensas=None, estado=None):
    """Como combat_loop (masmorra), mas pra Caçada em Dupla: sai sozinho
    (sem depender de 'someone_died' de um grupo de 4) quando o andar CHEGA em
    andar_maximo (não passa dele). Reaproveita Brain/HP/turno/almas — SEM tocar
    em combat_loop original, pra não arriscar a masmorra que já funciona.
    'estado' é um dict COMPARTILHADO entre as 2 contas: se uma marca
    estado['sair'] (ex: morte), a OUTRA sai NA MESMA HORA.
    Retorna o MOTIVO da saída: "andar_limite" | "morte" | "pocao_baixa" |
    "tela" | "rodadas" — quem chama usa isso pra decidir recomeçar ou pausar."""
    brain = Brain(s)
    # RETOMADA (pedido do usuário 2026-07-16): ver comentário igual no
    # combat_loop (Masmorra) sobre por que força o resync aqui.
    if getattr(s, "_retomando_conteudo", False):
        s._retomando_conteudo = False
        brain.rodadas_desde_resync_alma = RESYNC_ALMA_RODADAS
    rounds = 0            # limite de SEGURANÇA (MAX_ROUNDS) — reseta a cada andar, de propósito
    _ultimo_andar_rounds = None   # ver reset de 'rounds' logo abaixo, no parse_andar
    rounds_almas = 0       # BUG REAL corrigido (2026-07-12): usado só pra passar pro Brain.act()
    # (recarga das almas) — esse NUNCA reseta. Antes, os dois usavam a MESMA
    # variável 'rounds': ao trocar de andar, ela era zerada (de propósito,
    # só pro limite de segurança por andar) — só que isso também zerava o
    # contador que o Brain usa pra saber se uma alma já recarregou! Resultado:
    # toda vez que a dupla avançava de andar (o que acontece MUITO numa
    # caçada até o andar 41+), as almas ficavam "travadas" acreditando que
    # ainda estavam em recarga por dezenas de rodadas a mais do que deveriam
    # — o relatado "alma pronta mas não usa" bateu com uma taxa de uso real
    # de quase METADE do esperado pelos cooldowns configurados.
    sem_linha = 0   # nº de vezes seguidas que não achei minha linha (fallback)
    _ultima_limpeza_rounds = 0   # limpeza periódica do histórico durante a luta

    async def _capturar_resumo_caca():
        """BUG REAL corrigido 2026-07-16 (mesma causa do XP/Gold zerados na
        Cripta): antes tentava só 1 VEZ (sleep+refresh+parse) — se a tela de
        resumo ainda não tivesse carregado (ou já tivesse passado, sendo
        passageira) naquele instante exato, o loot dessa conta simplesmente
        não entrava no relatório, sem nem avisar. Agora tenta várias vezes,
        checando o texto atual ANTES de cada refresh nas trocas seguintes."""
        if recompensas is None:
            return
        for i in range(6):
            resumo = parse_resumo_caca(s.text)
            if resumo:
                recompensas.setdefault("resumo_por_conta", {})[s.name] = resumo
                return
            if i > 0:
                await poll_sleep()
            try:
                await s.refresh()
            except Exception as e:
                log(s.name, f"(resumo da caçada ignorado: {e!r})")
                return
        log(s.name, "(não achei a tela de resumo da caçada ao sair — "
                    "loot desta conta não será contado desta vez)")

    async def _sair_pocao_agora():
        """Item 2 (2026-07-16): chamado tanto no topo do loop quanto
        IMEDIATAMENTE depois de CADA brain.act() (incluindo os retries) —
        antes, só era checado 1x por volta do loop, então entre um
        brain.act() detectar 'sem poção' e a próxima checagem, o bot podia
        esperar a rodada inteira 'resolver' (até ROUND_TIMEOUT_CACA
        segundos, com retries chamando brain.act() de novo) com o HP
        crítico e sem cura. Retorna 'pocao_baixa' se saiu (o chamador deve
        encerrar a função na hora, devolvendo esse mesmo valor) ou None."""
        if not s.sair_caca_pocao:
            return None
        log(s.name, "🧪 saindo da caçada (Poções de Vida abaixo do limite). "
                    "O parceiro sai junto.")
        if estado is not None:
            estado["sair"] = "pocao_baixa"   # BUG REAL corrigido: antes só a
            # morte avisava o parceiro pra sair junto — poção baixa fazia só
            # ESTA conta sair, deixando o parceiro sozinho numa dupla
            # desfeita (visto em produção: ficou ~25min lutando sozinho até
            # finalmente "morrer" e só aí os dois saírem).
        await leave_room(s)
        # Captura o resumo de saída (XP/gold/drops) — BUG REAL corrigido
        # 2026-07-16 (mesma causa do relatório da Cripta ficar sempre
        # vazio): antes só capturava isso no ramo 'andar_limite', então
        # uma caçada que terminasse por poção baixa ou morte não tinha
        # nada pra registrar depois.
        await _capturar_resumo_caca()
        return "pocao_baixa"

    while True:
        await s.refresh()
        txt = s.text

        # A DUPLA mandou sair (ex: o parceiro morreu)? Sai NA MESMA HORA.
        if estado is not None and estado.get("sair"):
            log(s.name, f"🚪 saindo junto com a dupla ({estado['sair']}).")
            await leave_room(s)
            return estado["sair"]

        # captura recompensas da caçada (xp/gold/drops) — dedup por hash.
        # BLINDADO: um formato inesperado de recompensa não pode derrubar a
        # caçada (senão o bot reinicia à toa).
        if recompensas is not None:
            try:
                atualizar_recompensas(recompensas, s.texto_recompensas)
            except Exception as e:
                log(s.name, f"(recompensa ignorada: {e!r})")

        # poção de vida caiu abaixo do limite AO CURAR (marcado no act_potion)?
        _motivo_pocao = await _sair_pocao_agora()
        if _motivo_pocao:
            return _motivo_pocao

        andar = parse_andar(txt)
        s._andar_atual = andar   # pro gate "alma a partir do andar N" (use_soul_from_priority)
        # BUG REAL corrigido 2026-07-17 (usuário: "passei do limite de rodadas
        # na caçada" disparando bem antes do andar máximo configurado, mesmo
        # com o combate indo bem — "pensei que já tínhamos corrigido isso"):
        # o reset de 'rounds' mais abaixo (no ramo "not is_combat_screen" que
        # procura um botão 'Próximo/Avançar') NUNCA disparava de verdade em
        # Montanhas Gélidas — confirmado nos logs: o andar muda automaticamente
        # dentro da MESMA tela de combate (o cabeçalho já mostra o próximo
        # andar com o monstro seguinte em HP cheio, sem nenhuma tela
        # intermediária nem botão de confirmação). Como 'is_combat_screen'
        # continua True o tempo todo, aquele ramo nunca era alcançado, e
        # 'rounds' só crescia a caçada inteira até bater os 500 de segurança
        # e sair achando que travou, mesmo avançando andar após andar
        # normalmente. Agora reseta aqui, comparando o andar lido a cada
        # rodada com o último visto (companheiro do 'ZERA o contador' que já
        # existia pro ramo do botão — mantido como está, útil pros outros
        # conteúdos que passam por ali).
        if (andar is not None and _ultimo_andar_rounds is not None
                and andar > _ultimo_andar_rounds):
            rounds = 0
        if andar is not None:
            _ultimo_andar_rounds = andar
        if andar is not None and andar >= andar_maximo:
            log(s.name, f"🏁 cheguei no andar {andar} (limite {andar_maximo}) — saindo da caçada.")
            if recompensas is not None:
                # guarda o andar final ATINGIDO (não só o configurado) — pro
                # relatório mostrar até onde a caçada foi de verdade.
                recompensas["andar_final"] = max(recompensas.get("andar_final", 0), andar)
            await leave_room(s)
            # Ao sair, o jogo mostra um resumo (XP/gold/drops) PRÓPRIO desta
            # conta (drops são individuais, mesmo XP das 2).
            await _capturar_resumo_caca()
            return "andar_limite"

        if someone_died(txt):
            log(s.name, "💀 morte detectada na caçada — saindo. O parceiro sai junto.")
            # DEDUP: as 2 contas da dupla detectam a MESMA morte de forma
            # independente — só registra 1x (a 1ª a chegar aqui, antes de
            # 'estado[sair]' já estar marcado).
            ja_registrada = estado is not None and estado.get("sair") == "morte"
            if estado is not None:
                estado["sair"] = "morte"   # faz a OUTRA conta sair na mesma hora
            if not ja_registrada:
                try:
                    registrar_morte("caca_dupla")
                except Exception as e:
                    log(s.name, f"(não consegui registrar a morte: {e!r})")
            await leave_room(s)
            # ver comentário igual no ramo 'pocao_baixa' acima sobre por que
            # captura o resumo aqui também.
            await _capturar_resumo_caca()
            return "morte"

        if not is_combat_screen(s.message):
            if find_button(s.message, "proximo", "próximo", "continuar", "avancar", "avançar"):
                log(s.name, "➡️ avançando de andar.")
                await s.click_text("proximo", "próximo", "continuar",
                                   "avancar", "avançar", label="Próximo", required=False)
                # ZERA o contador de rodadas: MAX_ROUNDS é um limite de
                # segurança POR ANDAR (evita ficar preso lutando pra sempre),
                # não pra caçada inteira. Sem isso, ao somar as rodadas de
                # vários andares ele batia o limite e saía bem antes do andar
                # máximo configurado, mesmo indo tudo bem no combate.
                rounds = 0
                continue
            # LOBBY da caçada (esperando iniciar): ESPERA, não encerra à toa.
            if is_lobby_screen(s.message):
                await poll_sleep()
                continue
            # Pode ser tela transitória ou submenu aberto (Almas/Consumíveis) por
            # causa do card que se auto-atualiza. Antes de dar a caçada por
            # encerrada, tenta VOLTAR pro combate algumas vezes (evita reinício à toa).
            # BUG REAL corrigido (2026-07-13, morte relatada): às vezes aparece uma
            # notificação de OUTRA sala/masmorra ("A sala X expirou por
            # inatividade") que NÃO tem nada a ver com a caçada atual — parece
            # uma mensagem antiga/de outra sessão que fica "por cima" por um
            # instante. Isso fazia o bot desistir rápido demais (só 6
            # tentativas curtas) achando que realmente saiu do combate,
            # enquanto na verdade a conta CONTINUAVA lutando de verdade sem
            # ninguém cuidando do HP dela — e morreu assim. Agora, se o texto
            # bater com esse padrão de "outra sala", tenta bem mais vezes (o
            # dobro) antes de desistir, dando mais chance da tela real
            # reaparecer sozinha.
            # AMPLIADO 2026-07-16 (morte relatada: notificação "A troca foi
            # cancelada pelo outro jogador" — 1 clique perdido de troca com
            # validade de 20min, sem relação NENHUMA com a caçada): em vez de
            # tentar adivinhar toda frase possível de notificação avulsa,
            # detecta pelo FORMATO — tela não reconhecida com um único botão
            # 'Menu' (sem nada de combate/próximo) é a cara de um aviso do
            # tipo "toque OK pra fechar", venha de onde vier.
            _botoes_tela = button_texts(s.message)
            eh_notificacao_transitoria = (
                bool(re.search(r"expirou por inatividade", norm(txt)))
                or (len(_botoes_tela) == 1 and find_button(s.message, "menu") is not None))
            tentativas_voltar = 12 if eh_notificacao_transitoria else 6
            voltou = False
            for _ in range(tentativas_voltar):
                # Checagem de HP EM TODA tentativa, não só no final (BUG REAL
                # corrigido: a conta ficava sem ninguém olhando o HP dela
                # durante TODA a espera — se a notificação demorasse a sumir,
                # dava tempo de sobra pra morrer sem socorro nenhum).
                hp_emergencia = player_hp(s.text, s.char)
                if hp_emergencia and hp_emergencia[1]:
                    ratio_emerg = hp_emergencia[0] / hp_emergencia[1]
                    limite_emerg = getattr(s, "caca_vida_ratio", 0.4) or 0.4
                    if ratio_emerg <= limite_emerg:
                        log(s.name, f"🩺 emergência enquanto espera a tela real voltar: "
                                    f"HP em {ratio_emerg:.0%} — bebendo poção por segurança.")
                        await act_potion(s)
                if find_button(s.message, "voltar", "atras", "⬅", "◀", "🔙"):
                    await go_back(s)
                await s.refresh()
                if is_combat_screen(s.message) or find_button(
                        s.message, "proximo", "próximo", "continuar", "avancar", "avançar"):
                    voltou = True
                    break
                await poll_sleep()
            if voltou:
                continue
            # ÚLTIMA checagem de segurança ANTES de desistir de vez: se ainda
            # der pra ler o HP do próprio personagem em ALGUM lugar da tela
            # (mesmo não parecendo uma tela de combate reconhecível), bebe
            # poção se estiver baixo — mais seguro que simplesmente abandonar
            # o personagem torcendo pra estar tudo bem.
            hp_emergencia = player_hp(s.text, s.char)
            if hp_emergencia:
                ratio_emerg = hp_emergencia[0] / hp_emergencia[1] if hp_emergencia[1] else None
                limite_emerg = getattr(s, "caca_vida_ratio", 0.4) or 0.4
                if ratio_emerg is not None and ratio_emerg <= limite_emerg:
                    log(s.name, f"🩺 emergência antes de desistir da tela: HP em "
                                f"{ratio_emerg:.0%} — tentando beber poção por segurança.")
                    await act_potion(s)
            # mesmo enxugamento da masmorra: não despeja o texto inteiro numa
            # tela de conclusão conhecida (poluía o log 2x, uma por conta).
            if "conclu" in norm(txt):
                log(s.name, "🏁 caçada concluída — voltando ao menu.")
            else:
                log(s.name, "🏁 saí da tela de combate (caçada). Texto:\n"
                            f"    {txt}\n    botões: {button_texts(s.message)}")
            return "tela"

        # TURNO DA CAÇADA: ela TAMBÉM mostra a ampulheta ⏳ na linha de quem
        # ainda não agiu (mesmo sem tank). Uso ela igual à masmorra pra agir
        # EXATAMENTE na minha vez (sem atraso), só que achando minha linha por
        # nome + HP (a caçada não tem 'Nv.'). Depois de agir, espero a minha
        # ampulheta sumir pra não repetir a ação na mesma rodada.
        turno = my_turn_state_caca(txt, s.char)   # NÃO usar 'estado' (é o dict da dupla!)
        if turno == "waiting":
            sem_linha = 0
            rounds += 1
            rounds_almas += 1
            if rounds > config.MAX_ROUNDS:
                log(s.name, "⚠️ passei do limite de rodadas na caçada. Saindo.")
                await leave_room(s)
                return "rodadas"
            if rounds_almas - _ultima_limpeza_rounds >= 20:
                _ultima_limpeza_rounds = rounds_almas
                await limpar_historico(s)
                # ver comentário igual no combat_loop (Masmorra) sobre o
                # refresh extra necessário aqui — evita clicar num botão de
                # mensagem que a própria limpeza pode ter apagado.
                await s.refresh()
                txt = s.text
            _agora = time.time()
            _espera = _agora - getattr(s, "_t_fim_ciclo", _agora)  # tempo esperando a vez
            _t0 = time.time()
            await brain.act(rounds_almas)   # decide: poção > tônico(10min) > alma > ação
            _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
            if _motivo_pocao:
                return _motivo_pocao
            _t_acao = time.time() - _t0                            # tempo agindo (cliques)
            # ESPERA a minha ampulheta sumir (ação registrou/rodada resolveu) —
            # poll rápido, reforçando a ação se nada mudar (ver RETRY_ACAO_
            # APOS_CACA). HISTÓRICO: cheguei a tirar esse reforço achando que
            # ele causava ação duplicada (alma/poção reclicada) — mas a causa
            # raiz de verdade da morte investigada era OUTRA: o bot decidia
            # atacar/defender com o HP LIDO NO INÍCIO da rodada, sem reconferir
            # depois de gastar tempo em tônico/tentativa de alma — corrigido
            # em Brain._act_other/_act_tank (reconfere o HP antes de atacar/
            # defender). Com essa causa raiz já resolvida, tirar o reforço só
            # trouxe uma LENTIDÃO GRANDE sem necessidade (confirmado pelo
            # usuário: rodadas de 55-60s, muito mais lento que antes) — então
            # o reforço volta, mantendo a correção de HP que resolve o motivo
            # de verdade da morte.
            _t1 = time.time()
            _deadline = _t1 + config.ROUND_TIMEOUT_CACA
            _texto_antes = s.text
            _linha_vista = None
            _mudou = False
            _tentativas_retry = 0
            _ultima_tentativa = _t1
            while time.time() < _deadline:
                await s.refresh()
                _estado_confirm, _linha_vista = my_turn_state_caca_debug(s.text, s.char)
                if is_combat_screen(s.message) and _estado_confirm != "waiting":
                    # MINHA ampulheta sumiu — confirmado direto (mais rápido),
                    # mesmo que mais nada na tela tenha mudado (cobre ações
                    # "silenciosas" tipo Tônico, que não deixam rastro no
                    # texto/eventos mas ainda assim limpam a ampulheta). O
                    # 'is_combat_screen' evita o falso-positivo de um clique
                    # que falhou (ex: 'Encrypted data invalid') deixar a conta
                    # numa tela velha (lobby/erro) sendo confundida com "ação
                    # confirmada" — ver comentário igual no combat_loop.
                    _mudou = True
                    break
                if s.text != _texto_antes:
                    # A TELA MUDOU (novo evento, HP diferente, nova rodada com
                    # ampulheta RESETADA, etc.) mesmo que a MINHA linha ainda
                    # mostre ampulheta — pode ser uma rodada NOVA já pedindo
                    # ação de novo, não a mesma ação antiga ainda pendente.
                    # Solta o loop principal pra reavaliar do zero (e agir de
                    # novo se for o caso), em vez de ficar preso esperando.
                    _mudou = True
                    break
                if (time.time() - _ultima_tentativa >= config.RETRY_ACAO_APOS_CACA
                        and _tentativas_retry < config.MAX_TENTATIVAS_ACAO):
                    # Nada mudou em RETRY_ACAO_APOS_CACA segundos — o clique da
                    # ação pode ter se PERDIDO (falha silenciosa do Telegram).
                    # Em vez de só ficar esperando, tenta agir de novo — até
                    # MAX_TENTATIVAS_ACAO vezes (depois disso, só espera o
                    # resto do prazo normalmente, sem clicar mais — evita
                    # ficar reforçando pra sempre numa conexão muito lenta).
                    _tentativas_retry += 1
                    log(s.name, f"🔁 sem nenhuma mudança em {config.RETRY_ACAO_APOS_CACA:.0f}s — "
                                f"o clique pode ter falhado, tentando agir de novo "
                                f"(tentativa {_tentativas_retry}/{config.MAX_TENTATIVAS_ACAO}).")
                    await brain.act(rounds_almas)
                    _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
                    if _motivo_pocao:
                        return _motivo_pocao
                    _texto_antes = s.text
                    _ultima_tentativa = time.time()
                    continue
                await poll_sleep()
            if not _mudou:
                # esgotou o tempo (ROUND_TIMEOUT_CACA) SEM a rodada resolver —
                # loga a TELA INTEIRA (não só a linha) em ascii() (revela
                # qualquer caractere, mesmo os que o terminal não desenha,
                # tipo ⏳ sem fonte) — pra investigar se é algo diferente do
                # cronômetro normal de 45s da rodada.
                log(s.name, f"🔍 DEBUG: {config.ROUND_TIMEOUT_CACA:.0f}s e ainda via 'waiting'. "
                            f"Linha considerada minha: {ascii(_linha_vista)}\n"
                            f"🔍 DEBUG: tela inteira: {ascii(s.text)}")
            _t_confirm = time.time() - _t1                         # tempo até resolver a rodada
            log(s.name, f"⏱️ esperei {_espera:.1f}s a vez | agi em {_t_acao:.1f}s | "
                        f"rodada resolveu em {_t_confirm:.1f}s")
            s._t_fim_ciclo = time.time()
            continue
        if turno == "unknown":
            sem_linha += 1
            if sem_linha >= 10:
                log(s.name, f"⚠️ não achei minha linha ('{s.char}') na caçada — agindo por segurança.")
                sem_linha = 0
                rounds += 1
                rounds_almas += 1
                await brain.act(rounds_almas)
                _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
                if _motivo_pocao:
                    return _motivo_pocao
                continue

        await poll_sleep()


async def run_caca_dupla(sessions, baseline=0, continuar=False, grupo_idx=1, retomar=False):
    """Roda a Caçada em Dupla continuamente com EXATAMENTE 2 contas. 'baseline'
    é o total de caçadas do relatório no início desta execução (pra contar o
    limite a partir de agora, mantendo a conta ao reiniciar). 'continuar' =
    esta execução é a RETOMADA de um reinício automático (não repete o pop-up
    de poção). 'retomar' = a dupla JÁ ESTÁ numa caçada ATIVA (usuário parou o
    bot manualmente no meio e clicou Iniciar de novo, ou o PC reiniciou) ->
    pula a formação de sala e vai direto pro combate, igual à Cripta.
    'grupo_idx' identifica QUAL dupla é esta (1, 2, ...) — quando há
    mais de uma dupla rodando ao mesmo tempo, cada uma tem sua própria sala/
    combate e seu próprio limite de "max_cacadas" (contado à parte, mesmo que
    as outras duplas estejam rodando em paralelo). Retorna True se precisa
    REINICIAR o bot (erro), False se parou de propósito (limite/morte/poção/
    energia)."""
    tag = f"bot-dupla{grupo_idx}"
    if len(sessions) != 2:
        log(tag, f"❌ Caçada em Dupla {grupo_idx} precisa de exatamente 2 contas "
                 f"preenchidas (tem {len(sessions)}).")
        return False
    host, joiner = sessions
    cfg = config.CACA_DUPLA
    andar_maximo = int(cfg.get("andar_maximo", 49))
    energia_minima = int(cfg.get("energia_minima", 10))
    pocoes_reforco = int(cfg.get("pocoes_reforco", 2))
    max_cacadas = int(cfg.get("max_cacadas", 0))
    pocao_minima = int(cfg.get("pocao_vida_minima", 10))
    pocao_aviso = int(cfg.get("pocao_vida_aviso", 100))
    vida_min_pct = int(cfg.get("vida_min_pct", 40))
    reforco_pct = int(cfg.get("reforco_pct", 0))
    alma_min_andar = int(cfg.get("alma_min_andar", 0))
    # 'baseline' aqui é QUANTAS CAÇADAS ESTA DUPLA já tinha feito antes desta
    # execução (persistido em arquivo próprio por grupo — ver main()). Serve
    # de ponto de partida do contador local, que segue valendo após um
    # reinício automático (não reseta o limite por causa de um erro).
    feitas_local = baseline
    log(tag, f"🏔️ Caçada em Dupla {grupo_idx}: {host.name} + {joiner.name} "
             f"(andar máx {andar_maximo}, energia mín {energia_minima}, "
             f"limite {max_cacadas or 'sem limite'})"
             + (" — continuando após reinício." if continuar else "."))
    for s in sessions:
        s.pocao_minima_caca = pocao_minima   # ativa a checagem no ato de curar
        s.modo_caca = True                   # cura só por HP baixo (sem tank aqui)
        # HP% poção e HP% alma-tank são POR CONTA (settings.json ->
        # CACA_DUPLA.grupos[g][i].caca_vida_pct/tank_alma_pct, definidos no
        # painel por personagem); se a conta não tiver o próprio, cai pro
        # valor "padrão" (vida_min_pct) da aba — mantém compatível com saves
        # antigos, que só tinham o valor único.
        vida_pct_conta = s.acc.get("caca_vida_pct")
        vida_pct_conta = vida_min_pct if vida_pct_conta is None else int(vida_pct_conta)
        s.caca_vida_ratio = max(0, min(100, vida_pct_conta)) / 100.0
        s.caca_reforco_ratio = max(0, min(100, reforco_pct)) / 100.0
        alma_pct_conta = int(s.acc.get("tank_alma_pct", 60) or 0)
        s.tank_alma_ratio = max(0, min(100, alma_pct_conta)) / 100.0
        s.alma_min_andar = alma_min_andar   # só usa alma a partir deste andar (0=sempre)

    # Verificação de poção ANTES de iniciar — SÓ num início de verdade (não em
    # reinício automático nem numa RETOMADA manual, pra não travar/atrapalhar):
    # conta as Poções de Vida; se < aviso (padrão 100), pop-up pedindo
    # reabastecer e para.
    # EM PARALELO (as 2 contas leem o Inventário ao mesmo tempo) — antes era
    # uma de cada vez e isso sozinho já levava ~15s.
    # BUG REAL corrigido 2026-07-16 (usuário: retomada detectada certinho,
    # mas as 4 contas saíram do combate mesmo assim e criaram sala nova): essa
    # checagem só pulava com 'continuar' (reinício automático) — não com
    # 'retomar' (retomada manual, pedida pelo usuário) — e contar_pocoes_vida
    # chama back_to_menu() por dentro, que sem 'Menu'/'Viajar' disponíveis
    # durante o combate cai num /start de última instância, tirando a conta
    # da tela de combate ANTES do combat_loop_caca sequer começar.
    if not continuar and not retomar:
        async def _checar_aviso(s):
            qtd = await contar_pocoes_vida(s)
            if qtd is not None:
                s.pocoes_estimadas = qtd
            log(s.name, f"🧪 Poções de Vida no estoque: {qtd if qtd is not None else 'não confirmado'}.")
            return s, qtd
        resultados = await asyncio.gather(*(_checar_aviso(s) for s in sessions))
        for s, qtd in resultados:
            # None = não conseguiu ler — não pausa/avisa por engano (bug real
            # corrigido 2026-07-03), só segue (vai tentar de novo, se precisar,
            # na próxima checagem em combate).
            if qtd is not None and qtd < pocao_aviso:
                await asyncio.to_thread(
                    popup_aviso, "TofuBot — Caçada em Dupla",
                    f"Poção de Vida inferior a {pocao_aviso}!\n\n"
                    f"Conta {s.name}: {qtd} poções.\n\nFavor reabastecer.")
                log(s.name, f"⏹ pausado antes de iniciar: {qtd} Poções de Vida "
                            f"(< {pocao_aviso}). Reabasteça e clique Iniciar de novo.")
                registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd} (< {pocao_aviso})")
                return False

    esta_volta_retoma = retomar
    while True:
        try:
            estado_dupla = {"sair": None}    # compartilhado: morte de um -> outro sai
            for s in sessions:
                s.sair_caca_pocao = False    # zera a cada nova caçada

            if esta_volta_retoma:
                esta_volta_retoma = False
                log(tag, "▶️ retomando a Caçada em Dupla ATIVA (sem formar sala nova).")
                for s in sessions:
                    s._retomando_conteudo = True   # força resync de almas na 1ª ação
            else:
                # limpa as telas velhas de combate da caçada anterior (mesmo motivo
                # da masmorra: refresh não pega tela antiga -> sem 'Encrypted data
                # invalid' nem entrar em sala velha).
                await asyncio.gather(*(limpar_historico(s) for s in sessions))

                # REFORÇO ANTES de entrar na caçada (a conta precisa de energia pra
                # entrar — senão dá "precisa de 10 de energia"). Se a energia estiver
                # abaixo da mínima, bebe as Poções de Energia (nº configurado); e faz
                # o reforço de HP se estiver abaixo do HP% reforço. Se faltar Poção de
                # Energia pra repor, pausa (não fica preso sem conseguir entrar).
                # EM PARALELO pras 2 contas — sequencial aqui era a maior fatia do
                # delay que o usuário reportou (~60s só nessa etapa, uma conta
                # esperando a outra acabar de navegar o Inventário à toa).
                await asyncio.gather(
                    heal_at_menu_if_low(host, host.caca_reforco_ratio),
                    heal_at_menu_if_low(joiner, joiner.caca_reforco_ratio),
                )
                ok_host, ok_joiner = await asyncio.gather(
                    energia_reforco_se_baixo(host, energia_minima, pocoes_reforco),
                    energia_reforco_se_baixo(joiner, energia_minima, pocoes_reforco),
                )
                if not (ok_host and ok_joiner):
                    log(tag, "⏹ pausando esta Caçada em Dupla (acabaram as Poções de Energia). "
                             "Compre/produza mais e clique Iniciar de novo.")
                    registrar_pausa("pocao_energia_indisponivel",
                                    f"{host.name if not ok_host else joiner.name}")
                    return False

                code = await host_criar_cacada(host)
                if not code:
                    log(host.name, "❌ não criei a caçada — tentando de novo em instantes.")
                    await asyncio.sleep(3.0)
                    continue
                if not await joiner_entrar_cacada(joiner, code):
                    log(joiner.name, "❌ não entrei na caçada — tentando de novo em instantes.")
                    await asyncio.sleep(3.0)
                    continue
                if not await host_iniciar_cacada(host):
                    log(host.name, "⚠️ não consegui iniciar a caçada — tentando de novo.")
                    continue
                if not await wait_combat_started(host):
                    log(host.name, "⚠️ combate da caçada não começou a tempo — tentando de novo.")
                    continue

            log(tag, "⚔️ jogando esta Caçada em Dupla.")
            _t_inicio_caca = time.time()
            for _s in sessions:
                _s._t_inicio_conteudo = _t_inicio_caca
            rec = {"recompensas_vistas": set(), "acumulado": {"xp_total": 0, "jogadores": {}}}
            motivos = await asyncio.gather(
                combat_loop_caca(host, andar_maximo, rec, estado_dupla),
                combat_loop_caca(joiner, andar_maximo, rec, estado_dupla),
            )

            def _registrar_cacada_desta_execucao():
                """Registra o progresso desta execução da Caçada em Dupla —
                chamado não só quando bate o andar-limite, mas TAMBÉM quando
                termina por morte ou poção baixa (mesmo bug e mesma correção
                do relatório da Cripta, 2026-07-16: só registrava no
                andar-limite, deixando de fora a maioria das execuções reais,
                que terminam por um desses dois motivos)."""
                resumo_por_conta = rec.get("resumo_por_conta") or {}
                if resumo_por_conta:
                    xp_total = max((r["xp_total"] for r in resumo_por_conta.values()), default=0)
                    jogadores = {nome: {"gold": r["gold_total"], "drops": r["drops"]}
                                 for nome, r in resumo_por_conta.items()}
                    acumulado_final = {"xp_total": xp_total, "jogadores": jogadores}
                    raridades_final = {}
                    for r in resumo_por_conta.values():
                        raridades_final.update(r.get("raridades") or {})
                else:
                    acumulado_final = rec["acumulado"]
                    raridades_final = {}
                try:
                    duracao_segundos = time.time() - _t_inicio_caca
                    andar_final = rec.get("andar_final", andar_maximo)
                    acumulado_final_mapeado = dict(acumulado_final or {})
                    acumulado_final_mapeado["jogadores"] = _mapear_nomes_para_conta(
                        (acumulado_final or {}).get("jogadores"), sessions)
                    total, media_seg = registrar_cacada(acumulado_final_mapeado, grupo_idx=grupo_idx,
                                             duracao_segundos=duracao_segundos,
                                             andar_final=andar_final,
                                             raridades=raridades_final)
                except Exception as e:
                    log(tag, f"(não consegui registrar a caçada: {e!r})")
                    return
                nonlocal feitas_local
                feitas_local += 1
                _salvar_progresso_dupla(grupo_idx, feitas_local)
                if media_seg:
                    _salvar_estimativa("caca_dupla", "caca_dupla", feitas_local, max_cacadas, media_seg)
                log(tag, f"🏁 caçada #{total} registrada "
                         f"({feitas_local} desta dupla desde que iniciou).")

            # MORTE: alguém morreu -> as 2 já saíram (estado compartilhado). Para.
            if "morte" in motivos:
                log(tag, "⏹ pausando esta Caçada em Dupla (alguém morreu — a dupla "
                         "saiu junto). Clique Iniciar de novo quando quiser.")
                _registrar_cacada_desta_execucao()
                registrar_pausa("morte", f"detectado na Caçada em Dupla {grupo_idx}")
                return False
            if "pocao_baixa" in motivos:
                log(tag, "⏹ pausando esta Caçada em Dupla (Poções de Vida baixas). "
                         "Reponha o estoque e clique Iniciar de novo.")
                _registrar_cacada_desta_execucao()
                registrar_pausa("pocao_vida_baixa", "acabando durante a caçada")
                await asyncio.to_thread(
                    popup_aviso, "TofuBot — Caçada em Dupla",
                    f"As Poções de Vida acabaram (ou ficaram abaixo do mínimo "
                    f"configurado) DURANTE a caçada!\n\n"
                    f"A dupla já saiu da sala. Reabasteça e clique Iniciar de novo.")
                return False

            # caçada concluída (chegou no andar máximo): registra e conta.
            # BLINDADO: se o registro falhar, não derruba o loop — segue contando.
            if "andar_limite" in motivos:
                _registrar_cacada_desta_execucao()
            # (o reforço de HP/energia da PRÓXIMA caçada é feito no início da
            # próxima volta do loop — antes de entrar.)

            # LIMITE de caçadas: contador LOCAL desta dupla (não soma com as
            # outras duplas rodando em paralelo) — segue valendo após reinício
            # automático (feitas_local começa de 'baseline', lido do arquivo de
            # progresso desta dupla). Nunca excede o número informado.
            if max_cacadas and feitas_local >= max_cacadas:
                log(tag, f"🎯 esta dupla atingiu o limite de {max_cacadas} "
                         f"caçada(s) desde o início — parando.")
                registrar_pausa("limite_cacadas", f"dupla{grupo_idx}: {feitas_local}/{max_cacadas}")
                return False
            # PARADA SUAVE ("⏸ Parar no fim"): só CHECA aqui, não limpa o flag —
            # com 2 duplas rodando em paralelo, se cada uma limpasse ao terminar,
            # a 1ª a acabar apagaria o pedido antes da 2ª notar. Quem limpa de
            # vez é o main(), uma única vez, no próximo "Iniciar".
            if parar_no_fim_pedido():
                log(tag, "⏸ 'Parar no fim' atendido — esta dupla concluiu a "
                         "caçada atual, parando.")
                registrar_pausa("parar_no_fim", f"dupla{grupo_idx}: após concluir a caçada atual")
                return False

            s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
            if s._contador_perfil % 3 == 0:
                await atualizar_perfil_e_estimativa(s)
            await talvez_vender_no_mercado(s)
            await talvez_ler_inventario(s)

            # Manutenção agendada chegando perto: se o tempo até ela começar
            # for menor que a média de duração desta caçada, não forma uma
            # nova agora — espera a janela passar (auto, sem reiniciar).
            await evitar_novo_conteudo_por_manutencao("caca_dupla", rotulo="caçada em dupla")
        except Exception as e:
            log(tag, f"💥 erro na Caçada em Dupla {grupo_idx}: {e!r} — REINICIANDO pra continuar de onde parou.")
            log(tag, "🔎 detalhe do erro:\n" + traceback.format_exc())
            return True   # main() -> exit 42 -> iniciar.cmd relança e retoma


async def run_templo_oasis_dupla(sessions, baseline=0, continuar=False, grupo_idx=1, retomar=False):
    """Roda o Templo do Oásis (Duo) continuamente com EXATAMENTE 2 contas.
    MESMA sala/combate da Masmorra normal (Criar Sala com senha, Pronto,
    Iniciar, Atacar/Defender/Consumíveis/Almas — reaproveita host_criar_templo/
    joiner_entrar_templo/combat_loop), só que dentro da Fenda Solar (mapa do
    Oásis) e travado em 2 contas. 'baseline' é quantas execuções ESTA dupla já
    tinha concluído antes desta execução (persistido por dupla, sobrevive a
    reinício automático). 'grupo_idx' identifica qual dupla é esta, quando há
    mais de uma rodando ao mesmo tempo (cada uma com sua sala e seu próprio
    limite). 'retomar' = a dupla JÁ ESTÁ num Templo ATIVO (bot parado
    manualmente no meio e iniciado de novo, ou PC reiniciou) -> pula a
    formação de sala e vai direto pro combate, igual à Cripta/Caçada Dupla.
    Retorna True se precisa REINICIAR o bot (erro), False se parou de
    propósito (limite/morte/poção)."""
    tag = f"bot-templo{grupo_idx}"
    if len(sessions) != 2:
        log(tag, f"❌ Templo do Oásis (dupla {grupo_idx}) precisa de exatamente "
                 f"2 contas preenchidas (tem {len(sessions)}).")
        return False
    host, joiner = sessions
    cfg = getattr(config, "TEMPLO_OASIS", {}) or {}
    max_execucoes = int(cfg.get("max_execucoes", 0))
    pocao_minima = int(cfg.get("pocao_vida_minima", POCAO_VIDA_MINIMA))
    pocao_aviso = int(cfg.get("pocao_vida_aviso", 100))
    vida_min_pct = int(cfg.get("vida_min_pct", 40))
    feitas_local = baseline
    for s in sessions:
        # ativa a saída proativa por poção baixa, mesmo mecanismo da Masmorra/
        # Caçada em Dupla (ver act_potion): se cair abaixo disso ao curar, sai.
        s.pocao_minima_caca = pocao_minima
        # HP% pra beber poção é POR CONTA (settings.json -> grupo[i].caca_vida_pct,
        # definido no painel na aba Templo do Oásis) — cai pro padrão da aba se a
        # conta não tiver o próprio. Vale pra TODAS as contas, INCLUSIVE o tank
        # (sem 'tank_ativo': o tank cai no mesmo _act_other dos outros papéis —
        # continua defendendo/segurando aggro, mas pelo %HP configurado aqui, não
        # pelo limite global da Masmorra).
        vida_pct_conta = s.acc.get("caca_vida_pct")
        vida_pct_conta = vida_min_pct if vida_pct_conta is None else int(vida_pct_conta)
        s.caca_vida_ratio = max(0, min(100, vida_pct_conta)) / 100.0
        s.modo_caca = True
    log(tag, f"🏛️ Templo do Oásis (dupla {grupo_idx}): {host.name} + {joiner.name} "
             f"(limite {max_execucoes or 'sem limite'})"
             + (" — continuando após reinício." if continuar else "."))

    # Verificação de poção ANTES de iniciar — só num início de verdade (não em
    # reinício automático nem numa retomada manual), mesmo padrão da Caçada em
    # Dupla (mesmo bug real corrigido lá: contar_pocoes_vida chama
    # back_to_menu() por dentro, o que tira a conta do combate se já estiver
    # ativo).
    if not continuar and not retomar:
        async def _checar_aviso(s):
            qtd = await contar_pocoes_vida(s)
            log(s.name, f"🧪 Poções de Vida no estoque: {qtd if qtd is not None else 'não confirmado'}.")
            return s, qtd
        resultados = await asyncio.gather(*(_checar_aviso(s) for s in sessions))
        for s, qtd in resultados:
            if qtd is not None and qtd < pocao_aviso:
                await asyncio.to_thread(
                    popup_aviso, "TofuBot — Templo do Oásis",
                    f"Poção de Vida inferior a {pocao_aviso}!\n\n"
                    f"Conta {s.name}: {qtd} poções.\n\nFavor reabastecer.")
                log(s.name, f"⏹ pausado antes de iniciar: {qtd} Poções de Vida "
                            f"(< {pocao_aviso}). Reabasteça e clique Iniciar de novo.")
                registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd} (< {pocao_aviso})")
                return False

    esta_volta_retoma = retomar
    while True:
        try:
            shared = {
                "leave_event": asyncio.Event(),
                "restart": asyncio.Event(),
                "stop": asyncio.Event(),
                "code": None,
                "recompensas_vistas": set(),
                "acumulado": {"xp_total": 0, "jogadores": {}},
                "em_combate": {},
                "roles": {s.name: s.role for s in sessions},
                "recorder": next((s.name for s in sessions if s.role == "tank"), sessions[0].name),
            }

            if esta_volta_retoma:
                esta_volta_retoma = False
                log(tag, "▶️ retomando o Templo do Oásis ATIVO (sem formar sala nova).")
                for s in sessions:
                    s._retomando_conteudo = True   # força resync de almas na 1ª ação
            else:
                # limpa telas velhas de combate da execução anterior (mesmo motivo
                # da masmorra/caçada: refresh não confundir com sala/combate velhos).
                await asyncio.gather(*(limpar_historico(s) for s in sessions))
                # cura quem ficou baixo antes de formar sala nova
                await asyncio.gather(*(heal_at_menu_if_low(s) for s in sessions))

                host_char = await host_criar_templo(host)
                if not host_char:
                    log(host.name, "❌ não criei a sala do Templo do Oásis — tentando de novo em instantes.")
                    await asyncio.sleep(3.0)
                    continue
                shared["code"] = host_char
                await ready_up(host)
                if not await joiner_entrar_templo(joiner, host_char):
                    log(joiner.name, "❌ não entrei na sala do Templo do Oásis — tentando de novo em instantes.")
                    await asyncio.sleep(3.0)
                    continue

                # Sala SEM senha (igual a Cripta — confirmado no host_criar_templo:
                # "aqui NÃO existe etapa de senha"), então qualquer um pode achar
                # essa sala em 'Buscar Salas'. Mesma proteção que a Cripta já tinha
                # (pedido do usuário 2026-07-15: "faça igual tem na cripta"): se um
                # INTRUSO (personagem que não é nosso) entrar, sai todo mundo e
                # recria a sala do zero.
                nomes_nossos = [s.char for s in sessions]
                await host.refresh()
                if intruso_na_sala(host.text, nomes_nossos):
                    log(host.name, "🚫 intruso na sala do Templo do Oásis — saindo todos e recriando.")
                    await asyncio.gather(*(leave_room(s) for s in sessions))
                    await asyncio.sleep(2.0)
                    continue

                await ready_up(joiner)
                await host.refresh()
                if intruso_na_sala(host.text, nomes_nossos):
                    log(host.name, "🚫 intruso entrou antes de iniciar — saindo todos e recriando.")
                    await asyncio.gather(*(leave_room(s) for s in sessions))
                    await asyncio.sleep(2.0)
                    continue

                if not await host_start(host, 2):
                    log(host.name, "⚠️ não consegui iniciar o Templo do Oásis — tentando de novo.")
                    continue
                if not await wait_combat_started(host):
                    log(host.name, "⚠️ combate do Templo do Oásis não começou a tempo — tentando de novo.")
                    continue

            log(tag, "⚔️ jogando o Templo do Oásis (Duo).")
            _t_inicio_templo = time.time()
            for _s in sessions:
                _s._t_inicio_conteudo = _t_inicio_templo
            await asyncio.gather(*(
                combat_loop(s, shared["leave_event"], shared["restart"], shared,
                            marcadores_fim=("vitoria",))
                for s in sessions
            ))

            if shared["restart"].is_set():
                log(tag, "🔁 solicitado reinício automático do bot — reiniciando.")
                return True

            texto_final = shared.get("conclusao", {}).get(shared["recorder"])
            if not (texto_final and "vitoria" in norm(texto_final)):
                # tenta com a OUTRA conta antes de desistir (o 'recorder' pode
                # ter saído antes de capturar a tela final).
                for s in sessions:
                    alt = shared.get("conclusao", {}).get(s.name)
                    if alt and "vitoria" in norm(alt):
                        texto_final = alt
                        break
            if texto_final and "vitoria" in norm(texto_final):
                atualizar_recompensas(shared, texto_final)
                dano = parse_ranking_dano(texto_final)
                dano_mapeado = _mapear_nomes_para_conta(dano, sessions)
                # Prioriza a tela final '🌞 Templo do Oásis — Vitória!' (via
                # parse_loot_final_templo — confiável, mostra TODO mundo)
                # sobre os blocos transitórios 'Recompensas (vs Mob)'
                # capturados durante o combate (shared['acumulado']), que
                # podem se perder — mesma lógica já usada na Masmorra normal.
                loot_final = parse_loot_final_templo(texto_final)
                if loot_final:
                    acumulado_templo = {"xp_total": loot_final["xp_total"],
                                         "jogadores": loot_final["jogadores"]}
                    raridades_final = loot_final["raridades"]
                else:
                    acumulado_templo = dict(shared.get("acumulado") or {})
                    raridades_final = None
                acumulado_templo["jogadores"] = _mapear_nomes_para_conta(
                    (acumulado_templo or {}).get("jogadores"), sessions)
                total, media_seg = registrar_templo_oasis(
                    texto_final, dano_mapeado, acumulado_templo, grupo_idx=grupo_idx,
                    duracao_segundos=time.time() - _t_inicio_templo, raridades=raridades_final)
                feitas_local += 1
                _salvar_progresso_dupla_templo(grupo_idx, feitas_local)
                if media_seg:
                    _salvar_estimativa("templo_oasis", "templo_oasis", feitas_local,
                                       max_execucoes, media_seg)
                log(tag, f"🏁 Templo do Oásis #{total} concluído "
                         f"({feitas_local} desta dupla desde que iniciou).")
            else:
                log(tag, "⚠️ saí do combate sem confirmar a tela de Vitória — "
                         "não registrado (dupla morreu/saiu antes do fim?).")

            if shared["stop"].is_set():
                log(tag, "⏹ parando (Poção de Vida baixa detectada durante o Templo).")
                return False

            # cura quem ficou baixo, antes de recomeçar
            await asyncio.gather(*(heal_at_menu_if_low(s) for s in sessions))

            if max_execucoes and feitas_local >= max_execucoes:
                log(tag, f"🎯 esta dupla atingiu o limite de {max_execucoes} "
                         f"execuç(ões) do Templo do Oásis desde o início — parando.")
                registrar_pausa("limite_templo_oasis", f"dupla{grupo_idx}: {feitas_local}/{max_execucoes}")
                return False
            if parar_no_fim_pedido():
                log(tag, "⏸ 'Parar no fim' atendido — esta dupla concluiu o Templo do Oásis "
                         "atual, parando.")
                registrar_pausa("parar_no_fim", f"dupla{grupo_idx}: após concluir o Templo do Oásis atual")
                return False

            async def _talvez_atualizar_perfil(s):
                s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
                if s._contador_perfil % 3 == 0:
                    await atualizar_perfil_e_estimativa(s)
                await talvez_vender_no_mercado(s)
                await talvez_ler_inventario(s)
            await asyncio.gather(*(_talvez_atualizar_perfil(s) for s in sessions))

            await evitar_novo_conteudo_por_manutencao("templo_oasis", rotulo="Templo do Oásis")
        except Exception as e:
            log(tag, f"💥 erro no Templo do Oásis (dupla {grupo_idx}): {e!r} — REINICIANDO pra continuar de onde parou.")
            log(tag, "🔎 detalhe do erro:\n" + traceback.format_exc())
            return True   # main() -> exit 42 -> iniciar.cmd relança e retoma


def _cripta_btn_subs(nivel: str):
    """Substring EXATA do botão da Cripta escolhida ('cripta i (' NÃO casa com
    'cripta ii ('/'cripta iii (', evitando pegar o nível errado)."""
    n = {"I": "i", "II": "ii", "III": "iii"}.get((nivel or "I").upper(), "i")
    return (f"cripta {n} (",)


async def open_cripta(s: Session):
    """Chega na tela 'CRIPTA DO CEMITÉRIO' (com os botões Cripta I/II/III).
    Caminho: Menu -> Masmorra -> 'Cripta do Cemitério'. (A conta já foi levada
    ao mapa Cemitério Antigo antes, em main())."""
    for _ in range(8):
        await s.refresh()
        if find_button(s.message, "cripta i (", "cripta ii (", "cripta iii ("):
            return True
        if is_combat_screen(s.message) or find_button(s.message, "sair"):
            await leave_room(s)
            continue
        cdc = find_button(s.message, "cripta do cemiterio", "cripta do cemitério")
        if cdc:
            await s.click(cdc, label="Cripta do Cemitério")
            continue
        mm = find_button(s.message, "masmorra")
        if mm:
            await s.click(mm, label="Masmorra")
            continue
        mb = find_button(s.message, "menu")
        if mb:
            await s.click(mb, label="Menu")
            continue
        if await _tentar_evitar_start(s):
            continue
        await s.send_start()
    return find_button(s.message, "cripta i (", "cripta ii (", "cripta iii (") is not None


async def host_criar_cripta(s: Session, nivel: str):
    """HOST: abre a Cripta escolhida (I/II/III) — cria a sala SEM senha — e
    devolve o código lido do lobby ('🦴 Cripta I C6BF6C')."""
    if not await open_cripta(s):
        log(s.name, "❌ não cheguei na tela da Cripta (host).")
        return None
    if not await s.click_text(*_cripta_btn_subs(nivel), label=f"Cripta {nivel}"):
        return None
    await s.refresh()
    code = find_cripta_code(s.text)
    if code:
        log(s.name, f"✅ Cripta {nivel} criada. Código: {code}")
    else:
        log(s.name, f"⚠️ criei a Cripta mas não achei o código.\n    texto: {s.text}")
    return code


async def joiner_entrar_cripta(s: Session, code: str):
    """CONTA COMUM: entra na sala da Cripta pela lista de 'Buscar Salas' + código
    (a Cripta NÃO tem senha)."""
    if not await open_cripta(s):
        log(s.name, "❌ não cheguei na tela da Cripta (join).")
        return False
    if not await s.click_text("buscar salas", label="Buscar Salas"):
        return False
    for _ in range(10):
        alvo = find_button(s.message, code)
        if alvo:
            await s.click(alvo, label=f"sala {code}")
            log(s.name, "✅ entrei na Cripta.")
            return True
        prox = find_button(s.message, "proximo", "próximo")
        if prox:
            await s.click(prox, label="Próximo")
        else:
            log(s.name, f"❌ não achei a sala {code} na lista.\n"
                        f"    botões: {button_texts(s.message)}")
            return False
    return False


async def host_iniciar_cripta(s: Session) -> bool:
    """HOST: com todos prontos, clica '🚀 Iniciar' pra começar a Cripta."""
    for _ in range(int(config.LOBBY_TIMEOUT / config.POLL_INTERVAL)):
        await s.refresh()
        if is_combat_screen(s.message):
            return True
        b = find_button(s.message, "iniciar")
        if b:
            await s.click(b, label="Iniciar Cripta")
            return True
        await poll_sleep()
    log(s.name, "⚠️ não achei o botão 'Iniciar' no lobby da Cripta.")
    return False


async def combat_loop_cripta(s: Session, andar_maximo: int, estado: dict,
                              nomes_grupo: list, tank_por_ultimo: bool = True):
    """Combate da CRIPTA: parecido com combat_loop_caca, mas pra N contas (2 a
    5) — função SEPARADA, não mexe em combat_loop_caca (Caçada em Dupla
    intocada de propósito). A Cripta usa o layout da MASMORRA (linha 'Nv.' +
    ampulheta ⏳), então o turno é lido com my_turn_state (não a versão da
    caçada). 'estado' é um dict COMPARTILHADO entre todas as contas do grupo
    (morte/travamento faz todo mundo sair junto). 'tank_por_ultimo': o TANK
    espera os outros saírem primeiro (defendendo, aguenta dano) e só sai por
    último, pra ninguém ficar sozinho tomando dano à toa.
    Retorna o MOTIVO da saída: "andar_limite" | "morte" | "pocao_baixa" |
    "travou" | "tela" | "rodadas"."""
    brain = Brain(s)
    # RETOMADA (pedido do usuário 2026-07-16): ver comentário igual no
    # combat_loop (Masmorra) sobre por que força o resync aqui.
    if getattr(s, "_retomando_conteudo", False):
        s._retomando_conteudo = False
        brain.rodadas_desde_resync_alma = RESYNC_ALMA_RODADAS
    rounds = 0            # limite de SEGURANÇA (MAX_ROUNDS) — reseta a cada andar, de proposito
    rounds_almas = 0       # BUG REAL corrigido (2026-07-12, mesmo bug do combat_loop_caca):
    # passado pro Brain.act() (recarga de almas) - esse NUNCA reseta. A
    # Cripta e INFINITA (troca de andar o tempo todo), entao esse bug tinha
    # ainda mais impacto aqui do que na Cacada em Dupla.
    sem_linha = 0
    _ultima_limpeza_rounds = 0   # limpeza periódica do histórico durante a luta

    async def _capturar_progresso_acumulado():
        """BUG REAL corrigido 2026-07-16 (usuário: XP/Gold zerados numa
        Cripta que levou 31min e chegou no andar 33): o código sempre dava
        um s.refresh() ANTES de checar — mas leave_room() já pode ter
        deixado essa tela certinha em s.text um instante antes, e como ela
        pode ser passageira (o jogo às vezes segue sozinho pra outra tela),
        um refresh novo — pior ainda com a API do Telegram lenta — podia
        chegar tarde demais e perder a janela de captura. Agora CHECA o
        texto atual primeiro, só refresca se ainda não bateu, e avisa no
        log se mesmo assim não conseguir (pra não ficar silencioso)."""
        for i in range(8):
            if "progresso acumulado" in norm(s.text):
                s._saida_txt = s.text
                return
            if i > 0:
                await poll_sleep()
            await s.refresh()
        log(s.name, "⚠️ não vi a tela 'Progresso acumulado' depois de sair — "
                    "XP/Gold desta Cripta podem não entrar no relatório.")

    async def _sair_pocao_agora():
        """Item 2 (2026-07-16): chamado tanto no topo do loop quanto
        IMEDIATAMENTE depois de CADA brain.act() (incluindo os retries) —
        antes, só era checado 1x por volta do loop, então entre um
        brain.act() detectar 'sem poção' e a próxima checagem, o bot podia
        esperar a rodada inteira 'resolver' (até ROUND_TIMEOUT_CACA
        segundos, com retries chamando brain.act() de novo) com o HP
        crítico e sem cura. Retorna 'pocao_baixa' se saiu (o chamador deve
        encerrar a função na hora, devolvendo esse mesmo valor) ou None."""
        if not s.sair_caca_pocao:
            return None
        log(s.name, "🧪 saindo da cripta (Poções de Vida abaixo do limite).")
        if estado is not None:
            estado["sair"] = "pocao"
        await leave_room(s)
        # ao sair, mostra 'Progresso acumulado' (XP/gold) — captura pra
        # registrar (BUG REAL corrigido: antes só capturava isso na saída
        # por 'andar_limite', então qualquer Cripta que terminasse por
        # poção baixa ou morte não aparecia no relatório NENHUM).
        await _capturar_progresso_acumulado()
        return "pocao_baixa"

    while True:
        await s.refresh()
        txt = s.text

        if estado is not None and estado.get("sair"):
            log(s.name, f"🚪 saindo junto com o grupo ({estado['sair']}).")
            await leave_room(s)
            return estado["sair"]

        _motivo_pocao = await _sair_pocao_agora()
        if _motivo_pocao:
            return _motivo_pocao

        andar = parse_andar_cripta(txt)
        s._andar_atual = andar

        # Loot raro visto nos Eventos AO VIVO (ex: "encontrou um Saco das
        # Almas!") — TODAS as contas do grupo veem o MESMO evento na tela, e
        # esse loop roda MUITAS vezes enquanto o evento continua visível, então
        # dedupliqua por (andar + nome do evento + item) no 'estado'
        # COMPARTILHADO — cada drop real só entra 1 vez no relatório. O nome
        # no evento é o PERSONAGEM (ex: "[NEW]Pri"), que pode ser diferente do
        # APELIDO da conta (s.name, usado no XP/gold — ex: "Pri" ou "trol"
        # pro personagem "[NEW]Trrool") — por isso só a conta cujo s.char
        # bate com o nome do evento é quem registra o drop, usando s.name
        # como chave (pra casar certinho com o gold/xp da mesma pessoa no
        # relatório, em vez de virar uma linha separada com o nome errado).
        if estado is not None:
            vistos = estado.setdefault("drops_vistos", set())
            for nome_evento, item in parse_drops_evento_cripta(txt):
                chave = f"{andar}|{nome_evento}|{item}"
                if chave in vistos:
                    continue
                if norm(s.char) in norm(nome_evento):
                    vistos.add(chave)
                    estado.setdefault("drops_por_conta", {}).setdefault(s.name, []).append(item)

        if (andar is None and is_combat_screen(s.message)
                and getattr(s, "_log_andar_uma_vez", False)):
            s._log_andar_uma_vez = False
            log(s.name, "🔎 (não li o número do andar da Cripta — tela p/ ajustar):\n"
                        f"    {txt}")
        if andar is not None and andar >= andar_maximo:
            if estado is not None:
                # guarda o andar final ATINGIDO (não só o configurado) — pro
                # relatório mostrar até onde a Cripta foi de verdade (mesmo
                # padrão já usado na Caçada em Dupla).
                estado["andar_final"] = max(estado.get("andar_final", 0), andar)
            if tank_por_ultimo and s.role == "tank" and estado is not None:
                roles = estado.get("roles") or {}
                nao_tanks = [n for n in roles if roles[n] != "tank" and n != s.name]
                log(s.name, f"🏁 andar {andar} (limite {andar_maximo}) — tank "
                            f"aguardando os outros saírem pra sair por último.")
                for _ in range(80):
                    saiu = estado.get("saiu", set())
                    if all(n in saiu for n in nao_tanks):
                        break
                    await s.refresh()
                    if is_combat_screen(s.message) and my_turn_state(s.text, s.char) == "waiting":
                        await act_defender(s)
                    else:
                        await poll_sleep()
                log(s.name, "🏁 tank saindo por último.")
                await leave_room(s)
            else:
                if estado is not None:
                    estado.setdefault("saiu", set()).add(s.name)
                log(s.name, f"🏁 cheguei no andar {andar} (limite {andar_maximo}) — saindo.")
                await leave_room(s)
            # ao sair, mostra 'Progresso acumulado' (XP/gold) — captura pra registrar.
            await _capturar_progresso_acumulado()
            return "andar_limite"

        if someone_died(txt, nomes_grupo):
            log(s.name, "💀 morte detectada — saindo. O grupo sai junto.")
            ja_registrada = estado is not None and estado.get("sair") == "morte"
            if estado is not None:
                estado["sair"] = "morte"
            if not ja_registrada:
                try:
                    registrar_morte("cripta")
                except Exception as e:
                    log(s.name, f"(não consegui registrar a morte: {e!r})")
            await leave_room(s)
            # ao sair, mostra 'Progresso acumulado' (XP/gold) — captura pra
            # registrar (ver comentário igual no ramo 'pocao_baixa' acima).
            await _capturar_progresso_acumulado()
            return "morte"

        if estado is not None and rounds >= 3:
            travada = conta_travada_no_combate(estado, s.name)
            if travada:
                nome_t, papel_t = travada
                log(s.name, f"⚠️ '{nome_t}' ({papel_t}) travou na cripta "
                            f"(flood/rede/reinício?) — saindo pra não morrer.")
                estado["sair"] = "travou"
                await leave_room(s)
                return "travou"

        if not is_combat_screen(s.message):
            if find_button(s.message, "proximo", "próximo", "continuar", "avancar", "avançar"):
                log(s.name, "➡️ avançando de andar.")
                await s.click_text("proximo", "próximo", "continuar",
                                   "avancar", "avançar", label="Próximo", required=False)
                rounds = 0   # zera o contador de rodadas por andar (MAX_ROUNDS é por andar)
                continue
            if is_lobby_screen(s.message):
                await poll_sleep()
                continue
            # mesma proteção aplicada na Caçada em Dupla (bug real, morte
            # relatada): notificação de OUTRA sala/masmorra sem relação com o
            # combate atual pode aparecer por um instante — dá mais chances
            # antes de desistir. AMPLIADO 2026-07-16: detecta pelo FORMATO
            # (tela não reconhecida com um único botão 'Menu', tipo aviso de
            # "toque OK pra fechar"), não só pela frase específica — cobre
            # qualquer notificação avulsa parecida (ex: troca com prazo
            # expirado), não só a de sala expirada.
            _botoes_tela = button_texts(s.message)
            eh_notificacao_transitoria = (
                bool(re.search(r"expirou por inatividade", norm(txt)))
                or (len(_botoes_tela) == 1 and find_button(s.message, "menu") is not None))
            tentativas_voltar = 12 if eh_notificacao_transitoria else 6
            voltou = False
            for _ in range(tentativas_voltar):
                # Checagem de HP EM TODA tentativa (BUG REAL corrigido: antes
                # só conferia 1x, no final — se a notificação demorasse a
                # sumir, dava tempo de sobra pra morrer sem socorro nenhum).
                hp_emergencia = player_hp(s.text, s.char)
                if hp_emergencia and hp_emergencia[1]:
                    ratio_emerg = hp_emergencia[0] / hp_emergencia[1]
                    limite_emerg = getattr(s, "caca_vida_ratio", 0.4) or 0.4
                    if ratio_emerg <= limite_emerg:
                        log(s.name, f"🩺 emergência enquanto espera a tela real voltar: "
                                    f"HP em {ratio_emerg:.0%} — bebendo poção por segurança.")
                        await act_potion(s)
                if find_button(s.message, "voltar", "atras", "⬅", "◀", "🔙"):
                    await go_back(s)
                await s.refresh()
                if is_combat_screen(s.message) or find_button(
                        s.message, "proximo", "próximo", "continuar", "avancar", "avançar"):
                    voltou = True
                    break
                await poll_sleep()
            if voltou:
                continue
            # ÚLTIMA checagem de segurança antes de desistir de vez.
            hp_emergencia = player_hp(s.text, s.char)
            if hp_emergencia and hp_emergencia[1]:
                ratio_emerg = hp_emergencia[0] / hp_emergencia[1]
                if ratio_emerg <= 0.4:
                    log(s.name, f"🩺 emergência antes de desistir da tela: HP em "
                                f"{ratio_emerg:.0%} — tentando beber poção por segurança.")
                    await act_potion(s)
            if "conclu" in norm(txt):
                log(s.name, "🏁 cripta concluída — voltando ao menu.")
            else:
                log(s.name, "🏁 saí da tela de combate (cripta). Texto:\n"
                            f"    {txt}\n    botões: {button_texts(s.message)}")
            return "tela"

        turno = my_turn_state(txt, s.char)
        if turno == "waiting":
            sem_linha = 0
            rounds += 1
            rounds_almas += 1
            if rounds > config.MAX_ROUNDS:
                log(s.name, "⚠️ passei do limite de rodadas na cripta. Saindo.")
                await leave_room(s)
                return "rodadas"
            if rounds_almas - _ultima_limpeza_rounds >= 20:
                _ultima_limpeza_rounds = rounds_almas
                await limpar_historico(s)
                # ver comentário igual no combat_loop (Masmorra) sobre o
                # refresh extra necessário aqui — evita clicar num botão de
                # mensagem que a própria limpeza pode ter apagado.
                await s.refresh()
                txt = s.text
            _agora = time.time()
            _espera = _agora - getattr(s, "_t_fim_ciclo", _agora)
            _t0 = time.time()
            await brain.act(rounds_almas)
            _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
            if _motivo_pocao:
                return _motivo_pocao
            _t_acao = time.time() - _t0
            # ESPERA a minha ampulheta sumir (ação registrou/rodada resolveu),
            # REFORÇANDO o clique se nada mudar — mesmo mecanismo já usado na
            # Caçada em Dupla e agora na Masmorra normal/Templo do Oásis
            # (RETRY_ACAO_APOS_CACA/ROUND_TIMEOUT_CACA). Havia um receio antigo
            # de reclicar a MESMA alma que não tinha registrado (bug real já
            # visto aqui), mas a causa raiz de verdade era o HP desatualizado
            # antes de agir — já corrigida em Brain._act_other/_act_tank
            # (reconfere o HP antes de agir), que é compartilhado por todos os
            # modos. Com essa causa raiz resolvida, o reforço é seguro e evita
            # ficar preso esperando um clique que se perdeu.
            _t1 = time.time()
            _deadline = _t1 + config.ROUND_TIMEOUT_CACA
            _texto_antes = s.text
            _mudou = False
            _tentativas_retry = 0
            _ultima_tentativa = _t1
            while time.time() < _deadline:
                await s.refresh()
                if is_combat_screen(s.message) and my_turn_state(s.text, s.char) != "waiting":
                    # ver comentário igual no combat_loop (Masmorra) sobre
                    # por que 'is_combat_screen' é obrigatório aqui.
                    _mudou = True
                    break
                if s.text != _texto_antes:
                    _mudou = True
                    break
                if (time.time() - _ultima_tentativa >= config.RETRY_ACAO_APOS_CACA
                        and _tentativas_retry < config.MAX_TENTATIVAS_ACAO):
                    _tentativas_retry += 1
                    log(s.name, f"🔁 sem nenhuma mudança em {config.RETRY_ACAO_APOS_CACA:.0f}s "
                                f"na cripta — o clique pode ter falhado, tentando agir de novo "
                                f"(tentativa {_tentativas_retry}/{config.MAX_TENTATIVAS_ACAO}).")
                    await brain.act(rounds_almas)
                    _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
                    if _motivo_pocao:
                        return _motivo_pocao
                    _texto_antes = s.text
                    _ultima_tentativa = time.time()
                    continue
                await poll_sleep()
            if not _mudou:
                log(s.name, f"🔍 DEBUG: {config.ROUND_TIMEOUT_CACA:.0f}s e ainda via 'waiting' "
                            f"na cripta. tela inteira: {ascii(s.text)}")
            _t_confirm = time.time() - _t1
            log(s.name, f"⏱️ esperei {_espera:.1f}s a vez | agi em {_t_acao:.1f}s | "
                        f"rodada resolveu em {_t_confirm:.1f}s")
            s._t_fim_ciclo = time.time()
            continue
        if turno == "unknown":
            sem_linha += 1
            if sem_linha >= 10:
                log(s.name, f"⚠️ não achei minha linha ('{s.char}') na cripta — agindo por segurança.")
                sem_linha = 0
                rounds += 1
                rounds_almas += 1
                await brain.act(rounds_almas)
                _motivo_pocao = await _sair_pocao_agora()   # item 2: aborta JÁ
                if _motivo_pocao:
                    return _motivo_pocao
                continue

        await poll_sleep()


async def run_cripta(sessions, baseline=0, continuar=False, retomar=False):
    """Roda a Cripta continuamente com N contas (1 a 5 — confirmado que dá
    pra criar a sala sozinho e clicar Iniciar sem ninguém entrar). Sala SEM
    senha; se um INTRUSO entrar, sai todo mundo e recria. Para no
    'andar_maximo' (o conteúdo é infinito). 'retomar'=True: as contas já
    estão numa Cripta ATIVA (PC reiniciou / parou e iniciou manual) -> pula
    a formação e vai direto ao combate. Retorna True se precisa REINICIAR o
    bot (erro), False se parou de propósito (limite/morte/poção)."""
    if not (1 <= len(sessions) <= 5):
        log("bot", f"❌ A Cripta precisa de 1 a 5 contas (tem {len(sessions)}).")
        return False
    cfg = config.CRIPTA
    nivel = cfg.get("nivel", "I")
    andar_maximo = int(cfg.get("andar_maximo", 10))
    alma_min_andar = int(cfg.get("alma_min_andar", 0))
    max_criptas = int(cfg.get("max_criptas", 0))
    pocao_minima = int(config.POCOES.get("pocao_vida_minima", 10))
    pocao_aviso = int(config.POCOES.get("pocao_vida_aviso", 100))
    vida_min_pct = int(config.POCOES.get("vida_min_pct", 40))
    reforco_pct = int(config.POCOES.get("reforco_pct", 0))
    nomes_nossos = [s.char for s in sessions]
    log("bot", f"🦴 Cripta {nivel}: {len(sessions)} conta(s) "
               f"(andar máx {andar_maximo}, limite {max_criptas or 'sem limite'})"
               + (" — continuando após reinício." if continuar else "."))
    for s in sessions:
        s.pocao_minima_caca = pocao_minima
        s.modo_caca = True
        s.tank_ativo = True
        vida_pct_conta = s.acc.get("vida_min_pct", s.acc.get("caca_vida_pct"))
        vida_pct_conta = vida_min_pct if vida_pct_conta is None else int(vida_pct_conta)
        s.caca_vida_ratio = max(0, min(100, vida_pct_conta)) / 100.0
        s.caca_reforco_ratio = max(0, min(100, reforco_pct)) / 100.0
        s.alma_min_andar = alma_min_andar

    if not continuar and not retomar:
        async def _checar_aviso(s):
            qtd = await contar_pocoes_vida(s)
            if qtd is not None:
                s.pocoes_estimadas = qtd
            log(s.name, f"🧪 Poções de Vida no estoque: {qtd if qtd is not None else 'não confirmado'}.")
            return s, qtd
        for s, qtd in await asyncio.gather(*(_checar_aviso(s) for s in sessions)):
            if qtd is not None and qtd < pocao_aviso:
                await asyncio.to_thread(
                    popup_aviso, "TofuBot — Cripta",
                    f"Poção de Vida inferior a {pocao_aviso}!\n\n"
                    f"Conta {s.name}: {qtd} poções.\n\nFavor reabastecer.")
                log(s.name, f"⏹ pausado antes de iniciar: {qtd} Poções de Vida (< {pocao_aviso}).")
                registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd} (< {pocao_aviso})")
                return False

    host = sessions[0]
    joiners = sessions[1:]
    esta_volta_retoma = retomar
    while True:
        try:
            estado = {"sair": None, "em_combate": {},
                      "roles": {s.name: s.role for s in sessions}}
            for s in sessions:
                s.sair_caca_pocao = False
                s._log_andar_uma_vez = True
                s._log_alma_uma_vez = True   # loga a tela pós-clique da alma 1x se não lançar

            if esta_volta_retoma:
                esta_volta_retoma = False
                log("bot", "▶️ retomando a Cripta ATIVA (sem formar sala nova).")
                for s in sessions:
                    s._retomando_conteudo = True   # força resync de almas na 1ª ação
            else:
                await asyncio.gather(*(limpar_historico(s) for s in sessions))
                await asyncio.gather(*(heal_at_menu_if_low(s, s.caca_reforco_ratio) for s in sessions))

                faltou = []
                for s in sessions:
                    await open_cripta(s)
                    if keys_count_ossos(s.text) <= 0:
                        faltou.append(s.name)
                if faltou:
                    log("bot", f"⏹ sem Chave de Ossos: {', '.join(faltou)} — pausando. "
                               f"Consiga mais e clique Iniciar de novo.")
                    registrar_pausa("pocao_energia_indisponivel", f"sem Chave de Ossos: {', '.join(faltou)}")
                    return False

                code = await host_criar_cripta(host, nivel)
                if not code:
                    log(host.name, "❌ não criei a Cripta — tentando de novo.")
                    await asyncio.sleep(3.0)
                    continue
                oks = await asyncio.gather(*(joiner_entrar_cripta(s, code) for s in joiners))
                if not all(oks):
                    log("bot", "❌ nem todos entraram na Cripta — saindo e recriando.")
                    await asyncio.gather(*(leave_room(s) for s in sessions))
                    await asyncio.sleep(2.0)
                    continue

                await host.refresh()
                if intruso_na_sala(host.text, nomes_nossos):
                    log(host.name, "🚫 intruso na sala da Cripta — saindo todos e recriando.")
                    await asyncio.gather(*(leave_room(s) for s in sessions))
                    await asyncio.sleep(2.0)
                    continue

                await asyncio.gather(*(ready_up(s) for s in sessions))
                await host.refresh()
                if intruso_na_sala(host.text, nomes_nossos):
                    log(host.name, "🚫 intruso entrou antes de iniciar — saindo todos e recriando.")
                    await asyncio.gather(*(leave_room(s) for s in sessions))
                    await asyncio.sleep(2.0)
                    continue

                if not await host_iniciar_cripta(host):
                    log("bot", "⚠️ não consegui iniciar a Cripta — tentando de novo.")
                    continue
                if not await wait_combat_started(host):
                    log("bot", "⚠️ combate da Cripta não começou a tempo — tentando de novo.")
                    continue

            log("bot", "⚔️ jogando a Cripta.")
            _t_inicio_cripta = time.time()
            for s in sessions:
                s._t_inicio_conteudo = _t_inicio_cripta
                s._combat_hb = estado
            try:
                motivos = await asyncio.gather(*(
                    combat_loop_cripta(s, andar_maximo, estado, nomes_nossos, tank_por_ultimo=True)
                    for s in sessions))
            finally:
                for s in sessions:
                    s._combat_hb = None

            def _registrar_cripta_desta_execucao():
                """Registra o progresso desta execução da Cripta — chamado
                não só quando bate o andar-limite, mas TAMBÉM quando termina
                por morte ou poção baixa (BUG REAL corrigido 2026-07-16: só
                registrava no andar-limite, e como a Cripta é infinita por
                natureza, ela quase sempre termina por um desses dois — o
                relatório da Cripta ficava sempre vazio na prática). A tela
                de saída ('Progresso acumulado...') é capturada nos 3 casos
                (ver combat_loop_cripta), então os dados já estão disponíveis
                de qualquer jeito que a execução tenha terminado."""
                valores = {s.name: parse_saida_cripta(getattr(s, "_saida_txt", "") or "")
                          for s in sessions}
                gold_por_conta = {nome: g for nome, (x, g) in valores.items()}
                xp_por_conta = {nome: x for nome, (x, g) in valores.items()}
                duracao_segundos = time.time() - _t_inicio_cripta
                andar_final = estado.get("andar_final", andar_maximo)
                try:
                    total, media_seg = registrar_cripta(gold_por_conta, xp_por_conta, estado.get("drops_por_conta"),
                                             duracao_segundos=duracao_segundos,
                                             andar_final=andar_final)
                except Exception as e:
                    log("bot", f"(não consegui registrar a cripta: {e!r})")
                    total = _ler_relatorio_total_cripta()
                    media_seg = None
                feitas = total - baseline
                if media_seg:
                    _salvar_estimativa("cripta", "cripta", feitas, max_criptas, media_seg)
                xp_c = sum(xp_por_conta.values())
                gold_c = sum(gold_por_conta.values())
                log("bot", f"🏁 Cripta #{total} registrada ({feitas} desde que iniciou)."
                           f"  ⭐ {xp_c} XP (soma de todos) · 💰 {gold_c} gold (soma de todos)")

            if "morte" in motivos:
                log("bot", "⏹ pausando a Cripta (alguém morreu — todos saíram).")
                _registrar_cripta_desta_execucao()
                registrar_pausa("morte", "detectado na Cripta")
                return False
            if "pocao_baixa" in motivos:
                log("bot", "⏹ pausando a Cripta (Poções de Vida baixas).")
                _registrar_cripta_desta_execucao()
                registrar_pausa("pocao_vida_baixa", "acabando durante a Cripta")
                await asyncio.to_thread(
                    popup_aviso, "TofuBot — Cripta",
                    "As Poções de Vida acabaram DURANTE a Cripta!\n\n"
                    "O grupo já saiu. Reabasteça e clique Iniciar de novo.")
                return False
            if "travou" in motivos:
                log("bot", "⚠️ uma conta travou na Cripta — saindo e recomeçando pra não morrer.")
                await asyncio.sleep(3.0)
                continue
            if "andar_limite" in motivos:
                _registrar_cripta_desta_execucao()

            feitas = _ler_relatorio_total_cripta() - baseline
            if max_criptas and feitas >= max_criptas:
                log("bot", f"🎯 atingiu o limite de {max_criptas} cripta(s) — parando o bot.")
                registrar_pausa("limite_criptas", f"{feitas}/{max_criptas}")
                return False
            if parar_no_fim_pedido():
                log("bot", "⏸ 'Parar no fim' atendido — cripta concluída, parando o bot.")
                registrar_pausa("parar_no_fim", "após concluir a cripta atual")
                return False

            async def _talvez_atualizar_perfil(s):
                s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
                if s._contador_perfil % 3 == 0:
                    await atualizar_perfil_e_estimativa(s)
                await talvez_vender_no_mercado(s)
                await talvez_ler_inventario(s)
            await asyncio.gather(*(_talvez_atualizar_perfil(s) for s in sessions))

            await evitar_novo_conteudo_por_manutencao("cripta", rotulo="Cripta")
        except Exception as e:
            log("bot", f"💥 erro na Cripta: {e!r} — REINICIANDO pra continuar de onde parou.")
            log("bot", "🔎 detalhe do erro:\n" + traceback.format_exc())
            return True


async def detectar_conteudo_ativo(sessions) -> bool:
    """True se as contas JÁ ESTÃO num conteúdo ATIVO (tela de combate) — ex: o PC
    reiniciou no meio de uma cripta, ou o usuário parou o bot manualmente e
    iniciou de novo. Nesse caso o bot deve RETOMAR de onde parou (sem formar
    sala nova). Exige que TODAS as contas estejam em combate — se alguma
    caiu/saiu, é estado quebrado e faz o início normal. Tenta 2 leituras por
    conta (a 1ª pode vir velha)."""
    if not sessions:
        return False
    em_combate = 0
    for s in sessions:
        achou = False
        for _ in range(2):
            try:
                await s.refresh()
                if is_combat_screen(s.message):
                    achou = True
                    break
            except Exception:
                pass
            await poll_sleep()
        if achou:
            em_combate += 1
    return em_combate == len(sessions)


async def normalize_to_combat(s: Session):
    """
    Se a conta estiver num SUBMENU de uma masmorra ativa (Almas/Consumíveis),
    volta pro combate — pra que as 4 contas decidam 'continuar' de forma
    consistente (senão uma vai pro caminho de formação e trava no barrier).
    """
    for _ in range(3):
        await s.refresh()
        if is_combat_screen(s.message):
            return
        vb = find_button(s.message, "voltar", "🔙", "◀", "⬅", "atras")
        if not vb:
            return
        await s.click(vb, label="voltar")


async def sync_barrier(barrier: asyncio.Barrier, s: Session, label: str) -> bool:
    """Barrier com timeout de segurança: nunca trava pra sempre. Em formação
    normal todas chegam em segundos; se der dessincronia, para com log claro."""
    try:
        await asyncio.wait_for(barrier.wait(), timeout=120)
        return True
    except (asyncio.TimeoutError, asyncio.BrokenBarrierError):
        log(s.name, f"⚠️ dessincronia na {label} — reinício automático.")
        return False


async def run_account(s: Session, shared, barrier, n):
    """
    LOOP contínuo: uma masmorra após a outra. Se ao começar já houver uma
    masmorra ativa com todos vivos, CONTINUA ela (não sai). Ao concluir, volta
    pro menu e monta a próxima. O host de cada masmorra é a conta com mais
    Chaves de Masmorra (decidido em conjunto a cada ciclo).
    """
    # ativa a saída proativa por poção baixa TAMBÉM na masmorra (a Caçada em
    # Dupla já tinha isso): se, ao beber uma poção DURANTE o combate, o
    # estoque real cair abaixo de MASMORRA_POCAO_VIDA_MINIMA, act_potion()
    # marca s.sair_caca_pocao=True e o combat_loop (abaixo) sai do grupo e
    # avisa — em vez de só descobrir o problema quando alguém morre por
    # falta de cura. Configurável (antes era fixo no código).
    pocao_minima = int(getattr(config, "MASMORRA_POCAO_VIDA_MINIMA", POCAO_VIDA_MINIMA))
    pocao_aviso = int(getattr(config, "MASMORRA_POCAO_VIDA_AVISO", 100))
    s.pocao_minima_caca = pocao_minima

    # AVISO ANTES DE COMEÇAR (igual Templo do Oásis/Cripta/Caçada Dupla já
    # tinham): se o estoque já estiver baixo desde o início, avisa com
    # pop-up e pausa o bot, em vez de só descobrir isso já no meio da
    # primeira masmorra.
    qtd_inicial = await contar_pocoes_vida(s)
    log(s.name, f"🧪 Poções de Vida no estoque: {qtd_inicial if qtd_inicial is not None else 'não confirmado'}.")
    if qtd_inicial is not None and qtd_inicial < pocao_aviso:
        await asyncio.to_thread(
            popup_aviso, "TofuBot — Masmorra",
            f"Poção de Vida inferior a {pocao_aviso}!\n\n"
            f"Conta {s.name}: {qtd_inicial} poções.\n\nFavor reabastecer.")
        log(s.name, f"⏹ pausado antes de iniciar: {qtd_inicial} Poções de Vida (< {pocao_aviso}).")
        registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd_inicial} (< {pocao_aviso})")
        shared["stop"].set()
        return

    perdido_espera = 0   # quantas vezes seguidas esperei o grupo (me perdi da masmorra)
    while True:
        if shared["restart"].is_set():   # reinício automático solicitado
            return
        if shared["stop"].is_set():       # limite de masmorras atingido
            log(s.name, "⏹ parado (limite de masmorras atingido).")
            return
        try:
            # se estiver num submenu de masmorra ativa, volta pro combate (pra as
            # 4 contas decidirem 'continuar' de forma consistente)
            await normalize_to_combat(s)
            await s.refresh()
            # RESUME CONFIRMADO em 2 leituras: depois de um reinício, o histórico
            # do chat tem mensagens de combate ANTIGAS que ainda têm botões — uma
            # leitura só de is_combat_screen pega essa tela velha e o bot acha que
            # há masmorra ativa, tenta clicar num botão expirado ("Encrypted data
            # invalid") e dessincroniza (bug real 2026-07-03, causava loop de
            # reinício). Confirmar 2x seguidas evita agir em cima de tela velha.
            resume = is_combat_screen(s.message) and not someone_died(s.text)
            if resume:
                await poll_sleep()
                await s.refresh()
                resume = is_combat_screen(s.message) and not someone_died(s.text)

            if resume:
                log(s.name, "▶️ masmorra ativa detectada — continuando (sem sair, "
                            "sem ler chaves).")
                s._retomando_conteudo = True   # força resync de almas na 1ª ação (ver combat_loop)
                leave_event = shared["leave_event"]
            else:
                # EU não estou em combate. Mas, ANTES de tratar isso como "hora de
                # formar nova masmorra", CONSULTO as outras contas: se ALGUMA
                # ainda está em combate, a masmorra está ATIVA e eu apenas me
                # perdi (ex: peguei o lobby/menu no início). Nesse caso ESPERO as
                # outras terminarem — não formo sala nova nem travo na barreira
                # (era isso que causava o loop de reinício). Quando as outras
                # saírem do combate, elas param de publicar 'em combate' e eu sigo
                # pro fluxo normal, e todas reagrupam juntas. (pedido do usuário)
                shared.setdefault("em_combate", {})[s.name] = 0
                # Se o grupo ainda está em combate ATIVO, espero ele terminar. O
                # quanto esperar é decidido pelo HEARTBEAT em outras_em_combate
                # (desiste ~90s depois que o grupo PARA de progredir) — não por um
                # teto fixo em minutos. O 'perdido_espera < 600' abaixo é só uma
                # paranoia final (20 min) pra NUNCA ficar preso pra sempre; na
                # prática o heartbeat resolve muito antes (assim que a masmorra
                # acaba ou trava).
                if outras_em_combate(shared, s.name) and perdido_espera < 600:
                    perdido_espera += 1
                    if perdido_espera == 1:
                        log(s.name, "⏳ me perdi do grupo mas a masmorra ainda está "
                                    "ativa — esperando o grupo terminar pra reagrupar "
                                    "(sem reiniciar).")
                    await asyncio.sleep(2.0)
                    continue
                perdido_espera = 0

                # não há masmorra ativa: estamos no menu -> LIMPA as telas velhas
                # de combate da conversa (a masmorra anterior) pra o refresh não
                # se confundir com elas na masmorra nova.
                await limpar_historico(s)
                # --- checa as PRÓPRIAS Poções de Vida antes de formar o grupo —
                # decisão 100% INDIVIDUAL, sem esperar as outras 3 numa barreira.
                # BUG REAL visto em produção (2026-07-03): quando 3 contas já
                # estavam "resumindo" uma masmorra ativa (pulam esse trecho
                # inteiro) e só 1 caía aqui (ex: ficou de fora do grupo), essa
                # conta ficava SOZINHA numa barreira esperando as outras 3, que
                # nunca apareciam -> "dessincronia na checagem de poções" em
                # loop, reiniciando o bot repetidamente sem nunca pausar de
                # verdade por poção baixa. Cada conta agora decide por si:
                # se ESTIVER baixa, pausa o bot sozinha (não precisa de acordo
                # com as outras pra isso).
                qtd = await contar_pocoes_vida(s)
                log(s.name, f"🧪 Poções de Vida no estoque: {qtd if qtd is not None else 'não confirmado'}.")
                # None = não conseguiu ler (NÃO pausa por isso — bug real
                # corrigido 2026-07-03: o arqueiro tinha poção de sobra, mas
                # foi pausado por engano porque a leitura falhou e "0" virou
                # "estoque zerado" sem querer). Só pausa com número confirmado.
                if qtd is not None and qtd < pocao_minima:
                    # DUPLA CONFIRMAÇÃO: uma leitura isolada de estoque baixo
                    # pode ser lixo transitório (tela montando, FloodWait). Lê
                    # de novo; SÓ pausa se a 2ª leitura TAMBÉM confirmar baixo.
                    qtd2 = await contar_pocoes_vida(s)
                    log(s.name, f"🧪 Re-checagem de Poções de Vida: {qtd2 if qtd2 is not None else 'não confirmado'}.")
                    if qtd2 is None or qtd2 >= pocao_minima:
                        log(s.name, "✅ 2ª leitura não confirmou estoque baixo — seguindo normal.")
                    else:
                        log(s.name, f"⚠️ menos de {pocao_minima} Poções de Vida "
                                    f"({qtd2}) confirmado em 2 leituras — todas as contas saem e o bot pausa.")
                        # sinaliza pra TODAS saírem/pararem (não só esta conta):
                        # leave_event faz quem estiver em combate sair; stop
                        # impede formar/continuar masmorra.
                        shared["leave_event"].set()
                        shared["stop"].set()
                        registrar_pausa("pocao_vida_baixa", f"{s.name}: {qtd2} poções")
                        await asyncio.to_thread(
                            popup_aviso, "TofuBot — Masmorra",
                            f"Poção de Vida abaixo de {pocao_minima}!\n\n"
                            f"Conta {s.name}: {qtd2} poções.\n\nReabasteça e clique Iniciar de novo.")
                        return

                # --- lê chaves e todos decidem o host (conta com mais chaves) ---
                shared["keys"][s.name] = await read_keys_at_menu(s)
                if not await sync_barrier(barrier, s, "leitura de chaves"):
                    shared["restart"].set()
                    return
                host_name = max(shared["keys"], key=lambda k: shared["keys"][k])
                is_host = (s.name == host_name)
                if shared["keys"].get(host_name, 0) <= 0:
                    log(s.name, "❌ nenhuma conta tem Chave de Masmorra. Parando.")
                    return
                if is_host:
                    shared["leave_event"].clear()   # nova masmorra: zera o sinal de saída
                    shared["code"] = None
                    shared["recompensas_vistas"] = set()   # zera o relatório da masmorra nova
                    shared["acumulado"] = {"xp_total": 0, "jogadores": {}}
                    shared["morte_registrada"] = False   # zera a trava de dedup da morte
                    log(s.name, f"👑 host desta masmorra ({shared['keys'][s.name]} chaves).")
                if not await sync_barrier(barrier, s, "escolha de host"):
                    shared["restart"].set()
                    return
                leave_event = shared["leave_event"]

                # --- forma o grupo (falha aqui -> reinício limpo, não retry frágil) ---
                if is_host:
                    code = await host_create_room(s, config.SALA_SENHA)
                    shared["code"] = code
                    if not code:
                        log(s.name, "❌ host não criou a sala — reinício automático.")
                        shared["restart"].set()
                        return
                    await ready_up(s)
                    await host_start(s, n)
                else:
                    code = None
                    for _ in range(int(config.LOBBY_TIMEOUT / config.POLL_INTERVAL)):
                        if shared["restart"].is_set():
                            return
                        if shared.get("code"):
                            code = shared["code"]
                            break
                        await poll_sleep()
                    if not code:
                        log(s.name, "❌ não recebi o código da sala — reinício automático.")
                        shared["restart"].set()
                        return
                    await joiner_enter_room(s, code, config.SALA_SENHA)
                    await ready_up(s)

                if not await wait_combat_started(s):
                    log(s.name, "⚠️ combate não começou a tempo — reinício automático.")
                    shared["restart"].set()
                    return

            # --- joga a masmorra até acabar ---
            log(s.name, "⚔️ jogando a masmorra.")
            _t_inicio_masmorra = time.time()
            s._t_inicio_conteudo = _t_inicio_masmorra
            await combat_loop(s, leave_event, shared["restart"], shared)
            s._combat_hb = None   # saiu do combate: para de publicar batimento

            # registra a masmorra concluída (só a conta 'recorder', pra não
            # duplicar), e só se foi conclusão real. Tank não é obrigatório.
            # Usa o texto da tela final CAPTURADO pelo combat_loop no momento da
            # conclusão (shared["conclusao"]); se por algum motivo não houver,
            # cai pra reler a tela (compatível com o comportamento antigo).
            texto_final = shared.get("conclusao", {}).get(s.name)
            if not (texto_final and "conclu" in norm(texto_final)):
                await s.refresh()
                texto_final = s.text
            if s.name == shared["recorder"] and "conclu" in norm(texto_final):
                atualizar_recompensas(shared, texto_final)   # pega qualquer recompensa da tela final também
                dano = parse_ranking_dano(texto_final)
                # BUG REAL corrigido (prints do usuário 2026-07-15: masmorra de
                # 4 salas — Pirâmide do Deserto — perdendo XP/gold/drop das
                # salas 1-3): ANTES, quando a tela final ("Loot do Boss Final")
                # conseguia ser lida, ela SUBSTITUÍA inteiramente
                # shared['acumulado'] — só que essa tela mostra a recompensa
                # de UMA sala só (a última), não a soma da masmorra inteira.
                # Numa masmorra de sala única isso não fazia diferença (por
                # isso passou despercebido), mas numa de várias salas as
                # anteriores simplesmente sumiam do relatório.
                # shared['acumulado'] já é a fonte CONFIÁVEL e COMPLETA: ele
                # soma cada bloco 'Recompensas (vs Mob)' de CADA sala, sala
                # por sala, durante todo o combate (atualizar_recompensas roda
                # a cada refresh) — incluindo a da sala final, já somada pela
                # chamada logo acima. Então agora só usa a tela final pra
                # pegar a RARIDADE colorida dos itens (que os blocos
                # transitórios não trazem), sem nunca substituir os totais.
                loot_final = parse_loot_final_masmorra(texto_final)
                # Junta a raridade de TODAS as salas (shared['raridades_recompensas'],
                # atualizado a cada refresh durante o combate inteiro) com a da
                # tela final do chefe — cada sala tem seu próprio drop colorido,
                # não só a última.
                raridades_final = dict(shared.get("raridades_recompensas") or {})
                if loot_final:
                    raridades_final.update(loot_final["raridades"])
                # SOMA o acumulado das salas (1, 2, 3...) com o loot do CHEFE
                # (que só aparece na tela final, sem bloco transitório próprio)
                # — ver mesclar_acumulado_com_loot_final() pro motivo.
                acumulado_final = mesclar_acumulado_com_loot_final(shared.get("acumulado"), loot_final)
                _alt_atual = getattr(config, "MASMORRAS_ALTERNATIVAS", {}).get(config.TIPO_MASMORRA)
                mapa_desta_masmorra = _alt_atual["rotulo"] if _alt_atual \
                    else (config.MAPA_DESTINO or None)
                _grupo_sessions = shared.get("sessions") or [s]
                dano_mapeado = _mapear_nomes_para_conta(dano, _grupo_sessions)
                acumulado_final_mapeado = dict(acumulado_final or {})
                acumulado_final_mapeado["jogadores"] = _mapear_nomes_para_conta(
                    (acumulado_final or {}).get("jogadores"), _grupo_sessions)
                total, media_seg = registrar_masmorra(
                    texto_final, dano_mapeado, acumulado_final_mapeado,
                    duracao_segundos=time.time() - _t_inicio_masmorra,
                    raridades=raridades_final, mapa=mapa_desta_masmorra)
                feitas_na_execucao = total - shared["baseline"]
                shared["dungeons_done"] = feitas_na_execucao
                if media_seg:
                    chave_tempo = f"masmorra:{mapa_desta_masmorra}" if mapa_desta_masmorra else "masmorra"
                    _salvar_estimativa("masmorra", chave_tempo, feitas_na_execucao,
                                       config.MAX_DUNGEONS, media_seg)
                log(s.name, f"🏁 masmorra #{total} concluída e registrada. "
                            f"({feitas_na_execucao} desde que iniciou)")
                if config.MAX_DUNGEONS and feitas_na_execucao >= config.MAX_DUNGEONS:
                    log(s.name, f"🎯 atingiu o limite de {config.MAX_DUNGEONS} masmorra(s) desde o início — parando o bot.")
                    shared["stop"].set()
                    registrar_pausa("limite_masmorras", f"{feitas_na_execucao}/{config.MAX_DUNGEONS}")
                elif parar_no_fim_pedido():
                    log(s.name, "⏸ 'Parar no fim' atendido — masmorra concluída, parando.")
                    shared["stop"].set()
                    registrar_pausa("parar_no_fim", "após concluir a masmorra atual")

            if shared["stop"].is_set():
                log(s.name, "⏹ parando (limite atingido).")
                return

            # Manutenção agendada chegando perto (ver config.MANUTENCAO_*): se
            # o tempo até ela começar for menor que a média de duração desta
            # masmorra, não forma uma nova agora — espera a janela passar (e
            # só então segue o ciclo normal sozinho, sem precisar reiniciar).
            _alt_p_manut = getattr(config, "MASMORRAS_ALTERNATIVAS", {}).get(config.TIPO_MASMORRA)
            _mapa_p_manut = _alt_p_manut["rotulo"] if _alt_p_manut else (config.MAPA_DESTINO or None)
            await evitar_novo_conteudo_por_manutencao(
                f"masmorra:{_mapa_p_manut}" if _mapa_p_manut else "masmorra", rotulo="masmorra")

            # --- volta pro menu, cura quem ficou baixo, recomeça ---
            await heal_at_menu_if_low(s)
            # Perfil (nível/XP/estimativa pro próximo nível) — pedido do
            # usuário 2026-07-15: só confere a cada 3 masmorras (é uma
            # navegação a mais, não vale a pena fazer toda hora) e alimenta
            # o Status ao vivo via write_status (ver Brain.act()).
            s._contador_perfil = getattr(s, "_contador_perfil", 0) + 1
            if s._contador_perfil % 3 == 0:
                await atualizar_perfil_e_estimativa(s)
            await talvez_vender_no_mercado(s)
            await talvez_ler_inventario(s)
            log(s.name, "🔁 masmorra encerrada — recomeçando o ciclo.")

        except asyncio.BrokenBarrierError:
            return
        except Exception as e:
            log(s.name, f"💥 erro no ciclo: {e!r} — tentando de novo.")
            await asyncio.sleep(2.0)


async def _login_contas_mercado(rotulo_modo: str):
    """Login MÍNIMO (sem entrar em masmorra/caçada/etc) só das contas
    marcadas em config.MERCADO_CONTAS — usado tanto por 'Vender agora'
    quanto 'Ler inventário agora' (modos autônomos: ligam, fazem 1 coisa,
    desligam sozinhos). 'rotulo_modo' é só pro texto dos logs (ex: 'Vender
    agora', 'Ler inventário agora')."""
    contas_ok = getattr(config, "MERCADO_CONTAS", None) or []
    contas_config = [a for a in config.ACCOUNTS if a.get("phone", "").strip() in contas_ok]
    if not contas_config:
        log("bot", f"❌ {rotulo_modo}: nenhuma conta marcada em 'Contas que vendem' (aba Mercado).")
        return []
    sessions = []
    for acc in contas_config:
        nome = acc.get("name", "?")
        phone = acc.get("phone", "").strip()
        if not phone or not acc.get("char_name"):
            log(nome, "⏭️ telefone ou personagem em branco — preencha no app.")
            continue
        client = TelegramClient(config.session_path(APP_DIR, phone, nome),
                                config.API_ID, config.API_HASH, flood_sleep_threshold=360)
        try:
            await client.connect()
        except Exception as e:
            log(nome, f"❌ erro ao conectar: {e}")
            continue
        if not await client.is_user_authorized():
            log(nome, "❌ não está logada — clique em 'Login' no app pra logar esta conta.")
            await client.disconnect()
            continue
        try:
            bot = await client.get_entity(config.BOT_USERNAME)
        except Exception as e:
            log(nome, f"❌ não achei o bot '{config.BOT_USERNAME}': {e}")
            await client.disconnect()
            continue
        log(nome, f"✅ logada (modo {rotulo_modo}).")
        sessions.append(Session(client, bot, acc))
    if not sessions:
        log("bot", f"❌ {rotulo_modo}: nenhuma conta pronta.")
    return sessions


async def _rodar_vender_e_sair():
    """Modo especial 'Vender agora' com o bot DESLIGADO (pedido do usuário
    2026-07-15) — loga só as contas marcadas em config.MERCADO_CONTAS (aba
    Mercado), vende os itens configurados, e volta (quem chama isso, main(),
    encerra o processo em seguida)."""
    if not (getattr(config, "MERCADO_ITENS", None) or []):
        log("bot", "❌ Vender agora: nenhum item marcado pra vender (aba Mercado).")
        return
    sessions = await _login_contas_mercado("Vender agora")
    if not sessions:
        return
    log("bot", f"🛒 Vender agora: vendendo com {len(sessions)} conta(s)…")

    async def _vender_uma(s, atraso=0.0):
        # Escalona o INÍCIO de cada conta (mesmo raciocínio do POLL_JITTER no
        # loop principal): sem isso, todas as contas disparam cliques/checagens
        # no MESMO instante e a rajada derruba a API em FloodWait (o Telethon
        # engole isso por dentro, sem erro — só aparece como "get_messages
        # demorou Xs" — visto na prática 2026-07-16: 4 contas travando juntas
        # logo após o login, "andando de página em página" sem terminar).
        if atraso:
            await asyncio.sleep(atraso)
        try:
            total = await vender_itens_mercado(s)
            log(s.name, f"🛒 Vender agora: concluído ({total} item(ns) vendido(s)).")
        except Exception as e:
            log(s.name, f"⚠️ Vender agora: erro — {e!r}")

    await asyncio.gather(*(_vender_uma(s, i * 3.0 + random.uniform(0, 1.0))
                           for i, s in enumerate(sessions)))
    for s in sessions:
        try:
            await s.client.disconnect()
        except Exception:
            pass
    log("bot", "🛒 Vender agora: concluído pra todas as contas — encerrando sozinho.")


async def _rodar_ler_inventario_e_sair():
    """Modo especial 'Ler inventário agora' com o bot DESLIGADO (pedido do
    usuário 2026-07-15) — loga só as contas marcadas em config.MERCADO_CONTAS,
    lê o inventário de cada uma (joga tudo no banco de itens do Mercado), e
    volta (quem chama isso, main(), encerra o processo em seguida)."""
    sessions = await _login_contas_mercado("Ler inventário agora")
    if not sessions:
        return
    log("bot", f"📦 Ler inventário agora: lendo com {len(sessions)} conta(s)…")

    async def _ler_uma(s, atraso=0.0):
        # Mesmo escalonamento do 'Vender agora' (ver comentário lá) — evita
        # rajada simultânea de todas as contas logo após o login.
        if atraso:
            await asyncio.sleep(atraso)
        try:
            total = await ler_itens_inventario(s)
            log(s.name, f"📦 Ler inventário agora: concluído ({total} item(ns) vistos).")
        except Exception as e:
            log(s.name, f"⚠️ Ler inventário agora: erro — {e!r}")

    await asyncio.gather(*(_ler_uma(s, i * 3.0 + random.uniform(0, 1.0))
                           for i, s in enumerate(sessions)))
    for s in sessions:
        try:
            await s.client.disconnect()
        except Exception:
            pass
    log("bot", "📦 Ler inventário agora: concluído pra todas as contas — encerrando sozinho.")


async def main():
    await aguardar_fim_manutencao()
    if not config.API_ID or not config.API_HASH:
        log("bot", "❌ Preencha API ID e API HASH no app (e clique Salvar).")
        return False
    if not config.BOT_USERNAME:
        log("bot", "❌ Preencha o @ do bot no app (e clique Salvar).")
        return False

    # "🛒 Vender agora" com o bot DESLIGADO (pedido do usuário 2026-07-15):
    # o painel cria esse arquivo antes de LANÇAR o processo — loga só as
    # contas do Mercado, vende, e encerra sozinho (return False = não
    # reinicia, ver o if __name__ == "__main__" no fim do arquivo), sem
    # entrar em nenhum dos 6 conteúdos normais.
    if os.path.exists(VENDER_E_SAIR_FLAG):
        try:
            os.remove(VENDER_E_SAIR_FLAG)
        except OSError:
            pass
        await _rodar_vender_e_sair()
        return False

    # "📦 Ler inventário agora" com o bot DESLIGADO — mesma ideia do 'Vender
    # agora' acima, só que lê o inventário (joga tudo no banco de itens do
    # Mercado) em vez de vender.
    if os.path.exists(LER_INVENTARIO_E_SAIR_FLAG):
        try:
            os.remove(LER_INVENTARIO_E_SAIR_FLAG)
        except OSError:
            pass
        await _rodar_ler_inventario_e_sair()
        return False

    # Limpa o pedido de 'Parar no fim' só num início DE VERDADE (clique no
    # Iniciar) — NUNCA num reinício automático (exit 42 por erro), senão um
    # pedido de parada feito pouco antes de um crash seria perdido e o bot
    # continuaria rodando pra sempre. SESSAO_CONTINUAR_FLAG marca reinício
    # automático; só CHECAMOS aqui (não removemos — quem remove é o código
    # de masmorra/caçada mais abaixo, cada um na sua vez).
    if not os.path.exists(SESSAO_CONTINUAR_FLAG):
        limpar_parar_no_fim()

    modo_caca_dupla = config.MODO_CONTEUDO == "caca_dupla"
    modo_cripta = config.MODO_CONTEUDO == "cripta"
    modo_caca_solo = config.MODO_CONTEUDO == "caca_solo"
    modo_missao_oasis = config.MODO_CONTEUDO == "missao_oasis"
    modo_templo_oasis = config.MODO_CONTEUDO == "templo_oasis"
    modo_observador = config.MODO_CONTEUDO == "observador"
    # grupos_cfg: lista de duplas (cada uma = lista de 2 contas). Uma dupla =
    # uma caçada rodando sozinha; 2+ duplas = 2+ caçadas em paralelo, cada
    # uma na sua própria sala. contas_config é a lista ACHATADA (todas as
    # contas de todos os grupos) — usada só pra fazer login de todo mundo.
    grupos_cfg = config.CACA_DUPLA.get("grupos", []) if modo_caca_dupla else []
    # mesma ideia pro Templo do Oásis (Duo) — grupos PRÓPRIOS, contador PRÓPRIO.
    grupos_cfg_templo = config.TEMPLO_OASIS.get("grupos", []) if modo_templo_oasis else []
    if modo_caca_dupla:
        contas_config = [acc for grupo in grupos_cfg for acc in grupo]
    elif modo_templo_oasis:
        contas_config = [acc for grupo in grupos_cfg_templo for acc in grupo]
    elif modo_cripta:
        contas_config = config.CRIPTA.get("contas", [])
    elif modo_caca_solo:
        contas_config = config.CACA_SOLO.get("contas", [])
    elif modo_missao_oasis:
        contas_config = config.MISSAO_OASIS.get("contas", [])
    elif modo_observador:
        contas_config = config.OBSERVADOR.get("contas", [])
    else:
        # MASMORRA: entram só as contas marcadas como ATIVAS no painel (as
        # demais ficam logadas, só não participam). Sem o campo 'ativa'
        # (save antigo) = ativa, por compatibilidade.
        contas_config = [a for a in config.ACCOUNTS if a.get("ativa", True)]

    if modo_caca_dupla and not grupos_cfg:
        log("bot", "❌ Nenhuma dupla configurada na aba Caçada Dupla. "
                   "Monte pelo menos uma dupla (2 contas) no app.")
        return False
    if modo_templo_oasis and not grupos_cfg_templo:
        log("bot", "❌ Nenhuma dupla configurada na aba Templo do Oásis. "
                   "Monte pelo menos uma dupla (2 contas) no app.")
        return False
    if modo_cripta and not (1 <= len(contas_config) <= 5):
        log("bot", "❌ A Cripta precisa de 1 a 5 contas configuradas na aba Cripta.")
        return False
    if modo_caca_solo and not contas_config:
        log("bot", "❌ Nenhuma conta configurada na aba Caçada Solo.")
        return False
    if modo_missao_oasis and not contas_config:
        log("bot", "❌ Nenhuma conta configurada na aba Missão Oásis.")
        return False
    if modo_observador and not contas_config:
        log("bot", "❌ Nenhuma conta marcada no Observador.")
        return False

    sessions = []
    faltou = []
    # NÃO pede login no console: se a conta não estiver logada, avisa e para.
    # O login é feito pelo APP (botão Login). Assim o bot roda sem janela preta.
    for acc in contas_config:
        nome = acc.get("name", "?")
        phone = acc.get("phone", "").strip()
        if not phone or not acc.get("char_name"):
            log(nome, "⏭️ telefone ou personagem em branco — preencha no app.")
            faltou.append(nome)
            continue
        # flood_sleep_threshold: se o Telegram pedir pra ESPERAR até esse tempo
        # (FloodWait), o Telethon DORME sozinho e re-tenta, de forma transparente
        # em TODA requisição — em vez de LANÇAR o erro e derrubar o bot (era o
        # que acontecia: FloodWait de 257s > 60s padrão -> crash). 360s cobre os
        # floods típicos; só um flood MUITO maior que isso ainda seria lançado
        # (e aí o try/except do ciclo cuida). NÃO atrasa nada em operação normal.
        client = TelegramClient(config.session_path(APP_DIR, phone, nome),
                                config.API_ID, config.API_HASH,
                                flood_sleep_threshold=360)
        try:
            await client.connect()
        except Exception as e:
            log(nome, f"❌ erro ao conectar: {e}")
            faltou.append(nome)
            continue
        if not await client.is_user_authorized():
            log(nome, "❌ não está logada — clique em 'Login' no app pra logar esta conta.")
            faltou.append(nome)
            await client.disconnect()
            continue
        try:
            bot = await client.get_entity(config.BOT_USERNAME)
        except Exception as e:
            log(nome, f"❌ não achei o bot '{config.BOT_USERNAME}': {e}")
            faltou.append(nome)
            await client.disconnect()
            continue
        log(nome, "✅ logada.")
        sessions.append(Session(client, bot, acc))

    if faltou or not sessions:
        log("bot", f"Contas não prontas: {', '.join(faltou) or '(nenhuma)'}. "
                   f"Faça o Login no app e clique Iniciar de novo.")
        for s in sessions:
            await s.client.disconnect()
        return False

    # RETOMADA (só relevante quando o usuário para/inicia manualmente no meio
    # de um conteúdo em grupo — masmorra já resolve isso sozinha dentro do
    # próprio run_account, sem precisar disso aqui): detecta se as contas já
    # estão em combate, ANTES do passo de viagem genérica logo abaixo — sem
    # isso, o viajar_para rodava em TODO MUNDO incondicionalmente e podia
    # tirar a conta da tela de combate (via back_to_menu -> /start de última
    # instância) antes mesmo da detecção de retomada específica de cada modo
    # (mais abaixo) ter a chance de ver o combate ainda ativo. BUG REAL
    # corrigido 2026-07-16 (usuário: parou a Caçada em Dupla no meio do
    # combate, reiniciou, e "os 2 saíram e foi criada uma nova sala" — a
    # causa era exatamente essa viagem genérica rodando primeiro).
    retomar_cripta = False
    if modo_cripta:
        retomar_cripta = os.path.exists(SESSAO_CONTINUAR_FLAG) or await detectar_conteudo_ativo(sessions)
        if retomar_cripta:
            log("bot", "▶️ Cripta ATIVA detectada — retomando de onde parou "
                       "(sem viajar nem limpar a conversa).")

    sessions_ja_em_combate = set()
    if modo_caca_dupla or modo_templo_oasis:
        _ja_continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        for s in sessions:
            try:
                await s.refresh()
                if _ja_continuar or is_combat_screen(s.message):
                    sessions_ja_em_combate.add(s.name)
            except Exception:
                pass
        if sessions_ja_em_combate:
            rotulo = "Caçada em Dupla" if modo_caca_dupla else "Templo do Oásis"
            log("bot", f"▶️ {rotulo}: {len(sessions_ja_em_combate)} conta(s) já em "
                       f"combate ativo detectadas — pulando viagem pra elas (retomando).")

    # VIAJAR pro mapa certo ANTES de começar — todas as contas, em paralelo.
    # Se já estiver no mapa, cada uma só confere e segue.
    #  - Caçada em Dupla SÓ funciona em Montanhas Gélidas -> força esse mapa,
    #    ignorando o campo MAPA_DESTINO (que vale só pra masmorra).
    #  - Cripta -> Cemitério Antigo (pulado numa retomada — viajar sairia do combate).
    #  - Caçada Solo -> CADA CONTA escolhe o PRÓPRIO mapa (não é um só pra
    #    todas) — a viagem acontece dentro de run_caca_solo_conta, não aqui.
    #  - Missão Oásis -> SEMPRE Oásis Perdido, a viagem acontece dentro de
    #    run_missao_oasis_conta (mesma ideia da Caçada Solo).
    #  - Masmorra -> usa o mapa escolhido no painel (ou fica onde está se vazio).
    destino = ("Cemitério Antigo" if modo_cripta
               else config.CACA_MAPA if modo_caca_dupla
               else None if (modo_caca_solo or modo_missao_oasis or modo_observador)
               else config.MAPA_DESTINO)
    if destino and not retomar_cripta:
        # BUG REAL corrigido 2026-07-16 (usuário: parou a Caçada em Dupla no
        # meio do combate, reiniciou, e "os 2 saíram e foi criada uma nova
        # sala"): antes, essa viagem rodava em TODAS as contas incondicional-
        # mente — inclusive as que já estavam em combate ativo — e podia
        # tirar a conta da tela de combate (via back_to_menu -> /start de
        # última instância) antes mesmo da detecção de retomada (mais abaixo,
        # por dupla) ter a chance de ver o combate ainda ativo. Agora pula a
        # viagem só pras contas já detectadas em combate acima.
        alvo_viagem = [s for s in sessions if s.name not in sessions_ja_em_combate]
        if alvo_viagem:
            log("bot", f"🗺️ garantindo que todas as contas estão em '{destino}'…")
            await asyncio.gather(*(viajar_para(s, destino) for s in alvo_viagem))

    # Masmorra alternativa que exige uma SKIN específica equipada (ex:
    # Santuário de Altheryn -> "Culpa de Altheryn") — troca ANTES de entrar
    # na masmorra, só 1x (a skin fica equipada sozinha depois disso).
    # 'skin_por_raca' (ex: Hidra Ancestral): a skin MUDA por conta, conforme
    # a raça do personagem (ver config.RACA_POR_PAPEL) — cada conta resolve
    # a própria variante em vez de todas usarem o mesmo nome.
    # 'skin_unica' (ex: Covil da Hidra de Ossos): UMA conta (sempre a 1ª da
    # lista, por posição — não importa qual é, só a MISTURA final) usa uma
    # segunda skin fixa, enquanto o resto usa a principal (por raça, se for
    # o caso).
    _alt_config = getattr(config, "MASMORRAS_ALTERNATIVAS", {}).get(config.TIPO_MASMORRA)
    _skin_exigida = (_alt_config or {}).get("skin")
    if _skin_exigida and not modo_cripta and not modo_caca_dupla \
            and not (modo_caca_solo or modo_missao_oasis or modo_observador):
        _por_raca = (_alt_config or {}).get("skin_por_raca")
        _skin_unica = (_alt_config or {}).get("skin_unica")
        _raca_por_papel = getattr(config, "RACA_POR_PAPEL", {})

        async def _equipar_principal(s):
            if _por_raca:
                raca = _raca_por_papel.get(s.role)
                if not raca:
                    log(s.name, f"⚠️ papel '{s.role}' sem raça mapeada em RACA_POR_PAPEL — "
                                f"não sei qual variante de '{_skin_exigida}' equipar.")
                    return False
                return await garantir_skin_equipada(s, f"{_skin_exigida} ({raca})")
            return await garantir_skin_equipada(s, _skin_exigida)

        if _skin_unica and sessions:
            unica, *resto = sessions
            log("bot", f"🎨 garantindo skins: '{_skin_unica}' pra {unica.name}, "
                       f"'{_skin_exigida}'{' (por raça)' if _por_raca else ''} pro resto…")
            await asyncio.gather(garantir_skin_equipada(unica, _skin_unica),
                                  *(_equipar_principal(s) for s in resto))
        elif _por_raca:
            log("bot", f"🎨 garantindo que cada conta está com a variante certa de "
                       f"'{_skin_exigida}' pra sua raça…")
            await asyncio.gather(*(_equipar_principal(s) for s in sessions))
        else:
            log("bot", f"🎨 garantindo que todas as contas estão com a skin '{_skin_exigida}'…")
            await asyncio.gather(*(_equipar_principal(s) for s in sessions))

    # LIMPEZA PROFUNDA do histórico — só num INÍCIO DE VERDADE (quando o usuário
    # clica Iniciar), NUNCA em reinício automático: num reinício há masmorra/
    # caçada ATIVA e apagar tudo destruiria a tela de combate dela. Deixa a
    # conversa de cada conta realmente limpa no Telegram (a limpeza incremental
    # de 100/masmorra só some com as recentes; esta esvazia o acúmulo antigo).
    # Liga/desliga via config.LIMPEZA_PROFUNDA_ATIVO (pedido do usuário
    # 2026-07-16) — desligada, pula tanto a limpeza quanto o /start forçado
    # que ela exige (sem mensagens sobrando, não tem botão pra clicar sem ele).
    if (config.LIMPEZA_PROFUNDA_ATIVO and not os.path.exists(SESSAO_CONTINUAR_FLAG)
            and not retomar_cripta and not modo_observador):
        log("bot", "🧹 limpando o histórico das conversas…")
        await asyncio.gather(*(limpar_historico_completo(s) for s in sessions))

    if modo_cripta:
        log("bot", f"🚀 {len(sessions)} contas logadas. Rodando Cripta "
                   f"(Masmorra/Caçada desligadas — só um conteúdo por vez).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass
        continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        if continuar:
            try:
                os.remove(SESSAO_CONTINUAR_FLAG)
            except Exception:
                pass
        if continuar:
            try:
                baseline = int(open(SESSAO_BASELINE_FILE).read().strip())
            except Exception:
                baseline = _ler_relatorio_total_cripta()
        else:
            baseline = _ler_relatorio_total_cripta()
            try:
                with open(SESSAO_BASELINE_FILE, "w") as f:
                    f.write(str(baseline))
            except Exception:
                pass
        log("bot", f"📊 contando criptas a partir de agora (histórico atual: {baseline}).")
        try:
            reiniciar = await run_cripta(sessions, baseline, continuar, retomar_cripta)
        finally:
            for s in sessions:
                await s.client.disconnect()
        return bool(reiniciar)

    if modo_caca_solo:
        log("bot", f"🚀 {len(sessions)} conta(s) logada(s). Rodando Caçada Solo "
                   f"— cada uma sozinha, em paralelo (Masmorra/Caçada/Cripta desligadas).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass
        continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        if continuar:
            try:
                os.remove(SESSAO_CONTINUAR_FLAG)
            except Exception:
                pass
            try:
                baseline = int(open(SESSAO_BASELINE_FILE).read().strip())
            except Exception:
                baseline = _ler_relatorio_total_caca_solo()
        else:
            baseline = _ler_relatorio_total_caca_solo()
            try:
                with open(SESSAO_BASELINE_FILE, "w") as f:
                    f.write(str(baseline))
            except Exception:
                pass
        log("bot", f"📊 contando caçadas solo a partir de agora (histórico atual: {baseline}).")
        try:
            reiniciar = await run_caca_solo(sessions, baseline)
        finally:
            for s in sessions:
                await s.client.disconnect()
        return bool(reiniciar)

    if modo_missao_oasis:
        log("bot", f"🚀 {len(sessions)} conta(s) logada(s). Rodando Missão Oásis "
                   f"— cada uma sozinha, em paralelo (Masmorra/Caçada/Cripta/Solo desligadas).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass
        continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        if continuar:
            try:
                os.remove(SESSAO_CONTINUAR_FLAG)
            except Exception:
                pass
            try:
                baseline = int(open(SESSAO_BASELINE_FILE).read().strip())
            except Exception:
                baseline = _ler_relatorio_total_missao_oasis()
        else:
            baseline = _ler_relatorio_total_missao_oasis()
            try:
                with open(SESSAO_BASELINE_FILE, "w") as f:
                    f.write(str(baseline))
            except Exception:
                pass
        log("bot", f"📊 contando Missões do Oásis a partir de agora (histórico atual: {baseline}).")
        try:
            reiniciar = await run_missao_oasis(sessions, baseline)
        finally:
            for s in sessions:
                await s.client.disconnect()
        return bool(reiniciar)

    if modo_observador:
        log("bot", f"👁️ {len(sessions)} conta(s) logada(s). Modo OBSERVADOR "
                   f"— só lendo, sem clicar em nada (Masmorra/Caçada/Cripta/Solo/Oásis desligados).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass
        try:
            reiniciar = await run_observador(sessions)
        finally:
            for s in sessions:
                await s.client.disconnect()
        return bool(reiniciar)

    if modo_caca_dupla:
        n_grupos = len(grupos_cfg)
        log("bot", f"🚀 {len(sessions)} contas logadas. Rodando "
                   f"{n_grupos} Caçada(s) em Dupla em paralelo "
                   f"(Masmorra desligada — só um conteúdo por vez).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass

        # 'continuar' é GLOBAL (o reinício automático — exit 42 — relança o
        # processo inteiro, com TODAS as duplas juntas, não uma de cada vez).
        continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        if continuar:
            try:
                os.remove(SESSAO_CONTINUAR_FLAG)
            except Exception:
                pass

        # Divide as sessões (na mesma ordem de grupos_cfg, já achatada acima)
        # de volta em duplas — cada dupla vira uma sala/caçada independente.
        grupos_sessions = []
        idx = 0
        for grupo in grupos_cfg:
            grupos_sessions.append(sessions[idx: idx + len(grupo)])
            idx += len(grupo)

        # baseline de CADA dupla: numa CONTINUAÇÃO (reinício automático) lê o
        # progresso salvo daquela dupla (o limite max_cacadas segue valendo);
        # num início de verdade zera pra 0 (conta as caçadas desta execução).
        baselines = []
        for i in range(n_grupos):
            grupo_idx = i + 1
            if continuar:
                baselines.append(_ler_progresso_dupla(grupo_idx))
            else:
                _salvar_progresso_dupla(grupo_idx, 0)
                baselines.append(0)
        for i, b in enumerate(baselines):
            log("bot", f"📊 dupla {i + 1}: contando caçadas desta dupla a partir de {b}.")

        # RETOMADA por dupla (pedido do usuário 2026-07-16, mesmo padrão já
        # usado na Cripta): detecta se ESTA dupla específica já está numa
        # caçada ATIVA (bot parado manualmente no meio e iniciado de novo, ou
        # PC reiniciou) — se estiver, pula formar sala nova e vai direto pro
        # combate. Cada dupla é independente, então checa uma de cada vez.
        retomar_duplas = []
        for i, grupo_sessions in enumerate(grupos_sessions):
            retomar_g = continuar or await detectar_conteudo_ativo(grupo_sessions)
            retomar_duplas.append(retomar_g)
            if retomar_g:
                log("bot", f"▶️ Caçada em Dupla (dupla {i + 1}) ATIVA detectada — "
                           f"retomando de onde parou (sem formar sala nova).")

        try:
            resultados = await asyncio.gather(*(
                run_caca_dupla(grupo_sessions, baselines[i], continuar, grupo_idx=i + 1,
                               retomar=retomar_duplas[i])
                for i, grupo_sessions in enumerate(grupos_sessions)
            ))
        finally:
            for s in sessions:
                await s.client.disconnect()
        # se QUALQUER dupla pediu reinício (erro), reinicia o processo inteiro
        # (as demais duplas retomam do progresso salvo de cada uma).
        return any(resultados)   # True -> exit 42 -> iniciar.cmd relança e retoma

    if modo_templo_oasis:
        n_grupos = len(grupos_cfg_templo)
        log("bot", f"🚀 {len(sessions)} contas logadas. Rodando "
                   f"{n_grupos} Templo(s) do Oásis (Duo) em paralelo "
                   f"(Masmorra/Caçada/Cripta desligadas — só um conteúdo por vez).")
        try:
            if os.path.exists(STATUS_FILE):
                os.remove(STATUS_FILE)
        except Exception:
            pass

        continuar = os.path.exists(SESSAO_CONTINUAR_FLAG)
        if continuar:
            try:
                os.remove(SESSAO_CONTINUAR_FLAG)
            except Exception:
                pass

        # Divide as sessões (na mesma ordem de grupos_cfg_templo, já achatada
        # acima) de volta em duplas — cada dupla vira uma sala/Templo independente.
        grupos_sessions = []
        idx = 0
        for grupo in grupos_cfg_templo:
            grupos_sessions.append(sessions[idx: idx + len(grupo)])
            idx += len(grupo)

        baselines = []
        for i in range(n_grupos):
            grupo_idx = i + 1
            if continuar:
                baselines.append(_ler_progresso_dupla_templo(grupo_idx))
            else:
                _salvar_progresso_dupla_templo(grupo_idx, 0)
                baselines.append(0)
        for i, b in enumerate(baselines):
            log("bot", f"📊 dupla {i + 1}: contando execuções do Templo do Oásis a partir de {b}.")

        # RETOMADA por dupla (mesmo padrão da Caçada em Dupla/Cripta acima).
        retomar_duplas = []
        for i, grupo_sessions in enumerate(grupos_sessions):
            retomar_g = continuar or await detectar_conteudo_ativo(grupo_sessions)
            retomar_duplas.append(retomar_g)
            if retomar_g:
                log("bot", f"▶️ Templo do Oásis (dupla {i + 1}) ATIVO detectado — "
                           f"retomando de onde parou (sem formar sala nova).")

        try:
            resultados = await asyncio.gather(*(
                run_templo_oasis_dupla(grupo_sessions, baselines[i], continuar, grupo_idx=i + 1,
                                        retomar=retomar_duplas[i])
                for i, grupo_sessions in enumerate(grupos_sessions)
            ))
        finally:
            for s in sessions:
                await s.client.disconnect()
        return any(resultados)   # True -> exit 42 -> iniciar.cmd relança e retoma

    log("bot", f"🚀 {len(sessions)} contas logadas. Rodando masmorras em loop.")

    # baseline do limite de masmorras: se este processo é a CONTINUAÇÃO de um
    # reinício automático (erro/perdeu-a-vez), mantém a baseline salva; se é um
    # início de verdade (usuário clicou Iniciar), zera a partir do total atual
    # do relatório — assim MAX_DUNGEONS conta a partir de agora, não do
    # histórico acumulado no relatorio.json.
    if os.path.exists(SESSAO_CONTINUAR_FLAG):
        try:
            os.remove(SESSAO_CONTINUAR_FLAG)
        except Exception:
            pass
        try:
            baseline = int(open(SESSAO_BASELINE_FILE).read().strip())
        except Exception:
            baseline = _ler_relatorio_total()
    else:
        baseline = _ler_relatorio_total()
        try:
            with open(SESSAO_BASELINE_FILE, "w") as f:
                f.write(str(baseline))
        except Exception:
            pass
    log("bot", f"📊 contando masmorras a partir de agora (histórico atual: {baseline}).")
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)   # não mostra HP velho de uma execução anterior
    except Exception:
        pass

    shared = {
        "keys": {},                      # nome -> chaves lidas no ciclo
        "leave_event": asyncio.Event(),  # sinal de 'morreu, todos saem'
        "restart": asyncio.Event(),      # sinal de 'reiniciar o bot'
        "stop": asyncio.Event(),         # sinal de 'parar (limite de masmorras)'
        "dungeons_done": 0,              # quantas masmorras concluídas nesta execução
        "baseline": baseline,            # total do relatório no início desta execução
        "code": None,                    # código da sala do ciclo atual
        "recompensas_vistas": set(),     # hashes de blocos de recompensa já contados
        "acumulado": {"xp_total": 0, "jogadores": {}},  # ouro/xp/drop da masmorra atual
        "em_combate": {},                # nome -> timestamp da última rodada de combate (0 = fora)
        "roles": {s.name: s.role for s in sessions},  # nome -> papel (pra detectar tank sumido)
        # quem registra a masmorra concluída (evita duplicar): o tank se houver,
        # senão a 1ª conta — assim o tank NÃO é obrigatório.
        "recorder": next((s.name for s in sessions if s.role == "tank"), sessions[0].name),
        "sessions": sessions,   # lista das sessões do grupo — usada só pra
                                 # mapear nome-de-personagem -> apelido-de-
                                 # conta no relatório (_mapear_nomes_para_conta)
    }
    barrier = asyncio.Barrier(len(sessions))
    n = len(sessions)
    tasks = [asyncio.create_task(run_account(s, shared, barrier, n)) for s in sessions]
    try:
        await asyncio.gather(*tasks)
    finally:
        for s in sessions:
            await s.client.disconnect()
    return shared["restart"].is_set()


def _pode_reiniciar() -> bool:
    """Trava contra loop de reinício: no máximo 4 reinícios automáticos em 3 min."""
    marker = os.path.join(APP_DIR, "restart_times.txt")
    agora = time.time()
    tempos = []
    if os.path.exists(marker):
        try:
            tempos = [float(x) for x in open(marker).read().split() if x]
        except Exception:
            tempos = []
    tempos = [t for t in tempos if agora - t < 180]
    tempos.append(agora)
    try:
        with open(marker, "w") as f:
            f.write(" ".join(str(t) for t in tempos))
    except Exception:
        pass
    if len(tempos) > 4:
        print("⚠️ Muitos reinícios automáticos seguidos — parando pra evitar loop.")
        print("   Rode o iniciar.cmd de novo quando quiser.")
        try:
            registrar_pausa("muitos_reinicios", "4+ reinícios automáticos em 3 min — "
                                                 "verifique o run.log pra ver a causa.")
        except Exception:
            pass
        return False
    return True


if __name__ == "__main__":
    # publica o PID desta instância (o painel DESTA pasta usa pra saber se o
    # bot está rodando e pra parar SÓ ele — multi-instância, cada pasta é
    # independente).
    try:
        with open(BOT_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    try:
        precisa_reiniciar = asyncio.run(main())
    except KeyboardInterrupt:
        print("\nEncerrado pelo usuário.")
        _remover_pid_file()
        sys.exit(0)
    # código de saída 42 faz o iniciar.cmd relançar automaticamente
    if precisa_reiniciar and _pode_reiniciar():
        print("\n♻️ Reiniciando o bot (vai continuar de onde parou)...")
        try:
            with open(SESSAO_CONTINUAR_FLAG, "w") as f:
                f.write("1")
        except Exception:
            pass
        # NÃO apaga o bot.pid aqui: o relançamento sobrescreve com o novo PID
        sys.exit(42)
    _remover_pid_file()   # parada de verdade: some o PID (painel mostra Parado)
