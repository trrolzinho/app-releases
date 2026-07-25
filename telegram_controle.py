# -*- coding: utf-8 -*-
# =====================================================================
#  TofuBot — bot de CONTROLE via Telegram (pedido do usuário 2026-07-17).
#
#  O QUE É: um bot de VERDADE (criado no @BotFather — só bots recebem
#  callback_query de botão clicado, contas normais de usuário não), rodando
#  separado das contas que jogam. Fica num grupo/chat privado e deixa
#  ligar/pausar o bot, ver status ao vivo e o relatório, tudo com botões,
#  sem precisar abrir o painel gráfico.
#
#  FASE 1: Iniciar / Parar no fim / Parar agora / Status / Relatório.
#  FASE 1.5 (2026-07-19): Vender agora / Ler inventário agora (Mercado),
#  Ver log, Status por conta (submenu), aviso automático se o bot cair.
#  FASE 2 (futura): editar configs (mapa, HP%, etc.) direto pelo Telegram —
#  isso vai precisar o hunter.py recarregar o settings.json em quente
#  (hoje ele só lê 1x, na hora que o processo abre — ver config.py).
#
#  COMO RODAR:
#  1. Fale com @BotFather no Telegram, mande /newbot, escolha um nome, e ele
#     te dá um TOKEN (tipo "123456:ABC-DEF...").
#  2. Cole esse token no painel (ou direto no settings.json, chave
#     "CONTROLE_BOT_TOKEN") — ver config.CONTROLE_BOT_TOKEN.
#  3. Crie um grupo privado no Telegram, adicione o bot que você criou nele.
#  4. Rode este arquivo (python3 telegram_controle.py, ou empacotado igual
#     o bot.exe/painel.exe) NA MESMA PASTA do hunter.py/painel.py/config.py.
#  5. No grupo, cada pessoa autorizada manda /registrar UMA vez — dali em
#     diante todo mundo que registrou já pode usar os botões.
#
#  Reaproveita os MESMOS arquivos/flags que o painel.py já usa pra controlar
#  o bot (settings.json, bot.pid, parar_no_fim.flag, status.json,
#  relatorio.json) — não inventa nenhum mecanismo novo de comunicação.
# =====================================================================

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from telethon import TelegramClient, events, Button

import config

# ---------------------------------------------------------------------
#  Caminhos — MESMA lógica do painel.py (BASE = pasta deste arquivo, ou a
#  pasta do executável empacotado).
# ---------------------------------------------------------------------

def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = _app_dir()
SETTINGS_FILE = os.path.join(BASE, "settings.json")
STATUS_FILE = os.path.join(BASE, "status.json")
RELATORIO_FILE = os.path.join(BASE, "relatorio.json")
ESTIMATIVA_FILE = os.path.join(BASE, "estimativa.json")
EVENTOS_FILE = os.path.join(BASE, "eventos.json")
RUN_LOG = os.path.join(BASE, "run.log")
BOT_PID_FILE = os.path.join(BASE, "bot.pid")
PARAR_NO_FIM_FLAG = os.path.join(BASE, "parar_no_fim.flag")
# "⏹️🚪 Parar e Sair" (pedido do usuário 2026-07-21) — ver mesmo flag em
# hunter.py/painel.py.
PARAR_E_SAIR_FLAG = os.path.join(BASE, "sair_e_parar.flag")
VENDER_AGORA_FLAG = os.path.join(BASE, "vender_agora.flag")
VENDER_E_SAIR_FLAG = os.path.join(BASE, "vender_e_sair.flag")
LER_INVENTARIO_FLAG = os.path.join(BASE, "ler_inventario.flag")
LER_INVENTARIO_E_SAIR_FLAG = os.path.join(BASE, "ler_inventario_e_sair.flag")
# "🧪 Comprar poções" (pedido do usuário 2026-07-21) — mesmos 2 arquivos do
# lado do hunter.py (ver lá), conteúdo JSON {"ts", "tipo", "quantidade"}.
COMPRAR_POCOES_FLAG = os.path.join(BASE, "comprar_pocoes.flag")
COMPRAR_POCOES_E_SAIR_FLAG = os.path.join(BASE, "comprar_pocoes_e_sair.flag")
BOT_EXE = os.path.join(BASE, "bot.exe" if os.name == "nt" else "bot")
HUNTER_PY = os.path.join(BASE, "hunter.py")
IS_WINDOWS = (os.name == "nt")
STATUS_MAX_IDADE = 30   # segundos — mesmo valor do painel.py: status mais
                        # velho que isso é tratado como "sem dado ao vivo"
MONITORAR_INTERVALO = 20   # segundos entre checagens do "bot caiu sozinho"
# --- Alertas automáticos novos (pedido do usuário 2026-07-20) -------------
RESUMO_DIARIO_HORA = 23     # hora local (0-23) pra mandar o resumo do dia sozinho
CONTA_TRAVADA_COOLDOWN_SEG = 600   # não alerta a MESMA conta+contexto de novo
                                   # antes desse tempo (evita spam repetido
                                   # enquanto ela continua travada)

# LIMPEZA AUTOMÁTICA DA CONVERSA (pedido do usuário 2026-07-20: "tem como
# programar pra apagar essas mensagens antigas? ficar só o painel?") — guarda
# a ÚLTIMA mensagem do menu/painel e do aviso automático, POR CHAT, pra
# apagar a de antes sempre que uma nova for mandada (em vez de ir empilhando
# pra sempre). Fica só a mais recente de cada tipo na conversa.
_ULTIMA_MSG_PAINEL = {}    # chat_id -> Message (menu/painel)
_ULTIMA_MSG_AVISO = {}     # chat_id -> Message (aviso automático de bot caído)


async def _apagar_msg_antiga(dicionario: dict, chat_id) -> None:
    antiga = dicionario.get(chat_id)
    if antiga is None:
        return
    try:
        await antiga.delete()
    except Exception:
        pass   # já apagada por outra via, ou sem permissão — ignora


async def _enviar_painel(client, chat_id, texto, botoes) -> None:
    """Manda o menu/painel como mensagem NOVA, apagando a anterior (se
    ainda existir) — mantém só 1 cópia do painel na conversa, em vez de
    empilhar uma embaixo da outra a cada /start ou /menu."""
    await _apagar_msg_antiga(_ULTIMA_MSG_PAINEL, chat_id)
    nova = await client.send_message(chat_id, texto, buttons=botoes, parse_mode="markdown")
    _ULTIMA_MSG_PAINEL[chat_id] = nova


# ---------------------------------------------------------------------
#  Ler/escrever settings.json — SEMPRE ler tudo, mudar só a chave que
#  interessa, escrever tudo de novo (não pode apagar o resto que o painel
#  já salvou, tipo ACCOUNTS/CACA_DUPLA/etc).
# ---------------------------------------------------------------------

def _ler_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _salvar_settings(dados: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------
#  ⚙️ Configurações — liga/desliga cada aviso automático (pedido do usuário
#  2026-07-20: "achei que ficou MT poluído essa questão dos avisos"). Cada
#  chave default True (mantém o comportamento de antes pra quem não mexer
#  em nada) — dá pra desligar 1 por 1 sem afetar os outros.
# ---------------------------------------------------------------------

ALERTAS_CONFIGURAVEIS = [
    ("bot_parou", "🔴 Bot parou sozinho"),
    ("bot_pausou", "⏸️ Bot pausou (motivo específico)"),
    ("item_raro", "🎁 Item raro na hora"),
    ("morte", "💀 Morte em tempo real"),
    ("resumo_diario", "📈 Resumo diário automático"),
    ("conta_travada", "⚠️ Conta travada"),
    ("recorde", "🏆 Recorde pessoal"),
    ("compra_pocoes", "🧪 Compra de poções concluída"),
    ("subiu_nivel", "🎉 Subiu de nível"),
    ("dragao_derrotado", "🐲 Toda derrota do Dragão de Frost"),
    # 5 novos, pedido do usuário 2026-07-25 ("gostei de todos os alertas") —
    # diferente dos de cima (disparados por EVENTO pontual em eventos.json),
    # estes são checados por CONDIÇÃO a cada volta do monitor, comparando com
    # o estado da rodada anterior (ver _checar_alertas_periodicos) pra só
    # avisar na TRANSIÇÃO, sem repetir toda hora enquanto a condição persiste.
    ("buff_expirou", "🍀 Elixir/Tônico expirou"),
    ("estoque_baixo", "📦 Estoque baixo de Poção de Vida"),
    ("chaves_baixo", "🔑 Chaves de Masmorra acabando"),
    ("vip_vencendo", "👑 VIP vencendo em breve"),
    ("energia_cheia", "⚡ Energia cheia"),
]

# Alertas com default DESLIGADO (todos os outros ficam ligados por padrão)
# — "conta_travada" (2026-07-22) e "dragao_derrotado" (2026-07-23, pedido
# do usuário: "com ela padrão desligado, pra se eu ativar, receber
# notificação sempre que matar um dragão" — matar o dragão é rotina, só
# quem pedir explicitamente quer ser avisado toda vez).
_ALERTAS_DEFAULT_DESLIGADO = {"conta_travada", "dragao_derrotado"}


def _alerta_ativo(chave: str) -> bool:
    dados = _ler_settings()
    padrao = False if chave in _ALERTAS_DEFAULT_DESLIGADO else True
    return bool(dados.get(f"CONTROLE_ALERTA_{chave.upper()}", padrao))


def _alternar_alerta(chave: str) -> str:
    dados = _ler_settings()
    campo = f"CONTROLE_ALERTA_{chave.upper()}"
    padrao = False if chave in _ALERTAS_DEFAULT_DESLIGADO else True
    novo = not dados.get(campo, padrao)
    dados[campo] = novo
    _salvar_settings(dados)
    rotulo = next((r for c, r in ALERTAS_CONFIGURAVEIS if c == chave), chave)
    return f"{'🟢 Ligado' if novo else '🔴 Desligado'}: {rotulo}"


def _menu_configuracoes():
    linhas = []
    for chave, rotulo in ALERTAS_CONFIGURAVEIS:
        marcado = "✅" if _alerta_ativo(chave) else "⬜"
        linhas.append([Button.inline(f"{marcado} {rotulo}", f"alerta:{chave}".encode("utf-8"))])
    # 🌙 Modo silencioso (2026-07-21) — toggle + ajuste da janela
    ativo, ini, fim = _silencio_config()
    marcado = "✅" if ativo else "⬜"
    linhas.append([Button.inline(f"{marcado} 🌙 Silêncio ({ini:02d}h–{fim:02d}h)",
                                 b"silencio_toggle")])
    linhas.append([Button.inline("🌙 Mudar início", b"silencio_editar:inicio"),
                   Button.inline("🌙 Mudar fim", b"silencio_editar:fim")])
    # 🕐 Ajuste de fuso (pedido do usuário 2026-07-23: "horário de geração
    # do relatório diário no telegram tá 1 hora adiantado") — soma/subtrai
    # N horas antes de qualquer decisão baseada em horário (resumo diário,
    # modo silencioso). Não é bug identificado no código (tudo usa o
    # relógio do sistema de forma consistente) — é um ajuste manual pra
    # compensar o relógio/fuso do PRÓPRIO sistema, se estiver diferente do
    # esperado.
    ajuste_fuso = int(_ler_settings().get("CONTROLE_FUSO_AJUSTE_HORAS", 0) or 0)
    linhas.append([Button.inline(f"🕐 Ajuste de fuso: {ajuste_fuso:+d}h", b"fuso_editar")])
    # 🔁 Rotação de Rugido: REMOVIDA da tela de Configurações (pedido do
    # usuário 2026-07-22: "é uma ferramenta até então secreta, não vou
    # disponibilizar pros meus amigos ainda") — o controle continua
    # funcionando normalmente do lado do hunter.py (config.py +
    # TANK_ROTACAO_RUGIDO_ATIVA no settings.json, default ligado), só não
    # aparece mais como botão aqui. Pra reativar a exibição, é só devolver
    # o bloco que lia _ler_settings().get("TANK_ROTACAO_RUGIDO_ATIVA")
    # e montava o botão "rotacao_rugido_toggle" (removido daqui).
    linhas.append([Button.inline("⬅️ Voltar", b"menu")])
    return linhas


def _texto_menu_configuracoes() -> str:
    return ("⚙️ *Configurações — avisos automáticos*\n\n"
            "Toque pra ligar/desligar cada um (vale na hora, sem precisar "
            "reiniciar nada):")


# ---------------------------------------------------------------------
#  🌙 Modo silencioso (pedido do usuário 2026-07-21): dentro da janela
#  configurada (padrão 00h–07h), NENHUM aviso automático é mandado na
#  hora — tudo fica segurado em memória e sai num apanhado único
#  ("Enquanto estava em silêncio") assim que a janela termina. O resumo
#  diário automático não é afetado (RESUMO_DIARIO_HORA fica fora da
#  janela padrão; se o usuário configurar por cima, o resumo respeita a
#  janela também, já que passa pelo mesmo funil).
# ---------------------------------------------------------------------

_eventos_segurados = []   # textos já formatados, esperando a janela acabar
                          # (só memória — reiniciar o controle zera, aceitável)


def _silencio_config() -> tuple:
    """(ativo, hora_inicio, hora_fim) — horas locais 0-23."""
    dados = _ler_settings()
    return (bool(dados.get("CONTROLE_SILENCIO_ATIVO", False)),
            int(dados.get("CONTROLE_SILENCIO_INICIO", 0)),
            int(dados.get("CONTROLE_SILENCIO_FIM", 7)))


def _hora_local_ajustada() -> int:
    """Hora atual (0-23), com o ajuste manual de fuso somado (pedido do
    usuário 2026-07-23: "horário de geração do relatório diário no telegram
    tá 1 hora adiantado") — não achei nenhuma mistura de UTC com hora local
    no código (tudo já usa consistentemente o relógio do sistema onde o bot
    roda); o mais provável é o relógio/fuso do PRÓPRIO sistema estar 1h
    fora do esperado (comum em VPS configurada em UTC, por exemplo). Em vez
    de chutar uma correção fixa que pode não bater pra todo mundo, isso vira
    um ajuste manual em Configurações — soma (ou subtrai, se negativo) N
    horas antes de qualquer decisão baseada em horário (resumo diário,
    modo silencioso). Lê direto do settings.json (mesmo padrão AO VIVO do
    _silencio_config logo abaixo) — não do config.py carregado no boot, que
    só é lido 1x quando o controle liga."""
    ajuste = int(_ler_settings().get("CONTROLE_FUSO_AJUSTE_HORAS", 0) or 0)
    return (int(time.strftime("%H")) + ajuste) % 24


def _em_silencio() -> bool:
    ativo, ini, fim = _silencio_config()
    if not ativo or ini == fim:
        return False   # ini == fim seria uma janela nula — trata como desligado
    h = _hora_local_ajustada()
    if ini < fim:
        return ini <= h < fim
    return h >= ini or h < fim   # janela cruzando a meia-noite (ex: 22h–07h)


def _ids_autorizados() -> list:
    return list(getattr(config, "CONTROLE_TELEGRAM_IDS", []) or [])


def _autorizar_id(user_id: int) -> bool:
    """Adiciona 'user_id' na lista de autorizados (settings.json), se ainda
    não estiver. Retorna True se ADICIONOU agora (era novo)."""
    ids = _ids_autorizados()
    if user_id in ids:
        return False
    ids.append(user_id)
    dados = _ler_settings()
    dados["CONTROLE_TELEGRAM_IDS"] = ids
    _salvar_settings(dados)
    config.CONTROLE_TELEGRAM_IDS = ids   # aplica JÁ nesta execução, sem esperar restart
    return True


def _autorizado(user_id: int) -> bool:
    return user_id in _ids_autorizados()


def _salvar_chat_id(chat_id: int) -> None:
    """Guarda o chat/grupo onde o bot de controle está sendo usado — usado
    pelo aviso automático (monitorar_bot) pra saber pra onde mandar
    mensagem se o bot.exe cair sozinho. Só grava se mudou, pra não escrever
    o arquivo à toa a cada /menu."""
    if getattr(config, "CONTROLE_CHAT_ID", 0) == chat_id:
        return
    dados = _ler_settings()
    dados["CONTROLE_CHAT_ID"] = chat_id
    _salvar_settings(dados)
    config.CONTROLE_CHAT_ID = chat_id


# ---------------------------------------------------------------------
#  Iniciar/parar o bot — MESMA lógica de painel.py (bot_cmd/bot_rodando/
#  _pid_do_bot_vivo), só que sem nenhuma dependência de Tkinter (esse
#  arquivo roda sem janela nenhuma, junto com o bot de controle).
# ---------------------------------------------------------------------

def _ler_pid(caminho: str):
    try:
        with open(caminho) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_do_bot_vivo():
    """Mesma lógica de painel.py (ver comentário completo lá) — CORRIGIDO
    2026-07-25: a checagem antiga só olhava se o PID vivo tinha
    'hunter.py'/'bot.exe' no nome, sem checar QUAL pasta — um bot.pid
    copiado de outra pasta (ainda com o PID de lá vivo) fazia esta pasta
    achar que já tinha um bot rodando, quando era o de outra pasta. Agora
    exige o CAMINHO COMPLETO desta pasta (HUNTER_PY/BOT_EXE) na linha de
    comando do processo."""
    pid = _ler_pid(BOT_PID_FILE)
    if not pid:
        return None
    if IS_WINDOWS:
        try:
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
            cmdline = out.stdout or ""
            if HUNTER_PY in cmdline or BOT_EXE in cmdline:
                return pid
        except Exception:
            pass
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="ignore")
        if HUNTER_PY in cmdline or BOT_EXE in cmdline:
            return pid
    except Exception:
        return None
    return None


def bot_rodando() -> bool:
    return _pid_do_bot_vivo() is not None


def _bot_cmd():
    if os.path.exists(BOT_EXE):
        return [BOT_EXE]
    return [sys.executable, "-u", HUNTER_PY]


def iniciar_bot() -> str:
    if not os.path.exists(SETTINGS_FILE):
        return "⚠️ Não achei o settings.json — configure e salve pelo painel antes de iniciar."
    if bot_rodando():
        return "ℹ️ O bot já está rodando."
    try:
        os.remove(PARAR_NO_FIM_FLAG)   # pedido de "parar no fim" de uma sessão
    except OSError:                    # anterior não pode valer pro bot novo
        pass
    try:
        boot_log = open(os.path.join(BASE, "boot_stderr.log"), "w", encoding="utf-8")
        kwargs = {"cwd": BASE, "stdout": boot_log, "stderr": subprocess.STDOUT}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(_bot_cmd(), **kwargs)
    except Exception as err:
        return f"❌ Erro ao iniciar: {err}"
    return "🚀 Bot iniciado em segundo plano!"


def parar_bot_agora() -> str:
    try:
        os.remove(PARAR_NO_FIM_FLAG)
    except OSError:
        pass
    pid = _pid_do_bot_vivo()
    if not pid:
        return "ℹ️ O bot não está rodando — nada pra parar."
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
        else:
            subprocess.run(["kill", "-TERM", str(pid)], capture_output=True)
    except Exception as err:
        return f"❌ Erro ao parar: {err}"
    return "🛑 Bot parado."


def alternar_parar_no_fim() -> str:
    if os.path.exists(PARAR_NO_FIM_FLAG):
        try:
            os.remove(PARAR_NO_FIM_FLAG)
        except OSError:
            pass
        return "▶️ Parada no fim CANCELADA — o bot segue normal."
    if not bot_rodando():
        return "ℹ️ O bot não está rodando — nada pra parar."
    try:
        with open(PARAR_NO_FIM_FLAG, "w") as f:
            f.write("1")
    except OSError as err:
        return f"❌ Não consegui criar o sinal de parada: {err}"
    return ("⏸ Programado: o bot vai TERMINAR o conteúdo atual e parar, "
            "sem começar o próximo.")


def parar_e_sair() -> str:
    """'⏹️🚪 Parar e Sair' (pedido do usuário 2026-07-21: "quando paro o
    bot... fico levando ataques até morrer, em todos os locais, menos nas
    caçadas solo"): pede pro bot SAIR da sala/masmorra/caçada/cripta ATUAL
    agora (leave_room, já trata a tela de confirmação "Tem certeza?") e só
    DEPOIS parar de vez — sem deixar ninguém pra trás levando dano.
    Diferente de parar_bot_agora() (mata o processo na hora) e de
    alternar_parar_no_fim() (só termina DEPOIS que o conteúdo atual acaba
    sozinho, tarde demais se for o meio exato de um combate)."""
    if not bot_rodando():
        return "ℹ️ O bot não está rodando — nada pra sair."
    try:
        with open(PARAR_E_SAIR_FLAG, "w") as f:
            f.write("1")
    except OSError as err:
        return f"❌ Não consegui criar o sinal de saída: {err}"
    return ("🚪 Saindo da sala/masmorra atual agora e depois vai parar de vez. "
            "Pode levar alguns segundos (precisa confirmar a tela de saída).")


def _lancar_bot_em_modo(flag_e_sair: str, msg_log: str, conteudo_flag: str = "1") -> str:
    """Lança o bot.exe/hunter.py num modo pontual (Vender agora / Ler
    inventário agora / Comprar poções) — MESMA lógica de painel.py: grava a
    flag "e_sair" e sobe o processo; o hunter.py detecta a flag, faz só
    aquilo (sem entrar em masmorra/caçada/etc.) e encerra sozinho.
    'conteudo_flag' (pedido do usuário 2026-07-21, Comprar poções): a
    maioria dos modos só precisa de um '1' qualquer no arquivo (só a
    EXISTÊNCIA importa), mas Comprar poções precisa gravar JSON com
    tipo/quantidade — passa o conteúdo pronto em vez do '1' padrão."""
    try:
        with open(flag_e_sair, "w", encoding="utf-8") as f:
            f.write(conteudo_flag)
        boot_log = open(os.path.join(BASE, "boot_stderr.log"), "w", encoding="utf-8")
        kwargs = {"cwd": BASE, "stdout": boot_log, "stderr": subprocess.STDOUT}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(_bot_cmd(), **kwargs)
    except Exception as err:
        return f"❌ Erro ao iniciar: {err}"
    return msg_log


def vender_agora(contas: list = None) -> str:
    """'🛒 Vender agora' — pedido do usuário 2026-07-19, mesmo mecanismo que
    já existe no painel gráfico (vender_agora.flag / vender_e_sair.flag):
    bot já rodando -> pede uma venda avulsa pra cada conta marcada em
    'Contas que vendem', assim que ela ficar livre; bot desligado -> sobe o
    bot só nesse modo e encerra sozinho ao terminar.
    'contas' (pedido do usuário 2026-07-23: "tem como selecionar lá quem
    vende?"): lista de telefones — se informada, SÓ elas vendem (em vez de
    todas as marcadas na aba Mercado). None = comportamento de sempre."""
    if not os.path.exists(SETTINGS_FILE):
        return "⚠️ Não achei o settings.json — configure e salve pelo painel antes."
    # Formato do flag: timestamp puro (comportamento de sempre, quando não
    # há escolha de contas) ou JSON {"ts", "contas"} — o hunter.py aceita os
    # dois (ver vender_agora_timestamp/vender_agora_contas lá).
    if contas is not None:
        conteudo = json.dumps({"ts": time.time(), "contas": contas})
    else:
        conteudo = str(time.time())
    if bot_rodando():
        try:
            with open(VENDER_AGORA_FLAG, "w", encoding="utf-8") as f:
                f.write(conteudo)
        except Exception as e:
            return f"❌ Não consegui gravar o pedido: {e}"
        quem = (f"{len(contas)} conta(s) selecionada(s)" if contas is not None
                else "Cada conta marcada em 'Contas que vendem'")
        return (f"🛒 Pedido enviado! {quem} vai vender assim que ficar livre — "
                f"acompanhe pelo Status/Ver log.")
    return _lancar_bot_em_modo(
        VENDER_E_SAIR_FLAG,
        "🛒 Bot ligado em modo 'Vender agora' — vai vender e se desligar sozinho.",
        conteudo_flag=conteudo)


