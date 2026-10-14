"""Find the nearest market for a commodity (worker thread): Spansh first, Ardent when Spansh fails.

Spansh filters the market itself; ED Multitool's own filters (pad, carrier access, stations that would refuse
you docking) can still drop rows, so pages are asked one after another until `count` rows are kept, Spansh
has no more, or MAX_PAGES were asked. A Spansh reply with no rows is an answer, not a failure: Ardent is
asked only when Spansh could not be reached or answered with an error.
"""

import threading

from edmultitool.core.market import MarketFound, MarketQuery, MarketRow, Standings, keep, usable
from edmultitool.sources import ardent, spansh
from edmultitool.sources.http import Cancelled, PoliteClient, SourceError, brief, explain

MAX_PAGES = 3


def find(client: PoliteClient, q: MarketQuery, you: Standings, cancel: threading.Event) -> MarketFound:
    """`you` (a snapshot) only decides how many pages are enough; the plugin decides who may dock."""
    try:
        return _spansh(client, q, you, cancel)
    except Cancelled:
        raise
    except SourceError as exc:
        failed = brief(explain(exc))
    try:
        rows = usable(ardent.nearby(client, q, cancel), q)
    except Cancelled:
        raise
    except SourceError as exc:
        backup = explain(exc)
        if brief(backup) == failed:  # the same trouble twice: said once, with its advice
            raise SourceError(f"{failed}, and the backup Ardent too{backup[len(failed) :]}") from exc
        raise SourceError(f"{failed}, and the backup Ardent too: {backup}") from exc
    return MarketFound(tuple(rows), "Ardent", note=f"Spansh: {failed}")


def _spansh(client: PoliteClient, q: MarketQuery, you: Standings, cancel: threading.Event) -> MarketFound:
    got: list[MarketRow] = []
    for page in range(MAX_PAGES):
        try:
            rows, total = spansh.market_page(client, q, page, cancel)
        except Cancelled:
            raise
        except SourceError:
            if page == 0:
                raise
            break  # a later page failed: the nearer stations already found still stand
        got.extend(rows)
        kept, _barred = keep(got, q, you)
        if len(kept) >= q.count or not rows or (page + 1) * spansh.MARKET_PAGE >= total:
            break
    return MarketFound(tuple(usable(got, q)), "Spansh")
