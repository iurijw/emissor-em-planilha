"""Versão instalada, novidades de cada versão e atualização da instalação própria.

O histórico (até a versão instalada) vem de ``data/novidades.md``, que acompanha o código. As versões mais novas vêm
das releases do GitHub, cujo texto começa com a seção da versão no mesmo arquivo (workflow "Publicar versão").

Tipo de instalação (``instalacao()``):
- ``hospedagem``: máquina do Fly (``FLY_APP_NAME``). O painel atualiza sozinho: nada é consultado no GitHub.
- ``iniciar``: aberto pelo ``iniciar.bat`` (``EMISSOR_INSTALACAO=iniciar``). O administrador atualiza pela tela
  ("Atualizar agora": o servidor sai com ``CODIGO_ATUALIZAR`` e o ``iniciar.bat`` roda ``emissor-atualizar`` e abre o
  sistema de novo) ou deixa atualizar sozinho ao iniciar (``data/atualizacao.json``).
- ``docker``: imagem do ``docker compose`` (``EMISSOR_INSTALACAO=docker``). A tela mostra o comando.
- ``manual``: qualquer outro jeito de rodar (``uv run emissor``). A tela mostra o que fazer.

A atualização baixa o código da tag ``vX.Y.Z`` (ou faz ``git pull`` numa cópia do git), guarda antes uma cópia do
banco e do código em ``data/backups`` e nunca mexe na pasta ``data``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from emissor import config

log = logging.getLogger(__name__)

REPOSITORIO = os.environ.get("EMISSOR_REPOSITORIO", "iurijw/emissor-em-planilha")
URL_VERSOES = f"https://github.com/{REPOSITORIO}/releases"
TIMEOUT = 10.0
# Releases guardadas por 6 h (a API do GitHub sem token aceita 60 consultas por hora por IP); falha, por 15 min.
CACHE_SEGUNDOS = 6 * 3600
CACHE_FALHA_SEGUNDOS = 15 * 60
MAX_ZIP = 100_000_000
# Código de saída do servidor que pede ao iniciar.bat para atualizar e abrir de novo.
CODIGO_ATUALIZAR = 75
# Código de saída de ``emissor-atualizar`` quando o código mudou (o iniciar.bat recomeça com o arquivo novo).
CODIGO_ATUALIZADO = 3
# Desligada nos testes (sem internet).
ATIVA = True

ARQUIVO_NOVIDADES = config.PACKAGE_DIR / "data" / "novidades.md"
_SECAO = re.compile(r"^## (\d+\.\d+\.\d+)(?: \((\d{2}/\d{2}/\d{4})\))?\s*$")

# Nada disto é apagado nem substituído na atualização (relativo à pasta da instalação).
PROTEGIDOS = ("data", "temp", ".git", "backend/.venv", "frontend/node_modules", "frontend/dist")
# Pastas que são só do sistema: arquivo que saiu da versão nova é apagado (fora delas nada é apagado).
ESPELHADAS = ("backend/src", "backend/tests", "backend/scripts", "frontend/src", "frontend/public", "docs", ".github")

_cache: tuple[float, list[dict[str, Any]] | None, str | None] | None = None
_trava = threading.Lock()


class AtualizacaoError(Exception):
    pass


# --- versões -------------------------------------------------------------------------------------


def versao_tupla(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def instalacao() -> str:
    if os.environ.get("FLY_APP_NAME"):
        return "hospedagem"
    tipo = os.environ.get("EMISSOR_INSTALACAO", "").strip().lower()
    return tipo if tipo in ("iniciar", "docker") else "manual"


def ler_novidades(texto: str) -> list[dict[str, Any]]:
    """Seções ``## X.Y.Z (DD/MM/AAAA)`` com itens ``- `` (itens de várias linhas são juntados)."""
    texto = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    versoes: list[dict[str, Any]] = []
    atual: dict[str, Any] | None = None
    for linha in texto.splitlines():
        m = _SECAO.match(linha)
        if m:
            data = None
            if m.group(2):
                d, mes, a = m.group(2).split("/")
                data = f"{a}-{mes}-{d}"
            atual = {"versao": m.group(1), "data": data, "itens": []}
            versoes.append(atual)
        elif linha.startswith("## ") or linha.startswith("# "):
            atual = None
        elif atual is not None:
            if linha.startswith("- "):
                atual["itens"].append(linha[2:].strip())
            elif linha.startswith("  ") and linha.strip() and atual["itens"]:
                atual["itens"][-1] += " " + linha.strip()
    return versoes


