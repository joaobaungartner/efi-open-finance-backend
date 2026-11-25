from typing import Literal

from pydantic import Field, field_validator

from entities.efi_pix_request import PixBaseModel, PixPagamento

SIMULATED_PARTICIPANTS = {"sim-stone": "Stone", "sim-pagbank": "PagBank"}


class PixSimulationRequest(PixBaseModel):
    instituicao: Literal["sim-stone", "sim-pagbank"]
    favorecido: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}@example\.invalid$")
    valor: str

    @field_validator("valor")
    @classmethod
    def validar_valor(cls, value: str) -> str:
        return PixPagamento(valor=value).valor
