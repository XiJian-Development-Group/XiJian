import SwiftUI

struct CharacterListView: View {
    @Environment(AppViewModel.self) private var appVM
    @State private var characterVM: CharacterViewModel?
    @State private var searchText = ""
    @State private var showCreateSheet = false

    var filteredCharacters: [ServerCharacter] {
        guard let chars = characterVM?.characters else { return [] }
        if searchText.isEmpty { return chars }
        return chars.filter { $0.name.localizedCaseInsensitiveContains(searchText) || $0.persona.localizedCaseInsensitiveContains(searchText) }
    }

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(
                get: { characterVM?.selectedCharacter?.id },
                set: { id in characterVM?.selectedCharacter = characterVM?.characters.first { $0.id == id } }
            )) {
                if let chars = characterVM?.characters {
                    ForEach(filteredCharacters) { character in
                        HStack {
                            Image(systemName: "person.crop.circle.fill")
                                .font(.title2)
                                .foregroundStyle(.xiJianPurple)
                            VStack(alignment: .leading) {
                                Text(character.name)
                                    .font(.headline)
                                Text(character.persona)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                        }
                        .tag(character.id)
                        .contextMenu {
                            Button("删除", role: .destructive) {
                                characterVM?.deleteCharacter(at: character.id)
                            }
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .searchable(text: $searchText, prompt: "搜索角色...")
            .navigationTitle("角色")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button(action: { showCreateSheet = true }) {
                        Image(systemName: "plus")
                    }
                    .help("新建角色")
                }
                ToolbarItem(placement: .primaryAction) {
                    Button(action: {
                        Task { await characterVM?.fetchCharacters(baseURL: appVM.baseURL) }
                    }) {
                        Image(systemName: "arrow.clockwise")
                    }
                    .help("刷新")
                }
            }
        } detail: {
            if let character = characterVM?.selectedCharacter {
                CharacterDetailView(character: character, vm: characterVM, baseURL: appVM.baseURL)
            } else {
                ContentUnavailableView(
                    "选择角色",
                    systemImage: "person.crop.circle",
                    description: Text("从左侧选择一个角色，或创建新角色")
                )
            }
        }
        .onAppear {
            if characterVM == nil {
                characterVM = CharacterViewModel(apiClient: appVM.apiClient)
            }
            Task { await characterVM?.fetchCharacters(baseURL: appVM.baseURL) }
        }
        .sheet(isPresented: $showCreateSheet) {
            CreateCharacterSheet(
                onSave: { name, persona in
                    guard let vm = characterVM else { return false }
                    await vm.createCharacter(baseURL: appVM.baseURL, name: name, persona: persona)
                    await vm.fetchCharacters(baseURL: appVM.baseURL)
                    return vm.errorMessage == nil
                }
            )
        }
    }
}

struct CreateCharacterSheet: View {
    @State private var name = ""
    @State private var persona = ""
    @State private var isCreating = false
    @State private var errorMessage: String?

    let onSave: (String, String) async -> Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 16) {
            Text("创建新角色")
                .font(.title2)
                .padding(.top)

            TextField("角色名称", text: $name)
                .textFieldStyle(.roundedBorder)

            TextEditor(text: $persona)
                .font(.body)
                .frame(minHeight: 200)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                )

            if let error = errorMessage {
                Text(error)
                    .foregroundStyle(.xiJianRed)
                    .font(.caption)
            }

            HStack {
                Button("取消") { dismiss() }
                    .keyboardShortcut(.escape)

                Spacer()

                Button("创建") {
                    guard !name.isEmpty else { return }
                    isCreating = true
                    errorMessage = nil
                    Task {
                        let success = await onSave(name, persona)
                        isCreating = false
                        if success {
                            dismiss()
                        } else {
                            errorMessage = "创建失败，请检查服务器连接"
                        }
                    }
                }
                .keyboardShortcut(.return)
                .disabled(name.isEmpty || isCreating)
            }
        }
        .padding()
        .frame(width: 450, height: 400)
    }
}
