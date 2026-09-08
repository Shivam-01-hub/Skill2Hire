# Skill2Hire

AI-powered college placement platform that connects students, colleges, and recruiters through intelligent skill and job matching.

## Included in this MVP

- Student readiness dashboard with resume score and activity feed
- AI resume upload interaction
- Smart job matches with match scores and save state
- Skill gap snapshot and learning-plan navigation
- Application, profile, and analytics-ready navigation shell
- Responsive layout, semantic controls, keyboard focus states, and reduced-motion support
- FastAPI auth service with bcrypt passwords, JWT access tokens, refresh-token rotation, RBAC, TOTP MFA, and audit logs
- Role-protected API boundaries for student resume analysis, officer analytics, and recruiter matching
- Docker and Docker Compose development setup

## Run the platform

Frontend only:

## Run locally

```bash
npm install
npm run dev
```

Then open the local URL printed by Vite. Create a production build with `npm run build`.

Copy `.env.example` to `.env` if the API is running on another host or port. The Guide assistant calls `/assistant/chat`; with `GOOGLE_AI_API_KEY` or `OPENAI_API_KEY` configured it uses that provider, otherwise it uses the local placement response engine.

Backend API:

```bash
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Or run both services with Docker:

```bash
copy backend/.env.example backend/.env
docker compose up --build
```

The frontend login screen uses `VITE_API_URL` and also provides an explicit demo workspace for offline UI review. See [docs/architecture.md](docs/architecture.md) for the production AI, data and security boundaries.
"refresh" 
"update" 
" " 
