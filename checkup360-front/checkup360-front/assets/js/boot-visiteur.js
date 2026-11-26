/* Montage de l'application (fichier séparé : la CSP interdit les scripts en ligne). */
Sons.init();
DC.mount(Component, 'tpl-visiteur', document.getElementById('app'), DC.queryProps());
