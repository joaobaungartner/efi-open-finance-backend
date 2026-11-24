from copy import deepcopy

import pytest
from pydantic import ValidationError

from entities.efi_pix_request import PixByKeyRequest


def exemplo():
    return {
        "pagador": {
            "idParticipante": "participante-teste",
            "cpf": "52998224725",
        },
        "favorecido": {
            "chave": "chave-teste",
        },
        "pagamento": {
            "valor": "10.00",
            "idProprio": "teste-001",
        },
    }


def test_solicitacao_valida():
    result = PixByKeyRequest.model_validate(exemplo())
    assert result.pagamento.valor == "10.00"
    assert result.pagador.cpf == "52998224725"


def test_rejeita_cpf_ausente():
    data = exemplo()
    del data["pagador"]["cpf"]

    with pytest.raises(ValidationError):
        PixByKeyRequest.model_validate(data)


def test_rejeita_cpf_invalido():
    data = exemplo()
    data["pagador"]["cpf"] = "11111111111"

    with pytest.raises(ValidationError):
        PixByKeyRequest.model_validate(data)


@pytest.mark.parametrize(
    "valor",
    ["0.00", "-1.00", "1.001", "1", "01.00"],
)
def test_rejeita_valores_invalidos(valor):
    data = exemplo()
    data["pagamento"]["valor"] = valor

    with pytest.raises(ValidationError):
        PixByKeyRequest.model_validate(data)


def test_rejeita_chave_vazia():
    data = exemplo()
    data["favorecido"]["chave"] = ""

    with pytest.raises(ValidationError):
        PixByKeyRequest.model_validate(data)


def test_rejeita_campos_desconhecidos():
    data = deepcopy(exemplo())
    data["pagamento"]["campoNaoPermitido"] = "teste"

    with pytest.raises(ValidationError):
        PixByKeyRequest.model_validate(data)
