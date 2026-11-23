const $ = (id) => document.getElementById(id);

const ui = {
  statusPill: $("status-pill"),
  statusText: $("status-text"),
  startButton: $("start-button"),
  startLabel: $("start-label"),
  transportHint: $("transport-hint"),
  error: $("error-message"),
  setupBanner: $("setup-banner"),
  microphone: $("microphone-select"),
  output: $("output-select"),
  outputSupport: $("output-support"),
  micMeter: $("mic-meter"),
  outputMeter: $("output-meter"),
  micDb: $("mic-db"),
  outputDb: $("output-db"),
  duration: $("duration-value"),
  buffer: $("buffer-value"),
  tokens: $("tokens-value"),
  drops: $("drops-value"),
  cloneMode: $("clone-mode"),
  voiceIdField: $("voice-id-field"),
  voiceId: $("voice-id-input"),
  volume: $("volume-slider"),
  volumeValue: $("volume-value"),
  glossary: $("glossary-input"),
  transcript: $("transcript"),
  transcriptEmpty: $("transcript-empty"),
  transcriptTemplate: $("transcript-entry-template"),
  settingsDialog: $("settings-dialog"),
  settingsForm: $("settings-form"),
  settingsMessage: $("settings-message"),
  apiKey: $("api-key-input"),
  region: $("region-select"),
  workspace: $("workspace-input"),
  modelName: $("model-name"),
};

const MAX_TRANSCRIPT_ROWS = 200;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];

const runtime = {
  state: "idle",
  configured: false,
  socket: null,
  intentionalClose: false,
  acceptAudio: false,
  outputDirty: false,
  reconnectAttempts: 0,
  reconnectTimer: null,
  mediaStream: null,
  captureContext: null,
  captureNode: null,
  captureSource: null,
  captureMute: null,
  playbackContext: null,
  playbackNode: null,
  playbackGain: null,
  playbackElement: null,
  mediaDestination: null,
  bufferedMs: 0,
  playbackDrained: false,
  underruns: 0,
  droppedSamples: 0,
  startedAt: 0,
  durationTimer: null,
  finishTimer: null,
  noticeTimer: null,
  rows: [],
  sourceRows: new Map(),
  translationRows: new Map(),
  scrollPending: false,
};

const statusNames = {
  idle: "Остановлено",
  connecting: "Подключение",
  reconnecting: "Восстанавливаю связь",
  listening: "Слушаю · говорите",
  translating: "Перевожу",
  finishing: "Завершаю фразу",
  error: "Ошибка",
};

function setState(state) {
  runtime.state = state;
  ui.statusPill.dataset.state = state;
  ui.statusText.textContent = statusNames[state] || state;
  const active = !["idle", "error"].includes(state);
  ui.startButton.dataset.running = String(active);
  // Кнопка остаётся доступной даже во время подключения: его можно отменить.
  ui.startButton.disabled = false;
  ui.startLabel.textContent = active
    ? state === "finishing"
      ? "Остановить сейчас"
      : ["connecting", "reconnecting"].includes(state)
        ? "Отмена"
        : "Остановить перевод"
    : "Начать перевод";
  ui.transportHint.textContent = state === "listening"
    ? "Говорите короткими фразами. Русский микрофон не подключён к динамикам."
    : state === "translating"
      ? "Идёт перевод. Дождитесь китайской озвучки перед следующей фразой."
      : state === "finishing"
        ? "Получаем и проигрываем конец последней фразы. Нажмите ещё раз для немедленной остановки."
        : ["connecting", "reconnecting"].includes(state)
          ? "Соединяемся с моделью. Нажмите «Отмена», чтобы вернуться в исходное состояние."
          : "Разрешите доступ к микрофону. Исходный русский звук в OBS не отправляется.";
}

function showError(message) {
  clearTimeout(runtime.noticeTimer);
  ui.error.dataset.kind = "error";
  ui.error.textContent = String(message || "Неизвестная ошибка");
  ui.error.hidden = false;
  setState("error");
}