def ler_inventario_agora() -> str:
    """'📦 Ler inventário agora' — mesmo esquema do vender_agora(), só que lê
    o inventário de cada conta marcada e joga todo item visto na lista do
    Mercado."""
    if not os.path.exists(SETTINGS_FILE):
        return "⚠️ Não achei o settings.json — configure e salve pelo painel antes."
    if bot_rodando():
        try:
            with open(LER_INVENTARIO_FLAG, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception as e:
            return f"❌ Não consegui gravar o pedido: {e}"
        return ("📦 Pedido enviado! Cada conta marcada vai ler o inventário assim "
                "que ficar livre — depois, abra o painel e clique 'Atualizar lista'.")
    return _lancar_bot_em_modo(LER_INVENTARIO_E_SAIR_FLAG,
                               "📦 Bot ligado em modo 'Ler inventário agora' — vai ler e se desligar sozinho.")


_ITEM_LOJA_POR_TIPO = {
    "vida": "Poção de Vida", "energia": "Poção de Energia",
    "tonico_forca": "Super Tônico de Força",
    "tonico_precisao": "Super Tônico de Precisão",
    "tonico_defesa": "Super Tônico de Defesa",
}


def comprar_pocoes_agora(tipo: str, quantidade: int, contas: list = None) -> str:
    """'🧪 Comprar Poção de Vida/Energia' (pedido do usuário 2026-07-21:
    "digito uma quantidade e ele vai na loja e compra?") — mesmo esquema do
    vender_agora()/ler_inventario_agora(), mas grava JSON (tipo+quantidade)
    em vez de só um timestamp, já que a compra precisa saber O QUÊ e
    QUANTO. 'contas' (pedido do usuário 2026-07-22: "tem como escolher
    quais contas quer comprar?"): lista de telefones — se informada, SÓ
    essas contas compram (em vez de todas as marcadas em 'Contas que
    vendem'). None = comportamento de sempre (todas as marcadas)."""
    nome_item = _ITEM_LOJA_POR_TIPO.get(tipo, tipo)
    if not os.path.exists(SETTINGS_FILE):
        return "⚠️ Não achei o settings.json — configure e salve pelo painel antes."
    payload_dict = {"ts": time.time(), "tipo": tipo, "quantidade": quantidade}
    if contas is not None:
        payload_dict["contas"] = contas
    payload = json.dumps(payload_dict)
    if bot_rodando():
        try:
            with open(COMPRAR_POCOES_FLAG, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception as e:
            return f"❌ Não consegui gravar o pedido: {e}"
        return (f"🧪 Pedido enviado! {'A conta selecionada vai' if contas and len(contas) == 1 else 'As contas selecionadas vão'} "
                f"comprar {quantidade}x {nome_item} assim que ficar(em) livre(s) — "
                f"acompanhe pelo Status/Ver log.")
    return _lancar_bot_em_modo(
        COMPRAR_POCOES_E_SAIR_FLAG,
        f"🧪 Bot ligado em modo 'Comprar {nome_item}' ({quantidade}x) — "
        f"vai comprar e se desligar sozinho.",
        conteudo_flag=payload)


def _contas_disponiveis_compra() -> list:
    """Lista (nome, telefone) das contas com telefone+personagem preenchidos
    — mesma fonte que o resto do controle usa pra listar contas."""
    dados = _ler_settings()
    contas = dados.get("ACCOUNTS") or []
    return [(a.get("name") or a.get("phone", "?"), a.get("phone", "").strip())
            for a in contas if a.get("phone", "").strip() and a.get("char_name")]


def _menu_selecionar_contas_compra(user_id: int):
    """Tela de seleção de contas ANTES de perguntar a quantidade (pedido do
    usuário 2026-07-22) — toggle por conta + atalhos Todas/Nenhuma."""
    estado = _selecao_compra.get(user_id)
    if not estado:
        return []
    contas_disp = _contas_disponiveis_compra()
    linhas = []
    for nome, fone in contas_disp:
        marcado = "✅" if fone in estado["contas"] else "⬜"
        linhas.append([Button.inline(f"{marcado} {nome}", f"comprar_conta_t:{fone}".encode("utf-8"))])
    linhas.append([Button.inline("✅ Todas", b"comprar_conta_todas"),
                   Button.inline("⬜ Nenhuma", b"comprar_conta_nenhuma")])
    linhas.append([Button.inline("▶️ Continuar", b"comprar_conta_continuar"),
                   Button.inline("❌ Cancelar", b"comprar_conta_cancelar")])
    return linhas


def _texto_selecionar_contas_compra(user_id: int) -> str:
    estado = _selecao_compra.get(user_id)
    if not estado:
        return "⚠️ Sessão de seleção perdida — clica no botão de novo."
    nome_item = _ITEM_LOJA_POR_TIPO.get(estado["tipo"], estado["tipo"])
    n = len(estado["contas"])
    return (f"🧪 *Comprar {nome_item}*\n\n"
            f"Escolha quais contas vão comprar (toque pra marcar/desmarcar).\n"
            f"Selecionadas: *{n}*")


def _menu_selecionar_contas_venda(user_id: int):
    """Tela de seleção de contas do '🛒 Vender agora' (pedido do usuário
    2026-07-23: "no mercado, no telegram, tem vender agora só... tem como
    selecionar lá quem vende?") — mesmo padrão da tela de compra acima."""
    estado = _selecao_venda.get(user_id)
    if not estado:
        return []
    linhas = []
    for nome, fone in _contas_disponiveis_compra():
        marcado = "✅" if fone in estado["contas"] else "⬜"
        linhas.append([Button.inline(f"{marcado} {nome}", f"vender_conta_t:{fone}".encode("utf-8"))])
    linhas.append([Button.inline("✅ Todas", b"vender_conta_todas"),
                   Button.inline("⬜ Nenhuma", b"vender_conta_nenhuma")])
    linhas.append([Button.inline("▶️ Vender agora", b"vender_conta_continuar"),
                   Button.inline("❌ Cancelar", b"vender_conta_cancelar")])
    return linhas


def _texto_selecionar_contas_venda(user_id: int) -> str:
    estado = _selecao_venda.get(user_id)
    if not estado:
        return "⚠️ Sessão de seleção perdida — clica no botão de novo."
    n = len(estado["contas"])
    return (f"🛒 *Vender agora*\n\n"
            f"Escolha quais contas vão vender (toque pra marcar/desmarcar).\n"
            f"Selecionadas: *{n}*")


def ver_log(n: int = 25) -> str:
    """Últimas N linhas do run.log — pedido do usuário 2026-07-19. Trunca
    pro limite de mensagem do Telegram (4096 caracteres) mostrando só as
    linhas mais recentes que couberem, pra nunca dar erro ao enviar."""
    if not os.path.exists(RUN_LOG):
        return "ℹ️ Ainda não existe run.log (o bot nunca rodou nesta pasta)."
    try:
        with open(RUN_LOG, encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
    except Exception as e:
        return f"⚠️ Não consegui ler o run.log: {e}"
    ultimas = [l.rstrip("\n") for l in linhas[-n:]]
    texto = "📟 *Últimas linhas do log:*\n\n```\n" + "\n".join(ultimas) + "\n```"
    limite = 3900   # margem de segurança abaixo do limite de 4096 do Telegram
    while len(texto) > limite and ultimas:
        ultimas.pop(0)   # tira a linha mais ANTIGA restante até caber
        texto = "📟 *Últimas linhas do log (cortado):*\n\n```\n" + "\n".join(ultimas) + "\n```"
    return texto or "ℹ️ run.log está vazio."


# ---------------------------------------------------------------------
#  Status ao vivo (status.json) e Relatório (relatorio.json) — formata
#  pra texto, mesma informação que o painel mostra nas abas Status/Relatório.
# ---------------------------------------------------------------------

def _formatar_duracao(segundos) -> str:
    if segundos is None:
        return "—"
    segundos = int(round(segundos))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h}h {m:02d}min"
    if m:
        return f"{m}min {s:02d}s"
    return f"{s}s"


def _barra_hp(hp, hp_max, blocos: int = 10, cheio: str = "🟥", vazio: str = "⬜") -> str:
    """Barra visual de HP em blocos (estilo parecido com a barra do próprio
    jogo) — pedido do usuário 2026-07-19 ('tem como colocar os HP em
    barra?'): antes o Status só mostrava os números (123/170), sem dar uma
    noção visual rápida de quão cheio tá o HP. 'cheio'/'vazio' (pedido do
    usuário 2026-07-20): permite uma cor DIFERENTE da barra do jogador —
    usado pra barra do MONSTRO, pra não confundir uma com a outra. Retorna
    string vazia se não tiver HP/HP máximo pra calcular."""
    if hp is None or not hp_max:
        return ""
    ratio = max(0.0, min(1.0, hp / hp_max))
    cheios = round(ratio * blocos)
    if hp > 0 and cheios == 0:
        cheios = 1   # tem HP de verdade — nunca mostra a barra toda vazia
    cheios = min(blocos, cheios)
    return cheio * cheios + vazio * (blocos - cheios)


def formatar_mapas() -> str:
    """'🗺️ Onde cada um está' — pedido do usuário 2026-07-20: visão só de
    mapa/andar por conta, sem HP/stats (isso já tem em Status/Status por
    conta) — só o essencial pra ver rápido onde cada personagem está agora."""
    if not bot_rodando():
        return "⏹ O bot não está rodando agora."
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Bot rodando, mas ainda sem dado de status (pode ser só o início)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    agora = time.time()
    linhas = ["🗺️ *Onde cada um está agora*\n"]
    algum = False
    for nome, d in sorted(dados.items()):
        if not isinstance(d, dict) or "hp" not in d:
            continue
        idade = agora - (d.get("ts") or 0)
        if idade > STATUS_MAX_IDADE:
            continue
        algum = True
        progresso = d.get("progresso") or "—"
        linhas.append(f"👤 *{nome}* — {progresso}")
    if not algum:
        return "ℹ️ Bot rodando, mas sem status recente de nenhuma conta ainda."
    return "\n".join(linhas)


def formatar_status() -> str:
    if not bot_rodando():
        return "⏹ O bot não está rodando agora."
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Bot rodando, mas ainda sem dado de status (pode ser só o início)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    agora = time.time()

    def _linha_conta(nome, d, ocultar_monstro=False):
        hp, hp_max = d.get("hp"), d.get("hp_max")
        barra_hp = f"{hp}/{hp_max}" if hp is not None and hp_max else "—"
        linha = f"👤 *{nome}* — ❤️ {barra_hp}"
        if d.get("progresso"):
            linha += f" · {d['progresso']}"
        barra = _barra_hp(hp, hp_max)
        if barra:
            linha += f"\n   {barra}"
        # Nome do monstro/boss atual (pedido do usuário 2026-07-19: "mostra
        # qual mob cada 1 tá matando") — ver monster_name()/parse_monstro_
        # nome_solo() no hunter.py, conforme o modo. 'ocultar_monstro'
        # (pedido do usuário 2026-07-20: "o nome do adversário tá repetido,
        # deixa 1 só pra cada dupla") — quando todo mundo do grupo/dupla
        # está lutando com o MESMO monstro (visto pelo _monstro_comum()
        # abaixo), essa linha já foi mostrada 1x pro grupo inteiro, então
        # aqui não repete.
        if not ocultar_monstro:
            linha += _linha_monstro(d)
        if d.get("inicio_ts"):
            linha += f"\n   ⏱ há {_formatar_duracao(agora - d['inicio_ts'])}"
        return linha

    def _linha_monstro(d):
        if d.get("monstro_nome"):
            txt = f"\n   🐾 {d['monstro_nome']}"
            if d.get("hp_monstro") is not None and d.get("hp_monstro_max"):
                txt += f" ({d['hp_monstro']}/{d['hp_monstro_max']})"
        elif d.get("hp_monstro") is not None and d.get("hp_monstro_max"):
            txt = f"\n   👹 monstro: {d['hp_monstro']}/{d['hp_monstro_max']}"
        else:
            return ""
        barra_monstro = _barra_hp(d.get("hp_monstro"), d.get("hp_monstro_max"),
                                   cheio="🟪", vazio="⬜")
        if barra_monstro:
            txt += f"\n   {barra_monstro}"
        return txt

    def _monstro_chave(d):
        if d.get("hp_monstro") is None or not d.get("hp_monstro_max"):
            return None
        return (d.get("monstro_nome"), d["hp_monstro"], d["hp_monstro_max"])

    def _monstro_comum(lista):
        """Se TODO MUNDO do grupo (que tem dado de monstro) estiver lutando
        com o MESMO monstro (mesmo nome + mesmo HP/HP máx — sinal de que é
        o mesmo combate compartilhado, ex: Caçada em Dupla/Masmorra/Cripta),
        devolve o 'd' de qualquer um deles pra render 1 vez só. Se differ
        (ou é combate individual, tipo Caçada Solo/Missão Oásis), devolve
        None e cada conta mostra o próprio monstro normalmente."""
        chaves = {_monstro_chave(d) for _, d in lista}
        chaves.discard(None)
        if len(chaves) == 1 and all(_monstro_chave(d) is not None for _, d in lista):
            return lista[0][1]
        return None

    # Agrupa as contas com dado recente por MODO (pedido do usuário
    # 2026-07-19: "mostre qual conteúdo está fazendo") — normalmente é o
    # MESMO pra todas (só um conteúdo roda por vez), mas agrupar por modo
    # também cobre uma transição no meio (uma conta com dado mais antigo
    # enquanto as outras já mudaram de conteúdo).
    por_modo = {}
    for nome, d in sorted(dados.items()):
        if not isinstance(d, dict) or "hp" not in d:
            continue   # ex: chave "missao_oasis" tem outro formato — pula aqui
        idade = agora - (d.get("ts") or 0)
        if idade > STATUS_MAX_IDADE:
            continue   # dado velho demais — não mostra (evita informação enganosa)
        modo = d.get("modo") or "Conteúdo"
        por_modo.setdefault(modo, []).append((nome, d))
    if not por_modo:
        return "ℹ️ Bot rodando, mas sem status recente de nenhuma conta ainda."

    linhas = ["📊 *Status ao vivo*"]
    for modo, contas in por_modo.items():
        linhas.append(f"\n*{modo}*")
        # Dentro da Caçada em Dupla, agrupa Dupla 1 / Dupla 2 separadas
        # (pedido do usuário: "agrupa a dupla 1 e a dupla 2") — ver
        # s._dupla_num no hunter.py.
        duplas = {}
        sem_dupla = []
        for nome, d in contas:
            grupo = d.get("dupla")
            if grupo:
                duplas.setdefault(grupo, []).append((nome, d))
            else:
                sem_dupla.append((nome, d))
        for grupo_num in sorted(duplas.keys()):
            linhas.append(f"\n  *Dupla {grupo_num}:*")
            comum = _monstro_comum(duplas[grupo_num])
            if comum:
                linhas.append(_linha_monstro(comum).strip())
            linhas.extend(_linha_conta(nome, d, ocultar_monstro=bool(comum))
                          for nome, d in duplas[grupo_num])
        comum = _monstro_comum(sem_dupla) if sem_dupla else None
        if comum:
            linhas.append(_linha_monstro(comum).strip())
        linhas.extend(_linha_conta(nome, d, ocultar_monstro=bool(comum)) for nome, d in sem_dupla)
    return "\n".join(linhas)


def _nomes_contas() -> list:
    """Nomes das contas configuradas (config.ACCOUNTS) — usado pro submenu
    '👤 Status por conta'. Não depende do bot estar rodando nem de ter dado
    ao vivo — vem direto do settings.json/config.py."""
    return [str(c.get("name", "")).strip() for c in (getattr(config, "ACCOUNTS", []) or [])
            if str(c.get("name", "")).strip()]


# Bloco (chave em settings.json) de onde vem a lista de contas de cada modo
# — usado tanto pro HP% por conta (abaixo) quanto pra achar o "vida_min_pct"
# PADRÃO daquele modo quando uma conta não tem valor próprio ainda.
_BLOCO_POR_MODO = {
    "caca_dupla": "CACA_DUPLA", "templo_oasis": "TEMPLO_OASIS", "cripta": "CRIPTA",
    "caca_solo": "CACA_SOLO", "missao_oasis": "MISSAO_OASIS", "fortaleza_orcs": "FORTALEZA_ORCS",
}


def _contas_do_modo(dados: dict, modo: str) -> list:
    """Lista PLANA de contas (dicts, com 'vida_min_pct' entre outros campos)
    do modo indicado — pedido do usuário 2026-07-20 ('HP% de poção,
    independente do modo'). Cada modo guarda suas contas numa estrutura
    ligeiramente diferente (ACCOUNTS pra masmorra, 'grupos' pra dupla/
    templo, 'contas' pra cripta/solo/missão/fortaleza) — normaliza tudo
    numa lista só. São REFERÊNCIAS de verdade pros dicts dentro de 'dados'
    (não cópias) — editar um item aqui já edita 'dados' na hora; só falta
    salvar 'dados' inteiro depois."""
    if modo == "masmorra":
        return dados.get("ACCOUNTS") or []
    if modo in ("caca_dupla", "templo_oasis"):
        grupos = (dados.get(_BLOCO_POR_MODO[modo]) or {}).get("grupos") or []
        return [c for grupo in grupos for c in grupo]
    bloco = _BLOCO_POR_MODO.get(modo)
    if bloco:
        return (dados.get(bloco) or {}).get("contas") or []
    return []


def _nomes_contas_do_modo() -> list:
    dados = _ler_settings()
    nomes = [str(c.get("name", "")).strip() for c in _contas_do_modo(dados, _modo_atual())
             if str(c.get("name", "")).strip()]
    return nomes or _nomes_contas()   # modo sem contas configuradas ainda -> cai pro geral


def _hp_pct_conta(nome: str):
    """HP% de poção ATUAL de uma conta dentro do MODO ATIVO agora — ou o
    padrão daquele modo, se essa conta ainda não tiver um valor próprio."""
    modo = _modo_atual()
    dados = _ler_settings()
    for c in _contas_do_modo(dados, modo):
        if str(c.get("name", "")).strip() == nome and c.get("vida_min_pct") is not None:
            return c["vida_min_pct"]
    bloco = _BLOCO_POR_MODO.get(modo)
    if bloco:
        return (dados.get(bloco) or {}).get("vida_min_pct", 40)
    return 40


def _definir_hp_pct_conta(nome: str, valor: int) -> str:
    modo = _modo_atual()
    dados = _ler_settings()
    for c in _contas_do_modo(dados, modo):
        if str(c.get("name", "")).strip() == nome:
            c["vida_min_pct"] = valor
            _salvar_settings(dados)
            return f"✅ HP% de poção de *{nome}* atualizado pra *{valor}%* (modo: {modo})."
    return f"⚠️ Não achei '{nome}' na configuração do modo atual ({modo}) — atualize o menu."


def _menu_hp_contas():
    nomes = _nomes_contas_do_modo()
    if not nomes:
        return [[Button.inline("⬅️ Voltar", b"menu")]]
    linhas = [[Button.inline(f"{nome} — {_hp_pct_conta(nome)}%", f"hp_pct:{nome}".encode("utf-8"))]
              for nome in nomes]
    linhas.append([Button.inline("⬅️ Voltar", b"menu")])
    return linhas


def _texto_menu_hp_contas() -> str:
    modo = _modo_atual()
    rotulo = next((r for c, r in MODOS_CONTEUDO if c == modo), modo)
    if not _nomes_contas_do_modo():
        return f"⚠️ Nenhuma conta configurada ainda pro modo *{rotulo}*."
    return (f"🩺 *HP% de poção por conta*\n\nModo atual: *{rotulo}*\n\n"
            f"Toque numa conta pra mudar (só vale depois de reiniciar o bot):")


def formatar_status_conta(nome: str) -> str:
    """Detalhe de UMA conta só (HP/XP/Energia/stats/buff) — pedido do
    usuário 2026-07-19, mesma informação que já aparece no cartão da conta
    no painel gráfico (ver painel.py ContaCard.atualizar_status_resumo),
    só que puxada aqui pelo Telegram."""
    if not os.path.exists(STATUS_FILE):
        return f"ℹ️ Ainda sem dado nenhum de *{nome}* (bot nunca rodou, ou acabou de iniciar)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    info = dados.get(nome)
    if not isinstance(info, dict):
        return f"ℹ️ Ainda sem dado nenhum de *{nome}* (bot nunca rodou essa conta, ou o nome não bate)."
    agora = time.time()
    idade = agora - (info.get("ts") or 0)
    linhas = [f"👤 *{nome}*\n"]
    hp, hp_max = info.get("hp"), info.get("hp_max")
    if hp is not None and hp_max:
        linhas.append(f"❤️ HP: {hp}/{hp_max} ({hp / hp_max:.0%})")
        barra = _barra_hp(hp, hp_max)
        if barra:
            linhas.append(barra)
    if info.get("modo"):
        linhas.append(f"🎮 {info['modo']}" + (f" · Dupla {info['dupla']}" if info.get("dupla") else ""))
    if info.get("progresso"):
        linhas.append(f"📍 {info['progresso']}")
    if info.get("monstro_nome"):
        monstro_txt = f"🐾 {info['monstro_nome']}"
        if info.get("hp_monstro") is not None and info.get("hp_monstro_max"):
            monstro_txt += f" ({info['hp_monstro']}/{info['hp_monstro_max']})"
        linhas.append(monstro_txt)
    elif info.get("hp_monstro") is not None and info.get("hp_monstro_max"):
        linhas.append(f"👹 Monstro: {info['hp_monstro']}/{info['hp_monstro_max']}")
    barra_monstro = _barra_hp(info.get("hp_monstro"), info.get("hp_monstro_max"),
                               cheio="🟪", vazio="⬜")
    if barra_monstro:
        linhas.append(barra_monstro)
    nivel = info.get("nivel")
    if nivel is not None:
        linha_xp = f"⭐ Lv{nivel}"
        if info.get("xp_faltam") is not None:
            linha_xp += f" · faltam {info['xp_faltam']:,}".replace(",", ".") + " XP"
        linhas.append(linha_xp)
    energia, energia_max = info.get("energia"), info.get("energia_max")
    if energia is not None and energia_max:
        linhas.append(f"⚡ Energia: {energia}/{energia_max}")
    atk, defesa, crit = info.get("atk"), info.get("defesa"), info.get("crit")
    if atk is not None and defesa is not None and crit is not None:
        linhas.append(f"⚔️ ATK {atk}  🛡️ DEF {defesa}  🎯 CRIT {crit}%")
    if info.get("buff_texto"):
        linhas.append(f"🧪 {info['buff_texto']}")
    if info.get("inicio_ts"):
        linhas.append(f"⏱ há {_formatar_duracao(agora - info['inicio_ts'])}")
    if idade > STATUS_MAX_IDADE:
        linhas.append(f"\n_(dado de {_formatar_duracao(idade)} atrás — pode estar desatualizado)_")
    return "\n".join(linhas)


CONTAS_PAUSADAS_FILE = os.path.join(BASE, "contas_pausadas.json")


def _contas_pausadas() -> set:
    try:
        with open(CONTAS_PAUSADAS_FILE, encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def _alternar_pausa_conta(nome: str) -> bool:
    """Liga/desliga a pausa de UMA conta (pedido do usuário 2026-07-23:
    "detecta algum bug, pausa ela, termina os outros conteúdos e depois
    substituo os arquivos") — grava num arquivo próprio que o hunter.py lê
    ao vivo (mesmo padrão do toggle de rotação de Rugido), sem precisar
    reiniciar o bot. Retorna o NOVO estado (True = pausada agora)."""
    pausadas = _contas_pausadas()
    novo_estado = nome not in pausadas
    if novo_estado:
        pausadas.add(nome)
    else:
        pausadas.discard(nome)
    try:
        with open(CONTAS_PAUSADAS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(pausadas), f)
    except Exception:
        pass
    return novo_estado


def _menu_contas():
    """Um botão por conta configurada + Voltar — pedido do usuário
    2026-07-19 ('👤 Status por conta'). Contas pausadas (ver 2026-07-23:
    "detecta algum bug, pausa ela...") mostram ⏸️ na frente, pra ver de
    relance quem está pausada sem precisar entrar em cada uma."""
    nomes = _nomes_contas()
    if not nomes:
        return [[Button.inline("⬅️ Voltar", b"menu")]]
    pausadas = _contas_pausadas()
    linhas = [[Button.inline(f"{'⏸️ ' if nome in pausadas else '👤 '}{nome}", f"conta:{nome}".encode("utf-8"))]
              for nome in nomes]
    linhas.append([Button.inline("⬅️ Voltar", b"menu")])
    return linhas


def formatar_relatorio() -> str:
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída registrada)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    # Reescrito 2026-07-21 (usuário: "a relatório tá MT simples") e de
    # quebra corrige 2 problemas do print que ele mandou:
    #   1) a Fortaleza dos Orcs NÃO aparecia nos totais históricos
    #      (fortaleza_orcs_total existe no relatorio.json desde que o modo
    #      entrou, mas nunca foi adicionado nesta lista);
    #   2) a seção *Hoje* despejava as chaves CRUAS do diario
    #      ("gold_fortaleza_orcs: 7.760") — agora reaproveita
    #      _resumo_dia_legivel, o MESMO formatador da aba "📅 Por dia"
    #      (nomes com emoji, martelo/poeira, por-conta com ⭐/💰/⚔️/💀).
    #      Isso também mata em definitivo o bug antigo do dict cru
    #      (corrigido 2026-07-19), porque nada mais é despejado por chave.
    linhas = ["📈 *Relatório*\n"]
    totais = [
        ("🏰 Masmorra", dados.get("total", 0)),
        ("🏯 Fortaleza dos Orcs", dados.get("fortaleza_orcs_total", 0)),
        ("⚔️ Caçada em Dupla", dados.get("cacadas_total", 0)),
        ("🗡️ Caçada Solo", dados.get("caca_solo_total", 0)),
        ("🪦 Cripta", dados.get("criptas_total", 0)),
        ("🏛️ Templo do Oásis", dados.get("templo_oasis_total", 0)),
        ("🏜️ Missão Oásis", dados.get("missao_oasis_total", 0)),
    ]
    soma = sum(int(v or 0) for _, v in totais)
    if soma:
        linhas.append("*Totais históricos:*")
        for nome, valor in totais:
            if valor:
                linhas.append(f"{nome}: *{valor}*")
        linhas.append(f"Σ Geral: *{soma}* execuções")
    extras = []
    martelo = dados.get("martelo_magico_total", 0)
    if martelo:
        extras.append(f"🔨 Martelos Mágicos: *{martelo}*")
    poeira = dados.get("poeira_estelar_total", 0)
    if poeira:
        extras.append(f"🌟 Poeira Estelar: *{poeira}*")
    if extras:
        linhas.append(" · ".join(extras))
    hoje = time.strftime("%Y-%m-%d")
    diario_hoje = (dados.get("diario") or {}).get(hoje)
    if diario_hoje:
        bloco = _resumo_dia_legivel(hoje, diario_hoje)
        # _resumo_dia_legivel abre com "\n*AAAA-MM-DD*" (formato da aba Por
        # dia) — aqui troca por um cabeçalho mais amigável com dd/mm
        bloco[0] = f"\n*Hoje ({hoje[8:10]}/{hoje[5:7]}):*"
        linhas.extend(bloco)
    if soma == 0 and not diario_hoje:
        return "ℹ️ Relatório existe, mas ainda sem nenhuma execução concluída."
    return "\n".join(linhas)


# "Relatório com abas" (pedido do usuário 2026-07-19: "deixe o relatório
# mais completo, com abas") — o Telegram não tem abas de verdade, então
# cada "aba" do relatório do painel gráfico (Masmorra/Caçada em Dupla/
# Templo do Oásis/Cripta/Caçada Solo/Missão Oásis/Por dia) vira um botão
# que troca o texto da mensagem (mesmo padrão já usado em "Status por
# conta"). (chave da lista em relatorio.json, chave do total, rótulo)
_MODOS_RELATORIO = [
    ("masmorras", "total", "🏰 Masmorra"),
    ("cacadas", "cacadas_total", "⚔️ Caçada em Dupla"),
    ("temploses", "templo_oasis_total", "🏛️ Templo do Oásis"),
    ("criptas", "criptas_total", "🪦 Cripta"),
    ("caca_solo", "caca_solo_total", "🗡️ Caçada Solo"),
    ("missao_oasis", "missao_oasis_total", "🏜️ Missão Oásis"),
]


def _formatar_registro_generico(r: dict) -> str:
    """UMA execução em DUAS linhas curtas — antes era 1 linha longa com
    ' · ' que quebrava feio na largura do celular (print do usuário
    2026-07-21: cada execução virava 2 linhas desalinhadas). Agora a 1ª
    linha tem identificação (nº/hora/duração) e a 2ª (prefixo ┗) tem o
    resultado (XP/gold/mapa/conta/alvo/drops) — cada uma cabe na tela.
    Os modos não usam exatamente os mesmos campos (ver hunter.py:
    registrar_masmorra/registrar_caca_dupla/registrar_cripta/etc.), então
    lê de forma defensiva só o que existir em CADA registro."""
    linha1 = [f"*#{r.get('n', '?')}* · {r.get('hora', '—')}"]
    if r.get("tempo"):
        linha1.append(f"⏱ {r['tempo']}")
    linha2 = []
    if r.get("xp_total") is not None:
        linha2.append(f"⭐ {r['xp_total']:,}".replace(",", ".") + " XP")
    gold = r.get("gold")
    if isinstance(gold, dict) and gold:
        linha2.append(f"💰 {sum(gold.values()):,}".replace(",", ".") + "g")
    elif isinstance(gold, (int, float)) and gold:
        linha2.append(f"💰 {gold:,}".replace(",", ".") + "g")
    if r.get("mapa"):
        linha2.append(f"🗺 {r['mapa']}")
    if r.get("conta"):
        linha2.append(f"👤 {r['conta']}")
    if r.get("monstro_alvo"):
        linha2.append(f"🎯 {r['monstro_alvo']}")
    drops = r.get("drops")
    if isinstance(drops, dict):
        total_drops = sum(len(v) for v in drops.values() if isinstance(v, list))
        if total_drops:
            linha2.append(f"📦 {total_drops} item(ns)")
    texto = " · ".join(linha1)
    if linha2:
        texto += "\n┗ " + " · ".join(linha2)
    return texto


_ROTULO_TIPO_FORTALEZA = {"fosso_de_provas": "Fosso de Provas", "trono_khargath": "Trono de Khar'gath"}


def formatar_relatorio_modo(chave_lista: str, chave_total: str, label: str) -> str:
    """Detalhe de UM modo só ('aba' do relatório) — total histórico +
    últimas 5 execuções. A Fortaleza dos Orcs entra JUNTO na 'aba' da
    Masmorra (pedido do usuário 2026-07-20, mesma unificação já feita no
    relatório do painel) — sem isso, as execuções mais recentes da
    Fortaleza não apareciam aqui, só no painel (usuário reportou 2026-07-21:
    'não tá mostrando as últimas que fiz, que foi a masmorra nova dos
    orc... no painel mostra, mas no telegram não')."""
    if not os.path.exists(RELATORIO_FILE):
        return f"ℹ️ Ainda não tem relatório de {label} (nenhuma execução concluída)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    total = int(dados.get(chave_total, 0) or 0)
    lista = list(dados.get(chave_lista) or [])
    if chave_lista == "masmorras":
        total += int(dados.get("fortaleza_orcs_total", 0) or 0)
        try:
            for r in dados.get("fortaleza_orcs") or []:
                rr = dict(r)
                rr["mapa"] = _ROTULO_TIPO_FORTALEZA.get(rr.get("tipo"), "Fortaleza dos Orcs")
                lista.append(rr)
            lista.sort(key=lambda r: r.get("hora", ""))
        except Exception:
            pass   # se der problema só com os registros da Fortaleza, a Masmorra continua aparecendo
    if not total and not lista:
        return f"ℹ️ Ainda sem nenhuma execução de {label} registrada."
    linhas = [f"{label}\n\nTotal: *{total}* concluída(s)\n"]
    if lista:
        linhas.append("*Últimas execuções:*")
        for r in reversed(lista[-5:]):
            # sem "• " na frente — o registro agora ocupa 2 linhas próprias
            # (nº em negrito + linha ┗), o bullet só desalinhava a 2ª linha
            linhas.append(_formatar_registro_generico(r))
    return "\n".join(linhas)


_MODOS_DIARIO = [
    # (chave_contagem, chave_xp, chave_gold, rótulo)
    ("masmorras", "xp_masmorra", "gold_masmorra", "🏰 Masmorra"),
    ("cacadas", "xp_caca", "gold_caca", "⚔️ Caçada em Dupla"),
    ("templo_oasis", "xp_templo_oasis", "gold_templo_oasis", "🏛️ Templo do Oásis"),
    ("criptas", "xp_cripta", "gold_cripta", "🪦 Cripta"),
    ("caca_solo", "xp_caca_solo", "gold_caca_solo", "🗡️ Caçada Solo"),
    ("missao_oasis", "xp_missao_oasis", "gold_missao_oasis", "🏜️ Missão Oásis"),
    ("fortaleza_orcs", "xp_fortaleza_orcs", "gold_fortaleza_orcs", "🏯 Fortaleza dos Orcs"),
    ("observado", "xp_observado", "gold_observado", "👁️ Observador"),
]


def _resumo_dia_legivel(dia: str, d: dict) -> list:
    """Monta as linhas de UM dia, organizadas por conteúdo com nomes
    legíveis — em vez de despejar as chaves cruas do diario (pedido do
    usuário 2026-07-21: 'deixe esse relatório mais arrumado e
    entendível'). 'itens_hoje' (o despejo de TODOS os itens dropados,
    um dicionário aninhado enorme) fica de fora — já tem lugar próprio,
    o botão '📦 Drops de hoje'."""
    linhas = [f"\n*{dia}*"]
    algum_conteudo = False
    for chave_n, chave_xp, chave_gold, rotulo in _MODOS_DIARIO:
        n = d.get(chave_n, 0)
        if not n:
            continue
        algum_conteudo = True
        xp = d.get(chave_xp, 0)
        gold = d.get(chave_gold, 0)
        linha = f"{rotulo}: *{n}* execuç{'ão' if n == 1 else 'ões'}"
        if xp:
            linha += f" · ⭐ {xp:,}".replace(",", ".") + " XP"
        if gold:
            linha += f" · 💰 {gold:,}".replace(",", ".") + " gold"
        linhas.append(linha)
    if not algum_conteudo:
        linhas.append("(nenhuma execução concluída)")
    martelo = d.get("martelo_magico", 0)
    if martelo:
        linhas.append(f"🔨 {martelo} Martelo(s) Mágico(s) da Nurmora")
    poeira = d.get("poeira_estelar", 0)
    if poeira:
        linhas.append(f"🌟 {poeira} Poeira(s) Estelar")
    por_conta = d.get("por_conta")
    if isinstance(por_conta, dict) and por_conta:
        linhas.append("Por conta:")
        for nome_conta, dc in sorted(por_conta.items()):
            if not isinstance(dc, dict):
                continue
            partes = []
            if dc.get("xp"):
                partes.append("⭐ " + f"{dc['xp']:,}".replace(",", ".") + " XP")
            if dc.get("gold"):
                partes.append("💰 " + f"{dc['gold']:,}".replace(",", ".") + "g")
            if dc.get("dano"):
                partes.append("⚔️ " + f"{dc['dano']:,}".replace(",", ".") + " dano")
            if dc.get("mortes"):
                partes.append(f"💀 {dc['mortes']}")
            if partes:
                linhas.append(f"  • *{nome_conta}* — " + " · ".join(partes))
    return linhas


def formatar_relatorio_dia(dias: int = 5) -> str:
    """'aba' Por dia — resumo diário (últimos {dias} dias com dado), igual
    ao que já existe dentro do resumo geral pra HOJE, só que aqui olhando
    pra trás também, não só o dia de hoje."""
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída registrada)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    diario = dados.get("diario") or {}
    if not diario:
        return "ℹ️ Relatório existe, mas ainda sem nenhum dia registrado."
    linhas = ["📅 *Por dia*"]
    for dia in sorted(diario.keys(), reverse=True)[:dias]:
        d = diario.get(dia) or {}
        if not d:
            continue
        linhas.extend(_resumo_dia_legivel(dia, d))
    if len(linhas) == 1:
        return "ℹ️ Relatório existe, mas nenhum dos últimos dias tem dado."
    return "\n".join(linhas)


# ---------------------------------------------------------------------
#  📉 Gráfico do dia / 🏅 Comparativo de contas / 🔎 /drop
#  (pedidos do usuário 2026-07-21)
# ---------------------------------------------------------------------

# Todas as listas de execuções do relatorio.json (chave, rótulo) — usadas
# pelo gráfico por hora e pela busca de drop. Mesmas chaves que o hunter.py
# grava em registrar_masmorra/registrar_caca_dupla/etc.
_LISTAS_REGISTROS = [
    ("masmorras", "🏰 Masmorra"),
    ("fortaleza_orcs", "🏯 Fortaleza dos Orcs"),
    ("cacadas", "⚔️ Caçada em Dupla"),
    ("temploses", "🏛️ Templo do Oásis"),
    ("criptas", "🪦 Cripta"),
    ("caca_solo", "🗡️ Caçada Solo"),
    ("missao_oasis", "🏜️ Missão Oásis"),
]

GRAFICO_DIA_PNG = os.path.join(BASE, "grafico_dia.png")
GRAFICO_CONTAS_PNG = os.path.join(BASE, "grafico_contas.png")
GRAFICO_HEATMAP_PNG = os.path.join(BASE, "grafico_heatmap.png")
GRAFICO_SEMANAL_PNG = os.path.join(BASE, "grafico_semanal.png")
_DIAS_SEMANA_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def _ler_relatorio_ou_none():
    if not os.path.exists(RELATORIO_FILE):
        return None
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return None


def gerar_grafico_dia():
    """Gera um PNG com XP/hora e execuções/hora de HOJE (todos os modos
    somados), com as barras de ONTEM em cinza atrás pra comparação (pedido
    do usuário 2026-07-21: "adiciona esse comparativo do dia anterior") —
    devolve (caminho_png, legenda) ou (None, mensagem_de_erro).
    matplotlib é dependência OPCIONAL: se não estiver instalado, devolve
    uma mensagem explicando como instalar, sem quebrar nada (o resto do
    controle não usa matplotlib pra nada)."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return None, "ℹ️ Ainda não tem relatório pra desenhar (ou não consegui ler)."
    hoje_ddmm = time.strftime("%d/%m")
    ontem_ddmm = time.strftime("%d/%m", time.localtime(time.time() - 86400))

    def _por_hora(ddmm):
        xp_h, ex_h = [0] * 24, [0] * 24
        for chave, _rotulo in _LISTAS_REGISTROS:
            for r in dados.get(chave) or []:
                hora = r.get("hora", "")            # formato "dd/mm HH:MM"
                if not hora.startswith(ddmm + " "):
                    continue
                try:
                    h = int(hora[6:8])
                except (ValueError, IndexError):
                    continue
                ex_h[h] += 1
                xp = r.get("xp_total")
                if isinstance(xp, (int, float)):
                    xp_h[h] += int(xp)
        return xp_h, ex_h

    xp_hora, exec_hora = _por_hora(hoje_ddmm)
    xp_ontem, exec_ontem = _por_hora(ontem_ddmm)
    total_exec = sum(exec_hora)
    if not total_exec:
        return None, "ℹ️ Nenhuma execução concluída hoje ainda — sem o que desenhar."
    try:
        import matplotlib
        matplotlib.use("Agg")   # backend sem janela — só salva o arquivo
        import matplotlib.pyplot as plt
    except ImportError:
        # Mensagem AUTODIAGNOSTICÁVEL (2026-07-21: usuário instalou via apt
        # e "ele diz que ainda precisa do matplotlib" — o controle estava
        # rodando noutro Python, ex: venv, que não enxerga os pacotes do
        # sistema). Mostra QUAL interpretador este processo usa e o comando
        # exato pra instalar NELE — sem precisar adivinhar.
        py = sys.executable or "python3"
        return None, ("⚠️ O gráfico precisa do *matplotlib*, e ele não está "
                      "disponível no Python em que o controle está rodando:\n"
                      f"`{py}`\n\n"
                      "Instala direto nesse interpretador:\n"
                      f"`{py} -m pip install matplotlib`\n\n"
                      "(se esse caminho for de uma venv, o pip dela instala "
                      "sem reclamar do 'externally-managed'; depois é só "
                      "clicar de novo — não precisa reiniciar nada)")
    try:
        horas = list(range(24))
        tem_ontem = any(exec_ontem)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)
        if tem_ontem:
            # ontem por baixo (cinza, barra larga); hoje por cima (colorida,
            # mais estreita) — dá pra ver os dois sem um esconder o outro
            ax1.bar(horas, [x / 1000 for x in xp_ontem], width=0.8,
                    color="#c9c9c9", label=f"ontem ({ontem_ddmm})")
            ax2.bar(horas, exec_ontem, width=0.8, color="#c9c9c9",
                    label=f"ontem ({ontem_ddmm})")
        ax1.bar(horas, [x / 1000 for x in xp_hora], width=0.5,
                color="#4a90d9", label=f"hoje ({hoje_ddmm})")
        ax1.set_ylabel("XP (milhares)")
        ax1.set_title(f"TofuBot — {hoje_ddmm} vs {ontem_ddmm} · XP e execuções por hora"
                      if tem_ontem else
                      f"TofuBot — {hoje_ddmm} · XP e execuções por hora")
        ax1.grid(axis="y", alpha=0.3)
        ax1.legend(loc="upper right", fontsize=8)
        ax2.bar(horas, exec_hora, width=0.5, color="#78b159", label=f"hoje ({hoje_ddmm})")
        ax2.set_ylabel("Execuções")
        ax2.set_xlabel("Hora do dia")
        ax2.set_xticks(range(0, 24, 2))
        ax2.grid(axis="y", alpha=0.3)
        ax2.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(GRAFICO_DIA_PNG, dpi=110)
        plt.close(fig)
    except Exception as e:
        return None, f"⚠️ Deu erro gerando o gráfico: {e}"
    total_xp = sum(xp_hora)
    legenda = (f"📉 Hoje até agora: *{total_exec}* execuções · "
               + f"⭐ {total_xp:,}".replace(",", ".") + " XP")
    if any(exec_ontem):
        legenda += (f"\nOntem (dia todo): *{sum(exec_ontem)}* execuções · "
                    + f"⭐ {sum(xp_ontem):,}".replace(",", ".") + " XP")
    return GRAFICO_DIA_PNG, legenda


def _xp_gold_por_conta_no_dia(diario: dict, dia: str) -> dict:
    """{conta: (xp, gold)} de UM dia — 0,0 quando não tem dado."""
    resultado = {}
    por_conta = (diario.get(dia) or {}).get("por_conta")
    if isinstance(por_conta, dict):
        for nome, dc in por_conta.items():
            if isinstance(dc, dict):
                resultado[nome] = (int(dc.get("xp", 0) or 0), int(dc.get("gold", 0) or 0))
    return resultado


_MEDALHAS = ["🥇", "🥈", "🥉"]


def formatar_comparativo_contas() -> str:
    """'🏅 Comparativo contas' (pedido do usuário 2026-07-21): ranking de
    XP por conta HOJE com seta ▲/▼ comparando com ONTEM, mais o acumulado
    dos últimos 7 dias — pra ver de relance quem tá rendendo e quem caiu."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída registrada)."
    diario = dados.get("diario") or {}
    if not diario:
        return "ℹ️ Relatório existe, mas ainda sem nenhum dia registrado."
    hoje = time.strftime("%Y-%m-%d")
    ontem = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    de_hoje = _xp_gold_por_conta_no_dia(diario, hoje)
    de_ontem = _xp_gold_por_conta_no_dia(diario, ontem)
    linhas = ["🏅 *Comparativo de contas*"]
    if de_hoje:
        linhas.append("\n*Hoje vs ontem — XP:*")
        ranking = sorted(de_hoje.items(), key=lambda kv: -kv[1][0])
        for i, (nome, (xp, _gold)) in enumerate(ranking):
            pos = _MEDALHAS[i] if i < 3 else f"{i + 1}º"
            linha = f"{pos} *{nome}* — ⭐ " + f"{xp:,}".replace(",", ".")
            xp_ontem = de_ontem.get(nome, (0, 0))[0]
            if xp_ontem:
                delta_pct = round((xp - xp_ontem) * 100 / xp_ontem)
                if delta_pct > 0:
                    linha += f" (▲ +{delta_pct}%)"
                elif delta_pct < 0:
                    linha += f" (▼ {delta_pct}%)"
                else:
                    linha += " (= igual a ontem)"
            else:
                linha += " (sem dado de ontem)"
            linhas.append(linha)
    else:
        linhas.append("\n_(nenhuma conta com dado hoje ainda)_")
    # Acumulado dos últimos 7 dias com dado
    acumulado = {}
    for dia in sorted(diario.keys(), reverse=True)[:7]:
        for nome, (xp, gold) in _xp_gold_por_conta_no_dia(diario, dia).items():
            xp_ac, gold_ac = acumulado.get(nome, (0, 0))
            acumulado[nome] = (xp_ac + xp, gold_ac + gold)
    if acumulado:
        linhas.append("\n*Últimos 7 dias — acumulado:*")
        ranking7 = sorted(acumulado.items(), key=lambda kv: -kv[1][0])
        for i, (nome, (xp, gold)) in enumerate(ranking7):
            pos = _MEDALHAS[i] if i < 3 else f"{i + 1}º"
            linhas.append(f"{pos} *{nome}* — ⭐ " + f"{xp:,}".replace(",", ".")
                          + " XP · 💰 " + f"{gold:,}".replace(",", ".") + "g")
    return "\n".join(linhas)


# Listas de execuções concluídas em relatorio.json, uma por tipo de
# conteúdo (ver registrar_masmorra/registrar_cacada/etc no hunter.py) — cada
# registro tem 'hora' ("DD/MM HH:MM"), 'xp_total', 'duracao_segundos'
# (quando rastreado) e o gold em 'gold' (dict por conta) OU 'gold_total'
# (Cripta/Fortaleza, que dão o mesmo prêmio pra conta inteira).
_CONTEUDO_LISTAS_EFICIENCIA = [
    ("masmorras", "🏰 Masmorra"),
    ("cacadas", "⚔️ Caçada em Dupla"),
    ("temploses", "🏛️ Templo do Oásis"),
    ("criptas", "🕸️ Cripta"),
    ("fortaleza_orcs", "🏯 Fortaleza dos Orcs"),
    ("caca_solo", "🏹 Caçada Solo"),
]


def _gold_do_registro(r: dict) -> int:
    if "gold_total" in r:
        return int(r.get("gold_total") or 0)
    return sum((r.get("gold") or {}).values())


def _fmt_num(n) -> str:
    return f"{round(n):,}".replace(",", ".")


def formatar_eficiencia_por_conteudo() -> str:
    """'⚡ Eficiência XP/hora e Gold/hora por conteúdo' (pedido do usuário
    2026-07-25: "quero essa eficiência xp/hora gold/hora e também por
    minuto... e faça por conteúdo, não por conta") — diferente do
    Comparativo (que agrupa por CONTA), este agrupa pelas execuções de HOJE
    de cada TIPO de conteúdo (Masmorra/Caçada em Dupla/Templo/Cripta/
    Fortaleza/Caçada Solo), somando o XP e Gold de TODAS as contas
    envolvidas e dividindo pelo tempo real gasto (soma de 'duracao_segundos'
    de cada execução concluída hoje — não uma média genérica, o tempo de
    VERDADE registrado por execução), mostrando a taxa tanto por HORA quanto
    por MINUTO lado a lado. A Missão do Oásis não tem duração rastreada por
    execução (o XP/gold dela é somado por KILL, não por run concluída — ver
    registrar_missao_oasis_xp), então entra só com o total bruto do dia, sem
    taxa. A linha 'Geral' no fim soma tudo (todas as contas e conteúdos
    juntos)."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída registrada)."
    hoje_ddmm = time.strftime("%d/%m")
    resultados = []
    for chave, rotulo in _CONTEUDO_LISTAS_EFICIENCIA:
        registros_hoje = [r for r in (dados.get(chave) or [])
                          if (r.get("hora") or "").startswith(hoje_ddmm)]
        if not registros_hoje:
            continue
        xp = sum(r.get("xp_total", 0) for r in registros_hoje)
        gold = sum(_gold_do_registro(r) for r in registros_hoje)
        seg = sum(r.get("duracao_segundos") or 0 for r in registros_hoje)
        resultados.append((rotulo, xp, gold, seg, len(registros_hoje)))
    diario_hoje = (dados.get("diario") or {}).get(time.strftime("%Y-%m-%d")) or {}
    xp_mo = diario_hoje.get("xp_missao_oasis", 0)
    gold_mo = diario_hoje.get("gold_missao_oasis", 0)
    if not resultados and not xp_mo and not gold_mo:
        return "ℹ️ Ainda sem execução concluída hoje pra calcular eficiência."

    def _taxa_xp_hora(r):
        _, xp, _gold, seg, _n = r
        return (xp / (seg / 3600)) if seg else 0

    resultados.sort(key=_taxa_xp_hora, reverse=True)
    linhas = ["⚡ *Eficiência por conteúdo* (hoje)", ""]
    total_xp, total_gold, total_seg = xp_mo, gold_mo, 0
    for rotulo, xp, gold, seg, n in resultados:
        total_xp += xp
        total_gold += gold
        total_seg += seg
        if seg > 0:
            horas, minutos = seg / 3600, seg / 60
            linhas.append(f"{rotulo} — ⭐ {_fmt_num(xp / horas)}/h ({_fmt_num(xp / minutos)}/min) · "
                          f"💰 {_fmt_num(gold / horas)}/h ({_fmt_num(gold / minutos)}/min)"
                          f"\n   _({n}x hoje, {_formatar_duracao(seg)} rastreados)_")
        else:
            linhas.append(f"{rotulo} — ⭐ {_fmt_num(xp)} XP · 💰 {_fmt_num(gold)}g"
                          f"\n   _({n}x hoje, sem tempo rastreado)_")
    if xp_mo or gold_mo:
        linhas.append(f"🐫 Missão Oásis — ⭐ {_fmt_num(xp_mo)} XP · 💰 {_fmt_num(gold_mo)}g"
                      f"\n   _(sem tempo rastreado — XP/gold é por kill, não por execução)_")
    linhas.append("")
    if total_seg > 0:
        horas_t, minutos_t = total_seg / 3600, total_seg / 60
        linhas.append(f"📊 *Geral (todas as contas e conteúdos)* — "
                      f"⭐ {_fmt_num(total_xp / horas_t)}/h ({_fmt_num(total_xp / minutos_t)}/min) · "
                      f"💰 {_fmt_num(total_gold / horas_t)}/h ({_fmt_num(total_gold / minutos_t)}/min)"
                      f"\n_({_formatar_duracao(total_seg)} rastreados — Missão Oásis somada no total, "
                      f"sem entrar na taxa)_")
    else:
        linhas.append(f"📊 *Geral* — ⭐ {_fmt_num(total_xp)} XP · 💰 {_fmt_num(total_gold)}g")
    return "\n".join(linhas)


def gerar_grafico_contas():
    """'📊 Gráfico por conta' (pedido do usuário 2026-07-23: "tem como fazer
    um gráfico também, por personagem, pra comparar entre eles?") — PNG com
    XP e Gold de HOJE por conta, lado a lado com ONTEM (mesma ideia do
    'Gráfico do dia', mas comparando CONTAS entre si em vez de HORAS do
    dia) — devolve (caminho_png, legenda) ou (None, mensagem_de_erro).
    Mesma dependência opcional de matplotlib do gráfico do dia."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return None, "ℹ️ Ainda não tem relatório pra desenhar (ou não consegui ler)."
    diario = dados.get("diario") or {}
    if not diario:
        return None, "ℹ️ Relatório existe, mas ainda sem nenhum dia registrado."
    hoje = time.strftime("%Y-%m-%d")
    ontem = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    hoje_ddmm = time.strftime("%d/%m")
    ontem_ddmm = time.strftime("%d/%m", time.localtime(time.time() - 86400))
    de_hoje = _xp_gold_por_conta_no_dia(diario, hoje)
    de_ontem = _xp_gold_por_conta_no_dia(diario, ontem)
    if not de_hoje:
        return None, "ℹ️ Nenhuma conta com dado hoje ainda — sem o que desenhar."
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        py = sys.executable or "python3"
        return None, ("⚠️ O gráfico precisa do *matplotlib*, e ele não está "
                      "disponível no Python em que o controle está rodando:\n"
                      f"`{py}`\n\n"
                      "Instala direto nesse interpretador:\n"
                      f"`{py} -m pip install matplotlib`")
    # Ordena as contas por XP de hoje (maior primeiro) — mesma ordem do
    # ranking em formatar_comparativo_contas, pra ficar consistente.
    nomes = [nome for nome, _ in sorted(de_hoje.items(), key=lambda kv: -kv[1][0])]
    xp_hoje = [de_hoje[n][0] for n in nomes]
    gold_hoje = [de_hoje[n][1] for n in nomes]
    xp_ontem = [de_ontem.get(n, (0, 0))[0] for n in nomes]
    gold_ontem = [de_ontem.get(n, (0, 0))[1] for n in nomes]
    tem_ontem = any(xp_ontem) or any(gold_ontem)
    try:
        x = list(range(len(nomes)))
        largura = 0.35 if tem_ontem else 0.5
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(max(6, len(nomes) * 1.3), 6))
        if tem_ontem:
            ax1.bar([i - largura / 2 for i in x], [v / 1000 for v in xp_ontem],
                    width=largura, color="#c9c9c9", label=f"ontem ({ontem_ddmm})")
            ax1.bar([i + largura / 2 for i in x], [v / 1000 for v in xp_hoje],
                    width=largura, color="#4a90d9", label=f"hoje ({hoje_ddmm})")
            ax2.bar([i - largura / 2 for i in x], [v / 1000 for v in gold_ontem],
                    width=largura, color="#c9c9c9", label=f"ontem ({ontem_ddmm})")
            ax2.bar([i + largura / 2 for i in x], [v / 1000 for v in gold_hoje],
                    width=largura, color="#e8a33d", label=f"hoje ({hoje_ddmm})")
        else:
            ax1.bar(x, [v / 1000 for v in xp_hoje], width=largura, color="#4a90d9",
                    label=f"hoje ({hoje_ddmm})")
            ax2.bar(x, [v / 1000 for v in gold_hoje], width=largura, color="#e8a33d",
                    label=f"hoje ({hoje_ddmm})")
        ax1.set_ylabel("XP (milhares)")
        ax1.set_title(f"TofuBot — comparativo por conta · {hoje_ddmm}"
                      + (f" vs {ontem_ddmm}" if tem_ontem else ""))
        ax1.set_xticks(x)
        ax1.set_xticklabels(nomes, rotation=20, ha="right")
        ax1.grid(axis="y", alpha=0.3)
        ax1.legend(loc="upper right", fontsize=8)
        ax2.set_ylabel("Gold (milhares)")
        ax2.set_xticks(x)
        ax2.set_xticklabels(nomes, rotation=20, ha="right")
        ax2.grid(axis="y", alpha=0.3)
        ax2.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(GRAFICO_CONTAS_PNG, dpi=110)
        plt.close(fig)
    except Exception as e:
        return None, f"⚠️ Deu erro gerando o gráfico: {e}"
    total_xp = sum(xp_hoje)
    legenda = (f"📊 {len(nomes)} conta(s) hoje · ⭐ " + f"{total_xp:,}".replace(",", ".") + " XP no total")
    return GRAFICO_CONTAS_PNG, legenda


def gerar_grafico_heatmap(dias_lookback: int = 28):
    """'🔥 Heatmap hora x dia' (pedido do usuário 2026-07-24) — PNG cruzando
    HORA do dia com DIA DA SEMANA, pra ver em que combinação o bot mais
    gera execuções (ex: "sexta à noite é quando mais roda"). Usa as
    listas cruas de execução (_LISTAS_REGISTROS, mesma fonte do 'Gráfico
    do dia'), já que só elas têm granularidade de hora — o 'diario' só
    guarda totais por dia. Só o ANO é inferido (o campo 'hora' grava só
    'dd/mm HH:MM'): assume o ano atual e, se a data cair no futuro (típico
    virada de ano, registro de dezembro sendo lido em janeiro), corrige
    pro ano anterior. Devolve (caminho_png, legenda) ou
    (None, mensagem_de_erro). Mesma dependência opcional de matplotlib dos
    outros gráficos."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return None, "ℹ️ Ainda não tem relatório pra desenhar (ou não consegui ler)."

    ano_atual = time.localtime().tm_year
    agora = time.time()
    limite_ts = agora - dias_lookback * 86400

    matriz = [[0] * 24 for _ in range(7)]   # [dia_semana 0=Seg..6=Dom][hora] -> execuções
    total_exec = 0
    for chave, _rotulo in _LISTAS_REGISTROS:
        for r in dados.get(chave) or []:
            hora_str = r.get("hora", "")    # formato "dd/mm HH:MM"
            if len(hora_str) < 11:
                continue
            try:
                dd, mm, hh = int(hora_str[0:2]), int(hora_str[3:5]), int(hora_str[6:8])
            except (ValueError, IndexError):
                continue
            try:
                ts = time.mktime((ano_atual, mm, dd, hh, 0, 0, 0, 0, -1))
            except ValueError:
                continue
            if ts > agora + 3600:   # data "no futuro" -> era do ano anterior
                try:
                    ts = time.mktime((ano_atual - 1, mm, dd, hh, 0, 0, 0, 0, -1))
                except ValueError:
                    continue
            if ts < limite_ts:
                continue
            dia_semana = time.localtime(ts).tm_wday
            matriz[dia_semana][hh] += 1
            total_exec += 1

    if not total_exec:
        return None, f"ℹ️ Nenhuma execução nos últimos {dias_lookback} dias — sem o que desenhar."
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        py = sys.executable or "python3"
        return None, ("⚠️ O gráfico precisa do *matplotlib*, e ele não está "
                      "disponível no Python em que o controle está rodando:\n"
                      f"`{py}`\n\n"
                      "Instala direto nesse interpretador:\n"
                      f"`{py} -m pip install matplotlib`")
    try:
        matriz_np = np.array(matriz)
        fig, ax = plt.subplots(figsize=(11, 4.5))
        im = ax.imshow(matriz_np, cmap="YlOrRd", aspect="auto")
        ax.set_xticks(range(24))
        ax.set_xticklabels(range(24), fontsize=7)
        ax.set_yticks(range(7))
        ax.set_yticklabels(_DIAS_SEMANA_PT)
        ax.set_xlabel("Hora do dia")
        ax.set_title(f"TofuBot — atividade por hora × dia da semana (últimos {dias_lookback} dias)")
        maior = matriz_np.max() or 1
        for i in range(7):
            for j in range(24):
                v = matriz_np[i, j]
                if v:
                    cor_texto = "white" if v > maior * 0.6 else "black"
                    ax.text(j, i, str(int(v)), ha="center", va="center",
                            fontsize=6, color=cor_texto)
        cbar = fig.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label("Execuções")
        fig.tight_layout()
        fig.savefig(GRAFICO_HEATMAP_PNG, dpi=110)
        plt.close(fig)
    except Exception as e:
        return None, f"⚠️ Deu erro gerando o gráfico: {e}"

    pico_dia, pico_hora, pico_valor = 0, 0, 0
    for i in range(7):
        for j in range(24):
            if matriz[i][j] > pico_valor:
                pico_dia, pico_hora, pico_valor = i, j, matriz[i][j]
    legenda = (f"🔥 {total_exec} execuções nos últimos {dias_lookback} dias\n"
               f"Pico de atividade: *{_DIAS_SEMANA_PT[pico_dia]}* às *{pico_hora}h* "
               f"({pico_valor} execuções)")
    return GRAFICO_HEATMAP_PNG, legenda


_CORES_RARIDADE_GRAFICO = {"lendario": "#e8a33d", "epico": "#e0c34a", "raro": "#9b59b6",
                            "incomum": "#4a90d9", "normal": "#78b159", None: "#b0b0b0"}


def gerar_grafico_semanal(dias: int = 7):
    """'🗓️ Dashboard semanal' (pedido do usuário 2026-07-24) — PNG com 4
    painéis num resumo só: XP por dia, Gold por dia, execuções por modo
    (semana somada) e drops por raridade (semana somada). Fonte:
    dados['diario'][dia] — totais já agregados pelo hunter.py dia a dia,
    inclusive 'itens_hoje' que (apesar do nome) é gravado por dia dentro
    do diario, não só pra hoje. Devolve (caminho_png, legenda) ou
    (None, mensagem_de_erro). Mesma dependência opcional de matplotlib
    dos outros gráficos."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return None, "ℹ️ Ainda não tem relatório pra desenhar (ou não consegui ler)."
    diario = dados.get("diario") or {}
    if not diario:
        return None, "ℹ️ Relatório existe, mas ainda sem nenhum dia registrado."

    dias_alvo = [time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
                 for i in range(dias - 1, -1, -1)]

    xp_por_dia, gold_por_dia = [], []
    exec_por_modo = {rotulo: 0 for _n, _xp, _g, rotulo in _MODOS_DIARIO}
    drops_por_raridade = {}
    algum_dado = False

    for dia_str in dias_alvo:
        d = diario.get(dia_str) or {}
        xp_dia = sum(d.get(chave_xp, 0) or 0 for _n, chave_xp, _g, _r in _MODOS_DIARIO)
        gold_dia = sum(d.get(chave_gold, 0) or 0 for _n, _xp, chave_gold, _r in _MODOS_DIARIO)
        xp_por_dia.append(xp_dia)
        gold_por_dia.append(gold_dia)
        if xp_dia or gold_dia:
            algum_dado = True
        for chave_n, _xp, _g, rotulo in _MODOS_DIARIO:
            exec_por_modo[rotulo] += d.get(chave_n, 0) or 0
        for _nome, info in (d.get("itens_hoje") or {}).items():
            raridade = info.get("raridade")
            drops_por_raridade[raridade] = drops_por_raridade.get(raridade, 0) + info.get("qtd", 0)

    if not algum_dado:
        return None, f"ℹ️ Nenhum dado nos últimos {dias} dias — sem o que desenhar."
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        py = sys.executable or "python3"
        return None, ("⚠️ O gráfico precisa do *matplotlib*, e ele não está "
                      "disponível no Python em que o controle está rodando:\n"
                      f"`{py}`\n\n"
                      "Instala direto nesse interpretador:\n"
                      f"`{py} -m pip install matplotlib`")

    rotulos_dias = [time.strftime("%d/%m", time.strptime(d, "%Y-%m-%d")) for d in dias_alvo]
    modos_ativos = sorted(((r, v) for r, v in exec_por_modo.items() if v > 0),
                          key=lambda kv: -kv[1])
    raridades_ativas = [(chave, drops_por_raridade[chave]) for chave, _e in _EMOJI_RARIDADE_ORDEM
                        if drops_por_raridade.get(chave)]

    try:
        fig, axs = plt.subplots(2, 2, figsize=(11, 8))
        ax_xp, ax_gold = axs[0]
        ax_modo, ax_raridade = axs[1]

        ax_xp.bar(rotulos_dias, [x / 1000 for x in xp_por_dia], color="#4a90d9")
        ax_xp.set_title("XP por dia")
        ax_xp.set_ylabel("XP (milhares)")
        ax_xp.grid(axis="y", alpha=0.3)
        ax_xp.tick_params(axis="x", rotation=30)

        ax_gold.bar(rotulos_dias, [g / 1000 for g in gold_por_dia], color="#e8a33d")
        ax_gold.set_title("Gold por dia")
        ax_gold.set_ylabel("Gold (milhares)")
        ax_gold.grid(axis="y", alpha=0.3)
        ax_gold.tick_params(axis="x", rotation=30)

        if modos_ativos:
            # Tira o emoji do rótulo (ex: "🏰 Masmorra" -> "Masmorra") só pra
            # exibir no eixo do gráfico — a fonte padrão do matplotlib não
            # tem esses glyphs e mostraria um quadrado no lugar.
            nomes_modo = [r.split(" ", 1)[1] if " " in r else r for r, _v in modos_ativos]
            vals_modo = [v for _r, v in modos_ativos]
            ax_modo.barh(nomes_modo, vals_modo, color="#78b159")
            ax_modo.invert_yaxis()
            ax_modo.set_title("Execuções por modo (semana)")
            ax_modo.grid(axis="x", alpha=0.3)
            ax_modo.tick_params(axis="y", labelsize=8)
        else:
            ax_modo.axis("off")
            ax_modo.text(0.5, 0.5, "(sem execuções)", ha="center", va="center")

        if raridades_ativas:
            labels = [(c or "sem raridade") for c, _v in raridades_ativas]
            valores = [v for _c, v in raridades_ativas]
            cores = [_CORES_RARIDADE_GRAFICO.get(c, "#b0b0b0") for c, _v in raridades_ativas]
            ax_raridade.pie(valores, labels=labels, autopct="%1.0f%%",
                            colors=cores, textprops={"fontsize": 8})
            ax_raridade.set_title("Drops por raridade (semana)")
        else:
            ax_raridade.axis("off")
            ax_raridade.text(0.5, 0.5, "(sem drops)", ha="center", va="center")

        fig.suptitle(f"TofuBot — resumo da semana ({rotulos_dias[0]} a {rotulos_dias[-1]})",
                     fontsize=13, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        fig.savefig(GRAFICO_SEMANAL_PNG, dpi=110)
        plt.close(fig)
    except Exception as e:
        return None, f"⚠️ Deu erro gerando o gráfico: {e}"

    total_xp = sum(xp_por_dia)
    total_gold = sum(gold_por_dia)
    total_execs = sum(exec_por_modo.values())
    legenda = (f"🗓️ Semana ({rotulos_dias[0]} a {rotulos_dias[-1]}): *{total_execs}* execuções · "
               "⭐ " + f"{total_xp:,}".replace(",", ".") + " XP · "
               "💰 " + f"{total_gold:,}".replace(",", ".") + "g")
    return GRAFICO_SEMANAL_PNG, legenda


def _sem_acento(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                   if unicodedata.category(c) != "Mn")


def _texto_busca(texto: str) -> str:
    """Normalização AGRESSIVA pra comparar nomes de item (2026-07-21,
    usuário: '/drop não falou quem dropou' — a Couraça de Zul'gor estava no
    banco mas a ocorrência não era achada nas execuções): além de
    minúsculas/sem acento, joga fora TUDO que não for letra/número/espaço —
    apóstrofo tipográfico (') vs reto ('), emoji de raridade na frente,
    sufixos tipo '(ALMA)' e pontuação deixavam a comparação exata falhar
    entre o nome do banco e o nome gravado na lista de drops da execução."""
    limpo = "".join(c if c.isalnum() or c.isspace() else " "
                    for c in _sem_acento(texto))
    return " ".join(limpo.split())


def _chave_ordenacao_hora(hora: str) -> tuple:
    """'dd/mm HH:MM' -> (mm, dd, 'HH:MM') pra comparar cronologicamente —
    string crua ordenaria errado entre meses ('01/08' < '31/07' no
    lexicográfico). Virada de ano (dez->jan) ainda compara errado, mas o
    custo disso é só apontar uma 'última vez' de dezembro em janeiro —
    irrelevante pro propósito."""
    try:
        return (hora[3:5], hora[0:2], hora[6:])
    except Exception:
        return ("", "", "")


def _ocorrencias_drop(dados: dict, nome_item: str, max_ocorrencias: int = 3) -> list:
    """As ocorrências mais RECENTES do item nas listas de execuções —
    lista de 'dd/mm HH:MM · 👤 conta · Modo', da mais nova pra mais velha.
    Comparação via _texto_busca com contenção nos 2 sentidos (o nome no
    registro pode vir com sufixo/emoji a mais OU a menos que o do banco)."""
    alvo = _texto_busca(nome_item)
    if not alvo:
        return []
    achadas = []
    for chave, rotulo in _LISTAS_REGISTROS:
        for r in dados.get(chave) or []:
            drops = r.get("drops")
            if not isinstance(drops, dict):
                continue
            for conta, itens in drops.items():
                if not isinstance(itens, list):
                    continue
                for item in itens:
                    if not isinstance(item, str):
                        continue
                    norm_item = _texto_busca(item)
                    if norm_item and (alvo in norm_item or norm_item in alvo):
                        achadas.append((r.get("hora", ""), conta, rotulo))
                        break   # 1 hit por conta neste registro já basta
    achadas.sort(key=lambda t: _chave_ordenacao_hora(t[0]), reverse=True)
    return [f"{hora} · 👤 {conta} · {rotulo}"
            for hora, conta, rotulo in achadas[:max_ocorrencias]]


def _dias_com_drop(dados: dict, nome_item: str, max_dias: int = 5) -> list:
    """Em quais DIAS o item dropou, com a quantidade — vem do 'itens_hoje'
    do diário (o registro por-dia que o hunter.py mantém), que cobre até
    drops cujo parse por-conta falhou nas listas de execução. Devolve tipo
    ['21/07: 1x', '16/07: 1x'], do mais recente pro mais antigo."""
    alvo = _texto_busca(nome_item)
    dias = []
    diario = dados.get("diario") or {}
    for dia in sorted(diario.keys(), reverse=True):
        itens = (diario.get(dia) or {}).get("itens_hoje") or {}
        if not isinstance(itens, dict):
            continue
        for nome, info in itens.items():
            norm_nome = _texto_busca(nome)
            if norm_nome and (alvo in norm_nome or norm_nome in alvo):
                qtd = info.get("qtd", 1) if isinstance(info, dict) else 1
                dias.append(f"{dia[8:10]}/{dia[5:7]}: {qtd}x")
                break
        if len(dias) >= max_dias:
            break
    return dias


def formatar_busca_drop(termo: str) -> str:
    """'/drop <termo>' (pedido do usuário 2026-07-21): procura no
    banco_itens (a base que cresce sozinha — ver hunter.py
    _registrar_itens_no_banco) por nome, sem ligar pra maiúscula/acento/
    pontuação, e mostra raridade, quantas vezes já dropou, os DIAS em que
    dropou, e as últimas ocorrências com QUEM dropou (conta/hora/modo)."""
    dados = _ler_relatorio_ou_none()
    if dados is None:
        return "ℹ️ Ainda não tem relatório (nenhum drop registrado)."
    banco = dados.get("banco_itens") or {}
    if not banco:
        return "ℹ️ O banco de itens ainda está vazio."
    alvo = _texto_busca(termo)
    achados = [(nome, info) for nome, info in banco.items()
               if isinstance(info, dict) and alvo in _texto_busca(nome)]
    if not achados:
        return f"🔎 Nenhum item parecido com *{termo}* no banco de itens."
    achados.sort(key=lambda kv: -(kv[1].get("vezes_visto", 0) or 0))
    linhas = [f"🔎 *Busca: {termo}* — {len(achados)} item(ns)"]
    for nome, info in achados[:5]:
        linhas.append("")
        emoji = info.get("emoji", "")
        raridade = info.get("raridade") or "?"
        linhas.append(f"{emoji} *{nome}* ({raridade})".strip())
        vezes = info.get("vezes_visto", 0)
        primeira = info.get("primeira_vez", "?")
        linhas.append(f"┗ dropou *{vezes}x* · 1ª vez: {primeira}")
        dias = _dias_com_drop(dados, nome)
        if dias:
            linhas.append(f"┗ dias: {' · '.join(dias)}")
        origens = info.get("origens") or []
        if origens:
            linhas.append(f"┗ onde: {', '.join(origens[:4])}")
        ocorrencias = _ocorrencias_drop(dados, nome)
        for oc in ocorrencias:
            linhas.append(f"┗ {oc}")
    if len(achados) > 5:
        linhas.append(f"\n_(+{len(achados) - 5} outros — refina o termo pra ver menos resultados)_")
    return "\n".join(linhas)


def formatar_ranking_dia() -> str:
    """'🏆 Ranking do dia' (pedido do usuário 2026-07-20): quem rendeu mais
    XP hoje entre as contas, com gold e mortes junto. Fonte:
    diario[hoje]['por_conta'] (já existia, alimenta a aba 'Por dia' do
    relatório — só faltava um jeito rápido de ver só o ranking)."""
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    hoje = time.strftime("%Y-%m-%d")
    por_conta = ((dados.get("diario") or {}).get(hoje) or {}).get("por_conta") or {}
    if not por_conta:
        return "🏆 *Ranking de hoje*\n\nAinda sem nada registrado hoje."
    ordenado = sorted(por_conta.items(), key=lambda kv: -(kv[1].get("xp", 0) or 0))
    medalhas = ["🥇", "🥈", "🥉"]
    linhas = ["🏆 *Ranking de hoje* (por XP)"]
    for i, (nome, d) in enumerate(ordenado):
        prefixo = medalhas[i] if i < 3 else f"{i + 1}º"
        linha = f"{prefixo} *{nome}* — ⭐ " + f"{d.get('xp', 0):,}".replace(",", ".") + " XP"
        if d.get("gold"):
            linha += " · 💰 " + f"{d['gold']:,}".replace(",", ".") + "g"
        if d.get("mortes"):
            linha += f" · 💀 {d['mortes']}"
        linhas.append(linha)
    return "\n".join(linhas)


_ROTULOS_MODO = {
    "masmorra": "🏰 Masmorra", "caca_dupla": "⚔️ Caçada em Dupla",
    "cripta": "🪦 Cripta", "caca_solo": "🗡️ Caçada Solo",
    "missao_oasis": "🏜️ Missão Oásis", "templo_oasis": "🏛️ Templo do Oásis",
    "fortaleza_orcs": "🏯 Fortaleza dos Orcs", "observador": "👁️ Observador",
}


def formatar_ajustes_atuais() -> str:
    """'📋 Ver ajustes atuais' (pedido do usuário 2026-07-21) — resumo rápido
    do que está configurado AGORA no settings.json, sem precisar abrir o
    painel no PC só pra conferir. Mostra o modo ativo, quantas contas e os
    principais limites desse modo específico (cada modo guarda os campos
    num lugar diferente — ver config.py)."""
    dados = _ler_settings()
    # 🧩 MULTI-CONTEÚDO (pedido do usuário 2026-07-22): se GRUPOS_CONTEUDO
    # estiver preenchido, o "modo ativo" de MODO_CONTEUDO nem entra em jogo
    # (ver hunter.py main()) — mostrar só ele aqui seria enganoso (ignoraria
    # os grupos rodando de verdade). Mostra um resumo por grupo em vez disso.
    grupos_multi = (dados.get("GRUPOS_CONTEUDO") or []) if dados.get("MULTI_CONTEUDO_ATIVO") else []
    if grupos_multi:
        linhas = ["📋 *Ajustes atuais*", "", f"🧩 *Multi-conteúdo ativo* — {len(grupos_multi)} grupo(s):"]
        for g in grupos_multi:
            nome = g.get("nome") or "Grupo"
            modo_g = g.get("modo") or "?"
            n_contas = len(g.get("contas") or [])
            linhas.append(f"• *{nome}* — {_ROTULOS_MODO.get(modo_g, modo_g)} · {n_contas} conta(s)")
        return "\n".join(linhas)
    modo = dados.get("MODO_CONTEUDO") or "masmorra"
    linhas = ["📋 *Ajustes atuais*", "", f"Modo ativo: *{_ROTULOS_MODO.get(modo, modo)}*"]

    if modo == "masmorra":
        contas = dados.get("ACCOUNTS") or []
        ativas = [a for a in contas if a.get("ativa", True) and a.get("phone")]
        linhas.append(f"Contas ativas: *{len(ativas)}*")
        hp_min = int(round(float(dados.get("BETWEEN_DG_HEAL_RATIO", 0.85)) * 100))
        linhas.append(f"HP mín. pra iniciar a próxima: *{hp_min}%*")
        maxd = int(dados.get("MAX_DUNGEONS", 0) or 0)
        linhas.append(f"Quantas masmorras (limite): *{maxd if maxd else 'sem limite'}*")
        mapa = dados.get("MAPA_DESTINO") or "(fica onde está)"
        linhas.append(f"Masmorras: *{mapa}*")
    elif modo == "caca_dupla":
        cd = dados.get("CACA_DUPLA") or {}
        grupos = cd.get("grupos") or []
        n_duplas = sum(1 for g in grupos if all((c.get("phone") or "").strip() for c in g))
        linhas.append(f"Duplas configuradas: *{n_duplas}*")
        linhas.append(f"HP% poção em combate: *{cd.get('vida_min_pct', 40)}%*")
        maxc = int(cd.get("max_cacadas", 0) or 0)
        linhas.append(f"Quantas caçadas por dupla (limite): *{maxc if maxc else 'sem limite'}*")
        linhas.append(f"Andar máximo: *{cd.get('andar_maximo', 49)}*")
    elif modo == "cripta":
        cr = dados.get("CRIPTA") or {}
        linhas.append(f"Contas: *{len(cr.get('contas') or [])}*")
        linhas.append(f"Nível: *{cr.get('nivel', 'I')}*")
        linhas.append(f"Andar máximo: *{cr.get('andar_maximo', 10)}*")
        maxcr = int(cr.get("max_criptas", 0) or 0)
        linhas.append(f"Quantas criptas (limite): *{maxcr if maxcr else 'sem limite'}*")
    elif modo == "templo_oasis":
        to = dados.get("TEMPLO_OASIS") or {}
        grupos = to.get("grupos") or []
        n_duplas = sum(1 for g in grupos if all((c.get("phone") or "").strip() for c in g))
        linhas.append(f"Duplas configuradas: *{n_duplas}*")
        linhas.append(f"HP% poção padrão: *{to.get('vida_min_pct', 40)}%*")
        maxe = int(to.get("max_execucoes", 0) or 0)
        linhas.append(f"Quantas execuções por dupla (limite): *{maxe if maxe else 'sem limite'}*")
    elif modo == "fortaleza_orcs":
        fo = dados.get("FORTALEZA_ORCS") or {}
        tipo_label = "Fosso de Provas" if fo.get("tipo") != "trono_khargath" else "Trono de Khar'gath"
        linhas.append(f"Variante: *{tipo_label}*")
        linhas.append(f"Contas: *{len(fo.get('contas') or [])}*")
        maxe = int(fo.get("max_execucoes", 0) or 0)
        linhas.append(f"Quantas execuções (limite): *{maxe if maxe else 'sem limite'}*")
    elif modo == "caca_solo":
        cs = dados.get("CACA_SOLO") or {}
        contas = [c for c in (cs.get("contas") or []) if (c.get("phone") or "").strip()]
        linhas.append(f"Contas: *{len(contas)}*")
    elif modo == "missao_oasis":
        mo = dados.get("MISSAO_OASIS") or {}
        contas = [c for c in (mo.get("contas") or []) if (c.get("phone") or "").strip()]
        linhas.append(f"Contas: *{len(contas)}*")

    if os.path.exists(PARAR_NO_FIM_FLAG):
        linhas.append("")
        linhas.append("⏳ *Parar no fim* está programado.")
    return "\n".join(linhas)


def formatar_ranking_dano_dia() -> str:
    """'🥊 Ranking de dano' (pedido do usuário 2026-07-21: "faz uma
    somatória ao longo do dia, em todos os conteúdos") — soma o dano
    causado por cada conta em TODAS as execuções concluídas hoje, de
    QUALQUER conteúdo, numa tabela só. Mesma fonte que o Ranking de XP
    (diario[hoje]['por_conta']['dano']) — só que esse campo só existe pros
    conteúdos cuja tela final mostra 'Ranking de dano' (Masmorra e Templo
    do Oásis, por enquanto — Caçada em Dupla/Cripta/Fortaleza dos Orcs
    ainda não alimentam isso, a tela final delas não traz essa informação
    pra capturar)."""
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    hoje = time.strftime("%Y-%m-%d")
    por_conta = ((dados.get("diario") or {}).get(hoje) or {}).get("por_conta") or {}
    com_dano = {nome: d for nome, d in por_conta.items() if d.get("dano")}
    if not com_dano:
        return ("🥊 *Ranking de dano de hoje*\n\nAinda sem nada registrado hoje "
                "(só conta dano de Masmorra e Templo do Oásis por enquanto).")
    ordenado = sorted(com_dano.items(), key=lambda kv: -(kv[1].get("dano", 0) or 0))
    medalhas = ["🥇", "🥈", "🥉"]
    linhas = ["🥊 *Ranking de dano de hoje* (soma de todos os conteúdos)"]
    for i, (nome, d) in enumerate(ordenado):
        prefixo = medalhas[i] if i < 3 else f"{i + 1}º"
        linhas.append(f"{prefixo} *{nome}* — ⚔️ " + f"{d.get('dano', 0):,}".replace(",", ".") + " dano")
    return "\n".join(linhas)


_LABEL_TEMPO_MEDIO = {
    "masmorra": "🏰 Masmorra", "caca_dupla": "⚔️ Caçada em Dupla",
    "cripta": "🪦 Cripta", "templo_oasis": "🏛️ Templo do Oásis",
    "fortaleza_orcs": "🏯 Fortaleza dos Orcs",
}


def _label_chave_tempo(chave: str) -> str:
    if chave in _LABEL_TEMPO_MEDIO:
        return _LABEL_TEMPO_MEDIO[chave]
    if chave.startswith("masmorra:"):
        return f"🏰 Masmorra ({chave.split(':', 1)[1]})"
    if chave.startswith("caca_solo:"):
        return f"🗡️ Caçada Solo — {chave.split(':', 1)[1]}"
    return chave


def formatar_tempo_medio() -> str:
    """'⏱️ Tempo médio por conteúdo' (pedido do usuário 2026-07-20). Fonte:
    relatorio.json['tempo_medio'] — uma janela rolante (últimas N, ver
    config.MEDIA_JANELA) de durações por chave (ver _atualizar_tempo_medio
    no hunter.py), que já alimenta a estimativa de tempo do painel; aqui só
    mostra a média calculada, por conteúdo."""
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    tm = dados.get("tempo_medio") or {}
    if not tm:
        return ("⏱️ *Tempo médio por conteúdo*\n\nAinda sem dado suficiente "
                "(precisa de pelo menos 1 execução concluída com tempo medido).")
    linhas = ["⏱️ *Tempo médio por conteúdo*", "_(baseado nas últimas execuções de cada um)_"]
    for chave, lst in sorted(tm.items()):
        if not lst:
            continue
        media = sum(lst) / len(lst)
        linhas.append(f"• {_label_chave_tempo(chave)}: {_formatar_duracao(media)} "
                       f"(média de {len(lst)})")
    return "\n".join(linhas)


def formatar_estoque_pocoes() -> str:
    """'🧪 Estoque de consumíveis por conta' (pedido do usuário 2026-07-20).
    Fonte: status.json['estoque_pocao_vida'] (contado por contar_pocoes_
    vida/pocoes_vida_ok no hunter.py, sempre antes de iniciar masmorra/
    caçada — já existia só em log, nunca tinha sido gravado em lugar
    nenhum antes). Marca com ⚠️ quem estiver abaixo do mínimo configurado
    (config.POCAO_VIDA_MINIMA)."""
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Ainda sem dado de status (bot rodando há pouco tempo, ou parado)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    minimo = getattr(config, "MASMORRA_POCAO_VIDA_MINIMA", 50)
    linhas = ["🧪 *Estoque de consumíveis por conta*"]
    algum = False
    for nome, d in sorted(dados.items()):
        if not isinstance(d, dict) or d.get("estoque_pocao_vida") is None:
            continue
        algum = True
        qtd = d["estoque_pocao_vida"]
        aviso = " ⚠️ *baixo*" if qtd < minimo else ""
        linhas.append(f"👤 *{nome}* — 🧪 {qtd} Poção(ões) de Vida{aviso}")
    if not algum:
        return ("ℹ️ Ainda sem estoque de poção lido de nenhuma conta (é checado antes "
                 "de cada masmorra/caçada — pode levar um tempinho pra aparecer).")
    return "\n".join(linhas)


def _barra_progresso(pct: float, largura: int = 10) -> str:
    """Barra de texto em blocos Unicode (0.0-1.0 -> 'X%') — pedido do
    usuário 2026-07-25 ("já que já tem [o progresso de nível], coloca uma
    barrinha, pra ficar mais bonito"). O Telegram não desenha canvas feito o
    painel Tkinter (_desenhar_barra_jogo), então isso é o equivalente em
    texto puro, sempre monoespaçado dentro de um bloco ``` no chamador."""
    pct = max(0.0, min(1.0, pct))
    cheios = round(pct * largura)
    return "▓" * cheios + "░" * (largura - cheios) + f" {pct:.0%}"


def formatar_progresso_nivel() -> str:
    """'📈 Progresso de nível por conta' (pedido do usuário 2026-07-20).
    Fonte: status.json['nivel']/['xp_faltam']/['eta_proximo_nivel_seg'] —
    já calculados periodicamente pelo hunter.py (atualizar_perfil_e_
    estimativa) pro painel; aqui só formata pro Telegram. Barra de progresso
    (xp_pct_nivel) adicionada 2026-07-25, mesma fonte que o painel Tkinter
    já usava pra desenhar a barra de XP."""
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Ainda sem dado de status (bot rodando há pouco tempo, ou parado)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    linhas = ["📈 *Progresso de nível por conta*"]
    algum = False
    for nome, d in sorted(dados.items()):
        if not isinstance(d, dict) or d.get("nivel") is None:
            continue
        algum = True
        linha = f"👤 *{nome}* — ⭐ Lv{d['nivel']}"
        if d.get("xp_faltam") is not None:
            linha += " · faltam " + f"{d['xp_faltam']:,}".replace(",", ".") + " XP"
        xp_pct = d.get("xp_pct_nivel")
        if xp_pct is not None:
            linha += f"\n   `{_barra_progresso(xp_pct)}`"
        eta = d.get("eta_proximo_nivel_seg")
        if eta:
            linha += f"\n   ⏳ estimado em {_formatar_duracao(eta)}"
        linhas.append(linha)
    if not algum:
        return ("ℹ️ Ainda sem nível lido de nenhuma conta (é lido periodicamente do "
                 "Perfil — pode levar um tempinho pra aparecer).")
    return "\n".join(linhas)


def formatar_xp_total() -> str:
    """'⭐ XP total' (pedido do usuário 2026-07-22: "quero consultar o xp
    total do personagem") — mesmo número que o jogo mostra em 'XP:' no
    Perfil (ver ler_perfil/PERFIL_XP_RE no hunter.py), por conta. Fonte:
    status.json['xp_atual'] — lido periodicamente junto com nível/xp_faltam,
    sem custo extra de navegação."""
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Ainda sem dado de status (bot rodando há pouco tempo, ou parado)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    linhas = ["⭐ *XP total por conta*"]
    algum = False
    for nome, d in sorted(dados.items()):
        if not isinstance(d, dict) or d.get("xp_atual") is None:
            continue
        algum = True
        linha = f"👤 *{nome}*"
        if d.get("nivel") is not None:
            linha += f" — Lv{d['nivel']}"
        linha += " · " + f"{d['xp_atual']:,}".replace(",", ".") + " XP"
        linhas.append(linha)
    if not algum:
        return ("ℹ️ Ainda sem XP lido de nenhuma conta (é lido periodicamente do "
                 "Perfil — pode levar um tempinho pra aparecer).")
    return "\n".join(linhas)


def formatar_xp_real_hoje() -> str:
    """'📊 XP real hoje' (pedido do usuário 2026-07-23: "o rank de xp...
    varia muito do resultado real... entre eles: mortes, meu amigo usa meus
    chars às vezes... tem como fazer uma leitura à meia-noite e ir
    atualizando com base naquele valor?") — diferente do 'Ranking do dia'
    (que soma só o XP das execuções que o bot CONCLUIU), este mostra o
    ganho/perda REAL do personagem: XP atual (status.json) menos o XP
    capturado como referência hoje (relatorio.json['xp_baseline'] — ver
    _capturar_xp_baseline_se_novo_dia no hunter.py). Reflete morte (perde
    XP), uso manual por outra pessoa, ou qualquer outra coisa que mude o XP
    do personagem — não só o que o bot fez."""
    if not os.path.exists(STATUS_FILE):
        return "ℹ️ Ainda sem dado de status (bot rodando há pouco tempo, ou parado)."
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o status.json (arquivo corrompido/em uso)."
    dados_rel = _ler_relatorio_ou_none() or {}
    baseline_todos = dados_rel.get("xp_baseline") or {}
    hoje = time.strftime("%Y-%m-%d")
    linhas = ["📊 *XP real hoje* (ganho/perda de verdade — inclui mortes, uso "
             "manual, etc, não só o que o bot completou)", ""]
    resultados = []
    for nome, d in status.items():
        if not isinstance(d, dict) or d.get("xp_atual") is None:
            continue
        baseline = baseline_todos.get(nome)
        if not baseline or baseline.get("data") != hoje:
            continue   # baseline de hoje ainda não capturado pra essa conta
        diff = d["xp_atual"] - baseline["xp_atual"]
        resultados.append((nome, diff, d.get("nivel")))
    if not resultados:
        return ("ℹ️ Ainda sem baseline de hoje pra nenhuma conta — é capturado "
                "sozinho na 1ª leitura do Perfil depois da virada do dia (pode "
                "levar um tempinho, dependendo de quando cada conta reler o "
                "Perfil de novo).")
    resultados.sort(key=lambda x: -x[1])
    for nome, diff, nivel in resultados:
        sinal = "+" if diff >= 0 else ""
        linha = f"👤 *{nome}*"
        if nivel is not None:
            linha += f" — Lv{nivel}"
        linha += f" · {sinal}" + f"{diff:,}".replace(",", ".") + " XP"
        linhas.append(linha)
    return "\n".join(linhas)


_EMOJI_RARIDADE_ORDEM = [("lendario", "🟠"), ("epico", "🟡"), ("raro", "🟣"),
                          ("incomum", "🔵"), ("normal", "🟢"), (None, "⚪")]
DROPS_POR_PAGINA = 15


def formatar_relatorio_dragao() -> str:
    """'🐲 Dragão de Cristal de Frost' (pedido do usuário 2026-07-23) —
    total de derrotas + % de drop de cada um dos 6 itens lendários que ele
    pode soltar. Diferente da notificação do amigo do usuário (avisa no
    ENCONTRO): aqui só conta DERROTA de verdade — se o dragão aparecer no
    último andar programado da caçada e a dupla sair sem matar, isso NÃO
    entra na conta (confirmado pelo usuário: "não adianta notificação pra
    esse dragão" só de avistamento, o % de drop tem que ser sobre quem foi
    derrotado mesmo)."""
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída registrada)."
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso)."
    d = dados.get("dragao_cristal_frost") or {}
    derrotas = int(d.get("derrotas", 0) or 0)
    if derrotas == 0:
        return ("🐲 *Dragão de Cristal de Frost*\n\n"
                "ℹ️ Ainda sem nenhuma derrota registrada.")
    drops = d.get("drops") or {}
    itens_conhecidos = ["Báculo do Dragão de Gelo", "Arco do Dragão de Cristal",
                       "Varinha do Dragão de Gelo", "Lança do Dragão Glacial",
                       "Lâmina do Dragão Glacial", "Machado do Dragão de Cristal"]
    linhas = ["🐲 *Dragão de Cristal de Frost*", "", f"Derrotas: *{derrotas}*", "",
              "*% de drop por item:*"]
    # Mostra os 6 itens conhecidos sempre (mesmo com 0 drops até agora, pra
    # ver de relance quais ainda faltam) + qualquer item NOVO que apareça
    # nos dados mas não esteja na lista conhecida (ex: se o jogo adicionar
    # mais variantes de drop no futuro).
    todos_itens = itens_conhecidos + [i for i in drops if i not in itens_conhecidos]
    for item in todos_itens:
        qtd = int(drops.get(item, 0) or 0)
        pct = (qtd / derrotas * 100) if derrotas else 0
        linhas.append(f"• {item}: *{qtd}x* ({pct:.1f}%)")
    total_drops = sum(int(v or 0) for v in drops.values())
    pct_algum = (total_drops / derrotas * 100) if derrotas else 0
    linhas.append(f"\nChance de dropar ALGO lendário por derrota: *{pct_algum:.1f}%*")
    return "\n".join(linhas)


