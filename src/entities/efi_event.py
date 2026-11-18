from pydantic import BaseModel, ConfigDict, Field, model_validator


class EfiWebhookDTO(BaseModel):
    model_config = ConfigDict(extra="allow")
    identificadorPagamento: str | None = Field(default=None, max_length=300)
    identificadorVinculo: str | None = Field(default=None, max_length=300)
    identificadorAdesao: str | None = Field(default=None, max_length=300)
    status: str = Field(min_length=1, max_length=80)
    tipo: str = Field(min_length=1, max_length=80)
    valor: str | None = Field(default=None, max_length=50)
    endToEndId: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def check_identifier(self):
        if not (
            self.identificadorPagamento or self.identificadorVinculo or self.identificadorAdesao
        ):
            raise ValueError("Payload sem identificador de pagamento, vínculo ou adesão")
        return self

    @property
    def identifier(self) -> str:
        return (
            self.identificadorPagamento
            or self.identificadorVinculo
            or self.identificadorAdesao
            or ""
        )
