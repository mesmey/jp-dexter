const API_URL = (window.GAME_API_URL || "").replace(/\/+$/, "");
const TOKEN_KEY = "jpd_session";
const ROOM_KEY = "jpd_room";

const $ = (selector) => document.querySelector(selector);
let token = localStorage.getItem(TOKEN_KEY);
let user = null;
let roomId = localStorage.getItem(ROOM_KEY);
let registering = false;
let roomTimer;

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    throw new Error("Impossible de joindre le serveur. Vérifie que l’API est démarrée.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signOut();
    throw new Error(payload.detail || "Une erreur est survenue.");
  }
  return payload;
}

function showNotice(selector, message, isError = false) {
  const element = $(selector);
  if (!element) return;
  element.textContent = message || "";
  element.classList.toggle("error", isError);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function showSignedOut() {
  $("#auth").classList.remove("hidden");
  $("#game").classList.add("hidden");
}

function signOut() {
  token = null;
  user = null;
  roomId = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROOM_KEY);
  clearInterval(roomTimer);
  showSignedOut();
}

async function loadUser() {
  user = await request("/api/me");
  $("#auth").classList.add("hidden");
  $("#game").classList.remove("hidden");
  updateWallet();
  $("#daily").disabled = !user.daily_available;
  $("#daily-status").textContent = user.daily_available
    ? "Le bonus peut être récupéré une fois par jour."
    : "Tu as déjà récupéré ton bonus aujourd’hui.";
  if (roomId) {
    $("#home-view").classList.add("hidden");
    $("#room-view").classList.remove("hidden");
    await refreshRoom();
  } else {
    $("#home-view").classList.remove("hidden");
    $("#room-view").classList.add("hidden");
    await refreshRooms();
  }
  clearInterval(roomTimer);
  roomTimer = setInterval(async () => {
    if (!token) return;
    try {
      user = await request("/api/me");
      updateWallet();
      if (roomId) await refreshRoom();
      else await refreshRooms();
    } catch (error) {
      showNotice(roomId ? "#room-notice" : "#account-notice", error.message, true);
    }
  }, 1200);
}

function updateWallet() {
  if (!user) return;
  $("#player-name").textContent = user.username;
  $("#tokens").textContent = user.tokens;
  $("#points").textContent = user.points;
  $("#daily").disabled = !user.daily_available;
  $("#daily-status").textContent = user.daily_available
    ? "Le bonus peut être récupéré une fois par jour."
    : "Tu as déjà récupéré ton bonus aujourd’hui.";
}

async function refreshUser() {
  user = await request("/api/me");
  updateWallet();
}

async function refreshRooms() {
  const rooms = await request("/api/rooms");
  const list = $("#room-list");
  if (!rooms.length) {
    list.innerHTML = '<p class="muted small">Aucune room publique disponible pour le moment.</p>';
    return;
  }
  list.innerHTML = rooms.map((room) => `
    <div class="room-card">
      <div>
        <strong>${escapeHtml(room.name)}</strong>
        <span>${room.players}/6 joueurs · ${room.phase === "playing" ? "En partie" : "En attente"}</span>
      </div>
      <button class="button quiet join-public" data-code="${escapeHtml(room.code)}" type="button">Rejoindre</button>
    </div>
  `).join("");
  list.querySelectorAll(".join-public").forEach((button) => {
    button.addEventListener("click", () => joinRoom(button.dataset.code));
  });
}

function countdown(deadline) {
  if (!deadline) return "";
  return Math.max(0, Math.ceil((new Date(deadline).getTime() - Date.now()) / 1000));
}

