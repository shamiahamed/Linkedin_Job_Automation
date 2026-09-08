"""
Build email HTML content and cover-letter text for a job, then send via Brevo.
"""
from jinja2 import Environment, FileSystemLoader
from html import escape
from config import Config
from services.email_sender import EmailSender
from services.resume_selector import ResumeSelector


class EmailBuilder:
    def __init__(self, job, resume_override: dict = None, resume_pin: str = None):
        """resume_override: {'name': str, 'data': base64-str} from an uploaded Resume row.
        resume_pin: exact filename from the resumes/ folder to force for this job."""
        self.job = job
        self.resume_override = resume_override
        self.resume_pin = resume_pin
        self.template_env = Environment(loader=FileSystemLoader(Config.TEMPLATES_DIR))
        self.sender = EmailSender()
        self.selector = ResumeSelector()

    def custom_paragraph(self) -> str:
        """Generate a role-specific paragraph based on the job title."""
        title = (self.job.title or "").lower()
        if any(k in title for k in ["python", "django", "fastapi", "flask", "backend"]):
            return (
                "With hands-on experience building backend services with Python, FastAPI and Django, "
                "and deploying REST APIs, I am confident I can contribute effectively to your engineering team. "
                "I have also worked with databases such as PostgreSQL and MongoDB and containerized applications with Docker."
            )
        if any(k in title for k in ["data", "analyst", "bi", "power bi", "tableau"]):
            return (
                "My background in data analytics includes building Power BI dashboards, writing complex SQL queries, "
                "and transforming raw data into actionable business insights. I excel at automating reporting workflows "
                "and am comfortable working with large datasets."
            )
        if any(k in title for k in ["ml", "machine learning", "ai", "data scient", "deep learning", "nlp"]):
            return (
                "I have practical experience developing machine learning and AI solutions, including model training, "
                "evaluation and deployment. I am comfortable with Python, scikit-learn, TensorFlow and cloud deployment, "
                "and I enjoy solving real-world problems with ML."
            )
        if any(k in title for k in ["network", "support", "it support", "help desk", "technical"]):
            return (
                "I have experience in network and technical support, troubleshooting hardware and software issues, "
                "and managing IT infrastructure to ensure reliable operations. I am a quick learner with strong "
                "customer-focused communication skills."
            )
        if any(k in title for k in ["qa", "test", "quality"]):
            return (
                "I am experienced in manual and automated testing, writing test cases and using tools like Selenium "
                "and pytest to ensure product quality. I have a strong attention to detail and enjoy improving "
                "testing processes."
            )
        return (
            "I am an enthusiastic and quick-learning professional with strong technical skills and a proven ability "
            "to deliver results. I am eager to bring my experience and dedication to your team and contribute "
            "to the success of your organization."
        )

    def build_cover_letter_html(self) -> str:
        custom = self.custom_paragraph()
        try:
            from services import llm

            draft = llm.draft_email(self.job)
            if draft:
                custom = draft
        except Exception:
            pass
        template = self.template_env.get_template("cover_letter.html")
        return template.render(
            job_title=self.job.title or "",
            company=self.job.company or "your company",
            salary=self.job.salary or "",
            custom_paragraph=custom,
            applicant_name=Config.YOUR_NAME,
            applicant_phone=Config.YOUR_PHONE,
            applicant_email=Config.YOUR_EMAIL,
        )

    def build_phone_summary_html(self) -> str:
        template = self.template_env.get_template("phone_summary.html")
        return template.render(
            job_title=self.job.title or "",
            company=self.job.company or "Unknown",
            location=self.job.location or "",
            phones=self.job.phones or [],
            experience=self.job.experience or "",
            salary=self.job.salary or "",
            url=self.job.url or "",
            applicant_name=Config.YOUR_NAME,
            applicant_phone=Config.YOUR_PHONE,
            applicant_email=Config.YOUR_EMAIL,
        )

    def attachment_from_resume(self, resume_filename):
        """Return the attachment dict for Brevo from a resume file."""
        if not resume_filename:
            return None
        path = self.selector.resume_path(resume_filename)
        if not path.exists():
            return None
        import base64
        with open(path, "rb") as f:
            content = f.read()
        return {
            "content": base64.b64encode(content).decode(),
            "name": path.name,
        }

    def send_application(self) -> dict:
        """Send an application email to the first job email, if present."""
        if not self.job.emails:
            return {
                "success": False,
                "error": "No email found on this job; this is a phone-contact application.",
                "type": "phone_only",
            }

        resume_file = self.resume_pin or self.selector.select_for_job(self.job.title, self.job.description)
        attachments = []
        if self.resume_override:
            # Uploaded resume stored in the DB -> attach its bytes directly.
            import base64

            attachments.append({
                "content": self.resume_override["data"],
                "name": self.resume_override["name"],
            })
            resume_file = self.resume_override["name"]
        elif resume_file:
            att = self.attachment_from_resume(resume_file)
            if att:
                attachments.append(att)

        subject = f"Application for {self.job.title} - {Config.YOUR_NAME}"
        html = self.build_cover_letter_html()
        to_email = self.job.emails[0]
        to_name = self.job.company or ""

        result = self.sender.send_email(
            to_email=to_email,
            subject=subject,
            html_content=html,
            to_name=to_name,
            attachments=attachments,
        )
        if result.get("success"):
            return {
                "success": True,
                "type": "email",
                "to_email": to_email,
                "resume_used": resume_file,
                "message_id": result.get("message_id"),
            }
        return result

    def send_phone_summary(self, to_email: str = None) -> dict:
        """Send the phone job summary to the applicant's own email."""
        target = to_email or Config.YOUR_EMAIL
        subject = f"📞 Job Action Required - {self.job.title} at {self.job.company}"
        html = self.build_phone_summary_html()
        result = self.sender.send_email(
            to_email=target,
            subject=subject,
            html_content=html,
            to_name=Config.YOUR_NAME,
        )
        if result.get("success"):
            return {
                "success": True,
                "type": "phone_summary",
                "to_email": target,
            }
        return result

    def send_link_summary(self, to_email: str = None) -> dict:
        """Email the apply link of a link-only job to the applicant's own inbox,
        so they can open it and apply whenever they're ready (no auto-open)."""
        target = to_email or Config.YOUR_EMAIL
        link = getattr(self.job, "apply_link", "") or ""
        if not link:
            return {"success": False, "error": "No apply link on this job.", "type": "link_summary"}

        def _h(v):
            return escape(str(v or ""))

        html = f"""
<html><body style="font-family:Arial,Helvetica,sans-serif;padding:20px;color:#111">
  <h2 style="margin:0 0 8px">🔗 Apply-Link Job</h2>
  <p style="margin:0 0 4px;font-size:16px"><b>{_h(self.job.title)}</b></p>
  <p style="margin:0 0 4px;color:#555">{_h(self.job.company or 'Unknown')}{(' · ' + _h(self.job.location)) if self.job.location else ''}</p>
  <p style="margin:0 0 14px;color:#444">Experience: {_h(self.job.experience or 'Not mentioned')}{(' · Salary: ' + _h(self.job.salary)) if self.job.salary else ''}</p>
  <a href="{_h(link)}" style="display:inline-block;background:#0a66c2;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600">Open Apply Link</a>
  <p style="margin-top:18px;font-size:12px;color:#888">Sent by your Job Auto-Apply assistant. No resume attached — apply directly through the site link.</p>
</body></html>"""
        subject = f"🔗 Apply via Link - {self.job.title} at {self.job.company}"
        result = self.sender.send_email(
            to_email=target,
            subject=subject,
            html_content=html,
            to_name=Config.YOUR_NAME,
        )
        if result.get("success"):
            return {
                "success": True,
                "type": "link_summary",
                "to_email": target,
                "message_id": result.get("message_id"),
            }
        return result
