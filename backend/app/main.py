import json
import httpx
from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Annotated
import pyotp
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from jose import JWTError
from .config import get_settings
from .db import AssistantMessage, AuditLog, EmailCode, RefreshToken, User, create_tables, get_db
from .security import create_access_token, create_refresh_token, decode_access_token, hash_password, hash_token, verify_password

settings = get_settings()
app = FastAPI(title="Skill2Hire Platform API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
bearer = HTTPBearer(auto_error=False)

class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)
    role: str = "student"
class LoginIn(BaseModel):
    email: EmailStr
    password: str
    otp: str | None = None
class RefreshIn(BaseModel):
    refresh_token: str
class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
class MfaVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
class MessageOut(BaseModel):
    message: str
class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    role: str = "student"
    context: dict = {}
    history: list[dict] = []
class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    topic: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=5, max_length=4000)
class OtpRequestIn(BaseModel):
    email: EmailStr
    purpose: str = "verification"
class OtpVerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
class ProfileIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    skills: list[str] = Field(default_factory=list, max_length=50)
    resume_link: str | None = Field(default=None, max_length=500)

@app.on_event("startup")
def startup() -> None:
    create_tables()

def audit(db: Session, request: Request, action: str, user_id: int | None = None, metadata: dict | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, ip_address=request.client.host if request.client else None, metadata_json=json.dumps(metadata or {})))
    db.commit()

def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: Annotated[Session, Depends(get_db)]) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
        user = db.get(User, int(payload["sub"]))
    except (JWTError, ValueError):
        user = None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token")
    return user

def require_roles(*roles: str):
    def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role permissions")
        return user
    return dependency

@app.get("/health")
def health() -> dict: return {"status": "ok", "service": "platform-api"}

@app.get("/metrics")
def metrics() -> str:
    return "# HELP skill2hire_api_up API availability\n# TYPE skill2hire_api_up gauge\nskill2hire_api_up 1\n"

@app.get("/auth/oauth/{provider}")
def oauth_start(provider: str) -> dict:
    providers = {"google": ("https://accounts.google.com/o/oauth2/v2/auth", settings.google_client_id, "openid email profile"), "linkedin": ("https://www.linkedin.com/oauth/v2/authorization", settings.linkedin_client_id, "openid profile email"), "azure": ("https://login.microsoftonline.com/common/oauth2/v2.0/authorize", settings.azure_client_id, "openid email profile")}
    if provider not in providers: raise HTTPException(404, "Unsupported OAuth provider")
    endpoint, client_id, scope = providers[provider]
    if not client_id: raise HTTPException(503, f"{provider} OAuth is not configured")
    from urllib.parse import urlencode
    return {"provider": provider, "authorization_url": endpoint + "?" + urlencode({"client_id": client_id, "redirect_uri": settings.oauth_redirect_uri, "response_type": "code", "scope": scope})}

@app.get("/auth/oauth/callback")
def oauth_callback(code: str | None = None, state: str | None = None) -> dict:
    if not code: raise HTTPException(400, "Authorization code is required")
    return {"status": "callback_received", "next": "exchange code with the configured provider adapter"}

@app.websocket("/ws/assistant")
async def assistant_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            message = str(payload.get("message", ""))[:2000]
            role = str(payload.get("role", "student"))
            await websocket.send_json({"type": "typing", "value": True})
            await websocket.send_json({"type": "message", "reply": local_assistant_reply(message, role, payload.get("context", {})), "provider": "google" if settings.google_ai_api_key else "local"})
    except WebSocketDisconnect:
        return

def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()

async def send_email(to: str, subject: str, content: str) -> bool:
    if not settings.sendgrid_api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://api.sendgrid.com/v3/mail/send", headers={"Authorization": f"Bearer {settings.sendgrid_api_key}", "Content-Type": "application/json"}, json={"personalizations": [{"to": [{"email": to}]}], "from": {"email": settings.mail_from}, "subject": subject, "content": [{"type": "text/plain", "value": content}]})
            return response.status_code == 202
    except httpx.HTTPError:
        return False

def local_assistant_reply(message: str, role: str, context: dict) -> str:
    text = message.lower()
    if "contact" in text or "support" in text:
        return "You can use Help center to send a support request. Choose a topic and describe your question; the placement team will reply shortly."
    if role == "recruiter":
        return "I can rank candidates by verified skills, eligibility and match score. Try asking for a shortlist or interview schedule."
    if role == "officer":
        return "I can summarise placement performance, branch trends and eligible versus shortlisted students. Ask for a placement report."
    if "resume" in text:
        return "Your resume signal is strongest in research and prototyping. Add measurable outcomes and SQL keywords to improve discoverability."
    if "interview" in text:
        return "Start a structured mock interview: problem framing, approach, trade-offs and measurable impact. I can score each answer."
    if "job" in text or "role" in text:
        return "Your strongest current match is Product Designer at Razorpay with a 96% fit. Review eligibility, then apply."
    return "I can help with resume feedback, skill roadmaps, job matches and interview preparation."

