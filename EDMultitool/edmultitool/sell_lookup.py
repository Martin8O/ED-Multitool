"""Find where to sell the hold (worker thread): one Spansh search per commodity, best price first.

ED Multitool's own filters (pad, carrier access, the distance measured again, stations that would refuse you
docking) can still drop rows, so a second page is asked when the first kept fewer than `SHOWN` stations and
Spansh has more. Spansh is the only source: Ardent cannot sort by price, and the nearest stations are not the
ones that pay best. Any commodity's search failing fails the whole search (half an answer would hide the best
station for the whole load).
"""

import threading

from edmultitool.core.market import Standings
from edmultitool.core.sell import SHOWN, SellFound, SellQuery, SellStation, usable
from edmultitool.sources import spansh
from edmultitool.sources.http import Cancelled, PoliteClient, SourceError

MAX_PAGES = 2


def find(client: PoliteClient, q: SellQuery, you: Standings, cancel: threading.Event) -> SellFound:
    """`you` (a snapshot) only decides how many pages are enough; the plugin decides who may dock."""
    out: list[tuple[str, tuple[SellStation, ...]]] = []
    for good in q.goods:
        got: list[SellStation] = []
        for page in range(MAX_PAGES):
            try:
                rows, total = spansh.sell_page(client, q, good, page, cancel)
            except Cancelled:
                raise
            except SourceError:
                if page == 0:
                    raise
                break  # a later page failed: the stations already found still stand
            got.extend(rows)
            kept = [r for r in usable(got, q, good) if not (q.request.hide_barred and you.barred(r))]
            if len(kept) >= SHOWN or not rows or (page + 1) * spansh.MARKET_PAGE >= total:
                break
        out.append((good.name, tuple(usable(got, q, good))))
    return SellFound(tuple(out))
