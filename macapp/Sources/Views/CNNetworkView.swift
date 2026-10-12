import SwiftUI
import XiJianManagerKit

/// CN 网络设置视图：开关镜像 / 列出当前镜像 / 测速
struct CNNetworkView: View {
    @State private var options = CNNetworkAdapter.Options()
    @State private var latencies: [String: String] = [:]
    @State private var isTesting = false
    @State private var error: String?

    var body: some View {
        Form {
            Section("CN 镜像") {
                Toggle("启用镜像（GitHub/npm/pypi）", isOn: Binding(
                    get: { options.mirrorCN },
                    set: { options.mirrorCN = $0 }
                ))
                Toggle("跳过 TLS 验证（不推荐）", isOn: Binding(
                    get: { options.noCAVerify },
                    set: { options.noCAVerify = $0 }
                ))
            }

            Section("GitHub 镜像") {
                ForEach(CNNetworkAdapter.ghMirrors, id: \.self) { mirror in
                    HStack {
                        Text(mirror)
                            .font(.callout.monospaced())
                        Spacer()
                        if let latency = latencies[mirror] {
                            Text(latency)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(latency == "失败" ? .red : .secondary)
                        }
                    }
                }
                Button(loc("测速")) {
                    Task { await testLatency() }
                }
                .disabled(isTesting)
            }

            Section("npm / pypi") {
                LabeledContent("npm") {
                    Text(options.mirrorCN ? "https://registry.npmmirror.com/" : "默认")
                        .font(.callout.monospaced())
                }
                LabeledContent("pypi") {
                    Text(options.mirrorCN ? "https://pypi.tuna.tsinghua.edu.cn/simple" : "默认")
                        .font(.callout.monospaced())
                }
            }

            Section {
                Text(describe(options))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { loadOptions() }
        .onChange(of: options) { _ in saveOptions() }
    }

    private func loadOptions() {
        Task { options = await CNNetworkAdapter.shared.currentOptions() }
    }

    private func saveOptions() {
        Task { await CNNetworkAdapter.shared.updateOptions(options) }
    }

    private func describe(_ o: CNNetworkAdapter.Options) -> String {
        var lines: [String] = []
        lines.append("CN mirror: \(o.mirrorCN)")
        lines.append("CA verify: \(o.noCAVerify ? "skipped" : "enabled")")
        if o.mirrorCN {
            lines.append("npm registry: https://registry.npmmirror.com/")
            lines.append("pypi index: https://pypi.tuna.tsinghua.edu.cn/simple")
        }
        return lines.joined(separator: "\n")
    }

    private func testLatency() async {
        isTesting = true
        defer { isTesting = false }
        var results: [String: String] = [:]
        for mirror in CNNetworkAdapter.ghMirrors {
            let ms = await measureLatency(urlString: mirror)
            results[mirror] = ms.map { "\($0) ms" } ?? "失败"
        }
        latencies = results
    }

    private func measureLatency(urlString: String) async -> Int? {
        guard let url = URL(string: urlString) else { return nil }
        let start = Date()
        var request = URLRequest(url: url)
        request.httpMethod = "HEAD"
        request.timeoutInterval = 5
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, (200..<500).contains(http.statusCode) {
                return Int(Date().timeIntervalSince(start) * 1000)
            }
            return nil
        } catch {
            return nil
        }
    }
}
