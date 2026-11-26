"""Markdown simples e SEGURO para as respostas do Assistente.

Tudo é escapado primeiro (nenhum HTML da IA chega à página). Depois entram só:
títulos, parágrafos, listas, **negrito**, _itálico_, `código`, links (internos
"/..." ou https, estes em nova aba) e tabelas — cada tabela com link para baixar
como planilha.
"""

import html
import re

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])[_*](?!\s)(.+?)(?<!\s)[_*](?![\w*])")
_CODE = re.compile(r"`([^`]+)`")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _safe_url(url: str) -> tuple[str, bool] | None:
    """(url, é_externa) ou None se não for permitida."""
    url = html.unescape(url)
    if url.startswith("/") and not url.startswith("//"):
        return url, False
    if url.startswith("https://"):
        return url, True
    return None


def _inline(text: str) -> str:
    out = html.escape(text, quote=True)
    out = _CODE.sub(r"<code>\1</code>", out)

    def link(m: re.Match) -> str:
        safe = _safe_url(m.group(2))
        if safe is None:
            return m.group(1)
        url, external = safe
        attrs = ' target="_blank" rel="noopener noreferrer"' if external else ""
        return f'<a class="link" href="{html.escape(url, quote=True)}"{attrs}>{m.group(1)}</a>'

    out = _LINK.sub(link, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_tables(text: str) -> list[dict]:
    """Tabelas Markdown da resposta: [{"header": [...], "rows": [[...], ...]}]."""
    tables, lines, i = [], (text or "").splitlines(), 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _TABLE_SEP.match(lines[i + 1]):
            header = _cells(lines[i])
            rows, i = [], i + 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            tables.append({"header": header, "rows": rows})
            continue
        i += 1
    return tables


def render(text: str, table_url: str | None = None) -> str:
    """HTML da resposta. table_url: endereço base para baixar a tabela n ("…/tabela/{n}.csv")."""
    lines = (text or "").splitlines()
    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None
    table_n = 0

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if i < len(lines) - 1 and "|" in line and _TABLE_SEP.match(lines[i + 1]):
            flush_para(); close_list()
            header = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="table-wrap"><table class="table asst-table"><thead><tr>{head}</tr></thead>'
                       f"<tbody>{body}</tbody></table></div>")
            if table_url:
                out.append(f'<p class="asst-dl"><a class="link" href="{html.escape(table_url)}/{table_n}.csv">'
                           "Baixar esta tabela como planilha</a></p>")
            table_n += 1
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.*)$", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if not stripped:
            flush_para(); close_list()
        elif heading:
            flush_para(); close_list()
            level = min(len(heading.group(1)) + 2, 5)
            out.append(f"<h{level} class=\"asst-h\">{_inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline((bullet or number).group(1))}</li>")
        else:
            close_list()
            para.append(stripped)
        i += 1
    flush_para(); close_list()
    return "".join(out)
