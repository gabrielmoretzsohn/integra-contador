"""
autenticacao.py — Módulo de autenticação SERPRO (OAuth2 + certificado digital)

Fluxo:
  1. POST /authenticate com e-CNPJ (PFX) + Consumer Key/Secret → Bearer + JWT
  2. (Opcional) POST /Apoiar/AutenticarProcurador com XML assinado → jwt_token do procurador
"""

import base64
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import requests
from requests_pkcs12 import post as pkcs12_post

logger = logging.getLogger("integra_contador.auth")


class TokenCache:
    """Cache simples de token com controle de expiração."""

    def __init__(self):
        self._access_token: Optional[str] = None
        self._jwt_token: Optional[str] = None
        self._expira_em: Optional[datetime] = None

    @property
    def valido(self) -> bool:
        if not self._access_token or not self._expira_em:
            return False
        # margem de 60s para evitar expiração durante requisição
        return datetime.now() < (self._expira_em - timedelta(seconds=60))

    def salvar(self, access_token: str, jwt_token: str, expires_in: int):
        self._access_token = access_token
        self._jwt_token = jwt_token
        self._expira_em = datetime.now() + timedelta(seconds=expires_in)
        logger.info(f"Token salvo. Expira em: {self._expira_em.strftime('%H:%M:%S')}")

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token

    @property
    def jwt_token(self) -> Optional[str]:
        return self._jwt_token

    def invalidar(self):
        self._access_token = None
        self._jwt_token = None
        self._expira_em = None


class SerproAuth:
    """
    Gerencia autenticação na API SERPRO Integra Contador.

    Suporta dois modos:
    - Modo Escritório (role-type TERCEIROS): escritório usa seu próprio certificado,
      as empresas têm procuração ativa no e-CAC em nome do escritório.
    - Modo Procurador (AutenticarProcurador): software-house envia XML assinado.

    Para escritórios de contabilidade com procurações eletrônicas no e-CAC,
    usa-se o Modo Escritório (mais simples).
    """

    AUTH_URL = "https://autenticacao.sapi.serpro.gov.br/authenticate"

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        caminho_certificado: str,
        senha_certificado: str,
        cnpj_contratante: str,
    ):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.caminho_certificado = str(Path(caminho_certificado).resolve())
        self.senha_certificado = senha_certificado
        self.cnpj_contratante = cnpj_contratante
        self._cache = TokenCache()

    def _base64_credenciais(self) -> str:
        credenciais = f"{self.consumer_key}:{self.consumer_secret}"
        return base64.b64encode(credenciais.encode("utf-8")).decode("utf-8")

    def autenticar(self, forcar_renovacao: bool = False) -> Tuple[str, str]:
        """
        Obtém ou renova tokens de acesso.

        Returns:
            Tupla (access_token, jwt_token)
        """
        if not forcar_renovacao and self._cache.valido:
            logger.debug("Usando token em cache.")
            return self._cache.access_token, self._cache.jwt_token

        logger.info("Solicitando novo token SERPRO...")

        headers = {
            "Authorization": f"Basic {self._base64_credenciais()}",
            "role-type": "TERCEIROS",
            "content-type": "application/x-www-form-urlencoded",
        }
        body = {"grant_type": "client_credentials"}

        try:
            response = pkcs12_post(
                url=self.AUTH_URL,
                data=body,
                headers=headers,
                pkcs12_filename=self.caminho_certificado,
                pkcs12_password=self.senha_certificado,
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.SSLError as e:
            raise RuntimeError(
                f"Erro de certificado SSL. Verifique o arquivo PFX e a senha.\nDetalhe: {e}"
            ) from e
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(
                f"Erro HTTP na autenticação SERPRO ({response.status_code}): {response.text}"
            ) from e

        dados = response.json()

        access_token = dados.get("access_token")
        jwt_token = dados.get("jwt_token")
        expires_in = int(dados.get("expires_in", 1800))

        if not access_token or not jwt_token:
            raise RuntimeError(
                f"Resposta de autenticação inválida: {json.dumps(dados, indent=2)}"
            )

        self._cache.salvar(access_token, jwt_token, expires_in)
        logger.info(f"Autenticado com sucesso. Token expira em {expires_in}s.")
        return access_token, jwt_token

    def headers_requisicao(self) -> dict:
        """Retorna headers prontos para chamadas à API."""
        access_token, jwt_token = self.autenticar()
        return {
            "Authorization": f"Bearer {access_token}",
            "jwt_token": jwt_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def invalidar_cache(self):
        self._cache.invalidar()
