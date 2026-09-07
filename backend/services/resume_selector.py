"""
Auto-select the appropriate resume for a job based on role keywords.

Selection rules (highest score wins; title matches weigh double):

  Role                                             -> Resume
  Python/Backend/AI/Software/Full-stack developer  -> ShamimAhamed_Python_Resume (1).pdf
  Data/Business Analyst                            -> SHAMIM_AHAMED_J_Data_Analyst_PowerBI_Resume.pdf
  ML / Deep Learning / Data Science                -> SHAMIM_AHAMED_J_ML_AI_Engineer_Resume.pdf
  Network / IT Support / Helpdesk                  -> SHAMIM_AHAMED_J_Network_Technical_Support_Resume.pdf
  QA / Testing                                     -> ShamimAhamed_Testing_Resume_V2.pdf
  Frontend / UI / React / Node.js / Web            -> SHAMIM_AHAMED_J_FlowCV_Resume_2026-06-09.pdf
  Any other IT/tech role (default fallback)        -> SHAMIM_AHAMED_J_FlowCV_Resume_2026-06-09.pdf
  Any non-IT / non-tech role                       -> Shamim_Ahamed_NonIT_Resume.pdf

Resume filenames are resolved at runtime — if the file is missing, the rule is skipped.
"""
import re
from pathlib import Path
from config import Config


