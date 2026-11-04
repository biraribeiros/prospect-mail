"""Taxonomia de departamentos e necessidades previstas (contexto: agência de comunicação).

Cada necessidade: (descrição, relevância 1-5, tags de ofertas relacionadas). Tudo editável no painel.
As tags viram vínculos reais com o catálogo de ofertas na Etapa 5b.
"""

DEPARTAMENTOS = [
    {
        "nome": "Marketing e Comunicação",
        "email_patterns": ["marketing@", "comunicacao@", "imprensa@", "midia@"],
        "cargos": ["Diretor de Marketing", "Gerente de Marketing", "Coordenador de Comunicação", "Analista de Marketing"],
        "necessidades": [
            ("Vídeo institucional e conteúdo audiovisual", 5, ["video"]),
            ("Presença digital e redes sociais", 5, ["social"]),
            ("Identidade visual e rebranding", 4, ["branding"]),
            ("Campanhas de tráfego pago", 4, ["trafego"]),
        ],
    },
    {
        "nome": "RH e Gente",
        "email_patterns": ["rh@", "recrutamento@", "gente@", "talentos@"],
        "cargos": ["Gerente de RH", "Coordenador de Recursos Humanos", "Analista de Recrutamento", "Diretor de Pessoas"],
        "necessidades": [
            ("Marca empregadora e vídeos de recrutamento", 5, ["video", "branding"]),
            ("Comunicação interna e endomarketing", 5, ["endomarketing"]),
            ("Vídeos de treinamento e integração", 4, ["video"]),
            ("Campanhas de clima e engajamento", 3, ["endomarketing"]),
        ],
    },
    {
        "nome": "SST e Segurança do Trabalho",
        "email_patterns": ["sst@", "seguranca@", "segurancadotrabalho@", "cipa@"],
        "cargos": ["Técnico de Segurança do Trabalho", "Engenheiro de Segurança", "Coordenador de SST"],
        "necessidades": [
            ("Vídeos de treinamento de normas (NRs)", 5, ["video"]),
            ("Campanhas de SIPAT e prevenção", 4, ["endomarketing", "eventos"]),
            ("Comunicação visual de segurança", 3, ["branding"]),
        ],
    },
    {
        "nome": "Comercial e Vendas",
        "email_patterns": ["comercial@", "vendas@", "negocios@"],
        "cargos": ["Diretor Comercial", "Gerente de Vendas", "Coordenador Comercial", "Executivo de Contas"],
        "necessidades": [
            ("Materiais e apresentações de vendas", 5, ["branding", "video"]),
            ("Landing pages e geração de leads", 5, ["site", "trafego"]),
            ("Vídeos de produto e demonstração", 4, ["video"]),
        ],
    },
    {
        "nome": "Eventos",
        "email_patterns": ["eventos@", "feiras@"],
        "cargos": ["Gerente de Eventos", "Coordenador de Eventos", "Produtor de Eventos"],
        "necessidades": [
            ("Cobertura audiovisual e aftermovie", 5, ["video", "eventos"]),
            ("Identidade visual e cenografia", 4, ["branding", "eventos"]),
            ("Transmissão ao vivo", 3, ["video", "eventos"]),
        ],
    },
    {
        "nome": "Operações e Qualidade",
        "email_patterns": ["operacoes@", "qualidade@", "producao@"],
        "cargos": ["Gerente de Operações", "Coordenador de Qualidade", "Supervisor de Produção"],
        "necessidades": [
            ("Vídeos de processos e procedimentos", 4, ["video"]),
            ("Comunicação visual interna", 3, ["branding", "endomarketing"]),
        ],
    },
    {
        "nome": "Diretoria",
        "email_patterns": ["diretoria@", "presidencia@", "ceo@"],
        "cargos": ["Diretor Geral", "CEO", "Sócio-Administrador", "Presidente"],
        "necessidades": [
            ("Posicionamento e branding da empresa", 5, ["branding"]),
            ("Vídeo e relatório institucional", 4, ["video"]),
            ("Comunicação em momentos de crise", 3, ["social"]),
        ],
    },
    {
        "nome": "Compras e Financeiro",
        "email_patterns": ["compras@", "financeiro@", "suprimentos@"],
        "cargos": ["Gerente Financeiro", "Coordenador de Compras", "Analista Financeiro"],
        "necessidades": [
            ("Aprovação de orçamento de comunicação", 3, []),
            ("Comunicação com fornecedores e investidores", 2, ["branding"]),
        ],
    },
    {
        "nome": "TI",
        "email_patterns": ["ti@", "tecnologia@", "suporte@"],
        "cargos": ["Gerente de TI", "Coordenador de Tecnologia", "Analista de Sistemas"],
        "necessidades": [
            ("Tutoriais e documentação em vídeo", 3, ["video"]),
            ("Campanhas de segurança da informação", 3, ["endomarketing"]),
            ("Site e intranet", 3, ["site"]),
        ],
    },
    {
        "nome": "Jurídico",
        "email_patterns": ["juridico@", "compliance@"],
        "cargos": ["Gerente Jurídico", "Advogado Corporativo", "Coordenador de Compliance"],
        "necessidades": [
            ("Treinamentos de compliance e código de conduta", 4, ["video", "endomarketing"]),
            ("Gestão de reputação em crises", 3, ["social"]),
        ],
    },
]


def necessidades_json(itens):
    return [
        {"necessidade": n, "relevancia": r, "ofertas_relacionadas": tags}
        for n, r, tags in itens
    ]