function showNotice(message) {
  // Сбой одной фразы не должен останавливать весь эфир.
  clearTimeout(runtime.noticeTimer);
  ui.error.dataset.kind = "warning";
  ui.error.textContent = String(message);
  ui.error.hidden = false;
  runtime.noticeTimer = setTimeout(() => {
    if (ui.error.dataset.kind === "warning") clearError();
  }, 12000);
}

function clearError() {
  ui.error.hidden = true;
  ui.error.textContent = "";
  ui.error.dataset.kind = "";
}

function showSettingsMessage(message, isError = false) {
  ui.settingsMessage.textContent = message;
  ui.settingsMessage.style.color = isError ? "var(--danger)" : "var(--accent)";
  ui.settingsMessage.hidden = false;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!response.ok) throw new Error(`Локальный сервер: HTTP ${response.status}`);
  return response.json();
}

function applyConfig(config) {
  runtime.configured = Boolean(config.configured);
  ui.setupBanner.hidden = runtime.configured;
  ui.region.value = config.region || "intl";
  ui.workspace.value = config.workspace_id || "";
  ui.modelName.textContent = config.model || "qwen3.8-livetranslate-flash-realtime";
  ui.apiKey.placeholder = runtime.configured
    ? `Ключ уже задан (${config.key_source === "env" ? ".env" : "память сервера"})`
    : "sk-...";
}

async function loadConfig() {
  try {
    applyConfig(await apiRequest("/api/config"));
  } catch (error) {
    showError(`Панель не связалась с локальным сервером: ${error.message}`);
  }
}

function openSettings() {
  ui.apiKey.value = "";
  ui.settingsMessage.hidden = true;
  ui.settingsDialog.showModal();
}

async function saveSettings(check = false) {
  const saveButton = ui.settingsForm.querySelector("button[type='submit']");
  const checkButton = $("check-connection");
  saveButton.disabled = true;
  checkButton.disabled = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 80000);
  try {
    const saved = await apiRequest("/api/config", {
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({
        api_key: ui.apiKey.value.trim(),
        region: ui.region.value,
        workspace_id: ui.workspace.value.trim(),
      }),
    });
    applyConfig(saved);
    if (!saved.ok) throw new Error(saved.message);
    if (check) {
      showSettingsMessage("Проверяем соединение с моделью…");
      const checked = await apiRequest("/api/config/check", {
        method: "POST",
        signal: controller.signal,
      });
      if (!checked.ok) throw new Error(checked.message);
      showSettingsMessage(checked.message);
    } else {
      ui.settingsDialog.close();
      clearError();
      setState("idle");
    }
  } catch (error) {
    showSettingsMessage(
      error.name === "AbortError"
        ? "Проверка не уложилась в 80 секунд. Проверьте сеть, регион и Workspace ID."
        : error.message,
      true,
    );
  } finally {
    clearTimeout(timeout);
    saveButton.disabled = false;
    checkButton.disabled = false;
  }
}

function fillDeviceSelect(select, devices, kind) {
  const previous = select.value;
  select.replaceChildren();
  if (kind === "audiooutput") {
    const defaultOption = document.createElement("option");
    defaultOption.value = "default";
    defaultOption.textContent = "Устройство Windows по умолчанию";
    select.append(defaultOption);
  }
  devices.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || `${kind === "audioinput" ? "Микрофон" : "Выход"} ${index + 1}`;
    select.append(option);
  });
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}

async function loadDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) {
    throw new Error("Откройте панель в актуальном Chrome или Edge по адресу http://127.0.0.1:8765");
  }
  const previousOutput = ui.output.value;
  const devices = await navigator.mediaDevices.enumerateDevices();
  fillDeviceSelect(ui.microphone, devices.filter((item) => item.kind === "audioinput"), "audioinput");
  fillDeviceSelect(ui.output, devices.filter((item) => item.kind === "audiooutput"), "audiooutput");
  const cable = [...ui.output.options].find((option) => /cable input/i.test(option.textContent));
  // Автовыбор кабеля — только до первого ручного выбора пользователя.
  if (cable && !runtime.outputDirty && !runtime.acceptAudio) ui.output.value = cable.value;
  if (runtime.playbackContext && ui.output.value !== previousOutput) {
    // Список мог смениться после выдачи прав: реальный sink не должен разойтись с UI.
    await applyOutputDevice().catch(() => {});
  }
}

