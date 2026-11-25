from datetime import UTC
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Codigo = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
Nome = Annotated[str, Field(min_length=1, max_length=120)]
Referencia = Annotated[str, Field(min_length=1, max_length=80)]
Motivo = Annotated[str, Field(min_length=1, max_length=240)]
Id = Annotated[int, Field(gt=0)]
Ambiente = Literal["simulacao", "homologacao", "producao"]


def em_utc(instante):
    try:
        return instante.astimezone(UTC).replace(tzinfo=None)
    except OverflowError:
        raise ValueError("Data fora do intervalo representável em UTC") from None


InstanteUTC = Annotated[AwareDatetime, AfterValidator(em_utc)]


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Edicao(Entrada):
    @model_validator(mode="after")
    def validar_campos(self):
        if not self.model_fields_set or any(
            getattr(self, campo) is None for campo in self.model_fields_set
        ):
            raise ValueError("Informe ao menos um campo e use valores não nulos")
        return self


class NovoCliente(Entrada):
    codigo: Codigo
    nome: Nome


class EditarCliente(Edicao):
    codigo: Codigo | None = None
    nome: Nome | None = None
    ativo: bool | None = None


class NovaConta(Entrada):
    instituicao_id: Id
    referencia: Referencia
    ambiente: Ambiente


class NovoTerminal(Entrada):
    conta_id: Id
    identificador: Referencia
    apelido: Nome


class EditarTerminal(Edicao):
    apelido: Nome | None = None
    ativo: bool | None = None


class NovoVinculo(Entrada):
    cliente_id: Id
    inicio: InstanteUTC
    fim: InstanteUTC | None = None
    motivo: Motivo | None = None

    @model_validator(mode="after")
    def validar_vigencia(self):
        if self.fim is not None and self.fim <= self.inicio:
            raise ValueError("O fim deve ser posterior ao início")
        return self


class Transferencia(Entrada):
    cliente_id: Id
    vinculo_anterior_id: Id
    fim_anterior: InstanteUTC | None
    efetiva_em: InstanteUTC
    motivo: Motivo
