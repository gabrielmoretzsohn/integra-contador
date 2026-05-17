"""
servicos_simples.py - Integra-PGDAS (Simples Nacional)
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from .cliente_api import ClienteIntegra, IntegradorAPIError

logger = logging.getLogger("integra_contador.simples")


def consultar_declaracoes_pgdas(
    cliente: ClienteIntegra,
    cnpj_escritorio: str,
    numero_contribuinte: str,
    tipo_contribuinte: int,
    ano: str,
    mes: str = None,
) -> Dict:
    logger.info(f"[SIMPLES] Consultando: {numero_contribuinte} - {mes}/{ano}")

    if mes:
        periodo_apuracao = f"{ano}{mes.zfill(2)}"
        dados_entrada = json.dumps({"periodoApuracao": periodo_apuracao})
        periodo_label = f"{mes.zfill(2)}/{ano}"
    else:
        dados_entrada = json.dumps({"anoCalendario": ano})
        periodo_label = ano

    try:
        resp = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=tipo_contribuinte,
            id_sistema="PGDASD",
            id_servico="CONSDECLARACAO13",
            versao_sistema="1.0",
            dados=dados_entrada,
        )

        # Log COMPLETO para diagnostico
        logger.info(f"[SIMPLES] ===== RESPOSTA BRUTA =====")
        logger.info(f"[SIMPLES] {json.dumps(resp, ensure_ascii=False, default=str)}")
        logger.info(f"[SIMPLES] ===========================")

        # Extrai o campo dados (JSON string dentro da resposta)
        dados_raw = resp.get("dados", "")
        logger.info(f"[SIMPLES] campo dados raw: {repr(dados_raw[:500]) if dados_raw else 'VAZIO'}")

        declaracoes = _extrair_declaracoes(resp, dados_raw)
        transmitida = len(declaracoes) > 0

        logger.info(f"[SIMPLES] declaracoes extraidas: {len(declaracoes)} | transmitida: {transmitida}")
        if declaracoes:
            logger.info(f"[SIMPLES] primeira declaracao: {declaracoes[0]}")

        ultimo_periodo = ""
        ultimo_recibo  = ""
        ultima_data    = ""
        if declaracoes:
            d = declaracoes[-1]
            ultimo_periodo = str(d.get("periodoApuracao") or d.get("periodo") or "")
            ultimo_recibo  = str(d.get("recibo") or d.get("numeroDeclaracao") or "")
            ultima_data    = str(d.get("dataHoraTransmissao") or d.get("dataTransmissao") or "")

        return {
            "sucesso":        True,
            "contribuinte":   numero_contribuinte,
            "servico":        "Simples Nacional (PGDAS-D)",
            "periodo":        periodo_label,
            "transmitida":    transmitida,
            "total":          len(declaracoes),
            "declaracoes":    declaracoes,
            "ultimo_periodo": ultimo_periodo,
            "ultimo_recibo":  ultimo_recibo,
            "ultima_data":    ultima_data,
            "timestamp":      datetime.now().isoformat(),
        }

    except IntegradorAPIError as e:
        erro_str = str(e)
        logger.warning(f"[SIMPLES] IntegradorAPIError: {erro_str[:200]}")
        nao_simples = any(x in erro_str.lower() for x in [
            "nao optante", "nao enquadrado", "nao e simples",
            "nao e optante", "pgdas-001", "contribuinte nao",
        ])
        return {
            "sucesso":      True,
            "contribuinte": numero_contribuinte,
            "servico":      "Simples Nacional (PGDAS-D)",
            "periodo":      periodo_label,
            "transmitida":  False,
            "total":        0,
            "declaracoes":  [],
            "nao_simples":  nao_simples,
            "erro":         "" if nao_simples else erro_str[:150],
            "timestamp":    datetime.now().isoformat(),
        }


def _extrair_declaracoes(resp: dict, dados_raw: str) -> List[dict]:
    """
    Tenta todas as estruturas possiveis da resposta PGDAS.
    Loga cada tentativa para facilitar diagnostico.
    """

    # ── Tenta parsear o campo dados ──────────────────────────────────────────
    dados_obj = None
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            logger.info(f"[SIMPLES] dados_obj type: {type(dados_obj).__name__}")
            if isinstance(dados_obj, dict):
                logger.info(f"[SIMPLES] chaves no dados_obj: {list(dados_obj.keys())}")
        except Exception as e:
            logger.warning(f"[SIMPLES] Erro ao parsear dados: {e}")

    # ── Busca em todos os lugares possiveis ───────────────────────────────────
    # Tenta todas as variacoes de case conhecidas
    candidatos_chave = [
        "DeclaracoesEntregues",
        "declaracoesEntregues",
        "declaracoes_entregues",
        "DECLARACOESENTREGUES",
        "declaracoes",
        "listaDeclaracoes",
        "ListaDeclaracoes",
        "conteudo",
        "Conteudo",
        "itens",
        "Itens",
        "items",
    ]

    # Busca no dados_obj
    if isinstance(dados_obj, dict):
        for chave in candidatos_chave:
            val = dados_obj.get(chave)
            if isinstance(val, list) and val:
                logger.info(f"[SIMPLES] Encontrou lista em dados_obj['{chave}'] com {len(val)} itens")
                return _processar_lista(val)

        # Objeto unico com periodoApuracao
        if any(k in dados_obj for k in ["periodoApuracao", "recibo", "numeroDeclaracao", "Periodo"]):
            logger.info(f"[SIMPLES] dados_obj parece ser declaracao unica")
            return [_normalizar_declaracao(dados_obj)]

        # Busca recursiva por qualquer lista dentro do dados_obj
        lista = _buscar_lista_recursiva(dados_obj)
        if lista:
            logger.info(f"[SIMPLES] Encontrou lista recursivamente com {len(lista)} itens")
            return _processar_lista(lista)

    # Tenta dados_obj como lista direta
    if isinstance(dados_obj, list) and dados_obj:
        logger.info(f"[SIMPLES] dados_obj e lista com {len(dados_obj)} itens")
        return _processar_lista(dados_obj)

    # Busca diretamente na raiz da resposta
    for chave in candidatos_chave:
        val = resp.get(chave)
        if isinstance(val, list) and val:
            logger.info(f"[SIMPLES] Encontrou lista na raiz resp['{chave}']")
            return _processar_lista(val)

    logger.warning(f"[SIMPLES] Nenhuma declaracao encontrada. "
                   f"dados_obj: {type(dados_obj).__name__ if dados_obj is not None else 'None'}")
    return []


def _processar_lista(lista: list) -> List[dict]:
    """Processa uma lista de declaracoes em qualquer formato."""
    resultado = []
    for item in lista:
        if not isinstance(item, dict):
            continue

        # Estrutura confirmada: {Periodo: {...}, Operacao: [{IndiceDeclaracao: {...}}]}
        if "Periodo" in item or "Operacao" in item:
            periodo_obj = item.get("Periodo", item.get("periodo", {}))
            pa = ""
            if isinstance(periodo_obj, dict):
                pa = (periodo_obj.get("periodoApuracao") or
                      periodo_obj.get("PA") or
                      periodo_obj.get("pa") or "")

            operacoes = item.get("Operacao", item.get("operacao", [item]))
            if not isinstance(operacoes, list):
                operacoes = [operacoes]

            for op in operacoes:
                if not isinstance(op, dict):
                    continue
                indice = op.get("IndiceDeclaracao", op.get("indiceDeclaracao", op))
                if not isinstance(indice, dict):
                    continue
                decl = _normalizar_declaracao(indice)
                if not decl.get("periodoApuracao") and pa:
                    decl["periodoApuracao"] = pa
                resultado.append(decl)
        else:
            # Item ja e uma declaracao direta
            decl = _normalizar_declaracao(item)
            if decl.get("periodoApuracao") or decl.get("recibo") or decl.get("numeroDeclaracao"):
                resultado.append(decl)

    return resultado


def _normalizar_declaracao(d: dict) -> dict:
    return {
        "periodoApuracao":     (d.get("periodoApuracao") or d.get("PA") or d.get("pa") or
                                d.get("periodo") or ""),
        "numeroDeclaracao":    (d.get("numeroDeclaracao") or d.get("numero") or
                                d.get("NumeroDeclaracao") or ""),
        "recibo":              (d.get("recibo") or d.get("numeroRecibo") or
                                d.get("Recibo") or d.get("NumeroRecibo") or ""),
        "dataHoraTransmissao": (d.get("dataHoraTransmissao") or d.get("dataTransmissao") or
                                d.get("DataHoraTransmissao") or d.get("DataTransmissao") or ""),
        "tipoOperacao":        (d.get("tipoOperacao") or d.get("tipo") or d.get("TipoOperacao") or ""),
    }


def _buscar_lista_recursiva(obj, profundidade=0) -> Optional[list]:
    """Busca recursivamente a primeira lista com conteudo dict."""
    if profundidade > 5:
        return None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = _buscar_lista_recursiva(v, profundidade + 1)
            if r:
                return r
    return None


def _buscar_campo_recursivo(d, campo):
    if isinstance(d, dict):
        if campo in d:
            return d[campo]
        for v in d.values():
            r = _buscar_campo_recursivo(v, campo)
            if r is not None:
                return r
    return None
