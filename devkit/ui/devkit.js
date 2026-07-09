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

  // Theme handling (persisted in localStorage)
  const applyTheme = (theme) => {
    const root = document.documentElement;
    if (theme === "dark") {
      root.setAttribute("data-theme", "dark");
    } else if (theme === "light") {
      root.setAttribute("data-theme", "light");
    } else {
      // system - follow OS preference
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.setAttribute("data-theme", prefersDark ? "dark" : "light");
    }
  };

  const initTheme = () => {
    const saved = localStorage.getItem("devkit-theme") || "system";
    const select = $("#settings-theme");
    if (select) select.value = saved;
    applyTheme(saved);
    // Listen for system theme changes when in "system" mode
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      const current = localStorage.getItem("devkit-theme") || "system";
      if (current === "system") applyTheme("system");
    });
  };

  // Safe binding: only attach if element exists; wraps handler to catch errors.
  const on = (sel, event, handler) => {
    const el = $(sel);
    if (!el) { console.warn(`[devkit] element not found for binding: ${sel}`); return; }
    el.addEventListener(event, (e) => {
      try {
        const result = handler(e);
        if (result && typeof result.then === "function") {
          result.catch((err) => {
            console.error(`[devkit] ${sel} ${event} handler error:`, err);
            toast(`操作失败：${err.message || err}`, "err");
          });
        }
      } catch (err) {
        console.error(`[devkit] ${sel} ${event} handler error:`, err);
        toast(`操作失败：${err.message || err}`, "err");
      }
    });
  };

// Wait for pywebview API to be ready, then return the method.
// Caches the ready promise so rapid clicks don't re-create waiters.
let _apiReadyPromise = null;
let _apiReadyResolved = false;
const ensureApiReady = () => {
  if (window.pywebview && window.pywebview.api) {
    _apiReadyResolved = true;
    return Promise.resolve();
  }
  if (!_apiReadyPromise) {
    _apiReadyPromise = new Promise((resolve) => {
      if (window.pywebview && window.pywebview.api) {
        _apiReadyResolved = true;
        return resolve();
      }
      console.log("[devkit] waiting for pywebviewready...");
      window.addEventListener("pywebviewready", () => {
        console.log("[devkit] pywebviewready fired");
        _apiReadyResolved = true;
        resolve();
      }, { once: true });
      // Fallback: poll in case event already fired
      let polls = 0;
      const poll = setInterval(() => {
        if (window.pywebview && window.pywebview.api) {
          console.log("[devkit] bridge detected via poll");
          clearInterval(poll);
          _apiReadyResolved = true;
          resolve();
        }
        if (++polls > 50) { // 5 seconds max
          clearInterval(poll);
          console.error("[devkit] bridge NOT ready after 5s polling");
          resolve(); // resolve anyway to unblock and show error
        }
      }, 100);
    });
  }
  return _apiReadyPromise;
};

