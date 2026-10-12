import SwiftUI
import XiJianManagerKit

/// Workspace 切换视图：列出现有 profile、切换、创建、删除
struct WorkspaceSwitcherView: View {
    @State private var profiles: [ProfileManager.Profile] = []
    @State private var activeId: String = "default"
    @State private var newProfileName: String = ""
    @State private var showCreateSheet = false
    @State private var error: String?
    @State private var isLoading = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(xj: "Workspace")
                    .font(.title)
                Spacer()
                Button(loc("刷新")) {
                    Task { await load() }
                }
            }

            if isLoading {
                ProgressView()
            } else {
                List(profiles) { profile in
                    HStack {
                        Image(systemName: profile.isActive ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(profile.isActive ? Color.accentColor : Color.secondary)
                        VStack(alignment: .leading) {
                            Text(profile.id)
                                .font(.body)
                            Text(profile.path.path)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        Spacer()
                        if !profile.isDefault {
                            Button(loc("删除")) {
                                Task { await delete(profile.id) }
                            }
                            .buttonStyle(.borderless)
                            .foregroundStyle(.red)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        Task { await activate(profile.id) }
                    }
                    .padding(.vertical, 2)
                }
                .frame(minHeight: 200)
            }

            if let err = error {
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.red)
            }

            Spacer()
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .sheet(isPresented: $showCreateSheet) {
            createSheet
        }
        .toolbar {
            Button(loc("新建")) { showCreateSheet = true }
        }
        .task { await load() }
    }

    private var createSheet: some View {
        VStack(spacing: 16) {
            Text(xj: "新建 Workspace")
                .font(.headline)
            TextField("名称（字母数字 + -_）", text: $newProfileName)
                .textFieldStyle(.roundedBorder)
            HStack {
                Button(loc("取消")) {
                    newProfileName = ""
                    showCreateSheet = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(loc("创建")) {
                    Task { await create() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newProfileName.isEmpty)
            }
        }
        .padding(20)
        .frame(width: 320)
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            profiles = try await ProfileManager.shared.listProfiles()
            activeId = try await ProfileManager.shared.activeProfile()
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func activate(_ name: String) async {
        do {
            try await ProfileManager.shared.setActiveProfile(name)
            await load()
        } catch {
            self.error = "切换失败：\(error)"
        }
    }

    private func delete(_ name: String) async {
        do {
            try await ProfileManager.shared.deleteProfile(name)
            await load()
        } catch {
            self.error = "删除失败：\(error)"
        }
    }

    private func create() async {
        let name = newProfileName.trimmingCharacters(in: .whitespacesAndNewlines)
        defer {
            newProfileName = ""
            showCreateSheet = false
        }
        do {
            _ = try await ProfileManager.shared.createProfile(name: name)
            await load()
        } catch {
            self.error = "创建失败：\(error)"
        }
    }
}
