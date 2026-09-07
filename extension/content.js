const JobDetector = {
  backendUrl: 'http://localhost:8000',
  detectedJobs: new Set(),
  watchInterval: null,
  _ocrCache: {},
  _ocrInFlight: new Set(),
  _ocrDone: new Set(),
  // Apply links matched in a post are extracted (workday/lnkd.in/careers pages).
  // When a post has no email/phone but has an apply link, the link is opened
  // automatically so the job is never a dead "no_contact" capture.

  init() {
    this.loadBackendUrl();
    this.startWatching();
    this.injectFloatingUI();
    window.addEventListener('scroll', () => this.debouncedCheck(), { passive: true });
    window.addEventListener('error', (e) => this.log('page-error', { message: e.message, file: e.filename, line: e.lineno }));
    window.addEventListener('unhandledrejection', (e) => this.log('unhandledrejection', { message: String(e.reason || '') }));
  },

  log(msg, data) {
    const entry = { msg, data: data || {} };
    this._lastLogAt = Date.now();
    try {
      const h = { 'Content-Type': 'application/json' };
      if (this.apiToken) h['X-API-Key'] = this.apiToken;
      fetch(`${this.backendUrl}/api/debug/log`, {
        method: 'POST',
        headers: h,
        body: JSON.stringify({ msg, data: entry.data, page: window.location.href })
      }).catch(() => {});
    } catch (e) {}
    try {
      chrome.storage.local.get(['debugLogs'], (d) => {
        const arr = (d.debugLogs || []).slice(-300);
        arr.push({ ts: new Date().toISOString(), msg, data: entry.data });
        chrome.storage.local.set({ debugLogs: arr });
      });
    } catch (e) {}
    console.info('[job-automation]', msg, data || '');
  },

  loadBackendUrl() {
    chrome.storage.local.get(['backendUrl', 'apiToken'], (data) => {
      if (data.backendUrl) this.backendUrl = data.backendUrl;
      if (data.apiToken) this.apiToken = data.apiToken;
    });
  },

  debouncedCheck() {
    clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => this.checkForJobs(), 800);
  },

  startWatching() {
    this.checkForJobs();
    this.watchInterval = setInterval(() => this.checkForJobs(), 3000);
  },

  isOnJobsPage() {
    return window.location.pathname.includes('/jobs') ||
           window.location.pathname.includes('/feed');
  },

  logDomSnapshot(cards) {
    const seen = new Set();
    const main = document.querySelector('main') || document.body;
    const interesting = [];
    for (const el of main.querySelectorAll('[class]')) {
      const cls = (typeof el.className === 'string' ? el.className : el.getAttribute('class') || '').toString();
      if (interesting.length > 300) break;
      for (const c of cls.split(/\s+/)) {
        if (c && !seen.has(c) && /^(feed|update|occludable|shared|actor|showcase|home-|feed-entry|entity|artdeco|containers)/i.test(c)) {
          seen.add(c);
          interesting.push(c);
        }
      }
    }
    const ariaLabels = new Set();
    document.querySelectorAll('[aria-label]').forEach(el => {
      const al = el.getAttribute('aria-label');
      if (al && al.length < 60) ariaLabels.add(al);
    });
    this.log('dom-snapshot', {
      mainClass: (main.className || '').toString().slice(0, 120),
      classes: interesting,
      likes: document.querySelectorAll('button[aria-label*="Like"]').length,
      ariaLabels: [...ariaLabels].slice(0, 40),
      samples: (cards || []).slice(0, 3).map(c => ({
        cls: (typeof c.className === 'string' ? c.className : (c.getAttribute('class') || '')).toString().slice(0, 120),
        text: (c.innerText || '').replace(/\s+/g, ' ').slice(0, 180)
      }))
    });
  },

  getFeedJobCards() {
    // Class-based attempts first (jobs search page + any legacy feed classes)
    const classSelectors = [
      '.feed-shared-update-v2',
      '.occludable-update',
      '.feed-entry',
      '.feed__item',
      '.job-card-container',
      '.jobs-search-results__list-item',
      '.job-card-wrapper',
      '[data-control-name="job_card"]'
    ];
    for (const sel of classSelectors) {
      const found = document.querySelectorAll(sel);
      if (found.length) return [...found];
    }

    // Class-agnostic fallback (LinkedIn uses hashed CSS classes now).
    // Every post exposes a "Hide post by <Author>" control menu button.
    const posts = [];
    const seen = new Set();
    const markers = document.querySelectorAll(
      '[aria-label^="Hide post by "], [aria-label*="control menu for post by "]'
    );
    for (const m of markers) {
      let el = m;
      for (let i = 0; i < 12 && el && el !== document.body; i++) {
        el = el.parentElement;
        if (!el) continue;
        const hasReaction = !!el.querySelector('[aria-label*="Reaction button state"], [aria-label*="Open reactions menu"]');
        const tt = (el.innerText || '').trim();
        if (hasReaction && tt.length > 40 && !seen.has(el)) {
          seen.add(el);
          posts.push(el);
          this._feedSelector = 'hide-by-marker';
        }
        if (hasReaction) break;
      }
    }
    return posts;
  },

  getJobDetailPage() {
    if (!window.location.pathname.includes('/jobs/view/')) return null;
    return {
      url: window.location.href,
      title: document.querySelector('.job-details-jobs-unified-top-card__job-title, h1.t-24, [class*="job-title"]')?.innerText?.trim() || '',
      company: document.querySelector('.job-details-jobs-unified-top-card__company-name, [class*="company-name"]')?.innerText?.trim() || '',
      location: document.querySelector('.job-details-jobs-unified-top-card__tertiary-description-container, [class*="tertiary-description"]')?.innerText?.trim() || '',
      description: document.querySelector('.jobs-description__content, [class*="job-details-content"]')?.innerText?.trim() || ''
    };
  },

  extractEmail(text) {
    const emailRegex = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
    return text.match(emailRegex) || [];
  },

  extractPhone(text) {
    const phoneRegex = /(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/g;
    return text.match(phoneRegex) || [];
  },

  extractExperience(text) {
    const num = (text || '').match(/(\d+(?:\s*[-+–—]\s*\d+)?\s*\+?\s*)(?:years?|yrs?)/i);
    const numS = num ? num[0].trim() : '';
    const fresher = /\b(freshers?|entry[- ]?level|0 experience|no experience|recent graduates?|graduate trainee|passouts?)\b/i.test(text || '');
    if (fresher) return numS ? `Fresher (${numS})` : 'Fresher';
    return numS;
  },

  // Returns the maximum experience implied by the text, in months, or null
  // when there is no explicit mention. Fresher/entry-level posts count as 12.
  experienceMonths(text) {
    const t = ' ' + (text || '').toLowerCase().replace(/\s+/g, ' ') + ' ';
    let months = null;
    if (/\b(freshers?|entry[- ]?level|0 experience|no experience|recent graduates?|graduate trainee|passouts?)\b/i.test(t)) {
      months = Math.min(months ?? 12, 12);
    }
    const re = /\b(\d{1,2})(?:\s*(?:[-–—to+]\s*)(\d{1,2}))?\s*(?:plus|\+)?\s*(?:years?|yrs?)\b/g;
    let m;
    while ((m = re.exec(t)) !== null) {
      const a = parseInt(m[1], 10);
      const b = m[2] ? parseInt(m[2], 10) : a;
      const monthsU = Math.max(a, b) * 12;
      months = months === null ? monthsU : Math.max(months, monthsU);
    }
    return months;
  },

  // Pull the job's apply link out of the post text. Prefers a real careers/ATS
  // page (Workday, Taleo, Greenhouse, Lever, XYZ careers/jobs), then LinkedIn's
  // lnkd.in short link, then any other http link. Returns '' when absent.
  extractApplyLink(text) {
    const t = (text || '').trim();
    const found = [];
    for (const m of t.matchAll(/https?:\/\/[^\s<>"')\]]+/gi)) {
      const u = m[0].replace(/[.,;:!?)\]]+$/, '');
      if (u) found.push(u);
    }
    for (const b of (t.match(/\b(?:wd\d+\.myworkdaysite\.com|myworkdayjobs|workdayjobs|careers?\.[a-z0-9-]+\.[a-z]{2,}|jobs\.[a-z0-9-]+\.[a-z]{2,}|lnkd\.in\/[-\w]+)\b/gi) || [])) {
      found.push(/^https?:\/\//i.test(b) ? b : 'https://' + b);
    }
    // Phrase-scoped bare link: "Apply at <url>" / "career page <url>" / "share
    // your resume at <url>" — keeps real links even without an "https://" prefix.
    const bare = t.match(/(?:\bapply\s*(?:now)?\s*(?:at|here|on|via|using|link|through)?|\bcareer(?:s)?\s+(?:page|link)|\boff[- ]?campus\s+(?:drive|hiring)|\bapply\s+link|share\s+(?:your\s+)?(?:resume|cv|profile)\s+(?:to|at))\s*[:)\-]?\s*[^.,;!?\n]{0,70}?([a-z0-9][\w.-]+\.(?:com|in|co|co\.in|org|net|io|tech|it|jobs?|careers?)\b(?:\/[\w.\-/]*(?![.,;!?)\]]))?)/i);
    if (bare && bare[1]) {
      const u = /^https?:\/\//i.test(bare[1]) ? bare[1] : 'https://' + bare[1];
      if (!found.includes(u)) found.push(u);
    }
    if (!found.length) return '';
    // Keep only URLs with a real domain (llama-fake links like
    // "https://qualcomm-off-campus-drive-2026" have no dot-TLD and are useless).
    const real = found.filter(u =>
      /^https?:\/\/(?:[\w-]+\.)+[a-z]{2,}(?:\/|$)/i.test(u) || /lnkd\.in\/\w+/i.test(u)
    );
    const pool = real.length ? real : found;
    const hasPath = u => /^https?:\/\/(?:[\w-]+\.)+[a-z]{2,}\/\S+/i.test(u);
    for (const u of pool) {
      if (hasPath(u) && (/workday|taleo|smartrecruiters|greenhouse|lever\.co|bamboohr|icims|successfactors|dayforce|capsulehrm|simplify/i.test(u) || /careers?\.|jobs\.|\/careers?\//i.test(u))) {
        return u;
      }
    }
    const lnkd = pool.find(u => /lnkd\.in\/\w+/i.test(u));
    if (lnkd) return lnkd;
    return pool[0];
  },

  cleanText(text) {
    return text.replace(/\s+/g, ' ').trim();
  },

  // Find the job-flyer image inside a feed card. Picks the largest real photo
  // (excludes avatars, article thumbnails, company logos). Returns a
  // media.licdn.com https URL the backend can OCR, or '' when none.
  ocrFeedImage(card) {
    const imgs = [...card.querySelectorAll('img[src*="/dms/image/"]')].filter(img =>
      !img.closest('.feed-shared-actor, .update-components-actor, [class*="actor__avatar"], .update-components-article, [class*="article"], [class*="company-logo"], .job-card-container')
    );
    if (!imgs.length) return '';
    let best = imgs[0];
    for (const img of imgs) {
      const a = (img.naturalWidth || 0) * (img.getBoundingClientRect().width || 0);
      const b = (best.naturalWidth || 0) * (best.getBoundingClientRect().width || 0);
      if (a > b) best = img;
    }
    const src = best.currentSrc || best.src || '';
    if (!src) return '';
    let clean = (src.startsWith('//') ? 'https:' + src : src).replace(/&width=\d+/i, '&width=1400');
    if (!/^https:\/\//i.test(clean)) return '';
    return clean;
  },

  async scheduleOcr(src) {
    try {
      const h = { 'Content-Type': 'application/json' };
      if (this.apiToken) h['X-API-Key'] = this.apiToken;
      const res = await fetch(`${this.backendUrl}/api/ocr/from-image`, {
        method: 'POST',
        headers: h,
        body: JSON.stringify({ url: src })
      });
      const data = await res.json();
      this._ocrCache[src] = data && data.text ? data.text.trim() : '';
    } catch (e) {
      this._ocrCache[src] = '';
    }
    this._ocrDone.add(src);
    this._ocrInFlight.delete(src);
    this.log('ocr', { src: src.slice(0, 70), chars: (this._ocrCache[src] || '').length });
  },

  hasRoleWord(text) {
    return /\b(developer|engineer|analyst|architect|specialist|tester|testing|designer|manager|officer|consultant|lead|executive|intern|trainee|fresher|scientist|trainer|sales)\w*\b/i.test(text);
  },

  hasContactInfo(text) {
    return /(?:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|\+\d{9,}|\b\d{10}\b)/.test(text);
  },

  isJunkCompany(cand) {
    if (!cand || typeof cand !== 'string') return true;
    if (!/[A-Za-z]/.test(cand) || cand.length < 3) return true;
    if (/\b(developer|engineers?|analysts?|managers?|officers?|architects?|specialists?|executives?|consultants?|designers?|technicians?|testers?|trainees?|interns?|professionals?|experts?|trainers?|recruiters?|freshers?|candidates?|roles?|positions?|openings?|vacancies?|resumes?|experiences?|experienced|qualified|joiners?|members?|talents?|talent|people)\b/i.test(cand)) return true;
    if (/\b(you'?ll|you will|we'?re?|we are|your|our|get|you get|here's?|here is|apply now|click (?:here|below)|see|read|check|message|dm?s|now|today|ft|fte|ot|looking|job)\b/i.test(cand)) return true;
    return false;
  },

  // Strip the "Feed post <Actor> <time> Follow <body>" header block so recruiter
  // headlines (e.g. "CEO & Founder @X") don't leak into the extracted title.
  feedTitleText(text) {
    let t = text
      .replace(/^Feed post\s*/i, '')
      .replace(/\s*(?:\u2026|\.\.\.)\s*[Mm]ore\b.*$/s, '');
    for (let i = 0; i < 2; i++) {
      t = t.replace(/^.{0,260}?\b\d+[smhdw] ?(?:Follow(?:ing)?\s*)?/s, '');
    }
    t = t.replace(/^.{0,260}?\bFollow(?:ing)?\s*/i, '');
    return t.trim();
  },

  checkForJobs() {
    if (document.visibilityState !== 'visible') return;
    if (!this.isOnJobsPage()) return;

    if (window.location.pathname.includes('/jobs/view/')) {
      const job = this.getJobDetailPage();
      if (job.title && !this.detectedJobs.has(job.url)) {
        this.processJob(job);
      }
      return;
    }

    const cards = this.getFeedJobCards();
    // Log the scan only once per 60s, not every 3s
    if (Date.now() - (this._lastCheckLog || 0) > 60000) {
      this._lastCheckLog = Date.now();
      this.log('check', { feedPosts: document.querySelectorAll('.feed-shared-update-v2').length, cards: cards.length, feedSelector: this._feedSelector || null });
    }
    if (!this._snapped) {
      this._snapped = true;
      this.logDomSnapshot(cards);
    }
    for (const card of cards) {
      const job = this.parseFeedCard(card);
      if (job && job.title) {
        const jobKey = `${job.title}-${job.company}`;
        if (!this.detectedJobs.has(jobKey)) {
          this.log('job-found', { title: job.title, company: job.company, location: job.location, emails: job.emails, phones: job.phones, text: (card.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160) });
          this.autoCapture(job);
          this.log('captured', { key: jobKey });
        }
      } else {
        const txt = (card.innerText || '').replace(/\s+/g, ' ').trim();
        const sample = txt.slice(0, 60);
        if (sample && !this._skippedPosts) this._skippedPosts = new Set();
        if (sample && !this._skippedPosts.has(sample)) {
          this._skippedPosts.add(sample);
          if (this._skippedPosts.size > 100) this._skippedPosts.clear();
          this.log('post-skip', { empty: txt.length === 0, len: txt.length, sample: txt.slice(0, 90) });
        }
      }
    }
  },

  isJobPost(text) {
    // Reject non-opening posts: life updates, celebrations, onboarding announcements
    const negativeSignals = [
      /started a new position/i,
      /\bjob update\b/i,
      /congratulations/i,
      /celebrat/i,
      /\bwelcome(?:d)?s?\s+[A-Z][a-z]+ [A-Z]/i,
      /\bwelcome(?:d)?\s+(?:to|join) (?:our|the) team\b/i,
      /joins?\s+(?:our|the)\s+team\s+as\b/i,
      /\b(?:anniversary|birthday)\b/i,
      /graduated from/i,
      /\bnew (?:job|role|position)\s*(?:as|at|with)\b/i,
      /\b(?:moved?|transitioned) to a new/i,
      /promoted to/i,
      /hired (?:as|on)\b/i,
      // Training/demo/coaching ads, unpaid internship promos, interview-experience shares, headhunter ads
      /\b(?:free|online|virtual|remote|unpaid)\s+(?:internship|intern)\b/i,
      /\bregistration\s+(?:open|closes?|deadline|starts?|ends?)\b/i,
      /\b(?:demo|free demo|workshop)\s+session\b/i,
      /\bplacement\s+support\b/i,
      /\binterview\s+experience\b/i,
      /\bappeared\s+(?:for|in)\b.{0,50}?\binterview\b/i,
      /\bheadhunters?\b/i,
      /\bnext steps\b/i,
    ];
    if (negativeSignals.some(re => re.test(text))) return false;

    // Clear hiring words
    const hardSignals = [
      /we('| a)?re hiring/i,
      /\bhiring\b/i,
      /\bjob (opening|opportunity)\b/i,
      /\bvacanc/i,
      /\bopening for\b/i,
      /\bpositions?\b/i,
      /\bapply (now|to|today)\b/i,
      /\brecruit(ing|ment)\b/i,
      /\blooking for\b/i,
      /\burgent hiring\b/i,
      /\bcareer opportunity\b/i,
      /\bwalk[- ]?in\b/i,
      /\b#hiring\b/i,
      /\b#job(s|opening)?\b/i,
      /\bto apply\b/i,
      /\b(cv|resume)s?\b/i,
      /\bsend (your |me )?(cv|resume|profile)\b/i,
      /\bcurrent opening\b/i,
      /\bno[ .]?of[ .]?openings?\b/i,
      /\bopenings?\b/i,
      /\bcontact person\b/i,
    ];
    if (hardSignals.some(re => re.test(text))) {
      // A hard signal like "hiring" inside an unrelated quote shouldn't qualify alone
      return true;
    }

    // Soft rule 1: contact info + job terms (salary/opening/apply/etc.)
    const hasContact = /(?:\+\d{9,}|\d{10}|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|contact\s*(?:person|number)?)/i.test(text);
    const jobIndicators = [
      /\b(salary|stipend|package|ctc|remuneration|pay|payout)\b/i,
      /\b(work from home|shift|location|qualification|department|eligibility|benefits)\b/i,
      /\bopen(?:ing|ings)?\b/i,
      /\bvacanc/i,
      /\bapply\b/i,
    ];
    if (hasContact && jobIndicators.some(re => re.test(text))) return true;

    // Soft rule 2: recruiter's email + a role word → real job posting
    const hasEmail = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/.test(text);
    const hasRoleWord = /\b(developer|engineer|analyst|architect|specialist|tester|testing|designer|manager|officer|consultant|lead|executive|intern|trainee|fresher)\w*\b/i.test(text);
    if (hasEmail && hasRoleWord) return true;

    return false;
  },

  cleanTitleCandidate(c) {
    let clean = c
      .replace(/[\u2018\u2019\u2032]/g, "'")
      .split(/[|🔹◆•●✅🎓💼📌⚡📍]|(?:eligibility|experience|salary)\s*:/i)[0]
      .replace(/\([^)]*\)/g, ' ')
      .replace(/\s*\([^)]*$/, '')
      .replace(/[–—]/g, '-')
      .replace(/\s+at\s+[A-Z][A-Za-z0-9&.,'+-]*(?:\s+[A-Z][A-Za-z0-9&.,'+-]*)?\b/i, ' ')
      .replace(/\s+we(?:'| a)?re\b.*$/i, '')
      .replace(/\s+(?:with|for|from|as|and|in|looking|hiring|urgently|required|needed|we(?:' ?re)?)\b.*$/i, '')
      .replace(/[\s-]+$/, '')
      .trim();
    // Capitalize known tech words and first letter
    clean = clean
      .replace(/\breact\b/gi, 'React')
      .replace(/\bnode(?:\.js)?\b/gi, 'Node.js')
      .replace(/\bangular\b/gi, 'Angular')
      .replace(/\bpython\b/gi, 'Python')
      .replace(/\bjava\b/gi, 'Java')
      .replace(/\bmachine learning\b/gi, 'Machine Learning')
      .replace(/\bdevops\b/gi, 'DevOps')
      .replace(/\bfull stack\b/gi, 'Full Stack')
      .replace(/\bfront[- ]?end\b/gi, 'Frontend')
      .replace(/\bback[- ]?end\b/gi, 'Backend')
      .replace(/\bqa\b/gi, 'QA')
      .replace(/\bui\b/gi, 'UI')
      .replace(/\bux\b/gi, 'UX')
      .replace(/\bml\b/gi, 'ML');
    return clean.charAt(0).toUpperCase() + clean.slice(1);
  },

  normalizeCompanyCand(cand) {
    const stop = /\b(we're|we|our|the|a|an|this|apply|now|location|since|candidate|joins?|with|me|looking|hiring|is|are|at|from|for|to|of|in|by|and|or|greetings|dear|regards|follow(?:s|ing)?)\b/i;
    const legal = /\b(?:pvt\.?|private|limited|ltd\.?|llp|llc|inc\.?)\b/i;
    let words = cand.split(/\s+/).filter(Boolean);
    while (words.length && (legal.test(words[words.length - 1]) || stop.test(words[words.length - 1]))) words.pop();
    while (words.length && (stop.test(words[0]) || /^(that|with|when|those|these|since|join)\b/i.test(words[0]))) words.shift();
    const seen = new Set();
    const out = [];
    for (const wd of words) {
      if (seen.has(wd)) continue;
      seen.add(wd);
      out.push(wd);
    }
    const res = out.join(' ').trim();
    return this.isJunkCompany(res) ? '' : res;
  },

  extractCompany(text, title) {
    // 1. Explicit company/org field
    let m = text.match(/\b(?:company(?: name)?|organisation|organization)\s*[:|-]\s*([A-Z][A-Za-z0-9&.,'+-]{0,60})/i);
    if (m) return m[1].trim();

    // 2. Company named before "is/are hiring" (most specific: "X is hiring <role>")
    m = text.match(/\b([A-Z][A-Za-z0-9&.,'+-]{0,35}(?:\s+[A-Z][A-Za-z0-9&.,'+-]{0,35}){0,1})\s+(?:is|are)\s+(?:hiring|looking|recruiting)\b/i);
    if (m) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 2b. Company after "Founder/CEO @ <Startup>" header
    m = text.match(/\b(?:founder|co[- ]?founder|ceo|cto)\b[^|#\n]{0,60}?(?:of|@)\s*([A-Z][A-Za-z0-9._-]{1,30})/i);
    if (m) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 2b1. Page-share posts: "<Person> follows this page <Company>" -> the page name.
    m = text.match(/\bfollows?\s+this\s+page\s+([A-Z][A-Za-z0-9&.,'+-]{0,40}(?:\s+[A-Z][A-Za-z0-9&.,'+-]{0,30}){0,3})/i);
    if (m) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 2c. Company-page actor header ("Feed post <Company> Visit website 9h We're #hiring…").
    // The bullet (•) block stops person-posters (headlines w/ "• 2nd+ …") from leaking.
    m = text.match(/^Feed post\s+([^\u2022\n]{0,120}?)\s+(?:visit website|promoted|\d{1,3}(?:,\d{3})*\s*followers?|\d+[smhdw]|follow(?:ing)?)\b/i);
    if (m) {
      const actor = m[1].trim();
      if (!/\b(?:commented|reposted|activity|likes? this|shared|posted|works? at)\b/i.test(actor)) {
        const cand = this.normalizeCompanyCand(actor);
        if (cand) return cand;
      }
    }

    // 3. Company named in the post after "at"/"@", ending in Pvt/Ltd/LLP/Inc (allows interior hyphens/lowercase)
    m = text.match(/(?:at|@)\s+([A-Z][\w&.,'+ \-]{1,60}?)\s+(?:Pvt\.?\s*(?:Ltd\.?|Limited)|Private\s+Limited|Private|LLP|LLC|Inc\.?|Limited)\b/i);
    if (m) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 4. Role title/phase followed by "at <Company>" / "from <Company>" / "| <Company>"
    let rolePhrase = title || this.findRolePhrase(text);
    if (rolePhrase) {
      let idx = text.toLowerCase().indexOf(rolePhrase.toLowerCase());
      if (idx < 0 && title) idx = text.toLowerCase().indexOf(title.toLowerCase().split(' ')[0]);
      if (idx >= 0) {
        const after = text.slice(idx + rolePhrase.length, idx + rolePhrase.length + 160);
        m = after.match(/(?:at|@|from|\|\s*)\s*([A-Z][A-Za-z0-9&.,'+-]{0,35}(?:[-–—][A-Za-z][A-Za-z0-9&.,'+-]*|\s+[A-Z][A-Za-z0-9&.,'+-]{0,35}){0,4})/);
        if (m) {
          const cand = this.normalizeCompanyCand(m[1]);
          const firstTok = m[1].replace(/\|/g, ' ').trim().split(/\s+/)[0];
          if (cand && !/^(job|roles?|openings?|vacancies?)$/i.test(firstTok) && after.slice(m.index + m[0].length, m.index + m[0].length + 1) !== '@') return cand;
        }
      }
    }

    // 5. Strict Pvt/Ltd variant (each token capitalized, max 4 tokens)
    m = text.match(/\b([A-Z][A-Za-z0-9&.,'+-]{0,35}(?:\s+[A-Z][A-Za-z0-9&.,'+-]{0,35}){0,3})\s+(?:Pvt(?:\.| Ltd\.?)?|Private\s+Limited|Private|LLP|LLC|Inc[.]?|Limited?)\.?\b/i);
    if (m) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 6. Company after "opening/vacancy at/for"
    m = text.match(/\b(?:opening|vacancy|current opening|job opening|urgent walk[- ]?in)\b.{0,25}?(?:at|@|for)\s*([A-Z][A-Za-z0-9&.,'+-]{0,35}(?:\s+[A-Z][A-Za-z0-9&.,'+-]{0,35}){0,2})/i);
    if (m && !/\b(developers?|engineers?|analysts?|architects?|specialists?|executives?|managers?|consultants?|designers?|trainees?|interns?|candidate)\b/i.test(m[1])) {
      const cand = this.normalizeCompanyCand(m[1]);
      if (cand) return cand;
    }

    // 7. Email domain name
    m = text.match(/[\w.+-]+@([A-Za-z0-9-]+)(?:\.[A-Za-z]{2,})/);
    if (m) {
      const domain = m[1];
      return domain === 'gmail' || domain === 'yahoo' || domain === 'hotmail' || domain === 'outlook' ? '' : domain;
    }
    return '';
  },

  extractLocation(text) {
    // 1. Labeled: "Location: Hyderabad – 5 Days WFO", "Place: Coimbatore", "Work Location: Chennai"
    const m = text.match(/\b(?:work[ -]?locations?|locations?|places?|city|cities|based\s+in|office\s+in)\b\s*[:|-]?\s*([A-Z][A-Za-z .&,'-]{1,30})/i);
    if (m) {
const loc = m[1]
      .replace(/\s*[–—-].*$/, '')
      .replace(/\s*\(.*$/, '')
      .replace(/\s+(?:http|https|www|on[- ]?site|\d+).*$/i, '')
      .replace(/\s*(?:wfo|onsite|remote|hybrid)\s*$/i, '')
      .trim();
      // "Location: Remote Skills: ..." -> only keep a known place word when it is first.
      const tok = loc.split(/\s+/);
      if (tok.length > 1 && /\b(remote|wfh|hyderabad|bengaluru|bangalore|chennai|mumbai|pune|delhi|noida|gurugram|gurgaon|kolkata|ahmedabad|indore|nagpur|jaipur|kochi|coimbatore|trivandrum|thiruvananthapuram|sydney|dubai|singapore|bangalore|kochi|london|new york)\b/i.test(tok[0])) {
        return tok[0].charAt(0).toUpperCase() + tok[0].slice(1);
      }
      if (loc.length >= 2 && loc.length <= 30) return loc;
    }
    // 2. Known city / remote mentions (hashtags like #HyderabadJobs guarded via word boundary)
    const cities = /\b(hyderabad|bengaluru|bangalore|chennai|mumbai|pune|delhi(?:[ -]?ncr)?|noida|gurugram|gurgaon|kolkata|ahmedabad|indore|nagpur|jaipur|kochi|coimbatore|trivandrum|thiruvananthapuram|remote|work from home|chennai)\b/i;
    const c = text.match(cities);
    if (c) {
      const s = c[0];
      return s === 'work from home' ? 'Remote (WFH)' : s === 'remote' ? 'Remote' : s.charAt(0).toUpperCase() + s.slice(1);
    }
    return '';
  },

  findRolePhrase(text) {
    const suffixes = /\b(engineers?|developers?|analysts?|architects?|specialists?|executives?|managers?|consultants?|coordinators?|designers?|testers?|trainees?|interns?|instructor|officers?|professionals?|associates?|support)\b/i;
    const stop = /^(the|a|an|and|or|for|to|at|of|in|by|with|is|are|we|our|hiring|hiring for|looking|recruiting|recruitment|need|needed|requires?|required|opening|vacancy|vacancies|position|role|apply|want|seeking|urgently|candidate|candidates|experience|qualified|fresher|freshers|from|as)$/i;
    const words = text.replace(/[^A-Za-z0-9 &.,+/-]/g, ' ').split(/\s+/).filter(Boolean);
    for (let i = 0; i < words.length; i++) {
      if (suffixes.test(words[i])) {
        const taken = [];
        for (let j = i - 1; j >= 0 && taken.length < 6; j--) {
          if (stop.test(words[j])) break;
          taken.unshift(words[j]);
        }
        taken.push(words[i]);
        const seg = taken.join(' ');
        if (seg.length >= 5) {
          const cleaned = this.cleanTitleCandidate(seg);
          if (cleaned.length <= 60) return cleaned;
        }
      }
    }
    return '';
  },

  extractRoleTitle(text) {
    const JOB_SUFFIXES = /\b(developers?|engineers?|analysts?|architects?|specialists?|executives?|staff|leads?|trainees?|interns?|managers?|officers?|consultants?|coordinators?|designers?|technicians?|devops|qa|customers?[\s-]+support|business[\s-]+analysts?|project[\s-]+managers?|product[\s-]+managers?|technical[\s-]+support|professionals?|recruiters?|sales|marketing|admin|quality|operations|freshers?)\b/i;

    // 0. Multi-position post ("Open Positions: A, B, C") -> generic title.
    const suffixScan = new RegExp(JOB_SUFFIXES.source, 'gi');
    let count = 0; const seen = new Set(); let mm;
    while ((mm = suffixScan.exec(text)) && count < 12) {
      const w = mm[0].toLowerCase();
      if (!seen.has(w)) { seen.add(w); count++; }
    }
    const multiMarker = /\b(?:open\s+positions?|positions?\s*:|vacancies|multiple\s+(?:openings?|jobs?|positions?))\b/i.test(text);
    if ((multiMarker && count >= 2) || count >= 4) {
      const dept = text.match(/\bdepartment\s*:?\s*(?:-?\s*)([A-Za-z][^,\n]{1,25})/i);
      return dept ? `${dept[1].trim()} - Job Opening` : 'Job Opening';
    }

    // 1. Explicit recruiter phrasing
    const patterns = [
      /\bhiring\s*\|\s*([A-Za-z][^|#,\n]{2,50})/i,
      /\b(?:role|position|designation)\s*[:|-]\s*([A-Za-z][^|#,\n]{2,50})/i,
      /\bopening for\s+(?:a |an )?([A-Za-z][^|#,\n]{2,50})/i,
      /\bwe(?:'| a)re hiring\s+(?:a |an )?([A-Za-z][^|#,\n]{2,50})/i,
      /\b(?:position|role)\s+(?:of|for)\s+(?:a |an )?([A-Za-z][^|#,\n]{2,50})/i,
      /\b(hiring|recruiting|open(?:ing| position) for|looking for|need|requires?)\b[^|#\n]{0,35}?(?:\||\n)?\s*([A-Z][a-zA-Z0-9&\/.+-]*(\s+[a-zA-Z0-9&\/.+-]+){0,4})/i,
    ];

    for (const p of patterns) {
      const m = text.match(p);
      if (m && (m[2] || m[1])) {
        let candidate = (m[2] || m[1])
          .replace(/^(?:a |an |for )/i, '')
          .replace(/^(strong|excellent|good|great|skilled)\s+(?:knowledge|experience|background)\s+(?:of|in|with)\s+/i, '');
        candidate = this.cleanTitleCandidate(candidate);
        if (candidate.length >= 3 && candidate.length <= 60 && JOB_SUFFIXES.test(candidate)) {
          return candidate;
        }
      }
    }

    // 2. Generic role phrase ending in a job suffix (Engineer/Developer/Analyst/etc.)
    const phrase = this.findRolePhrase(text);
    if (phrase) return phrase;

    // 3. Known tech role keyword phrase — add suffix if missing
    const roleKws = /\b(Full[ -]?Stack|Front.?end|Back.?end|Machine Learning|Data Science|DevOps|Python|Java|Node(?:\.js)?|React|Angular|Vue|UI|UX|QA|Web|Network|Cloud|Android|iOS|Flutter|Database|Salesforce|Django|FastAPI|Artificial Intelligence)\b\s*(Developer|Engineer|Analyst|Architect|Specialist|Executive|Staff|Lead|Trainee|Intern)?s?/i;
    const m2 = text.match(roleKws);
    if (m2 && m2[1]) {
      let suffix = m2[2] ? m2[2] : 'Developer';
      return this.cleanTitleCandidate(`${m2[1].trim()} ${suffix}`);
    }

    // 4. Last resort: strong intent signal → generic title
    if (/(?:recruit|hiring|cv|resume|apply|vacancy|openings?|to apply|salary|contact person)/i.test(text)) {
      const dept = text.match(/\bdepartment\s*:?\s*(?:-?\s*)([A-Za-z][^,\n]{1,25})/i);
      return dept ? `${dept[1].trim()} - Job Opening` : 'Job Opening';
    }
    return '';
  },

  parseFeedCard(card) {
    // LinkedIn hides the tail of long posts (emails/phones/apply links) behind
    // "…more". Click it once, then wait for the NEXT scan so we never capture
    // a truncated post. Same for image posts: schedule the flyer OCR first and
    // capture only once its text is back.
    const moreBtn = card.querySelector('.feed-shared-inline-show-more-text__button--more, .show-more-less-text__button--more');
    if (moreBtn && card.dataset.jaExpanded !== '1') {
      card.dataset.jaExpanded = '1';
      try { moreBtn.click(); } catch (e) {}
      this.log('expanding', {});
      return null;
    }

    // Merge real anchor hrefs into the text: innerText often hides apply links
    // that only exist as the anchor URL (e.g. "View post" / link previews / the
    // Qualcomm "link tab" pointing at the article).
    const linksText = [...card.querySelectorAll('a[href]')]
      .map(a => a.getAttribute('href') || '')
      .filter(h => /^https?:\/\//i.test(h) && !/linkedin\.com\/(?:company|feed|advice|post|in\/|\?)/i.test(h))
      .join(' ');
    let text = this.cleanText((card.innerText || '') + (linksText ? ' ' + linksText : ''));

    // Job flyers hold the real role/company/contact details as an image. OCR
    // every flyer once (cached, throttled to 5 concurrent) and merge the text
    // so the backend extracts those fields instead of leaving "Job Opening".
    const imgSrc = this.ocrFeedImage(card);
    if (imgSrc && this._ocrCache[imgSrc] !== undefined) {
      text = this.cleanText(text + ' ' + this._ocrCache[imgSrc]);
    } else if (imgSrc && !this._ocrDone.has(imgSrc)) {
      if (this._ocrInFlight.has(imgSrc)) {
        if (this._ocrInFlight.size <= 5) return null; // wait for result, reparse next pass
      } else if (this._ocrInFlight.size < 5) {
        this._ocrInFlight.add(imgSrc);
        this.scheduleOcr(imgSrc);
        this.log('ocr-start', { src: imgSrc.slice(0, 90) });
        return null;
      }
    }

    const link = card.querySelector('a[href*="/jobs/"]');
    const url = link ? link.href.split('?')[0] : '';

    // Try structured job-card fields (Jobs search page)
    const jobTitleEl = card.querySelector('.job-card-list__title, .job-card-container__link, h3, [class*="job-title"], [data-automation="jobTitle"]');
    const companyEl = card.querySelector('.job-card-container__company-name, .job-card-list__company-name, .company-name, [class*="company"]');
    const locationEl = card.querySelector('.job-card-container__metadata-wrapper, .job-card-list__location, .artdeco-entity-lockup__subtitle, [class*="location"]');

    let title = jobTitleEl ? this.cleanText(jobTitleEl.innerText) : '';
    let company = companyEl ? this.cleanText(companyEl.innerText) : '';
    let location = locationEl ? this.cleanText(locationEl.innerText) : '';

    // Fallback: detect job text on the home feed
    if (!title && this.isJobPost(text)) {
      // Strip the actor/header block ("Feed post … Follow <body>") so recruiter
      // headlines don't pollute the extracted title.
      const titleText = this.feedTitleText(text);
      title = this.extractRoleTitle(titleText);
      if (!title) return null;
      // Validation: reject junk where neither a role word nor any contact appears
      if (title !== 'Job Opening' && !this.hasRoleWord(text) && !this.hasContactInfo(text)) return null;
      // Company: LinkedIn's own author metadata is the most reliable and exact
      // name (a company page literally names the company). Prefer it over regex
      // guesses, since text-match can capture sentence fragments ("that X").
      const hideBy = card.querySelector('[aria-label^="Hide post by "], [aria-label*="control menu for post by "]');
      const actor = card.querySelector('.update-components-actor__name, .feed-shared-actor__name, .update-components-actor, [class*="actor__name"]');
      const actorName = hideBy
        ? this.cleanText(hideBy.getAttribute('aria-label').replace(/^(?:hide post by|open control menu for post by)\s+/i, ''))
        : (actor ? this.cleanText(actor.innerText).split('\n')[0] : '');
      const actorCompany = actorName
        ? this.normalizeCompanyCand(actorName.replace(/\s*\|\s*$/i, ''))
        : '';
      if (actorCompany) {
        company = actorCompany;
      } else {
        // Only if LinkedIn gave no reliable author name, fall back to the post text
        const companyFromText = this.extractCompany(text, title);
        if (companyFromText) {
          company = companyFromText;
        }
      }
      // Location often appears after the company/hashtag block
      const locText = this.extractLocation(text);
      if (locText) location = locText;
      // Make sure we only keep genuine job posts
      if (!title) return null;
    }

    // On a jobs search card, only capture actual job cards
    if (!jobTitleEl && !this.isJobPost(text)) return null;

    if (!title) return null;

    return {
      url,
      title,
      company,
      location,
      description: text,
      source: 'linkedin'
    };
  },

  processJob(job) {
    this.detectedJobs.add(job.url || `${job.title}-${job.company}`);
    job.emails = this.extractEmail(job.description);
    job.phones = this.extractPhone(job.description);
    job.experience = this.extractExperience(this.feedTitleText(job.description));
    job.applyLink = this.extractApplyLink(job.description);

    job.hasEmail = job.emails.length > 0;
    job.hasPhone = job.phones.length > 0;
    job.hasApplyLink = !!job.applyLink;

    this.updatePopup(job);
    this.sendToBackend(job);
  },

  autoCapture(job) {
    this.processJob(job);
    this.showToast(`🎯 Captured: ${job.title} at ${job.company}`);
  },

  updatePopup(job) {
    chrome.runtime.sendMessage({
      type: 'JOB_DETECTED',
      job: job
    });
  },

  sendToBackend(job) {
    const h = { 'Content-Type': 'application/json' };
    if (this.apiToken) h['X-API-Key'] = this.apiToken;
    fetch(`${this.backendUrl}/api/jobs`, {
      method: 'POST',
      headers: h,
      body: JSON.stringify(job)
    })
    .then(res => res.json())
    .then(data => {
      job.backendId = data.id;
      chrome.runtime.sendMessage({ type: 'JOB_SAVED', jobId: data.id });
    })
    .catch(err => {
      console.error('Backend save failed:', err);
      chrome.runtime.sendMessage({
        type: 'BACKEND_OFFLINE',
        job: job
      });
    });
  },

  showToast(message) {
    let toast = document.getElementById('job-auto-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'job-auto-toast';
      toast.style.cssText = `
        position: fixed;
        bottom: 20px;
        right: 20px;
        background: rgba(15, 23, 42, 0.95);
        color: white;
        padding: 12px 20px;
        border-radius: 8px;
        font-size: 13px;
        z-index: 999999;
        max-width: 350px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        opacity: 0;
        transition: opacity 0.3s;
      `;
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.opacity = '1';
    setTimeout(() => { toast.style.opacity = '0'; }, 3000);
  },

  injectFloatingUI() {
    const div = document.createElement('div');
    div.id = 'job-auto-float';
    div.innerHTML = `
      <style>
        #job-auto-float {
          position: fixed;
          bottom: 20px;
          left: 0px;
          right: 0px;
          display: flex;
          justify-content: center;
          z-index: 999998;
          pointer-events: none;
          width: 100%;
        }
        #job-auto-float .pil {
          pointer-events: auto;
          background: #2563eb;
          color: white;
          border: none;
          padding: 8px 16px;
          border-radius: 20px;
          font-size: 13px;
          font-weight: 500;
          box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
          cursor: pointer;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }
        #job-auto-float .pil:hover { background: #1d4ed8; }
      </style>
      <div style="display:flex; gap:8px;">
        <button class="pil" id="job-auto-dumper">✨ Auto-Detect Active</button>
      </div>
    `;
    document.body.appendChild(div);

    // Shortcut: Ctrl+Shift+L to force-finalize current job if auto-detect missed
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'l') {
        e.preventDefault();
        const job = this.getJobDetailPage();
        if (job && job.title) {
          this.processJob(job);
        } else {
          this.showToast('Go to a LinkedIn job page to capture');
        }
      }
    });
  }
};

// Listen for popup commands
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'CAPTURE_NOW') {
    const job = JobDetector.getJobDetailPage();
    if (job && job.title) {
      JobDetector.processJob(job);
      sendResponse({ success: true, job });
    } else {
      // Not a single job detail page -> scan the feed/jobs list in view
      let captured = 0;
      const cards = JobDetector.getFeedJobCards();
      for (const card of cards) {
        const j = JobDetector.parseFeedCard(card);
        if (j && j.title) {
          const key = `${j.title}-${j.company}`;
          if (!JobDetector.detectedJobs.has(key)) {
            JobDetector.autoCapture(j);
            captured++;
          }
        }
      }
      sendResponse({
        success: captured > 0,
        captured,
        error: captured ? undefined : 'No job posts found on this page'
      });
    }
    return true;
  }

  if (msg.type === 'GET_STATUS') {
    sendResponse({
      detectedCount: JobDetector.detectedJobs.size,
      active: JobDetector.watchInterval !== null
    });
    return true;
  }
});

JobDetector.init();
