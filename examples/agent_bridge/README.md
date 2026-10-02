# AUV Agent Bridge (Local Adapter)

This bridge lets users connect an existing local agent to AUV without modifying the agent core.

## 1) Install

```powershell
cd examples/agent_bridge
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
```

## 2) Run

```powershell
uvicorn main:app --host 127.0.0.1 --port 8787 --reload
```

## 3) Connect in AUV

Use `external_agent` node with:

- `integration_mode`: `bridge_local`
- `endpoint_url`: leave empty (defaults to `http://127.0.0.1:8787`) or set explicitly
- `endpoint_path`: `/agent/tasks`

## 4) Customize your agent

Edit `handler.py` only, implement `handle_task(task)`:

- read `task.text`
- call your real agent function
- return `{ "text": "..." }`

No protocol wiring changes are required.
