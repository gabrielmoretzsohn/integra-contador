"""
servicos.py - Servicos Integra Contador

Fluxo correto do SITFIS (documentacao SERPRO v2.0):
  1. POST /Apoiar SOLICITARPROTOCOLO91  versao 2.0  contribuinte tipo 1 (CPF)
     - Resposta 200: protocolo no campo dados/protocoloRelatorio
     - Resposta 304: protocolo no header ETag (ainda valido, reutilizar)
  2. POST /Emitir RELATORIOSITFIS92     versao 2.0  com protocoloRelatorio
     - Resposta 202: em processamento, aguardar tempoEspera ms e tentar novamente
     - Resposta 200: PDF em base64 no campo dados
     - Resposta 204: processando (aguardar e tentar novamente)

IMPORTANTE: contribuinte TIPO 1 (CPF 11 digitos) obrigatorio para SITFIS
"""

import base64
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .cliente_api import ClienteIntegra, IntegradorAPIError

logger = logging.getLogger("integra_contador.servicos")

MAX_TENTATIVAS_EMISSAO = 8   # maximo de tentativas no loop de emissao
ESPERA_PADRAO_MS = 5000      # 5 segundos se API nao informar tempoEspera


# ─────────────────────────────────────────────────────────────────────────────
#  SITFIS — Diagnostico Fiscal
# ─────────────────────────────────────────────────────────────────────────────

def consultar_diagnostico_fiscal(
    cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte,
    cpf_responsavel="", diretorio_saida="relatorios"
):
    """
    Diagnostico Fiscal completo.
    O SITFIS exige CPF (tipo 1). Se for CNPJ (tipo 2), usa o CNPJ mesmo
    pois a doc diz que aceita PJ tambem.
    """
    logger.info(f"[SITFIS] Iniciando: {numero_contribuinte} (tipo {tipo_contribuinte})")

    # Passo 1: Solicitar protocolo
    protocolo = _solicitar_protocolo_sitfis(
        cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte
    )
    if not protocolo:
        return {
            "sucesso": False, "contribuinte": numero_contribuinte, "servico": "SITFIS",
            "erro": "Nao foi possivel obter o protocolo de emissao. "
                    "Verifique se a procuracao do SITFIS esta ativa no e-CAC."
        }

    logger.info(f"[SITFIS] Protocolo obtido: {protocolo[:30]}...")

    # Passo 2: Emitir relatorio com loop de espera
    return _emitir_com_loop(
        cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte,
        protocolo, "SITFIS", "RELATORIOSITFIS92", diretorio_saida, "sitfis"
    )


def _solicitar_protocolo_sitfis(cliente, cnpj_escritorio, numero, tipo):
    """Solicita o protocolo SITFIS. Trata HTTP 304 (protocolo em cache via header)."""
    try:
        resp = cliente.chamar(
            acao="Apoiar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero,
            tipo_contribuinte=tipo,
            id_sistema="SITFIS",
            id_servico="SOLICITARPROTOCOLO91",
            versao_sistema="2.0",
            dados="",
        )
        
        # 304: protocolo ainda valido — tenta recuperar do header ETag
        if resp.get("_status_304"):
            protocolo_header = resp.get("_protocolo_header", "")
            headers = resp.get("_headers", {})
            logger.info(f"[SITFIS] 304 headers: {headers}")

            if protocolo_header:
                logger.info(f"[SITFIS] Protocolo recuperado do header: {protocolo_header[:30]}")
                return protocolo_header

            # Se nao veio no header, log todos os headers para diagnostico
            logger.warning(
                f"[SITFIS] 304 sem protocolo no header. Headers disponíveis: "
                f"{list(headers.keys())}. "
                f"Nao e possivel obter o relatorio agora — a SERPRO retorna 304 "
                f"porque ja existe um protocolo valido para este CNPJ hoje, mas "
                f"o protocolo nao esta acessivel via header. "
                f"Tente novamente amanha ou verifique se o plano contratado inclui SITFIS."
            )
            return None

        return _extrair_protocolo(resp)

    except IntegradorAPIError as e:
        logger.error(f"[SITFIS] Erro ao solicitar protocolo: {e}")
        return None


