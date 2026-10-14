""" "A new version is out": reading GitHub's latest-release reply and comparing versions. Pure: no network."""

import re
from dataclasses import dataclass
from typing import Any

RELEASE_URL_PREFIX = "https://github.com/"  # a release page link from anywhere else is not shown
MAX_TAG_LEN = 40

_VERSION = re.compile(
    r"^v?(\d{1,6})\.(\d{1,6})(?:\.(\d{1,6}))?(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?(?:\+[0-9A-Za-z.-]+)?$"
)

# (major, minor, patch, pre-release identifiers); no identifiers = a final release
Version = tuple[int, int, int, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class Release:
    """The newest published release: its tag as GitHub has it, its page, its title."""

    tag: str
    url: str
    name: str = ""

    @property
    def version(self) -> str:
        return self.tag.removeprefix("v")


@dataclass(frozen=True, slots=True)
class UiUpdate:
    """What the page and the EDMC panel say about a newer version; empty `version` = nothing to say."""

    version: str = ""
    url: str = ""

    @property
    def text(self) -> str:
        return f"Version {self.version} is out: click for what's new and the download" if self.version else ""


def parse_version(text: str) -> Version | None:
    """ "0.31.0", "v0.31", "1.0.0-beta.2" → comparable parts; None for anything else."""
    m = _VERSION.match(text.strip())
    if m is None:
        return None
    major, minor, patch, pre = m.groups()
    return int(major), int(minor), int(patch or 0), tuple(pre.split(".")) if pre else ()


def _pre_key(identifiers: tuple[str, ...]) -> tuple[Any, ...]:
    """Pre-release order as semantic versioning has it: a final release is newer than any of its
    pre-releases; numbers compare as numbers and come before words; a longer list is newer."""
    if not identifiers:
        return (1,)
    return (0, *((0, int(i), "") if i.isdigit() else (1, 0, i) for i in identifiers))


def _key(v: Version) -> tuple[Any, ...]:
    return (v[0], v[1], v[2], _pre_key(v[3]))


def is_newer(current: str, latest: str) -> bool:
    """True when `latest` is a later version than `current`. A version either side cannot be read = False
    (never nag over something that cannot be compared)."""
    a, b = parse_version(current), parse_version(latest)
    return a is not None and b is not None and _key(b) > _key(a)


def parse_release(doc: Any) -> Release | None:
    """GitHub's "latest release" reply → Release; None when it is not one, is a draft or pre-release, or
    carries a tag or link that cannot be trusted."""
    if not isinstance(doc, dict) or doc.get("draft") is True or doc.get("prerelease") is True:
        return None
    tag, url, name = doc.get("tag_name"), doc.get("html_url"), doc.get("name")
    if not isinstance(tag, str) or len(tag) > MAX_TAG_LEN or parse_version(tag) is None:
        return None
    if not isinstance(url, str) or not url.startswith(RELEASE_URL_PREFIX) or len(url) > 300:
        return None
    return Release(tag.strip(), url, name[:100] if isinstance(name, str) else "")
