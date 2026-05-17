"""
servicos_receitabx.py - Integracao com o servico ReceitanetBX (SPED)

ARQUITETURA DA API RECEITABX:
  O ReceitanetBX e um servico Windows/Linux instalado localmente na maquina
  do contador. Ele expoe uma API REST LOCAL (localhost) — nao e uma API web remota.

  A API possui 4 endpoints locais:
    - Pesquisar Arquivos:  lista arquivos disponiveis no servidor da RFB
    - Solicitar Arquivos:  solicita download de arquivos especificos
    - Situacao dos Pedidos: verifica status dos pedidos de download
    - Consultar Pedidos:  lista pedidos existentes

  IMPORTANTE: Esta API SO FUNCIONA se o servico ReceitanetBX estiver instalado
  e rodando no computador local. A porta padrao e configurada no instalador
  (tipicamente 3333 ou 8080).

  O servico autentica com certificado digital e-CNPJ e se conecta ao servidor
  da Receita Federal para buscar os arquivos SPED transmitidos.

SISTEMAS SPED SUPORTADOS:
  - "Sped"         = EFD ICMS/IPI (SPED Fiscal)
  - "SpedPisCofins" = EFD Contribuicoes (PIS/COFINS)
  - "SpedEcd"      = ECD (Escrituracao Contabil Digital)
  - "SpedEcf"      = ECF (Escrituracao Contabil Fiscal)
  - "SpedEsocial"  = eSocial (se disponivel)

TIPOS DE ARQUIVO SPED:
  - "documento"    = arquivo EFD transmitido
  - "recibo"       = recibo de transmissao
"""

import json
import logging
import requests
from datetime import datetime, date
from typing import Dict, List, Optional

logger = logging.getLogger("integra_contador.receitabx")

# Porta padrao do servico local ReceitanetBX
# Pode ser alterada nas configuracoes se o usuario instalou em porta diferente
RECEITABX_BASE_URL = "http://localhost:3333"

# Mapeamento de sistemas SPED
SISTEMAS_SPED = {
    "efd_icms":        {"sistema": "Sped",          "descricao": "EFD ICMS/IPI (SPED Fiscal)"},
    "efd_contribuicoes": {"sistema": "SpedPisCofins", "descricao": "EFD Contribuicoes (PIS/COFINS)"},
    "ecd":             {"sistema": "SpedEcd",        "descricao": "ECD - Escrituracao Contabil Digital"},
    "ecf":             {"sistema": "SpedEcf",        "descricao": "ECF - Escrituracao Contabil Fiscal"},
}