function updateMeter(fill, output, rms) {
  const db = rms > 0.000001 ? Math.max(-60, 20 * Math.log10(rms)) : -Infinity;
  const percentage = Number.isFinite(db) ? Math.max(0, Math.min(100, ((db + 60) / 60) * 100)) : 0;
  fill.style.width = `${percentage}%`;
  output.textContent = Number.isFinite(db) ? `${db.toFixed(0)} dB` : "−∞ dB";
}

function resetMeters() {
  updateMeter(ui.micMeter, ui.micDb, 0);
  updateMeter(ui.outputMeter, ui.outputDb, 0);
  ui.buffer.textContent = "0 мс";
  runtime.bufferedMs = 0;
}

function renderDrops() {
  const droppedMs = Math.round((runtime.droppedSamples / 24000) * 1000);
  ui.drops.textContent = `${runtime.underruns} · ${droppedMs} мс`;
}

async function ensureCapture() {
  await stopCapture();
  const deviceId = ui.microphone.value;
  runtime.mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      channelCount: 1,
      echoCancellation: $("echo-cancellation").checked,
      noiseSuppression: $("noise-suppression").checked,
      autoGainControl: $("auto-gain").checked,
    },
  });
  // Просим сразу 16 кГц: браузер делает качественный ресемплинг вместо
  // линейной интерполяции. Если платформа откажет, worklet доконвертирует сам.
  runtime.captureContext = new AudioContext({ sampleRate: 16000, latencyHint: "interactive" });
  await runtime.captureContext.audioWorklet.addModule("/static/pcm-capture-worklet.js");
  runtime.captureSource = runtime.captureContext.createMediaStreamSource(runtime.mediaStream);
  runtime.captureNode = new AudioWorkletNode(runtime.captureContext, "pcm-capture-processor", {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    outputChannelCount: [1],
    processorOptions: { targetSampleRate: 16000, frameSamples: 1600 },
  });
  runtime.captureMute = runtime.captureContext.createGain();
  runtime.captureMute.gain.value = 0;
  runtime.captureSource.connect(runtime.captureNode);
  runtime.captureNode.connect(runtime.captureMute);
  runtime.captureMute.connect(runtime.captureContext.destination);
  runtime.captureNode.port.onmessage = (event) => {
    const data = event.data;
    if (data.type === "audio" && runtime.acceptAudio && runtime.socket?.readyState === WebSocket.OPEN) {
      runtime.socket.send(data.buffer);
    }
    if (data.type === "level") updateMeter(ui.micMeter, ui.micDb, data.rms);
  };
  await runtime.captureContext.resume();
  await loadDevices();
}

async function stopCapture() {
  runtime.acceptAudio = false;
  if (runtime.captureNode) runtime.captureNode.port.postMessage({ type: "stop" });
  if (runtime.mediaStream) runtime.mediaStream.getTracks().forEach((track) => track.stop());
  try { runtime.captureNode?.disconnect(); } catch { /* already disconnected */ }
  try { runtime.captureSource?.disconnect(); } catch { /* already disconnected */ }
  try { runtime.captureMute?.disconnect(); } catch { /* already disconnected */ }
  if (runtime.captureContext && runtime.captureContext.state !== "closed") await runtime.captureContext.close();
  runtime.mediaStream = null;
  runtime.captureNode = null;
  runtime.captureSource = null;
  runtime.captureMute = null;
  runtime.captureContext = null;
}