def historico_local() -> list[dict[str, Any]]:
    try:
        return ler_novidades(ARQUIVO_NOVIDADES.read_text(encoding="utf-8"))
    except OSError:
        log.warning("novidades.md não encontrado em %s", ARQUIVO_NOVIDADES)
        return []


def _itens_da_release(corpo: str) -> list[str]:
    """Itens da seção "## Novidades" do texto da release (o resto é a lista de PRs gerada pelo GitHub)."""
    itens: list[str] = []
    dentro = False
    for linha in (corpo or "").replace("\r\n", "\n").splitlines():
        if linha.startswith("## "):
            dentro = linha[3:].strip().lower() == "novidades"
            continue
        if dentro:
            if linha.startswith("- "):
                itens.append(linha[2:].strip())
            elif linha.startswith("  ") and linha.strip() and itens:
                itens[-1] += " " + linha.strip()
    return itens


def _buscar_releases() -> list[dict[str, Any]]:
    r = httpx.get(
        f"https://api.github.com/repos/{REPOSITORIO}/releases",
        params={"per_page": 30},
        headers={"Accept": "application/vnd.github+json", "User-Agent": config.VER_APLIC},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    r.raise_for_status()
    versoes = []
    for rel in r.json():
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "")
        if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
            continue
        publicada = str(rel.get("published_at") or "")[:10] or None
        versoes.append(
            {"versao": tag.lstrip("v"), "data": publicada, "itens": _itens_da_release(rel.get("body") or "")}
        )
    return versoes


def releases(forcar: bool = False) -> tuple[list[dict[str, Any]] | None, str | None]:
    """(versões publicadas, erro). Na hospedagem e nos testes não consulta nada."""
    global _cache
    if not ATIVA or instalacao() == "hospedagem":
        return None, None
    with _trava:
        if _cache and not forcar:
            quando, dados, erro = _cache
            if time.monotonic() - quando < (CACHE_SEGUNDOS if dados is not None else CACHE_FALHA_SEGUNDOS):
                return dados, erro
        try:
            dados, erro = _buscar_releases(), None
        except (httpx.HTTPError, ValueError) as e:
            log.warning("não foi possível consultar as versões no GitHub: %s", e)
            dados, erro = None, "Não foi possível consultar as versões novas agora (sem acesso ao GitHub)."
        _cache = (time.monotonic(), dados, erro)
        return dados, erro


def limpar_cache() -> None:
    global _cache
    _cache = None


# --- preferências e pedidos (arquivos em data/, lidos também pelo emissor-atualizar) ----------------


def _arq_preferencias() -> Path:
    return config.DATA_DIR / "atualizacao.json"


def _arq_pedido() -> Path:
    return config.DATA_DIR / "atualizar-agora"


def _arq_resultado() -> Path:
    return config.DATA_DIR / "atualizacao-resultado.json"


def automatica() -> bool:
    try:
        return bool(json.loads(_arq_preferencias().read_text(encoding="utf-8")).get("automatica"))
    except (OSError, ValueError, AttributeError):
        return False


def definir_automatica(ligar: bool) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _arq_preferencias().write_text(json.dumps({"automatica": bool(ligar)}), encoding="utf-8")


