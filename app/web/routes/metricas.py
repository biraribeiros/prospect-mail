"""Etapa 9: painel de métricas com 4 visões, teste entre ICPs e comparação A/B."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.models import IcpProfile
from app.metricas import queries as q
from app.metricas.stats import MIN_POR_GRUPO
from app.web.deps import Ctx, current_ctx, render

router = APIRouter(prefix="/metricas")


@router.get("")
def metricas(request: Request, a: int = 0, b: int = 0, teste: str = "", ctx: Ctx = Depends(current_ctx)):
    ws = ctx.workspace_id
    icps = ctx.db.scalars(select(IcpProfile).where(IcpProfile.workspace_id == ws).order_by(IcpProfile.nome)).all()
    ids = {i.id for i in icps}
    teste_icp = q.icp_test(ctx.db, ws, a, b, modo_teste=teste == "sim") if a in ids and b in ids and a != b else None
    return render(request, "metricas.html", ctx, ativo="metricas", aq=q.aquisicao(ctx.db, ws), ab=q.abordagem(ctx.db, ws),
                  cv=q.conversao(ctx.db, ws), it=q.inteligencia(ctx.db, ws), ab_test=q.ab_variantes(ctx.db, ws), icps=icps,
                  teste_icp=teste_icp, sel_a=a, sel_b=b, minimo=MIN_POR_GRUPO, teste_flag=teste == "sim")


@router.get("/relatorio")
def relatorio(ctx: Ctx = Depends(current_ctx)):
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(q.weekly_report(ctx.db, ctx.workspace_id))
