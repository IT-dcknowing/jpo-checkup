/*!
 * sons.js — effets sonores du site visiteur (Web Audio, sans dépendance).
 * Un élément portant data-son="identifiant" joue ce son au clic.
 * Tout échec (fichier absent, Web Audio indisponible) reste silencieux : le son n'est jamais bloquant.
 */
(function () {
  'use strict';

  var SONS = {
    clic:         { src: 'click_003',        gain: 0.30 },
    code_ok:      { src: 'confirmation_001', gain: 0.35 },
    etape:        { src: 'confirmation_002', gain: 0.35, echelle: [1, 1.122, 1.26, 1.498, 1.682], secours: 'etape' },
    lancer:       { src: 'maximize_006', gain: 0.40, secours: 'lancer' },
    fichier_ok:   { src: 'tick_001',         gain: 0.25, antiRafaleMs: 150 },
    etape_calcul: { src: 'glass_001',        gain: 0.22, echelle: [1, 1.122, 1.26, 1.498] },
    pret:         { src: 'confirmation_003', gain: 0.40, eclat: [['glass_002', 0], ['glass_004', 120], ['glass_006', 260]], eclatGain: 0.18, secours: 'confetti' },
    rdv_ok:       { src: 'confirmation_001', gain: 0.35 },
    son_on:       { src: 'switch_001',       gain: 0.25 },
    erreur:       { synth: 'erreur_douce',   gain: 0.20 },
    echec:        { synth: 'erreur_douce',   gain: 0.18 }
  };

  var ECART_MIN_MS = 80;
  var VOIX_MAX = 2;
  var ATTENTE_CHARGEMENT_MAX_MS = 400;
  var CLE_ETAT = 'jpo_son';
  var CLE_BLOCAGE = 'jpo_son_bloque';

  var script = document.currentScript;
  var BASE = script ? new URL('../audio/', script.src).href : 'assets/audio/';

  var ctx = null;
  var sortie = null;
  var volume = 1;
  var muet = false;
  var bloque = false; // ?son=0 : coupé pour toute la session, le bouton ne peut pas réactiver
  var tampons = {};   // nom de fichier -> Promise<AudioBuffer|null>
  var voix = [];      // sons en cours : { id, sources, fin }
  var dernierDepart = 0;
  var dernierParId = {};

  function lire(stockage, cle) { try { return window[stockage].getItem(cle); } catch (e) { return null; } }
  function ecrire(stockage, cle, v) {
    try { if (v === null) window[stockage].removeItem(cle); else window[stockage].setItem(cle, v); } catch (e) { /* stockage indisponible : préférence non mémorisée */ }
  }

  function init(options) {
    var param = new URLSearchParams(window.location.search).get('son');
    if (param === '0') ecrire('sessionStorage', CLE_BLOCAGE, '1');
    if (param === '1') { ecrire('sessionStorage', CLE_BLOCAGE, null); ecrire('localStorage', CLE_ETAT, 'on'); }
    bloque = param === '0' || lire('sessionStorage', CLE_BLOCAGE) === '1';
    var memorise = lire('localStorage', CLE_ETAT);
    muet = param === '1' ? false : bloque || (memorise ? memorise === 'off' : !!(options && options.muet));
  }

  /* À appeler dans un geste de l'utilisateur (politique de lecture automatique). */
  function deverrouiller() {
    if (muet) return;
    try {
      if (!ctx) {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        ctx = new AC();
        sortie = ctx.createGain();
        sortie.gain.value = volume;
        sortie.connect(ctx.destination);
        Object.keys(SONS).forEach(function (id) {
          var s = SONS[id];
          if (s.src) charger(s.src);
          (s.eclat || []).forEach(function (e) { charger(e[0]); });
        });
      }
      if (ctx.state !== 'running' && !document.hidden) ctx.resume().catch(function () {});
    } catch (e) { ctx = null; }
  }

  function decoder(donnees) {
    return new Promise(function (ok, ko) { ctx.decodeAudioData(donnees, ok, ko); });
  }

  function charger(nom) {
    if (!tampons[nom]) {
      tampons[nom] = fetch(BASE + nom + '.mp3')
        .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.arrayBuffer(); })
        .then(decoder)
        .catch(function () { delete tampons[nom]; return null; });
    }
    return tampons[nom];
  }

  function source(voixCourante, tampon, gain, vitesse, retardS) {
    var s = ctx.createBufferSource();
    var g = ctx.createGain();
    var depart = ctx.currentTime + (retardS || 0);
    s.buffer = tampon;
    s.playbackRate.value = vitesse || 1;
    g.gain.value = gain;
    s.connect(g); g.connect(sortie);
    s.start(depart);
    voixCourante.sources.push(s);
    voixCourante.fin = Math.max(voixCourante.fin, depart + tampon.duration / (vitesse || 1));
  }

  function note(voixCourante, frequence, retardS, dureeS, gain) {
    var o = ctx.createOscillator();
    var g = ctx.createGain();
    var t = ctx.currentTime + retardS;
    o.type = 'sine';
    o.frequency.value = frequence;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.005);
    g.gain.exponentialRampToValueAtTime(0.001, t + dureeS);
    o.connect(g); g.connect(sortie);
    o.start(t); o.stop(t + dureeS + 0.02);
    voixCourante.sources.push(o);
    voixCourante.fin = Math.max(voixCourante.fin, t + dureeS);
  }

  var SYNTH = {
    etape: function (v, rang, gain) { note(v, [523.25, 587.33, 659.25, 783.99, 880][rang - 1], 0, 0.14, gain || 0.25); },
    lancer: function (v, rang, gain) { [523.25, 783.99, 1046.5].forEach(function (f, i) { note(v, f, i * 0.06, 0.16, gain || 0.28); }); },
    confetti: function (v) {
      [523.25, 659.25, 783.99, 1046.5, 1318.51].forEach(function (f, i) { note(v, f, i * 0.07, 0.22, 0.28); });
      for (var i = 0; i < 5; i++) note(v, Math.random() < 0.5 ? 1567.98 : 2093, 0.35 + i * 0.12, 0.22, 0.08);
    },
    erreur_douce: function (v, rang, gain) { note(v, 392, 0, 0.18, gain || 0.15); note(v, 329.63, 0.15, 0.18, gain || 0.15); }
  };

  function arreterVoix(v) {
    v.sources.forEach(function (s) { try { s.stop(); } catch (e) { /* déjà arrêtée */ } });
  }

  /* Une voix par identifiant, VOIX_MAX voix au total (la plus ancienne est coupée). */
  function nouvelleVoix(id) {
    voix = voix.filter(function (v) {
      if (v.id === id) { arreterVoix(v); return false; }
      return v.fin > ctx.currentTime;
    });
    while (voix.length >= VOIX_MAX) arreterVoix(voix.shift());
    var v = { id: id, sources: [], fin: ctx.currentTime };
    voix.push(v);
    return v;
  }

  /*
   * jouer('etape', { rang: 2 })
   * Pour comparer des variantes : options.src (autre fichier), options.eclat (false | 'synth').
   */
  function jouer(id, options) {
    var o = options || {};
    var son = SONS[id];
    if (!son || muet || !ctx || document.hidden) return;
    var maintenant = Date.now();
    if (maintenant - dernierDepart < ECART_MIN_MS) return;
    if (son.antiRafaleMs && maintenant - (dernierParId[id] || 0) < son.antiRafaleMs) { dernierParId[id] = maintenant; return; }
    dernierDepart = maintenant;
    dernierParId[id] = maintenant;

    var rang = Math.min(Math.max(o.rang || 1, 1), son.echelle ? son.echelle.length : 1);
    var nom = o.src || son.src;
    if (!nom) { try { SYNTH[son.synth](nouvelleVoix(id), rang, son.gain); } catch (e) { /* silencieux */ } return; }

    var eclats = son.eclat && o.eclat === undefined ? son.eclat : [];
    Promise.all([charger(nom)].concat(eclats.map(function (e) { return charger(e[0]); }))).then(function (b) {
      if (muet || document.hidden || Date.now() - maintenant > ATTENTE_CHARGEMENT_MAX_MS) return;
      var v = nouvelleVoix(id);
      if (!b[0]) { if (son.secours) SYNTH[son.secours](v, rang); return; }
      source(v, b[0], son.gain, son.echelle ? son.echelle[rang - 1] : 1, 0);
      eclats.forEach(function (e, i) { if (b[i + 1]) source(v, b[i + 1], son.eclatGain, 1, e[1] / 1000); });
      if (o.eclat === 'synth') SYNTH.confetti(v);
    }).catch(function () {});
  }

  /* ---------- réglages ---------- */

  function couperTout() {
    voix.forEach(arreterVoix);
    voix = [];
  }

  /* Retourne le nouvel état : true si le son est coupé. */
  function basculerMuet() {
    if (bloque) return true;
    muet = !muet;
    ecrire('localStorage', CLE_ETAT, muet ? 'off' : 'on');
    if (muet) couperTout();
    else { deverrouiller(); jouer('son_on'); }
    return muet;
  }

  function regler(v) {
    volume = Math.min(Math.max(Number(v) || 0, 0), 1);
    if (sortie) sortie.gain.value = volume;
  }

  document.addEventListener('visibilitychange', function () {
    if (!ctx) return;
    if (document.hidden) {
      couperTout();
      ctx.suspend().catch(function () {});
    } else if (!muet) {
      ctx.resume().catch(function () {});
    }
  });

  /* Déverrouillage au premier geste, avant les gestionnaires de l'application (phase de capture). */
  ['pointerdown', 'keydown'].forEach(function (type) { document.addEventListener(type, deverrouiller, true); });
  document.addEventListener('click', function (e) {
    deverrouiller();
    var cible = e.target.closest && e.target.closest('[data-son]');
    if (cible) jouer(cible.getAttribute('data-son'));
  }, true);

  window.Sons = {
    SONS: SONS,
    init: init,
    deverrouiller: deverrouiller,
    jouer: jouer,
    basculerMuet: basculerMuet,
    estMuet: function () { return muet; },
    estBloque: function () { return bloque; },
    volume: regler
  };
})();
