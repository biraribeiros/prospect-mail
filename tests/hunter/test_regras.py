"""Regras puras: filtro de ruído, assinatura, nota de relacionamento, domínio e validações."""
from datetime import datetime, timedelta, timezone

import pytest

from app.hunter.mailbox.base import RawMessage
from app.hunter.mailbox.factory import safe_mbox_path, validate_imap_host
from app.hunter.mailbox.noise import NoiseFilter, dominio_tipo
from app.hunter.mailbox.score import detect_keywords, relationship_score
from app.hunter.mailbox.signature import parse_signature, strip_quoted

AGORA = datetime(2026, 11, 5, tzinfo=timezone.utc)


def msg(de="x@empresa.com.br", **headers):
    return RawMessage(message_id="1", date=AGORA, from_addr=de, headers=headers)


# ---------- ruído ----------
@pytest.mark.parametrize("addr", [
    "noreply@qualquer.com.br", "no-reply@x.com", "do_not_reply@x.com", "nao-responda@x.com.br",
    "mailer-daemon@x.com", "notifications@x.com", "newsletter-semanal@x.com", "alertas@x.com.br",
    "bounce+abc@x.com", "fatura@itau.com.br", "x@mail.nubank.com.br", "amigo@facebookmail.com",
    "pedido@mercadolivre.com.br", "no-reply@accounts.google.com",
])
def test_enderecos_de_ruido(addr):
    assert NoiseFilter().is_noise_address(addr)


@pytest.mark.parametrize("addr", ["maria@empresa.com.br", "joao.silva@gmail.com", "contato@loja.com.br",
                                  "marketing@agencia.com.br", "financeiro@fornecedor.com.br"])
def test_enderecos_normais_nao_sao_ruido(addr):
    assert not NoiseFilter().is_noise_address(addr)


def test_cabecalhos_de_lista_e_envio_em_massa():
    f = NoiseFilter()
    assert f.is_noise_message(msg(**{"list-unsubscribe": "<mailto:x>"}))
    assert f.is_noise_message(msg(**{"list-id": "<lista.x>"}))
    assert f.is_noise_message(msg(precedence="bulk"))
    assert f.is_noise_message(msg(**{"auto-submitted": "auto-generated"}))
    assert not f.is_noise_message(msg(**{"auto-submitted": "no"}))
    assert not f.is_noise_message(msg())


def test_filtro_editavel_por_workspace():
    f = NoiseFilter.from_config({"ruido": {"locais": ["cobranca-auto"], "dominios": ["spam.com.br"],
                                          "permitidos": ["alertas@meucliente.com.br", "itau.com.br"]}})
    assert f.is_noise_address("cobranca-auto@x.com") and f.is_noise_address("a@sub.spam.com.br")
    assert not f.is_noise_address("alertas@meucliente.com.br")  # exceção vence o padrão
    assert not f.is_noise_address("fatura@itau.com.br")  # domínio permitido
    assert not f.is_noise_message(msg("gerente@itau.com.br", **{"list-id": "x"}))


def test_dominio_pessoal_x_corporativo():
    assert dominio_tipo("a@gmail.com") == "pessoal" and dominio_tipo("a@hotmail.com") == "pessoal"
    assert dominio_tipo("a@empresa.com.br") == "corporativo"


# ---------- assinatura ----------
def test_assinatura_completa():
    corpo = ("Oi, segue o que combinamos.\n\nAtenciosamente,\nJoão da Silva\nDiretor Comercial\n"
             "Tel: +55 (34) 3822-1000 | Cel (34) 99876-5432\nhttps://www.empresa.com.br\n")
    s = parse_signature(corpo)
    assert (s.nome, s.cargo) == ("João da Silva", "Diretor Comercial")
    assert s.telefone == "553438221000" and s.site == "https://www.empresa.com.br"
    assert "Atenciosamente" not in s.trecho and len(s.trecho) <= 400


def test_assinatura_nome_e_cargo_na_mesma_linha_e_delimitador():
    s = parse_signature("texto\n-- \nMaria Souza | Gerente de Marketing\n(11) 91234-5678\n")
    assert (s.nome, s.cargo, s.telefone) == ("Maria Souza", "Gerente de Marketing", "11912345678")


def test_ignora_texto_citado():
    corpo = ("Perfeito!\n\nAtt,\nAna Lima\nAnalista\n\nEm seg, 3 de nov de 2026 escreveu:\n> Fulano Tal\n> Diretor\n"
             "> (11) 90000-0000\n")
    s = parse_signature(corpo)
    assert s.nome == "Ana Lima" and s.cargo == "Analista" and s.telefone is None
    assert "Fulano" not in strip_quoted(corpo)


def test_assinatura_vazia_ou_sem_dados():
    s = parse_signature("ok, obrigado")
    assert not s.tem_dados


# ---------- nota ----------
def test_palavras_chave():
    assert set(detect_keywords("Orçamento para contrato", "segue a NF-e e a proposta, reunião amanhã")) == {
        "orcamento", "contrato", "nf", "proposta", "reuniao"}
    assert detect_keywords("Oi", "tudo bem?") == []
    assert detect_keywords("Informação", "confiança") == []  # 'nf' dentro de palavra não conta


def test_nota_limites_e_ordem():
    recente = AGORA - timedelta(days=3)
    assert relationship_score(0, 0, None, []) == 0
    forte = relationship_score(10, 10, recente, ["orcamento", "proposta", "contrato", "nf", "reuniao"], AGORA)
    so_recebido = relationship_score(0, 10, recente, [], AGORA)
    antigo = relationship_score(10, 10, AGORA - timedelta(days=700), [], AGORA)
    assert 0 <= so_recebido < forte <= 100 and antigo < forte
    assert forte >= 90  # volume + mão dupla + recência + palavras-chave
    assert relationship_score(10, 10, recente, [], AGORA) > relationship_score(10, 1, recente, [], AGORA)  # equilíbrio


# ---------- validações de segurança ----------
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "192.168.1.10", "169.254.169.254",
                                  "::1", "servidor.local", "meuservidor", "", "a b.com"])
def test_host_imap_interno_ou_invalido_e_barrado(host):
    assert validate_imap_host(host)


def test_host_imap_publico_ok():
    assert validate_imap_host("imap.locaweb.com.br") is None and validate_imap_host("200.147.0.1") is None


def test_caminho_mbox_so_dentro_da_pasta_permitida(world):
    with pytest.raises(ValueError):
        safe_mbox_path(world.a.id, "/etc/passwd")
    with pytest.raises(ValueError):
        safe_mbox_path(world.a.id, "data/mbox/999/outro.mbox")  # pasta de outro workspace
