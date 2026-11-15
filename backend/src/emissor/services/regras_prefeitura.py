"""O que a prefeitura cadastrou no Sistema Nacional para o serviço: retenções do ISSQN, regimes especiais, histórico
de alíquotas e benefício municipal (parametrização do ADN, rotas do Swagger oficial, só leitura com o certificado).

Como as mensagens informativas do Emissor Nacional: **só avisos**, nunca bloqueiam. A regra exata (que tomador
retém, quem pode usar o regime) está na legislação municipal; o cadastro do município é um indício para conferir.
Formato das respostas conferido no ADN real em 15/11/2026 (Curitiba, Uberlândia, Ponta Grossa, Londrina):
- serviço sempre com 9 dígitos e pontos (``17.19.01.000``); 6 dígitos → 400 "deve ser composto por nove dígitos";
- ``tiposRetencao`` das retenções municipais usa os valores do ``tpRetISSQN`` (2 tomador, 3 intermediário);
- nos regimes especiais, a chave de cada regime é um número que muda a cada consulta (não diz qual regime é) e
  ``situacao`` não tem descrição oficial: só se usa "há regime cadastrado para o serviço" ou "não há";
- benefício: o tipo vem com números que não batem com o ``tpBM`` da NFS-e, então a descrição sai dos campos
  (``aliquotaDiferenciada``, ``reducaoPercentualBC``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from emissor.db import agora
from emissor.nfse import codes
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.errors import SefinError
from emissor.nfse.models import ConfigFiscal
from emissor.services import certificado as cert_service
from emissor.services import parametros

log = logging.getLogger(__name__)

TTL_RESPOSTA = 6 * 3600
TTL_FALHA = 5 * 60
# LC 116/2003, art. 6º, § 2º, II: o tomador pessoa jurídica retém o ISS destes subitens (o município que liga o
# "artigo sexto" no Sistema Nacional aplica a regra).
SUBITENS_ARTIGO_SEXTO = ("0305", "0702", "0704", "0705", "0709", "0710", "0712", "0716", "0717", "0719", "1102",
                         "1705", "1710")  # fmt: skip

AVISO = "aviso"
INFO = "info"

ClientFactory = Callable[[Ambiente, Certificado], SefinClient]


@dataclass
class Regra:
    tipo: str  # retencao | regime_especial | aliquota | beneficio
    nivel: str  # aviso (vale conferir) | info (bate com o cadastro)
    titulo: str
    mensagem: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Regras:
    municipio: str | None
    competencia: str
    regras: list[Regra] = field(default_factory=list)
    # [{aliquota "5.00", inicio "AAAA-MM-DD", fim}] do mais novo para o mais antigo
    historico_aliquotas: list[dict[str, str | None]] = field(default_factory=list)
    # Sem conferir (sem certificado, ADN fora do ar, serviço sem município de incidência único): o porquê.
    mensagem: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "municipio": self.municipio,
            "municipio_nome": codes.municipio_uf(self.municipio),
            "competencia": self.competencia,
            "regras": [r.as_dict() for r in self.regras],
            "historico_aliquotas": self.historico_aliquotas,
            "mensagem": self.mensagem,
        }


_cache: dict[tuple[str, str], tuple[float, tuple[int, Any] | None]] = {}
_lock = threading.Lock()


def limpar_cache() -> None:
    with _lock:
        _cache.clear()


class _SemResposta(Exception):
    """O ADN não respondeu (ou recusou a consulta): a regra fica sem conferir."""


def _consultar(
    ambiente: Ambiente, caminho: str, client: Callable[[], SefinClient], usar_cache: bool
) -> tuple[int, Any]:
    chave = (ambiente.value, caminho)
    with _lock:
        guardada = _cache.get(chave)
    if usar_cache and guardada and guardada[0] > time.monotonic():
        if guardada[1] is None:
            raise _SemResposta
        return guardada[1]
    try:
        r = client().parametrizacao(caminho)
    except SefinError as err:
        log.warning("consulta à parametrização do município falhou", extra={"caminho": caminho, "erro": err.resumo})
        with _lock:
            _cache[chave] = (time.monotonic() + TTL_FALHA, None)
        raise _SemResposta from err
    with _lock:
        _cache[chave] = (time.monotonic() + TTL_RESPOSTA, r)
    return r


def _dia(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _vigente(item: dict, competencia: date, ini: str = "dataInicioVigencia", fim: str = "dataFimVigencia") -> bool:
    a, b = _dia(item.get(ini)), _dia(item.get(fim))
    return (a is None or a <= competencia) and (b is None or competencia <= b)


def _pct(v: Any) -> str | None:
    try:
        return f"{Decimal(str(v).replace(',', '.')):.2f}"
    except (InvalidOperation, ValueError):
        return None


def _pct_tela(v: str | None) -> str:
    return (v or "").replace(".", ",").removesuffix(",00")


def _data_tela(d: date | None) -> str:
    return f"{d:%d/%m/%Y}" if d else ""


def _mesmo_servico(codigo: Any, c_trib_nac: str) -> bool:
    """``17.19.01.000`` (ou ``17.19.01.001``, com o desdobramento municipal) é do serviço ``171901``?"""
    digitos = "".join(ch for ch in str(codigo or "") if ch.isdigit())
    return digitos[:6] == c_trib_nac


def servico_completo(cfg: ConfigFiscal) -> str:
    return f"{codes.formatar_c_trib_nac(cfg.c_trib_nac)}.000"


def conferir(
    ambiente: Ambiente,
    cfg: ConfigFiscal,
    competencia: date | None = None,
    *,
    client_factory: ClientFactory | None = None,
    usar_cache: bool = True,
    ate: float | None = None,
) -> Regras:
    """Regras que a prefeitura de incidência cadastrou para o serviço da tributação ``cfg`` (prestador incluído).

    ``ate`` (``time.monotonic()``) limita o tempo: o que não couber fica sem conferir. Nunca levanta exceção.
    """
    competencia = competencia or agora().date()
    mes = f"{competencia:%m/%Y}"
    c_trib = (cfg.c_trib_nac or "").strip()
    if not (c_trib.isdigit() and len(c_trib) == 6):
        return Regras(None, mes, mensagem="Informe o código de tributação nacional (6 dígitos).")
    if cfg.c_pais_prestacao:
        return Regras(None, mes, mensagem="Serviço prestado no exterior: não há prefeitura para conferir.")
    mun, motivo = parametros.municipio_incidencia(cfg.c_mun_emissor, c_trib, cfg.c_loc_prestacao)
    if mun is None:
        return Regras(None, mes, mensagem=motivo)
    res = Regras(mun, mes)
    if not parametros.ATIVA:
        res.mensagem = "Conferência com a prefeitura desligada."
        return res
    cert = cert_service.carregar()
    if cert is None:
        res.mensagem = "Carregue o certificado digital para conferir com a prefeitura."
        return res

    aberto: list[SefinClient] = []

    def client() -> SefinClient:
        if ate is not None and time.monotonic() > ate:
            raise _SemResposta
        if not aberto:
            aberto.append((client_factory or SefinClient)(ambiente, cert).__enter__())
        return aberto[0]

    nome_mun = codes.municipio_uf(mun) or mun
    sem_resposta: list[str] = []
    etapas: list[tuple[str, Callable[[], None]]] = [
        (
            "o histórico de alíquotas",
            lambda: _aliquotas(res, ambiente, cfg, mun, nome_mun, competencia, client, usar_cache),
        ),
    ]
    if cfg.trib_issqn == "1":
        etapas += [
            ("as retenções", lambda: _retencoes(res, ambiente, cfg, mun, nome_mun, competencia, client, usar_cache)),
            (
                "os regimes especiais",
                lambda: _regimes(res, ambiente, cfg, mun, nome_mun, competencia, client, usar_cache),
            ),
        ]
        if cfg.n_bm:
            etapas.append(
                (
                    "o benefício municipal",
                    lambda: _beneficio(res, ambiente, cfg, mun, nome_mun, competencia, client, usar_cache),
                )
            )
    try:
        for nome, etapa in etapas:
            try:
                etapa()
            except _SemResposta:
                sem_resposta.append(nome)
            except Exception:  # resposta fora do formato: nunca atrapalha quem chamou
                log.exception("erro ao ler a parametrização do município", extra={"c_mun": mun, "etapa": nome})
                sem_resposta.append(nome)
    finally:
        if aberto:
            aberto[0].__exit__(None, None, None)
    if sem_resposta:
        res.mensagem = f"Não foi possível conferir agora {', '.join(sem_resposta)} com a prefeitura de {nome_mun}."
    # Avisos primeiro (o que vale conferir), depois o que bate com o cadastro.
    res.regras.sort(key=lambda r: r.nivel != AVISO)
    return res


# --- cada consulta ------------------------------------------------------------------------------------


def _aliquotas(
    res: Regras, amb: Ambiente, cfg: ConfigFiscal, mun: str, nome_mun: str, comp: date, client, cache
) -> None:
    status, dados = _consultar(amb, f"{mun}/{servico_completo(cfg)}/historicoaliquotas", client, cache)
    if not isinstance(dados, dict):
        raise _SemResposta
    lista = [
        a for v in (dados.get("aliquotas") or {}).values() if isinstance(v, list) for a in v if isinstance(a, dict)
    ]
    if status != 200 or not lista:
        return  # sem cadastro: a conferência do serviço (E0312) já avisa
    lista.sort(key=lambda a: str(a.get("DtIni") or ""), reverse=True)
    res.historico_aliquotas = [
        {"aliquota": _pct(a.get("Aliq")), "inicio": str(a.get("DtIni") or "")[:10] or None,
         "fim": str(a.get("DtFim") or "")[:10] or None}
        for a in lista
    ]  # fmt: skip
    atual = next((a for a in lista if _vigente(a, comp, "DtIni", "DtFim")), None)
    futura = next((a for a in reversed(lista) if (_dia(a.get("DtIni")) or comp) > comp), None)
    servico = codes.formatar_c_trib_nac(cfg.c_trib_nac)
    if atual is not None:
        ini = _dia(atual.get("DtIni"))
        anterior = next((a for a in lista if (_dia(a.get("DtIni")) or comp) < (ini or comp)), None)
        recente = ini is not None and (comp.year * 12 + comp.month) - (ini.year * 12 + ini.month) <= 1
        if anterior is not None and recente and _pct(anterior.get("Aliq")) != _pct(atual.get("Aliq")):
            res.regras.append(
                Regra(
                    "aliquota",
                    AVISO,
                    "A alíquota do ISSQN mudou",
                    f"A prefeitura de {nome_mun} mudou a alíquota do serviço {servico} de "
                    f"{_pct_tela(_pct(anterior.get('Aliq')))}% para {_pct_tela(_pct(atual.get('Aliq')))}% "
                    f"em {_data_tela(ini)}. Confira se o valor da nota e os tributos combinados com o cliente "
                    "levam isso em conta.",
                )
            )
        if str(atual.get("Incidencia") or "SIM").strip().upper() in ("NAO", "NÃO", "N"):
            res.regras.append(
                Regra(
                    "aliquota",
                    AVISO,
                    "Serviço cadastrado sem incidência",
                    f"A prefeitura de {nome_mun} cadastrou o serviço {servico} sem incidência do ISSQN. Confira na "
                    "legislação municipal como a nota deve sair.",
                )
            )
    if futura is not None and (atual is None or _pct(futura.get("Aliq")) != _pct(atual.get("Aliq"))):
        res.regras.append(
            Regra(
                "aliquota",
                INFO,
                "Alíquota nova já cadastrada",
                f"A partir de {_data_tela(_dia(futura.get('DtIni')))} a prefeitura de {nome_mun} cobra "
                f"{_pct_tela(_pct(futura.get('Aliq')))}% de ISSQN no serviço {servico}.",
            )
        )


def _retencoes(
    res: Regras, amb: Ambiente, cfg: ConfigFiscal, mun: str, nome_mun: str, comp: date, client, cache
) -> None:
    status, dados = _consultar(amb, f"{mun}/{comp.isoformat()}/retencoes", client, cache)
    if not isinstance(dados, dict) or status not in (200, 404):
        raise _SemResposta
    ret = dados.get("retencoes") if isinstance(dados.get("retencoes"), dict) else {}
    art6 = ret.get("artigoSexto") if isinstance(ret.get("artigoSexto"), dict) else {}
    hist_art6 = [h for h in art6.get("historico") or [] if isinstance(h, dict)]
    art6_ligado = bool(art6.get("habilitado")) and (not hist_art6 or any(_vigente(h, comp) for h in hist_art6))
    casos = []  # (descrição, tipos de retenção)
    for m in ret.get("retencoesMunicipais") or []:
        if not isinstance(m, dict) or not _vigente(m, comp):
            continue
        for s in m.get("servicos") or []:
            if not isinstance(s, dict) or not _mesmo_servico(s.get("codigoCompleto"), cfg.c_trib_nac):
                continue
            hist = [h for h in s.get("historico") or [] if isinstance(h, dict)]
            if not hist or any(_vigente(h, comp) for h in hist):
                casos.append(
                    (" ".join(str(m.get("descricao") or "").split()), {str(t) for t in m.get("tiposRetencao") or []})
                )
                break
    servico = codes.formatar_c_trib_nac(cfg.c_trib_nac)
    no_art6 = art6_ligado and cfg.c_trib_nac[:4] in SUBITENS_ARTIGO_SEXTO
    retido = cfg.tp_ret_issqn in ("2", "3")
    quem = {"2": "pelo tomador", "3": "pelo intermediário"}
    if not retido:
        for desc, tipos in casos:
            por = " ou ".join(quem[t] for t in ("2", "3") if t in tipos) or "pelo tomador"
            res.regras.append(
                Regra(
                    "retencao",
                    AVISO,
                    "O ISSQN pode ser retido",
                    f"A prefeitura de {nome_mun} cadastrou que o ISSQN do serviço {servico} pode ser retido {por} "
                    f"neste caso: “{desc}”. Se o tomador se enquadra, marque a retenção do ISSQN na tributação; "
                    "vale conferir na legislação municipal.",
                )
            )
        if no_art6 and not casos:
            res.regras.append(
                Regra(
                    "retencao",
                    AVISO,
                    "O ISSQN pode ser retido",
                    f"A prefeitura de {nome_mun} aplica a retenção da Lei Complementar 116 (art. 6º, § 2º, II) ao "
                    f"serviço {servico}: o tomador pessoa jurídica retém o ISSQN. Se for o caso, marque a retenção "
                    "do ISSQN na tributação; vale conferir na legislação municipal.",
                )
            )
        return
    por_quem = cfg.tp_ret_issqn
    combinam = [desc for desc, tipos in casos if not tipos or por_quem in tipos or "0" in tipos]
    if combinam or (no_art6 and por_quem == "2"):
        motivo = f"“{combinam[0]}”" if combinam else "a Lei Complementar 116 (art. 6º, § 2º, II)"
        res.regras.append(
            Regra(
                "retencao",
                INFO,
                "Retenção prevista pela prefeitura",
                f"A prefeitura de {nome_mun} prevê a retenção do ISSQN do serviço {servico} {quem[por_quem]}: "
                f"{motivo}. Confira se o tomador se enquadra.",
            )
        )
    elif casos:
        res.regras.append(
            Regra(
                "retencao",
                AVISO,
                "Quem retém o ISSQN",
                f"A prefeitura de {nome_mun} cadastrou a retenção do serviço {servico} só "
                f"{' ou '.join(quem[t] for t in ('2', '3') if t in casos[0][1])}, e a tributação diz retido "
                f"{quem[por_quem]}. Vale conferir na legislação municipal.",
            )
        )
    else:
        res.regras.append(
            Regra(
                "retencao",
                AVISO,
                "Retenção sem cadastro na prefeitura",
                f"A prefeitura de {nome_mun} não cadastrou no Sistema Nacional retenção do ISSQN para o serviço "
                f"{servico}. Se o tomador deve mesmo reter, está certo; vale conferir na legislação municipal.",
            )
        )


def _regimes(res: Regras, amb: Ambiente, cfg: ConfigFiscal, mun: str, nome_mun: str, comp: date, client, cache) -> None:
    status, dados = _consultar(
        amb, f"{mun}/{servico_completo(cfg)}/{comp.isoformat()}/regimes_especiais", client, cache
    )
    if not isinstance(dados, dict) or status not in (200, 404):
        raise _SemResposta
    vigentes = [
        r
        for por_servico in (dados.get("regimesEspeciais") or {}).values()
        if isinstance(por_servico, dict)
        for lista in por_servico.values()
        if isinstance(lista, list)
        for r in lista
        if isinstance(r, dict) and _vigente(r, comp, "dataInicio", "dataFim")
    ]
    servico = codes.formatar_c_trib_nac(cfg.c_trib_nac)
    obs = "; ".join(o for o in dict.fromkeys(" ".join(str(r.get("observacoes") or "").split()) for r in vigentes) if o)
    nome_regime = codes.REG_ESP_TRIB.get(cfg.reg_esp_trib or "0", cfg.reg_esp_trib)
    if vigentes and (cfg.reg_esp_trib or "0") == "0":
        res.regras.append(
            Regra(
                "regime_especial",
                AVISO,
                "Há regime especial para o serviço",
                f"A prefeitura de {nome_mun} cadastrou regime especial de tributação para o serviço {servico} "
                "(por exemplo, sociedade de profissionais ou profissional autônomo)"
                + (f": “{obs}”" if obs else "")
                + ". Se a empresa é enquadrada em algum, informe o regime em Configurações, Dados da empresa; "
                "vale conferir na legislação municipal.",
            )
        )
    elif vigentes:
        res.regras.append(
            Regra(
                "regime_especial",
                INFO,
                "Regime especial previsto",
                f"A prefeitura de {nome_mun} tem regime especial cadastrado para o serviço {servico}"
                + (f" (“{obs}”)" if obs else "")
                + f". A empresa informa “{nome_regime}”.",
            )
        )
    elif (cfg.reg_esp_trib or "0") != "0" and status == 404:
        res.regras.append(
            Regra(
                "regime_especial",
                AVISO,
                "Regime especial sem cadastro na prefeitura",
                f"A prefeitura de {nome_mun} não cadastrou nenhum regime especial de tributação para o serviço "
                f"{servico}, e a empresa informa “{nome_regime}”. A Sefin pode recusar a nota; vale conferir na "
                "legislação municipal.",
            )
        )


def _beneficio(
    res: Regras, amb: Ambiente, cfg: ConfigFiscal, mun: str, nome_mun: str, comp: date, client, cache
) -> None:
    n_bm = "".join(ch for ch in cfg.n_bm or "" if ch.isdigit())
    if len(n_bm) != 14:
        return  # a coerência da tributação já recusa
    if n_bm[:7] != mun:
        res.regras.append(
            Regra(
                "beneficio",
                AVISO,
                "Benefício de outro município",
                f"O benefício nº {n_bm} começa com o código de outro município ({codes.municipio_uf(n_bm[:7]) or n_bm[:7]}), "
                f"mas o ISSQN deste serviço é de {nome_mun}. A Sefin deve recusar a nota.",
            )
        )
        return
    status, dados = _consultar(amb, f"{mun}/{n_bm}/{comp.isoformat()}/beneficio", client, cache)
    if not isinstance(dados, dict) or status not in (200, 404):
        raise _SemResposta
    bm = dados.get("beneficio") if isinstance(dados.get("beneficio"), dict) else None
    if bm is None:
        if status == 404:
            res.regras.append(
                Regra(
                    "beneficio",
                    AVISO,
                    "Benefício não encontrado",
                    f"A prefeitura de {nome_mun} não tem o benefício nº {n_bm} cadastrado para {res.competencia}. "
                    "Confira o número com a prefeitura: a Sefin deve recusar a nota.",
                )
            )
        return
    servico = codes.formatar_c_trib_nac(cfg.c_trib_nac)
    desc = " ".join(str(bm.get("descricao") or "").split())
    problemas = []
    if not _vigente(bm, comp):
        problemas.append(f"não vale em {res.competencia}")
    servicos = [s for s in bm.get("servicos") or [] if isinstance(s, dict)]
    if servicos and not any(
        _mesmo_servico(s.get("codigoServico"), cfg.c_trib_nac) and _vigente(s, comp) for s in servicos
    ):
        problemas.append(f"não inclui o serviço {servico}")
    contribuintes = [c for c in bm.get("contribuintes") or [] if isinstance(c, dict)]
    if contribuintes and not any(
        str(c.get("tipoInscricao")) == "0"
        or ("".join(ch for ch in str(c.get("inscricao") or "") if ch.isalnum()).upper() == cfg.cnpj.upper()
            and _vigente(c, comp))
        for c in contribuintes
    ):  # fmt: skip
        problemas.append("é só para empresas cadastradas pela prefeitura, e o CNPJ desta não está na lista")
    reducao = _pct(bm.get("reducaoPercentualBC")) if bm.get("reducaoPercentualBC") is not None else None
    if reducao and cfg.p_red_bcbm and _pct(cfg.p_red_bcbm) != reducao:
        problemas.append(
            f"reduz a base em {_pct_tela(reducao)}%, e a tributação informa {_pct_tela(_pct(cfg.p_red_bcbm))}%"
        )
    if bm.get("aliquotaDiferenciada") is not None:
        efeito = f"alíquota diferenciada de {_pct_tela(_pct(bm.get('aliquotaDiferenciada')))}%"
    elif reducao:
        efeito = f"redução de {_pct_tela(reducao)}% da base de cálculo"
    else:
        efeito = None
    sobre = f"“{desc}”" + (f" ({efeito})" if efeito else "") if desc else (efeito or "")
    if problemas:
        res.regras.append(
            Regra(
                "beneficio",
                AVISO,
                "Benefício municipal não confere",
                f"O benefício nº {n_bm} da prefeitura de {nome_mun}"
                + (f", {sobre}," if sobre else "")
                + f" {'; '.join(problemas)}. A Sefin pode recusar a nota; confira com a prefeitura.",
            )
        )
    else:
        res.regras.append(
            Regra(
                "beneficio",
                INFO,
                "Benefício municipal cadastrado",
                f"O benefício nº {n_bm} está cadastrado pela prefeitura de {nome_mun}"
                + (f": {sobre}" if sobre else "")
                + ".",
            )
        )


# --- validação antes de emitir -------------------------------------------------------------------------


def avisos(ambiente: Ambiente, lista: list[tuple[str, ConfigFiscal]], ate: float) -> list[dict[str, str]]:
    """Regras de nível "aviso" de cada tributação (``(origem, cfg)``), para o diálogo de emissão. Não pedem ciência."""
    res: list[dict[str, str]] = []
    vistos: set[str] = set()
    for origem, cfg in lista:
        if time.monotonic() > ate:
            break
        for r in conferir(ambiente, cfg, ate=ate).regras:
            if r.nivel == AVISO and r.mensagem not in vistos:
                vistos.add(r.mensagem)
                res.append(r.as_dict() | {"origem": origem})
    return res
