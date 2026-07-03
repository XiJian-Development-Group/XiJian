/* ============================================================
 * 隙间 · 开发者工具 — UI 客户端
 *
 * 单纯调用 window.pywebview.api.*, 不发起任何 HTTP 请求。
 * 等待 pywebviewready 事件确认 js_api 已就绪再开始工作。
 * ============================================================ */

(() => {
  "use strict";

  // --------------------------------------------------------------
  // 状态
  // --------------------------------------------------------------

  /** @typedef {{path: string, size: number, name: string}} FileEntry */

  const state = {
    /** @type {FileEntry[]} */
    files: [],
    /** @type {{cooldown_seconds: number, max_attachment_bytes: number, ...} | null} */
    config: null,
    /** @type {string[] | null} */
    targetKinds: null,
    /** @type {string | null} */
    activeDeveloper: null,
  };

  // --------------------------------------------------------------
  // Utilities
  // --------------------------------------------------------------

  /** @param {number} bytes */
  const fmtBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1000) return `${bytes} B`;
    if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(2)} KB`;
    if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`;
    return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  };

  /** @param {string} iso */
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

  /** @param {string} hex */
  const shortSha = (hex) =>
    !hex ? "—" : `${hex.slice(0, 8)}…${hex.slice(-6)}`;

  /** @param {string} msg @param {'ok'|'err'|'warn'} [kind] */
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

  /** @param {string} sel */
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  /**
   * 调用 window.pywebview.api 并适配 {"ok": ...} 协议。
   * @param {string} method
   * @param  {...any} args
   * @returns {Promise<{ok: true, data: any} | {ok: false, error: any, status: number, ...}>}
   */
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
  // DOM 渲染
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
    $("#files-card-max").textContent = cfg.max_attachment_mb
      ? `${cfg.max_attachment_mb} MB`
      : "—";
    const cdMin = Math.floor((cfg.cooldown_seconds || 0) / 60);
    $("#files-card-cooldown").textContent = cdMin
      ? `${cdMin} 分钟`
      : `${cfg.cooldown_seconds ?? 0} 秒`;
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
    const ready =
      state.activeDeveloper &&
      $("#target-id").value.trim() &&
      state.files.length > 0;
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
      li.className =
        r.smtp_status === "sent"
          ? "history-list li--ok"
          : "history-list li--err";
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
      el.textContent =
        min > 0
          ? `还需等待 ${min} 分 ${sec} 秒`
          : `还需等待 ${sec} 秒`;
    }
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
    if (!id) {
      setStatus("#login-status", "请输入开发者 ID", "warn");
      return;
    }
    const resp = await callApi("login", id);
    if (!resp.ok) {
      setStatus("#login-status", `登录失败：${resp.message}`, "err");
      return;
    }
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
      // pywebview 的 create_file_dialog 返回 Promise<string[]>
      picked = await window.pywebview.create_file_dialog(
        window.pywebview.types.OPEN_MULTIPLE,
        {
          allow_multiple: true,
          file_types: [],
        }
      );
    } catch (err) {
      console.error("file dialog failed", err);
      toast("文件对话框失败", "err");
      return;
    }
    if (!picked || picked.length === 0) return;

    const next = [...picked];
    // 调 Python 端做体积预检，避开 JS 端读本地文件大小（pywebview 不暴露 size）
    const resp = await callApi("preview_size", next.map((p) => ({ path: p, size: 0 })));
    if (!resp.ok) {
      setStatus("#files-status", `预检失败：${resp.message}`, "err");
      return;
    }
    // Python 端未必能 stat 路径，简单的总大小为 0 时仍允许。让后端在 submit 时再次校验
    state.files = next.map((p) => ({
      path: p,
      name: p.split("/").pop(),
      size: 0,
    }));
    renderFiles();
    setStatus(
      "#files-status",
      `已选 ${state.files.length} 个文件 · 体积将在服务端复核`,
      "ok"
    );
  };

  const onClearFiles = () => {
    state.files = [];
    renderFiles();
    setStatus("#files-status", "已清空", "idle");
  };

  const onSubmit = async () => {
    if (!state.activeDeveloper) {
      toast("请先登录", "err");
      return;
    }
    const targetKind = $("#target-kind").value;
    const targetId = $("#target-id").value.trim();
    const aiRatio = parseFloat($("#ai-ratio").value || "0");
    const notes = $("#notes").value.trim();
    if (!targetId) {
      toast("请填写目标 ID", "err");
      return;
    }
    const payload = {
      notes,
      ai_ratio: Number.isFinite(aiRatio) ? aiRatio : 0,
      files: state.files.map((f) => f.path),
    };
    const fileEntries = state.files.map((f) => ({
      path: f.path,
      arcname: f.name,
      size: f.size,
    }));

    setStatus("#submit-status", "正在打包并投递（约 10–60 秒）…", "warn");
    $("#submit-btn").disabled = true;

    const resp = await callApi(
      "submit",
      state.activeDeveloper,
      targetKind,
      targetId,
      payload,
      fileEntries
    );

    if (!resp.ok) {
      setStatus(
        "#submit-status",
        `提交失败：${resp.message} (${resp.code || resp.status || ""})`,
        "err"
      );
      toast(`提交失败：${resp.message}`, "err");
      $("#submit-btn").disabled = false;
      refreshSubmitButton();
      return;
    }

    const r = resp.data;
    setStatus(
      "#submit-status",
      `提交成功：${r.id} · ${fmtBytes(r.archive_size)} · sha256 ${shortSha(r.content_sha256)} · smtp ${r.smtp_status} ${r.smtp_code}`,
      "ok"
    );
    toast(`已发送：${r.id}`, "ok");
    $("#status-bar").textContent = `上次提交 ${r.submitted_at}`;
    await refreshHistory();
    await refreshCooldown();
    $("#submit-btn").disabled = false;
    refreshSubmitButton();
  };

  const refreshHistory = async () => {
    const resp = await callApi("list_submissions", 20);
    if (!resp.ok) {
      renderHistory([]);
      return;
    }
    renderHistory(resp.data || []);
  };

  const refreshCooldown = async () => {
    if (!state.activeDeveloper) {
      renderCooldown(0);
      return;
    }
    const resp = await callApi("cooldown_for", state.activeDeveloper);
    if (!resp.ok) {
      renderCooldown(0);
      return;
    }
    renderCooldown(Number(resp.data) || 0);
  };

  // --------------------------------------------------------------
  // 绑定
  // --------------------------------------------------------------

  const bind = () => {
    $("#login-btn").addEventListener("click", onLogin);
    $("#logout-btn").addEventListener("click", onLogout);
    $("#developer-id").addEventListener("keydown", (e) => {
      if (e.key === "Enter") onLogin();
    });
    $("#pick-files-btn").addEventListener("click", onPickFiles);
    $("#clear-files-btn").addEventListener("click", onClearFiles);
    $("#submit-btn").addEventListener("click", onSubmit);
    $("#refresh-history-btn").addEventListener("click", refreshHistory);
    $("#target-id").addEventListener("input", refreshSubmitButton);
  };

  // --------------------------------------------------------------
  // 启动
  // --------------------------------------------------------------

  const start = () => {
    bind();
    // pywebview 在注入 api 之后会派发 ``pywebviewready`` 事件
    if (window.pywebview && window.pywebview.api) {
      loadBootstrap();
    } else {
      window.addEventListener("pywebviewready", () => loadBootstrap(), {
        once: true,
      });
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
