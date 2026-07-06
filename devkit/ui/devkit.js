/* ============================================================
 * 隙间 · 开发者工具 — UI 客户端
 *
 * 单纯调用 window.pywebview.api.*, 不发起任何 HTTP 请求。
 * ============================================================ */

(() => {
  "use strict";

  // --------------------------------------------------------------
  // 状态
  // --------------------------------------------------------------

  /** @typedef {{path: string, size: number, name: string}} FileEntry */

  const state = {
    files: [],
    config: null,
    targetKinds: null,
    activeDeveloper: null,
  };

  // --------------------------------------------------------------
  // Utilities
  // --------------------------------------------------------------

  const fmtBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1000) return `${bytes} B`;
    if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(2)} KB`;
    if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
    return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  };

  const fmtTime = (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toLocaleString();
    } catch {
      return iso;
    }
  };

  const shortSha = (hex) =>
    !hex ? "—" : `${hex.slice(0, 8)}…${hex.slice(-6)}`;

  let toastTimer = 0;
  const toast = (msg, kind = "ok") => {
    document.querySelectorAll(".toast").forEach((el) => el.remove());
    const el = document.createElement("div");
    el.className = `toast toast--${kind}`;
    el.textContent = msg;
    document.body.appendChild(el);
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => el.remove(), 3200);
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const callApi = async (method, ...args) => {
    if (!window.pywebview || !window.pywebview.api) {
      throw new Error("pywebview js_api not ready");
    }
    const fn = window.pywebview.api[method];
    if (typeof fn !== "function") {
      throw new Error(`DevKitApi.${method} is not a function`);
    }
    return fn(...args);
  };

  // --------------------------------------------------------------
  // Tab 切换
  // --------------------------------------------------------------

  const switchTab = (tabName) => {
    $$(".tab-nav__btn").forEach((btn) => {
      btn.classList.toggle("tab-nav__btn--active", btn.dataset.tab === tabName);
    });
    $$(".tab-panel").forEach((panel) => {
      panel.classList.toggle("tab-panel--active", panel.id === `tab-${tabName}`);
    });
  };

  // --------------------------------------------------------------
  // 提交 Tab 渲染
  // --------------------------------------------------------------

  const renderConfig = (cfg) => {
    state.config = cfg;
    $("#cfg-api-version").textContent = cfg.api_version ?? "—";
    $("#cfg-archive-format").textContent = cfg.preferred_archive_format ?? "—";
    $("#cfg-max-bytes").textContent = `${fmtBytes(cfg.max_attachment_bytes)} (${cfg.max_attachment_mb} MB)`;
    $("#cfg-smtp-host").textContent = cfg.smtp_host ?? "—";
    $("#cfg-smtp-port").textContent = String(cfg.smtp_port ?? "—");
    $("#cfg-smtp-tls").textContent = cfg.smtp_use_tls ? "✅" : "❌";
    $("#cfg-smtp-user").textContent = cfg.smtp_user ?? "—";
    $("#recipient-chip-value").textContent = cfg.recipient ?? "—";
    $("#files-card-max").textContent = cfg.max_attachment_mb ? `${cfg.max_attachment_mb} MB` : "—";
    const cdMin = Math.floor((cfg.cooldown_seconds || 0) / 60);
    $("#files-card-cooldown").textContent = cdMin ? `${cdMin} 分钟` : `${cfg.cooldown_seconds ?? 0} 秒`;
  };

  const renderTargetKinds = (kinds) => {
    state.targetKinds = kinds;
    const select = $("#target-kind");
    select.innerHTML = "";
    for (const k of kinds) {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      select.appendChild(opt);
    }
  };

  const renderDeveloperChip = () => {
    const chip = $("#developer-chip-value");
    const logoutBtn = $("#logout-btn");
    if (state.activeDeveloper) {
      chip.textContent = state.activeDeveloper;
      logoutBtn.hidden = false;
    } else {
      chip.textContent = "未登录";
      logoutBtn.hidden = true;
    }
  };

  const renderFiles = () => {
    const list = $("#files-list");
    list.innerHTML = "";
    for (const f of state.files) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.className = "files-list li__name";
      name.textContent = f.path;
      const size = document.createElement("span");
      size.className = "files-list li__size";
      size.textContent = fmtBytes(f.size);
      li.appendChild(name);
      li.appendChild(size);
      list.appendChild(li);
    }
    const total = state.files.reduce((acc, f) => acc + (f.size || 0), 0);
    const limit = state.config?.max_attachment_bytes ?? 0;
    const summary = $("#files-summary");
    if (state.files.length === 0) {
      summary.textContent = "尚未选择文件";
    } else {
      summary.textContent = `共 ${state.files.length} 个 · ${fmtBytes(total)} / ${fmtBytes(limit)}`;
    }
    refreshSubmitButton();
  };

  const refreshSubmitButton = () => {
    const btn = $("#submit-btn");
    const ready = state.activeDeveloper && $("#target-id").value.trim() && state.files.length > 0;
    btn.disabled = !ready;
  };

  const setStatus = (selector, text, kind = "idle") => {
    const el = document.querySelector(selector);
    if (!el) return;
    el.textContent = text;
    el.className = `status status--${kind}`;
  };

  const renderHistory = (records) => {
    const list = $("#history-list");
    list.innerHTML = "";
    for (const r of records) {
      const li = document.createElement("li");
      li.className = r.smtp_status === "sent" ? "history-list li--ok" : "history-list li--err";
      const row = document.createElement("div");
      row.className = "history-list li__row";
      const title = document.createElement("span");
      title.className = "history-list li__title";
      title.textContent = `${r.target_kind}:${r.target_id}`;
      const meta = document.createElement("span");
      meta.className = "history-list li__meta";
      meta.textContent = fmtTime(r.submitted_at);
      row.appendChild(title);
      row.appendChild(meta);
      const meta2 = document.createElement("div");
      meta2.className = "history-list li__meta";
      meta2.textContent = `${r.developer_id} · ${fmtBytes(r.archive_size)} · ${r.archive_format} · ${r.smtp_status}${r.smtp_code ? ` (${r.smtp_code})` : ""}`;
      const sha = document.createElement("div");
      sha.className = "history-list li__sha";
      sha.textContent = `sha256: ${shortSha(r.content_sha256)}`;
      li.appendChild(row);
      li.appendChild(meta2);
      li.appendChild(sha);
      list.appendChild(li);
    }
  };

  const renderCooldown = (seconds) => {
    const el = $("#cooldown-indicator");
    if (seconds <= 0) {
      el.textContent = "冷却空闲，可随时提交";
    } else {
      const min = Math.floor(seconds / 60);
      const sec = seconds % 60;
      el.textContent = min > 0 ? `还需等待 ${min} 分 ${sec} 秒` : `还需等待 ${sec} 秒`;
    }
  };

  // --------------------------------------------------------------
  // 通用列表渲染
  // --------------------------------------------------------------

  const renderItemList = (listId, items, templateFn) => {
    const list = $(`#${listId}`);
    list.innerHTML = "";
    if (!items || items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "item-list__empty";
      empty.textContent = "暂无数据";
      list.appendChild(empty);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      li.className = "item-list__item";
      li.dataset.id = item.id || "";
      li.innerHTML = templateFn(item);
      li.addEventListener("click", () => {
        $$(`#${listId} .item-list__item`).forEach((el) => el.classList.remove("item-list__item--active"));
        li.classList.add("item-list__item--active");
      });
      list.appendChild(li);
    }
  };

  // --------------------------------------------------------------
  // 角色编辑器
  // --------------------------------------------------------------

  let _selectedCharId = null;

  const renderCharList = async () => {
    const resp = await callApi("list_characters");
    if (!resp.ok) return;
    renderItemList("char-list", resp.data || [], (c) =>
      `<strong>${c.display_name || c.name}</strong><br/><small>${c.id} · ${(c.tags || []).join(", ")}</small>`
    );
    refreshCharButtons();
  };

  const refreshCharButtons = () => {
    const hasSel = !!_selectedCharId;
    $("#char-export-btn").disabled = !hasSel;
    $("#char-delete-btn").disabled = !hasSel;
  };

  const loadCharEditor = (char) => {
    _selectedCharId = char?.id || null;
    $("#char-editing-id").value = char?.id || "";
    $("#char-name").value = char?.name || "";
    $("#char-display-name").value = char?.display_name || "";
    $("#char-voice").value = char?.voice_profile || "";
    $("#char-emotion").value = char?.default_emotion || "neutral";
    $("#char-tags").value = (char?.tags || []).join(", ");
    $("#char-persona").value = char?.persona_doc || "";
    $("#char-editor-hint").textContent = char
      ? `编辑：${char.display_name || char.name}`
      : "新建角色";
    refreshCharButtons();
  };

  const resetCharEditor = () => {
    _selectedCharId = null;
    $("#char-editing-id").value = "";
    $("#char-name").value = "";
    $("#char-display-name").value = "";
    $("#char-voice").value = "";
    $("#char-emotion").value = "neutral";
    $("#char-tags").value = "";
    $("#char-persona").value = "";
    $("#char-editor-hint").textContent = "选择一个角色或新建后进行编辑";
    refreshCharButtons();
  };

  const onCharSelect = async (id) => {
    const resp = await callApi("get_character", id);
    if (resp.ok && resp.data) loadCharEditor(resp.data);
  };

  const onCharSave = async () => {
    const tags = $("#char-tags").value
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const data = {
      id: $("#char-editing-id").value || undefined,
      name: $("#char-name").value.trim(),
      display_name: $("#char-display-name").value.trim(),
      voice_profile: $("#char-voice").value.trim(),
      default_emotion: $("#char-emotion").value.trim(),
      tags,
      persona_doc: $("#char-persona").value,
    };
    if (!data.name) { toast("请填写角色名称", "err"); return; }
    const resp = await callApi("save_character", data);
    if (!resp.ok) { setStatus("#char-status", `保存失败：${resp.message}`, "err"); return; }
    toast("角色已保存", "ok");
    setStatus("#char-status", `已保存：${resp.data.id}`, "ok");
    await renderCharList();
    _selectedCharId = resp.data.id;
    refreshCharButtons();
  };

  const onCharDelete = async () => {
    if (!_selectedCharId) return;
    const resp = await callApi("delete_character", _selectedCharId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("已删除", "ok");
    resetCharEditor();
    await renderCharList();
  };

  const onCharExport = async () => {
    if (!_selectedCharId) return;
    const resp = await callApi("export_character", _selectedCharId);
    if (!resp.ok) { toast("导出失败", "err"); return; }
    toast("导出成功，可在创作提交标签页继续操作", "ok");
    setStatus("#char-status", "已导出，前往创作提交标签页提交", "ok");
  };

  // --------------------------------------------------------------
  // 记忆条目编辑器
  // --------------------------------------------------------------

  let _selectedMemId = null;

  const renderMemList = async () => {
    const charId = $("#mem-char-id").value.trim();
    if (!charId) {
      renderItemList("mem-list", [], () => "");
      return;
    }
    const resp = await callApi("list_memory_entries", charId);
    if (!resp.ok) { renderItemList("mem-list", [], () => ""); return; }
    renderItemList("mem-list", resp.data || [], (e) =>
      `<strong>[${e.type === "long" ? "长期" : "短期"}]</strong> ${e.content.slice(0, 60)}${e.content.length > 60 ? "…" : ""}<br/><small>重要性: ${e.importance} · ${(e.tags || []).join(", ")}</small>`
    );
    refreshMemButtons();
  };

  const refreshMemButtons = () => {
    const hasSel = !!_selectedMemId;
    $("#mem-export-btn").disabled = !hasSel;
    $("#mem-delete-btn").disabled = !hasSel;
  };

  const loadMemEditor = (entry) => {
    _selectedMemId = entry?.id || null;
    $("#mem-editing-id").value = entry?.id || "";
    $("#mem-char").value = entry?.character_id || $("#mem-char-id").value || "";
    $("#mem-type").value = entry?.type || "short";
    $("#mem-importance").value = entry?.importance ?? 0.5;
    $("#mem-tags").value = (entry?.tags || []).join(", ");
    $("#mem-content").value = entry?.content || "";
    $("#mem-editor-hint").textContent = entry
      ? `编辑条目：${entry.content.slice(0, 30)}…`
      : "新建记忆条目";
    refreshMemButtons();
  };

  const resetMemEditor = () => {
    _selectedMemId = null;
    $("#mem-editing-id").value = "";
    $("#mem-char").value = $("#mem-char-id").value || "";
    $("#mem-type").value = "short";
    $("#mem-importance").value = 0.5;
    $("#mem-tags").value = "";
    $("#mem-content").value = "";
    $("#mem-editor-hint").textContent = "新建或选择一条记忆进行编辑";
    refreshMemButtons();
  };

  const onMemSave = async () => {
    const tags = $("#mem-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
    const data = {
      id: $("#mem-editing-id").value || undefined,
      character_id: $("#mem-char").value.trim(),
      type: $("#mem-type").value,
      importance: parseFloat($("#mem-importance").value) || 0.5,
      tags,
      content: $("#mem-content").value,
    };
    if (!data.character_id) { toast("请填写角色 ID", "err"); return; }
    if (!data.content) { toast("请填写记忆内容", "err"); return; }
    const resp = await callApi("save_memory_entry", data);
    if (!resp.ok) { setStatus("#mem-status", `保存失败：${resp.message}`, "err"); return; }
    toast("记忆条目已保存", "ok");
    setStatus("#mem-status", `已保存：${resp.data.id}`, "ok");
    await renderMemList();
  };

  const onMemDelete = async () => {
    if (!_selectedMemId) return;
    const resp = await callApi("delete_memory_entry", _selectedMemId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("已删除", "ok");
    resetMemEditor();
    await renderMemList();
  };

  const onMemExport = async () => {
    const charId = $("#mem-char").value.trim() || $("#mem-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    const resp = await callApi("export_memory_entries", charId);
    if (!resp.ok) { toast("导出失败", "err"); return; }
    toast("导出成功", "ok");
    setStatus("#mem-status", "已导出，前往创作提交标签页提交", "ok");
  };

  // --------------------------------------------------------------
  // 世界观编辑器
  // --------------------------------------------------------------

  let _selectedWorldId = null;

  const renderWorldList = async () => {
    const resp = await callApi("list_worlds");
    if (!resp.ok) return;
    renderItemList("world-list", resp.data || [], (w) =>
      `<strong>${w.name}</strong><br/><small>${w.id}</small>`
    );
    refreshWorldButtons();
  };

  const refreshWorldButtons = () => {
    const hasSel = !!_selectedWorldId;
    $("#world-export-btn").disabled = !hasSel;
    $("#world-delete-btn").disabled = !hasSel;
  };

  const loadWorldEditor = (world) => {
    _selectedWorldId = world?.id || null;
    $("#world-editing-id").value = world?.id || "";
    $("#world-name").value = world?.name || "";
    $("#world-config").value = world?.config ? JSON.stringify(world.config, null, 2) : "";
    $("#world-doc").value = world?.world_doc || "";
    $("#world-editor-hint").textContent = world ? `编辑：${world.name}` : "新建世界观";
    refreshWorldButtons();
  };

  const resetWorldEditor = () => {
    _selectedWorldId = null;
    $("#world-editing-id").value = "";
    $("#world-name").value = "";
    $("#world-config").value = "";
    $("#world-doc").value = "";
    $("#world-editor-hint").textContent = "选择一个世界观或新建后进行编辑";
    refreshWorldButtons();
  };

  const onWorldSave = async () => {
    let config = {};
    try {
      const raw = $("#world-config").value.trim();
      if (raw) config = JSON.parse(raw);
    } catch { toast("配置文件 JSON 格式错误", "err"); return; }
    const data = {
      id: $("#world-editing-id").value || undefined,
      name: $("#world-name").value.trim(),
      config,
      world_doc: $("#world-doc").value,
    };
    if (!data.name) { toast("请填写世界观名称", "err"); return; }
    const resp = await callApi("save_world", data);
    if (!resp.ok) { setStatus("#world-status", `保存失败：${resp.message}`, "err"); return; }
    toast("世界观已保存", "ok");
    setStatus("#world-status", `已保存：${resp.data.id}`, "ok");
    await renderWorldList();
  };

  const onWorldDelete = async () => {
    if (!_selectedWorldId) return;
    const resp = await callApi("delete_world", _selectedWorldId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("已删除", "ok");
    resetWorldEditor();
    await renderWorldList();
  };

  const onWorldExport = async () => {
    if (!_selectedWorldId) return;
    const resp = await callApi("export_world", _selectedWorldId);
    if (!resp.ok) { toast("导出失败", "err"); return; }
    toast("导出成功", "ok");
    setStatus("#world-status", "已导出，前往创作提交标签页提交", "ok");
  };

  // --------------------------------------------------------------
  // 3D 模型预览
  // --------------------------------------------------------------

  let _selectedModelId = null;

  const renderModelList = async () => {
    const resp = await callApi("list_models");
    if (!resp.ok) return;
    renderItemList("model-list", resp.data || [], (m) =>
      `<strong>${m.name}</strong><br/><small>${m.format} · ${fmtBytes(m.size_bytes)}</small>`
    );
  };

  const showModelInfo = (model) => {
    if (!model) {
      $("#model-info").hidden = true;
      $("#model-viewer-container").innerHTML = '<p class="status status--idle">请从左侧列表选择一个模型</p>';
      return;
    }
    _selectedModelId = model.id;
    $("#model-info-name").textContent = model.name;
    $("#model-info-format").textContent = model.format;
    $("#model-info-size").textContent = fmtBytes(model.size_bytes);
    $("#model-editor-hint").textContent = model.name;
    $("#model-info").hidden = false;
    const container = $("#model-viewer-container");
    container.innerHTML = `<p class="status status--ok">已加载: ${model.name}<br/><small>路径: ${model.path}</small></p>`;
  };

  const onModelAdd = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast("pywebview 文件对话框未就绪", "err");
      return;
    }
    let picked;
    try {
      picked = await window.pywebview.create_file_dialog(
        window.pywebview.types.OPEN,
        { file_types: ["vrm", "glb", "gltf"] }
      );
    } catch { toast("文件对话框失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    const resp = await callApi("register_model", picked);
    if (!resp.ok) { toast(`添加失败：${resp.message}`, "err"); return; }
    toast("模型已添加", "ok");
    await renderModelList();
  };

  const onModelUnregister = async () => {
    if (!_selectedModelId) return;
    const resp = await callApi("unregister_model", _selectedModelId);
    if (!resp.ok) { toast("移除失败", "err"); return; }
    toast("已移除", "ok");
    _selectedModelId = null;
    showModelInfo(null);
    await renderModelList();
  };

  // --------------------------------------------------------------
  // 声音克隆
  // --------------------------------------------------------------

  let _selectedVoiceId = null;

  const renderVoiceList = async () => {
    const charId = $("#voice-char-id").value.trim();
    if (!charId) {
      renderItemList("voice-list", [], () => "");
      return;
    }
    const resp = await callApi("list_voices", charId);
    if (!resp.ok) { renderItemList("voice-list", [], () => ""); return; }
    renderItemList("voice-list", resp.data || [], (v) =>
      `<strong>${v.name}</strong><br/><small>${v.engine} · ${v.sample_path ? "有样本文件" : "无样本"}</small>`
    );
    refreshVoiceButtons();
  };

  const refreshVoiceButtons = () => {
    $("#voice-delete-btn").disabled = !_selectedVoiceId;
  };

  const loadVoiceEditor = (voice) => {
    _selectedVoiceId = voice?.id || null;
    $("#voice-editing-id").value = voice?.id || "";
    $("#voice-char").value = voice?.character_id || $("#voice-char-id").value || "";
    $("#voice-name").value = voice?.name || "";
    $("#voice-engine").value = voice?.engine || "melo-tts";
    $("#voice-sample-path").value = voice?.sample_path || "";
    $("#voice-editor-hint").textContent = voice ? `编辑：${voice.name}` : "添加声音样本";
    refreshVoiceButtons();
  };

  const resetVoiceEditor = () => {
    _selectedVoiceId = null;
    $("#voice-editing-id").value = "";
    $("#voice-char").value = $("#voice-char-id").value || "";
    $("#voice-name").value = "";
    $("#voice-engine").value = "melo-tts";
    $("#voice-sample-path").value = "";
    $("#voice-editor-hint").textContent = "添加或编辑声音样本信息";
    refreshVoiceButtons();
  };

  const onVoiceSave = async () => {
    const charId = $("#voice-char").value.trim();
    const name = $("#voice-name").value.trim();
    const engine = $("#voice-engine").value.trim();
    const samplePath = $("#voice-sample-path").value.trim() || null;
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    if (!name) { toast("请填写声音名称", "err"); return; }
    const resp = await callApi("save_voice", charId, name, samplePath, engine);
    if (!resp.ok) { setStatus("#voice-status", `保存失败：${resp.message}`, "err"); return; }
    toast("声音样本已保存", "ok");
    setStatus("#voice-status", `已保存：${resp.data.id}`, "ok");
    await renderVoiceList();
  };

  const onVoiceDelete = async () => {
    if (!_selectedVoiceId) return;
    const resp = await callApi("delete_voice", _selectedVoiceId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("已删除", "ok");
    resetVoiceEditor();
    await renderVoiceList();
  };

  const onVoicePickFile = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast("pywebview 文件对话框未就绪", "err");
      return;
    }
    let picked;
    try {
      picked = await window.pywebview.create_file_dialog(
        window.pywebview.types.OPEN,
        { file_types: ["wav", "mp3", "m4a", "ogg", "flac"] }
      );
    } catch { toast("文件对话框失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    $("#voice-sample-path").value = picked;
  };

  // --------------------------------------------------------------
  // 操作
  // --------------------------------------------------------------

  const loadBootstrap = async () => {
    try {
      const ping = await callApi("ping");
      if (!ping.ok) throw new Error("ping failed");
      const cfg = await callApi("whoami");
      if (!cfg.ok) throw new Error("whoami failed");
      renderConfig(cfg.data);
      const kinds = await callApi("target_kinds");
      if (!kinds.ok) throw new Error("target_kinds failed");
      renderTargetKinds(kinds.data);
      const me = await callApi("current_developer");
      if (me.ok && me.data && me.data.developer_id) {
        state.activeDeveloper = me.data.developer_id;
        $("#developer-id").value = me.data.developer_id;
      }
      renderDeveloperChip();
      await refreshHistory();
      await refreshCooldown();
      setStatus("#login-status", "就绪", "ok");
      $("#status-bar").textContent = "已连接";
    } catch (err) {
      console.error("bootstrap failed", err);
      setStatus("#login-status", `初始化失败：${err.message}`, "err");
      $("#status-bar").textContent = "未连接";
    }
  };

  const onLogin = async () => {
    const id = $("#developer-id").value.trim();
    if (!id) { setStatus("#login-status", "请输入开发者 ID", "warn"); return; }
    const resp = await callApi("login", id);
    if (!resp.ok) { setStatus("#login-status", `登录失败：${resp.message}`, "err"); return; }
    state.activeDeveloper = resp.data.developer_id;
    renderDeveloperChip();
    setStatus("#login-status", `已登录为 ${resp.data.developer_id}`, "ok");
    await refreshHistory();
    await refreshCooldown();
    toast("已切换开发者", "ok");
  };

  const onLogout = async () => {
    const resp = await callApi("logout");
    state.activeDeveloper = null;
    renderDeveloperChip();
    setStatus("#login-status", resp.data.previous ? `已退出 ${resp.data.previous}` : "已退出", "ok");
    await refreshCooldown();
  };

  const onPickFiles = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast("pywebview 文件对话框未就绪", "err");
      return;
    }
    let picked;
    try {
      picked = await window.pywebview.create_file_dialog(
        window.pywebview.types.OPEN_MULTIPLE,
        { allow_multiple: true, file_types: [] }
      );
    } catch { toast("文件对话框失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    const resp = await callApi("preview_size", picked.map((p) => ({ path: p, size: 0 })));
    if (!resp.ok) { setStatus("#files-status", `预检失败：${resp.message}`, "err"); return; }
    state.files = picked.map((p) => ({ path: p, name: p.split("/").pop(), size: 0 }));
    renderFiles();
    setStatus("#files-status", `已选 ${state.files.length} 个文件 · 体积将在服务端复核`, "ok");
  };

  const onClearFiles = () => {
    state.files = [];
    renderFiles();
    setStatus("#files-status", "已清空", "idle");
  };

  const onSubmit = async () => {
    if (!state.activeDeveloper) { toast("请先登录", "err"); return; }
    const targetKind = $("#target-kind").value;
    const targetId = $("#target-id").value.trim();
    const aiRatio = parseFloat($("#ai-ratio").value || "0");
    const notes = $("#notes").value.trim();
    if (!targetId) { toast("请填写目标 ID", "err"); return; }
    const payload = {
      notes,
      ai_ratio: Number.isFinite(aiRatio) ? aiRatio : 0,
      files: state.files.map((f) => f.path),
    };
    const fileEntries = state.files.map((f) => ({ path: f.path, arcname: f.name, size: f.size }));
    setStatus("#submit-status", "正在打包并投递（约 10–60 秒）…", "warn");
    $("#submit-btn").disabled = true;
    const resp = await callApi("submit", state.activeDeveloper, targetKind, targetId, payload, fileEntries);
    if (!resp.ok) {
      setStatus("#submit-status", `提交失败：${resp.message} (${resp.code || resp.status || ""})`, "err");
      toast(`提交失败：${resp.message}`, "err");
      $("#submit-btn").disabled = false;
      refreshSubmitButton();
      return;
    }
    const r = resp.data;
    setStatus("#submit-status", `提交成功：${r.id} · ${fmtBytes(r.archive_size)} · sha256 ${shortSha(r.content_sha256)} · smtp ${r.smtp_status} ${r.smtp_code}`, "ok");
    toast(`已发送：${r.id}`, "ok");
    $("#status-bar").textContent = `上次提交 ${r.submitted_at}`;
    await refreshHistory();
    await refreshCooldown();
    $("#submit-btn").disabled = false;
    refreshSubmitButton();
  };

  const refreshHistory = async () => {
    const resp = await callApi("list_submissions", 20);
    if (!resp.ok) { renderHistory([]); return; }
    renderHistory(resp.data || []);
  };

  const refreshCooldown = async () => {
    if (!state.activeDeveloper) { renderCooldown(0); return; }
    const resp = await callApi("cooldown_for", state.activeDeveloper);
    if (!resp.ok) { renderCooldown(0); return; }
    renderCooldown(Number(resp.data) || 0);
  };

  // --------------------------------------------------------------
  // 绑定
  // --------------------------------------------------------------

  const bind = () => {
    // Tab navigation
    $$(".tab-nav__btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Submit tab
    $("#login-btn").addEventListener("click", onLogin);
    $("#logout-btn").addEventListener("click", onLogout);
    $("#developer-id").addEventListener("keydown", (e) => { if (e.key === "Enter") onLogin(); });
    $("#pick-files-btn").addEventListener("click", onPickFiles);
    $("#clear-files-btn").addEventListener("click", onClearFiles);
    $("#submit-btn").addEventListener("click", onSubmit);
    $("#refresh-history-btn").addEventListener("click", refreshHistory);
    $("#target-id").addEventListener("input", refreshSubmitButton);

    // Character tab
    $("#char-refresh-btn").addEventListener("click", renderCharList);
    $("#char-new-btn").addEventListener("click", () => { resetCharEditor(); loadCharEditor(null); });
    $("#char-save-btn").addEventListener("click", onCharSave);
    $("#char-delete-btn").addEventListener("click", onCharDelete);
    $("#char-export-btn").addEventListener("click", onCharExport);
    $("#char-list").addEventListener("click", (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) onCharSelect(li.dataset.id);
    });

    // Memory tab
    $("#mem-refresh-btn").addEventListener("click", renderMemList);
    $("#mem-new-btn").addEventListener("click", () => { resetMemEditor(); loadMemEditor(null); });
    $("#mem-save-btn").addEventListener("click", onMemSave);
    $("#mem-delete-btn").addEventListener("click", onMemDelete);
    $("#mem-export-btn").addEventListener("click", onMemExport);
    $("#mem-char-id").addEventListener("change", renderMemList);
    $("#mem-list").addEventListener("click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_memory_entry", li.dataset.id);
        if (resp.ok) loadMemEditor(resp.data);
      }
    });

    // World tab
    $("#world-refresh-btn").addEventListener("click", renderWorldList);
    $("#world-new-btn").addEventListener("click", () => { resetWorldEditor(); loadWorldEditor(null); });
    $("#world-save-btn").addEventListener("click", onWorldSave);
    $("#world-delete-btn").addEventListener("click", onWorldDelete);
    $("#world-export-btn").addEventListener("click", onWorldExport);
    $("#world-list").addEventListener("click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_world", li.dataset.id);
        if (resp.ok) loadWorldEditor(resp.data);
      }
    });

    // Model tab
    $("#model-refresh-btn").addEventListener("click", renderModelList);
    $("#model-add-btn").addEventListener("click", onModelAdd);
    $("#model-unregister-btn").addEventListener("click", onModelUnregister);
    $("#model-list").addEventListener("click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_model_info", li.dataset.id);
        if (resp.ok) showModelInfo(resp.data);
      }
    });

    // Voice tab
    $("#voice-refresh-btn").addEventListener("click", renderVoiceList);
    $("#voice-new-btn").addEventListener("click", () => { resetVoiceEditor(); loadVoiceEditor(null); });
    $("#voice-save-btn").addEventListener("click", onVoiceSave);
    $("#voice-delete-btn").addEventListener("click", onVoiceDelete);
    $("#voice-pick-btn").addEventListener("click", onVoicePickFile);
    $("#voice-char-id").addEventListener("change", renderVoiceList);
    $("#voice-list").addEventListener("click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_voice", li.dataset.id);
        if (resp.ok) loadVoiceEditor(resp.data);
      }
    });
  };

  // --------------------------------------------------------------
  // 启动
  // --------------------------------------------------------------

  const start = () => {
    bind();
    if (window.pywebview && window.pywebview.api) {
      loadBootstrap();
    } else {
      window.addEventListener("pywebviewready", () => loadBootstrap(), { once: true });
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
