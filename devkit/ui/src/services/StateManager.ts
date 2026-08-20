// 状态管理 - 本地存储持久化

import type { DeveloperInfo, SubmitHistoryItem, PackageItem, ConfigState } from '../types';

const STORAGE_KEYS = {
  DEVELOPER: 'xijian_devkit_developer',
  HISTORY: 'xijian_devkit_history',
  PACKAGES: 'xijian_devkit_packages',
  CONFIG: 'xijian_devkit_config',
  UI_STATE: 'xijian_devkit_ui_state',
} as const;

export class StateManager {
  private developer: DeveloperInfo | null = null;
  private history: SubmitHistoryItem[] = [];
  private packages: PackageItem[] = [];
  private config: ConfigState | null = null;
  private uiState: Record<string, any> = {};
  private unsavedChanges = false;

  async load(): Promise<void> {
    try {
      const [dev, hist, pkgs, cfg, ui] = await Promise.all([
        this.getItem(STORAGE_KEYS.DEVELOPER),
        this.getItem(STORAGE_KEYS.HISTORY),
        this.getItem(STORAGE_KEYS.PACKAGES),
        this.getItem(STORAGE_KEYS.CONFIG),
        this.getItem(STORAGE_KEYS.UI_STATE),
      ]);

      this.developer = dev;
      this.history = hist || [];
      this.packages = pkgs || [];
      this.config = cfg;
      this.uiState = ui || {};
    } catch (e) {
      console.warn('Failed to load state:', e);
    }
  }

  // Developer
  getDeveloper(): DeveloperInfo | null { return this.developer; }
  setDeveloper(dev: DeveloperInfo | null): void {
    this.developer = dev;
    this.setItem(STORAGE_KEYS.DEVELOPER, dev);
  }
  clearDeveloper(): void {
    this.developer = null;
    this.removeItem(STORAGE_KEYS.DEVELOPER);
  }

  // History
  getHistory(): SubmitHistoryItem[] { return this.history; }
  addHistory(item: SubmitHistoryItem): void {
    this.history.unshift(item);
    if (this.history.length > 100) this.history.pop();
    this.setItem(STORAGE_KEYS.HISTORY, this.history);
  }
  updateHistory(): void { this.setItem(STORAGE_KEYS.HISTORY, this.history); }

  // Packages
  getPackages(): PackageItem[] { return this.packages; }
  setPackages(pkgs: PackageItem[]): void {
    this.packages = pkgs;
    this.setItem(STORAGE_KEYS.PACKAGES, pkgs);
  }

  // Config
  getConfig(): ConfigState | null { return this.config; }
  setConfig(cfg: ConfigState): void {
    this.config = cfg;
    this.setItem(STORAGE_KEYS.CONFIG, cfg);
  }

  // UI State (scroll positions, expanded sections, etc.)
  getUIState(key: string): any { return this.uiState[key]; }
  setUIState(key: string, value: any): void {
    this.uiState[key] = value;
    this.setItem(STORAGE_KEYS.UI_STATE, this.uiState);
  }

  // Unsaved changes tracking
  hasUnsavedChanges(): boolean { return this.unsavedChanges; }
  markUnsaved(): void { this.unsavedChanges = true; }
  markSaved(): void { this.unsavedChanges = false; }

  // Storage helpers
  private async getItem<T>(key: string): Promise<T | null> {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  }

  private async setItem(key: string, value: any): Promise<void> {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.warn(`Failed to save ${key}:`, e);
    }
  }

  private async removeItem(key: string): Promise<void> {
    try {
      localStorage.removeItem(key);
    } catch (e) {
      console.warn(`Failed to remove ${key}:`, e);
    }
  }
}