# Skill2Hire architecture

## Authentication

The FastAPI platform API owns authentication. Passwords are bcrypt hashes. Access tokens are short-lived signed JWTs; refresh tokens are opaque, hashed at rest, rotated on use, and revocable. Roles are `student`, `officer`, and `recruiter`. Protected endpoints use role dependencies. Audit logs record authentication actions and client IP metadata.

MFA is implemented with TOTP setup and verification endpoints. Email verification and password reset should be connected to SendGrid by supplying `SENDGRID_API_KEY`; the current local endpoint keeps the verification boundary explicit without sending mail from development.

## AI pipeline boundaries

- Resume parsing: FastAPI worker boundary for PDF text extraction and spaCy entities.
- Skill extraction: HuggingFace transformer embeddings stored as normalized skill vectors.
- Matching: FAISS cosine similarity between student skill vectors and job requirement vectors.
- Interview: LLM provider adapter returns question, answer rubric and score.
- Roadmap: structured weekly plan generated from skill gap deltas.
- Analytics: SQL aggregates feed officer and recruiter dashboards; predictive placement forecasts must be evaluated for bias before production use.

The frontend currently uses local mock data for these feature views until provider credentials, PostgreSQL migrations and model artifacts are configured.
