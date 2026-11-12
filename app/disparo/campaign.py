"""Campanha manual: você escreve a mensagem (HTML ou texto), o sistema preenche as variáveis, gera uma mensagem por contato
elegível e, depois da sua aprovação, entrega ao agendador de envio (aquecimento, janela comercial, intervalos, descadastro).

Só entram contatos com email VÁLIDO (confirmado ou liberado por você no Filtro). Quem já foi contatado nos últimos 90 dias,
quem está na lista de supressão e o excesso de contatos do mesmo domínio ficam de fora.
"""
import re
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.models import Campaign, Company, Contact, OutboundMessage, Suppression, Workspace
from app.disparo.compose import PALAVRAS_SPAM, SPAM_MAX, VAR_RE, caps_ratio, count_words, fill, match_icp, spam_check
from app.disparo.guard import domain_of_address
from app.disparo.htmlmail import html_to_text, sanitize_html

VARIAVEIS = {"primeiro_nome": "Primeiro nome", "nome": "Nome completo", "empresa": "Empresa", "cargo": "Cargo",
             "cidade": "Cidade", "remetente": "Nome da sua empresa"}
MAX_ASSUNTO = 90
MAX_PALAVRAS = 300
AVISO_PALAVRAS = 180
MAX_LINKS = 3
DIAS_SEM_REPETIR = 90
EDITAVEL = ("rascunho", "gerada")


def set_body(c: Campaign, formato: str, conteudo: str) -> None:
    """Grava o corpo no formato escolhido. HTML é limpo; o texto puro é sempre derivado dele."""
    if formato == "texto":
        c.formato, c.corpo_html, c.corpo_texto = "texto", None, conteudo.replace("\r\n", "\n").strip()
    else:
        limpo = sanitize_html(conteudo)
        c.formato, c.corpo_html, c.corpo_texto = "html", limpo, html_to_text(limpo)


def sample_vars(ws_nome: str) -> dict[str, str]:
    return {"primeiro_nome": "Maria", "nome": "Maria Souza", "empresa": "Empresa Exemplo", "cargo": "Gerente de Marketing",
            "cidade": "Patos de Minas", "remetente": ws_nome}


def contact_vars(ct: Contact, company: Company | None, ws_nome: str) -> dict[str, str]:
    return {"primeiro_nome": ct.primeiro_nome or "", "nome": ct.nome_completo or "", "cargo": ct.cargo or "",
            "empresa": (company.nome_fantasia or company.razao_social or "") if company else "",
            "cidade": (company.municipio or "") if company else "", "remetente": ws_nome}


def _fill_html(html_: str, vars_: dict[str, str]) -> tuple[str, list[str]]:
    faltam: list[str] = []

    def sub(m):
        v = (vars_.get(m.group(1)) or "").strip()
        if not v:
            faltam.append(m.group(1))
            return m.group(0)
        return escape(v, quote=False)
    return VAR_RE.sub(sub, html_), faltam


def render(c: Campaign, vars_: dict[str, str]) -> tuple[str, str, str | None, list[str]]:
    """(assunto, texto, html ou None, variáveis vazias). Variável vazia fica visível para a mensagem ser bloqueada."""
    assunto, f1 = fill(c.assunto, vars_)
    texto, f2 = fill(c.corpo_texto, vars_)
    html_, f3 = _fill_html(c.corpo_html, vars_) if c.formato == "html" and c.corpo_html else (None, [])
    return assunto, texto, html_, list(dict.fromkeys(f1 + f2 + f3))


def check_campaign(c: Campaign) -> tuple[list[str], list[str]]:
    """(erros que impedem aprovar, avisos). A nota de spam é conferida à parte, com o Rspamd."""
    erros: list[str] = []
    avisos: list[str] = []
    if not c.assunto.strip():
        erros.append("O assunto está vazio.")
    elif len(c.assunto) > MAX_ASSUNTO:
        erros.append(f"Assunto longo demais ({len(c.assunto)} letras; máximo {MAX_ASSUNTO}).")
    if not c.corpo_texto.strip():
        erros.append("A mensagem está vazia.")
    desconhecidas = sorted({v for v in VAR_RE.findall(c.assunto + " " + c.corpo_texto) if v not in VARIAVEIS})
    if desconhecidas:
        erros.append("Variável desconhecida: " + ", ".join("{{" + v + "}}" for v in desconhecidas)
                     + ". Use: " + ", ".join("{{" + v + "}}" for v in VARIAVEIS) + ".")
    palavras = count_words(c.corpo_texto)
    if palavras > MAX_PALAVRAS:
        erros.append(f"Mensagem longa demais ({palavras} palavras; máximo {MAX_PALAVRAS}).")
    elif palavras > AVISO_PALAVRAS:
        avisos.append(f"Mensagem longa ({palavras} palavras): textos curtos costumam ter mais resposta.")
    links = set(re.findall(r"https?://[^\s)]+", c.corpo_texto))
    if len(links) > MAX_LINKS:
        erros.append(f"Links demais ({len(links)}; máximo {MAX_LINKS}): muitos links elevam a chance de spam.")
    if caps_ratio(c.corpo_texto) > 0.2 or caps_ratio(c.assunto) > 0.5:
        avisos.append("Muitas palavras em MAIÚSCULAS: isso pesa na nota de spam.")
    low = (c.assunto + " " + c.corpo_texto).lower()
    ruins = [p for p in PALAVRAS_SPAM if p in low]
    if ruins:
        avisos.append("Palavras típicas de spam: " + ", ".join(ruins[:4]) + ".")
    return erros, avisos


