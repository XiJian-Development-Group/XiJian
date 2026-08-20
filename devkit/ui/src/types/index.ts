// 类型定义

export interface DeveloperInfo {
  id: string;
  name: string;
  avatar?: string;
  lastLogin: number;
}

export interface SubmitHistoryItem {
  id: string;
  timestamp: number;
  type: string;
  targetId: string;
  status: 'pending' | 'success' | 'failed';
  size: number;
  error?: string;
}

export interface ConfigState {
  apiVersion: string;
  archiveFormat: string;
  maxBytes: string;
  smtpHost: string;
  smtpPort: number;
  smtpTls: boolean;
  smtpUser: string;
  smtpFrom: string;
  smtpRecipient: string;
}

export interface PackageItem {
  id: string;
  name: string;
  type: 'character' | 'memory' | 'world' | 'dialog' | 'plot' | 'motion' | 'voice';
  size: number;
  version: string;
  selected: boolean;
}

export type TabId = 'submit' | 'character' | 'memory' | 'world' | 'model' | 'voice' | 'plot' | 'dialog' | 'motion' | 'ai' | 'settings';

export interface TabConfig {
  id: TabId;
  label: string;
  icon: string;
  component: string;
  hidden?: boolean;
  requiresAuth?: boolean;
}

export const TABS: TabConfig[] = [
  { id: 'submit', label: '创作提交', icon: '📤', component: 'SubmitTab' },
  { id: 'character', label: '角色人设', icon: '👤', component: 'CharacterTab' },
  { id: 'memory', label: '记忆条目', icon: '🧠', component: 'MemoryTab' },
  { id: 'world', label: '世界观', icon: '🌍', component: 'WorldTab' },
  { id: 'model', label: '3D 预览', icon: '🎭', component: 'ModelTab' },
  { id: 'voice', label: '声音克隆', icon: '🎙️', component: 'VoiceTab' },
  { id: 'plot', label: '剧情设计', icon: '📖', component: 'PlotTab' },
  { id: 'dialog', label: '对话样本', icon: '💬', component: 'DialogTab' },
  { id: 'motion', label: '动作编辑', icon: '🕺', component: 'MotionTab' },
  { id: 'ai', label: 'AI 辅助', icon: '🤖', component: 'AITab', hidden: true },
  { id: 'settings', label: '设置', icon: '⚙️', component: 'SettingsTab' },
];