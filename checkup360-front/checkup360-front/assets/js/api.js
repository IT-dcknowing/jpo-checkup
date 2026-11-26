/*!
 * api.js — toute la communication avec la backend Check-up 360 passe par ce fichier.
 * Mode démo : window.CHECKUP_CONFIG.mock = true (aucun appel réseau, données fictives).
 * Mode réel : mock = false et apiBase pointe vers FastAPI (ex. "/api").
 * Les routes attendues sont décrites dans README.md (section « Contrat API »).
 */
(function () {
  'use strict';
  var cfg = Object.assign({ apiBase: '/api', mock: true, mockCode: '482913' }, window.CHECKUP_CONFIG || {});

  var store = {
    get: function (k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) {} }
  };
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  /* Jeton conseiller : sessionStorage (effacé à la fermeture de l'onglet), jamais localStorage. */
  var adminStore = {
    get: function (k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } },
    set: function (k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del: function (k) { try { sessionStorage.removeItem(k); localStorage.removeItem(k); } catch (e) {} }
  };
  try { localStorage.removeItem('ck_admin_token'); } catch (e) {} /* ancien emplacement */
  function adminToken() { return adminStore.get('ck_admin_token'); }

  /* ---------- Supabase : comptes visiteurs (code à 6 chiffres par e-mail) et dépôt des fichiers ---------- */
  var sb = (!cfg.mock && window.supabase && cfg.supabaseUrl && cfg.supabaseKey)
    ? window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseKey, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true } })
    : null;
  var sessionListeners = [];
  if (sb) {
    sb.auth.onAuthStateChange(function (event, session) {
      if (session && session.user) {
        var known = store.get('ck_session');
        store.set('ck_session', { email: session.user.email });
        if (!known || known.email !== session.user.email) sessionListeners.forEach(function (cb) { try { cb({ email: session.user.email }); } catch (e) {} });
      } else if (event === 'SIGNED_OUT') {
        store.del('ck_session');
      }
    });
  }
  /* Jeton de session à jour (supabase-js le renouvelle avant expiration) */
  function token() {
    if (cfg.mock) { var s = store.get('ck_session'); return Promise.resolve(s && s.token); }
    if (!sb) return Promise.resolve(null);
    return sb.auth.getSession().then(function (r) { return r.data && r.data.session ? r.data.session.access_token : null; });
  }

  /* Messages d'erreur Supabase en français */
  function frError(error, fallback) {
    var m = String((error && error.message) || '');
    var st = (error && error.status) || 0;
    var out = fallback || 'Une erreur est survenue. Réessayez dans un instant.';
    if (st === 429 || /rate limit|security purposes|too many/i.test(m)) { out = 'Trop de demandes. Patientez une minute avant de redemander un code.'; st = 429; }
    else if (/expired|invalid/i.test(m) && /token|otp|code/i.test(m)) { out = 'Code incorrect ou expiré.'; st = 401; }
    else if (/email.*invalid|invalid.*email|unable to validate email/i.test(m)) { out = 'Adresse e-mail invalide.'; st = 422; }
    else if (/signups not allowed/i.test(m)) out = 'Les inscriptions sont fermées pour le moment.';
    else if (/payload too large|exceeded the maximum allowed size|too large/i.test(m)) { out = 'Fichier trop lourd : 20 Mo maximum.'; st = 413; }
    else if (/mime type|not supported/i.test(m)) { out = 'Format de fichier non accepté (CSV, XLSX ou PDF).'; st = 422; }
    else if (/failed to fetch|network/i.test(m)) out = 'Connexion impossible. Vérifiez votre accès à Internet.';
    else if (/email.*not.*confirm/i.test(m)) out = 'Adresse e-mail non confirmée : saisissez le code reçu.';
    var err = new Error(out); err.status = st; err.cause = error; return err;
  }

  function req(method, path, body, opts) {
    opts = opts || {};
    return (opts.admin ? Promise.resolve(adminToken()) : token()).then(function (t) {
      var headers = { Accept: 'application/json' };
      if (t) headers.Authorization = 'Bearer ' + t;
      var payload = body;
      if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
      return fetch(cfg.apiBase + path, { method: method, headers: headers, body: payload, credentials: 'same-origin', keepalive: !!opts.keepalive });
    }).then(function (r) {
      return r.text().then(function (txt) {
        var data = {};
        try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = { detail: txt }; }
        if (!r.ok) {
          var msg = (data && (data.detail && (typeof data.detail === 'string' ? data.detail : data.detail[0] && data.detail[0].msg))) || data.message || ('Erreur ' + r.status);
          var err = new Error(msg); err.status = r.status; err.data = data; throw err;
        }
        if (r.status === 202) data._status = 202;
        return data;
      });
    });
  }

  /* Téléchargement d'un fichier protégé par jeton : une navigation directe n'enverrait pas l'en-tête Authorization. */
  function downloadBlob(path, filename, admin) {
    return (admin ? Promise.resolve(adminToken()) : token()).then(function (t) {
      var headers = {};
      if (t) headers.Authorization = 'Bearer ' + t;
      return fetch(cfg.apiBase + path, { headers: headers, credentials: 'same-origin' });
    }).then(function (r) {
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
      entretien: d.entretien || null,
      emailEnvoye: d.email_envoye !== false,
      rapportDispo: d.rapport_disponible !== false
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
    /* Appelé quand une session s'ouvre hors de l'écran de code (lien reçu par e-mail, autre onglet) */
    onSession: function (cb) { sessionListeners.push(cb); },
    /* Erreur renvoyée par un lien d'e-mail (#error_description=...), en français ; null sinon */
    linkError: function () {
      try {
        var h = new URLSearchParams((location.hash || '').replace(/^#/, '') || location.search);
        if (!h.get('error') && !h.get('error_code')) return null;
        history.replaceState(null, '', location.pathname);
        var code = h.get('error_code') || '';
        return code === 'otp_expired' ? 'Ce lien a expiré ou a déjà servi. Demandez un nouveau code.' : 'Ce lien n’est pas valide. Demandez un nouveau code.';
      } catch (e) { return null; }
    },

    /* -- Connexion visiteur par code e-mail -- */
    requestCode: function (email) {
      if (cfg.mock) return sleep(700).then(function () { return { ok: true, expires_in: 600, resend_after: 30 }; });
      if (!sb) return Promise.reject(new Error('Configuration Supabase absente (config.js).'));
      return sb.auth.signInWithOtp({ email: email, options: { shouldCreateUser: true, emailRedirectTo: location.origin + '/' } }).then(function (r) {
        if (r.error) throw frError(r.error, 'Impossible d’envoyer le code pour le moment.');
        return { expires_in: 600, resend_after: 60 };
      });
    },
    verifyCode: function (email, code) {
      if (cfg.mock) {
        return sleep(600).then(function () {
          if (code !== cfg.mockCode) { var e = new Error('Code incorrect'); e.status = 401; e.data = { essais_restants: null }; throw e; }
          var s = { email: email, token: 'demo-token' }; store.set('ck_session', s); return s;
        });
      }
      return sb.auth.verifyOtp({ email: email, token: code, type: 'email' }).then(function (r) {
        if (r.error) { var e = frError(r.error, 'Code incorrect.'); e.status = e.status === 429 ? 429 : 401; e.data = { essais_restants: null }; throw e; }
        var s = { email: r.data.user.email }; store.set('ck_session', s); return s;
      });
    },
    logout: function () {
      if (sb) sb.auth.signOut().catch(function () {});
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
      /* 1. l'API vérifie formats, tailles et quotas AVANT tout envoi, crée les lignes documents et remet un jeton signé
         2. chaque fichier part dans Supabase Storage avec son jeton (aucun autre dépôt n'est permis aux visiteurs)
         3. l'API crée le diagnostic à partir du formulaire et des identifiants de documents */
      if (!sb) return Promise.reject(new Error('Configuration Supabase absente (config.js).'));
      var slots = Object.keys(files || {}).filter(function (k) { return files[k]; });
      var demande = slots.map(function (slot) { var f = files[slot]; return { nom: f.name.slice(0, 200), taille: f.size, categorie: slot.split(':')[0] }; });
      return req('POST', '/fichiers', { fichiers: demande }).then(function (r) {
        return Promise.all((r.fichiers || []).map(function (a, i) {
          var f = files[slots[i]];
          return sb.storage.from('documents').uploadToSignedUrl(a.chemin, a.jeton, f, { contentType: a.type }).then(function (u) {
            if (u.error) throw frError(u.error, 'Envoi du fichier « ' + f.name + ' » impossible.');
            return { id: a.id, type: slots[i].split(':')[0] };
          });
        }));
      }).then(function (docs) {
        return req('POST', '/diagnostics', Object.assign({}, form, { documents: docs }));
      });
    },
    /* Lance le traitement (requête longue, jusqu'à ~5 min) et suit l'avancement en relisant le journal toutes les 1,5 s.
       onEvent({etape, progression, message, niveau}) ; retourne une fonction d'arrêt. */
    subscribe: function (id, onEvent) {
      if (cfg.mock) return mockStream(id, onEvent);
      var stopped = false, next = 0, timer = null, fails = 0, debut = Date.now();
      var MAX_MS = 8 * 60 * 1000; /* au-delà, la fonction serveur (5 min) est forcément arrêtée */
      var base = '/diagnostics/' + encodeURIComponent(id);
      req('POST', base + '/traiter', null, { keepalive: false }).catch(function () { /* le suivi continue : l'état est en base */ });
      function poll() {
        if (stopped) return;
        req('GET', base + '/journal?depuis=' + next).then(function (d) {
          fails = 0;
          (d.evenements || []).forEach(function (ev) {
            onEvent({ etape: ev.etape, progression: ev.progression, message: ev.message || '', niveau: ev.niveau || '' });
          });
          next = d.suivant != null ? d.suivant : next;
          if (d.termine) { stopped = true; return; }
          if (Date.now() - debut > MAX_MS) {
            stopped = true;
            onEvent({ etape: 'ERREUR', progression: 0, niveau: 'erreur', message: 'Le traitement prend plus de temps que prévu. Un conseiller DC-KNOWING vous rappelle ; votre dossier reste visible dans Mes diagnostics.' });
            return;
          }
          timer = setTimeout(poll, 1500);
        }).catch(function (e) {
          if (e.status === 401 || e.status === 404) { stopped = true; onEvent({ etape: 'ERREUR', progression: 0, message: e.message, niveau: 'erreur' }); return; }
          fails += 1;
          timer = setTimeout(poll, Math.min(10000, 1500 * (fails + 1)));
        });
      }
      timer = setTimeout(poll, 600);
      return function () { stopped = true; if (timer) clearTimeout(timer); };
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
      return req('DELETE', '/me').then(function (r) { store.del('ck_session'); if (sb) sb.auth.signOut({ scope: 'local' }).catch(function () {}); return r; });
    },
    callback: function (id) {
      if (cfg.mock) return sleep(400).then(function () { return { ok: true }; });
      return req('POST', '/diagnostics/' + encodeURIComponent(id) + '/rappel');
    },

    /* -- Espace conseiller (JWT) -- */
    admin: {
      login: function (email, password, code) {
        if (cfg.mock) return sleep(800).then(function () { adminStore.set('ck_admin_token', 'demo-admin'); return { ok: true }; });
        var body = { email: email, password: password };
        if (code) body.code = code;
        return req('POST', '/admin/login', body).then(function (d) { adminStore.set('ck_admin_token', d.token || d.access_token); adminStore.set('ck_admin_role', d.role || 'conseiller'); return d; });
      },
      logout: function () { adminStore.del('ck_admin_token'); adminStore.del('ck_admin_role'); },
      role: function () { return adminStore.get('ck_admin_role') || 'conseiller'; },
      isLogged: function () { return !!adminToken(); },
      list: function (jour) { return req('GET', '/admin/diagnostics?jour=' + encodeURIComponent(jour), null, { admin: true }); },
      resend: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/renvoyer', null, { admin: true }); },
      retry: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/relancer', null, { admin: true }); },
      markInterview: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/entretien-realise', null, { admin: true }); },
      markCalled: function (id) { return req('POST', '/admin/diagnostics/' + encodeURIComponent(id) + '/rappel-effectue', null, { admin: true }); },
      pdf: function (id) { return req('GET', '/admin/diagnostics/' + encodeURIComponent(id) + '/pdf', null, { admin: true }); },
      detail: function (id) { return req('GET', '/admin/diagnostics/' + encodeURIComponent(id), null, { admin: true }); },
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
