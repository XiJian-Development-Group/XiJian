// 主应用类 - 管理标签页、状态、API 通信

import { TabManager } from './components/TabManager';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ApiService } from './services/ApiService';
import { StateManager } from './services/StateManager';
import type { TabId, DeveloperInfo, SubmitHistoryItem } from './types';

export class App {
  private root: HTMLElement;
  private tabManager: TabManager;
  private sidebar: Sidebar;
  private header: Header;
  private api: ApiService;
  private state: StateManager;

  constructor() {
    this.root = document.getElementById('app')!;
    this.api = new ApiService();
    this.state = new StateManager();
    this.tabManager = new TabManager(this);
    this.sidebar = new Sidebar(this);
    this.header = new Header(this);
    this.render();
    this.bindEvents();
    this.init();
  }

  private render(): void {
    this.root.innerHTML = `
      <header class="app-header" data-component="header"></header>
      <div class="app-body">
        <nav class="tab-nav" data-component="tabs"></nav>
        <main class="app-main">
          <div class="tab-panels" data-component="panels"></div>
          <aside class="app-sidebar" data-component="sidebar"></aside>
        </main>
      </div>
      <div class="modal-container" data-component="modals"></div>
      <div class="toast-container" data-component="toasts"></div>
    `;

    // 挂载子组件
    this.header.mount(this.root.querySelector('[data-component="header"]')!);
    this.tabManager.mount(
      this.root.querySelector('[data-component="tabs"]')!,
      this.root.querySelector('[data-component="panels"]')!
    );
    this.sidebar.mount(this.root.querySelector('[data-component="sidebar"]')!);
  }

  private bindEvents(): void {
    // 全局键盘快捷键
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        this.saveAll();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'r') {
        e.preventDefault();
        this.refreshCurrentTab();
      }
    });

    // 窗口关闭前确认
    window.addEventListener('beforeunload', (e) => {
      if (this.state.hasUnsavedChanges()) {
        e.preventDefault();
        e.returnValue = '';
      }
    });
  }

  private async init(): Promise<void> {
    // 加载持久化状态
    await this.state.load();
    this.sidebar.restoreState();

    // 检查更新
    this.checkForUpdates();
  }

  private async checkForUpdates(): Promise<void> {
    try {
      const update = await this.api.checkForUpdate();
      if (update.update_available) {
        this.showToast('发现新版本', `v${update.latest_version} 可用`, 'info', 10000);
      }
    } catch {
      // 忽略更新检查失败
    }
  }

  // 公共 API
  getApi(): ApiService { return this.api; }
  getState(): StateManager { return this.state; }
  getTabManager(): TabManager { return this.tabManager; }
  getSidebar(): Sidebar { return this.sidebar; }

  showToast(title: string, message: string, type: 'success' | 'error' | 'info' | 'warning' = 'info', duration = 5000): void {
    const container = this.root.querySelector('[data-component="toasts"]')!;
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.innerHTML = `
      <div class="toast-icon"></div>
      <div class="toast-content">
        <div class="toast-title">${title}</div>
        <div class="toast-message">${message}</div>
      </div>
      <button class="toast-close" aria-label="关闭">×</button>
    `;
    container.appendChild(toast);
    toast.querySelector('.toast-close')!.addEventListener('click', () => toast.remove());
    setTimeout(() => toast.remove(), duration);
  }

  async saveAll(): Promise<void> {
    // 触发当前标签页的保存
    await this.tabManager.saveCurrentTab();
    this.showToast('已保存', '所有更改已保存', 'success');
  }

  async refreshCurrentTab(): Promise<void> {
    await this.tabManager.refreshCurrentTab();
  }

  // 开发者登录/状态
  async login(developerId: string): Promise<DeveloperInfo> {
    const info = await this.api.login(developerId);
    this.state.setDeveloper(info);
    this.header.updateDeveloper(info);
    this.sidebar.updateHistory();
    return info;
  }

  async logout(): Promise<void> {
    await this.api.logout();
    this.state.clearDeveloper();
    this.header.updateDeveloper(null);
    this.sidebar.updateHistory();
  }

  mount(el: HTMLElement): void {
    // 已在构造函数中挂载
  }
}