def ultimo_resultado() -> dict[str, Any] | None:
    try:
        return json.loads(_arq_resultado().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gravar_resultado(ok: bool, de: str, para: str | None, mensagem: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _arq_resultado().write_text(
        json.dumps(
            {"ok": ok, "de": de, "para": para, "mensagem": mensagem, "quando": datetime.now().astimezone().isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def situacao(admin: bool, forcar: bool = False) -> dict[str, Any]:
    atual = config.APP_VERSION
    tipo = instalacao()
    locais = historico_local()
    publicadas, erro = releases(forcar)
    por_versao = {v["versao"]: v for v in locais if versao_tupla(v["versao"]) <= versao_tupla(atual)}
    for v in publicadas or []:
        # O texto da versão instalada e das anteriores vem do arquivo local (o da release pode não ter "Novidades").
        if versao_tupla(v["versao"]) > versao_tupla(atual) or v["versao"] not in por_versao:
            por_versao[v["versao"]] = v
    versoes = sorted(por_versao.values(), key=lambda v: versao_tupla(v["versao"]), reverse=True)
    novas = [v["versao"] for v in versoes if versao_tupla(v["versao"]) > versao_tupla(atual)]
    resultado = ultimo_resultado()
    return {
        "versao_atual": atual,
        "instalacao": tipo,
        "versoes": [v | {"nova": v["versao"] in novas, "atual": v["versao"] == atual} for v in versoes],
        "ultima": novas[0] if novas else atual,
        "novas": len(novas),
        "erro": erro,
        "url_versoes": URL_VERSOES,
        "automatica": automatica() if tipo == "iniciar" else False,
        "pode_atualizar": admin and tipo == "iniciar",
        "atualizando": _arq_pedido().exists(),
        "resultado": resultado if resultado and resultado.get("para") else None,
    }


def pedir_atualizacao(versao: str) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _arq_pedido().write_text(versao, encoding="utf-8")


def encerrar_para_atualizar() -> None:
    """Sai do servidor com o código que faz o iniciar.bat atualizar e abrir de novo."""
    logging.shutdown()
    os._exit(CODIGO_ATUALIZAR)


# --- atualização (emissor-atualizar, rodado pelo iniciar.bat antes de abrir o servidor) -------------


@dataclass
class Instalacao:
    raiz: Path
    dados: Path
    avisos: list[str] = field(default_factory=list)

    def protegido(self, rel: PurePosixPath) -> bool:
        partes = rel.parts
        if "__pycache__" in partes:
            return True
        texto = rel.as_posix()
        return any(texto == p or texto.startswith(p + "/") for p in PROTEGIDOS)


def _arquivos_do_zip(conteudo: bytes, versao: str) -> dict[str, bytes]:
    """Arquivos do ZIP da tag (sem a pasta de cima), conferindo que é mesmo o Emissor na versão pedida."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as e:
        raise AtualizacaoError("O arquivo baixado do GitHub não é um ZIP válido.") from e
    arquivos: dict[str, bytes] = {}
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        partes = PurePosixPath(info.filename).parts
        if len(partes) < 2:
            continue
        rel = PurePosixPath(*partes[1:])
        if rel.is_absolute() or ".." in rel.parts:
            raise AtualizacaoError(f"Caminho inválido no ZIP: {info.filename}")
        total += info.file_size
        if total > 4 * MAX_ZIP:
            raise AtualizacaoError("O ZIP baixado é grande demais.")
        arquivos[rel.as_posix()] = zf.read(info)
    pyproject = arquivos.get("backend/pyproject.toml", b"").decode("utf-8", "replace")
    m = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    if "iniciar.bat" not in arquivos or not m:
        raise AtualizacaoError("O ZIP baixado não parece ser do Emissor em Planilha.")
    if m.group(1) != versao:
        raise AtualizacaoError(f"O ZIP baixado é da versão {m.group(1)}, não da {versao}.")
    return arquivos


def _baixar_zip(versao: str) -> bytes:
    url = f"https://github.com/{REPOSITORIO}/archive/refs/tags/v{versao}.zip"
    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True, headers={"User-Agent": config.VER_APLIC}) as r:
        r.raise_for_status()
        buf = io.BytesIO()
        for parte in r.iter_bytes():
            buf.write(parte)
            if buf.tell() > MAX_ZIP:
                raise AtualizacaoError("O ZIP da versão nova é grande demais.")
    return buf.getvalue()


def _guardar_poucos(pasta: Path, padrao: str, manter: int) -> None:
    for velho in sorted(pasta.glob(padrao), key=lambda p: p.stat().st_mtime, reverse=True)[manter:]:
        velho.unlink(missing_ok=True)


def copiar_banco(inst: Instalacao, de: str, para: str) -> Path | None:
    banco = inst.dados / "emissor.db"
    if not banco.exists():
        return None
    pasta = inst.dados / "backups"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"emissor-{de}-antes-da-{para}-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(banco) as origem, sqlite3.connect(destino) as copia:
        origem.backup(copia)
    _guardar_poucos(pasta, "emissor-*.db", 3)
    return destino


def _arquivos_instalados(inst: Instalacao) -> dict[str, Path]:
    """Arquivos do sistema na instalação (sem as pastas protegidas nem a de dados)."""
    achados: dict[str, Path] = {}
    dados = inst.dados.resolve()
    for pasta, subpastas, arquivos in os.walk(inst.raiz):
        base = Path(pasta)
        rel_base = PurePosixPath(base.relative_to(inst.raiz).as_posix())
        subpastas[:] = [d for d in subpastas if not inst.protegido(rel_base / d) and (base / d).resolve() != dados]
        for nome in arquivos:
            rel = rel_base / nome
            if not inst.protegido(rel):
                achados[rel.as_posix()] = base / nome
    return achados


def copiar_codigo(inst: Instalacao, versao: str) -> Path:
    pasta = inst.dados / "backups"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"codigo-{versao}-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, caminho in _arquivos_instalados(inst).items():
            zf.write(caminho, rel)
    _guardar_poucos(pasta, "codigo-*.zip", 2)
    return destino


def aplicar_arquivos(inst: Instalacao, novos: dict[str, bytes]) -> int:
    """Grava só o que mudou e apaga o que saiu da versão nova dentro das pastas ``ESPELHADAS``."""
    mudados = 0
    for rel, conteudo in novos.items():
        if inst.protegido(PurePosixPath(rel)):
            continue
        destino = inst.raiz / rel
        if destino.is_file() and destino.read_bytes() == conteudo:
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
        mudados += 1
    for rel, caminho in _arquivos_instalados(inst).items():
        if rel not in novos and any(rel.startswith(p + "/") for p in ESPELHADAS):
            caminho.unlink()
            mudados += 1
    return mudados


def _restaurar(inst: Instalacao, copia: Path) -> None:
    with zipfile.ZipFile(copia) as zf:
        aplicar_arquivos(inst, {n: zf.read(n) for n in zf.namelist()})


def atualizar(inst: Instalacao, versao: str, baixar=_baixar_zip) -> None:
    """Troca o código da instalação pelo da tag ``vversao``. Em erro, volta o código anterior."""
    de = config.APP_VERSION
    copiar_banco(inst, de, versao)
    if (inst.raiz / ".git").exists():
        # Cópia do git: o próprio git atualiza (trocar arquivos por fora deixaria a cópia "modificada").
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=inst.raiz, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise AtualizacaoError(f"O git pull falhou: {(r.stderr or r.stdout).strip()[:500]}")
        return
    novos = _arquivos_do_zip(baixar(versao), versao)
    copia = copiar_codigo(inst, de)
    try:
        aplicar_arquivos(inst, novos)
    except Exception:
        log.exception("falha ao gravar a versão nova; voltando a anterior")
        _restaurar(inst, copia)
        raise


def ultima_publicada() -> str | None:
    publicadas, _ = releases(forcar=True)
    if not publicadas:
        return None
    return max((v["versao"] for v in publicadas), key=versao_tupla)


def main(argv: list[str] | None = None) -> int:
    """``emissor-atualizar``: chamado pelo iniciar.bat a cada abertura.

    Atualiza se o administrador pediu pela tela ou se a atualização automática está ligada. Devolve
    ``CODIGO_ATUALIZADO`` quando o código mudou; qualquer falha só avisa e o sistema abre na versão de antes.
    """
    inst = Instalacao(raiz=config.REPO_ROOT, dados=config.DATA_DIR)
    pedido = _arq_pedido()
    pediu = pedido.exists()
    if not pediu and not automatica():
        return 0
    de = config.APP_VERSION
    ultima: str | None = None
    print("Procurando versão nova do Emissor em Planilha...", flush=True)
    try:
        ultima = ultima_publicada()
        if ultima is None:
            raise AtualizacaoError("Não foi possível consultar as versões no GitHub (sem internet?).")
        if versao_tupla(ultima) <= versao_tupla(de):
            print(f"Já está na versão mais recente ({de}).", flush=True)
            if pediu:
                _gravar_resultado(True, de, None, "Já estava na versão mais recente.")
            return 0
        print(f"Atualizando da versão {de} para a {ultima} (cópia do banco em data\\backups)...", flush=True)
        atualizar(inst, ultima)
    except Exception as e:  # noqa: BLE001 - qualquer falha: abre a versão de antes
        msg = str(e) if isinstance(e, AtualizacaoError) else f"Falha inesperada: {e}"
        print(f"AVISO: a atualização não foi feita. {msg}", file=sys.stderr, flush=True)
        _gravar_resultado(False, de, ultima, msg)
        return 0
    finally:
        pedido.unlink(missing_ok=True)
    _gravar_resultado(True, de, ultima, f"Atualizado da versão {de} para a {ultima}.")
    print(f"Pronto: versão {ultima} instalada.", flush=True)
    return CODIGO_ATUALIZADO


if __name__ == "__main__":
    raise SystemExit(main())