def _tempo_rodando_bot() -> str:
    """Há quanto tempo o processo do bot ATUAL está rodando — usa a data de
    modificação de 'bot.pid' (gravado UMA vez, no exato momento em que o
    hunter.py sobe, dentro do bloco '__main__') como o início da sessão.
    Só faz sentido se o bot estiver de fato rodando agora (bot_rodando());
    devolve None caso contrário ou se o arquivo não existir/não puder ser
    lido, pra quem chama decidir se omite a linha."""
    if not bot_rodando():
        return None
    try:
        inicio = os.path.getmtime(BOT_PID_FILE)
    except OSError:
        return None
    return _formatar_duracao(time.time() - inicio)


def formatar_progresso_masmorra() -> str:
    """'🏰 Progresso da Masmorra' (pedido do usuário 2026-07-20): quantas
    masmorras já foram feitas NESTA SESSÃO (desde que o bot foi ligado
    agora) + progresso até o limite configurado (ex: 10/20) com tempo
    estimado até bater a meta, + HÁ QUANTO TEMPO O BOT ESTÁ RODANDO (pedido
    do usuário 2026-07-24: "só mostra a quantidade, mas não mostra o tempo
    que o bot tá rodando") + quantas hoje + um resumo dos drops de hoje.
    Fontes: estimativa.json (sessão — já gravado pelo hunter.py a cada
    masmorra concluída, ver _salvar_estimativa), bot.pid (início da sessão
    — ver _tempo_rodando_bot) e relatorio.json (hoje/drops — mesma fonte
    de 📅 Por dia / 📦 Drops de hoje).
    IMPORTANTE: os DROPS só existem agregados por DIA no relatório, não por
    sessão — se o bot foi ligado hoje mesmo (o caso comum), 'hoje' e
    'sessão' são a mesma coisa; se atravessou a virada do dia, os drops
    aqui cobrem só o dia de hoje, não a sessão inteira."""
    linhas = ["🏰 *Progresso da Masmorra*\n"]

    tempo_rodando = _tempo_rodando_bot()
    if tempo_rodando:
        linhas.append(f"🕒 Bot rodando há: *{tempo_rodando}*")

    est = {}
    if os.path.exists(ESTIMATIVA_FILE):
        try:
            with open(ESTIMATIVA_FILE, encoding="utf-8") as f:
                est = json.load(f) or {}
        except Exception:
            est = {}
    if est.get("modo") == "masmorra":
        feitas = est.get("feitas", 0)
        alvo = est.get("alvo", 0)
        media_seg = est.get("media_segundos")
        if alvo:
            linhas.append(f"▶️ Sessão atual: *{feitas}/{alvo}* masmorras")
            faltam = max(0, alvo - feitas)
            if faltam == 0:
                linhas.append("✅ Meta batida!")
            elif media_seg:
                linhas.append(f"⏳ Tempo estimado até o fim: ~{_formatar_duracao(faltam * media_seg)}")
        else:
            linhas.append(f"▶️ Sessão atual: *{feitas}* masmorra(s) (sem limite configurado)")
    else:
        linhas.append("ℹ️ Ainda sem masmorra concluída nesta sessão (ou o modo ativo não é Masmorra).")

    d = {}
    if os.path.exists(RELATORIO_FILE):
        try:
            with open(RELATORIO_FILE, encoding="utf-8") as f:
                rel = json.load(f) or {}
            hoje = time.strftime("%Y-%m-%d")
            d = (rel.get("diario") or {}).get(hoje) or {}
        except Exception:
            d = {}
    linhas.append(f"\n📅 Hoje: *{d.get('masmorras', 0)}* masmorra(s) concluída(s)")

    itens = d.get("itens_hoje") or {}
    if itens:
        ordem_raridade = {chave: i for i, (chave, _e) in enumerate(_EMOJI_RARIDADE_ORDEM)}
        top = sorted(itens.items(),
                    key=lambda kv: (ordem_raridade.get(kv[1].get("raridade"), 99),
                                    -kv[1].get("qtd", 0)))[:5]
        linhas.append("\n🎁 *Drops de hoje* (top 5):")
        for nome, info in top:
            emoji = next((e for r, e in _EMOJI_RARIDADE_ORDEM if r == info.get("raridade")), "⚪")
            linhas.append(f"  {emoji} {nome} — {info.get('qtd', 0)}×")
        if len(itens) > 5:
            linhas.append("_(lista completa em 📦 Drops de hoje)_")
    else:
        linhas.append("\n🎁 Ainda não dropou nada hoje.")
    return "\n".join(linhas)


