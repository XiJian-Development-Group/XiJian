// API 服务 - 通过 pywebview 与 Python 后端通信

declare const pywebview: {
  api: {
    login: (developerId: string) => Promise<{ success: boolean; developer?: any; error?: string }>;
    logout: () => Promise<void>;
    getPackages: () => Promise<{ packages: any[] }>;
    submit: (data: any) => Promise<{ success: boolean; jobId?: string; error?: string }>;
    getHistory: () => Promise<any[]>;
    checkForUpdate: () => Promise<{ update_available: boolean; latest_version: string }>;
    getConfig: () => Promise<any>;
    saveConfig: (config: any) => Promise<{ success: boolean; error?: string }>;
    loadCharacter: (id: string) => Promise<any>;
    saveCharacter: (data: any) => Promise<{ success: boolean; error?: string }>;
    deleteCharacter: (id: string) => Promise<{ success: boolean; error?: string }>;
    listCharacters: () => Promise<any[]>;
    loadMemory: (id: string) => Promise<any>;
    saveMemory: (data: any) => Promise<{ success: boolean; error?: string }>;
    deleteMemory: (id: string) => Promise<{ success: boolean; error?: string }>;
    listMemories: (characterId?: string) => Promise<any[]>;
    loadWorld: (id: string) => Promise<any>;
    saveWorld: (data: any) => Promise<{ success: boolean; error?: string }>;
    deleteWorld: (id: string) => Promise<{ success: boolean; error?: string }>;
    listWorlds: () => Promise<any[]>;
    loadPlot: (id: string) => Promise<any>;
    savePlot: (data: any) => Promise<{ success: boolean; error?: string }>;
    deletePlot: (id: string) => Promise<{ success: boolean; error?: string }>;
    listPlots: () => Promise<any[]>;
    loadDialog: (id: string) => Promise<any>;
    saveDialog: (data: any) => Promise<{ success: boolean; error?: string }>;
    deleteDialog: (id: string) => Promise<{ success: boolean; error?: string }>;
    listDialogs: () => Promise<any[]>;
    loadMotion: (id: string) => Promise<any>;
    saveMotion: (data: any) => Promise<{ success: boolean; error?: string }>;
    deleteMotion: (id: string) => Promise<{ success: boolean; error?: string }>;
    listMotions: () => Promise<any[]>;
    cloneVoice: (data: any) => Promise<{ success: boolean; jobId?: string; error?: string }>;
    previewVoice: (text: string, voiceId: string) => Promise<{ audioUrl?: string; error?: string }>;
    listVoices: () => Promise<any[]>;
    previewModel: (modelId: string) => Promise<{ modelUrl?: string; error?: string }>;
    exportPackage: (type: string, id: string) => Promise<{ blob: Blob; filename: string }>;
    importPackage: (file: File, type: string) => Promise<{ success: boolean; error?: string }>;
  };
};

export class ApiService {
  private async call<T>(method: string, ...args: any[]): Promise<T> {
    try {
      const fn = this.getNestedFunction(pywebview.api, method);
      if (!fn) throw new Error(`API method not found: ${method}`);
      return await fn(...args);
    } catch (error) {
      console.error(`API call failed: ${method}`, error);
      throw error;
    }
  }

  private getNestedFunction(obj: any, path: string): Function | null {
    return path.split('.').reduce((o, k) => (o || {})[k], obj) || null;
  }

  // 认证
  async login(developerId: string): Promise<{ success: boolean; developer?: any; error?: string }> {
    return this.call('login', developerId);
  }

  async logout(): Promise<void> {
    return this.call('logout');
  }

  // 包管理
  async getPackages(): Promise<any[]> {
    const result = await this.call<{ packages: any[] }>('getPackages');
    return result.packages;
  }

  // 提交
  async submit(data: any): Promise<{ success: boolean; jobId?: string; error?: string }> {
    return this.call('submit', data);
  }

  async getHistory(): Promise<any[]> {
    return this.call('getHistory');
  }

  // 更新检查
  async checkForUpdate(): Promise<{ update_available: boolean; latest_version: string }> {
    return this.call('checkForUpdate');
  }

  // 配置
  async getConfig(): Promise<any> {
    return this.call('getConfig');
  }

  async saveConfig(config: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveConfig', config);
  }

  // 角色
  async listCharacters(): Promise<any[]> {
    return this.call('listCharacters');
  }

  async loadCharacter(id: string): Promise<any> {
    return this.call('loadCharacter', id);
  }

  async saveCharacter(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveCharacter', data);
  }

  async deleteCharacter(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deleteCharacter', id);
  }

  // 记忆
  async listMemories(characterId?: string): Promise<any[]> {
    return this.call('listMemories', characterId);
  }

  async loadMemory(id: string): Promise<any> {
    return this.call('loadMemory', id);
  }

  async saveMemory(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveMemory', data);
  }

  async deleteMemory(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deleteMemory', id);
  }

  // 世界
  async listWorlds(): Promise<any[]> {
    return this.call('listWorlds');
  }

  async loadWorld(id: string): Promise<any> {
    return this.call('loadWorld', id);
  }

  async saveWorld(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveWorld', data);
  }

  async deleteWorld(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deleteWorld', id);
  }

  // 剧情
  async listPlots(): Promise<any[]> {
    return this.call('listPlots');
  }

  async loadPlot(id: string): Promise<any> {
    return this.call('loadPlot', id);
  }

  async savePlot(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('savePlot', data);
  }

  async deletePlot(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deletePlot', id);
  }

  // 对话
  async listDialogs(): Promise<any[]> {
    return this.call('listDialogs');
  }

  async loadDialog(id: string): Promise<any> {
    return this.call('loadDialog', id);
  }

  async saveDialog(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveDialog', data);
  }

  async deleteDialog(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deleteDialog', id);
  }

  // 动作
  async listMotions(): Promise<any[]> {
    return this.call('listMotions');
  }

  async loadMotion(id: string): Promise<any> {
    return this.call('loadMotion', id);
  }

  async saveMotion(data: any): Promise<{ success: boolean; error?: string }> {
    return this.call('saveMotion', data);
  }

  async deleteMotion(id: string): Promise<{ success: boolean; error?: string }> {
    return this.call('deleteMotion', id);
  }

  // 声音
  async listVoices(): Promise<any[]> {
    return this.call('listVoices');
  }

  async cloneVoice(data: any): Promise<{ success: boolean; jobId?: string; error?: string }> {
    return this.call('cloneVoice', data);
  }

  async previewVoice(text: string, voiceId: string): Promise<{ audioUrl?: string; error?: string }> {
    return this.call('previewVoice', text, voiceId);
  }

  // 3D 模型
  async previewModel(modelId: string): Promise<{ modelUrl?: string; error?: string }> {
    return this.call('previewModel', modelId);
  }

  // 资源包
  async exportPackage(type: string, id: string): Promise<{ blob: Blob; filename: string }> {
    return this.call('exportPackage', type, id);
  }

  async importPackage(file: File, type: string): Promise<{ success: boolean; error?: string }> {
    return this.call('importPackage', file, type);
  }
}