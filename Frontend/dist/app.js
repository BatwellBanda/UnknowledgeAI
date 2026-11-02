const $ = (id) => document.getElementById(id);
const tokenKey = 'uniknowledge.session';
let savedToken = null;
try { savedToken = sessionStorage.getItem(tokenKey); } catch { /* Use memory-only sign-in when browser storage is unavailable. */ }
const state = { token: savedToken, user: null, epoch: 0, documents: [], courses: [], mine: [], jobs: new Map(), pending: new Set(), requests: new Set(), view: 'library', register: false, poll: null };
const active = (job) => job && ['queued', 'running'].includes(job.status);
function node(tag, className = '', text = '') { const n = document.createElement(tag); n.className = className; n.textContent = text; return n; }
function control(text, action, className = 'button secondary compact') { const b = node('button', className, text); b.type = 'button'; b.addEventListener('click', () => Promise.resolve(action()).catch((e) => { if (!e.cancelled) notify(e.message); })); return b; }
function error(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
let noticeTimer;
function notify(message) { $('notification').textContent = message; $('notification').hidden = false; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('notification').hidden = true; }, 7000); }
function logout(message = '') {
  state.epoch++; state.requests.forEach((c) => c.abort()); state.requests.clear(); clearTimeout(state.poll); state.poll = null;
  try { sessionStorage.removeItem(tokenKey); } catch { /* No persistent token in this mode. */ }
  state.token = null; state.user = null; state.documents = []; state.courses = []; state.mine = []; state.jobs.clear(); state.pending.clear();
  ['documents', 'courses', 'answer', 'source-content'].forEach((id) => $(id).replaceChildren());
  document.querySelectorAll('dialog[open]').forEach((d) => d.close()); $('question-form').reset(); $('upload-form').reset(); $('workspace').hidden = true; $('auth-screen').hidden = false;
  error('auth-error', message); $('auth-form').reset(); $('email').focus();
}
async function api(path, options = {}, responseType = 'json') {
  const controller = new AbortController(); const epoch = state.epoch; state.requests.add(controller);
  const timer = setTimeout(() => controller.abort(), 150000);
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
  ['library', 'courses', 'assistant'].forEach((key) => { $(`${key}-view`).hidden = key !== view; });
  document.querySelectorAll('[data-view]').forEach((b) => { const selected = b.dataset.view === view; b.classList.toggle('active', selected); if (selected) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  $('breadcrumb').textContent = `Workspace / ${ { library: 'My materials', courses: 'Courses', assistant: 'Search & answers' }[view] }`;
}
function courseOptions() {
  for (const [id, label] of [['library-filter', 'All materials'], ['question-course', 'All my materials'], ['upload-course', 'Personal material']]) {
    const value = $(id).value; $(id).replaceChildren(new Option(label, ''));
    state.mine.forEach((course) => $(id).append(new Option(`${course.code} · ${course.title}`, course.id)));
    if (Array.from($(id).options).some((o) => o.value === value)) $(id).value = value;
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
    const actions = node('div', 'card-actions');
    if (joined) actions.append(control('View materials', () => { $('library-filter').value = course.id; navigate('library'); return loadDocuments(); }));
    if (state.user.role === 'STUDENT') actions.append(control(joined ? 'Leave course' : 'Join course', async () => {
      if (joined && !window.confirm('Leave this course? You will lose access to its published materials. Your private notes will remain available.')) return;
      await api(`/courses/${course.id}/enroll`, { method: joined ? 'DELETE' : 'POST' }); await loadMine(); renderCourses(); await loadDocuments(); notify(joined ? 'You have left the course.' : 'Course joined. Its published materials are now in your library.');
    }, joined ? 'text-button' : 'button primary compact'));
    card.append(actions); root.append(card);
  });
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
    card.append(top, node('h3', '', document.original_filename), node('p', 'card-meta', `${size} · ${new Date(document.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`), node('p', 'card-visibility', document.visibility === 'course' ? 'Shared course material' : 'Private · only you'));
    const actions = node('div', 'card-actions');
    actions.append(downloadControl(document.id, document.original_filename));
    if (owned) {
      const b = control(document.processing_status === 'failed' ? 'Retry processing' : document.processing_status === 'embedded' ? 'Process again' : 'Process material', () => queueDocument(document.id)); b.dataset.action = 'process'; b.disabled = active(job) || state.pending.has(document.id); actions.append(b);
    }
    if (['extracted', 'chunked', 'embedded'].includes(document.processing_status)) { const b = control('Read passages ↗', () => readDocument(document), 'text-button'); b.dataset.action = 'read'; actions.append(b); }
    card.append(actions);
    if (active(job)) card.append(node('p', 'job-note', { queued: 'Waiting to process.', extract: 'Reading your document…', chunk: 'Preparing useful passages…', embed: 'Making your material searchable…' }[job.stage] || 'Processing your material…'));
    root.append(card);
  });
  if (focusId && focusAction) root.querySelector(`[data-document-id="${CSS.escape(focusId)}"] [data-action="${CSS.escape(focusAction)}"]`)?.focus({ preventScroll: true });
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
function locationLabel(location) {
  if (!location || location.kind === 'unknown') return 'Location unavailable';
  if (location.page) return `Page ${location.page}`; if (location.slide) return `Slide ${location.slide}`;
  if (location.paragraph) return `${location.section ? `${location.section} · ` : ''}Paragraph ${location.paragraph}`;
  if (location.table) return `${location.section ? `${location.section} · ` : ''}Table ${location.table}`;
  return location.line_start === location.line_end ? `Line ${location.line_start}` : `Lines ${location.line_start}–${location.line_end}`;
}
function showSource(name, location, text, documentId) { $('source-title').textContent = name; $('source-location').textContent = locationLabel(location); $('source-content').replaceChildren(node('p', '', text), downloadControl(documentId, name)); if (!$('source-dialog').open) $('source-dialog').showModal(); }
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
$('logout').addEventListener('click', () => logout());
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => navigate(b.dataset.view)));
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => $(b.dataset.close).close()));
$('go-assistant').addEventListener('click', () => { navigate('assistant'); $('question').focus(); }); $('open-upload').addEventListener('click', openUpload);
$('refresh-library').addEventListener('click', () => busy($('refresh-library'), async () => { await loadMine(); await loadDocuments(); }, 'library-error'));
$('library-filter').addEventListener('change', () => loadDocuments().catch((e) => { if (!e.cancelled) error('library-error', e.message); }));
$('load-documents').addEventListener('click', () => busy($('load-documents'), () => loadDocuments(true), 'library-error'));
$('load-courses').addEventListener('click', () => busy($('load-courses'), () => loadCourses(true), 'courses-error'));
$('upload-course').addEventListener('change', updateSharing);
$('upload-form').addEventListener('submit', (event) => { event.preventDefault(); busy($('upload-submit'), async () => {
  const file = $('upload-file').files[0]; if (!file) throw new Error('Choose a document first.'); if (file.size > 20 * 1024 * 1024) throw new Error('Choose a file smaller than 20 MB.');
  if (!/\.(pdf|docx|pptx|txt)$/i.test(file.name)) throw new Error('Choose a PDF, Word, PowerPoint, or TXT file.');
  const data = new FormData(); data.append('file', file); if ($('upload-course').value) data.append('course_id', $('upload-course').value); data.append('visibility', $('share-material').checked ? 'course' : 'private');
  const uploaded = await api('/documents/upload', { method: 'POST', body: data }); $('upload-dialog').close(); $('upload-form').reset(); updateSharing();
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
    response.results.forEach((result) => { const card = node('article', 'source-card'); card.append(node('p', 'card-meta', locationLabel(result.source_location)), node('h3', '', result.document_name), node('p', '', result.text)); card.append(control('View source ↗', () => showSource(result.document_name, result.source_location, result.text, result.document_id), 'text-button')); root.append(card); });
  } else {
    const panel = node('article', 'answer-panel'); panel.append(node('h2', '', response.status === 'answered' ? 'From your materials' : 'Not enough support in your materials'));
    const text = node('div', 'answer-text'); const sources = new Map(response.citations.map((c) => [c.source_id, c]));
    response.answer.split(/\[(S\d+)\]/g).forEach((part, index) => { if (index % 2 && sources.has(part)) { const c = sources.get(part); text.append(control(`[${part}]`, () => showSource(c.document_name, c.source_location, c.quote, c.document_id), 'citation-inline')); } else text.append(document.createTextNode(index % 2 ? `[${part}]` : part)); });
    panel.append(text); const citations = node('div', 'citation-row'); response.citations.forEach((c) => citations.append(control(`${c.source_id} · ${c.document_name} · ${locationLabel(c.source_location)}`, () => showSource(c.document_name, c.source_location, c.quote, c.document_id)))); panel.append(citations); root.append(panel);
  }
}, 'question-error'); });
if (state.token) api('/users/me').then(enterWorkspace).catch((e) => { if (!e.cancelled) logout(e.message); });
