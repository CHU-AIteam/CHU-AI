(() => {
  "use strict";

  const API = "/api/admin/knowledge";
  const SESSION_KEY = "como.knowledge-admin.session";
  const MAX_FILE_BYTES = 8 * 1024 * 1024;
  const MAX_BACKUP_BYTES = 64 * 1024 * 1024;
  const BACKUP_JOB_KEY = "como.knowledge-admin.backup-job";
  const $ = (id) => document.getElementById(id);
  const nodes = Object.fromEntries(Array.from(document.querySelectorAll("[id]"), (node) => [node.id, node]));
  const state = {
    key: "", connected: false, status: null, view: "import", originView: "import",
    requestBusy: false, externalBusy: false, preview: null, previewDirty: false,
    editorTarget: null, editorReady: false, editorEnabled: true, restoreVersion: null, unsaved: false,
    job: null, jobKind: "publish", pollTimer: null, statusTimer: null, statusVersion: 0,
    backup: null, backupJob: null,
  };

  class AdminError extends Error {
    constructor(message, status = 0) { super(message); this.status = status; }
  }

  function safeMessage(value, fallback) {
    if (typeof value !== "string" || !value.trim() || value.length > 500) return fallback;
    if (/(?:api[_ -]?key|authorization|postgres(?:ql)?:|Bearer\s|AIza|-----BEGIN|[A-Za-z0-9_-]{40,})/i.test(value)) return fallback;
    return value;
  }

  function notify(message = "", tone = "info") {
    nodes.notice.textContent = message;
    nodes.notice.dataset.tone = tone;
    nodes.notice.hidden = !message;
    nodes.notice.setAttribute("role", tone === "error" ? "alert" : "status");
    nodes.notice.setAttribute("aria-live", tone === "error" ? "assertive" : "polite");
  }

  function storeKey(key) {
    state.key = key;
    try {
      if (key) sessionStorage.setItem(SESSION_KEY, key);
      else sessionStorage.removeItem(SESSION_KEY);
    } catch { /* The current window can still connect when session storage is unavailable. */ }
  }

  function takeInitialKey() {
    const url = new URL(window.location.href);
    const initial = new URLSearchParams(url.hash.slice(1)).get("key");
    // Remove credentials before any request or external navigation. Query keys are never used.
    if (url.hash || url.searchParams.has("key")) {
      url.hash = "";
      url.searchParams.delete("key");
      history.replaceState(null, "", url.pathname + url.search);
    }
    if (initial?.trim()) return initial.trim();
    try { return sessionStorage.getItem(SESSION_KEY) || ""; } catch { return ""; }
  }

  function clearTimers() {
    clearTimeout(state.pollTimer);
    clearTimeout(state.statusTimer);
    state.pollTimer = null;
    state.statusTimer = null;
  }

  function showConnection() {
    clearTimers();
    state.connected = false;
    state.job = null;
    state.externalBusy = false;
    nodes.workspace.hidden = true;
    nodes["connection-panel"].hidden = false;
    nodes.disconnect.hidden = true;
  }

  async function api(path, { method = "GET", body } = {}) {
    let response;
    try {
      response = await fetch(API + path, {
        method, credentials: "omit", cache: "no-store",
        headers: { "X-Admin-Key": state.key, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch {
      throw new AdminError("コモに接続できません。デスクトップの「コモ管理」で起動状態を確認し、もう一度お試しください。");
    }
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      if (response.status === 401) {
        storeKey("");
        showConnection();
        throw new AdminError("管理画面に接続できません。「コモ管理」から開き直すか、管理用の接続キーを確認してください。", response.status);
      }
      if (response.status === 403) throw new AdminError("この操作は許可されていません。反映先と管理画面の接続方法を確認してください。", 403);
      if (response.status === 409) {
        if (safeMessage(data?.detail, "").includes("有効期限") && state.preview) {
          const fromFile = state.originView === "import";
          recoverExpiredPreview();
          throw new AdminError("確認画面の有効期限が切れました。入力した内容は編集欄に残しています。もう一度「変更内容を確認する」を押してください。" +
            (fromFile ? "元のWordなどの資料も保存する場合は、USBのファイルを選び直してください。" : ""), 409);
        }
        if (state.preview) state.previewDirty = true;
        applyLocks();
        throw new AdminError("別の更新が行われたか、更新処理が実行中です。「最新の状態を確認」を押し、内容を確認し直してください。", 409);
      }
      if (response.status === 503) {
        throw new AdminError(safeMessage(data?.detail?.message, "管理画面を利用する準備が整っていません。初期設定とコモの起動状態を確認してください。"), 503);
      }
      const fallback = response.status >= 500
        ? "処理を完了できませんでした。現在の知識はそのまま利用できます。起動状態を確認して、もう一度お試しください。"
        : "内容を確認できませんでした。ファイル、タイトル、反映先を確認して、もう一度お試しください。";
      throw new AdminError(response.status >= 500 ? fallback : safeMessage(data?.detail, fallback), response.status);
    }
    if (data === null) throw new AdminError("コモからの応答を確認できませんでした。もう一度お試しください。");
    return data;
  }

  function locked() { return state.requestBusy || Boolean(state.job) || state.externalBusy; }
  function needsPreparation() { return state.status?.initialization_required === true; }

  function applyLocks() {
    const disabled = locked() || !state.connected;
    const unavailable = disabled || needsPreparation();
    document.querySelectorAll("[data-mutation]").forEach((node) => { node.disabled = unavailable || node.dataset.readOnly === "true"; });
    nodes["import-fields"].disabled = unavailable;
    nodes["edit-fields"].disabled = unavailable || !state.editorReady;
    nodes["use-replacement"].disabled = unavailable || !nodes["import-target"].value;
    nodes["restore-doc"].disabled = unavailable || !nodes["doc-version"].value;
    nodes.initialize.disabled = disabled;
    const targetRequired = state.preview?.needs_target_confirmation;
    nodes.publish.disabled = unavailable || !state.preview || targetRequired || (!state.previewDirty && !nodes["publish-confirm"].checked);
    nodes["publish-confirm"].disabled = unavailable || state.previewDirty || targetRequired;
    nodes.restore.disabled = unavailable || !state.restoreVersion || !nodes["restore-confirm"].checked;
    nodes["restore-confirm"].disabled = unavailable;
    nodes["back-to-form"].disabled = disabled;
    nodes["cancel-restore"].disabled = disabled;
    nodes["save-backup"].disabled = unavailable;
    nodes["choose-backup"].disabled = disabled;
    nodes["restore-backup"].disabled = disabled || !state.backup || !nodes["backup-confirm"].checked;
    document.querySelectorAll("[data-view]").forEach((node) => { node.disabled = disabled; });
    nodes.publish.textContent = state.previewDirty ? "修正内容を確認する" : "この内容をコモに反映する";
    nodes["connection-status"].textContent = disabled && state.connected ? "更新・確認中" : needsPreparation() ? "最初の準備が必要です" : "知識を更新できます";
    nodes["status-dot"].dataset.state = disabled || needsPreparation() ? "busy" : "ready";
  }

  function formatDate(value) {
    if (!value) return "まだ更新されていません";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "日時を確認できません";
    return new Intl.DateTimeFormat("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function documents() { return Array.isArray(state.status?.documents) ? state.status.documents : []; }
  function historyItems() { return Array.isArray(state.status?.history) ? state.status.history : []; }

  function renderDocuments() {
    const query = nodes["document-filter"].value.trim().toLocaleLowerCase("ja-JP");
    const filtered = documents().filter((item) => `${item.title || ""} ${item.source_filename || ""} ${item.id}`.toLocaleLowerCase("ja-JP").includes(query));
    nodes["document-count"].textContent = `${filtered.length.toLocaleString("ja-JP")}件の知識`;
    nodes["document-list"].replaceChildren();
    if (!filtered.length) nodes["document-list"].append(make("p", query ? "見つかりませんでした。別の言葉で探してください。" : "まだ知識が登録されていません。", "empty-state"));
    for (const item of filtered) {
      const button = make("button", undefined, "document-item");
      button.type = "button";
      button.dataset.mutation = "";
      button.dataset.readOnly = String(item.editable === false);
      button.setAttribute("aria-current", String(String(item.id) === String(state.editorTarget)));
      button.disabled = item.editable === false || locked() || needsPreparation();
      button.append(make("strong", item.title || item.source_filename || item.id));
      button.append(make("small", (item.enabled === false ? "利用停止中 ／ " : "") + (item.source_filename || item.id)));
      button.addEventListener("click", () => editDocument(item.id));
      nodes["document-list"].append(button);
    }
    const selected = nodes["import-target"].value;
    nodes["import-target"].replaceChildren(new Option("置き換える知識を選んでください", ""));
    const suggestions = new Set((state.preview?.suggested_targets || []).map((item) => item.id));
    const sorted = [...documents()].sort((a, b) => Number(suggestions.has(b.id)) - Number(suggestions.has(a.id)));
    for (const item of sorted) {
      if (item.editable !== false) nodes["import-target"].append(new Option(item.title || item.source_filename || item.id, String(item.id)));
    }
    if (documents().some((item) => String(item.id) === selected)) nodes["import-target"].value = selected;
  }

  function renderHistory() {
    nodes["history-list"].replaceChildren();
    if (!historyItems().length) nodes["history-list"].append(make("p", "更新履歴はまだありません。知識を反映すると、ここに記録されます。", "empty-state"));
    for (const version of historyItems()) {
      const row = make("article", undefined, "history-item");
      const description = make("div");
      description.append(make("h3", version.label || "知識の更新"));
      description.append(make("p", `知識 ${Number(version.document_count || 0).toLocaleString("ja-JP")}件`));
      row.append(description, make("time", formatDate(version.created_at), "history-date"));
      if (version.current || version.id === state.status?.active_version) {
        row.append(make("span", "現在の知識", "current-tag"));
      } else {
        const button = make("button", "この時点に戻す", "secondary-button");
        button.type = "button";
        button.dataset.mutation = "";
        button.addEventListener("click", () => selectRestore(version));
        row.append(button);
      }
      nodes["history-list"].append(row);
    }
  }

  async function loadStatus() {
    const version = ++state.statusVersion;
    const data = await api("/status");
    if (version !== state.statusVersion) return;
    state.status = data;
    state.connected = true;
    state.externalBusy = Boolean(data.busy) && !state.job;
    nodes["connection-panel"].hidden = true;
    nodes.workspace.hidden = false;
    nodes.disconnect.hidden = false;
    nodes["initialize-panel"].hidden = !needsPreparation();
    const lastJob = data.last_job;
    const previous = lastJob?.status === "failed" || lastJob?.status === "succeeded";
    nodes["last-operation"].hidden = !previous || !nodes["job-panel"].hidden;
    if (previous) {
      const label = lastJob.status === "failed" ? "前回の処理は完了しませんでした" : "前回の処理は完了しました";
      nodes["last-operation"].textContent = formatDate(lastJob.updated_at) + " ／ " + label + "。" + safeMessage(lastJob.error, "");
      nodes["last-operation"].dataset.tone = lastJob.status;
    }
    nodes.initialize.textContent = needsPreparation() && lastJob?.status === "failed" ? "もう一度準備する" : "準備をはじめる";
    nodes["last-updated"].textContent = formatDate(data.last_updated);
    if (data.last_updated) nodes["last-updated"].dateTime = data.last_updated;
    renderDocuments();
    renderHistory();
    applyLocks();
    const activeJob = data.active_job;
    const activeId = typeof activeJob === "string" ? activeJob : activeJob?.id || activeJob?.job_id;
    if (!state.job && data.busy && activeId && !["succeeded", "failed"].includes(activeJob?.status)) {
      const kind = lastJob?.id === activeId ? lastJob.kind : activeJob?.kind;
      startWatchingJob(activeId, kind || "publish");
    } else if (state.externalBusy) {
      nodes["connection-status"].textContent = "別の更新処理を待っています";
      clearTimeout(state.statusTimer);
      state.statusTimer = setTimeout(() => loadStatus().catch((error) => notify(error.message, "error")), 2000);
    } else if (!state.job && pendingBackup()) {
      startWatchingJob(pendingBackup().id, "backup");
    }
  }

  async function perform(callback) {
    if (locked()) return;
    state.requestBusy = true;
    applyLocks();
    notify();
    try { await callback(); } catch (error) { notify(error.message || "処理を完了できませんでした。もう一度お試しください。", "error"); }
    finally { state.requestBusy = false; applyLocks(); }
  }

  async function connect(key) {
    if (!key.trim()) return;
    storeKey(key.trim());
    nodes["connection-key"].value = "";
    nodes.connect.disabled = true;
    notify("コモに接続しています。");
    try { await loadStatus(); notify(); }
    catch (error) { notify(error.message, "error"); }
    finally { nodes.connect.disabled = false; }
  }

  function setView(view, focus = false) {
    if (!["import", "edit", "history", "backup"].includes(view)) return;
    state.view = view;
    nodes["preview-panel"].hidden = true;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const selected = button.dataset.view === view;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      nodes[`view-${button.dataset.view}`].hidden = !selected;
    });
    if (focus) nodes[`tab-${view}`].focus();
  }

  function recoverExpiredPreview() {
    const preview = state.preview;
    const title = nodes["preview-title"].value;
    const content = nodes["preview-content"].value;
    state.editorTarget = preview.action === "replace" ? preview.target_id : null;
    state.editorReady = true;
    nodes["document-actions"].hidden = true;
    state.preview = null;
    state.previewDirty = false;
    state.originView = "edit";
    nodes["edit-title"].value = title;
    nodes["edit-content"].value = content;
    nodes["editor-legend"].textContent = "確認の期限が切れた内容を編集";
    nodes["editor-source"].textContent = "入力内容は保持しています。内容を確認し直すと、新しい確認画面を作成します。";
    nodes["publish-confirm"].checked = false;
    setView("edit");
    renderDocuments();
    applyLocks();
    requestAnimationFrame(() => {
      if (state.view === "edit" && state.editorReady && !locked()) nodes["edit-title"].focus();
    });
  }

  function renderSteps(step, complete = false) {
    document.querySelectorAll("[data-step]").forEach((item) => {
      const number = Number(item.dataset.step);
      item.classList.toggle("current", !complete && number === step);
      item.classList.toggle("complete", complete || number < step);
      if (number === step && !complete) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
    });
  }

  function showPreview(preview, origin) {
    if (!preview?.preview_id || typeof preview.content !== "string") throw new AdminError("読み取った内容を確認できませんでした。もう一度読み取りをお試しください。");
    state.preview = preview;
    state.previewDirty = false;
    state.originView = origin;
    document.querySelectorAll('[role="tabpanel"]').forEach((panel) => { panel.hidden = true; });
    nodes["preview-panel"].hidden = false;
    nodes["preview-title"].value = preview.title || "";
    nodes["preview-content"].value = preview.content;
    nodes["publish-confirm"].checked = false;
    const replacing = preview.action === "replace";
    nodes["preview-action"].textContent = replacing ? "登録済みの知識を置き換えます" : "新しい知識を追加します";
    const source = preview.source_filename ? `「${preview.source_filename}」` : "入力した文章";
    nodes["preview-summary"].textContent = replacing ? `${source}の内容で、登録済みの知識を置き換えます。現在の内容と見比べてください。` : `${source}を新しい知識として追加します。`;
    nodes["publish-confirm-label"].textContent = replacing ? "内容を確認し、現在の知識をこの内容で置き換えることを確認しました" : "内容を確認し、この知識を追加することを確認しました";
    nodes["previous-section"].hidden = !replacing;
    nodes["previous-content"].textContent = preview.previous_content || "現在の内容を確認できません。置き換え先を確認してください。";
    nodes["previous-section"].parentElement.classList.toggle("with-previous", replacing);
    const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];
    nodes["preview-warnings"].replaceChildren(...warnings.map((warning) => make("li", safeMessage(warning, "読み取れない情報が含まれる可能性があります。反映する内容を確認してください。"))));
    nodes["preview-warnings"].hidden = !warnings.length;
    if (replacing && preview.previous_title) {
      nodes["preview-summary"].textContent = "「" + preview.previous_title + "」を " + source + " の内容で置き換えます。";
    }
    if (preview.needs_target_confirmation) {
      nodes["preview-action"].textContent = "同じ名前の資料が登録されています";
      nodes["preview-summary"].textContent = "置き換える資料を選ぶか、新しい資料として追加するかを選んでください。";
    }
    if (preview.enabled === false) {
      nodes["preview-summary"].textContent += " この資料は利用停止中です。再開するまで回答に使われません。";
    }
    nodes["import-destination"].hidden = origin !== "import";
    nodes["import-destination"].open = Boolean(preview.needs_target_confirmation);
    nodes["destination-summary"].textContent = preview.needs_target_confirmation ? "資料の反映先を選ぶ" : "反映先を変更";
    renderDocuments();
    nodes["import-target"].value = replacing ? preview.target_id : (preview.suggested_targets || [])[0]?.id || "";
    renderChanges(preview);
    renderSteps(2);
    applyLocks();
    nodes["preview-heading"].focus();
  }

  function canDiscardEdits() {
    return !state.unsaved || window.confirm("まだコモに反映していない編集があります。編集を破棄して進みますか？");
  }

  function selectTab(view, focus = false) {
    if (locked() || !canDiscardEdits()) return;
    if (state.unsaved) {
      state.unsaved = false;
      state.editorReady = false;
      state.editorTarget = null;
      state.preview = null;
      nodes["edit-title"].value = "";
      nodes["edit-content"].value = "";
      nodes["document-actions"].hidden = true;
    }
    setView(view, focus);
    applyLocks();
    notify();
  }

  function lineDifference(lines, other) {
    const counts = new Map();
    for (const line of other) counts.set(line, (counts.get(line) || 0) + 1);
    return lines.filter((line) => {
      const count = counts.get(line) || 0;
      if (count) counts.set(line, count - 1);
      return !count;
    });
  }

  function renderChanges(preview) {
    nodes["change-summary"].hidden = preview.action !== "replace";
    if (preview.action !== "replace") return;
    const before = (preview.previous_content || "").trim();
    const after = preview.content.trim();
    const lines = (value) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const oldLines = lines(before);
    const newLines = lines(after);
    const removed = lineDifference(oldLines, newLines);
    const added = lineDifference(newLines, oldLines);
    nodes["change-note"].textContent = before === after ? "本文に変更はありません。"
      : added.length || removed.length ? "追加 " + added.length + "行 ／ 削除 " + removed.length + "行"
      : "改行、書式、または文章の順序が変わっています。";
    const examples = [];
    for (const line of removed.slice(0, 6)) examples.push(make("li", "− " + line.slice(0, 180), "removed-line"));
    for (const line of added.slice(0, 6)) examples.push(make("li", "＋ " + line.slice(0, 180), "added-line"));
    nodes["change-lines"].replaceChildren(...examples);
  }

  async function confirmImportTarget(mode) {
    if (!state.preview || state.originView !== "import") return;
    await perform(async () => {
      const target = nodes["import-target"].value;
      if (mode === "replace" && !target) throw new AdminError("置き換える資料を選んでください。");
      const preview = await api("/preview", { method: "POST", body: {
        title: nodes["preview-title"].value.trim(),
        content: nodes["preview-content"].value.trim(),
        source_preview_id: state.preview.preview_id, import_mode: mode,
        ...(mode === "replace" ? { target_id: target } : {}),
      } });
      showPreview(preview, "import");
      notify();
    });
  }

  async function showDocumentHistory() {
    if (!state.editorTarget) return;
    await perform(async () => {
      const versions = await api("/documents/" + encodeURIComponent(state.editorTarget) + "/history");
      const older = versions.filter((item) => !item.current);
      nodes["doc-version"].replaceChildren(new Option(older.length ? "戻したい版を選んでください" : "以前の版はありません", ""));
      for (const item of older) {
        const label = formatDate(item.created_at) + " ／ " + item.title + (item.enabled ? "" : "（利用停止中）");
        nodes["doc-version"].append(new Option(label, item.id));
      }
      nodes["doc-history"].hidden = false;
    });
  }

  async function changeDocument(change) {
    if (!state.editorTarget || !canDiscardEdits()) return;
    const question = change.version_id ? "この資料だけ、選んだ日付の内容と利用状態に戻しますか？"
      : change.enabled ? "この資料をコモの回答に使うようにしますか？"
      : "この資料をコモの回答に使わないようにしますか？ 資料と履歴は残ります。";
    if (!window.confirm(question)) return;
    await perform(async () => {
      const job = await api("/documents/" + encodeURIComponent(state.editorTarget) + "/change", {
        method: "POST", body: { ...change, base_version: state.status.active_version },
      });
      startWatchingJob(job.job_id, "document");
    });
  }

  function pendingBackup() {
    if (state.backupJob) return state.backupJob;
    try {
      const saved = JSON.parse(localStorage.getItem(BACKUP_JOB_KEY) || "null");
      if (/^[a-f0-9]{32}$/.test(saved?.id || "")) return saved;
    } catch { /* Browser storage may be restricted. */ }
    return null;
  }

  function rememberBackup(value) {
    state.backupJob = value;
    try {
      if (value) localStorage.setItem(BACKUP_JOB_KEY, JSON.stringify(value));
      else localStorage.removeItem(BACKUP_JOB_KEY);
    } catch { /* Keep the job in the open window if storage is unavailable. */ }
  }

  function readTally() {
    try {
      const raw = localStorage.getItem(window.ComoFortuneTally.storageKey);
      if (raw === null) return Object.fromEntries(window.ComoFortuneTally.ids.map((id) => [id, 0]));
      const data = JSON.parse(raw);
      if (data?.version !== 1 || !data.counts) return null;
      const counts = Object.fromEntries(window.ComoFortuneTally.ids.map((id) => {
        const value = data.counts[id];
        return [id, Number.isSafeInteger(value) && value >= 0 ? value : 0];
      }));
      return Number.isSafeInteger(Object.values(counts).reduce((a, b) => a + b, 0)) ? counts : null;
    } catch { return null; }
  }

  function finishBackup(id) {
    const pending = pendingBackup();
    if (pending?.id !== id) return "";
    rememberBackup(null);
    if (!pending.tally) return "";
    try {
      localStorage.setItem(window.ComoFortuneTally.storageKey, JSON.stringify({ version: 1, counts: pending.tally }));
      return "";
    } catch {
      return "知識は復元できました。ブラウザーにおみくじ集計を保存できなかったため、集計は切り替えていません。";
    }
  }

  async function saveBackup() {
    await perform(async () => {
      const name = "como-backup-" + new Date().toISOString().slice(0, 10) + ".json";
      let destination = null;
      if (typeof window.showSaveFilePicker === "function") {
        try {
          destination = await window.showSaveFilePicker({
            suggestedName: name, types: [{ description: "コモのバックアップ", accept: { "application/json": [".json"] } }],
          });
        } catch (error) {
          if (error.name === "AbortError") return;
          if (error.name !== "SecurityError" && error.name !== "NotAllowedError") throw new AdminError("保存場所を開けませんでした。もう一度選んでください。");
        }
      }
      const tally = readTally();
      const archive = await api("/backup", { method: "POST", body: { tally } });
      const blob = new Blob([JSON.stringify(archive)], { type: "application/json" });
      if (destination) {
        const writer = await destination.createWritable();
        try {
          await writer.write(blob);
          await writer.close();
        } catch {
          await writer.abort().catch(() => {});
          throw new AdminError("バックアップを保存できませんでした。USBの空き容量と書き込み設定を確認してください。");
        }
        notify("バックアップを保存しました。", "success");
      } else {
        const url = URL.createObjectURL(blob);
        const link = make("a");
        link.href = url;
        link.download = name;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        notify("ダウンロードしたファイルをUSBなどに移してください。");
      }
    });
  }

  async function chooseBackup() {
    clearBackupReview();
    const file = nodes["backup-file"].files[0];
    if (!file) return;
    await perform(async () => {
      if (!file.size || file.size > MAX_BACKUP_BYTES) throw new AdminError("64MB以内のバックアップファイルを選んでください。");
      let archive;
      try { archive = JSON.parse(await file.text()); } catch { throw new AdminError("バックアップを読み取れません。ファイルを選び直してください。"); }
      const docs = archive?.versions?.at?.(-1)?.documents;
      if (archive?.format !== "chat-como-backup-1" || !Array.isArray(docs) || !docs.length) {
        throw new AdminError("コモ管理から保存したバックアップファイルを選んでください。");
      }
      state.backup = archive;
      nodes["backup-description"].textContent = file.name + " ／ " + formatDate(archive.created_at) + " ／ 資料 " + docs.length + " 件";
      nodes["backup-review"].hidden = false;
    });
  }

  function clearBackupReview() {
    state.backup = null;
    nodes["backup-confirm"].checked = false;
    nodes["backup-review"].hidden = true;
    applyLocks();
  }

  async function restoreBackup() {
    if (!state.backup || !nodes["backup-confirm"].checked || !canDiscardEdits()) return;
    await perform(async () => {
      const base = state.status.active_version;
      const url = "/backup/restore" + (base ? "?base_version=" + encodeURIComponent(base) : "");
      const job = await api(url, { method: "POST", body: state.backup });
      rememberBackup({ id: job.job_id, tally: state.backup.tally || null });
      startWatchingJob(job.job_id, "backup");
    });
  }

  function readBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1] || ""), { once: true });
      reader.addEventListener("error", () => reject(new AdminError("ファイルを読み込めません。USBの接続とファイルを確認してください。")), { once: true });
      reader.readAsDataURL(file);
    });
  }

  async function importFile(event) {
    event?.preventDefault();
    if (event && !canDiscardEdits()) return;
    await perform(async () => {
      const file = nodes["knowledge-file"].files[0];
      if (!file) throw new AdminError("USBのファイルを選んでください。");
      if (!/\.(docx|txt|md|markdown)$/i.test(file.name)) throw new AdminError("Word（.docx）、テキスト（.txt）、Markdown（.md）のファイルを選んでください。PDF・Excel・古いWord形式（.doc）は現在取り込めません。");
      if (!file.size) throw new AdminError("ファイルが空です。内容が保存されているファイルを選んでください。");
      if (file.size > MAX_FILE_BYTES) throw new AdminError("ファイルが大きすぎます。8MB以下の資料に分けて取り込んでください。");
      notify("ファイルの内容を読み取っています。少しお待ちください。");
      const preview = await api("/preview", { method: "POST", body: { filename: file.name, content_base64: await readBase64(file), import_mode: "choose" } });
      state.unsaved = false;
      notify();
      showPreview(preview, "import");
    });
  }

  async function editDocument(id) {
    if (!canDiscardEdits()) return;
    await perform(async () => {
      const item = await api(`/documents/${encodeURIComponent(id)}`);
      state.editorTarget = item.id || id;
      state.editorReady = true;
      state.editorEnabled = item.enabled !== false;
      state.unsaved = false;
      nodes["document-actions"].hidden = false;
      nodes["document-actions"].open = false;
      nodes["doc-history"].hidden = true;
      nodes["doc-version"].replaceChildren();
      nodes["set-enabled"].textContent = state.editorEnabled ? "この資料の利用を停止" : "この資料の利用を再開";
      nodes["edit-title"].value = item.title || "";
      nodes["edit-content"].value = item.content || "";
      nodes["editor-legend"].textContent = "知識の内容を編集";
      nodes["editor-source"].textContent = item.source_filename ? `元のファイル：${item.source_filename}` : "";
      setView("edit");
      renderDocuments();
      nodes["edit-title"].focus();
    });
  }

  function newDocument() {
    if (locked() || needsPreparation() || !canDiscardEdits()) return;
    state.editorTarget = null;
    state.editorReady = true;
    state.unsaved = false;
    nodes["document-actions"].hidden = true;
    nodes["edit-title"].value = "";
    nodes["edit-content"].value = "";
    nodes["editor-source"].textContent = "";
    nodes["editor-legend"].textContent = "新しい知識を入力";
    setView("edit");
    renderDocuments();
    applyLocks();
    nodes["edit-title"].focus();
  }

  async function previewEdit(event) {
    event.preventDefault();
    await perform(async () => {
      const title = nodes["edit-title"].value.trim();
      const content = nodes["edit-content"].value.trim();
      if (!title || !content) throw new AdminError("タイトルと内容を入力してください。");
      const preview = await api("/preview", { method: "POST", body: { title, content, ...(state.editorTarget ? { target_id: state.editorTarget } : {}) } });
      showPreview(preview, "edit");
    });
  }

  async function publish() {
    if (!state.preview || state.preview.needs_target_confirmation || locked()) return;
    await perform(async () => {
      if (state.previewDirty) {
        const title = nodes["preview-title"].value.trim();
        const content = nodes["preview-content"].value.trim();
        if (!title || !content) throw new AdminError("反映するタイトルと内容を入力してください。");
        const sameVersion = state.preview.base_version === state.status?.active_version;
        const preview = await api("/preview", { method: "POST", body: {
          title, content,
          ...(sameVersion ? { source_preview_id: state.preview.preview_id } : {}),
          ...(state.preview.action === "replace" && state.preview.target_id ? { target_id: state.preview.target_id } : {}),
        } });
        showPreview(preview, state.originView);
        notify("修正した内容を確認しました。内容を確認したら、確認欄にチェックを入れて反映してください。");
        return;
      }
      if (!nodes["publish-confirm"].checked) return;
      const job = await api("/publish", { method: "POST", body: { preview_id: state.preview.preview_id, base_version: state.preview.base_version } });
      startWatchingJob(job.job_id || job.id, "publish");
      renderSteps(3);
    });
  }

  function selectRestore(version) {
    if (locked() || needsPreparation()) return;
    state.restoreVersion = version;
    nodes["restore-confirm"].checked = false;
    nodes["restore-description"].textContent = `${formatDate(version.created_at)} ／ ${version.label || "知識の更新"} ／ 知識 ${Number(version.document_count || 0).toLocaleString("ja-JP")}件`;
    nodes["restore-panel"].hidden = false;
    applyLocks();
    nodes["restore-panel"].scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function restore() {
    if (!state.restoreVersion || !nodes["restore-confirm"].checked) return;
    await perform(async () => {
      const job = await api("/restore", { method: "POST", body: { version_id: state.restoreVersion.id, base_version: state.status.active_version } });
      startWatchingJob(job.job_id || job.id, "restore");
    });
  }

  function startWatchingJob(id, kind) {
    if (!id) throw new AdminError("更新を開始できたか確認できません。「最新の状態を確認」を押してください。");
    clearTimeout(state.pollTimer);
    clearTimeout(state.statusTimer);
    state.job = id;
    state.jobKind = kind;
    state.externalBusy = false;
    nodes["last-operation"].hidden = true;
    nodes["job-panel"].hidden = false;
    delete nodes["job-panel"].dataset.result;
    nodes["job-open-como"].hidden = true;
    const titles = {
      restore: "以前の知識に戻しています",
      initialize: "最初の準備をしています",
      backup: "バックアップを復元しています",
      document: "資料の状態を更新しています",
    };
    nodes["job-title"].textContent = titles[kind] || "知識を更新しています";
    nodes["job-state"].textContent = "準備中";
    nodes["job-message"].textContent = "処理を開始しました。完了まで少しお待ちください。";
    nodes["job-help"].textContent = "この画面で完了の表示をお待ちください。画面を開き直しても、実行中の更新は続きます。";
    nodes["job-progress"].removeAttribute("value");
    applyLocks();
    pollJob(id);
    nodes["job-panel"].scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function pollJob(id) {
    if (state.job !== id || !state.connected) return;
    try {
      const job = await api(`/jobs/${encodeURIComponent(id)}`);
      if (state.job !== id || !state.connected) return;
      const complete = job.status === "succeeded" || job.status === "failed";
      nodes["job-state"].textContent = job.status === "queued" ? "準備中" : job.status === "succeeded" ? "完了" : job.status === "failed" ? "完了できませんでした" : "実行中";
      nodes["job-message"].textContent = safeMessage(job.message, "知識を準備しています。少しお待ちください。");
      if (typeof job.progress === "number" && Number.isFinite(job.progress)) nodes["job-progress"].value = Math.max(0, Math.min(100, job.progress));
      else nodes["job-progress"].removeAttribute("value");
      if (!complete) { state.pollTimer = setTimeout(() => pollJob(id), 1200); return; }
      state.job = null;
      state.externalBusy = false;
      nodes["job-panel"].dataset.result = job.status;
      if (job.status === "succeeded") {
        const tallyError = finishBackup(id);
        nodes["job-progress"].value = 100;
        nodes["job-message"].textContent = state.jobKind === "restore" ? "以前の知識に戻しました。コモに質問して内容を確認してください。" : state.jobKind === "initialize" ? "準備ができました。知識の追加・編集を行えます。" : "コモに知識を反映しました。質問して内容を確認してください。";
        nodes["job-help"].textContent = "USBから取り込んだ資料は、このPCに保存されています。USBを抜いても利用できます。";
        nodes["job-open-como"].hidden = false;
        state.preview = null;
        state.previewDirty = false;
        state.editorReady = false;
        state.editorTarget = null;
        state.unsaved = false;
        nodes["document-actions"].hidden = true;
        state.restoreVersion = null;
        nodes["restore-panel"].hidden = true;
        nodes["publish-confirm"].checked = false;
        clearBackupReview();
        setView(state.view);
        renderSteps(3, true);
        notify(tallyError || "更新が完了しました。", tallyError ? "warning" : "success");
      } else {
        if (pendingBackup()?.id === id) rememberBackup(null);
        nodes["job-help"].textContent = state.jobKind === "initialize"
          ? "原因を確認してから「もう一度準備する」を押してください。"
          : "現在の知識はそのまま利用できます。原因を確認してからもう一度お試しください。";
        notify(safeMessage(job.error, "更新を完了できませんでした。コモの起動状態と資料の内容を確認して、もう一度お試しください。"), "error");
      }
      state.requestBusy = true;
      try { await loadStatus(); } catch (error) { notify(error.message, "error"); }
      finally { state.requestBusy = false; applyLocks(); }
    } catch (error) {
      if (state.job !== id || !state.connected) return;
      if (error.status === 404) {
        if (pendingBackup()?.id === id) rememberBackup(null);
        state.job = null;
        state.externalBusy = false;
        notify("更新状況を確認できません。「最新の状態を確認」で、知識の状態と履歴を確認してください。", "warning");
        try { await loadStatus(); } catch (statusError) { notify(statusError.message, "error"); }
        applyLocks();
      } else {
        notify("更新状況への接続が切れました。完了状況を再確認しています。再度「反映」を押さずにお待ちください。", "warning");
        state.pollTimer = setTimeout(() => pollJob(id), 3000);
      }
    }
  }

  nodes["connection-form"].addEventListener("submit", (event) => { event.preventDefault(); connect(nodes["connection-key"].value); });
  nodes.disconnect.addEventListener("click", () => { storeKey(""); showConnection(); notify("管理画面から切断しました。コモはそのまま利用できます。"); });
  nodes.refresh.addEventListener("click", async () => {
    nodes.refresh.disabled = true;
    try { await loadStatus(); notify("最新の状態を確認しました。"); } catch (error) { notify(error.message, "error"); }
    finally { nodes.refresh.disabled = false; }
  });
  nodes.initialize.addEventListener("click", () => perform(async () => {
    const job = await api("/initialize", { method: "POST" });
    startWatchingJob(job.job_id || job.id, "initialize");
  }));
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.view)));
  document.querySelector('.view-tabs').addEventListener("keydown", (event) => {
    const tabs = Array.from(document.querySelectorAll("[data-view]"));
    const index = tabs.indexOf(document.activeElement);
    if (index < 0 || locked() || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[next].dataset.view, true);
  });
  nodes["choose-file"].addEventListener("click", () => {
    if (!canDiscardEdits()) return;
    nodes["knowledge-file"].value = "";
    nodes["file-selection"].textContent = "ファイルはまだ選ばれていません";
    nodes["read-file"].hidden = true;
    nodes["knowledge-file"].click();
  });
  nodes["knowledge-file"].addEventListener("change", () => {
    const file = nodes["knowledge-file"].files[0];
    nodes["file-selection"].textContent = file ? `${file.name}（${(file.size / 1024).toLocaleString("ja-JP", { maximumFractionDigits: 0 })} KB）` : "ファイルはまだ選ばれていません";
    renderSteps(1);
    nodes["read-file"].hidden = !file;
    if (file) void importFile();
  });
  nodes["import-target"].addEventListener("change", applyLocks);
  nodes["use-replacement"].addEventListener("click", () => confirmImportTarget("replace"));
  nodes["use-add"].addEventListener("click", () => confirmImportTarget("add"));
  nodes["import-form"].addEventListener("submit", importFile);
  nodes["document-filter"].addEventListener("input", () => { renderDocuments(); applyLocks(); });
  nodes["new-document"].addEventListener("click", newDocument);
  nodes["edit-form"].addEventListener("submit", previewEdit);
  nodes["back-to-form"].addEventListener("click", () => {
    if (state.unsaved && state.originView === "import" && !canDiscardEdits()) return;
    if (state.unsaved && state.originView === "edit") {
      nodes["edit-title"].value = nodes["preview-title"].value;
      nodes["edit-content"].value = nodes["preview-content"].value;
    } else { state.unsaved = false; }
    setView(state.originView);
    renderSteps(1);
  });
  for (const id of ["edit-title", "edit-content"]) nodes[id].addEventListener("input", () => { state.unsaved = true; });
  for (const id of ["preview-title", "preview-content"]) nodes[id].addEventListener("input", () => {
    state.unsaved = true;
    state.previewDirty = true;
    nodes["publish-confirm"].checked = false;
    applyLocks();
  });
  nodes["publish-confirm"].addEventListener("change", applyLocks);
  nodes.publish.addEventListener("click", publish);
  nodes["restore-confirm"].addEventListener("change", applyLocks);
  nodes.restore.addEventListener("click", restore);
  nodes["cancel-restore"].addEventListener("click", () => { state.restoreVersion = null; nodes["restore-panel"].hidden = true; });
  nodes["set-enabled"].addEventListener("click", () => changeDocument({ enabled: !state.editorEnabled }));
  nodes["show-doc-history"].addEventListener("click", showDocumentHistory);
  nodes["doc-version"].addEventListener("change", applyLocks);
  nodes["restore-doc"].addEventListener("click", () => changeDocument({ version_id: nodes["doc-version"].value }));
  nodes["save-backup"].addEventListener("click", saveBackup);
  nodes["choose-backup"].addEventListener("click", () => {
    nodes["backup-file"].value = "";
    nodes["backup-file"].click();
  });
  nodes["backup-file"].addEventListener("change", chooseBackup);
  nodes["backup-confirm"].addEventListener("change", applyLocks);
  nodes["cancel-backup"].addEventListener("click", clearBackupReview);
  nodes["restore-backup"].addEventListener("click", restoreBackup);
  window.addEventListener("beforeunload", (event) => {
    if (!state.unsaved || state.job) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("pagehide", clearTimers);
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || !state.connected) return;
    loadStatus().then(() => { if (state.job) pollJob(state.job); }).catch((error) => notify(error.message, "error"));
  });

  const key = takeInitialKey();
  applyLocks();
  renderSteps(1);
  if (key) connect(key);
})();
