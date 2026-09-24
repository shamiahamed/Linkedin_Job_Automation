"""
Phase 1 tests — Google RSS source.

Covers the deterministic RSS parser + fetch/normalize path required by the
spec: basic parsing, malformed feeds, missing description, missing company,
duplicate URLs, multiple feeds, and source failure. Uses only the stdlib
(unittest + mock) so the suite runs anywhere Python does.
"""
import unittest
from unittest import mock

import requests

from config import Config
from services import rss_source


def _item(title="Software Engineer", url="http://example.com/job1", description="Build things", company=""):
    company_tag = f"<dc:creator xmlns:dc=\"http://purl.org/dc/elements/1.1/\">{company}</dc:creator>" if company else ""
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>{url}</link>"
        f"<guid isPermaLink=\"false\">{url}</guid>"
        f"<description>{description}</description>"
        f"<pubDate>Wed, 24 Sep 2026 09:00:00 GMT</pubDate>"
        f"{company_tag}"
        "</item>"
    )


def _rss(item_bodies):
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\">"
        "<channel><title>Google Jobs</title><link>http://example.com/</link>"
        "<description>feed</description>"
        + "".join(item_bodies) +
        "</channel></rss>"
    )


class RssParsingTests(unittest.TestCase):
    def test_parse_rss_basic(self):
        feed = _rss([
            _item(title="Python Developer", url="http://x/1", description="Python role", company="Acme Corp"),
            _item(title="QA Tester", url="http://x/2", description="QA role", company="Beta Ltd"),
        ])
        items = rss_source.parse_rss(feed)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "Python Developer")
        self.assertEqual(first["link"], "http://x/1")
        self.assertEqual(first["guid"], "http://x/1")
        self.assertEqual(first["description"], "Python role")
        self.assertEqual(first["company"], "Acme Corp")
        self.assertIn("2026", first["published_at"])

    def test_parse_rss_malformed(self):
        self.assertEqual(rss_source.parse_rss(""), [])
        self.assertEqual(rss_source.parse_rss("<rss><channel><item><title>oops"), [])
        self.assertEqual(rss_source.parse_rss("this is not xml at all"), [])
        self.assertEqual(rss_source.parse_rss(None), [])

    def test_parse_rss_missing_description(self):
        items = rss_source.parse_rss(_rss([
            "<item><title>Data Analyst</title><link>http://x/3</link></item>"
        ]))
        self.assertEqual(len(items), 1)
        job = rss_source.to_job_dict(items[0])
        self.assertEqual(job["description"], "")
        self.assertEqual(job["title"], "Data Analyst")
        self.assertEqual(job["url"], "http://x/3")

    def test_parse_rss_missing_company(self):
        items = rss_source.parse_rss(_rss([
            _item(title="React Developer", url="http://x/4", description="No company here")
        ]))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["company"], "")
        job = rss_source.to_job_dict(items[0])
        self.assertEqual(job["company"], "")
        self.assertEqual(job["location"], "")

    def test_atom_feed_parses(self):
        atom = (
            "<?xml version=\"1.0\"?><feed xmlns=\"http://www.w3.org/2005/Atom\">"
            "<title>Jobs</title>"
            "<entry><title>DevOps Engineer</title>"
            "<id>tag:example,2026:5</id>"
            "<link href=\"http://x/atom1\"/>"
            "<summary>Some summary</summary>"
            "<published>2026-09-24T09:00:00Z</published>"
            "<author><name>Mega Inc</name></author></entry>"
            "</feed>"
        )
        items = rss_source.parse_rss(atom)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "DevOps Engineer")
        self.assertEqual(items[0]["link"], "http://x/atom1")
        self.assertEqual(items[0]["company"], "Mega Inc")
        self.assertIn("2026", items[0]["published_at"])


