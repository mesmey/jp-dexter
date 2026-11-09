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

## Déploiement sur Vercel et Supabase

Vercel peut héberger l’API FastAPI et servir les fichiers HTML/CSS/JavaScript depuis le même projet. Supabase fournit PostgreSQL. `app.py` est l’entrée FastAPI détectée par Vercel ; l’API et le site utilisent ainsi la même origine. Le navigateur n’a pas accès à l’adresse de la base.

### 1. Choisir la bonne connexion Supabase

Comme Vercel exécute des fonctions serverless, utilise **Connect → Connection string → Transaction pooler** dans le tableau de bord Supabase. Copie son URI PostgreSQL et remplace `[YOUR-PASSWORD]` par le mot de passe de la base. N’utilise pas l’adresse Direct ni le Session pooler pour Vercel. Encode les caractères réservés du mot de passe dans l’URL. Le backend désactive les prepared statements pour être compatible avec le transaction pooler.

Ne mets jamais cette URL dans le dépôt, dans `web/config.js` ou dans le navigateur.

### 2. Importer le dépôt GitHub dans Vercel

1. Connecte-toi sur [vercel.com](https://vercel.com/) avec GitHub.
2. Choisis **Add New → Project** et importe `mesmey/jp-dexter`.
3. Garde la racine du dépôt comme **Root Directory** ; ne choisis pas le sous-dossier `web`.
4. Vercel détecte `requirements.txt` et `app.py`. Garde les réglages de build par défaut et déploie.
5. Dans **Project → Settings → Environment Variables**, ajoute `DATABASE_URL` avec l’URI PostgreSQL Supabase complète pour Production (et Preview si souhaité), puis redéploie.

L’application refuse explicitement de démarrer sur Vercel si `DATABASE_URL` manque. En local, elle utilise SQLite.

### 3. Vérifier le déploiement

Ouvre `https://<ton-projet>.vercel.app/health` : la réponse attendue est `{"status":"ok"}`. Puis ouvre la racine du site, crée un compte et vérifie le flux de jeu avec deux navigateurs/comptes. La base Supabase reste privée ; seules les requêtes de l’API accèdent à PostgreSQL.

Les limites du plan gratuit et les règles de disponibilité peuvent changer. Le déploiement convient à une bêta, pas à une disponibilité permanente garantie. Les fonctions serverless peuvent traiter des requêtes simultanées depuis plusieurs instances ; ce prototype est destiné à des essais et ne fournit pas encore de verrouillage distribué pour les manches très disputées.

## Ancien déploiement Render

`render.yaml` est conservé comme configuration alternative si tu souhaites revenir à Render. Pour séparer le site statique d’une API distante, `web/config.js` accepte une URL d’API dans `GAME_API_URL`; avec le déploiement Vercel ci-dessus, laisse-la vide pour utiliser la même origine.

## Périmètre du prototype

Les récompenses publicitaires, les paris sur des matchs réels et les paiements réels ne sont pas inclus. Les paiements doivent être étudiés séparément avant toute intégration. Le compte est lié à un pseudo/mot de passe : prévois une procédure de récupération de compte et une politique de confidentialité avant d’ouvrir le jeu au public.
