#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Organizador e Editor de listas M3U / M3U8
=========================================

Funcionalidades:
  * Selecionar uma lista M3U/M3U8 (arquivo ou URL)
  * Carregar todos os canais em uma Treeview com filtro/busca
  * Visualizar o canal selecionado em um campo de video (player integrado)
  * Adicionar canais ao banco de dados SQLite
  * Exportar o banco como uma nova lista M3U/M3U8
  * Janela separada para ver / editar os canais salvos no banco

Requisitos:
  Python 3.9+
  - tkinter (ja vem com o Python)
  - sqlite3 (ja vem com o Python)
  - pillow (PIL)  -> OPCIONAL, usado para thumbnail e capa do logo
  - python-vlc  -> OPCIONAL, para tocar o stream de video

O player usa VLC (python-vlc) quando disponivel. Sem o VLC instalado,
o app continua funcionando: abre a URL no navegador/sistema e mostra a capa
do canal em vez do video ao vivo.
"""

import os
import re
import sys
import json
import sqlite3
import threading
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    PIL_AVAILABLE = False

try:
    import vlc
    VLC_AVAILABLE = True
except Exception:  # pragma: no cover
    VLC_AVAILABLE = False


APP_TITLE = "Organizador M3U"
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canais.db")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) M3UOrganizer/1.0"


# --------------------------------------------------------------------------- #
#  Utilidades M3U
# --------------------------------------------------------------------------- #
def _decode(data: bytes) -> str:
    """Decodifica bytes tentando varios encodings comuns de listas IPTV."""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "iso-8859-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _clean_attrs(raw: str) -> dict:
    """Converte tvg-id="x" tvg-name="y" group-title="z" em dict."""
    attrs = {}
    for m in re.finditer(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"', raw or ""):
        attrs[m.group(1).lower()] = m.group(2).strip()
    return attrs


def parse_m3u(text: str):
    """
    Le o conteudo de uma lista M3U/M3U8.
    Retorna (canais, grupos, cabecalho_extra)
      canais: lista de dicts com os campos ja normalizados.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    canais = []
    grupos = []
    seen_groups = set()
    pending = {}       # atributos da linha #EXTINF lidos
    header_extra = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTM3U"):
            attrs = _clean_attrs(line)
            for k, v in attrs.items():
                if k not in ("x-tvg-url", "url-tvg"):
                    header_extra.append(f"{k}={v}")
            continue

        if line.startswith("#EXTGRP:") or line.startswith("#EXTGRP="):
            g = line.split(":", 1)[-1].strip()
            if g and g not in seen_groups:
                seen_groups.add(g)
                grupos.append(g)
            continue

        if line.startswith("#EXTINF"):
            body = line.split(":", 1)[-1] if ":" in line else ""
            dur, _, display = body.partition(",")
            attrs = _clean_attrs(body)
            nome = display.strip()
            try:
                dur_s = int(float(dur.strip() or -1))
            except ValueError:
                dur_s = -1

            grupo = (attrs.get("group-title") or "").strip()
            if grupo and grupo not in seen_groups:
                seen_groups.add(grupo)
                grupos.append(grupo)

            pending = {
                "nome": nome or attrs.get("tvg-name") or "Sem nome",
                "logo": (attrs.get("tvg-logo")
                         or attrs.get("logo")
                         or attrs.get("tvg-logo-small") or "").strip(),
                "grupo": grupo or "Sem grupo",
                "tvg_id": (attrs.get("tvg-id") or attrs.get("tvg-chno-id")
                           or attrs.get("tvg-name") or "").strip(),
                "duracao": dur_s,
                "attrs": attrs,
            }
            continue

        if line.startswith("#"):
            continue

        # Linha sem # -> e a URL do canal
        url = line
        if pending:
            canais.append({
                "nome": pending["nome"],
                "logo": pending["logo"],
                "grupo": pending["grupo"],
                "tvg_id": pending["tvg_id"],
                "url": url,
                "duracao": pending["duracao"],
                "atributos": pending["attrs"],
            })
        pending = {}

    return canais, grupos, header_extra


def build_m3u(canais, extra_header=None) -> str:
    """Monta o texto de uma lista M3U a partir de uma lista de canais."""
    out = ["#EXTM3U"]
    for h in (extra_header or []):
        out.append(f"# {h}")
    out.append("")
    for c in canais:
        nome = c.get("nome") or "Sem nome"
        logo = c.get("logo") or ""
        grupo = c.get("grupo") or "Sem grupo"
        tvg_id = c.get("tvg_id") or nome
        out.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{nome}" '
            f'tvg-logo="{logo}" group-title="{grupo}",{nome}'
        )
        out.append(c.get("url") or "")
        out.append("")
    return "\n".join(out)