class RssNormalizationTests(unittest.TestCase):
    def test_job_dict_shape_matches_ingest_contract(self):
        items = rss_source.parse_rss(_rss([
            _item(title="Fresher Software Engineer", url="http://x/5",
                  description="Freshers welcome. Python.", company="Tech House"),
            _item(title="Ops Manager Transport", url="http://x/6",
                  description="5+ years required.", company="Logi Co"),
        ]))
        jobs = [rss_source.to_job_dict(i) for i in items]
        expected_keys = {
            "title", "company", "location", "url", "description", "emails",
            "phones", "experience", "salary", "source", "apply_link", "published_at",
        }
        for job in jobs:
            self.assertEqual(set(job.keys()), expected_keys)
            self.assertEqual(job["source"], "auto_fetch")
            self.assertEqual(job["apply_link"], job["url"])
            self.assertEqual(job["emails"], [])
            self.assertEqual(job["phones"], [])
        # Fresher title -> "Fresher" label; senior title -> filtered out later.
        self.assertEqual(jobs[0]["experience"], "Fresher")
        self.assertTrue(rss_source.is_senior_role(jobs[1]["title"], jobs[1]["description"]))

    def test_default_searches_cover_initial_set(self):
        pairs = rss_source.configured_searches()
        self.assertEqual(len(pairs), 11)
        searches = {(q, loc) for q, loc in pairs}
        for query, location in [
            ("react developer", "Chennai"),
            ("python developer", "Chennai"),
            ("qa tester", "Chennai"),
            ("technical support", "Chennai"),
            ("software developer", "Chennai"),
            ("walk-in interview", "Chennai"),
            ("walk-in interview", "Madurai"),
            ("react developer", "Madurai"),
            ("python developer", "Madurai"),
            ("software freshers", "Tamil Nadu"),
            ("freshers hiring", "Tamil Nadu"),
        ]:
            self.assertIn((query, location), searches)


class RssFetchTests(unittest.TestCase):
    def setUp(self):
        # Network boundary mocked away; build_urls bypassed with explicit urls.
        patcher = mock.patch.object(rss_source.Config, "GOOGLE_RSS_FEED_URLS", "http://feed.example/a,http://feed.example/b")
        patcher.start()
        self.addCleanup(patcher.stop)
        mock.patch.object(rss_source.Config, "GOOGLE_RSS_ENABLED", "true").start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(rss_source, "fetch_one", side_effect=ValueError("must mock fetch_one")).start()

    def _patch_fetch(self, fn):
        mock.patch.object(rss_source, "fetch_one", side_effect=fn).start()

    def test_duplicate_url_deduped(self):
        feed = _rss([
            _item(title="Python Developer", url="http://x/dup", description="same job"),
            _item(title="Python Developer", url="http://x/dup", description="same job"),
        ])
        self._patch_fetch(lambda url: feed)
        jobs = rss_source.fetch_google_rss_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "http://x/dup")

    def test_multiple_feeds_merged(self):
        feed_a = _rss([_item(title="Python Developer", url="http://x/a", company="A")])
        feed_b = _rss([_item(title="QA Tester", url="http://x/b", company="B")])

        def fake(url):
            if url.endswith("/a"):
                return feed_a
            return feed_b

        self._patch_fetch(fake)
        jobs = rss_source.fetch_google_rss_jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual({j["title"] for j in jobs}, {"Python Developer", "QA Tester"})
        self.assertTrue(all(j["source"] == "auto_fetch" for j in jobs))

    def test_source_failure_returns_empty_without_raising(self):
        def fake(url):
            return None  # every feed times out / 404s

        self._patch_fetch(fake)
        self.assertEqual(rss_source.fetch_google_rss_jobs(), [])

    def test_failing_feed_does_not_block_healthy_feed(self):
        feed_ok = _rss([_item(title="React Developer", url="http://x/ok", company="Ok Inc")])

        def fake(url):
            if url.endswith("/a"):
                raise requests.ConnectionError("boom")
            return feed_ok

        self._patch_fetch(fake)
        jobs = rss_source.fetch_google_rss_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "React Developer")

    def test_senior_titles_filtered_at_fetch(self):
        feed = _rss([
            _item(title="Senior Engineer", url="http://x/senior", description="Lead platform work"),
            _item(title="Junior Developer", url="http://x/junior", description="Entry role"),
        ])
        self._patch_fetch(lambda url: feed)
        jobs = rss_source.fetch_google_rss_jobs()
        self.assertEqual([j["title"] for j in jobs], ["Junior Developer"])

    def test_disabled_source_returns_nothing(self):
        mock.patch.object(rss_source.Config, "GOOGLE_RSS_ENABLED", "0").start()
        self.assertEqual(rss_source.fetch_google_rss_jobs(), [])


if __name__ == "__main__":
    unittest.main()