class ResumeSelector:
    # (filename, label, keywords). Order matters: only conflict resolution.
    RULES = [
        (
            "ShamimAhamed_Python_Resume (1).pdf",
            "Python/Software Developer",
            [
                "python", "django", "fastapi", "flask", "backend",
                "software developer", "software development", "software engineer",
                "full stack", "fullstack", "api developer", "artificial intelligence",
                "ai engineer", "ai developer", " ai ",
            ],
        ),
        (
            "SHAMIM_AHAMED_J_Data_Analyst_PowerBI_Resume.pdf",
            "Data/Business Analyst",
            [
                "data analyst", "data analysis", "power bi", "powerbi", "tableau",
                "business analyst", "analytics", "sql developer", "data analyst intern",
                "bi ", "bi developer",
            ],
        ),
        (
            "SHAMIM_AHAMED_J_ML_AI_Engineer_Resume.pdf",
            "ML/AI Engineer",
            [
                "machine learning", "deep learning", "nlp", "data science",
                "computer vision", "mlops", "ml engineer", " ml ", "ml developer",
            ],
        ),
        (
            "SHAMIM_AHAMED_J_Network_Technical_Support_Resume.pdf",
            "Network/IT Support",
            [
                "network", "technical support", "it support", "network support",
                "helpdesk", "help desk", "system admin", "sysadmin", "network engineer",
                "desktop support", "it helpdesk", "it help desk",
            ],
        ),
        (
            "ShamimAhamed_Testing_Resume_V2.pdf",
            "QA/Testing",
            [
                "qa", "quality assurance", "quality analyst", "software tester",
                "automation tester", "test engineer", "test analyst", "testing",
            ],
        ),
        (
            "SHAMIM_AHAMED_J_FlowCV_Resume_2026-06-09.pdf",
            "Frontend/Full-stack IT (FlowCV)",
            [
                "frontend", "front end", "front-end", "ui developer", "ui/ux",
                "ux developer", "ux", "react", "reactjs", "react js", "node",
                "nodejs", "node.js", "javascript", "typescript", "angular",
                "vue", "web developer", "html", "css", "bootstrap", "tailwind",
                "android", "ios", "flutter", "mobile developer", "native mobile",
                "app developer", "mobile apps", "mobile app",
            ],
        ),
        (
            "Shamim_Ahamed_NonIT_Resume.pdf",
            "Non-IT / General",
            [
                "sales", "marketing", "hr", "human resource", "human resources",
                "admin", "administration", "administrative", "operation",
                "operations", "accountant", "accounting", "finance", "accounts",
                "customer service", "customer support", "customer care", "call center",
                "receptionist", "data entry", "business development", "recruiter",
                "clerk", "retail", "content writer", "content writing", "digital marketing",
                "social media", "teacher", "office", "executive", "coordinator",
                "telecaller", "telesales", "back office", "non it", "nonit",
            ],
        ),
    ]

    NON_IT_FALLBACK = "Shamim_Ahamed_NonIT_Resume.pdf"
    IT_FALLBACK = "SHAMIM_AHAMED_J_FlowCV_Resume_2026-06-09.pdf"
    DEFAULT_FALLBACK = IT_FALLBACK

    def __init__(self):
        self.resumes_dir = Config.RESUMES_DIR
        self.available = self.list_resumes()

    def list_resumes(self) -> list:
        return sorted(
            [f.name for f in self.resumes_dir.iterdir() if f.suffix.lower() == ".pdf"]
        )

    def _keyword_in_text(self, keyword: str, text: str) -> bool:
        """Match single words only as whole words, phrases as substrings."""
        keyword = keyword.strip()
        if not keyword:
            return False
        if " " in keyword or "-" in keyword:
            return keyword in text
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None

    def _rule_scores(self, title: str, combined: str):
        """Return list of (rule_index, resume_file, score, matched_keywords)."""
        results = []
        for idx, (file, label, keywords) in enumerate(self.RULES):
            if file not in self.available:
                continue
            title_hits = [k for k in keywords if self._keyword_in_text(k, title)]
            full_hits = [k for k in keywords if self._keyword_in_text(k, combined)]
            # Weight by keyword length (specificity); title counts double.
            score = sum(len(k) for k in title_hits) * 2 + sum(len(k) for k in full_hits)
            if score > 0:
                results.append((idx, file, score, title_hits + full_hits))
        return results

    def select_for_job(self, job_title: str, job_description: str = "") -> str:
        """Return the resume filename best matching the job."""
        if not self.available:
            return None

        title = (job_title or "").lower()
        combined = f"{title} {job_description or ''}".lower()

        scored = self._rule_scores(title, combined)
        if scored:
            # Highest score wins; tie-break: earliest rule in RULES list.
            scored.sort(key=lambda r: (-r[2], r[0]))
            return scored[0][1]

        # No keyword matched. Decide IT vs non-IT by checking non-IT vocabulary.
        nonit_words = ["sales", "marketing", "hr", "admin", "operation", "account",
                       "finance", "customer", "call center", "receptionist", "data entry",
                       "recruiter", "clerk", "retail", "content", "social media",
                       "teacher", "office", "coordinator", "executive", "telecaller"]
        combined_lower = combined
        for w in nonit_words:
            if self._keyword_in_text(w, combined_lower) or f" {w} " in f" {combined_lower} ":
                return self._resolve(self.NON_IT_FALLBACK)
        # Default: it's a tech/other role -> FlowCV
        return self._resolve(self.IT_FALLBACK)

    def _resolve(self, filename) -> str:
        if filename in self.available:
            return filename
        return self.available[0]

    def explain(self, job_title: str, job_description: str = "") -> str:
        """Human-readable explanation of the selection."""
        chosen = self.select_for_job(job_title, job_description)
        if not chosen:
            return "No resumes available in resumes/ folder."

        title = (job_title or "").lower()
        combined = f"{title} {job_description or ''}".lower()

        # Find which rule matched
        for file, label, keywords in self.RULES:
            if file != chosen:
                continue
            title_hits = [k for k in keywords if self._keyword_in_text(k, title)]
            full_hits = [k for k in keywords if self._keyword_in_text(k, combined)]
            matched = (title_hits + full_hits) or None
            if matched:
                where = "title" if title_hits else "description"
                return (f"Chosen: {chosen} ({label})\n"
                        f"Why: keywords {', '.join(dict.fromkeys(matched))} matched in job {where} "
                        f"({', '.join(keywords)})")
            break

        # No rule keyword matched — explain the fallback
        nonit_words = ["sales", "marketing", "hr", "admin", "operation", "account",
                       "finance", "customer", "call center", "receptionist", "data entry",
                       "recruiter", "clerk", "retail", "content", "social media",
                       "teacher", "office", "coordinator", "executive", "telecaller"]
        nonit_hit = any(self._keyword_in_text(w, combined) or f" {w} " in f" {combined} " for w in nonit_words)
        why = ("Non-IT/non-technical role -> NonIT resume"
               if nonit_hit else
               "No role-specific match -> default IT fallback (FlowCV)")
        label = next((r[1] for r in self.RULES if r[0] == chosen), "Generic")
        return f"Chosen: {chosen} ({label})\nWhy: {why}"

    def resume_path(self, filename: str) -> Path:
        return self.resumes_dir / filename