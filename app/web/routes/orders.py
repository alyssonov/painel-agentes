"""Central de pedidos (Operação): lista com filtros, planilha e lista de separação. Só leitura."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.timeutil import local_today, to_local
from app.models.enums import CHANNEL_LABELS, Channel
from app.models.user import User
from app.services.orders import PERIODS, SITUATIONS, OrderFilter, list_orders, pick_list, to_csv
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter(prefix="/pedidos")


def _filter(periodo: str, canal: str, situacao: str, busca: str, pagina: int) -> OrderFilter:
    return OrderFilter.from_query(periodo, canal, situacao, busca, pagina)


@router.get("")
def orders_page(request: Request, periodo: str = "7", canal: str = "", situacao: str = "", busca: str = "",
                pagina: int = 1, db: Session = Depends(get_db), user: User = Depends(current_user)):
    f = _filter(periodo, canal, situacao, busca, pagina)
    result = list_orders(db, f)
    return render(request, "orders.html", {
        "r": result, "f": f, "periods": PERIODS, "situations": SITUATIONS,
        "channels": {c.value: CHANNEL_LABELS[c] for c in Channel},
    }, db=db)


@router.get(".csv")
def orders_csv(periodo: str = "7", canal: str = "", situacao: str = "", busca: str = "",
               db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = list_orders(db, _filter(periodo, canal, situacao, busca, 1), paginate=False)
    filename = f"pedidos-{local_today():%Y-%m-%d}.csv"
    return Response(to_csv(result), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/separacao")
def pick_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return render(request, "picking.html", {"p": pick_list(db), "now": to_local(utcnow())}, db=db)
