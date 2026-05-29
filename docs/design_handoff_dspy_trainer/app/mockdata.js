/* ============================================================
   Mock data + live-run simulation
   Exposed as window.DB
   ============================================================ */
(function () {
  const now = Date.now();
  const mins = (m) => now - m * 60000;

  const ORG = { name: 'Cohere Labs', slug: 'cohere-labs', plan: 'Team' };

  const USER = { id: 'u_self', name: 'Kira Donovan', email: 'kira@coherelabs.ai', initials: 'KD', role: 'Owner' };

  const team = [
    { id: 'u_self', name: 'Kira Donovan', email: 'kira@coherelabs.ai', initials: 'KD', role: 'Owner', status: 'active', last: mins(2), via: 'github' },
    { id: 'u2', name: 'Marcus Pell', email: 'marcus@coherelabs.ai', initials: 'MP', role: 'Admin', status: 'active', last: mins(38), via: 'google' },
    { id: 'u3', name: 'Devi Raman', email: 'devi@coherelabs.ai', initials: 'DR', role: 'Member', status: 'active', last: mins(190), via: 'github' },
    { id: 'u4', name: 'Tom Asante', email: 'tom@coherelabs.ai', initials: 'TA', role: 'Member', status: 'active', last: mins(1440), via: 'google' },
    { id: 'u5', name: 'pending', email: 'sasha@coherelabs.ai', initials: 'S', role: 'Member', status: 'invited', last: null, via: null },
  ];

  const bundles = [
    {
      id: 'bdl_8f2a', name: 'support-triage-agent', version: 'v4', status: 'valid',
      uploadedAt: mins(54), size: '38.2 KB', author: 'Marcus Pell',
      module: 'module.py', metric: 'metric.py',
      signature: 'ticket: str, history: list[str] -> category: str, priority: str, reply: str',
      lmTarget: 'openai/gpt-4o-mini', dspyVersion: '2.5.6',
      diagnostics: [
        { level: 'ok', code: 'module_import', msg: 'module.py imported · TriageAgent(dspy.Module) found' },
        { level: 'ok', code: 'metric_import', msg: 'metric.py imported · judge_metric(gold, pred, trace) found' },
        { level: 'ok', code: 'signature', msg: 'Signature resolved · 2 inputs, 3 outputs' },
        { level: 'ok', code: 'deps', msg: 'All imports resolvable in sandbox (dspy 2.5.6, pydantic 2.x)' },
        { level: 'warn', code: 'no_seed', msg: 'No random seed set — runs may be non-deterministic' },
      ],
    },
    {
      id: 'bdl_3c1d', name: 'rag-citation-checker', version: 'v2', status: 'valid',
      uploadedAt: mins(1280), size: '51.7 KB', author: 'Devi Raman',
      module: 'module.py', metric: 'metric.py',
      signature: 'question: str, context: list[str] -> answer: str, citations: list[int]',
      lmTarget: 'anthropic/claude-3-5-sonnet', dspyVersion: '2.5.6',
      diagnostics: [
        { level: 'ok', code: 'module_import', msg: 'module.py imported · CitationRAG(dspy.Module) found' },
        { level: 'ok', code: 'metric_import', msg: 'metric.py imported · citation_f1(gold, pred, trace) found' },
        { level: 'ok', code: 'signature', msg: 'Signature resolved · 2 inputs, 2 outputs' },
      ],
    },
    {
      id: 'bdl_a90e', name: 'sql-generator', version: 'v7', status: 'invalid',
      uploadedAt: mins(15), size: '29.4 KB', author: 'Kira Donovan',
      module: 'module.py', metric: 'metric.py',
      signature: '—', lmTarget: 'openai/gpt-4o', dspyVersion: '2.4.9',
      diagnostics: [
        { level: 'ok', code: 'module_import', msg: 'module.py imported · SQLGen(dspy.Module) found' },
        { level: 'err', code: 'metric_signature', msg: "metric.py: judge_metric() missing required 'trace' parameter. Expected signature judge_metric(gold, pred, trace=None)" },
        { level: 'err', code: 'dspy_version', msg: 'Bundle pins dspy==2.4.9 — workspace requires >=2.5.0. Pin will be ignored.' },
        { level: 'warn', code: 'large_import', msg: 'module.py imports pandas — adds ~4s cold start per worker' },
      ],
    },
  ];

  const QUESTIONS = [
    { id: 'q1',  input: 'My invoice charged me twice for the March subscription. I want one refunded.', expected: 'category=billing · priority=high · reply mentions refund + timeline' },
    { id: 'q2',  input: 'How do I export my workspace data to CSV?', expected: 'category=how-to · priority=low · reply gives export steps' },
    { id: 'q3',  input: 'The dashboard has been down for 40 minutes and my team is blocked.', expected: 'category=outage · priority=urgent · reply acknowledges + status link' },
    { id: 'q4',  input: 'Can you delete my account and all associated data under GDPR?', expected: 'category=privacy · priority=high · reply confirms erasure process' },
    { id: 'q5',  input: 'Is there a student discount?', expected: 'category=sales · priority=low · reply links pricing/edu' },
    { id: 'q6',  input: 'I think someone logged into my account from another country.', expected: 'category=security · priority=urgent · reply triggers lock + reset' },
    { id: 'q7',  input: 'The mobile app keeps crashing on the upload screen on iOS 18.', expected: 'category=bug · priority=med · reply collects repro + version' },
    { id: 'q8',  input: 'What is your SLA for enterprise support?', expected: 'category=sales · priority=med · reply states SLA tiers' },
    { id: 'q9',  input: 'Reset my password — the email link expired twice.', expected: 'category=account · priority=med · reply sends fresh link' },
    { id: 'q10', input: 'You guys are useless, cancel everything right now.', expected: 'category=churn · priority=high · reply de-escalates + retention' },
    { id: 'q11', input: 'Do you support SSO with Okta on the Team plan?', expected: 'category=sales · priority=low · reply states SSO availability' },
    { id: 'q12', input: 'My API key leaked in a public repo, what do I do?', expected: 'category=security · priority=urgent · reply rotates key + audit' },
  ];

  const plans = [
    { id: 'pln_v21', name: 'Triage v4 — regression deck', bundleId: 'bdl_8f2a', status: 'running',
      questions: QUESTIONS, runsPerQuestion: 3, maxWorkers: 8, createdAt: mins(54), createdBy: 'Marcus Pell' },
    { id: 'pln_kx9', name: 'Triage v4 — urgent-path stress', bundleId: 'bdl_8f2a', status: 'succeeded',
      questions: QUESTIONS.slice(0, 8), runsPerQuestion: 5, maxWorkers: 12, createdAt: mins(220), createdBy: 'Kira Donovan' },
    { id: 'pln_rag', name: 'Citation F1 — golden set', bundleId: 'bdl_3c1d', status: 'succeeded',
      questions: QUESTIONS.slice(0, 10), runsPerQuestion: 2, maxWorkers: 6, createdAt: mins(1300), createdBy: 'Devi Raman' },
    { id: 'pln_dft', name: 'Triage — refund edge cases', bundleId: 'bdl_8f2a', status: 'draft',
      questions: QUESTIONS.slice(0, 5), runsPerQuestion: 3, maxWorkers: 8, createdAt: mins(12), createdBy: 'Kira Donovan' },
  ];

  // sample judge outputs
  const RATIONALES_PASS = [
    'Category and priority match the label exactly. Reply correctly proposes a refund and gives a 3–5 day timeline.',
    'Predicted urgent priority aligns with the outage scenario. Reply acknowledges impact and includes the status-page link.',
    'Security category correct; reply triggers an account lock and password reset as the gold answer requires.',
    'Answer cites the correct two source passages [2,5]. Citation F1 = 1.00 against gold.',
    'How-to category correct, low priority appropriate. Reply enumerates the CSV export steps accurately.',
  ];
  const RATIONALES_FAIL = [
    'Category predicted "support" but gold is "billing". Reply never mentions a refund — answer_mismatch.',
    'Priority predicted "med" but the outage scenario requires "urgent". Severity under-estimated.',
    'Reply omits the required status-page link and gives no ETA. Partial content, marked fail.',
    'Predicted citations [1,4] but gold is [2,5]. Citation F1 = 0.0 — wrong evidence selected.',
    'Model refused the request citing policy; gold expects a GDPR erasure confirmation. Off-target.',
  ];
  const FLAGS_POOL = ['answer_mismatch', 'priority_drift', 'missing_field', 'low_confidence', 'format_error', 'refusal', 'citation_miss'];

  function makeRunItems(plan, completeAll) {
    const items = [];
    let i = 0;
    plan.questions.forEach((q, qi) => {
      for (let a = 0; a < plan.runsPerQuestion; a++) {
        // deterministic pseudo-random
        const seed = (qi * 7 + a * 3 + 1);
        const passLikely = (seed % 10) < 7; // ~70% pass baseline
        const done = completeAll || (i < Math.floor(plan.questions.length * plan.runsPerQuestion * 0.18));
        const pass = passLikely && !(seed % 13 === 0);
        items.push({
          id: `ri_${plan.id}_${qi}_${a}`,
          jobId: 'job_' + plan.id,
          qId: q.id, qIndex: qi, attempt: a + 1,
          input: q.input, expected: q.expected,
          status: done ? (pass ? 'pass' : 'fail') : 'pending',
          score: done ? (pass ? +(0.82 + (seed % 18) / 100).toFixed(2) : +((seed % 40) / 100).toFixed(2)) : null,
          durationMs: done ? 1400 + (seed * 137) % 4200 : null,
          traceId: 'tr_' + Math.abs((qi * 131 + a * 17 + 9001)).toString(16),
          prediction: done ? samplePrediction(q, pass) : null,
          rationale: done ? (pass ? RATIONALES_PASS[seed % RATIONALES_PASS.length] : RATIONALES_FAIL[seed % RATIONALES_FAIL.length]) : null,
          flags: done && !pass ? [FLAGS_POOL[seed % FLAGS_POOL.length], ...(seed % 3 === 0 ? [FLAGS_POOL[(seed + 2) % FLAGS_POOL.length]] : [])] : [],
        });
        i++;
      }
    });
    return items;
  }

  function samplePrediction(q, pass) {
    const cat = q.expected.match(/category=([\w-]+)/);
    const pri = q.expected.match(/priority=([\w-]+)/);
    return {
      category: pass ? (cat ? cat[1] : 'support') : (q.id === 'q1' ? 'support' : 'general'),
      priority: pass ? (pri ? pri[1] : 'med') : 'med',
      reply: pass
        ? 'Thanks for reaching out — I can help with that right away. ' + (q.input.length > 60 ? 'I\'ve flagged this to the right team and ' : '') + 'here is what happens next…'
        : 'I understand your concern. Could you provide more details so I can route this correctly?',
    };
  }

  // jobs (one per non-draft plan)
  const jobs = plans.filter(p => p.status !== 'draft').map(p => {
    const complete = p.status === 'succeeded' || p.status === 'failed';
    const items = makeRunItems(p, complete);
    return {
      id: 'job_' + p.id, planId: p.id, bundleId: p.bundleId,
      status: p.status, startedAt: p.createdAt,
      runsPerQuestion: p.runsPerQuestion, maxWorkers: p.maxWorkers,
      items,
      mlflowParent: 'run_' + p.id.replace('pln_', ''),
    };
  });

  // ---- helpers / API surface ----
  function counts(items) {
    const c = { pass: 0, fail: 0, running: 0, queued: 0, pending: 0, total: items.length };
    items.forEach(it => { c[it.status] = (c[it.status] || 0) + 1; });
    c.done = c.pass + c.fail;
    return c;
  }
  function passRate(items) {
    const c = counts(items);
    return c.done ? c.pass / c.done : 0;
  }
  function avgScore(items) {
    const done = items.filter(i => i.score != null);
    if (!done.length) return 0;
    return done.reduce((s, i) => s + i.score, 0) / done.length;
  }

  window.DB = {
    ORG, USER, team, bundles, plans, jobs, QUESTIONS,
    makeRunItems, counts, passRate, avgScore,
    getBundle: (id) => bundles.find(b => b.id === id),
    getPlan: (id) => plans.find(p => p.id === id),
    getJob: (id) => jobs.find(j => j.id === id),
    getJobByPlan: (pid) => jobs.find(j => j.planId === pid),
    FLAGS_POOL, RATIONALES_PASS, RATIONALES_FAIL, samplePrediction,
  };
})();
