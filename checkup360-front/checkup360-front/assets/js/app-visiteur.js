/*!
 * app-visiteur.js — logique du site visiteur (accueil, connexion par code, formulaire en 6 étapes,
 * suivi en direct, résultat, Mes diagnostics). Toutes les données passent par window.CheckupAPI.
 */
var API = window.CheckupAPI;

class Component extends DCLogic {
  constructor(...a) {
    super(...a);
    this.timers = [];
    this.fileObjs = {};
    this.unsub = null;
    this.poll = null;
    this.K = {
      SECTEURS: ['Commerce et distribution', 'BTP et immobilier', 'Industrie et transformation', 'Services aux entreprises', 'Transport et logistique', 'Agriculture et agro-industrie', 'Hôtellerie et restauration', 'Santé (hors pharmacie)', 'Éducation et formation', 'Télécoms et TIC', 'Banque', 'Assurance', 'Pharmacie', 'Station-service (carburant)', 'Compagnie aérienne', 'Concessionnaire eau, électricité ou téléphone', 'Autre'],
      EXEMPT: ['Banque', 'Assurance', 'Pharmacie', 'Station-service (carburant)', 'Compagnie aérienne', 'Concessionnaire eau, électricité ou téléphone'],
      TYPE: [['ACTUEL', 'Client actuel'], ['REACTIVER', 'Ancien client'], ['PROSPECT', 'Première fois']],
      JOUR: [['2026-12-16', 'Mer. 16 · 08h30'], ['2026-12-17', 'Jeu. 17 · 10h30'], ['2026-12-18', 'Ven. 18 · 08h30']],
      JOURL: { '2026-12-16': 'Mercredi 16 décembre 2026', '2026-12-17': 'Jeudi 17 décembre 2026', '2026-12-18': 'Vendredi 18 décembre 2026' },
      INV: { '2026-12-16': ['Mercredi', '16', '08H30', 'dark'], '2026-12-17': ['Jeudi', '17', '10H30', 'light'], '2026-12-18': ['Vendredi', '18', '08H30', 'dark'] },
      REGIME: [['TEE', 'TEE (entreprenant)'], ['RME', 'RME'], ['REEL_SIMPLIFIE', 'Réel simplifié'], ['REEL_NORMAL', 'Réel normal'], ['NE_SAIT_PAS', 'Je ne sais pas']],
      FNE: [['ACTIVE', 'Activée'], ['EN_COURS_ACTIVATION', 'Activation en cours'], ['NON_ACTIVE', 'Non activée'], ['NE_SAIT_PAS', 'Je ne sais pas']],
      STAT: [['A_JOUR', 'À jour'], ['EN_RETARD', 'En retard'], ['NE_SAIT_PAS', 'Ne sait pas']],
      OBL: [['DGI', 'Déclarations fiscales'], ['CNPS', 'Cotisations sociales'], ['ITS', 'Impôt sur traitements et salaires'], ['CMU', 'Couverture maladie universelle'], ['DISA', 'Déclaration individuelle des salaires annuels']],
      DOCS: [['balance', 'Balance générale', 'Le plus complet : il suffit souvent à lui seul.', true], ['cr', 'Compte de résultat', 'Chiffre d’affaires, achats, charges et résultat.', false], ['bilan', 'Bilan', 'Actif, passif, trésorerie et dettes.', false], ['autre', 'Autre document', 'Journal de ventes, d’achats ou de paie, grand livre : diagnostic partiel.', false]],
      STEPS: [
        ['Entreprise', 'Votre entreprise', 'Ces informations identifient le dossier et choisissent les repères adaptés à votre secteur.'],
        ['Vous', 'Vous et votre visite', 'Pour vous rappeler si besoin et préparer votre invitation au bon jour.'],
        ['Fiscalité', 'Votre fiscalité', 'Nous vérifions que votre régime correspond à votre chiffre d’affaires. « Je ne sais pas » est accepté.'],
        ['Obligations', 'Vos obligations déclaratives', 'Une réponse par ligne. Elles alimentent votre score de conformité.'],
        ['Documents', 'Vos documents comptables', 'Un fichier par emplacement, 3 au maximum. La balance générale suffit souvent.'],
        ['Vérification', 'Vérifiez et lancez', 'Un dernier coup d’œil avant l’analyse. Vous pouvez modifier chaque bloc.']
      ],
      SLOTS: ['09:00', '09:30', '10:00', '10:30', '11:00', '11:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30'],
      STAGES: [['Lecture', 'Fichiers, encodage, OCR'], ['Analyse', 'Extraction IA et contrôles'], ['Calcul', 'Indicateurs et scores'], ['Rapport', 'Interprétation et rapport Word'], ['Envoyé', 'E-mail et Mes diagnostics']],
      ETAPE: { EN_ATTENTE: 0, LECTURE: 0, MAPPING_IA: 1, CALCUL: 2, INTERPRETATION: 3, PDF: 3, ENVOYE: 5 },
      PROG: {
        '2026-12-16': [['08:30', 'Accueil des visiteurs', 'Enregistrement et remise des badges'], ['09:00', 'Ouverture par le cabinet DC-KNOWING', ''], ['09:30', 'Check-up 360 et entretiens individuels', 'En continu jusqu’à 17h30', 'diag'], ['10:30', 'Présentation des 5 applications de La Suite Flow', 'Sell, RH, Compta, Legal, Task'], ['14:30', 'Atelier : la FNE en pratique', 'Avec Flow Sell et Flow Compta'], ['16:30', 'Démonstrations libres', '']],
        '2026-12-17': [['10:30', 'Accueil des visiteurs', 'Enregistrement et remise des badges'], ['11:00', 'Présentation de La Suite Flow', ''], ['11:00', 'Check-up 360 et entretiens individuels', 'En continu jusqu’à 17h30', 'diag'], ['14:30', 'Atelier : paie et déclarations sociales', 'Avec Flow RH'], ['16:00', 'Démonstrations Flow Legal et Flow Task', '']],
        '2026-12-18': [['08:30', 'Accueil des visiteurs', 'Enregistrement et remise des badges'], ['09:30', 'Check-up 360 et entretiens individuels', 'En continu jusqu’à 17h00', 'diag'], ['10:30', 'Présentation des 5 applications de La Suite Flow', ''], ['14:30', 'Atelier : piloter sa trésorerie', 'Avec Flow Compta'], ['17:00', 'Clôture des Journées', '']]
      }
    };
    this.state = this.initial();
  }
  initial() {
    return { screen: 'accueil', step: 1, f: this.emptyForm(), files: {}, err: {}, dragSlot: null, modal: null, busy: false, toasts: [],
      stage: 0, logs: [], pct: 0, elapsed: 0, failed: false, done: false, job: '', jobId: null, result: null, count: { p: 0, c: 0 }, confetti: false,
      slot: null, slotPick: null, taken: [], dl: null,
      user: null, auth: { step: 'email', email: '', code: '', err: null, busy: false, resendIn: 0, intent: 'diag' }, diags: [], diagsLoading: false, progDay: '2026-12-16' };
  }
  emptyForm() {
    return { raison: '', ncc: '', secteur: '', effectif: '', typeClient: '', jour: '2026-12-16', nom: '', tel: '', regime: '', fne: '', statuts: { DGI: '', CNPS: '', ITS: '', CMU: '', DISA: '' }, tva: '', consent: false };
  }
  sampleForm() {
    return { raison: 'Entreprise Exemple SARL', ncc: '', secteur: 'Commerce et distribution', effectif: '24', typeClient: 'PROSPECT', jour: '2026-12-16', nom: 'Aïcha Koné', tel: '07 00 00 00 00', regime: 'REEL_SIMPLIFIE', fne: 'NON_ACTIVE', statuts: { DGI: 'A_JOUR', CNPS: 'A_JOUR', ITS: 'A_JOUR', CMU: 'EN_RETARD', DISA: 'A_JOUR' }, tva: 'NON', consent: false };
  }
  componentDidMount() {
    const s = API.session();
    const draft = this.loadDraft();
    this.setState({ user: s ? { email: s.email } : null, f: draft || this.state.f, progDay: (draft && draft.jour) || '2026-12-16' }, () => { if (s) this.refreshDiags(); });
    this.tick = setInterval(() => {
      const a = this.state.auth;
      if (a.resendIn > 0) this.setState({ auth: { ...a, resendIn: a.resendIn - 1 } });
    }, 1000);
    const e = this.props.ecran;
    if (e && API.config.mock) this.demoScreen(e);
    // Session ouverte depuis un lien reçu par e-mail (ou un autre onglet)
    if (API.onSession) API.onSession((sess) => { this.setState({ user: { email: sess.email } }, () => this.refreshDiags()); });
    const linkErr = API.linkError && API.linkError();
    if (linkErr) { this.setState({ screen: 'auth' }); this.toast('bad', 'Lien expiré', linkErr); }
  }
  componentWillUnmount() { this.clearT(); clearInterval(this.tick); this.stopPoll(); if (this.unsub) this.unsub(); }
  componentDidUpdate(pp, ps) {
    if (ps.screen !== this.state.screen || ps.step !== this.state.step || ps.auth.step !== this.state.auth.step) { try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (e) {} }
    if (this.state.modal && ps.modal !== this.state.modal) setTimeout(() => { try { const b = document.querySelector('.mdl .mf .btn-a, .mdl .mf .btn-p, .mdl button'); if (b) b.focus(); } catch (e) {} }, 40);
    if (this.state.screen === 'auth' && this.state.auth.step === 'code' && ps.auth.step !== 'code') setTimeout(() => { try { const i = document.getElementById('a-code'); if (i) i.focus(); } catch (e) {} }, 60);
    if (ps.f !== this.state.f && this.state.screen === 'form') this.saveDraft();
    if (ps.screen !== this.state.screen) {
      if (this.state.screen === 'espace') { this.refreshDiags(); this.startPoll(); } else this.stopPoll();
    }
  }
  saveDraft() { try { const { consent, ...rest } = this.state.f; localStorage.setItem('ck_draft', JSON.stringify(rest)); } catch (e) {} }
  loadDraft() { try { const d = JSON.parse(localStorage.getItem('ck_draft')); return d ? { ...this.emptyForm(), ...d, consent: false } : null; } catch (e) { return null; } }
  clearDraft() { try { localStorage.removeItem('ck_draft'); } catch (e) {} }

