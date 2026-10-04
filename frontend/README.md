# EXOVET frontend

Next.js 14 app-router UI for the EXOVET vetting pipeline.

## Run

```bash
# backend first (WSL):
#   cd backend && ../vendor/py313/python -m uvicorn exovet.api.app:app --port 8000
# or from the repo root with the WSL venv:
#   bash scripts/wsl_py.sh -m uvicorn exovet.api.app:app --port 8000

cd frontend
npm install
npm run dev        # http://localhost:3000
```

`NEXT_PUBLIC_API_URL` overrides the backend URL (default
`http://localhost:8000`).
