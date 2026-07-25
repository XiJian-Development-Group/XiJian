XiJian Core API
================

使用说明
--------

1. 启动服务:
   macOS / Linux:
     ./xijian-api --port 18500

   Windows:
     xijian-api.exe --port 18500

2. 配置文件:
   config.toml 位于本目录，可按需修改。
   修改后重启服务生效。

3. 日志文件:
   logs/xijian-api.log (自动创建)

4. 存储目录:
   data/ (模型权重、用户上传文件等)

5. Token 文件:
   run/ (Bearer token，启动时自动生成)

6. 外部 AI 依赖 (可选):
   external_libs/ 目录用于放置 MLX / llama_cpp 等大型二进制依赖。
   将 AI 扩展包解压到此目录即可启用本地 AI 后端。

7. 常用参数:
   --port N         监听端口 (默认 18500)
   --host ADDR      监听地址 (默认 0.0.0.0)
   --dev            开发模式 (自动生成 token)
   --config PATH    指定配置文件
   --log-level L    日志级别 (DEBUG/INFO/WARNING/ERROR)
   --log-file PATH  日志文件路径
   --no-serve       仅初始化不启动服务 (冒烟测试)
   --version        打印版本信息

8. 健康检查:
   启动后访问 http://localhost:18500/v1/health

9. API 文档:
   http://localhost:18500/v1/

完整文档: https://github.com/your-org/xijian