def _emitir_com_loop(
    cliente, cnpj_escritorio, numero, tipo,
    protocolo, id_sistema, id_servico, diretorio, prefixo
):
    """
    Loop de emissao: chama /Emitir e aguarda conforme tempoEspera.
    - 202: em processamento, aguarda tempoEspera e chama novamente
    - 204: aguarda e chama novamente
    - 200: PDF disponivel
    """
    for tentativa in range(1, MAX_TENTATIVAS_EMISSAO + 1):
        logger.info(f"[{id_servico}] Tentativa {tentativa}/{MAX_TENTATIVAS_EMISSAO} de emissao...")
        try:
            resp = cliente.chamar(
                acao="Emitir",
                cnpj_contratante=cnpj_escritorio,
                cnpj_autor=cnpj_escritorio,
                numero_contribuinte=numero,
                tipo_contribuinte=tipo,
                id_sistema=id_sistema,
                id_servico=id_servico,
                versao_sistema="2.0",
                dados={"protocoloRelatorio": protocolo},
            )
            logger.info(f"[{id_servico}] Resposta emissao: {str(resp)[:500]}")

            status_http = resp.get("status", 200)

            # 202: em processamento
            if str(status_http) == "202":
                tempo_ms = _extrair_tempo_espera(resp)
                logger.info(f"[{id_servico}] Em processamento. Aguardando {tempo_ms}ms...")
                time.sleep(tempo_ms / 1000)
                continue

            # 204: aguardar e tentar novamente
            if str(status_http) == "204":
                logger.info(f"[{id_servico}] Status 204 - aguardando 5s...")
                time.sleep(5)
                continue

            # 200: sucesso, extrair PDF e analisar situacao fiscal
            caminho = _salvar_pdf_resposta(resp, diretorio, numero, prefixo)

            resultado = {
                "sucesso": True, "contribuinte": numero,
                "servico": id_servico, "protocolo": protocolo,
                "arquivo_pdf": caminho, "timestamp": datetime.now().isoformat(),
            }

            # Extrai informacao de debito se for relatorio SITFIS
            if id_servico == "RELATORIOSITFIS92":
                sit = _extrair_situacao_fiscal(resp)
                resultado["situacao_fiscal"]  = sit["situacao"]
                resultado["possui_debito"]    = sit["possui_debito"]
                resultado["descricao_debito"] = sit["descricao"]

            return resultado

        except IntegradorAPIError as e:
            logger.error(f"[{id_servico}] Erro na emissao: {e}")
            return {"sucesso": False, "contribuinte": numero,
                    "servico": id_servico, "erro": str(e)}

    return {
        "sucesso": False, "contribuinte": numero, "servico": id_servico,
        "erro": f"Timeout apos {MAX_TENTATIVAS_EMISSAO} tentativas de emissao."
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SITFIS — CND Federal
# ─────────────────────────────────────────────────────────────────────────────

def consultar_cnd_federal(
    cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte,
    cpf_responsavel="", diretorio_saida="relatorios"
):
    """CND Federal — usa o mesmo protocolo do SITFIS."""
    logger.info(f"[CND] Iniciando: {numero_contribuinte}")

    protocolo = _solicitar_protocolo_sitfis(
        cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte
    )
    if not protocolo:
        return {
            "sucesso": False, "contribuinte": numero_contribuinte,
            "servico": "CND Federal",
            "erro": "Nao foi possivel obter protocolo para CND. "
                    "Mesmo problema do SITFIS — verifique procuracao no e-CAC."
        }

    # CND usa o mesmo servico RELATORIOSITFIS92 — o relatorio SITFIS ja contem
    # as informacoes de debito/certidao (nao existe servico separado CERTIDAOSITFIS93)
    result = _emitir_com_loop(
        cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte,
        protocolo, "SITFIS", "RELATORIOSITFIS92", diretorio_saida, "cnd"
    )
    result["servico"] = "CND Federal"
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  CAIXA POSTAL
# ─────────────────────────────────────────────────────────────────────────────

def ler_mensagens_nao_lidas_pdf(
    cliente,
    cnpj_escritorio: str,
    numero_contribuinte: str,
    tipo_contribuinte: int,
    diretorio_saida: str = "relatorios",
) -> Dict:
    """
    Le o conteudo das mensagens NAO LIDAS da caixa postal e gera um PDF consolidado.
    Fluxo:
      1. Lista mensagens nao lidas (statusLeitura=1)
      2. Para cada mensagem, le o conteudo completo (OBTERMSGRFB62)
      3. Gera um PDF com todas as mensagens nao lidas
    """
    logger.info(f"[CAIXA POSTAL] Buscando mensagens nao lidas: {numero_contribuinte}")

    # Passo 1: listar nao lidas
    resultado_lista = listar_mensagens_caixa_postal(
        cliente, cnpj_escritorio, numero_contribuinte,
        tipo_contribuinte, apenas_nao_lidas=True
    )

    if not resultado_lista.get("sucesso"):
        return resultado_lista

    mensagens = resultado_lista.get("mensagens", [])
    if not mensagens:
        return {
            "sucesso": True,
            "contribuinte": numero_contribuinte,
            "servico": "Caixa Postal - Mensagens Nao Lidas",
            "total_nao_lidas": 0,
            "mensagens_completas": [],
            "arquivo_pdf": None,
            "timestamp": datetime.now().isoformat(),
        }

    logger.info(f"[CAIXA POSTAL] {len(mensagens)} mensagem(ns) nao lida(s). Lendo conteudo...")

    # Passo 2: ler conteudo de cada mensagem
    mensagens_completas = []
    for msg in mensagens[:20]:  # limite de 20 para nao sobrecarregar
        id_msg = (
            msg.get("idMensagem") or msg.get("id") or
            msg.get("codigoMensagem") or msg.get("codigo")
        )
        if not id_msg:
            mensagens_completas.append(msg)
            continue

        try:
            resp_leitura = cliente.chamar(
                acao="Consultar",
                cnpj_contratante=cnpj_escritorio,
                cnpj_autor=cnpj_escritorio,
                numero_contribuinte=numero_contribuinte,
                tipo_contribuinte=tipo_contribuinte,
                id_sistema="CAIXAPOSTAL",
                id_servico="OBTERMSGRFB62",
                versao_sistema="1.0",
                dados=json.dumps({"idMensagem": str(id_msg)}),
            )
            # Extrai conteudo da mensagem
            conteudo = _extrair_conteudo_mensagem(resp_leitura)
            msg_completa = {**msg, **conteudo, "id_mensagem": id_msg}
            mensagens_completas.append(msg_completa)
            logger.info(f"[CAIXA POSTAL] Mensagem {id_msg} lida com sucesso.")
        except IntegradorAPIError as e:
            logger.warning(f"[CAIXA POSTAL] Erro ao ler mensagem {id_msg}: {e}")
            mensagens_completas.append({**msg, "erro_leitura": str(e)[:100]})

        import time as _time
        _time.sleep(0.5)

    # Passo 3: gerar PDF com as mensagens
    caminho_pdf = _gerar_pdf_mensagens(
        mensagens_completas, diretorio_saida,
        numero_contribuinte, "caixa_postal_nao_lidas"
    )

    return {
        "sucesso": True,
        "contribuinte": numero_contribuinte,
        "servico": "Caixa Postal - Mensagens Nao Lidas",
        "total_nao_lidas": len(mensagens),
        "mensagens_completas": mensagens_completas,
        "arquivo_pdf": caminho_pdf,
        "timestamp": datetime.now().isoformat(),
    }


def _extrair_conteudo_mensagem(resp: dict) -> dict:
    """Extrai campos de conteudo de uma mensagem da caixa postal."""
    dados_raw = resp.get("dados", "")
    dados_obj = {}
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            if isinstance(dados_obj, dict):
                conteudo_lista = dados_obj.get("conteudo", [])
                if isinstance(conteudo_lista, list) and conteudo_lista:
                    dados_obj = conteudo_lista[0]
        except Exception:
            pass

    return {
        "assunto":        dados_obj.get("assunto")         or dados_obj.get("descricaoAssunto") or "",
        "texto":          dados_obj.get("texto")           or dados_obj.get("mensagem")         or dados_obj.get("corpo") or "",
        "data_envio":     dados_obj.get("dataHoraEnvio")   or dados_obj.get("dataEnvio")        or "",
        "remetente":      dados_obj.get("remetente")       or dados_obj.get("nomeRemetente")    or "Receita Federal",
        "situacao_leitura": dados_obj.get("situacaoLeitura") or dados_obj.get("statusLeitura")  or "nao_lida",
    }


def _gerar_pdf_mensagens(mensagens: list, diretorio: str, numero: str, prefixo: str) -> Optional[str]:
    """Gera PDF com o conteudo das mensagens da caixa postal."""
    Path(diretorio).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_base = f"{prefixo}_{numero}_{ts}"

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm

        caminho = Path(diretorio) / f"{nome_base}.pdf"
        doc = SimpleDocTemplate(str(caminho), pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)
        styles = getSampleStyleSheet()
        azul   = colors.HexColor("#1a3a6c")

        titulo_st = ParagraphStyle("titulo", parent=styles["Heading1"],
                                   fontSize=14, textColor=azul)
        subtit_st = ParagraphStyle("subtit", parent=styles["Heading2"],
                                   fontSize=11, textColor=azul, spaceBefore=12)
        corpo_st  = ParagraphStyle("corpo",  parent=styles["Normal"],
                                   fontSize=9, leading=14)
        meta_st   = ParagraphStyle("meta",   parent=styles["Normal"],
                                   fontSize=8, textColor=colors.grey)

        story = []
        story.append(Paragraph("Caixa Postal RFB — Mensagens Nao Lidas", titulo_st))
        story.append(Paragraph(
            f"CNPJ: {numero} &nbsp;|&nbsp; "
            f"Total: {len(mensagens)} mensagem(ns) &nbsp;|&nbsp; "
            f"Gerado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            meta_st
        ))
        story.append(Spacer(1, 0.5*cm))
        story.append(HRFlowable(width="100%", thickness=1, color=azul))
        story.append(Spacer(1, 0.3*cm))

        for i, msg in enumerate(mensagens, 1):
            assunto  = msg.get("assunto")    or msg.get("descricaoAssunto") or msg.get("assuntoModelo") or "Sem assunto"
            data     = msg.get("data_envio") or msg.get("dataHoraEnvio")    or msg.get("dataEnvio")     or ""
            remetente = msg.get("remetente") or "Receita Federal"
            texto    = msg.get("texto")      or msg.get("mensagem")         or msg.get("corpo")         or ""
            erro     = msg.get("erro_leitura", "")

            story.append(Paragraph(f"Mensagem {i}: {assunto}", subtit_st))

            meta_info = []
            if data:
                meta_info.append(f"Data: {data}")
            if remetente:
                meta_info.append(f"Remetente: {remetente}")
            if meta_info:
                story.append(Paragraph(" &nbsp;|&nbsp; ".join(meta_info), meta_st))

            story.append(Spacer(1, 0.2*cm))

            if erro:
                story.append(Paragraph(f"[Erro ao carregar conteudo: {erro}]", meta_st))
            elif texto:
                # Quebra texto longo em paragrafos
                for linha in texto.replace("\r\n", "\n").split("\n"):
                    linha = linha.strip()
                    if linha:
                        story.append(Paragraph(linha, corpo_st))
                    else:
                        story.append(Spacer(1, 0.1*cm))
            else:
                story.append(Paragraph("[Conteudo nao disponivel — verifique no e-CAC]", meta_st))

            story.append(Spacer(1, 0.3*cm))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                    color=colors.lightgrey, dash=(2, 2)))
            story.append(Spacer(1, 0.3*cm))

        doc.build(story)
        logger.info(f"PDF mensagens nao lidas salvo: {caminho}")
        return str(caminho)

    except ImportError:
        # Fallback HTML
        caminho_html = Path(diretorio) / f"{nome_base}.html"
        linhas = ""
        for i, msg in enumerate(mensagens, 1):
            assunto = msg.get("assunto") or msg.get("descricaoAssunto") or "Sem assunto"
            data    = msg.get("data_envio") or msg.get("dataHoraEnvio") or ""
            texto   = msg.get("texto") or msg.get("mensagem") or "[Sem conteudo]"
            linhas += f"""
            <div class="msg">
              <h3>Mensagem {i}: {assunto}</h3>
              <p class="meta">{data}</p>
              <div class="corpo">{texto.replace(chr(10), "<br>")}</div>
            </div><hr>"""

        html = f"""<!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8">
<title>Caixa Postal Nao Lidas - {numero}</title>
<style>
body{{font-family:Arial,sans-serif;padding:20px;color:#222}}
h2{{color:#1a3a6c}} h3{{color:#1a3a6c;margin-bottom:4px}}
.meta{{color:#666;font-size:12px;margin-bottom:8px}}
.corpo{{font-size:13px;line-height:1.6;background:#f8f8f8;
        padding:12px;border-left:3px solid #1a3a6c;margin-bottom:16px}}
.msg{{margin-bottom:20px}}
</style></head><body>
<h2>Caixa Postal RFB — Mensagens Nao Lidas</h2>
<p><strong>CNPJ:</strong> {numero} | <strong>Total:</strong> {len(mensagens)} |
<strong>Gerado:</strong> {datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
<hr>{linhas}</body></html>"""
        caminho_html.write_text(html, encoding="utf-8")
        return str(caminho_html)
    except Exception as e:
        logger.error(f"Erro ao gerar PDF mensagens: {e}")
        return None


