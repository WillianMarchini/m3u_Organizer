# 📺 Organizador M3U

Aplicação desktop em **Python + Tkinter + SQLite3** para organizar, visualizar e editar listas de canais IPTV (`.m3u` / `.m3u8`).

Carregue uma lista, navegue pelos canais, pré-visualize o stream e monte sua própria lista salvando apenas o que interessa em um banco SQLite.

![Tkinter](https://img.shields.io/badge/Tkinter-tkinter-blue) ![Python](https://img.shields.io/badge/Python-3.9+-3776AB) ![SQLite](https://img.shields.io/badge/SQLite-3-003B57) ![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ Funcionalidades

### Janela principal — organizar e salvar

| Recurso | Descrição |
|---|---|
| 📂 **Selecionar arquivo** | Abre qualquer lista `.m3u` / `.m3u8` do computador |
| 🌐 **Carregar URL** | Carrega a lista direto de um endereço HTTP/HTTPS |
| 🌳 **Treeview agrupada** | Todos os canais organizados por `group-title` |
| 🔍 **Busca e filtros** | Pesquisa por nome, grupo ou URL + filtro por grupo |
| 📊 **Ordenação** | Clique no cabeçalho para ordenar por qualquer coluna |
| 📺 **Player integrado** | Ao selecionar um canal, o stream abre no painel de vídeo |
| 💾 **Adicionar ao banco** | Selecione vários canais (ou todos os filtrados) e salve |
| 📤 **Exportar como M3U** | Gera uma nova lista a partir do que está no banco |

### Janela de canais salvos — gerenciar o banco

- 🔎 Busca por nome, URL e grupo
- ⭐ Marcar canais como **favoritos**
- ✏️ **Editar** nome, grupo, `tvg-id`, logo e URL
- 🗑️ **Remover** canais (individual ou em lote) e limpar o banco inteiro
- 📤 **Exportar** os selecionados ou o banco inteiro

### Detalhes técnicos

- 📦 **Leitor robusto** — detecta automaticamente a codificação (`UTF-8`, `CP1252`, `ISO-8859-1`), comumente embaralhada em listas IPTV
- 🔁 **Round-trip fiel** — exportar e reimportar preserva `tvg-id`, `tvg-name`, `tvg-logo` e `group-title`
- 🚫 **Sem duplicatas** — a URL é a chave de identificação; canals já salvos são ignorados com aviso
- ⚡ **Interface responsiva** — o download do logo roda em thread separada, sem travar a janela
- 💽 **Banco local** — um único arquivo `canais.db`, fácil de copiar ou fazer backup

---

## 📋 Requisitos

### Obrigatório

- **Python 3.9+**
- `tkinter` — já vem com o Python no Windows e macOS
  - No Linux, pode precisar ser instalado: `sudo apt install python3-tk`
- `sqlite3` — já vem com o Python

### Opcional

| Pacote | Para quê |
|---|---|
| `pillow` | Exibir a imagem do logo do canal no painel |
| `python-vlc` | Tocar o stream de vídeo (requer o [VLC media player](https://www.videolan.org/vlc/) instalado) |

Ambas estão declaradas no `requirements.txt`:

```bash
pip install -r requirements.txt
```

> A aplicação funciona **sem nenhum dos dois**. Sem o VLC, o painel mostra o logo do canal e o botão **Abrir no navegador** dá acesso direto ao stream.

---

## 🚀 Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/seu-usuario/organizador-m3u.git
cd organizador-m3u

# 2. (opcional) instalar as dependências extras
pip install -r requirements.txt

# 3. Executar
python m3u_organizador.py
```

No Windows você também pode dar duplo clique no arquivo, ou criar um atalho com o comando:
```
pythonw m3u_organizador.py
```
(`pythonw` abre sem o terminal preto na tela.)

---

## 📖 Como usar

### 1. Carregar a lista

Clique em **Selecionar arquivo...** e escolha sua lista, ou em **Carregar URL...** e cole o endereço de uma lista remota. Todos os canais aparecem na árvore, agrupados por categoria.

### 2. Navegar e pré-visualizar

Clique em um canal. O painel à direita tenta reproduzir o stream. Use **Parar vídeo** para interromper ou **Abrir no navegador** para assistir em outro programa.

### 3. Salvar os canais

Selecione os canais (**Ctrl**/**Shift** + clique para vários) e clique em **Adicionar ao banco**. Para pegar tudo o que está visível no filtro atual, use **Adicionar todos (filtrados)** — a confirmação com a contagem aparece antes.

### 4. Editar o que já está salvo

Clique em **Ver canais salvos...**. Na janela nova você pode editar, favoritar, remover e exportar. Duplo clique em um canal abre a edição; as teclas <kbd>Delete</kbd> e <kbd>F2</kbd> também funcionam.

### 5. Exportar sua lista

Use **Exportar banco como M3U...** na janela principal. O app pergunta se você quer exportar apenas o filtro visível ou o banco inteiro, e grava o arquivo com os atributos originais preservados.

---

## 🗂️ Estrutura do banco

O arquivo `canais.db` é criado automaticamente na mesma pasta do script.

```sql
canais
├── id         INTEGER  -- chave primária
├── nome       TEXT     -- nome exibido (tvg-name)
├── logo       TEXT     -- URL da imagem do canal
├── grupo      TEXT     -- group-title
├── tvg_id     TEXT     -- identificador do canal
├── url        TEXT     -- endereço do stream (UNIQUE na lógica do app)
├── duracao    INTEGER  -- duração em segundos
├── atributos  TEXT     -- atributos extras em JSON
├── favorito   INTEGER  -- 0 ou 1
└── criado_em  TEXT     -- data/hora do salvamento

listas           -- Agrupamentos de listas (estrutura pronta para uso futuro)
lista_canais     -- Relacionamento N:N entre listas e canais
```

Para fazer backup, basta copiar o arquivo `canais.db`.

---

## ⌨️ Atalhos

| Tecla | Janela principal | Canais salvos |
|---|---|---|
| <kbd>Duplo clique</kbd> | Adiciona o canal ao banco | Abre a edição |
| <kbd>Delete</kbd> | — | Remove a seleção |
| <kbd>F2</kbd> | — | Edita a seleção |

---

## ❓ Perguntas frequentes

**A lista carregou mas não aparece nenhum canal.**
O arquivo provavelmente não é uma M3U válida. Verifique se as entradas seguem o formato `#EXTINF:-1,...` seguidos da URL em uma linha seguinte.

**Alguns nomes aparecem com caracteres estranhos.**
Listas antigas usam Latin-1. O app já tenta três codificações automaticamente; se ainda ficar ruim, re-salve a lista original como UTF-8.

**O vídeo não toca.**
Instale o VLC media player e o `python-vlc`. O rodapé da janela mostra `VLC: sim` ou `VLC: nao` para confirmar o status.

**Os logos aparecem quebrados.**
Alguns servidores IPTV bloqueiam requisições sem User-Agent ou exigem HTTPS. O app envia um User-Agent de navegador, mas alguns servidores continuam inacessíveis.

**Onde ficam os arquivos?**
`canais.db` é criado ao lado do `m3u_organizador.py`. As listas exportadas vão para a pasta que você escolher na janela de salvamento.

---

## 🛠️ Tecnologias

- **Tkinter / ttk** — interface gráfica nativa, sem dependências pesadas
- **SQLite3** — banco embutido, sem servidor
- **Pillow** (opcional) — manipulação de imagens
- **python-vlc** (opcional) — reprodução de streams

---

## 📄 Licença

Distribuído sob a licença **MIT**. Sinta-se à vontade para usar, modificar e distribuir.

---

## ⚖️ Aviso legal

Este projeto é uma ferramenta de gerenciamento de listas de playlists. **Ele não fornece, hospeda nem redistribui conteúdo.** O usuário é responsável por garantir que possui os direitos sobre os canais que acessa e por cumprir a legislação aplicável à sua região.

## 🤝 Contribuições

Contribuições são bem-vindas!

1. Abra uma issue descrevendo o problema ou a sugestão
2. Envie um pull request com a alteração
3. Mantenha o estilo de código existente (PEP 8)

**Ideias para futuras versões:**

- Suporte a listas EPG (programação de TV)
- Exportação em outros formatos (XML, JSON, CSV)
- Agrupamento de canais em coleções nomeadas
- Importação automática de listas ao iniciar
