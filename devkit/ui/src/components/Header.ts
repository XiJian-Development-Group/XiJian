// 头部组件

import { App } from '../App';
import type { DeveloperInfo } from '../types';

export class Header {
  private app: App;
  private element: HTMLElement | null = null;

  constructor(app: App) {
    this.app = app;
  }

  mount(container: HTMLElement): void {
    this.element = container;
    this.render();
  }

  private render(): void {
    if (!this.element) return;
    const dev = this.app.getState().getDeveloper();
    this.element.innerHTML = `
      <div class="app-header__brand">
        <h1>隙间 · 开发者工具</h1>
        <p class="app-header__subtitle">创作、编辑、打包并提交你的内容</p>
      </div>
      <div class="app-header__meta">
        <div id="recipient-chip" class="chip chip--ghost" title="本次提交将发送到此邮箱">
          <span class="chip__label">收件</span>
          <span id="recipient-chip-value">…</span>
        </div>
        <div id="developer-chip" class="chip chip--ghost">
          <span class="chip__label">当前开发者</span>
          <span id="developer-chip-value">${dev ? dev.id : '未登录'}</span>
        </div>
        <button id="logout-btn" class="btn btn--ghost" type="button" ${dev ? '' : 'hidden'}>退出</button>
      </div>
    `;

    this.bindEvents();
  }

  private bindEvents(): void {
    if (!this.element) return;
    this.element.querySelector('#logout-btn')?.addEventListener('click', () => {
      this.app.logout();
    });
  }

  updateDeveloper(dev: DeveloperInfo | null): void {
    const valueEl = this.element?.querySelector('#developer-chip-value');
    const logoutBtn = this.element?.querySelector('#logout-btn') as HTMLButtonElement | null;
    if (valueEl) valueEl.textContent = dev ? dev.id : '未登录';
    if (logoutBtn) logoutBtn.hidden = !dev;
  }

  updateRecipient(recipient: string): void {
    const valueEl = this.element?.querySelector('#recipient-chip-value');
    if (valueEl) valueEl.textContent = recipient;
  }
}