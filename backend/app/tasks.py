from celery import Celery
from .config import get_settings

celery_app = Celery("skill2hire", broker=get_settings().redis_url, backend=get_settings().redis_url)

@celery_app.task(name="resume.analyse")
def analyse_resume_task(resume_id: str) -> dict:
    return {"resume_id": resume_id, "status": "analysed", "pipeline": ["extract", "spacy", "embeddings", "score"]}

@celery_app.task(name="email.send")
def send_email_task(recipient: str, subject: str) -> dict:
    return {"recipient": recipient, "subject": subject, "status": "queued-for-provider"}
