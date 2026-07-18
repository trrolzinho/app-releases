# -*- coding: utf-8 -*-
# =====================================================================
#  TofuBot — painel (app). Configura contas, faz login, roda o bot,
#  mostra relatorio de masmorras. Janela nativa (Tkinter).
#  (ADAPTADO PARA LINUX)
# =====================================================================

import datetime
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
import importlib
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext

import config

APP_NAME = "TofuBot"
APP_SUB = "Automação de Masmorra — Teletofus (Trroolzin Edition)"

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE = app_dir()
SETTINGS = os.path.join(BASE, "settings.json")
RUN_LOG = os.path.join(BASE, "run.log")
RELATORIO = os.path.join(BASE, "relatorio.json")
STATUS_FILE = os.path.join(BASE, "status.json")
ESTIMATIVA_FILE = os.path.join(BASE, "estimativa.json")
STATUS_MAX_IDADE = 30   # segundos: status mais velho que isso = considera "sem dado"
BOT_EXE = os.path.join(BASE, "bot.exe" if os.name == "nt" else "bot")   # empacotado
HUNTER_PY = os.path.join(BASE, "hunter.py")
INICIAR_CMD = os.path.join(BASE, "iniciar.cmd")   # launcher clássico do Windows
PARAR_NO_FIM_FLAG = os.path.join(BASE, "parar_no_fim.flag")
VENDER_AGORA_FLAG = os.path.join(BASE, "vender_agora.flag")
VENDER_E_SAIR_FLAG = os.path.join(BASE, "vender_e_sair.flag")
LER_INVENTARIO_FLAG = os.path.join(BASE, "ler_inventario.flag")
LER_INVENTARIO_E_SAIR_FLAG = os.path.join(BASE, "ler_inventario_e_sair.flag")
BOT_PID_FILE = os.path.join(BASE, "bot.pid")   # escrito pelo hunter.py/bot.exe

# Windows OU Linux/Mac — bot_rodando/iniciar/parar abaixo se adaptam sozinhos.
IS_WINDOWS = (os.name == "nt")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def _ler_pid(caminho):
    """PID do arquivo, ou None (sem arquivo / conteúdo inválido)."""
    try:
        with open(caminho) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_do_bot_vivo():
    """PID do bot DESTA pasta se ele estiver mesmo rodando, senão None.
    Multi-instância: cada PASTA tem seu próprio bot.pid, então o painel só
    controla o bot desta pasta — nunca mata bot de outra pasta/instância.
    Confere que o PID realmente existe E parece ser o bot (proteção contra
    PID reciclado pelo SO depois de um bot.pid velho)."""
    pid = _ler_pid(BOT_PID_FILE)
    if not pid:
        return None
    if IS_WINDOWS:
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW)
            linha = (out.stdout or "").lower()
            if "bot.exe" in linha or "python" in linha or "py.exe" in linha:
                return pid
        except Exception:
            pass
        return None
    # Linux/Mac: /proc/<pid>/cmdline existe e contém 'hunter.py' (ou é o
    # bot empacotado) = processo vivo e é o nosso mesmo.
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="ignore")
        if "hunter.py" in cmdline or os.path.basename(BOT_EXE) in cmdline:
            return pid
    except Exception:
        return None
    return None

# --- Paleta ESCURA (tema completo do app, trazido da versão do colega) ----
BG     = "#1b1c27"   # fundo geral da janela
PANEL  = "#232533"   # superfícies (LabelFrame, campos)
CARD   = "#2a2d40"   # cartões destacados ("Hoje")
ROW_A  = "#232533"   # zebra de tabela (par)
ROW_B  = "#2b2e42"   # zebra de tabela (ímpar)
BORDER = "#3a3d5c"   # bordas
FG     = "#e6e8f0"   # texto principal (claro)
MUTED  = "#9aa0b4"   # texto secundário
HEAD   = "#14151e"   # barra de topo (mais escura)
BTN     = "#333650"  # botão neutro
BTN_HOV = "#40446a"  # botão neutro (hover/active)
NENHUMA = "(nenhuma)"   # Texto padrão para ausência de alma

# Cores dos botões
BLUE = "#4a9eff"
ORANGE = "#ff9e57"
GREEN = "#5ed88a"
RED = "#ff6b6b"
YELLOW = "#f4c542"

# Configurações de jogo. ROLES precisa ter TODOS os papéis que existem no
# config.SOULS_CATALOG (lanceiro/berserker inclusive — usados pelo Zul'gor).
ROLES = ["tank", "suporte", "dps", "arqueiro", "lanceiro", "berserker"]
# TONICO_OPC: os 3 Super Tônicos (dura 10 min) e os 3 normais (dura 30 min) —
# os nomes têm que bater com as chaves de TONICO_SUBS/TONICO_DURACAO_MIN no
# hunter.py.
TONICO_OPC = {
    "Nenhum": "",
    "Super Tônico de Força": "super_forca",
    "Super Tônico de Defesa": "super_defesa",
    "Super Tônico de Precisão": "super_precisao",
    "Tônico de Força (30min)": "forca",
    "Tônico de Defesa (30min)": "defesa",
    "Tônico de Precisão (30min)": "precisao",
}
# ELIXIR_OPC: existem os dois no jogo — o normal e o Super (mesma duração, 30
# min; só muda a % de XP que cada um dá).
ELIXIR_OPC = {
    "Nenhum": "",
    "Elixir Sabedoria": "normal",
    "Super Elixir Sabedoria": "super",
}

# --- Alias: agora o app INTEIRO é escuro, então o Relatório usa a MESMA
# paleta (mantidos os nomes REL_* só pra não precisar tocar em cada referência
# já escrita antes).
REL_BG = BG
REL_CARD = CARD
REL_BORDER = BORDER
REL_TXT = FG
REL_MUTED = MUTED

# Cores de raridade dos Equipamentos
RARIDADE_CORES = {
    "normal": "#3ecf6d",     # verde
    "incomum": "#3ea6ff",    # azul
    "raro": "#b768ff",       # roxo
    "epico": "#ffd23e",      # amarelo
    "lendario": "#ff9d2f",   # laranja
}
RARIDADE_ORDEM = ["normal", "incomum", "raro", "epico", "lendario"]
RARIDADE_LABEL = {"normal": "Normal", "incomum": "Incomum", "raro": "Raro",
                   "epico": "Épico", "lendario": "Lendário"}


def bot_cmd():
    """Comando pra rodar o bot ESCONDIDO: bot(.exe) empacotado, ou o hunter.py
    usando o MESMO Python que está rodando este painel (sys.executable — se o
    painel foi aberto pela venv, o bot também usa a venv; funciona igual em
    qualquer sistema, sem precisar caçar o caminho do venv na mão)."""
    if os.path.exists(BOT_EXE):
        return [BOT_EXE]
    return [sys.executable, "-u", HUNTER_PY]

def _formatar_duracao_painel(segundos):
    """Formata segundos em texto legível: '45s', '12min 34s', '1h 05min'."""
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


# Palavras que aparecem em nomes de EQUIPAMENTO (arma/armadura/acessório) —
# usado só quando o item ainda não teve a raridade aprendida (nunca visto
# num drop com a bolinha colorida) pra não cair errado em "Consumíveis"
# (bug real: 'Varinha do Aprendiz'/'Báculo do Dragão de Gelo' apareciam como
# consumível só por falta de raridade conhecida, embora claramente sejam
# arma). Cobre os equipamentos vistos até agora no jogo; ir completando
# conforme aparecerem itens novos não reconhecidos.
EQUIPAMENTO_PALAVRAS = (
    "espada", "arco", "machado", "cajado", "varinha", "baculo", "báculo",
    "lanca", "lança", "adaga", "besta", "escudo", "armadura", "elmo",
    "capacete", "luva", "bota", "passos", "anel", "colar", "amuleto", "manto",
    "peitoral", "couraca", "couraça", "botas", "luvas", "lamina", "lâmina",
)
# Equipamento quase sempre tem os atributos escritos no próprio nome, tipo
# "Passos do Sol (DEF+7, HP+5)" — nenhum consumível tem esse padrão. Serve de
# reforço pra pegar itens cujo nome não bate com nenhuma palavra-chave da
# lista acima (ex: "Passos do Sol" não tem "bota"/"luva"/etc no nome).
ATRIBUTO_EQUIP_RE = re.compile(
    r"\((?:atk|def|crit|hp|precisao|precisão)\s*[+\-]\s*\d+", re.IGNORECASE)

# BUG REAL corrigido (2026-07-12): o próprio jogo mostra uma bolinha de
# raridade (🟢 = normal) até em consumíveis comuns, tipo "🟢 Poção de Vida
# x3" — o bot aprendia essa cor igual aprende de equipamento de verdade, e aí
# "Poção de Vida"/"Chave de Masmorra" apareciam coloridos no quadro de
# EQUIPAMENTOS (errado). Esses nomes CONHECIDOS de consumível/recurso agora
# são checados ANTES do catálogo aprendido — nunca viram equipamento, mesmo
# que tenham raridade aprendida. Lista crescendo conforme aparecem novos.
CONSUMIVEL_CONHECIDOS = frozenset(n.lower() for n in (
    "Poção de Vida", "Poção de Energia",
    "Chave de Masmorra", "Chave de Ossos", "Chave das Minas",
    "Minério do Dragão", "Minério do Dragão ✦", "Minério do Dragão ✦✦",
    "Minério do Dragão ✦✦✦",
    "Tônico de Força", "Tônico de Defesa", "Tônico de Precisão",
    "Super Tônico de Força", "Super Tônico de Defesa", "Super Tônico de Precisão",
    "Elixir de Sabedoria", "Super Elixir de Sabedoria", "Elixir da Fortuna",
    "Flor de Karkto Feroz", "Flor do Karkto Feroz", "Garra de Lagarto da Areia",
    "Dente de Cobra do Deserto", "Rosa Carmesim", "Totem Obscuro",
    "Bolsa Misteriosa", "Bússola Ancestral", "Colar da Paz (Huaguilli)",
    "Martelo Mágico do Gibby", "Poeira Estrelar", "Pó de Ossos",
    "Saco das Almas", "Livro de Sortilégios", "Golpe do Obelisco",
))


def _parece_equipamento(nome_item: str) -> bool:
    n = nome_item.lower()
    if ATRIBUTO_EQUIP_RE.search(n):
        return True
    return any(p in n for p in EQUIPAMENTO_PALAVRAS)


def _raridade_do_item(nome_item, catalogo_aprendido=None):
    """(raridade, nome_bonito, cor) pro item, ou None (cai pra consumível).
    Prioridade: 0) lista CONSUMIVEL_CONHECIDOS — nunca vira equipamento,
    mesmo com raridade aprendida (o jogo dá bolinha verde até pra poção);
    1) catálogo APRENDIDO automaticamente (relatorio.json — o hunter.py grava
    isso sozinho, a partir da cor real que o jogo mostra em cada drop);
    2) catálogo manual em config.ITENS_RARIDADE (reserva, pra itens antigos
    já registrados antes de existir o aprendizado automático); 3) palavra-
    chave no nome (_parece_equipamento) — se claramente é uma arma/armadura
    mas a raridade ainda não foi aprendida, cai no quadro de EQUIPAMENTOS
    mesmo assim (cor cinza, 'raridade desconhecida'), em vez de ser
    classificado errado como consumível."""
    nome_limpo = nome_item.strip(" ✦").strip()
    if nome_limpo.lower() in CONSUMIVEL_CONHECIDOS:
        return None
    raridade = (catalogo_aprendido or {}).get(nome_limpo)
    if not raridade:
        raridade = (getattr(config, "ITENS_RARIDADE", {}) or {}).get(nome_limpo)
    if not raridade:
        if _parece_equipamento(nome_limpo):
            return ("desconhecida", "Raridade desconhecida", MUTED)
        return None
    raridade = raridade.lower()
    cor = RARIDADE_CORES.get(raridade)
    if not cor:
        return None
    return (raridade, RARIDADE_LABEL.get(raridade, raridade.capitalize()), cor)


# --- Atualização automática (GitHub Releases) -------------------------
def _ssl_ctx():
    """Contexto SSL pra falar com a API do GitHub (checar/baixar atualização).
    BUG REAL visto em produção: num PC (rodando o painel.exe compilado), a
    checagem falhava com 'CERTIFICATE_VERIFY_FAILED: unable to get local
    issuer certificate' — o certifi (biblioteca que traz os certificados-raiz
    confiáveis, já que o Windows não empresta o dele pro Python sozinho
    sempre) não tinha o arquivo de certificados incluído certinho dentro do
    .exe compilado (precisa ser empacotado à parte pelo PyInstaller — ver
    gerar_exe.bat, opção --collect-data=certifi). Agora, além do certifi,
    tenta SOMAR os certificados que o Windows já confia (loja de certificados
    do sistema) como reforço — cobre o caso de o certifi falhar mesmo assim
    (ex: máquina corporativa com certificado próprio no meio do caminho)."""
    ctx = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    if ctx is None:
        ctx = ssl.create_default_context()
    if IS_WINDOWS:
        try:
            for cert_der, _encoding, _trust in ssl.enum_certificates("ROOT"):
                try:
                    ctx.load_verify_locations(cadata=cert_der)
                except Exception:
                    pass
        except Exception:
            pass
    return ctx

def _parse_ver(v):
    v = (v or "").strip().lstrip("vV")
    partes = []
    for p in v.split("."):
        num = "".join(c for c in p if c.isdigit())
        partes.append(int(num) if num else 0)
    partes = (partes + [0, 0, 0, 0])[:4]
    return tuple(partes)

def _github_latest(repo):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": "AppUpdater",
        "Accept": "application/vnd.github+json",
    })
    ctx = _ssl_ctx()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        data = json.load(r)
    tag = data.get("tag_name") or data.get("name") or ""
    assets = data.get("assets") or []
    zips = [a for a in assets if (a.get("name") or "").lower().endswith(".zip")]
    alvo = (getattr(config, "UPDATE_ASSET", "") or "").strip().lower()

    def _url(a):
        return a.get("browser_download_url") or ""

    zip_url = ""
    if alvo:
        for a in zips:
            if alvo in (a.get("name") or "").lower():
                zip_url = _url(a)
                break
    if not zip_url:
        for a in zips:
            if "atualiza" in (a.get("name") or "").lower():
                zip_url = _url(a)
                break
    if not zip_url and zips:
        zip_url = _url(zips[0])
    return tag, zip_url, data.get("html_url", "")


# Arquivos que NUNCA são sobrescritos por uma atualização (dados/config do
# usuário — sessões, progresso, relatório, log). Só código (.py/.cmd/.sh) é
# trocado.
_ATUALIZACAO_PROTEGIDOS = {
    "settings.json", "relatorio.json", "status.json", "run.log",
    "bot.pid", "parar_no_fim.flag", "sessao_continuar.flag",
    "sessao_baseline.txt",
}


def _atualizacao_arquivo_protegido(nome: str) -> bool:
    if nome in _ATUALIZACAO_PROTEGIDOS:
        return True
    if nome.startswith("sessao_progresso"):
        return True
    if nome.endswith(".session") or nome.endswith(".session-journal"):
        return True
    return False

def _nome_alma(rotulo):
    if not rotulo or rotulo == NENHUMA:
        return None
    return rotulo.rsplit(" (CD", 1)[0].strip()

def _mesmo_numero(a, b):
    da = "".join(c for c in (a or "") if c.isdigit())
    db = "".join(c for c in (b or "") if c.isdigit())
    if not da or not db:
        return False
    if da == db:
        return True
    curto, longo = (da, db) if len(da) <= len(db) else (db, da)
    return len(curto) >= 9 and longo.endswith(curto)

def _apagar_sessao(fone):
    import config
    base = config.session_path(BASE, fone)
    for ext in (".session", ".session-journal"):
        try:
            if os.path.exists(base + ext):
                os.remove(base + ext)
        except Exception:
            pass

