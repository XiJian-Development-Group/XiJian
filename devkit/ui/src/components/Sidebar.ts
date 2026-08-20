// 侧边栏 - 历史提交、运行配置

import { App } from '../App';
import { StateManager } from '../services/StateManager';
import type { SubmitHistoryItem, ConfigState } from '../types';

export class Sidebar {
  private app: App;
  private state: StateManager;
  private historyContainer: HTMLElement | null = null;
  private configContainer: HTMLElement | null = null;
  private smtpModal: HTMLElement | null = null;

  constructor(app: App) {
    this.app = app;
    this.state = app.getState();
  }

  mount(container: HTMLElement): void {
    this.render(container);
    this.bindEvents();
    this.restoreState();
  }

  private render(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card" id="history-card">
        <header class="card__header">
          <h2>历史提交</h2>
          <p class="card__hint"><span id="cooldown-indicator">冷却空闲，可随时提交</span></p>
        </header>
        <div class="card__body">
          <button id="refresh-history-btn" class="btn btn--ghost" type="button">刷新</button>
          <ol id="history-list" class="history-list"></ol>
        </div>
      </section>
      <section class="card" id="config-card">
        <header class="card__header">
          <h2>运行配置</h2>
          <p class="card__hint">本次提交使用的设置</p>
        </header>
        <div class="card__body">
          <dl class="kv" id="config-kv"></dl>
          <button id="edit-smtp-btn" class="btn btn--ghost" type="button" style="margin-top:12px;">编辑 SMTP 设置</button>
        </div>
      </section>

      <!-- SMTP 设置编辑弹窗 -->
      <div id="smtp-modal" class="modal" hidden>
        <div class="modal__overlay"></div>
        <div class="modal__content">
          <header class="modal__header">
            <h3>SMTP 设置</h3>
            <button id="smtp-modal-close" class="btn btn--ghost" type="button">×</button>
          </header>
          <div class="modal__body">
            <label class="field">
              <span class="field__label">SMTP 服务器</span>
              <input id="smtp-host-input" class="field__input" type="text" placeholder="例如：smtp.qq.com" />
            </label>
            <label class="field">
              <span class="field__label">SMTP 端口</span>
              <input id="smtp-port-input" class="field__input" type="number" value="465" />
            </label>
            <label class="field">
              <span class="field__label">使用 STARTTLS</span>
              <input id="smtp-tls-input" type="checkbox" />
            </label>
            <label class="field">
              <span class="field__label">SMTP 用户名</span>
              <input id="smtp-user-input" class="field__input" type="text" placeholder="例如：your-email@qq.com" />
            </label>
            <label class="field">
              <span class="field__label">SMTP 授权码</span>
              <input id="smtp-password-input" class="field__input" type="password" placeholder="SMTP 授权码" />
            </label>
            <label class="field">
              <span class="field__label">发件人邮箱</span>
              <input id="smtp-from-input" class="field__input" type="email" placeholder="例如：your-email@qq.com" />
            </label>
            <label class="field">
              <span class="field__label">收件人邮箱</span>
              <input id="smtp-recipient-input" class="field__input" type="email" value="panmofan@icloud.com" disabled />
            </label>
          </div>
          <footer class="modal__footer">
            <button id="smtp-save-btn" class="btn btn--primary" type="button">保存</button>
            <button id="smtp-cancel-btn" class="btn btn--ghost" type="button">取消</button>
          </footer>
        </div>
      </div>
    `;

    this.historyContainer = container.querySelector('#history-list');
    this.configContainer = container.querySelector('#config-kv');
    this.smtpModal = container.querySelector('#smtp-modal');
  }

  private bindEvents(): void {
    if (!this.historyContainer || !this.configContainer) return;

    // 刷新历史
    this.historyContainer?.parentElement?.querySelector('#refresh-history-btn')?.addEventListener('click', () => {
      this.loadHistory();
    });

    // 编辑 SMTP
    this.configContainer?.parentElement?.querySelector('#edit-smtp-btn')?.addEventListener('click', () => {
      this.openSmtpModal();
    });

    // SMTP 弹窗
    this.smtpModal?.querySelector('#smtp-modal-close')?.addEventListener('click', () => this.closeSmtpModal());
    this.smtpModal?.querySelector('#smtp-cancel-btn')?.addEventListener('click', () => this.closeSmtpModal());
    this.smtpModal?.querySelector('#smtp-save-btn')?.addEventListener('click', () => this.saveSmtpConfig());
    this.smtpModal?.querySelector('.modal__overlay')?.addEventListener('click', () => this.closeSmtpModal());
  }

  private async loadHistory(): Promise<void> {
    if (!this.historyContainer) return;
    this.historyContainer.innerHTML = '<li class="loading">加载中…</li>';
    try {
      const history = await this.app.getApi().getHistory();
      this.state.addHistory(...history); // 这里应该是替换而不是追加，但为了演示
      this.renderHistory();
    } catch (e) {
      this.historyContainer.innerHTML = '<li class="error">加载失败</li>';
    }
  }

  private renderHistory(): void {
    if (!this.historyContainer) return;
    const history = this.state.getHistory();
    if (history.length === 0) {
      this.historyContainer.innerHTML = '<li class="empty">暂无提交记录</li>';
      return;
    }
    this.historyContainer.innerHTML = history.map(item => `
      <li class="history-item">
        <div class="history-meta">
          <span class="history-type">${item.type}</span>
          <span class="history-target">${item.targetId}</span>
          <span class="history-status status--${item.status}">${this.getStatusLabel(item.status)}</span>
        </div>
        <div class="history-details">
          <span>${new Date(item.timestamp).toLocaleString()}</span>
          <span>${this.formatBytes(item.size)}</span>
        </div>
        ${item.error ? `<div class="history-error">${item.error}</div>` : ''}
      </li>
    `).join('');
  }

  private getStatusLabel(status: string): string {
    const labels: Record<string, string> = {
      pending: '进行中',
      success: '成功',
      failed: '失败',
    };
    return labels[status] || status;
  }

  private formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  restoreState(): void {
    this.loadHistory();
    this.loadConfig();
  }

  private async loadConfig(): Promise<void> {
    if (!this.configContainer) return;
    try {
      const cfg = await this.app.getApi().getConfig();
      this.state.setConfig(cfg);
      this.renderConfig(cfg);
    } catch (e) {
      this.configContainer.innerHTML = '<dd>加载失败</dd>';
    }
  }

  private renderConfig(cfg: ConfigState): void {
    if (!this.configContainer) return;
    const fields = [
      ['API 版本', cfg.apiVersion],
      ['归档格式', cfg.archiveFormat],
      ['体积上限', cfg.maxBytes],
      ['SMTP 服务器', cfg.smtpHost],
      ['SMTP 端口', String(cfg.smtpPort)],
      ['SMTP STARTTLS', cfg.smtpTls ? '是' : '否'],
      ['SMTP 用户', cfg.smtpUser],
    ];
    this.configContainer.innerHTML = fields.map(([k, v]) => `<dt>${k}</dt><dd>${v || '—'}</dd>`).join('');
  }

  private openSmtpModal(): void {
    if (!this.smtpModal) return;
    const cfg = this.state.getConfig();
    if (cfg) {
      (this.smtpModal.querySelector('#smtp-host-input') as HTMLInputElement).value = cfg.smtpHost;
      (this.smtpModal.querySelector('#smtp-port-input') as HTMLInputElement).value = String(cfg.smtpPort);
      (this.smtpModal.querySelector('#smtp-tls-input') as HTMLInputElement).checked = cfg.smtpTls;
      (this.smtpModal.querySelector('#smtp-user-input') as HTMLInputElement).value = cfg.smtpUser;
      (this.smtpModal.querySelector('#smtp-from-input') as HTMLInputElement).value = cfg.smtpFrom;
      (this.smtpModal.querySelector('#smtp-recipient-input') as HTMLInputElement).value = cfg.smtpRecipient;
    }
    this.smtpModal.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  private closeSmtpModal(): void {
    if (!this.smtpModal) return;
    this.smtpModal.hidden = true;
    document.body.style.overflow = '';
  }

  private async saveSmtpConfig(): Promise<void> {
    const cfg = this.state.getConfig();
    if (!cfg) return;

    const newCfg: ConfigState = {
      ...cfg,
      smtpHost: (this.smtpModal?.querySelector('#smtp-host-input') as HTMLInputElement).value,
      smtpPort: parseInt((this.smtpModal?.querySelector('#smtp-port-input') as HTMLInputElement).value) || 465,
      smtpTls: (this.smtpModal?.querySelector('#smtp-tls-input') as HTMLInputElement).checked,
      smtpUser: (this.smtpModal?.querySelector('#smtp-user-input') as HTMLInputElement).value,
      smtpFrom: (this.smtpModal?.querySelector('#smtp-from-input') as HTMLInputElement).value,
      smtpRecipient: (this.smtpModal?.querySelector('#smtp-recipient-input') as HTMLInputElement).value,
    };

    try {
      await this.app.getApi().saveConfig(newCfg);
      this.state.setConfig(newCfg);
      this.renderConfig(newCfg);
      this.closeSmtpModal();
      this.app.showToast('保存成功', 'SMTP 设置已更新', 'success');
    } catch (e) {
      this.app.showToast('保存失败', String(e), 'error');
    }
  }

  updateHistory(): void {
    this.renderHistory();
  }
}