const API_URL = (() => {
  if (window.location.port === "3000") {
    const protocol = window.location.protocol === "https:" ? "https" : "http";
    return `${protocol}://${window.location.hostname}:8000/api/chat`;
  }
  return "/api/chat";
})();
const HEALTH_URL = API_URL.replace(/\/chat$/, "/health");
const FEEDBACK_URL = API_URL.replace(/\/chat$/, "/feedback");
const DEFAULT_HOME_RETURN_SECONDS = 30;
const DEFAULT_HISTORY_MAX_EXCHANGES = 5;
const DEFAULT_HISTORY_WINDOW_MINUTES = 2;
const TYPING_INTERVAL_MS = 18;
const VALID_PASSWORDS = new Set(["commons", "commons."]);
const BOT_NAME = "コモ";
const INITIAL_BOT_MESSAGE = "やっほー！コモだよ。質問を入力してね。";
const INITIAL_QUICK_QUESTION_POOL = [
  "中部大学とは？",
  "建学の精神は？",
  "基本理念を教えて",
  "学生数はどれくらい？",
  "どんな学部がある？",
  "工学部には何学科ある？",
  "理工学部について教えて",
  "応用生物学部の特徴は？",
  "生命健康科学部について知りたい",
  "教育学部では何を学べる？",
  "国際交流制度はある？",
  "PASEOとは？",
  "留学制度について教えて",
  "キャリア支援は何がある？",
  "C-NETとは？",
  "Web面接用ブースはある？",
  "ラーニング・コモンズとは？",
  "スチューデント・コモンズとは？",
  "コモンズのルールは？",
  "不言実行館には何がある？",
  "学食の種類を教えて",
  "人気の食堂は？",
  "スタバはどこ？",
  "パン屋はある？",
  "クラブ・サークルについて教えて",
  "注目のクラブは？",
  "国際学科と英語英米文化学科の違いは？",
  "工学部と理工学部の違いは？",
  "教員免許は取れる？",
  "国家試験が必要な学科は？",
  "大学院はある？",
  "入試情報を知りたい",
  "奨学金について教えて",
  "アクセスを教えて",
  "オープンキャンパスはいつ？",
];

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
let configuredHistoryMaxExchanges = DEFAULT_HISTORY_MAX_EXCHANGES;
let configuredHistoryWindowMs = DEFAULT_HISTORY_WINDOW_MINUTES * 60 * 1000;
let homeReturnTimerId = null;
let warningTickerId = null;
let homeReturnDeadlineTs = 0;
let lastActivityResetAt = 0;
let chatStateVersion = 0;
let lastEnterSubmitAt = 0;
let questionIsComposing = false;
let currentInitialQuickQuestions = [];

const FEEDBACK_OPTIONS = [
  { value: "knowledge_missing", label: "知識がない" },
  { value: "wrong_answer", label: "答えが違う" },
  { value: "hard_to_understand", label: "わかりにくい" },
  { value: "knowledge_request", label: "知識を追加してほしい" },
  { value: "other", label: "その他" },
];

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
    openTitleScreen({ resetChat: true });
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

function openTitleScreen({ resetChat = false } = {}) {
  clearHomeReturnTimer();
  clearWarningTicker();
  if (resetChat) {
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

function createMessageElements(name, type) {
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
  return { message, messageText };
}

function createMessageElement(name, type) {
  return createMessageElements(name, type).messageText;
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

function setQuickQuestionLabels(labels) {
  Array.from(quickButtons).forEach((button, index) => {
    if (labels[index]) {
      button.textContent = labels[index];
    }
  });
}

function shuffleArray(items) {
  const shuffled = [...items];
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }
  return shuffled;
}

function pickRandomInitialQuickQuestions(count, previous = []) {
  if (count <= 0) {
    return [];
  }

  if (INITIAL_QUICK_QUESTION_POOL.length <= count) {
    return INITIAL_QUICK_QUESTION_POOL.slice(0, count);
  }

  let picked = shuffleArray(INITIAL_QUICK_QUESTION_POOL).slice(0, count);
  const previousSignature = previous.join("||");
  let retryCount = 0;
  while (picked.join("||") === previousSignature && retryCount < 5) {
    picked = shuffleArray(INITIAL_QUICK_QUESTION_POOL).slice(0, count);
    retryCount += 1;
  }
  return picked;
}

function resetQuickQuestions() {
  currentInitialQuickQuestions = pickRandomInitialQuickQuestions(
    quickButtons.length,
    currentInitialQuickQuestions,
  );
  setQuickQuestionLabels(currentInitialQuickQuestions);
}

function renderRecommendedQuestions(questions, canAnswer) {
  if (!canAnswer || !Array.isArray(questions)) {
    return;
  }

  const normalizedQuestions = questions
    .map((item) => String(item).trim())
    .filter((item, index, list) => item && list.indexOf(item) === index)
    .slice(0, 3);
  if (normalizedQuestions.length < 3) {
    return;
  }

  quickButtons.forEach((button, index) => {
    button.style.setProperty("--quick-index", index);
    button.classList.remove("quick-button-fetched");
    button.classList.add("quick-button-fetching");
  });

  window.setTimeout(() => {
    setQuickQuestionLabels(normalizedQuestions);
    quickButtons.forEach((button) => {
      button.classList.remove("quick-button-fetching");
      button.classList.add("quick-button-fetched");
    });
    window.setTimeout(() => {
      quickButtons.forEach((button) => {
        button.classList.remove("quick-button-fetched");
      });
    }, 420);
  }, 130);
}

function handleQuickQuestionClick(button) {
  const text = button.textContent.trim();
  if (!text) {
    return;
  }
  sendQuestion(text);
}

function bindQuickQuestionButtons() {
  quickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      handleQuickQuestionClick(button);
    });
  });
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
  resetQuickQuestions();
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
  const { message, messageText } = createMessageElements(name, type);
  let current = "";
  for (const char of text) {
    if (!shouldContinue()) {
      return { completed: false, message };
    }
    current += char;
    messageText.textContent = current;
    messages.scrollTop = messages.scrollHeight;
    await sleep(TYPING_INTERVAL_MS);
  }
  return { completed: true, message };
}

