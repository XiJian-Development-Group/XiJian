import SwiftUI

struct ServerSettingsView: View {
    @Environment(AppViewModel.self) private var appVM
    @State private var showingResetConfirmation = false
    @State private var pythonVersionValid: Bool?
    @State private var isCheckingPython = false

    var body: some View {
        Form {
            Section {
                LabeledContent("状态") {
                    HStack {
                        StatusIndicatorView(state: appVM.coreManager.state)
                        Text(statusText)
                            .foregroundStyle(.secondary)
                    }
                }

                HStack {
                    Spacer()
                    if case .starting = appVM.coreManager.state {
                        ProgressView()
                            .scaleEffect(0.8)
                    }

                    if case .running = appVM.coreManager.state {
                        Button("停止") {
                            appVM.stopServer()
                        }
                        .buttonStyle(.borderedProminent)
                    } else {
                        Button("启动") {
                            Task { await appVM.startServer() }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            } header: {
                Label("服务器", systemImage: "server.rack")
            }

            Section {
                Toggle("使用自定义服务器", isOn: Binding(
                    get: { appVM.serverConfig.useCustomServer },
                    set: { appVM.serverConfig.useCustomServer = $0; appVM.serverConfig.save() }
                ))

                if appVM.serverConfig.useCustomServer {
                    TextField("服务器地址", text: Binding(
                        get: { appVM.serverConfig.customServerURL },
                        set: { appVM.serverConfig.customServerURL = $0; appVM.serverConfig.save() }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .help("例如: http://192.168.1.100:5000")
                }
            } header: {
                Label("连接", systemImage: "network")
            }

            Section {
                TextField("Python 路径", text: Binding(
                    get: { appVM.serverConfig.customPythonPath },
                    set: { appVM.serverConfig.customPythonPath = $0; appVM.serverConfig.save() }
                ))
                .textFieldStyle(.roundedBorder)
                .help("留空则使用嵌入的 Python 或系统 /usr/bin/python3")

                HStack {
                    if isCheckingPython {
                        ProgressView()
                            .scaleEffect(0.7)
                        Text("检查中...")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else if let valid = pythonVersionValid {
                        Image(systemName: valid ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(valid ? .xiJianGreen : .xiJianRed)
                        Text(valid ? "Python 版本 ≥ 3.12" : "Python 版本过低（需 ≥ 3.12）")
                            .font(.caption)
                    }

                    Spacer()

                    Button("检测") {
                        checkPython()
                    }
                    .buttonStyle(.plain)
                    .font(.caption)
                }

                TextField("默认模型", text: Binding(
                    get: { appVM.serverConfig.defaultModel },
                    set: { appVM.serverConfig.defaultModel = $0; appVM.serverConfig.save() }
                ))
                .textFieldStyle(.roundedBorder)
                .help("聊天默认使用的模型 ID")

            } header: {
                Label("Python", systemImage: "terminal")
            }

            Section {
                Button("重置核心数据", role: .destructive) {
                    showingResetConfirmation = true
                }
                .help("删除已解压的核心组件，下次启动时重新解压")

                Text("核心组件位置: \(appVM.coreManager.corePath)")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } header: {
                Label("高级", systemImage: "wrench")
            }
        }
        .formStyle(.grouped)
        .navigationTitle("设置")
    }

    private var statusText: String {
        switch appVM.coreManager.state {
        case .stopped: return "已停止"
        case .extracting: return "解压核心组件中..."
        case .starting: return "启动中..."
        case .running(let port): return "运行中 (127.0.0.1:\(port))"
        case .error(let msg): return "错误: \(msg)"
        }
    }

    private func checkPython() {
        let path = appVM.serverConfig.customPythonPath.isEmpty ? "/usr/bin/python3" : appVM.serverConfig.customPythonPath
        isCheckingPython = true
        pythonVersionValid = nil

        Task {
            let valid = appVM.coreManager.verifyPythonVersion(at: path)
            pythonVersionValid = valid
            isCheckingPython = false
        }
    }
}