class ReceitaBXCliente:
    """
    Cliente para a API local do servico ReceitanetBX.
    Requer que o servico esteja instalado e rodando na maquina local.
    """

    def __init__(self, base_url: str = RECEITABX_BASE_URL, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

    def verificar_servico(self) -> bool:
        """Verifica se o servico ReceitanetBX esta rodando."""
        try:
            resp = requests.get(f"{self.base_url}/status", timeout=5)
            return resp.status_code in (200, 404)  # 404 = rodando mas endpoint nao existe
        except Exception:
            return False

    def pesquisar_arquivos(
        self,
        cnpj_contribuinte: str,
        sistema: str,
        tipo_arquivo: str = "documento",
        data_inicio: str = "",
        data_fim: str = "",
    ) -> Dict:
        """
        Pesquisa arquivos SPED disponiveis para um contribuinte.

        Args:
            cnpj_contribuinte: CNPJ sem formatacao
            sistema: "Sped", "SpedPisCofins", "SpedEcd", "SpedEcf"
            tipo_arquivo: "documento" (EFD) ou "recibo"
            data_inicio: "YYYY-MM-DD"
            data_fim:    "YYYY-MM-DD"

        Returns:
            Lista de arquivos disponiveis no servidor RFB
        """
        payload = {
            "niSolicitante":    cnpj_contribuinte,
            "tipoNiSolicitante": "cnpj",
            "sistema":          sistema,
            "tipoArquivo":      tipo_arquivo,
        }
        if data_inicio:
            payload["dataInicio"] = data_inicio
        if data_fim:
            payload["dataFim"] = data_fim

        try:
            resp = requests.post(
                f"{self.base_url}/pesquisar",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            return {"sucesso": True, "arquivos": resp.json()}
        except requests.exceptions.ConnectionError:
            return {
                "sucesso": False,
                "erro": "Servico ReceitanetBX nao encontrado. "
                        "Verifique se o servico esta instalado e rodando.",
                "nao_instalado": True,
            }
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    def solicitar_arquivos(
        self,
        cnpj_contribuinte: str,
        sistema: str,
        tipo_arquivo: str = "documento",
        data_inicio: str = "",
        data_fim: str = "",
    ) -> Dict:
        """Solicita o download dos arquivos SPED do servidor RFB."""
        payload = {
            "niSolicitante":    cnpj_contribuinte,
            "tipoNiSolicitante": "cnpj",
            "sistema":          sistema,
            "tipoArquivo":      tipo_arquivo,
        }
        if data_inicio:
            payload["dataInicio"] = data_inicio
        if data_fim:
            payload["dataFim"] = data_fim

        try:
            resp = requests.post(
                f"{self.base_url}/solicitar",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            dados = resp.json()
            return {
                "sucesso":   True,
                "id_pedido": dados.get("idpedido") or dados.get("id"),
                "situacao":  dados.get("situacao", "processando"),
                "dados":     dados,
            }
        except requests.exceptions.ConnectionError:
            return {
                "sucesso": False,
                "erro": "Servico ReceitanetBX nao encontrado.",
                "nao_instalado": True,
            }
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    def consultar_pedidos(self, cnpj_contribuinte: str = "") -> Dict:
        """Lista todos os pedidos de download (com situacao)."""
        try:
            params = {}
            if cnpj_contribuinte:
                params["niSolicitante"] = cnpj_contribuinte
            resp = requests.get(
                f"{self.base_url}/pedidos",
                params=params,
                timeout=self.timeout
            )
            resp.raise_for_status()
            pedidos = resp.json()
            return {"sucesso": True, "pedidos": pedidos if isinstance(pedidos, list) else [pedidos]}
        except requests.exceptions.ConnectionError:
            return {"sucesso": False, "erro": "Servico ReceitanetBX nao encontrado.", "nao_instalado": True}
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    def situacao_pedido(self, id_pedido: int) -> Dict:
        """Verifica situacao de um pedido especifico."""
        try:
            resp = requests.get(f"{self.base_url}/pedidos/{id_pedido}", timeout=self.timeout)
            resp.raise_for_status()
            dados = resp.json()
            return {
                "sucesso":  True,
                "situacao": dados.get("situacao", ""),
                "dados":    dados,
            }
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}


def verificar_entrega_sped(
    cnpj_contribuinte: str,
    sistemas: List[str],
    data_inicio: str,
    data_fim: str,
    porta_receitabx: int = 3333,
) -> Dict:
    """
    Verifica se os arquivos SPED foram transmitidos para um periodo.

    Args:
        cnpj_contribuinte: CNPJ sem formatacao
        sistemas: lista de sistemas a verificar: ["efd_icms", "efd_contribuicoes", "ecd", "ecf"]
        data_inicio: "YYYY-MM-DD"
        data_fim:    "YYYY-MM-DD"
        porta_receitabx: porta do servico local (padrao 3333)

    Returns:
        dict com situacao de cada sistema SPED
    """
    base_url = f"http://localhost:{porta_receitabx}"
    cliente  = ReceitaBXCliente(base_url=base_url)

    # Verifica se o servico esta rodando
    if not cliente.verificar_servico():
        return {
            "sucesso":       False,
            "nao_instalado": True,
            "contribuinte":  cnpj_contribuinte,
            "erro": (
                "O servico ReceitanetBX nao esta rodando nesta maquina. "
                f"Verifique se esta instalado e configurado na porta {porta_receitabx}. "
                "Baixe em: https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/download/receitanetbx"
            ),
        }

    resultados_por_sistema = {}
    for chave_sistema in sistemas:
        info = SISTEMAS_SPED.get(chave_sistema, {})
        if not info:
            continue
        sistema_sped = info["sistema"]
        descricao    = info["descricao"]

        logger.info(f"[RECEITABX] Pesquisando {descricao} para {cnpj_contribuinte}...")
        resultado = cliente.pesquisar_arquivos(
            cnpj_contribuinte, sistema_sped,
            data_inicio=data_inicio, data_fim=data_fim
        )

        if resultado.get("sucesso"):
            arquivos = resultado.get("arquivos", [])
            transmitido = len(arquivos) > 0
            # Extrai periodos dos arquivos encontrados
            periodos = []
            for arq in arquivos:
                for attr in arq.get("atributos", []):
                    if attr.get("nome") in ("periodoEscrituracao", "periodo", "dataInicio"):
                        periodos.append(attr.get("valor", ""))
            resultados_por_sistema[chave_sistema] = {
                "descricao":   descricao,
                "transmitido": transmitido,
                "total":       len(arquivos),
                "periodos":    periodos,
                "arquivos":    arquivos,
            }
        else:
            resultados_por_sistema[chave_sistema] = {
                "descricao":   descricao,
                "transmitido": None,
                "erro":        resultado.get("erro", ""),
            }

    return {
        "sucesso":      True,
        "contribuinte": cnpj_contribuinte,
        "periodo":      f"{data_inicio} a {data_fim}",
        "sistemas":     resultados_por_sistema,
        "timestamp":    datetime.now().isoformat(),
    }