def formatar_drops_dia(pagina: int = 0):
    """'📦 Drops de hoje' (pedido do usuário 2026-07-20, viu isso em outro
    bot — 'Hit Kill' — e quis igual aqui): lista TODOS os itens dropados
    hoje, agrupados por raridade (do lendário ao sem-raridade) e ordenados
    por quantidade dentro de cada grupo, com paginação (igual ao 'Hit
    Kill'). Fonte: diario[hoje]['itens_hoje'], gravado pelas funções
    registrar_masmorra/registrar_cacada/etc. no hunter.py (ver
    _registrar_drops_diario). Retorna (texto, pagina_atual, total_paginas).
    """
    if not os.path.exists(RELATORIO_FILE):
        return "ℹ️ Ainda não tem relatório (nenhuma execução concluída).", 0, 1
    try:
        with open(RELATORIO_FILE, encoding="utf-8") as f:
            dados = json.load(f) or {}
    except Exception:
        return "⚠️ Não consegui ler o relatorio.json (arquivo corrompido/em uso).", 0, 1
    hoje = time.strftime("%Y-%m-%d")
    itens = ((dados.get("diario") or {}).get(hoje) or {}).get("itens_hoje") or {}
    if not itens:
        return "📦 *Drops de hoje*\n\nAinda não dropou nenhum item hoje.", 0, 1

    # Agrupa por raridade (ordem: lendário -> ... -> sem raridade) e, dentro
    # de cada grupo, ordena por quantidade (maior primeiro) — mesmo visual
    # do 'Hit Kill'.
    por_raridade = {chave: [] for chave, _emoji in _EMOJI_RARIDADE_ORDEM}
    for nome, info in itens.items():
        raridade = info.get("raridade")
        por_raridade.setdefault(raridade, []).append((nome, info.get("qtd", 0)))
    linhas_itens = []
    for raridade, emoji in _EMOJI_RARIDADE_ORDEM:
        grupo = sorted(por_raridade.get(raridade, []), key=lambda t: -t[1])
        for nome, qtd in grupo:
            linhas_itens.append(f"{emoji} {nome} — {qtd}×")
    total_paginas = max(1, (len(linhas_itens) + DROPS_POR_PAGINA - 1) // DROPS_POR_PAGINA)
    pagina = max(0, min(pagina, total_paginas - 1))
    inicio = pagina * DROPS_POR_PAGINA
    pagina_itens = linhas_itens[inicio:inicio + DROPS_POR_PAGINA]
    total_itens = sum(v.get("qtd", 0) for v in itens.values())
    texto = (f"📦 *Drops de hoje* (por raridade 🟠→⚪) · {total_itens} item(ns) · "
             f"pág {pagina + 1}/{total_paginas}\n\n" + "\n".join(pagina_itens))
    return texto, pagina, total_paginas


def _botoes_drops(pagina: int, total_paginas: int):
    nav = []
    if pagina > 0:
        nav.append(Button.inline("◀️ Anterior", f"drops:{pagina - 1}".encode("utf-8")))
    if pagina < total_paginas - 1:
        nav.append(Button.inline("Próximo ▶️", f"drops:{pagina + 1}".encode("utf-8")))
    botoes = [nav] if nav else []
    botoes.append([Button.inline("🔄 Atualizar", f"drops:{pagina}".encode("utf-8"))])
    botoes.append([Button.inline("⬅️ Estatísticas", b"estatisticas")])
    return botoes


def _menu_relatorio():
    """1 botão por 'aba' (modo) + Por dia + Voltar."""
    botoes = [[Button.inline(label, f"rel_modo:{chave}".encode("utf-8"))]
              for chave, _tot, label in _MODOS_RELATORIO]
    botoes.append([Button.inline("📅 Por dia", b"rel_dia")])
    botoes.append([Button.inline("⬅️ Voltar", b"menu")])
    return botoes


# ---------------------------------------------------------------------
#  Menu principal (botões inline)
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
#  Escolher CONTEÚDO (qual modo roda) e, pra Caçada em Dupla, quais duplas
#  já configuradas no painel entram — pedido do usuário 2026-07-19.
# ---------------------------------------------------------------------

MODOS_CONTEUDO = [
    ("masmorra", "🏰 Masmorra"),
    ("caca_dupla", "⚔️ Caçada em Dupla"),
    ("cripta", "💀 Cripta"),
    ("caca_solo", "🏹 Caçada Solo"),
    ("missao_oasis", "🏝 Missão Oásis"),
    ("templo_oasis", "☀️ Templo do Oásis"),
    ("fortaleza_orcs", "🏯 Fortaleza dos Orcs"),
]


def _modo_atual() -> str:
    dados = _ler_settings()
    return dados.get("MODO_CONTEUDO") or "masmorra"


def _definir_modo(modo: str) -> None:
    """Troca MODO_CONTEUDO no settings.json (mesma chave que o painel usa —
    é ela sozinha que decide qual conteúdo roda, ver config._carregar_
    settings). Só vale de verdade depois de (re)iniciar o bot — config só é
    lido 1x, na hora que o processo abre."""
    dados = _ler_settings()
    dados["MODO_CONTEUDO"] = modo
    _salvar_settings(dados)


def _grupos_dupla() -> list:
    """Duplas JÁ configuradas no painel (CACA_DUPLA.grupos) — pedido do
    usuário 2026-07-19: escolher qual dupla mandar pelo Telegram, sem
    precisar reconfigurar quem tá em cada dupla (isso continua só no
    painel gráfico). Cada item: lista de 2 contas (dict com name/phone)."""
    dados = _ler_settings()
    return ((dados.get("CACA_DUPLA") or {}).get("grupos")) or []


def _selecionadas_dupla() -> set:
    dados = _ler_settings()
    return set((dados.get("CACA_DUPLA") or {}).get("selecionadas") or [])


def _alternar_dupla(indice: int) -> str:
    """Liga/desliga UMA dupla já configurada (soma/tira os 2 telefones dela
    de CACA_DUPLA.selecionadas) e garante MODO_CONTEUDO = 'caca_dupla' —
    sem mexer em quem está em cada dupla nem nos ajustes (HP%/almas/etc.),
    só ativos/inativos."""
    grupos = _grupos_dupla()
    if indice < 0 or indice >= len(grupos):
        return "⚠️ Dupla não encontrada (a lista pode ter mudado — atualize o menu)."
    fones_dupla = {c.get("phone", "") for c in grupos[indice] if c.get("phone")}
    dados = _ler_settings()
    cd = dados.get("CACA_DUPLA") or {}
    sel = set(cd.get("selecionadas") or [])
    se_estava_ativa = fones_dupla.issubset(sel) and bool(fones_dupla)
    if se_estava_ativa:
        sel -= fones_dupla
    else:
        sel |= fones_dupla
    cd["selecionadas"] = sorted(sel)
    dados["CACA_DUPLA"] = cd
    dados["MODO_CONTEUDO"] = "caca_dupla"
    _salvar_settings(dados)
    return "🔴 Dupla desativada." if se_estava_ativa else "🟢 Dupla ativada."


def _opcoes_masmorra() -> list:
    """Cada opção de 'qual masmorra rodar': os mapas normais conhecidos
    (config.MAPAS_CONHECIDOS) + as masmorras alternativas com sala especial
    (config.MASMORRAS_ALTERNATIVAS — Zuzu/Viadin/Hidra/Ossos), cada uma já
    com seu mapa fixo. Retorna lista de (rótulo, tipo, mapa)."""
    opcoes = [(f"🏰 {m}", "normal", m) for m in getattr(config, "MAPAS_CONHECIDOS", [])]
    for chave, info in (getattr(config, "MASMORRAS_ALTERNATIVAS", {}) or {}).items():
        opcoes.append((f"✨ {info.get('rotulo', chave)}", chave, info.get("mapa", "")))
    return opcoes


def _menu_masmorra():
    dados = _ler_settings()
    tipo_atual = dados.get("TIPO_MASMORRA") or "normal"
    mapa_atual = (dados.get("MAPA_DESTINO") or "").strip()
    linhas = []
    for i, (rotulo, tipo, mapa) in enumerate(_opcoes_masmorra()):
        if tipo == "normal":
            ativa = (tipo_atual == "normal") and mapa_atual.lower() == mapa.lower()
        else:
            ativa = (tipo_atual == tipo)
        marcado = "✅ " if ativa else ""
        linhas.append([Button.inline(f"{marcado}{rotulo}", f"masmorra:{i}".encode("utf-8"))])
    linhas.append([Button.inline("⬅️ Voltar", b"conteudo")])
    return linhas


def _texto_menu_masmorra() -> str:
    dados = _ler_settings()
    tipo_atual = dados.get("TIPO_MASMORRA") or "normal"
    mapa_atual = dados.get("MAPA_DESTINO") or "(mapa atual, sem trocar)"
    if tipo_atual != "normal":
        rotulo = next((r for r, t, m in _opcoes_masmorra() if t == tipo_atual), tipo_atual)
        atual_txt = rotulo
    else:
        atual_txt = f"🏰 Normal — {mapa_atual}"
    return (f"🏰 *Escolher Masmorra*\n\nAtiva agora: *{atual_txt}*\n\n"
            f"Escolha outra (só vale depois de reiniciar o bot):")


def _definir_masmorra(indice: int) -> str:
    opcoes = _opcoes_masmorra()
    if indice < 0 or indice >= len(opcoes):
        return "⚠️ Opção não encontrada (a lista pode ter mudado — atualize o menu)."
    rotulo, tipo, mapa = opcoes[indice]
    dados = _ler_settings()
    dados["TIPO_MASMORRA"] = tipo
    dados["MAPA_DESTINO"] = mapa
    dados["MODO_CONTEUDO"] = "masmorra"
    _salvar_settings(dados)
    return f"✅ Masmorra escolhida: {rotulo}."


def _menu_conteudo():
    atual = _modo_atual()
    linhas = []
    for chave, rotulo in MODOS_CONTEUDO:
        marcado = "✅ " if chave == atual else ""
        if chave == "caca_dupla":
            linhas.append([Button.inline(f"{marcado}{rotulo} →", b"conteudo_dupla")])
        elif chave == "masmorra":
            linhas.append([Button.inline(f"{marcado}{rotulo} →", b"conteudo_masmorra")])
        elif chave == "missao_oasis":
            # Seta pro submenu de contas (pedido do usuário 2026-07-23:
            # "coloque pra selecionar quais chars vão fazer a missão, no
            # painel do telegram") — mesmo padrão de Dupla/Masmorra acima.
            linhas.append([Button.inline(f"{marcado}{rotulo} →", b"conteudo_missao_oasis")])
        else:
            linhas.append([Button.inline(f"{marcado}{rotulo}", f"modo:{chave}".encode("utf-8"))])
    linhas.append([Button.inline("⬅️ Voltar", b"menu")])
    return linhas


def _contas_selecionadas_missao_oasis() -> set:
    dados = _ler_settings()
    mo = dados.get("MISSAO_OASIS") or {}
    return set(mo.get("selecionadas") or [])


def _alternar_conta_missao_oasis(fone: str) -> tuple:
    """Liga/desliga UMA conta na Missão Oásis (pedido do usuário 2026-07-23:
    "coloque pra selecionar quais chars vão fazer a missão, no painel do
    telegram"). Retorna (novo_estado: bool, avisar_item: bool) — avisar_item
    é True quando a conta foi LIGADA mas nunca teve um item-alvo configurado
    (o painel exige um item por conta pra ela fazer alguma coisa; aqui só
    liga/desliga, escolher o item continua sendo só pelo painel, igual
    HP%/Nurmora — não dá pra montar aquele combobox de itens direito nos
    botões do Telegram)."""
    dados = _ler_settings()
    mo = dados.setdefault("MISSAO_OASIS", {})
    selecionadas = set(mo.get("selecionadas") or [])
    contas = mo.get("contas") or []
    contas_por_fone = {c.get("phone", "").strip(): c for c in contas if c.get("phone")}
    novo_estado = fone not in selecionadas
    avisar_item = False
    if novo_estado:
        selecionadas.add(fone)
        # Reaproveita a config anterior dessa conta se ainda existir (ex:
        # foi desmarcada e remarcada sem passar pelo painel no meio) — senão
        # cria uma entrada mínima, a partir da aba chars (ACCOUNTS).
        if fone not in contas_por_fone:
            base = next((a for a in (dados.get("ACCOUNTS") or []) if a.get("phone", "").strip() == fone), None)
            if base:
                nova = {"name": base.get("name", ""), "phone": fone, "role": base.get("role", ""),
                        "char_name": base.get("char_name", ""), "monstro_alvo": "",
                        "fazer_nurmora": False, "focar_nurmora": False, "meta_martelos": 0}
                contas.append(nova)
                avisar_item = True
            # sem entrada em ACCOUNTS nem em contas — nada a acrescentar,
            # mas ainda assim entra em 'selecionadas' (o hunter.py ignora
            # silenciosamente contas sem char_name/monstro_alvo válido).
        elif not contas_por_fone[fone].get("monstro_alvo"):
            avisar_item = True
    else:
        selecionadas.discard(fone)
        contas = [c for c in contas if c.get("phone", "").strip() != fone]
    mo["selecionadas"] = sorted(selecionadas)
    mo["contas"] = contas
    _salvar_settings(dados)
    return novo_estado, avisar_item


def _menu_contas_missao_oasis(user_id: int = None):
    selecionadas = _contas_selecionadas_missao_oasis()
    linhas = []
    for nome, fone in _contas_disponiveis_compra():
        marcado = "✅" if fone in selecionadas else "⬜"
        linhas.append([Button.inline(f"{marcado} {nome}", f"mo_conta_t:{fone}".encode("utf-8"))])
    linhas.append([Button.inline("⬅️ Voltar", b"conteudo")])
    return linhas


def _texto_contas_missao_oasis() -> str:
    n = len(_contas_selecionadas_missao_oasis())
    return (f"🏝 *Contas na Missão Oásis*\n\n"
            f"Toque pra marcar/desmarcar quem participa. Selecionadas: *{n}*\n\n"
            f"⚠️ Contas marcadas aqui pela PRIMEIRA vez (nunca configuradas no "
            f"painel) entram sem item-alvo escolhido — abra o painel e defina "
            f"o item dela na aba Missão Oásis pra ela realmente fazer algo.")


def _texto_menu_conteudo() -> str:
    # 🧩 MULTI-CONTEÚDO (pedido do usuário 2026-07-22): com GRUPOS_CONTEUDO
    # preenchido, o hunter.py IGNORA MODO_CONTEUDO por completo — trocar o
    # modo aqui não teria efeito nenhum. Avisa isso explicitamente em vez de
    # deixar a pessoa escolher achando que vai valer.
    _dados_multi = _ler_settings()
    grupos_multi = (_dados_multi.get("GRUPOS_CONTEUDO") or []) if _dados_multi.get("MULTI_CONTEUDO_ATIVO") else []
    if grupos_multi:
        nomes = ", ".join(g.get("nome") or "Grupo" for g in grupos_multi)
        return (f"🎮 *Escolher conteúdo*\n\n⚠️ *Multi-conteúdo está ativo* "
                f"({len(grupos_multi)} grupo(s): {nomes}) — escolher um modo aqui "
                f"NÃO tem efeito, o multi-conteúdo tem prioridade. Desative os "
                f"grupos no painel (aba Multi-conteúdo) pra voltar a escolher 1 "
                f"modo só por aqui.")
    atual = _modo_atual()
    rotulo = next((r for c, r in MODOS_CONTEUDO if c == atual), atual)
    return (f"🎮 *Escolher conteúdo*\n\nAtivo agora: *{rotulo}*\n\n"
            f"Escolha outro (só vale depois de reiniciar o bot):")


def _menu_duplas():
    grupos = _grupos_dupla()
    if not grupos:
        return [[Button.inline("⬅️ Voltar", b"conteudo")]]
    sel = _selecionadas_dupla()
    linhas = []
    for i, grupo in enumerate(grupos):
        nomes = " + ".join(c.get("name") or c.get("phone", "?") for c in grupo)
        fones = {c.get("phone", "") for c in grupo if c.get("phone")}
        ativa = bool(fones) and fones.issubset(sel)
        marcado = "✅" if ativa else "⬜"
        linhas.append([Button.inline(f"{marcado} Dupla {i + 1}: {nomes}",
                                     f"dupla:{i}".encode("utf-8"))])
    linhas.append([Button.inline("⚙️ Ajustes (andar/limite)", b"ajustes_dupla")])
    linhas.append([Button.inline("🚀 Iniciar agora", b"iniciar"),
                   Button.inline("⬅️ Voltar", b"conteudo")])
    return linhas


def _texto_menu_duplas() -> str:
    grupos = _grupos_dupla()
    if not grupos:
        return ("⚠️ Nenhuma dupla configurada ainda. Configure em "
                "'Caçada Dupla' no painel gráfico primeiro (quem entra em "
                "cada dupla, papel, almas, etc.) — aqui só liga/desliga.")
    return "⚔️ *Caçada em Dupla — escolha quem vai*\n\nToque pra ligar/desligar cada dupla:"


# --- Editar andar máximo / limite de caçadas (pedido do usuário 2026-07-19)
# --- Só vale a partir da próxima vez que o bot for parado e iniciado de
# novo (mesmo esquema de sempre — sem hot reload nenhum, o usuário topou
# esse jeito mais simples de propósito, em vez de mexer no loop do
# hunter.py pra reler isso ao vivo).

_editando = {}   # user_id -> "andar_maximo" | "max_cacadas" (esperando resposta em texto)
# Seleção de contas pra "Comprar poções" (pedido do usuário 2026-07-22:
# "tem como escolher quais contas quer comprar?") — user_id -> {"tipo":
# "vida"|"energia", "contas": set(telefones)}. Estado só em memória (some
# se o controle reiniciar no meio — aceitável, é só uma seleção em
# andamento, não um pedido já confirmado).
_selecao_compra = {}
# Mesma ideia, agora pro '🛒 Vender agora' (pedido do usuário 2026-07-23:
# "tem como selecionar lá quem vende?") — user_id -> {"contas": set(fones)}.
_selecao_venda = {}


def _ajustes_dupla_atuais() -> dict:
    dados = _ler_settings()
    return dados.get("CACA_DUPLA") or {}


def _texto_ajustes_dupla() -> str:
    cd = _ajustes_dupla_atuais()
    andar = cd.get("andar_maximo", 49)
    limite = cd.get("max_cacadas", 0)
    limite_txt = "sem limite" if not limite else str(limite)
    return (f"⚙️ *Ajustes da Caçada em Dupla*\n\n"
            f"• Andar máximo: *{andar}*\n"
            f"• Quantas caçadas (limite): *{limite_txt}*\n\n"
            f"_Só vale a partir da próxima vez que parar e iniciar o bot._")


def _menu_ajustes_dupla():
    return [
        [Button.inline("✏️ Mudar andar máximo", b"editar:andar_maximo")],
        [Button.inline("✏️ Mudar limite de caçadas", b"editar:max_cacadas")],
        [Button.inline("⬅️ Voltar", b"conteudo_dupla")],
    ]


def _salvar_ajuste_dupla(campo: str, valor: int) -> None:
    dados = _ler_settings()
    cd = dados.get("CACA_DUPLA") or {}
    cd[campo] = valor
    dados["CACA_DUPLA"] = cd
    _salvar_settings(dados)


def _menu_principal():
    # Reorganizado (pedido do usuário 2026-07-20: "ficou MT poluído, vamos
    # reorganizar?") — o menu tinha crescido pra 10 linhas/~16 botões
    # conforme fomos adicionando recursos (Drops, Ranking, Tempo médio,
    # Estoque, Progresso). Agora "Vender agora"/"Ler inventário" viraram o
    # submenu "🛒 Mercado", e "Drops/Ranking/Tempo médio/Estoque/Progresso"
    # viraram o submenu "📊 Estatísticas" — mesmo padrão que "📈 Relatório"
    # já usava (ver _menu_relatorio). O menu principal volta a caber numa
    # tela só, sem perder nenhum botão (só ficou 1 clique mais fundo).
    # Compactado de novo (pedido do usuário 2026-07-21: "tem como deixar
    # esse menu mais organizado?") — de 10 pra 8 linhas, agrupando pares
    # lógicos ("Parar no fim"+"Parar e Sair" juntos; "Conteúdo"+
    # "Configurações" juntos) e encurtando rótulos que o Telegram vinha
    # TRUNCANDO em linhas de 2 botões (print do usuário mostrava "Status
    # por cont..."). Os callbacks (b"...") não mudam — só os textos.
    rodando = bot_rodando()
    parando_no_fim = os.path.exists(PARAR_NO_FIM_FLAG)
    linha1 = [Button.inline("⏹ Parar agora", b"parar_agora")] if rodando \
        else [Button.inline("🚀 Iniciar", b"iniciar")]
    linha2 = [Button.inline(
        "▶️ Cancelar parada" if parando_no_fim else "⏸ Parar no fim",
        b"parar_no_fim"),
        Button.inline("⏹️🚪 Parar e Sair", b"parar_e_sair")]
    linha3 = [Button.inline("📊 Status", b"status"),
              Button.inline("📈 Relatório", b"relatorio")]
    linha4 = [Button.inline("👤 Status contas", b"status_contas"),
              Button.inline("🩺 HP% contas", b"hp_contas")]
    linha5 = [Button.inline("🗺️ Mapas", b"mapas"),
              Button.inline("📟 Ver log", b"ver_log")]
    linha6 = [Button.inline("🛒 Mercado", b"mercado"),
              Button.inline("📊 Estatísticas", b"estatisticas")]
    linha7 = [Button.inline("🎮 Conteúdo", b"conteudo"),
              Button.inline("⚙️ Configurações", b"configuracoes")]
    linha8 = [Button.inline("🔄 Atualizar menu", b"menu")]
    return [linha1, linha2, linha3, linha4, linha5, linha6, linha7, linha8]


def _menu_mercado():
    """Submenu '🛒 Mercado' — agrupa Vender agora / Ler inventário / Comprar
    poções (pedido do usuário 2026-07-21: "digito uma quantidade e ele vai
    na loja e compra") / Comprar Super Tônicos (pedido do usuário
    2026-07-23 — só vendem no Mercador do Deserto)."""
    return [[Button.inline("🛒 Vender agora", b"vender_agora"),
             Button.inline("📦 Ler inventário", b"ler_inventario")],
            [Button.inline("🧪 Comprar Poção de Vida", b"comprar_pocao:vida"),
             Button.inline("⚡ Comprar Poção de Energia", b"comprar_pocao:energia")],
            [Button.inline("💪 Super Tônico Força", b"comprar_pocao:tonico_forca"),
             Button.inline("🎯 Super Tônico Precisão", b"comprar_pocao:tonico_precisao")],
            [Button.inline("🛡️ Super Tônico Defesa", b"comprar_pocao:tonico_defesa")],
            [Button.inline("⬅️ Menu", b"menu")]]


def _menu_estatisticas():
    """Submenu '📊 Estatísticas' — agrupa Drops de hoje / Ranking do dia /
    Tempo médio / Estoque de poções / Progresso de nível (antes soltos no
    menu principal, 4 linhas inteiras). Os gráficos (Gráfico do dia, por
    conta, Heatmap, Dashboard semanal) ganharam submenu próprio '📊
    Gráficos' (pedido do usuário 2026-07-24), pra não poluir esta lista."""
    return [[Button.inline("📦 Drops de hoje", b"drops:0")],
            [Button.inline("🏰 Progresso da Masmorra", b"progresso_masmorra")],
            [Button.inline("📊 Gráficos", b"graficos")],
            [Button.inline("🏅 Comparativo contas", b"comparativo_contas")],
            [Button.inline("⚡ Eficiência por conteúdo", b"eficiencia_conteudo")],
            [Button.inline("🏆 Ranking do dia", b"ranking_dia"),
             Button.inline("🥊 Ranking de dano", b"ranking_dano")],
            [Button.inline("⏱️ Tempo médio", b"tempo_medio"),
             Button.inline("🧪 Estoque de poções", b"estoque_pocoes")],
            [Button.inline("📈 Progresso de nível", b"progresso_nivel"),
             Button.inline("⭐ XP total", b"xp_total")],
            [Button.inline("📊 XP real hoje", b"xp_real_hoje")],
            [Button.inline("🐲 Dragão de Cristal de Frost", b"relatorio_dragao")],
            [Button.inline("📋 Ajustes atuais", b"ajustes_atuais")],
            [Button.inline("⬅️ Menu", b"menu")]]


def _menu_graficos():
    """Submenu '📊 Gráficos' (pedido do usuário 2026-07-24: "coloque esses
    gráficos tudo dentro de um novo clicável 'gráficos'") — agrupa os 4
    gráficos em PNG (antes soltos dentro de Estatísticas): Gráfico do dia,
    Gráfico por conta, Heatmap hora×dia e Dashboard semanal."""
    return [[Button.inline("📉 Gráfico do dia", b"grafico_dia"),
             Button.inline("📊 Gráfico por conta", b"grafico_contas")],
            [Button.inline("🔥 Heatmap hora×dia", b"grafico_heatmap"),
             Button.inline("🗓️ Dashboard semanal", b"grafico_semanal")],
            [Button.inline("⬅️ Estatísticas", b"estatisticas")]]


def _texto_menu() -> str:
    estado = "🟢 rodando" if bot_rodando() else "🔴 parado"
    if os.path.exists(PARAR_NO_FIM_FLAG):
        estado += " (vai parar no fim do conteúdo atual)"
    return f"🎛 *TofuBot — Controle*\n\nStatus: {estado}\n\nEscolha uma opção:"


def formatar_resumo_diario_automatico() -> str:
    """Resumo automático mandado sozinho na hora fixa (RESUMO_DIARIO_HORA) —
    pedido do usuário 2026-07-20: reaproveita formatar_relatorio_dia(1) (só
    hoje), só troca o título pra deixar claro que é o aviso automático."""
    texto = formatar_relatorio_dia(1)
    return texto.replace("📅 *Por dia*", "📈 *Resumo automático do dia*", 1)


def _data_resumo_ja_enviado() -> str:
    return _ler_settings().get("CONTROLE_RESUMO_ULTIMA_DATA", "")


def _marcar_resumo_enviado(data: str) -> None:
    dados = _ler_settings()
    dados["CONTROLE_RESUMO_ULTIMA_DATA"] = data
    _salvar_settings(dados)


_ultimo_evento_ts = 0.0          # cursor — só processa eventos MAIS NOVOS que isso
_ultimo_alerta_travada = {}      # (conta, contexto) -> ts do último alerta (throttle)


def _ler_eventos_novos() -> list:
    """Lê eventos.json (ver hunter.py _registrar_evento) e devolve só os que
    ainda não foram processados (mais novos que o cursor em memória). O
    cursor começa em 0.0, mas o PRIMEIRO uso de monitorar_bot já avança ele
    pro 'agora' sem alertar nada (mesmo espírito do 'estava_rodando' — não
    quer alertar de eventos ANTIGOS só porque o telegram_controle.py
    acabou de (re)iniciar)."""
    global _ultimo_evento_ts
    if not os.path.exists(EVENTOS_FILE):
        return []
    try:
        with open(EVENTOS_FILE, encoding="utf-8") as f:
            eventos = json.load(f) or []
    except Exception:
        return []
    if not eventos:
        return []
    novos = [e for e in eventos if e.get("ts", 0) > _ultimo_evento_ts]
    _ultimo_evento_ts = max(_ultimo_evento_ts, max(e.get("ts", 0) for e in eventos))
    return novos


def _formatar_evento(evento: dict):
    """Texto de UM evento pra mandar no grupo, ou None se não deve mandar
    nada agora (tipo desligado em ⚙️ Configurações, ou 'conta_travada' no
    cooldown, pra não repetir toda hora enquanto a mesma conta continua
    travada)."""
    tipo = evento.get("tipo")
    if tipo and not _alerta_ativo(tipo):
        return None
    if tipo == "bot_pausou":
        # Pedido do usuário 2026-07-23: "tem um aviso programado pra quando
        # falta pot de vida, antes de iniciar a run, mas não tá abrindo...
        # uso linux... se disparasse um pop up informando o motivo... pode
        # ser no telegram também" — o popup nativo (ctypes.windll) só
        # funcionava no Windows; corrigido pra ter fallback no Tkinter, MAS
        # o Telegram é mais confiável ainda (não depende de display/SO).
        descricao = evento.get("descricao", "?")
        detalhe = evento.get("detalhe", "")
        texto = f"⏸️ *Bot pausou*\n\n{descricao}"
        if detalhe:
            texto += f"\n\n{detalhe}"
        return texto
    if tipo == "item_raro":
        emoji = evento.get("emoji", "")
        item = evento.get("item", "?")
        raridade = evento.get("raridade", "")
        conta = evento.get("conta", "")
        origem = evento.get("origem", "")
        # CORRIGIDO 2026-07-22 (usuário: "relatório veio duplicado, coloque
        # pra vir só o que mostra o item e quem dropou") — antes duas
        # mensagens separadas chegavam pro MESMO drop (uma só com o mapa,
        # outra só com a conta, vindas de 2 funções diferentes no
        # hunter.py). Agora é 1 evento só, com item + quem + onde juntos.
        texto = f"🎁 *Item raro dropou!*\n\n{emoji} *{item}* ({raridade})"
        if conta:
            texto += f"\n👤 {conta}"
        if origem:
            texto += f"\n📍 {origem}"
        return texto
    if tipo == "morte":
        conta = evento.get("conta") or "grupo"
        modo = evento.get("modo", "")
        return f"💀 *Morte detectada*\n\n👤 {conta}\n📍 {modo}"
    if tipo == "conta_travada":
        conta = evento.get("conta", "?")
        contexto = evento.get("contexto", "")
        chave = (conta, contexto)
        agora = time.time()
        if agora - _ultimo_alerta_travada.get(chave, 0) < CONTA_TRAVADA_COOLDOWN_SEG:
            return None
        _ultimo_alerta_travada[chave] = agora
        return (f"⚠️ *Conta pode estar travada*\n\n👤 {conta}\n📍 {contexto}\n\n"
                f"Vale checar manualmente (📟 Ver log).")
    if tipo == "recorde":
        rotulo = evento.get("rotulo", "?")
        dur = evento.get("duracao_segundos")
        dur_ant = evento.get("duracao_anterior")
        texto = f"🏆 *Novo recorde de velocidade!*\n\n📍 {rotulo}: {_formatar_duracao(dur)}"
        if dur_ant:
            texto += f"\n(recorde anterior: {_formatar_duracao(dur_ant)})"
        return texto
    if tipo == "compra_pocoes":
        nome_item = evento.get("nome_item", "item")
        total = evento.get("total", 0)
        gasto = evento.get("gasto", 0)
        detalhes = evento.get("detalhes", "")
        texto = (f"🧪 *Compra de poções concluída*\n\n"
                f"{total}x {nome_item} · " + f"{gasto:,}".replace(",", ".") + " gold gastos")
        if detalhes:
            texto += f"\n\n{detalhes}"
        return texto
    if tipo == "subiu_nivel":
        conta = evento.get("conta", "?")
        nivel = evento.get("nivel", "?")
        nivel_anterior = evento.get("nivel_anterior")
        texto = f"🎉 *Subiu de nível!*\n\n👤 {conta} — Lv{nivel_anterior} ➜ Lv{nivel}" \
            if nivel_anterior else f"🎉 *Subiu de nível!*\n\n👤 {conta} — Lv{nivel}"
        return texto
    if tipo == "dragao_derrotado":
        grupo_idx = evento.get("grupo_idx", 1)
        derrotas_total = evento.get("derrotas_total", "?")
        item_dropado = evento.get("item_dropado", "")
        texto = f"🐲 *Dragão de Cristal de Frost derrotado!* (dupla {grupo_idx})\n\n" \
                f"Derrotas no total: {derrotas_total}"
        if item_dropado:
            texto += f"\n🎁 Dropou: *{item_dropado}*"
        else:
            texto += "\nSem drop lendário desta vez."
        return texto
    return None


# ---------------------------------------------------------------------
#  Alertas por CONDIÇÃO (diferente dos de cima, que vêm de eventos.json)
#  pedido do usuário 2026-07-25: checados a cada volta do monitor a partir
#  de status.json, comparando com o estado da rodada anterior (guardado em
#  memória, nos dicts abaixo) pra só avisar na TRANSIÇÃO — sem repetir a
#  cada poll enquanto a mesma condição continuar valendo.
# ---------------------------------------------------------------------

_ultimo_buff_ativo = {}       # nome -> tinha buff_texto (Tônico ATK/DEF/CRIT) na última checagem?
_ultimo_elixir_ativo = {}     # nome -> elixir_ativo (Sabedoria/Fortuna) na última checagem?
_ultimo_estoque_baixo = {}    # nome -> estoque de poção já estava baixo?
_ultimo_chaves_baixo = {}     # nome -> chaves já estavam baixas?
_ultimo_energia_cheia = {}    # nome -> energia já estava cheia?
_ultimo_vip_aviso_data = {}   # nome -> "AAAA-MM-DD" do último aviso de VIP (1x por dia)

CHAVES_MASMORRA_MINIMO = 3   # abaixo disso, avisa que tá acabando
VIP_AVISO_DIAS = 3           # avisa quando faltar <= N dias pro VIP vencer


def _checar_alertas_periodicos() -> list:
    """Lê status.json e devolve os textos prontos pra mandar AGORA (lista
    vazia se nada mudou desde a última checagem). Cada condição só gera
    aviso na TRANSIÇÃO de 'estava ok' pra 'não está mais' (ou, no caso do
    VIP, 1x por dia enquanto durar a janela de aviso) — evita spam a cada
    volta do monitor (MONITORAR_INTERVALO) enquanto a mesma situação persiste."""
    if not os.path.exists(STATUS_FILE):
        return []
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f) or {}
    except Exception:
        return []
    textos = []
    minimo_pocao = getattr(config, "MASMORRA_POCAO_VIDA_MINIMA", 50)
    hoje = time.strftime("%Y-%m-%d")
    for nome, d in status.items():
        if not isinstance(d, dict):
            continue
        # 🍀 Elixir/Tônico expirou: 2 flags independentes (uma conta pode ter
        # os dois configurados ao mesmo tempo) — 'buff_texto' é o Tônico
        # (ATK/DEF/CRIT, ver parse_buff/BUFF_RE no hunter.py), 'elixir_ativo'
        # é o Elixir de Sabedoria/Fortuna (+XP/+Drop, ver try_elixir). Cada
        # um avisa na sua própria transição de 'tinha' pra 'não tem mais'.
        buff = (d.get("buff_texto") or "").strip()
        tinha_tonico = _ultimo_buff_ativo.get(nome, False)
        if _alerta_ativo("buff_expirou") and tinha_tonico and not buff:
            textos.append(f"💪 *Tônico expirou*\n\n👤 *{nome}* — sem Super Tônico ativo agora.")
        _ultimo_buff_ativo[nome] = bool(buff)
        elixir_ativo_agora = d.get("elixir_ativo")
        tinha_elixir = _ultimo_elixir_ativo.get(nome, False)
        if (_alerta_ativo("buff_expirou") and elixir_ativo_agora is not None
                and tinha_elixir and not elixir_ativo_agora):
            textos.append(f"🍀 *Elixir expirou*\n\n👤 *{nome}* — sem Elixir de Sabedoria/Fortuna ativo agora.")
        if elixir_ativo_agora is not None:
            _ultimo_elixir_ativo[nome] = bool(elixir_ativo_agora)
        # 📦 Estoque baixo de Poção de Vida (mesmo limite do '🧪 Estoque de
        # consumíveis' — config.MASMORRA_POCAO_VIDA_MINIMA).
        qtd = d.get("estoque_pocao_vida")
        if qtd is not None:
            baixo_agora = qtd < minimo_pocao
            if (_alerta_ativo("estoque_baixo") and baixo_agora
                    and not _ultimo_estoque_baixo.get(nome, False)):
                textos.append(f"📦 *Estoque de Poção de Vida baixo*\n\n👤 *{nome}* — {qtd} restante(s).")
            _ultimo_estoque_baixo[nome] = baixo_agora
        # 🔑 Chaves de Masmorra acabando.
        chaves = d.get("chaves_masmorra")
        if chaves is not None:
            baixo_agora = chaves < CHAVES_MASMORRA_MINIMO
            if (_alerta_ativo("chaves_baixo") and baixo_agora
                    and not _ultimo_chaves_baixo.get(nome, False)):
                textos.append(f"🔑 *Chaves de Masmorra acabando*\n\n👤 *{nome}* — {chaves} restante(s).")
            _ultimo_chaves_baixo[nome] = baixo_agora
        # 👑 VIP vencendo em breve — avisa 1x por dia enquanto faltar
        # <= VIP_AVISO_DIAS dias (não é uma transição booleana simples, já
        # que a data não "reseta" sozinha durante a janela de aviso).
        vip = d.get("vip_ate")
        if vip and _alerta_ativo("vip_vencendo"):
            try:
                dias = (datetime.strptime(vip, "%d/%m/%Y") - datetime.now()).days
            except ValueError:
                dias = None
            if (dias is not None and 0 <= dias <= VIP_AVISO_DIAS
                    and _ultimo_vip_aviso_data.get(nome) != hoje):
                quando = "vence hoje" if dias == 0 else f"faltam {dias} dia(s)"
                textos.append(f"👑 *VIP vencendo*\n\n👤 *{nome}* — até {vip} ({quando}).")
                _ultimo_vip_aviso_data[nome] = hoje
        # ⚡ Energia cheia (bateu no máximo — vale a pena não deixar regenerar à toa).
        en, en_max = d.get("energia"), d.get("energia_max")
        if en is not None and en_max:
            cheia_agora = en >= en_max
            if (_alerta_ativo("energia_cheia") and cheia_agora
                    and not _ultimo_energia_cheia.get(nome, False)):
                textos.append(f"⚡ *Energia cheia*\n\n👤 *{nome}* — {en}/{en_max}.")
            _ultimo_energia_cheia[nome] = cheia_agora
    return textos


