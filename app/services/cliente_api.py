"""
cliente_api.py — Cliente HTTP para a API Integra Contador

Encapsula todas as chamadas POST /Consultar, /Apoiar, /Emitir e /Monitorar,
com tratamento de erros, retry automático e logging estruturado.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

import requests

from .autenticacao import SerproAuth

logger = logging.getLogger("integra_contador.api")


class IntegradorAPIError(Exception):
    """Erro retornado pela API SERPRO."""
    def __init__(self, codigo_http: int, mensagem: str, corpo: str = ""):
        self.codigo_http = codigo_http
        self.corpo = corpo
        super().__init__(f"HTTP {codigo_http}: {mensagem} | Corpo: {corpo[:300]}")


class ClienteIntegra:
    """
    Cliente para a API Integra Contador.

    Estrutura padrão do corpo (body) de toda requisição:
    {
        "contratante":      {"numero": "CNPJ_ESCRITORIO", "tipo": 2},
        "autorPedidoDados": {"numero": "CNPJ_ESCRITORIO", "tipo": 2},
        "contribuinte":     {"numero": "CNPJ_OU_CPF",     "tipo": 1|2},
        "pedidoDados": {
            "idSistema":    "SITFIS",
            "idServico":    "SOLICITARPROTOCOLO91",
            "versaoSistema":"1.0",
            "dados":        "{}"
        }
    }
    """

    # Mapeamento de ações para endpoints
    ENDPOINTS = {
        "Consultar": "/Consultar",
        "Apoiar":    "/Apoiar",
        "Emitir":    "/Emitir",
        "Monitorar": "/Monitorar",
    }

    def __init__(self, auth: SerproAuth, api_base_url: str, max_tentativas: int = 3):
        self.auth = auth
        self.api_base_url = api_base_url.rstrip("/")
        self.max_tentativas = max_tentativas
        self.sessao = requests.Session()

    def _montar_body(
        self,
        cnpj_contratante: str,
        cnpj_autor: str,
        numero_contribuinte: str,
        tipo_contribuinte: int,
        id_sistema: str,
        id_servico: str,
        versao_sistema: str,
        dados: Any,
    ) -> dict:
        dados_str = json.dumps(dados) if isinstance(dados, dict) else str(dados)
        # Conforme documentacao SERPRO: contratante e autorPedidoDados sao sempre
        # o escritorio (CNPJ = tipo 2). O contribuinte pode ser tipo 1 (CPF) ou 2 (CNPJ).
        # O campo "tipo" do contratante/autor deve ser 2 pois e CNPJ.
        # Para o SITFIS especificamente, enviar o contratante com tipo 2 e correto.
        return {
            "contratante": {
                "numero": cnpj_contratante,
                "tipo": 2,
            },
            "autorPedidoDados": {
                "numero": cnpj_autor,
                "tipo": 2,
            },
            "contribuinte": {
                "numero": numero_contribuinte,
                "tipo": tipo_contribuinte,
            },
            "pedidoDados": {
                "idSistema":    id_sistema,
                "idServico":    id_servico,
                "versaoSistema": versao_sistema,
                "dados":        dados_str,
            },
        }

    def chamar(
        self,
        acao: str,
        cnpj_contratante: str,
        cnpj_autor: str,
        numero_contribuinte: str,
        tipo_contribuinte: int,
        id_sistema: str,
        id_servico: str,
        versao_sistema: str = "1.0",
        dados: Any = "",
    ) -> dict:
        """
        Realiza uma chamada à API com retry automático.

        Args:
            acao: "Consultar", "Apoiar", "Emitir" ou "Monitorar"
            ...demais parâmetros conforme documentação SERPRO

        Returns:
            Dicionário com a resposta da API
        """
        if acao not in self.ENDPOINTS:
            raise ValueError(f"Ação inválida: {acao}. Use: {list(self.ENDPOINTS.keys())}")

        url = self.api_base_url + self.ENDPOINTS[acao]
        body = self._montar_body(
            cnpj_contratante, cnpj_autor, numero_contribuinte,
            tipo_contribuinte, id_sistema, id_servico, versao_sistema, dados
        )

        ultima_excecao = None
        for tentativa in range(1, self.max_tentativas + 1):
            try:
                headers = self.auth.headers_requisicao()
                logger.debug(
                    f"[Tentativa {tentativa}] {acao} | {id_sistema}/{id_servico} "
                    f"| Contribuinte: {numero_contribuinte}"
                )

                resp = self.sessao.post(url, json=body, headers=headers, timeout=60)

                # Token expirado → forçar renovação e tentar novamente
                if resp.status_code == 401:
                    logger.warning("Token expirado. Renovando...")
                    self.auth.invalidar_cache()
                    time.sleep(2)
                    continue

                # Erros da Receita (403 = acesso negado / sem procuração)
                if resp.status_code == 403:
                    raise IntegradorAPIError(
                        403,
                        "Acesso negado. Verifique se a procuração eletrônica está ativa no e-CAC.",
                        resp.text,
                    )

                # Log completo para diagnostico

                # 304 = Not Modified: protocolo ainda valido
                # A documentacao diz para usar os headers da resposta para recuperar o protocolo
                if resp.status_code == 304:
                    protocolo_header = (
                        resp.headers.get("ETag", "").strip('"').strip()
                        or resp.headers.get("X-Protocolo", "")
                        or resp.headers.get("protocolo", "")
                        or resp.headers.get("protocoloRelatorio", "")
                    )
                    logger.info(f"[HTTP 304] Protocolo header: {protocolo_header[:30] if protocolo_header else 'nenhum'}")
                    return {
                        "_status_304": True,
                        "_servico": id_servico,
                        "_protocolo_header": protocolo_header,
                        "_headers": dict(resp.headers),
                    }

                if resp.status_code not in (200, 202, 204):
                    raise IntegradorAPIError(resp.status_code, resp.reason, resp.text)

                content_type = resp.headers.get("Content-Type", "")
                texto = resp.text.strip()

                if not texto:
                    logger.warning("Resposta com corpo vazio.")
                    return {}

                if "application/json" in content_type or texto.startswith("{"):
                    dados = resp.json()
                    return dados
                else:
                    return {"_conteudo_raw": texto, "_content_type": content_type}

            except IntegradorAPIError:
                raise
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout na tentativa {tentativa}. Aguardando...")
                time.sleep(5 * tentativa)
                ultima_excecao = TimeoutError("Timeout na API SERPRO")
            except requests.exceptions.RequestException as e:
                logger.error(f"Erro de conexão na tentativa {tentativa}: {e}")
                time.sleep(5 * tentativa)
                ultima_excecao = e

        raise ultima_excecao or RuntimeError("Falha após todas as tentativas.")
