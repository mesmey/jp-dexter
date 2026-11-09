# Just Price Dexter

Prototype jouable d’un jeu de devinettes multijoueur : interface HTML/CSS/JavaScript, API Python FastAPI et base SQLite en local. L’API peut utiliser PostgreSQL pour un déploiement avec une base hébergée.

## Lancer en local

1. Installe Python 3.11 ou plus récent.
2. Dans ce dossier, crée un environnement virtuel et installe les dépendances :

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Lance le serveur :

   ```powershell
   uvicorn api.main:app --reload
   ```

4. Ouvre http://127.0.0.1:8000. La base locale `justprice.db` est créée au premier démarrage.

## Règles implémentées

- Les comptes commencent avec 200 tokens. Le bonus quotidien rapporte 20 tokens.
- Acheter un point coûte 10 tokens ; convertir un point en tokens en rapporte 7.
- Une room publique ou privée accepte jusqu’à 6 joueurs. Les rooms publiques sont listées ; une room privée se rejoint avec son code.
- Une manche démarre automatiquement dès qu’au moins 2 membres ont des points. Le nombre secret (1–1 000) et l’ordre des tours sont choisis côté serveur.
- Chaque proposition coûte un point et ajoute ce point à la cagnotte. Le gagnant remporte la cagnotte en points.
- Chaque tour dure 15 secondes. Au dépassement, un point est dépensé et le serveur propose un nombre aléatoire, visible dans l’historique.
- Un joueur sans point est éliminé de la manche. S’il en achète avec ses tokens, il revient à la fin de l’ordre des tours.
- Si tous les joueurs sont éliminés, la cagnotte est conservée dans la room et la partie attend que des joueurs aient de nouveau des points.
- Après une victoire, une nouvelle manche démarre automatiquement après 15 secondes si au moins 2 joueurs ont des points.

Les connexions utilisent un pseudo et un mot de passe. Les mots de passe sont stockés sous forme de dérivés PBKDF2 ; le navigateur conserve un jeton de session révocable à expiration.

## Publication gratuite (prototype)

Le site statique peut être publié sur Cloudflare Pages ou Netlify. L’API FastAPI peut être déployée sur Render et la base de données sur le plan gratuit de Supabase. Les quotas, périodes de veille et conditions des offres gratuites peuvent changer ; ils conviennent à un prototype, pas à une disponibilité garantie.

### API sur Render

Le fichier `render.yaml` déclare le service. Lors de la configuration, définis :

- `DATABASE_URL` : chaîne PostgreSQL de Supabase avec le pilote `postgresql://...` ou `postgres://...`.
- `CORS_ORIGINS` : URL exacte du site publié (par exemple `https://mon-jeu.pages.dev`), sans slash final.

Le service démarre avec un seul worker pour sérialiser les modifications de partie. N’utilise pas SQLite sur un hébergement éphémère : ses fichiers peuvent disparaître lors d’un redéploiement ou d’un redémarrage.

### Site statique

Publie le dossier `web/` en tant que site statique. Avant publication, remplace la valeur vide de `GAME_API_URL` dans `web/config.js` par l’URL publique de l’API Render, par exemple `https://mon-api.onrender.com`.

Pour tester le site séparément en local, ouvre-le via un serveur HTTP statique (pas directement en `file://`) et ajoute son origine à `CORS_ORIGINS`.

## Périmètre du prototype

Les récompenses publicitaires, les paris sur des matchs réels et les paiements réels ne sont pas inclus. Les paiements doivent être étudiés séparément avant toute intégration. Le compte est lié à un pseudo/mot de passe : prévois une procédure de récupération de compte et une politique de confidentialité avant d’ouvrir le jeu au public.