@app.post("/assistant/chat")
async def assistant_chat(payload: AssistantIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> dict:
    role = payload.role if payload.role in {"student", "officer", "recruiter"} else "student"
    system = f"You are Guide, a professional placement assistant. User role: {role}. Use dashboard context: {json.dumps(payload.context)}. Give concise, actionable and unbiased career guidance."
    reply = None
    if settings.google_ai_api_key:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.assistant_model}:generateContent?key={settings.google_ai_api_key}"
        body = {"system_instruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": payload.message}]}]}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, json=body); response.raise_for_status()
                reply = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError):
            reply = None
    if not reply and settings.openai_api_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {settings.openai_api_key}"}, json={"model": "gpt-4o-mini", "messages": [{"role": "system", "content": system}, *payload.history[-6:], {"role": "user", "content": payload.message}]}); response.raise_for_status()
                reply = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError):
            reply = None
    reply = reply or local_assistant_reply(payload.message, role, payload.context)
    db.add(AssistantMessage(user_id=None, role=role, message=payload.message)); db.add(AssistantMessage(user_id=None, role="assistant", message=reply)); db.commit()
    audit(db, request, "assistant.message", metadata={"role": role, "provider": "google" if settings.google_ai_api_key else "openai" if settings.openai_api_key else "local"})
    return {"reply": reply, "provider": "google" if settings.google_ai_api_key else "openai" if settings.openai_api_key else "local"}

