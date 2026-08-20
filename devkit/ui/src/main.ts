// DevKit UI 入口点
// 使用 Vite + TypeScript 模块化开发，构建后输出到 dist/ 供 PyInstaller 打包

import './styles/main.css';
import './styles/components.css';
import { App } from './App';

// 初始化应用
const app = new App();
app.mount('#app');

// 暴露给全局用于调试
declare global {
  interface Window {
    __XIJIAN_DEVKIT__: App;
  }
}
window.__XIJIAN_DEVKIT__ = app;