// Umbrella browser code. Talks to the endpoints in app/main.py. No login or tokens (demo).
const $ = id => document.getElementById(id);

// ---- conversation id: kept in this browser tab so a refresh continues the same chat ----
const KEY = 'umbrella_conversation_id';
let conversationId = sessionStorage.getItem(KEY) || crypto.randomUUID();
sessionStorage.setItem(KEY, conversationId);

$('newChat').onclick = () => {
  conversationId = crypto.randomUUID();
  sessionStorage.setItem(KEY, conversationId);
  $('messages').replaceChildren();
  $('chatView').classList.remove('started');
  toast('Started a new conversation');
};

// ---- helpers ----
const toast = msg => {
  const el = $('toast');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 4000);
};

// fetch wrapper: sends JSON, throws the server's error message on failure
const api = async (path, options = {}) => {
  const res = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...(options.headers || {})}});
  const data = await res.json();
  if (!res.ok) throw Error(data.detail || 'Request failed');
  return data;
};

// ---- tabs ----
const TITLES = {
  chat: "Ask your organization's knowledge",
  docs: 'Document management',
  approval: 'Approval requests',
  admin: 'Operational overview',
};
function view(name) {
  for (const x of ['chat', 'docs', 'approval', 'admin']) {
    $(x + 'View').classList.toggle('hidden', x !== name);
    $(x + 'Tab').classList.toggle('active', x === name);
  }
  $('heading').textContent = TITLES[name];
  if (name === 'docs') loadDocs();
  if (name === 'admin') loadMetrics();
  if (name === 'approval') loadApprovals();
}
$('chatTab').onclick = () => view('chat');
$('docsTab').onclick = () => view('docs');
$('adminTab').onclick = () => view('admin');
$('approvalTab').onclick = () => view('approval');
$('refreshApprovals').onclick = loadApprovals;

// ---- chat ----
// ---- tiny markdown renderer ----
// Models answer with **bold**, "- bullets" and "1. numbers". This builds real elements from them.
// Only createElement/textContent are used (never innerHTML), so answer text can't inject HTML.
function inline(parent, text) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
    if (!part) continue;
    if (part.startsWith('**') && part.endsWith('**')) {
      const b = document.createElement('strong'); b.textContent = part.slice(2, -2); parent.append(b);
    } else if (part.startsWith('`') && part.endsWith('`')) {
      const c = document.createElement('code'); c.textContent = part.slice(1, -1); parent.append(c);
    } else parent.append(part);
  }
}
function renderMarkdown(el, text) {
  let list = null, para = null;
  const flush = () => { list = null; para = null; };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    let m;
    if (!line) { flush(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flush(); const h = document.createElement('h3'); inline(h, m[2]); el.append(h);
    } else if ((m = line.match(/^[-*•]\s+(.*)$/)) || (m = line.match(/^\d+[.)]\s+(.*)$/))) {
      const ordered = /^\d/.test(line);
      if (!list || list.tagName !== (ordered ? 'OL' : 'UL')) {
        list = document.createElement(ordered ? 'ol' : 'ul'); el.append(list); para = null;
      }
      const li = document.createElement('li'); inline(li, m[1]); list.append(li);
    } else {
      list = null;
      if (!para) { para = document.createElement('p'); el.append(para); } else para.append(document.createElement('br'));
      inline(para, line);
    }
  }
}

// Adds one message bubble. A user message is plain text; an assistant message is rendered markdown.
function addMessage(content, kind, sources = []) {
  $('chatView').classList.add('started');          // hides the welcome block so answers get the room
  const el = document.createElement('div');
  el.className = 'message ' + kind;
  if (kind === 'assistant') renderMarkdown(el, content); else el.textContent = content;
  if (sources.length) {
    const wrap = document.createElement('div');
    wrap.className = 'sources';
    for (const s of sources) {                    // one expandable block per cited passage
      const d = document.createElement('details');
      const title = document.createElement('summary');
      title.textContent = `[${s.index}] ${s.title} — page ${s.page}`;
      const excerpt = document.createElement('p');
      excerpt.textContent = s.excerpt;
      d.append(title, excerpt);
      wrap.append(d);
    }
    el.append(wrap);
    const actions = document.createElement('div');
    actions.className = 'message-actions';
    const feedback = document.createElement('button');
    feedback.textContent = '👍 Helpful';
    feedback.onclick = async () => {
      try {
        await api('/api/feedback', {method: 'POST', body: JSON.stringify({rating: 1})});
        toast('Feedback saved');
      } catch (e) { toast(e.message); }
    };
    const copy = document.createElement('button');
    copy.textContent = 'Copy answer';
    copy.onclick = () => navigator.clipboard.writeText(content).then(() => toast('Copied'), () => toast('Copy failed'));
    actions.append(feedback, copy);
    el.append(actions);
  }
  $('messages').append(el);
  // Show a new answer from its first line (so you read from the top); other messages go to the bottom.
  if (kind === 'assistant' && content.length > 200) el.scrollIntoView({block: 'start', behavior: 'smooth'});
  else $('messages').scrollTo({top: $('messages').scrollHeight, behavior: 'smooth'});
}