async def monitorar_bot(client) -> None:
    """Aviso automático (pedido do usuário 2026-07-19): fica de olho no
    bot_rodando() em segundo plano e manda mensagem sozinho no grupo se o
    bot.exe cair — sem precisar ninguém perguntar. Só alerta numa transição
    de verdade (tava rodando -> parou), nunca no primeiro check (senão
    alertaria toda vez que o telegram_controle.py reinicia com o bot já
    parado, o que não é novidade nenhuma). Mensagem encurtada + apaga o
    aviso anterior antes de mandar um novo (pedido do usuário 2026-07-20:
    "diminua essa mensagem automática" + "apagar mensagens antigas").

    Também (pedido do usuário 2026-07-20): processa eventos em tempo real
    (item raro/morte/conta travada — ver hunter.py _registrar_evento) e
    manda o resumo diário sozinho às RESUMO_DIARIO_HORA."""
    await asyncio.sleep(5)   # dá um tempinho pro client.start() assentar
    estava_rodando = bot_rodando()
    _ler_eventos_novos()   # avança o cursor pro 'agora' sem alertar histórico
    while True:
        await asyncio.sleep(MONITORAR_INTERVALO)
        try:
            chat_id = getattr(config, "CONTROLE_CHAT_ID", 0)
            silencio = _em_silencio()   # avalia UMA vez por volta do loop
            agora_rodando = bot_rodando()
            if (estava_rodando and not agora_rodando and chat_id
                    and _alerta_ativo("bot_parou")):
                if silencio:
                    # 🌙 segura até a janela acabar (formatado, pronto pra sair)
                    _eventos_segurados.append("🔴 O bot parou. /menu pra conferir.")
                else:
                    try:
                        await _apagar_msg_antiga(_ULTIMA_MSG_AVISO, chat_id)
                        aviso = await client.send_message(
                            chat_id, "🔴 O bot parou. /menu pra conferir.")
                        _ULTIMA_MSG_AVISO[chat_id] = aviso
                    except Exception:
                        pass   # não derruba o monitor por causa de 1 falha de envio
            estava_rodando = agora_rodando

            if chat_id:
                for evento in _ler_eventos_novos():
                    # formata JÁ (toggle de Configurações e cooldown de conta
                    # travada valem no MOMENTO do evento, não na hora de sair
                    # do silêncio) — e segura só o texto pronto.
                    texto = _formatar_evento(evento)
                    if texto:
                        if silencio:
                            _eventos_segurados.append(texto)
                        else:
                            try:
                                await client.send_message(chat_id, texto, parse_mode="markdown")
                            except Exception:
                                pass

                # Alertas por CONDIÇÃO (buff expirou/estoque baixo/chaves
                # acabando/VIP vencendo/energia cheia) — ver
                # _checar_alertas_periodicos, mesmo funil de silêncio/envio
                # dos eventos pontuais acima.
                for texto in _checar_alertas_periodicos():
                    if silencio:
                        _eventos_segurados.append(texto)
                    else:
                        try:
                            await client.send_message(chat_id, texto, parse_mode="markdown")
                        except Exception:
                            pass

                # 🌙 saiu da janela de silêncio com coisa acumulada? Despeja
                # tudo num apanhado só (em blocos de ~3500 chars pra não
                # estourar o limite de 4096 do Telegram).
                if not silencio and _eventos_segurados:
                    pendentes = list(_eventos_segurados)
                    _eventos_segurados.clear()
                    blocos, atual = [], "🌙 *Enquanto estava em silêncio:*"
                    for t in pendentes:
                        if len(atual) + len(t) + 2 > 3500:
                            blocos.append(atual)
                            atual = ""
                        atual += ("\n\n" if atual else "") + t
                    if atual:
                        blocos.append(atual)
                    for bloco in blocos:
                        try:
                            await client.send_message(chat_id, bloco, parse_mode="markdown")
                        except Exception:
                            pass

                hoje = time.strftime("%Y-%m-%d")
                hora_agora = _hora_local_ajustada()
                if (hora_agora >= RESUMO_DIARIO_HORA and _data_resumo_ja_enviado() != hoje
                        and _alerta_ativo("resumo_diario") and not silencio):
                    try:
                        await client.send_message(chat_id, formatar_resumo_diario_automatico(),
                                                  parse_mode="markdown")
                    except Exception:
                        pass
                    _marcar_resumo_enviado(hoje)   # marca MESMO se o envio falhar — não
                                                    # fica tentando de novo sem parar no
                                                    # mesmo dia se der erro persistente
        except Exception:
            pass   # nunca deixa o loop de monitoramento morrer por causa de 1 erro


