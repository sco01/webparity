# SPDX-License-Identifier: BSD-2-Clause
"""New sites from the visits: linked domains and sub pages, within the limits."""

from webparity.db import DB
from webparity.discover import Discoverer, choose_subpages

CONFIG = {"discover": {}, "worker": {"exclude": {"domains": ["corp.se"]}}}


def visit(url, links=(), internal=()):
    return {
        "url": url,
        "browsers": {"ladybird": {"probe": {"link_domains": list(links), "internal_links": list(internal)}}},
    }


def rows(db):
    return {r["domain"]: (r["source"], r["priority"]) for r in db.q("SELECT domain, source, priority FROM sites")}


def test_linked_domains_are_queued_within_limits(tmp_path):
    db = DB(tmp_path / "w.db")
    db.add_site("https://home.se/")
    for i in range(5):  # big.se already has five rows (one retired)
        db.x(
            "INSERT INTO sites (url, domain, status) VALUES (?, ?, 'retired')",
            (f"https://a{i}.big.se/", f"a{i}.big.se"),
        )
    links = [
        "www.new.se",
        "other.com",
        "x.big.se",
        "jobs.corp.se",
        "sub.home.se",
        "new.se",
        *[f"s{i}.se" for i in range(30)],
    ]
    found = Discoverer(db, CONFIG).visit(visit("https://home.se/page/", links))
    got = rows(db)
    assert got["new.se"] == ("länk:home.se", 30)
    assert "other.com" not in got and "x.big.se" not in got and "jobs.corp.se" not in got and "sub.home.se" not in got
    assert found == {"domains": 15, "subpages": 0}  # new.se and 14 of the s*.se, then the limit per visit


def test_front_pages_get_two_subpages_and_pages_none(tmp_path):
    db = DB(tmp_path / "w.db")
    db.add_site("https://shop.se/")
    internal = [
        "https://shop.se/om-oss",
        "https://shop.se/nyheter/2026/vi-har-oppnat-en-ny-butik-i-centrum",
        "https://shop.se/produkter/stolar",
        "https://shop.se/api/x",
        "https://elsewhere.se/page",
    ]
    d = Discoverer(db, CONFIG)
    assert d.visit(visit("https://shop.se/", internal=internal))["subpages"] == 2
    got = rows(db)
    assert got["shop.se/nyheter/2026/vi-har-oppnat-en-ny-butik-i-centrum"] == ("undersida:shop.se", 25)
    assert sum(k.startswith("shop.se/") for k in got) == 2
    assert d.visit(visit("https://shop.se/om-oss/", internal=internal))["subpages"] == 0


def test_the_count_per_domain_is_shared_with_subpages(tmp_path):
    db = DB(tmp_path / "w.db")
    for key in ("tiny.se", "tiny.se/a", "tiny.se/b", "x.tiny.se"):
        db.x("INSERT INTO sites (url, domain) VALUES (?, ?)", ("https://" + key + "/", key))
    d = Discoverer(db, CONFIG, cache=True)
    internal = ["https://tiny.se/c/long-article-slug-here", "https://tiny.se/d/e"]
    assert d.visit(visit("https://tiny.se/", internal=internal))["subpages"] == 1  # four rows, room for one


def test_choose_subpages_prefers_articles_and_other_sections():
    urls = [
        "https://a.se/kontakt",
        "https://a.se/blogg/ett-langt-inlagg-om-saker",
        "https://a.se/blogg/x",
        "https://a.se/tjanster/bygg",
    ]
    assert choose_subpages(urls) == ["https://a.se/blogg/ett-langt-inlagg-om-saker", "https://a.se/tjanster/bygg"]
