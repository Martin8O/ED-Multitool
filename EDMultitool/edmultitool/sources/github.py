"""GitHub's public releases API: the newest release of this project (no token; see docs/api-notes.md)."""

import threading

from edmultitool.core.updates import Release, parse_release
from edmultitool.sources.http import BadReply, HttpError, PoliteClient

URL = "https://api.github.com/repos/Martin8O/ED-Multitool/releases/latest"
DAY = 24 * 3600.0  # a reply stands this long: the check asks GitHub about once a day


def latest_release(client: PoliteClient, cancel: threading.Event) -> Release | None:
    """The newest published release; None when there is none yet (404: no release, or the repository
    cannot be seen). Raises SourceError on every other failure."""
    resp = client.get(URL, cancel, ttl=DAY, valid=lambda doc: parse_release(doc) is not None)
    if resp.status == 404:
        return None
    if resp.status != 200:
        raise HttpError(resp.status, f"GitHub: HTTP {resp.status}")
    release = parse_release(resp.json())
    if release is None:
        raise BadReply("GitHub's release reply is not one this version can read")
    return release