# ---------------------------------------------------------------------
#  Bot de controle
# ---------------------------------------------------------------------

async def main():
    token = (getattr(config, "CONTROLE_BOT_TOKEN", "") or "").strip()
    if not token:
        print("❌ CONTROLE_BOT_TOKEN não configurado (settings.json / painel). "
              "Crie um bot no @BotFather e cole o token antes de rodar isto.")
        return
    api_id = getattr(config, "API_ID", 0)
    api_hash = getattr(config, "API_HASH", "")
    client = TelegramClient(os.path.join(BASE, "controle_bot"), api_id, api_hash)
    await client.start(bot_token=token)
    print("✅ Bot de controle conectado. Aguardando comandos...")

    @client.on(events.NewMessage(pattern=r"^/(start|registrar)$"))
    async def _registrar(event):
        user_id = event.sender_id
        _salvar_chat_id(event.chat_id)
        novo = _autorizar_id(user_id)
        if novo:
            await event.respond(
                f"✅ Registrado! Seu ID ({user_id}) agora está autorizado a "
                f"usar os comandos deste bot.")
        elif _autorizado(user_id):
            await event.respond("ℹ️ Você já estava registrado.")
        await _enviar_painel(client, event.chat_id, _texto_menu(), _menu_principal())
        # Apaga o /start/registrar que a PESSOA mandou (pedido do usuário
        # 2026-07-20: "tem como apagar essas mensagens antigas? ficar só o
        # painel?") — funciona sempre em chat privado; em GRUPO só se o bot
        # for admin com permissão de apagar mensagem de outros (senão só
        # ignora, sem quebrar nada).
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r"^/menu$"))
    async def _cmd_menu(event):
        if not _autorizado(event.sender_id):
            await event.respond("🚫 Você não está autorizado. Mande /registrar primeiro.")
            return
        _salvar_chat_id(event.chat_id)
        await _enviar_painel(client, event.chat_id, _texto_menu(), _menu_principal())
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r"^/drop(?:\s+(.+))?$"))
    async def _cmd_drop(event):
        """'/drop <termo>' (2026-07-21) — busca item no banco de itens.
        Comando de texto direto, sem precisar abrir menu nenhum."""
        if not _autorizado(event.sender_id):
            return
        termo = (event.pattern_match.group(1) or "").strip()
        if not termo:
            await event.respond("🔎 Uso: `/drop nome do item`\nEx: `/drop Manto`",
                                parse_mode="markdown")
            return
        await event.respond(formatar_busca_drop(termo), parse_mode="markdown")

    @client.on(events.NewMessage())
    async def _resposta_texto(event):
        """Captura a resposta em texto quando alguém clicou 'Mudar andar
        máximo'/'Mudar limite de caçadas' (ver _editando) — qualquer OUTRA
        mensagem (comandos, conversa normal) é ignorada aqui, sem interferir
        nos outros handlers (que rodam à parte, Telethon despacha pra
        todos os que combinam)."""
        user_id = event.sender_id
        campo = _editando.get(user_id)
        if not campo or not _autorizado(user_id):
            return
        texto = (event.raw_text or "").strip()
        if texto.startswith("/"):
            return   # comando de verdade (ex: /menu) — não trata como valor
        try:
            valor = int(texto)
        except ValueError:
            await event.respond("⚠️ Não entendi — manda só o número (ex: 49).")
            return
        _editando.pop(user_id, None)
        if campo.startswith("comprar_pocao:"):
            # 🧪 quantidade de Poção de Vida/Energia pra comprar (pedido do
            # usuário 2026-07-21) — qualquer inteiro positivo (a compra em
            # si divide em blocos de 250 do lado do hunter.py, aqui só
            # valida que faz sentido pedir). As contas vêm da seleção feita
            # antes (pedido do usuário 2026-07-22: "escolher quais contas
            # quer comprar") — se por algum motivo a seleção sumiu (ex:
            # controle reiniciou no meio), cai pro padrão de sempre (todas
            # as marcadas em 'Contas que vendem').
            tipo = campo.split(":", 1)[1]
            if valor <= 0:
                await event.respond("⚠️ Manda uma quantidade maior que zero.")
                return
            estado = _selecao_compra.pop(user_id, None)
            contas = sorted(estado["contas"]) if estado and estado.get("contas") else None
            msg = comprar_pocoes_agora(tipo, valor, contas=contas)
            await event.respond(msg, buttons=[[Button.inline("⬅️ Mercado", b"mercado")]])
            return
        if campo.startswith("silencio:"):
            # 🌙 hora de início/fim do modo silencioso (0-23)
            qual = campo.split(":", 1)[1]
            valor = max(0, min(23, valor))
            dados_s = _ler_settings()
            chave_s = "CONTROLE_SILENCIO_INICIO" if qual == "inicio" else "CONTROLE_SILENCIO_FIM"
            dados_s[chave_s] = valor
            _salvar_settings(dados_s)
            rotulo = "início" if qual == "inicio" else "fim"
            await event.respond(f"✅ Hora de {rotulo} do silêncio: *{valor:02d}h*.\n\n"
                                + _texto_menu_configuracoes(),
                                buttons=_menu_configuracoes(), parse_mode="markdown")
            return
        if campo.startswith("hp_pct:"):
            # "🩺 HP% de poção por conta" (pedido do usuário 2026-07-20) —
            # limita 0-100 (é uma porcentagem; os outros campos de baixo,
            # andar máximo/limite de caçadas, não têm teto).
            nome = campo.split(":", 1)[1]
            resultado = _definir_hp_pct_conta(nome, max(0, min(100, valor)))
            await event.respond(resultado + "\n\n" + _texto_menu_hp_contas(),
                                buttons=_menu_hp_contas(), parse_mode="markdown")
            return
        if campo == "fuso":
            # 🕐 ajuste de fuso em horas (pedido do usuário 2026-07-23) — PODE
            # ser negativo (diferente dos outros campos numéricos daqui pra
            # baixo, que são sempre >= 0), então trata ANTES do
            # 'valor = max(0, valor)' genérico lá embaixo.
            valor_fuso = max(-23, min(23, valor))
            dados_s = _ler_settings()
            dados_s["CONTROLE_FUSO_AJUSTE_HORAS"] = valor_fuso
            _salvar_settings(dados_s)
            await event.respond(f"✅ Ajuste de fuso: *{valor_fuso:+d}h*.\n\n"
                                + _texto_menu_configuracoes(),
                                buttons=_menu_configuracoes(), parse_mode="markdown")
            return
        valor = max(0, valor)
        _salvar_ajuste_dupla(campo, valor)
        rotulo = "Andar máximo" if campo == "andar_maximo" else "Limite de caçadas"
        await event.respond(f"✅ {rotulo} atualizado pra *{valor}*.\n\n" + _texto_ajustes_dupla(),
                            buttons=_menu_ajustes_dupla(), parse_mode="markdown")

    @client.on(events.CallbackQuery())
    async def _callback(event):
        user_id = event.sender_id
        if not _autorizado(user_id):
            await event.answer("🚫 Você não está autorizado. Mande /registrar no chat.",
                               alert=True)
            return
        _salvar_chat_id(event.chat_id)
        acao = event.data.decode("utf-8")
        if acao == "menu":
            await event.edit(_texto_menu(), buttons=_menu_principal(), parse_mode="markdown")
            await event.answer()
            return
        # "👤 Status por conta": submenu com 1 botão por conta configurada —
        # tem layout de botões PRÓPRIO (não o menu principal), por isso
        # trata à parte, com return antecipado.
        if acao == "status_contas":
            nomes = _nomes_contas()
            texto = ("👤 *Status por conta*\n\nEscolha uma conta:" if nomes
                     else "⚠️ Nenhuma conta configurada ainda (veja a aba chars no painel).")
            await event.edit(texto, buttons=_menu_contas(), parse_mode="markdown")
            await event.answer()
            return
        # "📈 Relatório com abas" (pedido do usuário 2026-07-19): submenu com
        # 1 botão por modo + Por dia — mesmo padrão de "Status por conta".
        if acao == "relatorio":
            await event.edit(formatar_relatorio(), buttons=_menu_relatorio(), parse_mode="markdown")
            await event.answer()
            return
        # "🛒 Mercado" e "📊 Estatísticas" (pedido do usuário 2026-07-20:
        # "ficou MT poluído, vamos reorganizar?") — os 2 novos submenus que
        # tiraram 4 linhas do menu principal.
        if acao == "mercado":
            await event.edit("🛒 *Mercado* — escolha uma opção:", buttons=_menu_mercado(),
                             parse_mode="markdown")
            await event.answer()
            return
        if acao == "estatisticas":
            await event.edit("📊 *Estatísticas* — escolha uma opção:\n\n"
                             "💡 Dica: `/drop nome do item` busca um drop no histórico.",
                             buttons=_menu_estatisticas(), parse_mode="markdown")
            await event.answer()
            return
        if acao == "graficos":
            await event.edit("📊 *Gráficos* — escolha uma opção:",
                             buttons=_menu_graficos(), parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("rel_modo:"):
            chave = acao.split(":", 1)[1]
            info_modo = next((m for m in _MODOS_RELATORIO if m[0] == chave), None)
            if info_modo:
                msg = formatar_relatorio_modo(*info_modo)
            else:
                msg = "⚠️ Modo de relatório desconhecido."
            botoes = [[Button.inline("⬅️ Relatório", b"relatorio"), Button.inline("🏠 Menu", b"menu")]]
            await event.edit(msg, buttons=botoes, parse_mode="markdown")
            await event.answer()
            return
        if acao == "rel_dia":
            botoes = [[Button.inline("⬅️ Relatório", b"relatorio"), Button.inline("🏠 Menu", b"menu")]]
            await event.edit(formatar_relatorio_dia(), buttons=botoes, parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("drops:"):
            pagina_pedida = int(acao.split(":", 1)[1] or 0)
            texto, pagina, total_paginas = formatar_drops_dia(pagina_pedida)
            await event.edit(texto, buttons=_botoes_drops(pagina, total_paginas), parse_mode="markdown")
            await event.answer()
            return
        # "📉 Gráfico do dia" (2026-07-21): manda uma FOTO nova no chat em
        # vez de editar o texto — imagem lê muito melhor no celular que
        # relatório em texto. O botão do submenu fica lá pra gerar de novo.
        if acao == "grafico_dia":
            await event.answer("🎨 Gerando gráfico…")
            caminho, legenda = gerar_grafico_dia()
            if not caminho:
                botoes = [[Button.inline("🔄 Tentar de novo", b"grafico_dia"),
                           Button.inline("⬅️ Gráficos", b"graficos")]]
                await event.edit(legenda, buttons=botoes, parse_mode="markdown")
                return
            try:
                await client.send_file(event.chat_id, caminho, caption=legenda,
                                       parse_mode="markdown")
            except Exception:
                await event.respond("⚠️ Gerei o gráfico mas não consegui mandar a imagem "
                                    "(permissão de mídia no grupo?).")
            return
        # "📊 Gráfico por conta" (pedido do usuário 2026-07-23: "tem como
        # fazer um gráfico também, por personagem, pra comparar entre
        # eles?") — mesmo esquema do Gráfico do dia acima, só que comparando
        # CONTAS entre si em vez de horas do dia.
        if acao == "grafico_contas":
            await event.answer("🎨 Gerando gráfico…")
            caminho, legenda = gerar_grafico_contas()
            if not caminho:
                botoes = [[Button.inline("🔄 Tentar de novo", b"grafico_contas"),
                           Button.inline("⬅️ Gráficos", b"graficos")]]
                await event.edit(legenda, buttons=botoes, parse_mode="markdown")
                return
            try:
                await client.send_file(event.chat_id, caminho, caption=legenda,
                                       parse_mode="markdown")
            except Exception:
                await event.respond("⚠️ Gerei o gráfico mas não consegui mandar a imagem "
                                    "(permissão de mídia no grupo?).")
            return
        # "🔥 Heatmap hora×dia" (2026-07-24): mesmo esquema dos gráficos
        # acima, cruzando hora do dia com dia da semana.
        if acao == "grafico_heatmap":
            await event.answer("🎨 Gerando gráfico…")
            caminho, legenda = gerar_grafico_heatmap()
            if not caminho:
                botoes = [[Button.inline("🔄 Tentar de novo", b"grafico_heatmap"),
                           Button.inline("⬅️ Gráficos", b"graficos")]]
                await event.edit(legenda, buttons=botoes, parse_mode="markdown")
                return
            try:
                await client.send_file(event.chat_id, caminho, caption=legenda,
                                       parse_mode="markdown")
            except Exception:
                await event.respond("⚠️ Gerei o gráfico mas não consegui mandar a imagem "
                                    "(permissão de mídia no grupo?).")
            return
        # "🗓️ Dashboard semanal" (2026-07-24): idem, resumo dos últimos 7 dias
        # em 4 painéis (XP, Gold, execuções por modo, drops por raridade).
        if acao == "grafico_semanal":
            await event.answer("🎨 Gerando gráfico…")
            caminho, legenda = gerar_grafico_semanal()
            if not caminho:
                botoes = [[Button.inline("🔄 Tentar de novo", b"grafico_semanal"),
                           Button.inline("⬅️ Gráficos", b"graficos")]]
                await event.edit(legenda, buttons=botoes, parse_mode="markdown")
                return
            try:
                await client.send_file(event.chat_id, caminho, caption=legenda,
                                       parse_mode="markdown")
            except Exception:
                await event.respond("⚠️ Gerei o gráfico mas não consegui mandar a imagem "
                                    "(permissão de mídia no grupo?).")
            return
        if acao == "silencio_toggle":
            dados_s = _ler_settings()
            novo = not dados_s.get("CONTROLE_SILENCIO_ATIVO", False)
            dados_s["CONTROLE_SILENCIO_ATIVO"] = novo
            _salvar_settings(dados_s)
            await event.answer(f"🌙 Modo silencioso {'ligado' if novo else 'desligado'}.")
            await event.edit(_texto_menu_configuracoes(), buttons=_menu_configuracoes(),
                             parse_mode="markdown")
            return
        if acao == "rotacao_rugido_toggle":
            dados_s = _ler_settings()
            novo = not dados_s.get("TANK_ROTACAO_RUGIDO_ATIVA", True)
            dados_s["TANK_ROTACAO_RUGIDO_ATIVA"] = novo
            _salvar_settings(dados_s)
            # O hunter.py lê esse valor DIRETO do settings.json a cada
            # rodada de combate (cache de 5s) — vale na hora, mesmo com uma
            # masmorra já em andamento, sem precisar reiniciar o bot.
            await event.answer(f"🔁 Rotação de Rugido {'ligada' if novo else 'desligada'} "
                               f"(vale em até 5s, mesmo numa masmorra já rodando).")
            await event.edit(_texto_menu_configuracoes(), buttons=_menu_configuracoes(),
                             parse_mode="markdown")
            return
        if acao.startswith("silencio_editar:"):
            qual = acao.split(":", 1)[1]
            _editando[user_id] = f"silencio:{qual}"
            await event.answer()
            rotulo = "início" if qual == "inicio" else "fim"
            await event.respond(f"🌙 Manda a hora de *{rotulo}* do silêncio "
                                f"(número de 0 a 23, ex: 7):", parse_mode="markdown")
            return
        if acao == "fuso_editar":
            _editando[user_id] = "fuso"
            await event.answer()
            await event.respond("🕐 Manda o ajuste de fuso em HORAS (pode ser negativo) — "
                                "ex: `-1` se o resumo diário está saindo 1h adiantado, "
                                "`1` se está atrasado, `0` pra desligar o ajuste:",
                                parse_mode="markdown")
            return
        if acao.startswith("comprar_pocao:"):
            tipo = acao.split(":", 1)[1]
            # Pré-marca com as contas já em 'Contas que vendem' (mesmo
            # padrão de sempre) — usuário pode ajustar antes de continuar.
            contas_marcadas = set(getattr(config, "MERCADO_CONTAS", None) or [])
            contas_disp = {fone for _nome, fone in _contas_disponiveis_compra()}
            _selecao_compra[user_id] = {"tipo": tipo, "contas": contas_marcadas & contas_disp}
            await event.answer()
            await event.edit(_texto_selecionar_contas_compra(user_id),
                             buttons=_menu_selecionar_contas_compra(user_id), parse_mode="markdown")
            return
        if acao.startswith("comprar_conta_t:"):
            fone = acao.split(":", 1)[1]
            estado = _selecao_compra.get(user_id)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Comprar de novo.")
                return
            if fone in estado["contas"]:
                estado["contas"].discard(fone)
            else:
                estado["contas"].add(fone)
            await event.answer()
            await event.edit(_texto_selecionar_contas_compra(user_id),
                             buttons=_menu_selecionar_contas_compra(user_id), parse_mode="markdown")
            return
        if acao in ("comprar_conta_todas", "comprar_conta_nenhuma"):
            estado = _selecao_compra.get(user_id)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Comprar de novo.")
                return
            if acao == "comprar_conta_todas":
                estado["contas"] = {fone for _nome, fone in _contas_disponiveis_compra()}
            else:
                estado["contas"] = set()
            await event.answer()
            await event.edit(_texto_selecionar_contas_compra(user_id),
                             buttons=_menu_selecionar_contas_compra(user_id), parse_mode="markdown")
            return
        if acao == "comprar_conta_cancelar":
            _selecao_compra.pop(user_id, None)
            await event.answer("Cancelado.")
            await event.edit("🛒 *Mercado* — escolha uma opção:", buttons=_menu_mercado(),
                             parse_mode="markdown")
            return
        if acao == "comprar_conta_continuar":
            estado = _selecao_compra.get(user_id)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Comprar de novo.")
                return
            if not estado["contas"]:
                await event.answer("⚠️ Marca pelo menos 1 conta antes de continuar.")
                return
            nome_item = _ITEM_LOJA_POR_TIPO.get(estado["tipo"], estado["tipo"])
            _editando[user_id] = f"comprar_pocao:{estado['tipo']}"
            await event.answer()
            n = len(estado["contas"])
            await event.respond(f"🧪 Quantas *{nome_item}* comprar? ({n} conta(s) "
                                f"selecionada(s)). Manda o número (ex: 500):",
                                parse_mode="markdown")
            return
        # 🛒 Vender agora — seleção de contas (pedido do usuário 2026-07-23:
        # "tem como selecionar lá quem vende?"), mesmo padrão da compra acima.
        if acao == "vender_agora":
            contas_marcadas = set(getattr(config, "MERCADO_CONTAS", None) or [])
            contas_disp = {fone for _nome, fone in _contas_disponiveis_compra()}
            _selecao_venda[user_id] = {"contas": contas_marcadas & contas_disp}
            await event.answer()
            await event.edit(_texto_selecionar_contas_venda(user_id),
                             buttons=_menu_selecionar_contas_venda(user_id), parse_mode="markdown")
            return
        if acao.startswith("vender_conta_t:"):
            fone = acao.split(":", 1)[1]
            estado = _selecao_venda.get(user_id)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Vender agora de novo.")
                return
            if fone in estado["contas"]:
                estado["contas"].discard(fone)
            else:
                estado["contas"].add(fone)
            await event.answer()
            await event.edit(_texto_selecionar_contas_venda(user_id),
                             buttons=_menu_selecionar_contas_venda(user_id), parse_mode="markdown")
            return
        if acao in ("vender_conta_todas", "vender_conta_nenhuma"):
            estado = _selecao_venda.get(user_id)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Vender agora de novo.")
                return
            if acao == "vender_conta_todas":
                estado["contas"] = {fone for _nome, fone in _contas_disponiveis_compra()}
            else:
                estado["contas"] = set()
            await event.answer()
            await event.edit(_texto_selecionar_contas_venda(user_id),
                             buttons=_menu_selecionar_contas_venda(user_id), parse_mode="markdown")
            return
        if acao == "vender_conta_cancelar":
            _selecao_venda.pop(user_id, None)
            await event.answer("Cancelado.")
            await event.edit("🛒 *Mercado* — escolha uma opção:", buttons=_menu_mercado(),
                             parse_mode="markdown")
            return
        if acao == "vender_conta_continuar":
            estado = _selecao_venda.pop(user_id, None)
            if not estado:
                await event.answer("⚠️ Sessão perdida — clica em Vender agora de novo.")
                return
            if not estado["contas"]:
                await event.answer("⚠️ Marca pelo menos 1 conta antes de vender.")
                _selecao_venda[user_id] = estado   # devolve o estado, não perde a seleção
                return
            await event.answer("🛒 Enviando pedido…")
            msg = vender_agora(contas=sorted(estado["contas"]))
            await event.edit(msg, buttons=[[Button.inline("⬅️ Mercado", b"mercado")]],
                             parse_mode="markdown")
            return
        if acao in ("ranking_dia", "ranking_dano", "tempo_medio", "estoque_pocoes", "progresso_nivel",
                    "progresso_masmorra", "ajustes_atuais", "comparativo_contas", "xp_total",
                    "relatorio_dragao", "xp_real_hoje", "eficiencia_conteudo"):
            _formatadores = {
                "ranking_dia": formatar_ranking_dia, "ranking_dano": formatar_ranking_dano_dia,
                "tempo_medio": formatar_tempo_medio,
                "estoque_pocoes": formatar_estoque_pocoes, "progresso_nivel": formatar_progresso_nivel,
                "progresso_masmorra": formatar_progresso_masmorra,
                "ajustes_atuais": formatar_ajustes_atuais,
                "comparativo_contas": formatar_comparativo_contas,
                "xp_total": formatar_xp_total,
                "relatorio_dragao": formatar_relatorio_dragao,
                "xp_real_hoje": formatar_xp_real_hoje,
                "eficiencia_conteudo": formatar_eficiencia_por_conteudo,
            }
            botoes = [[Button.inline("🔄 Atualizar", acao.encode("utf-8")),
                       Button.inline("⬅️ Estatísticas", b"estatisticas")]]
            await event.edit(_formatadores[acao](), buttons=botoes, parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("conta:"):
            nome = acao.split(":", 1)[1]
            msg = formatar_status_conta(nome)
            pausada = nome in _contas_pausadas()
            botoes = [[Button.inline("▶️ Retomar" if pausada else "⏸️ Pausar",
                                     f"pausar_conta:{nome}".encode("utf-8"))],
                      [Button.inline("⬅️ Outras contas", b"status_contas"),
                       Button.inline("🏠 Menu", b"menu")]]
            if pausada:
                msg += ("\n\n⏸️ *Esta conta está PAUSADA.* Em conteúdo independente "
                       "(Solo/Missão Oásis/Observador) só ela para, as outras seguem. "
                       "Em conteúdo de GRUPO (Masmorra/Caçada Dupla/Cripta/Templo/"
                       "Fortaleza), o grupo inteiro dela para no próximo ponto seguro "
                       "(mesma lógica do 'Parar no fim') — não dá pra tirar 1 membro "
                       "no meio da luta sem travar os outros.")
            await event.edit(msg, buttons=botoes, parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("pausar_conta:"):
            nome = acao.split(":", 1)[1]
            novo_estado = _alternar_pausa_conta(nome)
            await event.answer(f"{'⏸️ Pausada' if novo_estado else '▶️ Retomada'}: {nome}")
            msg = formatar_status_conta(nome)
            pausada = nome in _contas_pausadas()
            botoes = [[Button.inline("▶️ Retomar" if pausada else "⏸️ Pausar",
                                     f"pausar_conta:{nome}".encode("utf-8"))],
                      [Button.inline("⬅️ Outras contas", b"status_contas"),
                       Button.inline("🏠 Menu", b"menu")]]
            if pausada:
                msg += ("\n\n⏸️ *Esta conta está PAUSADA.* Em conteúdo independente "
                       "(Solo/Missão Oásis/Observador) só ela para, as outras seguem. "
                       "Em conteúdo de GRUPO (Masmorra/Caçada Dupla/Cripta/Templo/"
                       "Fortaleza), o grupo inteiro dela para no próximo ponto seguro "
                       "(mesma lógica do 'Parar no fim') — não dá pra tirar 1 membro "
                       "no meio da luta sem travar os outros.")
            await event.edit(msg, buttons=botoes, parse_mode="markdown")
            return
        # "🎮 Escolher conteúdo": submenu com os modos + atalho pra Caçada
        # em Dupla (que tem sub-submenu próprio, ver "conteudo_dupla").
        if acao == "conteudo":
            await event.edit(_texto_menu_conteudo(), buttons=_menu_conteudo(), parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("modo:"):
            modo = acao.split(":", 1)[1]
            _definir_modo(modo)
            await event.answer("✅ Conteúdo trocado! Reinicie o bot (Parar + Iniciar) pra valer.",
                               alert=True)
            await event.edit(_texto_menu_conteudo(), buttons=_menu_conteudo(), parse_mode="markdown")
            return
        if acao == "conteudo_dupla":
            await event.edit(_texto_menu_duplas(), buttons=_menu_duplas(), parse_mode="markdown")
            await event.answer()
            return
        if acao == "conteudo_masmorra":
            await event.edit(_texto_menu_masmorra(), buttons=_menu_masmorra(), parse_mode="markdown")
            await event.answer()
            return
        if acao == "conteudo_missao_oasis":
            await event.edit(_texto_contas_missao_oasis(), buttons=_menu_contas_missao_oasis(),
                             parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("mo_conta_t:"):
            fone = acao.split(":", 1)[1]
            novo_estado, avisar_item = _alternar_conta_missao_oasis(fone)
            if avisar_item:
                await event.answer("✅ Ligada — falta escolher o item-alvo dela no painel.", alert=True)
            else:
                await event.answer()
            await event.edit(_texto_contas_missao_oasis(), buttons=_menu_contas_missao_oasis(),
                             parse_mode="markdown")
            return
        if acao.startswith("masmorra:"):
            indice = int(acao.split(":", 1)[1])
            resultado = _definir_masmorra(indice)
            await event.answer(resultado, alert=True)
            await event.edit(_texto_menu_masmorra(), buttons=_menu_masmorra(), parse_mode="markdown")
            return
        if acao.startswith("dupla:"):
            indice = int(acao.split(":", 1)[1])
            resultado = _alternar_dupla(indice)
            await event.answer(resultado)
            await event.edit(_texto_menu_duplas(), buttons=_menu_duplas(), parse_mode="markdown")
            return
        if acao == "ajustes_dupla":
            await event.edit(_texto_ajustes_dupla(), buttons=_menu_ajustes_dupla(), parse_mode="markdown")
            await event.answer()
            return
        if acao == "hp_contas":
            await event.edit(_texto_menu_hp_contas(), buttons=_menu_hp_contas(), parse_mode="markdown")
            await event.answer()
            return
        if acao == "configuracoes":
            await event.edit(_texto_menu_configuracoes(), buttons=_menu_configuracoes(),
                             parse_mode="markdown")
            await event.answer()
            return
        if acao.startswith("alerta:"):
            chave = acao.split(":", 1)[1]
            resultado = _alternar_alerta(chave)
            await event.answer(resultado)
            await event.edit(_texto_menu_configuracoes(), buttons=_menu_configuracoes(),
                             parse_mode="markdown")
            return
        if acao.startswith("hp_pct:"):
            nome = acao.split(":", 1)[1]
            _editando[user_id] = f"hp_pct:{nome}"
            await event.answer()
            await event.respond(f"✏️ Digite o novo HP% de poção (0-100) pra *{nome}* "
                                f"e mande como mensagem normal aqui:", parse_mode="markdown")
            return
        if acao.startswith("editar:"):
            campo = acao.split(":", 1)[1]
            _editando[user_id] = campo
            rotulo = "andar máximo" if campo == "andar_maximo" else "limite de caçadas (0 = sem limite)"
            await event.answer()
            await event.respond(f"✏️ Digite o novo valor de *{rotulo}* (só número) "
                                f"e mande como mensagem normal aqui:", parse_mode="markdown")
            return
        if acao == "iniciar":
            msg = iniciar_bot()
        elif acao == "parar_agora":
            msg = parar_bot_agora()
        elif acao == "parar_no_fim":
            msg = alternar_parar_no_fim()
        elif acao == "parar_e_sair":
            msg = parar_e_sair()
        elif acao == "status":
            msg = formatar_status()
        elif acao == "mapas":
            msg = formatar_mapas()
        elif acao == "ler_inventario":
            msg = ler_inventario_agora()
        elif acao == "ver_log":
            msg = ver_log()
        else:
            msg = "⚠️ Ação desconhecida."
        await event.answer()
        # edita a mensagem do menu pra mostrar o resultado + o menu atualizado
        # de novo embaixo (evita poluir o chat com uma mensagem nova a cada clique)
        await event.edit(f"{msg}\n\n—\n{_texto_menu()}", buttons=_menu_principal(),
                         parse_mode="markdown")

    asyncio.create_task(monitorar_bot(client))
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