# ---------- público e geração ----------
def _suprimidos(session: Session, ws: int) -> set[str]:
    return {x.email_ou_dominio.lower() for x in session.scalars(select(Suppression).where(Suppression.workspace_id == ws))}


def valid_by_origin(session: Session, ws: int) -> list[tuple[str, int]]:
    return [(o or "", n) for o, n in session.execute(
        select(Contact.origem, func.count()).where(Contact.workspace_id == ws, Contact.email.is_not(None), Contact.bloqueado_envio.is_(False),
                                                   Contact.email_status == "valido").group_by(Contact.origem)
        .order_by(func.count().desc())).all()]


def generate(session: Session, ws: int, c: Campaign, origem: str = "", limite: int = 40, max_dominio: int = 2,
             agora: datetime | None = None) -> dict:
    """Refaz os rascunhos da campanha: uma mensagem por contato válido elegível. O que já foi aprovado/enviado não muda."""
    agora = agora or datetime.now(timezone.utc)
    limite, max_dominio = max(1, min(limite, 2000)), max(1, min(max_dominio, 20))
    wsr = session.get(Workspace, ws)
    session.execute(delete(OutboundMessage).where(OutboundMessage.campaign_id == c.id, OutboundMessage.status.in_(("rascunho", "bloqueada"))))
    ja_contatados = set(session.scalars(select(OutboundMessage.contact_id).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1, OutboundMessage.status.in_(("aprovada", "agendada", "enviada")),
        or_(OutboundMessage.campaign_id == c.id, OutboundMessage.criado_em >= agora - timedelta(days=DIAS_SEM_REPETIR)))))
    suprimidos = _suprimidos(session, ws)
    q = select(Contact).where(Contact.workspace_id == ws, Contact.email.is_not(None), Contact.bloqueado_envio.is_(False),
                              Contact.email_status == "valido")
    if origem:
        q = q.where(Contact.origem == origem)
    res = {"criadas": 0, "bloqueadas": 0, "ja_contatados": 0, "suprimidos": 0, "excesso_dominio": 0, "limite": False}
    por_dominio: dict[str, int] = {}
    for ct in session.scalars(q.order_by(Contact.nota_final.desc().nullslast(), Contact.id).limit(10000)):
        if res["criadas"] >= limite:
            res["limite"] = True
            break
        dom = domain_of_address(ct.email)
        if ct.id in ja_contatados:
            res["ja_contatados"] += 1
            continue
        if ct.email.lower() in suprimidos or dom in suprimidos:
            res["suprimidos"] += 1
            continue
        if por_dominio.get(dom, 0) >= max_dominio:
            res["excesso_dominio"] += 1
            continue
        por_dominio[dom] = por_dominio.get(dom, 0) + 1
        company = session.get(Company, ct.company_id) if ct.company_id else None
        assunto, texto, html_, faltam = render(c, contact_vars(ct, company, wsr.nome))
        bloq = bool(faltam)
        m = OutboundMessage(workspace_id=ws, contact_id=ct.id, company_id=company.id if company else None, campaign_id=c.id, passo=1,
                            objetivo="campanha", assunto=assunto[:200], corpo=texto, corpo_html=html_,
                            icp_id=match_icp(session, ws, company) if company else None,
                            status="bloqueada" if bloq else "rascunho",
                            motivo=("variável vazia: " + ", ".join(faltam)) if bloq else None)
        session.add(m)
        session.flush()
        m.thread_raiz_id = m.id
        res["criadas"] += 1
        res["bloqueadas"] += int(bloq)
    c.status = "gerada"
    session.flush()
    return res


def approve(session: Session, ws: int, c: Campaign, checker=None) -> dict:
    """Aprova os rascunhos (a decisão humana) e libera para o agendador. Confere regras e a nota de spam antes."""
    erros, _ = check_campaign(c)
    rasc = list(session.scalars(select(OutboundMessage).where(OutboundMessage.campaign_id == c.id, OutboundMessage.status == "rascunho")
                                .order_by(OutboundMessage.id)))
    if not rasc:
        erros.append("Não há mensagens em rascunho. Gere as mensagens primeiro.")
    if erros:
        return {"erros": erros}
    score, origem = spam_check(rasc[0].assunto, rasc[0].corpo, ws, checker, html=rasc[0].corpo_html)
    if score >= SPAM_MAX:
        return {"erros": [f"Nota de spam alta ({score:.1f} no {origem}; precisa ser menor que {SPAM_MAX:.0f}). Reescreva e gere de novo."]}
    for m in rasc:
        m.status, m.motivo, m.spam_score = "aprovada", None, score
    session.flush()
    return {"aprovadas": len(rasc), "nota_spam": score, "origem_nota": origem}


def discard_drafts(session: Session, c: Campaign) -> int:
    n = 0
    for m in session.scalars(select(OutboundMessage).where(OutboundMessage.campaign_id == c.id,
                                                           OutboundMessage.status.in_(("rascunho", "bloqueada")))):
        m.status = "descartada"
        n += 1
    session.flush()
    return n


def status_counts(session: Session, c: Campaign) -> dict[str, int]:
    return dict(session.execute(select(OutboundMessage.status, func.count()).where(OutboundMessage.campaign_id == c.id)
                                .group_by(OutboundMessage.status)).all())