const callApi = async (method, ...args) => {
  console.log("[devkit] callApi:", method, args);
  await ensureApiReady();
  if (!window.pywebview || !window.pywebview.api) {
    console.error("[devkit] pywebview or api missing after wait", { pywebview: !!window.pywebview, api: !!(window.pywebview && window.pywebview.api) });
    throw new Error("pywebview js_api not ready");
  }
  const fn = window.pywebview.api[method];
  if (typeof fn !== "function") {
    console.error("[devkit] method not a function:", method, "available:", Object.keys(window.pywebview.api || {}));
    throw new Error(`DevKitApi.${method} is not a function`);
  }
  console.log("[devkit] calling", method);
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
  // SMTP settings
  // --------------------------------------------------------------

  const openSmtpModal = async () => {
    const resp = await callApi("get_smtp_config");
    if (resp.ok) {
      const cfg = resp.data;
      $("#smtp-host-input").value = cfg.host || "";
      $("#smtp-port-input").value = cfg.port || 465;
      $("#smtp-tls-input").checked = cfg.use_tls || false;
      $("#smtp-user-input").value = cfg.user || "";
      $("#smtp-password-input").value = cfg.password || "";
      $("#smtp-from-input").value = cfg.from_addr || "";
    }
    const subResp = await callApi("get_submission_config");
    if (subResp.ok) {
      $("#smtp-recipient-input").value = subResp.data.recipient || "panmofan@icloud.com";
    }
    $("#smtp-modal").hidden = false;
  };

  const closeSmtpModal = () => {
    $("#smtp-modal").hidden = true;
  };

  const saveSmtpConfig = async () => {
    const smtpConfig = {
      host: $("#smtp-host-input").value.trim(),
      port: parseInt($("#smtp-port-input").value, 10) || 465,
      use_tls: $("#smtp-tls-input").checked,
      user: $("#smtp-user-input").value.trim(),
      password: $("#smtp-password-input").value,
      from_addr: $("#smtp-from-input").value.trim(),
    };
    if (!smtpConfig.host || !smtpConfig.user || !smtpConfig.password || !smtpConfig.from_addr) {
      toast("请填写所有必填字段", "warn");
      return;
    }
    const resp = await callApi("save_smtp_config", smtpConfig);
    if (resp.ok) {
      toast("SMTP 设置已保存", "ok");
      closeSmtpModal();
      // Reload config
      const cfgResp = await callApi("whoami");
      if (cfgResp.ok) renderConfig(cfgResp.data);
    } else {
      toast(`保存失败：${resp.message}`, "err");
    }
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

  // Minimal Markdown → HTML renderer for the world-doc live preview
  // (C1.2).  Supports headings, bold/italic, inline code, unordered
  // and ordered lists, and paragraphs.  Output is escaped first to avoid
  // injecting raw HTML from user content.
  const renderMarkdown = (md) => {
    if (!md) return "";
    const lines = md.split(/\r?\n/);
    const out = [];
    let inUl = false, inOl = false;
    const closeLists = () => {
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (inOl) { out.push("</ol>"); inOl = false; }
    };
    const inline = (t) => escHtml(t)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(?<!\*)\*(?!\*)(.+?)\*(?!\*)/g, "<em>$1</em>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
    for (const line of lines) {
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { closeLists(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
      const ul = line.match(/^\s*[-*]\s+(.*)$/);
      if (ul) { if (!inUl) { out.push("<ul>"); inUl = true; } if (inOl) { out.push("</ol>"); inOl = false; } out.push(`<li>${inline(ul[1])}</li>`); continue; }
      const ol = line.match(/^\s*\d+\.\s+(.*)$/);
      if (ol) { if (!inOl) { out.push("<ol>"); inOl = true; } if (inUl) { out.push("</ul>"); inUl = false; } out.push(`<li>${inline(ol[1])}</li>`); continue; }
      if (line.trim() === "") { closeLists(); continue; }
      closeLists();
      out.push(`<p>${inline(line)}</p>`);
    }
    closeLists();
    return out.join("\n");
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
    if (!el) return;
    if (seconds <= 0) { el.textContent = "冷却空闲，可随时提交"; return; }
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    el.textContent = m > 0 ? `还需等待 ${m} 分 ${s} 秒` : `还需等待 ${s} 秒`;
  };

  // Periodic cooldown refresh (every 5s) so UI updates in real-time
  let _cooldownTimer = null;
  const startCooldownTimer = () => {
    if (_cooldownTimer) return;
    _cooldownTimer = setInterval(() => { refreshCooldown(); }, 5000);
    console.log("[devkit] cooldown timer started (5s interval)");
  };
  const stopCooldownTimer = () => {
    if (_cooldownTimer) { clearInterval(_cooldownTimer); _cooldownTimer = null; }
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

    // Character config JSON (C2.3)
    const cfg = char?.character_config || {};
    const cfgStr = Object.keys(cfg).length > 0 ? JSON.stringify(cfg, null, 2) : "";
    $("#char-config-json").value = cfgStr;

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
    $("#char-config-json").value = "";
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
      character_config: (() => {
        try { return JSON.parse($("#char-config-json").value.trim() || "{}"); }
        catch { return {}; }
      })(),
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
    $("#char-persona").value = resp.data || "";
    toast("人设文档已导入", "ok");
  };

  const onCharPersonaTemplate = async () => {
    const picker = $("#char-persona-template-picker");
    if (picker.style.display !== "none") { picker.style.display = "none"; return; }
    const resp = await callApi("get_persona_templates");
    if (!resp.ok) { toast("获取模板列表失败", "err"); return; }
    const select = $("#char-persona-template-select");
    select.innerHTML = '<option value="">— 选择模板 —</option>';
    for (const [name, _] of Object.entries(resp.data || {})) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    picker.style.display = "flex";
  };

  const onCharPersonaTemplateApply = () => {
    const name = $("#char-persona-template-select").value;
    if (!name) { toast("请选择一个模板", "err"); return; }
    callApi("get_persona_templates").then(resp => {
      if (resp.ok && resp.data && resp.data[name]) {
        if ($("#char-persona").value.trim() && !confirm("当前人设文档内容将被覆盖，确定要继续吗？")) return;
        $("#char-persona").value = resp.data[name];
        $("#char-persona-template-picker").style.display = "none";
        toast("模板已应用", "ok");
      }
    });
  };

  const onCharPersonaTemplateCancel = () => {
    $("#char-persona-template-picker").style.display = "none";
  };

  const onCharConfigValidate = async () => {
    let config;
    try { config = JSON.parse($("#char-config-json").value.trim() || "{}"); }
    catch { setStatus("#char-config-status", "JSON 格式错误", "err"); return; }
    const resp = await callApi("validate_character_config", config);
    if (!resp.ok) { setStatus("#char-config-status", "校验请求失败", "err"); return; }
    const r = resp.data;
    if (r.ok) { setStatus("#char-config-status", "配置通过校验", "ok"); }
    else { setStatus("#char-config-status", (r.errors || []).join("；"), "err"); }
  };

  const onCharConfigAutofill = async () => {
    const persona = $("#char-persona").value.trim();
    if (!persona) { toast("请先填写人设文档", "err"); return; }
    const resp = await callApi("auto_suggest", "角色配置：\n" + persona.slice(0, 500));
    if (!resp.ok) { toast("自动填写失败", "err"); return; }
    const suggestion = resp.data?.suggestion || "";
    const existing = $("#char-config-json").value.trim();
    const comment = "// AI 建议（请复核后使用）：\n";
    $("#char-config-json").value = existing ? existing + "\n\n" + comment + suggestion : comment + suggestion;
    toast("已添加 AI 建议（标记为 source='ai_suggested'）", "ok");
  };

  // ---- save/load the character config JSON ----

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

  // ---- world custom events (C1.1) ----
  let _selectedWorldEventId = null;

  const renderWorldEvents = async (worldId) => {
    const resp = await callApi("list_world_events", worldId);
    if (!resp.ok) { renderItemList("world-event-list", [], () => ""); return; }
    renderItemList("world-event-list", resp.data || [], (e) =>
      `<strong>[${escHtml(e.kind)}] ${escHtml(e.name)}</strong><br/><small>优先级 ${e.priority} · ${e.is_enabled ? "启用" : "禁用"}</small>`
    );
  };

  const loadWorldEventEditor = (ev) => {
    _selectedWorldEventId = ev?.id || null;
    $("#world-event-editing-id").value = ev?.id || "";
    $("#world-event-name").value = ev?.name || "";
    $("#world-event-priority").value = ev?.priority ?? 50;
    $("#world-event-enabled").checked = ev ? !!ev.is_enabled : true;
    $("#world-event-trigger").value = ev?.trigger ? JSON.stringify(ev.trigger, null, 2) : "";
    $("#world-event-scene").value = ev?.scene || "";
    $("#world-event-effects").value = ev?.effects ? JSON.stringify(ev.effects) : "";
    $("#world-event-delete-btn").disabled = !ev;
  };

  const onWorldEventNew = () => { loadWorldEventEditor(null); };

  const onWorldEventSave = async () => {
    if (!_selectedWorldId) { toast("请先选择或保存世界观", "err"); return; }
    let trigger = {};
    try { const t = $("#world-event-trigger").value.trim(); if (t) trigger = JSON.parse(t); }
    catch { toast("触发器 JSON 格式错误", "err"); return; }
    let effects = {};
    try { const e = $("#world-event-effects").value.trim(); if (e) effects = JSON.parse(e); }
    catch { toast("影响 JSON 格式错误", "err"); return; }
    const data = {
      id: $("#world-event-editing-id").value || undefined,
      world_id: _selectedWorldId,
      name: $("#world-event-name").value.trim(),
      priority: parseInt($("#world-event-priority").value, 10) || 50,
      is_enabled: $("#world-event-enabled").checked,
      trigger,
      scene: $("#world-event-scene").value.trim(),
      effects,
    };
    if (!data.name) { toast("请填写事件名", "err"); return; }
    const resp = await callApi("save_world_event", data);
    if (!resp.ok) { setStatus("#world-event-status", `保存失败：${resp.message}`, "err"); return; }
    toast("事件已保存", "ok");
    setStatus("#world-event-status", `已保存：${resp.data.id}`, "ok");
    await renderWorldEvents(_selectedWorldId);
  };

  const onWorldEventDelete = async () => {
    if (!_selectedWorldId || !_selectedWorldEventId) return;
    const resp = await callApi("delete_world_event", _selectedWorldId, _selectedWorldEventId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("事件已删除", "ok");
    loadWorldEventEditor(null);
    await renderWorldEvents(_selectedWorldId);
  };

  const loadWorldEditor = async (world) => {
    _selectedWorldId = world?.id || null;
    const cfg = (world && world.config) || {};
    $("#world-editing-id").value = world?.id || "";
    $("#world-name").value = world?.name || "";
    $("#world-config").value = Object.keys(cfg).length ? JSON.stringify(cfg, null, 2) : "";
    $("#world-doc").value = (world && world.world_doc) || "";
    $("#world-cfg-timeflow").value = cfg.time_flow_multiplier ?? 30;
    $("#world-cfg-daylen").value = cfg.day_length_minutes ?? 1440;
    $("#world-cfg-night").value = cfg.night_ratio ?? 0.4;
    $("#world-cfg-weather").value = JSON.stringify(cfg.weather_probabilities || {}, null, 2);
    $("#world-cfg-lighting").value = (cfg.lighting_presets || ["default", "warm", "cold", "dramatic"]).join(", ");
    $("#world-cfg-audio").value = (cfg.ambient_audio_library || []).join(", ");
    $("#world-editor-hint").textContent = world ? `编辑：${world.name}` : "";
    $("#world-doc-preview").hidden = true;
    $("#world-doc-preview").innerHTML = "";
    $("#world-doc-lint").textContent = "";
    $("#world-doc-lint").className = "status status--idle";
    refreshWorldButtons();
    if (_selectedWorldId) await renderWorldEvents(_selectedWorldId);
  };

  const resetWorldEditor = () => {
    _selectedWorldId = null;
    $("#world-editing-id").value = "";
    $("#world-name").value = "";
    $("#world-config").value = "";
    $("#world-doc").value = "";
    $("#world-cfg-timeflow").value = 30;
    $("#world-cfg-daylen").value = 1440;
    $("#world-cfg-night").value = 0.4;
    $("#world-cfg-weather").value = "";
    $("#world-cfg-lighting").value = "default, warm, cold, dramatic";
    $("#world-cfg-audio").value = "";
    $("#world-editor-hint").textContent = "";
    $("#world-doc-preview").hidden = true;
    $("#world-doc-preview").innerHTML = "";
    $("#world-event-list").innerHTML = "";
    refreshWorldButtons();
  };

  const _collectWorldConfig = () => {
    let raw = {};
    try { const t = $("#world-config").value.trim(); if (t) raw = JSON.parse(t); }
    catch { raw = {}; }
    const lighting = $("#world-cfg-lighting").value.split(",").map((s) => s.trim()).filter(Boolean);
    const audio = $("#world-cfg-audio").value.split(",").map((s) => s.trim()).filter(Boolean);
    let weather = {};
    try { const w = $("#world-cfg-weather").value.trim(); if (w) weather = JSON.parse(w); }
    catch { weather = {}; }
    return {
      ...raw,
      time_flow_multiplier: parseFloat($("#world-cfg-timeflow").value) || 30,
      day_length_minutes: parseFloat($("#world-cfg-daylen").value) || 1440,
      night_ratio: parseFloat($("#world-cfg-night").value) || 0.4,
      weather_probabilities: weather,
      lighting_presets: lighting,
      ambient_audio_library: audio,
    };
  };

  const onWorldSave = async () => {
    const config = _collectWorldConfig();
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

  const onWorldCheckConfig = async () => {
    const config = _collectWorldConfig();
    const resp = await callApi("validate_world_config", config);
    if (!resp.ok) { $("#world-cfg-check-result").textContent = `检查失败：${resp.message}`; $("#world-cfg-check-result").className = "status status--err"; return; }
    const r = resp.data;
    $("#world-cfg-check-result").textContent = r.ok ? "配置校验通过" : ("校验未通过：" + (r.errors || []).join("；"));
    $("#world-cfg-check-result").className = r.ok ? "status status--ok" : "status status--err";
  };

  const onWorldDocPreview = async () => {
    const md = $("#world-doc").value;
    const resp = await callApi("lint_world_doc", md);
    if (resp.ok) {
      const r = resp.data;
      $("#world-doc-lint").textContent = r.ok ? "文档结构完整" : ("缺失关键字段：" + (r.missing || []).join(", "));
      $("#world-doc-lint").className = r.ok ? "status status--ok" : "status status--warn";
    }
    const el = $("#world-doc-preview");
    el.hidden = !el.hidden ? true : false;
    if (!el.hidden) el.innerHTML = renderMarkdown(md);
  };

  const onWorldDocTemplate = async () => {
    const picker = $("#world-doc-template-picker");
    if (picker.style.display !== "none") { picker.style.display = "none"; return; }
    const resp = await callApi("get_world_doc_templates");
    if (!resp.ok) { toast("获取模板列表失败", "err"); return; }
    const select = $("#world-doc-template-select");
    select.innerHTML = '<option value="">— 选择模板 —</option>';
    for (const [name, _] of Object.entries(resp.data || {})) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
    picker.style.display = "flex";
  };

  const onWorldDocTemplateApply = () => {
    const name = $("#world-doc-template-select").value;
    if (!name) { toast("请选择一个模板", "err"); return; }
    callApi("get_world_doc_templates").then(resp => {
      if (resp.ok && resp.data && resp.data[name]) {
        if ($("#world-doc").value.trim() && !confirm("当前文档内容将被覆盖，确定要继续吗？")) return;
        $("#world-doc").value = resp.data[name];
        $("#world-doc-template-picker").style.display = "none";
        toast("模板已应用", "ok");
      }
    });
  };

  const onWorldDocTemplateCancel = () => {
    $("#world-doc-template-picker").style.display = "none";
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
    $("#voice-sample-path").value = "";
    toast("录音已填入，点保存上传", "ok");
  };

  // --------------------------------------------------------------
  // Voice generation (TTS)
  // --------------------------------------------------------------

  const onVoiceTtsGenerate = async () => {
    const charId = $("#voice-tts-char").value.trim();
    const name = $("#voice-tts-name").value.trim();
    const text = $("#voice-tts-text").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    if (!name) { toast("请填写声音名称", "err"); return; }
    if (!text) { toast("请填写生成文本", "err"); return; }
    const resp = await callApi("generate_voice_from_text", charId, name, text);
    if (!resp.ok) { toast(`生成失败：${resp.message}`, "err"); return; }
    toast(`语音生成成功：${resp.data.id}`, "ok");
    if ($("#voice-char-id").value === charId) await renderVoiceList();
  };

  // --------------------------------------------------------------
  // Voice clone from file
  // --------------------------------------------------------------

  let _voiceClonePath = "";

  const onVoiceClonePick = async () => {
    if (!window.pywebview || !window.pywebview.create_file_dialog) { toast("pywebview 文件对话框未就绪", "err"); return; }
    let picked;
    try { picked = await window.pywebview.create_file_dialog(window.pywebview.types.OPEN, { file_types: ["wav", "mp3", "m4a", "ogg", "flac"] }); }
    catch { toast("文件选择失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    _voiceClonePath = picked;
    $("#voice-clone-path").value = picked;
  };

  const onVoiceClone = async () => {
    const charId = $("#voice-clone-char").value.trim();
    const name = $("#voice-clone-name").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    if (!name) { toast("请填写声音名称", "err"); return; }
    if (!_voiceClonePath) { toast("请选择源音频文件", "err"); return; }
    const resp = await callApi("clone_voice_from_file", charId, name, _voiceClonePath);
    if (!resp.ok) { toast(`克隆失败：${resp.message}`, "err"); return; }
    toast(`声音克隆成功：${resp.data.id}`, "ok");
    if ($("#voice-char-id").value === charId) await renderVoiceList();
  };

  // --------------------------------------------------------------
  // Voice export
  // --------------------------------------------------------------

  const onVoiceExport = async () => {
    if (!_selectedVoiceId) { toast("请先选择一个声音样本", "err"); return; }
    const resp = await callApi("export_voice", _selectedVoiceId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`voice:${_selectedVoiceId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，声音包已自动勾选", "ok");
    setStatus("#voice-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // Model generation + export
  // --------------------------------------------------------------

  const onModelGenerate = async () => {
    const desc = $("#model-gen-desc").value.trim();
    const name = $("#model-gen-name").value.trim();
    if (!desc) { toast("请填写模型描述", "err"); return; }
    const resp = await callApi("generate_model", desc, name || undefined);
    if (!resp.ok) { toast(`生成失败：${resp.message}`, "err"); return; }
    toast(`模型生成成功：${resp.data.name}`, "ok");
    await renderModelList();
  };

  const onModelExport = async () => {
    if (!_selectedModelId) { toast("请先选择一个模型", "err"); return; }
    const resp = await callApi("export_model", _selectedModelId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`model:${_selectedModelId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，模型包已自动勾选", "ok");
    setStatus("#model-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // Plot editor
  // --------------------------------------------------------------

  let _selectedPlotId = null;
  let _selectedPlotNodeId = null;
  let _selectedPlotEdgeId = null;

  const renderPlotList = async () => {
    const resp = await callApi("list_plots");
    if (!resp.ok) return;
    renderItemList("plot-list", resp.data || [], (p) =>
      `<strong>${escHtml(p.name)}</strong><br/><small>${escHtml(p.id)}</small>`
    );
    refreshPlotButtons();
  };

  const refreshPlotButtons = () => {
    $("#plot-export-btn").disabled = !_selectedPlotId;
    $("#plot-delete-btn").disabled = !_selectedPlotId;
  };

  const loadPlotEditor = (plot) => {
    _selectedPlotId = plot?.id || null;
    $("#plot-editing-id").value = plot?.id || "";
    $("#plot-name").value = plot?.name || "";
    $("#plot-description").value = plot?.description || "";
    $("#plot-editor-hint").textContent = plot ? `编辑：${plot.name}` : "";
    refreshPlotButtons();
  };

  const resetPlotEditor = () => {
    _selectedPlotId = null;
    $("#plot-editing-id").value = "";
    $("#plot-name").value = "";
    $("#plot-description").value = "";
    $("#plot-editor-hint").textContent = "";
    $("#plot-node-list").innerHTML = "";
    $("#plot-edge-list").innerHTML = "";
    _selectedPlotNodeId = null;
    _selectedPlotEdgeId = null;
    refreshPlotButtons();
  };

  const onPlotSave = async () => {
    const data = {
      id: $("#plot-editing-id").value || undefined,
      name: $("#plot-name").value.trim(),
      description: $("#plot-description").value.trim(),
    };
    if (!data.name) { toast("请填写剧情名称", "err"); return; }
    const resp = await callApi("save_plot", data);
    if (!resp.ok) { setStatus("#plot-status", `保存失败：${resp.message}`, "err"); return; }
    toast("剧情已保存", "ok");
    setStatus("#plot-status", `已保存：${resp.data.id}`, "ok");
    await renderPlotList();
  };

  const onPlotDelete = async () => {
    if (!_selectedPlotId) return;
    const resp = await callApi("delete_plot", _selectedPlotId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("剧情已删除", "ok");
    resetPlotEditor();
    await renderPlotList();
  };

  const onPlotExport = async () => {
    if (!_selectedPlotId) return;
    const resp = await callApi("export_plot", _selectedPlotId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`plot:${_selectedPlotId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，剧情包已自动勾选", "ok");
    setStatus("#plot-status", "已导出并跳转到创作提交", "ok");
  };

  // Plot nodes
  const renderPlotNodes = async (plotId) => {
    const resp = await callApi("get_plot_nodes", plotId);
    if (!resp.ok) { renderItemList("plot-node-list", [], () => ""); return; }
    renderItemList("plot-node-list", resp.data || [], (n) =>
      `<strong>[${n.type}]</strong> ${escHtml(n.name)}<br/><small>${escHtml((n.content || "").slice(0, 40))}</small>`
    );
  };

  const loadPlotNodeEditor = (node) => {
    _selectedPlotNodeId = node?.id || null;
    $("#plot-node-id").value = node?.id || "";
    $("#plot-node-name").value = node?.name || "";
    $("#plot-node-type").value = node?.type || "dialogue";
    $("#plot-node-content").value = node?.content || "";
  };

  const onPlotNodeNew = () => {
    _selectedPlotNodeId = null;
    $("#plot-node-id").value = "";
    $("#plot-node-name").value = "";
    $("#plot-node-type").value = "dialogue";
    $("#plot-node-content").value = "";
  };

  const onPlotNodeSave = async () => {
    if (!_selectedPlotId) { toast("请先选择或保存剧情", "err"); return; }
    const data = {
      id: $("#plot-node-id").value || undefined,
      plot_id: _selectedPlotId,
      name: $("#plot-node-name").value.trim(),
      type: $("#plot-node-type").value,
      content: $("#plot-node-content").value,
    };
    if (!data.name) { toast("请填写节点名称", "err"); return; }
    const resp = await callApi("save_plot_node", data);
    if (!resp.ok) { setStatus("#plot-status", `节点保存失败：${resp.message}`, "err"); return; }
    toast("节点已保存", "ok");
    await renderPlotNodes(_selectedPlotId);
  };

  const onPlotNodeDelete = async () => {
    if (!_selectedPlotNodeId) { toast("请先选择一个节点", "err"); return; }
    const resp = await callApi("delete_plot_node", _selectedPlotNodeId);
    if (!resp.ok) { toast("删除节点失败", "err"); return; }
    toast("节点已删除", "ok");
    onPlotNodeNew();
    if (_selectedPlotId) await renderPlotNodes(_selectedPlotId);
  };

  // Plot edges
  const renderPlotEdges = async (plotId) => {
    const resp = await callApi("get_plot_edges", plotId);
    if (!resp.ok) { renderItemList("plot-edge-list", [], () => ""); return; }
    renderItemList("plot-edge-list", resp.data || [], (e) =>
      `<strong>${escHtml(e.source_node_id)} → ${escHtml(e.target_node_id)}</strong><br/><small>${escHtml(e.label || "")}</small>`
    );
  };

  const loadPlotEdgeEditor = (edge) => {
    _selectedPlotEdgeId = edge?.id || null;
    $("#plot-edge-source").value = edge?.source_node_id || "";
    $("#plot-edge-target").value = edge?.target_node_id || "";
    $("#plot-edge-label").value = edge?.label || "";
  };

  const onPlotEdgeNew = () => {
    _selectedPlotEdgeId = null;
    $("#plot-edge-source").value = "";
    $("#plot-edge-target").value = "";
    $("#plot-edge-label").value = "";
  };

  const onPlotEdgeSave = async () => {
    if (!_selectedPlotId) { toast("请先选择或保存剧情", "err"); return; }
    const source = $("#plot-edge-source").value.trim();
    const target = $("#plot-edge-target").value.trim();
    if (!source || !target) { toast("请填写来源和目标节点 ID", "err"); return; }
    const data = {
      id: _selectedPlotEdgeId || undefined,
      plot_id: _selectedPlotId,
      source_node_id: source,
      target_node_id: target,
      label: $("#plot-edge-label").value.trim(),
    };
    const resp = await callApi("save_plot_edge", data);
    if (!resp.ok) { setStatus("#plot-status", `连线保存失败：${resp.message}`, "err"); return; }
    toast("连线已保存", "ok");
    await renderPlotEdges(_selectedPlotId);
  };

  const onPlotEdgeDelete = async () => {
    if (!_selectedPlotEdgeId) { toast("请先选择一条连线", "err"); return; }
    const resp = await callApi("delete_plot_edge", _selectedPlotEdgeId);
    if (!resp.ok) { toast("删除连线失败", "err"); return; }
    toast("连线已删除", "ok");
    onPlotEdgeNew();
    if (_selectedPlotId) await renderPlotEdges(_selectedPlotId);
  };

  // --------------------------------------------------------------
  // Dialog editor
  // --------------------------------------------------------------

  let _selectedDialogId = null;

  const renderDialogList = async () => {
    const charId = $("#dialog-char-id").value.trim();
    if (!charId) { renderItemList("dialog-list", [], () => ""); return; }
    const resp = await callApi("list_dialogs", charId);
    if (!resp.ok) { renderItemList("dialog-list", [], () => ""); return; }
    renderItemList("dialog-list", resp.data || [], (d) =>
      `<strong>[${escHtml(d.scene || "general")}]</strong> ${escHtml((d.user_message || "").slice(0, 40))}<br/><small>情感: ${escHtml(d.emotion || "neutral")}</small>`
    );
    refreshDialogButtons();
  };

  const refreshDialogButtons = () => {
    const hasSel = !!_selectedDialogId;
    $("#dialog-export-btn").disabled = !hasSel;
    $("#dialog-delete-btn").disabled = !hasSel;
  };

  const loadDialogEditor = (dialog) => {
    _selectedDialogId = dialog?.id || null;
    $("#dialog-editing-id").value = dialog?.id || "";
    $("#dialog-char").value = dialog?.character_id || $("#dialog-char-id").value || "";
    $("#dialog-scene").value = dialog?.scene || "";
    $("#dialog-user-msg").value = dialog?.user_message || "";
    $("#dialog-char-msg").value = dialog?.character_message || "";
    $("#dialog-emotion").value = dialog?.emotion || "neutral";
    $("#dialog-editor-hint").textContent = dialog ? `编辑对话` : "";
    refreshDialogButtons();
  };

  const resetDialogEditor = () => {
    _selectedDialogId = null;
    $("#dialog-editing-id").value = "";
    $("#dialog-char").value = $("#dialog-char-id").value || "";
    $("#dialog-scene").value = "";
    $("#dialog-user-msg").value = "";
    $("#dialog-char-msg").value = "";
    $("#dialog-emotion").value = "neutral";
    $("#dialog-editor-hint").textContent = "";
    refreshDialogButtons();
  };

  const onDialogSave = async () => {
    const data = {
      id: $("#dialog-editing-id").value || undefined,
      character_id: $("#dialog-char").value.trim(),
      scene: $("#dialog-scene").value.trim(),
      user_message: $("#dialog-user-msg").value,
      character_message: $("#dialog-char-msg").value,
      emotion: $("#dialog-emotion").value.trim(),
    };
    if (!data.character_id) { toast("请填写角色 ID", "err"); return; }
    if (!data.character_message) { toast("请填写角色回复", "err"); return; }
    const resp = await callApi("save_dialog", data);
    if (!resp.ok) { setStatus("#dialog-status", `保存失败：${resp.message}`, "err"); return; }
    toast("对话已保存", "ok");
    setStatus("#dialog-status", `已保存：${resp.data.id}`, "ok");
    await renderDialogList();
  };

  const onDialogDelete = async () => {
    if (!_selectedDialogId) return;
    const resp = await callApi("delete_dialog", _selectedDialogId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("对话已删除", "ok");
    resetDialogEditor();
    await renderDialogList();
  };

  const onDialogCheckMin = async () => {
    const charId = $("#dialog-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    const resp = await callApi("check_dialog_minimum", charId);
    if (!resp.ok) {
      $("#dialog-check-result").textContent = `检查失败：${resp.message}`;
      $("#dialog-check-result").className = "status status--err";
      return;
    }
    const r = resp.data;
    $("#dialog-check-result").textContent = r.message;
    $("#dialog-check-result").className = r.ok ? "status status--ok" : "status status--warn";
  };

  const onDialogExport = async () => {
    const charId = $("#dialog-char").value.trim() || $("#dialog-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    const resp = await callApi("export_dialogs", charId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`dialog:${charId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，对话包已自动勾选", "ok");
    setStatus("#dialog-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // Motion editor
  // --------------------------------------------------------------

  let _selectedMotionId = null;

  const renderMotionList = async () => {
    const charId = $("#motion-char-id").value.trim();
    if (!charId) { renderItemList("motion-list", [], () => ""); return; }
    const resp = await callApi("list_motions", charId);
    if (!resp.ok) { renderItemList("motion-list", [], () => ""); return; }
    renderItemList("motion-list", resp.data || [], (m) =>
      `<strong>${escHtml(m.name)}</strong><br/><small>${escHtml((m.description || "").slice(0, 40))}</small>`
    );
    refreshMotionButtons();
  };

  const refreshMotionButtons = () => {
    const hasSel = !!_selectedMotionId;
    $("#motion-export-btn").disabled = !hasSel;
    $("#motion-delete-btn").disabled = !hasSel;
  };

  const loadMotionEditor = (motion) => {
    _selectedMotionId = motion?.id || null;
    $("#motion-editing-id").value = motion?.id || "";
    $("#motion-char").value = motion?.character_id || $("#motion-char-id").value || "";
    $("#motion-name").value = motion?.name || "";
    $("#motion-description").value = motion?.description || "";
    $("#motion-params").value = motion?.params ? JSON.stringify(motion.params, null, 2) : "";
    $("#motion-file-path").value = motion?.file_path || "";
    $("#motion-editor-hint").textContent = motion ? `编辑：${motion.name}` : "";
    refreshMotionButtons();
  };

  const resetMotionEditor = () => {
    _selectedMotionId = null;
    $("#motion-editing-id").value = "";
    $("#motion-char").value = $("#motion-char-id").value || "";
    $("#motion-name").value = "";
    $("#motion-description").value = "";
    $("#motion-params").value = "";
    $("#motion-file-path").value = "";
    $("#motion-editor-hint").textContent = "";
    refreshMotionButtons();
  };

  const onMotionSave = async () => {
    let params = {};
    try { const raw = $("#motion-params").value.trim(); if (raw) params = JSON.parse(raw); }
    catch { toast("JSON 格式错误", "err"); return; }
    const data = {
      id: $("#motion-editing-id").value || undefined,
      character_id: $("#motion-char").value.trim(),
      name: $("#motion-name").value.trim(),
      description: $("#motion-description").value.trim(),
      params,
      file_path: $("#motion-file-path").value.trim() || undefined,
    };
    if (!data.character_id) { toast("请填写角色 ID", "err"); return; }
    if (!data.name) { toast("请填写动作名称", "err"); return; }
    const resp = await callApi("save_motion", data);
    if (!resp.ok) { setStatus("#motion-status", `保存失败：${resp.message}`, "err"); return; }
    toast("动作已保存", "ok");
    setStatus("#motion-status", `已保存：${resp.data.id}`, "ok");
    await renderMotionList();
  };

  const onMotionDelete = async () => {
    if (!_selectedMotionId) return;
    const resp = await callApi("delete_motion", _selectedMotionId);
    if (!resp.ok) { toast("删除失败", "err"); return; }
    toast("动作已删除", "ok");
    resetMotionEditor();
    await renderMotionList();
  };

  const onMotionImport = async () => {
    const charId = $("#motion-char").value.trim() || $("#motion-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    if (!window.pywebview || !window.pywebview.create_file_dialog) { toast("pywebview 文件对话框未就绪", "err"); return; }
    let picked;
    try { picked = await window.pywebview.create_file_dialog(window.pywebview.types.OPEN, { file_types: ["bvh", "fbx", "glb", "gltf"] }); }
    catch { toast("文件选择失败", "err"); return; }
    if (!picked || picked.length === 0) return;
    const resp = await callApi("import_motion_file", charId, picked);
    if (!resp.ok) { toast(`导入失败：${resp.message}`, "err"); return; }
    toast("动作文件已导入", "ok");
    $("#motion-file-path").value = picked;
    await renderMotionList();
  };

  const onMotionExport = async () => {
    const charId = $("#motion-char").value.trim() || $("#motion-char-id").value.trim();
    if (!charId) { toast("请填写角色 ID", "err"); return; }
    const resp = await callApi("export_motions", charId);
    if (!resp.ok) { toast(`导出失败：${resp.message}`, "err"); return; }
    await loadPackages();
    state.selectedPackages.add(`motion:${charId}`);
    renderPackages();
    refreshSubmitBtn();
    switchTab("submit");
    toast("已跳转创作提交，动作包已自动勾选", "ok");
    setStatus("#motion-status", "已导出并跳转到创作提交", "ok");
  };

  // --------------------------------------------------------------
  // AI assistant
  // --------------------------------------------------------------

  const renderAiLog = async () => {
    const resp = await callApi("list_assist_log", 50, 0);
    if (!resp.ok) { renderItemList("ai-log-list", [], () => ""); return; }
    renderItemList("ai-log-list", resp.data || [], (ev) =>
      `<strong>${escHtml(ev.type || ev.event_type || "?")}</strong> ${escHtml(ev.module || ev.target_module || "")}<br/><small>${escHtml((ev.description || "").slice(0, 50))}</small>`
    );
  };

  const resetAiEditor = () => {
    $("#ai-event-type").value = "generation";
    $("#ai-target-module").value = "";
    $("#ai-event-desc").value = "";
  };

  const onAiLog = async () => {
    const data = {
      event_type: $("#ai-event-type").value,
      target_module: $("#ai-target-module").value.trim(),
      description: $("#ai-event-desc").value.trim(),
    };
    if (!data.description) { toast("请填写事件描述", "err"); return; }
    const resp = await callApi("log_assist_event", data);
    if (!resp.ok) { setStatus("#ai-status", `记录失败：${resp.message}`, "err"); return; }
    toast("AI 事件已记录", "ok");
    setStatus("#ai-status", `已记录：${resp.data.id}`, "ok");
    resetAiEditor();
    await renderAiLog();
    await renderAiStats();
  };

  const onAiSuggest = async () => {
    const context = $("#ai-suggest-context").value.trim();
    if (!context) { toast("请填写上下文", "err"); return; }
    setStatus("#ai-suggest-result", "正在获取建议…", "warn");
    const resp = await callApi("auto_suggest", context);
    if (!resp.ok) { setStatus("#ai-suggest-result", `建议获取失败：${resp.message}`, "err"); return; }
    const result = resp.data;
    setStatus("#ai-suggest-result", result.suggestion || result.message || JSON.stringify(result), "ok");
  };

  const onAiCheckThreshold = async () => {
    const threshold = parseFloat($("#ai-threshold-input").value);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) {
      toast("请输入 0–1 之间的阈值", "err"); return;
    }
    const resp = await callApi("check_ai_threshold", threshold);
    if (!resp.ok) { setStatus("#ai-threshold-result", `检查失败：${resp.message}`, "err"); return; }
    const r = resp.data;
    const status = r.ok ? "ok" : "warn";
    setStatus("#ai-threshold-result", `当前使用率: ${(r.current_ratio * 100).toFixed(1)}% / 阈值: ${(r.threshold * 100).toFixed(1)}% — ${r.ok ? "合规" : "超标"}`, status);
  };

  const renderAiStats = async () => {
    const resp = await callApi("get_assist_stats");
    if (!resp.ok) return;
    const s = resp.data || {};
    $("#ai-stat-total").textContent = String(s.total_events ?? s.total ?? "—");
    $("#ai-stat-latest").textContent = s.latest_event_at || s.latest_at || "—";
    const ratioResp = await callApi("get_ai_ratio");
    if (ratioResp.ok && ratioResp.data) {
      const r = ratioResp.data;
      const pct = ((r.ai_ratio ?? r.ratio ?? 0) * 100).toFixed(1);
      $("#ai-stat-ratio").textContent = `${pct}%`;
    } else {
      $("#ai-stat-ratio").textContent = "—";
    }
  };

  // --------------------------------------------------------------
  // Settings tab
  // --------------------------------------------------------------

  const renderSettingsHistory = async () => {
    const resp = await callApi("list_submissions", 200);
    if (!resp.ok) { setStatus("#settings-history-status", "加载失败", "err"); return; }
    const list = $("#settings-history-list");
    if (!list) return;
    list.innerHTML = "";
    const items = resp.data || [];
    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "item-list__empty";
      li.textContent = "暂无提交历史";
      list.appendChild(li);
      return;
    }
    for (const r of items) {
      const li = document.createElement("li");
      li.className = "item-list__item";
      li.dataset.id = r.id;
      const dt = r.submitted_at ? new Date(r.submitted_at.replace("Z", "+00:00")).toLocaleString() : "—";
      const size = r.archive_size ? fmtBytes(r.archive_size) : "—";
      const statusClass = r.smtp_status === "sent" ? "status--ok" : (r.smtp_status === "pending" ? "status--warn" : "status--err");
      li.innerHTML = `
        <div class="item-info">
          <div class="item-name">${escHtml(r.id)}</div>
          <div class="item-desc">
            <span class="item-tag">${escHtml(r.developer_id)}</span>
            <span class="item-tag">${escHtml(r.target_kind)}:${escHtml(r.target_id)}</span>
            <span class="item-tag">${escHtml(size)}</span>
            <span class="item-tag ${statusClass}">${escHtml(r.smtp_status || "—")}</span>
          </div>
        </div>
        <div class="item-meta">
          <small>${escHtml(dt)}</small>
          <button class="btn btn--ghost btn--danger-ghost" data-action="delete" data-id="${escHtml(r.id)}" title="删除此记录及归档">删除</button>
        </div>
      `;
      list.appendChild(li);
    }
    // Bind delete buttons
    list.querySelectorAll("button[data-action=delete]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const subId = btn.dataset.id;
        if (!confirm(`确定要删除提交记录 ${subId} 及其本地归档文件吗？`)) return;
        setStatus("#settings-history-status", "删除中...", "warn");
        const resp = await callApi("delete_submission", subId);
        if (resp.ok) {
          toast(`已删除 ${subId}`, "ok");
          renderSettingsHistory();
        } else {
          setStatus("#settings-history-status", `删除失败：${resp.message}`, "err");
        }
      });
    });
    setStatus("#settings-history-status", `共 ${items.length} 条记录`, "ok");
  };

  const renderSettingsPackages = async () => {
    const resp = await callApi("list_submit_packages");
    if (!resp.ok) { setStatus("#settings-packages-status", "加载失败", "err"); return; }
    const list = $("#settings-packages-list");
    if (!list) return;
    list.innerHTML = "";
    const items = resp.data || [];
    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "item-list__empty";
      li.textContent = "暂无可提交内容包";
      list.appendChild(li);
      return;
    }
    for (const pkg of items) {
      const li = document.createElement("li");
      li.className = "item-list__item";
      li.dataset.id = pkg.package_id;
      li.innerHTML = `
        <div class="item-info">
          <div class="item-name">${escHtml(pkg.name)}</div>
          <div class="item-desc">
            <span class="item-tag">${escHtml(pkg.package_type)}</span>
            <span class="item-tag">${escHtml(pkg.description)}</span>
          </div>
        </div>
        <div class="item-meta">
          <button class="btn btn--ghost btn--danger-ghost" data-action="delete" data-id="${escHtml(pkg.package_id)}" title="删除此内容包（会移除底层内容）">删除</button>
        </div>
      `;
      list.appendChild(li);
    }
    // Bind delete buttons
    list.querySelectorAll("button[data-action=delete]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const pkgId = btn.dataset.id;
        if (!confirm(`确定要删除内容包 ${pkgId} 吗？这将移除对应的底层内容（角色/记忆/世界观/剧情/模型/声音）。`)) return;
        setStatus("#settings-packages-status", "删除中...", "warn");
        const resp = await callApi("delete_package", pkgId);
        if (resp.ok) {
          toast(`已删除 ${pkgId}`, "ok");
          renderSettingsPackages();
        } else {
          setStatus("#settings-packages-status", `删除失败：${resp.message}`, "err");
        }
      });
    });
    setStatus("#settings-packages-status", `共 ${items.length} 个内容包`, "ok");
  };

  const loadSettingsTab = async () => {
    if (!state.activeDeveloper) {
      setStatus("#settings-history-status", "请先登录", "warn");
      setStatus("#settings-packages-status", "请先登录", "warn");
      return;
    }
    await Promise.all([renderSettingsHistory(), renderSettingsPackages()]);
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
    console.log("[devkit] loadBootstrap started");
    try {
      const ping = await callApi("ping");
      console.log("[devkit] ping response:", ping);
      if (!ping.ok) throw new Error("ping failed");
      const cfg = await callApi("whoami");
      console.log("[devkit] whoami response:", cfg);
      if (!cfg.ok) throw new Error("whoami failed");
      renderConfig(cfg.data);
      const kinds = await callApi("target_kinds");
      console.log("[devkit] target_kinds:", kinds);
      if (kinds.ok) renderTargetKinds(kinds.data);
      const me = await callApi("current_developer");
      console.log("[devkit] current_developer:", me);
      if (me.ok && me.data && me.data.developer_id) {
        state.activeDeveloper = me.data.developer_id;
        $("#developer-id").value = me.data.developer_id;
      }
      renderDeveloperChip();
      await refreshHistory();
      await refreshCooldown();
      if (state.activeDeveloper) startCooldownTimer();
      await loadPackages();
      setStatus("#login-status", "就绪", "ok");
      $("#status-bar").textContent = "就绪";
      await populateCharDropdowns();
      await populateVoiceEngines();
      console.log("[devkit] bootstrap complete");
    } catch (err) {
      console.error("[devkit] bootstrap failed:", err);
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
    startCooldownTimer();
    refreshSubmitBtn();
    toast("登录成功", "ok");
  };

  const onLogout = async () => {
    await callApi("logout");
    state.activeDeveloper = null;
    stopCooldownTimer();
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
        if (btn.dataset.tab) {
          switchTab(btn.dataset.tab);
          if (btn.dataset.tab === "settings") loadSettingsTab();
        }
      });
    });

    // Help
    on("#help-btn", "click", openHelp);
    on("#help-close-btn", "click", closeHelp);
    on("#help-overlay", "click", (e) => { if (e.target === e.currentTarget) closeHelp(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHelp(); });
    $$(".help-nav__btn").forEach((btn) => {
      btn.addEventListener("click", () => switchHelpTab(btn.dataset.helpTab));
    });

    // SMTP settings
    on("#edit-smtp-btn", "click", openSmtpModal);
    on("#smtp-modal-close", "click", closeSmtpModal);
    on("#smtp-cancel-btn", "click", closeSmtpModal);
    on("#smtp-save-btn", "click", saveSmtpConfig);

    // Submit tab
    on("#login-btn", "click", onLogin);
    on("#logout-btn", "click", onLogout);
    on("#developer-id", "keydown", (e) => { if (e.key === "Enter") onLogin(); });
    on("#submit-btn", "click", onSubmit);
    on("#refresh-history-btn", "click", refreshHistory);
    on("#packages-refresh-btn", "click", loadPackages);

    // Character tab
    on("#char-refresh-btn", "click", renderCharList);
    on("#char-new-btn", "click", () => { resetCharEditor(); loadCharEditor(null); });
    on("#char-save-btn", "click", onCharSave);
    on("#char-delete-btn", "click", onCharDelete);
    on("#char-export-btn", "click", onCharExport);
    on("#char-import-persona-btn", "click", onCharImportPersona);
    on("#char-persona-template-btn", "click", onCharPersonaTemplate);
    on("#char-persona-template-apply-btn", "click", onCharPersonaTemplateApply);
    on("#char-persona-template-cancel-btn", "click", onCharPersonaTemplateCancel);
    on("#char-config-validate-btn", "click", onCharConfigValidate);
    on("#char-config-autofill-btn", "click", onCharConfigAutofill);
    on("#char-list", "click", (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) onCharSelect(li.dataset.id);
    });

    // Memory tab
    on("#mem-refresh-btn", "click", renderMemList);
    on("#mem-new-btn", "click", () => { resetMemEditor(); loadMemEditor(null); });
    on("#mem-save-btn", "click", onMemSave);
    on("#mem-delete-btn", "click", onMemDelete);
    on("#mem-export-btn", "click", onMemExport);
    on("#mem-char-id", "change", renderMemList);
    on("#mem-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_memory_entry", li.dataset.id);
        if (resp.ok) loadMemEditor(resp.data);
      }
    });

    // World tab
    on("#world-refresh-btn", "click", renderWorldList);
    on("#world-new-btn", "click", () => { resetWorldEditor(); loadWorldEditor(null); });
    on("#world-save-btn", "click", onWorldSave);
    on("#world-delete-btn", "click", onWorldDelete);
    on("#world-export-btn", "click", onWorldExport);
    on("#world-cfg-check-btn", "click", onWorldCheckConfig);
    on("#world-doc-preview-btn", "click", onWorldDocPreview);
    on("#world-doc-template-btn", "click", onWorldDocTemplate);
    on("#world-doc-template-apply-btn", "click", onWorldDocTemplateApply);
    on("#world-doc-template-cancel-btn", "click", onWorldDocTemplateCancel);
    on("#world-event-new-btn", "click", onWorldEventNew);
    on("#world-event-refresh-btn", "click", () => { if (_selectedWorldId) renderWorldEvents(_selectedWorldId); });
    on("#world-event-save-btn", "click", onWorldEventSave);
    on("#world-event-delete-btn", "click", onWorldEventDelete);
    on("#world-event-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id && _selectedWorldId) {
        const resp = await callApi("list_world_events", _selectedWorldId);
        const ev = (resp.data || []).find((x) => x.id === li.dataset.id);
        if (ev) loadWorldEventEditor(ev);
      }
    });
    on("#world-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_world", li.dataset.id);
        if (resp.ok) loadWorldEditor(resp.data);
      }
    });

    // Model tab
    on("#model-refresh-btn", "click", renderModelList);
    on("#model-add-btn", "click", onModelAdd);
    on("#model-unregister-btn", "click", onModelUnregister);
    on("#model-export-btn", "click", onModelExport);
    on("#model-gen-btn", "click", onModelGenerate);
    on("#model-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_model_info", li.dataset.id);
        if (resp.ok) showModelInfo(resp.data);
      }
    });

    // Voice tab
    on("#voice-refresh-btn", "click", renderVoiceList);
    on("#voice-new-btn", "click", () => { resetVoiceEditor(); loadVoiceEditor(null); });
    on("#voice-save-btn", "click", onVoiceSave);
    on("#voice-delete-btn", "click", onVoiceDelete);
    on("#voice-export-btn", "click", onVoiceExport);
    on("#voice-pick-btn", "click", onVoicePickFile);
    on("#voice-record-btn", "click", onVoiceRecord);
    on("#voice-stop-btn", "click", onVoiceStop);
    on("#voice-use-recording-btn", "click", onVoiceUseRecording);
    on("#voice-char-id", "change", async () => {
      await renderVoiceList();
      await populateCharDropdowns();
    });
    on("#voice-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_voice", li.dataset.id);
        if (resp.ok) loadVoiceEditor(resp.data);
      }
    });
    // Voice TTS
    on("#voice-tts-btn", "click", onVoiceTtsGenerate);
    // Voice clone
    on("#voice-clone-pick-btn", "click", onVoiceClonePick);
    on("#voice-clone-btn", "click", onVoiceClone);

    // Plot tab
    on("#plot-refresh-btn", "click", renderPlotList);
    on("#plot-new-btn", "click", () => { resetPlotEditor(); loadPlotEditor(null); });
    on("#plot-save-btn", "click", onPlotSave);
    on("#plot-delete-btn", "click", onPlotDelete);
    on("#plot-export-btn", "click", onPlotExport);
    on("#plot-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_plot", li.dataset.id);
        if (resp.ok) { loadPlotEditor(resp.data); await renderPlotNodes(li.dataset.id); await renderPlotEdges(li.dataset.id); }
      }
    });
    on("#plot-node-new-btn", "click", onPlotNodeNew);
    on("#plot-node-refresh-btn", "click", async () => { if (_selectedPlotId) await renderPlotNodes(_selectedPlotId); });
    on("#plot-node-save-btn", "click", onPlotNodeSave);
    on("#plot-node-delete-btn", "click", onPlotNodeDelete);
    on("#plot-node-list", "click", (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) loadPlotNodeEditor(JSON.parse(li.dataset.node || "{}"));
    });
    on("#plot-edge-new-btn", "click", onPlotEdgeNew);
    on("#plot-edge-refresh-btn", "click", async () => { if (_selectedPlotId) await renderPlotEdges(_selectedPlotId); });
    on("#plot-edge-save-btn", "click", onPlotEdgeSave);
    on("#plot-edge-delete-btn", "click", onPlotEdgeDelete);
    on("#plot-edge-list", "click", (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) loadPlotEdgeEditor(JSON.parse(li.dataset.edge || "{}"));
    });

    // Dialog tab
    on("#dialog-refresh-btn", "click", renderDialogList);
    on("#dialog-new-btn", "click", () => { resetDialogEditor(); loadDialogEditor(null); });
    on("#dialog-save-btn", "click", onDialogSave);
    on("#dialog-delete-btn", "click", onDialogDelete);
    on("#dialog-export-btn", "click", onDialogExport);
    on("#dialog-check-min-btn", "click", onDialogCheckMin);
    on("#dialog-char-id", "change", renderDialogList);
    on("#dialog-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_dialog", li.dataset.id);
        if (resp.ok) loadDialogEditor(resp.data);
      }
    });

    // Motion tab
    on("#motion-refresh-btn", "click", renderMotionList);
    on("#motion-new-btn", "click", () => { resetMotionEditor(); loadMotionEditor(null); });
    on("#motion-save-btn", "click", onMotionSave);
    on("#motion-delete-btn", "click", onMotionDelete);
    on("#motion-export-btn", "click", onMotionExport);
    on("#motion-import-btn", "click", onMotionImport);
    on("#motion-char-id", "change", renderMotionList);
    on("#motion-list", "click", async (e) => {
      const li = e.target.closest(".item-list__item");
      if (li && li.dataset.id) {
        const resp = await callApi("get_motion", li.dataset.id);
        if (resp.ok) loadMotionEditor(resp.data);
      }
    });

    // AI tab
    on("#ai-refresh-btn", "click", renderAiLog);
    on("#ai-new-btn", "click", () => { resetAiEditor(); });
    on("#ai-log-btn", "click", onAiLog);
    on("#ai-suggest-btn", "click", onAiSuggest);
    on("#ai-check-threshold-btn", "click", onAiCheckThreshold);
    on("#ai-refresh-stats-btn", "click", renderAiStats);

    // Settings tab
    on("#settings-history-refresh", "click", renderSettingsHistory);
    on("#settings-history-clear-all", "click", async () => {
      if (!confirm("确定要清空所有提交历史记录吗？此操作不可撤销，将同时删除所有本地归档文件。")) return;
      setStatus("#settings-history-status", "清空中...", "warn");
      const resp = await callApi("clear_submissions");
      if (resp.ok) {
        toast(`已清空 ${resp.data.deleted} 条记录`, "ok");
        renderSettingsHistory();
      } else {
        setStatus("#settings-history-status", `清空失败：${resp.message}`, "err");
      }
    });
    on("#settings-packages-refresh", "click", renderSettingsPackages);
    on("#settings-theme", "change", (e) => {
      const theme = e.target.value;
      localStorage.setItem("devkit-theme", theme);
      applyTheme(theme);
    });
  };

  // --------------------------------------------------------------
  // Start
  // --------------------------------------------------------------

  const start = () => {
    console.log("[devkit] start() called, document.readyState:", document.readyState);
    console.log("[devkit] pywebview present:", !!window.pywebview, "api present:", !!(window.pywebview && window.pywebview.api));
    
    // Initialize theme early (before DOM ready if possible)
    initTheme();
    
    // Wait for DOM to be fully ready before binding
    if (document.readyState !== "complete") {
      console.log("[devkit] waiting for DOM ready...");
      window.addEventListener("load", () => {
        console.log("[devkit] window.load -> bind + bootstrap");
        bind();
        initBridge();
      }, { once: true });
    } else {
      console.log("[devkit] DOM already complete -> bind + bootstrap");
      bind();
      initBridge();
    }
  };

  const initBridge = () => {
    if (window.pywebview && window.pywebview.api) {
      console.log("[devkit] bridge already ready, loading bootstrap");
      loadBootstrap();
    } else {
      console.log("[devkit] waiting for pywebviewready...");
      window.addEventListener("pywebviewready", () => {
        console.log("[devkit] pywebviewready -> loadBootstrap");
        loadBootstrap();
      }, { once: true });
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
