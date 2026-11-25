# Student interface

Buildless HTML/CSS/JavaScript, served by FastAPI at `/app/`. No Node package installation or separate frontend server is required.

Start the backend from `Backend`:

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/app/>. Start `python -m app.worker` in another terminal for queued processing. Ollama must be installed and its configured model available for generated answers; passage search remains usable independently.

The interface supports student registration/login, logout, course enrolment, private upload, instructor publishing, processing status/retry, course-filtered search, answers with source-quote dialogs, and reading extracted passages. It calls the existing API on the same origin. Backend permissions remain authoritative.

**Download original** is available on material cards and in source dialogs. Downloads use an authenticated request and temporary object URL; tokens are never placed in download URLs. The backend rechecks current access before serving the file. Files are saved as attachments rather than displayed as inline PDF/slide previews.

Tokens are stored in the current tab's sessionStorage and cleared on logout/expiry. Text from users, documents, and models is rendered with text nodes rather than HTML. Sign-out cancels pending requests and clears visible account data. Production HTTPS and a reviewed session strategy are still required before release.

There is no server-side chat history, offline synchronisation, quiz interface, or original PDF/slide viewer yet. The first release shows extracted passages and source locations. Library/catalog pagination is supported; course selectors currently include the first 100 taught/enrolled courses.