function renderRoom(room) {
  const previousGuess = $("#guess-number")?.value || "";
  $("#room-code").textContent = room.code;
  $("#room-title").textContent = room.name;

  const currentPlayer = room.players.find((player) => player.user_id === room.current_turn_id);
  const isMyTurn = room.phase === "playing" && room.current_turn_id === user.id;
  const phaseLabel = room.phase === "playing"
    ? `Manche ${room.round_number} · tour de ${currentPlayer ? escapeHtml(currentPlayer.username) : "—"}`
    : room.phase === "finished"
      ? "Manche terminée"
      : "En attente";

  const playerMarkup = room.players.map((player) => {
    const classes = [
      player.user_id === user.id ? "you" : "",
      player.eliminated ? "out" : "",
    ].filter(Boolean).join(" ");
    const labels = [
      player.user_id === room.current_turn_id && room.phase === "playing" ? "À son tour" : "",
      player.eliminated ? "Éliminé" : "",
      player.is_host ? "Hôte" : "",
    ].filter(Boolean).join(" · ");
    return `<li class="${classes}"><span>${escapeHtml(player.username)}${player.user_id === user.id ? " (toi)" : ""}<br><small class="muted">${escapeHtml(labels)}</small></span><span>${player.points} pts</span></li>`;
  }).join("");

  const historyMarkup = room.history.length
    ? [...room.history].reverse().map((entry) => `
      <li><b>${escapeHtml(entry.username)}</b> a proposé <b>${entry.number}</b> — ${escapeHtml(entry.result)}
        ${entry.automatic ? "<small>Proposition automatique (délai dépassé)</small>" : ""}
      </li>
    `).join("")
    : '<li class="muted">Les propositions apparaîtront ici.</li>';

  let actionMarkup = "";
  if (isMyTurn) {
    actionMarkup = `
      <p class="turn">C’est ton tour · ${countdown(room.deadline)} s</p>
      <form id="guess-form" class="guess-form">
        <label class="sr-only" for="guess-number">Ton nombre entre 1 et 1000</label>
        <input id="guess-number" type="number" min="1" max="1000" placeholder="1 à 1000" required autofocus>
        <button class="button primary" type="submit">Proposer · 1 point</button>
      </form>
    `;
  } else if (room.phase === "playing") {
    actionMarkup = `<p class="muted">Prochaine proposition dans ${countdown(room.deadline)} s.</p>`;
  } else if (room.phase === "finished") {
    actionMarkup = `<p class="turn">🏆 ${escapeHtml(room.winner_name || "Un joueur")} gagne ! Nombre secret : ${room.target}. Nouvelle manche dans ${countdown(room.next_round_at) || 0} s.</p>`;
  } else {
    actionMarkup = '<p class="muted">Une manche démarre quand au moins 2 joueurs ont des points.</p>';
  }

  $("#room-content").innerHTML = `
    <div class="status-card">
      <p class="eyebrow">${phaseLabel}</p>
      <p>${escapeHtml(room.message)}</p>
      <div class="pot">Cagnotte : ${room.pot} point${room.pot === 1 ? "" : "s"}</div>
      ${actionMarkup}
    </div>
    <div class="game-grid">
      <section class="subpanel">
        <h3>Joueurs (${room.players.length}/6)</h3>
        <ul class="players">${playerMarkup}</ul>
      </section>
      <section class="subpanel">
        <h3>Propositions</h3>
        <ul class="history">${historyMarkup}</ul>
      </section>
    </div>
    <p class="fine-print">Un tour dure 15 secondes. Sans proposition, tu perds 1 point et le serveur propose un nombre aléatoire visible par tous.</p>
  `;
  const guessField = $("#guess-number");
  if (guessField && previousGuess) guessField.value = previousGuess;
  $("#guess-form")?.addEventListener("submit", submitGuess);
}

async function refreshRoom() {
  if (!roomId) return;
  try {
    const room = await request(`/api/rooms/${encodeURIComponent(roomId)}`);
    renderRoom(room);
    await refreshUser();
  } catch (error) {
    if (
      error.message.includes("ne fais pas partie")
      || error.message.includes("Room introuvable")
    ) {
      roomId = null;
      localStorage.removeItem(ROOM_KEY);
      $("#room-view").classList.add("hidden");
      $("#home-view").classList.remove("hidden");
      await refreshRooms();
    } else {
      showNotice("#room-notice", error.message, true);
    }
  }
}

async function enterRoom(id) {
  roomId = id;
  localStorage.setItem(ROOM_KEY, id);
  $("#home-view").classList.add("hidden");
  $("#room-view").classList.remove("hidden");
  showNotice("#room-notice", "");
  await refreshRoom();
}

async function joinRoom(code) {
  try {
    const result = await request("/api/rooms/join", {
      method: "POST",
      body: JSON.stringify({ code: code.trim().toUpperCase() }),
    });
    await enterRoom(result.id);
  } catch (error) {
    showNotice("#room-notice", error.message, true);
    $("#room-view").classList.remove("hidden");
  }
}

async function submitGuess(event) {
  event.preventDefault();
  const input = $("#guess-number");
  const number = Number(input.value);
  if (!Number.isInteger(number) || number < 1 || number > 1000) {
    input.setCustomValidity("Choisis un nombre entier entre 1 et 1000.");
    input.reportValidity();
    return;
  }
  input.setCustomValidity("");
  try {
    const room = await request(`/api/rooms/${encodeURIComponent(roomId)}/guess`, {
      method: "POST",
      body: JSON.stringify({ number }),
    });
    renderRoom(room);
    await refreshUser();
  } catch (error) {
    showNotice("#room-notice", error.message, true);
    await refreshRoom();
  }
}

