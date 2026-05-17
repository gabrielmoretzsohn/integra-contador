"""
servicos_parcelamento.py - Integra-Parcelamento

Documentacao oficial confirmada:
  - PARCMEI: parcelamentos do MEI
    idServico: PEDIDOSPARC203  (lista pedidos)
  - PARCSN:  parcelamentos Simples Nacional
    idServico: LISTPARC163     (lista parcelamentos do contribuinte)

IMPORTANTE: aceita apenas contribuinte tipo 2 (CNPJ/PJ).

O sistema retorna parcelamentos ATIVOS de debitos do Simples Nacional e MEI.
Para debitos de Lucro Presumido/Real (DCTF) o parcelamento e gerido pela PGFN
e nao esta disponivel nesta API.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List

from .cliente_api import ClienteIntegra, IntegradorAPIError

logger = logging.getLogger("integra_contador.parcelamento")


def consultar_parcelamentos(
    cliente: ClienteIntegra,
    cnpj_escritorio: str,
    numero_contribuinte: str,
    tipo_contribuinte: int,
) -> Dict:
    """
    Consulta parcelamentos ativos do contribuinte.
    Tenta PARCSN (Simples Nacional) e PARCMEI em sequencia.
    So funciona para PJ (tipo 2).
    """
    if tipo_contribuinte != 2:
        return {
            "sucesso": True,
            "contribuinte": numero_contribuinte,
            "servico": "Parcelamento",
            "tem_parcelamento": None,
            "total": 0,
            "parcelamentos": [],
            "nota": "Parcelamento disponivel apenas para PJ (CNPJ)",
            "timestamp": datetime.now().isoformat(),
        }

    logger.info(f"[PARCELAMENTO] Consultando: {numero_contribuinte}")

    todos_parcelamentos = []
    erros = []

    # 1. Consulta PARCSN (Simples Nacional)
    try:
        resp_sn = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=2,
            id_sistema="PARCSN",
            id_servico="LISTPARC163",
            versao_sistema="1.0",
            dados="",
        )
        logger.info(f"[PARCELAMENTO-SN] Resposta: {str(resp_sn)[:400]}")
        parcs = _extrair_parcelamentos(resp_sn)
        for p in parcs:
            p["_origem"] = "PARCSN"
        todos_parcelamentos.extend(parcs)
    except IntegradorAPIError as e:
        erro = str(e)
        # 400 com "nao possui" = sem parcelamento, nao e erro real
        if any(x in erro.lower() for x in ["nao possui", "nao encontrado", "404", "inexistente"]):
            logger.info(f"[PARCELAMENTO-SN] Sem parcelamentos SN.")
        else:
            logger.warning(f"[PARCELAMENTO-SN] Erro: {erro[:120]}")
            erros.append(f"SN: {erro[:80]}")

    # 2. Consulta PARCMEI
    try:
        resp_mei = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=2,
            id_sistema="PARCMEI",
            id_servico="PEDIDOSPARC203",
            versao_sistema="1.0",
            dados="",
        )
        logger.info(f"[PARCELAMENTO-MEI] Resposta: {str(resp_mei)[:400]}")
        parcs = _extrair_parcelamentos(resp_mei)
        for p in parcs:
            p["_origem"] = "PARCMEI"
        todos_parcelamentos.extend(parcs)
    except IntegradorAPIError as e:
        erro = str(e)
        if any(x in erro.lower() for x in ["nao possui", "nao encontrado", "404", "inexistente"]):
            logger.info(f"[PARCELAMENTO-MEI] Sem parcelamentos MEI.")
        else:
            logger.warning(f"[PARCELAMENTO-MEI] Erro: {erro[:120]}")
            erros.append(f"MEI: {erro[:80]}")

    tem_parcelamento = len(todos_parcelamentos) > 0

    resultado = {
        "sucesso": True,
        "contribuinte": numero_contribuinte,
        "servico": "Parcelamento",
        "tem_parcelamento": tem_parcelamento,
        "total": len(todos_parcelamentos),
        "parcelamentos": todos_parcelamentos,
        "timestamp": datetime.now().isoformat(),
    }
    if erros and not todos_parcelamentos:
        resultado["sucesso"] = False
        resultado["erro"] = " | ".join(erros)
    return resultado


def _extrair_parcelamentos(resp: dict) -> List[dict]:
    dados_raw = resp.get("dados", "")
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados = json.loads(dados_raw)
            if isinstance(dados, list):
                return dados
            if isinstance(dados, dict):
                for chave in ["parcelamentos", "listaParcelamentos",
                              "pedidos", "listaPedidos", "conteudo"]:
                    val = dados.get(chave)
                    if isinstance(val, list):
                        return val
                # Objeto unico = um parcelamento
                if "numero" in dados or "situacao" in dados:
                    return [dados]
        except Exception:
            pass
    for chave in ["parcelamentos", "listaParcelamentos", "pedidos"]:
        val = resp.get(chave)
        if isinstance(val, list):
            return val
    return []
