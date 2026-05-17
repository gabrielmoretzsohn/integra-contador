"""
servicos_dctfweb.py - Integra-DCTFWeb

COMPORTAMENTO CONFIRMADO DA API:
  O CONSDECCOMPLETA33 retorna um PDF (base64 em PDFByteArrayBase64) quando
  a declaracao foi transmitida. Nao retorna campos JSON estruturados como
  recibo/status separados.

  Logica correta:
    - dados com PDFByteArrayBase64 presente e nao vazio = TRANSMITIDA (salva o PDF)
    - dados null ou vazio = NAO transmitida para este periodo/categoria
    - HTTP 400/404 com codigo especifico = NAO transmitida
    - HTTP 400 outro = erro tecnico

  Categorias mais comuns:
    "GERAL_MENSAL"    = empresas em geral (Lucro Presumido, Real, etc.)
    "SIMPLES_MENSAL"  = empresas do Simples Nacional (mas geralmente usam PGDAS)
    "PF_MENSAL"       = pessoa fisica (carnê-leão)
"""

import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

from .cliente_api import ClienteIntegra, IntegradorAPIError

logger = logging.getLogger("integra_contador.dctfweb")

# Categorias para tentar em sequencia quando nao souber o regime
CATEGORIAS_TENTATIVA = ["GERAL_MENSAL", "SIMPLES_MENSAL"]


def consultar_situacao_dctfweb(
    cliente: ClienteIntegra,
    cnpj_escritorio: str,
    numero_contribuinte: str,
    tipo_contribuinte: int,
    ano: str,
    mes: str,
    categoria: str = None,
    diretorio_saida: str = "relatorios",
) -> Dict:
    """
    Consulta se a DCTFWeb foi transmitida e salva o PDF se encontrado.

    Se categoria nao informada, tenta GERAL_MENSAL e SIMPLES_MENSAL.
    """
    logger.info(f"[DCTFWEB] Consultando {numero_contribuinte} - {mes}/{ano}")

    categorias = [categoria] if categoria else CATEGORIAS_TENTATIVA

    for cat in categorias:
        resultado = _consultar_categoria(
            cliente, cnpj_escritorio, numero_contribuinte,
            tipo_contribuinte, ano, mes, cat, diretorio_saida
        )
        # Se encontrou declaracao ou erro tecnico, para aqui
        if resultado.get("transmitida") is True:
            return resultado
        if resultado.get("_erro_tecnico"):
            resultado.pop("_erro_tecnico", None)
            return resultado

    # Nenhuma categoria encontrou declaracao
    return {
        "sucesso":      True,
        "contribuinte": numero_contribuinte,
        "servico":      "DCTFWeb",
        "periodo":      f"{mes}/{ano}",
        "transmitida":  False,
        "status":       "Nao transmitida para este periodo",
        "arquivo_pdf":  None,
        "timestamp":    datetime.now().isoformat(),
    }


def _consultar_categoria(
    cliente, cnpj_escritorio, numero_contribuinte,
    tipo_contribuinte, ano, mes, categoria, diretorio_saida
) -> Dict:
    dados = json.dumps({
        "categoria": categoria,
        "anoPA":     ano,
        "mesPA":     mes,
    })

    try:
        resp = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=tipo_contribuinte,
            id_sistema="DCTFWEB",
            id_servico="CONSDECCOMPLETA33",
            versao_sistema="1.0",
            dados=dados,
        )

        # A resposta retorna PDF em base64 dentro de dados.PDFByteArrayBase64
        dados_raw = resp.get("dados", "")
        pdf_b64   = None
        recibo    = ""
        status    = ""

        if isinstance(dados_raw, str) and dados_raw.strip():
            try:
                dados_obj = json.loads(dados_raw)
                if isinstance(dados_obj, dict):
                    pdf_b64 = dados_obj.get("PDFByteArrayBase64") or dados_obj.get("pdf")
                    recibo  = dados_obj.get("numeroRecibo") or dados_obj.get("recibo") or ""
                    status  = dados_obj.get("situacao")    or dados_obj.get("status")  or ""
                    # Busca recursiva se nao achou direto
                    if not pdf_b64:
                        pdf_b64 = _campo(dados_obj, "PDFByteArrayBase64")
            except Exception:
                # dados pode ser o proprio base64 do PDF
                if len(dados_raw) > 100:
                    pdf_b64 = dados_raw

        # Busca recursiva na resposta toda
        if not pdf_b64:
            pdf_b64 = _campo(resp, "PDFByteArrayBase64") or _campo(resp, "pdf")

        # Se tem PDF = foi transmitida
        if pdf_b64:
            caminho = _salvar_pdf_dctfweb(
                pdf_b64, diretorio_saida, numero_contribuinte, mes, ano, categoria
            )
            logger.info(f"[DCTFWEB] TRANSMITIDA ({categoria}) | PDF: {caminho}")
            return {
                "sucesso":      True,
                "contribuinte": numero_contribuinte,
                "servico":      "DCTFWeb",
                "periodo":      f"{mes}/{ano}",
                "categoria":    categoria,
                "transmitida":  True,
                "recibo":       recibo,
                "status":       status or "Transmitida",
                "arquivo_pdf":  caminho,
                "timestamp":    datetime.now().isoformat(),
            }

        # dados vazio = declaracao nao encontrada para esta categoria
        logger.info(f"[DCTFWEB] Sem declaracao para categoria {categoria}")
        return {
            "sucesso":      True,
            "contribuinte": numero_contribuinte,
            "transmitida":  False,
            "categoria":    categoria,
        }

    except IntegradorAPIError as e:
        erro_str = str(e)
        nao_encontrada = any(x in erro_str for x in [
            "404", "DCTFW-CNF-001", "nao encontrada", "nao localizada",
            "NAO_ENCONTRADA", "inexistente", "declaracao nao"
        ])
        if nao_encontrada:
            return {"sucesso": True, "transmitida": False, "categoria": categoria}

        # Erro tecnico real
        logger.warning(f"[DCTFWEB] Erro tecnico ({categoria}): {erro_str[:120]}")
        return {
            "sucesso":        False,
            "contribuinte":   numero_contribuinte,
            "servico":        "DCTFWeb",
            "periodo":        f"{mes}/{ano}",
            "transmitida":    None,
            "erro":           erro_str[:150],
            "_erro_tecnico":  True,
            "timestamp":      datetime.now().isoformat(),
        }


