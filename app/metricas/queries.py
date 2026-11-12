"""Etapa 9: as 4 visões (aquisição, abordagem, conversão, inteligência), teste entre ICPs, A/B e relatório semanal."""
import smtplib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mailer import send_mail
from app.core.models import (Company, Contact, Deal, Hypothesis, IcpProfile, InboundReply, JobRun, Offer, OpportunitySignal,
                             OutboundMessage, Workspace)
from app.disparo import breaker
from app.metricas.stats import MIN_POR_GRUPO, Verdict, compare_groups

JANELA_REUNIAO_DIAS = 21
AVISO_ABERTURA = ("A taxa de abertura não é confiável (imagens bloqueadas, pré-visualização automática de privacidade) e os emails "
                  "são só texto, sem pixel de rastreio: por isso ela não é medida. Use respostas e reuniões como critério.")
ESTAGIOS_REUNIAO = ("reuniao", "proposta", "ganho")


def _pct(a: int, b: int) -> float:
    return round(100 * a / b, 1) if b else 0.0


def aquisicao(session: Session, ws: int) -> dict:
    n_emp = session.scalar(select(func.count()).select_from(Company).where(Company.workspace_id == ws, Company.merged_into_id.is_(None))) or 0
    n_ct = session.scalar(select(func.count()).select_from(Contact).where(Contact.workspace_id == ws)) or 0
    por_status = dict(session.execute(select(Contact.email_status, func.count()).where(Contact.workspace_id == ws, Contact.email.is_not(None))
                                      .group_by(Contact.email_status)).all())
    com_email = sum(por_status.values())
    origem = func.coalesce(Contact.origem, "desconhecida")
    por_origem = session.execute(select(origem, func.count()).where(Contact.workspace_id == ws)
                                 .group_by(origem).order_by(func.count().desc()).limit(8)).all()
    custo = float(session.scalar(select(func.coalesce(func.sum(JobRun.custo_usd), 0)).where(JobRun.workspace_id == ws)) or 0)
    validos = por_status.get("valido", 0)
    sinais = session.scalar(select(func.count()).select_from(OpportunitySignal).where(OpportunitySignal.workspace_id == ws)) or 0
    return {"empresas": n_emp, "contatos": n_ct, "emails": com_email, "validos": validos, "pct_validos": _pct(validos, com_email),
            "por_status": por_status, "por_origem": [(o, n) for o, n in por_origem], "custo_usd": round(custo, 4), "sinais": sinais,
            "custo_por_valido": round(custo / validos, 4) if validos else None}


def abordagem(session: Session, ws: int) -> dict:
    base = (OutboundMessage.workspace_id == ws,)
    cont = dict(session.execute(select(OutboundMessage.status, func.count()).where(*base).group_by(OutboundMessage.status)).all())
    enviadas = cont.get("enviada", 0)
    bounces = session.scalar(select(func.count()).select_from(OutboundMessage).where(*base, OutboundMessage.bounce_tipo.is_not(None))) or 0
    respostas = session.scalar(select(func.count(func.distinct(InboundReply.contact_id))).where(InboundReply.workspace_id == ws)) or 0
    primeiros = session.scalar(select(func.count()).select_from(OutboundMessage).where(*base, OutboundMessage.passo == 1,
                                                                                         OutboundMessage.status == "enviada")) or 0
    return {"status": cont, "enviadas": enviadas, "primeiros_contatos": primeiros, "bounces": bounces, "taxa_bounce": _pct(bounces, enviadas),
            "respostas": respostas, "taxa_resposta": _pct(respostas, primeiros), "taxa_abertura": None, "aviso_abertura": AVISO_ABERTURA,
            "pausado": breaker.pause_info(session, ws)}


def conversao(session: Session, ws: int) -> dict:
    classes = dict(session.execute(select(InboundReply.classe, func.count()).where(InboundReply.workspace_id == ws)
                                   .group_by(InboundReply.classe)).all())
    estagios = dict(session.execute(select(Deal.estagio, func.count()).where(Deal.workspace_id == ws).group_by(Deal.estagio)).all())
    reunioes = session.scalar(select(func.count()).select_from(Deal).where(Deal.workspace_id == ws, Deal.reuniao_em.is_not(None))) or 0
    primeiros = session.scalar(select(func.count(func.distinct(OutboundMessage.contact_id))).where(
        OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1, OutboundMessage.status == "enviada")) or 0
    interessados = classes.get("interessado", 0)
    return {"classes": classes, "estagios": estagios, "reunioes": reunioes, "abordados": primeiros, "interessados": interessados,
            "pct_interessados": _pct(interessados, primeiros), "pct_reunioes": _pct(reunioes, primeiros),
            "funil": [("Abordados", primeiros), ("Responderam", sum(classes.values())), ("Interessados", interessados), ("Reuniões", reunioes),
                      ("Ganhos", estagios.get("ganho", 0))]}


