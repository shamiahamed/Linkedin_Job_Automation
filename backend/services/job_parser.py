"""
Parse raw job text (from OCR or LinkedIn scrape) into structured job data.
Extracts: title, company, location, emails, phones, experience, website.
"""
import re


class JobParser:
    @staticmethod
    def extract_email(text: str) -> list:
        pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        return list(set(re.findall(pattern, text)))

    @staticmethod
    def extract_phone(text: str) -> list:
        # Handles +91-XXXXXXXXXX, (XXX) XXX-XXXX, XXX-XXX-XXXX, XXXXX XXXXX
        patterns = [
            r"\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
            r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
            r"\b\d{10}\b",
        ]
        phones = []
        for p in patterns:
            phones.extend(re.findall(p, text))
        cleaned = []
        for p in phones:
            digits = re.sub(r"\D", "", p)
            if len(digits) >= 10 and digits not in cleaned:
                cleaned.append(digits)
        return cleaned

    @staticmethod
    def extract_experience(text: str) -> str:
        pattern = r"(\d+(?:\s*-\s*\d+)?\s*\+?\s*(?:years?|yrs?))"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def extract_salary(text: str) -> str:
        # Requires a currency symbol OR a unit (LPA/lakh/K/PA/per-month) so
        # that experience ranges like "2-3 years" are not mistaken for salary.
        # (?<![A-Za-z0-9]) stops false matches like "Rs." inside "years."
        patterns = [
            # Currency-symbol prefix: Rs 6 LPA, $75k, ₹12 LPA, $75k - $90k
            r"(?<![A-Za-z0-9])(?:₹|Rs\.?|\$|€|£)\s?[\d,.]+\s*(?:k|K)?\s*[-–—to]*\s*(?:₹|Rs\.?|\$|€|£)?\s*[\d,.]*\s*(?:k|K)?\s*(?:LPA|lacs?|lakhs?|\bper month|/month|per annum|annually)?",
            # Bare number with unit: 6-8 LPA, 4.5 to 6 LPA, 5 lakhs
            r"(?<![A-Za-z0-9])[\d,.]+\s*[-–—to]*\s*[\d,.]*\s+(?:LPA|lacs?|lakhs?|per month|/month|per annum|annually)\b",
            # LPA/lakh without space: 6LPA, 12lpa
            r"(?<![A-Za-z0-9])[\d,.]+\s?(?:LPA|lacs?|lakhs?)\b",
        ]
        for p in patterns:
            match = re.search(p, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return ""

    @staticmethod
    def extract_company(text: str) -> str:
        # Look for common patterns after "at" or "company"
        patterns = [
            r"(?:at|Company|company|Organization|organisation)\s*[:.]?\s*([A-Za-z0-9&.\- ]{2,50})",
            r"([A-Za-z0-9&.\- ]{2,50})(?:.*(?:is hiring|hiring|recruiting))",
        ]
        for p in patterns:
            match = re.search(p, text)
            if match:
                candidate = match.group(1).strip(' :,.-')
                if len(candidate) > 1:
                    return candidate
        return ""

    @staticmethod
    def extract_title(text: str) -> str:
        # Look for role names at start or after "role/position"
        patterns = [
            r"(?:Position|Role|Title|Designation)\s*[:.]?\s*([A-Za-z0-9_/&\- ]{3,60})",
            r"(?:hiring|recruiting|looking for)\s+(?:a|an)?\s*([A-Za-z0-9_/&\- ]{3,60})",
            r"([A-Za-z0-9_/&\- ]{3,40}(?:Engineer|Developer|Analyst|Manager|Tester|Designer|Consultant|Specialist|Scientist|Architect))",
        ]
        for p in patterns:
            match = re.search(p, text)
            if match:
                candidate = match.group(1).strip(' :,.-')
                if len(candidate) > 2:
                    return candidate
        return ""

    @staticmethod
    def extract_salary_text(text: str) -> str:
        return JobParser.extract_salary(text)

    @staticmethod
    def parse(text: str) -> dict:
        if not text:
            return {}
        text = " ".join(text.split())

        emails = JobParser.extract_email(text)
        phones = JobParser.extract_phone(text)

        return {
            "title": JobParser.extract_title(text),
            "company": JobParser.extract_company(text),
            "emails": emails,
            "phones": phones,
            "experience": JobParser.extract_experience(text),
            "salary": JobParser.extract_salary(text),
            "description": text,
            "has_email": len(emails) > 0,
            "has_phone": len(phones) > 0,
            "source": "ocr"
        }
