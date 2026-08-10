# docs/ 文档地图

XiJian 全部文档集中在本目录。按用途分三类：

## 面向用户

| 文档 | 内容 |
|------|------|
| [Dev. Function List功能清单v2.md](Dev.%20Function%20List%E5%8A%9F%E8%83%BD%E6%B8%85%E5%8D%95v2.md) | 功能清单（产品唯一权威来源 SSOT，含版本表） |
| [Dev. 角色制作队列.md](Dev.%20%E8%A7%92%E8%89%B2%E5%88%B6%E4%BD%9C%E9%98%9F%E5%88%97.md) | 角色制作排队规则 |
| [Deps.md](Deps.md) | 第三方依赖清单 |
| [README 根](../README.md) | 项目介绍入口（本目录的上层索引） |

## 开发者 / 维护者

| 文档 | 内容 |
|------|------|
| [Dev.md](Dev.md) | 开发者技术文档（架构 / 目录结构 / 模块接口） |
| [api.md](api.md) | 本地 API 协议规范（人读版；机器可读版为 openapi.yaml） |
| [BuildGuide.md](BuildGuide.md) | Core 构建与打包指南（PyInstaller） |
| [CoreStartupGuide.md](CoreStartupGuide.md) | Core 启动指南（安装 / 配置 / 启动 / 运维） |
| [macapp.md](macapp.md) | macOS 客户端（构建 / 运行 / 功能范围 / A6 通话 / 本地化） |
| [AIBackend.md](AIBackend.md) | AI 后端实现（MLX / GGUF 等） |
| [MCP.md](MCP.md) | Core MCP 功能说明 |
| [Problems.md](Problems.md) | 功能清单与代码实现的差距盘账 |
| [维护教程.md](维护教程.md) | 维护者总览（存储布局 / 版本号 / 资源包 / 文档同步地图，见其 §8） |
| [notes.md](notes.md) | 开发日志（每个功能改动的流水记录） |

## 数据 / 产物（非文档）

- `openapi.yaml` — api.md 的机器可读版（OpenAPI 3.0.3）
- `eval/` — 安全评测数据
- `md2pdf/` — 功能清单 / 角色队列的 PDF 导出
- `已弃置/` — 已归档文档（仅历史参考，不再更新）
