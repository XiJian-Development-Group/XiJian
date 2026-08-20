// 标签页管理器

import { App } from '../App';
import { TABS, type TabId, type TabConfig } from '../types';

export class TabManager {
  private app: App;
  private tabsNav: HTMLElement | null = null;
  private panelsContainer: HTMLElement | null = null;
  private activeTab: TabId = 'submit';
  private tabInstances: Map<TabId, any> = new Map();

  constructor(app: App) {
    this.app = app;
  }

  mount(nav: HTMLElement, panels: HTMLElement): void {
    this.tabsNav = nav;
    this.panelsContainer = panels;
    this.renderTabs();
    this.renderPanels();
    this.switchTab(this.activeTab);
  }

  private renderTabs(): void {
    if (!this.tabsNav) return;
    this.tabsNav.innerHTML = TABS
      .filter(t => !t.hidden)
      .map(t => `
        <button class="tab-nav__btn ${t.id === this.activeTab ? 'tab-nav__btn--active' : ''}" data-tab="${t.id}" ${t.requiresAuth && !this.app.getState().getDeveloper() ? 'disabled' : ''}>
          ${t.icon} ${t.label}
        </button>
      `).join('') +
      `<button class="tab-nav__btn" id="help-btn" type="button" style="margin-left:auto;color:var(--color-accent)">帮助</button>`;

    this.tabsNav.querySelectorAll('[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => this.switchTab(btn.getAttribute('data-tab') as TabId));
    });
    this.tabsNav.querySelector('#help-btn')?.addEventListener('click', () => this.showHelp());
  }

  private renderPanels(): void {
    if (!this.panelsContainer) return;
    this.panelsContainer.innerHTML = TABS
      .filter(t => !t.hidden)
      .map(t => `
        <section class="tab-panel ${t.id === this.activeTab ? 'tab-panel--active' : ''}" id="tab-${t.id}" data-tab="${t.id}">
          <div class="tab-panel__content"></div>
          <aside class="sidebar"></aside>
        </section>
      `).join('');
  }

  async switchTab(tabId: TabId): Promise<void> {
    if (this.activeTab === tabId) return;

    // 保存当前标签页状态
    await this.saveCurrentTab();

    // 更新 UI
    this.activeTab = tabId;
    this.tabsNav?.querySelectorAll('[data-tab]').forEach(btn => {
      btn.classList.toggle('tab-nav__btn--active', btn.getAttribute('data-tab') === tabId);
    });
    this.panelsContainer?.querySelectorAll('.tab-panel').forEach(panel => {
      panel.classList.toggle('tab-panel--active', panel.getAttribute('data-tab') === tabId);
    });

    // 加载/初始化新标签页
    await this.loadTab(tabId);
  }

  private async loadTab(tabId: TabId): Promise<void> {
    const panel = this.panelsContainer?.querySelector(`#tab-${tabId} .tab-panel__content`);
    const sidebar = this.panelsContainer?.querySelector(`#tab-${tabId} .sidebar`);
    if (!panel) return;

    const tabConfig = TABS.find(t => t.id === tabId);
    if (!tabConfig) return;

    // 动态导入标签页组件
    try {
      const module = await import(`../tabs/${tabConfig.component}`);
      const TabClass = module[tabConfig.component];
      if (TabClass) {
        const instance = new TabClass(this.app);
        this.tabInstances.set(tabId, instance);
        instance.mount(panel);
        if (sidebar && instance.mountSidebar) {
          instance.mountSidebar(sidebar);
        }
      }
    } catch (e) {
      console.error(`Failed to load tab ${tabId}:`, e);
      panel.innerHTML = `<div class="error-state">加载失败: ${tabId}</div>`;
    }
  }

  async saveCurrentTab(): Promise<void> {
    const instance = this.tabInstances.get(this.activeTab);
    if (instance && instance.save) {
      await instance.save();
    }
  }

  async refreshCurrentTab(): Promise<void> {
    const instance = this.tabInstances.get(this.activeTab);
    if (instance && instance.refresh) {
      await instance.refresh();
    }
  }

  private showHelp(): void {
    this.app.showToast('帮助', '文档: https://github.com/Mofan2020/XiJian/wiki', 'info', 8000);
  }
}