$('chatForm').onsubmit = async e => {
  e.preventDefault();
  const q = $('question').value.trim();
  if (!q) return;
  addMessage(q, 'user');
  $('question').value = '';
  addMessage('Searching approved documents...', 'assistant');
  const loading = $('messages').lastElementChild;
  try {
    const r = await api('/api/chat', {method: 'POST', body: JSON.stringify({question: q, conversation_id: conversationId})});
    loading.remove();
    addMessage(r.answer, 'assistant', r.sources);
    if (r.status === 'pending_approval') {        // sensitive request: point the user to the Approvals tab
      const btn = document.createElement('button');
      btn.textContent = 'Review approval request →';
      btn.onclick = () => view('approval');
      $('messages').lastElementChild.append(btn);
    }
  } catch (err) { loading.textContent = err.message; }
};

// Enter sends the question; Shift+Enter adds a new line
$('question').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('chatForm').requestSubmit(); }
});

// After a refresh, show the earlier messages of this conversation again
(async () => {
  try {
    const r = await api(`/api/conversations/${conversationId}/messages`);
    for (const m of r.messages) addMessage(m.content, m.role === 'user' ? 'user' : 'assistant');
  } catch (e) { /* no saved messages yet */ }
})();

// the three example buttons fill the question box
document.querySelectorAll('[data-q]').forEach(b => b.onclick = () => {
  $('question').value = b.dataset.q;
  $('question').focus();
});

// ---- documents ----
async function loadDocs() {
  try {
    const r = await api('/api/documents');
    $('docs').replaceChildren(...r.documents.map(d => {
      const row = document.createElement('div');
      row.textContent = `${d.title} — ${d.status} (v${d.current_version})`;
      return row;
    }));
  } catch (e) { $('docs').textContent = e.message; }
}

// Upload = 3 steps: (1) ask the server for a link, (2) send the file straight to S3, (3) tell the server to index it.
$('uploadForm').onsubmit = async e => {
  e.preventDefault();
  const f = $('file').files[0];
  if (!f) return;
  if (f.size > 10485760) { toast('Maximum file size is 10 MB'); return; }   // same as MAX_UPLOAD_BYTES
  try {
    const r = await api('/api/documents/uploads', {method: 'POST', body: JSON.stringify({filename: f.name})});
    const up = await fetch(r.upload_url, {method: 'PUT', headers: r.headers, body: f});
    if (!up.ok) throw Error('S3 upload failed');
    toast('Uploaded to S3. Processing document now...');
    await api('/api/documents/complete', {method: 'POST', body: JSON.stringify({document_id: r.document_id})});
    toast('Document indexed in Pinecone!');
    loadDocs();
  } catch (err) { toast(err.message); }
};

// ---- metrics ----
async function loadMetrics() {
  try {
    $('metrics').textContent = JSON.stringify(await api('/api/admin/metrics'), null, 2);
    $('guardrailMetrics').textContent = JSON.stringify(await api('/api/admin/guardrails'), null, 2);
  } catch (e) { $('metrics').textContent = e.message; }
}

// ---- approvals (admin) ----
async function loadApprovals() {
  const root = $('approvalList');
  root.textContent = 'Loading approvals...';
  try {
    const response = await api('/api/approvals');
    if (!Array.isArray(response.approvals)) throw Error('Unexpected API response: approvals list missing');
    const approvals = response.approvals;
    const isPending = a => String(a.status).trim().toLowerCase() === 'pending';
    $('approvalSummary').textContent = `${approvals.filter(isPending).length} pending · ${approvals.length} total (most recent 100)`;
    root.replaceChildren();
    if (!approvals.length) {
      root.textContent = 'No approval requests yet. Try asking the chatbot to delete a document.';
      return;
    }
    for (const a of approvals) {
      const card = document.createElement('article');
      card.className = 'approval-card';
      const header = document.createElement('div');
      header.className = 'approval-header';
      const title = document.createElement('strong');
      title.textContent = a.question;
      const badge = document.createElement('span');
      badge.className = 'approval-status ' + a.status;
      badge.textContent = a.status;
      header.append(title, badge);
      card.append(header);
      const meta = document.createElement('p');
      meta.textContent = `Requested by ${a.requester} · ${new Date(a.created_at).toLocaleString()}`;
      card.append(meta);
      if (isPending(a)) {
        const actions = document.createElement('div');
        actions.className = 'approval-actions';
        for (const [label, approve] of [['Approve', true], ['Reject', false]]) {
          const button = document.createElement('button');
          button.textContent = label;
          button.className = approve ? 'approve-button' : 'reject-button';
          button.onclick = async () => {
            const decision = approve ? 'approve' : 'reject';
            if (!confirm(`Are you sure you want to ${decision} this request? This records a decision only and does not execute the requested action.`)) return;
            button.disabled = true;
            try {
              const r = await api(`/api/approvals/${encodeURIComponent(a.id)}/decision`,
                                  {method: 'POST', body: JSON.stringify({approve})});
              toast(`Request ${r.status}. No action executed.`);
              await loadApprovals();
            } catch (e) { toast(e.message); button.disabled = false; }
          };
          actions.append(button);
        }
        card.append(actions);
      } else {
        const info = document.createElement('p');
        info.textContent = `Reviewed by ${a.reviewer || 'unknown'} · ${a.reviewed_at ? new Date(a.reviewed_at).toLocaleString() : ''} · No action executed`;
        card.append(info);
      }
      root.append(card);
    }
  } catch (e) {
    root.textContent = `Unable to load approvals: ${e.message}. Check /api/approvals and your Uvicorn terminal.`;
    console.error('Approvals loading failed:', e);
  }
}