async function applyOutputDevice() {
  if (!runtime.playbackContext) return;
  const sinkId = ui.output.value || "default";
  try {
    if (typeof runtime.playbackContext.setSinkId === "function") {
      await runtime.playbackContext.setSinkId(sinkId);
    } else if (runtime.playbackElement && typeof runtime.playbackElement.setSinkId === "function") {
      await runtime.playbackElement.setSinkId(sinkId);
      await runtime.playbackElement.play();
    }
    ui.outputSupport.hidden = true;
  } catch (error) {
    ui.outputSupport.textContent = `Не удалось выбрать аудиовыход: ${error.message}. Выберите другое устройство или перезапустите Chrome/Edge.`;
    ui.outputSupport.hidden = false;
    throw error;
  }
}

async function ensurePlayback() {
  await closePlayback();
  runtime.playbackContext = new AudioContext({ sampleRate: 24000, latencyHint: "interactive" });
  await runtime.playbackContext.audioWorklet.addModule("/static/pcm-playback-worklet.js");
  runtime.playbackNode = new AudioWorkletNode(runtime.playbackContext, "pcm-playback-processor", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [1],
    processorOptions: { sampleRate: 24000 },
  });
  runtime.playbackGain = runtime.playbackContext.createGain();
  runtime.playbackGain.gain.value = Number(ui.volume.value) / 100;
  runtime.playbackNode.connect(runtime.playbackGain);

  if (typeof runtime.playbackContext.setSinkId === "function") {
    runtime.playbackGain.connect(runtime.playbackContext.destination);
  } else {
    runtime.mediaDestination = runtime.playbackContext.createMediaStreamDestination();
    runtime.playbackGain.connect(runtime.mediaDestination);
    runtime.playbackElement = new Audio();
    runtime.playbackElement.autoplay = true;
    runtime.playbackElement.srcObject = runtime.mediaDestination.stream;
    if (typeof runtime.playbackElement.setSinkId !== "function") {
      ui.outputSupport.textContent = "Браузер не поддерживает выбор аудиовыхода. Используйте актуальный Chrome/Edge или временно назначьте CABLE Input устройством Windows по умолчанию.";
      ui.outputSupport.hidden = false;
    }
  }

  runtime.playbackNode.port.onmessage = (event) => {
    const data = event.data;
    if (data.type === "drained") {
      runtime.playbackDrained = true;
      runtime.bufferedMs = 0;
      return;
    }
    if (data.type !== "playback_status") return;
    runtime.bufferedMs = data.bufferedMs;
    runtime.underruns = data.underruns;
    runtime.droppedSamples = data.dropped;
    ui.buffer.textContent = `${data.bufferedMs} мс`;
    updateMeter(ui.outputMeter, ui.outputDb, data.rms);
    renderDrops();
  };
  await applyOutputDevice();
  await runtime.playbackContext.resume();
  await runtime.playbackElement?.play().catch(() => {});
}

async function closePlayback(flush = true) {
  if (runtime.playbackNode && flush) runtime.playbackNode.port.postMessage({ type: "flush" });
  if (runtime.playbackNode) runtime.playbackNode.port.postMessage({ type: "stop" });
  try { runtime.playbackNode?.disconnect(); } catch { /* already disconnected */ }
  try { runtime.playbackGain?.disconnect(); } catch { /* already disconnected */ }
  if (runtime.playbackElement) {
    runtime.playbackElement.pause();
    runtime.playbackElement.srcObject = null;
  }
  if (runtime.playbackContext && runtime.playbackContext.state !== "closed") await runtime.playbackContext.close();
  runtime.playbackContext = null;
  runtime.playbackNode = null;
  runtime.playbackGain = null;
  runtime.playbackElement = null;
  runtime.mediaDestination = null;
  runtime.playbackDrained = false;
  resetMeters();
}

