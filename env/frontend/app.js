const API_URL = (() => {
  if (window.location.port === "3000") {
    const protocol = window.location.protocol === "https:" ? "https" : "http";
    return `${protocol}://${window.location.hostname}:8000/api/chat`;
  }
  return "/api/chat";
})();
const HEALTH_URL = API_URL.replace(/\/chat$/, "/health");
const HISTORY_WINDOW_MS = 2 * 60 * 1000;
const DEFAULT_HOME_RETURN_SECONDS = 30;
const HISTORY_MAX_EXCHANGES = 5;
const TYPING_INTERVAL_MS = 18;
const VALID_PASSWORDS = new Set(["commons", "commons."]);
const BOT_NAME = "Chu-AI";
const INITIAL_BOT_MESSAGE = "やっほー！質問を入力してね。";

const screenPassword = document.getElementById("screen-password");
const screenTitle = document.getElementById("screen-title");
const chatApp = document.getElementById("chat-app");
const passwordForm = document.getElementById("password-form");
const passwordInput = document.getElementById("password-input");
const passwordError = document.getElementById("password-error");
const enterChat = document.getElementById("enter-chat");
const backToHome = document.getElementById("back-to-home");
const homeReturnWarning = document.getElementById("home-return-warning");
const form = document.getElementById("form");
const question = document.getElementById("question");
const send = document.getElementById("send");
const messages = document.getElementById("messages");
const messageList = document.getElementById("message-list") || messages;
const statusText = document.getElementById("status");
const quickButtons = document.querySelectorAll(".quick-button");
const exchangeHistory = [];

let isInputLocked = false;
let configuredHomeReturnSeconds = DEFAULT_HOME_RETURN_SECONDS;
let homeReturnTimerId = null;
let warningTickerId = null;
let homeReturnDeadlineTs = 0;
let lastActivityResetAt = 0;
let chatStateVersion = 0;

function showScreen(screenElement) {
  [screenPassword, screenTitle, chatApp].forEach((element) => {
    if (!element) {
      return;
    }
    element.classList.toggle("hidden", element !== screenElement);
  });
}

function setPasswordError(text) {
  if (!passwordError) {
    return;
  }
  passwordError.textContent = text;
}

function clearHomeReturnTimer() {
  if (homeReturnTimerId === null) {
    return;
  }
  window.clearTimeout(homeReturnTimerId);
  homeReturnTimerId = null;
}

function hideHomeReturnWarning() {
  if (!homeReturnWarning) {
    return;
  }
  homeReturnWarning.classList.add("hidden");
  homeReturnWarning.textContent = "";
}

function clearWarningTicker(resetDeadline = true) {
  if (warningTickerId !== null) {
    window.clearInterval(warningTickerId);
    warningTickerId = null;
  }
  if (resetDeadline) {
    homeReturnDeadlineTs = 0;
  }
  hideHomeReturnWarning();
}

function isChatVisible() {
  return Boolean(chatApp) && !chatApp.classList.contains("hidden");
}

function getHomeReturnMs() {
  return configuredHomeReturnSeconds * 1000;
}

function updateHomeReturnWarning() {
  if (!homeReturnWarning || !isChatVisible() || homeReturnDeadlineTs <= 0) {
    hideHomeReturnWarning();
    return;
  }

  const remainMs = homeReturnDeadlineTs - Date.now();
  if (remainMs <= 0) {
    hideHomeReturnWarning();
    return;
  }

  const remainSeconds = Math.ceil(remainMs / 1000);
  if (remainSeconds <= 10) {
    homeReturnWarning.classList.remove("hidden");
    homeReturnWarning.textContent = `ホームに戻ります...(${remainSeconds})`;
    return;
  }

  hideHomeReturnWarning();
}

function startWarningTicker() {
  clearWarningTicker(false);
  warningTickerId = window.setInterval(updateHomeReturnWarning, 250);
  updateHomeReturnWarning();
}