def read_list_source(path_or_url: str):
    """Le a lista de um arquivo local ou de uma URL (http/https)."""
    if re.match(r"^https?://", path_or_url, re.IGNORECASE):
        import urllib.request

        req = urllib.request.Request(path_or_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _decode(resp.read())
    with open(path_or_url, "rb") as f:
        return _decode(f.read())


# --------------------------------------------------------------------------- #
#  Banco de dados SQLite
# --------------------------------------------------------------------------- #
class Database:
    def __init__(self, path: str = DB_FILE):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    # -- esquema ---------------------------------------------------------- #
    def _create_schema(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS canais (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nome      TEXT NOT NULL,
                logo      TEXT DEFAULT '',
                grupo     TEXT DEFAULT '',
                tvg_id    TEXT DEFAULT '',
                url       TEXT NOT NULL,
                duracao   INTEGER DEFAULT -1,
                atributos TEXT DEFAULT '',
                favorito  INTEGER DEFAULT 0,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listas (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nome      TEXT NOT NULL,
                origem    TEXT DEFAULT '',
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lista_canais (
                lista_id INTEGER,
                canal_id INTEGER,
                ordem    INTEGER,
                PRIMARY KEY (lista_id, canal_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_canais_nome ON canais(nome)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_canais_url  ON canais(url)")
        self.conn.commit()

    # -- canais ------------------------------------------------------------ #
    def url_existe(self, url: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM canais WHERE url = ? LIMIT 1", (url,))
        return cur.fetchone() is not None

    def adicionar(self, canal: dict) -> tuple:
        """Insere um canal. Retorna (sucesso, mensagem)."""
        if not canal.get("url"):
            return False, "O canal nao tem URL."
        if self.url_existe(canal["url"]):
            return False, "Canal ja salvo no banco (URL duplicada)."

        cur = self.conn.execute(
            """INSERT INTO canais (nome, logo, grupo, tvg_id, url, duracao, atributos)
               VALUES (?,?,?,?,?,?,?)""",
            (
                canal.get("nome") or "Sem nome",
                canal.get("logo") or "",
                canal.get("grupo") or "Sem grupo",
                canal.get("tvg_id") or "",
                canal["url"],
                int(canal.get("duracao", -1)),
                json.dumps(canal.get("atributos") or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return True, f"'{canal.get('nome')}' salvo (id={cur.lastrowid})."

    def adicionar_muitos(self, canais: list):
        ok, dupl, erros = 0, 0, []
        for c in canais:
            r, msg = self.adicionar(c)
            if r:
                ok += 1
            elif "duplicada" in msg:
                dupl += 1
            else:
                erros.append(msg)
        return ok, dupl, erros

    def listar(self, busca: str = "", grupo: str = "", favoritos_only=False) -> list:
        sql = "SELECT * FROM canais WHERE 1=1"
        params = []
        if busca.strip():
            sql += " AND (nome LIKE ? OR url LIKE ? OR grupo LIKE ?)"
            t = f"%{busca.strip()}%"
            params += [t, t, t]
        if grupo.strip() and grupo != "Todos":
            sql += " AND grupo = ?"
            params.append(grupo.strip())
        if favoritos_only:
            sql += " AND favorito = 1"
        sql += " ORDER BY nome COLLATE NOCASE"
        return list(self.conn.execute(sql, params))

    def grupos(self) -> list:
        rows = self.conn.execute(
            "SELECT DISTINCT grupo FROM canais ORDER BY grupo COLLATE NOCASE"
        )
        return [r["grupo"] for r in rows]

    def atualizar(self, canal_id: int, campos: dict):
        permitidos = {"nome", "logo", "grupo", "tvg_id", "url", "duracao", "favorito"}
        campos = {k: v for k, v in campos.items() if k in permitidos}
        if not campos:
            return
        sets = ", ".join(f"{k} = ?" for k in campos)
        self.conn.execute(
            f"UPDATE canais SET {sets} WHERE id = ?", list(campos.values()) + [canal_id]
        )
        self.conn.commit()

    def alternar_favorito(self, canal_id: int):
        self.conn.execute(
            "UPDATE canais SET favorito = CASE favorito WHEN 1 THEN 0 ELSE 1 END "
            "WHERE id = ?",
            (canal_id,),
        )
        self.conn.commit()

    def remover(self, canal_id: int):
        self.conn.execute("DELETE FROM canais WHERE id = ?", (canal_id,))
        self.conn.commit()

    def remover_muitos(self, ids: list):
        if not ids:
            return 0
        self.conn.executemany("DELETE FROM canais WHERE id = ?", [(i,) for i in ids])
        self.conn.commit()
        return len(ids)

    def limpar(self):
        self.conn.execute("DELETE FROM canais")
        self.conn.commit()

    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM canais").fetchone()[0]

    def fechar(self):
        self.conn.commit()
        self.conn.close()


# --------------------------------------------------------------------------- #
#  Campo de video (player)
# --------------------------------------------------------------------------- #
class VideoPanel(ttk.Frame):
    """
    Area de video. Usa VLC se python-vlc estiver instalado.
    Sem VLC, mostra a imagem do logo do canal e um botao para abrir o stream
    no navegador/sistema.
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.player = None
        self.canal_atual = None
        self._img_ref = None
        self.vlc_frame = None

        self.lbl_status = ttk.Label(self, text="Nenhum canal selecionado",
                                   anchor="center", foreground="#555")
        self.lbl_status.pack(fill="x", pady=(4, 0))

        self.lbl_video = ttk.Label(self, anchor="center")
        self.lbl_video.pack(fill="both", expand=True, padx=4, pady=4)

        self.barra = ttk.Frame(self)
        self.barra.pack(fill="x", padx=4, pady=(0, 4))

        self.btn_abrir = ttk.Button(self.barra, text="Abrir no navegador",
                                    command=self._abrir_externo, state="disabled")
        self.btn_abrir.pack(side="left")

        self.btn_parar = ttk.Button(self.barra, text="Parar video",
                                    command=self.parar, state="disabled")
        self.btn_parar.pack(side="left", padx=6)

        self.lbl_info = ttk.Label(self.barra, text="", foreground="#666")
        self.lbl_info.pack(side="left", padx=8)

        if not VLC_AVAILABLE:
            self.lbl_status.config(
                text="VLC nao encontrado -> modo visualizacao por imagem.\n"
                     "Para tocar video: pip install python-vlc (e instale o VLC)")

    # -- API --------------------------------------------------------------- #
    def tocar(self, canal: dict):
        self.parar()
        self.canal_atual = canal
        url = canal.get("url", "")
        self.btn_abrir.config(state="normal")
        self.btn_parar.config(state="normal")
        self.lbl_info.config(text=f"URL: {url[:70]}")
        self.lbl_video.config(image="", text="Carregando...")

        if VLC_AVAILABLE:
            self._tocar_vlc(url)
        else:
            self._restaurar_layout()
            self._mostrar_logo(canal.get("logo", ""))

    def parar(self):
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            self.player = None
        if getattr(self, "vlc_frame", None) is not None:
            self.vlc_frame.destroy()
            self.vlc_frame = None
        self._restaurar_layout()
        self.lbl_video.config(image="", text="")
        self.lbl_status.config(text="Video parado")
        self.btn_parar.config(state="disabled")

    def _restaurar_layout(self):
        """Garante a hierarquia padrao: status, video, barra."""
        self.lbl_video.pack_forget()
        if getattr(self, "vlc_frame", None) is not None:
            self.vlc_frame.pack_forget()
        self.lbl_status.pack(fill="x", pady=(4, 0))
        self.lbl_video.pack(fill="both", expand=True, padx=4, pady=4)
        self.barra.pack(fill="x", padx=4, pady=(0, 4))

    def _abrir_externo(self):
        if self.canal_atual and self.canal_atual.get("url"):
            webbrowser.open(self.canal_atual["url"])

    # -- internos ---------------------------------------------------------- #
    def _tocar_vlc(self, url):
        self.lbl_status.config(text="Conectando ao stream...")
        self.lbl_video.config(image="", text="")
        try:
            if self.winfo_exists():
                self.lbl_status.pack_forget()
                self.lbl_video.pack_forget()
                self.barra.pack_forget()
                self.vlc_frame = ttk.Frame(self)
                self.vlc_frame.pack(fill="both", expand=True, padx=4, pady=4)
                self.lbl_status.pack(fill="x", pady=(4, 0), before=self.vlc_frame)
                self.barra.pack(fill="x", padx=4, pady=(0, 4))

                inst = vlc.Instance("--no-video-title-show", "--quiet")
                self.player = inst.media_player_new()
                self.player.set_hwnd(self.vlc_frame.winfo_id())
                media = inst.media_new(url)
                self.player.set_media(media)
                self.player.play()
                self.lbl_status.config(text="Reproduzindo")
        except Exception as e:
            self.player = None
            if getattr(self, "vlc_frame", None) is not None:
                self.vlc_frame.destroy()
                self.vlc_frame = None
            self._restaurar_layout()
            self.lbl_video.config(text=f"Falha no player:\n{e}")
            self._mostrar_logo(self.canal_atual.get("logo", ""))

    def _mostrar_logo(self, logo_url):
        def worker():
            data = None
            try:
                if not logo_url:
                    return None
                if re.match(r"^https?://", logo_url, re.IGNORECASE):
                    import urllib.request
                    req = urllib.request.Request(logo_url,
                                               headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        data = r.read()
                elif os.path.exists(logo_url):
                    with open(logo_url, "rb") as f:
                        data = f.read()
            except Exception:
                return None

            if not data or not PIL_AVAILABLE:
                return None
            try:
                import io
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                img.thumbnail((420, 260))
                return ImageTk.PhotoImage(img)
            except Exception:
                return None

        def finish(photo):
            if not self.winfo_exists():
                return
            if photo is not None:
                self._img_ref = photo
                self.lbl_video.config(image=photo, text="")
            else:
                self.lbl_video.config(
                    image="",
                    text="Sem imagem disponivel.\n(use 'Abrir no navegador' para ver)")

        t = threading.Thread(target=lambda: finish(worker()), daemon=True)
        t.start()


# --------------------------------------------------------------------------- #
#  Janela principal
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x720")
        self.minsize(940, 560)

        self.db = Database()
        self.canais = []          # canais da lista carregada
        self.filtro_grupos = []   # grupos da lista carregada
        self.origem_atual = ""
        self.ordem = {}           # nome -> posicao original na lista

        self._montar_estilo()
        self._montar_widgets()
        self._atualizar_status()
        self.protocol("WM_DELETE_WINDOW", self._sair)

    # -- estilo ------------------------------------------------------------ #
    def _montar_estilo(self):
        st = ttk.Style()
        try:
            st.theme_use("vista")
        except Exception:
            pass
        st.configure("Titulo.TLabel", font=("Segoe UI", 15, "bold"))
        st.configure("Card.TFrame", relief="groove", borderwidth=1)
        st.configure("Filtro.TEntry", padding=4)

    # -- widgets ----------------------------------------------------------- #
    def _montar_widgets(self):
        # ---------------- barra superior ---------------- #
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        top.columnconfigure(4, weight=1)

        ttk.Label(top, text="Organizador M3U", style="Titulo.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(top, text="1) Carregue uma lista:").grid(row=0, column=1, padx=(24, 6))

        self.btn_arquivo = ttk.Button(top, text="Selecionar arquivo...",
                                      command=self.selecionar_arquivo)
        self.btn_arquivo.grid(row=0, column=2, padx=4)

        self.btn_url = ttk.Button(top, text="Carregar URL...", command=self.carregar_url)
        self.btn_url.grid(row=0, column=3, padx=4)

        self.lista_origem = ttk.Label(top, text="Nenhuma lista carregada",
                                      foreground="#0a5")
        self.lista_origem.grid(row=0, column=5, sticky="w", padx=10)

        # ---------------- barra de filtros ---------------- #
        barra = ttk.Frame(self, padding=(8, 0, 8, 6))
        barra.pack(fill="x")
        barra.columnconfigure(1, weight=1)

        ttk.Label(barra, text="Buscar:").grid(row=0, column=0, sticky="w")
        self.var_busca = tk.StringVar()
        self.var_busca.trace_add("write", lambda *_: self._atualizar_tabela())
        self.ent_busca = ttk.Entry(barra, textvariable=self.var_busca, style="Filtro.TEntry")
        self.ent_busca.grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(barra, text="Grupo:").grid(row=0, column=2, padx=(12, 0))
        self.cb_grupo = ttk.Combobox(barra, state="readonly", width=26)
        self.cb_grupo.grid(row=0, column=3, sticky="w", padx=6)
        self.cb_grupo.bind("<<ComboboxSelected>>", lambda e: self._atualizar_tabela())

        self.chk_salvos = tk.BooleanVar(value=False)
        ttk.Checkbutton(barra, text="Somente canais ja salvos",
                        variable=self.chk_salvos,
                        command=self._atualizar_tabela).grid(row=0, column=4, padx=12)

        self.lbl_contagem = ttk.Label(barra, text="0 canais", foreground="#666")
        self.lbl_contagem.grid(row=0, column=5, padx=6)

        # ---------------- corpo: lista | video ---------------- #
        corpo = ttk.PanedWindow(self, orient="horizontal")
        corpo.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ---- esquerda: treeview ---- #
        esq = ttk.Frame(corpo)
        esq.rowconfigure(0, weight=1)
        esq.columnconfigure(0, weight=1)

        colunas = ("nome", "grupo", "status")
        self.tree = ttk.Treeview(esq, columns=colunas, show="tree headings",
                                  selectmode="extended")
        self.tree.heading("#0", text="Lista / Canal", command=lambda: self._ordenar("#0"))
        self.tree.heading("nome", text="Nome", command=lambda: self._ordenar("nome"))
        self.tree.heading("grupo", text="Grupo", command=lambda: self._ordenar("grupo"))
        self.tree.heading("status", text="No banco", command=lambda: self._ordenar("status"))

        self.tree.column("#0", width=240, minwidth=120)
        self.tree.column("nome", width=300, minwidth=150, stretch=False)
        self.tree.column("grupo", width=170, minwidth=90, stretch=False)
        self.tree.column("status", width=80, minwidth=60, stretch=False, anchor="center")

        vs = ttk.Scrollbar(esq, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("grupo", foreground="#0a5", font=("Segoe UI", 9, "bold"))
        self.tree.tag_configure("salvo", foreground="#087")
        self.tree.tag_configure("offline", foreground="#999")

        self.tree.bind("<<TreeviewSelect>>", self._selecionar_canal)
        self.tree.bind("<Double-1>", lambda e: self.adicionar_selecionados())

        # ---- acoes da lista ---- #
        acoes = ttk.Frame(esq, padding=(0, 6))
        acoes.grid(row=1, column=0, columnspan=2, sticky="ew")
        acoes.columnconfigure(4, weight=1)

        self.btn_add = ttk.Button(acoes, text="Adicionar ao banco",
                                  command=self.adicionar_selecionados, state="disabled")
        self.btn_add.grid(row=0, column=0, padx=(0, 6))

        self.btn_add_todos = ttk.Button(acoes, text="Adicionar todos (filtrados)",
                                        command=self.adicionar_todos, state="disabled")
        self.btn_add_todos.grid(row=0, column=1, padx=6)

        self.btn_ver_salvos = ttk.Button(acoes, text="Ver canais salvos...",
                                         command=self.abrir_gerenciador)
        self.btn_ver_salvos.grid(row=0, column=2, padx=6)

        self.btn_export = ttk.Button(acoes, text="Exportar banco como M3U...",
                                     command=self.exportar_m3u, state="disabled")
        self.btn_export.grid(row=0, column=3, padx=6)

        self.lbl_detalhe = ttk.Label(acoes, text="", foreground="#666",
                                     wraplength=430, justify="left")
        self.lbl_detalhe.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # ---- direita: video ---- #
        dir_ = ttk.Frame(corpo, style="Card.TFrame", padding=4)
        dir_.rowconfigure(1, weight=1)
        dir_.columnconfigure(0, weight=1)
        ttk.Label(dir_, text="2) Visualizar canal", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=4, pady=(2, 0))
        self.video = VideoPanel(dir_)
        self.video.grid(row=1, column=0, sticky="nsew")

        corpo.add(esq, weight=3)
        corpo.add(dir_, weight=2)

        # ---------------- barra de status ---------------- #
        self.lbl_status = ttk.Label(self, text="Pronto", relief="sunken", anchor="w")
        self.lbl_status.pack(fill="x", side="bottom")
        self.lbl_engine = ttk.Label(
            self,
            text=f"Banco: {os.path.basename(DB_FILE)}  |  "
                 f"Pillow: {'sim' if PIL_AVAILABLE else 'nao'}  |  "
                 f"VLC: {'sim' if VLC_AVAILABLE else 'nao'}",
            relief="sunken", anchor="e")
        self.lbl_engine.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------ #
    #  Carregar listas
    # ------------------------------------------------------------------ #
    def _status(self, msg):
        self.lbl_status.config(text=msg)
        self.update_idletasks()

    def selecionar_arquivo(self):
        caminho = filedialog.askopenfilename(
            title="Selecionar lista M3U/M3U8",
            filetypes=[("Listas M3U", "*.m3u *.m3u8"), ("Todos", "*.*")],
        )
        if caminho:
            self.carregar_lista(caminho)

    def carregar_url(self):
        url = simpledialog.askstring("Carregar URL",
                                     "Cole a URL da lista M3U/M3U8:", parent=self)
        if url and url.strip():
            self.carregar_lista(url.strip())

    def carregar_lista(self, origem):
        self._status(f"Carregando lista de {origem}...")
        self.update_idletasks()
        try:
            texto = read_list_source(origem)
            canais, grupos, header = parse_m3u(texto)
        except Exception as e:
            messagebox.showerror("Erro ao carregar",
                                 f"Nao foi possivel ler a lista:\n\n{e}")
            self._status("Falha ao carregar a lista.")
            return

        if not canais:
            messagebox.showwarning(
                "Lista vazia",
                "Nenhum canal foi encontrado nessa lista.\n"
                "Verifique se o arquivo e uma M3U valida.")
            self._status("Lista carregada, mas sem canais.")
            return

        self.canais = canais
        self.grupos = grupos
        self.header_extra = header
        self.origem_atual = origem
        self.ordem = {c["url"]: i for i, c in enumerate(canais)}

        self.lista_origem.config(text=f"{os.path.basename(origem) if os.path.isfile(origem) else origem}  ({len(canais)} canais)")
        self.cb_grupo["values"] = ["Todos"] + grupos
        self.cb_grupo.current(0)

        self._popular_tree()
        self.btn_add.config(state="normal")
        self.btn_add_todos.config(state="normal")
        self.btn_export.config(state="normal")
        self._status(f"Lista carregada com sucesso: {len(canais)} canais.")
        self._atualizar_contagem()

    def _popular_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._ordem_col = "#0"
        self._asc = True
        self._atualizar_tabela()

    # ------------------------------------------------------------------ #
    #  Tabela (grupos -> canais)
    # ------------------------------------------------------------------ #
    def _atualizar_tabela(self):
        if not getattr(self, "canais", None):
            return
        self.tree.delete(*self.tree.get_children())

        busca = self.var_busca.get().strip().lower()
        grupo_sel = self.cb_grupo.get() or "Todos"
        somente_salvos = self.chk_salvos.get()
        urls_salvas = self._urls_salvas()

        visiveis = []
        for c in self.canais:
            if busca and busca not in c["nome"].lower() \
                    and busca not in c["grupo"].lower() \
                    and busca not in c["url"].lower():
                continue
            if grupo_sel != "Todos" and c["grupo"] != grupo_sel:
                continue
            if somente_salvos and c["url"] not in urls_salvas:
                continue
            visiveis.append(c)

        # ordena
        col, rev = self._ordem_col, not self._asc
        if col in ("#0", "nome"):
            visiveis.sort(key=lambda c: (self.ordem.get(c["url"], 0)) if col == "#0"
                          else c["nome"].lower(), reverse=rev)
        elif col == "grupo":
            visiveis.sort(key=lambda c: (c["grupo"].lower(), c["nome"].lower()),
                          reverse=rev)
        elif col == "status":
            visiveis.sort(key=lambda c: (c["url"] in urls_salvas, c["nome"].lower()),
                          reverse=rev)

        nos_grupos = {}
        for c in visiveis:
            g = c["grupo"]
            if grupo_sel == "Todos":
                if g not in nos_grupos:
                    pid = self.tree.insert("", "end", text=f"[+] {g}",
                                            open=True, tags=("grupo",), values=("", "", ""))
                    nos_grupos[g] = pid
                parent = nos_grupos[g]
            else:
                parent = ""

            salvo = c["url"] in urls_salvas
            self.tree.insert(
                parent, "end", iid=f"c{self.ordem.get(c['url'], 0)}",
                text=c["nome"],
                values=(c["nome"], c["grupo"], "OK" if salvo else "-"),
                tags=("salvo",) if salvo else ("offline",),
            )

        self._visiveis = visiveis
        self._atualizar_contagem()

    def _atualizar_contagem(self):
        n = len(getattr(self, "_visiveis", []))
        self.lbl_contagem.config(text=f"{n} de {len(self.canais)} canais")

    def _urls_salvas(self):
        return {r[0] for r in self.db.conn.execute("SELECT url FROM canais")}

    def _ordenar(self, col):
        if getattr(self, "_ordem_col", None) == col:
            self._asc = not self._asc
        else:
            self._ordem_col = col
            self._asc = True
        self._atualizar_tabela()

    # ------------------------------------------------------------------ #
    #  Selecao / video
    # ------------------------------------------------------------------ #
    def _canal_do_iid(self, iid):
        if not iid.startswith("c"):
            return None
        try:
            idx = int(iid[1:])
        except ValueError:
            return None
        return self.canais[idx] if idx < len(self.canais) else None

    def _selecionar_canal(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        canal = self._canal_do_iid(sel[0])
        if not canal:
            return
        self.lbl_detalhe.config(
            text=f"Nome: {canal['nome']}\n"
                 f"Grupo: {canal['grupo']}\n"
                 f"URL: {canal['url']}"
        )
        self.video.tocar(canal)

    # ------------------------------------------------------------------ #
    #  Salvar no banco
    # ------------------------------------------------------------------ #
    def adicionar_selecionados(self):
        sel = [s for s in self.tree.selection()]
        canais = [c for c in (self._canal_do_iid(s) for s in sel) if c]
        if not canais:
            return
        self.btn_add.config(state="disabled")
        ok, dupl, erros = self.db.adicionar_muitos(canais)
        self._atualizar_tabela()
        self.btn_add.config(state="normal")

        msg = [f"{ok} canal(is) salvo(s) no banco."]
        if dupl:
            msg.append(f"{dupl} ja estavam salvos (duplicados).")
        if erros:
            msg.append("Erros:\n  " + "\n  ".join(erros[:8]))
        messagebox.showinfo("Resultado", "\n\n".join(msg))
        self._status(f"Adicionados: {ok} | Duplicados: {dupl}")
        self._atualizar_contagem()

    def adicionar_todos(self):
        alvos = list(getattr(self, "_visiveis", []))
        if not alvos:
            messagebox.showinfo("Nada a adicionar",
                                "Nenhum canal corresponde ao filtro atual.")
            return
        if not messagebox.askyesno(
            "Confirmar",
            f"Adicionar {len(alvos)} canal(is) filtrado(s) ao banco?"):
            return
        self._status("Adicionando canais...")
        self.update_idletasks()
        ok, dupl, erros = self.db.adicionar_muitos(alvos)
        self._atualizar_tabela()
        self._status(f"Adicionados: {ok} | Duplicados: {dupl}")
        messagebox.showinfo(
            "Resultado",
            f"{ok} canal(is) salvo(s).\n{dupl} duplicado(s) ignorado(s).")

    # ------------------------------------------------------------------ #
    #  Exportar
    # ------------------------------------------------------------------ #
    def exportar_m3u(self):
        canal = None
        self._status("Gerando arquivo M3U...")
        destino = filedialog.asksaveasfilename(
            title="Exportar banco como lista M3U",
            defaultextension=".m3u8",
            filetypes=[("Lista M3U8", "*.m3u8"), ("Lista M3U", "*.m3u")],
            initialfile="minha_lista.m3u8",
        )
        if not destino:
            self._status("Exportacao cancelada.")
            return

        usar_filtro = messagebox.askyesno(
            "Escopo da exportacao",
            "Exportar SOMENTE os canais que estao visiveis no filtro atual?\n\n"
            "Sim = apenas o filtro visivel\n"
            "Nao = todos os canais do banco")

        if usar_filtro and getattr(self, "_visiveis", None):
            ids = self._urls_salvas()
            alvos = [c for c in self._visiveis if c["url"] in ids]
        else:
            alvos = self.db.listar()

        if not alvos:
            messagebox.showwarning("Nada para exportar",
                                   "Nenhum canal selecionado/ salvo para exportar.")
            self._status("Nada para exportar.")
            return

        conteudo = build_m3u(alvos, header=[
            f"Gerado por {APP_TITLE}",
            f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"Total de canais: {len(alvos)}",
        ])
        try:
            with open(destino, "w", encoding="utf-8") as f:
                f.write(conteudo)
        except Exception as e:
            messagebox.showerror("Erro ao exportar", str(e))
            self._status("Falha na exportacao.")
            return

        self._status(f"Exportado: {len(alvos)} canais -> {destino}")
        messagebox.showinfo("Exportado",
                            f"{len(alvos)} canal(is) salvos em:\n{destino}")

    # ------------------------------------------------------------------ #
    #  Gerenciador do banco (janela separada)
    # ------------------------------------------------------------------ #
    def abrir_gerenciador(self):
        GerenciadorCanais(self, self.db)

    # ------------------------------------------------------------------ #
    def _atualizar_status(self):
        self._status(f"Pronto. Banco possui {self.db.total()} canal(is).")

    def _sair(self):
        try:
            self.video.parar()
        except Exception:
            pass
        self.db.fechar()
        self.destroy()


# --------------------------------------------------------------------------- #
#  Janela: canais salvos no banco
# --------------------------------------------------------------------------- #
class GerenciadorCanais(tk.Toplevel):
    def __init__(self, master, db: Database):
        super().__init__(master)
        self.db = db
        self.title("Canais salvos no banco de dados")
        self.geometry("1050x620")
        self.minsize(760, 420)
        self.transient(master)

        self._montar()
        self.recarregar()
        self.bind("<Delete>", lambda e: self.remover_selecionados())
        self.bind("<F2>", lambda e: self.editar_selecionado())
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    def _montar(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Buscar:").grid(row=0, column=0)
        self.var_busca = tk.StringVar()
        self.var_busca.trace_add("write", lambda *_: self.recarregar())
        ttk.Entry(top, textvariable=self.var_busca).grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Label(top, text="Grupo:").grid(row=0, column=2, padx=(12, 0))
        self.cb_grupo = ttk.Combobox(top, state="readonly", width=24)
        self.cb_grupo.grid(row=0, column=3, sticky="w", padx=6)
        self.cb_grupo.bind("<<ComboboxSelected>>", lambda e: self.recarregar())

        self.chk_fav = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Somente favoritos",
                        variable=self.chk_fav, command=self.recarregar
                        ).grid(row=0, column=4, padx=10)

        self.lbl_total = ttk.Label(top, text="", foreground="#666")
        self.lbl_total.grid(row=0, column=5, padx=6)

        # arvore
        meio = ttk.Frame(self, padding=(8, 0))
        meio.pack(fill="both", expand=True)
        cols = ("id", "nome", "grupo", "url")
        self.tree = ttk.Treeview(meio, columns=cols, show="headings",
                                  selectmode="extended")
        for c, txt, w in (("id", "ID", 50), ("nome", "Nome", 260),
                          ("grupo", "Grupo", 160), ("url", "URL", 460)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        vs = ttk.Scrollbar(meio, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("fav", background="#fff6c8")
        self.tree.bind("<Double-1>", lambda e: self.editar_selecionado())
        self.tree.bind("<<TreeviewSelect>>", self._mostrar_detalhe)

        # detalhe
        self.lbl_detalhe = ttk.Label(self, text="", padding=8, foreground="#333",
                                     wraplength=980, justify="left")
        self.lbl_detalhe.pack(fill="x")

        # botoes
        barra = ttk.Frame(self, padding=8)
        barra.pack(fill="x")
        for i, (txt, cmd) in enumerate((
            ("Editar...", self.editar_selecionado),
            ("Favorito", self.alternar_fav),
            ("Remover", self.remover_selecionados),
            ("Exportar selecionados...", self.exportar_selecionados),
            ("Exportar tudo...", self.exportar_tudo),
            ("Limpar banco", self.limpar),
        )):
            ttk.Button(barra, text=txt, command=cmd).pack(side="left", padx=4)

    # ------------------------------------------------------------------ #
    def recarregar(self):
        self.tree.delete(*self.tree.get_children())
        self.cb_grupo["values"] = ["Todos"] + self.db.grupos()
        linhas = self.db.listar(self.var_busca.get(), self.cb_grupo.get(),
                                self.chk_fav.get())
        for r in linhas:
            self.tree.insert("", "end", iid=str(r["id"]),
                             values=(r["id"], r["nome"], r["grupo"], r["url"]),
                             tags=("fav",) if r["favorito"] else ())
        self.lbl_total.config(text=f"{len(linhas)} de {self.db.total()} canais")
        self._sincronizar_pai()

    def _sincronizar_pai(self):
        try:
            self.master.recarregar_gerenciador = True
            self.master._atualizar_tabela()
            self.master.lbl_status.config(
                text=f"Banco: {self.db.total()} canal(is) salvo(s).")
        except Exception:
            pass

    def _selecionados(self):
        return [int(i) for i in self.tree.selection()]

    def _mostrar_detalhe(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            self.lbl_detalhe.config(text="")
            return
        cid = int(sel[0])
        r = self.db.conn.execute("SELECT * FROM canais WHERE id = ?", (cid,)).fetchone()
        if r:
            self.lbl_detalhe.config(
                text=(f"ID {r['id']}  |  {r['nome']}\n"
                      f"Grupo: {r['grupo']}   |   tvg-id: {r['tvg_id'] or '-'}   |   "
                      f"Favorito: {'sim' if r['favorito'] else 'nao'}\n"
                      f"URL: {r['url']}"))

    # ------------------------------------------------------------------ #
    def editar_selecionado(self):
        sel = self._selecionados()
        if len(sel) != 1:
            messagebox.showinfo("Editar", "Selecione exatamente 1 canal.", parent=self)
            return
        cid = sel[0]
        r = self.db.conn.execute("SELECT * FROM canais WHERE id = ?", (cid,)).fetchone()

        dlg = tk.Toplevel(self)
        dlg.title("Editar canal")
        dlg.transient(self)
        dlg.grab_set()
        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)

        campos = {}
        for i, (rot, chave) in enumerate((("Nome:", "nome"), ("Grupo:", "grupo"),
                                           ("tvg-id:", "tvg_id"),
                                           ("Logo (URL):", "logo"))):
            ttk.Label(frm, text=rot).grid(row=i, column=0, sticky="w", pady=4)
            v = tk.StringVar(value=r[chave] or "")
            ttk.Entry(frm, textvariable=v, width=52).grid(row=i, column=1, sticky="ew", pady=4)
            campos[chave] = v

        ttk.Label(frm, text="URL:").grid(row=4, column=0, sticky="nw", pady=4)
        tv = tk.Text(frm, height=4, width=54, wrap="word")
        tv.insert("1.0", r["url"] or "")
        tv.grid(row=4, column=1, sticky="ew", pady=4)
        campos["url"] = tv

        frm.columnconfigure(1, weight=1)

        def salvar():
            novo = {k: (v.get() if isinstance(v, tk.StringVar) else v.get("1.0", "end-1c").strip())
                    for k, v in campos.items()}
            if not novo["url"]:
                messagebox.showwarning("URL invalida", "A URL nao pode ficar vazia.",
                                       parent=dlg)
                return
            self.db.atualizar(cid, novo)
            self.recarregar()
            dlg.destroy()

        btns = ttk.Frame(dlg, padding=(14, 0, 14, 14))
        btns.pack(fill="x")
        ttk.Button(btns, text="Salvar", command=salvar).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancelar", command=dlg.destroy).pack(side="right")

    def alternar_fav(self):
        for cid in self._selecionados():
            self.db.alternar_favorito(cid)
        self.recarregar()

    def remover_selecionados(self):
        sel = self._selecionados()
        if not sel:
            return
        if messagebox.askyesno("Remover",
                               f"Remover {len(sel)} canal(is) do banco?", parent=self):
            n = self.db.remover_muitos(sel)
            self.recarregar()
            messagebox.showinfo("Removido", f"{n} canal(is) removido(s).", parent=self)

    def limpar(self):
        if messagebox.askyesno("Limpar banco",
                               "Apagar TODOS os canais do banco? Essa acao nao pode ser desfeita.",
                               parent=self):
            self.db.limpar()
            self.recarregar()

    def exportar_tudo(self):
        alvos = self.db.listar()
        self._salvar_m3u(alvos)

    def exportar_selecionados(self):
        sel = self._selecionados()
        alvos = [r for r in self.db.conn.execute(
            "SELECT * FROM canais WHERE id IN (%s)" %
            ",".join("?" * len(sel)), sel)] if sel else []
        self._salvar_m3u([dict(r) for r in alvos])

    def _salvar_m3u(self, canais):
        if not canais:
            messagebox.showinfo("Exportar", "Nada selecionado para exportar.", parent=self)
            return
        destino = filedialog.asksaveasfilename(
            title="Salvar lista M3U", defaultextension=".m3u8",
            filetypes=[("M3U8", "*.m3u8"), ("M3U", "*.m3u")],
            initialfile="meus_canais.m3u8", parent=self)
        if not destino:
            return
        with open(destino, "w", encoding="utf-8") as f:
            f.write(build_m3u(canais, header=[
                f"Exportado do banco por {APP_TITLE}",
                f"Data: {datetime.now():%d/%m/%Y %H:%M}",
                f"Total: {len(canais)} canais"]))
        messagebox.showinfo("Exportado", f"{len(canais)} canal(is) em:\n{destino}",
                            parent=self)

    def _fechar(self):
        try:
            self.master._atualizar_tabela()
        except Exception:
            pass
        self.destroy()


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    App().mainloop()
