import SwiftUI
import XiJianManagerKit

/// CN 网络设置视图：开关加速 / 探测候选源 / 看当前选中的源。
///
/// **这一页说的每一句都要是真的。** 以前这里有个「跳过 TLS 验证」的开关，
/// 勾上以后什么也没发生（只是往请求里塞了个没用的头），却会让人以为证书
/// 校验被关了；测速按钮测的是首页延迟，测完也不选任何一个源。现在：
/// 测的就是候选源本身，测完按优先级选出能用的那几个。
struct CNNetworkView: View {
    @State private var options = CNNetworkAdapter.Options()
    @State private var results: [CNProbeResult] = []
    @State private var plan = CNNetworkPlan()
    @State private var isTesting = false

    var body: some View {
        Form {
            Section(loc("国内加速")) {
                Toggle(loc("启用国内镜像"), isOn: Binding(
                    get: { options.mirrorCN },
                    set: { options.mirrorCN = $0 }
                ))
                Text(loc("开启后，安装和更新会走探测出来的镜像，Hermes 的子进程也会带上国内源的环境变量。"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section(loc("代理")) {
                TextField(loc("代理地址"), text: Binding(
                    get: { options.proxyURL },
                    set: { options.proxyURL = $0 }
                ))
                Text(loc("自己有梯子的时候填这里，Hermes 的子进程会带上这个代理。本机的地址不走代理。改完要重启网关才生效。"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section(loc("候选源")) {
                if results.isEmpty {
                    Text(options.mirrorCN ? loc("还没测过") : loc("启用国内镜像后可以测试各个源"))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(results) { result in
                        HStack {
                            Text(result.mirror.name)
                            Text(kindName(result.mirror.kind))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(statusText(result))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(result.reachable ? Color.green : Color.secondary)
                        }
                    }
                }
                Button(loc("测速")) {
                    Task { await testAll() }
                }
                .disabled(isTesting || !options.mirrorCN)
            }

            Section(loc("当前方案")) {
                ForEach(plan.summary, id: \.self) { line in
                    Text(line)
                        .font(.caption.monospaced())
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { load() }
        .onChange(of: options) { _ in save() }
    }

    private func kindName(_ kind: CNMirrorKind) -> String {
        switch kind {
        case .pypi: return "PyPI"
        case .git: return "Git"
        case .raw: return "raw"
        }
    }

    private func statusText(_ result: CNProbeResult) -> String {
        if result.reachable {
            if let ms = result.latencyMS {
                return "\(ms) ms"
            }
            return loc("可用")
        }
        return result.detail.isEmpty ? loc("不可用") : result.detail
    }

    private func load() {
        Task {
            options = await CNNetworkAdapter.shared.currentOptions()
            results = await CNNetworkAdapter.shared.currentProbeResults()
            plan = await CNNetworkAdapter.shared.currentPlan()
        }
    }

    private func save() {
        Task {
            await CNNetworkAdapter.shared.updateOptions(options)
            // 改完得真的作用到已经在跑的 hermes 上（重启才彻底生效）。
            await AppLifecycleManager.shared.applyNetworkEnvironment()
            if !options.mirrorCN {
                results = []
                plan = CNNetworkPlan()
            }
        }
    }

    private func testAll() async {
        isTesting = true
        defer { isTesting = false }
        results = await CNNetworkAdapter.shared.probeAll()
        plan = await CNNetworkAdapter.shared.currentPlan()
    }
}