function base64ToArrayBuffer(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function playTranslatedAudio(base64) {
  if (!runtime.playbackNode) return;
  const buffer = base64ToArrayBuffer(base64);
  runtime.playbackNode.port.postMessage({ type: "audio", buffer }, [buffer]);
}

function parseGlossary() {
  const phrases = {};
  const lines = ui.glossary.value.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length > 100) throw new Error("Словарь поддерживает не более 100 строк");
  lines.forEach((line, index) => {
    const separator = line.indexOf("=");
    if (separator < 1 || separator === line.length - 1) {
      throw new Error(`Словарь, строка ${index + 1}: нужен формат термин=翻译`);
    }
    const source = line.slice(0, separator).trim();
    const target = line.slice(separator + 1).trim();
    if (source.length > 200 || target.length > 200) throw new Error(`Словарь, строка ${index + 1}: слишком длинный термин`);
    phrases[source] = target;
  });
  return phrases;
}

function createTranscriptRow() {
  const element = ui.transcriptTemplate.content.firstElementChild.cloneNode(true);
  const row = {
    element,
    sourceElement: element.querySelector(".source-text"),
    targetElement: element.querySelector(".target-text"),
    sourceId: "",
    translationId: "",
    sourceText: "",
    translationText: "",
    sourceFinal: false,
    translationFinal: false,
  };
  runtime.rows.push(row);
  ui.transcript.append(element);
  ui.transcriptEmpty.hidden = true;
  trimTranscript();
  return row;
}

function trimTranscript() {
  // Многочасовой стрим не должен раздувать DOM и память.
  while (runtime.rows.length > MAX_TRANSCRIPT_ROWS) {
    const row = runtime.rows.shift();
    if (row.sourceId) runtime.sourceRows.delete(row.sourceId);
    if (row.translationId) runtime.translationRows.delete(row.translationId);
    row.element.remove();
  }
}

function sourceRow(id) {
  if (runtime.sourceRows.has(id)) return runtime.sourceRows.get(id);
  let row = [...runtime.rows].reverse().find((candidate) => !candidate.sourceId);
  if (!row) row = createTranscriptRow();
  row.sourceId = id;
  runtime.sourceRows.set(id, row);
  return row;
}

function translationRow(id, previousItemId = "") {
  if (runtime.translationRows.has(id)) return runtime.translationRows.get(id);
  // Официальная связь: translation_item.previous_item_id == asr_item.id.
  if (previousItemId && runtime.sourceRows.has(previousItemId)) {
    const paired = runtime.sourceRows.get(previousItemId);
    if (!paired.translationId) {
      paired.translationId = id;
      runtime.translationRows.set(id, paired);
      return paired;
    }
  }
  let row = runtime.rows.find((candidate) => !candidate.translationId);
  if (!row) row = createTranscriptRow();
  row.translationId = id;
  runtime.translationRows.set(id, row);
  return row;
}

function scheduleScroll() {
  if (runtime.scrollPending) return;
  runtime.scrollPending = true;
  requestAnimationFrame(() => {
    runtime.scrollPending = false;
    ui.transcript.scrollTop = ui.transcript.scrollHeight;
  });
}

function renderRow(row) {
  row.sourceElement.textContent = row.sourceText;
  row.targetElement.textContent = row.translationText;
  const sourcePending = row.sourceId && !row.sourceFinal;
  const translationPending = row.translationId && !row.translationFinal;
  row.element.classList.toggle("draft", Boolean(sourcePending || translationPending));
  scheduleScroll();
}

function clearTranscript() {
  runtime.rows = [];
  runtime.sourceRows.clear();
  runtime.translationRows.clear();
  ui.transcript.replaceChildren(ui.transcriptEmpty);
  ui.transcriptEmpty.hidden = false;
}

function handleModelEvent(event) {
  if (["listening", "translating"].includes(event.status) && !["idle", "finishing", "error", "reconnecting"].includes(runtime.state)) {
    setState(event.status);
  }
  if (event.source_delta) {
    const row = sourceRow(event.item_id || "source");
    row.sourceText += event.source_delta;
    renderRow(row);
  }
  if (event.source_final) {
    const row = sourceRow(event.item_id || "source");
    row.sourceText = event.source_final;
    row.sourceFinal = true;
    renderRow(row);
  }
  if (event.translation_delta) {
    const row = translationRow(event.item_id || event.response_id || "translation", event.previous_item_id);
    row.translationText += event.translation_delta;
    renderRow(row);
  }
  if (event.translation_final) {
    const row = translationRow(event.item_id || event.response_id || "translation", event.previous_item_id);
    row.translationText = event.translation_final;
    row.translationFinal = true;
    renderRow(row);
  }
  if (event.pairs) {
    event.pairs.forEach((pair) => translationRow(pair.item_id, pair.previous_item_id));
  }
  if (event.response_error) showNotice(event.response_error);
  if (event.usage?.total_tokens !== undefined) ui.tokens.textContent = String(event.usage.total_tokens);
  if (event.event_type === "error" && event.message) showError(event.message);
}

