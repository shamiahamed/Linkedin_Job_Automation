document.addEventListener('DOMContentLoaded', () => {
  const capturedCount = document.getElementById('capturedCount');
  const appliedCount = document.getElementById('appliedCount');
  const phoneCount = document.getElementById('phoneCount');
  const jobDetails = document.getElementById('jobDetails');
  const applyBtn = document.getElementById('applyBtn');
  const captureNowBtn = document.getElementById('captureNowBtn');
  const openDashboardBtn = document.getElementById('openDashboardBtn');
  const viewDetailsBtn = document.getElementById('viewDetailsBtn');
  const statusText = document.getElementById('statusText');
  const settingsBtn = document.getElementById('settingsBtn');

  let latestJob = null;

  function loadStatus() {
    chrome.runtime.sendMessage({ type: 'GET_STATUS' }, (status) => {
      if (status) {
        capturedCount.textContent = status.captured || 0;
        appliedCount.textContent = status.applied || 0;
        phoneCount.textContent = status.phoneOnly || 0;
      }
    });
  }

  function showJob(job) {
    latestJob = job;
    jobDetails.style.display = 'block';
    document.getElementById('jobTitle').textContent = job.title || '-';
    document.getElementById('jobCompany').textContent = job.company || '-';
    document.getElementById('jobLocation').textContent = job.location || '-';
    document.getElementById('jobContact').textContent = job.emails?.length
      ? job.emails.join(', ')
      : (job.phones?.length ? '📞 ' + job.phones.join(', ') : '-');

    const hasEmail = job.emails && job.emails.length > 0;
    applyBtn.style.display = hasEmail ? 'inline-block' : 'none';
    applyBtn.textContent = hasEmail ? '📧 Send Application' : '📞 Submit Phone Job';
  }

  function showToast(msg, isError = false) {
    let toast = document.getElementById('toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'toast';
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.background = isError ? '#dc2626' : '#0f172a';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'JOB_DETECTED') {
      showJob(msg.job);
      loadStatus();
    }
  });

  captureNowBtn.addEventListener('click', () => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      const isLinkedIn = tab && tab.url && /^https:\/\/www\.linkedin\.com\//.test(tab.url);
      if (!isLinkedIn) {
        showToast('⚠️ Open LinkedIn and refresh the feed (F5) first', true);
        return;
      }
      chrome.tabs.sendMessage(tab.id, { type: 'CAPTURE_NOW' }, (response) => {
        if (chrome.runtime.lastError) {
          showToast('⚠️ Refresh the LinkedIn page (F5) to activate the extension', true);
          return;
        }
        if (response && response.success) {
          if (response.job) {
            showJob(response.job);
          }
          loadStatus();
          showToast(`✅ Captured ${response.captured || 1} job(s) from this page`);
        } else {
          showToast('⚠️ ' + (response?.error || 'No job posts found'), true);
        }
      });
    });
  });

  applyBtn.addEventListener('click', () => {
    if (!latestJob) return;
    applyBtn.disabled = true;
    applyBtn.textContent = 'Processing...';

    chrome.runtime.sendMessage({ type: 'APPLY_JOB', jobId: latestJob.backendId }, (res) => {
      applyBtn.disabled = false;
      applyBtn.textContent = '📧 Send Application';
      if (res && res.success) {
        showToast('✅ Application sent!');
        loadStatus();
      } else {
        showToast('❌ Apply failed. Is backend running?', true);
      }
    });
  });

  viewDetailsBtn.addEventListener('click', () => {
    chrome.storage.local.get(['backendUrl'], (d) => {
      const base = (d.backendUrl || 'http://localhost:8000').replace(/\/$/, '');
      chrome.tabs.create({ url: `${base}/dashboard/jobs/${latestJob.backendId || ''}` });
    });
  });

  openDashboardBtn.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'OPEN_DASHBOARD' });
  });

  settingsBtn.addEventListener('click', () => {
    const url = prompt('Backend URL:', 'http://localhost:8000');
    if (url === null) return;
    const token = prompt('API Token (leave blank if local):', '');
    if (token === null) return;
    chrome.storage.local.set({ backendUrl: url.trim() || 'http://localhost:8000', apiToken: (token || '').trim() });
    showToast('Settings saved. Reload the LinkedIn tab to apply.');
  });

  // Load initial state
  chrome.storage.local.get(['latestJob'], (data) => {
    if (data.latestJob) showJob(data.latestJob);
  });

  loadStatus();
});
