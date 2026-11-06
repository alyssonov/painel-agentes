"""Limitador de requisições guiado pelos cabeçalhos da própria API.

O Olist informa em cada resposta (documentação "Limites de requisição"):
    X-RateLimit-Limit      limite total por minuto
    X-RateLimit-Remaining  quanto ainda resta no minuto atual
    X-RateLimit-Reset      segundos até o limite ser renovado
Quando está quase no fim, esperamos o reset em vez de levar um erro.
"""

import threading
import time
from collections.abc import Callable, Mapping


class HeaderRateLimiter:
    def __init__(self, *, reserve: int = 2, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        # reserve: quantas chamadas deixar sobrando (outros sistemas usam a mesma conta).
        self.reserve = reserve
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._remaining: int | None = None
        self._reset_at: float | None = None

    def wait(self) -> None:
        """Chamado antes de cada requisição: espera se o limite do minuto acabou."""
        with self._lock:
            if self._remaining is None or self._reset_at is None:
                return
            now = self._clock()
            if now >= self._reset_at:
                self._remaining = None
                return
            if self._remaining <= self.reserve:
                delay = self._reset_at - now
                self._remaining = None
                self._sleep(delay)

    def update(self, headers: Mapping[str, str]) -> None:
        """Chamado depois de cada resposta, com os cabeçalhos recebidos."""
        remaining = _to_int(headers.get("X-RateLimit-Remaining"))
        reset = _to_int(headers.get("X-RateLimit-Reset"))
        if remaining is None:
            return
        with self._lock:
            self._remaining = remaining
            self._reset_at = self._clock() + (reset if reset is not None else 60)

    def reset_delay(self, headers: Mapping[str, str], default: float = 30) -> float:
        """Quantos segundos esperar depois de um erro 429 (limite estourado)."""
        reset = _to_int(headers.get("X-RateLimit-Reset")) or _to_int(headers.get("Retry-After"))
        return float(reset) if reset is not None else default


def _to_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except ValueError:
        return None
