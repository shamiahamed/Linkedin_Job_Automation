const background = {
  backendUrl: 'http://localhost:8000',
  apiToken: '',
  openedLinks: new Set(),
  database: {
    captured: [],
    applied: [],
    phoneOnly: []
  },

  init() {
    chrome.storage.local.get(['backendUrl', 'apiToken', 'database'], (data) => {
      if (data.backendUrl) this.backendUrl = data.backendUrl;
      if (data.apiToken) this.apiToken = data.apiToken;
      if (data.database) this.database = { ...this.database, ...data.database };
    });
  },

  saveDatabase() {
    chrome.storage.local.set({ database: this.database });
  },

  async sendToBackend(path, method = 'GET', body = null) {
    try {
      const opts = {
        method,
        headers: { 'Content-Type': 'application/json' }
      };
      if (this.apiToken) opts.headers['X-API-Key'] = this.apiToken;
      if (body) opts.body = JSON.stringify(body);

      const res = await fetch(`${this.backendUrl}${path}`, opts);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Backend error:', err);
      return null;
    }
  },

  async applyToJob(jobId) {
    const result = await this.sendToBackend(`/api/jobs/${jobId}/apply`, 'POST');
    if (result && result.success) {
      if (!this.database.applied.includes(jobId)) {
        this.database.applied.push(jobId);
        this.saveDatabase();
      }
      return { success: true, result };
    }
    return { success: false, result };
  },

  openDashboard() {
    chrome.tabs.create({ url: `${this.backendUrl}/dashboard` });
  }
};

chrome.runtime.onInstalled.addListener(() => background.init());

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case 'JOB_DETECTED':
      if (!background.database.captured.some(j => j.url === msg.job.url)) {
        background.database.captured.push(msg.job);
        background.saveDatabase();
        background.sendToBackend('/api/jobs', 'POST', msg.job);
      }
      sendResponse({ ok: true });
      break;

    case 'JOB_SAVED':
      sendResponse({ ok: true });
      break;

    case 'BACKEND_OFFLINE':
      // Store job locally so it isn't lost
      if (msg.job) {
        const existing = background.database.captured.findIndex(j => j.url === msg.job.url);
        if (existing === -1) {
          background.database.captured.push({ ...msg.job, pendingSync: true });
          background.saveDatabase();
        }
      }
      sendResponse({ ok: true });
      break;

    case 'OPEN_APPLY_LINK':
      if (msg.url && !background.openedLinks.has(msg.key || msg.url)) {
        background.openedLinks.add(msg.key || msg.url);
        chrome.tabs.create({ url: msg.url });
      }
      sendResponse({ ok: true });
      break;

    case 'APPLY_JOB':
      background.applyToJob(msg.jobId).then(sendResponse);
      return true; // async response

    case 'GET_DATABASE':
      sendResponse(background.database);
      break;

    case 'OPEN_DASHBOARD':
      background.openDashboard();
      sendResponse({ ok: true });
      break;

    case 'GET_STATUS':
      sendResponse({
        captured: background.database.captured.length,
        applied: background.database.applied.length,
        phoneOnly: background.database.phoneOnly.length,
        backendOnline: true
      });
      break;

    default:
      sendResponse({ ok: true });
      break;
  }
});

background.init();
