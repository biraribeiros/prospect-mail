from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app.core.models import Company, Contact, Department, IcpProfile
from app.web.deps import Ctx, current_ctx, render

router = APIRouter()

SECOES = {
    "hunter": ("Hunter", "Sprint 1 a 3", "Encontra empresas, contatos e sinais de oportunidade (vagas, notícias, expansões)."),
    "filtro": ("Filtro", "Sprint 4 e 5", "Confere se os emails existem e dá notas de qualidade para cada contato."),
    "inteligencia": ("Inteligência", "Sprint 5", "Explica por que abordar cada empresa agora, com provas."),
    "disparo": ("Disparo", "Sprint 6 a 8", "Escreve as mensagens, você aprova, e o sistema envia com segurança."),
}


@router.get("/")
def home(request: Request, ctx: Ctx = Depends(current_ctx)):
    ws = ctx.workspace_id

    def count(model):
        return ctx.db.scalar(select(func.count()).select_from(model).where(model.workspace_id == ws))

    stats = {
        "icps": count(IcpProfile), "departamentos": count(Department),
        "empresas": count(Company), "contatos": count(Contact),
    }
    return render(request, "home.html", ctx, stats=stats, secoes=SECOES)


@router.get("/{secao}", include_in_schema=False)
def secao(secao: str, request: Request, ctx: Ctx = Depends(current_ctx)):
    if secao not in SECOES:
        raise HTTPException(status_code=404)
    titulo, sprint, texto = SECOES[secao]
    return render(request, "stub.html", ctx, titulo=titulo, sprint=sprint, texto=texto, ativo=secao)
