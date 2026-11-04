from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.core import csvio
from app.web.deps import Ctx, current_ctx, editor_ctx, render

router = APIRouter()


@router.get("/configuracoes/importar")
def import_page(request: Request, ctx: Ctx = Depends(current_ctx)):
    return render(request, "import.html", ctx, ativo="configuracoes",
                  col_contatos=csvio.CONTATO_COLUNAS, col_empresas=csvio.EMPRESA_COLUNAS)


async def _handle(request: Request, ctx: Ctx, file: UploadFile, fn, tipo: str):
    data = await file.read(csvio.MAX_BYTES + 1)
    try:
        res = fn(ctx.db, ctx.workspace_id, data, file.filename or "arquivo.csv")
        ctx.db.commit()
    except csvio.CsvError as e:
        ctx.db.rollback()
        return render(request, "import.html", ctx, status=422, ativo="configuracoes", error=str(e),
                      col_contatos=csvio.CONTATO_COLUNAS, col_empresas=csvio.EMPRESA_COLUNAS)
    return render(request, "import_result.html", ctx, res=res, tipo=tipo, ativo="configuracoes")


@router.post("/configuracoes/importar/contatos")
async def import_contatos(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(editor_ctx)):
    return await _handle(request, ctx, file, csvio.import_contacts, "contatos")


@router.post("/configuracoes/importar/empresas")
async def import_empresas(request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(editor_ctx)):
    return await _handle(request, ctx, file, csvio.import_companies, "empresas")


def _csv_response(stream, name: str):
    return StreamingResponse(stream, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/exportar/contatos.csv")
def export_contatos(ctx: Ctx = Depends(current_ctx)):
    return _csv_response(csvio.export_contacts(ctx.db, ctx.workspace_id), "contatos.csv")


@router.get("/exportar/empresas.csv")
def export_empresas(ctx: Ctx = Depends(current_ctx)):
    return _csv_response(csvio.export_companies(ctx.db, ctx.workspace_id), "empresas.csv")


@router.get("/configuracoes/modelo/contatos.csv")
def modelo_contatos(ctx: Ctx = Depends(current_ctx)):
    body = csvio.template_csv(csvio.CONTATO_COLUNAS, ["Maria Souza", "maria@empresa.com.br", "Gerente de Marketing", "Marketing e Comunicação", "", "empresa.com.br"])
    return PlainTextResponse(body, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="modelo_contatos.csv"'})


@router.get("/configuracoes/modelo/empresas.csv")
def modelo_empresas(ctx: Ctx = Depends(current_ctx)):
    body = csvio.template_csv(csvio.EMPRESA_COLUNAS, ["", "Empresa Exemplo Ltda", "Exemplo", "empresa.com.br", "https://empresa.com.br", "Patos de Minas", "MG", "ME"])
    return PlainTextResponse(body, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="modelo_empresas.csv"'})
