from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class PixBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class PixPagador(PixBaseModel):
    idParticipante: str = Field(min_length=1, max_length=120)
    cpf: str = Field(pattern=r"^[0-9]{11}$")
    cnpj: str | None = Field(
        default=None,
        pattern=r"^[0-9]{14}$",
    )

    @field_validator("cpf")
    @classmethod
    def validar_cpf(cls, value: str) -> str:
        digits = [int(c) for c in value]

        if len(set(digits)) == 1:
            raise ValueError("CPF inválido")

        for pos in (9, 10):
            digit = (
                sum(
                    digits[i] * (pos + 1 - i)
                    for i in range(pos)
                ) * 10
            ) % 11 % 10

            if digit != digits[pos]:
                raise ValueError("CPF inválido")

        return value


class PixFavorecidoChave(PixBaseModel):
    chave: str = Field(min_length=1, max_length=77)

    @field_validator("chave")
    @classmethod
    def validar_chave(cls, value: str) -> str:
        if any(c.isspace() for c in value):
            raise ValueError("Chave Pix inválida")
        return value


class PixPagamento(PixBaseModel):
    valor: str = Field(
        pattern=r"^(?:0|[1-9][0-9]{0,15})\.[0-9]{2}$"
    )
    infoPagador: str | None = Field(
        default=None, max_length=140
    )
    idProprio: str | None = Field(
        default=None, min_length=1, max_length=100
    )
    identificadorTransacao: str | None = Field(
        default=None, min_length=1, max_length=100
    )

    @field_validator("valor")
    @classmethod
    def validar_valor(cls, value: str) -> str:
        if Decimal(value) <= 0:
            raise ValueError("Valor Pix deve ser positivo")
        return value


class PixByKeyRequest(PixBaseModel):
    pagador: PixPagador
    favorecido: PixFavorecidoChave
    pagamento: PixPagamento