function scheduleReturnToHome() {
  clearHomeReturnTimer();
  const returnAfterMs = getHomeReturnMs();
  homeReturnDeadlineTs = Date.now() + returnAfterMs;
  startWarningTicker();
  homeReturnTimerId = window.setTimeout(() => {
    if (!isChatVisible()) {
      return;
    }
    openTitleScreen({ fromTimeout: true });
  }, returnAfterMs);
}

function noteUserActivity(force = false) {
  if (!isChatVisible()) {
    return;
  }

  const now = Date.now();
  if (!force && now - lastActivityResetAt < 220) {
    return;
  }
  lastActivityResetAt = now;
  scheduleReturnToHome();
}

function openTitleScreen({ fromTimeout = false } = {}) {
  clearHomeReturnTimer();
  clearWarningTicker();
  if (fromTimeout) {
    resetChatState();
  }
  setPasswordError("");
  showScreen(screenTitle);
}

function openChatScreen() {
  showScreen(chatApp);
  noteUserActivity(true);
  if (question) {
    question.focus();
  }
}

function setStatus(text, className) {
  if (!statusText) {
    return;
  }
  statusText.textContent = text;
  statusText.className = "status";
  if (className) {
    statusText.classList.add(className);
  }
}

function createMessageElement(name, type) {
  const message = document.createElement("article");
  message.className = `message ${type}`;

  const messageName = document.createElement("div");
  messageName.className = "message-name";
  messageName.textContent = name;

  const messageText = document.createElement("div");
  messageText.className = "message-text";

  message.append(messageName, messageText);
  messageList.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
  return messageText;
}

function addMessage(name, text, type) {
  const messageText = createMessageElement(name, type);
  messageText.textContent = text;
  messages.scrollTop = messages.scrollHeight;
}

function addThinkingMessage() {
  const message = document.createElement("article");
  message.className = "message bot thinking-message";

  const messageName = document.createElement("div");
  messageName.className = "message-name";
  messageName.textContent = BOT_NAME;

  const messageText = document.createElement("div");
  messageText.className = "message-text thinking-text";

  const label = document.createElement("span");
  label.textContent = "思考中";

  const dots = document.createElement("span");
  dots.className = "thinking-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dot.className = "thinking-dot";
    dot.textContent = ".";
    dots.appendChild(dot);
  }

  messageText.append(label, dots);
  message.append(messageName, messageText);
  messageList.appendChild(message);
  messages.scrollTop = messages.scrollHeight;
  return message;
}

function removeThinkingMessage(message) {
  if (message && message.parentNode) {
    message.parentNode.removeChild(message);
  }
}

