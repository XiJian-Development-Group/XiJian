import SwiftUI
import XiJianManagerKit

/// SkillHub 视图：已安装 skill 列表 + 安装 / 卸载
struct SkillHubView: View {
    @State private var skills: [SkillHubClient.Skill] = []
    @State private var isLoading = false
    @State private var error: String?
    @State private var installURL: String = ""
    @State private var showInstallSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "SkillHub")
                    .font(.title)
                Spacer()
                Button(loc("刷新")) { Task { await load() } }
                Button(loc("+ 安装")) { showInstallSheet = true }
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if skills.isEmpty {
                ContentUnavailableView(
                    "未安装 skill",
                    systemImage: "shippingbox",
                    description: Text("点 + 安装从 git URL 拉取")
                )
            } else {
                List {
                    ForEach(skills) { skill in
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(skill.id).font(.headline)
                                    if !skill.version.isEmpty {
                                        Text(skill.version)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                if !skill.description.isEmpty {
                                    Text(skill.description)
                                        .font(.callout)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                                Text(skill.path.path)
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                            Spacer()
                            Button(loc("卸载"), role: .destructive) {
                                Task { await uninstall(skill.id) }
                            }
                            .buttonStyle(.borderless)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showInstallSheet) {
            installSheet
        }
        .task { await load() }
    }

    private var installSheet: some View {
        VStack(spacing: 16) {
            Text(xj: "安装 Skill").font(.headline)
            TextField("git URL", text: $installURL)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            HStack {
                Button(loc("取消")) {
                    installURL = ""
                    showInstallSheet = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(loc("安装")) {
                    Task { await install() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(installURL.isEmpty)
            }
        }
        .padding(20)
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            skills = try await SkillHubClient.shared.listInstalled()
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func install() async {
        let url = installURL
        installURL = ""
        showInstallSheet = false
        do {
            _ = try await SkillHubClient.shared.install(fromGitURL: url)
            await load()
        } catch {
            self.error = "安装失败：\(error)"
        }
    }

    private func uninstall(_ name: String) async {
        do {
            try await SkillHubClient.shared.uninstall(name: name)
            await load()
        } catch {
            self.error = "卸载失败：\(error)"
        }
    }
}
