const $ = (id) => document.getElementById(id);
const tokenKey = 'uniknowledge.session';
let savedToken = null;
try { savedToken = sessionStorage.getItem(tokenKey); } catch { /* Use memory-only sign-in when browser storage is unavailable. */ }
const state = { token: savedToken, user: null, epoch: 0, documents: [], courses: [], mine: [], jobs: new Map(), pending: new Set(), requests: new Set(), view: 'library', register: false, poll: null, practice: { topics: [], questions: [], plan: null }, quiz: null, authoring: null, recording: null };
const active = (job) => job && ['queued', 'running'].includes(job.status);
function node(tag, className = '', text = '') { const n = document.createElement(tag); n.className = className; n.textContent = text; return n; }
function control(text, action, className = 'button secondary compact') { const b = node('button', className, text); b.type = 'button'; b.addEventListener('click', () => Promise.resolve(action()).catch((e) => { if (!e.cancelled) notify(e.message); })); return b; }
function error(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
let noticeTimer;
function notify(message) { $('notification').textContent = message; $('notification').hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notification').hidden = true; }, 7000); }
function logout(message = '') {
  state.epoch++; state.requests.forEach((c) => c.abort()); state.requests.clear(); clearTimeout(state.poll); state.poll = null;
  try { sessionStorage.removeItem(tokenKey); } catch { /* No persistent token in this mode. */ }
  state.token = null; state.user = null; state.documents = []; state.courses = []; state.mine = []; state.jobs.clear(); state.pending.clear(); state.practice = { topics: [], questions: [], plan: null }; state.quiz = null; state.authoring = null; closeRecording();
  ['documents', 'courses', 'answer', 'source-content', 'mastery', 'plan-sessions', 'plan-notes', 'my-questions', 'quiz-items', 'recording-player', 'transcript', 'notes'].forEach((id) => $(id).replaceChildren());
  document.querySelectorAll('dialog[open]').forEach((d) => d.close()); $('question-form').reset(); $('upload-form').reset(); $('workspace').hidden = true; $('auth-screen').hidden = false;
  error('auth-error', message); $('auth-form').reset(); $('email').focus();
}
async function api(path, options = {}, responseType = 'json') {
  const controller = new AbortController(); const epoch = state.epoch; state.requests.add(controller);
  const timer = setTimeout(() => controller.abort(), options.timeout || 150000);
  try {
    const headers = new Headers(options.headers || {});
    if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, { ...options, headers, signal: controller.signal });
    const data = response.status === 204 ? null : response.ok && responseType === 'blob' ? await response.blob() : await response.json();
    if (epoch !== state.epoch) throw Object.assign(new Error('Request cancelled'), { cancelled: true });
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith('/auth/')) { logout('Your session has expired. Please sign in again.'); throw Object.assign(new Error('Session expired'), { cancelled: true }); }
      let message = typeof data?.detail === 'string' ? data.detail : 'Check the form fields and try again.';
      if (response.status >= 500 && path === '/chat') message = response.status === 504 ? 'The assistant took too long. Try a shorter question.' : 'The assistant is unavailable right now. You can still find passages in your materials.';
      else if (response.status >= 500 && path === '/questions/generate') message = 'AI drafting is unavailable right now. You can still write questions from a passage in Search & answers.';
      else if (response.status >= 500 && path === '/search') message = 'Search is temporarily unavailable. Please try again shortly.';
      else if (response.status >= 500) message = 'The service is temporarily unavailable. Please try again.';
      throw Object.assign(new Error(message), { status: response.status });
    }
    return data;
  } catch (e) {
    if (controller.signal.aborted) throw Object.assign(new Error(epoch !== state.epoch ? 'Request cancelled' : 'The request took too long. Please try again.'), { cancelled: epoch !== state.epoch });
    if (e instanceof TypeError) throw new Error('Cannot connect. Check your connection and try again.');
    throw e;
  } finally { clearTimeout(timer); state.requests.delete(controller); }
}
async function busy(button, task, errorId) {
  const children = Array.from(button.childNodes); button.disabled = true; button.replaceChildren(document.createTextNode('Please wait…'));
  if (errorId) error(errorId);
  try { await task(); } catch (e) { if (!e.cancelled) errorId ? error(errorId, e.message) : notify(e.message); }
  finally { button.disabled = false; button.replaceChildren(...children); if (button.id === 'question-submit') button.textContent = document.querySelector('input[name=mode]:checked').value === 'chat' ? 'Ask with sources ↗' : 'Find passages ↗'; }
}
function navigate(view) {
  if (!state.user) return; state.view = view;
  ['library', 'courses', 'assistant', 'practice'].forEach((key) => { $(`${key}-view`).hidden = key !== view; });
  document.querySelectorAll('[data-view]').forEach((b) => { const selected = b.dataset.view === view; b.classList.toggle('active', selected); if (selected) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  $('breadcrumb').textContent = `Workspace / ${ { library: 'My materials', courses: 'Courses', assistant: 'Search & answers', practice: 'Practice & plan' }[view] }`;
  if (view === 'practice') loadPractice().catch((e) => { if (!e.cancelled) error('practice-error', e.message); });
}
function courseOptions() {
  for (const [id, label] of [['library-filter', 'All materials'], ['question-course', 'All my materials'], ['upload-course', 'Personal material']]) {
    const value = $(id).value; $(id).replaceChildren(new Option(label, ''));
    state.mine.forEach((course) => $(id).append(new Option(`${course.code} · ${course.title}`, course.id)));
    if (Array.from($(id).options).some((o) => o.value === value)) $(id).value = value;
  }
  for (const id of ['practice-course', 'author-course']) {
    const value = $(id).value; $(id).replaceChildren(...state.mine.map((course) => new Option(`${course.code} · ${course.title}`, course.id)));
    if (state.mine.some((c) => c.id === value)) $(id).value = value;
  }
  updateSharing();
}
async function loadMine() { state.mine = await api('/courses?mine=true&limit=100'); courseOptions(); }
async function loadCourses(append = false) {
  const offset = append ? state.courses.length : 0;
  const courses = await api(`/courses?limit=20&offset=${offset}`);
  state.courses = append ? state.courses.concat(courses) : courses; $('load-courses').hidden = courses.length < 20; renderCourses();
}
function renderCourses() {
  const root = $('courses'); root.replaceChildren();
  if (!state.courses.length) { root.append(empty('No courses yet.', 'Your university’s published courses will appear here when they are added.')); return; }
  state.courses.forEach((course) => {
    const card = node('article', 'document-card'); const joined = state.mine.some((c) => c.id === course.id); const instructor = course.instructor_id === state.user.id;
    const top = node('div', 'card-top'); top.append(node('span', 'file-icon', 'COURSE'), node('span', 'badge', instructor ? 'Teaching' : joined ? 'Enrolled' : 'Available'));
    card.append(top, node('p', 'card-meta', course.code), node('h3', '', course.title)); if (course.description) card.append(node('p', 'small muted', course.description));
    if (course.join_code) card.append(node('span', 'join-code', `Join code · ${course.join_code}`));
    const actions = node('div', 'card-actions');
    if (course.join_code) actions.append(control('Students', () => openRoster(course)), control('New code', async () => {
      if (!window.confirm('Issue a new join code? The current code will stop working; enrolled students keep access.')) return;
      await api(`/courses/${course.id}/join-code`, { method: 'POST' }); await loadCourses(); notify('A new join code is ready.');
    }, 'text-button'));
    if (joined) actions.append(control('View materials', () => { $('library-filter').value = course.id; navigate('library'); return loadDocuments(); }));
    if (state.user.role === 'STUDENT') actions.append(control(joined ? 'Leave course' : 'Join course', async () => {
      if (!joined) { openJoin(course); return; }
      if (!window.confirm('Leave this course? You will lose access to its published materials. Your private notes will remain available.')) return;
      await api(`/courses/${course.id}/enroll`, { method: 'DELETE' }); await loadMine(); renderCourses(); await loadDocuments(); notify('You have left the course.');
    }, joined ? 'text-button' : 'button primary compact'));
    card.append(actions); root.append(card);
  });
}
let joining = null;
function openJoin(course) { joining = course; $('join-title').textContent = `Join ${course.code}`; $('join-form').reset(); error('join-error'); $('join-dialog').showModal(); }
async function openRoster(course) {
  $('roster-title').textContent = `${course.code} students`; error('roster-error'); $('roster').replaceChildren(node('p', 'muted', 'Loading students…')); if (!$('roster-dialog').open) $('roster-dialog').showModal();
  try {
    const students = await api(`/courses/${course.id}/students`); const root = $('roster'); root.replaceChildren();
    if (!students.length) { root.append(node('p', 'muted', 'No students yet. Share the join code with your class.')); return; }
    students.forEach((s) => {
      const row = node('div', 'roster-row'); const who = node('div'); who.append(node('strong', '', s.full_name), node('p', 'muted', s.email));
      row.append(who, control('Remove', async () => { if (!window.confirm(`Remove ${s.full_name} from ${course.code}? They lose access to its shared materials.`)) return; await api(`/courses/${course.id}/students/${s.user_id}`, { method: 'DELETE' }); await openRoster(course); }, 'text-button danger'));
      root.append(row);
    });
  } catch (e) { if (!e.cancelled) error('roster-error', e.message); }
}
function empty(title, description) { const box = node('div', 'empty'); box.append(node('span', '', '▤'), node('h2', '', title), node('p', '', description)); return box; }
function downloadControl(id, name) {
  const button = control('Download original', () => busy(button, async () => {
    const blob = await api(`/documents/${encodeURIComponent(id)}/download`, {}, 'blob');
    const url = URL.createObjectURL(blob); const link = node('a');
    link.href = url; link.download = name.replace(/.*[\\/]/, '').replace(/[\x00-\x1f\x7f]/g, '') || 'material';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }), 'text-button');
  button.dataset.action = 'download'; return button;
}
async function loadDocuments(append = false) {
  error('library-error'); const filter = $('library-filter').value; const offset = append ? state.documents.length : 0;
  const documents = await api(`/documents?limit=20&offset=${offset}${filter ? `&course_id=${encodeURIComponent(filter)}` : ''}`);
  if (filter !== $('library-filter').value) return;
  state.documents = append ? state.documents.concat(documents) : documents; $('load-documents').hidden = documents.length < 20;
  await Promise.all(state.documents.filter((d) => d.owner_id === state.user.id).map(async (d) => {
    try { state.jobs.set(d.id, await api(`/documents/${d.id}/processing`)); } catch (e) { if (e.status !== 404 && !e.cancelled) throw e; }
  }));
  renderDocuments(); schedulePoll();
}
function statusLabel(document) {
  const job = state.jobs.get(document.id);
  if (active(job)) return { text: job.status === 'queued' ? 'Queued' : 'Processing', status: job.status };
  if (document.processing_status === 'embedded') return { text: 'Ready to explore', status: 'embedded' };
  if (document.processing_status === 'failed') return { text: 'Needs a retry', status: 'failed' };
  return { text: 'Not processed', status: document.processing_status };
}
function renderDocuments() {
  if (!state.user) return;
  const focused = document.activeElement; const focusedCard = focused?.closest('[data-document-id]'); const focusId = focusedCard?.dataset.documentId; const focusAction = focused?.dataset.action;
  const root = $('documents'); root.replaceChildren(); $('library-count').textContent = `${state.documents.length} material${state.documents.length === 1 ? '' : 's'} shown`;
  if (!state.documents.length) {
    const box = empty('Give your next question a place to start.', 'Upload your lecture notes, slides, or reading material. You can also join a course to see shared resources.');
    box.append(control('Upload your first material', openUpload, 'button primary')); root.append(box); return;
  }
  state.documents.forEach((document) => {
    const card = node('article', 'document-card'); card.dataset.documentId = document.id;
    const status = statusLabel(document); const owned = document.owner_id === state.user.id; const job = state.jobs.get(document.id);
    const top = node('div', 'card-top'); top.append(node('span', `file-icon ${document.file_type}`, document.file_type.toUpperCase()), node('span', `badge ${status.status}`, status.text));
    const size = document.file_size_bytes < 1024 * 1024 ? `${Math.max(1, Math.round(document.file_size_bytes / 1024))} KB` : `${(document.file_size_bytes / 1024 / 1024).toFixed(1)} MB`;
    const recording = isRecording(document); const length = document.media_duration_seconds ? `${formatTime(document.media_duration_seconds)} ${document.media_kind === 'video' ? 'video' : 'audio'} · ` : '';
    card.append(top, node('h3', '', document.original_filename), node('p', 'card-meta', `${length}${size} · ${new Date(document.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`), node('p', 'card-visibility', document.visibility === 'course' ? 'Shared course material' : 'Private · only you'));
    const actions = node('div', 'card-actions');
    if (recording) { const p = control('▶ Open recording', () => openRecording(document.id, document.original_filename), 'button primary compact'); p.dataset.action = 'play'; actions.append(p); }
    actions.append(downloadControl(document.id, document.original_filename));
    if (owned) {
      const b = control(document.processing_status === 'failed' ? 'Retry processing' : document.processing_status === 'embedded' ? 'Process again' : 'Process material', () => queueDocument(document.id)); b.dataset.action = 'process'; b.disabled = active(job) || state.pending.has(document.id); actions.append(b);
      const d = control('Delete', () => deleteDocument(document), 'text-button danger'); d.dataset.action = 'delete'; d.disabled = active(job); actions.append(d);
    }
    if (!recording && ['extracted', 'chunked', 'embedded'].includes(document.processing_status)) { const b = control('Read passages ↗', () => readDocument(document), 'text-button'); b.dataset.action = 'read'; actions.append(b); }
    card.append(actions);
    if (!active(job) && document.processing_status === 'failed' && owned && job?.error_message) card.append(node('p', 'job-note job-error', job.error_message));
    if (active(job)) card.append(node('p', 'job-note', { queued: 'Waiting to process.', extract: recording ? 'Transcribing your recording. Long lectures can take a while…' : 'Reading your document…', chunk: 'Preparing useful passages…', embed: 'Making your material searchable…' }[job.stage] || 'Processing your material…'));
    root.append(card);
  });
  if (focusId && focusAction) root.querySelector(`[data-document-id="${CSS.escape(focusId)}"] [data-action="${CSS.escape(focusAction)}"]`)?.focus({ preventScroll: true });
}
async function deleteDocument(document) {
  if (!window.confirm(`Delete “${document.original_filename}”? Its passages and the original file are removed permanently.`)) return;
  await api(`/documents/${encodeURIComponent(document.id)}`, { method: 'DELETE' }); state.jobs.delete(document.id); await loadDocuments(); notify('Material deleted.');
}
async function queueDocument(id) {
  if (state.pending.has(id)) return; state.pending.add(id); renderDocuments();
  try { state.jobs.set(id, await api(`/documents/${id}/process`, { method: 'POST' })); notify('Processing queued. You can keep exploring while it runs.'); }
  finally { state.pending.delete(id); renderDocuments(); schedulePoll(); }
}
function schedulePoll() {
  clearTimeout(state.poll); state.poll = null;
  if (state.user && Array.from(state.jobs.values()).some(active)) state.poll = setTimeout(pollJobs, 2500);
}
async function pollJobs() {
  if (!state.user) return;
  try {
    for (const [id, job] of state.jobs) {
      if (!active(job)) continue;
      const next = await api(`/documents/${id}/processing`); state.jobs.set(id, next);
      if (!active(next)) { const document = await api(`/documents/${id}`); state.documents = state.documents.map((d) => d.id === id ? document : d); }
    }
    renderDocuments();
  } catch (e) { if (!e.cancelled) error('library-error', e.message); }
  finally { schedulePoll(); }
}
const recordingTypes = ['mp3', 'm4a', 'wav', 'ogg', 'webm', 'mp4'];
const isRecording = (document) => Boolean(document?.media_kind) || recordingTypes.includes(document?.file_type);
function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0)); const h = Math.floor(total / 3600); const m = Math.floor(total / 60) % 60; const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}
function locationLabel(location) {
  if (!location || location.kind === 'unknown') return 'Location unavailable';
  if (location.kind === 'recording') return `Recording ${formatTime(location.start_seconds)}–${formatTime(location.end_seconds)}`;
  if (location.page) return `Page ${location.page}`; if (location.slide) return `Slide ${location.slide}`;
  if (location.paragraph) return `${location.section ? `${location.section} · ` : ''}Paragraph ${location.paragraph}`;
  if (location.table) return `${location.section ? `${location.section} · ` : ''}Table ${location.table}`;
  return location.line_start === location.line_end ? `Line ${location.line_start}` : `Lines ${location.line_start}–${location.line_end}`;
}
function playControl(documentId, name, location) {
  return control(`▶ Play from ${formatTime(location.start_seconds)}`, () => openRecording(documentId, name, location.start_seconds), 'button secondary compact');
}
function showSource(name, location, text, documentId) {
  $('source-title').textContent = name; $('source-location').textContent = locationLabel(location);
  const actions = node('div', 'card-actions'); if (location?.kind === 'recording') actions.append(playControl(documentId, name, location)); actions.append(downloadControl(documentId, name));
  $('source-content').replaceChildren(node('p', '', text), actions); if (!$('source-dialog').open) $('source-dialog').showModal();
}
async function readDocument(document) {
  $('source-title').textContent = document.original_filename; $('source-location').textContent = 'Extracted passages'; $('source-content').replaceChildren(node('p', '', 'Loading passages…')); $('source-dialog').showModal();
  try {
    const chunks = await api(`/documents/${document.id}/chunks?limit=100`); const root = $('source-content'); root.replaceChildren();
    if (!chunks.length) { const text = await api(`/documents/${document.id}/text`); root.append(node('p', '', text.text)); }
    else { chunks.forEach((chunk) => { const block = node('article'); block.append(node('h3', '', locationLabel(chunk.source_location)), node('p', '', chunk.content)); root.append(block); }); if (chunks.length === 100) root.append(node('p', 'muted', 'Showing the first 100 passages. Search to find a specific topic.')); }
    root.append(downloadControl(document.id, document.original_filename));
  } catch (e) { if (!e.cancelled) $('source-content').replaceChildren(node('p', 'error', e.message)); }
}
function openUpload() { error('upload-error'); $('upload-dialog').showModal(); }
function updateSharing() { const course = state.mine.find((c) => c.id === $('upload-course').value); const allowed = course?.instructor_id === state.user?.id && ['LECTURER', 'ADMIN'].includes(state.user?.role); $('share-label').hidden = !allowed; if (!allowed) $('share-material').checked = false; }
async function enterWorkspace(user) {
  state.user = user; $('profile-name').textContent = user.full_name; $('profile-role').textContent = user.role.toLowerCase(); $('avatar').textContent = user.full_name.split(/\s+/).slice(0, 2).map((n) => n[0]).join('').toUpperCase();
  $('auth-screen').hidden = true; $('workspace').hidden = false; $('course-create').hidden = !['LECTURER', 'ADMIN'].includes(user.role); navigate('library');
  try { await loadMine(); await Promise.all([loadDocuments(), loadCourses()]); }
  catch (e) { if (!e.cancelled) { error('library-error', e.message); error('courses-error', e.message); } }
}
$('auth-toggle').addEventListener('click', () => {
  state.register = !state.register; error('auth-error'); $('name-label').hidden = !state.register; $('full-name').required = state.register;
  $('auth-title').textContent = state.register ? 'Your next chapter.' : 'Welcome back.'; $('auth-description').textContent = state.register ? 'Create a student account and make room for your learning.' : 'Sign in to pick up where you left off.';
  $('auth-submit').textContent = state.register ? 'Create account ↗' : 'Sign in ↗'; $('auth-toggle').textContent = state.register ? 'Sign in' : 'Create an account'; $('auth-switch-label').textContent = state.register ? 'Already have an account?' : 'New here?'; $('password-help').hidden = !state.register;
  $('password').autocomplete = state.register ? 'new-password' : 'current-password'; if (state.register) $('password').minLength = 12; else $('password').removeAttribute('minlength');
});
$('auth-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('auth-submit'), async () => {
  const data = { email: $('email').value.trim(), password: $('password').value };
  if (state.register) { if (new TextEncoder().encode(data.password).length > 72) throw new Error('This password is too long. Use fewer characters.'); await api('/auth/register', { method: 'POST', body: JSON.stringify({ ...data, full_name: $('full-name').value.trim() }) }); }
  const token = await api('/auth/login', { method: 'POST', body: JSON.stringify(data) }); state.token = token.access_token; try { sessionStorage.setItem(tokenKey, state.token); } catch { /* Continue with this page's in-memory session. */ } $('password').value = ''; await enterWorkspace(await api('/users/me'));
}, 'auth-error'); });
$('logout').addEventListener('click', async () => {
  // Revoke the token on the server too; sign out locally even if that request fails.
  try { if (state.token) await api('/auth/logout', { method: 'POST' }); } catch { /* Local sign-out still proceeds. */ }
  logout();
});
$('join-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('join-submit'), async () => {
  const course = joining; if (!course) return;
  await api(`/courses/${course.id}/enroll`, { method: 'POST', body: JSON.stringify({ join_code: $('join-code').value.trim() }) });
  $('join-dialog').close(); await loadMine(); renderCourses(); await loadDocuments(); notify('Course joined. Its published materials are now in your library.');
}, 'join-error'); });
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => navigate(b.dataset.view)));
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => $(b.dataset.close).close()));
$('go-assistant').addEventListener('click', () => { navigate('assistant'); $('question').focus(); }); $('open-upload').addEventListener('click', openUpload);
$('refresh-library').addEventListener('click', () => busy($('refresh-library'), async () => { await loadMine(); await loadDocuments(); }, 'library-error'));
$('library-filter').addEventListener('change', () => loadDocuments().catch((e) => { if (!e.cancelled) error('library-error', e.message); }));
$('load-documents').addEventListener('click', () => busy($('load-documents'), () => loadDocuments(true), 'library-error'));
$('load-courses').addEventListener('click', () => busy($('load-courses'), () => loadCourses(true), 'courses-error'));
$('upload-course').addEventListener('change', updateSharing);
$('upload-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('upload-submit'), async () => {
  const file = $('upload-file').files[0]; if (!file) throw new Error('Choose a document first.'); const recording = uploadIsRecording();
  if (!recording && !/\.(pdf|docx|pptx|txt)$/i.test(file.name)) throw new Error('Choose a PDF, Word, PowerPoint or TXT file, or a lecture recording.');
  if (file.size > (recording ? 200 : 20) * 1024 * 1024) throw new Error(recording ? 'Choose a recording smaller than 200 MB.' : 'Choose a file smaller than 20 MB.');
  const data = new FormData(); data.append('file', file); if ($('upload-course').value) data.append('course_id', $('upload-course').value); data.append('visibility', $('share-material').checked ? 'course' : 'private');
  const captions = $('upload-captions').files[0]; if (recording && captions) data.append('captions', captions);
  // Large recordings can take a while to send on a slow connection.
  const uploaded = await api('/documents/upload', { method: 'POST', body: data, timeout: recording ? 30 * 60000 : undefined }); $('upload-dialog').close(); $('upload-form').reset(); $('captions-label').hidden = true; updateSharing();
  try { await queueDocument(uploaded.id); } catch (e) { if (!e.cancelled) notify('Material uploaded. Processing could not start; use Process material in your library.'); }
  $('library-filter').value = ''; navigate('library'); await loadDocuments();
}, 'upload-error'); });
$('course-form').addEventListener('submit', (event) => { event.preventDefault(); busy(event.submitter, async () => { await api('/courses', { method: 'POST', body: JSON.stringify({ code: $('course-code').value, title: $('course-title').value }) }); $('course-form').reset(); await loadMine(); await loadCourses(); notify('Course created.'); }, 'courses-error'); });
document.querySelectorAll('input[name=mode]').forEach((radio) => radio.addEventListener('change', () => { $('question-submit').textContent = radio.value === 'chat' ? 'Ask with sources ↗' : 'Find passages ↗'; }));
$('question-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('question-submit'), async () => {
  const mode = document.querySelector('input[name=mode]:checked').value; const payload = { query: $('question').value.trim(), top_k: 5 }; if ($('question-course').value) payload.course_id = $('question-course').value;
  const response = await api(mode === 'chat' ? '/chat' : '/search', { method: 'POST', body: JSON.stringify(payload) }); const root = $('answer'); root.replaceChildren();
  if (mode === 'search') {
    if (!response.results.length) { root.append(empty('No passages to show yet.', response.status === 'no_searchable_materials' ? 'Process your material first, or try another course.' : 'Try a different question or another course.')); return; }
    root.append(node('p', 'small muted', `${response.results.length} relevant passages · similarity is a ranking signal, not answer confidence.`));
    response.results.forEach((result) => { const card = node('article', 'source-card'); card.append(node('p', 'card-meta', locationLabel(result.source_location)), node('h3', '', result.document_name), node('p', '', result.text)); const actions = node('div', 'card-actions'); if (result.source_location?.kind === 'recording') actions.append(playControl(result.document_id, result.document_name, result.source_location)); actions.append(control('View source ↗', () => showSource(result.document_name, result.source_location, result.text, result.document_id), 'text-button'), control('Make practice question', () => openAuthoring(result))); card.append(actions); root.append(card); });
  } else {
    const panel = node('article', 'answer-panel'); panel.append(node('h2', '', response.status === 'answered' ? 'From your materials' : 'Not enough support in your materials'));
    const text = node('div', 'answer-text'); const sources = new Map(response.citations.map((c) => [c.source_id, c]));
    response.answer.split(/\[(S\d+)\]/g).forEach((part, index) => { if (index % 2 && sources.has(part)) { const c = sources.get(part); text.append(control(`[${part}]`, () => showSource(c.document_name, c.source_location, c.quote, c.document_id), 'citation-inline')); } else text.append(document.createTextNode(index % 2 ? `[${part}]` : part)); });
    panel.append(text); const citations = node('div', 'citation-row'); response.citations.forEach((c) => citations.append(control(`${c.source_id} · ${c.document_name} · ${locationLabel(c.source_location)}`, () => showSource(c.document_name, c.source_location, c.quote, c.document_id)))); panel.append(citations); root.append(panel);
  }
}, 'question-error'); });
// Recordings: playback from any passage, a timestamped transcript the uploader can correct, and private notes.
function uploadIsRecording() { const file = $('upload-file').files[0]; return Boolean(file) && new RegExp(`\\.(${recordingTypes.join('|')})$`, 'i').test(file.name); }
function media() { return $('recording-player').querySelector('audio, video'); }
function closeRecording() {
  const player = media(); if (player) { player.pause(); player.removeAttribute('src'); player.load(); }
  state.recording = null;
}
function seek(seconds) { const player = media(); if (!player) return; player.currentTime = seconds; player.play().catch(() => { /* The student can press play. */ }); }
async function openRecording(id, name, start = 0) {
  closeRecording();
  const recording = { id, start: Math.max(0, Number(start) || 0), document: null, transcript: null, notes: [], editing: null, editingNote: null }; state.recording = recording;
  $('recording-title').textContent = name; $('recording-meta').textContent = 'Loading the recording…'; error('recording-error'); $('recording-stale').hidden = true; $('transcript-filter').value = ''; $('note-body').value = '';
  ['recording-player', 'transcript', 'notes', 'transcript-tools'].forEach((key) => $(key).replaceChildren());
  if ($('source-dialog').open) $('source-dialog').close();
  if (!$('recording-dialog').open) $('recording-dialog').showModal();
  try {
    const [document, link, transcript, notes] = await Promise.all([api(`/documents/${id}`), api(`/documents/${id}/media-link`, { method: 'POST' }), api(`/documents/${id}/transcript`), api(`/documents/${id}/notes`)]);
    if (state.recording !== recording) return;
    Object.assign(recording, { document, transcript, notes });
    const player = node(document.media_kind === 'video' ? 'video' : 'audio', 'media'); player.controls = true; player.preload = 'metadata';
    player.setAttribute('aria-label', `Play ${name}`);
    // A media fragment starts playback at the passage; the server answers range requests for seeking.
    player.src = recording.start ? `${link.url}#t=${recording.start}` : link.url;
    player.addEventListener('timeupdate', () => highlightSegment(player.currentTime));
    player.addEventListener('error', () => { if (state.recording === recording) error('recording-error', 'Playback stopped. Close the recording and open it again.'); });
    $('recording-player').append(player);
    renderRecordingMeta(); renderTranscript(); renderNotes(); highlightSegment(recording.start);
  } catch (e) { if (!e.cancelled && state.recording === recording) { $('recording-meta').textContent = ''; error('recording-error', e.message); } }
}
function renderRecordingMeta() {
  const { id, document, transcript } = state.recording; const parts = [];
  if (document.media_duration_seconds) parts.push(`${formatTime(document.media_duration_seconds)} ${document.media_kind === 'video' ? 'video' : 'audio'}`);
  parts.push(transcript.source === 'captions' ? 'Transcript from captions' : transcript.source ? 'Automatic transcript · check names and figures against the audio' : 'No transcript yet');
  if (transcript.segments.some((s) => s.edited)) parts.push('corrected by the uploader');
  $('recording-meta').textContent = parts.join(' · ');
  const tools = $('transcript-tools'); tools.replaceChildren(); const banner = $('recording-stale'); banner.hidden = true;
  if (!transcript.editable) return;
  const processing = active(state.jobs.get(id));
  const reprocess = (label) => control(label, async () => { await queueDocument(id); if (state.recording?.id === id) { state.recording.transcript.stale = false; renderRecordingMeta(); } });
  if (processing) { banner.hidden = false; banner.replaceChildren(node('span', '', 'Processing this recording. Search and answers will use the new transcript when it finishes.')); }
  else if (transcript.stale) { banner.hidden = false; banner.replaceChildren(node('span', '', 'You changed the transcript. Search and answers still use the previous version.'), reprocess('Update search')); }
  else if (document.processing_status !== 'embedded') { banner.hidden = false; banner.replaceChildren(node('span', '', transcript.segments.length ? 'This transcript is not searchable yet.' : 'Process the recording to create its transcript.'), reprocess('Process recording')); }
  tools.append(control('Replace captions', () => $('captions-file').click(), 'text-button'));
  if (transcript.segments.length) tools.append(control('Transcribe again', async () => {
    if (!window.confirm('Remove this transcript, including your corrections, and transcribe the recording again? This needs automatic transcription on the server.')) return;
    await api(`/documents/${id}/transcript`, { method: 'DELETE' }); if (state.recording?.id !== id) return;
    state.recording.transcript = await api(`/documents/${id}/transcript`); renderRecordingMeta(); renderTranscript(); await queueDocument(id); renderRecordingMeta();
  }, 'text-button danger'));
}
function renderTranscript() {
  const recording = state.recording; const { transcript } = recording; const root = $('transcript'); root.replaceChildren(); const filter = $('transcript-filter').value.trim().toLowerCase();
  if (!transcript.segments.length) { root.append(node('li', 'muted transcript-empty', transcript.editable ? 'No transcript yet. Process the recording to transcribe it, or use Replace captions to add a caption file.' : 'The transcript is not ready yet. You can still play the recording.')); return; }
  transcript.segments.forEach((segment) => {
    if (filter && !segment.text.toLowerCase().includes(filter)) return;
    const item = node('li', 'segment'); item.dataset.start = segment.start_seconds;
    const time = control(formatTime(segment.start_seconds), () => seek(segment.start_seconds), 'time-button'); time.setAttribute('aria-label', `Play from ${formatTime(segment.start_seconds)}`); item.append(time);
    if (recording.editing === segment.id) {
      const form = node('form', 'inline-edit'); const input = node('textarea'); input.value = segment.text; input.rows = 2; input.maxLength = 2000; input.required = true; input.setAttribute('aria-label', `Transcript at ${formatTime(segment.start_seconds)}`);
      const save = node('button', 'button primary compact', 'Save'); form.append(input, save, control('Cancel', () => { recording.editing = null; renderTranscript(); }, 'text-button'));
      form.addEventListener('submit', (event) => { event.preventDefault(); busy(save, async () => {
        const updated = await api(`/documents/${recording.id}/transcript/segments/${segment.id}`, { method: 'PATCH', body: JSON.stringify({ text: input.value }) });
        if (state.recording !== recording) return; recording.transcript = updated; recording.editing = null; renderRecordingMeta(); renderTranscript(); notify('Transcript corrected.');
      }, 'recording-error'); });
      item.append(form); setTimeout(() => input.focus());
    } else {
      item.append(node('span', 'segment-text', segment.text));
      if (segment.edited) item.append(node('span', 'badge', 'Corrected'));
      if (transcript.editable) item.append(control('Edit', () => { recording.editing = segment.id; renderTranscript(); }, 'text-button'));
    }
    root.append(item);
  });
  if (!root.children.length) root.append(node('li', 'muted transcript-empty', 'No lines match.'));
}
function highlightSegment(seconds) {
  if (!state.recording) return; $('note-time').textContent = formatTime(seconds);
  const root = $('transcript'); let current = null;
  root.querySelectorAll('.segment').forEach((item) => { if (Number(item.dataset.start) <= seconds + 0.25) current = item; });
  root.querySelectorAll('.segment.current').forEach((item) => { if (item !== current) { item.classList.remove('current'); item.removeAttribute('aria-current'); } });
  if (!current || current.classList.contains('current')) return;
  current.classList.add('current'); current.setAttribute('aria-current', 'true');
  // Follow playback inside the transcript only, without moving the page or fighting the reader.
  if (!media()?.paused && (current.offsetTop < root.scrollTop || current.offsetTop > root.scrollTop + root.clientHeight - current.offsetHeight)) root.scrollTop = current.offsetTop - root.clientHeight / 3;
}
function renderNotes() {
  const recording = state.recording; const root = $('notes'); root.replaceChildren();
  if (!recording.notes.length) { root.append(node('li', 'muted small', 'No notes yet. Pause at a useful moment and write one.')); return; }
  recording.notes.forEach((note) => {
    const item = node('li', 'note');
    if (note.timestamp_seconds !== null) { const time = control(formatTime(note.timestamp_seconds), () => seek(note.timestamp_seconds), 'time-button'); time.setAttribute('aria-label', `Play from ${formatTime(note.timestamp_seconds)}`); item.append(time); }
    if (recording.editingNote === note.id) {
      const form = node('form', 'inline-edit'); const input = node('textarea'); input.value = note.body; input.rows = 3; input.maxLength = 5000; input.required = true; input.setAttribute('aria-label', 'Edit note');
      const save = node('button', 'button primary compact', 'Save'); form.append(input, save, control('Cancel', () => { recording.editingNote = null; renderNotes(); }, 'text-button'));
      form.addEventListener('submit', (event) => { event.preventDefault(); busy(save, async () => {
        const updated = await api(`/notes/${note.id}`, { method: 'PATCH', body: JSON.stringify({ body: input.value }) });
        if (state.recording !== recording) return; recording.notes = recording.notes.map((n) => n.id === note.id ? updated : n); recording.editingNote = null; renderNotes();
      }, 'recording-error'); });
      item.append(form); setTimeout(() => input.focus());
    } else {
      const actions = node('div', 'note-actions');
      actions.append(control('Edit', () => { recording.editingNote = note.id; renderNotes(); }, 'text-button'), control('Delete', async () => {
        if (!window.confirm('Delete this note?')) return; await api(`/notes/${note.id}`, { method: 'DELETE' });
        if (state.recording !== recording) return; recording.notes = recording.notes.filter((n) => n.id !== note.id); renderNotes();
      }, 'text-button danger'));
      item.append(node('p', 'note-body', note.body), actions);
    }
    root.append(item);
  });
}
$('upload-file').addEventListener('change', () => { const show = uploadIsRecording(); $('captions-label').hidden = !show; if (!show) $('upload-captions').value = ''; });
$('recording-dialog').addEventListener('close', closeRecording);
$('transcript-filter').addEventListener('input', () => { if (state.recording?.transcript) { renderTranscript(); highlightSegment(media()?.currentTime || 0); } });
$('captions-file').addEventListener('change', () => {
  const file = $('captions-file').files[0]; const recording = state.recording; if (!file || !recording) return;
  const data = new FormData(); data.append('captions', file); error('recording-error');
  api(`/documents/${recording.id}/transcript`, { method: 'PUT', body: data }).then((transcript) => {
    if (state.recording !== recording) return; recording.transcript = transcript; renderRecordingMeta(); renderTranscript(); notify('Captions imported.');
  }).catch((e) => { if (!e.cancelled) error('recording-error', e.message); }).finally(() => { $('captions-file').value = ''; });
});
$('note-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('note-submit'), async () => {
  const recording = state.recording; if (!recording?.document) return; const player = media();
  const timestamp = $('note-pin').checked && player ? Math.round(player.currentTime * 10) / 10 : null;
  await api(`/documents/${recording.id}/notes`, { method: 'POST', body: JSON.stringify({ body: $('note-body').value, timestamp_seconds: timestamp }) });
  const notes = await api(`/documents/${recording.id}/notes`); if (state.recording !== recording) return;
  recording.notes = notes; $('note-body').value = ''; renderNotes(); notify('Note saved.');
}, 'recording-error'); });
// Practice: topics, mastery, quizzes, the study plan and source-backed questions.
const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const levelLabel = { not_enough_evidence: 'Not enough evidence', weak: 'Needs work', developing: 'Developing', strong: 'Strong' };
const kindLabel = { quiz: 'Short quiz', revision: 'Revision', practice: 'Practice', review: 'Quick review' };
const difficultyLabel = { 1: 'Easy for me', 2: 'Normal', 3: 'Hard for me' };
function localDate(d = new Date()) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function dayLabel(iso) {
  const today = localDate(); const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
  if (iso === today) return 'Today'; if (iso === localDate(tomorrow)) return 'Tomorrow';
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}
function practiceCourse() { return state.mine.find((c) => c.id === $('practice-course').value); }
function teaches(course) { return Boolean(course) && course.instructor_id === state.user.id && ['LECTURER', 'ADMIN'].includes(state.user.role); }
function manages(course) { return Boolean(course) && (course.instructor_id === state.user.id || state.user.role === 'ADMIN'); }
weekdays.forEach((day, index) => { const label = node('label', 'weekday', day); const input = node('input'); input.type = 'number'; input.id = `weekday-${index}`; input.min = 0; input.max = 600; input.step = 15; input.required = true; label.append(input); $('weekday-inputs').append(label); });
for (let index = 0; index < 4; index++) {
  const row = node('div', 'option-row'); const radio = node('input'); radio.type = 'radio'; radio.name = 'author-correct'; radio.value = index; radio.required = true; radio.setAttribute('aria-label', `Option ${index + 1} is correct`);
  const input = node('input'); input.id = `author-option-${index}`; input.maxLength = 300; input.required = index < 2; input.placeholder = index < 2 ? `Option ${index + 1}` : `Option ${index + 1} (optional)`; input.setAttribute('aria-label', `Option ${index + 1}`);
  row.append(radio, input); $('author-option-rows').append(row);
}
async function loadPractice() {
  error('practice-error'); const course = practiceCourse(); $('practice-empty').replaceChildren(); $('practice-body').hidden = !course; $('start-quiz').disabled = !course;
  if (!course) { $('practice-empty').append(empty('Join a course to start practising.', 'Practice questions, progress and your study plan are organised by course.')); return; }
  const id = course.id; const today = localDate();
  const [topics, questions] = await Promise.all([api(`/courses/${id}/topics`), api(`/questions?course_id=${id}&limit=100`)]);
  let plan = await api(`/courses/${id}/study-plan?today=${today}`);
  const planned = plan.sessions.filter((s) => s.status === 'planned');
  // Replan when the plan is empty or has fallen behind; otherwise keep it stable.
  if (topics.length && (!planned.some((s) => s.session_date >= today) || planned.some((s) => s.session_date < today))) plan = await api(`/courses/${id}/study-plan/rebuild?today=${today}`, { method: 'POST' });
  if ($('practice-course').value !== id) return;
  state.practice = { topics, questions, plan }; renderPractice();
}
function renderPractice() {
  const course = practiceCourse(); if (!course || !state.practice.plan) return;
  $('topic-form').hidden = !manages(course);
  $('generate-topic').replaceChildren(...state.practice.topics.map((t) => new Option(t.name, t.id)));
  $('generate-form').hidden = !state.practice.topics.length;
  renderMastery(); renderPlan(); renderQuestions();
}
function renderMastery() {
  const root = $('mastery'); root.replaceChildren(); const { plan } = state.practice; const course = practiceCourse();
  if (!plan.mastery.length) { root.append(empty('No topics yet.', manages(course) ? 'Add the course topics below so students can practise them.' : 'Your lecturer has not added topics to this course yet.')); return; }
  plan.mastery.forEach((m) => {
    const card = node('article', 'document-card'); const top = node('div', 'card-top');
    top.append(node('span', 'file-icon', 'TOPIC'), node('span', `badge level-${m.level}`, levelLabel[m.level])); card.append(top, node('h3', '', m.topic_name));
    if (m.score === null) card.append(node('p', 'card-meta', `${m.answers} of 4 answers needed for a score`));
    else { const meter = node('meter', 'mastery-meter'); meter.min = 0; meter.max = 1; meter.low = 0.6; meter.high = 0.8; meter.optimum = 1; meter.value = m.score; meter.setAttribute('aria-label', `${m.topic_name} mastery`); card.append(meter, node('p', 'card-meta', `${Math.round(m.score * 100)}% · ${m.answers} recent answers`)); }
    const label = node('label', 'difficulty', 'How hard is it for you?'); const select = node('select');
    [1, 2, 3].forEach((level) => select.append(new Option(difficultyLabel[level], level))); select.value = plan.settings.topic_difficulty[m.topic_id] || 2;
    select.addEventListener('change', () => savePlan({ [m.topic_id]: Number(select.value) }).catch((e) => { if (!e.cancelled) error('practice-error', e.message); })); label.append(select); card.append(label);
    const actions = node('div', 'card-actions'); actions.append(control('Practise this topic', () => startQuiz(m.topic_id))); card.append(actions); root.append(card);
  });
}
function renderPlan() {
  const { plan } = state.practice; $('exam-date').value = plan.settings.exam_date || ''; $('exam-date').min = localDate();
  plan.settings.weekday_minutes.forEach((minutes, index) => { $(`weekday-${index}`).value = minutes; });
  $('plan-notes').replaceChildren(...plan.notes.map((note) => node('li', 'muted', note)));
  if (!plan.settings.saved) $('plan-notes').append(node('li', 'muted', 'This plan uses default availability (one hour on weekdays). Adjust it to fit your week.'));
  const root = $('plan-sessions'); root.replaceChildren(); const today = localDate();
  const visible = plan.sessions.filter((s) => s.session_date >= today || s.status === 'missed');
  if (!visible.length) { root.append(node('p', 'muted', plan.mastery.length ? 'No sessions planned. Add some available time above.' : 'Your plan will appear once the course has topics.')); return; }
  const days = new Map(); visible.forEach((s) => { if (!days.has(s.session_date)) days.set(s.session_date, []); days.get(s.session_date).push(s); });
  days.forEach((sessions, day) => {
    const group = node('section', 'plan-day'); group.append(node('h3', '', dayLabel(day)));
    sessions.forEach((s) => {
      const row = node('article', `plan-session ${s.status}`); const text = node('div');
      text.append(node('p', 'card-meta', `${kindLabel[s.kind]} · ${s.minutes} min${s.status === 'planned' ? '' : ` · ${s.status === 'missed' ? 'Missed' : 'Done'}`}`), node('strong', '', s.topic_name), node('p', 'small muted', s.reason));
      const actions = node('div', 'session-actions');
      if (s.status !== 'completed' && s.session_date <= today) actions.append(control('Practise', () => startQuiz(s.topic_id)), control('Mark done', async () => { await api(`/study-sessions/${s.id}/complete`, { method: 'POST' }); await loadPractice(); notify('Session marked as done.'); }, 'text-button'));
      row.append(text, actions); group.append(row);
    });
    root.append(group);
  });
}
function renderQuestions() {
  const root = $('my-questions'); root.replaceChildren(); const course = practiceCourse(); const { topics, questions } = state.practice;
  const topicName = (id) => topics.find((t) => t.id === id)?.name || 'Topic';
  if (!questions.length) { root.append(node('p', 'muted', 'You have not written practice questions for this course yet.')); return; }
  if (questions.some((q) => !q.reviewed)) root.append(node('p', 'small muted', 'Check AI drafts against their source before relying on them.'));
  questions.forEach((q) => {
    const card = node('article', 'source-card question-card'); const badges = node('div', 'badge-row');
    badges.append(node('span', 'badge', q.origin === 'generated' ? 'AI draft' : 'Written by you'), node('span', q.reviewed ? 'badge' : 'badge queued', q.reviewed ? 'Reviewed' : 'Needs review'), node('span', 'badge', q.visibility === 'course' ? 'Published to class' : 'Private'));
    card.append(badges, node('p', 'card-meta', `${topicName(q.topic_id)} · ${locationLabel(q.source_location)}`), node('h3', '', q.stem));
    const list = node('ol', 'option-list'); q.options.forEach((option, index) => list.append(node('li', index === q.correct_index ? 'correct' : '', index === q.correct_index ? `${option} · correct` : option)));
    card.append(list, node('p', 'small muted', q.explanation), node('p', 'quote', `“${q.source_quote}”`));
    const actions = node('div', 'card-actions'); const update = async (body, message) => { await api(`/questions/${q.id}`, { method: 'PATCH', body: JSON.stringify(body) }); await loadPractice(); notify(message); };
    if (!q.reviewed) actions.append(control('Mark reviewed', () => update({ reviewed: true }, 'Marked as reviewed.')));
    if (teaches(course)) actions.append(control(q.visibility === 'course' ? 'Unpublish' : 'Publish to class', async () => { await api(`/questions/${q.id}/publish`, { method: q.visibility === 'course' ? 'DELETE' : 'POST' }); await loadPractice(); notify(q.visibility === 'course' ? 'Question is private again.' : 'Enrolled students can now practise this question.'); }));
    actions.append(control('Delete', async () => { if (!window.confirm('Delete this question? Past quiz results keep their grade.')) return; await api(`/questions/${q.id}`, { method: 'DELETE' }); await loadPractice(); notify('Question deleted.'); }, 'text-button danger'));
    card.append(actions); root.append(card);
  });
}
function planSettings(difficulty = {}) {
  return { exam_date: $('exam-date').value || null, weekday_minutes: weekdays.map((_, index) => Math.max(0, Math.min(600, Number($(`weekday-${index}`).value) || 0))), topic_difficulty: { ...state.practice.plan.settings.topic_difficulty, ...difficulty } };
}
async function savePlan(difficulty) {
  const course = practiceCourse(); if (!course) return;
  state.practice.plan = await api(`/courses/${course.id}/study-plan/settings?today=${localDate()}`, { method: 'PUT', body: JSON.stringify(planSettings(difficulty)) }); renderPractice();
}
async function startQuiz(topicId = null) {
  const course = practiceCourse(); if (!course) return;
  state.quiz = await api('/quizzes', { method: 'POST', body: JSON.stringify({ course_id: course.id, topic_id: topicId, size: 5 }) });
  renderQuiz(); if (!$('quiz-dialog').open) $('quiz-dialog').showModal();
}
function renderQuiz() {
  const quiz = state.quiz; const submitted = quiz.status === 'submitted'; const root = $('quiz-items'); root.replaceChildren(); error('quiz-error');
  $('quiz-title').textContent = submitted ? `You scored ${quiz.correct_count} of ${quiz.total_count}` : `${quiz.total_count} question${quiz.total_count === 1 ? '' : 's'}`;
  $('quiz-summary').textContent = submitted ? 'Each answer links back to the passage it came from.' : 'Choose one answer for each question. Skip any you do not know; skipped questions count as incorrect.';
  $('quiz-submit').hidden = submitted;
  quiz.items.forEach((item) => {
    const box = node('fieldset', 'quiz-item'); const legend = node('legend'); legend.append(node('span', 'card-meta', item.topic_name || 'Practice'), node('strong', '', `${item.position + 1}. ${item.stem}`)); box.append(legend);
    item.options.forEach((option, index) => {
      const label = node('label', 'quiz-option'); const radio = node('input'); radio.type = 'radio'; radio.name = `quiz-${item.position}`; radio.value = index; radio.disabled = submitted; radio.checked = item.chosen_index === index;
      if (submitted && index === item.correct_index) label.classList.add('correct'); if (submitted && index === item.chosen_index && !item.is_correct) label.classList.add('incorrect');
      label.append(radio, document.createTextNode(option));
      // Say it in words as well as colour.
      if (submitted && index === item.correct_index) label.append(node('span', 'option-mark', index === item.chosen_index ? 'Your answer · correct' : 'Correct answer'));
      else if (submitted && index === item.chosen_index) label.append(node('span', 'option-mark', 'Your answer'));
      box.append(label);
    });
    if (submitted) {
      const answer = item.options[item.correct_index];
      box.append(node('p', item.is_correct ? 'result correct' : 'result incorrect', item.is_correct ? 'Correct.' : item.chosen_index === null ? `Skipped. The answer is “${answer}”.` : `Not quite. The answer is “${answer}”.`));
      if (item.explanation) box.append(node('p', 'small', item.explanation));
      if (item.source) box.append(control(`View source · ${item.source.document_name} · ${locationLabel(item.source.source_location)} ↗`, () => showSource(item.source.document_name, item.source.source_location, item.source.quote, item.source.document_id), 'text-button'));
      else if (item.source_unavailable) box.append(node('p', 'small muted', 'The source for this question is no longer available to you.'));
    }
    root.append(box);
  });
}
async function openAuthoring(result) {
  if (!state.mine.length) { notify('Join a course first. Practice questions belong to a course topic.'); return; }
  state.authoring = result; $('author-form').reset(); error('author-error');
  $('author-source').textContent = `${result.document_name} · ${locationLabel(result.source_location)}`; $('author-quote').value = result.text;
  if (result.course_id && state.mine.some((c) => c.id === result.course_id)) $('author-course').value = result.course_id;
  else if (!$('author-course').value) $('author-course').value = state.mine[0].id;
  $('author-dialog').showModal(); await loadAuthorTopics();
}
async function loadAuthorTopics() {
  const id = $('author-course').value; $('author-topic').replaceChildren(new Option('Loading topics…', ''));
  try {
    const topics = await api(`/courses/${id}/topics`); if ($('author-course').value !== id) return;
    $('author-topic').replaceChildren(...(topics.length ? topics.map((t) => new Option(t.name, t.id)) : [new Option('This course has no topics yet', '')]));
  } catch (e) { if (!e.cancelled) error('author-error', e.message); }
}
$('practice-course').addEventListener('change', () => loadPractice().catch((e) => { if (!e.cancelled) error('practice-error', e.message); }));
$('start-quiz').addEventListener('click', () => busy($('start-quiz'), () => startQuiz(), 'practice-error'));
$('plan-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('plan-submit'), async () => { await savePlan(); notify('Plan updated.'); }, 'practice-error'); });
$('topic-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('topic-submit'), async () => {
  await api(`/courses/${practiceCourse().id}/topics`, { method: 'POST', body: JSON.stringify({ name: $('topic-name').value }) }); $('topic-form').reset(); await loadPractice(); notify('Topic added.');
}, 'practice-error'); });
$('generate-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('generate-submit'), async () => {
  const result = await api('/questions/generate', { method: 'POST', body: JSON.stringify({ topic_id: $('generate-topic').value, count: Number($('generate-count').value) }) });
  if (result.status === 'no_searchable_materials') notify('Process some course material first; nothing is searchable yet.');
  else if (result.status === 'no_matches') notify('No passages matched this topic.');
  else notify(`${result.created.length} draft question${result.created.length === 1 ? '' : 's'} ready to review${result.rejected ? `; ${result.rejected} discarded because they could not be checked against the source` : ''}.`);
  await loadPractice();
}, 'practice-error'); });
$('quiz-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('quiz-submit'), async () => {
  const answers = {}; state.quiz.items.forEach((item) => { const checked = $('quiz-form').querySelector(`input[name="quiz-${item.position}"]:checked`); answers[item.position] = checked ? Number(checked.value) : null; });
  state.quiz = await api(`/quizzes/${state.quiz.id}/submit`, { method: 'POST', body: JSON.stringify({ answers }) }); renderQuiz();
  if (state.view === 'practice') await loadPractice();
}, 'quiz-error'); });
$('author-course').addEventListener('change', loadAuthorTopics);
$('author-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('author-submit'), async () => {
  const source = state.authoring; if (!source) return; if (!$('author-topic').value) throw new Error('Choose a topic. Your lecturer adds topics to each course.');
  const correct = Number($('author-form').querySelector('input[name="author-correct"]:checked')?.value ?? -1);
  const filled = [0, 1, 2, 3].map((index) => ({ index, text: $(`author-option-${index}`).value.trim() })).filter((o) => o.text);
  const correctIndex = filled.findIndex((o) => o.index === correct); if (correctIndex < 0) throw new Error('Select the correct answer next to a filled-in option.');
  await api('/questions', { method: 'POST', body: JSON.stringify({ topic_id: $('author-topic').value, chunk_id: source.chunk_id, source_quote: $('author-quote').value, stem: $('author-stem').value, options: filled.map((o) => o.text), correct_index: correctIndex, explanation: $('author-explanation').value }) });
  $('author-dialog').close(); notify('Question saved. It will appear in your quizzes for this course.');
}, 'author-error'); });
if (state.token) api('/users/me').then(enterWorkspace).catch((e) => { if (!e.cancelled) logout(e.message); });