function updateDuration() {
  if (!runtime.startedAt) return;
  const totalSeconds = Math.floor((Date.now() - runtime.startedAt) / 1000);
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = (totalSeconds % 60).toString().padStart(2, "0");
  ui.duration.textContent = `${minutes}:${seconds}`;
}

function sessionConfig() {
  return {
    type: "config",
    voice_clone_mode: ui.cloneMode.value,
    voice_id: ui.voiceId.value.trim(),
    phrases: parseGlossary(),
  };
}

function openSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  runtime.intentionalClose = false;
  runtime.socket = new WebSocket(`${protocol}://${location.host}/ws/translate`);

  runtime.socket.onopen = () => {
    runtime.socket.send(JSON.stringify(sessionConfig()));
  };
  runtime.socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "server_ready") {
      runtime.reconnectAttempts = 0;
      runtime.acceptAudio = true;
      if (!runtime.startedAt) runtime.startedAt = Date.now();
      clearInterval(runtime.durationTimer);
      runtime.durationTimer = setInterval(updateDuration, 1000);
      setState("listening");
    } else if (message.type === "model_audio") {
      playTranslatedAudio(message.audio);
    } else if (message.type === "model_event") {
      handleModelEvent(message);
    } else if (message.type === "server_finished") {
      finishGracefully();
    } else if (message.type === "server_error") {
      const canRecover = message.recoverable && !["idle", "error", "finishing"].includes(runtime.state);
      if (canRecover) {
        showNotice(`${message.message} Переподключаюсь…`);
        scheduleReconnect();
        try { runtime.socket.close(); } catch { /* already closed */ }
      } else {
        showError(message.message);
        cleanupSession(true);
      }
    }
  };
  runtime.socket.onerror = () => {
    if (runtime.state === "connecting" && runtime.reconnectAttempts === 0) {
      showNotice("Локальный WebSocket не открылся сразу. Повторяем…");
    }
  };
  runtime.socket.onclose = () => {
    if (runtime.intentionalClose || ["idle", "error", "reconnecting"].includes(runtime.state)) return;
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  runtime.acceptAudio = false;
  if (runtime.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    showError("Связь с сервисом перевода потеряна. Автоматические попытки исчерпаны — проверьте интернет и настройки API.");
    cleanupSession(true);
    return;
  }
  const delay = RECONNECT_DELAYS_MS[Math.min(runtime.reconnectAttempts, RECONNECT_DELAYS_MS.length - 1)];
  runtime.reconnectAttempts += 1;
  setState("reconnecting");
  ui.statusText.textContent = `Переподключение ${runtime.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} через ${Math.round(delay / 1000)} с`;
  clearTimeout(runtime.reconnectTimer);
  runtime.reconnectTimer = setTimeout(() => {
    if (["idle", "error"].includes(runtime.state)) return;
    openSocket();
  }, delay);
}

async function startSession() {
  clearError();
  if (!runtime.configured) {
    openSettings();
    return;
  }
  if (ui.cloneMode.value === "never" && !ui.voiceId.value.trim()) {
    showError("Укажите voice ID заранее созданного тембра или выберите другой режим клонирования.");
    return;
  }
  setState("connecting");
  runtime.reconnectAttempts = 0;
  try {
    parseGlossary();
    await loadDevices();
    await ensurePlayback();
    await ensureCapture();
    clearTranscript();
    ui.tokens.textContent = "—";
    ui.drops.textContent = "0 · 0 мс";
    ui.duration.textContent = "00:00";
    runtime.startedAt = 0;
    openSocket();
  } catch (error) {
    showError(error.message);
    await cleanupSession(true);
  }
}

