(() => {
  "use strict";

  const state = {
    packages: [],
    selectedPackages: new Set(),
    config: null,
    targetKinds: null,
    activeDeveloper: null,
  };

  const fmtBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return "";
    if (bytes < 1000) return `${bytes} B`;
    if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(2)} KB`;
    if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
    return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  };

  const fmtTime = (iso) => {
    if (!iso) return "";
    try { const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(); }
    catch { return iso; }
  };

  const shortSha = (hex) => !hex ? "" : `${hex.slice(0, 8)}${hex.slice(-6)}`;

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
    if (!window.pywebview || !window.pywebview.api) throw new Error("pywebview js_api not ready");
    const fn = window.pywebview.api[method];
    if (typeof fn !== "function") throw new Error(`DevKitApi.${method} is not a function`);
    return fn(...args);
  };

  const switchTab = (tabName) => {
    $$(".tab-nav__btn").forEach((btn) => {
      btn.classList.toggle("tab-nav__btn--active", btn.dataset.tab === tabName);
    });
    $$(".tab-panel").forEach((panel) => {
      panel.classList.toggle("tab-panel--active", panel.id === `tab-${tabName}`);
    });
  };

  // --------------------------------------------------------------
  // Config / header
  // --------------------------------------------------------------

  const renderConfig = (cfg) => {
    state.config = cfg;
    $("#cfg-api-version").textContent = cfg.api_version ?? "";
    $("#cfg-archive-format").textContent = cfg.preferred_archive_format ?? "";
    $("#cfg-max-bytes").textContent = `${fmtBytes(cfg.max_attachment_bytes)} (${cfg.max_attachment_mb} MB)`;
    $("#cfg-smtp-host").textContent = cfg.smtp_host ?? "";
    $("#cfg-smtp-port").textContent = String(cfg.smtp_port ?? "");
    $("#cfg-smtp-tls").textContent = cfg.smtp_use_tls ? "是" : "否";
    $("#cfg-smtp-user").textContent = cfg.smtp_user ?? "";
    $("#recipient-chip-value").textContent = cfg.recipient ?? "";
  };

  const renderTargetKinds = (kinds) => {
    state.targetKinds = kinds;
    const select = $("#target-kind");
    if (!select) return;
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
      chip.textContent = "";
      logoutBtn.hidden = true;
    }
  };

  const setStatus = (selector, text, kind = "idle") => {
    const el = $(selector);
    if (!el) return;
    el.textContent = text;
    el.className = `status status--${kind}`;
  };

  // --------------------------------------------------------------
  // Submit tab — packages
  // --------------------------------------------------------------

  const loadPackages = async () => {
    const resp = await callApi("list_submit_packages");
    if (!resp.ok) { setStatus("#packages-status", "加载失败", "err"); return; }
    state.packages = resp.data || [];
    renderPackages();
    setStatus("#packages-status", `共 ${state.packages.length} 个可提交内容`, "ok");
    refreshSubmitBtn();
  };

  const renderPackages = () => {
    const container = $("#packages-list");
    container.innerHTML = "";
    for (const pkg of state.packages) {
      const checked = state.selectedPackages.has(pkg.package_id);
      const div = document.createElement("div");
      div.className = `package-item${checked ? " package-item--checked" : ""}`;
      div.dataset.packageId = pkg.package_id;
      div.innerHTML = `
        <input type="checkbox" ${checked ? "checked" : ""} />
        <div class="package-item__info">
          <div class="package-item__name">${escHtml(pkg.name)}</div>
          <div class="package-item__desc">${escHtml(pkg.description)}</div>
        </div>
        <span class="package-item__type">${escHtml(pkg.package_type)}</span>
      `;
      div.addEventListener("click", (e) => {
        if (e.target.tagName === "INPUT") return;
        const cb = div.querySelector("input[type=checkbox]");
        cb.checked = !cb.checked;
        cb.dispatchEvent(new Event("change"));
      });
      const cb = div.querySelector("input[type=checkbox]");
      cb.addEventListener("change", () => {
        if (cb.checked) {
          state.selectedPackages.add(pkg.package_id);
          div.classList.add("package-item--checked");
        } else {
          state.selectedPackages.delete(pkg.package_id);
          div.classList.remove("package-item--checked");
        }
        refreshSubmitBtn();
      });
      container.appendChild(div);
    }
  };

  const escHtml = (s) => {
    if (!s) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  };

  const refreshSubmitBtn = () => {
    const btn = $("#submit-btn");
    btn.disabled = !state.activeDeveloper || state.selectedPackages.size === 0;
  };

  // --------------------------------------------------------------
  // Submit tab — submit
  // --------------------------------------------------------------

  const _sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const onSubmit = async () => {
    if (!state.activeDeveloper) { toast("请先登录", "err"); return; }
    if (state.selectedPackages.size === 0) { toast("请勾选要提交的内容包", "err"); return; }
    const packageIds = Array.from(state.selectedPackages);
    const aiRatio = parseFloat($("#ai-ratio").value || "0");
    const notes = $("#notes").value.trim();
    const payload = { notes, ai_ratio: Number.isFinite(aiRatio) ? aiRatio : 0 };
    const startedAt = new Date();

    const step = (n, total, msg) => {
      const elapsed = ((new Date() - startedAt) / 1000).toFixed(1);
      setStatus("#submit-status", `[${n}/${total}] ${msg}（${elapsed} 秒）`, "warn");
    };

    $("#submit-btn").disabled = true;

    try {
      step(1, 5, "检查冷却时间与输入");
      await _sleep(50);

      step(2, 5, `解析 ${packageIds.length} 个内容包`);
      await _sleep(50);

      step(3, 5, "打包文件为 7Z 归档");
      await _sleep(50);

      step(4, 5, "通过 SMTP 发送邮件（可能需要 10–60 秒）");
      const resp = await callApi("submit", state.activeDeveloper, null, null, payload, null, packageIds);
      const elapsed = ((new Date() - startedAt) / 1000).toFixed(1);

      if (!resp.ok) {
        setStatus("#submit-status", `[失败] 耗时 ${elapsed} 秒 · ${resp.message} (${resp.code || resp.status || ""})`, "err");
        toast(`提交失败：${resp.message}`, "err");
        $("#submit-btn").disabled = false;
        refreshSubmitBtn();
        return;
      }

      const r = resp.data;
      step(5, 5, `提交完成 · 归档 ${fmtBytes(r.archive_size)} · SHA256 ${shortSha(r.content_sha256)} · SMTP ${r.smtp_status}`);
      setStatus(
        "#submit-status",
        `[完成] 耗时 ${elapsed} 秒 · ID ${r.id} · ${fmtBytes(r.archive_size)} · sha256 ${shortSha(r.content_sha256)} · SMTP ${r.smtp_status}${r.smtp_code ? ` (${r.smtp_code})` : ""}`,
        "ok",
      );
      toast(`提交成功：${r.id}`, "ok");
      $("#status-bar").textContent = `上次提交 ${r.submitted_at}`;
      await refreshHistory();
      await refreshCooldown();
      state.selectedPackages.clear();
      renderPackages();
    } catch (err) {
      const elapsed = ((new Date() - startedAt) / 1000).toFixed(1);
      setStatus("#submit-status", `[异常] 耗时 ${elapsed} 秒 · ${err.message}`, "err");
      toast(`提交异常：${err.message}`, "err");
    } finally {
      $("#submit-btn").disabled = false;
      refreshSubmitBtn();
    }
  };

  // --------------------------------------------------------------
  // Submit tab — history & cooldown
  // --------------------------------------------------------------

  const renderHistory = (records) => {
    const list = $("#history-list");
    list.innerHTML = "";
    for (const r of records) {
      const li = document.createElement("li");
      li.className = r.smtp_status === "sent" ? "history-list li--ok" : "history-list li--err";
      li.innerHTML = `
        <div class="history-list li__row">
          <span class="history-list li__title">${escHtml(r.target_kind)}:${escHtml(r.target_id)}</span>
          <span class="history-list li__meta">${fmtTime(r.submitted_at)}</span>
        </div>
        <div class="history-list li__meta">${escHtml(r.developer_id)}  ${fmtBytes(r.archive_size)}  ${escHtml(r.archive_format)}  ${escHtml(r.smtp_status)}${r.smtp_code ? ` (${escHtml(r.smtp_code)})` : ""}</div>
        <div class="history-list li__sha">sha256: ${shortSha(r.content_sha256)}</div>
      `;
      list.appendChild(li);
    }
  };

  const renderCooldown = (seconds) => {
    const el = $("#cooldown-indicator");
    if (seconds <= 0) { el.textContent = "冷却空闲，可随时提交"; return; }
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    el.textContent = m > 0 ? `还需等待 ${m} 分 ${s} 秒` : `还需等待 ${s} 秒`;
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
  // Character editor
  // --------------------------------------------------------------

  let _selectedCharId = null;

  const renderCharList = async () => {
    const resp = await callApi("list_characters");
    if (!resp.ok) return;
    renderItemList("char-list", resp.data || [], (c) =>
      `<strong>${escHtml(c.display_name || c.name)}</strong><br/><small>${escHtml(c.id)}  ${((c.tags || []).join(", "))}</small>`
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
    $("#char-lang-style").value = char?.language_style || "";
    $("#char-emotion").value = char?.default_emotion || "neutral";
    $("#char-tags").value = (char?.tags || []).join(", ");
    $("#char-persona").value = char?.persona_doc || "";

    // Assigned dropdowns
    const setOpt = (id, val) => {
      const sel = $(`#${id}`);
      if (!sel) return;
      for (const opt of sel.options) { if (opt.value === val) { opt.selected = true; return; } }
    };
    setOpt("char-assigned-world", char?.assigned_world || "");
    setOpt("char-assigned-memory", char?.assigned_memory_pack || "");
    setOpt("char-assigned-voice", char?.assigned_voice_pack || "");
    setOpt("char-assigned-model", char?.assigned_model || "");

    // Memory config
    const mc = char?.memory_config || {};
    $("#char-mc-max-long").value = mc.max_long_term ?? 200;
    $("#char-mc-long-imp-min").value = mc.long_term_importance_min ?? 0.6;
    $("#char-mc-max-short").value = mc.max_short_term ?? 50;
    $("#char-mc-short-decay").value = mc.short_term_decay_rate ?? 0.05;
    $("#char-mc-short-imp-min").value = mc.short_term_importance_min ?? 0.3;
    $("#char-mc-max-ctx").value = mc.max_context_tokens ?? 8000;
    $("#char-mc-reserve").value = mc.reserve_tokens_for_reply ?? 2000;
    $("#char-mc-force-recall").value = mc.force_recall_on_history ? "true" : "false";

    $("#char-editor-hint").textContent = char
      ? `编辑：${char.display_name || char.name}`
      : "";
    refreshCharButtons();
  };

  const resetCharEditor = () => {
    _selectedCharId = null;
    $("#char-editing-id").value = "";
    $("#char-name").value = "";
    $("#char-display-name").value = "";
    $("#char-lang-style").value = "";
    $("#char-emotion").value = "neutral";
    $("#char-tags").value = "";
    $("#char-persona").value = "";
    $("#char-assigned-world").value = "";
    $("#char-assigned-memory").value = "";
    $("#char-assigned-voice").value = "";
    $("#char-assigned-model").value = "";
    $("#char-mc-max-long").value = 200;
    $("#char-mc-long-imp-min").value = 0.6;
    $("#char-mc-max-short").value = 50;
    $("#char-mc-short-decay").value = 0.05;
    $("#char-mc-short-imp-min").value = 0.3;
    $("#char-mc-max-ctx").value = 8000;
    $("#char-mc-reserve").value = 2000;
    $("#char-mc-force-recall").value = "true";
    $("#char-editor-hint").textContent = "";
    refreshCharButtons();
  };

  const onCharSelect = async (id) => {
    const resp = await callApi("get_character", id);
    if (resp.ok && resp.data) loadCharEditor(resp.data);
  };

  const onCharSave = async () => {
    const tags = $("#char-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
    const data = {
      id: $("#char-editing-id").value || undefined,
      name: $("#char-name").value.trim(),
      display_name: $("#char-display-name").value.trim(),
      language_style: $("#char-lang-style").value.trim(),
      voice_profile: "",
      default_emotion: $("#char-emotion").value.trim(),
      tags,
      persona_doc: $("#char-persona").value,
      assigned_world: $("#char-assigned-world").value,
      assigned_memory_pack: $("#char-assigned-memory").value,
      assigned_voice_pack: $("#char-assigned-voice").value,
      assigned_model: $("#char-assigned-model").value,
      memory_config: {
        max_long_term: parseInt($("#char-mc-max-long").value) || 200,
        long_term_importance_min: parseFloat($("#char-mc-long-imp-min").value) || 0.6,
        max_short_term: parseInt($("#char-mc-max-short").value) || 50,
        short_term_decay_rate: parseFloat($("#char-mc-short-decay").value) || 0.05,
        short_term_importance_min: parseFloat($("#char-mc-short-imp-min").value) || 0.3,
        max_context_tokens: parseInt($("#char-mc-max-ctx").value) || 8000,
        reserve_tokens_for_reply: parseInt($("#char-mc-reserve").value) || 2000,
        force_recall_on_history: $("#char-mc-force-recall").value === "true",
      },
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
    toast("角色已删除", "ok");
    resetCharEditor();
    await renderCharList();
  };

  const onCharExport = async () => {
    if (!_selectedCharId) return;
    const resp = await callApi("export_character", _selectedCharId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    // Refresh the submit-tab packages list and pre-tick this character
    // so the developer only needs to click "提交" once they arrive.
    await loadPackages();
    state.selectedPackages.add(`char:${_selectedCharId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，角色包已自动勾选", "ok");
    setStatus("#char-status", "已导出并跳转到创作提交", "ok");
  };

  const onCharImportPersona = async () => {
    if (!_selectedCharId) { toast("请先选择一个角色", "err"); return; }
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast("pywebview 文件对话框未就绪", "err");
      return;
    }
    let picked;
    try {
      picked = await window.pywebview.create_file_dialog(
        window.pywebview.types.OPEN,
        { file_types: ["md", "markdown", "txt"] }
      );
    } catch { toast("文件选择失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    const resp = await callApi("import_persona", _selectedCharId, picked);
    if (!resp.ok) { toast(`导入失败：${resp.message}`, "err"); return; }
    $("#char-persona").value = resp.data.message;
    toast("人设导入成功", "ok");
    setStatus("#char-status", "人设已导入", "ok");
  };

  // --------------------------------------------------------------
  // Character dropdown population
  // --------------------------------------------------------------

  const populateCharDropdowns = async () => {
    const worldsResp = await callApi("list_worlds");
    const worlds = worldsResp.ok ? (worldsResp.data || []) : [];
    populateSelect("#char-assigned-world", worlds, (w) => ({ value: w.id, label: w.name }));

    const memCharsResp = await callApi("list_memory_characters");
    const memChars = memCharsResp.ok ? (memCharsResp.data || []) : [];
    populateSelect("#char-assigned-memory", memChars, (c) => ({ value: c, label: c }));

    const voiceCharsResp = await callApi("list_voice_characters");
    const voiceChars = voiceCharsResp.ok ? (voiceCharsResp.data || []) : [];
    populateSelect("#char-assigned-voice", voiceChars, (c) => ({ value: c, label: c }));

    const modelsResp = await callApi("list_models");
    const models = modelsResp.ok ? (modelsResp.data || []) : [];
    populateSelect("#char-assigned-model", models, (m) => ({ value: m.id, label: m.name }));
  };

  const populateSelect = (selId, items, mapFn) => {
    const sel = $(selId);
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">  </option>';
    for (const item of items) {
      const { value, label } = mapFn(item);
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      sel.appendChild(opt);
    }
    sel.value = current;
  };

  // --------------------------------------------------------------
  // Memory editor
  // --------------------------------------------------------------

  let _selectedMemId = null;

  const renderMemList = async () => {
    const charId = $("#mem-char-id").value.trim();
    if (!charId) { renderItemList("mem-list", [], () => ""); return; }
    const resp = await callApi("list_memory_entries", charId);
    if (!resp.ok) { renderItemList("mem-list", [], () => ""); return; }
    renderItemList("mem-list", resp.data || [], (e) =>
      `<strong>[${e.type === "long" ? "长期" : "短期"}]</strong> ${escHtml(e.content.slice(0, 60))}${e.content.length > 60 ? "…" : ""}<br/><small>重要性: ${e.importance} · ${(e.tags || []).join(", ")}</small>`
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
    $("#mem-editor-hint").textContent = entry ? `编辑条目：${entry.content.slice(0, 30)}` : "";
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
    $("#mem-editor-hint").textContent = "";
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
    toast("记忆已保存", "ok");
    setStatus("#mem-status", `已保存：${resp.data.id}`, "ok");
    await renderMemList();
  };

  const onMemDelete = async () => {
    if (!_selectedMemId) return;
    const resp = await callApi("delete_memory_entry", _selectedMemId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("记忆已删除", "ok");
    resetMemEditor();
    await renderMemList();
  };

  const onMemExport = async () => {
    const charId = $("#mem-char").value.trim() || $("#mem-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    const resp = await callApi("export_memory_entries", charId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`memory:${charId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，记忆包已自动勾选", "ok");
    setStatus("#mem-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // World editor
  // --------------------------------------------------------------

  let _selectedWorldId = null;

  const renderWorldList = async () => {
    const resp = await callApi("list_worlds");
    if (!resp.ok) return;
    renderItemList("world-list", resp.data || [], (w) =>
      `<strong>${escHtml(w.name)}</strong><br/><small>${escHtml(w.id)}</small>`
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
    $("#world-editor-hint").textContent = world ? `编辑：${world.name}` : "";
    refreshWorldButtons();
  };

  const resetWorldEditor = () => {
    _selectedWorldId = null;
    $("#world-editing-id").value = "";
    $("#world-name").value = "";
    $("#world-config").value = "";
    $("#world-doc").value = "";
    $("#world-editor-hint").textContent = "";
    refreshWorldButtons();
  };

  const onWorldSave = async () => {
    let config = {};
    try { const raw = $("#world-config").value.trim(); if (raw) config = JSON.parse(raw); }
    catch { toast("JSON 格式错误", "err"); return; }
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
    toast("世界观已删除", "ok");
    resetWorldEditor();
    await renderWorldList();
  };

  const onWorldExport = async () => {
    if (!_selectedWorldId) return;
    const resp = await callApi("export_world", _selectedWorldId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`world:${_selectedWorldId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，世界观已自动勾选", "ok");
    setStatus("#world-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // 3D model viewer
  // --------------------------------------------------------------

  let _selectedModelId = null;

  const renderModelList = async () => {
    const resp = await callApi("list_models");
    if (!resp.ok) return;
    renderItemList("model-list", resp.data || [], (m) =>
      `<strong>${escHtml(m.name)}</strong><br/><small>${escHtml(m.format)}  ${fmtBytes(m.size_bytes)}</small>`
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
    // Trigger the real 3D preview.  We first fetch the file bytes via
    // the Python bridge (WKWebView can't fetch file:// URLs), then
    // hand them to the three-loader module.
    const container = $("#model-viewer-container");
    container.innerHTML = '<p class="status status--idle">加载预览…</p>';
    (async () => {
      const resp = await callApi("read_model_bytes", model.id);
      if (!resp.ok) {
        container.innerHTML = `<p class="status status--err">读取失败：${escHtml(resp.message || "")}</p>`;
        setStatus("#model-status", `读取失败：${resp.message || ""}`, "err");
        return;
      }
      if (!resp.data) {
        container.innerHTML = `<p class="status status--err">模型文件不可访问，请重新添加。</p>`;
        setStatus("#model-status", "模型文件不可访问", "err");
        return;
      }
      const info = resp.data;
      if (!window.DevKitThreeViewer) {
        container.innerHTML = `<p class="status status--err">three-loader.js 未加载，无法预览 3D 模型</p>`;
        return;
      }
      try {
        await window.DevKitThreeViewer.renderModel(container, info);
        setStatus("#model-status", `已加载 ${info.name}（${fmtBytes(info.size_bytes)}）`, "ok");
      } catch (err) {
        container.innerHTML = `<p class="status status--err">预览失败：${escHtml(err.message || String(err))}</p>`;
        setStatus("#model-status", `预览失败：${err.message || ""}`, "err");
      }
    })();
  };

  const onModelAdd = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) { toast("pywebview 文件对话框未就绪", "err"); return; }
    let picked;
    try { picked = await window.pywebview.create_file_dialog(window.pywebview.types.OPEN, { file_types: ["vrm", "glb", "gltf"] }); }
    catch { toast("文件选择失败", "err"); return; }
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
    toast("模型已移除", "ok");
    _selectedModelId = null;
    showModelInfo(null);
    await renderModelList();
  };

  // --------------------------------------------------------------
  // Voice clone
  // --------------------------------------------------------------

  let _selectedVoiceId = null;

  const populateVoiceEngines = async () => {
    const resp = await callApi("list_voice_engines");
    if (!resp.ok) return;
    const sel = $("#voice-engine");
    sel.innerHTML = "";
    for (const eng of resp.data || []) {
      const opt = document.createElement("option");
      opt.value = eng;
      opt.textContent = eng;
      sel.appendChild(opt);
    }
  };

  const renderVoiceList = async () => {
    const charId = $("#voice-char-id").value.trim();
    if (!charId) { renderItemList("voice-list", [], () => ""); return; }
    const resp = await callApi("list_voices", charId);
    if (!resp.ok) { renderItemList("voice-list", [], () => ""); return; }
    renderItemList("voice-list", resp.data || [], (v) =>
      `<strong>${escHtml(v.name)}</strong><br/><small>${escHtml(v.engine)} · ${v.sample_path ? "有样本文件" : "无样本"}</small>`
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
    $("#voice-editor-hint").textContent = voice ? `编辑：${voice.name}` : "";
    refreshVoiceButtons();
  };

  const resetVoiceEditor = () => {
    _selectedVoiceId = null;
    $("#voice-editing-id").value = "";
    $("#voice-char").value = $("#voice-char-id").value || "";
    $("#voice-name").value = "";
    $("#voice-engine").value = "melo-tts";
    $("#voice-sample-path").value = "";
    $("#voice-editor-hint").textContent = "";
    clearVoiceRecording();
    refreshVoiceButtons();
  };

  const onVoiceSave = async () => {
    const charId = $("#voice-char").value.trim();
    const name = $("#voice-name").value.trim();
    const engine = $("#voice-engine").value;
    const samplePath = $("#voice-sample-path").value.trim() || null;
    const recordedB64 = $("#voice-recorded-b64").value.trim() || null;
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    if (!name) { toast("请填写声音名称", "err"); return; }
    if (!samplePath && !recordedB64) {
      toast("请选择音频文件，或先录制一段样本", "err");
      return;
    }
    const resp = await callApi("save_voice", charId, name, samplePath, engine, recordedB64);
    if (!resp.ok) { setStatus("#voice-status", `保存失败：${resp.message}`, "err"); return; }
    toast("声音已保存", "ok");
    setStatus("#voice-status", `已保存：${resp.data.id}`, "ok");
    // Save succeeded — wipe the recording buffer so the next save
    // doesn't accidentally re-upload the same clip.
    clearVoiceRecording();
    await renderVoiceList();
  };

  const onVoiceDelete = async () => {
    if (!_selectedVoiceId) return;
    const resp = await callApi("delete_voice", _selectedVoiceId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("声音已删除", "ok");
    resetVoiceEditor();
    await renderVoiceList();
  };

  const onVoicePickFile = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) { toast("pywebview 文件对话框未就绪", "err"); return; }
    let picked;
    try { picked = await window.pywebview.create_file_dialog(window.pywebview.types.OPEN, { file_types: ["wav", "mp3", "m4a", "ogg", "flac"] }); }
    catch { toast("文件选择失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    $("#voice-sample-path").value = picked;
    // Clear any prior recording so the form doesn't try to upload
    // both at once.
    clearVoiceRecording();
  };

  // --------------------------------------------------------------
  // Voice recording (MediaRecorder)
  // --------------------------------------------------------------

  let _mediaRecorder = null;
  let _recordingChunks = [];
  let _recordingUrl = null;

  const setRecordingUi = (state) => {
    const recBtn = $("#voice-record-btn");
    const stopBtn = $("#voice-stop-btn");
    const useBtn = $("#voice-use-recording-btn");
    const hint = $("#voice-record-hint");
    const playback = $("#voice-playback");
    if (state === "idle") {
      recBtn.disabled = false;
      stopBtn.disabled = true;
      useBtn.disabled = !playback.src;
      hint.textContent = "直接通过麦克风录制参考样本。录制后试听，再点「使用此录音」填入。";
    } else if (state === "recording") {
      recBtn.disabled = true;
      stopBtn.disabled = false;
      useBtn.disabled = true;
      hint.textContent = "正在录音……点击「停止」结束。";
    } else if (state === "ready") {
      recBtn.disabled = false;
      stopBtn.disabled = true;
      useBtn.disabled = !playback.src;
      hint.textContent = "录音完成。点击播放试听，或点「使用此录音」将数据填入保存表单。";
    }
  };

  const clearVoiceRecording = () => {
    const playback = $("#voice-playback");
    if (playback.src && _recordingUrl) {
      URL.revokeObjectURL(_recordingUrl);
      _recordingUrl = null;
    }
    playback.removeAttribute("src");
    playback.hidden = true;
    playback.load();
    $("#voice-recorded-b64").value = "";
    _recordingChunks = [];
    setRecordingUi("idle");
  };

  const onVoiceRecord = async () => {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      toast("当前 webview 不支持麦克风录制（缺少 navigator.mediaDevices）", "err");
      return;
    }
    // Prefer a webm/opus mime if the browser offers it — small files,
    // good for short voice samples.
    const mimeCandidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    let mimeType = "";
    for (const m of mimeCandidates) {
      if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) {
        mimeType = m; break;
      }
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      toast(`麦克风权限被拒绝：${err.message || err}`, "err");
      return;
    }
    try {
      _mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (err) {
      toast(`MediaRecorder 初始化失败：${err.message || err}`, "err");
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    _recordingChunks = [];
    clearVoiceRecording();
    _mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) _recordingChunks.push(e.data);
    };
    _mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (_recordingChunks.length === 0) { setRecordingUi("idle"); return; }
      const blob = new Blob(_recordingChunks, { type: mimeType || "audio/webm" });
      _recordingUrl = URL.createObjectURL(blob);
      const playback = $("#voice-playback");
      playback.src = _recordingUrl;
      playback.hidden = false;
      // Base64-encode for the Python bridge.  FileReader is the most
      // portable across WKWebView / WebView2 / webkitgtk; we keep the
      // data-URL prefix so the server knows the mime.
      const reader = new FileReader();
      reader.onload = () => {
        const result = typeof reader.result === "string" ? reader.result : "";
        $("#voice-recorded-b64").value = result;
        // Using a recording invalidates any prior sample_path.
        $("#voice-sample-path").value = "";
        setRecordingUi("ready");
        toast("录音完成", "ok");
      };
      reader.onerror = () => {
        toast(`读取录音失败：${reader.error?.message || "未知错误"}`, "err");
        setRecordingUi("idle");
      };
      reader.readAsDataURL(blob);
    };
    _mediaRecorder.start();
    setRecordingUi("recording");
  };

  const onVoiceStop = () => {
    if (_mediaRecorder && _mediaRecorder.state !== "inactive") {
      _mediaRecorder.stop();
    }
  };

  const onVoiceUseRecording = () => {
    const b64 = $("#voice-recorded-b64").value.trim();
    if (!b64) { toast("暂无录音", "err"); return; }
    // The hidden b64 field is what gets uploaded by onVoiceSave —
    // nothing else to do here besides visual confirmation.
    $("#voice-sample-path").value = "";  // path is no longer the source
    toast("录音已填入，点保存上传", "ok");
  };

  // --------------------------------------------------------------
  // Item list renderer
  // --------------------------------------------------------------

  const renderItemList = (listId, items, templateFn) => {
    const list = $(`#${listId}`);
    list.innerHTML = "";
    if (!items || items.length === 0) {
      const empty = document.createElement("li");
      empty.className = "item-list__empty";
      empty.textContent = "";
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
  // Help modal
  // --------------------------------------------------------------

  const openHelp = () => {
    const overlay = $("#help-overlay");
    if (!overlay) return;
    overlay.hidden = false;
    switchHelpTab("general");
  };

  const closeHelp = () => {
    const overlay = $("#help-overlay");
    if (overlay) overlay.hidden = true;
  };

  const switchHelpTab = (tab) => {
    $$(".help-nav__btn").forEach((btn) => {
      btn.classList.toggle("help-nav__btn--active", btn.dataset.helpTab === tab);
    });
    $$(".help-content").forEach((content) => {
      content.hidden = content.id !== `help-content-${tab}`;
    });
  };

  // --------------------------------------------------------------
  // Bootstrap
  // --------------------------------------------------------------

  const loadBootstrap = async () => {
    try {
      const ping = await callApi("ping");
      if (!ping.ok) throw new Error("ping failed");
      const cfg = await callApi("whoami");
      if (!cfg.ok) throw new Error("whoami failed");
      renderConfig(cfg.data);
      const kinds = await callApi("target_kinds");
      if (kinds.ok) renderTargetKinds(kinds.data);
      const me = await callApi("current_developer");
      if (me.ok && me.data && me.data.developer_id) {
        state.activeDeveloper = me.data.developer_id;
        $("#developer-id").value = me.data.developer_id;
      }
      renderDeveloperChip();
      await refreshHistory();
      await refreshCooldown();
      await loadPackages();
      setStatus("#login-status", "就绪", "ok");
      $("#status-bar").textContent = "就绪";
      await populateCharDropdowns();
      await populateVoiceEngines();
    } catch (err) {
      console.error("bootstrap failed", err);
      setStatus("#login-status", `初始化失败：${err.message}`, "err");
      $("#status-bar").textContent = "";
    }
  };

  // --------------------------------------------------------------
  // Login / logout
  // --------------------------------------------------------------

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
    refreshSubmitBtn();
    toast("登录成功", "ok");
  };

  const onLogout = async () => {
    await callApi("logout");
    state.activeDeveloper = null;
    renderDeveloperChip();
    setStatus("#login-status", "已退出", "ok");
    await refreshCooldown();
    refreshSubmitBtn();
  };

  // --------------------------------------------------------------
  // Bind events
  // --------------------------------------------------------------

  const bind = () => {
    $$(".tab-nav__btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.tab) switchTab(btn.dataset.tab);
      });
    });

    // Help
    $("#help-btn").addEventListener("click", openHelp);
    $("#help-close-btn").addEventListener("click", closeHelp);
    $("#help-overlay").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeHelp(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHelp(); });
    $$(".help-nav__btn").forEach((btn) => {
      btn.addEventListener("click", () => switchHelpTab(btn.dataset.helpTab));
    });

    // Submit tab
    $("#login-btn").addEventListener("click", onLogin);
    $("#logout-btn").addEventListener("click", onLogout);
    $("#developer-id").addEventListener("keydown", (e) => { if (e.key === "Enter") onLogin(); });
    $("#submit-btn").addEventListener("click", onSubmit);
    $("#refresh-history-btn").addEventListener("click", refreshHistory);
    $("#packages-refresh-btn").addEventListener("click", loadPackages);

    // Character tab
    $("#char-refresh-btn").addEventListener("click", renderCharList);
    $("#char-new-btn").addEventListener("click", () => { resetCharEditor(); loadCharEditor(null); });
    $("#char-save-btn").addEventListener("click", onCharSave);
    $("#char-delete-btn").addEventListener("click", onCharDelete);
    $("#char-export-btn").addEventListener("click", onCharExport);
    $("#char-import-persona-btn").addEventListener("click", onCharImportPersona);
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
    $("#voice-record-btn").addEventListener("click", onVoiceRecord);
    $("#voice-stop-btn").addEventListener("click", onVoiceStop);
    $("#voice-use-recording-btn").addEventListener("click", onVoiceUseRecording);
    $("#voice-char-id").addEventListener("change", async () => {
      await renderVoiceList();
      // Keep the character's "assigned_voice_pack" dropdown in sync
      // — so that if the user types a brand-new char id here first
      // and then switches to the character tab, it shows up as a
      // selectable option without an extra manual refresh.
      await populateCharDropdowns();
    });
    $("#voice-list").addEventListener("click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_voice", li.dataset.id);
        if (resp.ok) loadVoiceEditor(resp.data);
      }
    });
  };

  // --------------------------------------------------------------
  // Start
  // --------------------------------------------------------------

  const start = () => {
    bind();
    if (window.pywebview && window.pywebview.api) loadBootstrap();
    else window.addEventListener("pywebviewready", () => loadBootstrap(), { once: true });
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
