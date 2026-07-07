import SwiftUI

struct CharacterDetailView: View {
    @State var character: ServerCharacter
    @State private var editedName: String = ""
    @State private var editedPersona: String = ""
    @State private var hasChanges: Bool = false
    @State private var isSaving = false

    let vm: CharacterViewModel?
    let baseURL: String

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("编辑角色")
                    .font(.title)
                    .bold()

                VStack(alignment: .leading, spacing: 6) {
                    Text("名称")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                    TextField("角色名称", text: $editedName)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: editedName) { _, _ in checkChanges() }
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("人设描述")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $editedPersona)
                        .font(.body)
                        .frame(minHeight: 250)
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                        )
                        .onChange(of: editedPersona) { _, _ in checkChanges() }
                }

                if let error = vm?.errorMessage {
                    Text(error)
                        .foregroundStyle(.xiJianRed)
                        .font(.caption)
                }

                HStack {
                    Spacer()
                    if isSaving {
                        ProgressView()
                    }
                    Button("保存") {
                        save()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!hasChanges || isSaving)
                }
            }
            .padding()
        }
        .onAppear {
            editedName = character.name
            editedPersona = character.persona
        }
    }

    private func checkChanges() {
        hasChanges = editedName != character.name || editedPersona != character.persona
    }

    private func save() {
        guard hasChanges, let vm else { return }
        isSaving = true
        Task {
            await vm.updateCharacter(baseURL: baseURL, id: character.id, name: editedName, persona: editedPersona)
            await vm.fetchCharacters(baseURL: baseURL)
            character.name = editedName
            character.persona = editedPersona
            isSaving = false
            hasChanges = false
        }
    }
}
