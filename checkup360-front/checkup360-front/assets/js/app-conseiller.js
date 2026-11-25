/*!
 * app-conseiller.js — tableau de bord des conseillers (connexion JWT, liste du jour, détail, actions).
 * En mode démo, les dépôts sont fictifs et avancent seuls ; en mode réel, la liste est rechargée toutes les 10 s.
 */
var API = window.CheckupAPI;
var MOCK = API.config.mock;

class Component extends DCLogic {
  constructor(...a) {
    super(...a);
    this.timers = [];
    this.FLOW = ['EN_ATTENTE', 'LECTURE', 'MAPPING_IA', 'CALCUL', 'INTERPRETATION', 'PDF', 'ENVOYE'];
    this.STL = { EN_ATTENTE: 'En attente', LECTURE: 'Lecture', MAPPING_IA: 'Analyse IA', CALCUL: 'Calcul', INTERPRETATION: 'Interprétation', PDF: 'PDF', ENVOYE: 'Envoyé', ERREUR: 'Erreur' };
    this.state = { logged: API.admin.isLogged(), lf: { mail: '', pw: '' }, pwShow: false, loginErr: null, loginBusy: false, day: '16', filter: 'all', q: '', typeF: '', selId: null, jobs: MOCK ? this.seed() : [], loading: false, toasts: [], logoutModal: false, busy: null, newId: null };
  }
  golden() {
    return {
      alertes: [['Critique', 'FNE non activée'], ['Élevée', 'Trésorerie nette négative (−12,5 M)'], ['Moyenne', 'CMU déclarée en retard'], ['Info', 'Performance fragile (score < 60)']],
      plan: [['P1', 'Activer la facture normalisée électronique (FNE)', 'sell'], ['P2', 'Régulariser la CMU', 'rh'], ['P2', 'Plan de trésorerie à 13 semaines', 'compta'], ['P2', 'Ramener le délai clients à 60 j (+24,4 M)', 'sell'], ['P3', 'Allonger le délai fournisseurs (+10,2 M)', 'compta'], ['P3', 'Gagner 2 points de marge brute (+8,4 M d’EBE/an)', 'sell']]
    };
  }
  seed() {
    const g = this.golden();
    const J = (ref, nom, type, secteur, eff, heure, statut, perf, conf, couv, ent, extra) => ({ id: ref, ref, nom, type, secteur, eff, heure, statut, perf, conf, couv, ent, dur: extra && extra.dur, indicatif: !!(extra && extra.ind), alertes: (extra && extra.al) || [], plan: (extra && extra.plan) || [], errMsg: extra && extra.err, final: extra && extra.final });
    return [
      J('JPO-1612-001', 'Société Lagunaire de Négoce', 'ACTUEL', 'Commerce', 32, '09:12', 'ENVOYE', 68, 84, 100, 'Fait', { dur: '4 min 58', al: [['Moyenne', 'Délai clients 71 j']], plan: [['P2', 'Ramener le délai clients à 60 j', 'sell'], ['P3', 'Gagner 2 points de marge brute', 'sell']] }),
      J('JPO-1612-002', 'BTP Akwaba', 'PROSPECT', 'BTP', 58, '09:26', 'ENVOYE', 44, 59, 92, '10:30', { dur: '6 min 12', al: [['Critique', 'Régime plus léger que le CA ne le permet'], ['Élevée', 'CNPS en retard']], plan: [['P1', 'Activer la FNE', 'sell'], ['P2', 'Régulariser CNPS', 'rh'], ['P4', 'Stabiliser l’endettement', 'compta']] }),
      J('JPO-1612-003', 'Clinique Les Palmiers', 'REACTIVER', 'Santé', 41, '09:40', 'ERREUR', null, null, null, null, { err: 'OCR illisible : confiance globale 0,24 (seuil 0,30).' }),
      J('JPO-1612-004', 'Agro Bandama', 'ACTUEL', 'Agriculture', 120, '10:05', 'ENVOYE', 81, 96, 100, 'Fait', { dur: '5 min 21', al: [['Info', 'Masse salariale 31 % du CA']], plan: [['P3', 'Maintenir le délai clients sous 60 j', 'sell']] }),
      J('JPO-1612-005', 'Transports Comoé', 'PROSPECT', 'Transport', 19, '10:18', 'ENVOYE', 36, 42, 58, null, { dur: '7 min 03', ind: true, al: [['Critique', 'Risque critique (score < 40)'], ['Critique', 'Capitaux propres négatifs'], ['Élevée', 'Risque de redressement (conformité < 50)']], plan: [['P1', 'Activer la FNE', 'sell'], ['P1', 'Reconstituer les capitaux propres', 'compta'], ['P2', 'Plan de trésorerie à 13 semaines', 'compta']] }),
      J('JPO-1612-006', 'Yopougon Conseil', 'PROSPECT', 'Services', 8, '10:34', 'ENVOYE', 57, 75, 88, '11:30', { dur: '5 min 05', al: [['Élevée', 'FNE en cours d’activation']], plan: [['P1', 'Finaliser l’activation FNE', 'sell'], ['P3', 'Allonger le délai fournisseurs', 'compta']] }),
      J('JPO-1612-007', 'Pharmacie du Plateau Sud', 'REACTIVER', 'Pharmacie', 14, '10:51', 'ENVOYE', 72, 90, 97, null, { dur: '4 min 40', al: [['Info', 'Secteur exempté de FNE']], plan: [['P2', 'Régulariser la DISA', 'rh']] }),
      J('JPO-1612-017', 'Entreprise Exemple SARL', 'PROSPECT', 'Commerce', 24, '11:02', 'ENVOYE', 50, 75, 95, null, { dur: '5 min 22', al: g.alertes, plan: g.plan }),
      J('JPO-1612-018', 'Imprimerie San Pedro', 'PROSPECT', 'Industrie', 27, '11:14', 'CALCUL', null, null, null, null, { final: { perf: 63, conf: 71, couv: 90, al: [['Élevée', 'FNE non activée']], plan: [['P1', 'Activer la FNE', 'sell'], ['P3', 'Gagner 2 points de marge brute', 'sell']] } }),
      J('JPO-1612-019', 'Hôtel Assinie Lagune', 'ACTUEL', 'Hôtellerie', 46, '11:16', 'MAPPING_IA', null, null, null, null, { final: { perf: 55, conf: 88, couv: 93, al: [['Moyenne', 'Masse salariale 38 % du CA']], plan: [['P3', 'Optimiser la masse salariale', 'rh']] } }),
      J('JPO-1612-020', 'Groupe Scolaire Les Étoiles', 'PROSPECT', 'Éducation', 35, '11:18', 'LECTURE', null, null, null, null, { final: { perf: 47, conf: 64, couv: 81, al: [['Élevée', 'ITS en retard']], plan: [['P2', 'Régulariser l’ITS', 'rh'], ['P2', 'Plan de trésorerie à 13 semaines', 'compta']] } })
    ];
  }
  componentDidMount() { if (this.state.logged) this.startLive(); }
  /* Mise en forme d'un dépôt renvoyé par GET /api/admin/diagnostics */
  fromApi(d) {
    const t = d.created_at ? new Date(d.created_at) : null;
    const mod = (m) => { const x = (m || '').toLowerCase(); return x.includes('rh') ? 'rh' : x.includes('compta') ? 'compta' : x.includes('legal') ? 'legal' : x.includes('task') ? 'task' : 'sell'; };
    const niv = { critique: 'Critique', elevee: 'Élevée', 'élevée': 'Élevée', moyenne: 'Moyenne', info: 'Info' };
    return {
      id: d.id || d.job_id, ref: d.reference || d.ref || d.job_id || d.id, nom: d.nom_reel || d.entreprise || d.nom || '—',
      type: d.type_client || 'PROSPECT', secteur: d.secteur || '—', eff: d.effectif || '—',
      heure: d.heure || (t ? String(t.getHours()).padStart(2, '0') + ':' + String(t.getMinutes()).padStart(2, '0') : '--:--'),
      statut: d.statut || 'EN_ATTENTE', perf: d.score_performance ?? null, conf: d.score_conformite ?? null, couv: d.couverture_pct ?? null,
      ent: d.entretien_realise ? 'Fait' : d.rappel_effectue ? 'Rappelé' : (d.entretien || null), indicatif: !!d.indicatif,
      alertes: (d.alertes || []).map((a) => Array.isArray(a) ? a : [niv[(a.niveau || '').toLowerCase()] || a.niveau || 'Info', a.texte || a.message || '']),
      plan: (d.plan_action || []).map((p) => Array.isArray(p) ? p : [p.priorite || p.prio, p.action, mod(p.module || p.outil)]),
      errMsg: d.erreur || null, journal: d.journal || null, auditApi: d.audit || null
    };
  }
  /* Ouvre le détail : journal et audit réels lus sur l'API */
  openJob(id) {
    this.setState({ selId: id });
    if (MOCK) return;
    API.admin.detail(id).then((d) => this.setState((st) => ({ jobs: st.jobs.map((j) => (j.id === id ? { ...j, journal: d.journal || [], auditApi: d.audit || [], errMsg: d.erreur || j.errMsg } : j)) })))
      .catch((err) => this.toast('bad', 'Détail indisponible', err.message));
  }
  load() {
    if (MOCK) return;
    const day = { '16': '2026-12-16', '17': '2026-12-17', '18': '2026-12-18' }[this.state.day];
    this.setState({ loading: true });
    API.admin.list(day).then((r) => this.setState((st) => ({ jobs: (r.items || r || []).map((d) => { const n = this.fromApi(d); const old = st.jobs.find((x) => x.id === n.id); return old && old.journal ? { ...n, journal: old.journal, auditApi: old.auditApi } : n; }), loading: false }), () => { if (this.state.selId) this.openJob(this.state.selId); })).catch((e) => {
      this.setState({ loading: false });
      if (e.status === 401) { API.admin.logout(); this.setState({ logged: false, loginErr: 'Session expirée : reconnectez-vous.' }); }
      else this.toast('bad', 'Chargement impossible', e.message);
    });
  }
  componentDidUpdate(pp, ps) { if (ps.day !== this.state.day && this.state.logged) this.load(); }
  componentWillUnmount() { this.clearT(); }
  clearT() { this.timers.forEach((t) => { clearTimeout(t); clearInterval(t); }); this.timers = []; }
  later(fn, ms) { const t = setTimeout(fn, ms); this.timers.push(t); }
  toast(kind, title, text) {
    const id = Date.now() + Math.random();
    this.setState((s) => ({ toasts: [...s.toasts, { id, kind, title, text }].slice(-3) }));
    this.later(() => this.setState((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })), 4500);
  }
  startLive() {
    this.clearT();
    if (!MOCK) { this.load(); const p = setInterval(() => this.load(), 10000); this.timers.push(p); return; }
    const iv = setInterval(() => this.tick(), 2600);
    this.timers.push(iv);
    this.later(() => {
      const nj = { id: 'JPO-1612-021', ref: 'JPO-1612-021', nom: 'Quincaillerie Adjamé Centrale', type: 'ACTUEL', secteur: 'Commerce', eff: 12, heure: '11:21', statut: 'EN_ATTENTE', perf: null, conf: null, couv: null, ent: null, alertes: [], plan: [], final: { perf: 61, conf: 80, couv: 96, al: [['Moyenne', 'Délai clients 66 j']], plan: [['P2', 'Ramener le délai clients à 60 j', 'sell']] } };
      this.setState((s) => ({ jobs: [...s.jobs, nj], newId: nj.id }));
      this.toast('info', 'Nouveau dépôt', 'Quincaillerie Adjamé Centrale · 2 fichiers');
    }, 7000);
  }
  tick() {
    const sent = [];
    this.setState((s) => ({
      jobs: s.jobs.map((j) => {
        const i = this.FLOW.indexOf(j.statut);
        if (i < 0 || j.statut === 'ENVOYE') return j;
        const nx = this.FLOW[i + 1];
        if (nx === 'ENVOYE' && j.final) { sent.push(j.nom); return { ...j, statut: 'ENVOYE', perf: j.final.perf, conf: j.final.conf, couv: j.final.couv, alertes: j.final.al, plan: j.final.plan, dur: '5 min ' + (10 + j.nom.length % 40) }; }
        return { ...j, statut: nx };
      })
    }), () => sent.forEach((n) => this.toast('ok', 'Rapport envoyé', n)));
  }
  grade(v) { return v >= 80 ? 'A' : v >= 60 ? 'B' : v >= 40 ? 'C' : 'D'; }
  gradeCls(v) { return v >= 80 ? 'b-ok' : v >= 60 ? 'b-dark' : v >= 40 ? 'b-warn' : 'b-bad'; }
  decorate(j) {
    const run = this.FLOW.indexOf(j.statut) >= 0 && j.statut !== 'ENVOYE';
    const has = j.statut === 'ENVOYE' && j.perf != null;
    const typeL = { ACTUEL: 'Actuel', REACTIVER: 'À réactiver', PROSPECT: 'Prospect' }[j.type];
    const typeCls = { ACTUEL: 'b-dark', REACTIVER: 'b-acc', PROSPECT: 'b-info' }[j.type];
    const stCls = j.statut === 'ENVOYE' ? 'b-ok' : j.statut === 'ERREUR' ? 'b-bad' : 'b-run';
    const prog = Math.round((this.FLOW.indexOf(j.statut) + 0.5) / 7 * 100);
    const nivCls = { Critique: 'b-bad', 'Élevée': 'b-warn', Moyenne: 'b-acc', Info: 'b-info' };
    const prioCls = { P1: 'b-bad', P2: 'b-dark', P3: 'b-acc', P4: '' };
    const modL = { sell: 'Flow Sell', rh: 'Flow RH', compta: 'Flow Compta', legal: 'Flow Legal', task: 'Flow Task' };
    const idx = j.statut === 'ERREUR' ? 1 : this.FLOW.indexOf(j.statut);
    const [hh, mm] = j.heure.split(':').map(Number);
    const tAt = (k) => { const t = hh * 60 + mm + k; return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
    const hm = (iso) => { if (!iso) return '--:--'; const d = new Date(iso); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); };
    const real = !MOCK;
    const hist = real ? (j.journal || []).map((ev) => ({ t: hm(ev.t), l: ev.message, c: ev.niveau === 'erreur' ? '#DC2626' : ev.niveau === 'ok' ? '#15803D' : '#000000' })) : this.FLOW.slice(0, Math.max(1, idx + 1)).map((st, k) => ({ t: tAt(k), l: this.STL[st], c: k === idx && run ? '#FFD700' : '#000000' }));
    if (!real && j.statut === 'ERREUR') hist.push({ t: tAt(2), l: 'Erreur : ' + (j.errMsg || 'lecture impossible'), c: '#DC2626' });
    if (j.ent === 'Fait') hist.push({ t: tAt(40), l: 'Entretien réalisé', c: '#15803D' });
    return {
      ...j, typeL, typeCls, stCls, stL: this.STL[j.statut], running: run, prog,
      hasScore: has, noScore: !has, gp: has ? this.grade(j.perf) : '', gc: has ? this.grade(j.conf) : '',
      gpCls: has ? this.gradeCls(j.perf) : '', gcCls: has ? this.gradeCls(j.conf) : '',
      couvL: j.couv != null ? j.couv + ' %' : '—',
      entL: j.ent === 'Fait' ? 'Réalisé' : j.ent === 'Rappelé' ? 'Client rappelé' : j.ent ? 'Réservé ' + j.ent : j.statut === 'ERREUR' ? 'Rappel à faire' : '—',
      entDone: j.ent === 'Fait', entBtn: j.ent === 'Fait' ? 'Entretien réalisé' : 'Marquer l’entretien réalisé',
      effectif: j.eff, isErr: j.statut === 'ERREUR',
      ringPCls: has && j.perf < 40 ? 'ring bad' : 'ring gold',
      perfAria: 'Performance ' + j.perf + ' sur 100', confAria: 'Conformité ' + j.conf + ' sur 100',
      alertes: (j.alertes || []).map(([niv, t]) => ({ niv, t, cls: nivCls[niv] || '' })),
      plan: (j.plan || []).map(([prio, a, m]) => ({ prio, a, cls: prioCls[prio], mod: 'p-' + m, modL: modL[m] })),
      hasAlerts: (j.alertes || []).length > 0, hasPlan: (j.plan || []).length > 0,
      hist,
      audit: real ? (j.auditApi || []).map((a) => hm(a.horodatage) + ' · ' + a.acteur + ' · ' + a.action) : [tAt(0) + ' · système · dépôt reçu (empreinte SHA-256)', tAt(1) + ' · worker · fichiers lus en conteneur isolé'].concat(j.statut === 'ENVOYE' ? [tAt(6) + ' · système · PDF stocké chiffré, e-mail envoyé'] : []),
      openL: 'Voir le détail de ' + j.nom
    };
  }
  renderVals() {
    const s = this.state;
    const dayJobs = MOCK ? (s.day === '16' ? s.jobs : []) : s.jobs;
    const q = s.q.trim().toLowerCase();
    const match = (j) => {
      if (s.typeF && j.type !== s.typeF) return false;
      if (q && !(j.nom.toLowerCase().includes(q) || j.ref.toLowerCase().includes(q))) return false;
      if (s.filter === 'run') return this.FLOW.indexOf(j.statut) >= 0 && j.statut !== 'ENVOYE';
      if (s.filter === 'sent') return j.statut === 'ENVOYE';
      if (s.filter === 'err') return j.statut === 'ERREUR';
      return true;
    };
    const rows = dayJobs.filter(match).sort((a, b) => b.heure.localeCompare(a.heure)).map((j) => ({ ...this.decorate(j), trCls: (s.selId === j.id ? 'sel ' : '') + (s.newId === j.id ? 'new' : ''), open: () => this.openJob(j.id) }));
    const run = dayJobs.filter((j) => this.FLOW.indexOf(j.statut) >= 0 && j.statut !== 'ENVOYE').length;
    const sentJ = dayJobs.filter((j) => j.statut === 'ENVOYE' && j.perf != null);
    const err = dayJobs.filter((j) => j.statut === 'ERREUR').length;
    const avg = (k) => sentJ.length ? Math.round(sentJ.reduce((a, j) => a + j[k], 0) / sentJ.length) : '—';
    const chip = (id, l, n) => ({ l, n, cls: s.filter === id ? 'chip on' : 'chip', pressed: s.filter === id ? 'true' : 'false', pick: () => this.setState({ filter: id }) });
    const selJob = s.jobs.find((j) => j.id === s.selId);
    const sel = selJob ? this.decorate(selJob) : { hist: [], audit: [], alertes: [], plan: [] };
    const filtersOn = s.filter !== 'all' || q || s.typeF;
    const dayL = { '16': 'Mercredi 16 décembre 2026 · jour 1', '17': 'Jeudi 17 décembre 2026 · jour 2', '18': 'Vendredi 18 décembre 2026 · jour 3' };
    const name = s.lf.mail ? s.lf.mail.split('@')[0].replace(/[._-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Conseiller du jour';
    const initials = name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
    const toasts = s.toasts.map((t) => ({ ...t, cls: 't-' + t.kind, ok: t.kind === 'ok', bad: t.kind === 'bad', info: t.kind === 'info' }));
    const upd = (fn) => this.setState((st) => ({ jobs: st.jobs.map((j) => (j.id === st.selId ? fn(j) : j)) }));
    return {
      showLogin: !s.logged, showDash: s.logged,
      lf: s.lf, pwType: s.pwShow ? 'text' : 'password', pwAria: s.pwShow ? 'Masquer le mot de passe' : 'Afficher le mot de passe',
      mailCls: s.loginErr && !s.lf.mail ? 'in err' : 'in', pwCls: s.loginErr && !s.lf.pw ? 'in err' : 'in',
      loginErr: s.loginErr, loginCls: s.loginBusy ? 'loading' : '',
      onMail: (e) => { const v = e.target.value; this.setState((st) => ({ lf: { ...st.lf, mail: v }, loginErr: null })); },
      onPw: (e) => { const v = e.target.value; this.setState((st) => ({ lf: { ...st.lf, pw: v }, loginErr: null })); },
      togglePw: () => this.setState({ pwShow: !s.pwShow }),
      login: (e) => {
        e.preventDefault();
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.lf.mail) || !s.lf.pw) { this.setState({ loginErr: 'Saisissez votre e-mail professionnel et votre mot de passe.' }); return; }
        this.setState({ loginBusy: true });
        API.admin.login(s.lf.mail.trim(), s.lf.pw).then(() => { this.setState({ loginBusy: false, logged: true, lf: { ...s.lf, pw: '' } }); this.startLive(); this.toast('ok', 'Connecté', 'Bonne journée de salon !'); })
          .catch((err) => this.setState({ loginBusy: false, loginErr: err.status === 401 ? 'E-mail ou mot de passe incorrect.' : err.message }));
      },
      days: [['16', 'Mer. 16'], ['17', 'Jeu. 17'], ['18', 'Ven. 18']].map(([v, l]) => ({ l, cls: s.day === v ? 'tab on' : 'tab', pressed: s.day === v ? 'true' : 'false', pick: () => this.setState({ day: v, selId: null }) })),
      dayLong: dayL[s.day], userName: name, initials,
      stats: { total: dayJobs.length, capPct: Math.min(100, Math.round(dayJobs.length / 15 * 100)), sent: sentJ.length, run, err, p: avg('perf'), c: avg('conf'), avg: sentJ.length ? '5 min 31' : '—' },
      chips: [chip('all', 'Tous', dayJobs.length), chip('run', 'En cours', run), chip('sent', 'Envoyés', sentJ.length), chip('err', 'Erreurs', err)],
      q: s.q, typeF: s.typeF,
      onQ: (e) => this.setState({ q: e.target.value }),
      onType: (e) => this.setState({ typeF: e.target.value }),
      rows, hasRows: rows.length > 0, noRows: rows.length === 0,
      emptyTitle: dayJobs.length ? 'Aucun dépôt ne correspond' : 'Journée à venir',
      emptyText: dayJobs.length ? 'Modifiez la recherche ou les filtres pour retrouver un diagnostic.' : 'Les dépôts de ce jour apparaîtront ici en temps réel dès l’ouverture du stand à 9 h.',
      canReset: !!filtersOn && dayJobs.length > 0,
      resetFilters: () => this.setState({ filter: 'all', q: '', typeF: '' }),
      exportCsv: () => MOCK ? this.toast('info', 'Export prêt', 'diagnostics_' + s.day + '-12-2026.csv · ' + dayJobs.length + ' lignes (mode démo)') : API.admin.exportCsv({ '16': '2026-12-16', '17': '2026-12-17', '18': '2026-12-18' }[s.day]).catch((err) => this.toast('bad', 'Export impossible', err.message)),
      hasSel: !!selJob, sel,
      closeSel: () => this.setState({ selId: null }),
      openReport: () => { if (MOCK) { window.open('rapport.html', '_blank', 'noopener'); return; } API.admin.pdf(s.selId).then((r) => window.open(r.url, '_blank', 'noopener')).catch((err) => this.toast('bad', 'Rapport indisponible', err.message)); },
      ovClick: (e) => { if (e.target === e.currentTarget) this.setState({ selId: null }); },
      ovKey: (e) => { if (e.key === 'Escape') this.setState({ selId: null }); },
      resendCls: s.busy === 'resend' ? 'loading' : '', retryCls: s.busy === 'retry' ? 'loading' : '',
      resend: () => { if (!MOCK) { this.setState({ busy: 'resend' }); API.admin.resend(s.selId).then(() => { this.setState({ busy: null }); this.toast('ok', 'E-mail renvoyé', 'Nouveau lien signé valable 7 jours.'); this.load(); }).catch((err) => { this.setState({ busy: null }); this.toast('bad', 'Action impossible', err.message); }); return; } this.setState({ busy: 'resend' }); this.later(() => { this.setState({ busy: null }); this.toast('ok', 'E-mail renvoyé', 'Nouveau lien signé valable 7 jours.'); }, 1000); },
      retry: () => { if (!MOCK) { this.setState({ busy: 'retry' }); API.admin.retry(s.selId).then(() => { this.setState({ busy: null }); this.toast('ok', 'Traitement relancé', 'Repris à l’étape de lecture.'); this.load(); }).catch((err) => { this.setState({ busy: null }); this.toast('bad', 'Action impossible', err.message); }); return; } this.setState({ busy: 'retry' }); this.later(() => { upd((j) => ({ ...j, statut: 'LECTURE', errMsg: null, final: { perf: 58, conf: 70, couv: 74, al: [['Info', 'Lecture partielle']], plan: [['P2', 'Plan de trésorerie à 13 semaines', 'compta']] } })); this.setState({ busy: null }); this.toast('info', 'Traitement relancé', 'Repris à l’étape de lecture.'); }, 900); },
      callClient: () => { if (!MOCK) { this.setState({ busy: 'call' }); API.admin.markCalled(s.selId).then(() => { this.setState({ busy: null }); this.toast('ok', 'Rappel noté', 'Le client a été recontacté.'); this.load(); }).catch((err) => { this.setState({ busy: null }); this.toast('bad', 'Action impossible', err.message); }); return; } upd((j) => ({ ...j, ent: 'Rappelé' })); this.toast('ok', 'Rappel noté', 'Le client a été recontacté.'); },
      markDone: () => { if (!MOCK) { this.setState({ busy: 'done' }); API.admin.markInterview(s.selId).then(() => { this.setState({ busy: null }); this.toast('ok', 'Entretien enregistré', sel.nom); this.load(); }).catch((err) => { this.setState({ busy: null }); this.toast('bad', 'Action impossible', err.message); }); return; } upd((j) => ({ ...j, ent: 'Fait' })); this.toast('ok', 'Entretien enregistré', sel.nom); },
      showLogoutModal: s.logoutModal,
      askLogout: () => this.setState({ logoutModal: true }),
      cancelLogout: () => this.setState({ logoutModal: false }),
      ovcClick: (e) => { if (e.target === e.currentTarget) this.setState({ logoutModal: false }); },
      logout: () => { API.admin.logout(); this.clearT(); this.setState({ logged: false, logoutModal: false, selId: null, lf: { mail: '', pw: '' }, toasts: [] }); },
      toasts
    };
  }
}