def _rate_by(session: Session, ws: int, col) -> list[tuple[str, int, int]]:
    """(grupo, abordados, responderam) pelo valor da coluna/expressão `col` aplicada à primeira mensagem enviada."""
    resp = select(InboundReply.contact_id).where(InboundReply.workspace_id == ws).distinct().subquery()
    rows = session.execute(select(col, func.count(func.distinct(OutboundMessage.contact_id)),
                                  func.count(func.distinct(resp.c.contact_id)))
                           .select_from(OutboundMessage).outerjoin(resp, resp.c.contact_id == OutboundMessage.contact_id)
                           .where(OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1, OutboundMessage.status == "enviada")
                           .group_by(col).order_by(func.count(func.distinct(OutboundMessage.contact_id)).desc())).all()
    return [(str(g) if g is not None else "—", n, r) for g, n, r in rows]


def inteligencia(session: Session, ws: int) -> dict:
    hip = dict(session.execute(select(Hypothesis.status, func.count()).where(Hypothesis.workspace_id == ws).group_by(Hypothesis.status)).all())
    por_oferta = session.execute(select(Offer.nome, func.count(func.distinct(OutboundMessage.contact_id)), func.count(func.distinct(InboundReply.contact_id)))
                                 .select_from(OutboundMessage).join(Hypothesis, Hypothesis.id == OutboundMessage.hypothesis_id)
                                 .join(Offer, Offer.id == Hypothesis.offer_id)
                                 .outerjoin(InboundReply, InboundReply.contact_id == OutboundMessage.contact_id)
                                 .where(OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1, OutboundMessage.status == "enviada")
                                 .group_by(Offer.nome).order_by(func.count(func.distinct(OutboundMessage.contact_id)).desc())).all()
    por_sinal = session.execute(select(OpportunitySignal.tipo, func.count(func.distinct(OutboundMessage.contact_id)), func.count(func.distinct(InboundReply.contact_id)))
                                .select_from(OutboundMessage).join(Hypothesis, Hypothesis.id == OutboundMessage.hypothesis_id)
                                .join(OpportunitySignal, OpportunitySignal.id == Hypothesis.signal_id)
                                .outerjoin(InboundReply, InboundReply.contact_id == OutboundMessage.contact_id)
                                .where(OutboundMessage.workspace_id == ws, OutboundMessage.passo == 1, OutboundMessage.status == "enviada")
                                .group_by(OpportunitySignal.tipo)).all()
    aprov = hip.get("aprovada", 0)
    decididas = aprov + hip.get("rejeitada", 0)
    return {"hipoteses": hip, "pct_aprovacao": _pct(aprov, decididas),
            "por_oferta": [(n, e, r, _pct(r, e)) for n, e, r in por_oferta], "por_sinal": [(t, e, r, _pct(r, e)) for t, e, r in por_sinal]}


# ---------- teste entre ICPs ----------
def _cohort(session: Session, ws: int, icp_id: int) -> list[tuple[int, datetime]]:
    """(contato, data do 1º envio) das abordagens enviadas deste ICP."""
    return [(c, d) for c, d in session.execute(
        select(OutboundMessage.contact_id, func.min(OutboundMessage.enviada_em)).where(
            OutboundMessage.workspace_id == ws, OutboundMessage.icp_id == icp_id, OutboundMessage.passo == 1,
            OutboundMessage.status == "enviada").group_by(OutboundMessage.contact_id))]


