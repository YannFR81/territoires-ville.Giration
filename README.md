# Giration — fork avec portage Python (version 1.3.0)

*L’intelligence artificielle au service de l’action publique efficiente.*

> **Ce fork ajoute [Giration Python](giration-python/)**, un portage du logiciel Giration du CERTU/CEREMA, pour
> Windows 10 et 11, sans installation ni numéro de licence :
> - le modèle cinématique du CERTU (tracteur et jusqu'à deux remorques), une bibliothèque de véhicules sourcée et
>   une vitesse calculée automatiquement ;
> - une application de bureau : on trace la giration en cliquant sur la photographie aérienne de l'IGN, avec
>   des guides pendant la saisie, les corrections du véhicule en fin de saisie et un outil Giratoire ;
> - les bordures, bordures franchissables et obstacles relevés sur la photo, et le bâti de la BD TOPO, pris en
>   compte dans le verdict ; la comparaison des variantes ;
> - l'export DXF pour AutoCAD et COVADIS, en Lambert-93 ou dans la zone CC du lieu, la note de giration en PDF,
>   le KML et le GeoJSON.
>
> - **Documentation** : [présentation](giration-python/README.md) · [nouveautés](giration-python/NOUVEAUTES.md) ·
>   [méthode](giration-python/docs/methode.md).
> - **Compilation et tests** : [workflow GitHub Actions](.github/workflows/giration-python.yml).
>
> Les fichiers VB6 du CEREMA, ci-dessous et à la racine du dépôt, sont **inchangés**. Le portage est distribué
> sous la même licence GNU GPL v3 ; il n'est ni édité ni validé par le CEREMA.

---

# territoires-ville.Giration
Logiciel permettant d'appréhender le déplacement d'un véhicule.
Logiciel développé au CERTU sous Visual Basic 6.0.
Initialement vendu au CERTU sous licence propriétaire et désormais diffusé en logiciel libre sous licence GPL.
Ce logiciel n'est plus maintenu.
-------------------------------------------------------------------------------------------------------------
A la 1ere ouverture d'un executable, une interface vous demandant un numéro de licence va s'ouvrir : 
 - Indiquez dans le champ Licence au moins un caractère alphanumérique de votre choix puis cliquer sur Enregistrer
Une message box vous indiquant que votre licence a été enregistré avec succès apparaitera alors.
 - Dans cette message Box cliquer sur Ok : le logiciel s'ouvrira et 2 fichiers apparaiteront à la racine de votre exécutable (relic.ctu et reser.ctu).
 - Tant que ces 2 fichiers seront présent à la racine de votre exe la fenêtre vous demandant un numéro de licence
 n'apparaitera pas.
-------------------------------------------------------------------------------------------------------------
Les guides utilisateurs (.pdf et .odt) et l'aide en ligne (.chm) ont été crées à l'époque où ce logiciel était propriétaire : dans la configuration actuelle ne pas tenir compte des parties sur le contrat de licence qui de fait n'ont plus lieu d'être.