function setInputLocked(locked) {
  isInputLocked = locked;
  question.disabled = locked;
  send.disabled = locked;
  quickButtons.forEach((button) => {
    button.disabled = locked;
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function resetChatState() {
  chatStateVersion += 1;
  exchangeHistory.length = 0;
  setInputLocked(false);
  setStatus("");
  if (question) {
    question.value = "";
  }
  if (messageList) {
    messageList.innerHTML = "";
    addMessage(BOT_NAME, INITIAL_BOT_MESSAGE, "bot");
  }
}

async function typeMessage(name, text, type, shouldContinue = () => true) {
  const messageText = createMessageElement(name, type);
  let current = "";
  for (const char of text) {
    if (!shouldContinue()) {
      return false;
    }
    current += char;
    messageText.textContent = current;
    messages.scrollTop = messages.scrollHeight;
    await sleep(TYPING_INTERVAL_MS);
  }
  return true;
}

function buildHistoryPrompt(currentQuestion) {
  const now = Date.now();
  const recent = exchangeHistory
    .filter((item) => now - item.ts <= HISTORY_WINDOW_MS)
    .slice(-HISTORY_MAX_EXCHANGES);

  if (recent.length === 0) {
    return currentQuestion;
  }

  const lines = ["【過去の会話履歴】"];
  recent.forEach((item, index) => {
    lines.push(`${index + 1}.`);
    lines.push(`ユーザー: ${item.user}`);
    lines.push(`${BOT_NAME}: ${item.bot}`);
  });
  lines.push("");
  lines.push("【現在の質問】");
  lines.push(currentQuestion);
  return lines.join("\n");
}

function pushExchange(userText, botText) {
  exchangeHistory.push({
    user: userText,
    bot: botText,
    ts: Date.now(),
  });
}

async function loadHomeReturnSeconds() {
  try {
    const response = await fetch(HEALTH_URL);
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const candidate = Number.parseInt(data.home_return_seconds, 10);
    if (Number.isFinite(candidate) && candidate > 0) {
      configuredHomeReturnSeconds = candidate;
    }
  } catch (error) {
    // ヘルス取得失敗時はデフォルトを利用
  }
}

async function sendQuestion(text) {
  noteUserActivity(true);

  if (isInputLocked) {
    return;
  }

  const requestText = text.trim();
  if (!requestText) {
    question.focus();
    return;
  }
  const currentStateVersion = chatStateVersion;

  addMessage("あなた", requestText, "user");
  question.value = "";
  setInputLocked(true);
  setStatus("回答中", "loading");
  const thinkingMessage = addThinkingMessage();

  try {
    const promptText = buildHistoryPrompt(requestText);
    const response = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: promptText,
        knowledge_mode: "all",
      }),
    });

    if (!response.ok) {
      throw new Error(`API error: ${response.status}`);
    }
    if (currentStateVersion !== chatStateVersion) {
      return;
    }

    const data = await response.json();
    if (currentStateVersion !== chatStateVersion) {
      return;
    }
    removeThinkingMessage(thinkingMessage);
    setStatus("表示中", "loading");
    const didComplete = await typeMessage(BOT_NAME, data.answer, "bot", () => currentStateVersion === chatStateVersion);
    if (!didComplete || currentStateVersion !== chatStateVersion) {
      return;
    }
    pushExchange(requestText, data.answer);
    setStatus("");
    noteUserActivity(true);
  } catch (error) {
    if (currentStateVersion !== chatStateVersion) {
      return;
    }
    removeThinkingMessage(thinkingMessage);
    addMessage(BOT_NAME, `接続エラーだよ。バックエンドが起動しているか確認してね。\n${error.message}`, "bot");
    setStatus("エラー", "error");
  } finally {
    removeThinkingMessage(thinkingMessage);
    if (currentStateVersion !== chatStateVersion) {
      return;
    }
    setInputLocked(false);
    question.focus();
  }
}

function bindEvents() {
  if (passwordForm) {
    passwordForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = passwordInput ? passwordInput.value.trim() : "";
      if (VALID_PASSWORDS.has(input)) {
        if (passwordInput) {
          passwordInput.value = "";
        }
        openTitleScreen();
        return;
      }
      setPasswordError("パスワードが違います。");
      if (passwordInput) {
        passwordInput.focus();
      }
    });
  }

  if (enterChat) {
    enterChat.addEventListener("click", () => {
      openChatScreen();
    });
  }

  if (backToHome) {
    backToHome.addEventListener("click", () => {
      openTitleScreen();
    });
  }

  const passiveActivityEvents = ["pointerdown", "wheel", "touchstart", "input"];
  passiveActivityEvents.forEach((eventName) => {
    document.addEventListener(
      eventName,
      () => {
        noteUserActivity();
      },
      { capture: true, passive: true },
    );
  });

  document.addEventListener(
    "keydown",
    () => {
      noteUserActivity();
    },
    { capture: true },
  );

  if (messages) {
    messages.addEventListener(
      "scroll",
      () => {
        noteUserActivity();
      },
      { passive: true },
    );
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      sendQuestion(question.value);
    });
  }

  quickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      sendQuestion(button.textContent);
    });
  });

  if (question) {
    question.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendQuestion(question.value);
      }
    });
  }
}

async function initialize() {
  await loadHomeReturnSeconds();
  bindEvents();
  showScreen(screenPassword);
  if (passwordInput) {
    passwordInput.focus();
  }
}

initialize();
