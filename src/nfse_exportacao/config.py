from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    cert_path: Path = Path("")
    cert_password: str = ""

    nfse_ambiente: Literal["producaorestrita", "producao"] = "producaorestrita"
    nfse_base_url_homolog: str = "https://sefin.producaorestrita.nfse.gov.br"
    nfse_base_url_prod: str = "https://sefin.nfse.gov.br"

    prestador_cnpj: str = ""
    prestador_razao_social: str = ""
    prestador_inscricao_municipal: str = ""
    prestador_cep: str = ""
    prestador_logradouro: str = ""
    prestador_numero: str = ""
    prestador_complemento: str = ""
    prestador_bairro: str = ""
    prestador_municipio_ibge: str = ""
    prestador_uf: str = ""
    prestador_regime: str = ""

    servico_codigo_lc116: str = ""
    servico_descricao: str = ""

    tomador_nome: str = ""
    tomador_documento_exterior: str = ""
    tomador_pais: str = "BR"
    tomador_cep_ext: str = ""
    tomador_cidade: str = ""
    tomador_estado: str = ""
    tomador_logradouro: str = ""
    tomador_numero: str = "S/N"
    tomador_bairro: str = ""

    aliq_simples_aprox: float = 0.0

    output_dir: Path = Path("./out")

    @property
    def nfse_base_url(self) -> str:
        return self.nfse_base_url_prod if self.nfse_ambiente == "producao" else self.nfse_base_url_homolog


@lru_cache
def get_settings() -> Settings:
    return Settings()
