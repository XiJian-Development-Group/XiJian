// 占位标签页基类

import { App } from '../App';

export abstract class BaseTab {
  protected app: App;

  constructor(app: App) {
    this.app = app;
  }

  abstract mount(container: HTMLElement): void;

  async save(): Promise<void> { /* 可选实现 */ }
  async refresh(): Promise<void> { /* 可选实现 */ }
}

// 角色人设标签页
export class CharacterTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header">
          <h2>角色人设</h2>
          <p class="card__hint">管理所有已创建的角色人设文档</p>
        </header>
        <div class="card__body">
          <div class="row row--gap">
            <button id="create-character-btn" class="btn btn--primary">新建角色</button>
            <button id="refresh-characters-btn" class="btn btn--ghost">刷新列表</button>
          </div>
          <div id="characters-list" class="characters-list"></div>
        </div>
      </section>
    `;
    this.bindEvents();
    this.loadCharacters();
  }

  private bindEvents(): void {
    container?.querySelector('#create-character-btn')?.addEventListener('click', () => this.openCreateDialog());
    container?.querySelector('#refresh-characters-btn')?.addEventListener('click', () => this.loadCharacters());
  }

  private async loadCharacters(): Promise<void> {
    const listEl = container?.querySelector('#characters-list');
    if (!listEl) return;
    listEl.innerHTML = '<p class="loading">加载中…</p>';
    try {
      const chars = await this.app.getApi().listCharacters();
      this.renderCharacters(chars);
    } catch (e) {
      listEl!.innerHTML = '<p class="error">加载失败</p>';
    }
  }

  private renderCharacters(chars: any[]): void {
    const listEl = container?.querySelector('#characters-list');
    if (!listEl) return;
    if (chars.length === 0) {
      listEl.innerHTML = '<p class="empty">暂无角色，点击"新建角色"创建</p>';
      return;
    }
    listEl.innerHTML = chars.map(c => `
      <div class="character-card">
        <h3>${c.display_name || c.id}</h3>
        <p>${c.description || '无描述'}</p>
        <div class="card-actions">
          <button class="btn btn--ghost btn--sm" data-action="edit" data-id="${c.id}">编辑</button>
          <button class="btn btn--ghost btn--sm btn--danger" data-action="delete" data-id="${c.id}">删除</button>
        </div>
      </div>
    `).join('');
    listEl.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const action = btn.getAttribute('data-action');
        const id = btn.getAttribute('data-id');
        if (action === 'edit') this.openEditDialog(id!);
        else if (action === 'delete') this.deleteCharacter(id!);
      });
    });
  }

  private openCreateDialog(): void { /* 打开创建对话框 */ }
  private openEditDialog(id: string): void { /* 打开编辑对话框 */ }
  private async deleteCharacter(id: string): Promise<void> {
    if (!confirm('确定删除此角色？')) return;
    try {
      await this.app.getApi().deleteCharacter(id);
      this.app.showToast('已删除', '角色已删除', 'success');
      this.loadCharacters();
    } catch (e) {
      this.app.showToast('删除失败', String(e), 'error');
    }
  }

  private get container(): HTMLElement | null {
    return document.querySelector('#tab-character .tab-panel__content');
  }
}

// 记忆条目标签页
export class MemoryTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>记忆条目</h2></header>
        <div class="card__body"><p class="empty">记忆管理功能开发中…</p></div>
      </section>
    `;
  }
}

// 世界观标签页
export class WorldTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>世界观</h2></header>
        <div class="card__body"><p class="empty">世界观管理功能开发中…</p></div>
      </section>
    `;
  }
}

// 3D 预览标签页
export class ModelTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>3D 模型预览</h2></header>
        <div class="card__body">
          <div id="model-viewer" style="width:100%;height:500px;background:var(--color-bg);border-radius:var(--radius-md)"></div>
        </div>
      </section>
    `;
  }
}

// 声音克隆标签页
export class VoiceTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>声音克隆</h2></header>
        <div class="card__body"><p class="empty">声音克隆功能开发中…</p></div>
      </section>
    `;
  }
}

// 剧情设计标签页
export class PlotTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>剧情设计</h2></header>
        <div class="card__body"><p class="empty">剧情设计功能开发中…</p></div>
      </section>
    `;
  }
}

// 对话样本标签页
export class DialogTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>对话样本</h2></header>
        <div class="card__body"><p class="empty">对话样本管理功能开发中…</p></div>
      </section>
    `;
  }
}

// 动作编辑标签页
export class MotionTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>动作编辑</h2></header>
        <div class="card__body"><p class="empty">动作编辑功能开发中…</p></div>
      </section>
    `;
  }
}

// AI 辅助标签页
export class AITab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>AI 辅助</h2></header>
        <div class="card__body"><p class="empty">AI 辅助功能开发中…</p></div>
      </section>
    `;
  }
}

// 设置标签页
export class SettingsTab extends BaseTab {
  mount(container: HTMLElement): void {
    container.innerHTML = `
      <section class="card">
        <header class="card__header"><h2>设置</h2></header>
        <div class="card__body"><p class="empty">设置功能开发中…</p></div>
      </section>
    `;
  }
}