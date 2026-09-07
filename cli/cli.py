"""
CLI tool for Job Auto-Apply Assistant.

Usage examples:
  python cli.py scrape --url https://www.linkedin.com/jobs/view/...
  python cli.py ocr --image screenshot.png
  python cli.py list
  python cli.py apply --job-id 1
  python cli.py summary --job-id 1  (for phone-only jobs)
  python cli.py stats
  python cli.py test-email
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

app = typer.Typer(help="Job Auto-Apply Assistant CLI")
console = Console()

from database import Base, engine, SessionLocal
from models import Job, Application
from services.email_builder import EmailBuilder
from services.scraper import LinkedInScraper
from services.ocr import OCRProcessor
from services.email_sender import EmailSender

# Ensure the database schema exists (safe no-op if already present)
Base.metadata.create_all(bind=engine)


@app.command()
def scrape(url: str):
    """Scrape a LinkedIn job page and save it."""
    console.print(f"[cyan]Scraping:[/cyan] {url}")
    scraper = LinkedInScraper()
    try:
        job_data = scraper.fetch_job(url)
    except Exception as e:
        console.print(f"[red]Scrape failed:[/red] {e}")
        raise typer.Exit(1)

    db = SessionLocal()
    existing = db.query(Job).filter(Job.url == url).first()
    if existing:
        console.print("[yellow]Job already in database.[/yellow]")
        console.print(f"  ID: {existing.id} | {existing.title} | {existing.company}")
        db.close()
        return

    job = Job(**job_data)
    db.add(job)
    db.commit()
    db.refresh(job)
    db.close()

    console.print(f"[green]Saved job #{job.id}:[/green] {job.title} at {job.company}")
    if job_data.get("emails"):
        console.print(f"[green]Emails found:[/green] {', '.join(job_data['emails'])}")
    if job_data.get("phones"):
        console.print(f"[yellow]Phones found:[/yellow] {', '.join(job_data['phones'])}")


@app.command()
def ocr(image: str):
    """Extract job data from a screenshot via OCR."""
    console.print(f"[cyan]Processing image:[/cyan] {image}")
    processor = OCRProcessor()
    try:
        parsed = processor.process_screenshot(image)
    except Exception as e:
        console.print(f"[red]OCR failed:[/red] {e}")
        raise typer.Exit(1)

    console.print(Panel.fit(
        f"[bold]Title:[/bold] {parsed.get('title', '-')}\n"
        f"[bold]Company:[/bold] {parsed.get('company', '-')}\n"
        f"[bold]Experience:[/bold] {parsed.get('experience', '-')}\n"
        f"[bold]Emails:[/bold] {', '.join(parsed.get('emails', [])) or '-'}\n"
        f"[bold]Phones:[/bold] {', '.join(parsed.get('phones', [])) or '-'}",
        title="OCR Result", border_style="cyan"
    ))

    if not parsed.get("title"):
        console.print("[red]No confident job title found. Job NOT saved.[/red]")
        return

    db = SessionLocal()
    job = Job(
        title=parsed["title"],
        company=parsed.get("company"),
        location=parsed.get("location"),
        description=parsed.get("description"),
        emails=parsed.get("emails", []),
        phones=parsed.get("phones", []),
        experience=parsed.get("experience", ""),
        salary=parsed.get("salary", ""),
        source="ocr",
        has_email=parsed.get("has_email", False),
        has_phone=parsed.get("has_phone", False),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    db.close()

    console.print(f"[green]Job saved as #{job.id}[/green]")


@app.command()
def list():
    """List all captured jobs."""
    db = SessionLocal()
    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    db.close()

    if not jobs:
        console.print("[yellow]No jobs captured yet.[/yellow]")
        return

    table = Table(title="Captured Jobs", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Company")
    table.add_column("Contact", max_width=30)
    table.add_column("Status", style="green")
    table.add_column("Source")

    for j in jobs:
        contact = ""
        if j.emails:
            contact = "Email: " + j.emails[0]
        elif j.phones:
            contact = "Phone: " + j.phones[0]
        table.add_row(
            str(j.id), j.title or "-", j.company or "-",
            contact, j.status, j.source
        )
    console.print(table)


@app.command()
def apply(job_id: int):
    """Send an application email for a job."""
    db = SessionLocal()
    job = db.query(Job).filter(Job.id == job_id).first()
    db.close()
    if not job:
        console.print(f"[red]Job #{job_id} not found.[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Applying to:[/cyan] {job.title} at {job.company}")

    builder = EmailBuilder(job)
    if job.emails:
        console.print(f"[cyan]Sending to:[/cyan] {job.emails[0]}")
        result = builder.send_application()
    else:
        console.print("[yellow]No email on job — will send phone summary to your inbox.[/yellow]")
        result = builder.send_phone_summary()

    if result.get("success"):
        console.print("[green]Application sent successfully![/green]")
        if result.get("resume_used"):
            console.print(f"[green]Resume used:[/green] {result['resume_used']}")

        # Update DB status
        db = SessionLocal()
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "applied" if result.get("type") == "email" else "phone_summary_sent"
        db.add(Application(
            job_id=job.id,
            resume_used=result.get("resume_used"),
            email_sent_to=result.get("to_email"),
            type=result.get("type", "email"),
            status="sent",
        ))
        db.commit()
        db.close()
    else:
        console.print(f"[red]Failed:[/red] {result.get('error', 'Unknown error')}")
        raise typer.Exit(1)


@app.command()
def summary(job_id: int):
    """Preview the phone-job summary email for a job."""
    db = SessionLocal()
    job = db.query(Job).filter(Job.id == job_id).first()
    db.close()
    if not job:
        console.print(f"[red]Job #{job_id} not found.[/red]")
        raise typer.Exit(1)

    phones = ", ".join(job.phones or []) or "-"
    console.print(Panel.fit(
        f"[bold]Position:[/bold] {job.title or '-'}\n"
        f"[bold]Company:[/bold] {job.company or '-'}\n"
        f"[bold]Location:[/bold] {job.location or '-'}\n"
        f"[bold]Contact Phone:[/bold] [red]{phones}[/red]\n"
        f"[bold]Experience:[/bold] {job.experience or '-'}\n"
        f"[bold]Salary:[/bold] {job.salary or '-'}\n"
        f"[bold]Link:[/bold] {job.url or '-'}",
        title=f"Phone Summary - Job #{job_id}", border_style="yellow"
    ))
    console.print("[yellow]This summary will be emailed to your inbox when you apply.[/yellow]")


@app.command()
def stats():
    """Show application statistics."""
    db = SessionLocal()
    total = db.query(Job).count()
    applied = db.query(Job).filter(Job.status == "applied").count()
    pending = db.query(Job).filter(Job.status == "pending").count()
    phone_only = db.query(Job).filter(Job.has_phone == True, Job.has_email == False).count()
    summary_sent = db.query(Job).filter(Job.status == "phone_summary_sent").count()
    db.close()

    table = Table(title="Application Stats", box=box.DOUBLE)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="bold")
    table.add_row("Total Jobs Captured", str(total))
    table.add_row("Applied via Email", str(applied))
    table.add_row("Phone Summaries Sent", str(summary_sent))
    table.add_row("Pending", str(pending))
    table.add_row("Phone-Only Jobs (no email)", str(phone_only))
    console.print(table)


@app.command()
def test_email():
    """Send a test email to verify Brevo configuration."""
    sender = EmailSender()
    if not sender.configured:
        console.print("[red]BREVO_API_KEY not set in .env[/red]")
        raise typer.Exit(1)

    from config import Config
    console.print(f"[cyan]Sending test email to {Config.YOUR_EMAIL}...[/cyan]")
    result = sender.send_email(
        to_email=Config.YOUR_EMAIL,
        subject="Test - Job Auto-Apply CLI",
        html_content="<h3>CLI works!</h3><p>Brevo email configuration is valid.</p>",
        to_name=Config.YOUR_NAME,
    )
    if result.get("success"):
        console.print("[green]Test email sent successfully![/green]")
    else:
        console.print(f"[red]Test failed:[/red] {result.get('error')}")
        raise typer.Exit(1)


@app.command()
def watch():
    """Monitor the database for new jobs (useful for debugging)."""
    console.print("[cyan]Watching for new jobs... Press Ctrl+C to stop.[/cyan]")
    seen = set()
    db = SessionLocal()
    jobs = db.query(Job).all()
    for j in jobs:
        seen.add(j.id)
    db.close()

    try:
        while True:
            db = SessionLocal()
            jobs = db.query(Job).order_by(Job.created_at.desc()).all()
            db.close()
            for j in jobs:
                if j.id not in seen:
                    seen.add(j.id)
                    console.print(f"[green]New job:[/green] #{j.id} {j.title} at {j.company}")
            time.sleep(3)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


if __name__ == "__main__":
    app()