async function postFeedback(payload) {
  const response = await fetch(FEEDBACK_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`feedback error: ${response.status}`);
  }

  return response.json();
}

function renderFeedbackPrompt(message, payload) {
  if (!message || !payload?.chatLogId) {
    return;
  }

  const scrollFeedbackIntoView = () => {
    window.requestAnimationFrame(() => {
      if (message.scrollIntoView) {
        message.scrollIntoView({
          behavior: "smooth",
          block: "end",
        });
      }
      messages.scrollTop = messages.scrollHeight;
    });
  };

  const container = document.createElement("section");
  container.className = "message-feedback";

  const title = document.createElement("p");
  title.className = "message-feedback-title";
  title.textContent = "この回答はどうだった？";

  const actionRow = document.createElement("div");
  actionRow.className = "message-feedback-actions";

  const helpfulButton = document.createElement("button");
  helpfulButton.type = "button";
  helpfulButton.className = "feedback-chip feedback-chip-positive";
  helpfulButton.textContent = "役に立った";

  const notHelpfulButton = document.createElement("button");
  notHelpfulButton.type = "button";
  notHelpfulButton.className = "feedback-chip feedback-chip-negative";
  notHelpfulButton.textContent = "足りなかった";

  actionRow.append(helpfulButton, notHelpfulButton);

  const detailPanel = document.createElement("div");
  detailPanel.className = "message-feedback-detail hidden";

  const detailLabel = document.createElement("p");
  detailLabel.className = "message-feedback-detail-label";
  detailLabel.textContent = "どこが足りなかった？";

  const optionRow = document.createElement("div");
  optionRow.className = "message-feedback-options";

  let selectedType = "knowledge_missing";
  const updateFeedbackDetailCopy = () => {
    const isKnowledgeRequest = selectedType === "knowledge_request";
    detailLabel.textContent = isKnowledgeRequest
      ? "どのような知識ですか？"
      : "どこが足りなかった？";
    commentInput.placeholder = isKnowledgeRequest
      ? "例：自販機の場所、トイレの場所、不言実行館で借りられるもの"
      : "知りたかったこと、足りなかった点があれば教えてね";
  };

  const optionButtons = FEEDBACK_OPTIONS.map((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "feedback-chip";
    button.dataset.feedbackType = option.value;
    button.textContent = option.label;
    if (option.value === selectedType) {
      button.classList.add("selected");
    }
    button.addEventListener("click", () => {
      selectedType = option.value;
      optionButtons.forEach((item) => {
        item.classList.toggle("selected", item === button);
      });
      updateFeedbackDetailCopy();
      status.textContent = "";
      status.classList.remove("error");
    });
    optionRow.appendChild(button);
    return button;
  });

  const commentInput = document.createElement("textarea");
  commentInput.className = "feedback-comment";
  commentInput.rows = 3;
  commentInput.maxLength = 500;
  commentInput.placeholder = "知りたかったこと、足りなかった点があれば教えてね";

  const submitButton = document.createElement("button");
  submitButton.type = "button";
  submitButton.className = "feedback-submit";
  submitButton.textContent = "送信";

  const status = document.createElement("p");
  status.className = "message-feedback-status";

  detailPanel.append(detailLabel, optionRow, commentInput, submitButton, status);
  container.append(title, actionRow, detailPanel);
  message.appendChild(container);

  const setFeedbackDisabled = (disabled) => {
    helpfulButton.disabled = disabled;
    notHelpfulButton.disabled = disabled;
    submitButton.disabled = disabled;
    optionButtons.forEach((button) => {
      button.disabled = disabled;
    });
    commentInput.disabled = disabled;
  };

  const markFeedbackDone = (text) => {
    actionRow.remove();
    detailPanel.remove();
    const done = document.createElement("p");
    done.className = "message-feedback-status done";
    done.textContent = text;
    container.appendChild(done);
    scrollFeedbackIntoView();
  };

  const submitFeedback = async ({ helpful, feedbackType, comment }) => {
    setFeedbackDisabled(true);
    status.textContent = "送信中...";
    status.classList.remove("error");

    try {
      await postFeedback({
        chat_log_id: payload.chatLogId,
        helpful,
        feedback_type: feedbackType,
        comment,
      });
      markFeedbackDone(
        helpful
          ? "フィードバックありがとう。"
          : "フィードバックありがとう。改善に活かします。",
      );
    } catch (error) {
      status.textContent = "送信できなかったよ。時間をおいてもう一度試してね。";
      status.classList.add("error");
      setFeedbackDisabled(false);
    }
  };

  helpfulButton.addEventListener("click", () => {
    submitFeedback({
      helpful: true,
      feedbackType: "helpful",
      comment: "",
    });
  });

  notHelpfulButton.addEventListener("click", () => {
    detailPanel.classList.remove("hidden");
    status.textContent = "";
    commentInput.focus();
    scrollFeedbackIntoView();
  });

  submitButton.addEventListener("click", () => {
    const comment = commentInput.value.trim();
    if (selectedType === "knowledge_request" && !comment) {
      status.textContent = "追加してほしい知識を書いてから送信してね。";
      status.classList.add("error");
      commentInput.focus();
      scrollFeedbackIntoView();
      return;
    }

    submitFeedback({
      helpful: false,
      feedbackType: selectedType,
      comment,
    });
  });

  updateFeedbackDetailCopy();
  scrollFeedbackIntoView();
}

