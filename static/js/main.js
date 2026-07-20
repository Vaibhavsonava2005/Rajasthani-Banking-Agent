/* ============================================================
   Rajasthani Voice Pro — main.js
   ============================================================ */
'use strict';

document.addEventListener('DOMContentLoaded', () => {

  /* ── DOM Refs ─────────────────────────────────────────────── */
  const uploadForm    = document.getElementById('uploadForm');
  const fileInput     = document.getElementById('fileInput');
  const dropZone      = document.getElementById('dropZone');
  const dropContent   = document.getElementById('dropZoneContent');
  const uploadBtn     = document.getElementById('uploadBtn');
  const statusMsg     = document.getElementById('statusMsg');
  const resultsSection= document.getElementById('resultsSection');
  const statsSection  = document.getElementById('statsSection');
  const recordsBody   = document.getElementById('recordsBody');
  const callAllBtn    = document.getElementById('callAllBtn');
  const cancelBatchBtn= document.getElementById('cancelBatchBtn');

  /* ── State ────────────────────────────────────────────────── */
  let processedData = [];
  let pollIntervals = {};
  let currentAudio  = null;
  let currentPlayIdx = null;

  /* ── Helpers ──────────────────────────────────────────────── */
  function formatINR(num) {
    if (!num && num !== 0) return '₹0';
    const n = Math.round(Number(num));
    const s = n.toString();
    if (s.length <= 3) return '₹' + s;
    const last3 = s.slice(-3);
    const rest = s.slice(0, -3);
    return '₹' + rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
  }

  function showToast(message, type = 'info') {
    const icons = { success:'fa-circle-check', error:'fa-circle-exclamation', info:'fa-circle-info', warning:'fa-triangle-exclamation' };
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i><span>${message}</span>`;
    document.getElementById('toastContainer').appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateX(100%)'; t.style.transition = '.3s'; setTimeout(() => t.remove(), 350); }, 4000);
  }

  function showLoading(text = 'Processing your file...') {
    const ol = document.getElementById('loadingOverlay');
    ol.querySelector('.spinner-text').textContent = text;
    ol.classList.remove('hidden');
  }
  function hideLoading() { document.getElementById('loadingOverlay').classList.add('hidden'); }

  function showStatus(msg, type) {
    statusMsg.className = `status-msg ${type}`;
    const icon = type === 'success' ? 'fa-circle-check' : 'fa-circle-exclamation';
    statusMsg.innerHTML = `<i class="fa-solid ${icon}"></i> ${msg}`;
    statusMsg.classList.remove('hidden');
  }
  function hideStatus() { statusMsg.classList.add('hidden'); }

  /* ── Drag & Drop ──────────────────────────────────────────── */
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const files = e.dataTransfer.files;
    if (files.length) {
      fileInput.files = files;
      updateDropLabel(files[0].name);
    }
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) updateDropLabel(fileInput.files[0].name);
  });
  function updateDropLabel(name) {
    dropContent.innerHTML = `<i class="fa-solid fa-file-circle-check drop-icon" style="color:var(--success)"></i>
      <span class="drop-primary">${name}</span>
      <span class="drop-secondary">Ready to process</span>`;
  }

  /* ── Upload & Process ─────────────────────────────────────── */
  uploadForm.addEventListener('submit', async e => {
    e.preventDefault();
    if (!fileInput.files.length) { showToast('Please select a file first', 'error'); return; }

    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Processing...';
    hideStatus();
    showLoading();

    try {
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);

      const res = await fetch('/upload', { method: 'POST', body: formData });
      const json = await res.json();

      if (!res.ok) throw new Error(json.error || 'Upload failed');

      processedData = json.data || [];
      renderTable(processedData);
      updateStats(processedData);

      statsSection.classList.remove('hidden');
      resultsSection.classList.remove('hidden');
      showStatus(`✓ ${processedData.length} records processed successfully`, 'success');

      if (json.call_configured) {
        callAllBtn.classList.remove('hidden');
        showToast('Twilio calling is ready!', 'success');
      } else {
        showToast('Twilio not configured — speech only mode', 'warning');
      }

      resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    } catch (err) {
      showStatus(err.message, 'error');
      showToast(err.message, 'error');
    } finally {
      hideLoading();
      uploadBtn.disabled = false;
      uploadBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Process & Generate Speech';
    }
  });

  /* ── Stats ────────────────────────────────────────────────── */
  function updateStats(data) {
    document.getElementById('statRecordsValue').textContent = data.length;
    document.getElementById('statLoanValue').textContent    = formatINR(data.reduce((s, r) => s + Number(r.total_loan   || 0), 0));
    document.getElementById('statPaidValue').textContent    = formatINR(data.reduce((s, r) => s + Number(r.paid_loan    || 0), 0));
    document.getElementById('statBalanceValue').textContent = formatINR(data.reduce((s, r) => s + Number(r.balance_loan || 0), 0));
  }

  /* ── Render Table ─────────────────────────────────────────── */
  function renderTable(data) {
    recordsBody.innerHTML = '';
    data.forEach((rec, idx) => {
      const tr = document.createElement('tr');
      tr.id = `row-${idx}`;
      const callDisabled = !rec.phone_valid ? 'disabled' : '';
      tr.innerHTML = `
        <td>${idx + 1}</td>
        <td class="td-name">${escHtml(rec.name)}</td>
        <td class="td-phone">${rec.phone_number || '<span style="color:var(--danger)">invalid</span>'}</td>
        <td>${escHtml(rec.bank_name)}</td>
        <td>${formatINR(rec.emi_amount)}</td>
        <td>${escHtml(rec.due_date)}</td>
        <td>
          <span class="badge ${rec.call_type === 'recovery' ? 'badge-failed' : 'badge-completed'}">
            ${rec.call_type === 'recovery' ? 'Recovery' : 'Reminder'}
          </span>
        </td>
        <td>${formatINR(rec.total_loan)}</td>
        <td>${formatINR(rec.paid_loan)}</td>
        <td>${formatINR(rec.balance_loan)}</td>
        <td><span class="badge badge-idle" id="badge-${idx}">idle</span></td>
        <td>
          <div class="action-btns">
            <button class="icon-btn" id="playBtn-${idx}" onclick="playAudio(${idx})" title="Play Speech">
              <i class="fa-solid fa-play" id="playIcon-${idx}"></i>
            </button>
            <button class="icon-btn btn-call" id="callBtn-${idx}" onclick="callUser(${idx})" title="Call User" ${callDisabled}>
              <i class="fa-solid fa-phone"></i>
            </button>
          </div>
        </td>`;
      recordsBody.appendChild(tr);
    });
  }

  function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  /* ── Play Audio (Sarvam AI API) ───────────────────────────── */
  window.playAudio = async (idx) => {
    const btn  = document.getElementById(`playBtn-${idx}`);
    const icon = document.getElementById(`playIcon-${idx}`);
    const rec  = processedData[idx];

    // If already playing this record, pause
    if (currentAudio && currentPlayIdx === idx) {
      if (!currentAudio.paused) {
        currentAudio.pause();
        icon.className = 'fa-solid fa-play';
        return;
      } else {
        currentAudio.play();
        icon.className = 'fa-solid fa-pause';
        return;
      }
    }

    // Pause any existing audio
    if (currentAudio) {
      currentAudio.pause();
      if (currentPlayIdx !== null) {
        const prevIcon = document.getElementById(`playIcon-${currentPlayIdx}`);
        if (prevIcon) prevIcon.className = 'fa-solid fa-play';
      }
    }
    
    if (!rec || !rec.rajasthani_text) {
      showToast('No text available to play', 'error');
      return;
    }

    btn.disabled = true;
    icon.className = 'fa-solid fa-spinner fa-spin';

    try {
      // Base64 encode the text for the URL
      // Use standard btoa with utf-8 encoding trick, then convert to urlsafe
      const standard_b64 = btoa(unescape(encodeURIComponent(rec.rajasthani_text)));
      const urlsafe_b64 = standard_b64.replace(/\+/g, '-').replace(/\//g, '_');
      
      const res = await fetch(`/audio?b64=${encodeURIComponent(urlsafe_b64)}`);
      if (!res.ok) {
          const json = await res.json();
          throw new Error(json.error || 'Audio generation failed');
      }
      
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);

      currentAudio    = new Audio(url);
      currentPlayIdx  = idx;

      currentAudio.onended = () => {
        icon.className = 'fa-solid fa-play';
        btn.disabled   = false;
        currentAudio   = null;
        currentPlayIdx = null;
      };
      currentAudio.onerror = () => {
        showToast('Audio playback error', 'error');
        icon.className = 'fa-solid fa-play';
        btn.disabled   = false;
      };

      await currentAudio.play();
      icon.className = 'fa-solid fa-pause';
      btn.disabled   = false;

    } catch (err) {
      showToast(err.message, 'error');
      icon.className = 'fa-solid fa-play';
      btn.disabled   = false;
    }
  };

  /* ── Call User ────────────────────────────────────────────── */
  window.callUser = async (idx) => {
    const btn   = document.getElementById(`callBtn-${idx}`);
    const badge = document.getElementById(`badge-${idx}`);
    btn.disabled = true;
    
    // Deep AI Logic: Pre-warm Vercel cache to eliminate the silence delay when user answers!
    setBadge(badge, 'generating');
    const rec = processedData[idx];
    try {
        const standard_b64 = btoa(unescape(encodeURIComponent(rec.rajasthani_text)));
        const urlsafe_b64 = standard_b64.replace(/\+/g, '-').replace(/\//g, '_');
        await fetch(`/audio?b64=${encodeURIComponent(urlsafe_b64)}`);
    } catch (e) {
        console.warn("Pre-fetch failed, falling back to dynamic");
    }

    setBadge(badge, 'initiated');

    try {
      const res = await fetch(`/call`, { 
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone_number: rec.phone_number,
          rajasthani_text: rec.rajasthani_text
        })
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || 'Call failed');

      // Store the Twilio Call SID for status polling
      processedData[idx].call_sid = json.call_sid;
      showToast(`📞 Calling ${rec.name}...`, 'info');
      startPolling(idx);
    } catch (err) {
      setBadge(badge, 'failed');
      showToast(err.message, 'error');
      btn.disabled = false;
    }
  };

  /* ── Polling ──────────────────────────────────────────────── */
  const TERMINAL = new Set(['completed', 'failed', 'busy', 'no-answer', 'canceled']);

  function startPolling(idx) {
    if (pollIntervals[idx]) clearInterval(pollIntervals[idx]);
    
    const sid = processedData[idx].call_sid;
    if (!sid) return;

    pollIntervals[idx] = setInterval(async () => {
      try {
        const res  = await fetch(`/call-status?sid=${sid}`);
        if (!res.ok) return;
        const data = await res.json();
        
        const badge = document.getElementById(`badge-${idx}`);
        if (badge) setBadge(badge, data.status);
        
        if (TERMINAL.has(data.status)) {
          clearInterval(pollIntervals[idx]);
          delete pollIntervals[idx];
          
          const btn = document.getElementById(`callBtn-${idx}`);
          if (btn) btn.disabled = false;
          
          if (data.status === 'completed') {
            showToast(`✅ Call to ${processedData[idx]?.name} answered and completed!`, 'success');
          } else if (data.status === 'busy' || data.status === 'no-answer') {
            showToast(`⚠️ User cut the call or did not attend (${data.status})`, 'warning');
          } else {
            showToast(`Call ${data.status}: ${processedData[idx]?.name}`, 'warning');
          }
          
          // Store status in data for reliable batch resuming
          if (processedData[idx]) processedData[idx].status = data.status;
        }
      } catch { /* silently ignore poll errors */ }
    }, 2000);
  }

  function setBadge(el, status) {
    el.className = `badge badge-${status}`;
    // Map Twilio raw statuses to beautiful UI text
    const displayNames = {
      'queued': 'Queued',
      'initiated': 'Initiated',
      'ringing': 'Ringing...',
      'in-progress': 'In Progress 🔊',
      'completed': 'Completed ✓',
      'busy': 'Busy (Cut)',
      'no-answer': 'No Answer',
      'canceled': 'Canceled',
      'failed': 'Failed ❌',
      'generating': 'Generating Audio ⚡'
    };
    el.textContent = displayNames[status] || status;
  }

  /* ── Call All (Frontend Batch Loop) ───────────────────────── */
  let cancelBatch = false;
  
  callAllBtn.addEventListener('click', async () => {
    callAllBtn.disabled = true;
    cancelBatchBtn.classList.remove('hidden');
    cancelBatch = false;
    showToast('Starting batch calls...', 'info');
    
    let queued = 0;
    for (let i = 0; i < processedData.length; i++) {
      if (cancelBatch) {
        showToast('Batch cancelled by user', 'warning');
        break;
      }
      
      const rec = processedData[i];
      if (!rec.phone_valid) continue;
      
      // Skip if already dialed and in a terminal state, or currently in progress
      if (rec.status && TERMINAL.has(rec.status)) {
        continue;
      }
      if (rec.call_sid && !rec.status) {
        // Active call in progress, skip dialing again
        continue;
      }
      
      queued++;
      await window.callUser(i);
      
      // Wait 3 seconds between calls to avoid rate limits
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
    
    showToast(`📞 Batch finished — ${queued} calls processed`, 'success');
    cancelBatchBtn.classList.add('hidden');
    callAllBtn.disabled = false;
  });

  cancelBatchBtn.addEventListener('click', () => {
    cancelBatch = true;
    showToast('Cancelling remaining calls...', 'warning');
    cancelBatchBtn.classList.add('hidden');
    callAllBtn.disabled = false;
  });

  /* ── Config Check on Load ─────────────────────────────────── */
  (async () => {
    try {
      const res  = await fetch('/call-config-status');
      const json = await res.json();
      if (!json.configured) {
        showToast('⚠️ Twilio not configured — calling disabled', 'warning');
      }
    } catch { /* ignore */ }
  })();

});