  /* Écrans de démonstration (?ecran=...) — uniquement en mode démo */
  demoScreen(e) {
    const u = { email: 'direction@exemple.ci' };
    const sf = { ...this.sampleForm(), consent: true };
    const map = {
      'connexion-email': { screen: 'auth' }, 'connexion-code': { screen: 'auth', auth: { ...this.state.auth, step: 'code', email: u.email, resendIn: 24 } },
      'etape-1': { screen: 'form', step: 1, user: u }, 'etape-5': { screen: 'form', step: 5, user: u, f: sf }, 'etape-6': { screen: 'form', step: 6, user: u, f: { ...sf, consent: false } },
      'espace': { screen: 'espace', user: u, f: sf }, 'programme': { modal: 'programme' }
    };
    if (map[e]) this.setState(map[e]);
  }

  clearT() { this.timers.forEach((t) => { clearTimeout(t); clearInterval(t); }); this.timers = []; }
  later(fn, ms) { const t = setTimeout(fn, ms); this.timers.push(t); return t; }
  toast(kind, title, text) {
    const id = Date.now() + Math.random();
    this.setState((s) => ({ toasts: [...s.toasts, { id, kind, title, text }].slice(-3) }));
    setTimeout(() => this.setState((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })), 4500);
  }
  fail(err, title) { this.toast('bad', title || 'Une erreur est survenue', (err && err.message) || 'Réessayez dans un instant.'); }
  setF(k, v) {
    this.setState((s) => {
      const f = { ...s.f, [k]: v };
      if (k === 'secteur') {
        if (this.K.EXEMPT.includes(v)) f.fne = 'NON_APPLICABLE';
        else if (s.f.fne === 'NON_APPLICABLE') f.fne = '';
      }
      return { f, err: { ...s.err, [k]: null } };
    });
  }
  setStat(k, v) { this.setState((s) => ({ f: { ...s.f, statuts: { ...s.f.statuts, [k]: v } }, err: { ...s.err, statuts: null } })); }
  fmtSize(b) { return b >= 1000000 ? (b / 1000000).toFixed(1).replace('.', ',') + ' Mo' : Math.max(1, Math.round(b / 1000)) + ' Ko'; }
  fmtT(sec) { const s = Math.max(0, Math.round(sec)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); }
  validEmail(x) { return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test((x || '').trim()); }

  /* ---------- Mes diagnostics ---------- */
  refreshDiags() {
    if (!this.state.user) return;
    this.setState({ diagsLoading: true });
    API.myDiagnostics().then((diags) => this.setState({ diags, diagsLoading: false })).catch((e) => {
      this.setState({ diagsLoading: false });
      if (e.status === 401) this.expire(); else this.fail(e, 'Impossible de charger vos diagnostics');
    });
  }
  startPoll() { this.stopPoll(); this.poll = setInterval(() => { if (this.state.diags.some((d) => d.st === 'EN_COURS')) this.refreshDiags(); }, 5000); }
  stopPoll() { if (this.poll) clearInterval(this.poll); this.poll = null; }
  expire() { API.logout(); this.setState({ user: null, screen: 'auth', auth: { ...this.initial().auth, intent: 'espace' } }); this.toast('info', 'Session expirée', 'Reconnectez-vous avec un nouveau code.'); }

  /* ---------- connexion ---------- */
  goAuth(intent) { this.setState((s) => ({ screen: 'auth', modal: null, auth: { ...this.initial().auth, intent, email: s.auth.email } })); }
  sendCode() {
    const a = this.state.auth;
    if (!this.validEmail(a.email)) { this.setState({ auth: { ...a, err: 'Saisissez une adresse e-mail valide, par exemple direction@entreprise.ci.' } }); return; }
    this.setState({ auth: { ...a, busy: true, err: null } });
    API.requestCode(a.email.trim()).then((r) => {
      this.setState((s) => ({ auth: { ...s.auth, busy: false, step: 'code', code: '', resendIn: (r && r.resend_after) || 30, tries: 3 } }));
      this.toast('info', 'Code envoyé', 'Consultez la boîte de ' + a.email.trim());
    }).catch((e) => this.setState((s) => ({ auth: { ...s.auth, busy: false, err: e.status === 429 ? 'Trop de demandes. Patientez une minute.' : e.message } })));
  }
  verifyCode() {
    const a = this.state.auth;
    if (a.code.length < 6 || a.busy) return;
    this.setState({ auth: { ...a, busy: true, err: null } });
    API.verifyCode(a.email.trim(), a.code).then((sess) => {
      const intent = this.state.auth.intent;
      this.setState((s) => ({ user: { email: sess.email }, auth: { ...s.auth, busy: false } }));
      this.toast('ok', 'Connexion réussie', intent === 'diag' ? 'Commençons votre diagnostic.' : 'Bienvenue dans Mes diagnostics.');
      if (intent === 'diag') this.newDiag(); else this.setState({ screen: 'espace' });
      this.refreshDiags();
    }).catch((e) => {
      const s = this.state;
      const left = e.data && e.data.essais_restants != null ? e.data.essais_restants : (s.auth.tries ?? 3) - 1;
      const msg = e.status === 410 ? 'Code expiré. Demandez un nouveau code.' : left > 0 ? 'Code incorrect. Il vous reste ' + left + (left > 1 ? ' essais.' : ' essai.') : 'Trop d’essais. Demandez un nouveau code.';
      this.setState({ auth: { ...s.auth, busy: false, tries: left, code: '', err: msg } });
    });
  }

  /* ---------- formulaire ---------- */
  newDiag() {
    this.fileObjs = {};
    this.setState((s) => ({ screen: 'form', step: 1, modal: null, err: {}, files: {}, result: null, f: s.f.raison && !s.job ? s.f : { ...this.emptyForm(), nom: s.f.nom, tel: s.f.tel, typeClient: s.f.typeClient, jour: s.f.jour || '2026-12-16' } }));
  }
  validate(step) {
    const s = this.state, f = s.f, e = {};
    if (step === 1) {
      if (!f.raison.trim()) e.raison = 'Indiquez la raison sociale.';
      if (!f.secteur) e.secteur = 'Choisissez un secteur.';
      const n = Number(f.effectif);
      if (!(Number.isInteger(n) && n >= 1)) e.effectif = 'Un nombre entier de salariés (1 ou plus).';
    }
    if (step === 2) {
      if (!f.nom.trim()) e.nom = 'Indiquez votre nom.';
      const d = f.tel.replace(/\D/g, '').replace(/^225(?=\d{10}$)/, '');
      if (d.length !== 10) e.tel = 'Numéro incomplet : 10 chiffres attendus.';
      if (!f.typeClient) e.typeClient = 'Choisissez une réponse.';
    }
    if (step === 3) {
      if (!f.regime) e.regime = 'Choisissez une réponse.';
      if (!f.fne) e.fne = 'Choisissez une réponse.';
      if (!f.tva) e.tva = 'Choisissez oui ou non.';
    }
    if (step === 4 && Object.values(f.statuts).some((v) => !v)) e.statuts = 'Répondez pour les 5 lignes (« Ne sait pas » est accepté).';
    if (step === 5 && !Object.keys(s.files).length) e.files = 'Déposez au moins un fichier, idéalement la balance générale.';
    if (step === 6 && !f.consent) e.consent = 'Votre consentement est nécessaire pour lancer l’analyse.';
    return e;
  }
  next() {
    const e = this.validate(this.state.step);
    const n = Object.keys(e).length;
    if (n) { this.setState({ err: e }); this.toast('bad', 'Il manque une réponse', n > 1 ? n + ' éléments à compléter.' : '1 élément à compléter.'); return; }
    if (this.state.step < 6) this.setState((s) => ({ step: s.step + 1, err: {} }));
    else this.launch();
  }
  putFile(slot, fl) {
    if (!fl) return;
    const ext = (fl.name.split('.').pop() || '').toLowerCase();
    if (!['csv', 'xlsx', 'pdf'].includes(ext)) { this.toast('bad', 'Format non accepté', fl.name + ' : CSV, XLSX ou PDF uniquement.'); this.setState({ dragSlot: null }); return; }
    if (fl.size > 20 * 1024 * 1024) { this.toast('bad', 'Fichier trop lourd', fl.name + ' dépasse 20 Mo.'); this.setState({ dragSlot: null }); return; }
    const files = { ...this.state.files };
    if (!files[slot] && Object.keys(files).length >= 3) { this.toast('bad', 'Maximum atteint', '3 fichiers par diagnostic.'); return; }
    files[slot] = { name: fl.name, size: fl.size, ext };
    this.fileObjs[slot] = fl;
    this.setState((s) => ({ files, dragSlot: null, err: { ...s.err, files: null } }));
    this.toast('ok', 'Fichier ajouté', fl.name);
  }
  payload() {
    const f = this.state.f;
    const nsp = (v) => (v === 'NE_SAIT_PAS' ? 'NON_DISPONIBLE' : v);
    return {
      raison_sociale: f.raison.trim(), ncc: f.ncc.trim() || null, secteur: f.secteur, effectif: Number(f.effectif),
      contact_nom: f.nom.trim(), contact_email: this.state.user ? this.state.user.email : '', contact_telephone: f.tel.replace(/\s+/g, ''),
      type_client: f.typeClient, jour_evenement: f.jour,
      regime_fiscal: f.regime, fne: nsp(f.fne),
      statut_dgi: nsp(f.statuts.DGI), statut_cnps: nsp(f.statuts.CNPS), statut_its: nsp(f.statuts.ITS), statut_cmu: nsp(f.statuts.CMU), statut_disa: nsp(f.statuts.DISA),
      tva_exoneree: f.tva === 'OUI', consentement_artci: !!f.consent
    };
  }
  launch() {
    if (this.state.busy) return;
    this.setState({ busy: true });
    let files = this.fileObjs;
    if (API.config.mock && !Object.keys(files).length) files = {};
    API.createDiagnostic(this.payload(), files).then((r) => {
      const id = r.job_id || r.id;
      this.clearDraft();
      this.setState({ busy: false, screen: 'suivi', stage: 0, logs: [], pct: 0, elapsed: 0, failed: false, done: false, job: r.reference || id, jobId: id }, () => this.follow(id));
      this.refreshDiags();
    }).catch((e) => {
      this.setState({ busy: false });
      if (e.status === 401) this.expire();
      else if (e.status === 413) this.toast('bad', 'Fichiers trop lourds', '20 Mo maximum par fichier.');
      else if (e.status === 429) this.toast('bad', 'Trop de dépôts', 'Limite de 5 dépôts par heure atteinte.');
      else this.fail(e, 'Envoi impossible');
    });
  }
  follow(id) {
    if (this.unsub) this.unsub();
    this.clearT();
    const start = Date.now();
    const iv = setInterval(() => { if (!this.state.failed && !this.state.done) this.setState({ elapsed: Date.now() - start }); }, 250);
    this.timers.push(iv);
    const t0 = () => this.fmtT((Date.now() - start) / 1000);
    this.unsub = API.subscribe(id, (ev) => {
      const kind = ev.niveau === 'erreur' || ev.etape === 'ERREUR' ? 'bad' : ev.niveau === 'ok' || ev.etape === 'ENVOYE' ? 'ok' : '';
      this.setState((s) => {
        const patch = { logs: ev.message ? [...s.logs, { id: s.logs.length + 1, time: t0(), text: ev.message, kind }] : s.logs };
        if (ev.progression != null) patch.pct = Math.max(s.pct, Math.min(100, Math.round(ev.progression)));
        if (this.K.ETAPE[ev.etape] != null) patch.stage = Math.max(s.stage, this.K.ETAPE[ev.etape]);
        return patch;
      });
      if (ev.etape === 'ERREUR') {
        this.setState({ failed: true, failMsg: ev.message || '' });
        this.toast('bad', 'Diagnostic interrompu', 'Un conseiller va vous rappeler.');
        this.refreshDiags();
      }
      if (ev.etape === 'ENVOYE') {
        this.setState({ done: true, pct: 100, stage: 5 });
        API.getDiagnostic(id).then((d) => {
          this.later(() => { this.setState({ screen: 'resultat', result: d, confetti: true, slot: d && d.entretien }); this.finish(d); }, 900);
          this.refreshDiags();
        }).catch((e) => this.fail(e, 'Résultat indisponible'));
      }
    });
  }
  finish(d) {
    const P = (d && d.perf) || 0, C = (d && d.conf) || 0;
    let p = 0, c = 0;
    const iv = setInterval(() => {
      p = Math.min(P, p + Math.max(1, Math.ceil(P / 50))); c = Math.min(C, c + Math.max(1, Math.ceil(C / 40)));
      this.setState({ count: { p, c } });
      if (p >= P && c >= C) clearInterval(iv);
    }, 28);
    this.timers.push(iv);
    this.later(() => this.setState({ confetti: false }), 4600);
    this.later(() => this.toast('ok', 'Rapport envoyé', 'Aussi disponible dans Mes diagnostics.'), 600);
  }
  openPdf(id, ref) {
    this.setState({ dl: id });
    API.pdfUrl(id).then((r) => {
      this.setState({ dl: null });
      if (r && r.url) window.open(r.url, '_blank', 'noopener');
      this.toast('info', 'Diagnostic_DC-KNOWING_' + ref + (API.config.mock ? '.pdf' : '.docx'), API.config.mock ? 'Aperçu du rapport ouvert (mode démo).' : 'Lien sécurisé valable 7 jours.');
    }).catch((e) => { this.setState({ dl: null }); this.fail(e, 'Rapport indisponible'); });
  }
  goHomeNow() {
    this.clearT(); if (this.unsub) { this.unsub(); this.unsub = null; }
    this.setState((s) => ({ ...this.initial(), user: s.user, diags: s.diags, f: s.screen === 'form' ? s.f : { ...this.emptyForm(), nom: s.f.nom, tel: s.f.tel, jour: s.f.jour } }));
  }
  qrCells() {
    const n = 21, out = [];
    const finder = (r, c) => { const z = [[0, 0], [0, n - 7], [n - 7, 0]].find(([a, b]) => r >= a && r < a + 7 && c >= b && c < b + 7); if (!z) return null; const rr = r - z[0], cc = c - z[1]; return rr === 0 || rr === 6 || cc === 0 || cc === 6 || (rr >= 2 && rr <= 4 && cc >= 2 && cc <= 4); };
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) { const fd = finder(r, c); out.push((fd === null ? ((r * 7 + c * 13 + r * c) % 5 < 2) : fd) ? '#000000' : '#FFFFFF'); }
    return out;
  }
  openSlotFor(diagId) {
    this.setState((s) => ({ modal: 'slot', slotPick: s.slot, slotDiag: diagId, taken: [] }));
    API.slots(this.state.f.jour).then((r) => this.setState({ taken: (r && r.pris) || [] })).catch(() => {});
  }

  renderVals() {
    const s = this.state, f = s.f, K = this.K, err = s.err || {};
    const keys = ['raison', 'secteur', 'effectif', 'nom', 'tel', 'consent'];
    const cls = {}, inv = {}, noErr = {}, on = {};
    keys.forEach((k) => { cls[k] = err[k] ? 'in err' : 'in'; inv[k] = err[k] ? 'true' : 'false'; noErr[k] = !err[k]; });
    ['raison', 'ncc', 'secteur', 'effectif', 'nom', 'tel'].forEach((k) => { on[k] = (e) => this.setF(k, e.target.value); });
    const exempt = K.EXEMPT.includes(f.secteur);
    const seg = (key, list, onCls) => list.map(([v, l]) => ({ v, l, cls: f[key] === v ? 'seg ' + (onCls || 'on') : 'seg', pressed: f[key] === v ? 'true' : 'false', pick: () => this.setF(key, v) }));
    const statRows = K.OBL.map(([k, d]) => ({ k, d, aria: 'Statut ' + k, opts: K.STAT.map(([v, l]) => { const sel = f.statuts[k] === v; return { l, pressed: sel ? 'true' : 'false', cls: sel ? 'seg ' + (v === 'A_JOUR' ? 'on-ok' : v === 'EN_RETARD' ? 'on-bad' : 'on') : 'seg', pick: () => this.setStat(k, v) }; }) }));
    const steps = K.STEPS.map(([l], i) => { const n = i + 1; return { n, l, done: n < s.step, notDone: n >= s.step, cls: n < s.step ? 'done' : n === s.step ? 'cur' : '', dis: n > s.step, ac: n === s.step ? 'step' : undefined, sep: n < 6, sepColor: n < s.step ? '#000000' : '#E0E0E0', go: () => { if (n < s.step) this.setState({ step: n, err: {} }); } }; });
    const label = (list, v) => { const x = list.find((a) => a[0] === v); return x ? x[1] : '—'; };
    const fneLabel = f.fne === 'NON_APPLICABLE' ? 'Non applicable' : label(K.FNE, f.fne);
    const goStep = (n) => () => this.setState({ step: n, err: {} });
    const userEmail = (s.user && s.user.email) || '';
    const fileList = Object.values(s.files);
    const recap = [
      { t: 'Entreprise', edit: goStep(1), editL: 'Modifier l’entreprise', rows: [{ l: 'Raison sociale', v: f.raison || '—' }, { l: 'Secteur', v: f.secteur || '—' }, { l: 'Effectif', v: f.effectif ? f.effectif + ' salarié(s)' : '—' }] },
      { t: 'Vous', edit: goStep(2), editL: 'Modifier vos coordonnées', rows: [{ l: 'Nom', v: f.nom || '—' }, { l: 'E-mail', v: userEmail || '—' }, { l: 'Visite', v: label(K.JOUR, f.jour) }] },
      { t: 'Fiscalité', edit: goStep(3), editL: 'Modifier la fiscalité', rows: [{ l: 'Régime', v: label(K.REGIME, f.regime) }, { l: 'FNE', v: fneLabel }, { l: 'TVA exonérée', v: f.tva === 'OUI' ? 'Oui' : f.tva === 'NON' ? 'Non' : '—' }] },
      { t: 'Obligations', edit: goStep(4), editL: 'Modifier les obligations', rows: K.OBL.map(([k]) => ({ l: k, v: f.statuts[k] ? label(K.STAT, f.statuts[k]) : '—' })) },
      { t: 'Documents', edit: goStep(5), editL: 'Modifier les documents', rows: fileList.length ? fileList.map((x) => ({ l: x.ext.toUpperCase(), v: x.name })) : [{ l: 'Fichiers', v: 'Aucun' }] }
    ];
    const summary = [{ l: 'Entreprise', v: f.raison || '—' }, { l: 'Secteur', v: f.secteur || '—' }, { l: 'Visite', v: label(K.JOUR, f.jour) }, { l: 'E-mail', v: userEmail || '—' }, { l: 'Fichiers', v: fileList.length + ' / 3' }];
    const full = fileList.length >= 3;
    const docSlots = K.DOCS.map(([k, l, d, rec]) => {
      const x = s.files[k];
      const pick = (e) => { this.putFile(k, e.target.files && e.target.files[0]); e.target.value = ''; };
      return { k, l, d, rec, isFilled: !!x, isEmpty: !x && !full, isOff: !x && full, over: s.dragSlot === k ? 'over' : '', aria: 'Déposer : ' + l, replaceAria: 'Remplacer ' + l, removeAria: 'Retirer ' + l,
        name: x ? x.name : '', size: x ? this.fmtSize(x.size) : '', ext: x ? x.ext.toUpperCase() : '', pick,
        dragOver: (e) => { e.preventDefault(); if (s.dragSlot !== k) this.setState({ dragSlot: k }); },
        dragLeave: (e) => { e.preventDefault(); this.setState({ dragSlot: null }); },
        drop: (e) => { e.preventDefault(); this.putFile(k, e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]); },
        remove: () => { delete this.fileObjs[k]; this.setState((st) => { const files = { ...st.files }; delete files[k]; return { files }; }); } };
    });
    const remainMin = Math.max(1, Math.round((6 - s.step) * 0.5 + 0.5));

    const pct = s.done ? 100 : Math.min(99, s.pct);
    const running = s.screen === 'suivi' && !s.failed && !s.done;
    const stages = K.STAGES.map(([l, d], i) => {
      const st = s.failed && i === s.stage ? 'fail' : (i < s.stage || s.done) ? 'done' : i === s.stage ? 'cur' : 'todo';
      return { n: i + 1, l, d, isDone: st === 'done', isCur: st === 'cur', isFail: st === 'fail', isTodo: st === 'todo',
        border: st === 'cur' ? '#000000' : st === 'fail' ? '#DC2626' : st === 'done' ? '#15803D' : '#E0E0E0', bg: st === 'cur' ? '#FEF9E7' : st === 'fail' ? '#FEE2E2' : '#FFFFFF',
        dotBg: st === 'done' ? '#15803D' : st === 'cur' ? '#FFD700' : st === 'fail' ? '#DC2626' : '#EDEDED', dotFg: st === 'todo' ? '#525252' : st === 'cur' ? '#000000' : '#FFFFFF', anim: st === 'done' ? 'popin .4s both' : 'none' };
    });
    const elapsedSec = s.elapsed / 1000;
    const remainL = s.failed ? 'Traitement arrêté.' : s.done ? 'Terminé : ouverture de vos résultats…' : pct > 0 ? 'Temps restant estimé : environ ' + Math.max(1, Math.ceil((5.5 * 60 * (100 - pct) / 100) / 60)) + ' min' : 'Démarrage de l’analyse…';
    const logs = s.logs.map((l) => ({ ...l, color: l.kind === 'bad' ? '#FCA5A5' : l.kind === 'ok' ? '#86EFAC' : '#FFFFFF' }));
    const palette = ['#FFD700', '#000000', '#F2C200', '#FEF9E7', '#262626'];
    const confettiPieces = Array.from({ length: 34 }, (_, i) => ({ x: (i * 37 + 7) % 100, w: 6 + (i % 4) * 2, h: 10 + (i % 3) * 4, bg: palette[i % 5], d: ((i * 13) % 20) / 20 }));
    const slots = K.SLOTS.filter((t) => f.jour !== '2026-12-17' || t >= '10:30').map((t) => { const taken = (s.taken || []).includes(t); const sel = s.slotPick === t; return { t, dis: taken, cls: sel ? 'seg on-a' : 'seg', pressed: sel ? 'true' : 'false', aria: taken ? t + ', déjà réservé' : t, pick: () => this.setState({ slotPick: t }) }; });
    const toasts = s.toasts.map((t) => ({ ...t, cls: 't-' + t.kind, ok: t.kind === 'ok', bad: t.kind === 'bad', info: t.kind === 'info', close: () => this.setState((st) => ({ toasts: st.toasts.filter((x) => x.id !== t.id) })) }));
    const closeModal = () => this.setState({ modal: null });
    const a = s.auth;
    const intentDiag = a.intent === 'diag';
    const grade = (v) => (v >= 80 ? 'A' : v >= 60 ? 'B' : v >= 40 ? 'C' : 'D');
    const diagRows = s.diags.map((d) => ({ ...d, done: d.st === 'ENVOYE', running: d.st === 'EN_COURS', failed: d.st === 'ERREUR',
      stL: d.st === 'ENVOYE' ? 'Envoyé' : d.st === 'EN_COURS' ? 'En cours' : 'À rappeler', stCls: d.st === 'ENVOYE' ? 'b-ok' : d.st === 'EN_COURS' ? 'b-warn' : 'b-bad',
      gpL: d.perf != null ? d.perf + '/100 (' + (d.gp || grade(d.perf)) + ')' : '', gcL: d.conf != null ? d.conf + '/100 (' + (d.gc || grade(d.conf)) + ')' : '',
      perfAria: 'Performance ' + d.perf + ' sur 100', confAria: 'Conformité ' + d.conf + ' sur 100',
      entL: d.entretien ? 'entretien à ' + d.entretien : 'rapport envoyé par e-mail',
      openCls: s.dl === d.id ? 'loading' : '',
      open: () => this.openPdf(d.id, d.ref),
      download: () => this.openPdf(d.id, d.ref),
      follow: () => this.setState({ screen: 'suivi', job: d.ref, jobId: d.id, stage: 0, logs: [], pct: d.pct || 0, failed: false, done: false }, () => this.follow(d.id)) }));
    const nm = (f.nom || '').trim();
    const firstName = nm ? nm.split(' ')[0] : (userEmail ? userEmail.split('@')[0].split(/[._-]/)[0].replace(/^\w/, (c) => c.toUpperCase()) : '');
    const iv = K.INV[f.jour] || K.INV['2026-12-16'];
    const invi = { dayName: iv[0], dayNum: iv[1], time: iv[2], tone: iv[3], who: (nm || userEmail || 'Invité') + (f.raison ? ' · ' + f.raison : ''), code: 'JPO-DCK-2026-' + (userEmail.length * 7 % 900 + 100) };
    const progItems = (K.PROG[s.progDay] || []).map(([t, title, d, tag]) => ({ t, title, d, hasD: !!d, mine: tag === 'diag', dot: tag === 'diag' ? 'hl2' : '' }));
    const progDays = K.JOUR.map(([v, l]) => ({ l, cls: s.progDay === v ? 'daytab on' : 'daytab', pressed: s.progDay === v ? 'true' : 'false', pick: () => this.setState({ progDay: v }) }));
    const doneDiags = s.diags.filter((d) => d.st === 'ENVOYE');
    const r = s.result || {};
    const prioCls = { P1: 'b-bad', P2: 'b-dark', P3: 'b-acc', P4: '' };
    const modCls = (m) => { const x = (m || '').toLowerCase(); return x.includes('rh') ? 'p-rh' : x.includes('compta') ? 'p-compta' : x.includes('legal') ? 'p-legal' : x.includes('task') ? 'p-task' : 'p-sell'; };
    const res = {
      perf: r.perf || 0, conf: r.conf || 0, gp: r.gp || grade(r.perf || 0), gc: r.gc || grade(r.conf || 0),
      perfBadge: (r.perf || 0) < 40 ? 'Risque critique' : (r.perf || 0) < 60 ? 'Performance fragile' : 'Bonne performance',
      perfBadgeCls: (r.perf || 0) < 40 ? 'b-bad' : (r.perf || 0) < 60 ? 'b-warn' : 'b-ok',
      confBadge: r.alerte || ((r.conf || 0) < 50 ? 'Risque de redressement' : 'Conformité solide'),
      confBadgeCls: r.alerte || (r.conf || 0) < 50 ? 'b-bad' : 'b-ok',
      verdict: r.verdict || '', hasVerdict: !!r.verdict,
      bien: r.bien || [], traiter: r.traiter || [],
      actions: (r.plan || []).slice(0, 3).map((p) => ({ ...p, cls: prioCls[p.prio] || '', mod: modCls(p.module) })),
      planCount: (r.plan || []).length,
      perfAria: 'Score de performance ' + (r.perf || 0) + ' sur 100', confAria: 'Score de conformité ' + (r.conf || 0) + ' sur 100',
      indicatif: !!r.indicatif
    };

    return {
      show: { accueil: s.screen === 'accueil', auth: s.screen === 'auth', form: s.screen === 'form', suivi: s.screen === 'suivi', resultat: s.screen === 'resultat', espace: s.screen === 'espace', s1: s.step === 1, s2: s.step === 2, s3: s.step === 3, s4: s.step === 4, s5: s.step === 5, s6: s.step === 6 },
      m: { notice: s.modal === 'notice', quit: s.modal === 'quit', slot: s.modal === 'slot', programme: s.modal === 'programme', invitation: s.modal === 'invitation', logout: s.modal === 'logout', del: s.modal === 'del' },
      f, err, cls, inv, invi, noErr, on, exempt, notExempt: !exempt, steps, statRows, recap, summary, docSlots, stages, logs, pct, running, slots, toasts, confettiPieces, res,
      secteurs: K.SECTEURS, demo: API.config.mock,
      typeOpts: seg('typeClient', K.TYPE), jourOpts: seg('jour', K.JOUR, 'on-a'), regimeOpts: seg('regime', K.REGIME), fneOpts: seg('fne', K.FNE),
      tvaOpts: seg('tva', [['OUI', 'Oui, exonérée'], ['NON', 'Non']]),
      step: s.step, stepTitle: K.STEPS[s.step - 1][1], stepWhy: K.STEPS[s.step - 1][2], remainForm: s.step === 6 ? 'dernière étape' : 'encore environ ' + remainMin + ' min',
      stepPct: Math.round((s.step - 1) / 6 * 100 + 8),
      canBack: s.step > 1, noBack: s.step === 1, isLast: s.step === 6, notLast: s.step < 6,
      fileCount: fileList.length, fileCountCls: full ? 'b-acc' : fileList.length ? 'b-ok' : '',
      failed: s.failed, failMsg: s.failMsg || 'Votre diagnostic n’a pas pu être produit automatiquement.', allDone: s.done && s.screen === 'suivi', elapsedL: this.fmtT(elapsedSec), remainL,
      barCls: running ? 'stripes' : '', barColor: s.failed ? '#DC2626' : s.done ? '#15803D' : '#FFD700',
      job: s.job, entreprise: f.raison || 'Votre entreprise', emailShown: userEmail || 'votre adresse e-mail', telShown: f.tel || 'votre numéro',
      count: s.count, confetti: s.confetti,
      hasSlot: !!s.slot, noSlot: !s.slot, slotL: s.slot ? (K.JOURL[f.jour] || '') + ' à ' + s.slot : '', slotBtn: s.slot ? 'Changer de créneau' : 'Réserver mon entretien',
      jourL: K.JOURL[f.jour] || '', noPick: !s.slotPick, noDoneDiag: doneDiags.length === 0,
      launchCls: s.busy ? 'loading' : '', dlCls: s.dl ? 'loading' : '',
      quitTitle: 'Quitter le diagnostic ?', quitText: 'Votre brouillon est conservé sur cet appareil : vous pourrez reprendre plus tard depuis l’accueil.', quitBtn: 'Quitter',
      logged: !!s.user, headerCta: !s.user && (s.screen === 'accueil'), userEmail, initials: (firstName || userEmail || '?').slice(0, 2).toUpperCase(), firstName: firstName || 'et bienvenue',
      mesCls: s.screen === 'espace' ? 'navbtn on' : 'navbtn', mesCurrent: s.screen === 'espace' ? 'page' : undefined,
      hasDiags: s.diags.length > 0, noDiags: s.diags.length === 0 && !s.diagsLoading, diagsLoading: s.diagsLoading && !s.diags.length, diagCount: s.diags.length, diagCountL: s.diags.length ? s.diags.length + ' diagnostic' + (s.diags.length > 1 ? 's' : '') + ' · conservés 30 jours' : 'Aucun pour l’instant', diagRows,
      progItems, progDays, qr: this.qrCells(),
      auth: a, authEmailStep: a.step === 'email', authCodeStep: a.step === 'code',
      authKicker: intentDiag ? 'Diagnostic Check-up 360 · étape préalable' : 'Mes diagnostics',
      authTitle: intentDiag ? 'D’abord, votre e-mail' : 'Accéder à mes diagnostics',
      authLead: intentDiag ? 'Votre rapport sera envoyé à cette adresse et rangé dans « Mes diagnostics ». Un code suffit : pas de mot de passe.' : 'Retrouvez vos rapports, votre invitation et le programme. Saisissez l’e-mail utilisé lors de votre diagnostic.',
      authStep3: intentDiag ? 'Votre diagnostic en 6 étapes courtes' : 'Votre espace « Mes diagnostics »',
      authStep3d: intentDiag ? 'Environ 3 minutes, puis le rapport en 10 minutes maximum.' : 'Rapports, invitation, programme et entretien.',
      authBackL: 'Accueil',
      a1bg: a.step === 'email' ? '#FFD700' : '#000000', a1fg: a.step === 'email' ? '#000000' : '#FFFFFF', a2bg: a.step === 'code' ? '#FFD700' : '#FFFFFF', a2fg: a.step === 'code' ? '#000000' : '#525252',
      authMailCls: a.err && a.step === 'email' ? 'in err' : 'in', authMailInv: a.err && a.step === 'email' ? 'true' : 'false',
      otpCls: a.err && a.step === 'code' ? 'otp err' : 'otp', codeInv: a.err && a.step === 'code' ? 'true' : 'false', codeIncomplete: (a.code || '').length < 6 || a.busy,
      authBusyCls: a.busy ? 'loading' : '', resendWait: a.resendIn > 0, resendL: a.resendIn > 0 ? 'Renvoyer dans ' + a.resendIn + ' s' : 'Renvoyer le code',

      goHome: () => { if (s.screen === 'form' && f.raison) this.setState({ modal: 'quit' }); else this.goHomeNow(); },
      start: () => { if (s.user) this.newDiag(); else this.goAuth('diag'); },
      startFromModal: () => { if (s.user) this.newDiag(); else this.goAuth('diag'); },
      openEspace: () => { if (s.user) this.setState({ screen: 'espace', modal: null }); else this.goAuth('espace'); },
      openProgramme: () => this.setState({ modal: 'programme' }),
      openInvitation: () => this.setState({ modal: 'invitation' }),
      addCalendar: () => { if (API.config.mock) this.toast('ok', 'Ajouté à votre agenda', 'Fichier JPO-DC-KNOWING.ics (mode démo).'); else API.downloadIcs().then(() => this.toast('ok', 'Invitation téléchargée', 'Ouvrez JPO-DC-KNOWING.ics pour l’ajouter à votre agenda.')).catch((e) => this.fail(e, 'Agenda indisponible')); },
      sendInvitation: () => { API.sendInvitation().then(() => { this.setState({ modal: null }); this.toast('ok', 'Invitation envoyée', 'Consultez ' + userEmail); }).catch((e) => this.fail(e)); },
      askLogout: () => this.setState({ modal: 'logout' }),
      logout: () => { API.logout(); this.clearT(); this.setState({ ...this.initial() }); this.toast('info', 'Déconnecté', 'À bientôt aux Journées Portes Ouvertes.'); },
      askDelete: () => this.setState({ modal: 'del' }),
      confirmDelete: () => { API.deleteMyData().then(() => { this.clearT(); this.clearDraft(); this.setState({ ...this.initial() }); this.toast('ok', 'Données supprimées', 'Votre espace a été effacé.'); }).catch((e) => this.fail(e)); },
      authBack: () => this.goHomeNow(),
      onAuthEmail: (e) => { const v = e.target.value; this.setState((st) => ({ auth: { ...st.auth, email: v, err: null } })); },
      onCode: (e) => { const v = e.target.value.replace(/\D/g, '').slice(0, 6); this.setState((st) => ({ auth: { ...st.auth, code: v, err: null } })); },
      sendCode: (e) => { e.preventDefault(); this.sendCode(); },
      verifyCode: (e) => { e.preventDefault(); this.verifyCode(); },
      changeEmail: () => this.setState({ auth: { ...a, step: 'email', code: '', err: null } }),
      resend: () => { this.setState({ auth: { ...a, step: 'email' } }, () => this.sendCode()); },
      askQuit: () => this.setState({ modal: 'quit' }),
      confirmQuit: () => this.goHomeNow(),
      fillSample: () => { this.setState({ f: { ...this.sampleForm(), consent: f.consent }, err: {} }); this.toast('info', 'Exemple chargé', 'Dossier fictif pré-rempli.'); },
      sampleFiles: () => {
        const mk = (name, type, size) => { try { return new File([new Uint8Array(Math.min(size, 2048))], name, { type }); } catch (e) { return { name, size }; } };
        const list = { balance: mk('balance_generale_2025.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 1240000), cr: mk('compte_de_resultat_2025.pdf', 'application/pdf', 820000), bilan: mk('bilan_2025.pdf', 'application/pdf', 980000) };
        this.fileObjs = list;
        const sizes = { balance: 1240000, cr: 820000, bilan: 980000 };
        const files = {}; Object.keys(list).forEach((k) => { files[k] = { name: list[k].name, size: sizes[k], ext: list[k].name.split('.').pop() }; });
        this.setState((st) => ({ files, err: { ...st.err, files: null } }));
      },
      showSampleFiles: API.config.mock,
      allUpToDate: () => this.setState((st) => ({ f: { ...st.f, statuts: { DGI: 'A_JOUR', CNPS: 'A_JOUR', ITS: 'A_JOUR', CMU: 'A_JOUR', DISA: 'A_JOUR' } }, err: { ...st.err, statuts: null } })),
      back: () => this.setState((st) => ({ step: Math.max(1, st.step - 1), err: {} })),
      submitForm: (e) => { e.preventDefault(); this.next(); },
      toggleConsent: (e) => this.setState((st) => ({ f: { ...st.f, consent: e.target.checked }, err: { ...st.err, consent: null } })),
      openNotice: () => this.setState({ modal: 'notice' }),
      acceptNotice: () => this.setState((st) => ({ modal: null, f: { ...st.f, consent: true }, err: { ...st.err, consent: null } })),
      closeModal,
      ovClick: (e) => { if (e.target === e.currentTarget) closeModal(); },
      ovKey: (e) => { if (e.key === 'Escape') closeModal(); },
      retryFiles: () => { this.clearT(); if (this.unsub) this.unsub(); this.setState({ screen: 'form', step: 5, files: {}, failed: false, logs: [], elapsed: 0, stage: 0 }); this.fileObjs = {}; },
      callMe: () => { API.callback(s.jobId).then(() => this.toast('ok', 'Demande transmise', 'Un conseiller vous appelle dans les prochaines minutes.')).catch((e) => this.fail(e)); },
      openReport: () => this.openPdf(s.jobId, s.job),
      download: () => this.openPdf(s.jobId, s.job),
      openSlot: () => this.openSlotFor(s.jobId || (doneDiags[0] && doneDiags[0].id)),
      confirmSlot: () => {
        const t = s.slotPick, id = s.slotDiag || s.jobId || (doneDiags[0] && doneDiags[0].id);
        API.bookSlot(id, f.jour, t).then(() => { this.setState({ slot: t, modal: null }); this.toast('ok', 'Entretien confirmé', label(K.JOUR, f.jour) + ' à ' + t); this.refreshDiags(); }).catch((e) => this.fail(e, 'Réservation impossible'));
      },
      askRestart: () => this.newDiag()
    };
  }
}