function buildHistoryPrompt(currentQuestion) {
  pruneExchangeHistory();
  if (configuredHistoryMaxExchanges <= 0 || configuredHistoryWindowMs <= 0) {
    return currentQuestion;
  }

  const now = Date.now();
  const recent = exchangeHistory
    .filter((item) => now - item.ts <= configuredHistoryWindowMs)
    .slice(-configuredHistoryMaxExchanges);

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
  pruneExchangeHistory();
}

function pruneExchangeHistory() {
  if (configuredHistoryMaxExchanges <= 0 || configuredHistoryWindowMs <= 0) {
    exchangeHistory.length = 0;
    return;
  }

  const now = Date.now();
  while (exchangeHistory.length > 0 && now - exchangeHistory[0].ts > configuredHistoryWindowMs) {
    exchangeHistory.shift();
  }

  if (exchangeHistory.length > configuredHistoryMaxExchanges) {
    exchangeHistory.splice(0, exchangeHistory.length - configuredHistoryMaxExchanges);
  }
}

async function loadRuntimeSettings() {
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

    const historyMax = Number.parseInt(data.chat_history_max_exchanges, 10);
    if (Number.isFinite(historyMax) && historyMax >= 0) {
      configuredHistoryMaxExchanges = historyMax;
    }

    const historyWindowMinutes = Number.parseFloat(data.chat_history_window_minutes);
    if (Number.isFinite(historyWindowMinutes) && historyWindowMinutes >= 0) {
      configuredHistoryWindowMs = historyWindowMinutes * 60 * 1000;
    }

    pruneExchangeHistory();
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
        knowledge_mode: "search",
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
    const typeResult = await typeMessage(
      BOT_NAME,
      data.answer,
      "bot",
      () => currentStateVersion === chatStateVersion,
    );
    if (!typeResult.completed || currentStateVersion !== chatStateVersion) {
      return;
    }
    pushExchange(requestText, data.answer);
    renderRecommendedQuestions(data.recommended_questions, data.can_answer);
    renderFeedbackPrompt(typeResult.message, {
      chatLogId: data.chat_log_id,
    });
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
      openTitleScreen({ resetChat: true });
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

  if (question) {
    const submitFromEnter = () => {
      const now = Date.now();
      if (now - lastEnterSubmitAt < 120) {
        return;
      }
      lastEnterSubmitAt = now;
      sendQuestion(question.value);
    };

    question.addEventListener("compositionstart", () => {
      questionIsComposing = true;
    });

    question.addEventListener("compositionend", () => {
      questionIsComposing = false;
    });

    question.addEventListener("keydown", (event) => {
      const isEnterKey =
        event.key === "Enter" ||
        event.code === "Enter" ||
        event.code === "NumpadEnter" ||
        event.keyCode === 13;
      if (!isEnterKey) {
        return;
      }
      if (event.isComposing || questionIsComposing || event.keyCode === 229) {
        return;
      }
      if (event.shiftKey) {
        return;
      }
      event.preventDefault();
      submitFromEnter();
    });
  }

  bindQuickQuestionButtons();
}

async function initialize() {
  await loadRuntimeSettings();
  resetQuickQuestions();
  bindEvents();
  showScreen(screenPassword);
  if (passwordInput) {
    passwordInput.focus();
  }
}

initialize();