def _salvar_pdf_dctfweb(pdf_b64: str, diretorio: str, numero: str,
                         mes: str, ano: str, categoria: str) -> str:
    """Decodifica e salva o PDF da DCTFWeb."""
    try:
        Path(diretorio).mkdir(parents=True, exist_ok=True)
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        cat_tag = categoria.lower().replace("_", "")[:10]
        nome    = f"dctfweb_{numero}_{ano}{mes}_{cat_tag}_{ts}.pdf"
        caminho = Path(diretorio) / nome

        dados_pdf = base64.b64decode(pdf_b64)
        if dados_pdf[:4] == b'%PDF':
            caminho.write_bytes(dados_pdf)
            logger.info(f"PDF DCTFWeb salvo: {caminho}")
            return str(caminho)
        else:
            logger.warning("[DCTFWEB] Conteudo nao e PDF valido")
            return ""
    except Exception as e:
        logger.error(f"[DCTFWEB] Erro ao salvar PDF: {e}")
        return ""


def _campo(d, nome):
    if isinstance(d, dict):
        if nome in d:
            return d[nome]
        for v in d.values():
            r = _campo(v, nome)
            if r:
                return r
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  MIT — Módulo de Inclusão de Tributos (lancado em abril/2025)
#  idSistema: DCTFWEB  (MIT e integrado a DCTFWeb)
#  Servicos:
#    CONSAPUR_MIT36  - Consultar apuração MIT por periodo
#    CONSENC_MIT37   - Consultar situação de encerramento MIT
# ─────────────────────────────────────────────────────────────────────────────

def consultar_mit(
    cliente: ClienteIntegra,
    cnpj_escritorio: str,
    numero_contribuinte: str,
    tipo_contribuinte: int,
    ano: str,
    mes: str,
    diretorio_saida: str = "relatorios",
) -> Dict:
    """
    Consulta o MIT (Módulo de Inclusão de Tributos) para um período.
    O MIT substitui a DCTF-Fazendária (PGD) desde 2025.
    Retorna situação da apuração e se foi encerrada/transmitida.
    """
    logger.info(f"[MIT] Consultando {numero_contribuinte} - {mes}/{ano}")

    resultado = {
        "sucesso":      False,
        "contribuinte": numero_contribuinte,
        "servico":      "MIT",
        "periodo":      f"{mes}/{ano}",
        "transmitida":  False,
        "situacao":     "",
        "arquivo_pdf":  None,
        "timestamp":    datetime.now().isoformat(),
    }

    dados = json.dumps({"anoPA": ano, "mesPA": mes})

    # Passo 1: consultar apuracao MIT
    try:
        resp_apur = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=tipo_contribuinte,
            id_sistema="DCTFWEB",
            id_servico="CONSAPUR_MIT36",
            versao_sistema="1.0",
            dados=dados,
        )
        logger.info(f"[MIT] Resposta apuracao: {str(resp_apur)[:500]}")

        dados_raw = resp_apur.get("dados", "")
        situacao = ""
        encerrada = False

        if isinstance(dados_raw, str) and dados_raw.strip():
            try:
                dados_obj = json.loads(dados_raw)
                situacao  = (dados_obj.get("situacao") or dados_obj.get("status") or
                             dados_obj.get("situacaoApuracao") or "")
                encerrada = str(situacao).upper() in ("ENCERRADA", "TRANSMITIDA", "ATIVA", "ENTREGUE")
                if not encerrada:
                    encerrada = dados_obj.get("encerrada") or dados_obj.get("transmitida") or False
            except Exception:
                pass

        # PDF indica transmissao
        pdf_b64 = _campo(resp_apur, "PDFByteArrayBase64") or _campo(resp_apur, "pdf")
        caminho = None
        if pdf_b64:
            caminho  = _salvar_pdf_dctfweb(pdf_b64, diretorio_saida, numero_contribuinte,
                                            mes, ano, "mit")
            encerrada = True

        resultado.update({
            "sucesso":     True,
            "transmitida": encerrada,
            "situacao":    situacao or ("Encerrada/Transmitida" if encerrada else "Em andamento"),
            "arquivo_pdf": caminho,
        })

    except IntegradorAPIError as e:
        erro = str(e)
        nao_encontrado = any(x in erro for x in ["404", "nao encontr", "MIT-CNF", "inexist"])
        resultado.update({
            "sucesso":     True,
            "transmitida": False,
            "situacao":    "Nao encontrado para este periodo" if nao_encontrado else f"Erro: {erro[:80]}",
            "erro":        "" if nao_encontrado else erro[:150],
        })

    return resultado
