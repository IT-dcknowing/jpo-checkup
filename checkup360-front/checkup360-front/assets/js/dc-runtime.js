/*!
 * dc-runtime.js — mini moteur de gabarits pour le front Check-up 360.
 * Lit un gabarit HTML (balises <sc-if>, <sc-for>, trous {{ chemin }}) et le rend avec Preact.
 * Syntaxe :
 *   {{ a.b.c }}                 recherche pointée dans renderVals() (pas d'expression)
 *   <sc-if value="{{ x }}">     rendu si x est vrai
 *   <sc-for list="{{ xs }}" as="x">  répétition, {{ x.champ }} et {{ $index }} disponibles
 *   onClick="{{ fn }}"          événements (onChange sur un champ texte = à chaque frappe)
 * Utilisation : DC.mount(Component, 'id-du-gabarit', élément, props)
 */
(function () {
  'use strict';
  var P = window.preact;
  if (!P) throw new Error('Preact doit être chargé avant dc-runtime.js');
  var h = P.h, Fragment = P.Fragment;
  var VOID = { area: 1, base: 1, br: 1, col: 1, embed: 1, hr: 1, img: 1, input: 1, link: 1, meta: 1, source: 1, track: 1, wbr: 1 };
  var HOLE = /\{\{\s*([\s\S]*?)\s*\}\}/g;

  var decoder = null;
  function decode(s) {
    if (s.indexOf('&') < 0) return s;
    decoder = decoder || document.createElement('textarea');
    decoder.innerHTML = s;
    return decoder.value;
  }

  /* ---------- analyse du gabarit (sans le parseur HTML du navigateur, qui déplacerait
     <sc-for> hors des <select>, <table> ou <ol>) ---------- */
  function parse(src) {
    var root = { tag: '#root', attrs: [], children: [] };
    var stack = [root];
    var i = 0, n = src.length;
    var ws = /\s/;
    while (i < n) {
      if (src.charCodeAt(i) === 60 /* < */) {
        if (src.startsWith('<!--', i)) { var ce = src.indexOf('-->', i); i = ce < 0 ? n : ce + 3; continue; }
        if (src[i + 1] === '/') {
          var e = src.indexOf('>', i);
          var closing = src.slice(i + 2, e).trim();
          for (var k = stack.length - 1; k > 0; k--) { if (stack[k].tag === closing) { stack.length = k; break; } }
          i = e + 1; continue;
        }
        if (src[i + 1] === '!') { i = src.indexOf('>', i) + 1; continue; }
        var m = /^[a-zA-Z][\w:-]*/.exec(src.slice(i + 1, i + 80));
        if (m) {
          var tag = m[0], j = i + 1 + tag.length, attrs = [], selfClose = false;
          while (j < n) {
            while (ws.test(src[j])) j++;
            if (src[j] === '>') { j++; break; }
            if (src[j] === '/' && src[j + 1] === '>') { selfClose = true; j += 2; break; }
            var am = /^[^\s=>\/]+/.exec(src.slice(j, j + 200));
            if (!am) { j++; continue; }
            var name = am[0]; j += name.length;
            while (ws.test(src[j])) j++;
            var val = null;
            if (src[j] === '=') {
              j++; while (ws.test(src[j])) j++;
              var q = src[j];
              if (q === '"' || q === "'") { var qe = src.indexOf(q, j + 1); val = src.slice(j + 1, qe); j = qe + 1; }
              else { var vm = /^[^\s>]+/.exec(src.slice(j)); val = vm[0]; j += val.length; }
            }
            attrs.push([name, val === null ? '' : decode(val), val === null]);
          }
          var node = { tag: tag, attrs: attrs, children: [] };
          stack[stack.length - 1].children.push(node);
          if (!selfClose && !VOID[tag.toLowerCase()]) {
            if (tag === 'script' || tag === 'style') {
              var se = src.indexOf('</' + tag, j);
              node.children.push({ text: src.slice(j, se) });
              j = src.indexOf('>', se) + 1;
            } else stack.push(node);
          }
          i = j; continue;
        }
      }
      var t = src.indexOf('<', i + 1);
      if (t < 0) t = n;
      stack[stack.length - 1].children.push({ text: decode(src.slice(i, t)) });
      i = t;
    }
    return root;
  }

  /* ---------- compilation des trous ---------- */
  function parts(str) {
    var out = [], last = 0, m;
    HOLE.lastIndex = 0;
    while ((m = HOLE.exec(str))) {
      if (m.index > last) out.push(str.slice(last, m.index));
      out.push({ e: m[1] });
      last = HOLE.lastIndex;
    }
    if (last < str.length) out.push(str.slice(last));
    return out;
  }
  function compile(node) {
    if (node.text !== undefined) {
      if (/^\s*$/.test(node.text) && node.text.indexOf('\n') >= 0) return null; // blancs de mise en page
      node.p = parts(node.text);
      return node;
    }
    node.ca = node.attrs.filter(function (a) { return a[0].indexOf('hint-') !== 0; })
      .map(function (a) { return [a[0], a[2] ? true : parts(a[1])]; });
    node.children = node.children.map(compile).filter(Boolean);
    return node;
  }

  function lookup(expr, scope) {
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    if (expr === 'null') return null;
    if (expr === 'undefined') return undefined;
    if (/^-?\d+(\.\d+)?$/.test(expr)) return Number(expr);
    var q = expr[0];
    if ((q === '"' || q === "'") && expr[expr.length - 1] === q) return expr.slice(1, -1);
    var path = expr.split('.'), v = scope;
    for (var i = 0; i < path.length; i++) {
      if (v === null || v === undefined) return undefined;
      v = v[path[i]];
    }
    return v;
  }
  function value(p, scope) {
    if (p === true) return true;
    if (p.length === 1 && typeof p[0] === 'object') return lookup(p[0].e, scope);
    var s = '';
    for (var i = 0; i < p.length; i++) {
      var x = p[i];
      if (typeof x === 'string') s += x;
      else { var v = lookup(x.e, scope); if (v !== undefined && v !== null && v !== false) s += v; }
    }
    return s;
  }
  function attr(node, name, scope) {
    for (var i = 0; i < node.ca.length; i++) if (node.ca[i][0] === name) return value(node.ca[i][1], scope);
    return undefined;
  }
  function isTextField(node) {
    if (node.tag === 'textarea') return true;
    if (node.tag !== 'input') return false;
    var t = '';
    for (var i = 0; i < node.ca.length; i++) if (node.ca[i][0] === 'type' && node.ca[i][1] !== true) t = String(node.ca[i][1].join ? node.ca[i][1].join('') : '');
    return ['checkbox', 'radio', 'file', 'range', 'color'].indexOf(t) < 0;
  }

  /* ---------- rendu ---------- */
  function kids(list, scope) {
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var r = renderNode(list[i], scope);
      if (r !== null && r !== undefined) out.push(r);
    }
    return out;
  }
  function renderNode(node, scope) {
    if (node.text !== undefined) {
      if (node.p.length === 1 && typeof node.p[0] === 'string') return node.p[0];
      return value(node.p, scope);
    }
    var tag = node.tag;
    if (tag === 'helmet' || tag === 'script') return null;
    if (tag === 'sc-if') return attr(node, 'value', scope) ? h(Fragment, null, kids(node.children, scope)) : null;
    if (tag === 'sc-for') {
      var list = attr(node, 'list', scope) || [];
      var as = attr(node, 'as', scope) || 'item';
      return h(Fragment, null, list.map(function (it, idx) {
        var s = Object.create(scope); s[as] = it; s.$index = idx;
        return h(Fragment, { key: (it && (it.id || it.ref || it.k)) || idx }, kids(node.children, s));
      }));
    }
    var props = {};
    for (var i = 0; i < node.ca.length; i++) {
      var name = node.ca[i][0], v = value(node.ca[i][1], scope);
      if (/^on[A-Z]/.test(name)) {
        if (typeof v !== 'function') continue;
        if (name === 'onChange' && isTextField(node)) name = 'onInput';
        props[name] = v; continue;
      }
      if (v === undefined || v === null || v === false) {
        if (name === 'checked') props.checked = false;
        else if (name === 'value') props.value = '';
        continue;
      }
      props[name] = v;
    }
    return h(tag, props, kids(node.children, scope));
  }

  function DCLogic(props) { P.Component.call(this, props); }
  DCLogic.prototype = Object.create(P.Component.prototype);
  DCLogic.prototype.constructor = DCLogic;
  DCLogic.prototype.render = function () {
    var tpl = this.constructor.__tpl;
    return h(Fragment, null, kids(tpl.children, this.renderVals ? this.renderVals() : {}));
  };

  window.DCLogic = DCLogic;
  window.DC = {
    compile: function (src) { return compile(parse(src)); },
    mount: function (Comp, templateId, el, props) {
      var src = document.getElementById(templateId).textContent;
      Comp.__tpl = compile(parse(src));
      P.render(h(Comp, props || {}), el);
    },
    queryProps: function () {
      var out = {};
      try { new URLSearchParams(location.search).forEach(function (v, k) { out[k] = v === 'true' ? true : v === 'false' ? false : v; }); } catch (e) {}
      return out;
    }
  };
})();