async function stopSession() {
  clearTimeout(runtime.reconnectTimer);
  runtime.reconnectTimer = null;
  if (["connecting", "reconnecting"].includes(runtime.state)) {
    await cleanupSession(true);
    return;
  }
  if (runtime.state === "finishing") {
    await cleanupSession(true);
    return;
  }
  if (!runtime.socket || runtime.socket.readyState !== WebSocket.OPEN) {
    await cleanupSession(true);
    return;
  }
  setState("finishing");
  await stopCapture();
  clearInterval(runtime.durationTimer);
  runtime.durationTimer = null;
  runtime.socket.send(JSON.stringify({ type: "finish" }));
  runtime.finishTimer = setTimeout(() => {
    showError("Последняя фраза не завершилась за 50 секунд. Сессия закрыта.");
    cleanupSession(true);
  }, 50_000);
}

async function finishGracefully() {
  clearTimeout(runtime.finishTimer);
  runtime.finishTimer = null;
  runtime.intentionalClose = true;
  try { runtime.socket?.close(); } catch { /* already closed */ }
  // Доигрываем остаток: drain ставится в очередь после последних аудиосообщений,
  // а worklet снимает prebuffer-гейт и присылает drained только после опустошения.
  if (runtime.playbackNode && runtime.playbackContext?.state === "running") {
    runtime.playbackDrained = false;
    runtime.playbackNode.port.postMessage({ type: "drain" });
    const deadline = Date.now() + 35_000;
    while (!runtime.playbackDrained && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  await cleanupSession(false);
}

async function cleanupSession(flushPlayback = true) {
  clearInterval(runtime.durationTimer);
  clearTimeout(runtime.finishTimer);
  clearTimeout(runtime.reconnectTimer);
  runtime.durationTimer = null;
  runtime.finishTimer = null;
  runtime.reconnectTimer = null;
  runtime.startedAt = 0;
  runtime.intentionalClose = true;
  await stopCapture();
  if (runtime.socket && runtime.socket.readyState <= WebSocket.OPEN) {
    try { runtime.socket.close(); } catch { /* already closed */ }
  }
  runtime.socket = null;
  await closePlayback(flushPlayback);
  if (runtime.state !== "error") setState("idle");
}

function bindEvents() {
  ui.startButton.addEventListener("click", () => {
    if (["idle", "error"].includes(runtime.state)) startSession();
    else stopSession();
  });
  $("settings-button").addEventListener("click", openSettings);
  $("setup-button").addEventListener("click", openSettings);
  $("close-settings").addEventListener("click", () => ui.settingsDialog.close());
  ui.settingsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSettings(false);
  });
  $("check-connection").addEventListener("click", () => saveSettings(true));
  $("refresh-devices").addEventListener("click", () => loadDevices().catch((error) => showError(error.message)));
  ui.output.addEventListener("change", () => {
    runtime.outputDirty = true;
    applyOutputDevice().catch(() => {});
  });
  ui.cloneMode.addEventListener("change", () => {
    ui.voiceIdField.hidden = ui.cloneMode.value !== "never";
  });
  ui.volume.addEventListener("input", () => {
    ui.volumeValue.textContent = `${ui.volume.value}%`;
    if (runtime.playbackGain) runtime.playbackGain.gain.value = Number(ui.volume.value) / 100;
  });
  $("clear-transcript").addEventListener("click", clearTranscript);
  window.addEventListener("beforeunload", (event) => {
    if (!["idle", "error"].includes(runtime.state)) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

async function init() {
  bindEvents();
  setState("idle");
  resetMeters();
  ui.voiceIdField.hidden = ui.cloneMode.value !== "never";
  await Promise.all([loadConfig(), loadDevices()]);
}

init().catch((error) => showError(error.message));
