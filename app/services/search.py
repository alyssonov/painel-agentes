"""Busca por palavras, igual em todo o painel.

Cada palavra digitada precisa aparecer em algum lugar do item (título, SKU,
número...), em QUALQUER ordem, sem diferenciar maiúsculas, minúsculas e
acentos. Ex.: "protetor biz" encontra "Protetor Escapamento Biz"; "pecas"
encontra "Peças". Palavras de ligação ("de", "da", "para"...) são ignoradas.
"""

import re
import unicodedata

# Palavras de ligação que não ajudam a achar o produto.
STOPWORDS = {"de", "da", "do", "das", "dos", "e", "para", "pra", "com", "a", "o", "as", "os", "em", "p/", "c/"}


def normalize(text: str | None) -> str:
    """Minúsculas, sem acentos, só letras/números separados por espaço."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def terms(query: str | None) -> list[str]:
    """Palavras da busca (sem repetidas e sem as de ligação, a não ser que só reste ela)."""
    words = list(dict.fromkeys(normalize(query).split()))
    useful = [w for w in words if w not in STOPWORDS]
    return useful or words


def matches(query_terms: list[str], *texts: str | None) -> bool:
    """Todas as palavras aparecem em algum dos textos (em qualquer ordem)?

    Sem espaços também vale: "cg160" encontra "CG 160" e "cg 160" encontra "CG160".
    """
    if not query_terms:
        return True
    haystack = " ".join(normalize(t) for t in texts if t)
    compact = haystack.replace(" ", "")
    return all(term in haystack or term in compact for term in query_terms)
