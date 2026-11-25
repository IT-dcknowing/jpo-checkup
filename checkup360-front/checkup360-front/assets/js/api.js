/*!
 * api.js — toute la communication avec la backend Check-up 360 passe par ce fichier.
 * Mode démo : window.CHECKUP_CONFIG.mock = true (aucun appel réseau, données fictives).
 * Mode réel : mock = false et apiBase pointe vers FastAPI (ex. "/api").
 * Les routes attendues sont décrites dans README.md (section « Contrat API »).
 */
(function () {
  'use strict';
  var cfg = Object.assign({ apiBase: '/api', mock: true, mockCode: '482913', sseWithToken: true }, window.CHECKUP_CONFIG || {});

  var store = {
    get: function (k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) {} }
  };
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function token() { var s = store.get('ck_session'); return s && s.token; }
  function adminToken() { return store.get('ck_admin_token'); }

  function req(method, path, body, opts) {
    opts = opts || {};
    var headers = { Accept: 'application/json' };
    var t = opts.admin ? adminToken() : token();
    if (t) headers.Authorization = 'Bearer ' + t;
    var payload = body;
    if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    return fetch(cfg.apiBase + path, { method: method, headers: headers, body: payload, credentials: 'include' }).then(function (r) {
      return r.text().then(function (txt) {
        var data = {};
        try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { detail: txt }; }
        if (!r.ok) {
          var msg = (data && (data.detail && (typeof data.detail === 'string' ? data.detail : data.detail[0] && data.detail[0].msg))) || data.message || ('Erreur ' + r.status);
          var err = new Error(msg); err.status = r.status; err.data = data; throw err;
        }
        return data;
      });
    });
  }

  /* Téléchargement d'un fichier protégé par jeton : une navigation directe n'enverrait pas l'en-tête Authorization. */
  function downloadBlob(path, filename, admin) {
    var headers = {}, t = admin ? adminToken() : token();
    if (t) headers.Authorization = 'Bearer ' + t;
    return fetch(cfg.apiBase + path, { headers: headers, credentials: 'include' }).then(function (r) {
      if (!r.ok) return r.text().then(function (txt) {
        var d = {}; try { d = JSON.parse(txt); } catch (e) {}
        var err = new Error((d && typeof d.detail === 'string' && d.detail) || 'Erreur ' + r.status); err.status = r.status; throw err;
      });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    });
  }

  /* Mise en forme commune d'un diagnostic (la backend peut renvoyer ses propres noms de champs). */
  function normalize(d) {
    if (!d) return d;
    var st = d.statut || d.status || 'EN_ATTENTE';
    var etat = st === 'ENVOYE' ? 'ENVOYE' : st === 'ERREUR' ? 'ERREUR' : 'EN_COURS';
    return {
      id: d.id || d.job_id,
      ref: d.reference || d.ref || d.job_id || d.id,
      ent: d.entreprise || d.raison_sociale || d.ent || 'Votre entreprise',
      date: d.date_affichee || d.date || (d.created_at ? new Date(d.created_at).toLocaleString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''),
      st: etat,
      statut: st,
      pct: d.progression != null ? d.progression : etat === 'ENVOYE' ? 100 : 0,
      perf: d.score_performance != null ? d.score_performance : d.perf,
      conf: d.score_conformite != null ? d.score_conformite : d.conf,
      gp: d.grade_performance || d.gp,
      gc: d.grade_conformite || d.gc,
      couverture: d.couverture_pct != null ? d.couverture_pct : d.couverture,
      indicatif: !!d.indicatif,
      verdict: d.verdict || '',
      bien: d.points_forts || d.bien || [],
      traiter: d.points_a_traiter || d.traiter || [],
      plan: (d.plan_action || d.plan || []).map(function (p) {
        return { prio: p.priorite || p.prio, action: p.action, delai: p.delai, module: p.module || p.outil };
      }),
      alerte: d.alerte_principale || d.alerte || '',
      entretien: d.entretien || null
    };
  }

  /* ------------------------------------------------------------------ */
  /* Données de démonstration                                            */
  /* ------------------------------------------------------------------ */
  var GOLDEN = {
    score_performance: 50, grade_performance: 'C', score_conformite: 75, grade_conformite: 'B', couverture_pct: 95,
    verdict: 'Une activité en croissance (+5,0 %), mais une trésorerie négative et une FNE non activée à traiter dans les 30 jours.',
    points_forts: ['Chiffre d’affaires en hausse de 5,0 % (420 M FCFA)', 'Déclarations DGI et cotisations CNPS à jour', 'Régime fiscal cohérent avec votre chiffre d’affaires', 'Endettement maîtrisé (45 % des capitaux propres)'],
    points_a_traiter: ['FNE non activée', 'Trésorerie nette négative (−12,5 M FCFA)', 'Clients payés en 78 jours (repère 60)', 'CMU déclarée en retard'],
    alerte_principale: 'FNE à activer',
    plan_action: [
      { priorite: 'P1', action: 'Activer la facture normalisée électronique (FNE)', delai: 'sous 30 jours', module: 'Flow Sell' },
      { priorite: 'P2', action: 'Régulariser la CMU', delai: 'sous 30 jours', module: 'Flow RH' },
      { priorite: 'P2', action: 'Plan de trésorerie à 13 semaines', delai: 'sous 30 jours', module: 'Flow Compta' },
      { priorite: 'P2', action: 'Ramener le délai clients à 60 jours (+24,4 M)', delai: '3 à 6 mois', module: 'Flow Sell' },
      { priorite: 'P3', action: 'Allonger le délai fournisseurs à 45 jours (+10,2 M)', delai: '3 à 6 mois', module: 'Flow Compta' },
      { priorite: 'P3', action: 'Gagner 2 points de marge brute (+8,4 M d’EBE/an)', delai: '6 à 12 mois', module: 'Flow Sell' }
    ]
  };
  function mockList() { return store.get('ck_mock_diags') || []; }
  function mockSave(list) { store.set('ck_mock_diags', list); }
  function mockUpdate(id, patch) { mockSave(mockList().map(function (d) { return d.id === id ? Object.assign({}, d, patch) : d; })); }
  var mockScenario = function () { try { return new URLSearchParams(location.search).get('scenario') || 'succes'; } catch (e) { return 'succes'; } };

  /* ------------------------------------------------------------------ */
  /* API publique                                                        */
  /* ------------------------------------------------------------------ */
  var API = {
    config: cfg,
    normalize: normalize,
    session: function () { return store.get('ck_session'); },

    /* -- Connexion visiteur par code e-mail -- */
    requestCode: function (email) {
      if (cfg.mock) return sleep(700).then(function () { return { ok: true, expires_in: 600, resend_after: 30 }; });
      return req('POST', '/auth/code', { email: email });
    },
    verifyCode: function (email, code) {
      if (cfg.mock) {
        return sleep(600).then(function () {
          if (code !== cfg.mockCode) { var e = new Error('Code incorrect'); e.status = 401; e.data = { essais_restants: null }; throw e; }
          var s = { email: email, token: 'demo-token' }; store.set('ck_session', s); return s;
        });
      }
      return req('POST', '/auth/verify', { email: email, code: code }).then(function (d) {
        var s = { email: d.email || email, token: d.token || d.access_token }; store.set('ck_session', s); return s;
      });
    },
    logout: function () {
      if (!cfg.mock) req('POST', '/auth/logout').catch(function () {});
      store.del('ck_session');
    },

    /* -- Mes diagnostics -- */
    myDiagnostics: function () {
      if (cfg.mock) return sleep(200).then(function () { return mockList().map(normalize); });
      return req('GET', '/me/diagnostics').then(function (d) { return (d.items || d || []).map(normalize); });
    },
    getDiagnostic: function (id) {
      if (cfg.mock) return sleep(150).then(function () { return normalize(mockList().find(function (d) { return d.id === id; })); });
      return req('GET', '/diagnostics/' + encodeURIComponent(id)).then(normalize);
    },
    /* form : objet des champs du formulaire ; files : { balance: File, cr: File, bilan: File, autre: File } */
    createDiagnostic: function (form, files) {
      if (cfg.mock) {
        return sleep(800).then(function () {
          var code = { '2026-12-16': '1612', '2026-12-17': '1712', '2026-12-18': '1812' }[form.jour_evenement] || '1612';
          var id = 'JPO-' + code + '-0' + (25 + Math.floor(Math.random() * 60));
          var list = mockList().filter(function (d) { return d.id !== id; });
          list.unshift({ id: id, reference: id, entreprise: form.raison_sociale, date: 'À l’instant', statut: 'EN_ATTENTE', progression: 0 });
          mockSave(list);
          return { job_id: id, reference: id };
        });
      }
      var fd = new FormData();
      Object.keys(form).forEach(function (k) {
        var v = form[k];
        if (v === undefined || v === null) return;
        fd.append(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
      });
      Object.keys(files || {}).forEach(function (slot) { if (files[slot]) fd.append('fichiers', files[slot], files[slot].name); fd.append('types_fichiers', slot); });
      return req('POST', '/diagnostics', fd);
    },
    /* Abonnement au flux d'avancement (SSE). onEvent({etape, progression, message, niveau}) ; retourne une fonction d'arrêt. */
    subscribe: function (id, onEvent) {
      if (cfg.mock) return mockStream(id, onEvent);
      var url = cfg.apiBase + '/diagnostics/' + encodeURIComponent(id) + '/sse';
      if (cfg.sseWithToken && token()) url += '?token=' + encodeURIComponent(token());
      var es = new EventSource(url, { withCredentials: true });
      var handle = function (ev) {
        var d = {};
        try { d = JSON.parse(ev.data); } catch (e) { d = { message: ev.data }; }
        onEvent({ etape: d.etape || d.statut || ev.type.toUpperCase(), progression: d.progression, message: d.message || '', niveau: d.niveau || '' });
        if (d.etape === 'ENVOYE' || d.etape === 'ERREUR' || d.statut === 'ENVOYE' || d.statut === 'ERREUR') es.close();
      };
      es.onmessage = handle;
      ['etape', 'log', 'progression', 'statut'].forEach(function (t) { es.addEventListener(t, handle); });
      es.onerror = function () { /* le navigateur se reconnecte seul ; on ferme si terminé */ };
      return function () { es.close(); };
    },
    pdfUrl: function (id) {
      if (cfg.mock) return Promise.resolve({ url: 'rapport.html' });
      return req('GET', '/diagnostics/' + encodeURIComponent(id) + '/pdf');
    },

    /* -- Entretien et invitation -- */
    slots: function (jour) {
      if (cfg.mock) return sleep(150).then(function () { return { pris: ['09:30', '10:30', '14:00', '15:30'] }; });
      return req('GET', '/entretiens/creneaux?jour=' + encodeURIComponent(jour));
    },
    bookSlot: function (id, jour, heure) {
      if (cfg.mock) return sleep(500).then(function () { mockUpdate(id, { entretien: heure }); return { ok: true }; });
      return req('POST', '/diagnostics/' + encodeURIComponent(id) + '/entretien', { jour: jour, heure: heure });
    },
    sendInvitation: function () {
      if (cfg.mock) return sleep(500).then(function () { return { ok: true }; });
      return req('POST', '/me/invitation/envoi');
    },
    icsUrl: function () { return cfg.mock ? null : cfg.apiBase + '/me/invitation.ics'; },
    downloadIcs: function () { return downloadBlob('/me/invitation.ics', 'JPO-DC-KNOWING.ics', false); },
    deleteMyData: function () {
      if (cfg.mock) return sleep(600).then(function () { store.del('ck_mock_diags'); store.del('ck_session'); return { ok: true }; });
      return req('DELETE', '/me').then(function (r) { store.del('ck_session'); return r; });
    },
    callback: function (id) {
      if (cfg.mock) return sleep(400).then(function () { return { ok: true }; });
      return req('POST', '/diagnostics/' + encodeURIComponent(id) + '/rappel');
    },

    /* -- Espace conseiller (JWT) -- */
    admin: {
      login: function (email, password) {
        if (cfg.mock) return sleep(800).then(function () { store.set('ck_admin_token', 'demo-admin'); return { ok: true }; });
        return req('POST', '/admin/login', { email: email, password: password }).then(function (d) { store.set('ck_admin_token', d.token || d.access_token); return d; });
      },
      logout: function () { store.del('ck_admin_token'); },
      isLogged: function () { return !!adminToken(); },
      list: function (jour) { return req('GET', '/admin/diagnostics?jour=' + encodeURIComponent(jour), null, { admin: true }); },
      resend: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/renvoyer', null, { admin: true }); },
      retry: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/relancer', null, { admin: true }); },
      markInterview: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/entretien-realise', null, { admin: true }); },
      markCalled: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/rappel-effectue', null, { admin: true }); },
      pdf: function (id) { return req('GET', '/admin/diagnostics/' + encodeURIComponent(id) + '/pdf', null, { admin: true }); },
      exportUrl: function (jour) { return cfg.apiBase + '/admin/diagnostics.csv?jour=' + encodeURIComponent(jour); },
      exportCsv: function (jour) { return downloadBlob('/admin/diagnostics.csv?jour=' + encodeURIComponent(jour), 'diagnostics_' + jour + '.csv', true); }
    }
  };

  /* Flux simulé : mêmes étapes que la backend (LECTURE → MAPPING_IA → CALCUL → INTERPRETATION → PDF → ENVOYE). */
  function mockStream(id, onEvent) {
    var timers = [];
    var fail = mockScenario() === 'erreur';
    var at = function (ms, ev) { timers.push(setTimeout(function () {
      mockUpdate(id, { statut: ev.etape, progression: ev.progression });
      if (ev.etape === 'ENVOYE') mockUpdate(id, Object.assign({}, GOLDEN, { date: '16 déc. 2026 · à l’instant' }));
      onEvent(ev);
    }, ms)); };
    at(300, { etape: 'LECTURE', progression: 3, message: 'Fichiers reçus · empreinte calculée' });
    at(1200, { etape: 'LECTURE', progression: 9, message: 'Lecture en environnement isolé · encodage détecté UTF-8' });
    if (fail) {
      at(2200, { etape: 'LECTURE', progression: 16, message: 'Document numérisé : passage en OCR' });
      at(3600, { etape: 'LECTURE', progression: 22, message: 'OCR : confiance globale 0,24 (seuil 0,30)', niveau: 'erreur' });
      at(4100, { etape: 'ERREUR', progression: 22, message: 'Rapport automatique annulé · un conseiller vous rappelle', niveau: 'erreur' });
    } else {
      at(2200, { etape: 'LECTURE', progression: 17, message: 'Texte extrait, totaux par classe repérés' });
      at(3000, { etape: 'MAPPING_IA', progression: 24, message: 'Anonymisation : noms, NCC, RIB et téléphones remplacés' });
      at(4200, { etape: 'MAPPING_IA', progression: 34, message: 'Extraction IA : confiance globale 0,91' });
      at(5300, { etape: 'MAPPING_IA', progression: 43, message: 'Contrôles : bilan équilibré · données exploitées 95 %' });
      at(6300, { etape: 'CALCUL', progression: 51, message: '8 indicateurs calculés (SYSCOHADA révisé)' });
      at(7200, { etape: 'CALCUL', progression: 58, message: 'Performance 50/100 (C) · Conformité 75/100 (B)' });
      at(8000, { etape: 'CALCUL', progression: 65, message: 'Plan d’action : 6 actions priorisées' });
      at(8900, { etape: 'INTERPRETATION', progression: 72, message: 'Interprétation rédigée · chaque nombre vérifié' });
      at(10100, { etape: 'PDF', progression: 82, message: 'PDF généré : 3 pages · polices embarquées' });
      at(11200, { etape: 'PDF', progression: 92, message: 'E-mail en cours d’envoi' });
      at(12400, { etape: 'ENVOYE', progression: 100, message: 'E-mail envoyé · ajouté à Mes diagnostics', niveau: 'ok' });
    }
    return function () { timers.forEach(clearTimeout); };
  }

  window.CheckupAPI = API;
})();