@app.post("/support/contact", response_model=MessageOut, status_code=201)
def contact(payload: ContactIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    audit(db, request, "support.contact", metadata={"email": payload.email, "topic": payload.topic})
    return MessageOut(message="Support request received. The placement team will reply shortly.")

@app.get("/search")
def global_search(q: str = "", limit: int = 8, db: Session = Depends(get_db)) -> dict:
    term = q.strip().lower()
    jobs = [{"type": "job", "title": "Product Designer", "company": "Razorpay", "location": "Bengaluru"}, {"type": "job", "title": "UX Research Intern", "company": "Meesho", "location": "Remote"}, {"type": "job", "title": "Associate Product Manager", "company": "PhonePe", "location": "Pune"}]
    skills = ["Figma", "User research", "Data storytelling", "SQL basics", "Prototyping"]
    students = [{"type": "student", "title": user.full_name, "email": user.email, "role": user.role} for user in db.query(User).limit(20).all()]
    results = [item for item in [*jobs, *[{"type": "skill", "title": skill} for skill in skills], *students] if not term or term in json.dumps(item).lower()]
    return {"query": q, "results": results[:max(1, min(limit, 20))], "suggestions": sorted({item.get("title", "") for item in results if item.get("title")})[:5]}

@app.post("/auth/email-otp/request", response_model=MessageOut)
async def request_email_otp(payload: OtpRequestIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    if payload.purpose not in {"verification", "password_reset", "mfa"}: raise HTTPException(400, "Unsupported OTP purpose")
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(EmailCode(email=payload.email, purpose=payload.purpose, code_hash=code_hash(code), expires_at=datetime.utcnow() + timedelta(minutes=15))); db.commit()
    delivered = await send_email(payload.email, "Skill2Hire verification code", f"Your Skill2Hire code is {code}. It expires in 15 minutes.")
    audit(db, request, "auth.otp_requested", metadata={"purpose": payload.purpose, "delivered": delivered})
    return MessageOut(message="OTP sent by email" if delivered else "OTP created. Configure SENDGRID_API_KEY to deliver email.")

@app.post("/auth/email-otp/verify", response_model=MessageOut)
def verify_email_otp(payload: OtpVerifyIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    record = db.query(EmailCode).filter(EmailCode.email == payload.email, EmailCode.code_hash == code_hash(payload.code), EmailCode.used.is_(False), EmailCode.expires_at > datetime.utcnow()).order_by(EmailCode.id.desc()).first()
    if not record: raise HTTPException(400, "Invalid or expired OTP")
    record.used = True; user = db.query(User).filter(User.email == payload.email).first()
    if user: user.is_verified = True
    db.commit(); audit(db, request, "auth.otp_verified", user.id if user else None); return MessageOut(message="Email verified successfully")

@app.put("/profile", response_model=MessageOut)
def update_profile(payload: ProfileIn, request: Request, user: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    user.full_name = payload.full_name; user.phone = payload.phone; user.skills = json.dumps(payload.skills); user.resume_link = payload.resume_link; db.commit(); audit(db, request, "profile.updated", user.id); return MessageOut(message="Profile updated")

@app.delete("/profile", response_model=MessageOut)
def delete_profile(request: Request, user: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    user.full_name = "Deleted user"; user.phone = None; user.skills = "[]"; user.resume_link = None; user.password_hash = hash_password(secrets.token_urlsafe(32)); db.commit(); audit(db, request, "profile.deleted", user.id); return MessageOut(message="Profile data deleted")

@app.post("/ai/resume/upload")
async def upload_resume(request: Request, file: UploadFile = File(...), user: User = Depends(require_roles("student"))) -> dict:
    allowed = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if file.content_type not in allowed: raise HTTPException(415, "Only PDF and DOCX resumes are supported")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024: raise HTTPException(413, "Resume must be 10 MB or smaller")
    return {"status": "queued", "filename": file.filename, "analysis": "resume-parser-worker", "accepted_types": ["pdf", "docx"]}

@app.post("/auth/register", response_model=MessageOut, status_code=201)
def register(payload: RegisterIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    if payload.role not in {"student", "officer", "recruiter"}:
        raise HTTPException(400, "Role must be student, officer, or recruiter")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(409, "Email already registered")
    user = User(email=payload.email, full_name=payload.full_name, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user); db.commit(); db.refresh(user)
    audit(db, request, "auth.register", user.id, {"role": user.role})
    return MessageOut(message="Registration successful. Verify your email before signing in.")

@app.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> TokenOut:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        audit(db, request, "auth.login_failed", metadata={"email": payload.email}); raise HTTPException(401, "Invalid email or password")
    if user.mfa_secret and (not payload.otp or not pyotp.TOTP(user.mfa_secret).verify(payload.otp)):
        raise HTTPException(401, "MFA code required or invalid")
    access = create_access_token(user.id, user.role); refresh, expires = create_refresh_token(user.id)
    db.add(RefreshToken(token_hash=hash_token(refresh), user_id=user.id, expires_at=expires)); db.commit()
    audit(db, request, "auth.login", user.id, {"role": user.role})
    return TokenOut(access_token=access, refresh_token=refresh)

@app.post("/auth/refresh", response_model=TokenOut)
def refresh(payload: RefreshIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> TokenOut:
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(payload.refresh_token), RefreshToken.revoked.is_(False)).first()
    if not stored or stored.expires_at < datetime.utcnow(): raise HTTPException(401, "Invalid or expired refresh token")
    user = db.get(User, stored.user_id); stored.revoked = True; new_refresh, expires = create_refresh_token(user.id)
    db.add(RefreshToken(token_hash=hash_token(new_refresh), user_id=user.id, expires_at=expires)); db.commit()
    audit(db, request, "auth.refresh", user.id)
    return TokenOut(access_token=create_access_token(user.id, user.role), refresh_token=new_refresh)

@app.post("/auth/logout", response_model=MessageOut)
def logout(payload: RefreshIn, request: Request, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(payload.refresh_token)).first()
    if stored: stored.revoked = True; db.commit(); audit(db, request, "auth.logout", stored.user_id)
    return MessageOut(message="Signed out")

@app.get("/auth/me")
def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "is_verified": user.is_verified, "mfa_enabled": bool(user.mfa_secret)}

@app.post("/auth/verify-email/{user_id}", response_model=MessageOut)
def verify_email(user_id: int, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    user = db.get(User, user_id)
    if not user: raise HTTPException(404, "User not found")
    user.is_verified = True; db.commit(); return MessageOut(message="Email verified")

@app.post("/auth/mfa/setup")
def mfa_setup(user: Annotated[User, Depends(current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    user.mfa_secret = pyotp.random_base32(); db.commit(); return {"secret": user.mfa_secret, "otpauth_url": pyotp.TOTP(user.mfa_secret).provisioning_uri(user.email, issuer_name="Skill2Hire")}

@app.post("/auth/mfa/verify", response_model=MessageOut)
def mfa_verify(payload: MfaVerifyIn, user: Annotated[User, Depends(current_user)]) -> MessageOut:
    if not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(payload.code): raise HTTPException(400, "Invalid MFA code")
    return MessageOut(message="MFA enabled")

@app.post("/ai/resume/analyse")
def analyse_resume(user: Annotated[User, Depends(require_roles("student"))]) -> dict:
    return {"status": "queued", "owner_id": user.id, "pipeline": ["pdf-text", "spacy-entities", "transformer-embeddings", "skill-score"]}

@app.get("/officer/analytics")
def officer_analytics(user: Annotated[User, Depends(require_roles("officer"))]) -> dict:
    return {"eligible": 482, "shortlisted": 164, "selected": 78, "forecast": 84.2}

@app.get("/recruiter/matches")
def recruiter_matches(user: Annotated[User, Depends(require_roles("recruiter"))]) -> dict:
    return {"candidates": [], "ranking": "faiss-cosine-similarity", "owner_id": user.id}