def carregar():
    if os.path.exists(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    d = {"API_ID": "", "API_HASH": "", "BOT_USERNAME": "", "SALA_SENHA": "1234",
         "ACCOUNTS": [], "TANK_HEAL_RATIO": 0.40, "TANK_CRITICAL_RATIO": 0.15,
         "OTHER_HEAL_RATIO": 0.40, "BETWEEN_DG_HEAL_RATIO": 0.85, "MAX_DUNGEONS": 0}
    try:
        import config
        d.update({"API_ID": config.API_ID or "", "API_HASH": config.API_HASH,
                  "BOT_USERNAME": config.BOT_USERNAME, "SALA_SENHA": config.SALA_SENHA,
                  "ACCOUNTS": config.ACCOUNTS, "MAX_DUNGEONS": getattr(config, "MAX_DUNGEONS", 0)})
    except Exception:
        pass
    return d

def bot_rodando():
    """True se o bot DESTA pasta está rodando (pelo bot.pid) — funciona tanto
    no Windows quanto no Linux. Durante o reinício automático (alguns segundos)
    pode aparecer 'Parado' por um instante — normal."""
    return _pid_do_bot_vivo() is not None

class SecaoRecolhivel(tk.Frame):
    """Seção com um cabeçalho clicável (▼/▶) que mostra/esconde o conteúdo.
    Serve pra 'minimizar' blocos do painel que ocupam espaço (ex: Credenciais,
    que depois de configurada quase nunca muda; ou a lista de Contas, que
    depois de configurada também raramente precisa ficar visível). Coloque os
    widgets DENTRO de `.corpo`. 'fill'/'expand': como o corpo é empacotado
    quando aberto (padrão fill="x" — passe fill="both", expand=True pra
    seções que devem crescer verticalmente, tipo uma lista longa)."""
    def __init__(self, parent, titulo, aberto=True, fill="x", expand=False, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._titulo = titulo
        self._aberto = aberto
        self._fill = fill
        self._expand = expand
        self.btn = tk.Button(self, bg=BTN, fg=FG, activebackground=BTN_HOV,
                             activeforeground=FG, relief="flat", anchor="w",
                             font=("Segoe UI", 9, "bold"), cursor="hand2",
                             command=self._toggle)
        self.btn.pack(fill="x")
        self.corpo = tk.Frame(self, bg=BG, highlightthickness=1,
                              highlightbackground=BORDER)
        self._render()
        if aberto:
            self.corpo.pack(fill=self._fill, expand=self._expand)

    def _render(self):
        self.btn.config(text=("▼  " if self._aberto else "▶  ") + self._titulo)

    def _toggle(self):
        self._aberto = not self._aberto
        self._render()
        if self._aberto:
            self.corpo.pack(fill=self._fill, expand=self._expand)
        else:
            self.corpo.pack_forget()


class ContaCard:
    def __init__(self, parent, painel, dados, on_remover=None):
        self.painel = painel
        self.on_remover = on_remover
        # Título da moldura = nome da conta (ajuda a escanear a lista rápido,
        # em vez de todos os cartões ficarem com título em branco iguais).
        self.frame = ttk.LabelFrame(parent, text=f" {dados.get('name', '').strip() or 'Conta'} ")
        self.frame.pack(fill="x", padx=4, pady=8)

        # marca se esta conta ENTRA na masmorra (as desmarcadas ficam logadas,
        # só não participam) — resposta prática pra "tenho 5 personagens, só
        # quero levar 4": desmarca em vez de apagar telefone/personagem.
        # Default marcado; compatível com saves antigos (sem 'ativa'). SEM
        # checkbox aqui de propósito — Configuração é só pra configurar; a
        # SELEÇÃO de quem vai em cada conteúdo mora na aba específica dele
        # (Masmorras > Masmorra tem sua própria lista, que usa ESTA MESMA
        # variável — ver _rebuild_masmorra_selector).
        self.ativa = tk.BooleanVar(value=dados.get("ativa", True))

        linha1 = tk.Frame(self.frame, bg=BG)
        linha1.pack(fill="x", padx=6, pady=(4, 0))
        for j, txt in enumerate(["Nome", "Telefone (+55...)", "Papel", "Personagem", "Login", ""]):
            tk.Label(linha1, text=txt, bg=BG, font=("Segoe UI", 8, "bold")).grid(row=0, column=j, padx=4)

        self.nome = ttk.Entry(linha1, width=13)
        self.nome.insert(0, dados.get("name", ""))
        self.nome.bind("<KeyRelease>", lambda e: self.frame.config(
            text=f" {self.nome.get().strip() or 'Conta'} "))
        self.fone = ttk.Entry(linha1, width=17)
        self.fone.insert(0, dados.get("phone", ""))
        self.papel = ttk.Combobox(linha1, width=10, values=ROLES, state="readonly")
        self.papel.set(dados.get("role", ROLES[0]))
        self.perso = ttk.Entry(linha1, width=15)
        self.perso.insert(0, dados.get("char_name", ""))
        self.login_lbl = tk.Label(linha1, text="—", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        if on_remover is not None:
            self.remove_btn = tk.Button(linha1, text="✕", command=self._remover, bg=RED,
                                         fg="white", activebackground=RED, activeforeground="white",
                                         relief="flat", width=2, cursor="hand2")
        else:
            self.remove_btn = None

        self.nome.grid(row=1, column=0, padx=4, pady=3)
        self.fone.grid(row=1, column=1, padx=4, pady=3)
        self.papel.grid(row=1, column=2, padx=4, pady=3)
        self.perso.grid(row=1, column=3, padx=4, pady=3)
        self.login_lbl.grid(row=1, column=4, padx=4)
        if self.remove_btn is not None:
            self.remove_btn.grid(row=1, column=5, padx=4)

        self.papel.bind("<<ComboboxSelected>>", lambda e: self._rebuild_souls())

        # Sem barra de HP aqui (era redundante) — o HP ao vivo de todas as
        # contas já aparece junto na aba Configuração, no painel "❤ Status
        # ao vivo (HP)" (com o botão "🔍 Ampliar" pra ver bem grande).
        self.hp_canvas = None
        self.hp_text = None

        self.souls_frame = tk.Frame(self.frame, bg=BG)
        self.souls_frame.pack(fill="x", padx=6, pady=(2, 6))
        self.soul_vars = {}
        self._selecao_inicial = dados.get("souls")
        self._tonico_inicial = dados.get("tonico", "")
        self._elixir_inicial = (dados.get("elixir") or "")
        self.alma1 = self.alma2 = self.tonico_cb = self.elixir_cb = None
        self._rebuild_souls()

    MAX_ALMAS = 2

    def _rotulo(self, nome, catalogo):
        for n, cd in catalogo:
            if n == nome:
                return f"{n} (CD{cd})"
        return NENHUMA

    def _rebuild_souls(self):
        ton_atual = self._tonico_inicial
        if self.tonico_cb is not None:
            ton_atual = TONICO_OPC.get(self.tonico_cb.get(), "")
        elixir_atual = self._elixir_inicial
        if getattr(self, "elixir_cb", None) is not None:
            elixir_atual = ELIXIR_OPC.get(self.elixir_cb.get(), "")
        for w in self.souls_frame.winfo_children():
            w.destroy()
        role = self.papel.get()
        catalogo = config.SOULS_CATALOG.get(role, [])
        opcoes = [NENHUMA] + [f"{n} (CD{cd})" for n, cd in catalogo]

        sel = self._selecao_inicial
        if sel is None:
            sel = [n for n, _ in config.DEFAULT_SOULS.get(role, [])]
        nomes_papel = [n for n, _ in catalogo]
        sel = [n for n in sel if n in nomes_papel][:self.MAX_ALMAS]
        a1 = sel[0] if len(sel) >= 1 else None
        a2 = sel[1] if len(sel) >= 2 else None

        tk.Label(self.souls_frame, text="Almas (1ª / 2ª):", bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.alma1 = ttk.Combobox(self.souls_frame, width=19, values=opcoes, state="readonly")
        self.alma1.set(self._rotulo(a1, catalogo))
        self.alma1.pack(side="left", padx=(3, 3))
        self.alma2 = ttk.Combobox(self.souls_frame, width=19, values=opcoes, state="readonly")
        self.alma2.set(self._rotulo(a2, catalogo))
        self.alma2.pack(side="left", padx=(0, 10))

        tk.Label(self.souls_frame, text="Tônico:", bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self.tonico_cb = ttk.Combobox(self.souls_frame, width=24,
                                       values=list(TONICO_OPC.keys()), state="readonly")
        rot_ton = next((k for k, v in TONICO_OPC.items() if v == (ton_atual or "")), "Nenhum")
        self.tonico_cb.set(rot_ton)
        self.tonico_cb.pack(side="left", padx=2)

        # Elixir de Sabedoria: dura 30 min (o Tônico dura só 10) — existem o
        # normal E o Super (mesma duração, % de XP diferente). Vale pra
        # Caçada Solo E Caçada em Dupla.
        tk.Label(self.souls_frame, text="Elixir:", bg=BG,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 2))
        self.elixir_cb = ttk.Combobox(self.souls_frame, width=20,
                                      values=list(ELIXIR_OPC.keys()), state="readonly")
        rot_elx = next((k for k, v in ELIXIR_OPC.items() if v == (elixir_atual or "")), "Nenhum")
        self.elixir_cb.set(rot_elx)
        self.elixir_cb.pack(side="left", padx=2)

        self._selecao_inicial = None
        self._tonico_inicial = ""

    def _remover(self):
        if self.on_remover:
            self.on_remover(self)

    def destroy(self):
        self.frame.destroy()

    def set_hp(self, hp, hp_max):
        # A barra de HP saiu do cartão (ver __init__) — o HP ao vivo agora só
        # aparece no painel "Status ao vivo" da aba Configuração. Mantido como
        # no-op pra não quebrar quem ainda chama card.set_hp(...) por aí.
        pass

    def coletar(self):
        nome = self.nome.get().strip()
        fone = self.fone.get().strip()
        papel = self.papel.get().strip()
        perso = self.perso.get().strip()
        if not fone and not perso:
            return None
        if not fone or not perso:
            raise ValueError(f"Conta '{nome or papel}': preencha telefone e personagem "
                              f"(ou deixe os dois em branco pra não usar essa conta).")
        souls = []
        for combo in (self.alma1, self.alma2):
            n = _nome_alma(combo.get() if combo else "")
            if n and n not in souls:
                souls.append(n)
        tonico = TONICO_OPC.get(self.tonico_cb.get() if self.tonico_cb else "Nenhum", "")
        return {"name": nome or papel, "phone": fone, "role": papel,
                "char_name": perso, "souls": souls, "tonico": tonico,
                "ativa": self.ativa.get(),
                "elixir": ELIXIR_OPC.get(self.elixir_cb.get() if self.elixir_cb else "Nenhum", "")}


class Painel:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("760x920")
        root.minsize(700, 700)
        root.configure(bg=BG)

        # --- Tema escuro GLOBAL -----------------------------------------
        # tk_setPalette muda a cor PADRÃO de widgets tk.* (Label, Frame,
        # Checkbutton, Entry...) sem precisar editar cada um; quem já define
        # bg/fg explícito (botões coloridos, etc.) continua igual. ttk.*
        # (Notebook, LabelFrame, Combobox, Treeview, Scrollbar) não é afetado
        # por tk_setPalette — por isso o ttk.Style logo abaixo.
        root.tk_setPalette(background=BG, foreground=FG,
                           activeBackground=BTN_HOV, activeForeground=FG,
                           highlightColor=BORDER, highlightBackground=BORDER)
        style = ttk.Style()
        try:
            style.theme_use("clam")   # tema mais "estilizável" que o padrão
        except Exception:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TNotebook", background=BG, bordercolor=BORDER)
        style.configure("TNotebook.Tab", background=PANEL, foreground=FG, padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", BTN_HOV)],
                 foreground=[("selected", FG)])
        style.configure("TFrame", background=BG)
        style.configure("TLabelframe", background=BG, foreground=FG, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("TEntry", fieldbackground=PANEL, foreground=FG,
                        insertcolor=FG, bordercolor=BORDER)
        style.configure("TCombobox", fieldbackground=PANEL, foreground=FG,
                        background=PANEL, arrowcolor=FG, bordercolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                 foreground=[("readonly", FG)])
        style.configure("TScrollbar", background=PANEL, troughcolor=BG,
                        bordercolor=BORDER, arrowcolor=FG)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=FG, rowheight=24, bordercolor=BORDER, borderwidth=0)
        style.configure("Treeview.Heading", background=BORDER, foreground=FG,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", BTN_HOV)])
        style.map("Treeview.Heading", background=[("active", BORDER)])
        # combobox usa uma janela popup própria pra lista — precisa configurar
        # via option_add (tk clássico), o ttk.Style não alcança essa parte.
        root.option_add("*TCombobox*Listbox.background", PANEL)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", BTN_HOV)
        root.option_add("*TCombobox*Listbox.selectForeground", FG)

        head = tk.Frame(root, bg=HEAD)
        head.pack(fill="x")
        tk.Label(head, text="🍥  " + APP_NAME, bg=HEAD, fg="white",
                 font=("Segoe UI", 18, "bold")).pack(side="left", padx=16, pady=10)
        self.status = tk.Label(head, text="●  Parado", bg=HEAD, fg="#ff8a80",
                               font=("Segoe UI", 11, "bold"))
        self.status.pack(side="right", padx=(4, 16))
        
        self.btn_update = tk.Button(head, text="⟳ Atualizar",
                                     command=self._checar_atualizacao,
                                     bg="#3a3d5c", fg="white", relief="flat",
                                     activebackground="#4a4e78", activeforeground="white",
                                     font=("Segoe UI", 9), cursor="hand2", bd=0,
                                     padx=10, pady=4)
        self.btn_update.pack(side="right", padx=4, pady=10)
        # Engrenagem de atalhos rápidos (pedido do usuário 2026-07-15): abre
        # uma janelinha separada só com os botões de ação (Salvar/Login/
        # Iniciar/Parar/etc) — útil pra quem deixa o painel minimizado numa
        # aba diferente e não quer navegar até a Configuração toda vez.
        self.btn_config_rapida = tk.Button(head, text="⚙", command=self._abrir_config_rapida,
                                            bg=HEAD, fg="#9aa0b4", relief="flat",
                                            activebackground="#4a4e78", activeforeground="white",
                                            font=("Segoe UI", 14), cursor="hand2", bd=0,
                                            padx=8, pady=2)
        self.btn_config_rapida.pack(side="right", padx=(4, 0))
        tk.Label(head, text=f"v{config.VERSION}", bg=HEAD, fg="#9aa0b4",
                 font=("Segoe UI", 9)).pack(side="right", padx=4)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.nb = nb
        self.tab_cfg = tk.Frame(nb, bg=BG)
        self.tab_masmorras = tk.Frame(nb, bg=BG)
        self.tab_caca = tk.Frame(nb, bg=BG)
        self.tab_cripta = tk.Frame(nb, bg=BG)
        self.tab_solo = tk.Frame(nb, bg=BG)
        self.tab_oasis = tk.Frame(nb, bg=BG)
        self.tab_observador = tk.Frame(nb, bg=BG)
        self.tab_mercado = tk.Frame(nb, bg=BG)
        self.tab_rel = tk.Frame(nb, bg=BG)
        # Abas escondidas antes de repassar o painel pra outra pessoa (pedido
        # do usuário 2026-07-15) — a função continua funcionando por baixo
        # dos panos se já estiver configurada, só o atalho visual some. Ver
        # config.PAINEL_ABAS_OCULTAS (lista com "mercado"/"observador").
        _abas_ocultas = set(getattr(config, "PAINEL_ABAS_OCULTAS", None) or [])
        nb.add(self.tab_cfg, text="  Configuração  ")
        nb.add(self.tab_masmorras, text="  Masmorras  ")
        nb.add(self.tab_caca, text="  Caçada Dupla  ")
        nb.add(self.tab_cripta, text="  Cripta  ")
        nb.add(self.tab_solo, text="  Caçada Solo  ")
        nb.add(self.tab_oasis, text="  Missão Oásis  ")
        if "observador" not in _abas_ocultas:
            nb.add(self.tab_observador, text="  Observador  ")
        if "mercado" not in _abas_ocultas:
            nb.add(self.tab_mercado, text="  Mercado  ")
        nb.add(self.tab_rel, text="  Relatório  ")

        # "Masmorras" (Templo do Oásis é conhecido pelos jogadores como
        # "masmorra duo") tem 2 sub-abas: a Masmorra normal (grupo de até 4,
        # com o cadastro de contas) e o Templo do Oásis (dupla).
        sub_masmorras_nb = ttk.Notebook(self.tab_masmorras)
        sub_masmorras_nb.pack(fill="both", expand=True)
        self.sub_masmorras_nb = sub_masmorras_nb
        self.tab_masmorra_normal = tk.Frame(sub_masmorras_nb, bg=BG)
        self.tab_templo = tk.Frame(sub_masmorras_nb, bg=BG)
        sub_masmorras_nb.add(self.tab_masmorra_normal, text="  Masmorra  ")
        sub_masmorras_nb.add(self.tab_templo, text="  Templo do Oásis  ")
        sub_masmorras_nb.bind("<<NotebookTabChanged>>", self._on_sub_masmorras_tab_change)

        dados = carregar()
        self._build_config(dados)
        self._build_masmorra_subtab(dados)
        self._build_caca_dupla(dados)
        self._build_templo_oasis(dados)
        self._build_cripta(dados)
        self._build_caca_solo(dados)
        self._build_missao_oasis(dados)
        self._build_observador(dados)
        self._build_mercado(dados)
        self._build_relatorio()
        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self._tick()

    def _on_sub_masmorras_tab_change(self, event):
        try:
            texto = self.sub_masmorras_nb.tab(self.sub_masmorras_nb.select(), "text")
            if "Templo do Oásis" in texto:
                self._rebuild_templo_selector(preservar=True)
            elif "Masmorra" in texto:
                self._rebuild_masmorra_selector(preservar=True)
        except Exception:
            pass

    def _on_tab_change(self, event):
        try:
            texto = self.nb.tab(self.nb.select(), "text")
            if "Caçada Dupla" in texto:
                self._rebuild_caca_selector(preservar=True)
            elif "Masmorras" in texto:
                self._on_sub_masmorras_tab_change(None)
            elif "Mercado" in texto:
                self._rebuild_mercado_selector(preservar=True)
        except Exception:
            pass


    # ---------------- aba Caçada Dupla ----------------
    def _tornar_exclusivo(self, nome):
        """Só um conteúdo (Masmorra / Caçada Dupla / Templo do Oásis / Cripta /
        Caçada Solo / Missão Oásis) pode estar ativo por vez — desmarca os
        OUTROS automaticamente quando um é marcado (antes só dava erro no
        Salvar pedindo pra desmarcar na mão, mas o texto da checkbox já
        promete 'desliga os outros' — agora desliga de verdade, sem precisar
        fazer isso manualmente)."""
        pares = (("masmorra", getattr(self, "masmorra_ativa", None)),
                 ("caca", getattr(self, "caca_ativa", None)),
                 ("templo", getattr(self, "templo_ativa", None)),
                 ("cripta", getattr(self, "cripta_ativa", None)),
                 ("solo", getattr(self, "solo_ativa", None)),
                 ("oasis", getattr(self, "oasis_ativa", None)),
                 ("observador", getattr(self, "observador_ativa", None)))
        ativado_var = dict(pares).get(nome)
        if ativado_var is None or not ativado_var.get():
            return
        for outro_nome, outro_var in pares:
            if outro_nome != nome and outro_var is not None and outro_var.get():
                outro_var.set(False)

    def _build_caca_dupla(self, dados):
        body = self.tab_caca
        tk.Label(body, text="Conteúdo separado da Masmorra (nível mínimo 42). Só um "
                            "conteúdo roda por vez — ativar aqui desliga a Masmorra.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=650,
                 justify="left").pack(anchor="w", padx=12, pady=(8, 4))

        cd = dados.get("CACA_DUPLA") or {}
        if not cd.get("grupos") and isinstance(cd.get("duplas"), list) and len(cd["duplas"]) == 2:
            cd = dict(cd)
            cd["grupos"] = [cd["duplas"]]
        self._caca_ajustes_dados = cd   
        self.caca_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "caca_dupla"))
        self.caca_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("caca"))
        tk.Checkbutton(body, text="Ativar Caçada em Dupla (desliga a Masmorra)",
                       variable=self.caca_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")
                       ).pack(anchor="w", padx=12, pady=(0, 8))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        
        specs = [("Andar máximo", "andar_maximo", 49),
                 ("Energia mínima", "energia_minima", 10),
                 ("Poções de reforço", "pocoes_reforco", 2),
                 ("Poções vida mín.", "pocao_vida_minima", 10),
                 ("Aviso poção <", "pocao_vida_aviso", 100),
                 ("Quantas caçadas", "max_cacadas", 0),
                 ("HP% reforço (0=off)", "reforco_pct", 0),
                 ("Alma a partir andar (0=sempre)", "alma_min_andar", 0)]
        POR_LINHA = 2
        self.caca_ajustes = {}
        for i, (lbl, key, default) in enumerate(specs):
            bloco, col = divmod(i, POR_LINHA)
            row_lbl, row_ent = bloco * 2, bloco * 2 + 1
            tk.Label(ajustes, text=lbl, bg=BG).grid(row=row_lbl, column=col, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(ajustes, width=8)
            e.insert(0, str(cd.get(key, default)))
            e.grid(row=row_ent, column=col, padx=8, pady=(0, 6), sticky="w")
            self.caca_ajustes[key] = e
        ultima_linha = (len(specs) - 1) // POR_LINHA * 2 + 2
        tk.Label(ajustes, text="Aviso poção <: antes de iniciar, se o estoque estiver abaixo disso, abre pop-up e pausa (reabastecer).\n"
                               "Poções vida mín.: sai da caçada se, ao beber, ficar abaixo disso.\n"
                               "HP% poção agora é POR CONTA — veja em \"Contas na dupla\", embaixo.\n"
                               "HP% reforço: bebe 1 poção no início se entrar abaixo desse % (0 = desligado).\n"
                               "Alma a partir andar: nos andares fáceis o grupo só ATACA (mais rápido); "
                               "a partir desse andar passa a usar as almas (0 = sempre usa).\n"
                               "Quantas caçadas: 0 = sem limite.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=640).grid(
                 row=ultima_linha, column=0, columnspan=POR_LINHA, padx=8, pady=(4, 4), sticky="w")


        self.caca_cartoes = []
        self._caca_sel_saved = list(cd.get("selecionadas") or [])
        self._caca_pct_salvos = {}
        self._caca_grupo_salvo = {}
        for gi, grupo in enumerate(cd.get("grupos") or [], start=1):
            for d in grupo:
                fone = d.get("phone", "")
                self._caca_pct_salvos[fone] = {"vida": d.get("caca_vida_pct"),
                                                "alma": d.get("tank_alma_pct")}
                self._caca_grupo_salvo[fone] = str(gi)

        sel = ttk.LabelFrame(body, text=" Contas nas duplas (até 2 duplas rodando ao mesmo tempo) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Marque a conta e escolha a Dupla (1 ou 2) — cada dupla precisa de "
                           "EXATAMENTE 2 contas e roda numa caçada própria, in paralelo com a outra. "
                           "Contas sem marcar ficam paradas.  ·  HP% poção: só dessa conta.  ·  "
                           "HP% alma (tank): só aparece na conta com papel tank.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=650,
                 justify="left").pack(anchor="w", padx=8, pady=(4, 0))
        self.caca_sel_frame = tk.Frame(sel, bg=BG)
        self.caca_sel_frame.pack(fill="x", padx=8, pady=4)
        self.caca_sel_vars = {}
        self.caca_grupo_vars = {}
        self.caca_pct_entries = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_caca_selector(preservar=True))
        self._rebuild_caca_selector()

    def _rebuild_caca_selector(self, preservar=False):
        if preservar and getattr(self, "caca_sel_vars", None):
            self._caca_sel_saved = [f for f, v in self.caca_sel_vars.items() if v.get()]
        if preservar and getattr(self, "caca_grupo_vars", None):
            for fone, gvar in self.caca_grupo_vars.items():
                self._caca_grupo_salvo[fone] = gvar.get()
        if preservar and getattr(self, "caca_pct_entries", None):
            for fone, (e_vida, e_alma) in self.caca_pct_entries.items():
                self._caca_pct_salvos[fone] = {
                    "vida": e_vida.get().strip(),
                    "alma": e_alma.get().strip() if e_alma is not None else
                            self._caca_pct_salvos.get(fone, {}).get("alma"),
                }
        for w in self.caca_sel_frame.winfo_children():
            w.destroy()
        self.caca_sel_vars = {}
        self.caca_grupo_vars = {}
        self.caca_pct_entries = {}
        marcadas = set(self._caca_sel_saved)
        cd = getattr(self, "_caca_ajustes_dados", {}) or {}
        
        vida_padrao = cd.get("vida_min_pct", 40)
        algum = False
        for i, card in enumerate(getattr(self, "cartoes", [])):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            linha = tk.Frame(self.caca_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=1)
            var = tk.BooleanVar(value=(fone in marcadas))
            tk.Checkbutton(linha, text=rotulo, variable=var, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 9), anchor="w", width=44,
                           command=lambda f=fone: self._on_caca_sel_toggle(f)).pack(side="left")
            tk.Label(linha, text="Dupla:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
            grupo_var = tk.StringVar(value=self._caca_grupo_salvo.get(fone, "1"))
            cb_grupo = ttk.Combobox(linha, textvariable=grupo_var, values=("1", "2"),
                                     width=2, state="readonly")
            cb_grupo.pack(side="left")
            salvo = self._caca_pct_salvos.get(fone, {})
            tk.Label(linha, text="HP% poção:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
            e_vida = ttk.Entry(linha, width=4)
            e_vida.insert(0, str(salvo.get("vida") or vida_padrao))
            e_vida.pack(side="left")
            
            e_alma = None
            if card.papel.get() == "tank":
                tk.Label(linha, text="HP% alma (tank):", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
                e_alma = ttk.Entry(linha, width=4)
                e_alma.insert(0, str(salvo.get("alma") or 60))
                e_alma.pack(side="left")
            self.caca_sel_vars[fone] = var
            self.caca_grupo_vars[fone] = grupo_var
            self.caca_pct_entries[fone] = (e_vida, e_alma)
        if not algum:
            tk.Label(self.caca_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _on_caca_sel_toggle(self, fone):
        if not self.caca_sel_vars[fone].get():
            return
        grupo_desta = self.caca_grupo_vars[fone].get()
        marcadas_no_grupo = [f for f, v in self.caca_sel_vars.items()
                              if v.get() and self.caca_grupo_vars[f].get() == grupo_desta]
        if len(marcadas_no_grupo) > 2:
            self.caca_sel_vars[fone].set(False)
            messagebox.showwarning("Só 2 contas por dupla",
                                   f"A Dupla {grupo_desta} já tem 2 contas marcadas. "
                                   "Desmarque uma, ou mude a Dupla desta conta antes de marcá-la.")

    def _coletar_caca_dupla(self):
        ativa = self.caca_ativa.get()
        selecionadas = [f for f, v in getattr(self, "caca_sel_vars", {}).items() if v.get()]

        por_grupo = {"1": [], "2": []}
        for fone in selecionadas:
            gvar = self.caca_grupo_vars.get(fone)
            g = gvar.get() if gvar else "1"
            por_grupo.setdefault(g, []).append(fone)

        if ativa:
            if not selecionadas:
                raise ValueError("Caçada em Dupla ativada: marque pelo menos uma dupla "
                                  "(2 contas) em \"Contas nas duplas\".")
            for gnum, fones in por_grupo.items():
                if fones and len(fones) != 2:
                    raise ValueError(f"Dupla {gnum}: marque EXATAMENTE 2 contas "
                                      f"(tem {len(fones)}).")

        def _montar_conta(fone):
            for card in getattr(self, "cartoes", []):
                if card.fone.get().strip() != fone:
                    continue
                c = card.coletar()
                if not c:
                    return None
                entries = self.caca_pct_entries.get(fone)
                if entries:
                    e_vida, e_alma = entries
                    try:
                        c["caca_vida_pct"] = max(0, min(100, int(e_vida.get().strip())))
                    except ValueError:
                        pass
                    if e_alma is not None:
                        try:
                            c["tank_alma_pct"] = max(0, min(100, int(e_alma.get().strip())))
                        except ValueError:
                            pass
                return c
            return None

        grupos = []
        for gnum in ("1", "2"):
            fones = por_grupo.get(gnum) or []
            if len(fones) != 2:
                continue
            grupo = [c for c in (_montar_conta(f) for f in fones) if c]
            if len(grupo) == 2:
                grupos.append(grupo)

        ajustes = {"selecionadas": selecionadas, "grupos": grupos}
        for key, e in self.caca_ajustes.items():
            minimo = 0 if key in ("max_cacadas", "pocao_vida_minima",
                                   "vida_min_pct", "reforco_pct", "pocao_vida_aviso",
                                   "alma_min_andar") else 1
            try:
                valor = max(minimo, int(e.get().strip()))
                if key in ("vida_min_pct", "reforco_pct"):
                    valor = min(valor, 100)       
                ajustes[key] = valor
            except ValueError:
                pass
        return ativa, ajustes

    # ---------------- aba Templo do Oásis ----------------
    def _build_templo_oasis(self, dados):
        body = self.tab_templo
        tk.Label(body, text="Templo do Oásis (Duo, nível mínimo 40) — dentro da Fenda Solar "
                            "(mapa do Oásis). MESMA sala/combate da Masmorra normal (Criar Sala, "
                            "Pronto, Iniciar, Atacar/Defender/Almas), só que travado em 2 contas. "
                            "Só um conteúdo roda por vez — ativar aqui desliga a Masmorra.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=650,
                 justify="left").pack(anchor="w", padx=12, pady=(8, 4))

        to = dados.get("TEMPLO_OASIS") or {}
        self._templo_ajustes_dados = to
        self.templo_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "templo_oasis"))
        self.templo_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("templo"))
        tk.Checkbutton(body, text="Ativar Templo do Oásis (desliga a Masmorra)",
                       variable=self.templo_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")
                       ).pack(anchor="w", padx=12, pady=(0, 8))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        specs = [("Quantas execuções", "max_execucoes", 0),
                 ("Poções vida mín.", "pocao_vida_minima", 50),
                 ("Aviso poção <", "pocao_vida_aviso", 100)]
        self.templo_ajustes = {}
        for i, (lbl, key, default) in enumerate(specs):
            tk.Label(ajustes, text=lbl, bg=BG).grid(row=0, column=i, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(ajustes, width=8)
            e.insert(0, str(to.get(key, default)))
            e.grid(row=1, column=i, padx=8, pady=(0, 6), sticky="w")
            self.templo_ajustes[key] = e
        tk.Label(ajustes, text="HP% poção é POR CONTA (inclusive o tank) — veja embaixo.\n"
                               "Quantas execuções: 0 = sem limite.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=640).grid(
                 row=2, column=0, columnspan=len(specs), padx=8, pady=(4, 4), sticky="w")

        self._templo_sel_saved = list(to.get("selecionadas") or [])
        self._templo_grupo_salvo = {}
        self._templo_pct_salvos = {}
        for gi, grupo in enumerate(to.get("grupos") or [], start=1):
            for d in grupo:
                fone = d.get("phone", "")
                self._templo_grupo_salvo[fone] = str(gi)
                self._templo_pct_salvos[fone] = d.get("caca_vida_pct")

        sel = ttk.LabelFrame(body, text=" Contas nas duplas (até 2 duplas rodando ao mesmo tempo) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Marque a conta e escolha a Dupla (1 ou 2) — cada dupla precisa de "
                           "EXATAMENTE 2 contas e roda no seu próprio Templo, em paralelo com a "
                           "outra. Contas sem marcar ficam paradas.  ·  HP% poção: abaixo desse "
                           "%, a conta bebe poção (vale pra TODAS, inclusive o tank).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=650,
                 justify="left").pack(anchor="w", padx=8, pady=(4, 0))
        self.templo_sel_frame = tk.Frame(sel, bg=BG)
        self.templo_sel_frame.pack(fill="x", padx=8, pady=4)
        self.templo_sel_vars = {}
        self.templo_grupo_vars = {}
        self.templo_pct_entries = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_templo_selector(preservar=True))
        self._rebuild_templo_selector()

    def _rebuild_templo_selector(self, preservar=False):
        if preservar and getattr(self, "templo_sel_vars", None):
            self._templo_sel_saved = [f for f, v in self.templo_sel_vars.items() if v.get()]
        if preservar and getattr(self, "templo_grupo_vars", None):
            for fone, gvar in self.templo_grupo_vars.items():
                self._templo_grupo_salvo[fone] = gvar.get()
        if preservar and getattr(self, "templo_pct_entries", None):
            for fone, e_vida in self.templo_pct_entries.items():
                self._templo_pct_salvos[fone] = e_vida.get().strip()
        for w in self.templo_sel_frame.winfo_children():
            w.destroy()
        self.templo_sel_vars = {}
        self.templo_grupo_vars = {}
        self.templo_pct_entries = {}
        marcadas = set(self._templo_sel_saved)
        vida_padrao = getattr(self, "_templo_ajustes_dados", {}).get("vida_min_pct", 40)
        algum = False
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            linha = tk.Frame(self.templo_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=1)
            var = tk.BooleanVar(value=(fone in marcadas))
            tk.Checkbutton(linha, text=rotulo, variable=var, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 9), anchor="w", width=44,
                           command=lambda f=fone: self._on_templo_sel_toggle(f)).pack(side="left")
            tk.Label(linha, text="Dupla:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
            grupo_var = tk.StringVar(value=self._templo_grupo_salvo.get(fone, "1"))
            cb_grupo = ttk.Combobox(linha, textvariable=grupo_var, values=("1", "2"),
                                     width=2, state="readonly")
            cb_grupo.pack(side="left")
            tk.Label(linha, text="HP% poção:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))
            e_vida = ttk.Entry(linha, width=4)
            e_vida.insert(0, str(self._templo_pct_salvos.get(fone) or vida_padrao))
            e_vida.pack(side="left")
            self.templo_sel_vars[fone] = var
            self.templo_grupo_vars[fone] = grupo_var
            self.templo_pct_entries[fone] = e_vida
        if not algum:
            tk.Label(self.templo_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _on_templo_sel_toggle(self, fone):
        if not self.templo_sel_vars[fone].get():
            return
        grupo_desta = self.templo_grupo_vars[fone].get()
        marcadas_no_grupo = [f for f, v in self.templo_sel_vars.items()
                              if v.get() and self.templo_grupo_vars[f].get() == grupo_desta]
        if len(marcadas_no_grupo) > 2:
            self.templo_sel_vars[fone].set(False)
            messagebox.showwarning("Só 2 contas por dupla",
                                   f"A Dupla {grupo_desta} já tem 2 contas marcadas. "
                                   "Desmarque uma, ou mude a Dupla desta conta antes de marcá-la.")

    def _coletar_templo_oasis(self):
        ativa = self.templo_ativa.get()
        selecionadas = [f for f, v in getattr(self, "templo_sel_vars", {}).items() if v.get()]

        por_grupo = {"1": [], "2": []}
        for fone in selecionadas:
            gvar = self.templo_grupo_vars.get(fone)
            g = gvar.get() if gvar else "1"
            por_grupo.setdefault(g, []).append(fone)

        if ativa:
            if not selecionadas:
                raise ValueError("Templo do Oásis ativado: marque pelo menos uma dupla "
                                  "(2 contas) em \"Contas nas duplas\".")
            for gnum, fones in por_grupo.items():
                if fones and len(fones) != 2:
                    raise ValueError(f"Dupla {gnum}: marque EXATAMENTE 2 contas "
                                      f"(tem {len(fones)}).")

        def _montar_conta(fone):
            for card in getattr(self, "cartoes", []):
                if card.fone.get().strip() != fone:
                    continue
                c = card.coletar()
                if not c:
                    return None
                e_vida = self.templo_pct_entries.get(fone)
                if e_vida is not None:
                    try:
                        c["caca_vida_pct"] = max(0, min(100, int(e_vida.get().strip())))
                    except ValueError:
                        pass
                return c
            return None

        grupos = []
        for gnum in ("1", "2"):
            fones = por_grupo.get(gnum) or []
            if len(fones) != 2:
                continue
            grupo = [c for c in (_montar_conta(f) for f in fones) if c]
            if len(grupo) == 2:
                grupos.append(grupo)

        ajustes = {"selecionadas": selecionadas, "grupos": grupos}
        for key, e in self.templo_ajustes.items():
            try:
                ajustes[key] = max(0, int(e.get().strip()))
            except ValueError:
                pass
        return ativa, ajustes

    # ---------------- aba Cripta ----------------
    def _build_cripta(self, dados):
        body = self.tab_cripta
        tk.Label(body, text="Cripta do Cemitério (mapa Cemitério Antigo). Masmorra "
                            "infinita — o bot para no andar escolhido. Sala SEM senha; se "
                            "um intruso entrar, sai e recria. Ativar aqui desliga a Masmorra "
                            "e a Caçada em Dupla (só um conteúdo por vez).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=650, justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        cr = dados.get("CRIPTA") or {}
        self.cripta_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "cripta"))
        self.cripta_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("cripta"))
        tk.Checkbutton(body, text="Ativar Cripta (desliga a Masmorra e a Caçada em Dupla)",
                       variable=self.cripta_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 8))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        tk.Label(ajustes, text="Qual Cripta", bg=BG, fg=FG).grid(row=0, column=0, padx=8, pady=(4, 0), sticky="w")
        self.cripta_nivel = ttk.Combobox(ajustes, width=8, state="readonly", values=["I", "II", "III"])
        self.cripta_nivel.set(cr.get("nivel", "I"))
        self.cripta_nivel.grid(row=1, column=0, padx=8, pady=(0, 6), sticky="w")
        # As Poções (HP% poção, reforço, mínimo, aviso) ficam na seção "Poções"
        # da aba Configuração — vale igual pra Caçada Dupla e Cripta. Aqui só
        # o específico da Cripta: qual nível, andar, alma-a-partir e limite.
        specs = [("Andar máximo", "andar_maximo", 10),
                 ("Alma a partir andar (0=sempre)", "alma_min_andar", 0),
                 ("Quantas criptas", "max_criptas", 0)]
        self.cripta_ajustes = {}
        for i, (lbl, key, default) in enumerate(specs):
            col = 1 + i % 2
            row_lbl = (i // 2) * 2
            tk.Label(ajustes, text=lbl, bg=BG, fg=FG).grid(row=row_lbl, column=col, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(ajustes, width=8)
            e.insert(0, str(cr.get(key, default)))
            e.grid(row=row_lbl + 1, column=col, padx=8, pady=(0, 6), sticky="w")
            self.cripta_ajustes[key] = e

        # Poções DA CRIPTA (independente da Caçada Dupla, que mantém as suas
        # próprias na aba dela — não mexemos nisso). HP% poção NÃO fica mais
        # aqui — virou POR CONTA, na lista "Contas na Cripta" embaixo (igual
        # Masmorra/Templo do Oásis: cada personagem tem HP máximo diferente,
        # então o mesmo % geral não fazia sentido pra todo mundo).
        pocoes = dados.get("POCOES") or {}
        pocf = ttk.LabelFrame(body, text=" Poções (Cripta) ")
        pocf.pack(fill="x", padx=12, pady=6)
        poc_specs = [("HP% reforço (0=off)", "reforco_pct", 0),
                     ("Poções vida mín.", "pocao_vida_minima", 10), ("Aviso poção <", "pocao_vida_aviso", 100)]
        self.cripta_pocoes = {}
        for i, (lbl, key, default) in enumerate(poc_specs):
            tk.Label(pocf, text=lbl, bg=BG, fg=FG).grid(row=0, column=i, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(pocf, width=8)
            e.insert(0, str(pocoes.get(key, default)))
            e.grid(row=1, column=i, padx=8, pady=(0, 6), sticky="w")
            self.cripta_pocoes[key] = e

        tk.Label(ajustes, text="Andar máximo: o bot para ao alcançar esse andar (a Cripta é "
                               "infinita).  ·  Alma a partir andar: nos andares fáceis o grupo só "
                               "ATACA (mais rápido); a partir desse andar passa a usar as almas "
                               "(0 = sempre usa).  ·  Quantas criptas: 0 = sem limite.  ·  Precisa de "
                               "1 🦴 Chave de Ossos por conta.  ·  HP% poção agora é por conta, na "
                               "lista \"Contas na Cripta\" embaixo.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=640).grid(
                 row=100, column=0, columnspan=3, padx=8, pady=(4, 4), sticky="w")

        self._cripta_sel_saved = list(cr.get("selecionadas") or
                                      [d.get("phone", "") for d in (cr.get("contas") or [])])
        self._cripta_pct_salvos = {
            d.get("phone", ""): d.get("vida_min_pct")
            for d in (cr.get("contas") or [])
        }
        sel = ttk.LabelFrame(body, text=" Contas na Cripta (marque de 1 a 5 das configuradas) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Escolha quais contas vão na Cripta (1 a 5). HP% poção: abaixo desse "
                           "%, a conta bebe poção (vale pra TODAS, inclusive o tank). As não "
                           "marcadas ficam paradas.", bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=650, justify="left").pack(anchor="w", padx=8, pady=(4, 0))
        self.cripta_sel_frame = tk.Frame(sel, bg=BG)
        self.cripta_sel_frame.pack(fill="x", padx=8, pady=4)
        self.cripta_sel_vars = {}
        self.cripta_pct_entries = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_cripta_selector(preservar=True))
        self._rebuild_cripta_selector()

    def _rebuild_cripta_selector(self, preservar=False):
        if preservar and getattr(self, "cripta_sel_vars", None):
            self._cripta_sel_saved = [f for f, v in self.cripta_sel_vars.items() if v.get()]
        if preservar and getattr(self, "cripta_pct_entries", None):
            for fone, e_vida in self.cripta_pct_entries.items():
                self._cripta_pct_salvos[fone] = e_vida.get().strip()
        for w in self.cripta_sel_frame.winfo_children():
            w.destroy()
        self.cripta_sel_vars = {}
        self.cripta_pct_entries = {}
        marcadas = set(self._cripta_sel_saved)
        algum = False
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            linha = tk.Frame(self.cripta_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=1)
            var = tk.BooleanVar(value=(fone in marcadas))
            self.cripta_sel_vars[fone] = var
            tk.Checkbutton(linha, text=rotulo, variable=var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           anchor="w", font=("Segoe UI", 9), width=44,
                           command=lambda f=fone: self._on_cripta_sel_toggle(f)).pack(side="left")
            tk.Label(linha, text="HP% poção:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 2))
            e_vida = ttk.Entry(linha, width=4)
            default_vida = 50 if card.papel.get() == "tank" else 80
            valor_salvo = self._cripta_pct_salvos.get(fone)
            e_vida.insert(0, str(valor_salvo if valor_salvo not in (None, "") else default_vida))
            e_vida.pack(side="left")
            self.cripta_pct_entries[fone] = e_vida
        if not algum:
            tk.Label(self.cripta_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _on_cripta_sel_toggle(self, fone):
        marcadas = [f for f, v in self.cripta_sel_vars.items() if v.get()]
        if len(marcadas) > 5:
            self.cripta_sel_vars[fone].set(False)
            messagebox.showwarning("Máx 5", "A Cripta usa no máximo 5 contas.")

    def _coletar_cripta(self):
        ativa = self.cripta_ativa.get()
        selecionadas = [f for f, v in getattr(self, "cripta_sel_vars", {}).items() if v.get()]
        if ativa and not (1 <= len(selecionadas) <= 5):
            raise ValueError("Cripta ativada: marque de 1 a 5 contas (das configuradas).")
        contas = []
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if fone not in selecionadas:
                continue
            c = card.coletar()
            if c:
                e_vida = getattr(self, "cripta_pct_entries", {}).get(fone)
                if e_vida is not None:
                    try:
                        c["vida_min_pct"] = max(0, min(100, int(e_vida.get().strip())))
                    except ValueError:
                        pass
                contas.append(c)
        ajustes = {"selecionadas": selecionadas, "contas": contas,
                   "nivel": self.cripta_nivel.get() or "I"}
        for key, e in self.cripta_ajustes.items():
            try:
                ajustes[key] = max(0, int(e.get().strip()))
            except ValueError:
                pass
        pocoes = {}
        for key, e in getattr(self, "cripta_pocoes", {}).items():
            try:
                valor = max(0, int(e.get().strip()))
                if key == "reforco_pct":
                    valor = min(valor, 100)
                pocoes[key] = valor
            except ValueError:
                pass
        return ativa, ajustes, pocoes

    # ---------------- aba Caçada Solo ----------------
    def _build_caca_solo(self, dados):
        body = self.tab_solo
        tk.Label(body, text="Cada conta caça SOZINHA (sem sala/parceiro), em paralelo com "
                            "as outras. Ataca, usa alma (dps/lanceiro/arqueiro/berserker) e "
                            "cura pelo HP%. Quando a energia acabar, reabastece com Poção de "
                            "Energia e volta a caçar sozinho. Ativar aqui desliga a Masmorra, "
                            "a Caçada em Dupla e a Cripta.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=650, justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        cs = dados.get("CACA_SOLO") or {}
        self.solo_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "caca_solo"))
        self.solo_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("solo"))
        tk.Checkbutton(body, text="Ativar Caçada Solo (desliga os outros conteúdos)",
                       variable=self.solo_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 8))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        specs = [("Energia mínima", "energia_minima", 5),
                 ("Reabastecer até", "energia_alvo", 35),
                 ("Quantas caçadas", "max_cacadas", 0),
                 ("HP mín. após armadilha", "hp_minimo_armadilha", 0)]
        self.solo_ajustes = {}
        for i, (lbl, key, default) in enumerate(specs):
            col = i
            tk.Label(ajustes, text=lbl, bg=BG, fg=FG).grid(row=0, column=col, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(ajustes, width=8)
            e.insert(0, str(cs.get(key, default)))
            e.grid(row=1, column=col, padx=8, pady=(0, 6), sticky="w")
            self.solo_ajustes[key] = e

        tk.Label(ajustes, text="Energia mínima/Reabastecer até: quando a energia cair abaixo "
                               "do mínimo, bebe Poção de Energia até chegar no alvo, depois "
                               "volta a caçar.  ·  Goblin Gibby (Martelo Mágico) e Mercador "
                               "Viajante (cura barata, se o HP estiver baixo): compra automático "
                               "sempre que aparecer.  ·  Quantas caçadas: 0 = sem limite.  ·  "
                               "HP mín. após armadilha: valor REAL de HP (não %) — se, depois "
                               "de cair numa armadilha, o HP confirmado ficar igual ou abaixo "
                               "desse número, bebe poção na hora (extra de segurança, além do "
                               "HP% normal). 0 = desliga essa checagem extra.  ·  "
                               "Tônico do Mercador do Deserto: agora é por conta, na lista "
                               "embaixo (só aparece quando o mapa dela é Deserto Escaldante).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=640).grid(
                 row=100, column=0, columnspan=5, padx=8, pady=(4, 4), sticky="w")

        self._solo_sel_saved = list(cs.get("selecionadas") or
                                    [d.get("phone", "") for d in (cs.get("contas") or [])])
        self._solo_mapa_conta_saved = {d.get("phone", ""): d.get("mapa", "")
                                       for d in (cs.get("contas") or [])}
        self._solo_hp_por_mob_saved = {d.get("phone", ""): (d.get("hp_por_mob") or {})
                                       for d in (cs.get("contas") or [])}
        self._solo_deserto_modo_saved = {}
        for d in (cs.get("contas") or []):
            fone = d.get("phone", "")
            if d.get("deserto_modo") in ("geral", "bosses", "poeira"):
                self._solo_deserto_modo_saved[fone] = d["deserto_modo"]
            else:
                # compat com saves antigos (só tinham o booleano so_bosses_deserto)
                self._solo_deserto_modo_saved[fone] = "bosses" if d.get("so_bosses_deserto") else "geral"
        self._solo_alvo_oasis_saved = {d.get("phone", ""): (d.get("alvo_oasis") or "")
                                       for d in (cs.get("contas") or [])}
        self._solo_fugir_boss_floresta_saved = {d.get("phone", ""): bool(d.get("fugir_boss_floresta"))
                                                for d in (cs.get("contas") or [])}
        self._solo_hp_geral_saved = {d.get("phone", ""): d.get("vida_min_pct")
                                     for d in (cs.get("contas") or [])}
        self._solo_tonico_deserto_saved = {d.get("phone", ""): (d.get("tonico_deserto") or "")
                                           for d in (cs.get("contas") or [])}
        sel = ttk.LabelFrame(body, text=" Contas na Caçada Solo (cada uma independente) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Marque quantas quiser — cada uma caça sozinha, ao mesmo tempo, "
                           "cada uma no SEU próprio mapa (em branco = fica no mapa em que já "
                           "estiver). As não marcadas ficam paradas.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=650, justify="left").pack(anchor="w", padx=8, pady=(4, 0))

        self.solo_sel_vars = {}
        self.solo_mapa_conta_vars = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_solo_selector(preservar=True))

        canvas_wrap = tk.Frame(sel, bg=BG)
        canvas_wrap.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        solo_canvas = tk.Canvas(canvas_wrap, bg=BG, highlightthickness=0)
        solo_scroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=solo_canvas.yview)
        self.solo_sel_frame = tk.Frame(solo_canvas, bg=BG)
        self.solo_sel_frame.bind(
            "<Configure>", lambda e: solo_canvas.configure(scrollregion=solo_canvas.bbox("all")))
        solo_canvas.create_window((0, 0), window=self.solo_sel_frame, anchor="nw")
        solo_canvas.configure(yscrollcommand=solo_scroll.set)
        solo_canvas.pack(side="left", fill="both", expand=True)
        solo_scroll.pack(side="left", fill="y")
        solo_canvas.bind("<Enter>", lambda e: solo_canvas.bind_all(
            "<MouseWheel>", lambda ev: solo_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        solo_canvas.bind("<Leave>", lambda e: solo_canvas.unbind_all("<MouseWheel>"))
        solo_canvas.bind("<Button-4>", lambda e: solo_canvas.yview_scroll(-1, "units"))
        solo_canvas.bind("<Button-5>", lambda e: solo_canvas.yview_scroll(1, "units"))

        self._rebuild_solo_selector()

    def _rebuild_solo_selector(self, preservar=False):
        if preservar and getattr(self, "solo_sel_vars", None):
            self._solo_sel_saved = [f for f, v in self.solo_sel_vars.items() if v.get()]
        if preservar and getattr(self, "solo_mapa_conta_vars", None):
            for fone, var in self.solo_mapa_conta_vars.items():
                self._solo_mapa_conta_saved[fone] = var.get()
        if preservar and getattr(self, "solo_mob_entries_por_conta", None):
            # MESCLA em vez de sobrescrever (dict(...) parte do que já tinha) —
            # BUG REAL corrigido 2026-07-17: como agora existe um 2º quadro de
            # HP% por monstro (Floresta Profunda, ver abaixo), sobrescrever o
            # dict inteiro aqui apagava os valores da Floresta que tivessem
            # sido preservados antes por aquele outro bloco (ou vice-versa).
            for fone, entries in self.solo_mob_entries_por_conta.items():
                vals = dict(self._solo_hp_por_mob_saved.get(fone, {}))
                for mob, e in entries.items():
                    try:
                        vals[mob] = int(e.get().strip())
                    except ValueError:
                        pass
                self._solo_hp_por_mob_saved[fone] = vals
        if preservar and getattr(self, "solo_mob_entries_floresta_por_conta", None):
            for fone, entries in self.solo_mob_entries_floresta_por_conta.items():
                vals = dict(self._solo_hp_por_mob_saved.get(fone, {}))
                for mob, e in entries.items():
                    try:
                        vals[mob] = int(e.get().strip())
                    except ValueError:
                        pass
                self._solo_hp_por_mob_saved[fone] = vals
        if preservar and getattr(self, "solo_fugir_boss_floresta_vars", None):
            for fone, var in self.solo_fugir_boss_floresta_vars.items():
                self._solo_fugir_boss_floresta_saved[fone] = var.get()
        if preservar and getattr(self, "solo_so_bosses_vars", None):
            for fone, var in self.solo_so_bosses_vars.items():
                self._solo_deserto_modo_saved[fone] = var.get()
        if preservar and getattr(self, "solo_alvo_oasis_vars", None):
            for fone, var in self.solo_alvo_oasis_vars.items():
                self._solo_alvo_oasis_saved[fone] = var.get()
        if preservar and getattr(self, "solo_hp_geral_entries", None):
            for fone, e in self.solo_hp_geral_entries.items():
                self._solo_hp_geral_saved[fone] = e.get().strip()
        if preservar and getattr(self, "solo_tonico_deserto_vars", None):
            for fone, var in self.solo_tonico_deserto_vars.items():
                self._solo_tonico_deserto_saved[fone] = var.get()
        for w in self.solo_sel_frame.winfo_children():
            w.destroy()
        self.solo_sel_vars = {}
        self.solo_mapa_conta_vars = {}
        self.solo_mob_entries_por_conta = {}
        self.solo_mobs_frame_por_conta = {}
        self.solo_so_bosses_vars = {}
        self.solo_deserto_frame_por_conta = {}
        self.solo_alvo_oasis_vars = {}
        self.solo_oasis_frame_por_conta = {}
        self.solo_mob_entries_floresta_por_conta = {}
        self.solo_mobs_frame_floresta_por_conta = {}
        self.solo_fugir_boss_floresta_vars = {}
        self.solo_floresta_frame_por_conta = {}
        self.solo_hp_geral_entries = {}
        self.solo_hp_geral_frame_por_conta = {}
        self.solo_tonico_deserto_vars = {}
        marcadas = set(self._solo_sel_saved)
        try:
            _mapas_conta = [""] + list(config.MAPAS_CONHECIDOS)
        except Exception:
            _mapas_conta = [""]
        if "Floresta Sombria" in _mapas_conta and "Floresta Profunda" not in _mapas_conta:
            idx = _mapas_conta.index("Floresta Sombria")
            _mapas_conta.insert(idx + 1, "Floresta Profunda")
        try:
            _mobs = list(config.MOBS_MONTANHAS_GELIDAS)
        except Exception:
            _mobs = []
        algum = False
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            bloco = tk.Frame(self.solo_sel_frame, bg=BG)
            bloco.pack(fill="x", anchor="w", pady=(1, 4))
            linha = tk.Frame(bloco, bg=BG)
            linha.pack(fill="x", anchor="w")
            var = tk.BooleanVar(value=(fone in marcadas))
            self.solo_sel_vars[fone] = var
            tk.Checkbutton(linha, text=rotulo, variable=var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           anchor="w", width=44, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(linha, text="Mapa:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 2))
            mapa_var = tk.StringVar(value=self._solo_mapa_conta_saved.get(fone, ""))
            cb_mapa = ttk.Combobox(linha, textvariable=mapa_var, values=_mapas_conta,
                                   width=16, state="readonly")
            cb_mapa.pack(side="left")
            self.solo_mapa_conta_vars[fone] = mapa_var

            # HP% por monstro — cada CONTA tem sua defesa/HP diferente, então
            # o mesmo bicho pode exigir % de cura diferente por personagem.
            # Só aparece quando O MAPA DESSA CONTA é Montanhas Gélidas.
            mobs_frame = ttk.LabelFrame(bloco, text=f" HP% por monstro ({nome}) ")
            hp_salvo = self._solo_hp_por_mob_saved.get(fone, {})
            try:
                vida_padrao_conta = int(self._solo_hp_geral_saved.get(fone) or 40)
            except (TypeError, ValueError):
                vida_padrao_conta = 40
            entries = {}
            for i, mob in enumerate(_mobs):
                r, c = divmod(i, 3)
                cel = tk.Frame(mobs_frame, bg=BG)
                cel.grid(row=r, column=c, padx=6, pady=3, sticky="w")
                tk.Label(cel, text=mob, bg=BG, fg=FG, font=("Segoe UI", 8),
                         wraplength=130, justify="left").pack(anchor="w")
                e = ttk.Entry(cel, width=6)
                e.insert(0, str(hp_salvo.get(mob, vida_padrao_conta)))
                e.pack(anchor="w")
                entries[mob] = e
            self.solo_mob_entries_por_conta[fone] = entries
            self.solo_mobs_frame_por_conta[fone] = mobs_frame

            # Deserto Escaldante: pergunta se essa conta luta com TUDO (geral)
            # ou SÓ com os 3 bosses raros que dropam item (foge do resto). Só
            # aparece quando O MAPA DESSA CONTA é Deserto Escaldante — igual
            # o HP% por monstro só aparece pra Montanhas Gélidas.
            deserto_frame = ttk.LabelFrame(bloco, text=f" Deserto Escaldante ({nome}) ")
            modo_salvo = self._solo_deserto_modo_saved.get(fone, "geral")
            modo_var = tk.StringVar(value=modo_salvo)
            self.solo_so_bosses_vars[fone] = modo_var
            tk.Radiobutton(deserto_frame, text="Geral (luta com qualquer monstro)",
                           variable=modo_var, value="geral", bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(4, 0))
            tk.Radiobutton(deserto_frame, text="Só os 3 bosses (Neith, Thoth, Seth) — foge do resto",
                           variable=modo_var, value="bosses", bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(0, 0))
            tk.Radiobutton(deserto_frame, text="Caçar Poeira Estrelar (foge de TODOS, inclusive bosses)",
                           variable=modo_var, value="poeira", bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(0, 4))
            self.solo_deserto_frame_por_conta[fone] = deserto_frame

            # Oásis Perdido: escolhe UM monstro específico entre os comuns do
            # mapa pra lutar só com ele (foge do resto, volta a caçar até ele
            # aparecer de novo). "Nenhum" = luta com qualquer um, igual antes.
            # Só aparece quando O MAPA DESSA CONTA é Oásis Perdido.
            oasis_frame = ttk.LabelFrame(bloco, text=f" Oásis Perdido ({nome}) ")
            try:
                _mobs_oasis = list(config.MOBS_OASIS_PERDIDO)
            except Exception:
                _mobs_oasis = []
            alvo_var = tk.StringVar(value=self._solo_alvo_oasis_saved.get(fone, ""))
            cb_alvo = ttk.Combobox(oasis_frame, textvariable=alvo_var, state="readonly",
                                   width=28, values=["(nenhum — luta com tudo)"] + _mobs_oasis)
            cb_alvo.set(alvo_var.get() if alvo_var.get() else "(nenhum — luta com tudo)")

            def _alvo_mudou(event=None, alvo_var=alvo_var, cb_alvo=cb_alvo):
                v = cb_alvo.get()
                alvo_var.set("" if v == "(nenhum — luta com tudo)" else v)
            cb_alvo.bind("<<ComboboxSelected>>", _alvo_mudou)
            cb_alvo.pack(anchor="w", padx=8, pady=(4, 4))
            self.solo_alvo_oasis_vars[fone] = alvo_var
            self.solo_oasis_frame_por_conta[fone] = oasis_frame

            # HP% por monstro da FLORESTA PROFUNDA — mesmo princípio do
            # quadro de Montanhas Gélidas acima (lista de monstros
            # diferente, ver config.MOBS_FLORESTA_PROFUNDA). Só aparece
            # quando O MAPA DESSA CONTA é "Floresta Profunda". BUG REAL
            # corrigido 2026-07-17 (relato do usuário: "não colocou pra
            # setar %hp para o Boss"): o Boss fica de fora de
            # MOBS_FLORESTA_PROFUNDA no config (ele tem o filtro próprio de
            # "fugir do boss"), mas quando a conta NÃO foge dele (luta
            # normal), ainda precisa de um %HP PRÓPRIO — ele é bem mais
            # forte (1800 HP) que os comuns (260-450). Junta o nome do boss
            # aqui só na hora de montar a grade do painel (o hp_por_mob no
            # hunter.py já funciona igual pra QUALQUER nome de monstro, boss
            # incluso — não precisou mexer no hunter.py).
            mobs_frame_floresta = ttk.LabelFrame(
                bloco, text=f" HP% por monstro — Floresta Profunda ({nome}) ")
            try:
                _mobs_floresta = list(config.MOBS_FLORESTA_PROFUNDA)
                _boss_floresta_grid = getattr(config, "BOSS_FLORESTA_PROFUNDA", "")
                if _boss_floresta_grid:
                    _mobs_floresta = _mobs_floresta + [_boss_floresta_grid]
            except Exception:
                _mobs_floresta = []
            entries_floresta = {}
            for i, mob in enumerate(_mobs_floresta):
                r, c = divmod(i, 3)
                cel = tk.Frame(mobs_frame_floresta, bg=BG)
                cel.grid(row=r, column=c, padx=6, pady=3, sticky="w")
                tk.Label(cel, text=mob, bg=BG, fg=FG, font=("Segoe UI", 8),
                         wraplength=130, justify="left").pack(anchor="w")
                e = ttk.Entry(cel, width=6)
                e.insert(0, str(hp_salvo.get(mob, vida_padrao_conta)))
                e.pack(anchor="w")
                entries_floresta[mob] = e
            self.solo_mob_entries_floresta_por_conta[fone] = entries_floresta
            self.solo_mobs_frame_floresta_por_conta[fone] = mobs_frame_floresta

            # "Fugir do Boss" da Floresta Profunda — pedido do usuário
            # 2026-07-17 (print mostrando o Boss 'Abominação do Aspecto
            # Caído', 1800 HP, bem mais forte que os goblins comuns do mapa,
            # 260-450 HP): marcado, a conta foge SÓ dele e continua caçando
            # os comuns normalmente. Desmarcado (padrão) = luta com tudo.
            floresta_frame = ttk.LabelFrame(bloco, text=f" Floresta Profunda ({nome}) ")
            fugir_boss_var = tk.BooleanVar(
                value=self._solo_fugir_boss_floresta_saved.get(fone, False))
            tk.Checkbutton(floresta_frame,
                           text=f"Fugir do Boss ({config.BOSS_FLORESTA_PROFUNDA}) "
                                f"— continua caçando os comuns",
                           variable=fugir_boss_var, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=4)
            self.solo_fugir_boss_floresta_vars[fone] = fugir_boss_var
            self.solo_floresta_frame_por_conta[fone] = floresta_frame

            # HP% poção (GERAL, por conta) — pra qualquer mapa QUE NÃO SEJA
            # Montanhas Gélidas (essa já tem o "HP% por monstro" mais fino,
            # acima). Substituiu o campo único "HP% poção (padrão)" que
            # ficava no quadro de Ajustes geral (tratava todo mundo igual).
            # Fica na MESMA linha do Tônico do Mercador do Deserto (que só
            # aparece quando o mapa é Deserto Escaldante), lado a lado, pra
            # não espalhar quadro por quadro embaixo um do outro.
            linha_hp_tonico = tk.Frame(bloco, bg=BG)

            hp_geral_frame = ttk.LabelFrame(linha_hp_tonico, text=f" HP% poção ({nome}) ")
            hp_geral_frame.pack(side="left")
            tk.Label(hp_geral_frame, text="Bebe poção quando o HP cair abaixo desse %:",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(4, 0))
            e_hp_geral = ttk.Entry(hp_geral_frame, width=6)
            valor_salvo_geral = self._solo_hp_geral_saved.get(fone)
            e_hp_geral.insert(0, str(valor_salvo_geral if valor_salvo_geral not in (None, "") else 40))
            e_hp_geral.pack(anchor="w", padx=8, pady=(0, 4))
            self.solo_hp_geral_entries[fone] = e_hp_geral
            self.solo_hp_geral_frame_por_conta[fone] = hp_geral_frame

            # Tônico do Mercador do Deserto — POR CONTA agora (antes era um
            # único combobox geral na aba de Ajustes, valendo igual pra todo
            # mundo). Só aparece quando o mapa dessa conta é Deserto
            # Escaldante (é a única tela onde esse NPC aparece).
            tonico_deserto_frame = ttk.LabelFrame(linha_hp_tonico, text=f" Tônico do Mercador ({nome}) ")
            tk.Label(tonico_deserto_frame, text="Comprar quando aparecer:", bg=BG, fg=MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=8, pady=(4, 0))
            _tonico_map_inv = {"": "Ignorar", "atk": "Super ATK", "def": "Super DEF", "crit": "Super CRIT"}
            tonico_var = tk.StringVar(
                value=_tonico_map_inv.get(self._solo_tonico_deserto_saved.get(fone, ""), "Ignorar"))
            cb_tonico_deserto = ttk.Combobox(tonico_deserto_frame, textvariable=tonico_var,
                                             width=16, state="readonly",
                                             values=["Ignorar", "Super ATK", "Super DEF", "Super CRIT"])
            cb_tonico_deserto.pack(anchor="w", padx=8, pady=(0, 4))
            self.solo_tonico_deserto_vars[fone] = tonico_var
            self.solo_deserto_tonico_frame_por_conta = getattr(
                self, "solo_deserto_tonico_frame_por_conta", {})
            self.solo_deserto_tonico_frame_por_conta[fone] = tonico_deserto_frame

            def _toggle(event=None, fone=fone, mapa_var=mapa_var, mobs_frame=mobs_frame,
                        deserto_frame=deserto_frame, oasis_frame=oasis_frame,
                        linha_hp_tonico=linha_hp_tonico, hp_geral_frame=hp_geral_frame,
                        tonico_deserto_frame=tonico_deserto_frame,
                        mobs_frame_floresta=mobs_frame_floresta, floresta_frame=floresta_frame):
                mapa_atual = mapa_var.get().strip()
                # BUG REAL corrigido 2026-07-17 (relato do usuário: "ficou MT
                # distante o nome do chat e o quadro de %hps" — print
                # mostrando um vão vazio entre a linha da conta e o quadro
                # de HP% por monstro): em Montanhas Gélidas/Floresta
                # Profunda, o 'HP% poção (geral)' fica escondido (linha
                # abaixo), mas o CONTAINER dele (linha_hp_tonico) continuava
                # sendo empacotado vazio, reservando a margem/padding dele à
                # toa. Agora só empacota linha_hp_tonico nos mapas onde ela
                # realmente mostra algo (qualquer um que NÃO seja Montanhas/
                # Floresta) — nesses dois, ela nem entra no layout.
                if mapa_atual in ("Montanhas Gélidas", "Floresta Profunda"):
                    linha_hp_tonico.pack_forget()
                    hp_geral_frame.pack_forget()
                else:
                    linha_hp_tonico.pack(fill="x", padx=(30, 4), pady=(2, 2))
                    hp_geral_frame.pack(side="left")
                if mapa_atual == "Montanhas Gélidas":
                    mobs_frame.pack(fill="x", padx=(30, 4), pady=(2, 2))
                else:
                    mobs_frame.pack_forget()
                if mapa_atual == "Deserto Escaldante":
                    deserto_frame.pack(fill="x", padx=(30, 4), pady=(2, 2))
                    tonico_deserto_frame.pack(side="left", padx=(12, 0))
                else:
                    deserto_frame.pack_forget()
                    tonico_deserto_frame.pack_forget()
                if mapa_atual == "Oásis Perdido":
                    oasis_frame.pack(fill="x", padx=(30, 4), pady=(2, 2))
                else:
                    oasis_frame.pack_forget()
                if mapa_atual == "Floresta Profunda":
                    mobs_frame_floresta.pack(fill="x", padx=(30, 4), pady=(2, 2))
                    floresta_frame.pack(fill="x", padx=(30, 4), pady=(2, 2))
                else:
                    mobs_frame_floresta.pack_forget()
                    floresta_frame.pack_forget()
            cb_mapa.bind("<<ComboboxSelected>>", _toggle)
            _toggle()
        if not algum:
            tk.Label(self.solo_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _coletar_caca_solo(self):
        ativa = self.solo_ativa.get()
        selecionadas = [f for f, v in getattr(self, "solo_sel_vars", {}).items() if v.get()]
        if ativa and not selecionadas:
            raise ValueError("Caçada Solo ativada: marque pelo menos 1 conta.")
        contas = []
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if fone not in selecionadas:
                continue
            c = card.coletar()
            if c:
                mapa_var = getattr(self, "solo_mapa_conta_vars", {}).get(fone)
                c["mapa"] = mapa_var.get().strip() if mapa_var else ""
                hp_por_mob = {}
                # Junta as entradas de Montanhas Gélidas E Floresta Profunda no
                # MESMO dict final (hp_por_mob é indexado por NOME do monstro,
                # então não há conflito entre os dois mapas — cada um só
                # aparece na tela do seu mapa mesmo). BUG REAL corrigido
                # 2026-07-17: salvar só um dos dois quadros aqui perderia os
                # valores do outro sempre que a conta trocasse de mapa.
                for mob, e in getattr(self, "solo_mob_entries_por_conta", {}).get(fone, {}).items():
                    try:
                        hp_por_mob[mob] = max(0, min(100, int(e.get().strip())))
                    except ValueError:
                        pass
                for mob, e in getattr(self, "solo_mob_entries_floresta_por_conta", {}).get(fone, {}).items():
                    try:
                        hp_por_mob[mob] = max(0, min(100, int(e.get().strip())))
                    except ValueError:
                        pass
                c["hp_por_mob"] = hp_por_mob
                fugir_boss_var = getattr(self, "solo_fugir_boss_floresta_vars", {}).get(fone)
                c["fugir_boss_floresta"] = bool(fugir_boss_var.get()) if fugir_boss_var else False
                so_bosses_var = getattr(self, "solo_so_bosses_vars", {}).get(fone)
                modo_deserto = so_bosses_var.get() if so_bosses_var else "geral"
                c["deserto_modo"] = modo_deserto
                c["so_bosses_deserto"] = (modo_deserto == "bosses")   # compat com versões antigas
                alvo_oasis_var = getattr(self, "solo_alvo_oasis_vars", {}).get(fone)
                c["alvo_oasis"] = (alvo_oasis_var.get().strip() if alvo_oasis_var else "")
                e_hp_geral = getattr(self, "solo_hp_geral_entries", {}).get(fone)
                if e_hp_geral is not None:
                    try:
                        c["vida_min_pct"] = max(0, min(100, int(e_hp_geral.get().strip())))
                    except ValueError:
                        pass
                _tonico_map = {"Ignorar": "", "Super ATK": "atk", "Super DEF": "def", "Super CRIT": "crit"}
                tonico_var = getattr(self, "solo_tonico_deserto_vars", {}).get(fone)
                c["tonico_deserto"] = _tonico_map.get(tonico_var.get(), "") if tonico_var else ""
                contas.append(c)
        ajustes = {"selecionadas": selecionadas, "contas": contas, "mapa": ""}
        for key, e in self.solo_ajustes.items():
            try:
                valor = max(0, int(e.get().strip()))
                if key == "vida_min_pct":
                    valor = min(valor, 100)
                ajustes[key] = valor
            except ValueError:
                pass
        return ativa, ajustes

    # ---------------- aba Missão Oásis ----------------
    # ---------------- aba Observador ----------------
    def _build_observador(self, dados):
        body = self.tab_observador
        tk.Label(body, text="Modo OBSERVADOR: o bot NÃO clica em nada — só fica lendo a "
                            "tela das contas marcadas e capturando XP/Gold/Loot pro "
                            "Relatório normal, enquanto você joga na mão. Serve pra "
                            "qualquer conteúdo (Masmorra, Caçada em Dupla, Cripta, Templo "
                            "do Oásis, Caçada Solo, Missão Oásis) — não precisa escolher "
                            "qual. Ativar aqui desliga os outros conteúdos.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=650, justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        ob = dados.get("OBSERVADOR") or {}
        self.observador_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "observador"))
        self.observador_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("observador"))
        tk.Checkbutton(body, text="Ativar Observador (desliga os outros conteúdos)",
                       variable=self.observador_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 8))

        sel = ttk.LabelFrame(body, text=" Contas a observar (marque quantas quiser) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        self.observador_sel_vars = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_observador_selector(preservar=True))
        self.observador_sel_frame = tk.Frame(sel, bg=BG)
        self.observador_sel_frame.pack(fill="x", padx=8, pady=4)
        contas_salvas = {c.get("phone", "") for c in (ob.get("contas") or [])}
        self._observador_sel_saved = contas_salvas
        self._rebuild_observador_selector(preservar=False)

    def _rebuild_observador_selector(self, preservar=False):
        if preservar:
            for fone, var in self.observador_sel_vars.items():
                if var.get():
                    self._observador_sel_saved.add(fone)
                else:
                    self._observador_sel_saved.discard(fone)
        for w in self.observador_sel_frame.winfo_children():
            w.destroy()
        self.observador_sel_vars = {}
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            nome = card.nome.get().strip() or fone
            var = tk.BooleanVar(value=(fone in self._observador_sel_saved))
            self.observador_sel_vars[fone] = var
            linha = tk.Frame(self.observador_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=2)
            tk.Checkbutton(linha, text=f"{nome} · {fone} · {card.papel.get()} · {card.perso.get().strip()}",
                           variable=var, bg=BG, fg=FG, selectcolor=PANEL,
                           activebackground=BG, activeforeground=FG).pack(side="left")

    def _coletar_observador(self):
        ativa = bool(getattr(self, "observador_ativa", tk.BooleanVar(value=False)).get())
        for fone, var in getattr(self, "observador_sel_vars", {}).items():
            if var.get():
                self._observador_sel_saved.add(fone)
            else:
                self._observador_sel_saved.discard(fone)
        contas_por_fone = {}
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if fone:
                contas_por_fone[fone] = card.nome.get().strip() or fone
        contas = []
        for fone in self._observador_sel_saved:
            nome = contas_por_fone.get(fone)
            if nome:
                contas.append({"phone": fone, "name": nome})
        if ativa and not contas:
            raise ValueError("Observador ativado: marque pelo menos 1 conta pra observar.")
        return ativa, {"contas": contas}

    def _build_mercado(self, dados):
        body = self.tab_mercado

        linha_ativar = tk.Frame(body, bg=BG)
        linha_ativar.pack(fill="x", anchor="w", padx=12, pady=(10, 8))
        self.mercado_ativo = tk.BooleanVar(value=bool(dados.get("MERCADO_ATIVO", False)))
        tk.Checkbutton(linha_ativar, text="Ativar venda automática no Mercado", variable=self.mercado_ativo,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")).pack(side="left")
        self._icone_info(linha_ativar,
            "Vende sozinho os itens marcados abaixo, de tempos em tempos, direto do "
            "inventário. Só vende quando a conta está LIVRE (nunca interrompe uma "
            "masmorra/caçada em andamento). A lista de itens vem do banco que cresce "
            "sozinho conforme eles vão dropando — não precisa cadastrar nada na mão."
            ).pack(side="left", padx=(4, 14))
        self._botao(linha_ativar, "🛒  Vender agora", ORANGE, self._vender_agora)
        self._icone_info(linha_ativar,
            "Dispara uma venda avulsa pras contas marcadas abaixo (SALVE antes). Não "
            "precisa ativar a venda automática nem esperar o intervalo."
            ).pack(side="left", padx=(2, 10))
        self._botao(linha_ativar, "📦  Ler inventário agora", BLUE, self._ler_inventario_agora)
        self._icone_info(linha_ativar,
            "Vai no inventário de cada conta marcada e joga todo item que já tiver "
            "direto na lista abaixo — útil pra popular a lista rapidinho."
            ).pack(side="left", padx=(2, 0))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        tk.Label(ajustes, text="Intervalo (min):", bg=BG, fg=FG).grid(
            row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        self.mercado_intervalo = ttk.Entry(ajustes, width=6)
        self.mercado_intervalo.insert(0, str(dados.get("MERCADO_INTERVALO_MIN", 30)))
        self.mercado_intervalo.grid(row=0, column=1, padx=(0, 18), pady=8, sticky="w")

        tk.Label(ajustes, text="Vender reforço:", bg=BG, fg=FG).grid(
            row=0, column=2, padx=(0, 4), pady=8, sticky="w")
        _reforcos_salvos = set(dados.get("MERCADO_REFORCOS", [0, 1, 2, 3]))
        self.mercado_reforco_vars = {}
        for i, nivel in enumerate([0, 1, 2, 3]):
            var = tk.BooleanVar(value=(nivel in _reforcos_salvos))
            self.mercado_reforco_vars[nivel] = var
            tk.Checkbutton(ajustes, text=f"+{nivel}", variable=var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 9)).grid(row=0, column=3 + i, padx=3, pady=8, sticky="w")
        self._icone_info(ajustes,
            "Esse filtro SÓ vale pra equipamento e alma (que têm '+N'); poção, tônico, "
            "chave, minério e outros materiais são sempre vendidos quando marcados, "
            "sem depender disso.").grid(row=0, column=7, padx=(6, 8), pady=8, sticky="w")

        tk.Label(ajustes, text="Mapa com mercador:", bg=BG, fg=FG).grid(
            row=1, column=0, columnspan=2, padx=8, pady=(0, 10), sticky="w")
        try:
            _mapas_sem_mercador = {m.lower() for m in
                                   (getattr(config, "MERCADO_MAPAS_SEM_MERCADOR", None) or [])}
            _mapas_mercado = [m for m in config.MAPAS_CONHECIDOS if m.lower() not in _mapas_sem_mercador]
        except Exception:
            _mapas_mercado = []
        self.mercado_mapa_venda = ttk.Combobox(ajustes, width=20, values=_mapas_mercado, state="readonly")
        self.mercado_mapa_venda.set(str(dados.get("MERCADO_MAPA_VENDA", "Floresta Sombria")))
        self.mercado_mapa_venda.grid(row=1, column=2, columnspan=4, padx=(0, 4), pady=(0, 10), sticky="w")
        self._icone_info(ajustes,
            "Se a conta estiver num mapa sem mercador (ex: Oásis Perdido) na hora de "
            "vender, ela viaja pra este mapa, vende, e volta.").grid(
            row=1, column=7, padx=(6, 8), pady=(0, 10), sticky="w")

        sec_contas = ttk.LabelFrame(body, text=" Contas que vendem ")
        sec_contas.pack(fill="x", padx=12, pady=6)
        self.mercado_sel_frame = tk.Frame(sec_contas, bg=BG)
        self.mercado_sel_frame.pack(fill="x", padx=8, pady=6)
        self._mercado_sel_saved = set(dados.get("MERCADO_CONTAS", []))

        sec_itens = ttk.LabelFrame(body, text=" Itens marcados pra vender ")
        sec_itens.pack(fill="both", expand=True, padx=12, pady=6)
        topo_itens = tk.Frame(sec_itens, bg=BG)
        topo_itens.pack(fill="x", padx=8, pady=(6, 2))
        self._botao(topo_itens, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_mercado_selector(preservar=True))
        canvas_itens = tk.Canvas(sec_itens, bg=BG, highlightthickness=0, height=260)
        scroll_itens = ttk.Scrollbar(sec_itens, orient="vertical", command=canvas_itens.yview)
        self.mercado_itens_frame = tk.Frame(canvas_itens, bg=BG)
        self.mercado_itens_frame.bind(
            "<Configure>", lambda e: canvas_itens.configure(scrollregion=canvas_itens.bbox("all")))
        canvas_itens.create_window((0, 0), window=self.mercado_itens_frame, anchor="nw")
        canvas_itens.configure(yscrollcommand=scroll_itens.set)
        canvas_itens.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        scroll_itens.pack(side="right", fill="y", pady=(0, 8))

        self._mercado_itens_saved = set(dados.get("MERCADO_ITENS", []))
        self.mercado_item_vars = {}
        self._rebuild_mercado_selector(preservar=False)

    def _rebuild_mercado_selector(self, preservar=False):
        if preservar and getattr(self, "mercado_sel_vars", None):
            for fone, var in self.mercado_sel_vars.items():
                if var.get():
                    self._mercado_sel_saved.add(fone)
                else:
                    self._mercado_sel_saved.discard(fone)
        if preservar and getattr(self, "mercado_item_vars", None):
            for nome, var in self.mercado_item_vars.items():
                if var.get():
                    self._mercado_itens_saved.add(nome)
                else:
                    self._mercado_itens_saved.discard(nome)
        for w in self.mercado_sel_frame.winfo_children():
            w.destroy()
        self.mercado_sel_vars = {}
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            nome = card.nome.get().strip() or fone
            var = tk.BooleanVar(value=(fone in self._mercado_sel_saved))
            self.mercado_sel_vars[fone] = var
            linha = tk.Frame(self.mercado_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=2)
            tk.Checkbutton(linha, text=f"{nome} · {fone}", variable=var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG).pack(side="left")

        for w in self.mercado_itens_frame.winfo_children():
            w.destroy()
        self.mercado_item_vars = {}
        banco = {}
        if os.path.exists(RELATORIO):
            try:
                with open(RELATORIO, encoding="utf-8") as f:
                    banco = (json.load(f) or {}).get("banco_itens") or {}
            except Exception:
                banco = {}
        if not banco:
            tk.Label(self.mercado_itens_frame,
                     text="(nenhum item registrado ainda — jogue um pouco pra ele aprender sozinho)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=4, pady=4)
            return
        _peso_raridade = {"lendario": 5, "epico": 4, "raro": 3, "incomum": 2, "normal": 1}
        _cor_raridade = {"lendario": "#fb8c00", "epico": "#fdd835", "raro": "#8e24aa",
                         "incomum": "#1e88e5", "normal": "#43a047"}
        _n_colunas = 4
        for i in range(_n_colunas):
            self.mercado_itens_frame.columnconfigure(i, weight=1, uniform="mercado_item")
        for idx, nome in enumerate(sorted(
                banco, key=lambda n: (-_peso_raridade.get(banco[n].get("raridade"), 0), n))):
            info = banco[nome]
            cor = _cor_raridade.get(info.get("raridade"), MUTED)
            var = tk.BooleanVar(value=(nome in self._mercado_itens_saved))
            self.mercado_item_vars[nome] = var
            celula = tk.Frame(self.mercado_itens_frame, bg=BG)
            celula.grid(row=idx // _n_colunas, column=idx % _n_colunas,
                       sticky="w", padx=4, pady=1)
            bolinha = tk.Canvas(celula, width=14, height=14, bg=BG, highlightthickness=0)
            bolinha.create_oval(2, 2, 13, 13, fill=cor, outline=cor)
            bolinha.pack(side="left", padx=(2, 6))
            tk.Checkbutton(celula, text=nome, variable=var,
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG, font=("Segoe UI", 9), anchor="w").pack(side="left")

    def _coletar_mercado(self):
        ativo = bool(getattr(self, "mercado_ativo", tk.BooleanVar(value=False)).get())
        for fone, var in getattr(self, "mercado_sel_vars", {}).items():
            if var.get():
                self._mercado_sel_saved.add(fone)
            else:
                self._mercado_sel_saved.discard(fone)
        for nome, var in getattr(self, "mercado_item_vars", {}).items():
            if var.get():
                self._mercado_itens_saved.add(nome)
            else:
                self._mercado_itens_saved.discard(nome)
        try:
            intervalo = max(1, int(self.mercado_intervalo.get().strip()))
        except ValueError:
            intervalo = 30
        reforcos = [n for n, var in getattr(self, "mercado_reforco_vars", {}).items() if var.get()]
        mapa_venda = self.mercado_mapa_venda.get().strip() or "Floresta Sombria"
        if ativo and not self._mercado_sel_saved:
            raise ValueError("Mercado ativado: marque pelo menos 1 conta pra vender.")
        if ativo and not self._mercado_itens_saved:
            raise ValueError("Mercado ativado: marque pelo menos 1 item pra vender.")
        return {"MERCADO_ATIVO": ativo, "MERCADO_INTERVALO_MIN": intervalo,
                "MERCADO_REFORCOS": reforcos, "MERCADO_ITENS": sorted(self._mercado_itens_saved),
                "MERCADO_CONTAS": sorted(self._mercado_sel_saved), "MERCADO_MAPA_VENDA": mapa_venda}

    def _build_missao_oasis(self, dados):
        body = self.tab_oasis
        tk.Label(body, text="Cada conta faz a busca ALEATÓRIA do Sunred (Oásis Perdido) "
                            "sozinha, em paralelo. O bot aceita a busca oferecida, confere se "
                            "o item bate com o escolhido abaixo — se não bateu, desiste e "
                            "tenta de novo — e caça até completar (50 do item-alvo + 200 no "
                            "total, contando os 50). Contas SEPARADAS das da Caçada Solo, mesmo "
                            "caçando no mesmo mapa. Ativar aqui desliga a Masmorra, a Caçada em "
                            "Dupla, a Cripta e a Caçada Solo.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9),
                 wraplength=650, justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        mo = dados.get("MISSAO_OASIS") or {}
        self.oasis_ativa = tk.BooleanVar(value=(dados.get("MODO_CONTEUDO") == "missao_oasis"))
        self.oasis_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("oasis"))
        tk.Checkbutton(body, text="Ativar Missão Oásis (desliga os outros conteúdos)",
                       variable=self.oasis_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(0, 8))

        ajustes = ttk.LabelFrame(body, text=" Ajustes ")
        ajustes.pack(fill="x", padx=12, pady=6)
        specs = [("Energia mínima", "energia_minima", 5),
                 ("Reabastecer até", "energia_alvo", 35),
                 ("Quantas missões", "max_missoes", 0)]
        self.oasis_ajustes = {}
        for i, (lbl, key, default) in enumerate(specs):
            col = i
            tk.Label(ajustes, text=lbl, bg=BG, fg=FG).grid(row=0, column=col, padx=8, pady=(4, 0), sticky="w")
            e = ttk.Entry(ajustes, width=8)
            e.insert(0, str(mo.get(key, default)))
            e.grid(row=1, column=col, padx=8, pady=(0, 6), sticky="w")
            self.oasis_ajustes[key] = e

        tk.Label(ajustes, text="Energia mínima/Reabastecer até: quando a energia cair abaixo do "
                               "mínimo, bebe Poção de Energia até chegar no alvo, depois volta a "
                               "procurar/caçar.  ·  Quantas missões: 0 = sem limite.  ·  Cada "
                               "tentativa de aceitar/verificar/desistir da busca gasta 1 de "
                               "energia, igual ao 'Caçar' normal da Caçada Solo.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), justify="left", wraplength=640).grid(
                 row=100, column=0, columnspan=4, padx=8, pady=(4, 4), sticky="w")

        self._oasis_sel_saved = list(mo.get("selecionadas") or
                                     [d.get("phone", "") for d in (mo.get("contas") or [])])
        _item_por_monstro = {v: k for k, v in getattr(config, "ITENS_MISSAO_OASIS", {}).items()}
        self._oasis_alvo_saved = {d.get("phone", ""): _item_por_monstro.get(d.get("monstro_alvo", ""), "")
                                  for d in (mo.get("contas") or [])}
        self._oasis_nurmora_saved = {d.get("phone", ""): bool(d.get("fazer_nurmora"))
                                     for d in (mo.get("contas") or [])}
        self._oasis_foco_nurmora_saved = {d.get("phone", ""): bool(d.get("focar_nurmora"))
                                          for d in (mo.get("contas") or [])}
        self._oasis_vida_saved = {d.get("phone", ""): d.get("vida_min_pct")
                                  for d in (mo.get("contas") or [])}
        sel = ttk.LabelFrame(body, text=" Contas na Missão Oásis (cada uma independente, "
                                        "com seu próprio item-alvo) ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Marque quantas quiser — cada uma faz sua própria busca, ao mesmo "
                           "tempo. O ITEM é OBRIGATÓRIO (sem ele, a conta fica parada) — o bot "
                           "sabe sozinho qual monstro dropa cada um. HP%: abaixo desse %, essa "
                           "conta bebe poção. "
                           "'+ Nurmora' liga a quest do Martelo Mágico (opcional) só pra essa "
                           "conta enquanto ela procura o Sunred. '🎯 Só Nurmora' foge de QUALQUER "
                           "monstro (explora mais rápido) e sempre aceita a Nurmora — sem mexer "
                           "na busca do Sunred (nunca cancela uma em andamento). As não marcadas "
                           "ficam paradas.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=650, justify="left").pack(anchor="w", padx=8, pady=(4, 4))

        self.oasis_sel_vars = {}
        self.oasis_alvo_vars = {}
        self.oasis_nurmora_vars = {}
        self.oasis_foco_nurmora_vars = {}
        self.oasis_vida_vars = {}
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_oasis_selector(preservar=True))

        # Frame SIMPLES, sem canvas/scroll — igual Cripta e Caçada em Dupla
        # (usar canvas aqui não tinha necessidade real, já que o número de
        # contas é o mesmo, e deixava o alinhamento diferente dos outros 2).
        self.oasis_sel_frame = tk.Frame(sel, bg=BG)
        self.oasis_sel_frame.pack(fill="x", padx=8, pady=4)

        self._rebuild_oasis_selector()

    def _rebuild_oasis_selector(self, preservar=False):
        if preservar and getattr(self, "oasis_sel_vars", None):
            self._oasis_sel_saved = [f for f, v in self.oasis_sel_vars.items() if v.get()]
        if preservar and getattr(self, "oasis_alvo_vars", None):
            for fone, var in self.oasis_alvo_vars.items():
                self._oasis_alvo_saved[fone] = var.get()
        if preservar and getattr(self, "oasis_nurmora_vars", None):
            for fone, var in self.oasis_nurmora_vars.items():
                self._oasis_nurmora_saved[fone] = var.get()
        if preservar and getattr(self, "oasis_foco_nurmora_vars", None):
            for fone, var in self.oasis_foco_nurmora_vars.items():
                self._oasis_foco_nurmora_saved[fone] = var.get()
        if preservar and getattr(self, "oasis_vida_vars", None):
            for fone, e_vida in self.oasis_vida_vars.items():
                self._oasis_vida_saved[fone] = e_vida.get().strip()
        for w in self.oasis_sel_frame.winfo_children():
            w.destroy()
        self.oasis_sel_vars = {}
        self.oasis_alvo_vars = {}
        self.oasis_nurmora_vars = {}
        self.oasis_foco_nurmora_vars = {}
        self.oasis_vida_vars = {}
        marcadas = set(self._oasis_sel_saved)
        try:
            _itens = list(config.ITENS_MISSAO_OASIS.keys())
        except Exception:
            _itens = []
        algum = False
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            linha = tk.Frame(self.oasis_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=(1, 2))
            var = tk.BooleanVar(value=(fone in marcadas))
            self.oasis_sel_vars[fone] = var
            tk.Checkbutton(linha, text=rotulo, variable=var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           anchor="w", width=44, font=("Segoe UI", 9)).pack(side="left")
            tk.Label(linha, text="Item:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 2))
            alvo_var = tk.StringVar(value=self._oasis_alvo_saved.get(fone, ""))
            cb_alvo = ttk.Combobox(linha, textvariable=alvo_var, values=_itens,
                                   width=28, state="readonly")
            if alvo_var.get():
                cb_alvo.set(alvo_var.get())
            cb_alvo.pack(side="left", padx=(0, 8))
            self.oasis_alvo_vars[fone] = alvo_var

            nurmora_var = tk.BooleanVar(value=bool(self._oasis_nurmora_saved.get(fone)))
            tk.Checkbutton(linha, text="+ Nurmora", variable=nurmora_var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(side="left")
            self.oasis_nurmora_vars[fone] = nurmora_var

            foco_nurmora_var = tk.BooleanVar(value=bool(self._oasis_foco_nurmora_saved.get(fone)))
            tk.Checkbutton(linha, text="🎯 Só Nurmora", variable=foco_nurmora_var, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 8)).pack(side="left")
            self.oasis_foco_nurmora_vars[fone] = foco_nurmora_var

            tk.Label(linha, text="HP%:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 2))
            e_vida = ttk.Entry(linha, width=4)
            valor_salvo = self._oasis_vida_saved.get(fone)
            e_vida.insert(0, str(valor_salvo if valor_salvo not in (None, "") else 40))
            e_vida.pack(side="left")
            self.oasis_vida_vars[fone] = e_vida
        if not algum:
            tk.Label(self.oasis_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _coletar_missao_oasis(self):
        ativa = self.oasis_ativa.get()
        selecionadas = [f for f, v in getattr(self, "oasis_sel_vars", {}).items() if v.get()]
        if ativa and not selecionadas:
            raise ValueError("Missão Oásis ativada: marque pelo menos 1 conta.")
        contas = []
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if fone not in selecionadas:
                continue
            c = card.coletar()
            if c:
                alvo_var = getattr(self, "oasis_alvo_vars", {}).get(fone)
                item_escolhido = alvo_var.get().strip() if alvo_var else ""
                if ativa and not item_escolhido:
                    raise ValueError(f"Missão Oásis: a conta '{c.get('name', fone)}' está "
                                      f"marcada mas sem item escolhido.")
                c["monstro_alvo"] = config.ITENS_MISSAO_OASIS.get(item_escolhido, "")
                nurmora_var = getattr(self, "oasis_nurmora_vars", {}).get(fone)
                c["fazer_nurmora"] = bool(nurmora_var.get()) if nurmora_var else False
                foco_nurmora_var = getattr(self, "oasis_foco_nurmora_vars", {}).get(fone)
                c["focar_nurmora"] = bool(foco_nurmora_var.get()) if foco_nurmora_var else False
                e_vida = getattr(self, "oasis_vida_vars", {}).get(fone)
                texto_vida = e_vida.get().strip() if e_vida else ""
                if texto_vida:
                    try:
                        c["vida_min_pct"] = max(0, min(100, int(texto_vida)))
                    except ValueError:
                        pass
                contas.append(c)
        ajustes = {"selecionadas": selecionadas, "contas": contas}
        for key, e in self.oasis_ajustes.items():
            try:
                valor = max(0, int(e.get().strip()))
                if key == "vida_min_pct":
                    valor = min(valor, 100)
                ajustes[key] = valor
            except ValueError:
                pass
        return ativa, ajustes


    # ---------------- aba Configuração ----------------
    def _build_config(self, dados):
        body = self.tab_cfg
        tk.Label(body, text=APP_SUB, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(pady=(6, 0))

        sec_cred = SecaoRecolhivel(body, "Credenciais (my.telegram.org) e bot",
                                   aberto=not bool(dados.get("BOT_USERNAME")))
        sec_cred.pack(fill="x", padx=12, pady=6)
        cred = tk.Frame(sec_cred.corpo, bg=BG)
        cred.pack(fill="x")
        self.api_id = self._campo(cred, "API ID (número):", str(dados.get("API_ID", "")), 0)
        self.api_hash = self._campo(cred, "API HASH:", dados.get("API_HASH", ""), 1)
        self.bot_user = self._campo(cred, "@ do bot (sem @):", dados.get("BOT_USERNAME", ""), 2)
        self.senha = self._campo(cred, "Senha da sala (4 dígitos):", dados.get("SALA_SENHA", "1234"), 3)
        cred.columnconfigure(1, weight=1)

        # Rugido do Tank — janela de HP%: fora dessa faixa (baixo ou alto
        # demais), o tank nem tenta usar o Rugido do Rochedo (aggro). O
        # Escudo de Ossos (cura) continua liberado só na rodada seguinte a
        # um Rugido confirmado — sem faixa própria (ver Brain.prioridade_tank).
        sec_rugido_col = SecaoRecolhivel(body, "🛡 Rugido do Tank — janela de HP%", aberto=False)
        sec_rugido_col.pack(fill="x", padx=12, pady=6)
        sec_rugido = sec_rugido_col.corpo
        rug_linha = tk.Frame(sec_rugido, bg=BG)
        rug_linha.pack(fill="x", padx=8, pady=6)
        tk.Label(rug_linha, text="HP% mín.:", bg=BG, fg=FG, font=("Segoe UI", 8)).pack(side="left")
        self.rugido_min = ttk.Entry(rug_linha, width=5)
        self.rugido_min.insert(0, str(dados.get("TANK_RUGIDO_HP_MIN", 40)))
        self.rugido_min.pack(side="left", padx=(4, 16))
        tk.Label(rug_linha, text="HP% máx.:", bg=BG, fg=FG, font=("Segoe UI", 8)).pack(side="left")
        self.rugido_max = ttk.Entry(rug_linha, width=5)
        self.rugido_max.insert(0, str(dados.get("TANK_RUGIDO_HP_MAX", 90)))
        self.rugido_max.pack(side="left", padx=(4, 0))
        tk.Label(sec_rugido, text="O tank só usa o Rugido do Rochedo (aggro) quando o HP estiver DENTRO dessa\n"
                                  "faixa — abaixo do mínimo é perigoso demais (foca em curar), acima do máximo\n"
                                  "não tem necessidade (HP já está bem). Fora da faixa, ele simplesmente não\n"
                                  "tenta o Rugido nessa rodada (ataca/defende normal, ou usa outra alma se tiver).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # Pausa automática de manutenção: o jogo às vezes fica fora do ar num
        # horário fixo (ex: toda madrugada) — o bot simplesmente espera (sem
        # clicar em nada) e volta sozinho quando a janela passa.
        sec_manut_col = SecaoRecolhivel(body, "🛠 Pausa programada", aberto=False)
        sec_manut_col.pack(fill="x", padx=12, pady=6)
        sec_manut = sec_manut_col.corpo
        self.manutencao_ativa = tk.BooleanVar(value=bool(dados.get("MANUTENCAO_ATIVA", False)))
        tk.Checkbutton(sec_manut, text="Ativar pausa automática", variable=self.manutencao_ativa,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 9)).pack(anchor="w", padx=8, pady=(6, 2))
        manut_linha = tk.Frame(sec_manut, bg=BG)
        manut_linha.pack(fill="x", padx=8, pady=(0, 6))
        tk.Label(manut_linha, text="De (HH:MM):", bg=BG, fg=FG, font=("Segoe UI", 8)).pack(side="left")
        self.manutencao_inicio = ttk.Entry(manut_linha, width=7)
        self.manutencao_inicio.insert(0, str(dados.get("MANUTENCAO_INICIO", "05:00")))
        self.manutencao_inicio.pack(side="left", padx=(4, 16))
        tk.Label(manut_linha, text="Até (HH:MM):", bg=BG, fg=FG, font=("Segoe UI", 8)).pack(side="left")
        self.manutencao_fim = ttk.Entry(manut_linha, width=7)
        self.manutencao_fim.insert(0, str(dados.get("MANUTENCAO_FIM", "06:00")))
        self.manutencao_fim.pack(side="left", padx=(4, 0))
        tk.Label(sec_manut, text="Nesse intervalo (horário local, todo dia) o bot só espera — não clica em nada,\n"
                                 "não desconecta. Assim que o horário 'Até' passa, volta a jogar sozinho, sem\n"
                                 "precisar clicar Iniciar de novo. Se 'Até' for menor que 'De', entende que a\n"
                                 "janela passa da meia-noite (ex: 23:30 até 00:30).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        sec_media_col = SecaoRecolhivel(body, "📊 Janela das médias (tempo/XP por execução)", aberto=False)
        sec_media_col.pack(fill="x", padx=12, pady=6)
        sec_media = sec_media_col.corpo
        media_linha = tk.Frame(sec_media, bg=BG)
        media_linha.pack(fill="x", padx=8, pady=6)
        tk.Label(media_linha, text="Últimas quantas execuções:", bg=BG, fg=FG,
                 font=("Segoe UI", 8)).pack(side="left")
        self.media_janela = ttk.Entry(media_linha, width=6)
        self.media_janela.insert(0, str(dados.get("MEDIA_JANELA", 10)))
        self.media_janela.pack(side="left", padx=(4, 0))
        tk.Label(sec_media, text="Usada nas estimativas de tempo (Masmorra/Cripta/Caçada em Dupla/\n"
                                 "Templo do Oásis/Caçada Solo) e no cálculo de quanto falta pro próximo\n"
                                 "nível. Maior = estimativa mais estável, mas reage mais devagar quando o\n"
                                 "ritmo muda de verdade (trocar de mapa, subir de nível). Menor = reage\n"
                                 "rápido, mas oscila mais. Não pesa em performance — padrão 10.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # Status ao vivo (HP/andar/cronômetro): some por completo quando
        # config.STATUS_AO_VIVO_ATIVO = False (flag pra quem quiser testar/
        # abrir mão dele por performance) — nem monta os widgets, e
        # write_status() do lado do hunter.py também já é no-op nesse caso,
        # então status.json nem chega a ser lido/escrito.
        if config.STATUS_AO_VIVO_ATIVO:
            sec_status_col = SecaoRecolhivel(body, "❤ Status ao vivo (HP)", aberto=True)
            sec_status_col.pack(fill="x", padx=12, pady=6)
            self._build_status_ao_vivo(sec_status_col.corpo)
        else:
            # Precisa existir mesmo desligado — o tick principal atualiza
            # self.estimativa_lbl independente do Status ao vivo estar
            # ligado, e sem isso o painel quebra (AttributeError) já no
            # primeiro tick. mostrar=False = criado mas invisível.
            self._criar_estimativa_lbl(body, mostrar=False)

        barra = tk.Frame(body, bg=BG); barra.pack(fill="x", padx=12, pady=(2, 8))
        self._botao(barra, "💾  Salvar", BLUE, self.salvar)
        self._botao(barra, "🔑  Login", ORANGE, self.login)
        self._botao(barra, "▶  Iniciar", GREEN, self.iniciar)
        self._botao(barra, "⏸  Parar", RED, self.parar)
        self.btn_parar_fim = self._botao(barra, "⏸ Parar no fim", ORANGE, self.parar_no_fim)
        self._botao(barra, "📟  Ver log", BLUE, self.abrir_log_terminal)

        tk.Label(body, text="Passos: 1) Salvar  2) Login (digita o código de cada conta aqui no app)  "
                            "3) Iniciar.", bg=BG, fg=MUTED, wraplength=650, justify="left").pack(anchor="w", padx=12)

        # Contas: seção RECOLHÍVEL (igual Credenciais) — some do caminho depois
        # de configurada, já que quase não muda no dia a dia. O corpo usa
        # fill="both"+expand=True (a lista de contas cresce à vontade).
        sec_contas = SecaoRecolhivel(body, "Contas (deixe uma em branco pra não usar; adicione mais se precisar)",
                                     aberto=True, fill="both", expand=True)
        sec_contas.pack(fill="both", expand=True, padx=12, pady=6)
        contas = sec_contas.corpo

        top_contas = tk.Frame(contas, bg=BG)
        top_contas.pack(fill="x", padx=6, pady=(6, 0))
        self._botao(top_contas, "➕  Adicionar conta", GREEN, lambda: self._add_conta({}))

        canvas_wrap = tk.Frame(contas, bg=BG)
        canvas_wrap.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        canvas = tk.Canvas(canvas_wrap, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=canvas.yview)
        self.contas_frame = tk.Frame(canvas, bg=BG)
        self.contas_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.contas_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        canvas.bind('<Button-4>', lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind('<Button-5>', lambda e: canvas.yview_scroll(1, "units"))

        self.cartoes = []
        base_acc = dados.get("ACCOUNTS") or [
            {"name": "tank", "role": "tank"}, {"name": "suporte", "role": "suporte"},
            {"name": "magodps", "role": "dps"}, {"name": "arqueiro", "role": "arqueiro"},
        ]
        for a in base_acc:
            self._add_conta(a)

        add_row = tk.Frame(contas, bg=BG)
        add_row.pack(fill="x", padx=6, pady=6)
        self._botao(add_row, "+ Adicionar conta", BLUE, lambda: self._add_conta({}))

        logf = ttk.LabelFrame(body, text=" Atividade ")
        logf.pack(fill="both", expand=True, padx=12, pady=6)
        self.logbox = scrolledtext.ScrolledText(logf, height=8, state="disabled",
                                                font=("Consolas", 8), bg="#111318", fg="#b7f7c2")
        self.logbox.pack(fill="both", expand=True, padx=2, pady=2)

    # ---------------- sub-aba Masmorra (dentro de "Masmorras") ----------------
    def _build_masmorra_subtab(self, dados):
        body = self.tab_masmorra_normal

        tk.Label(body, text="Conteúdo padrão do bot (grupo de até 5 contas). Só um conteúdo "
                            "roda por vez — ativar aqui desliga a Caçada em Dupla, Cripta, "
                            "Caçada Solo e Missão Oásis.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=650,
                 justify="left").pack(anchor="w", padx=12, pady=(8, 4))
        self.masmorra_ativa = tk.BooleanVar(
            value=(dados.get("MODO_CONTEUDO") in (None, "", "masmorra")))
        tk.Checkbutton(body, text="✔ Ativar Masmorra (desliga os outros conteúdos)",
                       variable=self.masmorra_ativa, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(0, 6))
        self.masmorra_ativa.trace_add("write", lambda *a: self._tornar_exclusivo("masmorra"))

        linha_wrap = tk.Frame(body, bg=BG)
        linha_wrap.pack(fill="x", padx=12, pady=6)
        linha_canvas = tk.Canvas(linha_wrap, bg=BG, highlightthickness=0)
        linha_hscroll = ttk.Scrollbar(linha_wrap, orient="horizontal", command=linha_canvas.xview)
        linha_canvas.configure(xscrollcommand=linha_hscroll.set)
        linha_canvas.pack(side="top", fill="x", expand=True)
        linha_hscroll.pack(side="top", fill="x")
        linha = tk.Frame(linha_canvas, bg=BG)
        linha_canvas.create_window((0, 0), window=linha, anchor="nw")

        def _linha_on_configure(event=None):
            linha_canvas.configure(scrollregion=linha_canvas.bbox("all"))
            linha_canvas.configure(height=linha.winfo_reqheight())
        linha.bind("<Configure>", _linha_on_configure)
        self.heal = {}
        for col in range(4):
            linha.columnconfigure(col, uniform="masmorra_ajustes")

        reforco = ttk.LabelFrame(linha, text=" HP mínimo pra iniciar a próxima ")
        reforco.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        tk.Label(reforco, text="%", bg=BG, fg=FG, font=("Segoe UI", 8)).grid(
            row=0, column=0, padx=6, pady=(4, 0), sticky="w")
        e_reforco = ttk.Entry(reforco, width=7)
        e_reforco.insert(0, str(int(round(float(dados.get("BETWEEN_DG_HEAL_RATIO", 0.85)) * 100))))
        e_reforco.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="w")
        self.heal["BETWEEN_DG_HEAL_RATIO"] = e_reforco
        tk.Label(reforco, text="Antes de entrar na PRÓXIMA masmorra, se o HP\n"
                              "estiver abaixo desse %, bebe poção pra já começar\n"
                              "no talo (em vez de entrar machucado).",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").grid(
                 row=2, column=0, padx=6, pady=(0, 4), sticky="w")

        pocf = ttk.LabelFrame(linha, text=" Poções ")
        pocf.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        self.masmorra_pocoes = {}
        poc_specs = [("Poções vida mín.", "MASMORRA_POCAO_VIDA_MINIMA", 50),
                     ("Aviso poção <", "MASMORRA_POCAO_VIDA_AVISO", 100)]
        for col, (lbl, key, default) in enumerate(poc_specs):
            tk.Label(pocf, text=lbl, bg=BG, fg=FG, font=("Segoe UI", 8)).grid(
                row=0, column=col, padx=6, pady=(4, 0), sticky="w")
            e = ttk.Entry(pocf, width=7)
            e.insert(0, str(dados.get(key, default)))
            e.grid(row=1, column=col, padx=6, pady=(0, 6), sticky="w")
            self.masmorra_pocoes[key] = e
        tk.Label(pocf, text="Aviso: antes de começar, se o estoque estiver\n"
                            "abaixo disso, avisa e pausa (reabastecer).\n"
                            "Vida mín.: pausa se cair abaixo disso durante o ciclo.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").grid(
                 row=2, column=0, columnspan=2, padx=6, pady=(0, 4), sticky="w")

        qtd = ttk.LabelFrame(linha, text=" Quantas masmorras ")
        qtd.grid(row=0, column=2, sticky="nsew", padx=(0, 12))
        tk.Label(qtd, text="0 = sem limite", bg=BG, fg=FG, font=("Segoe UI", 8)).grid(
            row=0, column=0, padx=6, pady=(4, 0), sticky="w")
        self.qtd = ttk.Entry(qtd, width=7)
        self.qtd.insert(0, str(dados.get("MAX_DUNGEONS", 0)))
        self.qtd.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="w")

        mapf = ttk.LabelFrame(linha, text=" Ir para o mapa (antes de começar) ")
        mapf.grid(row=0, column=3, sticky="nsew")
        tk.Label(mapf, text="Mapa", bg=BG, fg=FG, font=("Segoe UI", 8)).grid(
            row=0, column=0, padx=6, pady=(4, 0), sticky="w")
        try:
            _remover_da_masmorra = {"oásis perdido", "montanhas gélidas", "abismo"}
            _mapas = [m for m in config.MAPAS_CONHECIDOS if m.lower() not in _remover_da_masmorra]
        except Exception:
            _mapas = []
        # Rótulos das masmorras alternativas (Zuzu, Masmorra do Viadin,
        # Altheryn, etc) vêm de config.MASMORRAS_ALTERNATIVAS — somar uma
        # nova lá já faz ela aparecer aqui sozinha, sem editar o painel.
        try:
            _alt_masmorras = getattr(config, "MASMORRAS_ALTERNATIVAS", {})
            _rotulos_alt = [v["rotulo"] for v in _alt_masmorras.values()]
            self._rotulo_para_tipo = {v["rotulo"]: k for k, v in _alt_masmorras.items()}
        except Exception:
            _rotulos_alt = []
            self._rotulo_para_tipo = {}
        self.mapa = ttk.Combobox(mapf, width=20, values=[""] + _mapas + _rotulos_alt)
        _tipo_salvo = dados.get("TIPO_MASMORRA")
        _rotulo_salvo = _alt_masmorras.get(_tipo_salvo, {}).get("rotulo") if _tipo_salvo else None
        self.mapa.set(_rotulo_salvo or dados.get("MAPA_DESTINO", ""))
        self.mapa.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="w")
        tk.Label(mapf, text="Vazio = fica onde já está. Opções como 'Zuzu'/\n"
                            "'Masmorra do Viadin' são masmorras\n"
                            "alternativas; nos outros mapas, cria a Normal de sempre.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 7), justify="left").grid(
                 row=2, column=0, padx=6, pady=(0, 4), sticky="w")

        tk.Label(body, text="As contas (telefone/personagem/almas/tônico) ficam na aba Configuração, "
                            "junto com Login/Iniciar/Parar — os ajustes aqui valem só pra Masmorra normal.",
                 bg=BG, fg=MUTED, wraplength=900, justify="left").pack(anchor="w", padx=12, pady=(4, 4))

        self._masmorra_pct_salvos = {
            a.get("phone", ""): a.get("vida_min_pct")
            for a in (dados.get("ACCOUNTS") or [])
        }
        sel = ttk.LabelFrame(body, text=" Contas que vão na Masmorra ")
        sel.pack(fill="both", expand=True, padx=12, pady=6)
        tk.Label(sel, text="Marque só quem vai na masmorra.\nHP% poção: abaixo desse %, a conta "
                           "bebe poção (vale pra TODAS, inclusive o tank). Contas sem marcar "
                           "ficam logadas, só não entram.",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8), wraplength=900,
                 justify="left").pack(anchor="w", padx=8, pady=(4, 0))
        self.masmorra_sel_frame = tk.Frame(sel, bg=BG)
        self.masmorra_sel_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self._botao(sel, "↻  Atualizar lista", BLUE,
                    lambda: self._rebuild_masmorra_selector(preservar=True))
        self._rebuild_masmorra_selector()

    def _rebuild_masmorra_selector(self, preservar=False):
        if preservar and getattr(self, "masmorra_pct_entries", None):
            for fone, e_vida in self.masmorra_pct_entries.items():
                self._masmorra_pct_salvos[fone] = e_vida.get().strip()
        for w in self.masmorra_sel_frame.winfo_children():
            w.destroy()
        self.masmorra_pct_entries = {}
        algum = False
        for card in getattr(self, "cartoes", []):
            fone = card.fone.get().strip()
            if not fone:
                continue
            algum = True
            nome = card.nome.get().strip() or card.papel.get()
            rotulo = f"{nome}  ·  {fone}  ·  {card.papel.get()}  ·  {card.perso.get().strip()}"
            linha = tk.Frame(self.masmorra_sel_frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=1)
            # 'variable=card.ativa' — MESMA BooleanVar do checkbox "Esta conta
            # vai na masmorra" dentro do cartão completo (Configuração): os
            # dois ficam sincronizados automaticamente, marcar aqui já marca
            # lá (e vice-versa), sem precisar duplicar estado.
            tk.Checkbutton(linha, text=rotulo, variable=card.ativa, bg=BG, fg=FG,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=("Segoe UI", 9), anchor="w", width=44).pack(side="left")
            tk.Label(linha, text="HP% poção:", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(
                side="left", padx=(8, 2))
            e_vida = ttk.Entry(linha, width=4)
            default_vida = 50 if card.papel.get() == "tank" else 80
            valor_salvo = self._masmorra_pct_salvos.get(fone)
            e_vida.insert(0, str(valor_salvo if valor_salvo not in (None, "") else default_vida))
            e_vida.pack(side="left")
            self.masmorra_pct_entries[fone] = e_vida
        if not algum:
            tk.Label(self.masmorra_sel_frame, text="(configure contas na aba Configuração primeiro)",
                     bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

    def _add_conta(self, dados_conta):
        card = ContaCard(self.contas_frame, self, dados_conta, on_remover=self.remover_conta)
        self.cartoes.append(card)

    def remover_conta(self, card):
        if len(self.cartoes) <= 1:
            messagebox.showwarning("Não dá", "Precisa de pelo menos uma conta.")
            return
        card.destroy()
        self.cartoes.remove(card)

    # ---------------- aba Relatório ----------------
    def _build_relatorio(self):
        body = self.tab_rel
        body.configure(bg=REL_BG)

        style = ttk.Style()
        style.configure("Relatorio.Treeview", background=REL_CARD, fieldbackground=REL_CARD,
                        foreground=REL_TXT, rowheight=24, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Relatorio.Treeview.Heading", background=REL_BORDER, foreground=REL_TXT,
                         font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Relatorio.Treeview", background=[("selected", "#2d3a5c")])
        style.map("Relatorio.Treeview.Heading", background=[("active", REL_BORDER)])
        # Barras de progresso da Missão Oásis — antes usavam o Progressbar
        # padrão (cinza sem graça); agora cada uma tem sua cor (laranja pra
        # Monstros, verde-água pra Itens), trilho escuro combinando com o
        # resto do painel, e mais espessura.
        style.configure("Monstros.Horizontal.TProgressbar", troughcolor=REL_CARD,
                        background="#ff9f5b", bordercolor=REL_CARD, lightcolor="#ff9f5b",
                        darkcolor="#ff9f5b", thickness=14)
        style.configure("Itens.Horizontal.TProgressbar", troughcolor=REL_CARD,
                        background="#4ecbc4", bordercolor=REL_CARD, lightcolor="#4ecbc4",
                        darkcolor="#4ecbc4", thickness=14)

        top = tk.Frame(body, bg=REL_BG); top.pack(fill="x", padx=14, pady=(10, 4))
        self._botao(top, "↻  Atualizar", BLUE, self.atualizar_relatorio)
        self._botao(top, "🗑️  Resetar...", RED, self._show_reset_menu)
        self.pausa_box = tk.Label(top, text="Última pausa: —", bg=REL_BG, fg=REL_MUTED,
                                  font=("Segoe UI", 9), anchor="w", justify="left")
        self.pausa_box.pack(side="left", padx=10)

        # --- Cards "Hoje" (Masmorra / Caçada / Cripta / Total) ---
        cards = tk.Frame(body, bg=REL_BG)
        cards.pack(fill="x", padx=14, pady=6)
        self.card_masm = self._criar_card_hoje(cards, "🏰 Masmorra")
        self.card_caca = self._criar_card_hoje(cards, "🏔️ Caçada")
        self.card_templo = self._criar_card_hoje(cards, "🏛️ Templo O.")
        self.card_crip = self._criar_card_hoje(cards, "🦴 Cripta")
        self.card_solo = self._criar_card_hoje(cards, "🏹 Solo")
        self.card_oasis = self._criar_card_hoje(cards, "🏜️ Oásis")
        self.card_total = self._criar_card_hoje(cards, "Σ Total hoje", destaque=True)

        # --- Legenda de raridade ---
        legenda = tk.Frame(body, bg=REL_BG)
        legenda.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(legenda, text="Raridade:", bg=REL_BG, fg=REL_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 8))
        for nome_tier in reversed(RARIDADE_ORDEM):
            cor = RARIDADE_CORES[nome_tier]
            item = tk.Frame(legenda, bg=REL_BG)
            item.pack(side="left", padx=6)
            tk.Label(item, text="●", bg=REL_BG, fg=cor, font=("Segoe UI", 10)).pack(side="left")
            tk.Label(item, text=" " + RARIDADE_LABEL[nome_tier], bg=REL_BG, fg=REL_MUTED,
                     font=("Segoe UI", 8)).pack(side="left")

        sub = ttk.Notebook(body)
        sub.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.sub_masm = tk.Frame(sub, bg=REL_BG)
        self.sub_caca = tk.Frame(sub, bg=REL_BG)
        self.sub_templo = tk.Frame(sub, bg=REL_BG)
        self.sub_cripta = tk.Frame(sub, bg=REL_BG)
        self.sub_solo = tk.Frame(sub, bg=REL_BG)
        self.sub_dia = tk.Frame(sub, bg=REL_BG)
        self.sub_oasis = tk.Frame(sub, bg=REL_BG)
        sub.add(self.sub_masm, text="  Masmorra  ")
        sub.add(self.sub_caca, text="  Caçada em Dupla  ")
        sub.add(self.sub_templo, text="  Templo do Oásis  ")
        sub.add(self.sub_cripta, text="  Cripta  ")
        sub.add(self.sub_solo, text="  Caçada Solo  ")
        sub.add(self.sub_oasis, text="  🏜️ Oásis  ")
        sub.add(self.sub_dia, text="  Por dia  ")

        self.rel_masm = self._build_modo_tab(self.sub_masm,
                                             colunas=("n", "hora", "mapa", "tempo", "xp", "gold", "loot"))
        self.rel_cripta = self._build_modo_tab(self.sub_cripta)
        # Caçada Solo: uma sub-aba POR PERSONAGEM (cada um caça sozinho, então
        # o relatório também é individual) — as abas são criadas dinamicamente
        # em atualizar_relatorio(), conforme os nomes que aparecerem nos dados.
        self.sub_solo_nb = ttk.Notebook(self.sub_solo)
        self.sub_solo_nb.pack(fill="both", expand=True)
        self.rel_solo_por_conta = {}   # nome da conta -> widgets (de _build_modo_tab)

        sub_caca_nb = ttk.Notebook(self.sub_caca)
        sub_caca_nb.pack(fill="both", expand=True)
        self.sub_caca1 = tk.Frame(sub_caca_nb, bg=REL_BG)
        self.sub_caca2 = tk.Frame(sub_caca_nb, bg=REL_BG)
        sub_caca_nb.add(self.sub_caca1, text="  Dupla 1  ")
        sub_caca_nb.add(self.sub_caca2, text="  Dupla 2  ")
        self.rel_caca1 = self._build_modo_tab(self.sub_caca1)
        self.rel_caca2 = self._build_modo_tab(self.sub_caca2)

        sub_templo_nb = ttk.Notebook(self.sub_templo)
        sub_templo_nb.pack(fill="both", expand=True)
        self.sub_templo1 = tk.Frame(sub_templo_nb, bg=REL_BG)
        self.sub_templo2 = tk.Frame(sub_templo_nb, bg=REL_BG)
        sub_templo_nb.add(self.sub_templo1, text="  Dupla 1  ")
        sub_templo_nb.add(self.sub_templo2, text="  Dupla 2  ")
        self.rel_templo1 = self._build_modo_tab(self.sub_templo1,
                                                 colunas=("n", "hora", "tempo", "xp", "gold", "loot"))
        self.rel_templo2 = self._build_modo_tab(self.sub_templo2,
                                                 colunas=("n", "hora", "tempo", "xp", "gold", "loot"))

        tk.Label(self.sub_oasis, text="Progresso por conta (atualiza a cada caçada)",
                 bg=REL_BG, fg=REL_TXT, font=("Segoe UI", 10, "bold")).pack(
                 anchor="w", padx=8, pady=(8, 4))
        oasis_canvas_wrap = tk.Frame(self.sub_oasis, bg=REL_BG)
        oasis_canvas_wrap.pack(fill="both", expand=True, padx=4, pady=4)
        _oasis_prog_canvas = tk.Canvas(oasis_canvas_wrap, bg=REL_BG, highlightthickness=0)
        _oasis_prog_scroll = ttk.Scrollbar(oasis_canvas_wrap, orient="vertical",
                                           command=_oasis_prog_canvas.yview)
        self.oasis_progress_frame = tk.Frame(_oasis_prog_canvas, bg=REL_BG)
        self.oasis_progress_frame.bind(
            "<Configure>", lambda e: _oasis_prog_canvas.configure(
                scrollregion=_oasis_prog_canvas.bbox("all")))
        _oasis_prog_canvas.create_window((0, 0), window=self.oasis_progress_frame, anchor="nw")
        _oasis_prog_canvas.configure(yscrollcommand=_oasis_prog_scroll.set)
        _oasis_prog_canvas.pack(side="left", fill="both", expand=True)
        _oasis_prog_scroll.pack(side="left", fill="y")
        _oasis_prog_canvas.bind("<Enter>", lambda e: _oasis_prog_canvas.bind_all(
            "<MouseWheel>", lambda ev: _oasis_prog_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")))
        _oasis_prog_canvas.bind("<Leave>", lambda e: _oasis_prog_canvas.unbind_all("<MouseWheel>"))
        self._oasis_progress_widgets = {}   # nome da conta -> dict de widgets da linha

        self.diario_box = scrolledtext.ScrolledText(self.sub_dia, state="disabled",
                                                    font=("Consolas", 9), bg=REL_CARD, fg=REL_TXT,
                                                    insertbackground=REL_TXT, bd=0)
        self.diario_box.pack(fill="both", expand=True, padx=4, pady=4)

        self.atualizar_relatorio()

    def _criar_card_hoje(self, parent, titulo, destaque=False):
        card = tk.Frame(parent, bg=REL_CARD, highlightbackground=REL_BORDER,
                        highlightthickness=1)
        card.pack(side="left", fill="both", expand=True, padx=6)
        cor_titulo = "#ffd166" if destaque else REL_TXT
        tk.Label(card, text=titulo, bg=REL_CARD, fg=cor_titulo,
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        corpo = tk.Label(card, text="—", bg=REL_CARD, fg=REL_MUTED,
                         font=("Segoe UI", 9), anchor="w", justify="left")
        corpo.pack(fill="x", padx=10, pady=(0, 10))
        return corpo

    def _show_reset_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="⚠️ Zerar TUDO (Geral)", command=lambda: self._resetar_relatorio("tudo"))
        menu.add_separator()
        menu.add_command(label="Zerar apenas Masmorra", command=lambda: self._resetar_relatorio("masmorra"))
        menu.add_command(label="Zerar apenas Caçada Dupla 1", command=lambda: self._resetar_relatorio("caca1"))
        menu.add_command(label="Zerar apenas Caçada Dupla 2", command=lambda: self._resetar_relatorio("caca2"))
        menu.add_command(label="Zerar apenas Templo do Oásis Dupla 1", command=lambda: self._resetar_relatorio("templo1"))
        menu.add_command(label="Zerar apenas Templo do Oásis Dupla 2", command=lambda: self._resetar_relatorio("templo2"))
        menu.add_command(label="Zerar apenas Cripta", command=lambda: self._resetar_relatorio("cripta"))
        menu.add_command(label="Zerar apenas Caçada Solo", command=lambda: self._resetar_relatorio("caca_solo"))
        menu.add_command(label="Zerar apenas Histórico Diário", command=lambda: self._resetar_relatorio("diario"))
        
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        menu.post(x, y)

    def _resetar_relatorio(self, tipo):
        if not os.path.exists(RELATORIO):
            messagebox.showinfo("Vazio", "Ainda não existe nenhum relatório salvo.")
            return

        nomes_tipos = {
            "tudo": "TODO o relatório (Masmorras, Caçadas, Templo do Oásis, Criptas, Caçada Solo e Histórico Diário)",
            "masmorra": "os registros da Masmorra",
            "caca1": "os registros da Caçada Dupla 1",
            "caca2": "os registros da Caçada Dupla 2",
            "templo1": "os registros do Templo do Oásis Dupla 1",
            "templo2": "os registros do Templo do Oásis Dupla 2",
            "cripta": "os registros da Cripta",
            "caca_solo": "os registros da Caçada Solo",
            "diario": "o Histórico Diário"
        }

        confirmar = messagebox.askyesno("Confirmar Reset", 
                                        f"Tem certeza que deseja apagar {nomes_tipos[tipo]}?\n\nIsso não pode ser desfeito.")
        if not confirmar:
            return

        dados = {}
        try:
            with open(RELATORIO, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass

        if tipo == "tudo":
            dados = {}
        elif tipo == "masmorra":
            dados["masmorras"] = []
            dados["total"] = 0
        elif tipo == "caca1":
            dados["cacadas"] = [r for r in dados.get("cacadas", []) if int(r.get("grupo", 1)) != 1]
            dados["cacadas_total"] = len(dados["cacadas"])
        elif tipo == "caca2":
            dados["cacadas"] = [r for r in dados.get("cacadas", []) if int(r.get("grupo", 1)) != 2]
            dados["cacadas_total"] = len(dados["cacadas"])
        elif tipo == "templo1":
            dados["temploses"] = [r for r in dados.get("temploses", []) if int(r.get("grupo", 1)) != 1]
            dados["templo_oasis_total"] = len(dados["temploses"])
        elif tipo == "templo2":
            dados["temploses"] = [r for r in dados.get("temploses", []) if int(r.get("grupo", 1)) != 2]
            dados["templo_oasis_total"] = len(dados["temploses"])
        elif tipo == "cripta":
            dados["criptas"] = []
            dados["criptas_total"] = 0
        elif tipo == "caca_solo":
            dados["caca_solo"] = []
            dados["caca_solo_total"] = 0
        elif tipo == "diario":
            dados["diario"] = {}

        try:
            with open(RELATORIO, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            self.atualizar_relatorio()
            messagebox.showinfo("Sucesso", "Registros resetados com sucesso!")
        except Exception as err:
            messagebox.showerror("Erro", f"Ocorreu um erro ao resetar o relatório:\n{err}")

    @staticmethod
    def _agrupar_itens(lista_itens):
        """Lista repetida (['Poção de Vida', 'Poção de Vida', ...]) -> dict
        {item: quantidade}, ordenado do mais pro menos comum."""
        contagem = {}
        for it in lista_itens:
            contagem[it] = contagem.get(it, 0) + 1
        return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))

    def _inserir_itens_coloridos(self, text_widget, itens, catalogo_raridades, separador=", "):
        """Insere 'Item ×N' um atrás do outro num Text widget, colorido pela
        raridade (mesmo catálogo usado no quadro de equipamentos) — 'itens'
        pode ser uma lista repetida (agrupa e conta aqui) ou já um dict
        {item: qtd}."""
        agrupado = itens if isinstance(itens, dict) else self._agrupar_itens(itens)
        if not agrupado:
            text_widget.insert("end", "(nenhum item)")
            return
        ordenado = sorted(agrupado.items(), key=lambda kv: -kv[1])
        for i, (item, qtd) in enumerate(ordenado):
            tier = _raridade_do_item(item, catalogo_raridades)
            texto = f"{item} ×{qtd}"
            if tier:
                _, tier_nome, cor = tier
                tag = f"cor_{tier_nome}"
                text_widget.tag_configure(tag, foreground=cor)
                text_widget.insert("end", texto, tag)
            else:
                text_widget.insert("end", texto)
            if i < len(ordenado) - 1:
                text_widget.insert("end", separador)

    def _build_modo_tab(self, parent, colunas=("n", "hora", "tempo", "andar", "xp", "gold", "loot")):
        parent.configure(bg=REL_BG)
        tot = tk.Label(parent, text="—", bg=REL_BG, fg=REL_TXT,
                       font=("Segoe UI", 10, "bold"), anchor="w", justify="left")
        tot.pack(fill="x", padx=8, pady=(8, 4))

        equip_frame = tk.LabelFrame(parent, text=" 🛡️ Loot (equipamentos — cor = raridade) ",
                                    bg=REL_CARD, fg=REL_TXT, font=("Segoe UI", 9, "bold"),
                                    bd=1, relief="solid")
        equip_frame.pack(fill="x", padx=8, pady=3)
        equip_box = scrolledtext.ScrolledText(equip_frame, height=7, bg=REL_CARD, fg=REL_TXT,
                                              font=("Segoe UI", 9), bd=0, wrap="word",
                                              cursor="arrow", insertbackground=REL_TXT)
        equip_box.pack(fill="x", padx=6, pady=6)
        equip_box.config(state="disabled")

        # Consumíveis/recursos: ANTES era um Label com wraplength fixo (só
        # ocupava metade da largura de verdade, e quebrava linha em vez de
        # deixar rolar) e sem cor nenhuma. Agora é um Text sem quebra de
        # linha (wrap="none") + scroll HORIZONTAL, ocupando a largura toda
        # do quadro, com a MESMA coloração por raridade do quadro de cima.
        cons_frame = tk.LabelFrame(parent, text=" 🧪 Consumíveis / recursos ",
                                   bg=REL_CARD, fg=REL_TXT, font=("Segoe UI", 9, "bold"),
                                   bd=1, relief="solid")
        cons_frame.pack(fill="both", expand=True, padx=8, pady=3)
        cons_inner = tk.Frame(cons_frame, bg=REL_CARD)
        cons_inner.pack(fill="both", expand=True, padx=6, pady=6)
        cons_lbl = tk.Text(cons_inner, height=6, bg=REL_CARD, fg=REL_TXT,
                           font=("Segoe UI", 9), bd=0, wrap="none",
                           cursor="arrow", insertbackground=REL_TXT)
        cons_hsb = ttk.Scrollbar(cons_inner, orient="horizontal", command=cons_lbl.xview)
        cons_lbl.configure(xscrollcommand=cons_hsb.set)
        cons_lbl.pack(fill="both", expand=True, side="top")
        cons_hsb.pack(fill="x", side="bottom")
        cons_lbl.config(state="disabled")

        cols = tuple(colunas)
        titulos = {"n": "#", "hora": "Hora", "tempo": "Tempo", "andar": "Andar",
                   "mapa": "Masmorra", "xp": "XP", "gold": "Gold", "loot": "Loot"}
        larguras = {"n": 40, "hora": 100, "tempo": 80, "andar": 55, "mapa": 130,
                    "xp": 90, "gold": 90, "loot": 280}
        tree_frame = tk.Frame(parent, bg=REL_BG)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(4, 2))
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings",
                            style="Relatorio.Treeview", height=10)
        for c in cols:
            tree.heading(c, text=titulos[c])
            tree.column(c, width=larguras[c], anchor=("w" if c == "loot" else "center"),
                       stretch=(c == "loot"))
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        tree.tag_configure("par", background=REL_CARD)
        tree.tag_configure("impar", background="#171a26")

        # Painel de detalhe: como o Loot às vezes não cabe na coluna, clicar
        # numa linha mostra o texto COMPLETO aqui embaixo (sem precisar rolar
        # de lado pra ler tudo).
        detalhe_frame = tk.LabelFrame(parent, text=" 🔎 Loot completo da linha selecionada (agrupado, cor = raridade) ",
                                      bg=REL_CARD, fg=REL_TXT, font=("Segoe UI", 9, "bold"),
                                      bd=1, relief="solid")
        detalhe_frame.pack(fill="x", padx=8, pady=(2, 8))
        detalhe_lbl = scrolledtext.ScrolledText(detalhe_frame, height=3, bg=REL_CARD, fg=REL_TXT,
                                                font=("Segoe UI", 9), bd=0, wrap="word",
                                                cursor="arrow", insertbackground=REL_TXT)
        detalhe_lbl.pack(fill="x", padx=8, pady=6)
        detalhe_lbl.insert("end", "(clique numa linha da tabela pra ver o loot completo)")
        detalhe_lbl.config(state="disabled")

        def _on_select(event, lbl=detalhe_lbl, t=tree):
            sel = t.selection()
            if not sel:
                return
            info = getattr(t, "_loot_bruto", {}).get(sel[0])
            lbl.config(state="normal")
            lbl.delete("1.0", "end")
            if info:
                self._inserir_itens_coloridos(lbl, info, getattr(self, "_ultimo_catalogo_raridades", {}))
            else:
                lbl.insert("end", "(sem loot nessa execução)")
            lbl.config(state="disabled")
        tree.bind("<<TreeviewSelect>>", _on_select)

        return {"total": tot, "equip_box": equip_box, "cons_lbl": cons_lbl, "tree": tree,
                "detalhe": detalhe_lbl}

    @staticmethod
    def _agg(runs):
        xp = sum(int(r.get("xp_total", 0)) for r in runs)
        por_conta = {}
        for r in runs:
            for nome, g in (r.get("gold") or {}).items():
                por_conta.setdefault(nome, {"gold": 0, "drops": {}})["gold"] += g
            for nome, itens in (r.get("drops") or {}).items():
                pc = por_conta.setdefault(nome, {"gold": 0, "drops": {}})
                for it in itens:
                    pc["drops"][it] = pc["drops"].get(it, 0) + 1
        duracoes = [r["duracao_segundos"] for r in runs if r.get("duracao_segundos") is not None]
        duracao_media = (sum(duracoes) / len(duracoes)) if duracoes else None
        return xp, por_conta, duracao_media

    def _preencher_modo(self, runs, label, widgets, catalogo_raridades=None, extra_texto=""):
        tot_lbl = widgets["total"]; equip_box = widgets["equip_box"]
        cons_lbl = widgets["cons_lbl"]; tree = widgets["tree"]

        xp, por_conta, duracao_media = self._agg(runs)
        gold_total = sum(pc["gold"] for pc in por_conta.values())
        media_txt = f"   ·   ⏱️ média {_formatar_duracao_painel(duracao_media)}" if duracao_media else ""
        tot_lbl.config(text=f"{len(runs)} {label}(s) concluída(s)   ·   ⭐ {xp} XP   ·   "
                            f"💰 {gold_total} gold (soma das contas){media_txt}{extra_texto}")

        equip_box.config(state="normal")
        equip_box.delete("1.0", "end")
        equip_box.tag_configure("conta", foreground=REL_MUTED, font=("Segoe UI", 9, "bold"))
        teve_equip = False
        cons_por_conta = {}
        for nome in sorted(por_conta):
            pc = por_conta[nome]
            equip_itens, cons_itens = {}, {}
            for item, qtd in pc["drops"].items():
                tier = _raridade_do_item(item, catalogo_raridades)
                (equip_itens if tier else cons_itens)[item] = qtd
            if equip_itens:
                teve_equip = True
                equip_box.insert("end", f"{nome}:  ", "conta")
                self._inserir_itens_coloridos(equip_box, equip_itens, catalogo_raridades)
                equip_box.insert("end", "\n")
            if cons_itens:
                cons_por_conta[nome] = cons_itens
        if not teve_equip:
            equip_box.insert("end", "(nenhum equipamento dropado ainda)")
        equip_box.config(state="disabled")

        cons_lbl.config(state="normal")
        cons_lbl.delete("1.0", "end")
        cons_lbl.tag_configure("conta", foreground=REL_MUTED, font=("Segoe UI", 9, "bold"))
        if cons_por_conta:
            for i, nome in enumerate(sorted(cons_por_conta)):
                cons_lbl.insert("end", f"{nome}:  ", "conta")
                self._inserir_itens_coloridos(cons_lbl, cons_por_conta[nome], catalogo_raridades)
                if i < len(cons_por_conta) - 1:
                    cons_lbl.insert("end", "\n")
        else:
            cons_lbl.insert("end", "(nenhum consumível/recurso ainda)")
        cons_lbl.config(state="disabled")

        self._ultimo_catalogo_raridades = catalogo_raridades or {}
        colunas_tree = tree["columns"]
        for iid in tree.get_children():
            tree.delete(iid)
        tree._loot_bruto = {}
        for i, r in enumerate(reversed(runs)):
            tempo_str = r.get("tempo", r.get("duracao", "")) or "—"
            andar = r.get("andar", "—")
            drops_por_conta = r.get("drops") or {}
            todos_itens = [it for itens in drops_por_conta.values() for it in itens]
            agrupado = self._agrupar_itens(todos_itens)
            loot_txt = ", ".join(f"{item} ×{qtd}" for item, qtd in agrupado.items()) or "—"
            gold_run = sum((r.get("gold") or {}).values())
            valores_disp = {"n": r.get("n"), "hora": r.get("hora", ""), "tempo": tempo_str,
                            "andar": andar, "mapa": r.get("mapa") or "Normal",
                            "xp": r.get("xp_total", 0), "gold": gold_run,
                            "loot": loot_txt}
            iid = tree.insert("", "end", values=tuple(valores_disp[c] for c in colunas_tree),
                             tags=("par" if i % 2 == 0 else "impar",))
            tree._loot_bruto[iid] = todos_itens
        if not runs:
            tree.insert("", "end", values=tuple(
                "—" if c in ("n", "hora") else "" for c in colunas_tree))
            if colunas_tree and colunas_tree[1] == "hora":
                tree.set(tree.get_children()[0], "hora", f"Ainda não há {label} registrada.")

    def _exportar_catalogo_itens(self, catalogo_raridades):
        """Gera/atualiza 'catalogo_itens.json' — um banco de dados próprio com
        TODOS os itens já vistos em qualquer drop (nome, raridade aprendida,
        e tipo: equipamento/consumível/desconhecida), pra consulta futura
        (ex: eu — Claude — poder ver rapidamente o que já existe cadastrado
        em vez de descobrir tudo de novo do zero a cada atualização, e pra
        você conferir/corrigir manualmente se algum item ficar classificado
        errado). Roda toda vez que o Relatório é aberto/atualizado — bem
        barato (só alguns milhares de itens, na pior hipótese), então não
        precisa de nenhum controle de 'só se mudou'."""
        if not catalogo_raridades:
            return
        catalogo = {}
        for nome, raridade in sorted(catalogo_raridades.items()):
            tier = _raridade_do_item(nome, catalogo_raridades)
            if tier:
                tipo = "equipamento"
            elif _parece_equipamento(nome):
                tipo = "equipamento"
            else:
                tipo = "consumivel"
            catalogo[nome] = {"raridade": raridade, "tipo": tipo}
        caminho = os.path.join(BASE, "catalogo_itens.json")
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(catalogo, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            pass

    def atualizar_relatorio(self):
        dados = {"masmorras": [], "cacadas": [], "diario": {}}
        if os.path.exists(RELATORIO):
            try:
                with open(RELATORIO, encoding="utf-8") as f:
                    dados = json.load(f)
            except Exception:
                pass

        pausa = dados.get("ultima_pausa")
        if pausa:
            self.pausa_box.config(
                text=f"Última pausa: {pausa.get('descricao', pausa.get('motivo', '?'))}"
                     f"{' — ' + pausa['detalhe'] if pausa.get('detalhe') else ''}"
                     f"  ({pausa.get('quando', '')})")
        else:
            self.pausa_box.config(text="Última pausa: — (nunca pausou sozinho)")

        catalogo_raridades = dados.get("raridades", {})
        self._exportar_catalogo_itens(catalogo_raridades)
        self._preencher_modo(dados.get("masmorras", []), "masmorra", self.rel_masm, catalogo_raridades)
        todas_cacadas = dados.get("cacadas", [])
        caca_g1 = [r for r in todas_cacadas if int(r.get("grupo", 1)) == 1]
        caca_g2 = [r for r in todas_cacadas if int(r.get("grupo", 1)) == 2]
        self._preencher_modo(caca_g1, "caçada (Dupla 1)", self.rel_caca1, catalogo_raridades)
        self._preencher_modo(caca_g2, "caçada (Dupla 2)", self.rel_caca2, catalogo_raridades)
        todos_temploses = dados.get("temploses", [])
        templo_g1 = [r for r in todos_temploses if int(r.get("grupo", 1)) == 1]
        templo_g2 = [r for r in todos_temploses if int(r.get("grupo", 1)) == 2]
        self._preencher_modo(templo_g1, "Templo (Dupla 1)", self.rel_templo1, catalogo_raridades)
        self._preencher_modo(templo_g2, "Templo (Dupla 2)", self.rel_templo2, catalogo_raridades)
        self._preencher_modo(dados.get("criptas", []), "cripta", self.rel_cripta, catalogo_raridades)
        # Caçada Solo: agrupa os registros POR PERSONAGEM (cada 'gold'/'drops'
        # de um registro tem uma chave só, o nome da conta) e garante uma
        # sub-aba pra cada nome encontrado, criando na hora se for novo.
        por_personagem = {}
        for r in dados.get("caca_solo", []):
            nomes = list((r.get("gold") or {}).keys()) or list((r.get("drops") or {}).keys())
            nome = nomes[0] if nomes else "?"
            por_personagem.setdefault(nome, []).append(r)
        # Atualiza TODAS as abas já criadas ANTES + as que têm dado AGORA — senão
        # uma conta que foi zerada (0 registros agora) ficava com a aba mostrando
        # o dado antigo, porque só percorríamos quem tinha registro na hora.
        todos_os_nomes = set(self.rel_solo_por_conta.keys()) | set(por_personagem.keys())
        for nome in todos_os_nomes:
            runs = por_personagem.get(nome, [])
            if nome not in self.rel_solo_por_conta:
                aba = tk.Frame(self.sub_solo_nb, bg=REL_BG)
                self.sub_solo_nb.add(aba, text=f"  {nome}  ")
                self.rel_solo_por_conta[nome] = self._build_modo_tab(
                    aba, colunas=("n", "hora", "xp", "gold", "loot"))
            horas = []
            for r in runs:
                try:
                    # ano fixo (2000) só pra satisfazer o strptime — não usamos o
                    # ano real em lugar nenhum, só a DIFERENÇA entre os horários
                    # (evita o aviso de depreciação do Python 3.15 sobre datas
                    # sem ano, que passará a dar erro em versões futuras).
                    horas.append(datetime.datetime.strptime(
                        f"2000/{r.get('hora', '')}", "%Y/%d/%m %H:%M"))
                except ValueError:
                    pass
            if len(horas) >= 2:
                tempo_txt = f"   ·   ⏱️ caçando há {_formatar_duracao_painel((max(horas) - min(horas)).total_seconds())} (1ª → última caçada)"
            else:
                tempo_txt = ""
            self._preencher_modo(runs, "caçada", self.rel_solo_por_conta[nome],
                                catalogo_raridades, extra_texto=tempo_txt)

        hoje_key = datetime.date.today().strftime("%Y-%m-%d")
        d = (dados.get("diario") or {}).get(hoje_key, {})
        xm = d.get("xp_masmorra", d.get("xp_total", 0)); gm = d.get("gold_masmorra", 0)
        nm = d.get("masmorras", 0); mm = d.get("mortes_masmorra", 0)
        xc = d.get("xp_caca", 0); gc = d.get("gold_caca", 0); nc = d.get("cacadas", 0)
        mc = d.get("mortes_caca_dupla", 0)
        xt = d.get("xp_templo_oasis", 0); gt = d.get("gold_templo_oasis", 0); nt = d.get("templo_oasis", 0)
        mt = d.get("mortes_templo_oasis", 0)
        xcr = d.get("xp_cripta", 0); gcr = d.get("gold_cripta", 0); ncr = d.get("criptas", 0)
        mcr = d.get("mortes_cripta", 0)
        xs = d.get("xp_caca_solo", 0); gs = d.get("gold_caca_solo", 0); ns = d.get("caca_solo", 0)
        ms = d.get("mortes_caca_solo", 0)
        no = d.get("missao_oasis", 0)
        xo = d.get("xp_missao_oasis", 0); go = d.get("gold_missao_oasis", 0)
        mo = d.get("mortes_missao_oasis", 0)
        martelo_hoje = d.get("martelo_magico", 0)
        self.card_masm.config(text=f"{nm} feita(s)\n⭐ {xm} XP\n💰 {gm} gold\n☠️ {mm} morte(s)")
        self.card_caca.config(text=f"{nc} feita(s)\n⭐ {xc} XP\n💰 {gc} gold\n☠️ {mc} morte(s)")
        self.card_templo.config(text=f"{nt} feita(s)\n⭐ {xt} XP\n💰 {gt} gold\n☠️ {mt} morte(s)")
        self.card_crip.config(text=f"{ncr} feita(s)\n⭐ {xcr} XP\n💰 {gcr} gold\n☠️ {mcr} morte(s)")
        self.card_solo.config(text=f"{ns} feita(s)\n⭐ {xs} XP\n💰 {gs} gold\n☠️ {ms} morte(s)")
        self.card_oasis.config(text=f"{no} concluída(s)\n⭐ {xo} XP\n💰 {go} gold\n☠️ {mo} morte(s)\n"
                                    f"🔨 {martelo_hoje} Martelo(s)")
        self.card_total.config(text=f"⭐ {xm + xc + xt + xcr + xs + xo} XP\n"
                                    f"💰 {gm + gc + gt + gcr + gs + go} gold\n"
                                    f"☠️ {mm + mc + mt + mcr + ms + mo} morte(s)")

        linhas = []
        for dia, info in sorted((dados.get("diario") or {}).items(), reverse=True)[:30]:
            xm2 = info.get("xp_masmorra", info.get("xp_total", 0))
            gm2 = info.get("gold_masmorra", 0)
            xc2 = info.get("xp_caca", 0)
            gc2 = info.get("gold_caca", 0)
            xt2 = info.get("xp_templo_oasis", 0)
            gt2 = info.get("gold_templo_oasis", 0)
            xcr2 = info.get("xp_cripta", 0)
            gcr2 = info.get("gold_cripta", 0)
            xs2 = info.get("xp_caca_solo", 0)
            gs2 = info.get("gold_caca_solo", 0)
            xo2 = info.get("xp_missao_oasis", 0)
            go2 = info.get("gold_missao_oasis", 0)
            xp_total_dia = xm2 + xc2 + xt2 + xcr2 + xs2 + xo2
            gold_total_dia = gm2 + gc2 + gt2 + gcr2 + gs2 + go2
            mortes_total_dia = (info.get("mortes_masmorra", 0) + info.get("mortes_caca_dupla", 0)
                                + info.get("mortes_templo_oasis", 0) + info.get("mortes_cripta", 0)
                                + info.get("mortes_caca_solo", 0) + info.get("mortes_missao_oasis", 0))
            linhas.append(dia)
            linhas.append(f"   🏰 {info.get('masmorras', 0)} masm · ⭐ {xm2} XP · 💰 {gm2}g · "
                          f"☠️ {info.get('mortes_masmorra', 0)}")
            linhas.append(f"   🏔️ {info.get('cacadas', 0)} caça · ⭐ {xc2} XP · 💰 {gc2}g · "
                          f"☠️ {info.get('mortes_caca_dupla', 0)}")
            linhas.append(f"   🏛️ {info.get('templo_oasis', 0)} templo(s) · ⭐ {xt2} XP · 💰 {gt2}g · "
                          f"☠️ {info.get('mortes_templo_oasis', 0)}")
            linhas.append(f"   💀 {info.get('criptas', 0)} cripta · ⭐ {xcr2} XP · 💰 {gcr2}g · "
                          f"☠️ {info.get('mortes_cripta', 0)}")
            linhas.append(f"   🏹 {info.get('caca_solo', 0)} solo · ⭐ {xs2} XP · 💰 {gs2}g · "
                          f"☠️ {info.get('mortes_caca_solo', 0)}")
            linhas.append(f"   🏜️ {info.get('missao_oasis', 0)} missão(ões) oásis · "
                          f"⭐ {xo2} XP · 💰 {go2}g · ☠️ {info.get('mortes_missao_oasis', 0)}")
            _martelos_dia = info.get("martelo_magico", 0)
            if _martelos_dia:
                linhas.append(f"   🔨 {_martelos_dia} Martelo(s) Mágico(s) da Nurmora")
            linhas.append(f"   Σ TOTAL DO DIA: ⭐ {xp_total_dia} XP · 💰 {gold_total_dia}g · "
                          f"☠️ {mortes_total_dia} morte(s)")
            por_conta_dia = info.get("por_conta") or {}
            if por_conta_dia:
                linhas.append("   👤 Por personagem:")
                for nome in sorted(por_conta_dia, key=lambda n: -por_conta_dia[n].get("xp", 0)):
                    pc = por_conta_dia[nome]
                    mortes_pc = pc.get("mortes", 0)
                    mortes_txt = f" · ☠️ {mortes_pc}" if mortes_pc else ""
                    martelo_pc = pc.get("martelo_magico", 0)
                    martelo_txt = f" · 🔨 {martelo_pc}" if martelo_pc else ""
                    linhas.append(f"      {nome}: ⭐ {pc.get('xp', 0)} XP · 💰 {pc.get('gold', 0)}g"
                                  f"{mortes_txt}{martelo_txt}")
            linhas.append("")
        self.diario_box.config(state="normal"); self.diario_box.delete("1.0", "end")
        self.diario_box.insert("end", "\n".join(linhas) if linhas else "Ainda sem dados diários.")
        self.diario_box.config(state="disabled")

    def _campo(self, parent, label, valor, linha):
        tk.Label(parent, text=label, bg=BG).grid(row=linha, column=0, sticky="w", padx=8, pady=4)
        e = ttk.Entry(parent)
        e.insert(0, valor)
        e.grid(row=linha, column=1, sticky="ew", padx=8, pady=4)
        return e

    def _botao(self, parent, texto, cor, cmd):
        # width=11 fixo cortava rótulos maiores (ex.: "Ler inventário agora");
        # agora a largura mínima é 11, mas cresce pra caber o texto todo.
        largura = max(11, len(texto.strip()) + 2)
        b = tk.Button(parent, text=texto, command=cmd, bg=cor, fg="white",
                      font=("Segoe UI", 10, "bold"), relief="flat", width=largura,
                      activebackground=cor, cursor="hand2", padx=4, pady=6)
        b.pack(side="left", padx=5)
        return b

    def _add_tooltip(self, widget, texto):
        """Balãozinho de ajuda: some pra sempre, aparece só ao passar o mouse."""
        estado = {"win": None}

        def mostrar(_e=None):
            if estado["win"] is not None:
                return
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(win, text=texto, bg="#2b2b2b", fg="white",
                     font=("Segoe UI", 8), justify="left", padx=8, pady=6,
                     wraplength=340, relief="solid", bd=1).pack()
            estado["win"] = win

        def esconder(_e=None):
            if estado["win"] is not None:
                estado["win"].destroy()
                estado["win"] = None

        widget.bind("<Enter>", mostrar)
        widget.bind("<Leave>", esconder)
        return widget

    def _icone_info(self, parent, texto):
        """Cria o ícone de ajuda mas NÃO o posiciona — o chamador deve dar
        .pack(...) ou .grid(...) nele, conforme o gerenciador do container pai
        (nunca misturar os dois no mesmo parent)."""
        lbl = tk.Label(parent, text=" ⓘ", bg=BG, fg=MUTED,
                        font=("Segoe UI", 9, "bold"), cursor="question_arrow")
        self._add_tooltip(lbl, texto)
        return lbl

    def _log_gui(self, msg):
        self.root.after(0, lambda: self._append(msg))

    def _append(self, msg):
        self.logbox.config(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def _coletar(self):
        api_id = self.api_id.get().strip()
        if not api_id.isdigit():
            raise ValueError("API ID tem que ser só números.")
        if not self.api_hash.get().strip():
            raise ValueError("Preencha o API HASH.")
        if not self.bot_user.get().strip():
            raise ValueError("Preencha o @ do bot (sem @).")
        senha = self.senha.get().strip()
        if len(senha) != 4 or not senha.isdigit():
            raise ValueError("A senha da sala tem que ter 4 dígitos.")

        caca_ativa, caca_ajustes = self._coletar_caca_dupla()
        templo_ativa, templo_ajustes = self._coletar_templo_oasis()
        cripta_ativa, cripta_ajustes, cripta_pocoes = self._coletar_cripta()
        solo_ativa, solo_ajustes = self._coletar_caca_solo()
        oasis_ativa, oasis_ajustes = self._coletar_missao_oasis()
        observador_ativa, observador_ajustes = self._coletar_observador()
        mercado_settings = self._coletar_mercado()
        if sum([caca_ativa, templo_ativa, cripta_ativa, solo_ativa, oasis_ativa]) > 1:
            raise ValueError("Ative só um conteúdo por vez: Caçada Dupla, Templo do Oásis, "
                              "Cripta, Caçada Solo OU Missão Oásis (desmarque os outros).")

        contas = []
        for card in self.cartoes:
            c = card.coletar()
            if c is not None:
                # HP% poção mora na lista da aba Masmorras > Masmorra (não
                # no cartão), igual ao Templo do Oásis — injeta aqui na hora
                # de montar a conta final.
                e_vida = getattr(self, "masmorra_pct_entries", {}).get(c["phone"])
                if e_vida is not None:
                    try:
                        c["vida_min_pct"] = max(0, min(100, int(e_vida.get().strip())))
                    except ValueError:
                        pass
                contas.append(c)
        if not (caca_ativa or templo_ativa or cripta_ativa or solo_ativa or oasis_ativa
                or observador_ativa):
            if not contas:
                raise ValueError("Preencha pelo menos uma conta (telefone + personagem).")
        try:
            maxd = int(self.qtd.get().strip() or 0)
        except ValueError:
            maxd = 0
        modo = ("caca_dupla" if caca_ativa else "templo_oasis" if templo_ativa
                else "cripta" if cripta_ativa
                else "caca_solo" if solo_ativa else "missao_oasis" if oasis_ativa
                else "observador" if observador_ativa else "masmorra")
        _mapa_valor = self.mapa.get().strip()
        _tipo_alt = getattr(self, "_rotulo_para_tipo", {}).get(_mapa_valor)
        _mapa_da_alt = (config.MASMORRAS_ALTERNATIVAS.get(_tipo_alt, {}).get("mapa")
                        if _tipo_alt else None)
        out = {"API_ID": int(api_id), "API_HASH": self.api_hash.get().strip(),
               "BOT_USERNAME": self.bot_user.get().strip().lstrip("@"),
               "SALA_SENHA": senha, "ACCOUNTS": contas, "MAX_DUNGEONS": max(0, maxd),
               "MAPA_DESTINO": _mapa_da_alt if _tipo_alt else _mapa_valor,
               "TIPO_MASMORRA": _tipo_alt or "normal",
               "MODO_CONTEUDO": modo,
               "CACA_DUPLA": caca_ajustes,
               "TEMPLO_OASIS": templo_ajustes,
               "CRIPTA": cripta_ajustes,
               "POCOES": cripta_pocoes,
               "CACA_SOLO": solo_ajustes,
               "MISSAO_OASIS": oasis_ajustes,
               "OBSERVADOR": observador_ajustes}
        out.update(mercado_settings)
        for key, e in self.heal.items():
            try:
                out[key] = max(0.0, min(1.0, float(e.get().strip()) / 100.0))
            except ValueError:
                pass
        try:
            r_min = max(0, min(100, int(self.rugido_min.get().strip())))
            r_max = max(0, min(100, int(self.rugido_max.get().strip())))
            if r_min > r_max:
                r_min, r_max = r_max, r_min
            out["TANK_RUGIDO_HP_MIN"] = r_min
            out["TANK_RUGIDO_HP_MAX"] = r_max
        except ValueError:
            pass
        out["MANUTENCAO_ATIVA"] = bool(self.manutencao_ativa.get())

        def _valida_hhmm(texto, padrao):
            partes = texto.strip().split(":")
            if len(partes) == 2 and all(p.isdigit() for p in partes):
                h, m = int(partes[0]), int(partes[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return f"{h:02d}:{m:02d}"
            return padrao
        out["MANUTENCAO_INICIO"] = _valida_hhmm(self.manutencao_inicio.get(), "05:00")
        out["MANUTENCAO_FIM"] = _valida_hhmm(self.manutencao_fim.get(), "06:00")
        try:
            out["MEDIA_JANELA"] = max(3, min(200, int(self.media_janela.get().strip())))
        except ValueError:
            out["MEDIA_JANELA"] = 10
        for key, e in getattr(self, "masmorra_pocoes", {}).items():
            try:
                out[key] = max(0, int(e.get().strip()))
            except ValueError:
                pass
        return out

    def _vender_agora(self):
        """'🛒 Vender agora' (pedido do usuário 2026-07-15):
        - Bot JÁ rodando: grava um timestamp em vender_agora.flag — cada
          conta marcada em 'Contas que vendem' dispara uma venda avulsa
          assim que ficar livre (sem mexer no que já está rodando).
        - Bot DESLIGADO: grava vender_e_sair.flag e LANÇA o processo (mesmo
          jeito do botão Iniciar) — o hunter.py detecta esse arquivo, loga
          só as contas do Mercado, vende, e encerra sozinho (sem entrar em
          masmorra/caçada/etc, sem precisar de mais nada seu)."""
        if not os.path.exists(SETTINGS):
            messagebox.showwarning("Salve primeiro", "Clique em Salvar antes de Vender agora.")
            return
        if bot_rodando():
            try:
                with open(VENDER_AGORA_FLAG, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            except Exception as e:
                messagebox.showerror("Vender agora", f"Não consegui gravar o pedido:\n{e}")
                return
            messagebox.showinfo("Vender agora",
                "Pedido enviado! Cada conta marcada em 'Contas que vendem' vai vender assim que "
                "ficar livre (entre execuções) — pode levar alguns minutos dependendo do que "
                "estiver fazendo agora. Acompanhe pelo log.")
            return
        try:
            with open(VENDER_E_SAIR_FLAG, "w", encoding="utf-8") as f:
                f.write("1")
            if IS_WINDOWS and os.path.exists(INICIAR_CMD):
                self._iniciar_proc = subprocess.Popen(
                    ["cmd", "/c", INICIAR_CMD], cwd=BASE,
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                cmd = bot_cmd()
                boot_log = open(os.path.join(BASE, "boot_stderr.log"), "w", encoding="utf-8")
                self._iniciar_proc = subprocess.Popen(cmd, cwd=BASE, stdout=boot_log,
                                                      stderr=subprocess.STDOUT,
                                                      creationflags=NO_WINDOW)
            self._log_gui("🛒 Bot ligado em modo 'Vender agora' — vai vender e se desligar sozinho.")
        except Exception as err:
            messagebox.showerror("Erro ao iniciar", f"Erro: {err}")

    def _ler_inventario_agora(self):
        """'📦 Ler inventário agora' (pedido do usuário 2026-07-15): mesmo
        esquema do 'Vender agora', só que lê o inventário de cada conta
        marcada em 'Contas que vendem' e joga TODO item visto (com bolinha
        de raridade) na lista de itens do Mercado — sem precisar esperar
        eles dropar de novo."""
        if not os.path.exists(SETTINGS):
            messagebox.showwarning("Salve primeiro", "Clique em Salvar antes de Ler inventário agora.")
            return
        if bot_rodando():
            try:
                with open(LER_INVENTARIO_FLAG, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            except Exception as e:
                messagebox.showerror("Ler inventário agora", f"Não consegui gravar o pedido:\n{e}")
                return
            messagebox.showinfo("Ler inventário agora",
                "Pedido enviado! Cada conta marcada em 'Contas que vendem' vai ler o inventário "
                "assim que ficar livre (entre execuções) — pode levar alguns minutos. Depois, "
                "clique em 'Atualizar lista' pra ver os itens novos.")
            return
        try:
            with open(LER_INVENTARIO_E_SAIR_FLAG, "w", encoding="utf-8") as f:
                f.write("1")
            if IS_WINDOWS and os.path.exists(INICIAR_CMD):
                self._iniciar_proc = subprocess.Popen(
                    ["cmd", "/c", INICIAR_CMD], cwd=BASE,
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                cmd = bot_cmd()
                boot_log = open(os.path.join(BASE, "boot_stderr.log"), "w", encoding="utf-8")
                self._iniciar_proc = subprocess.Popen(cmd, cwd=BASE, stdout=boot_log,
                                                      stderr=subprocess.STDOUT,
                                                      creationflags=NO_WINDOW)
            self._log_gui("📦 Bot ligado em modo 'Ler inventário' — vai ler e se desligar sozinho.")
        except Exception as err:
            messagebox.showerror("Erro ao iniciar", f"Erro: {err}")

    def _limpar_itens_duplicados(self):
        """Junta entradas do banco_itens (aba Mercado) que são o MESMO item
        mas ficaram com nomes levemente diferentes — a causa mais comum era
        um emoji de classe de arma (🏹🗡️🪓...) que sobrava colado no nome
        antes da correção do dia 2026-07-15 (ver hunter.py:
        parse_recompensas). Soma 'vezes_visto' e junta as 'origens' das
        entradas juntadas, mantendo a raridade de maior peso encontrada."""
        if not os.path.exists(RELATORIO):
            messagebox.showinfo("Limpeza de itens", "Nenhum relatorio.json encontrado ainda.")
            return
        try:
            with open(RELATORIO, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception as e:
            messagebox.showerror("Limpeza de itens", f"Não consegui ler o relatorio.json:\n{e}")
            return
        banco = dados.get("banco_itens") or {}
        if not banco:
            messagebox.showinfo("Limpeza de itens", "Não há itens registrados ainda.")
            return

        def _nome_limpo(n):
            n2 = re.sub(r"^[^\wÀ-ÿ]+", "", n).strip()
            n2 = re.sub(r"[✦\s]+$", "", n2).strip()
            return n2 or n

        peso = {"lendario": 5, "epico": 4, "raro": 3, "incomum": 2, "normal": 1}
        novo_banco = {}
        total_antes = len(banco)
        for nome, info in banco.items():
            chave = _nome_limpo(nome)
            alvo = novo_banco.setdefault(chave, {"raridade": None, "emoji": "",
                                                 "primeira_vez": "", "vezes_visto": 0, "origens": []})
            alvo["vezes_visto"] += info.get("vezes_visto", 0)
            for o in info.get("origens", []) or []:
                if o not in alvo["origens"]:
                    alvo["origens"].append(o)
            if peso.get(info.get("raridade"), 0) > peso.get(alvo.get("raridade"), 0):
                alvo["raridade"] = info.get("raridade")
                alvo["emoji"] = info.get("emoji", "")
            if info.get("primeira_vez") and (not alvo["primeira_vez"]
                                              or info["primeira_vez"] < alvo["primeira_vez"]):
                alvo["primeira_vez"] = info["primeira_vez"]

        dados["banco_itens"] = novo_banco
        try:
            with open(RELATORIO, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Limpeza de itens", f"Não consegui salvar:\n{e}")
            return

        total_depois = len(novo_banco)
        juntados = total_antes - total_depois
        messagebox.showinfo("Limpeza de itens",
            f"Pronto! {total_antes} → {total_depois} itens"
            f"{f' (juntei {juntados} duplicado(s))' if juntados else ' (nenhum duplicado encontrado)'}.")
        if getattr(self, "mercado_itens_frame", None) is not None:
            self._rebuild_mercado_selector(preservar=True)

    def _abrir_config_rapida(self):
        """Janela pequena e separada (fica sempre por cima) só com os botões
        de ação principais — pedido do usuário 2026-07-15, pra não precisar
        navegar até a aba Configuração toda vez que quiser Salvar/Iniciar/
        Parar/Ver log rapidinho."""
        if getattr(self, "_config_rapida_win", None) is not None and self._config_rapida_win.winfo_exists():
            self._config_rapida_win.lift()
            self._config_rapida_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("Ajustes rápidos")
        win.configure(bg=BG)
        win.geometry("300x400")
        win.minsize(220, 260)
        win.resizable(True, True)
        win.attributes("-topmost", True)
        self._config_rapida_win = win
        tk.Label(win, text="⚙ Ajustes rápidos", bg=BG, fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(pady=(14, 10))
        corpo = tk.Frame(win, bg=BG)
        corpo.pack(fill="both", expand=True, padx=14)

        def _botao_vertical(texto, cor, cmd):
            b = tk.Button(corpo, text=texto, command=cmd, bg=cor, fg="white",
                          font=("Segoe UI", 10, "bold"), relief="flat",
                          activebackground=cor, cursor="hand2", padx=4, pady=8)
            b.pack(side="top", fill="x", pady=4)
            return b

        _botao_vertical("💾  Salvar", BLUE, self.salvar)
        _botao_vertical("🔑  Login", ORANGE, self.login)
        _botao_vertical("▶  Iniciar", GREEN, self.iniciar)
        _botao_vertical("⏸  Parar", RED, self.parar)
        _botao_vertical("⏸  Parar no fim", ORANGE, self.parar_no_fim)
        _botao_vertical("📟  Ver log", BLUE, self.abrir_log_terminal)
        _botao_vertical("🧹  Limpar itens duplicados", ORANGE, self._limpar_itens_duplicados)
        tk.Button(win, text="Ir pra aba Configuração →",
                  command=lambda: (self.nb.select(self.tab_cfg), win.destroy()),
                  bg=BG, fg="#8ab4ff", relief="flat", activebackground=BG,
                  activeforeground="#aecbff", font=("Segoe UI", 8, "underline"),
                  cursor="hand2", bd=0).pack(pady=(6, 12))

    def salvar(self):
        try:
            dados = self._coletar()
        except ValueError as err:
            messagebox.showerror("Faltou algo", str(err))
            return None
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
        except Exception as err:
            messagebox.showerror("Erro ao salvar", str(err))
            return None
        messagebox.showinfo("Salvo", "Configuração salva!")
        return dados

    def _checar_atualizacao(self):
        """Verifica a versão mais recente publicada em config.UPDATE_REPO
        (GitHub Releases) e, se houver uma mais nova, baixa o .zip e aplica
        (substitui só os arquivos de código — nunca settings.json, sessões,
        relatório ou logs). Funciona igual em Windows e Linux (é Python puro:
        urllib + zipfile), sem depender de bot.exe."""
        if bot_rodando():
            if not messagebox.askyesno(
                    "Bot rodando",
                    "O bot está rodando agora. Os arquivos novos só valem a partir do "
                    "próximo 'Iniciar' — o que já estiver em execução continua na versão "
                    "atual até você parar e iniciar de novo.\n\nQuer checar e baixar a "
                    "atualização mesmo assim?"):
                return
        self.btn_update.config(state="disabled", text="⟳ Verificando...")
        threading.Thread(target=self._atualizar_thread, daemon=True).start()

    def _atualizar_thread(self):
        repo = getattr(config, "UPDATE_REPO", "") or ""
        if not repo:
            self.root.after(0, lambda: self._atualizar_fim(
                erro="UPDATE_REPO não está configurado no config.py."))
            return
        try:
            tag, zip_url, html_url = _github_latest(repo)
        except Exception as e:
            msg = str(e)
            self.root.after(0, lambda: self._atualizar_fim(
                erro=f"Não consegui checar atualização:\n{msg}"))
            return
        if not tag:
            self.root.after(0, lambda: self._atualizar_fim(
                erro=f"Não achei nenhum Release publicado em '{repo}' ainda."))
            return
        if _parse_ver(tag) <= _parse_ver(config.VERSION):
            self.root.after(0, lambda: self._atualizar_fim(
                info=f"Você já está na versão mais recente (v{config.VERSION})."))
            return
        if not zip_url:
            self.root.after(0, lambda: self._atualizar_fim(
                erro=f"Encontrei a versão {tag}, mas não achei um .zip anexado a "
                     f"esse Release.\n\nConfira em: {html_url}"))
            return
        tmp_zip = os.path.join(BASE, "_update_tmp.zip")
        tmp_dir = os.path.join(BASE, "_update_tmp_extract")
        try:
            ctx = _ssl_ctx()
            req = urllib.request.Request(zip_url, headers={"User-Agent": "TofuBotUpdater"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                conteudo = r.read()
            with open(tmp_zip, "wb") as f:
                f.write(conteudo)
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            with zipfile.ZipFile(tmp_zip) as z:
                z.extractall(tmp_dir)

            # separa: painel.exe novo (sou EU rodando — não dá pra sobrescrever
            # na hora, ver mais abaixo), bot.exe novo (esse pode trocar direto,
            # já não está rodando nesse momento) e o resto (.py/.cmd/.sh — só
            # pra quem roda a partir do código-fonte). MODO ONEDIR (PyInstaller
            # --onedir, trocado de --onefile pra evitar falso-positivo de
            # antivírus): painel.exe/bot.exe agora vêm acompanhados de uma
            # pasta '_internal' com as bibliotecas — não dá pra copiar ela
            # arquivo por arquivo aqui (alguns já estão em uso pelo processo
            # atual), então só ANOTA o caminho dela e pula por completo nesse
            # loop; quem copia de vez é o .bat auxiliar (_preparar_self_
            # update_exe), depois que o processo atual fechar de verdade.
            rodando_como_exe = bool(getattr(sys, "frozen", False))
            novo_painel_exe = None
            novo_bot_exe = None
            novo_internal_dir = None
            aplicados = []
            for raiz, dirs, arquivos in os.walk(tmp_dir):
                if os.path.basename(raiz) == "_internal":
                    if novo_internal_dir is None:
                        novo_internal_dir = raiz
                    dirs[:] = []
                    continue
                for nome in arquivos:
                    if _atualizacao_arquivo_protegido(nome):
                        continue
                    origem = os.path.join(raiz, nome)
                    if nome.lower() == "painel.exe":
                        novo_painel_exe = origem
                        continue
                    if nome.lower() == "bot.exe":
                        novo_bot_exe = origem
                        continue
                    if rodando_como_exe:
                        # Quem usa o .exe NUNCA recebe .py/.cmd/.sh do zip —
                        # o zip do Release pode ter as duas coisas juntas (pra
                        # servir tanto quem roda os .py quanto quem roda o
                        # .exe), mas o .exe não precisa e não deve ganhar o
                        # código-fonte (é o problema que estamos evitando).
                        continue
                    if not nome.endswith((".py", ".cmd", ".sh")):
                        continue
                    destino = os.path.join(BASE, nome)
                    shutil.copy2(origem, destino)
                    aplicados.append(nome)

            if novo_painel_exe and rodando_como_exe and IS_WINDOWS:
                # EU SOU o painel.exe rodando agora — não dá pra me sobrescrever
                # enquanto estou aberto (Windows trava o arquivo em uso, e a
                # pasta '_internal' também tem arquivos em uso). Um .bat
                # auxiliar espera eu fechar de verdade, troca tudo (exe(s) +
                # '_internal'), e me reabre — daí eu me fecho sozinho.
                self.root.after(0, lambda: self._preparar_self_update_exe(
                    novo_painel_exe, novo_bot_exe, tag, tmp_dir, novo_internal_dir))
                return

            if novo_bot_exe:
                destino_bot = os.path.join(BASE, "bot.exe")
                shutil.copy2(novo_bot_exe, destino_bot)
                aplicados.append("bot.exe")
                if novo_internal_dir:
                    # painel roda via .py (não travou nada), mas o bot.exe
                    # baixado é onedir e precisa da '_internal' dele também.
                    shutil.copytree(novo_internal_dir, os.path.join(BASE, "_internal"),
                                    dirs_exist_ok=True)
                    aplicados.append("_internal")
            if novo_painel_exe and not rodando_como_exe:
                # painel.exe novo baixado, mas estou rodando via python
                # painel.py (não sou o exe) — aplica igual, sem risco de
                # travar (não estou usando esse arquivo agora).
                destino_painel = os.path.join(BASE, "painel.exe")
                shutil.copy2(novo_painel_exe, destino_painel)
                aplicados.append("painel.exe")

            if not aplicados:
                if rodando_como_exe:
                    self.root.after(0, lambda: self._atualizar_fim(
                        info=f"Encontrei a versão {tag}, mas ela ainda não tem um "
                             f".exe compilado anexado (só código-fonte) — essa "
                             f"atualização é só pra quem roda os arquivos .py. "
                             f"Nada foi alterado aqui."))
                else:
                    self.root.after(0, lambda: self._atualizar_fim(
                        erro="Baixei o Release, mas não achei nenhum arquivo "
                             ".py/.cmd/.sh dentro do zip pra aplicar."))
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return
            shutil.rmtree(tmp_dir, ignore_errors=True)
            # BUG REAL corrigido (relatado pelo usuário): quem roda via
            # python (não o .exe) aplicava os .py novos, mas o 'config'
            # já importado na memória continuava com o VERSION ANTIGO até
            # fechar e abrir de novo — clicando em Atualizar de novo sem
            # reabrir, o bot comparava com a versão VELHA e achava que
            # ainda tinha atualização disponível, num loop confuso. Agora
            # recarrega o config na hora — o VERSION fica certo mesmo sem
            # reabrir (o resto do código, tipo hunter.py/painel.py, ainda
            # precisa reabrir pra valer, mas pelo menos a checagem de
            # versão para de ficar "presa" na versão antiga).
            if "config.py" in aplicados:
                try:
                    importlib.reload(config)
                except Exception:
                    pass
            self.root.after(0, lambda: self._atualizar_fim(
                info=f"Atualizado para a versão {tag}!\n\n"
                     f"Arquivos trocados: {', '.join(sorted(aplicados))}\n\n"
                     f"Feche e abra o TofuBot de novo para usar a versão nova."))
        except Exception as e:
            msg = str(e)
            self.root.after(0, lambda: self._atualizar_fim(
                erro=f"Erro ao baixar/aplicar a atualização:\n{msg}"))
        finally:
            try:
                if os.path.exists(tmp_zip):
                    os.remove(tmp_zip)
            except Exception:
                pass

    def _preparar_self_update_exe(self, novo_painel_exe: str, novo_bot_exe: str, tag: str,
                                  tmp_dir: str, novo_internal_dir: str = None):
        """Monta um .bat que espera ESTE painel.exe fechar de verdade, troca o
        arquivo pela versão nova (e o bot.exe/pasta '_internal' também, se
        vieram novos), reabre o painel.exe atualizado, e se autodestrói.
        Depois disso, fecha este processo (painel.exe atual) — só então o
        .bat consegue sobrescrever os arquivos, já que o Windows mantém tudo
        que está em uso (o .exe E os arquivos dentro de '_internal', modo
        --onedir) bloqueado enquanto está rodando."""
        if not messagebox.askyesno(
                "Atualização pronta",
                f"Encontrei a versão {tag}. Pra aplicar, o TofuBot precisa "
                f"FECHAR sozinho, trocar o arquivo, e abrir de novo — isso leva "
                f"só alguns segundos.\n\nSe o bot estiver rodando, ele PARA "
                f"agora (não fica travado no meio de uma masmorra).\n\n"
                f"Quer continuar?"):
            self._atualizar_fim(info="Atualização cancelada.")
            return

        # garante que o bot(.exe)/hunter.py em background não fica travado
        # segurando arquivos (ex: bot.exe) que o .bat também precisa trocar.
        try:
            self._parar_bot_para_atualizar()
        except Exception:
            pass

        # CHECAGEM ANTES de preparar o .bat: se o antivírus já quarentenou/
        # apagou o .exe recém-baixado (comum com .exe vindos da internet),
        # é melhor avisar CLARAMENTE agora do que deixar o .bat descobrir
        # isso sozinho mais tarde, escondido — bug real relatado: "fecha e
        # reabre, mas continua mostrando que tem atualização" (o arquivo
        # nunca chegou a ser copiado de verdade).
        if not os.path.exists(novo_painel_exe) or os.path.getsize(novo_painel_exe) < 100_000:
            self._atualizar_fim(
                erro="O painel.exe novo baixado sumiu ou ficou incompleto antes de "
                     "aplicar a atualização — isso costuma acontecer quando o "
                     "antivírus coloca o arquivo em quarentena por engano (comum "
                     "com .exe baixado da internet).\n\n"
                     "Confira a quarentena do seu antivírus, adicione uma exceção "
                     "pra pasta do TofuBot, e tente 'Atualizar' de novo.")
            return

        exe_atual = os.path.abspath(sys.executable)
        pid_atual = os.getpid()
        bat_path = os.path.join(BASE, "_aplicar_atualizacao_painel.bat")
        linhas = [
            "@echo off",
            "setlocal",
            "title TofuBot - aplicando atualizacao...",
            f"echo Aguardando o TofuBot (PID {pid_atual}) fechar...",
            ":espera",
            f'tasklist /fi "PID eq {pid_atual}" 2^>nul | find "{pid_atual}" >nul',
            "if not errorlevel 1 (",
            "    timeout /t 1 /nobreak >nul",
            "    goto espera",
            ")",
            "timeout /t 1 /nobreak >nul",
            "echo Copiando painel.exe novo...",
            f'copy /y "{novo_painel_exe}" "{exe_atual}"',
            "if errorlevel 1 (",
            "    echo.",
            "    echo [ERRO] Nao consegui copiar o painel.exe novo por cima do atual.",
            "    echo Isso costuma acontecer se o antivirus colocou o arquivo baixado",
            "    echo em quarentena/apagou ele antes desse script conseguir usar.",
            "    echo Confira o antivirus (pasta de quarentena) e tente 'Atualizar' de novo.",
            "    echo.",
            "    pause",
            "    exit /b 1",
            ")",
        ]
        if novo_bot_exe:
            destino_bot = os.path.join(BASE, "bot.exe")
            linhas += [
                "echo Copiando bot.exe novo...",
                f'copy /y "{novo_bot_exe}" "{destino_bot}"',
                "if errorlevel 1 (",
                "    echo [ERRO] Nao consegui copiar o bot.exe novo.",
                "    pause",
                "    exit /b 1",
                ")",
            ]
        if novo_internal_dir:
            # xcopy /E (com subpastas, inclusive vazias) /I (destino é pasta)
            # /Y (sobrescreve sem perguntar) — sincroniza a '_internal' nova
            # por cima da antiga. Não apaga a antiga inteira antes (mais
            # seguro: se faltar 1 arquivo novo por algum motivo, não perde os
            # antigos à toa).
            destino_internal = os.path.join(BASE, "_internal")
            linhas += [
                "echo Copiando arquivos internos (_internal) novos...",
                f'xcopy "{novo_internal_dir}" "{destino_internal}\\" /E /I /Y',
                "if errorlevel 1 (",
                "    echo [ERRO] Nao consegui copiar a pasta _internal nova.",
                "    pause",
                "    exit /b 1",
                ")",
            ]
        tmp_dir_extracao = tmp_dir
        linhas += [
            "echo.",
            "echo Atualizacao aplicada! Reabrindo o TofuBot...",
            f'rmdir /s /q "{tmp_dir_extracao}" 2^>nul',
            f'start "" "{exe_atual}"',
            "timeout /t 2 /nobreak >nul",
            'del "%~f0"',
        ]
        try:
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write("\r\n".join(linhas))
        except Exception as e:
            self._atualizar_fim(erro=f"Não consegui preparar o script de atualização:\n{e}")
            return

        try:
            # JANELA VISÍVEL DE PROPÓSITO (antes rodava escondida, com
            # CREATE_NO_WINDOW): se algo falhar no meio do caminho (ex:
            # antivírus apagou o .exe baixado antes de chegar aqui), o
            # usuário via só a atualização "sumir" sem nenhuma pista do que
            # deu errado. Agora qualquer erro aparece na tela, com pause,
            # em vez de falhar em silêncio e reabrir a versão ANTIGA sem
            # avisar nada (bug real relatado: "fecha e abre, mas continua
            # mostrando que tem atualização" — o.exe novo nunca chegou a
            # ser copiado de verdade, e ninguém percebeu por quê).
            subprocess.Popen(["cmd", "/c", bat_path], cwd=BASE,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as e:
            self._atualizar_fim(erro=f"Não consegui iniciar o script de atualização:\n{e}")
            return

        # Fecha JÁ, sem depender de nenhum diálogo bloqueante antes — o .bat
        # (agora visível) já mostra o que está acontecendo por conta própria.
        os._exit(0)

    def _parar_bot_para_atualizar(self):
        """Se o bot(.exe)/hunter.py estiver rodando, para ele antes de trocar
        o bot.exe (senão o arquivo fica em uso e o .bat não consegue
        sobrescrever)."""
        if not bot_rodando():
            return
        try:
            self.parar()
        except Exception:
            pass

    def _atualizar_fim(self, info=None, erro=None):
        self.btn_update.config(state="normal", text="⟳ Atualizar")
        if erro:
            messagebox.showerror("Atualização", erro)
        elif info:
            messagebox.showinfo("Atualização", info)

    def login(self):
        if self.salvar() is None:
            return
        threading.Thread(target=self._login_thread, daemon=True).start()

    def _login_thread(self):
        import asyncio
        import config
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        dados = carregar()
        try:
            api_id = int(dados.get("API_ID") or 0)
        except ValueError:
            api_id = 0
        api_hash = dados.get("API_HASH", "")
        vistos = set()
        contas = []
        contas_caca = [c for grupo in (dados.get("CACA_DUPLA") or {}).get("grupos", []) for c in grupo]
        contas_templo = [c for grupo in (dados.get("TEMPLO_OASIS") or {}).get("grupos", []) for c in grupo]
        for acc in dados.get("ACCOUNTS", []) + contas_caca + contas_templo:
            fone = acc.get("phone", "").strip()
            sid = config.session_id(fone) if fone else ""
            if fone and sid not in vistos:
                vistos.add(sid)
                contas.append(acc)
        if not api_id or not api_hash:
            self._log_gui("⚠️ Preencha e Salve API ID/HASH antes do login.")
            return
        if not contas:
            self._log_gui("⚠️ Nenhuma conta com telefone pra logar. Preencha "
                          "telefone + personagem e clique Salvar antes do Login.")
            return
        self._log_gui("🔑 Vou logar " + str(len(contas)) + " conta(s): "
                      + ", ".join(f"{a.get('name','?')} ({a.get('phone','?')})" for a in contas))
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run():
            for idx, acc in enumerate(contas):
                nome = acc.get("name", f"conta{idx+1}")
                fone = acc.get("phone", "")
                self._set_login_status(fone, "conectando…", MUTED)
                client = TelegramClient(config.session_path(BASE, fone, nome), api_id, api_hash)
                try:
                    await client.connect()
                    precisa_codigo = not await client.is_user_authorized()
                    if not precisa_codigo:
                        try:
                            me = await client.get_me()
                        except Exception:
                            me = None
                        num_sessao = getattr(me, "phone", "") if me else ""
                        if _mesmo_numero(num_sessao, fone):
                            self._set_login_status(fone, "logada ✓", GREEN)
                            self._log_gui(f"{nome}: já estava logada ✓")
                        else:
                            self._log_gui(f"{nome}: ⚠️ a sessão salva não era deste número "
                                          f"(era de +{num_sessao or '?'}) — refazendo o login "
                                          f"de {fone}.")
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                            await asyncio.sleep(0.2)
                            _apagar_sessao(fone)
                            client = TelegramClient(
                                config.session_path(BASE, fone, nome), api_id, api_hash)
                            await client.connect()
                            precisa_codigo = True
                    if precisa_codigo:
                        self._set_login_status(fone, "pedindo código…", ORANGE)
                        self._log_gui(f"{nome}: pedindo o código do Telegram…")
                        await client.send_code_request(fone)
                        code = self._ask(f"Código de login de {nome}\n({fone}):")
                        if not code:
                            self._set_login_status(fone, "cancelado", RED)
                            continue
                        try:
                            await client.sign_in(fone, code.strip())
                        except SessionPasswordNeededError:
                            pw = self._ask(f"Senha de 2 etapas de {nome}:", secret=True)
                            await client.sign_in(password=pw)
                        self._set_login_status(fone, "logada ✓", GREEN)
                        self._log_gui(f"{nome}: logada ✓")
                except Exception as e:
                    self._set_login_status(fone, "erro", RED)
                    self._log_gui(f"{nome}: erro no login — {e}")
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            self._log_gui("✅ Login concluído. Agora clique em Iniciar.")

        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    def _set_login_status(self, phone, texto, cor):
        def aplicar():
            for card in self.cartoes + self.caca_cartoes:
                if card.fone.get().strip() == phone:
                    card.login_lbl.config(text=texto, fg=cor)
                    break
        self.root.after(0, aplicar)

    def _ask(self, prompt, secret=False):
        res = {}
        ev = threading.Event()

        def show():
            res["v"] = simpledialog.askstring("TofuBot — Login", prompt,
                                              show="*" if secret else "", parent=self.root)
            ev.set()
        self.root.after(0, show)
        ev.wait()
        return res.get("v")

    def abrir_log_terminal(self):
        """Abre o run.log num TERMINAL SEPARADO do sistema (tipo 'tail -f') —
        mais fácil de acompanhar em tempo real que a caixinha pequena aqui
        dentro do painel. No Linux tenta os terminais mais comuns (um de
        cada vez, até um funcionar); no Windows usa o PowerShell."""
        try:
            if not os.path.exists(RUN_LOG):
                open(RUN_LOG, "a", encoding="utf-8").close()
        except Exception as err:
            messagebox.showerror("Erro", f"Não consegui preparar o run.log: {err}")
            return

        if IS_WINDOWS:
            try:
                # ANTES: montava um comando CMD+PowerShell como uma ÚNICA
                # string com aspas dentro de aspas, passado direto pro
                # subprocess.Popen — o Windows reescapa essa string sozinho
                # (list2cmdline) e isso bagunçava as aspas internas, abrindo
                # a janela mas sem rodar o comando de verdade (log real:
                # "abre, mas não mostra nada"). Agora escreve um .bat
                # temporário com o comando pronto e só manda o CMD rodar
                # ESSE arquivo — sem nenhum reescape de string no meio.
                bat_path = os.path.join(BASE, "_ver_log_tmp.bat")
                log_escapado = RUN_LOG.replace("'", "''")   # aspas simples no PowerShell: dobra
                conteudo_bat = (
                    "@echo off\r\n"
                    "chcp 65001 >nul\r\n"
                    "title TofuBot - run.log\r\n"
                    "powershell -NoExit -Command "
                    "\"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                    f"Get-Content -Path '{log_escapado}' -Wait -Tail 80 -Encoding UTF8\"\r\n"
                )
                with open(bat_path, "w", encoding="utf-8") as f:
                    f.write(conteudo_bat)
                # O CMD clássico (conhost.exe) não tem fonte com emoji de
                # verdade — mesmo com a codificação certa (UTF-8), os
                # emojis do log aparecem como "?" (limitação da fonte do
                # console antigo, não é mais bug de encoding). O Windows
                # Terminal (wt.exe — já vem de fábrica no Windows 11 e é
                # instalável de graça pela Microsoft Store no Windows 10)
                # renderiza emoji direito. Tenta ele primeiro; se não
                # achar, cai pro CMD clássico (funciona, só sem emoji).
                wt_path = shutil.which("wt.exe") or shutil.which("wt")
                if wt_path:
                    subprocess.Popen([wt_path, "cmd", "/c", bat_path], cwd=BASE)
                else:
                    subprocess.Popen(["cmd", "/c", bat_path], cwd=BASE,
                                     creationflags=subprocess.CREATE_NEW_CONSOLE)
            except Exception as err:
                messagebox.showerror("Erro ao abrir o log", str(err))
            return

        # BUG REAL 2026-07-16 (usuário no Linux Mint: botão não fazia
        # NADA, nem uma janela piscava): pra xterm/x-terminal-emulator/
        # xfce4-terminal, o código mandava "bash -c \"...\"" como UMA
        # string só de argumento — mas esses terminais (diferente do
        # gnome-terminal/konsole) NÃO reinterpretam essa string como
        # shell, tentam executar um "programa" com esse nome gigante
        # (com espaços e aspas dentro), falham na hora e fecham sem
        # nunca abrir janela nenhuma. Solução igual à do Windows (.bat):
        # escreve um script .sh pronto e só pede pro terminal RODAR o
        # arquivo — funciona igual não importa como cada terminal trata
        # aspas/argumentos.
        try:
            sh_path = os.path.join(BASE, "_ver_log_tmp.sh")
            with open(sh_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n"
                        f"tail -n 200 -f '{RUN_LOG}'\n"
                        "exec bash\n")
            os.chmod(sh_path, 0o755)
        except Exception as err:
            messagebox.showerror("Erro", f"Não consegui preparar o script do log: {err}")
            return

        candidatos = [
            ["gnome-terminal", "--", sh_path],
            ["konsole", "-e", sh_path],
            ["mate-terminal", "-e", sh_path],
            ["xfce4-terminal", "-e", sh_path],
            ["tilix", "-e", sh_path],
            ["terminator", "-e", sh_path],
            ["x-terminal-emulator", "-e", sh_path],
            ["xterm", "-e", sh_path],
        ]
        erros = []
        for cmd in candidatos:
            try:
                proc = subprocess.Popen(cmd, cwd=BASE)
            except (FileNotFoundError, OSError) as err:
                erros.append(f"{cmd[0]}: {err}")
                continue
            # BUG REAL 2026-07-16 (usuário: "clico e nenhuma janela abre, sem
            # erro nenhum"): Popen() só falha (lança exceção) se o PROGRAMA
            # não existir — mas ele pode existir e mesmo assim fechar sozinho
            # na hora (ex: sem display gráfico disponível naquele momento),
            # e Popen() não percebe isso (só lança processos, não espera eles
            # funcionarem). Sem checar se o processo ainda está de pé pouco
            # depois, o código assumia sucesso e PARAVA de tentar os outros
            # terminais da lista — mesmo tendo fechado sem abrir nada visível.
            time.sleep(0.4)
            if proc.poll() is None:   # None = ainda rodando de verdade
                return
            erros.append(f"{cmd[0]}: fechou sozinho logo depois de abrir (código {proc.poll()})")
        messagebox.showwarning(
            "Terminal não encontrado/não abriu",
            "Tentei os terminais mais comuns (gnome-terminal, konsole, mate-terminal, "
            "xfce4-terminal, tilix, terminator, xterm), mas nenhum ficou de pé.\n\n"
            "Você pode ver o log manualmente abrindo um terminal e rodando:\n\n"
            f"tail -f {RUN_LOG}\n\n"
            "Detalhes técnicos:\n" + "\n".join(erros))

    def iniciar(self):
        if not os.path.exists(SETTINGS):
            messagebox.showwarning("Salve primeiro", "Clique em Salvar antes de Iniciar.")
            return
        if bot_rodando():
            messagebox.showinfo("Já rodando", "O TofuBot já está rodando.")
            return
        # limpa um pedido de "parar no fim" ANTIGO (de uma sessão anterior) —
        # senão o bot novo pararia logo após o 1º conteúdo sem você pedir.
        self._limpar_parar_no_fim()
        try:
            if IS_WINDOWS and os.path.exists(INICIAR_CMD):
                # Windows "clássico": Popen guarda o PROCESSO do iniciar.cmd —
                # o Parar mata essa árvore inteira (janela do loop + bot.exe
                # filho), sem tocar nas OUTRAS instâncias (outras pastas).
                self._iniciar_proc = subprocess.Popen(
                    ["cmd", "/c", INICIAR_CMD], cwd=BASE,
                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                cmd = bot_cmd()
                boot_log = open(os.path.join(BASE, "boot_stderr.log"), "w", encoding="utf-8")
                self._iniciar_proc = subprocess.Popen(cmd, cwd=BASE, stdout=boot_log,
                                                      stderr=subprocess.STDOUT,
                                                      creationflags=NO_WINDOW)
            self._log_gui("🚀 Bot iniciado em segundo plano!")
        except Exception as err:
            messagebox.showerror("Erro ao iniciar", f"Erro: {err}")

    def _limpar_parar_no_fim(self):
        try:
            os.remove(PARAR_NO_FIM_FLAG)
        except OSError:
            pass
        self._sync_btn_parar_fim()

    def _sync_btn_parar_fim(self):
        """Deixa o botão refletindo o estado real do pedido (o hunter.py apaga
        o flag quando atende — aí o botão volta ao normal sozinho no _tick)."""
        b = getattr(self, "btn_parar_fim", None)
        if b is None:
            return
        if os.path.exists(PARAR_NO_FIM_FLAG):
            b.config(text="⏸ Vai parar ✔", bg="#8a6d1a", activebackground="#8a6d1a")
        else:
            b.config(text="⏸ Parar no fim", bg=ORANGE, activebackground=ORANGE)

    def parar_no_fim(self):
        """PARADA SUAVE: o bot TERMINA o conteúdo atual (masmorra/caçada/
        cripta) e para, sem começar o próximo. Clicar de novo CANCELA."""
        if os.path.exists(PARAR_NO_FIM_FLAG):
            self._limpar_parar_no_fim()
            messagebox.showinfo("Cancelado", "Parada no fim CANCELADA — o bot segue normal.")
            return
        if not bot_rodando():
            messagebox.showinfo("Bot parado", "O bot não está rodando — nada pra parar. "
                                              "(Use este botão com o bot em execução.)")
            return
        try:
            with open(PARAR_NO_FIM_FLAG, "w") as f:
                f.write("1")
        except OSError as err:
            messagebox.showerror("Erro", f"Não consegui criar o sinal de parada: {err}")
            return
        self._sync_btn_parar_fim()
        messagebox.showinfo("Programado",
                            "O bot vai TERMINAR o conteúdo atual (masmorra/caçada/cripta) "
                            "e parar — sem começar o próximo.\n\nClique de novo pra cancelar.")

    def parar(self):
        """Parada IMEDIATA — SÓ do bot DESTA pasta (multi-instância): mata a
        árvore que ESTE painel abriu e/ou o PID do bot.pid. Nada de matar por
        nome de imagem genérico (mataria bot de OUTRAS pastas ou qualquer
        python.exe da máquina)."""
        self._limpar_parar_no_fim()   # um pedido de "parar no fim" pendente perde o sentido
        proc = getattr(self, "_iniciar_proc", None)
        if IS_WINDOWS:
            if proc is not None and proc.poll() is None:
                try:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   creationflags=NO_WINDOW, capture_output=True)
                except Exception:
                    pass
            pid = _pid_do_bot_vivo()
            if pid:
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   creationflags=NO_WINDOW, capture_output=True)
                except Exception:
                    pass
        else:
            pid = _pid_do_bot_vivo()
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        try:
            os.remove(BOT_PID_FILE)
        except OSError:
            pass
        self._log_gui("🛑 Comando de parada enviado.")


    def _atualizar_progresso_oasis(self):
        """Lê status.json (chave 'missao_oasis' de cada conta, gravada pelo
        hunter.py a cada vitória — ver write_status_missao_oasis) e desenha
        uma linha por conta com o progresso AO VIVO da busca do Sunred
        (sem precisar esperar a missão terminar pra ver algo)."""
        dados = {}
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, encoding="utf-8") as f:
                    dados = json.load(f)
            except Exception:
                dados = {}
        agora = time.time()
        vistos = set()
        for nome, info in dados.items():
            mo = (info or {}).get("missao_oasis")
            if not mo or not mo.get("item"):
                continue
            if (agora - mo.get("ts", 0)) >= STATUS_MAX_IDADE * 20:
                continue   # muito velho (execução antiga) — não mostra
            vistos.add(nome)
            w = self._oasis_progress_widgets.get(nome)
            if w is None:
                linha = tk.Frame(self.oasis_progress_frame, bg=REL_CARD,
                                 highlightbackground="#4a4f6e", highlightthickness=1)
                linha.pack(fill="x", padx=6, pady=5, anchor="w")
                cabecalho = tk.Frame(linha, bg=REL_CARD)
                cabecalho.pack(fill="x", padx=10, pady=(8, 2))
                tk.Label(cabecalho, text=f"🧭 {nome}", bg=REL_CARD, fg="#ffd166",
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                item_lbl = tk.Label(linha, text="", bg=REL_CARD, fg=REL_TXT,
                                    font=("Segoe UI", 9), anchor="w")
                item_lbl.pack(fill="x", padx=10, pady=(0, 6))
                kills_row = tk.Frame(linha, bg=REL_CARD)
                kills_row.pack(fill="x", padx=10, pady=(0, 4))
                tk.Label(kills_row, text="🐾 Monstros", bg=REL_CARD, fg="#ff9f5b",
                         font=("Segoe UI", 8, "bold"), width=12, anchor="w").pack(side="left")
                kills_bar = ttk.Progressbar(kills_row, length=220, maximum=100,
                                            style="Monstros.Horizontal.TProgressbar")
                kills_bar.pack(side="left", padx=(4, 10))
                kills_txt = tk.Label(kills_row, text="", bg=REL_CARD, fg=REL_TXT,
                                     font=("Segoe UI", 9, "bold"), anchor="w")
                kills_txt.pack(side="left")
                itens_row = tk.Frame(linha, bg=REL_CARD)
                itens_row.pack(fill="x", padx=10, pady=(0, 10))
                tk.Label(itens_row, text="📦 Itens", bg=REL_CARD, fg="#4ecbc4",
                         font=("Segoe UI", 8, "bold"), width=12, anchor="w").pack(side="left")
                itens_bar = ttk.Progressbar(itens_row, length=220, maximum=100,
                                            style="Itens.Horizontal.TProgressbar")
                itens_bar.pack(side="left", padx=(4, 10))
                itens_txt = tk.Label(itens_row, text="", bg=REL_CARD, fg=REL_TXT,
                                     font=("Segoe UI", 9, "bold"), anchor="w")
                itens_txt.pack(side="left")
                w = {"linha": linha, "item": item_lbl, "kills_bar": kills_bar,
                     "kills_txt": kills_txt, "itens_bar": itens_bar, "itens_txt": itens_txt}
                self._oasis_progress_widgets[nome] = w
            kills, kills_meta = mo.get("kills", 0), mo.get("kills_meta", 200) or 1
            itens, itens_meta = mo.get("itens", 0), mo.get("itens_meta", 50) or 1
            pct_kills = min(100, 100 * kills / kills_meta)
            pct_itens = min(100, 100 * itens / itens_meta)
            w["item"].config(text=f"🎯 Coletar {itens_meta}x {mo.get('item', '')}")
            w["kills_bar"].config(value=pct_kills)
            w["kills_txt"].config(text=f"{kills}/{kills_meta}  ({pct_kills:.0f}%)")
            w["itens_bar"].config(value=pct_itens)
            w["itens_txt"].config(text=f"{itens}/{itens_meta}  ({pct_itens:.0f}%)")
        # remove linhas de contas que sumiram (terminaram/pararam)
        for nome in list(self._oasis_progress_widgets.keys()):
            if nome not in vistos:
                self._oasis_progress_widgets[nome]["linha"].destroy()
                del self._oasis_progress_widgets[nome]
        if not vistos:
            if not getattr(self, "_oasis_progress_vazio_lbl", None):
                self._oasis_progress_vazio_lbl = tk.Label(
                    self.oasis_progress_frame,
                    text="(nenhuma conta com Missão Oásis em andamento no momento)",
                    bg=REL_BG, fg=REL_MUTED, font=("Segoe UI", 9))
                self._oasis_progress_vazio_lbl.pack(anchor="w", padx=8, pady=8)
        elif getattr(self, "_oasis_progress_vazio_lbl", None):
            self._oasis_progress_vazio_lbl.destroy()
            self._oasis_progress_vazio_lbl = None

    # ---------------- Status ao vivo (HP, estilo do jogo) ----------------
    def _build_status_ao_vivo(self, parent):
        """Painel com o HP de cada conta AO VIVO, no estilo da barra do
        próprio jogo (blocos vermelhos/vazios + nome + número), em vez da
        barra sólida que já existe dentro de cada cartão de conta."""
        box = ttk.LabelFrame(parent, text=" ❤ Status ao vivo (HP) ")
        box.pack(side="left", padx=12, fill="both", expand=True)
        topo = tk.Frame(box, bg=BG)
        topo.pack(fill="x", padx=10, pady=(6, 0))
        tk.Button(topo, text="🔍 Ampliar", command=self._abrir_status_vivo_popup,
                  bg="#3a3d5c", fg="white", relief="flat", font=("Segoe UI", 8),
                  padx=8, pady=2).pack(side="right")
        self.status_vivo_frame = tk.Frame(box, bg=BG)
        # Barra do MONSTRO/BOSS — ÚNICA pro grupo todo (não repete uma vez
        # por conta: quando todo mundo está na mesma sala/masmorra, é o MESMO
        # boss, então mostrar 5x a mesma barra só empilhava informação
        # repetida à toa). Fica em cima da lista de contas, escondida quando
        # ninguém está enfrentando nada no momento.
        self.boss_geral_frame = tk.Frame(box, bg=BG)
        tk.Label(self.boss_geral_frame, text="👹", bg=BG, font=("Segoe UI", 12)).pack(side="left")
        self.boss_geral_canvas = tk.Canvas(self.boss_geral_frame, width=220, height=20,
                                           bg=BG, highlightthickness=0)
        self.boss_geral_canvas.pack(side="left", padx=(4, 8))
        self.boss_geral_lbl = tk.Label(self.boss_geral_frame, text="", bg=BG,
                                       fg="#ff9800", font=("Segoe UI", 10))
        self.boss_geral_lbl.pack(side="left")
        # não empacota boss_geral_frame ainda — só quando houver boss de
        # verdade (ver _atualizar_status_vivo)
        self.status_vivo_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.status_vivo_rows = {}
        self._status_vivo_vazio_lbl = None
        self.status_vivo_popup = None
        self.status_vivo_popup_frame = None
        self.status_vivo_popup_rows = {}
        self._status_vivo_popup_vazio_lbl = None
        self._ultimo_status_dados = {}
        # Estimativa de tempo restante pro alvo configurado (ex: "quero fazer
        # 30 masmorras") — calculada sozinha a partir da média de duração das
        # últimas 10 execuções daquele conteúdo (ver estimativa.json, escrito
        # pelo hunter.py a cada conclusão). Deixa BEM explícito o que cada
        # número significa, pra não confundir com outra coisa do painel.
        # Este label é atualizado no tick PRINCIPAL do painel (roda sempre,
        # não só quando o Status ao vivo está ligado) — por isso a criação
        # foi separada em _criar_estimativa_lbl(), chamada SEMPRE (ver
        # _build_config()), senão o painel quebra (AttributeError) no 1º
        # tick quando STATUS_AO_VIVO_ATIVO = False.
        self._criar_estimativa_lbl(box, mostrar=True)
        self._ultimo_status_rodando = False
        self._garantir_status_vivo_rows()

    def _criar_estimativa_lbl(self, parent, mostrar: bool) -> None:
        """Cria self.estimativa_lbl (sempre precisa existir, ver comentário
        acima) — se mostrar=False, cria mas NÃO empacota (fica invisível,
        sem custo real), pro tick principal ter algo pra atualizar sem quebrar."""
        self.estimativa_lbl = tk.Label(parent, text="", bg=BG, fg="#8ab4ff",
                                       font=("Segoe UI", 9), justify="left",
                                       wraplength=520, anchor="w")
        if mostrar:
            self.estimativa_lbl.pack(fill="x", padx=10, pady=(0, 8))

    def _abrir_status_vivo_popup(self):
        """Abre (ou traz pra frente, se já estiver aberta) uma janela separada
        e maior com o mesmo painel de HP ao vivo."""
        if self.status_vivo_popup is not None and self.status_vivo_popup.winfo_exists():
            self.status_vivo_popup.lift()
            self.status_vivo_popup.focus_force()
            return
        pop = tk.Toplevel(self.root)
        pop.title("TofuBot — Status ao vivo (HP)")
        pop.configure(bg=BG)
        pop.geometry("640x480")

        def _ao_fechar():
            self.status_vivo_popup = None
            self.status_vivo_popup_frame = None
            self.status_vivo_popup_rows = {}
            self._status_vivo_popup_vazio_lbl = None
            pop.destroy()
        pop.protocol("WM_DELETE_WINDOW", _ao_fechar)

        tk.Label(pop, text="❤ Status ao vivo (HP)", bg=BG, fg=FG,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        self.boss_geral_popup_frame = tk.Frame(pop, bg=BG)
        tk.Label(self.boss_geral_popup_frame, text="👹", bg=BG, font=("Segoe UI", 16)).pack(side="left")
        self.boss_geral_popup_canvas = tk.Canvas(self.boss_geral_popup_frame, width=360, height=28,
                                                 bg=BG, highlightthickness=0)
        self.boss_geral_popup_canvas.pack(side="left", padx=(6, 10))
        self.boss_geral_popup_lbl = tk.Label(self.boss_geral_popup_frame, text="", bg=BG,
                                             fg="#ff9800", font=("Segoe UI", 13))
        self.boss_geral_popup_lbl.pack(side="left")
        self.status_vivo_popup_frame = tk.Frame(pop, bg=BG)
        self.status_vivo_popup_frame.pack(fill="both", expand=True, padx=16, pady=8)
        self.status_vivo_popup = pop
        self.status_vivo_popup_rows = {}
        self._status_vivo_popup_vazio_lbl = None
        self._garantir_status_vivo_rows()
        self._atualizar_status_vivo(self._ultimo_status_dados, time.time(), self._ultimo_status_rodando)

    def _desenhar_barra_jogo(self, canvas, hp, hp_max, segmentos=10, cor_cheio="#e53935"):
        """Desenha a barra em BLOCOS (cor_cheio = vida restante, cinza-escuro
        = vida perdida), igual ao visual usado nas telas de combate do jogo.
        'cor_cheio' é vermelho por padrão (personagem); usar outra cor (ex:
        laranja) pra barra do monstro/boss, pra não confundir uma com a
        outra visualmente."""
        canvas.delete("all")
        w = int(canvas["width"]); h = int(canvas["height"])
        gap = 3
        seg_w = (w - gap * (segmentos - 1)) / segmentos
        if not hp_max:
            for i in range(segmentos):
                x0 = i * (seg_w + gap)
                canvas.create_rectangle(x0, 0, x0 + seg_w, h, fill="#3a3d5c", outline="")
            return
        ratio = max(0.0, min(1.0, (hp or 0) / hp_max))
        cheios = round(ratio * segmentos)
        for i in range(segmentos):
            x0 = i * (seg_w + gap)
            cor = cor_cheio if i < cheios else "#3a3d5c"
            canvas.create_rectangle(x0, 0, x0 + seg_w, h, fill=cor, outline="")

    def _construir_linhas_status(self, frame, rows_dict, nomes_atuais, vazio_attr, grande=False):
        """Sincroniza as linhas de um painel de status (cria/remove conforme
        as contas configuradas) — usado tanto no painel normal quanto na
        janela ampliada (popup), só muda o tamanho da fonte/barra."""
        for nome in list(rows_dict.keys()):
            if nome not in nomes_atuais:
                rows_dict[nome][0].destroy()
                del rows_dict[nome]

        vazio_lbl = getattr(self, vazio_attr)
        if not nomes_atuais:
            if vazio_lbl is None:
                vazio_lbl = tk.Label(frame, text="(sem contas configuradas)",
                                      bg=BG, fg=MUTED, font=("Segoe UI", 8))
                vazio_lbl.pack(anchor="w")
                setattr(self, vazio_attr, vazio_lbl)
            return
        if vazio_lbl is not None:
            vazio_lbl.destroy()
            setattr(self, vazio_attr, None)

        f_coracao = ("Segoe UI", 16 if grande else 12)
        f_nome = ("Segoe UI", 13 if grande else 10, "bold")
        f_hp = ("Segoe UI", 13 if grande else 10)
        largura_nome = 14 if grande else 12
        largura_bar, altura_bar = (360, 28) if grande else (220, 20)

        for nome in nomes_atuais:
            if nome in rows_dict:
                continue
            linha = tk.Frame(frame, bg=BG)
            linha.pack(fill="x", anchor="w", pady=5 if grande else 3)
            linha_topo = tk.Frame(linha, bg=BG)
            linha_topo.pack(fill="x", anchor="w")
            tk.Label(linha_topo, text="❤", bg=BG, fg="#e53935", font=f_coracao).pack(side="left")
            lbl_nome = tk.Label(linha_topo, text=nome, bg=BG, fg=FG, font=f_nome,
                                 width=largura_nome, anchor="w")
            lbl_nome.pack(side="left", padx=(4, 8))
            canvas = tk.Canvas(linha_topo, width=largura_bar, height=altura_bar, bg=BG,
                                highlightthickness=0)
            canvas.pack(side="left")
            self._desenhar_barra_jogo(canvas, None, None)
            lbl_hp = tk.Label(linha_topo, text="—", bg=BG, fg=MUTED, font=f_hp)
            lbl_hp.pack(side="left", padx=8)
            lbl_progresso = tk.Label(linha_topo, text="", bg=BG, fg="#8ab4ff", font=f_hp)
            lbl_progresso.pack(side="left", padx=(4, 0))
            lbl_monstro = tk.Label(linha_topo, text="", bg=BG, fg="#ff8a65", font=f_hp)
            lbl_monstro.pack(side="left", padx=(8, 0))
            lbl_tempo = tk.Label(linha_topo, text="", bg=BG, fg=MUTED, font=f_hp)
            lbl_tempo.pack(side="left", padx=(8, 0))
            # Linha 2: nível / XP faltando pro próximo / estimativa de tempo
            # (pedido do usuário 2026-07-15) — atualizado periodicamente
            # (ver atualizar_perfil_e_estimativa no hunter.py), não a cada
            # rodada, então pode ficar "parado" por um tempo entre atualizações.
            lbl_nivel = tk.Label(linha, text="", bg=BG, fg="#b39ddb",
                                 font=("Segoe UI", 9 if grande else 7))
            lbl_nivel.pack(anchor="w", padx=(28 if grande else 22, 0))
            rows_dict[nome] = (linha, canvas, lbl_hp, lbl_nome, lbl_progresso, lbl_monstro,
                               lbl_tempo, lbl_nivel)

    def _garantir_status_vivo_rows(self):
        """Sincroniza as linhas do painel principal (e da janela ampliada, se
        estiver aberta) com as contas configuradas (mesma lista dos cartões de
        Masmorra + Caçada em Dupla)."""
        todos_cartoes = getattr(self, "cartoes", []) + getattr(self, "caca_cartoes", [])
        nomes_atuais = []
        for card in todos_cartoes:
            if not card.fone.get().strip():
                continue
            nomes_atuais.append(card.nome.get().strip())

        self._construir_linhas_status(self.status_vivo_frame, self.status_vivo_rows,
                                       nomes_atuais, "_status_vivo_vazio_lbl", grande=False)
        if self.status_vivo_popup is not None and self.status_vivo_popup.winfo_exists():
            self._construir_linhas_status(self.status_vivo_popup_frame, self.status_vivo_popup_rows,
                                           nomes_atuais, "_status_vivo_popup_vazio_lbl", grande=True)

    def _atualizar_status_vivo(self, dados, agora, rodando):
        # Seção nem foi montada (config.STATUS_AO_VIVO_ATIVO = False) — não
        # há self.status_vivo_frame/rows pra atualizar, então não faz nada.
        if not config.STATUS_AO_VIVO_ATIVO:
            return
        self._ultimo_status_dados = dados
        self._ultimo_status_rodando = rodando
        self._garantir_status_vivo_rows()
        conjuntos = [self.status_vivo_rows]
        if self.status_vivo_popup is not None and self.status_vivo_popup.winfo_exists():
            conjuntos.append(self.status_vivo_popup_rows)
        # Boss/monstro: UM valor só, compartilhado pro grupo inteiro — pega o
        # primeiro encontrado entre as contas ativas (normalmente é o MESMO
        # bicho pra todo mundo na mesma sala; se houver 2 duplas lutando com
        # bosses diferentes ao mesmo tempo, mostra só a 1ª encontrada — uma
        # simplificação deliberada pra não empilhar barra repetida à toa).
        hp_boss = hp_boss_max = None
        if rodando:
            for info in dados.values():
                if info and (agora - info.get("ts", 0)) < STATUS_MAX_IDADE:
                    hm, hmm = info.get("hp_monstro"), info.get("hp_monstro_max")
                    if hm is not None and hmm:
                        hp_boss, hp_boss_max = hm, hmm
                        break
        tem_boss = hp_boss is not None and hp_boss_max
        for frame_boss, canvas_boss, lbl_boss, status_frame in (
                (self.boss_geral_frame, self.boss_geral_canvas, self.boss_geral_lbl,
                 self.status_vivo_frame),
                (getattr(self, "boss_geral_popup_frame", None),
                 getattr(self, "boss_geral_popup_canvas", None),
                 getattr(self, "boss_geral_popup_lbl", None),
                 self.status_vivo_popup_frame if self.status_vivo_popup is not None
                 and self.status_vivo_popup.winfo_exists() else None)):
            if frame_boss is None or status_frame is None:
                continue
            if tem_boss:
                self._desenhar_barra_jogo(canvas_boss, hp_boss, hp_boss_max, cor_cheio="#ff9800")
                ratio_boss = max(0.0, min(1.0, hp_boss / hp_boss_max))
                lbl_boss.config(text=f"{hp_boss}/{hp_boss_max} ({ratio_boss:.0%})")
                if not frame_boss.winfo_ismapped():
                    frame_boss.pack(fill="x", padx=10, pady=(0, 6), before=status_frame)
            elif frame_boss.winfo_ismapped():
                frame_boss.pack_forget()
        for rows_dict in conjuntos:
            for nome, (_, canvas, lbl_hp, _, lbl_progresso, lbl_monstro, lbl_tempo,
                       lbl_nivel) in rows_dict.items():
                hp = hp_max = None
                progresso = ""
                tempo_txt = ""
                nivel_txt = ""
                if rodando:
                    info = dados.get(nome)
                    if info and (agora - info.get("ts", 0)) < STATUS_MAX_IDADE:
                        hp, hp_max = info.get("hp"), info.get("hp_max")
                        progresso = info.get("progresso") or ""
                        inicio_ts = info.get("inicio_ts")
                        if inicio_ts:
                            tempo_txt = f"⏱ {_formatar_duracao_painel(agora - inicio_ts)}"
                        nivel = info.get("nivel")
                        if nivel is not None:
                            xp_faltam = info.get("xp_faltam")
                            xp_faltam_fmt = f"{xp_faltam:,}".replace(",", ".") \
                                if xp_faltam is not None else "?"
                            eta_seg = info.get("eta_proximo_nivel_seg")
                            eta_txt = (f" · ~{_formatar_duracao_painel(eta_seg)} pro próximo nível"
                                       if eta_seg else "")
                            nivel_txt = f"🎓 Lv {nivel} · faltam {xp_faltam_fmt} XP{eta_txt}"
                self._desenhar_barra_jogo(canvas, hp, hp_max)
                if hp_max:
                    ratio = max(0.0, min(1.0, (hp or 0) / hp_max))
                    lbl_hp.config(text=f"{hp}/{hp_max} ({ratio:.0%})", fg=FG)
                else:
                    lbl_hp.config(text="—", fg=MUTED)
                lbl_progresso.config(text=progresso)
                lbl_tempo.config(text=tempo_txt)
                lbl_nivel.config(text=nivel_txt)


    def _atualizar_hp(self, rodando):
        todos_cartoes = self.cartoes + self.caca_cartoes
        if not rodando:
            for card in todos_cartoes:
                card.set_hp(None, None)
            self._atualizar_status_vivo({}, time.time(), rodando)
            self.estimativa_lbl.config(text="")
            return
        dados = {}
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, encoding="utf-8") as f:
                    dados = json.load(f)
            except Exception:
                dados = {}
        agora = time.time()
        for card in todos_cartoes:
            info = dados.get(card.nome.get().strip())
            if info and (agora - info.get("ts", 0)) < STATUS_MAX_IDADE:
                card.set_hp(info.get("hp"), info.get("hp_max"))
            else:
                card.set_hp(None, None)
        self._atualizar_status_vivo(dados, agora, rodando)
        self._atualizar_estimativa(agora)

    def _atualizar_estimativa(self, agora):
        """Mostra 'faltam ~X pras Y execuções restantes de <conteúdo>' —
        calculado sozinho a partir da média das últimas 10 execuções desse
        conteúdo específico (ver estimativa.json, escrito pelo hunter.py a
        cada conclusão). Conta pra baixo entre uma conclusão e outra (usando
        o tempo real decorrido desde a última vez que o arquivo foi
        atualizado), e reseta pra um valor novo assim que a próxima execução
        terminar de verdade."""
        if not os.path.exists(ESTIMATIVA_FILE):
            self.estimativa_lbl.config(text="")
            return
        try:
            with open(ESTIMATIVA_FILE, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            self.estimativa_lbl.config(text="")
            return
        if (agora - info.get("ts", 0)) > 3600:
            # info velha (mais de 1h) — provavelmente de uma sessão anterior,
            # não vale mais a pena mostrar como se fosse "ao vivo agora".
            self.estimativa_lbl.config(text="")
            return
        alvo = int(info.get("alvo") or 0)
        feitas = int(info.get("feitas") or 0)
        media_seg = info.get("media_segundos")
        modo_nomes = {"masmorra": "Masmorra", "caca_dupla": "Caçada em Dupla",
                      "cripta": "Cripta", "templo_oasis": "Templo do Oásis"}
        modo_txt = modo_nomes.get(info.get("modo"), info.get("modo", "conteúdo"))
        if not media_seg:
            self.estimativa_lbl.config(text="")
            return
        media_txt = _formatar_duracao_painel(media_seg)
        if alvo <= 0:
            # "sem limite" configurado — não dá pra estimar um fim, mas
            # ainda mostra a média por execução (informação útil sozinha).
            self.estimativa_lbl.config(
                text=f"⏱ {modo_txt}: {feitas} concluída(s) desde que iniciou "
                     f"· média de {media_txt} por execução (sem limite configurado).")
            return
        restantes = max(0, alvo - feitas)
        se_ja_bateu = restantes == 0
        if se_ja_bateu:
            self.estimativa_lbl.config(
                text=f"⏱ {modo_txt}: {feitas}/{alvo} — meta atingida!")
            return
        tempo_total_restante = restantes * media_seg
        decorrido_desde_ultima = max(0.0, agora - info.get("ts", agora))
        tempo_exibido = max(0.0, tempo_total_restante - decorrido_desde_ultima)
        self.estimativa_lbl.config(
            text=f"⏱ Estimativa ({modo_txt}): faltam ~{_formatar_duracao_painel(tempo_exibido)} "
                 f"pras {restantes} execução(ões) restante(s) até a meta de {alvo} "
                 f"({feitas}/{alvo} já concluídas · média de {media_txt}/execução, "
                 f"últimas 10) — número aproximado, pode variar com a sorte/lag.")

    def _tick(self):
        rodando = bot_rodando()
        if rodando:
            self.status.config(text="●  Rodando", fg="#69f0ae")
            try:
                if os.path.exists(RUN_LOG):
                    with open(RUN_LOG, encoding="utf-8", errors="ignore") as f:
                        txt = "".join(f.readlines()[-40:])
                    self.logbox.config(state="normal")
                    self.logbox.delete("1.0", "end")
                    self.logbox.insert("end", txt)
                    self.logbox.see("end")
                    self.logbox.config(state="disabled")
            except Exception:
                pass
        else:
            self.status.config(text="●  Parado", fg="#ff8a80")
        self._atualizar_hp(rodando)
        self._atualizar_progresso_oasis()
        self._sync_btn_parar_fim()
        try:
            total = 0
            if os.path.exists(RELATORIO):
                with open(RELATORIO, encoding="utf-8") as f:
                    _d = json.load(f)
                    total = (_d.get("total", 0) + _d.get("cacadas_total", 0)
                            + _d.get("templo_oasis_total", 0)
                            + _d.get("criptas_total", 0) + _d.get("caca_solo_total", 0))
            if total != getattr(self, "_last_rel", -1):
                self._last_rel = total
                self.atualizar_relatorio()
        except Exception:
            pass
        self.root.after(1500, self._tick)

if __name__ == "__main__":
    root = tk.Tk()
    Painel(root)
    root.mainloop()
