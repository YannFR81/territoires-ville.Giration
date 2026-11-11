# Feuille de route de Giration

Décisions de Yann Steffan (10 octobre 2026) :

- application de bureau qui réutilise la carte de Girabase, sort le DXF et marche avec l'assistant d'IA ; une version web viendra plus tard ;
- deux logiciels séparés, Girabase et Giration, avec un bouton pour passer de l'un à l'autre ;
- l'étape 1 d'abord, comparée à COVADIS le lundi 12 octobre.

| Étape | Contenu | État |
|---|---|---|
| 1. Moteur et preuve de précision | Moteur fidèle et suivi de tracé, enveloppes, bibliothèque, exports DXF/KML/GeoJSON/JSON, ligne de commande, validation | **0.1.0** (intégrée à la 1.2.1) |
| 2. Giration sur orthophoto (lot 1 des principes) | Carte IGN, saisie par points de passage, guides, corrections de fin de saisie et trajet au mieux, voie de droite (option gauche), Giratoire, Mesurer, site Girabase, verdict, Annuler/Rétablir, exports | **1.2.1** |
| 2 bis. Contraintes (lot 2 des principes) | Bordures, bordures franchissables et obstacles dessinés (B), bâti BD TOPO, conflits dans le verdict, comparaison des variantes, note PDF, DXF en Lambert-93 ou CC | **1.3.0** |
| 3. Assistance | Mode départ → arrivée, diagnostics et « Corriger pour moi », obstacles (BD TOPO, dessin, DXF), variantes, assistant pas à pas, note PDF | à faire |
| 4. Girabase | Ouverture du `.gsite`, matrice des mouvements, retour de dimensionnement, outils MCP | préfiguré par `exemples/lombers` |
| 5. Manœuvres et diffusion | Marches arrière, aires de retournement, grille CERTU à l'écran et `.gir`, exe et CI | à faire |

Le détail se trouve dans les documents de projet « propositions de conception » et « principes de fonctionnement et ergonomie » (11 octobre 2026), qui fixe les règles de la saisie assistée.