$("#auth-toggle").addEventListener("click", () => {
  registering = !registering;
  $("#auth-submit").textContent = registering ? "Créer mon compte" : "Se connecter";
  $("#auth-toggle").textContent = registering
    ? "Déjà un compte ? Se connecter"
    : "Pas encore de compte ? S’inscrire";
  $("#password").autocomplete = registering ? "new-password" : "current-password";
  showNotice("#auth-error", "");
});

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showNotice("#auth-error", "");
  const credentials = {
    username: $("#username").value.trim(),
    password: $("#password").value,
  };
  try {
    const result = await request(registering ? "/api/register" : "/api/login", {
      method: "POST",
      body: JSON.stringify(credentials),
    });
    token = result.token;
    localStorage.setItem(TOKEN_KEY, token);
    await loadUser();
  } catch (error) {
    showNotice("#auth-error", error.message, true);
  }
});

$("#logout").addEventListener("click", signOut);

$("#daily").addEventListener("click", async () => {
  try {
    await request("/api/daily", { method: "POST" });
    await refreshUser();
    showNotice("#account-notice", "20 tokens ajoutés à ton compte.");
  } catch (error) {
    showNotice("#account-notice", error.message, true);
  }
});

$("#buy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const quantity = Number($("#buy-quantity").value);
    await request("/api/store/buy", {
      method: "POST",
      body: JSON.stringify({ quantity }),
    });
    await refreshUser();
    showNotice("#account-notice", `${quantity} point${quantity > 1 ? "s" : ""} acheté${quantity > 1 ? "s" : ""}.`);
  } catch (error) {
    showNotice("#account-notice", error.message, true);
  }
});

$("#room-buy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const quantity = Number($("#room-buy-quantity").value);
    await request("/api/store/buy", {
      method: "POST",
      body: JSON.stringify({ quantity }),
    });
    await refreshUser();
    await refreshRoom();
    showNotice("#room-store-notice", `${quantity} point${quantity > 1 ? "s" : ""} acheté${quantity > 1 ? "s" : ""}.`);
  } catch (error) {
    showNotice("#room-store-notice", error.message, true);
  }
});

$("#convert-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const quantity = Number($("#convert-quantity").value);
    await request("/api/store/convert", {
      method: "POST",
      body: JSON.stringify({ quantity }),
    });
    await refreshUser();
    showNotice("#account-notice", `${quantity} point${quantity > 1 ? "s" : ""} converti${quantity > 1 ? "s" : ""} en ${quantity * 7} tokens.`);
  } catch (error) {
    showNotice("#account-notice", error.message, true);
  }
});

$("#room-convert-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const quantity = Number($("#room-convert-quantity").value);
    await request("/api/store/convert", {
      method: "POST",
      body: JSON.stringify({ quantity }),
    });
    await refreshUser();
    await refreshRoom();
    showNotice("#room-store-notice", `${quantity} point${quantity > 1 ? "s" : ""} converti${quantity > 1 ? "s" : ""} en ${quantity * 7} tokens.`);
  } catch (error) {
    showNotice("#room-store-notice", error.message, true);
  }
});

$("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await request("/api/rooms", {
      method: "POST",
      body: JSON.stringify({
        name: $("#room-name").value.trim(),
        private: $("#room-private").checked,
      }),
    });
    await enterRoom(result.id);
    showNotice("#room-notice", `Room créée. Partage ce code : ${result.code}`);
  } catch (error) {
    showNotice("#room-notice", error.message, true);
  }
});

$("#join-form").addEventListener("submit", (event) => {
  event.preventDefault();
  joinRoom($("#join-code").value);
});

$("#refresh-rooms").addEventListener("click", async () => {
  try {
    await refreshRooms();
    showNotice("#room-notice", "Liste actualisée.");
  } catch (error) {
    showNotice("#room-notice", error.message, true);
  }
});

$("#leave-room").addEventListener("click", async () => {
  try {
    await request(`/api/rooms/${encodeURIComponent(roomId)}/leave`, { method: "DELETE" });
    roomId = null;
    localStorage.removeItem(ROOM_KEY);
    $("#room-view").classList.add("hidden");
    $("#home-view").classList.remove("hidden");
    await refreshUser();
    await refreshRooms();
  } catch (error) {
    showNotice("#room-notice", error.message, true);
  }
});

if (token) {
  loadUser().catch(() => signOut());
} else {
  showSignedOut();
}