def listar_mensagens_caixa_postal(
    cliente, cnpj_escritorio, numero_contribuinte, tipo_contribuinte,
    apenas_nao_lidas=False, pagina=0
):
    logger.info(f"[CAIXA POSTAL] Listando: {numero_contribuinte}")
    dados = {
        "statusLeitura": "1" if apenas_nao_lidas else "0",
        "indicadorPagina": str(pagina),
        "ponteiroPagina": "00000000000000",
    }
    try:
        resp = cliente.chamar(
            acao="Consultar",
            cnpj_contratante=cnpj_escritorio,
            cnpj_autor=cnpj_escritorio,
            numero_contribuinte=numero_contribuinte,
            tipo_contribuinte=tipo_contribuinte,
            id_sistema="CAIXAPOSTAL",
            id_servico="MSGCONTRIBUINTE61",
            versao_sistema="1.0",
            dados=dados,
        )
        mensagens = _extrair_mensagens(resp)
        logger.info(f"[CAIXA POSTAL] {len(mensagens)} mensagem(ns).")
        return {
            "sucesso": True, "contribuinte": numero_contribuinte,
            "servico": "Caixa Postal RFB",
            "total_mensagens": len(mensagens), "mensagens": mensagens,
            "timestamp": datetime.now().isoformat(),
        }
    except IntegradorAPIError as e:
        logger.error(f"[CAIXA POSTAL] Erro: {e}")
        return {"sucesso": False, "contribuinte": numero_contribuinte,
                "servico": "Caixa Postal", "erro": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def _extrair_situacao_fiscal(resp: dict) -> dict:
    """
    Analisa a resposta do SITFIS para extrair:
    - situacao: "REGULAR" / "IRREGULAR" / "NAO_IDENTIFICADO"
    - possui_debito: True / False / None
    - descricao: texto descritivo

    A API retorna os dados dentro do campo 'dados' como JSON com campos como:
    situacaoFiscal, possuiDebito, descricaoSituacao, totalDebitos, etc.
    """
    resultado = {
        "situacao":    "NAO_IDENTIFICADO",
        "possui_debito": None,
        "descricao":   "",
    }

    # Tenta extrair do campo 'dados' (JSON string)
    dados_raw = resp.get("dados", "")
    dados_obj = {}
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
        except Exception:
            pass

    # Campos diretos conhecidos da API SITFIS
    sit = (
        dados_obj.get("situacaoFiscal")
        or dados_obj.get("situacao")
        or dados_obj.get("descricaoSituacao")
        or _extrair_campo_aninhado(resp, "situacaoFiscal")
        or _extrair_campo_aninhado(resp, "situacao")
        or ""
    )

    tem_debito = (
        dados_obj.get("possuiDebito")
        or dados_obj.get("temDebito")
        or dados_obj.get("debitoTotal")
        or _extrair_campo_aninhado(resp, "possuiDebito")
    )

    total_debitos = (
        dados_obj.get("totalDebitos")
        or dados_obj.get("valorDebitos")
        or dados_obj.get("debitoTotal")
        or _extrair_campo_aninhado(resp, "totalDebitos")
        or ""
    )

    # Interpreta situacao
    sit_upper = str(sit).upper()
    if any(x in sit_upper for x in ["REGULAR", "REGULAR", "NEGATIVA"]):
        resultado["situacao"]     = "REGULAR"
        resultado["possui_debito"] = False
    elif any(x in sit_upper for x in ["IRREGULAR", "PENDENCIA", "DEBITO", "POSITIVA", "PGFN"]):
        resultado["situacao"]     = "IRREGULAR"
        resultado["possui_debito"] = True
    elif sit:
        resultado["situacao"] = sit

    # Força possui_debito se vier explicitamente
    if tem_debito is not None:
        if isinstance(tem_debito, bool):
            resultado["possui_debito"] = tem_debito
        elif str(tem_debito).upper() in ("TRUE", "S", "SIM", "1"):
            resultado["possui_debito"] = True
        elif str(tem_debito).upper() in ("FALSE", "N", "NAO", "0"):
            resultado["possui_debito"] = False

    # Monta descricao
    partes = []
    if sit:
        partes.append(str(sit))
    if total_debitos:
        partes.append(f"Total debitos: R$ {total_debitos}")
    resultado["descricao"] = " | ".join(partes) if partes else ""

    logger.info(f"[SITFIS] Situacao fiscal extraida: {resultado}")
    return resultado


def _extrair_protocolo(resp):
    """Extrai o protocolo de todas as possiveis estruturas de resposta."""
    # Direto na raiz
    for chave in ["protocoloRelatorio", "protocolo", "numeroProtocolo", "nrProtocolo"]:
        val = resp.get(chave)
        if val and len(str(val)) > 5:
            return str(val)

    # Dentro do campo "dados" (JSON string)
    dados_raw = resp.get("dados", "")
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            if isinstance(dados_obj, dict):
                for chave in ["protocoloRelatorio", "protocolo", "numeroProtocolo"]:
                    val = dados_obj.get(chave)
                    if val and len(str(val)) > 5:
                        return str(val)
            elif isinstance(dados_obj, str) and len(dados_obj) > 10:
                return dados_obj
        except Exception:
            if len(dados_raw.strip()) > 10 and not dados_raw.startswith("{"):
                return dados_raw.strip()

    # Busca recursiva
    for chave in ["protocoloRelatorio", "protocolo", "numeroProtocolo"]:
        val = _extrair_campo_aninhado(resp, chave)
        if val and len(str(val)) > 5:
            return str(val)

    return None


def _extrair_tempo_espera(resp):
    """Extrai tempoEspera em ms da resposta 202."""
    dados_raw = resp.get("dados", "")
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            if isinstance(dados_obj, dict):
                te = dados_obj.get("tempoEspera")
                if te:
                    return max(int(te), 1000)
        except Exception:
            pass
    return ESPERA_PADRAO_MS


def _salvar_pdf_resposta(resp, diretorio, numero, prefixo):
    Path(diretorio).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = Path(diretorio) / f"{prefixo}_{numero}_{ts}.pdf"

    # Tenta extrair PDF base64
    pdf_b64 = None
    dados_raw = resp.get("dados", "")
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            if isinstance(dados_obj, dict):
                pdf_b64 = (dados_obj.get("pdf") or dados_obj.get("arquivo")
                           or dados_obj.get("PDFByteArrayBase64")
                           or dados_obj.get("conteudo"))
            elif isinstance(dados_obj, str):
                pdf_b64 = dados_obj
        except Exception:
            pdf_b64 = dados_raw

    if not pdf_b64:
        pdf_b64 = (resp.get("pdf") or resp.get("arquivo")
                   or resp.get("_conteudo_raw")
                   or _extrair_campo_aninhado(resp, "pdf")
                   or _extrair_campo_aninhado(resp, "PDFByteArrayBase64"))

    if pdf_b64:
        try:
            dados_pdf = base64.b64decode(pdf_b64)
            if dados_pdf[:4] == b'%PDF':  # Verifica assinatura PDF
                caminho.write_bytes(dados_pdf)
                logger.info(f"PDF salvo: {caminho}")
                return str(caminho)
        except Exception as e:
            logger.warning(f"Erro ao decodificar PDF: {e}")

    # Fallback: salvar JSON para analise
    caminho_json = caminho.with_suffix(".json")
    caminho_json.write_text(
        json.dumps(resp, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )
    logger.info(f"Resposta salva como JSON (sem PDF): {caminho_json}")
    return str(caminho_json)


def _extrair_campo_aninhado(dados, campo):
    if isinstance(dados, dict):
        if campo in dados:
            return dados[campo]
        for v in dados.values():
            r = _extrair_campo_aninhado(v, campo)
            if r:
                return r
    return None


def _extrair_situacao_certidao(resp):
    for campo in ["situacao", "tipoCertidao", "descricaoSituacao", "statusCertidao"]:
        val = _extrair_campo_aninhado(resp, campo)
        if val:
            return str(val)
    return "Verifique o arquivo gerado"


def _extrair_mensagens(resp):
    dados_raw = resp.get("dados", "")
    if isinstance(dados_raw, str) and dados_raw.strip():
        try:
            dados_obj = json.loads(dados_raw)
            conteudo = dados_obj.get("conteudo", []) if isinstance(dados_obj, dict) else []
            if isinstance(conteudo, list) and conteudo:
                # conteudo pode ter lista de mensagens dentro
                primeiro = conteudo[0] if conteudo else {}
                if isinstance(primeiro, dict):
                    msgs = primeiro.get("mensagens") or primeiro.get("listaMensagens")
                    if isinstance(msgs, list):
                        return msgs
                return conteudo
        except Exception:
            pass
    for campo in ["mensagens", "listaMensagens", "items"]:
        val = resp.get(campo)
        if isinstance(val, list):
            return val
    return []
