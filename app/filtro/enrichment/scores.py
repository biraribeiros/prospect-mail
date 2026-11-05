"""Etapa 5a: quatro notas independentes (empresa, contato, email, oportunidade), nota final ponderada e nota de "vida".

Tudo em funções puras (0-100), fáceis de testar e de explicar: nenhuma nota usa IA.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

PESOS_FINAL = {"empresa": 0.25, "contato": 0.20, "email": 0.30, "oportunidade": 0.25}
REDES = ("instagram", "facebook", "linkedin", "youtube", "tiktok", "twitter")


def clamp(v: float) -> int:
    return max(0, min(100, int(round(v))))


@dataclass
class CompanyFacts:
    cnpj_ativo: bool = False
    tem_cnpj: bool = False
    site_ok: bool = False
    tem_telefone: bool = False
    liveness: int = 0
    n_evidencias: int = 0


@dataclass
class ContactFacts:
    tem_nome: bool = False
    nome_confianca: float = 0.0
    tem_cargo: bool = False
    tem_departamento: bool = False
    papel_decisao: bool = False  # decisor/aprovador/orçamento
    relacionamento: int = 0  # 0-100 (Etapa 1)


def liveness(site_ok: bool, tem_maps: bool, redes: int, tem_telefone: bool, sinais_recentes: int, blog_recente: bool = False) -> int:
    """Nota de 'vida' da empresa (0-100): site no ar, blog recente, Maps, redes sociais, telefone e sinais."""
    return clamp((30 if site_ok else 0) + (10 if blog_recente else 0) + (20 if tem_maps else 0)
                 + min(redes, 3) * 8 + (6 if tem_telefone else 0) + min(sinais_recentes, 2) * 5)


def nota_empresa(f: CompanyFacts) -> int:
    return clamp((20 if f.tem_cnpj else 0) + (15 if f.cnpj_ativo else 0) + (15 if f.site_ok else 0)
                 + (10 if f.tem_telefone else 0) + f.liveness * 0.30 + min(f.n_evidencias, 5) * 2)


def nota_contato(f: ContactFacts) -> int:
    return clamp((25 * (0.5 + 0.5 * f.nome_confianca) if f.tem_nome else 0) + (25 if f.tem_cargo else 0)
                 + (15 if f.tem_departamento else 0) + (20 if f.papel_decisao else 0) + f.relacionamento * 0.15)


def nota_email(status: str, tipo: str | None, dominio_da_empresa: bool) -> int:
    base = {"valido": 85, "arriscado": 40, "nao_verificado": 20, "invalido": 0, "bloqueado": 0}.get(status, 20)
    if base == 0:
        return 0
    ajuste = {"pessoal": 10, "funcao": 0, "generico": -10}.get(tipo or "", 0)
    return clamp(base + ajuste + (5 if dominio_da_empresa else 0))


def nota_oportunidade(sinais: list[tuple[float, str, datetime | None]], agora: datetime | None = None) -> int:
    """sinais = [(confiança, polaridade, valido_ate)]. Sinal vencido não conta; no máximo dois positivos somam."""
    agora = agora or datetime.now(timezone.utc)
    pos = sorted((c for c, pol, ate in sinais if pol == "positivo" and (ate is None or ate > agora)), reverse=True)[:2]
    neg = sum(1 for c, pol, ate in sinais if pol == "negativo" and (ate is None or ate > agora))
    return clamp(sum(45 * c + 10 for c in pos) - 15 * neg)


def nota_final(empresa: int, contato: int, email: int, oportunidade: int) -> int:
    return clamp(empresa * PESOS_FINAL["empresa"] + contato * PESOS_FINAL["contato"] + email * PESOS_FINAL["email"]
                 + oportunidade * PESOS_FINAL["oportunidade"])


# Setor em linguagem humana, pela divisão (2 primeiros dígitos) do CNAE — usado quando não há IA ou como base.
SETORES = {
    "01": "Agropecuária", "02": "Produção florestal", "03": "Pesca e aquicultura", "05": "Mineração", "06": "Petróleo e gás",
    "10": "Indústria de alimentos", "11": "Indústria de bebidas", "13": "Indústria têxtil", "14": "Confecção e vestuário",
    "16": "Madeira e móveis", "17": "Papel e celulose", "18": "Gráfica e impressão", "20": "Indústria química",
    "21": "Farmacêutica", "22": "Plástico e borracha", "23": "Materiais de construção", "24": "Metalurgia",
    "25": "Produtos de metal", "26": "Eletrônicos e informática", "27": "Equipamentos elétricos", "28": "Máquinas e equipamentos",
    "29": "Automotivo", "31": "Móveis", "35": "Energia", "36": "Água e saneamento", "41": "Construção civil",
    "42": "Obras de infraestrutura", "43": "Serviços de construção", "45": "Comércio e reparo de veículos",
    "46": "Comércio atacadista", "47": "Comércio varejista", "49": "Transporte terrestre", "52": "Logística e armazenagem",
    "53": "Correios e entregas", "55": "Hotelaria", "56": "Alimentação e restaurantes", "58": "Editoras e software",
    "59": "Audiovisual", "60": "Rádio e TV", "61": "Telecomunicações", "62": "Tecnologia da informação",
    "63": "Serviços de informação", "64": "Serviços financeiros", "65": "Seguros", "66": "Atividades financeiras auxiliares",
    "68": "Imobiliário", "69": "Jurídico e contabilidade", "70": "Consultoria empresarial", "71": "Engenharia e arquitetura",
    "72": "Pesquisa e desenvolvimento", "73": "Publicidade e pesquisa de mercado", "74": "Design e serviços técnicos",
    "77": "Aluguel de equipamentos", "78": "Recursos humanos", "80": "Segurança privada", "81": "Serviços para edifícios",
    "82": "Serviços administrativos", "84": "Administração pública", "85": "Educação", "86": "Saúde",
    "87": "Assistência residencial", "88": "Assistência social", "90": "Artes e espetáculos", "93": "Esporte e lazer",
    "94": "Associações e sindicatos", "95": "Reparação de equipamentos", "96": "Serviços pessoais",
}

# gatilhos previsíveis (momentos que costumam gerar demanda) por setor — base editável; a IA só refina
GATILHOS_BASE = {
    "Educação": ["período de matrículas", "início do semestre", "vestibular"],
    "Saúde": ["campanhas de saúde (outubro rosa, novembro azul)", "abertura de nova unidade"],
    "Comércio varejista": ["Black Friday", "Dia das Mães", "Natal", "liquidações de estação"],
    "Alimentação e restaurantes": ["datas comemorativas", "lançamento de cardápio"],
    "Construção civil": ["lançamento de empreendimento", "entrega de obra"],
    "Indústria de alimentos": ["feiras do setor", "lançamento de produto"],
    "Hotelaria": ["alta temporada", "feriados prolongados"],
    "Imobiliário": ["lançamento de empreendimento", "feirões de imóveis"],
}


def setor_humano(cnae: str | None) -> str | None:
    d = "".join(ch for ch in (cnae or "") if ch.isdigit())[:2]
    return SETORES.get(d)


def gatilhos(setor: str | None) -> list[str]:
    return GATILHOS_BASE.get(setor or "", ["aniversário da empresa", "início de ano (planejamento)", "feiras do setor"])