def icp_test(session: Session, ws: int, icp_a: int, icp_b: int, *, modo_teste: bool = False, agora: datetime | None = None,
             minimo: int = MIN_POR_GRUPO) -> dict:
    """Compara dois ICPs pelo critério 'reunião em 21 dias' (mínimo 40 abordados por ICP). Veredito automático."""
    agora = agora or datetime.now(timezone.utc)
    res, maduros = {}, True
    for key, icp_id in (("a", icp_a), ("b", icp_b)):
        coorte = _cohort(session, ws, icp_id)
        deals = {d.contact_id: d for d in session.scalars(select(Deal).where(Deal.workspace_id == ws, Deal.contact_id.in_([c for c, _ in coorte] or [0])))}
        sucesso = 0
        for contato, inicio in coorte:
            d = deals.get(contato)
            if d and d.reuniao_em and inicio <= d.reuniao_em <= inicio + timedelta(days=JANELA_REUNIAO_DIAS):
                sucesso += 1
            if agora < inicio + timedelta(days=JANELA_REUNIAO_DIAS):
                maduros = False
        nome = session.get(IcpProfile, icp_id).nome
        res[key] = {"icp_id": icp_id, "nome": nome, "abordados": len(coorte), "reunioes": sucesso, "taxa": _pct(sucesso, len(coorte))}
    v: Verdict = compare_groups(res["a"]["nome"], res["a"]["abordados"], res["a"]["reunioes"], res["b"]["nome"], res["b"]["abordados"],
                                res["b"]["reunioes"], minimo=minimo, maduros=maduros or modo_teste)
    return {**res, "veredito": v, "modo_teste": modo_teste}


def ab_variantes(session: Session, ws: int) -> dict:
    """A/B de assunto + abertura: cada variante (A/B/C) é um par assunto/abertura diferente escolhido na Etapa 7."""
    grupos = _rate_by(session, ws, OutboundMessage.variante)
    out = {"grupos": [(g, n, r, _pct(r, n)) for g, n, r in grupos if g != "—"], "veredito": None}
    cands = sorted([x for x in out["grupos"]], key=lambda x: -x[3])
    if len(cands) >= 2:
        a, b = cands[0], cands[-1]
        out["veredito"] = compare_groups(f"variante {a[0]}", a[1], a[2], f"variante {b[0]}", b[1], b[2], minimo=30, desfecho="resposta")
    return out


# ---------- relatório semanal ----------
def weekly_report(session: Session, ws: int, agora: datetime | None = None) -> str:
    agora = agora or datetime.now(timezone.utc)
    desde = agora - timedelta(days=7)
    wsr = session.get(Workspace, ws)

    def n(model, *where):
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0
    enviadas = n(OutboundMessage, OutboundMessage.workspace_id == ws, OutboundMessage.enviada_em >= desde)
    bounces = n(OutboundMessage, OutboundMessage.workspace_id == ws, OutboundMessage.enviada_em >= desde, OutboundMessage.bounce_tipo.is_not(None))
    reps = n(InboundReply, InboundReply.workspace_id == ws, InboundReply.recebido_em >= desde)
    inter = n(InboundReply, InboundReply.workspace_id == ws, InboundReply.recebido_em >= desde, InboundReply.classe == "interessado")
    novos = n(Contact, Contact.workspace_id == ws, Contact.criado_em >= desde)
    reun = n(Deal, Deal.workspace_id == ws, Deal.reuniao_em >= desde)
    pend_h = n(Hypothesis, Hypothesis.workspace_id == ws, Hypothesis.status == "pendente")
    pend_m = n(OutboundMessage, OutboundMessage.workspace_id == ws, OutboundMessage.status == "rascunho", OutboundMessage.passo == 1)
    custo = float(session.scalar(select(func.coalesce(func.sum(JobRun.custo_usd), 0)).where(JobRun.workspace_id == ws, JobRun.iniciado_em >= desde)) or 0)
    pausa = breaker.pause_info(session, ws)
    linhas = [f"Resumo semanal — {wsr.nome} ({desde.date():%d/%m} a {agora.date():%d/%m/%Y})", "",
              f"• Contatos novos: {novos}", f"• Emails enviados: {enviadas} (bounces: {bounces}, {_pct(bounces, enviadas)}%)",
              f"• Respostas: {reps} (interessados: {inter})", f"• Reuniões marcadas: {reun}", f"• Gasto com APIs: US$ {custo:.2f}", "",
              f"Esperando você: {pend_h} hipótese(s) para aprovar e {pend_m} mensagem(ns) para aprovar."]
    if pausa:
        linhas += ["", "⚠ ENVIO PAUSADO pelo disjuntor: " + pausa.get("motivo", "")]
    return "\n".join(linhas)


def send_weekly_report(session: Session, ws: int) -> int:
    texto = weekly_report(session, ws)
    to = breaker.admin_emails(session, ws)
    if not to:
        return 0
    try:
        send_mail(to, "[Prospector] Resumo da semana", texto)
    except (OSError, smtplib.SMTPException):
        return 0
    return len(to)
