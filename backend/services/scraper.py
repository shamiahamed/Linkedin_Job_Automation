"""
Scrape a LinkedIn job post from a public URL.
NOTE: LinkedIn blocks anonymous scraping. This service works best with:
  1. The Chrome extension (which reads the live DOM while you're logged in), OR
  2. An authenticated session cookie
"""
import re
import requests
from bs4 import BeautifulSoup


class LinkedInScraper:
    def __init__(self, session_cookie: str = None):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        if session_cookie:
            self.headers["Cookie"] = session_cookie

    def fetch_job(self, url: str) -> dict:
        """Fetch and parse a LinkedIn job detail page."""
        resp = requests.get(url, headers=self.headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title = ""
        title_el = soup.select_one(
            "h1.top-card-layout__title, "
            ".job-details-jobs-unified-top-card__job-title, "
            "h1[class*='job-title']"
        )
        if title_el:
            title = title_el.get_text(strip=True)

        # Company
        company = ""
        company_el = soup.select_one(
            ".top-card-layout__second-subline a, "
            ".job-details-jobs-unified-top-card__company-name, "
            "[class*='company-name']"
        )
        if company_el:
            company = company_el.get_text(strip=True)

        # Location (scan meta content)
        location = self._extract_meta(soup, "location")

        # Description
        description = ""
        desc_el = soup.select_one(
            ".show-more-less-html__markup, "
            ".jobs-description__content, "
            ".job-details-jobs-unified-top-card__description"
        )
        if desc_el:
            description = desc_el.get_text("\n", strip=True)

        from services.job_parser import JobParser
        emails = JobParser.extract_email(description)
        phones = JobParser.extract_phone(description)
        experience = JobParser.extract_experience(description)

        return {
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "description": description,
            "emails": emails,
            "phones": phones,
            "experience": experience,
            "has_email": len(emails) > 0,
            "has_phone": len(phones) > 0,
            "source": "linkedin_scraper",
        }

    def _extract_meta(self, soup, prop: str) -> str:
        el = soup.find("meta", attrs={"property": f"og:{prop}"}) or \
             soup.find("meta", attrs={"name": f"og:{prop}"})
        if el and el.get("content"):
            return el["content"].strip()
        return ""