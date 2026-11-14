# Giration — portage Python (fork YannFR81)

Instructions pour les agents d'IA travaillant sur ce dépôt.
Lu par Gemini / Antigravity (`AGENTS.md`) et par Claude (via `CLAUDE.md`).

Amont : CEREMA/territoires-ville.Giration (code VB6 de Giration 3.3, GPL v3).

## Le projet

Portage Python de **Giration** (CERTU / CEREMA) : simulation des girations de véhicules
(épures de balayage, bibliothèque de véhicules, export DXF vers AutoCAD / COVADIS).

- **Branche de travail : `giration-python-1.3.0`** (dossier `giration-python/`). C'est là qu'est tout le code Python.
- `master` contient le code VB6 d'origine, inchangé : ne pas y développer.
- Tests : `pytest` dans `giration-python/` (dossier `tests/`).
- Statut : la version 1.3.0 doit être proposée au CEREMA par une *issue* d'abord ; PR seulement s'ils répondent.

## Points de vigilance pour la relecture
- Cinématique (rayon de braquage, empattement, porte-à-faux, remorques) : comparer aux résultats du logiciel d'origine
  et au guide `Giration33_guide_utilisateur.pdf`.
- Exports DXF : unités en mètres, calques nommés de façon stable.

## Règles communes à tous les agents (Claude, Gemini / Antigravity, autres)

- **Propriétaire du fork :** YannFR81, projeteur routier (conception routière, AutoCAD / COVADIS).
  C'est lui qui tranche les choix métier et de terminologie.
- **Crédit :** uniquement sous le pseudo **YannFR81**, jamais de nom réel (commits, README, feuilles de route, PR).
- **Langue :** français pour la documentation, les messages de commit et les échanges ; apostrophe typographique ’ dans les textes d'interface.
- **Relations avec le dépôt d'origine (amont) :** proposer d'abord par une *issue* ; n'ouvrir une PR qu'après
  réponse des mainteneurs. Ne jamais pousser ni ouvrir de PR vers l'amont sans accord explicite de YannFR81.
- **Fichiers propres au fork :** `AGENTS.md`, `CLAUDE.md`, `NOTES_REVUE.md` (et `CONTEXTE_YANNFR81.md` le cas échéant)
  ne doivent **jamais** figurer dans une branche proposée à l'amont. Créer les branches destinées à l'amont
  à partir de la branche de l'amont (`upstream/<branche>`), ou retirer ces fichiers avant la PR.
- **Pas de force-push** sur une branche dont une PR est ouverte.

## Travail en binôme

1. Un agent écrit, **l'autre relit** : aucun agent ne valide seul son propre travail.
2. Calculs normatifs ou moteur de calcul : écrire ou compléter d'abord les **tests sur cas de référence**
   (guides, abaques, exemples publiés), puis modifier le code. Un résultat de référence qui change = arrêt et signalement.
3. À la fin de chaque session, ajouter une entrée en tête de `NOTES_REVUE.md`
   (date, agent, branche, fait / vérifié / en suspens / question pour YannFR81).
4. Avant de commencer, lire `NOTES_REVUE.md` pour reprendre là où l'autre agent s'est arrêté.
