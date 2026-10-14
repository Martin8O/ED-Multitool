"""Find the nearest stations your ship can land at with the services asked for (worker thread): Spansh's
station search.

ED Multitool's own filters (medium pad, stations that would refuse you docking) can drop rows, so pages are
asked one after another until enough rows are kept, Spansh has no more, or MAX_PAGES were asked. Spansh orders
by the system's distance only: within one system the order is arbitrary, so a page that ends in the same
system as the last row shown asks for the next one too (a station nearer the arrival star may be on it).
"""

import math
import threading

from edmultitool.core.market import Standings
from edmultitool.core.nearby import STATIONS_SHOWN, ServiceQuery, ServiceRow, split_barred, usable
from edmultitool.sources import spansh
from edmultitool.sources.http import Cancelled, PoliteClient, SourceError

MAX_PAGES = 4


def find(client: PoliteClient, q: ServiceQuery, you: Standings, cancel: threading.Event) -> list[ServiceRow]:
    """Every usable row found, nearest first. `you` (a snapshot) only decides how many pages are enough;
    the plugin decides who may dock with the standings known when the answer comes."""
    got: list[ServiceRow] = []
    for page in range(MAX_PAGES):
        try:
            rows, total = spansh.stations_page(client, q, page, cancel)
        except Cancelled:
            raise
        except SourceError:
            if page == 0:
                raise
            break  # a later page failed: the nearer stations already found still stand
        got.extend(rows)
        if not rows or (page + 1) * spansh.MARKET_PAGE >= total:
            break
        kept, _barred = split_barred(usable(got, q), q.request, you)
        if len(kept) >= STATIONS_SHOWN:
            last_shown = _ly(q, kept[STATIONS_SHOWN - 1])
            if _ly(q, rows[-1]) > last_shown + 0.005:
                break  # this page already went past the last system shown: nothing nearer can follow
    return usable(got, q)


def _ly(q: ServiceQuery, r: ServiceRow) -> float:
    return math.dist(q.star_pos, (r.x, r.y, r.z))
