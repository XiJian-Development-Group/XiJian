// 创作提交标签页

import { App } from '../App';
import type { PackageItem } from '../types';

export class SubmitTab {
  private app: App;
  private packages: PackageItem[] = [];
  private selectedPackages = new Set<string>();
  private developerId = '';
  private targetKind = '';
  private targetId = '';
  private aiRatio = 0;
  private notes = '';

  constructor(app: App) {
    this.app = app;
  }

  mount(container: HTMLElement): void {
    this.render(container);
    this.bindEvents();
    this.loadPackages();
    this.loadConfig();
  }

  private render(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card" id="login-card">
        <header class="card__header">
          <h2>1. 登录</h2>
          <p class="card__hint">填一个便于识别的标识，我们会用它来记录这次提交。</p>
        </header>
        <div class="card__body">
          <label class="field">
            <span class="field__label">开发者 ID</span>
            <input id="developer-id" class="field__input" type="text" placeholder="例如：alice-32a1" autocomplete="off" spellcheck="false" />
          </label>
          <div class="row row--end">
            <button id="login-btn" class="btn btn--primary" type="button">登录</button>
          </div>
          <p id="login-status" class="status status--idle" role="status"></p>
        </div>
      </section>

      <section class="card" id="target-card">
        <header class="card__header">
          <h2>2. 选择产出物</h2>
          <p class="card__hint">选择提交类型，并填上对应的标识。</p>
        </header>
        <div class="card__body">
          <div class="grid grid--2">
            <label class="field">
              <span class="field__label">类型</span>
              <select id="target-kind" class="field__input"></select>
            </label>
            <label class="field">
              <span class="field__label">标识</span>
              <input id="target-id" class="field__input" type="text" placeholder="例如：char_yuki" autocomplete="off" spellcheck="false" />
            </label>
          </div>
          <label class="field">
            <span class="field__label">AI 协助占比 (0–1)</span>
            <input id="ai-ratio" class="field__input" type="number" min="0" max="1" step="0.05" value="0" />
          </label>
          <label class="field">
            <span class="field__label">补充说明（可选）</span>
            <textarea id="notes" class="field__input" rows="3" placeholder="本次提交的额外说明，会随内容一起发送"></textarea>
          </label>
        </div>
      </section>

      <section class="card" id="packages-card">
        <header class="card__header">
          <h2>3. 选择要提交的内容包</h2>
          <p class="card__hint">勾选需要提交的内容（角色、记忆包、世界观等），系统会自动打包。</p>
        </header>
        <div class="card__body">
          <button id="packages-refresh-btn" class="btn btn--ghost" type="button">刷新可提交列表</button>
          <div id="packages-list" class="packages-list"></div>
          <p id="packages-status" class="status status--idle" role="status"></p>
        </div>
      </section>

      <section class="card" id="submit-card">
        <header class="card__header">
          <h2>4. 提交</h2>
          <p class="card__hint">点击后会开始打包并发送，过程通常需要几秒到一分钟。</p>
        </header>
        <div class="card__body">
          <div class="row row--end">
            <button id="submit-btn" class="btn btn--primary btn--danger" type="button" disabled>提交</button>
          </div>
          <p id="submit-status" class="status status--idle" role="status"></p>
        </div>
      </section>
    `;
  }

  private bindEvents(): void {
    // 登录
    const loginBtn = container?.querySelector('#login-btn') as HTMLButtonElement;
    loginBtn?.addEventListener('click', () => this.login());

    // 刷新包列表
    container?.querySelector('#packages-refresh-btn')?.addEventListener('click', () => this.loadPackages());

    // 提交
    const submitBtn = container?.querySelector('#submit-btn') as HTMLButtonElement;
    submitBtn?.addEventListener('click', () => this.submit());
  }

  private get container(): HTMLElement | null {
    return document.querySelector('#tab-submit .tab-panel__content');
  }

  private async login(): Promise<void> {
    const input = this.container?.querySelector('#developer-id') as HTMLInputElement;
    const statusEl = this.container?.querySelector('#login-status');
    const developerId = input?.value.trim();

    if (!developerId) {
      this.setStatus(statusEl, '请输入开发者 ID', 'error');
      return;
    }

    this.setStatus(statusEl, '登录中…', 'loading');
    try {
      const result = await this.app.getApi().login(developerId);
      if (result.success && result.developer) {
        this.developerId = developerId;
        await this.app.login(result.developer);
        this.setStatus(statusEl, `登录成功：${result.developer.id}`, 'success');
        this.enableSubmit();
      } else {
        this.setStatus(statusEl, result.error || '登录失败', 'error');
      }
    } catch (e) {
      this.setStatus(statusEl, `登录失败: ${e}`, 'error');
    }
  }

  private async loadPackages(): Promise<void> {
    const listEl = this.container?.querySelector('#packages-list');
    const statusEl = this.container?.querySelector('#packages-status');
    if (!listEl) return;

    listEl.innerHTML = '<p class="loading">加载中…</p>';
    this.setStatus(statusEl, '刷新列表…', 'loading');

    try {
      const packages = await this.app.getApi().getPackages();
      this.packages = packages.map((p: any) => ({
        id: p.id,
        name: p.name,
        type: p.type,
        size: p.size,
        version: p.version,
        selected: false,
      }));
      this.renderPackageList();
      this.setStatus(statusEl, `共 ${packages.length} 个可提交包`, 'success');
    } catch (e) {
      listEl.innerHTML = '<p class="error">加载失败</p>';
      this.setStatus(statusEl, `加载失败: ${e}`, 'error');
    }
  }

  private renderPackageList(): void {
    const listEl = this.container?.querySelector('#packages-list');
    if (!listEl) return;

    if (this.packages.length === 0) {
      listEl.innerHTML = '<p class="empty">暂无可提交的内容包</p>';
      return;
    }

    listEl.innerHTML = this.packages.map(p => `
      <label class="package-item">
        <input type="checkbox" value="${p.id}" ${this.selectedPackages.has(p.id) ? 'checked' : ''} />
        <div class="package-info">
          <span class="package-name">${p.name}</span>
          <span class="package-meta">${p.type} · v${p.version} · ${this.formatBytes(p.size)}</span>
        </div>
      </label>
    `).join('');

    listEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', (e) => {
        const target = e.target as HTMLInputElement;
        if (target.checked) this.selectedPackages.add(target.value);
        else this.selectedPackages.delete(target.value);
        this.updateSubmitButton();
      });
    });
  }

  private formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  private async loadConfig(): Promise<void> {
    try {
      const cfg = await this.app.getApi().getConfig();
      const kindSelect = this.container?.querySelector('#target-kind') as HTMLSelectElement;
      if (kindSelect && cfg.packageTypes) {
        kindSelect.innerHTML = cfg.packageTypes.map((t: string) => `<option value="${t}">${t}</option>`).join('');
      }
    } catch (e) {
      console.warn('Failed to load config:', e);
    }
  }

  private setStatus(el: HTMLElement | null, message: string, type: 'idle' | 'loading' | 'success' | 'error'): void {
    if (!el) return;
    el.textContent = message;
    el.className = `status status--${type}`;
  }

  private enableSubmit(): void {
    const btn = this.container?.querySelector('#submit-btn') as HTMLButtonElement;
    if (btn) btn.disabled = false;
  }

  private updateSubmitButton(): void {
    const btn = this.container?.querySelector('#submit-btn') as HTMLButtonElement;
    if (btn) btn.disabled = this.selectedPackages.size === 0 || !this.developerId;
  }

  private async submit(): Promise<void> {
    const btn = this.container?.querySelector('#submit-btn') as HTMLButtonElement;
    const statusEl = this.container?.querySelector('#submit-status');
    const targetKind = (this.container?.querySelector('#target-kind') as HTMLSelectElement)?.value;
    const targetId = (this.container?.querySelector('#target-id') as HTMLInputElement)?.value.trim();
    const aiRatio = parseFloat((this.container?.querySelector('#ai-ratio') as HTMLInputElement)?.value || '0');
    const notes = (this.container?.querySelector('#notes') as HTMLTextAreaElement)?.value.trim();

    if (!targetKind || !targetId) {
      this.setStatus(statusEl, '请填写产出物类型和标识', 'error');
      return;
    }
    if (this.selectedPackages.size === 0) {
      this.setStatus(statusEl, '请至少选择一个内容包', 'error');
      return;
    }

    btn.disabled = true;
    this.setStatus(statusEl, '打包并提交中…', 'loading');

    try {
      const packageIds = Array.from(this.selectedPackages);
      const result = await this.app.getApi().submit({
        developer_id: this.developerId,
        target_kind: targetKind,
        target_id: targetId,
        ai_ratio: aiRatio,
        notes: notes,
        package_ids: packageIds,
      });

      if (result.success) {
        this.setStatus(statusEl, `提交成功！任务 ID: ${result.jobId}`, 'success');
        this.app.showToast('提交成功', `任务 ${result.jobId} 已进入队列`, 'success');
        // 清空表单
        this.selectedPackages.clear();
        this.renderPackageList();
        this.updateSubmitButton();
        // 刷新历史
        this.app.getSidebar().updateHistory();
      } else {
        this.setStatus(statusEl, result.error || '提交失败', 'error');
      }
    } catch (e) {
      this.setStatus(statusEl, `提交失败: ${e}`, 'error');
    } finally {
      btn.disabled = false;
    }
  }
}